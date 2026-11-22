import json
import os
import threading
import time
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import quote

from flask import Flask, jsonify, render_template_string, request, send_from_directory

from auto_clipper import (
    DEFAULT_EDIT_LENGTH,
    detect_reactions_and_clip,
    get_edits_dir,
)

app = Flask(__name__)

JOBS_LOCK = threading.Lock()
JOBS: Dict[str, Dict[str, Any]] = {}


def _parse_time_to_seconds(raw: Any) -> Optional[float]:
    if raw is None:
        return None
    s = str(raw).strip()
    if not s:
        return None
    if ":" in s:
        parts = [p.strip() for p in s.split(":")]
        try:
            nums = [float(p) for p in parts]
        except ValueError:
            return None
        if len(nums) == 2:
            return round(nums[0] * 60.0 + nums[1], 2)
        if len(nums) == 3:
            return round(nums[0] * 3600.0 + nums[1] * 60.0 + nums[2], 2)
        return None
    try:
        return round(float(s), 2)
    except ValueError:
        return None


def _parse_manual_editor_payload(
    manual_clips_json_raw: str,
    manual_start_raw: str,
    manual_dur_raw: str,
    manual_end_raw: str,
    manual_lines_raw: str,
    default_dur: float = DEFAULT_EDIT_LENGTH,
    default_slice_sec: float = 6.5,
) -> Tuple[Optional[List[Tuple[float, float]]], List[Dict[str, Any]]]:
    records: List[Dict[str, Any]] = []
    windows: List[Tuple[float, float]] = []
    fallback_len = max(2.0, min(60.0, float(default_slice_sec or 6.5)))

    def add_window(st: float, dur: float, label: str = "") -> None:
        st_clean = round(max(0.0, float(st)), 2)
        dur_clean = round(max(2.0, min(180.0, float(dur))), 2)
        en_clean = round(st_clean + dur_clean, 2)
        windows.append((st_clean, dur_clean))
        records.append(
            {
                "start": st_clean,
                "duration": dur_clean,
                "end": en_clean,
                "label": label.strip() or f"Clip #{len(records) + 1}",
            }
        )

    raw_json = (manual_clips_json_raw or "").strip()
    if raw_json:
        try:
            parsed = json.loads(raw_json)
            if isinstance(parsed, list):
                for idx, item in enumerate(parsed, start=1):
                    if not isinstance(item, dict):
                        continue
                    st = _parse_time_to_seconds(item.get("start"))
                    if st is None:
                        continue
                    en = _parse_time_to_seconds(item.get("end"))
                    dur = _parse_time_to_seconds(item.get("duration"))
                    if en is not None and en > st:
                        computed_dur = en - st
                    elif dur is not None and dur >= 2.0:
                        computed_dur = dur
                    else:
                        computed_dur = min(default_dur, fallback_len)
                    lbl = str(item.get("label") or f"Manual Clip {idx}")
                    add_window(st, computed_dur, lbl)
        except Exception:
            pass

    for idx, raw_line in enumerate((manual_lines_raw or "").splitlines(), start=1):
        line = raw_line.strip()
        if not line:
            continue
        if "-" in line and "," not in line:
            left, right = [x.strip() for x in line.split("-", 1)]
            st = _parse_time_to_seconds(left)
            en = _parse_time_to_seconds(right)
            if st is not None and en is not None and en > st:
                add_window(st, max(2.0, en - st), f"Range {idx}")
                continue

        tokens = [t.strip() for t in line.replace(";", ",").replace(" ", ",").split(",") if t.strip()]
        if len(tokens) >= 2:
            st = _parse_time_to_seconds(tokens[0])
            dur = _parse_time_to_seconds(tokens[1])
            if st is not None and dur is not None and dur >= 2.0:
                add_window(st, dur, f"Line Clip {idx}")

    st_single = _parse_time_to_seconds(manual_start_raw)
    en_single = _parse_time_to_seconds(manual_end_raw)
    dur_single = _parse_time_to_seconds(manual_dur_raw)
    if st_single is not None:
        if en_single is not None and en_single > st_single:
            add_window(st_single, max(2.0, en_single - st_single), "Quick Clip")
        elif dur_single is not None and dur_single >= 2.0:
            add_window(st_single, dur_single, "Quick Clip")
        else:
            add_window(st_single, min(default_dur, fallback_len), "Quick Clip")

    return (windows if windows else None), records


def _list_saved_edits() -> List[Dict[str, Any]]:
    edits_dir = get_edits_dir()
    items: List[Dict[str, Any]] = []
    exts = {".mp4", ".mov", ".mkv", ".webm"}
    files = sorted(
        [p for p in edits_dir.iterdir() if p.is_file() and p.suffix.lower() in exts],
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    for p in files[:60]:
        size_mb = round(p.stat().st_size / (1024 * 1024), 2)
        items.append(
            {
                "name": p.name,
                "size_mb": size_mb,
                "url": f"/edits/{quote(p.name)}",
            }
        )
    return items


def _build_spread_windows_for_run(
    run_idx: int,
    total_runs: int,
    clip_selection_mode: str,
    max_clips: int,
    custom_windows: Optional[List[Tuple[float, float]]],
) -> Optional[List[Tuple[float, float]]]:
    if not custom_windows:
        return None
    if clip_selection_mode == "limited":
        selected = custom_windows[:max_clips]
    else:
        selected = custom_windows

    if total_runs <= 1 or len(selected) <= 1:
        return selected

    shift = (run_idx - 1) % len(selected)
    return selected[shift:] + selected[:shift]


def _run_edit_job(job_id: str, payload: Dict[str, Any]) -> None:
    def append_log(msg: str) -> None:
        with JOBS_LOCK:
            job = JOBS.get(job_id)
            if job is not None:
                job["logs"].append(msg)
                job["logs"] = job["logs"][-250:]

    stop_event: threading.Event = payload["stop_event"]
    final_edits_count = max(1, min(20, int(payload.get("final_edits_count", 1))))
    clip_selection_mode = payload.get("clip_selection_mode", "all_best")
    backend_clip_mode = "all_good" if clip_selection_mode == "all_best" else "count"
    effective_max_clips = 20 if clip_selection_mode == "all_best" else int(payload["max_clips"])

    all_outputs: List[Dict[str, Any]] = []

    append_log(
        f"[{payload['label']}] Target: {final_edits_count} final edit(s) x {payload['edit_length']:.0f}s | "
        f"Mode: {'ALL BEST CLIPS (full video spread)' if clip_selection_mode == 'all_best' else f'LIMITED ({effective_max_clips} clips)'} | "
        f"Slice: {payload['clip_slice_sec']:.1f}s"
    )

    try:
        for run_idx in range(1, final_edits_count + 1):
            if stop_event.is_set():
                append_log(f"Stopped before final edit #{run_idx}/{final_edits_count}.")
                break

            if final_edits_count > 1:
                append_log(f"--- Rendering final edit #{run_idx} of {final_edits_count} for {payload['label']} ---")

            run_windows = _build_spread_windows_for_run(
                run_idx=run_idx,
                total_runs=final_edits_count,
                clip_selection_mode=clip_selection_mode,
                max_clips=effective_max_clips,
                custom_windows=payload["custom_windows"],
            )

            query_for_run = payload["music_query"] or None
            if not query_for_run and final_edits_count > 1:
                rot_queries = [
                    "sem demora super slowed edit beat no copyright",
                    "super slowed phonk gaming edit beat instrumental",
                    "cinematic slowed synth edit beat royalty free",
                    "dark slowed bass gaming montage beat",
                ]
                query_for_run = rot_queries[(run_idx - 1) % len(rot_queries)]

            outputs = detect_reactions_and_clip(
                source_input=payload["source_input"],
                custom_output_dir=str(get_edits_dir()),
                is_youtube=True,
                clip_mode=backend_clip_mode,
                max_clips=effective_max_clips,
                max_good_cap=20,
                chunk_duration=payload["edit_length"],
                edit_duration=payload["edit_length"],
                fit_mode=payload["fit_mode"],
                stop_event=stop_event,
                on_progress=append_log,
                auto_edit=True,
                music_query=query_for_run,
                enable_captions=payload["enable_captions"],
                custom_windows=run_windows,
                clip_slice_sec=payload["clip_slice_sec"],
            )
            for out_p in (outputs or []):
                all_outputs.append(
                    {
                        "path": out_p,
                        "name": Path(out_p).name,
                        "url": f"/edits/{quote(Path(out_p).name)}",
                    }
                )

        with JOBS_LOCK:
            job = JOBS.get(job_id)
            if job is not None:
                job["outputs"] = all_outputs
                job["status"] = "stopped" if stop_event.is_set() else "completed"
    except Exception as exc:
        append_log(f"Error: {exc}")
        with JOBS_LOCK:
            job = JOBS.get(job_id)
            if job is not None:
                job["error"] = str(exc)
                job["status"] = "failed"


def _spawn_job(
    source_input: str,
    edit_length: float,
    max_clips: int,
    clip_slice_sec: float,
    fit_mode: str,
    music_query: str,
    enable_captions: bool,
    custom_windows: Optional[List[Tuple[float, float]]],
    label: str,
    final_edits_count: int = 1,
    clip_selection_mode: str = "all_best",
    initial_note: Optional[str] = None,
) -> str:
    job_id = uuid.uuid4().hex[:8]
    stop_event = threading.Event()
    mode_label = "All Best Clips" if clip_selection_mode == "all_best" else f"Limited ({max_clips})"
    logs = [
        f"Job {job_id} ({label}) queued: {final_edits_count} final edit(s) x {edit_length:.0f}s, "
        f"mode={mode_label}, slice={clip_slice_sec:.1f}s."
    ]
    if initial_note:
        logs.append(initial_note)

    payload = {
        "label": label,
        "source_input": source_input,
        "edit_length": edit_length,
        "max_clips": max_clips,
        "clip_slice_sec": clip_slice_sec,
        "final_edits_count": final_edits_count,
        "clip_selection_mode": clip_selection_mode,
        "fit_mode": fit_mode,
        "music_query": music_query,
        "enable_captions": enable_captions,
        "custom_windows": custom_windows,
        "stop_event": stop_event,
    }

    with JOBS_LOCK:
        JOBS[job_id] = {
            "job_id": job_id,
            "label": label,
            "source_input": source_input,
            "status": "running",
            "logs": logs,
            "outputs": [],
            "error": None,
            "stop_event": stop_event,
            "created_at": time.time(),
        }

    worker = threading.Thread(target=_run_edit_job, args=(job_id, payload), daemon=True)
    worker.start()
    return job_id


PAGE_HTML = r"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8" />
<meta name="viewport" content="width=device-width, initial-scale=1" />
<title>CLIPNTAX — 9:16 Vertical Pipeline</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800&family=JetBrains+Mono:wght@400;500&display=swap" rel="stylesheet">
<style>
:root{
  --bg:#05060a; --glass:rgba(16,19,30,.62); --glass-2:rgba(255,255,255,.04); --glass-3:rgba(255,255,255,.07);
  --line:rgba(255,255,255,.09); --line-2:rgba(255,255,255,.18);
  --text:#f4f6fb; --muted:#8b93a7; --p:#7c6cff; --p2:#4f8bff; --cy:#2de2e6; --em:#34d399; --rose:#fb4f7a; --amb:#fbbf24;
  --grad:linear-gradient(135deg,#7c6cff 0%,#4f8bff 55%,#2de2e6 100%);
  --r:20px; --rs:12px; --mono:"JetBrains Mono",ui-monospace,Menlo,Consolas,monospace;
}
*{box-sizing:border-box}
html{scroll-behavior:smooth;scroll-padding-top:90px}
body{margin:0;font-family:"Inter","Segoe UI",system-ui,sans-serif;background:var(--bg);color:var(--text);line-height:1.5;-webkit-font-smoothing:antialiased;overflow-x:hidden}
::selection{background:rgba(124,108,255,.45)}
::-webkit-scrollbar{width:10px;height:10px}::-webkit-scrollbar-thumb{background:rgba(255,255,255,.14);border-radius:10px;border:2px solid transparent;background-clip:padding-box}::-webkit-scrollbar-track{background:transparent}

.bg{position:fixed;inset:0;z-index:-3;overflow:hidden;background:var(--bg)}
.bg-photo{position:absolute;inset:-8% 0;background:
  linear-gradient(180deg,rgba(5,6,10,.55),rgba(5,6,10,.92) 70%),
  url("https://images.unsplash.com/photo-1550745165-9bc0b252726f?auto=format&fit=crop&w=2000&q=60") center/cover no-repeat;
  opacity:.55;will-change:transform;filter:saturate(1.2)}
.bg-aurora{position:absolute;inset:0;background:
  radial-gradient(700px 500px at 15% 10%,rgba(124,108,255,.28),transparent 60%),
  radial-gradient(700px 500px at 88% 18%,rgba(45,226,230,.18),transparent 60%),
  radial-gradient(800px 600px at 50% 105%,rgba(79,139,255,.20),transparent 60%);
  animation:drift 18s ease-in-out infinite alternate}
@keyframes drift{to{transform:scale(1.12) translate3d(-2%,2%,0)}}
.bg-grid{position:absolute;inset:0;z-index:-2;background-image:linear-gradient(var(--line) 1px,transparent 1px),linear-gradient(90deg,var(--line) 1px,transparent 1px);background-size:56px 56px;mask-image:radial-gradient(ellipse at 50% 0%,#000 0%,transparent 70%);-webkit-mask-image:radial-gradient(ellipse at 50% 0%,#000 0%,transparent 70%);opacity:.5}
.cursor-glow{position:fixed;width:520px;height:520px;border-radius:50%;pointer-events:none;z-index:-1;background:radial-gradient(circle,rgba(124,108,255,.16),transparent 65%);transform:translate(-50%,-50%);left:50%;top:30%;transition:left .25s ease-out,top .25s ease-out}

.progress{position:fixed;top:0;left:0;height:3px;width:0;background:var(--grad);z-index:100;box-shadow:0 0 14px rgba(124,108,255,.8)}
.nav{position:sticky;top:12px;z-index:50;max-width:1240px;margin:12px auto 0;padding:10px 14px 10px 18px;display:flex;align-items:center;justify-content:space-between;gap:14px;background:var(--glass);backdrop-filter:blur(18px) saturate(1.4);-webkit-backdrop-filter:blur(18px) saturate(1.4);border:1px solid var(--line);border-radius:999px;box-shadow:0 20px 50px -20px #000}
.logo{display:flex;align-items:center;gap:10px;font-weight:700;letter-spacing:-.02em;font-size:15px}
.logo i{width:28px;height:28px;border-radius:9px;background:var(--grad);display:grid;place-items:center;font-style:normal;font-size:14px;box-shadow:0 6px 20px -4px rgba(124,108,255,.7)}
.nav-links{display:flex;gap:4px}
.nav-links a{color:var(--muted);text-decoration:none;font-size:13px;font-weight:500;padding:7px 14px;border-radius:999px;transition:.25s}
.nav-links a:hover{color:var(--text);background:var(--glass-2)}
.nav-links a.active{color:#fff;background:var(--glass-3);box-shadow:inset 0 0 0 1px var(--line-2)}
.status-badge{font-size:12px;font-weight:500;padding:7px 14px;border-radius:999px;background:var(--glass-2);border:1px solid var(--line);color:#d4d9e6;display:flex;align-items:center;gap:8px;white-space:nowrap;max-width:260px;overflow:hidden;text-overflow:ellipsis}
.dot{width:7px;height:7px;border-radius:50%;background:var(--em);box-shadow:0 0 10px var(--em)}
.dot.run{background:var(--amb);box-shadow:0 0 10px var(--amb);animation:pulse 1s infinite}
@keyframes pulse{50%{opacity:.3;transform:scale(.7)}}
@media(max-width:900px){.nav-links{display:none}}

.wrap{max-width:1240px;margin:0 auto;padding:0 20px}
.hero{padding:90px 0 70px;text-align:center;position:relative}
.eyebrow{display:inline-flex;align-items:center;gap:8px;font-size:12px;font-weight:600;color:#c7c2ff;padding:6px 14px;border-radius:999px;background:rgba(124,108,255,.12);border:1px solid rgba(124,108,255,.35);letter-spacing:.04em;text-transform:uppercase}
.hero h1{font-size:clamp(38px,6.4vw,76px);line-height:1.02;letter-spacing:-.045em;margin:22px auto 18px;font-weight:800;max-width:900px}
.grad{background:var(--grad);-webkit-background-clip:text;background-clip:text;color:transparent;background-size:200% auto;animation:shine 6s linear infinite}
@keyframes shine{to{background-position:200% center}}
.hero p{color:var(--muted);font-size:clamp(15px,1.6vw,18px);max-width:640px;margin:0 auto 30px}
.hero-cta{display:flex;gap:12px;justify-content:center;flex-wrap:wrap}
.stats{display:flex;justify-content:center;gap:12px;flex-wrap:wrap;margin-top:44px}
.stat{padding:12px 20px;border-radius:14px;background:var(--glass);border:1px solid var(--line);backdrop-filter:blur(10px);min-width:130px}
.stat b{display:block;font-size:22px;letter-spacing:-.03em}.stat span{font-size:11.5px;color:var(--muted)}
.scroll-hint{margin:46px auto 0;width:24px;height:38px;border:2px solid var(--line-2);border-radius:14px;position:relative}
.scroll-hint::after{content:"";position:absolute;left:50%;top:7px;width:4px;height:8px;margin-left:-2px;border-radius:4px;background:var(--cy);animation:wheel 1.6s infinite}
@keyframes wheel{0%{opacity:1;transform:translateY(0)}100%{opacity:0;transform:translateY(14px)}}

.layout{display:grid;grid-template-columns:minmax(0,1.55fr) minmax(0,1fr);gap:22px;align-items:start}
@media(max-width:1000px){.layout{grid-template-columns:1fr}}
.stack{display:flex;flex-direction:column;gap:22px}
.sticky{position:sticky;top:92px}
@media(max-width:1000px){.sticky{position:static}}

.panel{position:relative;background:var(--glass);border:1px solid var(--line);border-radius:var(--r);padding:24px;backdrop-filter:blur(16px);-webkit-backdrop-filter:blur(16px);box-shadow:0 30px 60px -30px #000;overflow:hidden;transition:border-color .3s,transform .3s}
.panel::before{content:"";position:absolute;inset:0;pointer-events:none;background:radial-gradient(420px circle at var(--mx,50%) var(--my,0%),rgba(124,108,255,.13),transparent 60%);opacity:0;transition:opacity .3s}
.panel:hover{border-color:var(--line-2)}.panel:hover::before{opacity:1}
.panel-head{display:flex;justify-content:space-between;align-items:center;gap:12px;margin-bottom:18px}
.panel-head h2{margin:0;font-size:18px;letter-spacing:-.02em;font-weight:700}
.panel-head small{display:block;color:var(--muted);font-weight:400;font-size:12.5px;margin-top:2px;letter-spacing:0}
.step{font-family:var(--mono);font-size:11px;font-weight:500;color:var(--cy);background:rgba(45,226,230,.09);border:1px solid rgba(45,226,230,.25);padding:4px 10px;border-radius:999px}

label{display:block;font-size:12px;font-weight:600;color:#aab1c4;margin:16px 0 7px;letter-spacing:.01em}
label:first-of-type{margin-top:0}
input[type=text],input[type=number],textarea{width:100%;background:rgba(0,0,0,.28);border:1px solid var(--line);color:var(--text);border-radius:var(--rs);padding:11px 13px;font:inherit;font-size:13.5px;transition:.2s}
input::placeholder,textarea::placeholder{color:#566078}
input:focus,textarea:focus{outline:none;border-color:var(--p);box-shadow:0 0 0 4px rgba(124,108,255,.18);background:rgba(0,0,0,.4)}
textarea{min-height:90px;resize:vertical;font-family:var(--mono);font-size:12px}
input[readonly]{opacity:.65}
input[type=number]{-moz-appearance:textfield;text-align:center}
input[type=number]::-webkit-outer-spin-button,input[type=number]::-webkit-inner-spin-button{-webkit-appearance:none}
.row-2{display:grid;grid-template-columns:1fr 1fr;gap:14px}
@media(max-width:560px){.row-2{grid-template-columns:1fr}}

.stepper{display:flex;align-items:stretch;background:rgba(0,0,0,.28);border:1px solid var(--line);border-radius:var(--rs);overflow:hidden;transition:.2s}
.stepper:focus-within{border-color:var(--p);box-shadow:0 0 0 4px rgba(124,108,255,.18)}
.stepper input{border:0;background:transparent;box-shadow:none!important;border-radius:0;padding:11px 4px}
.stepper button{width:38px;border:0;background:transparent;color:var(--muted);font-size:17px;cursor:pointer;transition:.15s}
.stepper button:hover{background:var(--glass-3);color:#fff}.stepper button:active{background:rgba(124,108,255,.3)}

.seg{display:grid;grid-template-columns:repeat(3,1fr);gap:4px;padding:4px;background:rgba(0,0,0,.28);border:1px solid var(--line);border-radius:var(--rs);position:relative}
.seg.two-col{grid-template-columns:repeat(2,1fr)}
.seg button{position:relative;z-index:1;border:0;background:transparent;color:var(--muted);font:inherit;font-size:12.5px;font-weight:600;padding:9px 6px;border-radius:9px;cursor:pointer;transition:color .2s}
.seg button.on{color:#fff}
.seg .thumb{position:absolute;top:4px;bottom:4px;left:4px;width:calc((100% - 8px)/3);border-radius:9px;background:var(--grad);box-shadow:0 8px 20px -6px rgba(124,108,255,.7);transition:transform .35s cubic-bezier(.22,1,.36,1)}
.seg.two-col .thumb{width:calc((100% - 8px)/2)}

.switch{display:flex;align-items:center;justify-content:space-between;gap:12px;margin-top:18px;padding:13px 15px;border-radius:var(--rs);background:var(--glass-2);border:1px solid var(--line);cursor:pointer;font-size:13px;font-weight:500;color:#dfe3ee;margin-bottom:0}
.switch small{display:block;color:var(--muted);font-size:11.5px;font-weight:400}
.switch input{display:none}
.tg{width:42px;height:24px;border-radius:999px;background:#2a3042;position:relative;transition:.3s;flex:none}
.tg::after{content:"";position:absolute;top:3px;left:3px;width:18px;height:18px;border-radius:50%;background:#fff;transition:.3s cubic-bezier(.22,1,.36,1)}
.switch input:checked+.tg{background:var(--grad)}.switch input:checked+.tg::after{transform:translateX(18px)}

.cut-plan-box{margin-top:18px;border-radius:var(--rs);background:rgba(0,0,0,.25);border:1px solid var(--line);padding:14px}
.cut-plan-title{display:flex;justify-content:space-between;font-size:12px;font-weight:600;color:#cbd2e3;margin-bottom:10px}
.timeline{display:flex;height:10px;border-radius:99px;overflow:hidden;background:rgba(255,255,255,.05);gap:2px;margin-bottom:12px}
.timeline span{background:var(--grad);flex:1;opacity:.85;transition:.3s;animation:grow .6s cubic-bezier(.22,1,.36,1) both}
@keyframes grow{from{transform:scaleX(0)}}
.cut-list{display:flex;flex-direction:column;gap:6px;max-height:170px;overflow-y:auto}
.cut-chip{display:flex;justify-content:space-between;gap:10px;font-size:12px;padding:8px 11px;border-radius:9px;background:var(--glass-2);border:1px solid var(--line);animation:pop .35s both}
@keyframes pop{from{opacity:0;transform:translateY(6px)}}
.cut-chip .meta{color:var(--cy);font-family:var(--mono);font-size:11px;white-space:nowrap}

.toolbar{display:flex;flex-wrap:wrap;gap:8px}
.manual-rows{display:flex;flex-direction:column;gap:8px;margin-top:14px;max-height:230px;overflow-y:auto}
.m-row{display:grid;grid-template-columns:1fr 1fr 1.2fr auto;gap:8px;align-items:center;padding:8px;border-radius:12px;background:var(--glass-2);border:1px solid var(--line);animation:pop .35s both}
.m-row input{padding:8px 10px;font-size:12px}
@media(max-width:560px){.m-row{grid-template-columns:1fr 1fr}}

.btn{position:relative;overflow:hidden;border:1px solid transparent;border-radius:12px;padding:11px 18px;font:inherit;font-size:13.5px;font-weight:600;cursor:pointer;color:var(--text);transition:transform .2s cubic-bezier(.22,1,.36,1),box-shadow .25s,background .25s,border-color .25s,opacity .2s}
.btn:hover:not(:disabled){transform:translateY(-2px)}.btn:active:not(:disabled){transform:scale(.97)}
.btn:disabled{opacity:.4;cursor:not-allowed}
.btn-primary{background:var(--grad);color:#fff;box-shadow:0 12px 30px -10px rgba(124,108,255,.8)}
.btn-primary:hover:not(:disabled){box-shadow:0 18px 40px -10px rgba(124,108,255,.95)}
.btn-lg{padding:14px 26px;font-size:14.5px;border-radius:14px}
.btn-block{width:100%}
.btn-ghost{background:var(--glass-2);border-color:var(--line);color:#dfe3ee}
.btn-ghost:hover:not(:disabled){border-color:var(--line-2);background:var(--glass-3)}
.btn-cyan{background:rgba(45,226,230,.11);border-color:rgba(45,226,230,.35);color:#a8f6f8}
.btn-cyan:hover:not(:disabled){background:rgba(45,226,230,.2);box-shadow:0 12px 30px -12px rgba(45,226,230,.6)}
.btn-danger{background:rgba(251,79,122,.1);border-color:rgba(251,79,122,.3);color:#ffb3c6}
.btn-danger:hover:not(:disabled){background:rgba(251,79,122,.2)}
.btn-sm{padding:8px 13px;font-size:12px;border-radius:10px}
.ripple{position:absolute;border-radius:50%;background:rgba(255,255,255,.35);transform:scale(0);animation:rip .6s ease-out;pointer-events:none}
@keyframes rip{to{transform:scale(4);opacity:0}}

.actions{display:grid;grid-template-columns:1fr auto;gap:10px}
.bar{height:4px;border-radius:99px;background:rgba(255,255,255,.06);overflow:hidden;margin:16px 0 4px;opacity:0;transition:.3s}
.bar.on{opacity:1}.bar i{display:block;height:100%;width:40%;background:var(--grad);border-radius:99px;animation:slide 1.3s ease-in-out infinite}
@keyframes slide{0%{transform:translateX(-100%)}100%{transform:translateX(250%)}}
.log-console{background:#04050a;border:1px solid var(--line);border-radius:var(--rs);padding:14px;height:230px;overflow-y:auto;font-family:var(--mono);font-size:11.5px;color:#b9c1d6;white-space:pre-wrap;line-height:1.65}
.term-dots{display:flex;gap:6px;margin:16px 0 8px}.term-dots i{width:9px;height:9px;border-radius:50%;background:#2b3145}
.term-dots i:nth-child(1){background:#fb4f7a}.term-dots i:nth-child(2){background:#fbbf24}.term-dots i:nth-child(3){background:#34d399}
.jobs-title{font-size:12px;font-weight:600;color:#aab1c4;margin:18px 0 8px}
.active-jobs-list{display:flex;flex-direction:column;gap:7px;max-height:170px;overflow-y:auto}
.job-row{display:flex;justify-content:space-between;align-items:center;gap:10px;padding:10px 12px;border-radius:11px;background:var(--glass-2);border:1px solid var(--line);font-size:12px;cursor:pointer;transition:.2s}
.job-row:hover{border-color:var(--line-2)}.job-row.selected{border-color:var(--p);background:rgba(124,108,255,.1)}
.job-row .lbl{overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.job-state{font-size:10.5px;font-weight:700;text-transform:uppercase;letter-spacing:.05em;padding:3px 9px;border-radius:99px;background:rgba(45,226,230,.12);color:var(--cy);flex:none}
.job-state.completed{background:rgba(52,211,153,.14);color:var(--em)}.job-state.failed{background:rgba(251,79,122,.14);color:var(--rose)}.job-state.stopped{background:rgba(251,191,36,.14);color:var(--amb)}
.empty{color:var(--muted);font-size:12.5px;padding:6px 2px}

section.block{margin-top:90px}
.sec-head{display:flex;justify-content:space-between;align-items:end;gap:16px;margin-bottom:22px;flex-wrap:wrap}
.sec-head h2{margin:0;font-size:clamp(26px,3.4vw,38px);letter-spacing:-.035em;font-weight:800}
.sec-head p{margin:6px 0 0;color:var(--muted);font-size:14px}
.edits-grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(230px,1fr));gap:18px}
.edit-card{background:var(--glass);border:1px solid var(--line);border-radius:18px;padding:12px;transition:transform .35s cubic-bezier(.22,1,.36,1),border-color .3s,box-shadow .3s;backdrop-filter:blur(10px)}
.edit-card:hover{transform:translateY(-6px);border-color:rgba(124,108,255,.5);box-shadow:0 30px 50px -25px rgba(124,108,255,.45)}
.edit-card video{width:100%;aspect-ratio:9/16;border-radius:12px;background:#000;object-fit:cover;display:block}
.edit-name{margin-top:11px;font-size:12.5px;font-weight:600;word-break:break-word}
.edit-foot{margin-top:8px;display:flex;justify-content:space-between;align-items:center;font-size:12px;color:var(--muted)}
.edit-foot a{color:#fff;text-decoration:none;font-weight:600;padding:5px 11px;border-radius:99px;background:var(--glass-3);border:1px solid var(--line);transition:.2s}
.edit-foot a:hover{background:var(--grad);border-color:transparent}
footer{text-align:center;color:#4d566b;font-size:12px;padding:70px 0 40px}

.reveal{opacity:0;transform:translateY(34px) scale(.985);transition:opacity .8s cubic-bezier(.22,1,.36,1),transform .8s cubic-bezier(.22,1,.36,1)}
.reveal.is-visible{opacity:1;transform:none}
.toasts{position:fixed;right:20px;bottom:20px;display:flex;flex-direction:column;gap:10px;z-index:200}
.toast{padding:12px 16px;border-radius:12px;background:rgba(16,19,30,.92);border:1px solid var(--line-2);backdrop-filter:blur(14px);font-size:13px;box-shadow:0 20px 40px -15px #000;animation:tin .4s cubic-bezier(.22,1,.36,1);display:flex;gap:10px;align-items:center;max-width:340px}
.toast.err{border-color:rgba(251,79,122,.5)}.toast.ok{border-color:rgba(52,211,153,.5)}
@keyframes tin{from{opacity:0;transform:translateX(30px)}}
.to-top{position:fixed;left:20px;bottom:20px;width:44px;height:44px;border-radius:50%;border:1px solid var(--line-2);background:var(--glass);backdrop-filter:blur(12px);color:#fff;cursor:pointer;font-size:18px;opacity:0;pointer-events:none;transform:translateY(10px);transition:.3s;z-index:90}
.to-top.show{opacity:1;pointer-events:auto;transform:none}.to-top:hover{background:var(--grad)}
@media(prefers-reduced-motion:reduce){html{scroll-behavior:auto}*{animation:none!important;transition:none!important}.reveal{opacity:1;transform:none}}
</style>
</head>
<body>
<div class="progress" id="scrollProgress"></div>
<div class="bg"><div class="bg-photo" id="bgPhoto"></div><div class="bg-aurora"></div></div>
<div class="bg-grid"></div>
<div class="cursor-glow" id="cursorGlow"></div>

<nav class="nav">
  <div class="logo"><i>▶</i> CLIPNTAX</div>
  <div class="nav-links" id="navLinks">
    <a href="#top" class="active">Home</a>
    <a href="#sec-studio">Studio</a>
    <a href="#sec-manual">Manual</a>
    <a href="#sec-batch">Batch</a>
    <a href="#sec-gallery">Gallery</a>
  </div>
  <div class="status-badge"><span class="dot" id="statusDot"></span><span id="statusBadge">Ready</span></div>
</nav>

<main class="wrap" id="top">
  <header class="hero reveal">
    <span class="eyebrow">✦ 9:16 Vertical Pipeline</span>
    <h1>Turn any video into <span class="grad">scroll-stopping edits</span></h1>
    <p>Whole-video peak slicing, beat sync, spoken pop captions, manual clip control and multi-video batching — all in CLIPNTAX.</p>
    <div class="hero-cta">
      <a href="#sec-studio" style="text-decoration:none"><button type="button" class="btn btn-primary btn-lg">Start Editing →</button></a>
      <a href="#sec-gallery" style="text-decoration:none"><button type="button" class="btn btn-ghost btn-lg">View Saved Edits</button></a>
    </div>
    <div class="stats">
      <div class="stat"><b>{{ default_edit_length }}s</b><span>Default edit</span></div>
      <div class="stat"><b>9:16</b><span>Vertical format</span></div>
      <div class="stat"><b>12×</b><span>Parallel videos</span></div>
      <div class="stat"><b>/EDITS</b><span>Output folder</span></div>
    </div>
    <div class="scroll-hint"></div>
  </header>

  <form id="studioForm" onsubmit="handleGenerate(event)">
    <div class="layout" id="sec-studio">
      <div class="stack">

        <!-- 01 -->
        <section class="panel reveal">
          <div class="panel-head"><h2>Source &amp; Edit Settings<small>Paste a link and shape the edit</small></h2><span class="step">01</span></div>

          <label for="source_input">Video / Live Stream URL</label>
          <input type="text" id="source_input" name="source_input" placeholder="https://www.youtube.com/watch?v=... or live URL" />

          <div class="row-2" style="margin-top:16px">
            <div>
              <label for="final_edits_count">Exact Final Edits Wanted</label>
              <div class="stepper"><button type="button" onclick="stepNum('final_edits_count',-1)">−</button>
                <input type="number" id="final_edits_count" name="final_edits_count" min="1" max="20" step="1" value="1" oninput="rebuildCutPlan()" />
              <button type="button" onclick="stepNum('final_edits_count',1)">+</button></div>
            </div>
            <div>
              <label for="edit_duration">Each Final Edit Length (sec)</label>
              <div class="stepper"><button type="button" onclick="stepNum('edit_duration',-1)">−</button>
                <input type="number" id="edit_duration" name="edit_duration" min="4" max="120" step="1" value="{{ default_edit_length }}" oninput="rebuildCutPlan()" />
              <button type="button" onclick="stepNum('edit_duration',1)">+</button></div>
            </div>
          </div>

          <div class="row-2" style="margin-top:16px">
            <div>
              <label>Clip Pool Mode (All Best vs Limited)</label>
              <input type="hidden" id="clip_selection_mode" name="clip_selection_mode" value="all_best" />
              <div class="seg two-col" id="clipModeSeg">
                <div class="thumb" id="clipModeThumb"></div>
                <button type="button" data-v="all_best" class="on">All Best Clips</button>
                <button type="button" data-v="limited">Limited Clips</button>
              </div>
            </div>
            <div>
              <label for="max_clips">Limited Clips Count (when Limited)</label>
              <div class="stepper"><button type="button" onclick="stepNum('max_clips',-1)">−</button>
                <input type="number" id="max_clips" name="max_clips" min="1" max="20" step="1" value="3" oninput="rebuildCutPlan()" />
              <button type="button" onclick="stepNum('max_clips',1)">+</button></div>
            </div>
          </div>

          <div class="row-2" style="margin-top:16px">
            <div>
              <label for="clip_slice_sec">Each Cut Clip Length (sec)</label>
              <div class="stepper"><button type="button" onclick="stepNum('clip_slice_sec',-0.5)">−</button>
                <input type="number" id="clip_slice_sec" name="clip_slice_sec" min="2" max="60" step="0.5" value="6.5" oninput="rebuildCutPlan()" />
              <button type="button" onclick="stepNum('clip_slice_sec',0.5)">+</button></div>
            </div>
            <div>
              <label>Vertical Framing</label>
              <input type="hidden" id="fit_mode" name="fit_mode" value="blurred_bg" />
              <div class="seg" id="fitSeg">
                <div class="thumb" id="fitThumb"></div>
                <button type="button" data-v="blurred_bg" class="on">Blur BG</button>
                <button type="button" data-v="center_crop">Full Crop</button>
                <button type="button" data-v="letterbox">Letterbox</button>
              </div>
            </div>
          </div>

          <label for="music_query" style="margin-top:16px">Edit Music / Beat Query (Optional)</label>
          <input type="text" id="music_query" name="music_query" placeholder="Auto-rotating slowed / synth / phonk beat" />

          <label class="switch">
            <span>Spoken pop captions<small>Only actual speech is captioned</small></span>
            <input type="checkbox" id="enable_captions" name="enable_captions" checked /><span class="tg"></span>
          </label>

          <div class="cut-plan-box">
            <div class="cut-plan-title"><span>Cut Clips Breakdown</span><span id="planSummary" style="color:var(--cy)">1 edit • 20s • All Best Clips</span></div>
            <div class="timeline" id="timeline"></div>
            <div class="cut-list" id="cutListPreview"></div>
          </div>
        </section>

        <!-- 02 -->
        <section class="panel reveal" id="sec-manual">
          <div class="panel-head"><h2>Manual Clip Editor<small>Pin exact moments instead of auto-detect</small></h2><span class="step">02</span></div>
          <div class="toolbar">
            <button type="button" class="btn btn-ghost btn-sm" onclick="addManualRow()">＋ Add Manual Clip</button>
            <button type="button" class="btn btn-ghost btn-sm" onclick="syncManualToCutPlan()">⟳ Sync Cuts</button>
            <button type="button" class="btn btn-danger btn-sm" onclick="clearManualRows()">Clear All</button>
          </div>
          <div class="manual-rows" id="manualRows"></div>
          <input type="hidden" id="manual_clips_json" name="manual_clips_json" />

          <div class="row-2" style="margin-top:18px">
            <div><label for="manual_start">Quick Start (sec / MM:SS)</label>
              <input type="text" id="manual_start" name="manual_start" placeholder="e.g. 30 or 01:10" oninput="rebuildCutPlan()" /></div>
            <div><label for="manual_duration">Quick Length (sec)</label>
              <input type="text" id="manual_duration" name="manual_duration" placeholder="e.g. 8" oninput="rebuildCutPlan()" /></div>
          </div>
          <label for="manual_windows_lines" style="margin-top:16px">Batch Clip Windows (start, dur or MM:SS - MM:SS)</label>
          <textarea id="manual_windows_lines" name="manual_windows_lines" placeholder="15, 7&#10;42, 6.5&#10;01:20 - 01:28" oninput="rebuildCutPlan()"></textarea>
        </section>

        <!-- 03 -->
        <section class="panel reveal" id="sec-batch">
          <div class="panel-head"><h2>Multi-Video Simultaneous Edits<small>Queue up to 12 videos at once</small></h2><span class="step">03</span></div>
          <label for="multi_video_urls">Video / Live URLs (one per line)</label>
          <textarea id="multi_video_urls" name="multi_video_urls" style="min-height:120px" placeholder="https://www.youtube.com/watch?v=VIDEO_1&#10;https://www.youtube.com/watch?v=VIDEO_2&#10;https://www.youtube.com/watch?v=VIDEO_3" oninput="updateBatchSummary()"></textarea>
          <div class="row-2" style="margin-top:16px">
            <div>
              <label for="edits_per_video">Final Edits Per Video</label>
              <div class="stepper"><button type="button" onclick="stepNum('edits_per_video',-1)">−</button>
                <input type="number" id="edits_per_video" name="edits_per_video" min="1" max="10" step="1" value="1" oninput="syncFromBatchPerVideo()" />
              <button type="button" onclick="stepNum('edits_per_video',1)">+</button></div>
            </div>
            <div><label for="batch_edit_len_mirror">Edit Length (from 01)</label>
              <input type="text" id="batch_edit_len_mirror" value="{{ default_edit_length }}s" readonly /></div>
          </div>
          <div id="batchSummaryNote" style="margin-top:12px;font-size:12px;color:var(--cy);font-family:var(--mono)">0 URLs × 1 edit/video = 0 total edits</div>
          <button type="button" class="btn btn-cyan btn-block" id="batchBtn" style="margin-top:14px" onclick="handleBatchGenerate()">⚡ Launch Exact Final Edits</button>
        </section>
      </div>

      <!-- 04 -->
      <aside class="sticky">
        <section class="panel reveal" id="sec-render">
          <div class="panel-head"><h2>Render &amp; Progress<small>Live pipeline activity</small></h2><span class="step">04</span></div>
          <div class="actions">
            <button type="submit" class="btn btn-primary btn-lg" id="generateBtn">Generate Final Edit(s)</button>
            <button type="button" class="btn btn-danger btn-lg" id="stopBtn" onclick="stopJob()" disabled>Stop</button>
          </div>
          <div class="bar" id="runBar"><i></i></div>
          <div class="term-dots"><i></i><i></i><i></i></div>
          <div class="log-console" id="logConsole">Choose All Best Clips or Limited Clips, set Exact Final Edits Wanted, and click Generate.</div>
          <div class="jobs-title">Active Jobs</div>
          <div class="active-jobs-list" id="activeJobsList"><div class="empty">No jobs yet.</div></div>
        </section>
      </aside>
    </div>
  </form>

  <section class="block reveal" id="sec-gallery">
    <div class="sec-head">
      <div><h2>Saved Edits</h2><p>Everything rendered to /EDITS — hover a card to preview.</p></div>
      <button type="button" class="btn btn-ghost" onclick="refreshSavedEdits(true)">↻ Refresh Gallery</button>
    </div>
    <div class="edits-grid" id="savedEditsGrid">
      {% for edit in saved_edits %}
      <article class="edit-card">
        <video src="{{ edit.url }}" controls preload="metadata" muted playsinline></video>
        <div class="edit-name">{{ edit.name }}</div>
        <div class="edit-foot"><span>{{ edit.size_mb }} MB</span><a href="{{ edit.url }}" download>Download</a></div>
      </article>
      {% else %}
      <div class="empty">No edits saved in /EDITS yet.</div>
      {% endfor %}
    </div>
  </section>
  <footer>CLIPNTAX · 9:16 Vertical Pipeline</footer>
</main>

<button class="to-top" id="toTop" onclick="window.scrollTo({top:0,behavior:'smooth'})" aria-label="Back to top">↑</button>
<div class="toasts" id="toasts"></div>

<script>
let watchedJobIds = [];
let selectedJobId = null;
let pollInterval = null;

const $ = id => document.getElementById(id);
const esc = s => String(s ?? "").replace(/[&<>"']/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));

function toast(msg, kind = "") {
  const t = document.createElement("div");
  t.className = "toast " + kind;
  t.textContent = msg;
  $("toasts").appendChild(t);
  setTimeout(() => { t.style.transition = ".4s"; t.style.opacity = 0; t.style.transform = "translateX(30px)"; setTimeout(() => t.remove(), 400); }, 3200);
}
function setStatus(txt, running) {
  $("statusBadge").textContent = txt;
  $("statusDot").classList.toggle("run", !!running);
}

function parseTimeSec(val) {
  if (val === null || val === undefined) return null;
  const s = String(val).trim();
  if (!s) return null;
  if (s.includes(":")) {
    const parts = s.split(":").map(p => parseFloat(p.trim()));
    if (parts.some(n => Number.isNaN(n))) return null;
    if (parts.length === 2) return +(parts[0] * 60 + parts[1]).toFixed(2);
    if (parts.length === 3) return +(parts[0] * 3600 + parts[1] * 60 + parts[2]).toFixed(2);
    return null;
  }
  const num = parseFloat(s);
  return Number.isNaN(num) ? null : +num.toFixed(2);
}

function stepNum(id, delta) {
  const el = $(id);
  const min = parseFloat(el.min), max = parseFloat(el.max);
  let v = (parseFloat(el.value) || 0) + delta;
  if (!Number.isNaN(min)) v = Math.max(min, v);
  if (!Number.isNaN(max)) v = Math.min(max, v);
  el.value = +v.toFixed(2);
  el.dispatchEvent(new Event("input", { bubbles: true }));
}

function initSeg() {
  const fitBtns = [...document.querySelectorAll("#fitSeg button")];
  fitBtns.forEach((b, i) => b.addEventListener("click", () => {
    fitBtns.forEach(x => x.classList.remove("on"));
    b.classList.add("on");
    $("fit_mode").value = b.dataset.v;
    $("fitThumb").style.transform = `translateX(${i * 100}%)`;
  }));

  const modeBtns = [...document.querySelectorAll("#clipModeSeg button")];
  modeBtns.forEach((b, i) => b.addEventListener("click", () => {
    modeBtns.forEach(x => x.classList.remove("on"));
    b.classList.add("on");
    $("clip_selection_mode").value = b.dataset.v;
    $("clipModeThumb").style.transform = `translateX(${i * 100}%)`;
    rebuildCutPlan();
  }));
}

function addManualRow(startVal = "", durVal = "", labelVal = "") {
  const container = $("manualRows");
  const idx = container.children.length + 1;
  const row = document.createElement("div");
  row.className = "m-row";
  row.innerHTML = `
    <input type="text" class="m-start" placeholder="Start (e.g. 12)" value="${esc(startVal)}" oninput="rebuildCutPlan()" />
    <input type="text" class="m-dur" placeholder="Len (e.g. 6)" value="${esc(durVal)}" oninput="rebuildCutPlan()" />
    <input type="text" class="m-label" placeholder="Clip ${idx}" value="${esc(labelVal)}" oninput="rebuildCutPlan()" />
    <button type="button" class="btn btn-danger btn-sm" onclick="removeManualRow(this)">✕</button>`;
  container.appendChild(row);
  rebuildCutPlan();
}
function removeManualRow(btn) {
  const row = btn.closest(".m-row");
  if (row) row.remove();
  rebuildCutPlan();
}
function clearManualRows() {
  $("manualRows").innerHTML = "";
  $("manual_start").value = "";
  $("manual_duration").value = "";
  $("manual_windows_lines").value = "";
  rebuildCutPlan();
}

function collectManualClips() {
  const sliceLen = Math.max(2, Math.min(60, parseFloat($("clip_slice_sec")?.value) || 6.5));
  const clips = [];
  document.querySelectorAll("#manualRows .m-row").forEach((row, i) => {
    const st = parseTimeSec(row.querySelector(".m-start")?.value);
    const dur = parseTimeSec(row.querySelector(".m-dur")?.value);
    const lbl = (row.querySelector(".m-label")?.value || "").trim() || `Manual Clip ${i + 1}`;
    if (st !== null && st >= 0) {
      const cleanDur = (dur !== null && dur >= 2) ? dur : sliceLen;
      clips.push({ start: st, duration: cleanDur, end: +(st + cleanDur).toFixed(2), label: lbl });
    }
  });

  const lines = ($("manual_windows_lines")?.value || "").split("\n");
  lines.forEach((raw, idx) => {
    const ln = raw.trim();
    if (!ln) return;
    if (ln.includes("-") && !ln.includes(",")) {
      const [a, b] = ln.split("-", 2).map(x => x.trim());
      const st = parseTimeSec(a);
      const en = parseTimeSec(b);
      if (st !== null && en !== null && en > st) {
        const d = Math.max(2, +(en - st).toFixed(2));
        clips.push({ start: st, duration: d, end: +(st + d).toFixed(2), label: `Range ${idx + 1}` });
      }
      return;
    }
    const parts = ln.replace(/;/g, ",").replace(/\s+/g, ",").split(",").map(x => x.trim()).filter(Boolean);
    if (parts.length >= 2) {
      const st = parseTimeSec(parts[0]);
      const dur = parseTimeSec(parts[1]);
      if (st !== null && dur !== null && dur >= 2) {
        clips.push({ start: st, duration: dur, end: +(st + dur).toFixed(2), label: `Batch ${idx + 1}` });
      }
    }
  });

  const qStart = parseTimeSec($("manual_start")?.value);
  const qDur = parseTimeSec($("manual_duration")?.value);
  if (qStart !== null && qStart >= 0) {
    const d = (qDur !== null && qDur >= 2) ? qDur : sliceLen;
    clips.push({ start: qStart, duration: d, end: +(qStart + d).toFixed(2), label: "Quick Clip" });
  }

  $("manual_clips_json").value = JSON.stringify(clips);
  return clips;
}

function syncManualToCutPlan() {
  const mode = $("clip_selection_mode").value;
  const editLen = Math.max(4, Math.min(120, parseFloat($("edit_duration").value) || 20));
  const clipCount = mode === "all_best"
    ? Math.max(4, Math.min(12, Math.ceil(editLen / 3.5)))
    : Math.max(1, Math.min(20, parseInt($("max_clips").value, 10) || 3));
  const sliceLen = Math.max(2, Math.min(60, parseFloat($("clip_slice_sec").value) || 6.5));
  $("manualRows").innerHTML = "";
  for (let i = 0; i < clipCount; i++) {
    addManualRow(+(i * (sliceLen + 8)).toFixed(1), sliceLen, `Cut ${i + 1}`);
  }
  rebuildCutPlan();
  toast(`Synced ${clipCount} cut(s) to manual editor`, "ok");
}

function syncFromBatchPerVideo() {
  const perVid = Math.max(1, Math.min(10, parseInt($("edits_per_video")?.value, 10) || 1));
  if ($("final_edits_count")) {
    $("final_edits_count").value = perVid;
  }
  updateBatchSummary();
  rebuildCutPlan();
}

function updateBatchSummary() {
  const editLen = Math.max(4, Math.min(120, parseFloat($("edit_duration").value) || 20));
  const perVid = Math.max(1, Math.min(10, parseInt($("edits_per_video")?.value, 10) || 1));
  const urls = ($("multi_video_urls")?.value || "").split("\n").map(u => u.trim()).filter(Boolean);
  const mirror = $("batch_edit_len_mirror");
  if (mirror) mirror.value = `${editLen.toFixed(0)}s`;
  const note = $("batchSummaryNote");
  if (note) note.textContent = `${urls.length} URL(s) × ${perVid} final edit(s)/video = ${urls.length * perVid} total final edits`;
}

function rebuildCutPlan() {
  const editLen = Math.max(4, Math.min(120, parseFloat($("edit_duration").value) || 20));
  const finalCount = Math.max(1, Math.min(20, parseInt($("final_edits_count")?.value, 10) || 1));
  const mode = $("clip_selection_mode")?.value || "all_best";
  const limCount = Math.max(1, Math.min(20, parseInt($("max_clips").value, 10) || 3));
  const sliceLen = Math.max(2, Math.min(60, parseFloat($("clip_slice_sec").value) || 6.5));
  const manualClips = collectManualClips();
  const listEl = $("cutListPreview");
  const summaryEl = $("planSummary");
  const tl = $("timeline");

  if (manualClips.length > 0) {
    const activeManual = mode === "limited" ? manualClips.slice(0, limCount) : manualClips;
    summaryEl.textContent = `${finalCount} edit(s) • ${editLen.toFixed(0)}s • ${activeManual.length} manual cuts`;
    listEl.innerHTML = activeManual.map((c, idx) => `
      <div class="cut-chip"><span>${idx + 1}. ${esc(c.label)}</span>
      <span class="meta">${c.start}s → ${c.end}s (${c.duration}s)</span></div>`).join("");
    tl.innerHTML = activeManual.map(c => `<span style="flex:${c.duration}"></span>`).join("");
  } else if (mode === "all_best") {
    const estSlices = Math.max(4, Math.min(10, Math.round(editLen / Math.max(2.5, Math.min(sliceLen, 4.5)))));
    summaryEl.textContent = `${finalCount} edit(s) • ${editLen.toFixed(0)}s • All Best Clips (full video)`;
    let html = "";
    for (let i = 0; i < estSlices; i++) {
      html += `<div class="cut-chip"><span>Full-Video Peak #${i + 1}</span><span class="meta">~${sliceLen}s source • varied scene</span></div>`;
    }
    listEl.innerHTML = html;
    tl.innerHTML = Array.from({ length: estSlices }, () => `<span></span>`).join("");
  } else {
    const perCut = Math.max(2, Math.min(sliceLen, +(editLen / limCount).toFixed(1)));
    summaryEl.textContent = `${finalCount} edit(s) • ${editLen.toFixed(0)}s • Limited (${limCount} clips)`;
    let html = "";
    for (let i = 0; i < limCount; i++) {
      html += `<div class="cut-chip"><span>Limited Clip #${i + 1}</span><span class="meta">~${perCut}s segment</span></div>`;
    }
    listEl.innerHTML = html;
    tl.innerHTML = Array.from({ length: limCount }, () => `<span></span>`).join("");
  }
  updateBatchSummary();
}

function startPolling() {
  if (pollInterval) clearInterval(pollInterval);
  pollInterval = setInterval(pollAllJobs, 1300);
  pollAllJobs();
}

async function handleGenerate(ev) {
  ev.preventDefault();
  collectManualClips();
  const fd = new FormData($("studioForm"));
  if (!String(fd.get("source_input") || "").trim()) {
    setStatus("Enter a video URL first");
    toast("Enter a video URL first", "err");
    $("source_input").focus();
    return;
  }
  $("generateBtn").disabled = true;
  $("stopBtn").disabled = false;
  setStatus("Starting...", true);
  try {
    const res = await fetch("/api/generate", { method: "POST", body: fd });
    const data = await res.json();
    if (!res.ok) {
      setStatus(data.error || "Error");
      toast(data.error || "Error", "err");
      $("generateBtn").disabled = false;
      return;
    }
    watchedJobIds = Array.from(new Set([...watchedJobIds, data.job_id]));
    selectedJobId = data.job_id;
    toast("Render started", "ok");
    startPolling();
  } catch (e) {
    setStatus("Network error");
    toast("Network error", "err");
    $("generateBtn").disabled = false;
  }
}

async function handleBatchGenerate() {
  collectManualClips();
  const urls = ($("multi_video_urls").value || "").split("\n").map(u => u.trim()).filter(Boolean);
  if (!urls.length) {
    setStatus("Paste at least 1 URL in Multi-Video Edits");
    toast("Paste at least 1 URL in Multi-Video Edits", "err");
    return;
  }
  const fd = new FormData($("studioForm"));
  fd.set("urls_list", JSON.stringify(urls));
  $("batchBtn").disabled = true;
  $("stopBtn").disabled = false;
  setStatus(`Launching ${urls.length} video job(s)...`, true);
  try {
    const res = await fetch("/api/generate-batch", { method: "POST", body: fd });
    const data = await res.json();
    $("batchBtn").disabled = false;
    if (!res.ok) {
      setStatus(data.error || "Batch error");
      toast(data.error || "Batch error", "err");
      return;
    }
    const created = (data.jobs || []).map(j => j.job_id);
    watchedJobIds = Array.from(new Set([...watchedJobIds, ...created]));
    if (created.length > 0) selectedJobId = created[0];
    toast(`Launched ${created.length} job(s)`, "ok");
    $("sec-render").scrollIntoView({ behavior: "smooth", block: "center" });
    startPolling();
  } catch (e) {
    $("batchBtn").disabled = false;
    setStatus("Network error");
    toast("Network error", "err");
  }
}

async function stopJob() {
  if (!selectedJobId) return;
  await fetch(`/api/stop/${selectedJobId}`, { method: "POST" });
  setStatus("Stopping...", true);
  toast("Stop requested");
}

async function pollAllJobs() {
  if (!watchedJobIds.length) return;
  const results = await Promise.all(watchedJobIds.map(async id => {
    try { const r = await fetch(`/api/status/${id}`); return r.ok ? r.json() : null; } catch (e) { return null; }
  }));
  const valid = results.filter(Boolean);
  if (!valid.length) return;

  $("activeJobsList").innerHTML = valid.map(j => `
    <div class="job-row ${j.job_id === selectedJobId ? "selected" : ""}" onclick="selectJob('${esc(j.job_id)}')">
      <span class="lbl">${esc(j.label || j.job_id)}</span>
      <span class="job-state ${esc(j.status)}">${esc(j.status)}</span>
    </div>`).join("");

  const activeJob = valid.find(j => j.job_id === selectedJobId) || valid[valid.length - 1];
  if (activeJob) {
    selectedJobId = activeJob.job_id;
    const logEl = $("logConsole");
    const stick = logEl.scrollTop + logEl.clientHeight >= logEl.scrollHeight - 30;
    logEl.textContent = (activeJob.logs || []).join("\n");
    if (stick) logEl.scrollTop = logEl.scrollHeight;
  }

  const runningCount = valid.filter(j => j.status === "running").length;
  const anyRunning = runningCount > 0;
  $("generateBtn").disabled = anyRunning;
  $("stopBtn").disabled = !anyRunning;
  $("runBar").classList.toggle("on", anyRunning);
  setStatus(anyRunning ? `Running (${runningCount} active)` : "Completed", anyRunning);

  if (!anyRunning) {
    clearInterval(pollInterval);
    pollInterval = null;
    toast("All jobs finished", "ok");
    refreshSavedEdits();
  }
}
function selectJob(id) { selectedJobId = id; pollAllJobs(); }

function bindCardHover() {
  document.querySelectorAll(".edit-card video").forEach(v => {
    v.muted = true;
    v.onmouseenter = () => { v.play().catch(() => {}); };
    v.onmouseleave = () => { if (!v.controls || document.activeElement !== v) { v.pause(); } };
  });
}
async function refreshSavedEdits(manual) {
  const res = await fetch("/api/edits");
  if (!res.ok) return;
  const data = await res.json();
  const grid = $("savedEditsGrid");
  const edits = data.edits || [];
  if (!edits.length) {
    grid.innerHTML = '<div class="empty">No edits saved in /EDITS yet.</div>';
    return;
  }
  grid.innerHTML = edits.map(e => `
    <article class="edit-card">
      <video src="${esc(e.url)}" controls preload="metadata" muted playsinline></video>
      <div class="edit-name">${esc(e.name)}</div>
      <div class="edit-foot"><span>${esc(e.size_mb)} MB</span><a href="${esc(e.url)}" download>Download</a></div>
    </article>`).join("");
  bindCardHover();
  if (manual === true) toast(`${edits.length} edit(s) loaded`, "ok");
}

function initScrollFx() {
  const prog = $("scrollProgress"), photo = $("bgPhoto"), top = $("toTop");
  let ticking = false;
  const onScroll = () => {
    const y = window.scrollY, h = document.documentElement.scrollHeight - innerHeight;
    prog.style.width = (h > 0 ? (y / h) * 100 : 0) + "%";
    photo.style.transform = `translate3d(0,${y * -0.12}px,0)`;
    top.classList.toggle("show", y > 600);
    ticking = false;
  };
  addEventListener("scroll", () => { if (!ticking) { ticking = true; requestAnimationFrame(onScroll); } }, { passive: true });
  onScroll();

  const els = document.querySelectorAll(".reveal");
  if (!("IntersectionObserver" in window)) { els.forEach(e => e.classList.add("is-visible")); }
  else {
    const obs = new IntersectionObserver(es => es.forEach(en => {
      if (en.isIntersecting) { en.target.classList.add("is-visible"); obs.unobserve(en.target); }
    }), { threshold: 0.06, rootMargin: "0px 0px -30px 0px" });
    els.forEach(e => obs.observe(e));
  }

  const links = [...document.querySelectorAll("#navLinks a")];
  const map = {};
  links.forEach(a => { const t = document.querySelector(a.getAttribute("href")); if (t) map[a.getAttribute("href")] = t; });
  if ("IntersectionObserver" in window) {
    const so = new IntersectionObserver(es => es.forEach(en => {
      if (en.isIntersecting) {
        links.forEach(a => a.classList.toggle("active", map[a.getAttribute("href")] === en.target));
      }
    }), { rootMargin: "-40% 0px -50% 0px" });
    Object.values(map).forEach(t => so.observe(t));
  }

  const glow = $("cursorGlow");
  addEventListener("mousemove", e => { glow.style.left = e.clientX + "px"; glow.style.top = e.clientY + "px"; }, { passive: true });

  document.querySelectorAll(".panel").forEach(p => p.addEventListener("mousemove", e => {
    const r = p.getBoundingClientRect();
    p.style.setProperty("--mx", (e.clientX - r.left) + "px");
    p.style.setProperty("--my", (e.clientY - r.top) + "px");
  }));
}

document.addEventListener("click", e => {
  const b = e.target.closest(".btn");
  if (!b || b.disabled) return;
  const r = b.getBoundingClientRect(), size = Math.max(r.width, r.height);
  const s = document.createElement("span");
  s.className = "ripple";
  s.style.cssText = `width:${size}px;height:${size}px;left:${e.clientX - r.left - size / 2}px;top:${e.clientY - r.top - size / 2}px`;
  b.appendChild(s);
  setTimeout(() => s.remove(), 600);
});

addManualRow("", "", "Clip 1");
rebuildCutPlan();
initSeg();
bindCardHover();
initScrollFx();
</script>
</body>
</html>
"""


def _extract_common_settings(form_data) -> Tuple[float, int, float, str, str, bool, str, int]:
    raw_edit_len = (
        form_data.get("edit_duration")
        or form_data.get("chunk_duration")
        or ""
    ).strip()
    try:
        edit_length = float(raw_edit_len) if raw_edit_len else float(DEFAULT_EDIT_LENGTH)
    except ValueError:
        edit_length = float(DEFAULT_EDIT_LENGTH)
    edit_length = max(4.0, min(120.0, edit_length))

    try:
        max_clips = max(1, min(20, int(form_data.get("max_clips") or 3)))
    except ValueError:
        max_clips = 3

    try:
        clip_slice_sec = max(2.0, min(60.0, float(form_data.get("clip_slice_sec") or 6.5)))
    except ValueError:
        clip_slice_sec = 6.5

    fit_mode = (form_data.get("fit_mode") or "blurred_bg").strip()
    music_query = (form_data.get("music_query") or "").strip()
    enable_captions = form_data.get("enable_captions") is not None

    raw_mode = (form_data.get("clip_selection_mode") or "all_best").strip().lower()
    clip_selection_mode = "limited" if raw_mode == "limited" else "all_best"

    try:
        final_edits_count = max(1, min(20, int(form_data.get("final_edits_count") or 1)))
    except ValueError:
        final_edits_count = 1

    return (
        edit_length,
        max_clips,
        clip_slice_sec,
        fit_mode,
        music_query,
        enable_captions,
        clip_selection_mode,
        final_edits_count,
    )


@app.route("/", methods=["GET"])
def index() -> str:
    return render_template_string(
        PAGE_HTML,
        default_edit_length=int(DEFAULT_EDIT_LENGTH),
        saved_edits=_list_saved_edits(),
    )


@app.route("/api/generate", methods=["POST"])
def api_generate():
    source_input = (request.form.get("source_input") or "").strip()
    if not source_input:
        return jsonify({"error": "Please enter a video or live stream URL."}), 400

    (
        edit_length,
        max_clips,
        clip_slice_sec,
        fit_mode,
        music_query,
        enable_captions,
        clip_selection_mode,
        final_edits_count,
    ) = _extract_common_settings(request.form)

    custom_windows, manual_records = _parse_manual_editor_payload(
        manual_clips_json_raw=request.form.get("manual_clips_json") or "",
        manual_start_raw=request.form.get("manual_start") or "",
        manual_dur_raw=request.form.get("manual_duration") or "",
        manual_end_raw=request.form.get("manual_end") or "",
        manual_lines_raw=request.form.get("manual_windows_lines") or "",
        default_dur=edit_length,
        default_slice_sec=clip_slice_sec,
    )
    note = f"Loaded {len(manual_records)} manual clip window(s)." if manual_records else None
    job_id = _spawn_job(
        source_input=source_input,
        edit_length=edit_length,
        max_clips=max_clips,
        clip_slice_sec=clip_slice_sec,
        fit_mode=fit_mode,
        music_query=music_query,
        enable_captions=enable_captions,
        custom_windows=custom_windows,
        label=f"Single Video ({final_edits_count} edit(s))",
        final_edits_count=final_edits_count,
        clip_selection_mode=clip_selection_mode,
        initial_note=note,
    )
    return jsonify({"job_id": job_id, "status": "running", "final_edits_count": final_edits_count})


@app.route("/api/generate-batch", methods=["POST"])
def api_generate_batch():
    raw_list = request.form.get("urls_list") or ""
    urls: List[str] = []
    if raw_list.strip():
        try:
            parsed = json.loads(raw_list)
            if isinstance(parsed, list):
                urls = [str(u).strip() for u in parsed if str(u).strip()]
        except Exception:
            pass
    if not urls:
        urls = [ln.strip() for ln in (request.form.get("multi_video_urls") or "").splitlines() if ln.strip()]
    if not urls:
        return jsonify({"error": "Provide at least one video URL for simultaneous edits."}), 400

    (
        edit_length,
        max_clips,
        clip_slice_sec,
        fit_mode,
        music_query,
        enable_captions,
        clip_selection_mode,
        final_edits_count,
    ) = _extract_common_settings(request.form)

    try:
        edits_per_video = max(1, min(10, int(request.form.get("edits_per_video") or final_edits_count)))
    except ValueError:
        edits_per_video = final_edits_count

    custom_windows, _ = _parse_manual_editor_payload(
        manual_clips_json_raw=request.form.get("manual_clips_json") or "",
        manual_start_raw=request.form.get("manual_start") or "",
        manual_dur_raw=request.form.get("manual_duration") or "",
        manual_end_raw=request.form.get("manual_end") or "",
        manual_lines_raw=request.form.get("manual_windows_lines") or "",
        default_dur=edit_length,
        default_slice_sec=clip_slice_sec,
    )

    spawned = []
    for idx, url in enumerate(urls[:12], start=1):
        lbl = f"Video #{idx} (x{edits_per_video}): {url[:28]}"
        jid = _spawn_job(
            source_input=url,
            edit_length=edit_length,
            max_clips=max_clips,
            clip_slice_sec=clip_slice_sec,
            fit_mode=fit_mode,
            music_query=music_query,
            enable_captions=enable_captions,
            custom_windows=custom_windows,
            label=lbl,
            final_edits_count=edits_per_video,
            clip_selection_mode=clip_selection_mode,
        )
        spawned.append({"job_id": jid, "label": lbl, "status": "running"})

    return jsonify(
        {
            "jobs": spawned,
            "count": len(spawned),
            "edits_per_video": edits_per_video,
            "total_final_edits": len(spawned) * edits_per_video,
        }
    )


@app.route("/api/status/<job_id>", methods=["GET"])
def api_status(job_id: str):
    with JOBS_LOCK:
        job = JOBS.get(job_id)
        if not job:
            return jsonify({"error": "Job not found."}), 404
        return jsonify(
            {
                "job_id": job_id,
                "label": job["label"],
                "status": job["status"],
                "logs": list(job["logs"]),
                "outputs": list(job["outputs"]),
                "error": job["error"],
            }
        )


@app.route("/api/stop/<job_id>", methods=["POST"])
def api_stop(job_id: str):
    with JOBS_LOCK:
        job = JOBS.get(job_id)
        if not job:
            return jsonify({"error": "Job not found."}), 404
        ev: Optional[threading.Event] = job.get("stop_event")
        if ev:
            ev.set()
        return jsonify({"job_id": job_id, "stopping": True})


@app.route("/api/edits", methods=["GET"])
def api_edits():
    return jsonify({"edits": _list_saved_edits()})


@app.route("/edits/<path:filename>", methods=["GET"])
def serve_edit_file(filename: str):
    edits_dir = get_edits_dir()
    target = (edits_dir / filename).resolve()
    if not target.is_file() or edits_dir.resolve() not in target.parents:
        return jsonify({"error": "File not found."}), 404
    return send_from_directory(str(edits_dir), target.name, as_attachment=False)


if __name__ == "__main__":
    get_edits_dir()
    host = os.environ.get("HOST", "0.0.0.0")
    port = int(os.environ.get("PORT", "5000"))
    app.run(host=host, port=port, debug=False, threaded=True)
import array
import json
import math
import random
import re
import shutil
import statistics
import subprocess
import sys
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Callable, Dict, Iterable, List, Optional, Set, Tuple


DEFAULT_COOKIES_PATH = Path("cookies.txt")
BEST_FORMAT_SELECTOR = "bv*[ext=mp4]+ba[ext=m4a]/bv*+ba/b[ext=mp4]/best"
EDITS_FOLDER_NAME = "EDITS"
FINAL_EDITS_FOLDER_NAME = "FINAL_EDITS"
DEFAULT_EDIT_LENGTH = 20.0
DEFAULT_EDIT_STYLE = "gaming_pro"
EDIT_STYLE_OPTIONS = ("gaming_pro", "davinci_pro", "shorts_hype", "beat_montage", "auto_ultimate")
BASE_STYLES = ("gaming_pro", "davinci_pro", "shorts_hype", "beat_montage")
MIN_SOURCE_CLIP_SEC = 5.0
MAX_SOURCE_CLIP_SEC = 10.0
PEAK_CLIP_DURATION = 10.0
OUT_W, OUT_H, OUT_FPS = 1080, 1920, 30
SHOT_WORKERS = 3

RUN_COUNTER_LOCK = threading.Lock()
GLOBAL_RUN_COUNTER = 0

MUSIC_QUERIES_POOL = [
    "sem demora super slowed edit beat no copyright",
    "super slowed phonk gaming edit beat instrumental",
    "cinematic slowed synth edit beat royalty free",
    "dark slowed bass gaming montage beat",
    "atmospheric slowed edit beat no copyright",
    "aggressive phonk drift gaming montage no copyright",
    "hard bass trap edit beat instrumental no copyright",
]

FLOW_MOTIONS = [
    "punch_in",
    "pull_out",
    "beat_pulse",
    "drift_pan",
    "shake_hit",
    "dutch_roll",
    "snap_roll",
    "whip_in",
    "static_hold",
]

FLOW_TRANSITIONS = [
    "fade", "fadeblack", "fadewhite", "dissolve", "wipeleft", "wiperight",
    "slideleft", "slideright", "slideup", "circleopen", "radial", "pixelize",
    "distance", "hblur", "zoomin", "smoothleft", "smoothright", "squeezeh",
]
SOFT_TRANSITIONS = {"fade", "fadeblack", "dissolve", "smoothleft", "smoothright", "circleopen"}
HARD_TRANSITIONS = {"fadewhite", "distance", "zoomin", "hblur", "slideleft", "slideright", "pixelize", "radial"}

LAYOUTS = ["blur_fit", "fill_crop", "fill_pan", "center_zoom"]

SPEED_PROFILES: Dict[str, List[Tuple[float, float]]] = {
    "none": [(1.0, 1.0)],
    "slowmo_hit": [(0.40, 1.35), (0.35, 0.5), (0.25, 1.0)],
    "ramp_up": [(0.45, 0.6), (0.55, 1.5)],
    "ramp_down": [(0.55, 1.4), (0.45, 0.55)],
    "pulse_ramp": [(0.30, 1.0), (0.25, 0.5), (0.45, 1.2)],
    "slow_drift": [(1.0, 0.75)],
    "hype_fast": [(1.0, 1.25)],
}

COLOR_GRADES = [
    "cyber_neon",
    "teal_orange",
    "halation_cinema",
    "high_contrast_hud",
    "vivid_pop",
    "moody_dark",
    "warm_gold",
]

GRADE_FILTERS = {
    "cyber_neon": "eq=contrast=1.18:brightness=0.02:saturation=1.34,colorbalance=rs=-0.04:gs=0.02:bs=0.12",
    "teal_orange": "eq=contrast=1.16:brightness=0.015:saturation=1.28,colorbalance=rs=0.08:gs=0.01:bs=-0.08",
    "halation_cinema": "eq=contrast=1.15:brightness=0.02:saturation=1.24,colorbalance=rs=0.07:gs=0.02:bs=-0.03",
    "high_contrast_hud": "eq=contrast=1.22:brightness=0.02:saturation=1.26",
    "vivid_pop": "eq=contrast=1.12:brightness=0.03:saturation=1.55",
    "moody_dark": "eq=contrast=1.25:brightness=-0.04:saturation=1.05,colorbalance=bs=0.06:rs=-0.03",
    "warm_gold": "eq=contrast=1.14:brightness=0.025:saturation=1.22,colorbalance=rs=0.09:gs=0.03:bs=-0.10",
}

CAPTION_ACCENTS = ["&H0000FFFF&", "&H00FFFF00&", "&H0000FF00&", "&H00FF00FF&"]

STYLE_PROFILES: Dict[str, dict] = {
    "gaming_pro": dict(
        pace=1.0, ramp_p=0.50, glitch_p=0.35, flash_p=0.50, bloom_p=0.55, breather_p=0.14,
        motion_w=dict(punch_in=1.0, pull_out=0.8, beat_pulse=1.1, drift_pan=0.7, shake_hit=1.5,
                      dutch_roll=0.8, snap_roll=0.9, whip_in=1.1, static_hold=0.2),
        trans_w=dict(fade=0.5, fadewhite=1.2, wipeleft=0.8, slideleft=1.0, circleopen=0.7, radial=0.8,
                     pixelize=0.7, distance=1.2, hblur=1.1, zoomin=1.2, smoothleft=0.6, squeezeh=0.6),
        layout_w=dict(blur_fit=1.0, fill_crop=1.2, fill_pan=1.0, center_zoom=1.3),
        grade_w=dict(cyber_neon=1.4, teal_orange=1.0, high_contrast_hud=1.2, vivid_pop=0.8, moody_dark=0.8),
        shake=1.0, caption_size=86,
    ),
    "davinci_pro": dict(
        pace=1.15, ramp_p=0.60, glitch_p=0.08, flash_p=0.20, bloom_p=0.75, breather_p=0.25,
        motion_w=dict(punch_in=1.5, pull_out=1.4, beat_pulse=0.6, drift_pan=1.6, shake_hit=0.4,
                      dutch_roll=0.8, snap_roll=0.5, whip_in=0.6, static_hold=0.3),
        trans_w=dict(fade=1.6, fadeblack=1.0, dissolve=1.4, smoothleft=1.2, smoothright=1.2, circleopen=0.6,
                     distance=0.8, hblur=1.0, zoomin=0.7, wipeleft=0.4),
        layout_w=dict(blur_fit=1.2, fill_crop=1.0, fill_pan=1.4, center_zoom=1.0),
        grade_w=dict(halation_cinema=1.6, teal_orange=1.4, warm_gold=1.0, moody_dark=1.0),
        shake=0.5, caption_size=78,
    ),
    "shorts_hype": dict(
        pace=0.80, ramp_p=0.50, glitch_p=0.45, flash_p=0.60, bloom_p=0.45, breather_p=0.08,
        motion_w=dict(punch_in=1.2, pull_out=0.7, beat_pulse=1.3, drift_pan=0.5, shake_hit=1.4,
                      dutch_roll=1.0, snap_roll=1.2, whip_in=1.6, static_hold=0.1),
        trans_w=dict(slideleft=1.5, slideright=1.5, slideup=1.0, wipeleft=1.0, wiperight=1.0, zoomin=1.6,
                     hblur=1.5, pixelize=1.0, radial=1.0, fadewhite=1.3, squeezeh=0.8, fade=0.3),
        layout_w=dict(blur_fit=0.7, fill_crop=1.5, fill_pan=1.3, center_zoom=1.4),
        grade_w=dict(vivid_pop=1.6, cyber_neon=1.2, teal_orange=1.0, high_contrast_hud=1.0),
        shake=1.3, caption_size=92,
    ),
    "beat_montage": dict(
        pace=0.70, ramp_p=0.25, glitch_p=0.25, flash_p=0.70, bloom_p=0.5, breather_p=0.06,
        motion_w=dict(punch_in=1.2, pull_out=0.8, beat_pulse=2.2, drift_pan=0.6, shake_hit=1.0,
                      dutch_roll=0.7, snap_roll=1.0, whip_in=1.0, static_hold=0.2),
        trans_w=dict(fadewhite=1.5, zoomin=1.5, distance=1.2, hblur=1.1, slideup=0.8, circleopen=0.8,
                     radial=0.8, fade=0.6, wipeleft=0.6),
        layout_w=dict(blur_fit=0.9, fill_crop=1.3, fill_pan=0.9, center_zoom=1.3),
        grade_w=dict(cyber_neon=1.2, teal_orange=1.2, vivid_pop=1.0, halation_cinema=0.8, moody_dark=0.8),
        shake=0.9, caption_size=92,
    ),
}


class EffectDeck:
    def __init__(self, pool: List[str], rng: random.Random) -> None:
        self._base_pool = list(dict.fromkeys(pool))
        self._rng = rng
        self._remaining: List[str] = []
        self._used_in_edit: Set[str] = set()
        self._refill()

    def _refill(self) -> None:
        unused = [x for x in self._base_pool if x not in self._used_in_edit]
        if not unused:
            self._used_in_edit.clear()
            unused = list(self._base_pool)
        self._rng.shuffle(unused)
        self._remaining = unused

    def draw_unique(self) -> str:
        if not self._remaining:
            self._refill()
        choice = self._remaining.pop()
        self._used_in_edit.add(choice)
        return choice


class VarietyPicker:
    def __init__(self, weights: Dict[str, float], rng: random.Random, cooldown: int = 2) -> None:
        self._w = {k: float(v) for k, v in weights.items() if v > 0}
        self._rng = rng
        self._cooldown = max(0, cooldown)
        self._recent: List[str] = []
        self._count: Dict[str, int] = {k: 0 for k in self._w}

    def draw(self, boost: Optional[Dict[str, float]] = None, allowed: Optional[Iterable[str]] = None) -> str:
        boost = boost or {}
        keys = [k for k in self._w if allowed is None or k in set(allowed)]
        if not keys:
            keys = list(self._w)
        recent = set(self._recent[-self._cooldown:]) if self._cooldown else set()
        cands = [k for k in keys if k not in recent] or keys
        weights = [max(0.01, self._w[k] * boost.get(k, 1.0) / (1.0 + 0.8 * self._count[k])) for k in cands]
        choice = self._rng.choices(cands, weights=weights, k=1)[0]
        self._recent.append(choice)
        self._count[choice] += 1
        return choice


def _next_run_offset() -> int:
    global GLOBAL_RUN_COUNTER
    with RUN_COUNTER_LOCK:
        val = GLOBAL_RUN_COUNTER
        GLOBAL_RUN_COUNTER += 1
        return val


def get_project_root() -> Path:
    return Path(__file__).resolve().parent


def get_edits_dir(base_output_dir: Optional[str] = None) -> Path:
    _ = base_output_dir
    edits_dir = get_project_root() / EDITS_FOLDER_NAME
    edits_dir.mkdir(parents=True, exist_ok=True)
    return edits_dir


def get_final_edits_dir(base_output_dir: Optional[str] = None) -> Path:
    _ = base_output_dir
    final_dir = get_project_root() / FINAL_EDITS_FOLDER_NAME
    final_dir.mkdir(parents=True, exist_ok=True)
    return final_dir


def save_edits_to_folder(
    edit_files: Iterable[str],
    base_output_dir: Optional[str] = None,
    move_files: bool = True,
) -> List[str]:
    edits_dir = get_edits_dir(base_output_dir)
    saved: List[str] = []

    for raw_path in edit_files:
        src = Path(raw_path)
        if not src.is_file():
            continue
        dst = edits_dir / src.name
        if src.resolve() == dst.resolve():
            saved.append(str(dst))
            continue

        try:
            if move_files:
                shutil.move(str(src), str(dst))
            else:
                shutil.copy2(str(src), str(dst))
            saved.append(str(dst))
        except Exception:
            pass

    return saved


def list_candidate_edits(base_output_dir: Optional[str] = None) -> List[Dict[str, object]]:
    edits_dir = get_edits_dir(base_output_dir)
    candidates: List[Dict[str, object]] = []
    files = sorted(
        [p for p in edits_dir.iterdir() if p.is_file() and p.suffix.lower() == ".mp4"],
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    for p in files:
        stat = p.stat()
        dur = _probe_media_duration(p)
        candidates.append(
            {
                "name": p.name,
                "path": str(p.resolve()),
                "duration_sec": dur,
                "size_mb": round(stat.st_size / (1024 * 1024), 2),
            }
        )
    return candidates


def move_selected_edits_to_final(
    selected_edits: Iterable[str],
    base_output_dir: Optional[str] = None,
    copy_instead_of_move: bool = False,
    on_progress: Optional[Callable[[str], None]] = None,
) -> List[str]:
    def log(msg: str) -> None:
        if on_progress:
            try:
                on_progress(msg)
            except Exception:
                pass

    edits_dir = get_edits_dir(base_output_dir)
    final_dir = get_final_edits_dir(base_output_dir)
    moved: List[str] = []

    for item in selected_edits:
        raw = str(item).strip().strip('"').strip("'")
        if not raw:
            continue
        src = Path(raw)
        if not src.is_absolute() and not src.is_file():
            src = edits_dir / raw

        if not src.is_file():
            log(f"Skipped (not found): {raw}")
            continue

        dst = final_dir / src.name
        if src.resolve() == dst.resolve():
            moved.append(str(dst))
            continue

        try:
            if copy_instead_of_move:
                shutil.copy2(str(src), str(dst))
                log(f"Copied to FINAL_EDITS: {dst.name}")
            else:
                shutil.move(str(src), str(dst))
                log(f"Moved to FINAL_EDITS: {dst.name}")
            moved.append(str(dst))
        except Exception as exc:
            log(f"Failed moving {src.name}: {exc}")

    return moved


def select_and_move_final_edits(
    base_output_dir: Optional[str] = None,
    copy_instead_of_move: bool = False,
) -> List[str]:
    items = list_candidate_edits(base_output_dir)
    if not items:
        print(f"No .mp4 edits found in {get_edits_dir(base_output_dir)}")
        return []

    print("\n=== Candidate Edits (EDITS/) ===")
    for idx, info in enumerate(items, start=1):
        dur_str = f"{info['duration_sec']}s" if info["duration_sec"] is not None else "?s"
        print(f"  [{idx}] {info['name']}  ({dur_str}, {info['size_mb']} MB)")

    choice = input("\nEnter numbers to move to FINAL_EDITS (e.g. 1,2), 'all', or Enter to skip: ").strip().lower()
    if not choice:
        return []

    chosen_paths: List[str] = []
    if choice == "all":
        chosen_paths = [str(it["path"]) for it in items]
    else:
        for tok in re.split(r"[,\s]+", choice):
            if tok.isdigit():
                pos = int(tok)
                if 1 <= pos <= len(items):
                    chosen_paths.append(str(items[pos - 1]["path"]))

    if not chosen_paths:
        print("No valid edit numbers selected.")
        return []

    return move_selected_edits_to_final(
        chosen_paths,
        base_output_dir=base_output_dir,
        copy_instead_of_move=copy_instead_of_move,
        on_progress=print,
    )


def _safe_unlink(path: Path, retries: int = 8, delay: float = 0.25) -> None:
    for _ in range(retries):
        try:
            if path.is_file():
                path.unlink()
            return
        except PermissionError:
            time.sleep(delay)
        except Exception:
            return


def _cleanup_dir(folder: Path) -> None:
    try:
        if folder.exists():
            shutil.rmtree(folder, ignore_errors=True)
    except Exception:
        pass


def _resolve_cookies(cookies_path: Optional[Path] = None) -> Optional[Path]:
    candidates = [
        Path(cookies_path) if cookies_path else None,
        DEFAULT_COOKIES_PATH,
        get_project_root() / "cookies.txt",
    ]
    for c in candidates:
        if c and c.is_file() and c.stat().st_size > 0:
            return c
    return None


def _yt_base_args(cookies_path: Optional[Path] = None) -> List[str]:
    args = [
        "yt-dlp",
        "-4",
        "--no-warnings",
        "--socket-timeout",
        "15",
        "--retries",
        "2",
        "--remote-components",
        "ejs:github",
    ]
    resolved_cookies = _resolve_cookies(cookies_path)
    if resolved_cookies:
        args.extend(["--cookies", str(resolved_cookies)])
    return args


def _probe_media_duration(media_path: Path) -> Optional[float]:
    cmd = [
        "ffprobe",
        "-v",
        "error",
        "-show_entries",
        "format=duration",
        "-of",
        "default=noprint_wrappers=1:nokey=1",
        str(media_path),
    ]
    try:
        res = subprocess.run(cmd, capture_output=True, text=True, timeout=15)
        if res.returncode == 0 and res.stdout:
            val = float(res.stdout.strip().splitlines()[0])
            if val > 0:
                return round(val, 2)
    except Exception:
        pass
    return None


_XFADE_CACHE: Optional[Set[str]] = None


def _supported_xfades() -> Set[str]:
    global _XFADE_CACHE
    if _XFADE_CACHE is None:
        names: Set[str] = set()
        try:
            r = subprocess.run(["ffmpeg", "-hide_banner", "-h", "filter=xfade"],
                               capture_output=True, text=True, timeout=10)
            txt = (r.stdout or "") + "\n" + (r.stderr or "")
            names = set(re.findall(r"^\s{2,}([a-z0-9]+)\s+\d+\s*(?:\S.*)?$", txt, re.M))
        except Exception:
            names = set()
        _XFADE_CACHE = names if names else {"fade"}
    return _XFADE_CACHE


def _fetch_metadata_no_download(
    source_input: str,
    cookies_path: Optional[Path] = None,
    on_progress: Optional[Callable[[str], None]] = None,
) -> Tuple[List[dict], float, str]:
    def log(msg: str) -> None:
        if on_progress:
            try:
                on_progress(msg)
            except Exception:
                pass

    cmd = [
        *_yt_base_args(cookies_path=cookies_path),
        "--dump-single-json",
        "--no-download",
        source_input,
    ]
    try:
        res = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        if res.returncode == 0 and res.stdout:
            info = json.loads(res.stdout)
            dur = float(info.get("duration") or 0.0)
            title = str(info.get("title") or "")
            heatmap = info.get("heatmap")
            if isinstance(heatmap, list) and len(heatmap) > 0:
                log(f"Found replay heatmap ({len(heatmap)} points) — scanning full video.")
                return heatmap, dur, title
            log("No replay heatmap found — scanning full video timeline.")
            return [], dur, title
    except Exception:
        pass
    return [], 0.0, ""


def _resolve_stream_urls(
    source_input: str,
    cookies_path: Optional[Path] = None,
    selector: str = BEST_FORMAT_SELECTOR,
) -> List[str]:
    cmd = [
        *_yt_base_args(cookies_path=cookies_path),
        "-f",
        selector,
        "-g",
        source_input,
    ]
    try:
        res = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        if res.returncode == 0 and res.stdout:
            return [ln.strip() for ln in res.stdout.splitlines() if ln.strip().startswith("http")]
    except Exception:
        pass
    return []


def _ensure_varied_5_to_10s_clips(
    candidates: List[Tuple[float, float]],
    total_dur: float,
    min_clips: int = 8,
    min_len: float = MIN_SOURCE_CLIP_SEC,
    max_len: float = MAX_SOURCE_CLIP_SEC,
    rng: Optional[random.Random] = None,
) -> List[Tuple[float, float]]:
    r = rng or random.Random()
    lo = max(2.0, min(60.0, float(min_len)))
    hi = max(lo, min(60.0, float(max_len)))
    cleaned: List[Tuple[float, float]] = []

    for st, dur in candidates:
        st_f = max(0.0, float(st))
        d_f = max(lo, min(hi, float(dur)))
        if total_dur > 0:
            st_f = min(st_f, max(0.0, total_dur - lo))
            d_f = min(d_f, max(2.0, total_dur - st_f))
        if all(abs(st_f - prev_st) >= 3.5 for prev_st, _ in cleaned):
            cleaned.append((round(st_f, 2), round(d_f, 2)))

    if total_dur > lo and len(cleaned) < min_clips:
        needed = min_clips - len(cleaned)
        for s_idx in range(needed * 4):
            if len(cleaned) >= min_clips:
                break
            zone_frac = ((s_idx % max(1, min_clips)) + r.uniform(0.05, 0.92)) / max(1, min_clips)
            max_start = max(0.0, total_dur - lo)
            cand_st = round(min(max_start, max(0.0, zone_frac * max_start)), 2)
            if all(abs(cand_st - prev_st) >= 4.0 for prev_st, _ in cleaned):
                avail = max(2.0, total_dur - cand_st)
                chosen_len = round(min(avail, r.uniform(lo, hi)), 2)
                cleaned.append((cand_st, chosen_len))

    if not cleaned:
        dur_val = round(min(hi, total_dur if total_dur > 0 else hi), 2)
        cleaned.append((0.0, max(2.0, dur_val)))

    cleaned.sort(key=lambda item: item[0])
    return cleaned


def _select_10s_peak_clips_from_heatmap(
    heatmap: List[dict],
    total_dur: float,
    clip_count: int,
    clip_len: float = PEAK_CLIP_DURATION,
    min_slice_len: float = MIN_SOURCE_CLIP_SEC,
    on_progress: Optional[Callable[[str], None]] = None,
) -> List[Tuple[float, float]]:
    def log(msg: str) -> None:
        if on_progress:
            try:
                on_progress(msg)
            except Exception:
                pass

    points: List[Tuple[float, float, float]] = []
    max_end = total_dur
    for item in heatmap:
        if not isinstance(item, dict):
            continue
        try:
            st = float(item.get("start_time"))
            en = float(item.get("end_time"))
            val = float(item.get("value"))
        except (TypeError, ValueError):
            continue
        if en > st and val >= 0.0:
            points.append((st, en, val))
            if en > max_end:
                max_end = en

    if not points:
        return []

    effective_dur = total_dur if total_dur > 0 else max_end
    lo = max(2.0, min(60.0, float(min_slice_len)))
    hi = max(lo, min(60.0, float(clip_len)))

    if effective_dur <= lo:
        return [(0.0, max(2.0, round(effective_dur, 2)))]

    rng = random.Random()
    target_pool = max(8, clip_count)
    chosen_starts: List[float] = []

    num_zones = min(10, target_pool)
    zone_span = effective_dur / num_zones
    for z in range(num_zones):
        z_st = z * zone_span
        z_en = (z + 1) * zone_span
        z_pts = [p for p in points if z_st <= ((p[0] + p[1]) * 0.5) < z_en]
        z_pts.sort(key=lambda p: p[2], reverse=True)
        for st, en, _ in z_pts[:2]:
            center = (st + en) * 0.5
            win_len = round(rng.uniform(lo, min(hi, max(lo, effective_dur))), 2)
            max_start = max(0.0, effective_dur - win_len)
            cand_st = round(max(0.0, min(max_start, center - win_len * 0.5 + rng.uniform(-1.5, 1.5))), 2)
            if all(abs(cand_st - prev) >= 4.5 for prev in chosen_starts):
                chosen_starts.append(cand_st)
                break

    ranked_pts = sorted(points, key=lambda p: p[2], reverse=True)
    for st, en, _ in ranked_pts:
        if len(chosen_starts) >= target_pool:
            break
        center = (st + en) * 0.5
        win_len = round(rng.uniform(lo, min(hi, max(lo, effective_dur))), 2)
        max_start = max(0.0, effective_dur - win_len)
        cand_st = round(max(0.0, min(max_start, center - win_len * 0.5 + rng.uniform(-1.5, 1.5))), 2)
        if all(abs(cand_st - prev) >= 4.5 for prev in chosen_starts):
            chosen_starts.append(cand_st)

    raw_clips: List[Tuple[float, float]] = []
    for st in sorted(chosen_starts):
        win_len = round(min(rng.uniform(lo, hi), effective_dur - st), 2)
        raw_clips.append((st, max(2.0, win_len)))

    result = _ensure_varied_5_to_10s_clips(
        raw_clips, total_dur=effective_dur, min_clips=target_pool, min_len=lo, max_len=hi, rng=rng
    )
    for idx, (st, d_val) in enumerate(result, start=1):
        log(f"Source Clip #{idx} ({d_val:.1f}s): {st:.1f}s -> {st + d_val:.1f}s")
    return result


def _stream_audio_energy_buckets(stream_url: str, total_dur: float, bucket_sec: float = 0.5) -> List[float]:
    num_buckets = max(1, int(math.ceil(total_dur / bucket_sec)))
    sample_rate = 8000
    cmd = [
        "ffmpeg", "-v", "error", "-reconnect", "1", "-reconnect_streamed", "1",
        "-reconnect_delay_max", "5", "-i", stream_url,
        "-vn", "-ac", "1", "-ar", str(sample_rate), "-f", "s16le", "pipe:1",
    ]
    try:
        res = subprocess.run(cmd, capture_output=True, timeout=150)
        raw = res.stdout or b""
        if len(raw) < 2:
            return [0.35] * num_buckets
        samples = array.array("h")
        samples.frombytes(raw[: len(raw) - (len(raw) % 2)])
        if not samples:
            return [0.35] * num_buckets
        samples_per_bucket = max(1, int(sample_rate * bucket_sec))
        buckets: List[float] = []
        for b_idx in range(num_buckets):
            s_i = b_idx * samples_per_bucket
            e_i = min(len(samples), s_i + samples_per_bucket)
            if s_i >= e_i:
                buckets.append(0.0)
                continue
            chunk = samples[s_i:e_i]
            rms = math.sqrt(sum(float(x) * float(x) for x in chunk) / len(chunk)) / 32768.0
            if rms <= 1e-6:
                buckets.append(0.0)
            else:
                db = 20.0 * math.log10(rms)
                buckets.append(max(0.0, min(1.0, (db + 48.0) / 42.0)))
        return buckets
    except Exception:
        return [0.35] * num_buckets


def _stream_motion_buckets(stream_url: str, total_dur: float, bucket_sec: float = 0.5) -> List[float]:
    num_buckets = max(1, int(math.ceil(total_dur / bucket_sec)))
    buckets = [0.0] * num_buckets
    cmd = [
        "ffmpeg", "-hide_banner", "-loglevel", "info", "-reconnect", "1",
        "-reconnect_streamed", "1", "-reconnect_delay_max", "5", "-i", stream_url,
        "-an", "-vf", "fps=2,scale=160:-2,select='gt(scene,0.12)',showinfo",
        "-f", "null", "-",
    ]
    try:
        res = subprocess.run(cmd, capture_output=True, text=True, timeout=150)
        text = (res.stdout or "") + "\n" + (res.stderr or "")
        for line in text.splitlines():
            if "pts_time:" not in line:
                continue
            tm = re.search(r"pts_time:([0-9.]+)", line)
            sm = re.search(r"scene_score[:=]([0-9.]+)", line)
            if tm:
                t_val = float(tm.group(1))
                sc_val = float(sm.group(1)) if sm else 0.25
                b_idx = min(num_buckets - 1, max(0, int(t_val / bucket_sec)))
                buckets[b_idx] = min(1.0, buckets[b_idx] + min(1.0, sc_val * 2.5))
    except Exception:
        pass
    return buckets


def _select_10s_peak_clips_from_stream(
    source_input: str,
    total_dur: float,
    clip_count: int,
    clip_len: float = PEAK_CLIP_DURATION,
    min_slice_len: float = MIN_SOURCE_CLIP_SEC,
    cookies_path: Optional[Path] = None,
    on_progress: Optional[Callable[[str], None]] = None,
) -> List[Tuple[float, float]]:
    def log(msg: str) -> None:
        if on_progress:
            try:
                on_progress(msg)
            except Exception:
                pass

    rng = random.Random()
    target_pool = max(8, clip_count)
    lo = max(2.0, min(60.0, float(min_slice_len)))
    hi = max(lo, min(60.0, float(clip_len)))

    if total_dur <= lo:
        return [(0.0, max(2.0, round(total_dur, 2)))]

    log("Resolving lightweight stream for full-video audio & motion scan...")
    light_urls = _resolve_stream_urls(
        source_input,
        cookies_path=cookies_path,
        selector="worstvideo[ext=mp4]/worstvideo/bestaudio/best",
    )
    if not light_urls:
        log("Stream scan fallback: sampling across full video timeline.")
        return _ensure_varied_5_to_10s_clips([], total_dur=total_dur, min_clips=target_pool, min_len=lo, max_len=hi, rng=rng)

    bucket_sec = 1.0 if total_dur > 900 else 0.5
    stream_url = light_urls[0]

    log(f"Scanning audio & motion across {total_dur:.0f}s...")
    with ThreadPoolExecutor(max_workers=2) as pool:
        fut_audio = pool.submit(_stream_audio_energy_buckets, stream_url, total_dur, bucket_sec)
        fut_motion = pool.submit(_stream_motion_buckets, stream_url, total_dur, bucket_sec)
        audio_b = fut_audio.result(timeout=170)
        motion_b = fut_motion.result(timeout=170)

    n_b = min(len(audio_b), len(motion_b))
    if n_b <= 0:
        return _ensure_varied_5_to_10s_clips([], total_dur=total_dur, min_clips=target_pool, min_len=lo, max_len=hi, rng=rng)

    excitement = [0.60 * audio_b[i] + 0.40 * motion_b[i] for i in range(n_b)]
    prefix = [0.0] * (n_b + 1)
    for i in range(n_b):
        prefix[i + 1] = prefix[i] + excitement[i]

    win_sec = rng.uniform(lo, hi)
    win_buckets = max(1, int(round(win_sec / bucket_sec)))
    max_start_sec = max(0.0, total_dur - win_sec)
    max_start_b = min(n_b - 1, max(0, int(max_start_sec / bucket_sec)))

    scored_by_bucket: List[Tuple[int, float, float]] = []
    for sb in range(0, max_start_b + 1):
        eb = min(n_b, sb + win_buckets)
        span = max(1, eb - sb)
        avg_sc = (prefix[eb] - prefix[sb]) / span
        peak_sc = max(excitement[sb:eb]) if eb > sb else 0.0
        st_sec = round(min(max_start_sec, sb * bucket_sec), 2)
        scored_by_bucket.append((sb, round(0.60 * avg_sc + 0.40 * peak_sc, 4), st_sec))

    chosen: List[Tuple[float, float]] = []
    min_gap = max(4.5, win_sec * 0.75)

    num_zones = min(10, target_pool)
    zone_size = max(1, (max_start_b + 1) // num_zones)
    for z in range(num_zones):
        z_lo = z * zone_size
        z_hi = (max_start_b + 1) if z == num_zones - 1 else min(max_start_b + 1, (z + 1) * zone_size)
        zone_cands = sorted(scored_by_bucket[z_lo:z_hi], key=lambda item: item[1], reverse=True)
        for _, _, st in zone_cands:
            if all(abs(st - prev_st) >= min_gap for prev_st, _ in chosen):
                c_len = round(min(total_dur - st, rng.uniform(lo, hi)), 2)
                chosen.append((st, max(2.0, c_len)))
                break

    overall_cands = sorted(scored_by_bucket, key=lambda item: item[1], reverse=True)
    for _, _, st in overall_cands:
        if len(chosen) >= target_pool:
            break
        if all(abs(st - prev_st) >= min_gap for prev_st, _ in chosen):
            c_len = round(min(total_dur - st, rng.uniform(lo, hi)), 2)
            chosen.append((st, max(2.0, c_len)))

    result = _ensure_varied_5_to_10s_clips(
        chosen, total_dur=total_dur, min_clips=target_pool, min_len=lo, max_len=hi, rng=rng
    )
    for idx, (st, d_val) in enumerate(result, start=1):
        log(f"Source Clip #{idx} ({d_val:.1f}s): {st:.1f}s -> {st + d_val:.1f}s")
    return result


def _spread_clips_across_video(
    clips: List[Tuple[float, float]],
    total_dur: float,
    clip_mode: str,
    max_clips: int,
    run_offset: int = 0,
    rng: Optional[random.Random] = None,
    truncate: bool = False,
) -> List[Tuple[float, float]]:
    if not clips:
        return []
    r = rng or random.Random()
    ordered = sorted(clips, key=lambda item: item[0])

    if total_dur > 0 and len(ordered) > 2:
        num_buckets = min(len(ordered), max(3, max_clips if clip_mode == "count" else 6))
        bucket_span = total_dur / num_buckets
        stratified: List[Tuple[float, float]] = []
        used_indices: Set[int] = set()

        for b_idx in range(num_buckets):
            b_start = b_idx * bucket_span
            b_end = (b_idx + 1) * bucket_span
            in_bucket = [
                (i, c) for i, c in enumerate(ordered)
                if i not in used_indices and b_start <= c[0] < b_end
            ]
            if not in_bucket:
                in_bucket = [(i, c) for i, c in enumerate(ordered) if i not in used_indices]
            if in_bucket:
                pick_pos = (run_offset + b_idx) % len(in_bucket)
                chosen_i, chosen_c = in_bucket[pick_pos]
                used_indices.add(chosen_i)
                stratified.append(chosen_c)

        for i, c in enumerate(ordered):
            if i not in used_indices:
                stratified.append(c)
        ordered = stratified

    if run_offset and len(ordered) > 1:
        shift = run_offset % len(ordered)
        ordered = ordered[shift:] + ordered[:shift]

    if truncate and clip_mode == "count":
        limit = max(1, min(len(ordered), int(max_clips)))
        return ordered[:limit]

    pool = ordered.copy()
    if clip_mode != "count":
        r.shuffle(pool)
    return pool


def _list_local_assets(folder_name: str) -> List[Path]:
    folder = get_project_root() / "assets" / folder_name
    if not folder.is_dir():
        return []
    exts = (".mp3", ".m4a", ".wav", ".ogg", ".aac")
    return sorted([p for p in folder.iterdir() if p.is_file() and p.suffix.lower() in exts])


def _acquire_rotating_music_track(
    work_dir: Path,
    rng: random.Random,
    music_query: Optional[str] = None,
    custom_music_path: Optional[str] = None,
    cookies_path: Optional[Path] = None,
    on_progress: Optional[Callable[[str], None]] = None,
) -> Optional[Path]:
    def log(msg: str) -> None:
        if on_progress:
            try:
                on_progress(msg)
            except Exception:
                pass

    if custom_music_path:
        p = Path(custom_music_path)
        if p.is_file():
            return p

    local_tracks = _list_local_assets("music")
    if local_tracks:
        chosen = rng.choice(local_tracks)
        log(f"Selected beat track: {chosen.name}")
        return chosen

    query = (music_query or "").strip() or rng.choice(MUSIC_QUERIES_POOL)
    search_index = rng.randint(1, 3)
    music_out = work_dir / f"edit_music_{uuid.uuid4().hex[:5]}.m4a"

    cmd = [
        *_yt_base_args(cookies_path=cookies_path),
        "-f", "ba[ext=m4a]/ba/bestaudio",
        "--no-playlist",
        "--playlist-start", str(search_index),
        "--playlist-end", str(search_index),
        "-o", str(music_out.with_suffix(".%(ext)s")),
        f"ytsearch3:{query}",
    ]
    try:
        log(f"Fetching beat (#{search_index}) for '{query}'...")
        subprocess.run(cmd, capture_output=True, text=True, timeout=60)
        for cand in work_dir.glob(f"{music_out.stem}.*"):
            if cand.is_file() and cand.stat().st_size > 1024:
                return cand
    except Exception:
        pass
    return None


def _music_envelope(audio_path: Path, max_sec: float = 120.0, hop_sec: float = 0.05) -> List[float]:
    sample_rate = 8000
    cmd = [
        "ffmpeg", "-v", "error", "-t", f"{max_sec:.2f}", "-i", str(audio_path),
        "-vn", "-ac", "1", "-ar", str(sample_rate),
        "-af", "lowpass=f=150,highpass=f=28",
        "-f", "s16le", "pipe:1",
    ]
    try:
        res = subprocess.run(cmd, capture_output=True, timeout=40)
        raw = res.stdout or b""
        if len(raw) < 4:
            return []
        samples = array.array("h")
        samples.frombytes(raw[: len(raw) - (len(raw) % 2)])
        hop_len = max(1, int(sample_rate * hop_sec))
        env: List[float] = []
        for i in range(0, len(samples) - hop_len, hop_len):
            chunk = samples[i: i + hop_len]
            env.append(math.sqrt(sum(float(x) * float(x) for x in chunk) / len(chunk)) / 32768.0)
        return env
    except Exception:
        return []


def _beats_from_envelope(env: List[float], hop_sec: float = 0.05) -> List[float]:
    if len(env) < 8:
        return []
    ranked = sorted(env)
    p95 = ranked[int(len(ranked) * 0.95)] if ranked else 0.0
    floor = max(0.012, p95 * 0.22)
    beats: List[float] = []
    for idx in range(2, len(env) - 2):
        local_avg = sum(env[max(0, idx - 10): min(len(env), idx + 11)]) / 21.0
        if env[idx] >= env[idx - 1] and env[idx] >= env[idx + 1] and env[idx] > max(floor, local_avg * 1.25):
            t_sec = round(idx * hop_sec, 2)
            if not beats or (t_sec - beats[-1]) >= 0.30:
                beats.append(t_sec)
    return beats


def _detect_beat_and_bass_drops(audio_path: Path, max_sec: float = 45.0) -> List[float]:
    return _beats_from_envelope(_music_envelope(audio_path, max_sec=max_sec))


def _analyze_music(audio_path: Path, target_dur: float, rng: random.Random) -> Optional[dict]:
    hop = 0.05
    env = _music_envelope(audio_path, max_sec=150.0, hop_sec=hop)
    if len(env) < 20:
        return None
    total = len(env) * hop
    beats_all = _beats_from_envelope(env, hop)

    prefix = [0.0] * (len(env) + 1)
    for i, v in enumerate(env):
        prefix[i + 1] = prefix[i] + v

    def avg(a_sec: float, b_sec: float) -> float:
        a = max(0, int(a_sec / hop))
        b = min(len(env), int(b_sec / hop))
        return (prefix[b] - prefix[a]) / max(1, b - a)

    start = 0.0
    if total > target_dur + 3.0:
        starts = [b for b in beats_all if b + target_dur <= total - 0.5]
        if starts:
            scored = sorted(((avg(b, b + target_dur), b) for b in starts), reverse=True)
            start = rng.choice(scored[: max(1, min(4, len(scored)))])[1]

    rel_beats = [round(b - start, 2) for b in beats_all if start <= b <= start + target_dur + 1.0]

    drop: Optional[float] = None
    best_rise = 0.0
    for i in range(int(target_dur * 0.18 / hop), int(target_dur * 0.65 / hop)):
        t0 = start + i * hop
        rise = avg(t0, t0 + 1.0) - avg(t0 - 1.0, t0)
        if rise > best_rise:
            best_rise, drop = rise, i * hop
    if drop is not None and rel_beats:
        drop = min(rel_beats, key=lambda b: abs(b - drop))
    return {"start": round(start, 2), "beats": rel_beats, "drop": drop}


def _plan_edit(
    source_clips: List[Tuple[float, float]],
    beat_times: List[float],
    drop_time: Optional[float],
    rng: random.Random,
    total_target: float,
    style: str,
    total_video_dur: float,
    used_moments: List[float],
    on_progress: Optional[Callable[[str], None]] = None,
) -> dict:
    def log(msg: str) -> None:
        if on_progress:
            try:
                on_progress(msg)
            except Exception:
                pass

    prof = STYLE_PROFILES[style]
    T = float(total_target)
    xf = _supported_xfades()

    beats = [b for b in beat_times if 0.5 <= b <= T - 0.7]
    if len(beats) < 4:
        period = 60.0 / rng.uniform(100.0, 148.0)
        beats = [round(k * period, 2) for k in range(1, int(T / period) + 1) if 0.5 <= k * period <= T - 0.7]

    D = drop_time if (drop_time and 0.18 * T <= drop_time <= 0.7 * T) else rng.uniform(0.28, 0.42) * T
    if beats:
        D = min(beats, key=lambda b: abs(b - D))
    D = round(D, 2)

    bounds: List[float] = [0.0]
    cur = 0.0
    pace = max(0.35, float(prof["pace"]))
    while len(bounds) < 24 and T - cur > 2.0:
        if cur < D * 0.95:
            lo, hi = 2.0, 3.2
        elif cur >= T - 3.2:
            lo, hi = 1.5, 2.4
        else:
            lo, hi = 0.8, 1.7
            if rng.random() < prof["breather_p"]:
                lo, hi = 2.0, 2.8
        tgt = max(0.45, rng.uniform(lo, hi) * pace)
        want = cur + tgt
        cands = [b for b in beats if b > cur + 0.7 * tgt]
        nxt = min(cands, key=lambda b: abs(b - want)) if cands else want
        if nxt <= cur + 0.45:
            nxt = cur + tgt
        if cur < D - 1.0 and nxt >= D - 0.9:
            nxt = D
        nxt = round(nxt, 2)
        if T - nxt < 1.2:
            break
        bounds.append(nxt)
        cur = nxt
    bounds.append(round(T, 2))

    n = len(bounds) - 1
    slots = [round(bounds[i + 1] - bounds[i], 3) for i in range(n)]
    phases: List[str] = []
    for i in range(n):
        s = bounds[i]
        phases.append("intro" if s < D - 0.05 else ("outro" if s >= T - 3.2 else "drop"))
    first_drop = next((i for i, p in enumerate(phases) if p == "drop"), None)

    t_weights = {k: v for k, v in prof["trans_w"].items() if k in xf} or {"fade": 1.0}
    trans_picker = VarietyPicker(t_weights, rng, cooldown=2)
    trans_in: List[str] = [""] * n
    td_in: List[float] = [0.0] * n
    for k in range(n - 1):
        j = k + 1
        ph = phases[j]
        if j == first_drop:
            boost = {nm: 3.0 for nm in HARD_TRANSITIONS}
        elif ph == "drop":
            boost = {**{nm: 1.8 for nm in HARD_TRANSITIONS}, **{nm: 0.4 for nm in SOFT_TRANSITIONS}}
        else:
            boost = {nm: 2.2 for nm in SOFT_TRANSITIONS}
        name = trans_picker.draw(boost=boost)
        if ph == "drop":
            td = rng.uniform(0.10, 0.20)
        elif name in SOFT_TRANSITIONS:
            td = rng.uniform(0.30, 0.45)
        else:
            td = rng.uniform(0.18, 0.30)
        td = max(0.06, min(td, 0.4 * min(slots[k], slots[j])))
        trans_in[j], td_in[j] = name, round(td, 3)

    motion_picker = VarietyPicker(prof["motion_w"], rng, cooldown=3)
    layout_picker = VarietyPicker(prof["layout_w"], rng, cooldown=1)
    speed_picker = VarietyPicker({k: 1.0 for k in SPEED_PROFILES if k != "none"}, rng, cooldown=1)
    motion_boost = {
        "intro": dict(drift_pan=2.0, pull_out=2.0, punch_in=1.5, shake_hit=0.2, whip_in=0.4, beat_pulse=0.7),
        "drop": dict(shake_hit=2.0, beat_pulse=2.0, whip_in=1.8, snap_roll=1.4, dutch_roll=1.3, drift_pan=0.4, pull_out=0.5),
        "outro": dict(pull_out=2.5, drift_pan=1.5, static_hold=2.0, shake_hit=0.2, whip_in=0.3),
    }
    speed_boost = {
        "intro": dict(slow_drift=2.0, ramp_up=1.5, slowmo_hit=0.3, hype_fast=0.2),
        "drop": dict(slowmo_hit=2.0, pulse_ramp=1.5, ramp_up=1.0, hype_fast=1.3, slow_drift=0.3),
        "outro": dict(slow_drift=2.0, ramp_down=1.5, slowmo_hit=0.5, hype_fast=0.1),
    }
    ramp_mult = {"intro": 0.5, "drop": 1.4, "outro": 0.7}

    beat_period = 0.5
    if len(beats) >= 3:
        diffs = [beats[i + 1] - beats[i] for i in range(len(beats) - 1) if 0.2 < beats[i + 1] - beats[i] < 1.2]
        if diffs:
            beat_period = statistics.median(diffs)

    used_intervals: List[Tuple[float, float]] = []
    used_idx: Set[int] = set()
    free_p = 0.0 if len(source_clips) >= n + 2 else 0.35

    def choose_source(need: float) -> float:
        best: Optional[Tuple[float, float]] = None
        best_ci = -1
        for _ in range(18):
            if total_video_dur > 0 and rng.random() < free_p:
                s = rng.uniform(0.0, max(0.0, total_video_dur - need - 0.3))
                ci = -1
            else:
                idxs = list(range(len(source_clips)))
                ws = [0.2 if i in used_idx else 1.0 for i in idxs]
                ci = rng.choices(idxs, weights=ws, k=1)[0]
                cs, cd = source_clips[ci]
                if need <= cd:
                    s = rng.uniform(cs, cs + (cd - need))
                else:
                    s = max(0.0, cs - rng.uniform(0.0, (need - cd) * 0.6))
            if total_video_dur > 0:
                s = min(s, max(0.0, total_video_dur - need - 0.3))
            s = max(0.0, s)
            overlap = sum(max(0.0, min(s + need, b) - max(s, a)) for a, b in used_intervals)
            near_batch = sum(1.0 for m in used_moments if abs(s - m) < 4.0)
            penalty = overlap * 10.0 + near_batch * 2.0 + (0.5 if ci in used_idx else 0.0)
            if best is None or penalty < best[0]:
                best = (penalty, s)
                best_ci = ci
            if penalty == 0.0:
                break
        assert best is not None
        used_intervals.append((best[1], best[1] + need))
        used_moments.append(best[1])
        if best_ci >= 0:
            used_idx.add(best_ci)
        return round(best[1], 2)

    shots: List[dict] = []
    for i in range(n):
        ph = phases[i]
        d_out = slots[i] + td_in[i] / 2.0 + (td_in[i + 1] / 2.0 if i + 1 < n else 0.0)
        d = round(d_out, 3)
        beat_t = round(td_in[i] / 2.0, 3)

        is_hit = (i == first_drop)
        motion = motion_picker.draw(boost=motion_boost[ph])
        if is_hit and rng.random() < 0.7:
            motion = "shake_hit" if rng.random() < 0.6 else "whip_in"
        layout = layout_picker.draw()

        speed = "none"
        if d >= 1.5 and rng.random() < prof["ramp_p"] * ramp_mult[ph]:
            speed = speed_picker.draw(boost=speed_boost[ph])
        need = sum(fr * d * sp for fr, sp in SPEED_PROFILES[speed]) + 0.35

        shots.append(dict(
            idx=i, phase=ph, slot=slots[i], dur=d, beat_t=beat_t, is_hit=is_hit,
            src_start=choose_source(need), src_len=round(need, 2),
            layout=layout, motion=motion, speed=speed,
            flash=bool(is_hit or rng.random() < prof["flash_p"] * (0.8 if ph == "drop" else 0.25)),
            glitch=bool(ph == "drop" and rng.random() < prof["glitch_p"]),
            bloom=bool(rng.random() < prof["bloom_p"]),
            intensity=round(prof["shake"] * rng.uniform(0.8, 1.25), 2),
            dir=rng.choice([-1, 1]),
            period=round(beat_period, 3),
            flash_amt=round(rng.uniform(0.30, 0.55), 2),
            trans_in=trans_in[i], td_in=td_in[i],
        ))

    for s in shots:
        log(f"Shot {s['idx'] + 1}/{n} [{s['phase']}] {s['dur']:.2f}s | {s['layout']} + {s['motion']} | "
            f"speed={s['speed']} | in: {s['trans_in'] or '-'}"
            f"{' | flash' if s['flash'] else ''}{' | glitch' if s['glitch'] else ''}")
    return {"shots": shots, "bounds": bounds, "drop": D, "beats": beats}


def _layout_fragment(layout: str, dur: float, direction: int) -> str:
    bg = ("scale=270:480:force_original_aspect_ratio=increase,crop=270:480,gblur=sigma=5,"
          "scale=1080:1920,eq=brightness=-0.05:saturation=1.2")
    if layout == "fill_crop":
        return "[rv]scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920,setsar=1[composed]"
    if layout == "fill_pan":
        prog = f"min(1,t/{max(0.5, dur):.2f})"
        p = f"0.5+0.30*{direction}*(2*{prog}-1)" if direction else "0.5"
        return ("[rv]scale=1080:1920:force_original_aspect_ratio=increase,"
                f"crop=1080:1920:x='(iw-ow)*({p})':y='(ih-oh)/2',setsar=1[composed]")
    if layout == "center_zoom":
        return (f"[rv]split=2[bg_in][fg_in];[bg_in]{bg}[bg_v];"
                "[fg_in]crop='trunc(iw*0.72/2)*2':'trunc(ih*0.72/2)*2',"
                "scale=1080:1920:force_original_aspect_ratio=decrease,setsar=1[fg_v];"
                "[bg_v][fg_v]overlay=(W-w)/2:(H-h)/2,setsar=1[composed]")
    return (f"[rv]split=2[bg_in][fg_in];[bg_in]{bg}[bg_v];"
            "[fg_in]scale=1080:1920:force_original_aspect_ratio=decrease,setsar=1[fg_v];"
            "[bg_v][fg_v]overlay=(W-w)/2:(H-h)/2,setsar=1[composed]")


def _motion_chain(sh: dict) -> str:
    m, d, bt, k = sh["motion"], sh["dur"], sh["beat_t"], sh["intensity"]
    direction = sh["dir"]
    n = max(8, int(d * OUT_FPS))
    ease = f"(1-pow(1-min(1,on/{n}),3))"
    zp = ":d=1:s=1080x1920:fps=30"
    centre = ":x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)'"
    up = "scale=1620:2880:flags=bilinear"

    if m == "punch_in":
        return f"{up},zoompan=z='1+{0.18 * k:.3f}*{ease}'{centre}{zp}"
    if m == "pull_out":
        a = 0.20 * k
        return f"{up},zoompan=z='{1 + a:.3f}-{a:.3f}*{ease}'{centre}{zp}"
    if m == "beat_pulse":
        per = max(6, int(round(sh["period"] * OUT_FPS)))
        bt_f = int(round(bt * OUT_FPS))
        return (f"{up},zoompan=z='1+{0.075 * k:.3f}*pow(max(0,1-mod(on-{bt_f}+{per * 400},{per})/({per}*0.7)),2)'"
                f"{centre}{zp}")
    if m == "drift_pan":
        prog = f"min(1,on/{n})"
        px = prog if direction > 0 else f"(1-{prog})"
        return (f"{up},zoompan=z='1.16+{0.04 * k:.3f}*{prog}':x='(iw-iw/zoom)*{px}':y='ih/2-(ih/zoom/2)'{zp}")
    if m == "shake_hit":
        a = 20.0 * k
        decay = f"gte(t,{bt})*exp(-7*(t-{bt}))"
        return (f"scale=1188:2112,crop=1080:1920:x='54+{a:.1f}*{decay}*sin(55*t)':"
                f"y='96+{a:.1f}*{decay}*cos(61*t)'")
    if m == "dutch_roll":
        per = max(1.2, d * 1.6)
        return (f"scale=1296:2304,rotate=a='{direction * 0.06:.3f}*sin(2*PI*t/{per:.2f})':ow=1296:oh=2304:c=black,"
                "crop=1080:1920")
    if m == "snap_roll":
        return (f"scale=1404:2496,rotate=a='{direction * 0.13:.3f}*pow(max(0,1-max(0,t-{bt})/0.45),2)':"
                "ow=1404:oh=2496:c=black,crop=1080:1920")
    if m == "whip_in":
        off = 54 * direction
        fall = f"pow(max(0,1-max(0,t-{bt})/0.26),2)"
        return (f"scale=1296:2304,crop=1080:1920:x='54+{off}*{fall}':y=96,"
                f"avgblur=sizeX=61:sizeY=1:enable='lt(t,{bt + 0.2:.2f})'")
    return "null"


def _build_shot_graph(sh: dict, audio_label: str, video_label: str, silent: bool, grade: str) -> str:
    d, bt = sh["dur"], sh["beat_t"]
    segs = SPEED_PROFILES[sh["speed"]]
    ramped = not (len(segs) == 1 and segs[0][1] == 1.0)
    parts: List[str] = []

    if not ramped:
        parts.append(f"{video_label}setpts=PTS-STARTPTS,fps={OUT_FPS}[rv]")
    else:
        cnt = len(segs)
        parts.append(f"{video_label}setpts=PTS-STARTPTS,split={cnt}" + "".join(f"[vs{i}]" for i in range(cnt)))
        pos = 0.0
        for i, (frac, spd) in enumerate(segs):
            src_d = frac * d * spd
            parts.append(f"[vs{i}]trim=start={pos:.3f}:end={pos + src_d:.3f},setpts=(PTS-STARTPTS)/{spd}[vt{i}]")
            pos += src_d
        parts.append("".join(f"[vt{i}]" for i in range(cnt)) + f"concat=n={cnt}:v=1:a=0,fps={OUT_FPS}[rv]")

    parts.append(_layout_fragment(sh["layout"], d, sh["dir"]))
    parts.append(f"[composed]{_motion_chain(sh)}[moved]")

    fx: List[str] = []
    if sh["flash"]:
        fx.append(f"eq=brightness='{sh['flash_amt']}*gte(t,{bt})*max(0,1-(t-{bt})/0.14)':eval=frame")
    if sh["glitch"]:
        win = f"between(t,{bt},{bt + 0.2:.2f})"
        fx.append(f"rgbashift=rh=-16:bh=16:gv=6:enable='{win}'")
        fx.append(f"noise=alls=28:allf=t:enable='{win}'")
    parts.append(f"[moved]{','.join(fx) if fx else 'null'}[fx]")

    last = "[fx]"
    if sh["bloom"]:
        parts.append("[fx]split=2[ba][bb];[bb]scale=270:480,gblur=sigma=8,scale=1080:1920[bbl];"
                     "[ba][bbl]blend=all_mode=screen:all_opacity=0.30[bl]")
        last = "[bl]"
    parts.append(f"{last}{GRADE_FILTERS.get(grade, GRADE_FILTERS['high_contrast_hud'])},vignette=angle=PI/5,"
                 f"noise=alls=2:allf=t,format=yuv420p,setsar=1,trim=0:{d:.3f},setpts=PTS-STARTPTS[vout]")

    afmt = "aformat=sample_fmts=fltp:sample_rates=44100:channel_layouts=stereo"
    if silent:
        parts.append(f"{audio_label}{afmt},atrim=0:{d:.3f},asetpts=PTS-STARTPTS[aout]")
    else:
        tail = (f"highpass=f=85,equalizer=f=3400:width_type=q:w=1.5:g=2.5,"
                f"acompressor=threshold=-17dB:ratio=3.2:attack=10:release=110,{afmt},"
                f"atrim=0:{d:.3f},asetpts=PTS-STARTPTS")
        if not ramped:
            parts.append(f"{audio_label}aresample=async=1:first_pts=0,asetpts=PTS-STARTPTS,{tail}[aout]")
        else:
            cnt = len(segs)
            parts.append(f"{audio_label}aresample=async=1:first_pts=0,asetpts=PTS-STARTPTS,asplit={cnt}"
                         + "".join(f"[as{i}]" for i in range(cnt)))
            pos = 0.0
            for i, (frac, spd) in enumerate(segs):
                src_d = frac * d * spd
                parts.append(f"[as{i}]atrim=start={pos:.3f}:end={pos + src_d:.3f},asetpts=PTS-STARTPTS,"
                             f"atempo={spd}[at{i}]")
                pos += src_d
            parts.append("".join(f"[at{i}]" for i in range(cnt)) + f"concat=n={cnt}:v=0:a=1,{tail}[aout]")
    return ";".join(parts)


def _input_args(url: str, start: float, length: float) -> List[str]:
    args: List[str] = []
    if url.lower().startswith(("http://", "https://")):
        args += ["-reconnect", "1", "-reconnect_streamed", "1", "-reconnect_delay_max", "5"]
    args += ["-ss", f"{start:.2f}", "-t", f"{length:.2f}", "-i", url]
    return args


_SHOT_ENC = ["-r", str(OUT_FPS), "-c:v", "libx264", "-profile:v", "high", "-level", "4.1",
             "-pix_fmt", "yuv420p", "-preset", "veryfast", "-crf", "18",
             "-c:a", "aac", "-ar", "44100", "-ac", "2", "-b:a", "192k"]


def _render_shot(sh: dict, stream_urls: List[str], dst: Path, grade: str) -> bool:
    d = sh["dur"]
    min_ok = max(0.4, d * 0.85)
    simple = {**sh, "speed": "none", "motion": "static_hold", "layout": "blur_fit",
              "flash": False, "glitch": False, "bloom": False}

    def run(plan: dict, silent: bool) -> bool:
        _safe_unlink(dst)
        src_len = plan["src_len"]
        inputs = _input_args(stream_urls[0], plan["src_start"], src_len)
        if silent:
            inputs += ["-f", "lavfi", "-t", f"{d:.2f}", "-i", "anullsrc=channel_layout=stereo:sample_rate=44100"]
            a_label = "[1:a]"
        elif len(stream_urls) >= 2:
            inputs += _input_args(stream_urls[1], plan["src_start"], src_len)
            a_label = "[1:a]"
        else:
            a_label = "[0:a]"
        graph = _build_shot_graph(plan, a_label, "[0:v]", silent, grade)
        cmd = ["ffmpeg", "-y", "-v", "error", *inputs, "-filter_complex", graph,
               "-map", "[vout]", "-map", "[aout]", "-t", f"{d:.2f}", *_SHOT_ENC, str(dst)]
        try:
            res = subprocess.run(cmd, capture_output=True, text=True, timeout=180)
            if res.returncode != 0:
                return False
            probed = _probe_media_duration(dst) if dst.is_file() else None
            return bool(probed is not None and probed >= min_ok and dst.stat().st_size > 1024)
        except subprocess.TimeoutExpired:
            return False

    for plan, silent in ((sh, False), (sh, False), (simple, False), (simple, True)):
        if run(plan, silent):
            return True
        time.sleep(0.4)
    _safe_unlink(dst)
    return False


def _merge_shots_xfade(
    shot_files: List[Path], durs: List[float], trans: List[str], tds: List[float],
    out_path: Path, total_target: float,
) -> bool:
    n = len(shot_files)
    if n == 0:
        return False
    if n == 1:
        try:
            shutil.copy2(str(shot_files[0]), str(out_path))
            return out_path.is_file()
        except Exception:
            return False

    inputs: List[str] = []
    for f in shot_files:
        inputs += ["-i", str(f)]
    v_parts: List[str] = []
    a_parts: List[str] = []
    v_lab, a_lab = "[0:v]", "[0:a]"
    acc = durs[0]
    for k in range(1, n):
        td = max(0.06, min(tds[k - 1], 0.45 * min(acc, durs[k])))
        off = max(0.0, acc - td)
        v_parts.append(f"{v_lab}[{k}:v]xfade=transition={trans[k - 1]}:duration={td:.3f}:offset={off:.3f}[xv{k}]")
        a_parts.append(f"{a_lab}[{k}:a]acrossfade=d={td:.3f}:c1=tri:c2=tri[xa{k}]")
        v_lab, a_lab = f"[xv{k}]", f"[xa{k}]"
        acc = acc + durs[k] - td

    cmd = ["ffmpeg", "-y", "-v", "error", *inputs,
           "-filter_complex", ";".join(v_parts + a_parts),
           "-map", v_lab, "-map", a_lab, "-t", f"{total_target:.2f}", *_SHOT_ENC, str(out_path)]
    try:
        res = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
        return res.returncode == 0 and out_path.is_file() and out_path.stat().st_size > 1024
    except Exception:
        return False


def _merge_shots_concat(shot_files: List[Path], out_path: Path, total_target: float, work: Path) -> bool:
    concat_list = work / "shots.txt"
    concat_list.write_text("".join(f"file '{p.resolve().as_posix()}'\n" for p in shot_files), encoding="utf-8")
    try:
        res = subprocess.run(
            ["ffmpeg", "-y", "-v", "error", "-f", "concat", "-safe", "0", "-i", str(concat_list),
             "-t", f"{total_target:.2f}", *_SHOT_ENC, str(out_path)],
            capture_output=True, text=True, timeout=240,
        )
        return res.returncode == 0 and out_path.is_file()
    except Exception:
        return False


def _generate_spoken_pop_captions(
    video_path: Path,
    ass_path: Path,
    edit_style: str = DEFAULT_EDIT_STYLE,
    on_progress: Optional[Callable[[str], None]] = None,
) -> bool:
    def log(msg: str) -> None:
        if on_progress:
            try:
                on_progress(msg)
            except Exception:
                pass

    pop_slots: List[Tuple[float, float, str]] = []
    try:
        from faster_whisper import WhisperModel

        model = WhisperModel("base", device="cpu", compute_type="int8")
        segments, _ = model.transcribe(str(video_path), vad_filter=True, word_timestamps=True)

        spoken_words: List[Tuple[float, float, str]] = []
        for seg in segments:
            words = getattr(seg, "words", None)
            if words:
                for w in words:
                    raw_w = re.sub(r"[^A-Za-z0-9'?!-]+", "", (w.word or "").strip())
                    if not raw_w:
                        continue
                    ws = float(w.start if w.start is not None else (seg.start or 0.0))
                    we = float(w.end if w.end is not None else (ws + 0.35))
                    if we <= ws:
                        we = ws + 0.35
                    spoken_words.append((ws, we, raw_w.upper()))
            else:
                txt_words = [
                    re.sub(r"[^A-Za-z0-9'?!-]+", "", tok).upper()
                    for tok in (seg.text or "").strip().split()
                ]
                txt_words = [tok for tok in txt_words if tok]
                if not txt_words:
                    continue
                s_t = float(seg.start or 0.0)
                e_t = float(seg.end or (s_t + 1.0))
                step = max(0.25, (e_t - s_t) / len(txt_words))
                for i, tok in enumerate(txt_words):
                    ws = s_t + i * step
                    we = min(e_t, ws + step)
                    spoken_words.append((ws, we, tok))

        max_phrase_len = 20 if edit_style in {"gaming_pro", "davinci_pro"} else 16
        i = 0
        while i < len(spoken_words):
            w1_s, w1_e, w1_t = spoken_words[i]
            if i + 1 < len(spoken_words):
                w2_s, w2_e, w2_t = spoken_words[i + 1]
                if (w2_s - w1_e) <= 0.45 and (len(w1_t) + len(w2_t)) <= max_phrase_len:
                    pop_slots.append((round(w1_s, 2), round(max(w1_s + 0.30, w2_e), 2), f"{w1_t} {w2_t}"))
                    i += 2
                    continue
            pop_slots.append((round(w1_s, 2), round(max(w1_s + 0.30, w1_e), 2), w1_t))
            i += 1
    except Exception as exc:
        log(f"Speech caption note: {exc}")
        return False

    if not pop_slots:
        log("No spoken words detected in edit audio — skipping captions.")
        return False

    def fmt_ass_time(sec: float) -> str:
        cs = int(round(max(0.0, sec) * 100))
        h = cs // 360000
        m = (cs % 360000) // 6000
        s = (cs % 6000) // 100
        rem = cs % 100
        return f"{h}:{m:02d}:{s:02d}.{rem:02d}"

    font_size = STYLE_PROFILES.get(edit_style, STYLE_PROFILES[DEFAULT_EDIT_STYLE])["caption_size"]
    margin_v = 370

    header = (
        "[Script Info]\n"
        "ScriptType: v4.00+\n"
        "PlayResX: 1080\n"
        "PlayResY: 1920\n\n"
        "[V4+ Styles]\n"
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, "
        "Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, "
        "Shadow, Alignment, MarginL, MarginR, MarginV, Encoding\n"
        f"Style: Default,Arial,{font_size},&H00FFFFFF,&H0000FFFF,&H00000000,&H88000000,-1,0,0,0,"
        f"100,100,2,0,1,5,7,2,75,75,{margin_v},1\n\n"
        "[Events]\n"
        "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n"
    )
    lines = [header]
    accent = random.choice(CAPTION_ACCENTS)
    for idx, (st, en, txt) in enumerate(pop_slots):
        dur_ms = max(240, int((en - st) * 1000))
        pop_settle_ms = min(135, max(55, dur_ms // 2))
        fade_in_ms = 35
        fade_out_ms = min(125, max(45, dur_ms // 4))
        pop_scale = 122 if edit_style == "gaming_pro" else 116
        colour = f"\\1c{accent}" if idx % 3 == 2 else ""
        pop_tag = (
            f"{{\\an2{colour}\\fscx{pop_scale}\\fscy{pop_scale}\\t(0,{pop_settle_ms},\\fscx100\\fscy100)"
            f"\\fad({fade_in_ms},{fade_out_ms})}}{txt}"
        )
        lines.append(f"Dialogue: 0,{fmt_ass_time(st)},{fmt_ass_time(en)},Default,,0,0,0,,{pop_tag}\n")

    try:
        ass_path.write_text("".join(lines), encoding="utf-8")
        log(f"Generated {len(pop_slots)} clean captions ({edit_style}).")
        return ass_path.is_file()
    except Exception:
        return False


def _escape_subtitles_path(path: Path) -> str:
    s = str(path.resolve()).replace("\\", "/")
    return s.replace(":", "\\:")


def _hit_sfx_filters(src_label: str, times: List[float], vol: float, tag: str) -> Tuple[List[str], List[str]]:
    times = [t for t in times if t >= 0.0]
    if not times:
        return [], []
    afmt = "aformat=sample_fmts=fltp:sample_rates=44100:channel_layouts=stereo"
    parts: List[str] = []
    outs: List[str] = []
    if len(times) == 1:
        parts.append(f"{src_label}{afmt},adelay={int(times[0] * 1000)}:all=1,volume={vol}[{tag}0]")
        return parts, [f"[{tag}0]"]
    parts.append(f"{src_label}{afmt},asplit={len(times)}" + "".join(f"[{tag}s{i}]" for i in range(len(times))))
    for i, t in enumerate(times):
        parts.append(f"[{tag}s{i}]adelay={int(t * 1000)}:all=1,volume={vol}[{tag}{i}]")
        outs.append(f"[{tag}{i}]")
    return parts, outs


def _render_single_edit(
    edit_idx: int,
    total_edits: int,
    clip_pool_for_edit: List[Tuple[float, float]],
    stream_urls: List[str],
    work_dir: Path,
    edits_dir: Path,
    video_title: str,
    total_target_dur: float,
    fit_mode: str,
    auto_edit: bool,
    music_query: Optional[str],
    custom_music_path: Optional[str],
    sfx_file: Optional[Path],
    enable_captions: bool,
    cookies_path: Optional[Path],
    stop_event: Optional[threading.Event],
    edit_style: str = DEFAULT_EDIT_STYLE,
    grade_history: Optional[List[str]] = None,
    on_progress: Optional[Callable[[str], None]] = None,
    total_video_dur: float = 0.0,
    used_moments: Optional[List[float]] = None,
) -> Optional[str]:
    _ = fit_mode

    def log(msg: str) -> None:
        if on_progress:
            try:
                on_progress(f"[Edit {edit_idx}/{total_edits}] {msg}")
            except Exception:
                pass

    rng = random.Random()
    sub_work = work_dir / f"edit_{edit_idx:02d}_{uuid.uuid4().hex[:4]}"
    sub_work.mkdir(parents=True, exist_ok=True)
    used_moments = used_moments if used_moments is not None else []

    try:
        style = edit_style if edit_style in BASE_STYLES else rng.choice(BASE_STYLES)
        prof = STYLE_PROFILES[style]
        if edit_style == "auto_ultimate":
            log(f"Auto style picked: {style}")

        music_file: Optional[Path] = None
        if auto_edit:
            music_file = _acquire_rotating_music_track(
                work_dir=sub_work, rng=rng, music_query=music_query,
                custom_music_path=custom_music_path, cookies_path=cookies_path, on_progress=log,
            )
        music_start = 0.0
        beat_times: List[float] = []
        drop_time: Optional[float] = None
        if music_file and music_file.is_file():
            info = _analyze_music(music_file, total_target_dur, rng)
            if info:
                music_start, beat_times, drop_time = info["start"], info["beats"], info["drop"]
            log(f"Beat grid: {len(beat_times)} hits, music starts at {music_start:.1f}s"
                f"{f', drop @ {drop_time:.1f}s' if drop_time is not None else ''}.")

        plan = _plan_edit(
            source_clips=clip_pool_for_edit, beat_times=beat_times, drop_time=drop_time, rng=rng,
            total_target=total_target_dur, style=style, total_video_dur=total_video_dur,
            used_moments=used_moments, on_progress=log,
        )
        shots: List[dict] = plan["shots"]

        g_hist = grade_history if grade_history is not None else []
        grade_w = {g: prof["grade_w"].get(g, 0.25) for g in COLOR_GRADES}
        for g in g_hist[-2:]:
            grade_w[g] = grade_w.get(g, 1.0) * 0.15
        color_style = rng.choices(list(grade_w), weights=list(grade_w.values()), k=1)[0]
        g_hist.append(color_style)

        log(f"Rendering {len(shots)} shots [{style} / {color_style}]...")
        shot_paths = [sub_work / f"shot_{s['idx']:02d}.mp4" for s in shots]

        def job(i: int) -> bool:
            if stop_event and stop_event.is_set():
                return False
            return _render_shot(shots[i], stream_urls, shot_paths[i], color_style)

        with ThreadPoolExecutor(max_workers=max(1, SHOT_WORKERS)) as pool:
            results = list(pool.map(job, range(len(shots))))
        if stop_event and stop_event.is_set():
            log("Stopped by user.")
            return None

        ok_idx = [i for i, r in enumerate(results) if r]
        if not ok_idx:
            log("Could not render any shots.")
            return None
        if len(ok_idx) < len(shots):
            log(f"{len(shots) - len(ok_idx)} shot(s) failed and were skipped.")

        files = [shot_paths[i] for i in ok_idx]
        durs = [(_probe_media_duration(f) or shots[i]["dur"]) for f, i in zip(files, ok_idx)]
        trans = [shots[i]["trans_in"] or "fade" for i in ok_idx[1:]]
        tds = [shots[i]["td_in"] or 0.2 for i in ok_idx[1:]]

        merged_video = sub_work / "merged_shots.mp4"
        if not _merge_shots_xfade(files, durs, trans, tds, merged_video, total_target_dur):
            log("Transition merge failed — falling back to hard cuts.")
            if not _merge_shots_concat(files, merged_video, total_target_dur, sub_work):
                log("Failed to stitch slices.")
                return None

        actual_total_dur = min(total_target_dur, _probe_media_duration(merged_video) or total_target_dur)

        ass_file: Optional[Path] = None
        if enable_captions:
            cand_ass = sub_work / "spoken_pop_captions.ass"
            if _generate_spoken_pop_captions(merged_video, cand_ass, edit_style=style, on_progress=log):
                ass_file = cand_ass

        cut_times = [b for b in plan["bounds"][1:-1] if b < actual_total_dur - 0.3]
        drop_t = plan["drop"] if plan["drop"] in cut_times else None
        hit_times: List[float] = []
        if drop_t is not None:
            hit_times.append(drop_t)
        extra = [t for t in cut_times if t != drop_t]
        rng.shuffle(extra)
        hit_times += extra[: 5 - len(hit_times)]
        hit_times = sorted(hit_times)

        clean_tag = re.sub(r"[^A-Za-z0-9]+", "_", video_title).strip("_")[:20] or "edit"
        final_edit = (
            edits_dir
            / f"{clean_tag}_{style}_edit{edit_idx}_{int(round(actual_total_dur))}s_{uuid.uuid4().hex[:5]}_9x16.mp4"
        )
        tmp_final = sub_work / "final_bass_tmp.mp4"

        def final_pass(with_ass: Optional[Path]) -> bool:
            vf = "[0:v]setpts=PTS-STARTPTS,setsar=1"
            if with_ass and with_ass.is_file():
                vf += f",subtitles='{_escape_subtitles_path(with_ass)}'"
            out_st = max(0.0, actual_total_dur - 0.28)
            vf += f",fade=t=in:st=0:d=0.22,fade=t=out:st={out_st:.2f}:d=0.28[vout]"

            have_music = bool(music_file and music_file.is_file())
            inputs: List[str] = [
                "-i", str(merged_video),
                "-f", "lavfi", "-t", f"{actual_total_dur:.2f}", "-i", "anullsrc=channel_layout=stereo:sample_rate=44100",
            ]
            fp: List[str] = [vf]
            mix: List[str] = []
            vid_vol = "0.64" if have_music else "1.0"
            afmt = "aformat=sample_fmts=fltp:sample_rates=44100:channel_layouts=stereo"

            fp.append(
                f"[0:a]aresample=async=1:first_pts=0,asetpts=PTS-STARTPTS,highpass=f=80,"
                f"acompressor=threshold=-16dB:ratio=3:attack=8:release=110,{afmt},"
                f"atrim=0:{actual_total_dur:.2f},asetpts=PTS-STARTPTS,volume={vid_vol}[src_a]"
            )
            fp.append(f"[1:a]{afmt},atrim=0:{actual_total_dur:.2f},asetpts=PTS-STARTPTS,volume=0.001[pad_a]")
            nxt = 2
            if have_music:
                inputs += ["-stream_loop", "-1", "-ss", f"{music_start:.2f}", "-i", str(music_file)]
                m_idx, nxt = nxt, nxt + 1
                fp.append("[src_a]asplit=2[src_mix][src_sc]")
                fp.append(
                    f"[{m_idx}:a]aresample=async=1:first_pts=0,asetpts=PTS-STARTPTS,"
                    f"atrim=0:{actual_total_dur:.2f},asetpts=PTS-STARTPTS,"
                    f"equalizer=f=95:width_type=o:w=1.0:g=3.0,{afmt},"
                    f"volume=0.43,afade=t=in:st=0:d=0.40,afade=t=out:st={max(0.0, actual_total_dur - 1.1):.2f}:d=1.1[mus_raw];"
                    f"[mus_raw][src_sc]sidechaincompress=threshold=0.08:ratio=4:attack=20:release=200[mus_a]"
                )
                mix += ["[src_mix]", "[pad_a]", "[mus_a]"]
            else:
                mix += ["[src_a]", "[pad_a]"]

            if hit_times:
                if sfx_file and sfx_file.is_file():
                    inputs += ["-i", str(sfx_file)]
                    s_idx, nxt = nxt, nxt + 1
                    p, o = _hit_sfx_filters(f"[{s_idx}:a]", hit_times, 0.52, "sx")
                else:
                    inputs += ["-f", "lavfi", "-t", "0.9", "-i", "sine=frequency=52:sample_rate=44100"]
                    s_idx, nxt = nxt, nxt + 1
                    fp.append(f"[{s_idx}:a]afade=t=in:st=0:d=0.01,afade=t=out:st=0.06:d=0.8,volume=1.0[boom_src]")
                    p, o = _hit_sfx_filters("[boom_src]", hit_times, 0.8, "sx")
                fp += p
                mix += o

            fp.append(
                f"{''.join(mix)}amix=inputs={len(mix)}:duration=longest:dropout_transition=2,"
                f"atrim=0:{actual_total_dur:.2f},asetpts=PTS-STARTPTS,"
                f"loudnorm=I=-14:TP=-1.5:LRA=11,aresample=44100[aout]"
            )
            cmd = [
                "ffmpeg", "-y", "-v", "error", *inputs,
                "-filter_complex", ";".join(fp),
                "-map", "[vout]", "-map", "[aout]",
                "-t", f"{actual_total_dur:.2f}", "-r", str(OUT_FPS),
                "-c:v", "libx264", "-profile:v", "high", "-level", "4.1",
                "-pix_fmt", "yuv420p", "-preset", "veryfast", "-crf", "19",
                "-c:a", "aac", "-ar", "44100", "-ac", "2", "-b:a", "192k",
                "-movflags", "+faststart", str(tmp_final),
            ]
            try:
                _safe_unlink(tmp_final)
                r = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
                if r.returncode != 0:
                    log(f"Final mix note: {(r.stderr or '').strip().splitlines()[-1:] or 'ffmpeg error'}")
                return r.returncode == 0 and tmp_final.is_file()
            except Exception:
                return False

        ok = final_pass(ass_file)
        if not ok and ass_file is not None:
            log("Caption burn-in failed — retrying without captions.")
            ok = final_pass(None)
        if not ok:
            log("Failed to finalize edit.")
            return None

        tmp_final.replace(final_edit)
        probed = _probe_media_duration(final_edit)
        if probed is not None:
            log(f"Saved {probed:.2f}s edit (target {total_target_dur:.2f}s) -> {final_edit.name}")
        else:
            log(f"Saved {actual_total_dur:.1f}s edit -> {final_edit.name}")
        return str(final_edit)
    finally:
        _cleanup_dir(sub_work)


def detect_reactions_and_clip(
    source_input: str,
    custom_output_dir: Optional[str] = None,
    is_youtube: bool = True,
    clip_mode: str = "count",
    max_clips: int = 3,
    max_good_cap: int = 20,
    chunk_duration: float = DEFAULT_EDIT_LENGTH,
    edit_duration: Optional[float] = None,
    fit_mode: str = "blurred_bg",
    cookies_path: Optional[Path] = None,
    stop_event: Optional[threading.Event] = None,
    prompt_interactively: bool = False,
    on_progress: Optional[Callable[[str], None]] = None,
    auto_edit: bool = True,
    music_query: Optional[str] = None,
    custom_music_path: Optional[str] = None,
    enable_captions: bool = True,
    custom_windows: Optional[List[Tuple[float, float]]] = None,
    edit_style: str = DEFAULT_EDIT_STYLE,
    clip_slice_sec: Optional[float] = None,
) -> List[str]:
    def log(msg: str) -> None:
        if on_progress:
            try:
                on_progress(msg)
            except Exception:
                pass

    _ = is_youtube
    chosen_style = edit_style if edit_style in EDIT_STYLE_OPTIONS else DEFAULT_EDIT_STYLE
    normalized_mode = "all_good" if (clip_mode or "").strip().lower() in {"all_good", "all_best"} else "count"
    run_offset = _next_run_offset()

    edits_dir = get_edits_dir(custom_output_dir)
    get_final_edits_dir(custom_output_dir)
    work_dir = edits_dir / f"_bass_batch_{uuid.uuid4().hex[:6]}"
    work_dir.mkdir(parents=True, exist_ok=True)
    rng = random.Random()

    chosen_len = edit_duration if edit_duration is not None else chunk_duration
    total_target_dur = round(max(4.0, min(120.0, float(chosen_len))), 2)

    slice_hi = max(MIN_SOURCE_CLIP_SEC, min(60.0, float(clip_slice_sec))) if clip_slice_sec is not None else PEAK_CLIP_DURATION
    slice_lo = min(MIN_SOURCE_CLIP_SEC, slice_hi)

    num_edits = max(1, min(20, int(max_good_cap if normalized_mode == "all_good" else max_clips)))
    log(
        f"Generating {num_edits} [{chosen_style}] 9:16 edit(s) | "
        f"Mode: {'ALL BEST CLIPS' if normalized_mode == 'all_good' else f'LIMITED ({max_clips})'} | "
        f"Length: {total_target_dur:.1f}s."
    )

    try:
        heatmap, meta_dur, video_title = _fetch_metadata_no_download(
            source_input=source_input, cookies_path=cookies_path, on_progress=log,
        )

        source_clips: List[Tuple[float, float]] = []
        if custom_windows:
            for st, dur_val in custom_windows:
                st_f = max(0.0, float(st))
                d_f = max(2.0, min(60.0, float(dur_val)))
                source_clips.append((round(st_f, 2), round(d_f, 2)))
            source_clips = _ensure_varied_5_to_10s_clips(
                source_clips, total_dur=meta_dur, min_clips=max(6, len(source_clips)),
                min_len=min(2.0, slice_lo), max_len=max(slice_hi, 10.0), rng=rng,
            )
            log(f"Using {len(source_clips)} custom clip windows.")

        desired_pool = min(32, max(16 if normalized_mode == "all_good" else 14, num_edits * 5))
        if not source_clips and heatmap and meta_dur > 0:
            source_clips = _select_10s_peak_clips_from_heatmap(
                heatmap=heatmap, total_dur=meta_dur, clip_count=desired_pool,
                clip_len=slice_hi, min_slice_len=slice_lo, on_progress=log,
            )

        if not source_clips and meta_dur > 0:
            source_clips = _select_10s_peak_clips_from_stream(
                source_input=source_input, total_dur=meta_dur, clip_count=desired_pool,
                clip_len=slice_hi, min_slice_len=slice_lo, cookies_path=cookies_path, on_progress=log,
            )

        if not source_clips:
            source_clips = _ensure_varied_5_to_10s_clips(
                [], total_dur=meta_dur, min_clips=max(8, desired_pool // 2),
                min_len=slice_lo, max_len=slice_hi, rng=rng,
            )

        base_pool = _spread_clips_across_video(
            clips=source_clips, total_dur=meta_dur, clip_mode=normalized_mode,
            max_clips=max_clips, run_offset=run_offset, rng=rng, truncate=False,
        )
        if not base_pool:
            raise RuntimeError("No source clips available for editing.")
        log(f"Source pool: {len(base_pool)} scenes spread across the video.")

        stream_urls = _resolve_stream_urls(
            source_input=source_input, cookies_path=cookies_path, selector=BEST_FORMAT_SELECTOR,
        )
        if not stream_urls:
            raise RuntimeError("Could not resolve stream URLs for edit rendering.")

        sfx_files = _list_local_assets("sfx")
        sfx_file: Optional[Path] = rng.choice(sfx_files) if sfx_files else None

        generated_edits: List[str] = []
        grade_history: List[str] = []
        used_moments: List[float] = []
        for idx in range(num_edits):
            if stop_event and stop_event.is_set():
                log("Batch stopped by user.")
                break

            if idx > 0 and idx % 5 == 0:
                refreshed_urls = _resolve_stream_urls(
                    source_input=source_input, cookies_path=cookies_path, selector=BEST_FORMAT_SELECTOR,
                )
                if refreshed_urls:
                    stream_urls = refreshed_urls

            shift = ((run_offset + idx) * 3) % len(base_pool)
            pool_for_this_edit = base_pool[shift:] + base_pool[:shift]

            out_path = _render_single_edit(
                edit_idx=idx + 1, total_edits=num_edits, clip_pool_for_edit=pool_for_this_edit,
                stream_urls=stream_urls, work_dir=work_dir, edits_dir=edits_dir,
                video_title=video_title, total_target_dur=total_target_dur, fit_mode=fit_mode,
                auto_edit=auto_edit, music_query=music_query, custom_music_path=custom_music_path,
                sfx_file=sfx_file, enable_captions=enable_captions, cookies_path=cookies_path,
                stop_event=stop_event, edit_style=chosen_style, grade_history=grade_history,
                on_progress=log, total_video_dur=meta_dur, used_moments=used_moments,
            )
            if out_path:
                generated_edits.append(out_path)

        if not generated_edits:
            raise RuntimeError("Could not generate any edits.")

        if prompt_interactively:
            select_and_move_final_edits(custom_output_dir)

        return generated_edits
    finally:
        _cleanup_dir(work_dir)


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1].lower() in {"pick", "final", "choose"}:
        select_and_move_final_edits()
    elif len(sys.argv) > 1:
        url_arg = sys.argv[1]
        count_arg = int(sys.argv[2]) if len(sys.argv) > 2 and sys.argv[2].isdigit() else 3
        dur_arg = float(sys.argv[3]) if len(sys.argv) > 3 else DEFAULT_EDIT_LENGTH
        style_arg = sys.argv[4].lower() if len(sys.argv) > 4 and sys.argv[4].lower() in EDIT_STYLE_OPTIONS else DEFAULT_EDIT_STYLE
        detect_reactions_and_clip(
            source_input=url_arg,
            max_clips=count_arg,
            edit_duration=dur_arg,
            edit_style=style_arg,
            on_progress=print,
            prompt_interactively=True,
        )
    else:
        select_and_move_final_edits()
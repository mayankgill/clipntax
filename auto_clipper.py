import array
import json
import math
import random
import re
import shutil
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
EDIT_STYLE_OPTIONS = ("gaming_pro", "davinci_pro", "shorts_hype", "beat_montage")
MIN_SOURCE_CLIP_SEC = 5.0
MAX_SOURCE_CLIP_SEC = 10.0
PEAK_CLIP_DURATION = 10.0

RUN_COUNTER_LOCK = threading.Lock()
GLOBAL_RUN_COUNTER = 0

MUSIC_QUERIES_POOL = [
    "sem demora super slowed edit beat no copyright",
    "super slowed phonk gaming edit beat instrumental",
    "cinematic slowed synth edit beat royalty free",
    "dark slowed bass gaming montage beat",
    "atmospheric slowed edit beat no copyright",
]

FLOW_MOTIONS = [
    "quaternion_roll",
    "whip_swish_flow",
    "spiral_dolly_in",
    "elastic_tilt_curve",
    "optical_smear_pan",
    "orbital_figure8",
    "anamorphic_stretch",
    "pendulum_arc",
]

FLOW_TRANSITIONS = [
    "vector_morph_wipe",
    "chromatic_dispersion",
    "luma_flow_dissolve",
    "portal_zoom_through",
    "shockwave_bend",
    "exposure_bloom_cut",
    "sub_impact_pulse",
    "velvet_cross_flow",
]

COLOR_GRADES = [
    "cyber_neon",
    "teal_orange",
    "halation_cinema",
    "high_contrast_hud",
]


class EffectDeck:
    """
    Draws randomized motion and transition styles without repeating any single
    motion or transition within the same edit.
    """

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
        if folder.is_dir():
            for p in folder.iterdir():
                if p.is_file():
                    _safe_unlink(p)
            folder.rmdir()
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

    # Stratify extra samples across the entire video duration so early/mid/late scenes are included
    if total_dur > lo and len(cleaned) < min_clips:
        needed = min_clips - len(cleaned)
        for s_idx in range(needed * 3):
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

    # Divide timeline into zones so peaks from beginning, middle, and end all enter the pool
    num_zones = min(8, target_pool)
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
        res = subprocess.run(cmd, capture_output=True, timeout=60)
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
        res = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
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
        audio_b = fut_audio.result(timeout=75)
        motion_b = fut_motion.result(timeout=75)

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

    # Pick top peak from each zone of the video first so clips come from all over the video
    num_zones = min(8, target_pool)
    zone_size = max(1, (max_start_b + 1) // num_zones)
    for z in range(num_zones):
        z_lo = z * zone_size
        z_hi = (max_start_b + 1) if z == num_zones - 1 else min(max_start_b + 1, (z + 1) * zone_size)
        zone_cands = sorted(scored_by_bucket[z_lo:z_hi], key=lambda item: item[1], reverse=True)
        for _, _, st in zone_cands:
            if all(abs(st - prev_st) >= min_gap for _, prev_st in chosen):
                c_len = round(min(total_dur - st, rng.uniform(lo, hi)), 2)
                chosen.append((st, max(2.0, c_len)))
                break

    # Fill any remaining slots with overall high-scoring moments across the video
    overall_cands = sorted(scored_by_bucket, key=lambda item: item[1], reverse=True)
    for _, _, st in overall_cands:
        if len(chosen) >= target_pool:
            break
        if all(abs(st - prev_st) >= min_gap for _, prev_st in chosen):
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
) -> List[Tuple[float, float]]:
    """
    Distributes clips across the full video timeline and shifts selection by
    run_offset so multiple edits from the same video pull different scenes.
    """
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

    if clip_mode == "count":
        limit = max(1, min(len(ordered), int(max_clips)))
        return ordered[:limit]

    pool = ordered.copy()
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
        subprocess.run(cmd, capture_output=True, text=True, timeout=45)
        for cand in work_dir.glob(f"{music_out.stem}.*"):
            if cand.is_file() and cand.stat().st_size > 1024:
                return cand
    except Exception:
        pass
    return None


def _detect_beat_and_bass_drops(audio_path: Path, max_sec: float = 45.0) -> List[float]:
    sample_rate = 8000
    hop_sec = 0.05
    cmd = [
        "ffmpeg", "-v", "error", "-t", f"{max_sec:.2f}", "-i", str(audio_path),
        "-vn", "-ac", "1", "-ar", str(sample_rate),
        "-af", "lowpass=f=150,highpass=f=28",
        "-f", "s16le", "pipe:1",
    ]
    try:
        res = subprocess.run(cmd, capture_output=True, timeout=20)
        raw = res.stdout or b""
        if len(raw) < 4:
            return []
        samples = array.array("h")
        samples.frombytes(raw[: len(raw) - (len(raw) % 2)])
        hop_len = max(1, int(sample_rate * hop_sec))
        env: List[float] = []
        for i in range(0, len(samples) - hop_len, hop_len):
            chunk = samples[i : i + hop_len]
            rms = math.sqrt(sum(float(x) * float(x) for x in chunk) / len(chunk)) / 32768.0
            env.append(rms)

        if len(env) < 8:
            return []

        beats: List[float] = []
        for idx in range(2, len(env) - 2):
            local_avg = sum(env[max(0, idx - 10) : min(len(env), idx + 11)]) / 21.0
            if env[idx] >= env[idx - 1] and env[idx] >= env[idx + 1]:
                if env[idx] > max(0.045, local_avg * 1.28):
                    t_sec = round(idx * hop_sec, 2)
                    if not beats or (t_sec - beats[-1]) >= 0.45:
                        beats.append(t_sec)
        return beats
    except Exception:
        return []


def _build_random_dropped_beat_shots(
    source_clips: List[Tuple[float, float]],
    beat_times: List[float],
    rng: random.Random,
    target_total_dur: float = DEFAULT_EDIT_LENGTH,
    edit_style: str = DEFAULT_EDIT_STYLE,
    on_progress: Optional[Callable[[str], None]] = None,
) -> List[Tuple[float, float, float, str, str]]:
    def log(msg: str) -> None:
        if on_progress:
            try:
                on_progress(msg)
            except Exception:
                pass

    exact_target = round(max(2.0, float(target_total_dur)), 2)
    if not source_clips:
        return []

    min_cut_gap = 2.5 if edit_style in {"gaming_pro", "davinci_pro", "shorts_hype"} else 2.2
    min_slot_len = 2.2

    cut_points: List[float] = [0.0]
    for b_t in beat_times:
        if 2.3 <= b_t <= exact_target - 1.8:
            if (b_t - cut_points[-1]) >= min_cut_gap:
                cut_points.append(round(b_t, 2))

    if len(cut_points) < 3:
        t_cur = 0.0
        paced_steps = [4.2, 3.6, 4.0, 3.4, 3.8]
        rng.shuffle(paced_steps)
        s_i = 0
        while t_cur + 2.5 < exact_target and len(cut_points) < 8:
            t_cur = round(min(exact_target, t_cur + paced_steps[s_i % len(paced_steps)]), 2)
            if t_cur - cut_points[-1] >= min_cut_gap:
                cut_points.append(t_cur)
            s_i += 1

    if cut_points[-1] < exact_target:
        if exact_target - cut_points[-1] < min_slot_len and len(cut_points) > 1:
            cut_points[-1] = exact_target
        else:
            cut_points.append(exact_target)
    else:
        cut_points[-1] = exact_target

    raw_slots: List[float] = []
    for i in range(len(cut_points) - 1):
        d = round(cut_points[i + 1] - cut_points[i], 2)
        if d >= min_slot_len:
            raw_slots.append(d)
        elif raw_slots:
            raw_slots[-1] = round(raw_slots[-1] + d, 2)

    if not raw_slots:
        raw_slots = [exact_target]

    cur_sum = round(sum(raw_slots), 2)
    diff = round(exact_target - cur_sum, 2)
    if diff > 0:
        raw_slots[-1] = round(raw_slots[-1] + diff, 2)
    elif diff < 0:
        need_trim = abs(diff)
        for idx in range(len(raw_slots) - 1, -1, -1):
            if need_trim <= 0.0:
                break
            can_take = max(0.0, round(raw_slots[idx] - min_slot_len, 2))
            take = min(need_trim, can_take)
            raw_slots[idx] = round(raw_slots[idx] - take, 2)
            need_trim = round(need_trim - take, 2)
        if need_trim > 0:
            raw_slots[-1] = round(max(1.6, raw_slots[-1] - need_trim), 2)

    clip_order = source_clips.copy()
    last_clip_idx: Optional[int] = None
    used_clip_indices: Set[int] = set()

    motion_deck = EffectDeck(FLOW_MOTIONS, rng)
    trans_deck = EffectDeck(FLOW_TRANSITIONS, rng)

    shots: List[Tuple[float, float, float, str, str]] = []
    for idx, slice_len in enumerate(raw_slots):
        candidates_idx = [i for i in range(len(clip_order)) if i != last_clip_idx]
        if not candidates_idx:
            candidates_idx = list(range(len(clip_order)))

        unused_candidates = [i for i in candidates_idx if i not in used_clip_indices]
        pool_choices = unused_candidates if unused_candidates else candidates_idx
        c_idx = rng.choice(pool_choices)
        last_clip_idx = c_idx
        used_clip_indices.add(c_idx)
        if len(used_clip_indices) >= len(clip_order):
            used_clip_indices.clear()

        clip_start, clip_avail = clip_order[c_idx]
        usable_len = min(clip_avail, slice_len)
        max_drop = max(0.0, clip_avail - usable_len)
        random_drop_sec = round(rng.uniform(0.0, max_drop), 2) if max_drop > 0 else 0.0
        actual_start = round(clip_start + random_drop_sec, 2)

        m_pick = motion_deck.draw_unique()
        t_pick = trans_deck.draw_unique()
        effect_tag = f"{m_pick}|{t_pick}"

        phase = "buildup" if idx == 0 else "bass_drop"
        pulse_t = round(min(0.18, slice_len * 0.16), 2)
        shots.append((actual_start, round(slice_len, 2), pulse_t, effect_tag, phase))

    total_built = round(sum(s[1] for s in shots), 2)
    log(
        f"Built {len(shots)} flowing [{edit_style}] slices ({total_built:.2f}s / {exact_target:.2f}s) "
        f"across {len(source_clips)} spread source clips."
    )
    return shots


def _build_blurred_bg_and_effect_vf(
    dur_sec: float,
    color_style: str,
    effect_tag: str,
    beat_pulse_t: float,
    is_first: bool,
    is_last: bool,
) -> str:
    if "|" in effect_tag:
        motion_style, trans_style = effect_tag.split("|", 1)
    else:
        motion_style, trans_style = "spiral_dolly_in", effect_tag

    t0 = f"{beat_pulse_t:.2f}"
    t1 = f"{min(dur_sec, beat_pulse_t + 0.30):.2f}"
    flow_span = f"{max(0.45, min(dur_sec * 0.65, 1.40)):.2f}"

    base_compose = (
        "[0:v]setpts=PTS-STARTPTS,split=2[bg_in][fg_in];"
        "[bg_in]scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920,gblur=sigma=24,eq=brightness=-0.04:saturation=1.15[bg_v];"
        "[fg_in]scale=1080:1920:force_original_aspect_ratio=decrease,setsar=1[fg_v];"
        "[bg_v][fg_v]overlay=(W-w)/2:(H-h)/2,setsar=1[composed]"
    )

    if motion_style == "quaternion_roll":
        motion_vf = f"[composed]rotate='0.26*sin(PI*min(1,t/{flow_span}))':fillcolor=none:ow=1080:oh=1920[moved]"
    elif motion_style == "whip_swish_flow":
        motion_vf = (
            f"[composed]tmix=frames=3:weights='0.22 0.36 0.42',"
            f"crop=1012:1799:34+30*sin(PI*min(1,t/{flow_span})):59:scale=1080:1920,setsar=1[moved]"
        )
    elif motion_style == "spiral_dolly_in":
        motion_vf = (
            f"[composed]crop=1040-48*sin(PI*0.5*min(1,t/{flow_span})):1848-85*sin(PI*0.5*min(1,t/{flow_span})):"
            f"20+24*sin(PI*0.5*min(1,t/{flow_span})):36+42*sin(PI*0.5*min(1,t/{flow_span})),"
            f"scale=1080:1920,setsar=1[moved]"
        )
    elif motion_style == "elastic_tilt_curve":
        motion_vf = f"[composed]rotate='0.12*sin(2*PI*t/{flow_span})*exp(-1.5*t/{flow_span})':fillcolor=none:ow=1080:oh=1920[moved]"
    elif motion_style == "optical_smear_pan":
        motion_vf = (
            f"[composed]tmix=frames=4:weights='0.15 0.25 0.30 0.30',"
            f"crop=1024:1820:28-20*cos(PI*min(1,t/{flow_span})):50:scale=1080:1920,setsar=1[moved]"
        )
    elif motion_style == "orbital_figure8":
        motion_vf = (
            f"[composed]crop=1036:1842:22+18*sin(2*PI*t/{flow_span}):40+14*sin(4*PI*t/{flow_span}),"
            f"scale=1080:1920,setsar=1[moved]"
        )
    elif motion_style == "anamorphic_stretch":
        motion_vf = f"[composed]lenscorrection=k1=-0.18:k2=-0.08:enable='between(t,0,{flow_span})',setsar=1[moved]"
    else:
        motion_vf = f"[composed]rotate='-0.10*cos(PI*min(1,t/{flow_span}))':fillcolor=none:ow=1080:oh=1920[moved]"

    if trans_style == "chromatic_dispersion":
        trans_vf = (
            f"[moved]split=3[r0][g0][b0];"
            f"[r0]rgbashift=rh=12:rv=-3:enable='between(t,{t0},{t1})'[r1];"
            f"[g0]rgbashift=gh=-8:gv=2:enable='between(t,{t0},{t1})'[g1];"
            f"[b0]rgbashift=bh=8:bv=5:enable='between(t,{t0},{t1})'[b1];"
            f"[r1][g1]blend=all_mode='addition'[rg];[rg][b1]blend=all_mode='screen',setsar=1[tr_v]"
        )
    elif trans_style == "luma_flow_dissolve":
        trans_vf = "[moved]fade=t=in:st=0:d=0.28,setsar=1[tr_v]"
    elif trans_style == "portal_zoom_through":
        trans_vf = f"[moved]fade=t=in:st=0:d=0.20,eq=contrast=1.08:enable='between(t,0,{t1})',setsar=1[tr_v]"
    elif trans_style == "shockwave_bend":
        trans_vf = (
            f"[moved]crop=1044:1856:22+12*sin(18*t)*between(t,{t0},{t1}):"
            f"38+10*cos(18*t)*between(t,{t0},{t1}),scale=1080:1920,setsar=1[tr_v]"
        )
    elif trans_style == "exposure_bloom_cut":
        trans_vf = f"[moved]fade=t=in:st=0:d=0.18,eq=brightness=0.09:enable='between(t,{t0},{t1})',setsar=1[tr_v]"
    elif trans_style == "sub_impact_pulse":
        trans_vf = f"[moved]fade=t=in:st=0:d=0.14,eq=contrast=1.12:brightness=0.04:enable='between(t,{t0},{t1})',setsar=1[tr_v]"
    elif trans_style == "velvet_cross_flow":
        trans_vf = "[moved]fade=t=in:st=0:d=0.32,setsar=1[tr_v]"
    else:
        trans_vf = "[moved]tmix=frames=2:weights='0.35 0.65',fade=t=in:st=0:d=0.16,setsar=1[tr_v]"

    if color_style == "cyber_neon":
        grade_str = "eq=contrast=1.18:brightness=0.02:saturation=1.34,colorbalance=rs=-0.04:gs=0.02:bs=0.12"
    elif color_style == "teal_orange":
        grade_str = "eq=contrast=1.16:brightness=0.015:saturation=1.28,colorbalance=rs=0.08:gs=0.01:bs=-0.08"
    elif color_style == "halation_cinema":
        grade_str = "eq=contrast=1.15:brightness=0.02:saturation=1.24,colorbalance=rs=0.07:gs=0.02:bs=-0.03"
    else:
        grade_str = "eq=contrast=1.20:brightness=0.02:saturation=1.26"

    end_chain = f"{grade_str},vignette=PI/5:0.24,noise=alls=3:allf=t"
    if is_first:
        end_chain += ",fade=t=in:st=0:d=0.22"
    if is_last:
        out_st = max(0.0, dur_sec - 0.28)
        end_chain += f",fade=t=out:st={out_st:.2f}:d=0.28"

    end_chain += f",trim=0:{dur_sec:.2f},setpts=PTS-STARTPTS[vout]"
    return f"{base_compose};{motion_vf};{trans_vf};[tr_v]{end_chain}"


def _extract_mixed_effect_shot(
    stream_urls: List[str],
    start_sec: float,
    dur_sec: float,
    dst_clip: Path,
    fit_mode: str,
    color_style: str,
    transition_style: str,
    beat_pulse_t: float,
    phase: str,
    is_first: bool,
    is_last: bool,
    edit_style: str = DEFAULT_EDIT_STYLE,
) -> bool:
    _ = (fit_mode, phase, edit_style)
    vf = _build_blurred_bg_and_effect_vf(
        dur_sec=dur_sec,
        color_style=color_style,
        effect_tag=transition_style,
        beat_pulse_t=beat_pulse_t,
        is_first=is_first,
        is_last=is_last,
    )
    min_acceptable_dur = max(0.4, dur_sec * 0.85)

    for _ in range(2):
        _safe_unlink(dst_clip)
        if len(stream_urls) >= 2:
            v_url, a_url = stream_urls[0], stream_urls[1]
            af = (
                f"[1:a]aresample=async=1:first_pts=0,asetpts=PTS-STARTPTS,"
                f"highpass=f=85,equalizer=f=3400:width_type=q:w=1.5:g=2.5,"
                f"acompressor=threshold=-17dB:ratio=3.2:attack=10:release=110,"
                f"aformat=sample_fmts=fltp:sample_rates=44100:channel_layouts=stereo,"
                f"atrim=0:{dur_sec:.2f},asetpts=PTS-STARTPTS,volume=1.0[aout]"
            )
            cmd = [
                "ffmpeg", "-y",
                "-reconnect", "1", "-reconnect_streamed", "1", "-reconnect_delay_max", "5",
                "-ss", f"{start_sec:.2f}", "-i", v_url,
                "-reconnect", "1", "-reconnect_streamed", "1", "-reconnect_delay_max", "5",
                "-ss", f"{start_sec:.2f}", "-i", a_url,
                "-filter_complex", f"{vf};{af}",
                "-map", "[vout]", "-map", "[aout]",
                "-t", f"{dur_sec:.2f}",
                "-r", "30",
                "-c:v", "libx264", "-profile:v", "high", "-level", "4.1",
                "-pix_fmt", "yuv420p", "-preset", "veryfast", "-crf", "19",
                "-c:a", "aac", "-ar", "44100", "-ac", "2", "-b:a", "160k",
                str(dst_clip),
            ]
        else:
            af = (
                f"[0:a]aresample=async=1:first_pts=0,asetpts=PTS-STARTPTS,"
                f"highpass=f=85,equalizer=f=3400:width_type=q:w=1.5:g=2.5,"
                f"acompressor=threshold=-17dB:ratio=3.2:attack=10:release=110,"
                f"aformat=sample_fmts=fltp:sample_rates=44100:channel_layouts=stereo,"
                f"atrim=0:{dur_sec:.2f},asetpts=PTS-STARTPTS,volume=1.0[aout]"
            )
            cmd = [
                "ffmpeg", "-y",
                "-reconnect", "1", "-reconnect_streamed", "1", "-reconnect_delay_max", "5",
                "-ss", f"{start_sec:.2f}", "-i", stream_urls[0],
                "-filter_complex", f"{vf};{af}",
                "-map", "[vout]", "-map", "[aout]",
                "-t", f"{dur_sec:.2f}",
                "-r", "30",
                "-c:v", "libx264", "-profile:v", "high", "-level", "4.1",
                "-pix_fmt", "yuv420p", "-preset", "veryfast", "-crf", "19",
                "-c:a", "aac", "-ar", "44100", "-ac", "2", "-b:a", "160k",
                str(dst_clip),
            ]

        try:
            res = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
            probed = _probe_media_duration(dst_clip) if (res.returncode == 0 and dst_clip.is_file()) else None
            if probed is not None and probed >= min_acceptable_dur and dst_clip.stat().st_size > 1024:
                return True
        except subprocess.TimeoutExpired:
            pass
        time.sleep(0.5)

    _safe_unlink(dst_clip)
    fallback_vf = (
        "[0:v]setpts=PTS-STARTPTS,split=2[bg_f][fg_f];"
        "[bg_f]scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920,gblur=sigma=24[bg_out];"
        "[fg_f]scale=1080:1920:force_original_aspect_ratio=decrease,setsar=1[fg_out];"
        f"[bg_out][fg_out]overlay=(W-w)/2:(H-h)/2,setsar=1,trim=0:{dur_sec:.2f},setpts=PTS-STARTPTS[vout]"
    )
    fallback_cmd = [
        "ffmpeg", "-y",
        "-reconnect", "1", "-reconnect_streamed", "1", "-reconnect_delay_max", "5",
        "-ss", f"{start_sec:.2f}", "-i", stream_urls[0],
        "-f", "lavfi", "-t", f"{dur_sec:.2f}", "-i", "anullsrc=channel_layout=stereo:sample_rate=44100",
        "-filter_complex",
        f"{fallback_vf};[1:a]aformat=sample_fmts=fltp:sample_rates=44100:channel_layouts=stereo,"
        f"atrim=0:{dur_sec:.2f},asetpts=PTS-STARTPTS[aout]",
        "-map", "[vout]", "-map", "[aout]",
        "-t", f"{dur_sec:.2f}",
        "-r", "30",
        "-c:v", "libx264", "-pix_fmt", "yuv420p", "-preset", "veryfast", "-crf", "20",
        "-c:a", "aac", "-ar", "44100", "-ac", "2", "-b:a", "160k",
        str(dst_clip),
    ]
    try:
        r_fb = subprocess.run(fallback_cmd, capture_output=True, text=True, timeout=120)
        return r_fb.returncode == 0 and dst_clip.is_file() and dst_clip.stat().st_size > 1024
    except Exception:
        _safe_unlink(dst_clip)
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
        from faster_whisper import WhisperModel  # type: ignore

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

    font_size = 86 if edit_style == "gaming_pro" else (78 if edit_style == "davinci_pro" else 92)
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
    for st, en, txt in pop_slots:
        dur_ms = max(240, int((en - st) * 1000))
        pop_settle_ms = min(135, max(55, dur_ms // 2))
        fade_in_ms = 35
        fade_out_ms = min(125, max(45, dur_ms // 4))
        pop_scale = 122 if edit_style == "gaming_pro" else 116
        pop_tag = (
            f"{{\\an2\\fscx{pop_scale}\\fscy{pop_scale}\\t(0,{pop_settle_ms},\\fscx100\\fscy100)"
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
) -> Optional[str]:
    def log(msg: str) -> None:
        if on_progress:
            try:
                on_progress(f"[Edit {edit_idx}/{total_edits}] {msg}")
            except Exception:
                pass

    rng = random.Random()
    sub_work = work_dir / f"edit_{edit_idx:02d}_{uuid.uuid4().hex[:4]}"
    sub_work.mkdir(parents=True, exist_ok=True)

    try:
        music_file: Optional[Path] = None
        if auto_edit:
            music_file = _acquire_rotating_music_track(
                work_dir=sub_work,
                rng=rng,
                music_query=music_query,
                custom_music_path=custom_music_path,
                cookies_path=cookies_path,
                on_progress=log,
            )

        beat_times: List[float] = []
        if music_file and music_file.is_file():
            beat_times = _detect_beat_and_bass_drops(music_file, max_sec=total_target_dur + 10.0)
            log(f"Detected {len(beat_times)} beat/bass hits.")

        shots = _build_random_dropped_beat_shots(
            source_clips=clip_pool_for_edit,
            beat_times=beat_times,
            rng=rng,
            target_total_dur=total_target_dur,
            edit_style=edit_style,
            on_progress=log,
        )
        if not shots:
            shots = [(clip_pool_for_edit[0][0], total_target_dur, 0.14, "spiral_dolly_in|vector_morph_wipe", "bass_drop")]

        g_hist = grade_history if grade_history is not None else []
        eligible_grades = [g for g in COLOR_GRADES if g not in g_hist[-1:]] or list(COLOR_GRADES)
        color_style = rng.choice(eligible_grades)
        g_hist.append(color_style)

        shot_files: List[Path] = []
        log(f"Rendering {len(shots)} [{edit_style} / {color_style}] flowing slices...")
        for s_idx, (st, dur, pulse_t, trans_style, phase) in enumerate(shots):
            if stop_event and stop_event.is_set():
                log("Stopped by user.")
                return None

            shot_path = sub_work / f"shot_{s_idx:02d}.mp4"
            ok = _extract_mixed_effect_shot(
                stream_urls=stream_urls,
                start_sec=st,
                dur_sec=dur,
                dst_clip=shot_path,
                fit_mode=fit_mode,
                color_style=color_style,
                transition_style=trans_style,
                beat_pulse_t=pulse_t,
                phase=phase,
                is_first=(s_idx == 0),
                is_last=(s_idx == len(shots) - 1),
                edit_style=edit_style,
            )
            if ok:
                shot_files.append(shot_path)

        if not shot_files:
            log("Could not render beat-synced slices.")
            return None

        concat_list = sub_work / "shots.txt"
        concat_list.write_text(
            "".join(f"file '{p.resolve().as_posix()}'\n" for p in shot_files),
            encoding="utf-8",
        )
        merged_video = sub_work / "merged_shots.mp4"
        r_merge = subprocess.run(
            [
                "ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", str(concat_list),
                "-t", f"{total_target_dur:.2f}",
                "-c:v", "libx264", "-preset", "veryfast", "-crf", "19",
                "-c:a", "aac", "-ar", "44100", "-ac", "2", "-b:a", "192k",
                str(merged_video),
            ],
            capture_output=True, text=True, timeout=180,
        )
        if r_merge.returncode != 0 or not merged_video.is_file():
            log("Failed to stitch slices.")
            return None

        actual_total_dur = total_target_dur
        ass_file: Optional[Path] = None
        if enable_captions:
            cand_ass = sub_work / "spoken_pop_captions.ass"
            if _generate_spoken_pop_captions(merged_video, cand_ass, edit_style=edit_style, on_progress=log):
                ass_file = cand_ass

        vf_chain = "[0:v]setpts=PTS-STARTPTS,setsar=1"
        if ass_file and ass_file.is_file():
            vf_chain = f"{vf_chain},subtitles='{_escape_subtitles_path(ass_file)}'"
        vf_chain = f"{vf_chain}[vout]"

        inputs: List[str] = [
            "-i", str(merged_video),
            "-f", "lavfi", "-t", f"{actual_total_dur:.2f}", "-i", "anullsrc=channel_layout=stereo:sample_rate=44100",
        ]
        filter_parts: List[str] = [vf_chain]
        mix_labels: List[str] = []

        vid_vol = "0.64" if (music_file and music_file.is_file()) else "1.0"
        mus_vol = "0.43"

        filter_parts.append(
            f"[0:a]aresample=async=1:first_pts=0,asetpts=PTS-STARTPTS,"
            f"highpass=f=80,acompressor=threshold=-16dB:ratio=3:attack=8:release=110,"
            f"aformat=sample_fmts=fltp:sample_rates=44100:channel_layouts=stereo,"
            f"atrim=0:{actual_total_dur:.2f},asetpts=PTS-STARTPTS,volume={vid_vol}[src_a]"
        )
        filter_parts.append(
            f"[1:a]aformat=sample_fmts=fltp:sample_rates=44100:channel_layouts=stereo,"
            f"atrim=0:{actual_total_dur:.2f},asetpts=PTS-STARTPTS,volume=0.001[pad_a]"
        )
        mix_labels.extend(["[src_a]", "[pad_a]"])

        next_idx = 2
        if music_file and music_file.is_file():
            inputs.extend(["-stream_loop", "-1", "-i", str(music_file)])
            m_idx = next_idx
            next_idx += 1
            filter_parts.append(
                f"[{m_idx}:a]aresample=async=1:first_pts=0,asetpts=PTS-STARTPTS,"
                f"atrim=0:{actual_total_dur:.2f},asetpts=PTS-STARTPTS,"
                f"equalizer=f=95:width_type=o:w=1.0:g=3.0,"
                f"aformat=sample_fmts=fltp:sample_rates=44100:channel_layouts=stereo,"
                f"volume={mus_vol},afade=t=in:st=0:d=0.40,afade=t=out:st={max(0.0, actual_total_dur - 1.1):.2f}:d=1.1[mus_raw];"
                f"[mus_raw][src_a]sidechaincompress=threshold=0.08:ratio=4:attack=20:release=200[mus_a]"
            )
            mix_labels.append("[mus_a]")

        if sfx_file and sfx_file.is_file() and beat_times:
            delays = [int(b * 1000) for b in beat_times[:7] if b < actual_total_dur]
            if delays:
                inputs.extend(["-i", str(sfx_file)])
                s_idx = next_idx
                next_idx += 1
                delay_str = "|".join(str(d) for d in delays)
                filter_parts.append(
                    f"[{s_idx}:a]aresample=async=1:first_pts=0,asetpts=PTS-STARTPTS,"
                    f"aformat=sample_fmts=fltp:sample_rates=44100:channel_layouts=stereo,"
                    f"adelay={delay_str}:all=1,volume=0.52[sfx_a]"
                )
                mix_labels.append("[sfx_a]")

        filter_parts.append(
            f"{''.join(mix_labels)}amix=inputs={len(mix_labels)}:duration=longest:dropout_transition=2,"
            f"atrim=0:{actual_total_dur:.2f},asetpts=PTS-STARTPTS,dynaudnorm=f=150:g=6[aout]"
        )

        clean_tag = re.sub(r"[^A-Za-z0-9]+", "_", video_title).strip("_")[:20] or "edit"
        final_edit = (
            edits_dir
            / f"{clean_tag}_{edit_style}_edit{edit_idx}_{int(round(actual_total_dur))}s_{uuid.uuid4().hex[:5]}_9x16.mp4"
        )
        tmp_final = sub_work / "final_bass_tmp.mp4"

        r_final = subprocess.run(
            [
                "ffmpeg", "-y",
                *inputs,
                "-filter_complex", ";".join(filter_parts),
                "-map", "[vout]", "-map", "[aout]",
                "-t", f"{actual_total_dur:.2f}",
                "-r", "30",
                "-c:v", "libx264", "-profile:v", "high", "-level", "4.1",
                "-pix_fmt", "yuv420p", "-preset", "veryfast", "-crf", "19",
                "-c:a", "aac", "-ar", "44100", "-ac", "2", "-b:a", "192k",
                "-movflags", "+faststart",
                str(tmp_final),
            ],
            capture_output=True, text=True, timeout=180,
        )
        if r_final.returncode != 0 or not tmp_final.is_file():
            log("Failed to finalize edit.")
            return None

        tmp_final.replace(final_edit)
        probed = _probe_media_duration(final_edit)
        if probed is not None:
            log(f"Saved {probed:.2f}s edit (target {actual_total_dur:.2f}s) -> {final_edit.name}")
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
            source_input=source_input,
            cookies_path=cookies_path,
            on_progress=log,
        )

        source_clips: List[Tuple[float, float]] = []
        if custom_windows:
            for st, dur_val in custom_windows:
                st_f = max(0.0, float(st))
                d_f = max(2.0, min(60.0, float(dur_val)))
                source_clips.append((round(st_f, 2), round(d_f, 2)))
            source_clips = _ensure_varied_5_to_10s_clips(
                source_clips,
                total_dur=meta_dur,
                min_clips=max(6, len(source_clips)),
                min_len=min(2.0, slice_lo),
                max_len=max(slice_hi, 10.0),
                rng=rng,
            )
            log(f"Using {len(source_clips)} custom clip windows.")

        desired_pool = max(10 if normalized_mode == "all_good" else 6, num_edits * 3)
        if not source_clips and heatmap and meta_dur > 0:
            source_clips = _select_10s_peak_clips_from_heatmap(
                heatmap=heatmap,
                total_dur=meta_dur,
                clip_count=desired_pool,
                clip_len=slice_hi,
                min_slice_len=slice_lo,
                on_progress=log,
            )

        if not source_clips and meta_dur > 0:
            source_clips = _select_10s_peak_clips_from_stream(
                source_input=source_input,
                total_dur=meta_dur,
                clip_count=desired_pool,
                clip_len=slice_hi,
                min_slice_len=slice_lo,
                cookies_path=cookies_path,
                on_progress=log,
            )

        if not source_clips:
            source_clips = _ensure_varied_5_to_10s_clips(
                [], total_dur=meta_dur, min_clips=8, min_len=slice_lo, max_len=slice_hi, rng=rng
            )

        spread_clips = _spread_clips_across_video(
            clips=source_clips,
            total_dur=meta_dur,
            clip_mode=normalized_mode,
            max_clips=max_clips,
            run_offset=run_offset,
            rng=rng,
        )

        stream_urls = _resolve_stream_urls(
            source_input=source_input,
            cookies_path=cookies_path,
            selector=BEST_FORMAT_SELECTOR,
        )
        if not stream_urls:
            raise RuntimeError("Could not resolve stream URLs for edit rendering.")

        sfx_files = _list_local_assets("sfx")
        sfx_file: Optional[Path] = rng.choice(sfx_files) if sfx_files else None

        base_pool = spread_clips.copy()
        if normalized_mode == "all_good":
            rng.shuffle(base_pool)

        slice_size = max(
            3 if normalized_mode == "count" else 5,
            min(len(base_pool), (len(base_pool) + num_edits - 1) // max(1, num_edits) + 2),
        )

        generated_edits: List[str] = []
        grade_history: List[str] = []
        for idx in range(num_edits):
            if stop_event and stop_event.is_set():
                log("Batch stopped by user.")
                break

            start_i = ((run_offset + idx) * 2) % len(base_pool)
            pool_for_this_edit = [
                base_pool[(start_i + k) % len(base_pool)] for k in range(slice_size)
            ]
            rng.shuffle(pool_for_this_edit)

            out_path = _render_single_edit(
                edit_idx=idx + 1,
                total_edits=num_edits,
                clip_pool_for_edit=pool_for_this_edit,
                stream_urls=stream_urls,
                work_dir=work_dir,
                edits_dir=edits_dir,
                video_title=video_title,
                total_target_dur=total_target_dur,
                fit_mode=fit_mode,
                auto_edit=auto_edit,
                music_query=music_query,
                custom_music_path=custom_music_path,
                sfx_file=sfx_file,
                enable_captions=enable_captions,
                cookies_path=cookies_path,
                stop_event=stop_event,
                edit_style=chosen_style,
                grade_history=grade_history,
                on_progress=log,
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
import array
import json
import math
import re
import shutil
import subprocess
import threading
import time
from pathlib import Path
from typing import Callable, Iterable, List, Optional, Tuple


DEFAULT_COOKIES_PATH = Path("cookies.txt")
BEST_FORMAT_SELECTOR = "bv*[ext=mp4]+ba[ext=m4a]/bv*+ba/b[ext=mp4]/best"
EDITS_FOLDER_NAME = "EDITS"


def get_edits_dir(base_output_dir: Optional[str] = None) -> Path:
    """
    Returns the single shared 'EDITS' folder for edits from any creator.
    """
    _ = base_output_dir
    project_root = Path(__file__).resolve().parent
    edits_dir = project_root / EDITS_FOLDER_NAME
    edits_dir.mkdir(parents=True, exist_ok=True)
    return edits_dir


def save_edits_to_folder(
    edit_files: Iterable[str],
    base_output_dir: Optional[str] = None,
    move_files: bool = True,
) -> List[str]:
    """
    Moves or copies edit files from any creator into the single shared 'EDITS' folder.
    """
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


def _extract_meaningful_error(text: str) -> str:
    lines = [ln.strip() for ln in (text or "").splitlines() if ln.strip()]
    skip_markers = ("last message repeated", "opening '", "for reading", "exiting normally, received signal")
    for ln in reversed(lines):
        low = ln.lower()
        if not any(m in low for m in skip_markers):
            return ln
    return lines[-1] if lines else ""


def _resolve_cookies(cookies_path: Optional[Path] = None) -> Optional[Path]:
    candidates = [
        Path(cookies_path) if cookies_path else None,
        DEFAULT_COOKIES_PATH,
        Path(__file__).resolve().parent / "cookies.txt",
    ]
    for c in candidates:
        if c and c.is_file() and c.stat().st_size > 0:
            return c
    return None


def _yt_base_args(
    cookies_path: Optional[Path] = None,
    player_client: Optional[str] = None,
) -> List[str]:
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
    if player_client:
        args.extend(["--extractor-args", f"youtube:player_client={player_client}"])
    resolved_cookies = _resolve_cookies(cookies_path)
    if resolved_cookies:
        args.extend(["--cookies", str(resolved_cookies)])
    return args


def _fetch_metadata_no_download(
    source_input: str,
    cookies_path: Optional[Path] = None,
    on_progress: Optional[Callable[[str], None]] = None,
) -> Tuple[List[dict], float]:
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
        res = subprocess.run(cmd, capture_output=True, text=True, timeout=25)
        if res.returncode == 0 and res.stdout:
            info = json.loads(res.stdout)
            dur = float(info.get("duration") or 0.0)
            heatmap = info.get("heatmap")
            if isinstance(heatmap, list) and len(heatmap) > 0:
                log(f"Found 'Most replayed' heatmap ({len(heatmap)} points) — no full download needed.")
                return heatmap, dur
            log("No heatmap metadata found — will scan stream peaks without saving full video.")
            return [], dur
        err = _extract_meaningful_error(res.stderr or res.stdout)
        if err:
            log(f"Metadata note: {err}")
    except subprocess.TimeoutExpired:
        log("Metadata lookup timed out after 25s.")
    except Exception as exc:
        log(f"Metadata error: {exc}")

    return [], 0.0


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
        res = subprocess.run(cmd, capture_output=True, text=True, timeout=25)
        if res.returncode == 0 and res.stdout:
            return [ln.strip() for ln in res.stdout.splitlines() if ln.strip().startswith("http")]
    except Exception:
        pass
    return []


def _select_windows_from_heatmap(
    heatmap: List[dict],
    total_dur: float,
    seg_dur: float,
    target_count: int,
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
    if effective_dur <= seg_dur:
        return [(0.0, max(1.0, round(effective_dur, 2)))]

    max_start = max(0.0, effective_dur - seg_dur)
    cand_set = {0.0, round(max_start, 2)}
    for st, en, _ in points:
        center = (st + en) * 0.5
        cand_set.add(round(max(0.0, min(max_start, center - seg_dur * 0.5)), 2))
        cand_set.add(round(max(0.0, min(max_start, st)), 2))

    step = max(2.0, min(8.0, max_start / 40.0)) if max_start > 0 else 2.0
    cur = 0.0
    while cur <= max_start and len(cand_set) < 80:
        cand_set.add(round(cur, 2))
        cur += step

    candidates = sorted(cand_set)

    def window_heat(w_start: float) -> Tuple[float, float]:
        w_end = w_start + seg_dur
        total_weight = 0.0
        weighted_sum = 0.0
        peak_val = 0.0
        for p_st, p_en, p_val in points:
            overlap = min(w_end, p_en) - max(w_start, p_st)
            if overlap > 0.0:
                weighted_sum += overlap * p_val
                total_weight += overlap
                if p_val > peak_val:
                    peak_val = p_val
        avg_val = (weighted_sum / total_weight) if total_weight > 0 else 0.0
        score = 0.65 * avg_val + 0.35 * peak_val
        return round(score, 4), round(peak_val, 4)

    scored = [(window_heat(st)[0], window_heat(st)[1], st) for st in candidates]
    scored.sort(key=lambda item: item[0], reverse=True)

    selected: List[Tuple[float, float, float]] = []
    min_gap = max(2.0, seg_dur * 0.75)
    for sc, pk, st in scored:
        if all(abs(st - prev_st) >= min_gap for _, _, prev_st in selected):
            selected.append((sc, pk, st))
            if len(selected) >= target_count:
                break

    if len(selected) < target_count:
        used = {st for _, _, st in selected}
        for sc, pk, st in scored:
            if st not in used:
                selected.append((sc, pk, st))
                used.add(st)
                if len(selected) >= target_count:
                    break

    selected.sort(key=lambda item: item[2])
    for idx, (sc, pk, st) in enumerate(selected, start=1):
        actual_d = round(min(seg_dur, effective_dur - st), 2)
        log(f"Iconic shot #{idx}: {st:.1f}s -> {st + actual_d:.1f}s (heat={sc:.2f}, peak={pk:.2f})")

    return [(st, round(min(seg_dur, effective_dur - st), 2)) for _, _, st in selected]


def _stream_audio_energy_buckets(
    stream_url: str,
    total_dur: float,
    bucket_sec: float = 0.5,
) -> List[float]:
    num_buckets = max(1, int(math.ceil(total_dur / bucket_sec)))
    sample_rate = 8000
    cmd = [
        "ffmpeg",
        "-v", "error",
        "-reconnect", "1",
        "-reconnect_streamed", "1",
        "-reconnect_delay_max", "5",
        "-i", stream_url,
        "-vn",
        "-ac", "1",
        "-ar", str(sample_rate),
        "-f", "s16le",
        "pipe:1",
    ]
    try:
        res = subprocess.run(cmd, capture_output=True, timeout=45)
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
            start_i = b_idx * samples_per_bucket
            end_i = min(len(samples), start_i + samples_per_bucket)
            if start_i >= end_i:
                buckets.append(0.0)
                continue
            chunk = samples[start_i:end_i]
            sq_sum = 0.0
            for s in chunk:
                sq_sum += float(s) * float(s)
            rms = math.sqrt(sq_sum / len(chunk)) / 32768.0
            if rms <= 1e-6:
                buckets.append(0.0)
            else:
                db = 20.0 * math.log10(rms)
                buckets.append(max(0.0, min(1.0, (db + 48.0) / 42.0)))
        return buckets
    except Exception:
        return [0.35] * num_buckets


def _stream_motion_buckets(
    stream_url: str,
    total_dur: float,
    bucket_sec: float = 0.5,
) -> List[float]:
    num_buckets = max(1, int(math.ceil(total_dur / bucket_sec)))
    buckets = [0.0] * num_buckets
    cmd = [
        "ffmpeg",
        "-hide_banner",
        "-loglevel", "info",
        "-reconnect", "1",
        "-reconnect_streamed", "1",
        "-reconnect_delay_max", "5",
        "-i", stream_url,
        "-an",
        "-vf", "fps=2,scale=160:-2,select='gt(scene,0.12)',showinfo",
        "-f", "null",
        "-",
    ]
    try:
        res = subprocess.run(cmd, capture_output=True, text=True, timeout=45)
        text = (res.stdout or "") + "\n" + (res.stderr or "")
        for line in text.splitlines():
            if "pts_time:" not in line:
                continue
            t_match = re.search(r"pts_time:([0-9.]+)", line)
            s_match = re.search(r"scene_score[:=]([0-9.]+)", line)
            if t_match:
                t_val = float(t_match.group(1))
                sc_val = float(s_match.group(1)) if s_match else 0.25
                b_idx = min(num_buckets - 1, max(0, int(t_val / bucket_sec)))
                buckets[b_idx] = min(1.0, buckets[b_idx] + min(1.0, sc_val * 2.5))
    except Exception:
        pass
    return buckets


def _select_iconic_windows_from_stream(
    source_input: str,
    total_dur: float,
    seg_dur: float,
    target_count: int,
    cookies_path: Optional[Path] = None,
    on_progress: Optional[Callable[[str], None]] = None,
) -> List[Tuple[float, float]]:
    def log(msg: str) -> None:
        if on_progress:
            try:
                on_progress(msg)
            except Exception:
                pass

    if total_dur <= seg_dur or target_count <= 0:
        return [(0.0, max(1.0, round(total_dur, 2)))]

    log("Analyzing remote stream peaks (audio hype + visual action) in memory...")
    light_urls = _resolve_stream_urls(
        source_input,
        cookies_path=cookies_path,
        selector="worstvideo[ext=mp4]/worstvideo/bestaudio/best",
    )
    if not light_urls:
        return []

    bucket_sec = 0.5
    audio_b = _stream_audio_energy_buckets(light_urls[0], total_dur, bucket_sec=bucket_sec)
    motion_b = _stream_motion_buckets(light_urls[0], total_dur, bucket_sec=bucket_sec)
    n_b = min(len(audio_b), len(motion_b))

    excitement = [0.65 * audio_b[i] + 0.35 * motion_b[i] for i in range(n_b)]
    prefix = [0.0] * (n_b + 1)
    for i in range(n_b):
        prefix[i + 1] = prefix[i] + excitement[i]

    win_buckets = max(1, int(round(seg_dur / bucket_sec)))
    max_start_sec = max(0.0, total_dur - seg_dur)
    max_start_b = min(n_b - 1, max(0, int(max_start_sec / bucket_sec)))

    scored: List[Tuple[float, float]] = []
    for sb in range(0, max_start_b + 1):
        eb = min(n_b, sb + win_buckets)
        span = max(1, eb - sb)
        avg_score = (prefix[eb] - prefix[sb]) / span
        peak_score = max(excitement[sb:eb]) if eb > sb else 0.0
        scored.append((round(0.75 * avg_score + 0.25 * peak_score, 4), round(min(max_start_sec, sb * bucket_sec), 2)))

    scored.sort(key=lambda item: item[0], reverse=True)

    selected: List[Tuple[float, float]] = []
    min_gap = max(2.0, seg_dur * 0.75)
    for sc, st in scored:
        if all(abs(st - prev_st) >= min_gap for _, prev_st in selected):
            selected.append((sc, st))
            if len(selected) >= target_count:
                break

    if len(selected) < target_count:
        used = {st for _, st in selected}
        for sc, st in scored:
            if st not in used:
                selected.append((sc, st))
                used.add(st)
                if len(selected) >= target_count:
                    break

    selected.sort(key=lambda item: item[1])
    for idx, (sc, st) in enumerate(selected, start=1):
        actual_d = round(min(seg_dur, total_dur - st), 2)
        log(f"Iconic shot #{idx}: {st:.1f}s -> {st + actual_d:.1f}s (peak score={sc:.2f})")

    return [(st, round(min(seg_dur, total_dur - st), 2)) for _, st in selected]


def _build_vf_filter(fit_mode: str) -> str:
    mode = (fit_mode or "blurred_bg").strip().lower()
    if mode == "center_crop":
        return (
            "[0:v]scale=1080:1920:force_original_aspect_ratio=increase,"
            "crop=1080:1920,setsar=1[vout]"
        )
    if mode == "letterbox":
        return (
            "[0:v]scale=1080:1920:force_original_aspect_ratio=decrease,"
            "pad=1080:1920:(ow-iw)/2:(oh-ih)/2:black,setsar=1[vout]"
        )
    return (
        "[0:v]split[bg][fg];"
        "[bg]scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920,boxblur=24:4[vbg];"
        "[fg]scale=1080:1920:force_original_aspect_ratio=decrease[vfg];"
        "[vbg][vfg]overlay=(W-w)/2:(H-h)/2,setsar=1[vout]"
    )


def _render_9x16_from_stream_urls(
    stream_urls: List[str],
    start_sec: float,
    dur_sec: float,
    dst_path: str,
    fit_mode: str = "blurred_bg",
) -> bool:
    if not stream_urls:
        return False

    dst = Path(dst_path)
    dst.parent.mkdir(parents=True, exist_ok=True)
    tmp_out = dst.with_name(f"{dst.stem}_tmp_9x16.mp4")
    vf = _build_vf_filter(fit_mode)

    if len(stream_urls) >= 2:
        v_url, a_url = stream_urls[0], stream_urls[1]
        cmd = [
            "ffmpeg",
            "-y",
            "-reconnect", "1",
            "-reconnect_streamed", "1",
            "-reconnect_delay_max", "5",
            "-ss", f"{start_sec:.2f}",
            "-t", f"{dur_sec:.2f}",
            "-i", v_url,
            "-reconnect", "1",
            "-reconnect_streamed", "1",
            "-reconnect_delay_max", "5",
            "-ss", f"{start_sec:.2f}",
            "-t", f"{dur_sec:.2f}",
            "-i", a_url,
            "-filter_complex",
            f"{vf};[1:a]aformat=sample_fmts=fltp:sample_rates=44100:channel_layouts=stereo,dynaudnorm=f=150:g=6[aout]",
            "-map", "[vout]",
            "-map", "[aout]",
            "-c:v", "libx264",
            "-profile:v", "high",
            "-level", "4.1",
            "-pix_fmt", "yuv420p",
            "-preset", "fast",
            "-crf", "18",
            "-c:a", "aac",
            "-ar", "44100",
            "-ac", "2",
            "-b:a", "192k",
            "-movflags", "+faststart",
            str(tmp_out),
        ]
    else:
        cmd = [
            "ffmpeg",
            "-y",
            "-reconnect", "1",
            "-reconnect_streamed", "1",
            "-reconnect_delay_max", "5",
            "-ss", f"{start_sec:.2f}",
            "-t", f"{dur_sec:.2f}",
            "-i", stream_urls[0],
            "-filter_complex",
            f"{vf};[0:a]aformat=sample_fmts=fltp:sample_rates=44100:channel_layouts=stereo,dynaudnorm=f=150:g=6[aout]",
            "-map", "[vout]",
            "-map", "[aout]",
            "-c:v", "libx264",
            "-profile:v", "high",
            "-level", "4.1",
            "-pix_fmt", "yuv420p",
            "-preset", "fast",
            "-crf", "18",
            "-c:a", "aac",
            "-ar", "44100",
            "-ac", "2",
            "-b:a", "192k",
            "-movflags", "+faststart",
            str(tmp_out),
        ]

    try:
        res = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
    except subprocess.TimeoutExpired:
        _safe_unlink(tmp_out)
        return False

    if res.returncode == 0 and tmp_out.is_file() and tmp_out.stat().st_size > 1024:
        try:
            tmp_out.replace(dst)
            return True
        except Exception:
            pass

    _safe_unlink(tmp_out)
    return False


def detect_reactions_and_clip(
    source_input: str,
    custom_output_dir: Optional[str] = None,
    is_youtube: bool = True,
    clip_mode: str = "count",
    max_clips: int = 3,
    max_good_cap: int = 20,
    chunk_duration: float = 20.0,
    fit_mode: str = "blurred_bg",
    cookies_path: Optional[Path] = None,
    stop_event: Optional[threading.Event] = None,
    prompt_interactively: bool = False,
    on_progress: Optional[Callable[[str], None]] = None,
) -> List[str]:
    def log(msg: str) -> None:
        if on_progress:
            try:
                on_progress(msg)
            except Exception:
                pass

    out_dir = Path(custom_output_dir or "clip_output")
    out_dir.mkdir(parents=True, exist_ok=True)
    shared_edits_dir = get_edits_dir(str(out_dir))

    target_count = max_good_cap if clip_mode == "all_good" else max_clips
    target_count = max(1, min(50, int(target_count)))
    seg_dur = max(5.0, float(chunk_duration))

    heatmap, meta_dur = _fetch_metadata_no_download(
        source_input=source_input,
        cookies_path=cookies_path,
        on_progress=log,
    )

    windows: List[Tuple[float, float]] = []
    if heatmap and meta_dur > 0:
        windows = _select_windows_from_heatmap(
            heatmap=heatmap,
            total_dur=meta_dur,
            seg_dur=seg_dur,
            target_count=target_count,
            on_progress=log,
        )

    if not windows and meta_dur > 0:
        windows = _select_iconic_windows_from_stream(
            source_input=source_input,
            total_dur=meta_dur,
            seg_dur=seg_dur,
            target_count=target_count,
            cookies_path=cookies_path,
            on_progress=log,
        )

    if not windows:
        windows = [(0.0, seg_dur)]

    stream_urls = _resolve_stream_urls(
        source_input=source_input,
        cookies_path=cookies_path,
        selector=BEST_FORMAT_SELECTOR,
    )
    if not stream_urls:
        raise RuntimeError("Could not resolve stream URLs for direct 9:16 extraction.")

    generated: List[str] = []
    log(f"Extracting {len(windows)} iconic 9:16 clips (shared edits folder: '{shared_edits_dir}')...")
    for idx, (st, dur) in enumerate(windows, start=1):
        if stop_event and stop_event.is_set():
            log("Clipping stopped by user.")
            break
        final_clip = out_dir / f"best_shot_{idx:02d}_9x16.mp4"
        ok = _render_9x16_from_stream_urls(
            stream_urls=stream_urls,
            start_sec=st,
            dur_sec=dur,
            dst_path=str(final_clip),
            fit_mode=fit_mode,
        )
        if ok:
            generated.append(str(final_clip))
            log(f"Exported clip {idx}/{len(windows)}: {final_clip.name}")

    return generated
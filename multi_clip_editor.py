import math
import os
import random
import shutil
import struct
import subprocess
import wave
from pathlib import Path
from typing import Callable, List, Optional, Tuple


ALL_XFADE_TRANSITIONS = [
    "smoothleft",
    "smoothright",
    "smoothup",
    "smoothdown",
    "wipeleft",
    "wiperight",
    "wipeup",
    "wipedown",
    "slideleft",
    "slideright",
    "slideup",
    "slidedown",
    "dissolve",
    "fade",
    "fadeblack",
    "fadewhite",
    "fadegrays",
    "circleopen",
    "circleclose",
    "rectcrop",
    "distance",
    "diagtl",
    "diagtr",
    "diagbl",
    "diagbr",
    "hlslice",
    "hrslice",
    "vuslice",
    "vdslice",
    "hblur",
    "pixelize",
    "radial",
    "zoomin",
    "squeezev",
    "squeezeh",
]

AUTO_TRANSITIONS = list(ALL_XFADE_TRANSITIONS)

VIDEO_EFFECT_POOL = [
    "lens_blur",
    "zoom_blur",
    "mosaic_blur",
    "prism_blur",
    "camera_shake",
    "dent_warp",
    "vortex_spin",
    "jpeg_damage",
    "drop_shadow",
    "edge_detect",
    "halftones",
    "outline_pop",
    "wiggle",
    "blanking_fill",
    "glass_box",
    "transform_pan",
    "continuous_zoom",
    "dynamic_zoom",
    "retime_curve",
    "speed_ramp",
    "opacity_fade",
    "halation_bloom",
    "grain_vignette",
    "chromatic_aberration",
    "gaussian_motion_blur",
    "chroma_key",
    "rotoscoping_pop",
    "broll_overlay",
    "bezier_keyframe",
]

AUDIO_EFFECT_POOL = [
    "voice_isolation",
    "dialogue_leveler",
    "noise_reduction",
    "de_esser",
    "reverb_echo",
    "muffled_wall_eq",
    "pitch_up",
    "pitch_down",
    "ducking_polish",
]

ASS_COLOR_PALETTE = [
    "&H00FFFFFF&",
    "&H0000FFFF&",
    "&H00FFFF00&",
    "&H0055FF55&",
    "&H0040AAFF&",
    "&H00FF80FF&",
]

CAPTION_POSITIONS_1080X1920 = [
    (540, 1460),
    (540, 1280),
    (540, 1580),
    (510, 1360),
    (570, 1360),
    (540, 1140),
    (540, 1510),
]

FONT_VARIANTS = [
    "Arial",
    "Impact",
    "Trebuchet MS",
    "Georgia",
]

H264_COMPAT_ARGS = [
    "-c:v",
    "libx264",
    "-profile:v",
    "high",
    "-level",
    "4.1",
    "-pix_fmt",
    "yuv420p",
    "-preset",
    "fast",
    "-crf",
    "20",
    "-movflags",
    "+faststart",
]

AAC_COMPAT_ARGS = [
    "-c:a",
    "aac",
    "-ar",
    "44100",
    "-ac",
    "2",
    "-b:a",
    "160k",
]


def _format_ass_time(seconds: float) -> str:
    sec = max(0.0, float(seconds))
    hrs = int(sec // 3600)
    mins = int((sec % 3600) // 60)
    secs = int(sec % 60)
    cs = int(round((sec - int(sec)) * 100))
    if cs >= 100:
        secs += 1
        cs = 0
    if secs >= 60:
        mins += 1
        secs = 0
    if mins >= 60:
        hrs += 1
        mins = 0
    return f"{hrs}:{mins:02d}:{secs:02d}.{cs:02d}"


def _clean_caption_text(text: str, mode: str = "pop") -> str:
    cleaned = (text or "").replace("\r", " ").replace("\n", " ").strip()
    cleaned = " ".join(cleaned.split())
    cleaned = cleaned.replace("{", "(").replace("}", ")")
    if mode == "script":
        return cleaned
    return cleaned.upper()


def _pick_synced_caption_style(
    cue_idx: int,
    s_t: float,
    e_t: float,
    dur_sec: float,
    cue_text: str,
    caption_style: str,
    base_font: str,
    caption_highlight: bool,
    scene_idx: int,
) -> Tuple[int, str, str, int, int]:
    style = (caption_style or "pop_bounce").strip().lower()
    span = max(0.12, e_t - s_t)
    progress = min(1.0, max(0.0, s_t / max(0.5, dur_sec)))
    words = cue_text.split()
    is_short_punch = len(words) <= 1 or len(cue_text) <= 5
    is_fast_burst = span <= 0.45

    if not caption_highlight:
        color_hex = "&H00FFFFFF&"
    elif is_short_punch and (cue_idx % 2 == 0):
        color_hex = "&H0000FFFF&"
    elif progress > 0.65 and (cue_idx % 2 == 1):
        color_hex = "&H00FFFF00&"
    else:
        palette_idx = (scene_idx + cue_idx) % len(ASS_COLOR_PALETTE)
        color_hex = ASS_COLOR_PALETTE[palette_idx]

    base_size = 66 if is_short_punch else (54 if len(cue_text) <= 12 else 46)
    if is_fast_burst:
        base_size += 6
    if style == "kinetic":
        base_size += 6 if (cue_idx % 2 == 0) else -2
    elif style == "lower_third":
        base_size = max(38, base_size - 12)
    elif style == "typewriter":
        base_size = max(44, base_size - 4)
    font_size = max(36, min(84, base_size))

    if style == "script":
        chosen_font = "Georgia"
    elif base_font and base_font.strip().lower() not in {"", "auto"}:
        if (cue_idx + scene_idx) % 4 == 2 and is_short_punch:
            chosen_font = "Impact"
        else:
            chosen_font = base_font.strip()
    else:
        chosen_font = FONT_VARIANTS[(scene_idx + cue_idx) % len(FONT_VARIANTS)]

    if style == "lower_third":
        pos_x, pos_y = 540, 1620
    elif style == "kinetic":
        kinetic_spots = [
            (540, 1460),
            (540, 1080),
            (470, 1320),
            (610, 1320),
            (540, 1560),
            (540, 1200),
        ]
        pos_x, pos_y = kinetic_spots[(scene_idx * 3 + cue_idx) % len(kinetic_spots)]
    else:
        pos_idx = (scene_idx + cue_idx + (1 if is_short_punch else 0)) % len(CAPTION_POSITIONS_1080X1920)
        pos_x, pos_y = CAPTION_POSITIONS_1080X1920[pos_idx]

    return font_size, color_hex, chosen_font, pos_x, pos_y


def _write_scene_ass(
    voice_cues: List[Tuple[float, float, str]],
    punch_text: Optional[str],
    dur_sec: float,
    ass_path: Path,
    scene_idx: int = 0,
    caption_style: str = "pop_bounce",
    font_name: str = "Arial",
    caption_highlight: bool = True,
) -> bool:
    style = (caption_style or "pop_bounce").strip().lower()
    default_font = (font_name or "Arial").replace(",", " ").strip() or "Arial"

    header = (
        "[Script Info]\n"
        "ScriptType: v4.00+\n"
        "PlayResX: 1080\n"
        "PlayResY: 1920\n"
        "ScaledBorderAndShadow: yes\n\n"
        "[V4+ Styles]\n"
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, "
        "Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, "
        "Alignment, MarginL, MarginR, MarginV, Encoding\n"
        f"Style: Default,{default_font},56,&H00FFFFFF&,&H0000FFFF&,&H00000000&,&H99000000&,"
        "-1,0,0,0,100,100,1,0,1,4,3,2,40,40,180,1\n\n"
        "[Events]\n"
        "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n"
    )

    events: List[str] = []

    if punch_text and punch_text.strip():
        p_clean = _clean_caption_text(punch_text, mode=style)
        if p_clean:
            p_end = round(min(float(dur_sec), max(1.2, float(dur_sec) * 0.45)), 2)
            p_y = 1660 if style == "lower_third" else 230
            p_font = "Georgia" if style == "script" else "Impact"
            events.append(
                f"Dialogue: 1,{_format_ass_time(0.0)},{_format_ass_time(p_end)},Default,,0,0,0,,"
                f"{{\\an5\\pos(540,{p_y})\\fn{p_font}\\fs62\\c&H0000FFFF&\\bord4\\shad3\\fscx108\\fscy108}}{p_clean}"
            )

    for c_idx, (c_start, c_end, c_text) in enumerate(voice_cues or []):
        cue_clean = _clean_caption_text(c_text, mode=style)
        if not cue_clean:
            continue
        s_t = max(0.0, float(c_start))
        e_t = min(float(dur_sec), max(s_t + 0.15, float(c_end)))
        if e_t <= s_t:
            continue

        fsize, col_hex, c_font, px, py = _pick_synced_caption_style(
            cue_idx=c_idx,
            s_t=s_t,
            e_t=e_t,
            dur_sec=float(dur_sec),
            cue_text=cue_clean,
            caption_style=style,
            base_font=default_font,
            caption_highlight=caption_highlight,
            scene_idx=scene_idx,
        )

        pop_tag = "\\fscx114\\fscy114\\t(0,110,\\fscx100\\fscy100)" if style != "lower_third" else ""

        if style == "typewriter" and len(cue_clean) > 1:
            chars = list(cue_clean)
            span = max(0.18, e_t - s_t)
            step = span / len(chars)
            for k in range(1, len(chars) + 1):
                sub_s = round(s_t + (k - 1) * step, 2)
                sub_e = round(min(float(dur_sec), s_t + k * step), 2)
                if sub_e > sub_s:
                    partial = "".join(chars[:k])
                    events.append(
                        f"Dialogue: 0,{_format_ass_time(sub_s)},{_format_ass_time(sub_e)},Default,,0,0,0,,"
                        f"{{\\an5\\pos({px},{py})\\fn{c_font}\\fs{fsize}\\c{col_hex}\\bord4\\shad3}}{partial}"
                    )
        else:
            events.append(
                f"Dialogue: 0,{_format_ass_time(s_t)},{_format_ass_time(e_t)},Default,,0,0,0,,"
                f"{{\\an5\\pos({px},{py})\\fn{c_font}\\fs{fsize}\\c{col_hex}\\bord4\\shad3{pop_tag}}}{cue_clean}"
            )

    if not events:
        return False

    ass_path.write_text(header + "\n".join(events) + "\n", encoding="utf-8")
    return True


def _probe_duration(video_path: str) -> float:
    try:
        cmd = [
            "ffprobe",
            "-v",
            "error",
            "-show_entries",
            "format=duration",
            "-of",
            "default=noprint_wrappers=1:nokey=1",
            video_path,
        ]
        res = subprocess.run(cmd, capture_output=True, text=True, check=True)
        val = float((res.stdout or "").strip())
        if val > 0:
            return val
    except Exception:
        pass
    return 8.0


def _has_audio_stream(video_path: str) -> bool:
    try:
        cmd = [
            "ffprobe",
            "-v",
            "error",
            "-select_streams",
            "a",
            "-show_entries",
            "stream=codec_type",
            "-of",
            "csv=p=0",
            video_path,
        ]
        res = subprocess.run(cmd, capture_output=True, text=True, check=True)
        return bool((res.stdout or "").strip())
    except Exception:
        return False


def _detect_music_beats(music_path: str, work_dir: Path) -> Tuple[float, List[float]]:
    if not music_path or not os.path.isfile(music_path):
        return 120.0, [round(i * 0.5, 2) for i in range(1, 240)]

    try:
        import librosa  # type: ignore

        wav_tmp = work_dir / "music_analysis.wav"
        cmd = [
            "ffmpeg",
            "-y",
            "-i",
            music_path,
            "-vn",
            "-ac",
            "1",
            "-ar",
            "22050",
            str(wav_tmp),
        ]
        res = subprocess.run(cmd, capture_output=True, text=True)
        src_for_librosa = str(wav_tmp) if (res.returncode == 0 and wav_tmp.is_file()) else music_path
        y, sr = librosa.load(src_for_librosa, sr=22050, mono=True)
        tempo, beat_frames = librosa.beat.beat_track(y=y, sr=sr)
        beat_times = librosa.frames_to_time(beat_frames, sr=sr).tolist()
        bpm = float(tempo[0]) if hasattr(tempo, "__len__") and len(tempo) > 0 else float(tempo or 120.0)
        if bpm <= 40 or bpm > 240:
            bpm = 120.0
        clean_beats = [round(float(t), 2) for t in beat_times if float(t) > 0.25]
        if clean_beats:
            return round(bpm, 1), clean_beats
    except Exception:
        pass

    beat_interval = 0.5
    return 120.0, [round(i * beat_interval, 2) for i in range(1, 240)]


def _snap_duration_to_beats(
    target_dur: float,
    min_dur: float,
    max_dur: float,
    beats: List[float],
    cursor_sec: float,
) -> float:
    if not beats:
        return round(max(min_dur, min(max_dur, target_dur)), 2)

    desired_end = cursor_sec + target_dur
    candidates = [b for b in beats if (b - cursor_sec) >= min_dur and (b - cursor_sec) <= max_dur]
    if not candidates:
        return round(max(min_dur, min(max_dur, target_dur)), 2)

    best_b = min(candidates, key=lambda b: abs(b - desired_end))
    return round(max(min_dur, min(max_dur, best_b - cursor_sec)), 2)


def _synthesize_whoosh_sfx(wav_path: Path, duration: float = 0.35, sample_rate: int = 44100) -> str:
    n_samples = max(1, int(duration * sample_rate))
    rng = random.Random(42)
    with wave.open(str(wav_path), "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sample_rate)
        frames = bytearray()
        prev = 0.0
        for i in range(n_samples):
            frac = i / max(1, n_samples - 1)
            env = math.sin(math.pi * frac) ** 1.6
            freq = 90.0 + 380.0 * (frac ** 2)
            tone = math.sin(2.0 * math.pi * freq * (i / sample_rate))
            noise = rng.uniform(-1.0, 1.0)
            alpha = 0.12 + 0.35 * frac
            prev = (1.0 - alpha) * prev + alpha * noise
            sample = env * (0.45 * tone + 0.55 * prev) * 0.35
            clamped = max(-1.0, min(1.0, sample))
            frames.extend(struct.pack("<h", int(clamped * 32767)))
        wf.writeframes(frames)
    return str(wav_path)


def _transcribe_scene_captions(
    src_path: str,
    start_sec: float,
    dur_sec: float,
    work_dir: Path,
    scene_idx: int,
    model_size: str = "base",
) -> List[Tuple[float, float, str]]:
    try:
        import whisper  # type: ignore
    except Exception:
        return []

    wav_path = work_dir / f"voice_{scene_idx:03d}.wav"
    extract_cmd = [
        "ffmpeg",
        "-y",
        "-ss",
        str(max(0.0, float(start_sec))),
        "-t",
        str(max(0.5, float(dur_sec))),
        "-i",
        src_path,
        "-vn",
        "-ac",
        "1",
        "-ar",
        "16000",
        str(wav_path),
    ]
    res = subprocess.run(extract_cmd, capture_output=True, text=True)
    if res.returncode != 0 or not wav_path.is_file():
        return []

    try:
        model = whisper.load_model(model_size)
        result = model.transcribe(str(wav_path), fp16=False, word_timestamps=False)
    except Exception:
        return []

    cues: List[Tuple[float, float, str]] = []
    for seg in result.get("segments") or []:
        raw_text = str(seg.get("text") or "").strip()
        if not raw_text:
            continue
        s_t = max(0.0, float(seg.get("start", 0.0)))
        e_t = min(float(dur_sec), float(seg.get("end", s_t + 1.0)))
        if e_t <= s_t:
            e_t = min(float(dur_sec), s_t + 1.0)

        words = raw_text.split()
        if not words:
            continue
        step_words = 2 if len(words) >= 4 else 1
        chunks = [" ".join(words[i : i + step_words]) for i in range(0, len(words), step_words)]
        span = max(0.2, e_t - s_t)
        step = span / len(chunks)
        for idx, chunk in enumerate(chunks):
            c_start = round(s_t + idx * step, 2)
            c_end = round(min(float(dur_sec), s_t + (idx + 1) * step), 2)
            if c_end > c_start:
                cues.append((c_start, c_end, chunk))

    return cues


def _plan_dynamic_scenes(
    valid_clips: List[str],
    base_duration: float = 5.0,
    scenes_per_clip: int = 1,
    allow_repeat_and_vary: bool = True,
    beats: Optional[List[float]] = None,
    sync_to_music: bool = False,
) -> List[Tuple[str, float, float]]:
    durations = {p: _probe_duration(p) for p in valid_clips}
    plan: List[Tuple[str, float, float]] = []

    base_d = max(1.5, min(30.0, float(base_duration)))
    repeat_factor = 1
    if allow_repeat_and_vary and len(valid_clips) >= 2:
        repeat_factor = 2
    elif allow_repeat_and_vary and max(1, scenes_per_clip) == 1:
        repeat_factor = 2

    total_passes = max(max(1, scenes_per_clip), repeat_factor if allow_repeat_and_vary else 1)
    timeline_cursor = 0.0

    for pass_idx in range(total_passes):
        ordered_clips = list(valid_clips)
        if allow_repeat_and_vary and pass_idx > 0 and len(ordered_clips) > 1:
            random.shuffle(ordered_clips)

        for clip_path in ordered_clips:
            total_dur = durations[clip_path]
            if allow_repeat_and_vary:
                mult = random.choice([0.55, 0.75, 1.0, 1.25, 1.5])
                target_dur = round(max(1.5, min(total_dur, base_d * mult)), 2)
                max_start = max(0.0, total_dur - target_dur)
                if max_start <= 0.1:
                    start_t = 0.0
                else:
                    start_t = round(random.uniform(0.0, max_start), 2)
            else:
                target_dur = round(min(total_dur, base_d), 2)
                usable = max(0.0, total_dur - target_dur)
                if total_passes <= 1 or usable <= 0:
                    start_t = 0.0
                else:
                    start_t = round((usable / max(1, total_passes - 1)) * pass_idx, 2)

            max_avail = max(0.8, total_dur - start_t)
            if sync_to_music and beats:
                actual_dur = _snap_duration_to_beats(
                    target_dur=target_dur,
                    min_dur=0.8,
                    max_dur=max_avail,
                    beats=beats,
                    cursor_sec=timeline_cursor,
                )
            else:
                actual_dur = round(min(target_dur, max_avail), 2)

            plan.append((clip_path, start_t, actual_dur))
            timeline_cursor += max(0.3, actual_dur - 0.25)

    return plan


def _apply_video_effect_filter(
    in_lbl: str,
    out_lbl: str,
    effect_name: str,
    dur_sec: float,
) -> str:
    eff = (effect_name or "auto_random").strip().lower()
    if eff in {"auto", "auto_random", "random", "none", ""}:
        eff = random.choice(VIDEO_EFFECT_POOL)

    span = max(1.0, float(dur_sec))

    if eff == "lens_blur":
        return (
            f"{in_lbl}split[lb_base][lb_b];"
            f"[lb_b]boxblur=18:3[lb_soft];"
            f"[lb_base][lb_soft]overlay=0:0:enable='lt(t,0.45)',setsar=1{out_lbl}"
        )
    if eff == "zoom_blur":
        return (
            f"{in_lbl}split[zb_a][zb_b];"
            f"[zb_b]scale=1240:2204,boxblur=14:2,crop=1080:1920:(iw-1080)/2:(ih-1920)/2[zb_glow];"
            f"[zb_a][zb_glow]overlay=0:0:enable='lt(t,0.35)',setsar=1{out_lbl}"
        )
    if eff == "mosaic_blur":
        return (
            f"{in_lbl}split[m_a][m_b];"
            f"[m_b]scale=90:160:flags=neighbor,scale=1080:1920:flags=neighbor[m_pix];"
            f"[m_a][m_pix]overlay=0:0:enable='between(t,0.1,0.55)',setsar=1{out_lbl}"
        )
    if eff == "prism_blur":
        return f"{in_lbl}rgbashift=rh=8:rv=-5:bh=-8:bv=5:edge=smear,setsar=1{out_lbl}"
    if eff == "camera_shake":
        return (
            f"{in_lbl}scale=1120:1992,"
            f"crop=1080:1920:'20+18*sin(55*t)':'36+22*cos(48*t)',setsar=1{out_lbl}"
        )
    if eff == "dent_warp":
        return (
            f"{in_lbl}scale=1140:2026,"
            f"crop=1080:1920:'30+24*sin(3*t)':'53+24*cos(3*t)',setsar=1{out_lbl}"
        )
    if eff == "vortex_spin":
        return (
            f"{in_lbl}rotate='0.14*sin(4*t)':fillcolor=black@0:ow=rotw(iw):oh=roth(ih),"
            f"scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920,setsar=1{out_lbl}"
        )
    if eff == "jpeg_damage":
        return (
            f"{in_lbl}split[j_a][j_b];"
            f"[j_b]scale=270:480:flags=area,scale=1080:1920:flags=neighbor,rgbashift=rh=5:bh=-5[j_blk];"
            f"[j_a][j_blk]overlay=0:0:enable='between(t,0.15,0.50)',setsar=1{out_lbl}"
        )
    if eff == "drop_shadow":
        return (
            f"{in_lbl}split[ds_fg][ds_sh];"
            f"[ds_sh]scale=1020:1814,boxblur=16:2,pad=1080:1920:42:64:color=black@0.65[ds_back];"
            f"[ds_back][ds_fg]overlay=(W-w)/2:(H-h)/2,setsar=1{out_lbl}"
        )
    if eff == "edge_detect":
        return (
            f"{in_lbl}split[ed_a][ed_b];"
            f"[ed_b]edgedetect=low=0.12:high=0.35:mode=colormix[ed_line];"
            f"[ed_a][ed_line]blend=all_mode=screen:all_opacity=0.35,setsar=1{out_lbl}"
        )
    if eff == "halftones":
        return (
            f"{in_lbl}split[ht_a][ht_b];"
            f"[ht_b]scale=180:320:flags=neighbor,scale=1080:1920:flags=neighbor[ht_dot];"
            f"[ht_a][ht_dot]blend=all_mode=overlay:all_opacity=0.25,setsar=1{out_lbl}"
        )
    if eff == "outline_pop":
        return (
            f"{in_lbl}scale=1024:1820,pad=1080:1920:(ow-iw)/2:(oh-ih)/2:color=white@0.85,setsar=1{out_lbl}"
        )
    if eff == "wiggle":
        return (
            f"{in_lbl}scale=1100:1956,"
            f"crop=1080:1920:'10+9*sin(32*t)':'18+11*cos(29*t)',setsar=1{out_lbl}"
        )
    if eff == "blanking_fill":
        return (
            f"{in_lbl}drawbox=x=0:y=0:w=1080:h=160:color=black:t=fill,"
            f"drawbox=x=0:y=1760:w=1080:h=160:color=black:t=fill,setsar=1{out_lbl}"
        )
    if eff == "glass_box":
        return (
            f"{in_lbl}split[gb_a][gb_b];"
            f"[gb_b]crop=920:260:80:1420,boxblur=16:3[gb_pane];"
            f"[gb_a][gb_pane]overlay=80:1420,"
            f"drawbox=x=80:y=1420:w=920:h=260:color=white@0.22:t=2,setsar=1{out_lbl}"
        )
    if eff == "transform_pan":
        return (
            f"{in_lbl}scale=1180:2098,"
            f"crop=1080:1920:'50+45*sin(2*PI*t/{span})':'89+55*cos(2*PI*t/{span})',setsar=1{out_lbl}"
        )
    if eff == "continuous_zoom":
        return (
            f"{in_lbl}scale='iw*(1+0.15*t/{span})':'ih*(1+0.15*t/{span})':eval=frame,"
            f"crop=1080:1920:(iw-1080)/2:(ih-1920)/2,setsar=1{out_lbl}"
        )
    if eff == "dynamic_zoom":
        mid_t = max(0.4, round(span * 0.5, 2))
        return (
            f"{in_lbl}scale='iw*(1+0.20*(1-t/0.25)*gt(1-t/0.25,0)+0.08*(1-abs(t-{mid_t})/0.20)*gt(1-abs(t-{mid_t})/0.20,0))':"
            f"'ih*(1+0.20*(1-t/0.25)*gt(1-t/0.25,0)+0.08*(1-abs(t-{mid_t})/0.20)*gt(1-abs(t-{mid_t})/0.20,0))':eval=frame,"
            f"crop=1080:1920:(iw-1080)/2:(ih-1920)/2,setsar=1{out_lbl}"
        )
    if eff == "retime_curve":
        return f"{in_lbl}setpts=PTS/1.18,setsar=1{out_lbl}"
    if eff == "speed_ramp":
        return f"{in_lbl}setpts=PTS/1.12,fps=30,setsar=1{out_lbl}"
    if eff == "opacity_fade":
        fade_out_start = max(0.2, round(span - 0.35, 2))
        return f"{in_lbl}fade=t=in:st=0:d=0.25,fade=t=out:st={fade_out_start}:d=0.35,setsar=1{out_lbl}"
    if eff == "halation_bloom":
        return (
            f"{in_lbl}split[hb_a][hb_b];"
            f"[hb_b]boxblur=22:4,rgbashift=rh=6:bh=-4[hb_glow];"
            f"[hb_a][hb_glow]blend=all_mode=screen:all_opacity=0.24,setsar=1{out_lbl}"
        )
    if eff == "grain_vignette":
        return (
            f"{in_lbl}noise=alls=10:allf=t+u,"
            f"drawbox=x=0:y=0:w=1080:h=85:color=black@0.35:t=fill,"
            f"drawbox=x=0:y=1835:w=1080:h=85:color=black@0.35:t=fill,setsar=1{out_lbl}"
        )
    if eff == "chromatic_aberration":
        return f"{in_lbl}rgbashift=rh=10:bh=-10:edge=smear,setsar=1{out_lbl}"
    if eff == "gaussian_motion_blur":
        return (
            f"{in_lbl}split[gm_a][gm_b];"
            f"[gm_b]boxblur=12:2,rgbashift=rh=7:rv=0:bh=-7:bv=0[gm_m];"
            f"[gm_a][gm_m]overlay=0:0:enable='lt(t,0.40)',setsar=1{out_lbl}"
        )
    if eff == "chroma_key":
        return (
            f"{in_lbl}split[ck_a][ck_b];"
            f"[ck_b]edgedetect=low=0.08:high=0.25:mode=wires[ck_w];"
            f"[ck_a][ck_w]blend=all_mode=lighten:all_opacity=0.20,setsar=1{out_lbl}"
        )
    if eff == "rotoscoping_pop":
        return (
            f"{in_lbl}split[rt_bg][rt_fg];"
            f"[rt_bg]boxblur=10:2[rt_soft];"
            f"[rt_fg]scale=1120:1992,crop=1080:1920:20:36[rt_pop];"
            f"[rt_soft][rt_pop]overlay=0:0,setsar=1{out_lbl}"
        )
    if eff == "broll_overlay":
        return (
            f"{in_lbl}split[br_main][br_pip];"
            f"[br_pip]scale=360:640,drawbox=x=0:y=0:w=360:h=640:color=white@0.45:t=3[br_box];"
            f"[br_main][br_box]overlay=680:180:enable='between(t,0.4,min({span}-0.3,2.6))',setsar=1{out_lbl}"
        )
    if eff == "bezier_keyframe":
        return (
            f"{in_lbl}scale='iw*(1+0.14*(3*pow(min(1,t/{span}),2)-2*pow(min(1,t/{span}),3)))':"
            f"'ih*(1+0.14*(3*pow(min(1,t/{span}),2)-2*pow(min(1,t/{span}),3)))':eval=frame,"
            f"crop=1080:1920:(iw-1080)/2:(ih-1920)/2,setsar=1{out_lbl}"
        )

    return f"{in_lbl}setsar=1{out_lbl}"


def _apply_audio_effect_filter(
    in_lbl: str,
    out_lbl: str,
    audio_effect: str = "auto_random",
) -> str:
    eff = (audio_effect or "auto_random").strip().lower()
    if eff in {"auto", "auto_random", "random"}:
        eff = random.choice(AUDIO_EFFECT_POOL)

    if eff in {"none", "off", "raw"}:
        return f"{in_lbl}aformat=sample_fmts=fltp:sample_rates=44100:channel_layouts=stereo{out_lbl}"
    if eff == "voice_isolation":
        chain = "highpass=f=120,lowpass=f=4200,acompressor=threshold=-18dB:ratio=3:attack=10:release=120"
    elif eff == "dialogue_leveler":
        chain = "dynaudnorm=f=150:g=7,acompressor=threshold=-16dB:ratio=2.5:attack=15:release=150"
    elif eff == "noise_reduction":
        chain = "highpass=f=80,lowpass=f=12000,acompressor=threshold=-20dB:ratio=2"
    elif eff == "de_esser":
        chain = "highpass=f=90,equalizer=f=6500:width_type=o:width=1.5:g=-6,acompressor=threshold=-20dB:ratio=3"
    elif eff == "reverb_echo":
        chain = "aecho=0.8:0.7:120|240:0.35|0.2"
    elif eff == "muffled_wall_eq":
        chain = "lowpass=f=650:p=2,equalizer=f=320:width_type=o:width=1.2:g=4"
    elif eff == "pitch_up":
        chain = "asetrate=44100*1.12,aresample=44100"
    elif eff == "pitch_down":
        chain = "asetrate=44100*0.88,aresample=44100"
    elif eff == "ducking_polish":
        chain = "dynaudnorm=f=120:g=9,acompressor=threshold=-22dB:ratio=4:attack=8:release=180"
    else:
        chain = "acompressor=threshold=-18dB:ratio=2.5"

    return (
        f"{in_lbl}{chain},aformat=sample_fmts=fltp:sample_rates=44100:channel_layouts=stereo{out_lbl}"
    )


def _build_scene_segment(
    src_path: str,
    out_path: str,
    start_sec: float,
    dur_sec: float,
    work_dir: Path,
    scene_idx: int,
    punch_text: Optional[str] = None,
    voice_cues: Optional[List[Tuple[float, float, str]]] = None,
    edit_style: str = "auto_random",
    audio_effect: str = "auto_random",
    caption_style: str = "pop_bounce",
    font_name: str = "Arial",
    caption_highlight: bool = True,
    impact_bounce: bool = True,
    sfx_whoosh: bool = True,
) -> str:
    has_audio = _has_audio_stream(src_path)
    actual_dur = max(0.6, float(dur_sec))

    vf = (
        "[0:v]scale=1080:1920:force_original_aspect_ratio=decrease,"
        "pad=1080:1920:(ow-iw)/2:(oh-ih)/2:black,setsar=1[v0]"
    )
    last = "[v0]"

    vf += ";" + _apply_video_effect_filter(last, "[veff]", edit_style, actual_dur)
    last = "[veff]"

    if impact_bounce:
        vf += (
            f";{last}scale=1110:1972,"
            f"crop=1080:1920:"
            f"'15+14*sin(45*t)*((1-t/0.16)*gt(1-t/0.16,0))':"
            f"'26+18*cos(38*t)*((1-t/0.16)*gt(1-t/0.16,0))',setsar=1[v_bnc]"
        )
        last = "[v_bnc]"

    ass_file = work_dir / f"caps_{scene_idx:03d}.ass"
    has_caps = _write_scene_ass(
        voice_cues=voice_cues or [],
        punch_text=punch_text,
        dur_sec=actual_dur,
        ass_path=ass_file,
        scene_idx=scene_idx,
        caption_style=caption_style,
        font_name=font_name,
        caption_highlight=caption_highlight,
    )
    if has_caps:
        ass_posix = ass_file.resolve().as_posix().replace(":", "\\:")
        vf += f";{last}ass=filename='{ass_posix}'[v_sub]"
        last = "[v_sub]"

    if has_audio:
        af = _apply_audio_effect_filter("[0:a]", "[abase]", audio_effect=audio_effect)
    else:
        af = "anullsrc=r=44100:cl=stereo[abase]"

    extra_inputs: List[str] = []
    if sfx_whoosh:
        sfx_path = _synthesize_whoosh_sfx(work_dir / f"whoosh_{scene_idx:03d}.wav", duration=0.35)
        extra_inputs = ["-i", sfx_path]
        af += (
            ";[1:a]aformat=sample_fmts=fltp:sample_rates=44100:channel_layouts=stereo[sfx_norm];"
            "[abase][sfx_norm]amix=inputs=2:duration=first:dropout_transition=0:weights=1 0.55[aout]"
        )
    else:
        af += ";[abase]anull[aout]"

    cmd = [
        "ffmpeg",
        "-y",
        "-ss",
        str(max(0.0, float(start_sec))),
        "-i",
        src_path,
        *extra_inputs,
        "-t",
        str(actual_dur),
        "-filter_complex",
        f"{vf};{af}",
        "-map",
        last,
        "-map",
        "[aout]",
        *H264_COMPAT_ARGS,
        *AAC_COMPAT_ARGS,
        out_path,
    ]
    res = subprocess.run(cmd, capture_output=True, text=True)
    if res.returncode != 0:
        raise RuntimeError(f"Scene build failed ({res.returncode}): {res.stderr[-600:]}")
    return out_path


def _reencode_compat_copy(src_path: str, dst_path: str) -> None:
    cmd = [
        "ffmpeg",
        "-y",
        "-i",
        src_path,
        *H264_COMPAT_ARGS,
        *AAC_COMPAT_ARGS,
        dst_path,
    ]
    res = subprocess.run(cmd, capture_output=True, text=True)
    if res.returncode != 0 or not os.path.isfile(dst_path):
        shutil.copy2(src_path, dst_path)


def _join_scenes_with_transitions(
    scene_paths: List[str],
    out_path: str,
    transition_type: str = "random",
    transition_dur: float = 0.25,
    jl_cut_mode: str = "j_l_mix",
    on_log: Optional[Callable[[str], None]] = None,
) -> str:
    out_file = Path(out_path).resolve()
    out_file.parent.mkdir(parents=True, exist_ok=True)
    work_dir = out_file.parent if out_file.parent.name == "_multi_edit_work" else (out_file.parent / "_multi_edit_work")
    work_dir.mkdir(parents=True, exist_ok=True)

    if len(scene_paths) == 1:
        _reencode_compat_copy(scene_paths[0], str(out_file))
        return str(out_file)

    mode = (transition_type or "random").strip().lower()
    is_random = mode in {"random", "auto", "auto_random", "all"}
    t_dur = max(0.12, min(0.5, float(transition_dur)))
    jl_mode = (jl_cut_mode or "j_l_mix").strip().lower()

    durations = [_probe_duration(p) for p in scene_paths]
    min_d = min(durations)
    if min_d <= t_dur * 2.5:
        t_dur = max(0.1, round((min_d / 2.6), 2))

    inputs_cmd: List[str] = []
    for p in scene_paths:
        inputs_cmd.extend(["-i", p])

    filter_parts: List[str] = []
    cur_v = "[0:v]"
    cur_a = "[0:a]"
    cumulative_offset = 0.0
    used_transitions: List[str] = []
    last_t: Optional[str] = None

    for i in range(1, len(scene_paths)):
        prev_effective = durations[i - 1] - (t_dur if i > 1 else 0.0)
        cumulative_offset += max(0.2, prev_effective - t_dur)

        if is_random:
            choices = [t for t in AUTO_TRANSITIONS if t != last_t] or AUTO_TRANSITIONS
            t_name = random.choice(choices)
            last_t = t_name
        else:
            t_name = mode if mode in ALL_XFADE_TRANSITIONS else "fade"
        used_transitions.append(t_name)

        if jl_mode == "j_cut":
            a_dur = round(min(0.5, t_dur * 1.4), 2)
        elif jl_mode == "l_cut":
            a_dur = round(min(0.5, t_dur * 1.25), 2)
        elif jl_mode == "j_l_mix":
            a_dur = round(min(0.5, t_dur * (1.35 if i % 2 == 1 else 1.15)), 2)
        else:
            a_dur = t_dur

        next_v_in = f"[{i}:v]"
        next_a_in = f"[{i}:a]"
        out_v = f"[v_xf{i}]" if i < len(scene_paths) - 1 else "[v_final]"
        out_a = f"[a_xf{i}]" if i < len(scene_paths) - 1 else "[a_final]"

        filter_parts.append(
            f"{cur_v}{next_v_in}xfade=transition={t_name}:duration={t_dur}:offset={cumulative_offset:.2f},setsar=1{out_v}"
        )
        filter_parts.append(
            f"{cur_a}{next_a_in}acrossfade=d={a_dur}:c1=tri:c2=tri{out_a}"
        )
        cur_v = out_v
        cur_a = out_a

    if on_log and used_transitions:
        try:
            on_log(f"Transitions: {', '.join(used_transitions)} ({jl_mode})")
        except Exception:
            pass

    cmd = [
        "ffmpeg",
        "-y",
        *inputs_cmd,
        "-filter_complex",
        ";".join(filter_parts),
        "-map",
        "[v_final]",
        "-map",
        "[a_final]",
        *H264_COMPAT_ARGS,
        *AAC_COMPAT_ARGS,
        str(out_file),
    ]
    res = subprocess.run(cmd, capture_output=True, text=True)
    if res.returncode != 0:
        if on_log:
            try:
                on_log("Primary xfade pass had an issue; trying safe transitions...")
            except Exception:
                pass
        safe_parts: List[str] = []
        s_cur_v = "[0:v]"
        s_cur_a = "[0:a]"
        s_offset = 0.0
        for i in range(1, len(scene_paths)):
            prev_eff = durations[i - 1] - (t_dur if i > 1 else 0.0)
            s_offset += max(0.2, prev_eff - t_dur)
            fb_t = ["wipeleft", "wiperight", "slideleft", "slideright", "fade"][i % 5]
            s_out_v = f"[sv_xf{i}]" if i < len(scene_paths) - 1 else "[sv_final]"
            s_out_a = f"[sa_xf{i}]" if i < len(scene_paths) - 1 else "[sa_final]"
            safe_parts.append(
                f"{s_cur_v}[{i}:v]xfade=transition={fb_t}:duration={t_dur}:offset={s_offset:.2f},setsar=1{s_out_v}"
            )
            safe_parts.append(
                f"{s_cur_a}[{i}:a]acrossfade=d={t_dur}{s_out_a}"
            )
            s_cur_v = s_out_v
            s_cur_a = s_out_a

        retry_cmd = [
            "ffmpeg",
            "-y",
            *inputs_cmd,
            "-filter_complex",
            ";".join(safe_parts),
            "-map",
            "[sv_final]",
            "-map",
            "[sa_final]",
            *H264_COMPAT_ARGS,
            *AAC_COMPAT_ARGS,
            str(out_file),
        ]
        res_retry = subprocess.run(retry_cmd, capture_output=True, text=True)
        if res_retry.returncode != 0:
            list_file = work_dir / "fallback.txt"
            list_file.write_text(
                "\n".join(f"file '{Path(p).resolve().as_posix()}'" for p in scene_paths),
                encoding="utf-8",
            )
            fb_cmd = [
                "ffmpeg",
                "-y",
                "-f",
                "concat",
                "-safe",
                "0",
                "-i",
                str(list_file),
                *H264_COMPAT_ARGS,
                *AAC_COMPAT_ARGS,
                str(out_file),
            ]
            res2 = subprocess.run(fb_cmd, capture_output=True, text=True)
            if res2.returncode != 0:
                raise RuntimeError(f"Transition join failed: {res2.stderr[-600:]}")

    return str(out_file)


def _apply_adjustment_and_music_pass(
    merged_path: str,
    final_path: str,
    work_dir: Path,
    adjustment_mode: str = "auto_polish",
    music_path: Optional[str] = None,
    music_volume: float = 0.35,
    duck_music_under_voice: bool = True,
) -> str:
    work_dir.mkdir(parents=True, exist_ok=True)
    mode = (adjustment_mode or "auto_polish").strip().lower()
    temp_adj = work_dir / "adj_merged.mp4"

    if mode == "vignette_frame":
        vf = (
            "[0:v]drawbox=x=0:y=0:w=1080:h=90:color=black@0.45:t=fill,"
            "drawbox=x=0:y=1830:w=1080:h=90:color=black@0.45:t=fill,setsar=1[vout]"
        )
    elif mode == "punch_pulse":
        vf = (
            "[0:v]scale='iw*(1+0.04*abs(sin(3*t)))':'ih*(1+0.04*abs(sin(3*t)))':eval=frame,"
            "crop=1080:1920:(iw-1080)/2:(ih-1920)/2,setsar=1[vout]"
        )
    else:
        vf = "[0:v]setsar=1[vout]"

    has_music = bool(music_path and os.path.isfile(music_path))
    m_vol = max(0.05, min(1.0, float(music_volume)))

    if has_music:
        if duck_music_under_voice:
            af = (
                "[0:a]aformat=sample_fmts=fltp:sample_rates=44100:channel_layouts=stereo[voice];"
                f"[1:a]aloop=loop=-1:size=2e+09,aformat=sample_fmts=fltp:sample_rates=44100:channel_layouts=stereo,volume={m_vol}[mus_raw];"
                "[mus_raw][voice]sidechaincompress=threshold=0.04:ratio=6:attack=20:release=250:level_sc=1[mus_ducked];"
                "[voice][mus_ducked]amix=inputs=2:duration=first:dropout_transition=0:weights=1.0 0.85,"
                "loudnorm=I=-16:TP=-1.5:LRA=11[aout]"
            )
        else:
            af = (
                "[0:a]aformat=sample_fmts=fltp:sample_rates=44100:channel_layouts=stereo[voice];"
                f"[1:a]aloop=loop=-1:size=2e+09,aformat=sample_fmts=fltp:sample_rates=44100:channel_layouts=stereo,volume={m_vol}[mus_raw];"
                "[voice][mus_raw]amix=inputs=2:duration=first:dropout_transition=0:weights=1.0 0.75,"
                "loudnorm=I=-16:TP=-1.5:LRA=11[aout]"
            )
        cmd = [
            "ffmpeg",
            "-y",
            "-i",
            merged_path,
            "-stream_loop",
            "-1",
            "-i",
            music_path,
            "-filter_complex",
            f"{vf};{af}",
            "-map",
            "[vout]",
            "-map",
            "[aout]",
            "-shortest",
            *H264_COMPAT_ARGS,
            *AAC_COMPAT_ARGS,
            str(temp_adj),
        ]
    else:
        cmd = [
            "ffmpeg",
            "-y",
            "-i",
            merged_path,
            "-filter_complex",
            f"{vf};[0:a]loudnorm=I=-16:TP=-1.5:LRA=11[aout]",
            "-map",
            "[vout]",
            "-map",
            "[aout]",
            *H264_COMPAT_ARGS,
            *AAC_COMPAT_ARGS,
            str(temp_adj),
        ]

    res = subprocess.run(cmd, capture_output=True, text=True)
    if res.returncode == 0 and temp_adj.is_file():
        shutil.move(str(temp_adj), final_path)
    else:
        _reencode_compat_copy(merged_path, final_path)
    return final_path


def make_multi_clip_edit(
    clip_paths: List[str],
    output_path: str = r"clip_output\Trial\final_multi_edit_9x16.mp4",
    scene_duration: float = 5.0,
    scenes_per_clip: int = 1,
    punch_texts: Optional[List[str]] = None,
    live_voice_captions: bool = True,
    whisper_model: str = "base",
    transition_type: str = "random",
    jl_cut_mode: str = "j_l_mix",
    edit_style: str = "auto_random",
    audio_effect: str = "auto_random",
    caption_style: str = "pop_bounce",
    font_name: str = "Arial",
    adjustment_mode: str = "auto_polish",
    music_path: Optional[str] = None,
    music_volume: float = 0.35,
    sync_to_music: bool = True,
    duck_music_under_voice: bool = True,
    caption_highlight: bool = True,
    impact_bounce: bool = True,
    sfx_whoosh: bool = True,
    allow_repeat_and_vary: bool = True,
    on_log: Optional[Callable[[str], None]] = None,
) -> str:
    def log(msg: str) -> None:
        if on_log:
            try:
                on_log(msg)
            except Exception:
                pass

    valid_clips = [p for p in clip_paths if p and os.path.isfile(p)]
    if not valid_clips:
        raise FileNotFoundError("No valid input clips found in clip_paths.")

    out_file = Path(output_path)
    out_file.parent.mkdir(parents=True, exist_ok=True)
    work_dir = out_file.parent / "_multi_edit_work"
    work_dir.mkdir(parents=True, exist_ok=True)

    user_punches = [t for t in (punch_texts or []) if t and t.strip()]
    segment_files: List[str] = []

    has_music = bool(music_path and os.path.isfile(music_path))
    beats: Optional[List[float]] = None
    if has_music and sync_to_music:
        bpm, beats = _detect_music_beats(music_path, work_dir)
        log(f"Music beat sync active (~{bpm} BPM, {len(beats)} beat markers).")

    try:
        scene_plan = _plan_dynamic_scenes(
            valid_clips=valid_clips,
            base_duration=scene_duration,
            scenes_per_clip=scenes_per_clip,
            allow_repeat_and_vary=allow_repeat_and_vary,
            beats=beats,
            sync_to_music=bool(has_music and sync_to_music),
        )
        log(f"Planned {len(scene_plan)} dynamic scene(s) across {len(valid_clips)} clip(s).")

        for scene_idx, (clip_path, start_t, actual_dur) in enumerate(scene_plan):
            p_text = user_punches[scene_idx % len(user_punches)] if user_punches else None

            cues: List[Tuple[float, float, str]] = []
            if live_voice_captions:
                log(f"Reading voice from scene {scene_idx + 1} ({actual_dur:.1f}s)...")
                cues = _transcribe_scene_captions(
                    src_path=clip_path,
                    start_sec=start_t,
                    dur_sec=actual_dur,
                    work_dir=work_dir,
                    scene_idx=scene_idx,
                    model_size=whisper_model,
                )
                if cues:
                    log(f"Scene {scene_idx + 1}: synced {len(cues)} spoken caption burst(s).")
                else:
                    log(f"Scene {scene_idx + 1}: no speech detected.")

            seg_out = str(work_dir / f"scene_{scene_idx:03d}.mp4")
            _build_scene_segment(
                src_path=clip_path,
                out_path=seg_out,
                start_sec=start_t,
                dur_sec=actual_dur,
                work_dir=work_dir,
                scene_idx=scene_idx,
                punch_text=p_text,
                voice_cues=cues,
                edit_style=edit_style,
                audio_effect=audio_effect,
                caption_style=caption_style,
                font_name=font_name,
                caption_highlight=caption_highlight,
                impact_bounce=impact_bounce,
                sfx_whoosh=sfx_whoosh,
            )
            segment_files.append(seg_out)

        if not segment_files:
            raise RuntimeError("No scenes generated.")

        raw_merged = str(work_dir / "merged_pre_adj.mp4")
        log(f"Merging {len(segment_files)} scene(s) with transitions & J/L audio...")
        _join_scenes_with_transitions(
            scene_paths=segment_files,
            out_path=raw_merged,
            transition_type=transition_type,
            transition_dur=0.25,
            jl_cut_mode=jl_cut_mode,
            on_log=log,
        )

        if has_music:
            log(f"Syncing & mixing music track with auto-ducking (vol={music_volume:.2f})...")
        else:
            log(f"Applying adjustment clip pass ({adjustment_mode})...")
        _apply_adjustment_and_music_pass(
            merged_path=raw_merged,
            final_path=str(out_file),
            work_dir=work_dir,
            adjustment_mode=adjustment_mode,
            music_path=music_path if has_music else None,
            music_volume=music_volume,
            duck_music_under_voice=duck_music_under_voice,
        )
    finally:
        shutil.rmtree(work_dir, ignore_errors=True)

    return str(out_file)
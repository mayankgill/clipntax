import uuid
from pathlib import Path
from typing import Callable, Dict, List, Optional

from creator_manager import CreatorRegistry, detect_channel_name_from_video, sanitize_creator_name
from multi_clip_editor import make_multi_clip_edit


def run_multi_creator_edits(
    creator_jobs: List[Dict[str, object]],
    output_root: str = "clip_output",
    shared_editor_kwargs: Optional[Dict[str, object]] = None,
    on_log: Optional[Callable[[str], None]] = None,
) -> Dict[str, List[str]]:
    def log(msg: str) -> None:
        if on_log:
            try:
                on_log(msg)
            except Exception:
                pass

    registry = CreatorRegistry(output_root=Path(output_root))
    base_kwargs = dict(shared_editor_kwargs or {})
    results: Dict[str, List[str]] = {}

    normalized_jobs: List[Dict[str, object]] = []
    for idx, job in enumerate(creator_jobs, start=1):
        clip_paths = [str(p) for p in (job.get("clip_paths") or []) if p]
        if not clip_paths:
            log(f"Skipping job {idx}: no input clips provided.")
            continue

        explicit_name = str(job.get("creator") or job.get("channel") or "").strip()
        detected_name = detect_channel_name_from_video(
            clip_paths[0],
            fallback_name=explicit_name or None,
        )
        channel_name = sanitize_creator_name(explicit_name or detected_name)
        aliases = list(job.get("aliases") or [])
        if detected_name and detected_name != channel_name:
            aliases.append(detected_name)

        merged_entry = dict(job)
        merged_entry["creator"] = channel_name
        merged_entry["aliases"] = aliases
        merged_entry["clip_paths"] = clip_paths
        normalized_jobs.append(merged_entry)

    for idx, job in enumerate(normalized_jobs, start=1):
        channel_name = str(job["creator"])
        aliases = list(job.get("aliases") or [])
        clip_paths = list(job["clip_paths"])  # type: ignore[arg-type]

        creator_dir = registry.get_creator_folder(creator_name=channel_name, aliases=aliases)
        safe_channel = sanitize_creator_name(channel_name)
        log(f"[{idx}/{len(normalized_jobs)}] Folder '{safe_channel}' -> {creator_dir}")

        job_kwargs = dict(base_kwargs)
        for override_key in (
            "scene_duration",
            "scenes_per_clip",
            "punch_texts",
            "live_voice_captions",
            "whisper_model",
            "transition_type",
            "jl_cut_mode",
            "edit_style",
            "audio_effect",
            "caption_style",
            "font_name",
            "adjustment_mode",
            "music_path",
            "music_volume",
            "sync_to_music",
            "duck_music_under_voice",
            "caption_highlight",
            "impact_bounce",
            "sfx_whoosh",
            "allow_repeat_and_vary",
        ):
            if override_key in job and job[override_key] is not None:
                job_kwargs[override_key] = job[override_key]

        short_id = uuid.uuid4().hex[:6]
        out_file = creator_dir / f"{safe_channel}_{short_id}_9x16.mp4"

        saved_path = make_multi_clip_edit(
            clip_paths=clip_paths,
            output_path=str(out_file),
            on_log=log,
            **job_kwargs,
        )
        registry.record_saved_clip(
            creator_name=channel_name,
            clip_path=saved_path,
            aliases=aliases,
        )
        results.setdefault(channel_name, []).append(saved_path)
        log(f"Saved edit in '{safe_channel}' -> {saved_path}")

    return results
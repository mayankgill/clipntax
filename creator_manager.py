import json
import os
import re
import subprocess
from pathlib import Path
from typing import Dict, List, Optional


DEFAULT_OUTPUT_ROOT = Path("clip_output")
DEFAULT_REGISTRY_PATH = Path("clip_output") / "creator_registry.json"
TRIAL_FOLDER_NAME = "Trial"


def sanitize_creator_name(name: Optional[str]) -> str:
    raw = (name or "").strip()
    if not raw or raw.lower() in {"unknown_channel", "unknown channel", "unknown", "none", "trial"}:
        return TRIAL_FOLDER_NAME
    cleaned = re.sub(r'[<>:"/\\|?*\x00-\x1f]+', "_", raw)
    cleaned = re.sub(r"\s+", "_", cleaned).strip("._ ")
    return cleaned[:80] if cleaned else TRIAL_FOLDER_NAME


def _normalize_key(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", (name or "").strip().lower())


def detect_channel_name_from_video(video_path: str, fallback_name: Optional[str] = None) -> str:
    """
    Detect creator/channel name from video metadata tags or filename,
    returning 'Trial' when no creator name is available.
    """
    if fallback_name and fallback_name.strip():
        return sanitize_creator_name(fallback_name.strip())

    if video_path and os.path.isfile(video_path):
        try:
            cmd = [
                "ffprobe",
                "-v",
                "error",
                "-show_entries",
                "format_tags=artist,uploader,channel,album_artist:stream_tags=artist,uploader,channel",
                "-of",
                "json",
                video_path,
            ]
            res = subprocess.run(cmd, capture_output=True, text=True, check=True)
            parsed = json.loads(res.stdout or "{}")

            tags_pool: List[dict] = []
            fmt_tags = (parsed.get("format") or {}).get("tags")
            if isinstance(fmt_tags, dict):
                tags_pool.append(fmt_tags)
            for st in parsed.get("streams") or []:
                st_tags = (st or {}).get("tags")
                if isinstance(st_tags, dict):
                    tags_pool.append(st_tags)

            for tags in tags_pool:
                for k, v in tags.items():
                    key_l = str(k).strip().lower()
                    val_s = str(v or "").strip()
                    if key_l in {"artist", "uploader", "channel", "album_artist"} and val_s:
                        return sanitize_creator_name(val_s)
        except Exception:
            pass

        stem = Path(video_path).stem
        handle_match = re.search(r"(@[A-Za-z0-9_.-]+)", stem)
        if handle_match:
            return sanitize_creator_name(handle_match.group(1))

        if " - " in stem:
            candidate = stem.split(" - ", 1)[0].strip()
            if candidate and not candidate.lower().startswith(("edit_", "clip_", "video_", "trial_")):
                return sanitize_creator_name(candidate)

    return TRIAL_FOLDER_NAME


class CreatorRegistry:
    def __init__(
        self,
        output_root: Path = DEFAULT_OUTPUT_ROOT,
        registry_path: Optional[Path] = None,
    ) -> None:
        self.output_root = Path(output_root)
        self.registry_path = Path(registry_path) if registry_path else (self.output_root / "creator_registry.json")
        self.output_root.mkdir(parents=True, exist_ok=True)
        self.registry_path.parent.mkdir(parents=True, exist_ok=True)
        self.data: Dict[str, object] = self._load()

    def _load(self) -> Dict[str, object]:
        if self.registry_path.is_file():
            try:
                parsed = json.loads(self.registry_path.read_text(encoding="utf-8"))
                if isinstance(parsed, dict) and isinstance(parsed.get("creators"), dict):
                    return parsed
            except Exception:
                pass
        return {"creators": {}}

    def save(self) -> None:
        self.registry_path.write_text(
            json.dumps(self.data, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )

    def resolve_creator(
        self,
        creator_name: Optional[str],
        aliases: Optional[List[str]] = None,
    ) -> Dict[str, object]:
        creators: Dict[str, dict] = self.data["creators"]  # type: ignore[assignment]
        folder_name = sanitize_creator_name(creator_name)
        display_label = TRIAL_FOLDER_NAME if folder_name == TRIAL_FOLDER_NAME else (creator_name or folder_name).strip()

        norm_target = _normalize_key(folder_name)
        alias_keys = {_normalize_key(a) for a in (aliases or []) if a and a.strip()}
        alias_keys.add(norm_target)

        matched_key: Optional[str] = None
        for key, entry in creators.items():
            existing_keys = {_normalize_key(key), _normalize_key(entry.get("folder_name", ""))}
            for al in entry.get("aliases", []):
                existing_keys.add(_normalize_key(al))
            if existing_keys & alias_keys:
                matched_key = key
                break

        if matched_key is None:
            matched_key = folder_name
            creator_dir = self.output_root / folder_name
            creator_dir.mkdir(parents=True, exist_ok=True)
            clean_aliases = sorted(
                {x.strip() for x in ([display_label] + list(aliases or [])) if x and x.strip()}
            )
            creators[matched_key] = {
                "display_name": display_label,
                "folder_name": folder_name,
                "folder_path": str(creator_dir),
                "aliases": clean_aliases,
                "clips_saved": 0,
            }
        else:
            entry = creators[matched_key]
            existing_folder = entry.get("folder_name") or folder_name
            creator_dir = self.output_root / existing_folder
            creator_dir.mkdir(parents=True, exist_ok=True)
            entry["folder_path"] = str(creator_dir)
            known_aliases = set(entry.get("aliases", []))
            if display_label:
                known_aliases.add(display_label)
            for al in aliases or []:
                if al and al.strip():
                    known_aliases.add(al.strip())
            entry["aliases"] = sorted(known_aliases)

        self.save()
        return creators[matched_key]

    def get_creator_folder(
        self,
        creator_name: Optional[str] = None,
        aliases: Optional[List[str]] = None,
    ) -> Path:
        entry = self.resolve_creator(creator_name=creator_name, aliases=aliases)
        folder = Path(str(entry["folder_path"]))
        folder.mkdir(parents=True, exist_ok=True)
        return folder

    def record_saved_clip(
        self,
        creator_name: Optional[str],
        clip_path: str,
        aliases: Optional[List[str]] = None,
    ) -> None:
        entry = self.resolve_creator(creator_name=creator_name, aliases=aliases)
        entry["clips_saved"] = int(entry.get("clips_saved") or 0) + 1
        entry["last_clip"] = str(clip_path)
        self.save()

    def list_creators(self) -> List[dict]:
        creators: Dict[str, dict] = self.data["creators"]  # type: ignore[assignment]
        return list(creators.values())
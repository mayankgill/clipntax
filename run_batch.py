from pathlib import Path
from batch_creator_clips import run_multi_creator_edits

found_videos = [
    str(p)
    for folder in ("clips_output", "uploads")
    if Path(folder).is_dir()
    for p in Path(folder).glob("*.mp4")
]

if not found_videos:
    raise FileNotFoundError("No .mp4 files found in clips_output or uploads.")

# Replace "MyChannelName" with the actual creator/channel name,
# and put clips from the same channel together in clip_paths.
jobs = [
    {
        "creator": "MyChannelName",
        "aliases": ["@mychannelname"],
        "clip_paths": found_videos[:2],
    },
]

saved = run_multi_creator_edits(
    creator_jobs=jobs,
    output_root="clip_output",
    on_log=print,
)

print("Done:", saved)
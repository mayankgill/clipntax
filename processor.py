import os
import subprocess


def cut_clip(input_path, output_path, start_time=0, duration=20, vertical=True):
    """
    Cuts a segment from input_path and saves it to output_path.
    When vertical=True, keeps the full frame centered inside a 1080x1920
    9:16 canvas with a blurred background at high visual quality.
    """
    out_dir = os.path.dirname(output_path)
    if out_dir:
        os.makedirs(out_dir, exist_ok=True)

    cmd = [
        "ffmpeg", "-y",
        "-ss", str(start_time),
        "-i", input_path,
        "-t", str(duration),
    ]

    if vertical:
        filter_complex = (
            "[0:v]scale=1080:1920:force_original_aspect_ratio=increase:force_divisible_by=2:flags=lanczos,"
            "crop=1080:1920,boxblur=24:12[bg];"
            "[0:v]scale=1080:1920:force_original_aspect_ratio=decrease:force_divisible_by=2:flags=lanczos[fg];"
            "[bg][fg]overlay=(W-w)/2:(H-h)/2,format=yuv420p,setsar=1[v]"
        )
        cmd += [
            "-filter_complex", filter_complex,
            "-map", "[v]",
            "-map", "0:a?",
            "-c:v", "libx264",
            "-preset", "slow",
            "-crf", "18",
            "-c:a", "aac",
            "-b:a", "192k",
        ]
    else:
        cmd += [
            "-c:v", "libx264",
            "-preset", "slow",
            "-crf", "18",
            "-c:a", "aac",
            "-b:a", "192k",
        ]

    cmd.append(output_path)
    try:
        subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, timeout=300)
    except subprocess.CalledProcessError as e:
        err_msg = (e.stderr or b"").decode("utf-8", errors="ignore")
        print(f"FFmpeg failed with exit code {e.returncode}:\n{err_msg}")
        raise RuntimeError(f"FFmpeg error: {err_msg[-400:]}") from e
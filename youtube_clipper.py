import os
import yt_dlp
from yt_dlp.utils import download_range_func

def download_youtube_clip(youtube_url, start_seconds, end_seconds, output_filename="youtube_clip.mp4"):
    output_dir = "clips_output"
    os.makedirs(output_dir, exist_ok=True)
    output_path = os.path.join(output_dir, output_filename)
    
    ydl_opts = {
        'format': 'bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/best',
        'download_ranges': download_range_func(None, [(start_seconds, end_seconds)]),
        'force_keyframes_at_cuts': True,
        'outtmpl': output_path.replace('.mp4', ''),
        'verbose': True
    }
    
    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        ydl.download([youtube_url])
    return output_path
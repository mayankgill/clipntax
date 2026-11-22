# CLIPNTAX

I built CLIPNTAX to turn video links into 9:16 vertical edits for YouTube and Instagram. I used Gemini while putting this project together and shaping the code.

---

## What It Does

* **9:16 Vertical Framing:** Creates vertical edits with options like blurred background, full crop, and letterbox, saving finished clips into the `EDITS` folder.
* **Clip Selection & Counts:** Lets you choose how many final edits you want and switch between **All Best Clips** (spread across the video timeline) or **Limited Clips** with a custom clip count and slice length.
* **Manual & Batch Workflows:** Includes a manual clip editor for custom timestamps or ranges, multi-video batch runs, live job logs, and a gallery to preview and download finished edits.
* **Web UI + Backend:** Runs a Flask web interface (`app.py`) connected to `auto_clipper.py` and the supporting scripts in the repo.

---

## Project Structure

| File / Folder | Purpose |
| :--- | :--- |
| `app.py` | Flask web server, API routes, and browser interface |
| `auto_clipper.py` | Clip selection, scene spreading across the timeline, and editing pipeline |
| `database.py`, `creator_manager.py`, `batch_creator_clips.py`, `multi_clip_editor.py`, `processor.py`, `run_batch.py`, `youtube_clipper.py` | Supporting scripts for creators, batch runs, processing, and database handling |
| `EDITS/` | Output directory where rendered vertical edits are saved |

---

## How I Run It

1. Open the project folder in Windows PowerShell.
2. Start the server:
   ```powershell
   python app.py

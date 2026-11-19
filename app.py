import threading
import uuid
from pathlib import Path
from typing import Any, Dict, List

from flask import Flask, jsonify, render_template_string, request, send_from_directory
from werkzeug.utils import secure_filename

from auto_clipper import DEFAULT_COOKIES_PATH, detect_reactions_and_clip
from creator_manager import CreatorRegistry, TRIAL_FOLDER_NAME, detect_channel_name_from_video, sanitize_creator_name
from multi_clip_editor import make_multi_clip_edit


BASE_DIR = Path(__file__).resolve().parent
DEFAULT_OUTPUT_DIR = BASE_DIR / "clip_output"
LEGACY_CLIPS_DIR = BASE_DIR / "clips_output"
UPLOAD_DIR = BASE_DIR / "uploads"
DEFAULT_OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
LEGACY_CLIPS_DIR.mkdir(parents=True, exist_ok=True)
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)

ALLOWED_VIDEO_EXTS = {".mp4", ".mov", ".mkv", ".avi", ".webm"}
ALLOWED_MUSIC_EXTS = {".mp3", ".wav", ".m4a", ".ogg", ".flac", ".aac"}

app = Flask(__name__)

JOBS_LOCK = threading.Lock()
JOBS: Dict[str, Dict[str, Any]] = {}
ACTIVE_JOB_ID: str | None = None


HTML_PAGE = """<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0" />
  <title>Clipntax Studio — Vertical 9:16 Clipper &amp; Music Sync</title>
  <style>
    :root {
      --bg: #0b0e14;
      --panel: #121722;
      --panel-2: #0d111a;
      --border: #252e40;
      --text: #eef2f7;
      --muted: #8e9bb0;
      --accent: #3b82f6;
      --accent-hover: #2563eb;
      --green: #16a34a;
      --green-hover: #15803d;
      --red: #dc2626;
      --pill: #1a2232;
    }
    * { box-sizing: border-box; font-family: "Segoe UI", system-ui, -apple-system, sans-serif; }
    body {
      margin: 0;
      background: radial-gradient(circle at top right, #152036 0%, var(--bg) 52%);
      color: var(--text);
      padding: 24px;
    }
    .container { max-width: 1340px; margin: 0 auto; }
    .topbar {
      display: flex; justify-content: space-between; align-items: center;
      flex-wrap: wrap; gap: 12px; margin-bottom: 20px;
    }
    .brand h1 { margin: 0; font-size: 24px; letter-spacing: -0.02em; }
    .brand .sub { color: var(--muted); font-size: 13.5px; margin-top: 3px; }
    .preset-bar { display: flex; gap: 8px; flex-wrap: wrap; align-items: center; }
    .preset-label { font-size: 12px; color: var(--muted); font-weight: 600; }
    .preset-btn {
      padding: 6px 12px; border-radius: 999px; font-size: 12.5px; font-weight: 600;
      background: var(--pill); color: var(--text); border: 1px solid var(--border); cursor: pointer;
    }
    .preset-btn:hover { border-color: var(--accent); color: #93c5fd; }

    .two-col { display: grid; grid-template-columns: 1fr 1.15fr; gap: 20px; align-items: start; }
    .card {
      background: var(--panel); border: 1px solid var(--border); border-radius: 14px;
      padding: 20px; margin-bottom: 18px; box-shadow: 0 10px 28px rgba(0,0,0,0.25);
    }
    .section-header {
      display: flex; justify-content: space-between; align-items: center;
      margin-bottom: 14px; padding-bottom: 10px; border-bottom: 1px solid var(--border);
    }
    .section-title { font-size: 13px; font-weight: 700; color: #60a5fa; text-transform: uppercase; letter-spacing: 0.05em; }
    .tag { font-size: 11.5px; padding: 2px 9px; border-radius: 999px; background: var(--pill); color: var(--muted); border: 1px solid var(--border); }

    .grid { display: grid; grid-template-columns: 1fr 1fr; gap: 13px; }
    .full { grid-column: 1 / -1; }
    label { display: block; font-size: 12.5px; color: #cbd5e1; margin-bottom: 5px; font-weight: 600; }
    input[type="text"], input[type="number"], select {
      width: 100%; padding: 9px 11px; border-radius: 9px;
      border: 1px solid var(--border); background: var(--panel-2); color: var(--text); font-size: 13.5px;
    }
    input:focus, select:focus { outline: none; border-color: var(--accent); }

    .radio-row { display: flex; gap: 10px; }
    .radio-pill, .check-pill {
      flex: 1; display: flex; align-items: center; gap: 8px;
      background: var(--panel-2); border: 1px solid var(--border); border-radius: 9px;
      padding: 9px 11px; cursor: pointer; font-size: 13px; color: #e2e8f0;
    }
    .radio-pill input, .check-pill input { width: auto; margin: 0; accent-color: var(--accent); }

    details.advanced {
      margin-top: 14px; background: var(--panel-2); border: 1px solid var(--border);
      border-radius: 10px; padding: 10px 12px;
    }
    details.advanced summary {
      cursor: pointer; font-size: 12.5px; font-weight: 700; color: #93c5fd; list-style: none;
    }
    details.advanced[open] summary { margin-bottom: 12px; }

    .dropzone {
      border: 2px dashed #334155; border-radius: 12px; padding: 18px 14px;
      text-align: center; background: var(--panel-2); cursor: pointer; transition: 0.15s ease;
    }
    .dropzone.dragover { border-color: var(--accent); background: #132038; }
    .dropzone p { margin: 2px 0; font-size: 13.5px; color: var(--text); }
    .dropzone span { font-size: 12px; color: var(--muted); }

    ul.file-queue, ul.clips { list-style: none; padding: 0; margin: 10px 0 0; }
    ul.file-queue li {
      padding: 6px 10px; background: var(--panel-2); border: 1px solid var(--border);
      border-radius: 7px; margin-bottom: 5px; font-size: 12.5px; color: #93c5fd;
      display: flex; justify-content: space-between; align-items: center;
    }
    .creator-pills { display: flex; flex-wrap: wrap; gap: 8px; margin-top: 10px; }
    .creator-chip {
      padding: 6px 12px; border-radius: 9px; font-size: 12.5px; font-weight: 600;
      background: var(--pill); border: 1px solid var(--border); color: #e2e8f0; cursor: pointer;
    }
    .creator-chip:hover { border-color: var(--accent); color: #93c5fd; }
    .creator-chip.active { border-color: var(--green); background: #10261a; color: #86efac; }
    .folder-hint {
      margin-top: 10px; font-size: 12px; color: var(--muted);
      background: var(--panel-2); border: 1px solid var(--border); border-radius: 8px; padding: 8px 10px;
    }

    .actions { display: flex; gap: 10px; margin-top: 16px; flex-wrap: wrap; }
    button {
      padding: 10px 16px; border-radius: 9px; border: none;
      font-weight: 600; font-size: 13.5px; cursor: pointer; transition: 0.15s ease;
    }
    .btn-primary { background: var(--green); color: #fff; }
    .btn-primary:hover { background: var(--green-hover); }
    .btn-accent { background: var(--accent); color: #fff; }
    .btn-accent:hover { background: var(--accent-hover); }
    .btn-danger { background: var(--red); color: #fff; }
    .btn-secondary { background: var(--pill); color: var(--text); border: 1px solid var(--border); }

    pre {
      background: var(--panel-2); border: 1px solid var(--border); border-radius: 10px;
      padding: 12px; max-height: 230px; overflow-y: auto; font-size: 12.5px;
      white-space: pre-wrap; word-break: break-word; margin: 0; color: #93c5fd;
    }
    .status-badge {
      display: inline-block; padding: 4px 11px; border-radius: 999px;
      font-size: 12px; font-weight: 700; background: var(--pill); color: #e2e8f0; border: 1px solid var(--border);
    }
    ul.clips li {
      padding: 8px 12px; background: var(--panel-2); border: 1px solid var(--border);
      border-radius: 8px; margin-bottom: 6px; display: flex; justify-content: space-between; align-items: center;
      font-size: 12.5px; gap: 12px;
    }
    a { color: #60a5fa; text-decoration: none; font-weight: 600; }
    a:hover { text-decoration: underline; }
    @media (max-width: 1000px) {
      .two-col { grid-template-columns: 1fr; }
      .grid { grid-template-columns: 1fr; }
      .radio-row { flex-direction: column; }
    }
  </style>
</head>
<body>
  <div class="container">
    <div class="topbar">
      <div class="brand">
        <h1>Clipntax Studio</h1>
        <div class="sub">Vertical 9:16 Auto Clipper • Beat &amp; Music Sync • One-Folder Creator Memory</div>
      </div>
      <div class="preset-bar">
        <span class="preset-label">Quick Presets:</span>
        <button type="button" class="preset-btn" onclick="applyPreset('hype')">⚡ Fast Beat Hype</button>
        <button type="button" class="preset-btn" onclick="applyPreset('smooth')">🎬 Smooth Story</button>
        <button type="button" class="preset-btn" onclick="applyPreset('clean')">🎙️ Clean Voice + Music</button>
      </div>
    </div>

    <div class="two-col">
      <div>
        <form id="clipForm" class="card">
          <div class="section-header">
            <span class="section-title">1. YouTube VOD / Live Clipper</span>
            <span class="tag">9:16 Vertical</span>
          </div>
          <div class="grid">
            <div>
              <label for="yt_creator_name">Creator / Channel Name</label>
              <input id="yt_creator_name" name="yt_creator_name" type="text" placeholder="Leave blank -> saves in Trial folder" oninput="syncCreatorPreview()" />
            </div>

            <div>
              <label for="yt_creator_aliases">Optional Aliases (, separated)</label>
              <input id="yt_creator_aliases" name="yt_creator_aliases" type="text" placeholder="@alt_handle, Nickname" />
            </div>

            <div class="full">
              <label for="url">New YouTube VOD or Live URL</label>
              <input id="url" name="url" type="text" placeholder="Paste new video or live stream URL here..." required />
            </div>

            <div class="full">
              <label>Clip Selection Mode</label>
              <div class="radio-row">
                <label class="radio-pill">
                  <input type="radio" name="clip_mode" value="count" checked onchange="toggleCountInput()" />
                  Exact count
                </label>
                <label class="radio-pill">
                  <input type="radio" name="clip_mode" value="all_good" onchange="toggleCountInput()" />
                  Every good reaction
                </label>
              </div>
            </div>

            <div id="countBox">
              <label for="max_clips">Number of Clips (1 – 50)</label>
              <input id="max_clips" name="max_clips" type="number" min="1" max="50" value="3" />
            </div>

            <div id="capBox" style="display:none;">
              <label for="max_good_cap">Safety Cap (1 – 50)</label>
              <input id="max_good_cap" name="max_good_cap" type="number" min="1" max="50" value="20" />
            </div>

            <div>
              <label for="chunk_duration">Clip Length (sec)</label>
              <input id="chunk_duration" name="chunk_duration" type="number" min="5" max="120" step="1" value="20" />
            </div>

            <div>
              <label for="fit_mode">Base Framing</label>
              <select id="fit_mode" name="fit_mode">
                <option value="blurred_bg" selected>Blurred Background</option>
                <option value="letterbox">Black Bars / Letterbox</option>
                <option value="center_crop">Center Crop (9:16)</option>
              </select>
            </div>
          </div>

          <div class="folder-hint" id="ytFolderPreview">
            Target folder: <strong>clip_output/Trial/</strong> (no creator entered yet — clips save in Trial)
          </div>

          <div class="actions">
            <button type="submit" class="btn-primary">Save Clips to Folder</button>
          </div>
        </form>

        <div class="card">
          <div class="section-header">
            <span class="section-title">Remembered Creators (Click to Select)</span>
            <span class="tag" id="creatorCountTag">0 saved</span>
          </div>
          <div style="font-size:12.5px; color:var(--muted);">
            Saved across restarts. Pick a creator to fill their name, paste a new URL above, and keep every clip in one folder:
          </div>
          <div id="creatorChips" class="creator-pills"></div>
        </div>
      </div>

      <form id="multiEditForm" class="card">
        <div class="section-header">
          <span class="section-title">2. Music-Synced Multi-Clip Editor</span>
          <span class="tag">Beat Sync + Ducking</span>
        </div>
        <div class="grid">
          <div>
            <label for="creator_name">Creator / Channel Name</label>
            <input id="creator_name" name="creator_name" type="text" placeholder="Leave blank -> saves in Trial folder" />
          </div>

          <div>
            <label for="creator_aliases">Optional Aliases / @Handles (, separated)</label>
            <input id="creator_aliases" name="creator_aliases" type="text" placeholder="@handle, Alternate Name" />
          </div>

          <div class="full">
            <label>Drop Video Clips Here or Click to Select</label>
            <div id="dropzone" class="dropzone" onclick="document.getElementById('clipFilesInput').click()">
              <p><strong>Drag &amp; drop video clips here</strong> or click to browse</p>
              <span>Supports .mp4, .mov, .mkv, .webm • Auto-exports compatible 9:16 H.264 MP4</span>
            </div>
            <input id="clipFilesInput" type="file" accept=".mp4,.mov,.mkv,.avi,.webm,video/*" multiple style="display:none;" onchange="handleFileSelect(this.files)" />
            <ul id="fileQueue" class="file-queue"></ul>
          </div>

          <div>
            <label for="musicFileInput">Song Track for Beat &amp; Audio Sync</label>
            <input id="musicFileInput" name="music" type="file" accept=".mp3,.wav,.m4a,.ogg,.flac,.aac,audio/*" />
          </div>

          <div>
            <label for="music_volume">Music Volume (0.05 – 1.0)</label>
            <input id="music_volume" name="music_volume" type="number" min="0.05" max="1.0" step="0.05" value="0.35" />
          </div>

          <div>
            <label for="edit_style">Visual / VFX Style</label>
            <select id="edit_style" name="edit_style">
              <option value="auto_random" selected>Auto Random (Mix All VFX)</option>
              <option value="dynamic_zoom">Crash / Dynamic Zoom</option>
              <option value="continuous_zoom">Continuous Zoom</option>
              <option value="transform_pan">Transform / Whip Pan</option>
              <option value="camera_shake">Camera Shake</option>
              <option value="prism_blur">Prism / RGB Split</option>
              <option value="lens_blur">Lens Blur</option>
              <option value="zoom_blur">Zoom Blur</option>
              <option value="mosaic_blur">Mosaic Blur</option>
              <option value="dent_warp">Dent Warp</option>
              <option value="vortex_spin">Vortex Spin</option>
              <option value="jpeg_damage">JPEG Damage</option>
              <option value="drop_shadow">Drop Shadow</option>
              <option value="edge_detect">Edge Detect</option>
              <option value="halftones">Halftones</option>
              <option value="outline_pop">Outline Pop</option>
              <option value="wiggle">Wiggle</option>
              <option value="blanking_fill">Blanking Fill</option>
              <option value="glass_box">Glass Box</option>
              <option value="retime_curve">Speed Ramp (Retime)</option>
              <option value="speed_ramp">Speed Ramp (Fast -&gt; Slow-Mo)</option>
              <option value="opacity_fade">Opacity Fade</option>
              <option value="halation_bloom">Halation &amp; Bloom</option>
              <option value="grain_vignette">Film Grain &amp; Vignette</option>
              <option value="chromatic_aberration">Chromatic Aberration</option>
              <option value="gaussian_motion_blur">Gaussian &amp; Motion Blur</option>
              <option value="chroma_key">Chroma / Edge Key</option>
              <option value="rotoscoping_pop">Rotoscoping Cutout Pop</option>
              <option value="broll_overlay">B-Roll Cutaway PIP</option>
              <option value="bezier_keyframe">Bezier Keyframe Curve</option>
            </select>
          </div>

          <div>
            <label for="transition_type">Transitions</label>
            <select id="transition_type" name="transition_type">
              <option value="random" selected>Auto Random (All Transitions)</option>
              <option value="smoothleft">Smooth Left</option>
              <option value="smoothright">Smooth Right</option>
              <option value="wipeleft">Wipe Left</option>
              <option value="wiperight">Wipe Right</option>
              <option value="slideleft">Slide Left</option>
              <option value="slideright">Slide Right</option>
              <option value="dissolve">Dissolve</option>
              <option value="fade">Crossfade</option>
              <option value="fadeblack">Dip to Black</option>
              <option value="fadewhite">Dip to White</option>
              <option value="diagtl">Diagonal Slice</option>
              <option value="hlslice">Horizontal Slice</option>
              <option value="vuslice">Vertical Slice</option>
              <option value="circleopen">Circle Open</option>
              <option value="radial">Radial Wipe</option>
              <option value="zoomin">Zoom In Cut</option>
              <option value="pixelize">Pixelize Cut</option>
            </select>
          </div>

          <div class="full">
            <label class="check-pill">
              <input id="sync_to_music" name="sync_to_music" type="checkbox" checked />
              Snap Scene Switches &amp; Transitions to Song Beats
            </label>
          </div>

          <div class="full">
            <label class="check-pill">
              <input id="duck_music_under_voice" name="duck_music_under_voice" type="checkbox" checked />
              Auto-Duck Song Volume Under Speech &amp; Dialogue
            </label>
          </div>

          <div class="full">
            <label class="check-pill">
              <input id="live_voice_captions" name="live_voice_captions" type="checkbox" checked />
              Transcribe Voice &amp; Burn Synced Captions
            </label>
          </div>
        </div>

        <details class="advanced">
          <summary>Advanced Audio, Captions &amp; Pacing Options</summary>
          <div class="grid">
            <div>
              <label for="jl_cut_mode">J-Cut &amp; L-Cut Audio Bridge</label>
              <select id="jl_cut_mode" name="jl_cut_mode">
                <option value="j_l_mix" selected>J-Cut + L-Cut Alternating</option>
                <option value="j_cut">J-Cut (Audio Leads)</option>
                <option value="l_cut">L-Cut (Audio Trails)</option>
                <option value="standard">Standard Cut</option>
              </select>
            </div>

            <div>
              <label for="audio_effect">Voice / Clip Audio Polish</label>
              <select id="audio_effect" name="audio_effect">
                <option value="auto_random" selected>Auto Random Audio Polish</option>
                <option value="voice_isolation">Voice Isolation</option>
                <option value="dialogue_leveler">Dialogue Leveler</option>
                <option value="noise_reduction">Noise Reduction</option>
                <option value="de_esser">De-Esser</option>
                <option value="ducking_polish">Dialogue Ducking &amp; Polish</option>
                <option value="reverb_echo">Reverb / Echo</option>
                <option value="muffled_wall_eq">Muffled Behind-a-Wall EQ</option>
                <option value="pitch_up">Pitch Up</option>
                <option value="pitch_down">Pitch Down</option>
                <option value="none">Raw / Unprocessed</option>
              </select>
            </div>

            <div>
              <label for="caption_style">Caption Style</label>
              <select id="caption_style" name="caption_style">
                <option value="pop_bounce" selected>Pop-Up / Bounce In</option>
                <option value="kinetic">Kinetic Multi-Spot</option>
                <option value="typewriter">Typewriter Reveal</option>
                <option value="lower_third">Lower Thirds</option>
                <option value="script">Script / Casual</option>
              </select>
            </div>

            <div>
              <label for="font_name">Caption Font</label>
              <select id="font_name" name="font_name">
                <option value="Arial" selected>Arial</option>
                <option value="Impact">Impact</option>
                <option value="Trebuchet MS">Trebuchet MS</option>
                <option value="Georgia">Georgia</option>
              </select>
            </div>

            <div>
              <label for="adjustment_mode">Master Polish Pass</label>
              <select id="adjustment_mode" name="adjustment_mode">
                <option value="auto_polish" selected>Auto Loudness Polish</option>
                <option value="vignette_frame">Cinema Vignette</option>
                <option value="punch_pulse">Beat Punch Pulse</option>
                <option value="none">None</option>
              </select>
            </div>

            <div>
              <label for="whisper_model">Whisper Model</label>
              <select id="whisper_model" name="whisper_model">
                <option value="base" selected>Base (Balanced)</option>
                <option value="tiny">Tiny (Fastest)</option>
                <option value="small">Small (Accurate)</option>
              </select>
            </div>

            <div>
              <label for="scene_duration">Base Scene Length (sec)</label>
              <input id="scene_duration" name="scene_duration" type="number" min="1.5" max="30" step="0.5" value="5.0" />
            </div>

            <div>
              <label for="scenes_per_clip">Min Scenes Per Clip</label>
              <input id="scenes_per_clip" name="scenes_per_clip" type="number" min="1" max="10" step="1" value="1" />
            </div>

            <div class="full">
              <label for="punch_texts">Punchline / Hook Texts (| separated)</label>
              <input id="punch_texts" name="punch_texts" type="text" placeholder="Optional, e.g. NO WAY | WAIT FOR IT" />
            </div>

            <div>
              <label class="check-pill">
                <input id="caption_highlight" name="caption_highlight" type="checkbox" checked />
                Caption Color &amp; Pop
              </label>
            </div>

            <div>
              <label class="check-pill">
                <input id="impact_bounce" name="impact_bounce" type="checkbox" checked />
                Cut Impact Bounce
              </label>
            </div>

            <div>
              <label class="check-pill">
                <input id="sfx_whoosh" name="sfx_whoosh" type="checkbox" checked />
                Whoosh SFX
              </label>
            </div>

            <div>
              <label class="check-pill">
                <input id="allow_repeat_and_vary" name="allow_repeat_and_vary" type="checkbox" checked />
                Dynamic Repeats &amp; Cut Vary
              </label>
            </div>
          </div>
        </details>

        <div class="actions">
          <button type="submit" class="btn-accent">Generate 9:16 Edit</button>
        </div>
      </form>
    </div>

    <div class="card">
      <div class="section-header">
        <strong>Live Console &amp; Output Clips</strong>
        <div style="display:flex; gap:8px; align-items:center;">
          <span id="statusBadge" class="status-badge">Idle</span>
          <button type="button" class="btn-danger" onclick="stopJob()">Stop</button>
          <button type="button" class="btn-secondary" onclick="clearLogs()">Clear</button>
        </div>
      </div>
      <pre id="logBox">Ready. Enter a creator name (or leave blank to save in Trial), paste a URL or drop clips, and start.</pre>
      <ul id="clipList" class="clips"></ul>
    </div>
  </div>

  <script>
    let currentJobId = null;
    let pollTimer = null;
    let selectedFiles = [];
    let knownCreators = [];

    function toFolderSlug(raw) {
      const trimmed = (raw || "").trim();
      if (!trimmed || ["unknown_channel", "unknown channel", "unknown", "none", "trial"].includes(trimmed.toLowerCase())) {
        return "Trial";
      }
      const s = trimmed.replace(/[<>:"/\\\\|?*\\x00-\\x1f]+/g, "_").replace(/\\s+/g, "_").replace(/^[._ ]+|[._ ]+$/g, "");
      return s.slice(0, 80) || "Trial";
    }

    function syncCreatorPreview() {
      const raw = document.getElementById("yt_creator_name").value.trim();
      const preview = document.getElementById("ytFolderPreview");
      if (!raw) {
        preview.innerHTML = 'Target folder: <strong>clip_output/Trial/</strong> (no creator entered yet — clips save in Trial)';
        return;
      }
      const matched = knownCreators.find((c) =>
        (c.display_name || "").toLowerCase() === raw.toLowerCase() ||
        (c.folder_name || "").toLowerCase() === raw.toLowerCase() ||
        (c.aliases || []).some((a) => String(a).toLowerCase() === raw.toLowerCase())
      );
      const folder = matched ? matched.folder_name : toFolderSlug(raw);
      const existingCount = matched ? (matched.clips_saved || 0) : 0;
      preview.innerHTML = `Target folder: <strong>clip_output/${folder}/</strong> (${existingCount} previous clip${existingCount === 1 ? "" : "s"} remembered — all new clips go here)`;
    }

    function applyPreset(kind) {
      if (kind === "hype") {
        document.getElementById("scene_duration").value = "3.0";
        document.getElementById("music_volume").value = "0.42";
        document.getElementById("edit_style").value = "dynamic_zoom";
        document.getElementById("transition_type").value = "random";
        document.getElementById("caption_style").value = "kinetic";
        document.getElementById("sync_to_music").checked = true;
        document.getElementById("duck_music_under_voice").checked = true;
        document.getElementById("impact_bounce").checked = true;
      } else if (kind === "smooth") {
        document.getElementById("scene_duration").value = "6.5";
        document.getElementById("music_volume").value = "0.28";
        document.getElementById("edit_style").value = "continuous_zoom";
        document.getElementById("transition_type").value = "dissolve";
        document.getElementById("caption_style").value = "lower_third";
        document.getElementById("sync_to_music").checked = true;
        document.getElementById("duck_music_under_voice").checked = true;
        document.getElementById("impact_bounce").checked = false;
      } else if (kind === "clean") {
        document.getElementById("scene_duration").value = "5.0";
        document.getElementById("music_volume").value = "0.22";
        document.getElementById("edit_style").value = "auto_random";
        document.getElementById("transition_type").value = "random";
        document.getElementById("caption_style").value = "pop_bounce";
        document.getElementById("audio_effect").value = "voice_isolation";
        document.getElementById("sync_to_music").checked = true;
        document.getElementById("duck_music_under_voice").checked = true;
      }
    }

    function toggleCountInput() {
      const mode = document.querySelector('input[name="clip_mode"]:checked').value;
      document.getElementById("countBox").style.display = mode === "count" ? "block" : "none";
      document.getElementById("capBox").style.display = mode === "all_good" ? "block" : "none";
    }

    function clearLogs() {
      document.getElementById("logBox").textContent = "";
    }

    function renderFileQueue() {
      const ul = document.getElementById("fileQueue");
      ul.innerHTML = "";
      selectedFiles.forEach((f, idx) => {
        const li = document.createElement("li");
        li.innerHTML = `<span>${idx + 1}. ${f.name}</span><a href="#" onclick="removeFile(${idx}); return false;">Remove</a>`;
        ul.appendChild(li);
      });
    }

    function removeFile(idx) {
      selectedFiles.splice(idx, 1);
      renderFileQueue();
    }

    function handleFileSelect(fileList) {
      for (const f of Array.from(fileList || [])) {
        selectedFiles.push(f);
      }
      renderFileQueue();
    }

    const dz = document.getElementById("dropzone");
    ["dragenter", "dragover"].forEach((ev) => {
      dz.addEventListener(ev, (e) => {
        e.preventDefault();
        dz.classList.add("dragover");
      });
    });
    ["dragleave", "drop"].forEach((ev) => {
      dz.addEventListener(ev, (e) => {
        e.preventDefault();
        dz.classList.remove("dragover");
      });
    });
    dz.addEventListener("drop", (e) => {
      e.preventDefault();
      if (e.dataTransfer && e.dataTransfer.files) {
        handleFileSelect(e.dataTransfer.files);
      }
    });

    async function loadCreators() {
      try {
        const res = await fetch("/api/creators");
        if (!res.ok) return;
        const data = await res.json();
        knownCreators = data.creators || [];
        document.getElementById("creatorCountTag").textContent = `${knownCreators.length} saved`;
        const box = document.getElementById("creatorChips");
        box.innerHTML = "";
        if (!knownCreators.length) {
          box.innerHTML = '<span style="font-size:12px; color:var(--muted);">No creators saved yet. Leave blank to save in Trial or type a creator name.</span>';
          syncCreatorPreview();
          return;
        }
        const currentVal = document.getElementById("yt_creator_name").value.trim().toLowerCase();
        knownCreators.forEach((c) => {
          const btn = document.createElement("button");
          btn.type = "button";
          btn.className = "creator-chip";
          const label = c.display_name || c.folder_name;
          const count = c.clips_saved || 0;
          if (currentVal && (label.toLowerCase() === currentVal || (c.folder_name || "").toLowerCase() === currentVal)) {
            btn.classList.add("active");
          }
          btn.textContent = `${label} (${count} clips)`;
          btn.onclick = () => {
            const fillVal = label === "Trial" ? "" : label;
            document.getElementById("yt_creator_name").value = fillVal;
            document.getElementById("creator_name").value = fillVal;
            document.getElementById("url").focus();
            syncCreatorPreview();
            loadCreators();
          };
          box.appendChild(btn);
        });
        syncCreatorPreview();
      } catch (_) {}
    }

    function beginPolling(jobId) {
      currentJobId = jobId;
      document.getElementById("clipList").innerHTML = "";
      if (pollTimer) clearInterval(pollTimer);
      pollTimer = setInterval(pollJob, 1000);
      pollJob();
    }

    async function startYouTubeJob(e) {
      e.preventDefault();
      const mode = document.querySelector('input[name="clip_mode"]:checked').value;
      const rawAliases = document.getElementById("yt_creator_aliases").value || "";
      const payload = {
        creator_name: document.getElementById("yt_creator_name").value.trim(),
        creator_aliases: rawAliases.split(",").map((s) => s.trim()).filter(Boolean),
        url: document.getElementById("url").value.trim(),
        clip_mode: mode,
        max_clips: parseInt(document.getElementById("max_clips").value || "3", 10),
        max_good_cap: parseInt(document.getElementById("max_good_cap").value || "20", 10),
        chunk_duration: parseFloat(document.getElementById("chunk_duration").value || "20"),
        fit_mode: document.getElementById("fit_mode").value,
        output_dir: "{{ default_output_dir }}"
      };

      const res = await fetch("/api/clip", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload)
      });
      const data = await res.json();
      if (!res.ok) {
        alert(data.error || "Failed to start clipping job");
        return;
      }
      beginPolling(data.job_id);
    }

    async function startMultiEditJob(e) {
      e.preventDefault();
      if (!selectedFiles.length) {
        alert("Drop or select at least one video clip first.");
        return;
      }

      const formData = new FormData();
      selectedFiles.forEach((file) => formData.append("clips", file));

      const musicEl = document.getElementById("musicFileInput");
      if (musicEl && musicEl.files && musicEl.files.length > 0) {
        formData.append("music", musicEl.files[0]);
      }

      const fields = [
        "creator_name", "creator_aliases", "music_volume", "edit_style",
        "transition_type", "jl_cut_mode", "audio_effect", "caption_style",
        "font_name", "adjustment_mode", "scene_duration", "scenes_per_clip",
        "whisper_model", "punch_texts"
      ];
      fields.forEach((id) => {
        formData.append(id, document.getElementById(id).value || "");
      });

      const checks = [
        "sync_to_music", "duck_music_under_voice", "live_voice_captions",
        "caption_highlight", "impact_bounce", "sfx_whoosh", "allow_repeat_and_vary"
      ];
      checks.forEach((id) => {
        formData.append(id, document.getElementById(id).checked ? "true" : "false");
      });

      const res = await fetch("/api/multi_edit", {
        method: "POST",
        body: formData
      });
      const data = await res.json();
      if (!res.ok) {
        alert(data.error || "Failed to start multi-clip edit");
        return;
      }
      beginPolling(data.job_id);
    }

    async function stopJob() {
      if (!currentJobId) return;
      await fetch(`/api/stop/${currentJobId}`, { method: "POST" });
    }

    async function pollJob() {
      if (!currentJobId) return;
      const res = await fetch(`/api/status/${currentJobId}`);
      if (!res.ok) return;
      const job = await res.json();

      document.getElementById("statusBadge").textContent = job.status;
      const logBox = document.getElementById("logBox");
      logBox.textContent = (job.logs || []).join("\\n");
      logBox.scrollTop = logBox.scrollHeight;

      const listEl = document.getElementById("clipList");
      listEl.innerHTML = "";
      (job.clips || []).forEach((filePath) => {
        const parts = filePath.split(/[\\\\/]/);
        const fname = parts.pop();
        const folder = parts.pop() || "";
        const rel = folder && folder !== "clip_output" ? `${folder}/${fname}` : fname;
        const li = document.createElement("li");
        li.innerHTML = `<span>${filePath}</span><a href="/clips/${encodeURIComponent(rel)}" target="_blank">Open MP4</a>`;
        listEl.appendChild(li);
      });

      if (job.status === "completed" || job.status === "stopped" || job.status === "error") {
        clearInterval(pollTimer);
        pollTimer = null;
        loadCreators();
      }
    }

    document.getElementById("clipForm").addEventListener("submit", startYouTubeJob);
    document.getElementById("multiEditForm").addEventListener("submit", startMultiEditJob);
    loadCreators();
  </script>
</body>
</html>
"""


def _clamp_int(val: Any, default: int, lo: int = 1, hi: int = 50) -> int:
    try:
        return max(lo, min(hi, int(val)))
    except (TypeError, ValueError):
        return default


def _run_clip_job(job_id: str, params: Dict[str, Any]) -> None:
    stop_event: threading.Event = params["stop_event"]

    def append_log(msg: str) -> None:
        with JOBS_LOCK:
            JOBS[job_id]["logs"].append(msg)

    try:
        registry = CreatorRegistry(output_root=DEFAULT_OUTPUT_DIR)
        raw_creator = str(params.get("creator_name") or "").strip()
        aliases = list(params.get("creator_aliases") or [])

        effective_folder = sanitize_creator_name(raw_creator) if raw_creator else TRIAL_FOLDER_NAME
        target_dir = registry.get_creator_folder(creator_name=effective_folder, aliases=aliases)
        append_log(f"Saving clips to folder: {target_dir}")

        clips: List[str] = detect_reactions_and_clip(
            source_input=params["url"],
            custom_output_dir=str(target_dir),
            is_youtube=True,
            clip_mode=params["clip_mode"],
            max_clips=params["max_clips"],
            max_good_cap=params["max_good_cap"],
            chunk_duration=params["chunk_duration"],
            fit_mode=params["fit_mode"],
            cookies_path=DEFAULT_COOKIES_PATH,
            stop_event=stop_event,
            prompt_interactively=False,
            on_progress=append_log,
        )

        if not raw_creator and clips:
            detected = detect_channel_name_from_video(clips[0], fallback_name=None)
            if detected and detected != TRIAL_FOLDER_NAME:
                effective_folder = sanitize_creator_name(detected)
                creator_folder = registry.get_creator_folder(creator_name=effective_folder, aliases=aliases)
                moved_clips: List[str] = []
                for c_path in clips:
                    src_p = Path(c_path)
                    if src_p.is_file() and src_p.parent.resolve() != creator_folder.resolve():
                        dst_p = creator_folder / src_p.name
                        try:
                            src_p.replace(dst_p)
                            moved_clips.append(str(dst_p))
                        except Exception:
                            moved_clips.append(str(src_p))
                    else:
                        moved_clips.append(str(src_p))
                clips = moved_clips

        for c_path in clips:
            registry.record_saved_clip(
                creator_name=effective_folder,
                clip_path=c_path,
                aliases=aliases,
            )

        with JOBS_LOCK:
            JOBS[job_id]["clips"] = clips
            JOBS[job_id]["status"] = "stopped" if stop_event.is_set() else "completed"
    except Exception as exc:
        append_log(f"Fatal job error: {exc}")
        with JOBS_LOCK:
            JOBS[job_id]["status"] = "error"


def _run_multi_edit_job(job_id: str, params: Dict[str, Any]) -> None:
    stop_event: threading.Event = params["stop_event"]

    def append_log(msg: str) -> None:
        with JOBS_LOCK:
            JOBS[job_id]["logs"].append(msg)

    try:
        if stop_event.is_set():
            with JOBS_LOCK:
                JOBS[job_id]["status"] = "stopped"
            return

        registry = CreatorRegistry(output_root=DEFAULT_OUTPUT_DIR)
        explicit_creator = str(params.get("creator_name") or "").strip()
        aliases = list(params.get("creator_aliases") or [])

        if explicit_creator:
            channel_name = sanitize_creator_name(explicit_creator)
        else:
            detected_name = detect_channel_name_from_video(params["clip_paths"][0], fallback_name=None)
            channel_name = sanitize_creator_name(detected_name) if detected_name else TRIAL_FOLDER_NAME

        creator_dir = registry.get_creator_folder(creator_name=channel_name, aliases=aliases)
        safe_channel = sanitize_creator_name(channel_name)
        output_path = str(creator_dir / f"{safe_channel}_{job_id}_9x16.mp4")
        append_log(f"Routing edit to folder: {creator_dir}")

        out_path = make_multi_clip_edit(
            clip_paths=params["clip_paths"],
            output_path=output_path,
            scene_duration=params["scene_duration"],
            scenes_per_clip=params["scenes_per_clip"],
            punch_texts=params["punch_texts"],
            live_voice_captions=params["live_voice_captions"],
            whisper_model=params["whisper_model"],
            transition_type=params["transition_type"],
            jl_cut_mode=params["jl_cut_mode"],
            edit_style=params["edit_style"],
            audio_effect=params["audio_effect"],
            caption_style=params["caption_style"],
            font_name=params["font_name"],
            adjustment_mode=params["adjustment_mode"],
            music_path=params["music_path"],
            music_volume=params["music_volume"],
            sync_to_music=params["sync_to_music"],
            duck_music_under_voice=params["duck_music_under_voice"],
            caption_highlight=params["caption_highlight"],
            impact_bounce=params["impact_bounce"],
            sfx_whoosh=params["sfx_whoosh"],
            allow_repeat_and_vary=params["allow_repeat_and_vary"],
            on_log=append_log,
        )
        registry.record_saved_clip(
            creator_name=channel_name,
            clip_path=out_path,
            aliases=aliases,
        )
        append_log(f"Saved edit: {out_path}")
        with JOBS_LOCK:
            JOBS[job_id]["clips"] = [out_path]
            JOBS[job_id]["status"] = "stopped" if stop_event.is_set() else "completed"
    except Exception as exc:
        append_log(f"Edit error: {exc}")
        with JOBS_LOCK:
            JOBS[job_id]["status"] = "error"


@app.route("/")
def index():
    return render_template_string(HTML_PAGE, default_output_dir=str(DEFAULT_OUTPUT_DIR))


@app.route("/api/creators")
def api_creators():
    registry = CreatorRegistry(output_root=DEFAULT_OUTPUT_DIR)
    return jsonify({"creators": registry.list_creators()})


@app.route("/api/clip", methods=["POST"])
def api_clip():
    global ACTIVE_JOB_ID
    data = request.get_json(silent=True) or request.form.to_dict() or {}
    url = str(data.get("url") or "").strip()
    if not url:
        return jsonify({"error": "Please provide a YouTube video or live stream URL."}), 400

    creator_name = str(data.get("creator_name") or "").strip()
    raw_aliases = data.get("creator_aliases") or []
    if isinstance(raw_aliases, str):
        creator_aliases = [a.strip() for a in raw_aliases.split(",") if a.strip()]
    else:
        creator_aliases = [str(a).strip() for a in raw_aliases if str(a).strip()]

    clip_mode = str(data.get("clip_mode") or "count").strip().lower()
    if clip_mode not in {"count", "all_good"}:
        clip_mode = "count"

    max_clips = _clamp_int(data.get("max_clips"), default=3, lo=1, hi=50)
    max_good_cap = _clamp_int(data.get("max_good_cap"), default=20, lo=1, hi=50)

    try:
        chunk_duration = max(5.0, min(120.0, float(data.get("chunk_duration") or 20.0)))
    except (TypeError, ValueError):
        chunk_duration = 20.0

    fit_mode = str(data.get("fit_mode") or "blurred_bg").strip()
    if fit_mode not in {"blurred_bg", "letterbox", "center_crop"}:
        fit_mode = "blurred_bg"

    job_id = str(uuid.uuid4())[:8]
    stop_event = threading.Event()

    with JOBS_LOCK:
        JOBS[job_id] = {
            "id": job_id,
            "status": "running",
            "logs": [f"Started YouTube clip job {job_id} (folder='{creator_name or TRIAL_FOLDER_NAME}', mode={clip_mode}, max_clips={max_clips})"],
            "clips": [],
            "stop_event": stop_event,
        }
        ACTIVE_JOB_ID = job_id

    threading.Thread(
        target=_run_clip_job,
        args=(
            job_id,
            {
                "creator_name": creator_name,
                "creator_aliases": creator_aliases,
                "url": url,
                "clip_mode": clip_mode,
                "max_clips": max_clips,
                "max_good_cap": max_good_cap,
                "chunk_duration": chunk_duration,
                "fit_mode": fit_mode,
                "output_dir": str(DEFAULT_OUTPUT_DIR),
                "stop_event": stop_event,
            },
        ),
        daemon=True,
    ).start()

    return jsonify({"job_id": job_id, "status": "running"})


@app.route("/api/multi_edit", methods=["POST"])
def api_multi_edit():
    global ACTIVE_JOB_ID
    uploaded = request.files.getlist("clips")
    saved_paths: List[str] = []
    batch_id = str(uuid.uuid4())[:8]

    for idx, f in enumerate(uploaded):
        if not f or not f.filename:
            continue
        safe_name = secure_filename(f.filename)
        ext = Path(safe_name).suffix.lower()
        if ext not in ALLOWED_VIDEO_EXTS:
            ext = ".mp4"
        dest = UPLOAD_DIR / f"{batch_id}_{idx:02d}_{Path(safe_name).stem or 'clip'}{ext}"
        f.save(str(dest))
        if dest.is_file() and dest.stat().st_size > 0:
            saved_paths.append(str(dest))

    if not saved_paths:
        return jsonify({"error": "No valid video files received."}), 400

    creator_name = str(request.form.get("creator_name") or "").strip()
    raw_aliases = str(request.form.get("creator_aliases") or "")
    creator_aliases = [a.strip() for a in raw_aliases.split(",") if a.strip()]

    music_saved_path: str | None = None
    music_file = request.files.get("music")
    if music_file and music_file.filename:
        m_safe = secure_filename(music_file.filename)
        m_ext = Path(m_safe).suffix.lower()
        if m_ext not in ALLOWED_MUSIC_EXTS:
            m_ext = ".mp3"
        m_dest = UPLOAD_DIR / f"{batch_id}_music_{Path(m_safe).stem or 'track'}{m_ext}"
        music_file.save(str(m_dest))
        if m_dest.is_file() and m_dest.stat().st_size > 0:
            music_saved_path = str(m_dest)

    try:
        music_volume = max(0.05, min(1.0, float(request.form.get("music_volume") or 0.35)))
    except (TypeError, ValueError):
        music_volume = 0.35

    sync_to_music = str(request.form.get("sync_to_music") or "true").strip().lower() in {"true", "1", "yes", "on"}
    duck_music_under_voice = str(request.form.get("duck_music_under_voice") or "true").strip().lower() in {"true", "1", "yes", "on"}

    valid_styles = {
        "auto_random", "lens_blur", "zoom_blur", "mosaic_blur", "prism_blur",
        "camera_shake", "dent_warp", "vortex_spin", "jpeg_damage", "drop_shadow",
        "edge_detect", "halftones", "outline_pop", "wiggle", "blanking_fill",
        "glass_box", "transform_pan", "continuous_zoom", "dynamic_zoom",
        "retime_curve", "speed_ramp", "opacity_fade", "halation_bloom",
        "grain_vignette", "chromatic_aberration", "gaussian_motion_blur",
        "chroma_key", "rotoscoping_pop", "broll_overlay", "bezier_keyframe",
    }
    edit_style = str(request.form.get("edit_style") or "auto_random").strip().lower()
    if edit_style not in valid_styles:
        edit_style = "auto_random"

    try:
        scene_duration = max(1.5, min(30.0, float(request.form.get("scene_duration") or 5.0)))
    except (TypeError, ValueError):
        scene_duration = 5.0

    scenes_per_clip = _clamp_int(request.form.get("scenes_per_clip"), default=1, lo=1, hi=10)

    transition_type = str(request.form.get("transition_type") or "random").strip().lower()
    jl_cut_mode = str(request.form.get("jl_cut_mode") or "j_l_mix").strip().lower()
    if jl_cut_mode not in {"j_l_mix", "j_cut", "l_cut", "standard"}:
        jl_cut_mode = "j_l_mix"

    valid_audio = {
        "auto_random", "voice_isolation", "dialogue_leveler", "noise_reduction",
        "de_esser", "ducking_polish", "reverb_echo", "muffled_wall_eq",
        "pitch_up", "pitch_down", "none",
    }
    audio_effect = str(request.form.get("audio_effect") or "auto_random").strip().lower()
    if audio_effect not in valid_audio:
        audio_effect = "auto_random"

    caption_style = str(request.form.get("caption_style") or "pop_bounce").strip().lower()
    if caption_style not in {"pop_bounce", "kinetic", "typewriter", "lower_third", "script"}:
        caption_style = "pop_bounce"

    font_name = str(request.form.get("font_name") or "Arial").strip()
    if font_name not in {"Arial", "Impact", "Trebuchet MS", "Georgia"}:
        font_name = "Arial"

    valid_adj = {"auto_polish", "vignette_frame", "punch_pulse", "none"}
    adjustment_mode = str(request.form.get("adjustment_mode") or "auto_polish").strip().lower()
    if adjustment_mode not in valid_adj:
        adjustment_mode = "auto_polish"

    whisper_model = str(request.form.get("whisper_model") or "base").strip().lower()
    if whisper_model not in {"tiny", "base", "small"}:
        whisper_model = "base"

    raw_punches = str(request.form.get("punch_texts") or "")
    punch_texts = [t.strip() for t in raw_punches.split("|") if t.strip()] or None

    live_voice_captions = str(request.form.get("live_voice_captions") or "true").strip().lower() in {"true", "1", "yes", "on"}
    caption_highlight = str(request.form.get("caption_highlight") or "true").strip().lower() in {"true", "1", "yes", "on"}
    impact_bounce = str(request.form.get("impact_bounce") or "true").strip().lower() in {"true", "1", "yes", "on"}
    sfx_whoosh = str(request.form.get("sfx_whoosh") or "true").strip().lower() in {"true", "1", "yes", "on"}
    allow_repeat_and_vary = str(request.form.get("allow_repeat_and_vary") or "true").strip().lower() in {"true", "1", "yes", "on"}

    stop_event = threading.Event()

    with JOBS_LOCK:
        JOBS[batch_id] = {
            "id": batch_id,
            "status": "running",
            "logs": [f"Started edit {batch_id} (folder='{creator_name or TRIAL_FOLDER_NAME}', song={'yes' if music_saved_path else 'no'})"],
            "clips": [],
            "stop_event": stop_event,
        }
        ACTIVE_JOB_ID = batch_id

    threading.Thread(
        target=_run_multi_edit_job,
        args=(
            batch_id,
            {
                "creator_name": creator_name,
                "creator_aliases": creator_aliases,
                "clip_paths": saved_paths,
                "scene_duration": scene_duration,
                "scenes_per_clip": scenes_per_clip,
                "punch_texts": punch_texts,
                "live_voice_captions": live_voice_captions,
                "whisper_model": whisper_model,
                "transition_type": transition_type,
                "jl_cut_mode": jl_cut_mode,
                "edit_style": edit_style,
                "audio_effect": audio_effect,
                "caption_style": caption_style,
                "font_name": font_name,
                "adjustment_mode": adjustment_mode,
                "music_path": music_saved_path,
                "music_volume": music_volume,
                "sync_to_music": sync_to_music,
                "duck_music_under_voice": duck_music_under_voice,
                "caption_highlight": caption_highlight,
                "impact_bounce": impact_bounce,
                "sfx_whoosh": sfx_whoosh,
                "allow_repeat_and_vary": allow_repeat_and_vary,
                "stop_event": stop_event,
            },
        ),
        daemon=True,
    ).start()

    return jsonify({"job_id": batch_id, "status": "running"})


@app.route("/api/stop/<job_id>", methods=["POST"])
def api_stop(job_id: str):
    with JOBS_LOCK:
        job = JOBS.get(job_id)
        if not job:
            return jsonify({"error": "Job not found"}), 404
        job["stop_event"].set()
        job["logs"].append("Stop requested by user...")
    return jsonify({"job_id": job_id, "stopping": True})


@app.route("/api/status/<job_id>")
def api_status(job_id: str):
    with JOBS_LOCK:
        job = JOBS.get(job_id)
        if not job:
            return jsonify({"error": "Job not found"}), 404
        return jsonify(
            {
                "id": job["id"],
                "status": job["status"],
                "logs": list(job["logs"]),
                "clips": list(job["clips"]),
            }
        )


@app.route("/clips/<path:rel_path>")
def serve_clip(rel_path: str):
    target_new = DEFAULT_OUTPUT_DIR / rel_path
    if target_new.is_file():
        return send_from_directory(str(DEFAULT_OUTPUT_DIR), rel_path, as_attachment=False)
    return send_from_directory(str(LEGACY_CLIPS_DIR), Path(rel_path).name, as_attachment=False)


if __name__ == "__main__":
    print("Starting Clipntax Studio at http://127.0.0.1:5000")
    app.run(host="127.0.0.1", port=5000, debug=False, threaded=True)
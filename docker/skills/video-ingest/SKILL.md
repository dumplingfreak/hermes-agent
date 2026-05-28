---
name: video-ingest
description: Raw acquisition only — download a YouTube video, extract transcript and (if mode=full) frames + video archive. Saves everything into the vault raw folder. Does NOT write a wiki report. Report writing is done separately by Claude Code using the `ingest` command. Supports mode=full (transcript + video + frames) and mode=transcript_only (transcript + metadata only). Handles long videos with no captions via Groq audio chunking.
---

# Skill: Video Ingest — Raw Acquisition (YouTube only)

Download a YouTube video and build the raw evidence archive. No analysis, no wiki report — acquire and preserve raw materials so Claude Code can do the report pass later.

**Mode is set per channel in `channels.json`.** When called from `yt-channel-watch`, the mode is passed from the channel config. When called manually, default to `full` unless told otherwise.

---

## CRITICAL RULES
- **YouTube only** — this skill does not handle Circle.so or Verified Investing dashboard URLs
- **NEVER run `yt-dlp --dump-json`** as a pipeline step — metadata only. Exception: duration check only when needed.
- **NEVER stop mid-task to ask the user anything** — run all steps start to finish
- **NEVER run `find` to search for frames** — they are always in `/tmp/hermes_ingest/`
- **Do NOT write a wiki report** — stop after raw archive is committed. Claude Code runs `ingest [domain]` separately.
- **Do NOT do vision analysis or vault cross-referencing** — that is Claude Code's job.
- **Model routing:** use **deepseek/deepseek-v4-flash exclusively**. Do not use Codex, Claude Haiku, or any other model.
- **Dirty vault safety:** commit only files touched by this ingest. Use `git commit --only -- <pathspec...>` when unrelated staged files exist.
- **Keep the MP4** (full mode only) — do not delete it after frame extraction. Frames must come from the retained archive file.

---

## Step 0 — Detect domain and mode

**Domain** from video title/channel:

| Domain | Signals |
|---|---|
| `finance` | crypto, stocks, Bitcoin, trading, investing, TA, macro, Fed, markets |
| `psychology` | mindset, habits, behavior, therapy, neuroscience |
| `tech` | AI, coding, software, hardware, tools, LLMs |
| `music` | violin, practice, performance, theory |
| `other` | anything else |

**Mode** is passed in by the caller. If not specified, default to `full`.

- `full` — transcript + video archive + frames + contact sheet
- `transcript_only` — transcript + metadata only, no video download, no frames

---

## Step 1 — Extract transcript (captions)

```bash
mkdir -p /tmp/hermes_ingest
yt-dlp --write-subs --write-auto-subs --sub-langs en --skip-download \
  --write-info-json \
  --output "/tmp/hermes_ingest/video" "[URL]"
```

Check if captions were downloaded:
```bash
ls /tmp/hermes_ingest/video*.vtt 2>/dev/null || ls /tmp/hermes_ingest/video*.srt 2>/dev/null
```

**If captions exist** → extract `transcript.txt` from them (see Step 3).

**If no captions exist** → check video duration from the info JSON:
```bash
python3 -c "
import json
d = json.load(open('/tmp/hermes_ingest/video.info.json'))
print(f'duration={d[\"duration\"]} title={d[\"title\"][:80]}')
"
```

- Duration **≤ 1800s (30 min)**: download audio and run Groq (Step 1b).
- Duration **> 1800s (30 min)**: download audio, split into 10-min chunks, run Groq on each chunk (Step 1c).

---

## Step 1b — Groq transcription (short video, no captions)

```bash
# Download audio only
yt-dlp --format "bestaudio" --extract-audio --audio-format mp3 \
  --output "/tmp/hermes_ingest/audio.%(ext)s" "[URL]"

# Transcribe with Groq
python3 - << 'PYEOF'
import os, requests

audio_path = "/tmp/hermes_ingest/audio.mp3"
api_key = os.environ.get("GROQ_API_KEY", "")
if not api_key:
    print("ERROR: GROQ_API_KEY not set")
    exit(1)

with open(audio_path, "rb") as f:
    resp = requests.post(
        "https://api.groq.com/openai/v1/audio/transcriptions",
        headers={"Authorization": f"Bearer {api_key}"},
        files={"file": ("audio.mp3", f, "audio/mpeg")},
        data={"model": "whisper-large-v3", "response_format": "text"}
    )

if resp.status_code == 200:
    with open("/tmp/hermes_ingest/transcript.txt", "w") as out:
        out.write(resp.text)
    print(f"Groq transcript: {len(resp.text)} chars")
else:
    print(f"Groq error {resp.status_code}: {resp.text}")
    exit(1)
PYEOF
```

---

## Step 1c — Groq transcription (long video, split into chunks)

```bash
# Download audio only
yt-dlp --format "bestaudio" --extract-audio --audio-format mp3 \
  --output "/tmp/hermes_ingest/audio.%(ext)s" "[URL]"

# Split into 10-minute chunks and transcribe each
python3 - << 'PYEOF'
import os, subprocess, requests, glob

audio_path = "/tmp/hermes_ingest/audio.mp3"
chunks_dir = "/tmp/hermes_ingest/chunks"
os.makedirs(chunks_dir, exist_ok=True)
api_key = os.environ.get("GROQ_API_KEY", "")
if not api_key:
    print("ERROR: GROQ_API_KEY not set")
    exit(1)

# Split into 10-min chunks
subprocess.run([
    "ffmpeg", "-y", "-i", audio_path,
    "-f", "segment", "-segment_time", "600",
    "-c", "copy",
    f"{chunks_dir}/chunk_%03d.mp3"
], check=True)

chunks = sorted(glob.glob(f"{chunks_dir}/chunk_*.mp3"))
print(f"Split into {len(chunks)} chunks")

all_text = []
for i, chunk_path in enumerate(chunks):
    print(f"Transcribing chunk {i+1}/{len(chunks)}: {chunk_path}")
    with open(chunk_path, "rb") as f:
        resp = requests.post(
            "https://api.groq.com/openai/v1/audio/transcriptions",
            headers={"Authorization": f"Bearer {api_key}"},
            files={"file": (os.path.basename(chunk_path), f, "audio/mpeg")},
            data={"model": "whisper-large-v3", "response_format": "text"}
        )
    if resp.status_code == 200:
        all_text.append(resp.text.strip())
        print(f"  OK: {len(resp.text)} chars")
    else:
        print(f"  ERROR chunk {i+1}: {resp.status_code} {resp.text[:200]}")
        # Continue — partial transcript is still useful

full_transcript = "\n\n".join(all_text)
with open("/tmp/hermes_ingest/transcript.txt", "w") as out:
    out.write(full_transcript)
print(f"\nFull transcript: {len(full_transcript)} chars from {len(all_text)} chunks")
PYEOF
```

---

## Step 2 — Download video + extract frames (full mode only)

Skip this step entirely if `mode=transcript_only`.

```bash
yt-dlp --format "bestvideo[height<=720]+bestaudio/best[height<=720]/best" \
  --merge-output-format mp4 \
  --output "/tmp/hermes_ingest/video.%(ext)s" "[URL]"

# Verify
ffprobe -v error \
  -show_entries stream=index,codec_type,codec_name,width,height:format=duration,size \
  -of json /tmp/hermes_ingest/video.mp4

# Extract frames at 1 per 30 seconds
ffmpeg -y -i /tmp/hermes_ingest/video.mp4 \
  -vf "fps=1/30,scale=1280:-1" \
  /tmp/hermes_ingest/frame_%04d.jpg

# Generate contact sheet
ffmpeg -y -pattern_type glob -i '/tmp/hermes_ingest/frame_*.jpg' \
  -filter_complex tile=6x0 \
  /tmp/hermes_ingest/contact_sheet.jpg
```

Verify frame count: `ls /tmp/hermes_ingest/frame_*.jpg | wc -l`

---

## Step 3 — Extract transcript.txt from captions (if captions exist)

If Step 1 produced a VTT/SRT, extract clean text now (skip if Groq already wrote `transcript.txt`):

```bash
python3 - << 'PYEOF'
import re, pathlib, glob

# Find the caption file
files = glob.glob("/tmp/hermes_ingest/video*.vtt") + glob.glob("/tmp/hermes_ingest/video*.srt")
if not files:
    print("No caption file found")
    exit(0)

vtt = pathlib.Path(files[0]).read_text(errors="replace")
lines = vtt.splitlines()
seen, out = set(), []
for line in lines:
    line = line.strip()
    if not line or line.startswith("WEBVTT") or re.match(r"^\d+$", line) or "-->" in line:
        continue
    clean = re.sub(r"<[^>]+>", "", line)  # strip inline tags
    if clean and clean not in seen:
        seen.add(clean)
        out.append(clean)

pathlib.Path("/tmp/hermes_ingest/transcript.txt").write_text("\n".join(out))
print(f"Transcript: {len(out)} lines")
PYEOF
```

---

## Step 4 — Build raw archive

Archive folder paths by domain:

| Domain | Path |
|---|---|
| `finance` | `~/vault/raw/finance/youtube/YYYY-MM-DD_[analyst]_[topic_slug]/` |
| `psychology` | `~/vault/raw/psychology/youtube/YYYY-MM-DD_[source]_[topic_slug]/` |
| `tech` | `~/vault/raw/tech/youtube/YYYY-MM-DD_[source]_[topic_slug]/` |
| `other` | `~/vault/raw/other/YYYY-MM-DD_[source]_[topic_slug]/` |

Use lowercase, hyphenated slugs. Keep it short (analyst + 2-3 topic words).

```bash
ARCHIVE=~/vault/raw/finance/youtube/YYYY-MM-DD_[slug]
mkdir -p "$ARCHIVE/visuals"  # only needed for full mode

# Always copy
cp /tmp/hermes_ingest/video.info.json  "$ARCHIVE/metadata.info.json"
cp /tmp/hermes_ingest/transcript.txt   "$ARCHIVE/transcript.txt"
[ -f /tmp/hermes_ingest/video.en.vtt ] && cp /tmp/hermes_ingest/video.en.vtt "$ARCHIVE/video.en.vtt"

# Full mode only
cp /tmp/hermes_ingest/video.mp4        "$ARCHIVE/video.mp4"
cp /tmp/hermes_ingest/frame_*.jpg      "$ARCHIVE/visuals/"
cp /tmp/hermes_ingest/contact_sheet.jpg "$ARCHIVE/visual_contact_sheet.jpg"
```

Write `README.md`:

**Full mode:**
```markdown
# [Full video title]

- **Source URL:** [URL]
- **YouTube ID:** [id]
- **Channel:** [channel name]
- **Upload date:** YYYYMMDD
- **Duration:** HH:MM
- **Mode:** full — transcript + frames + HD audio+video archive
- **Video evidence file:** `video.mp4` — 1280x720 HD MP4 with H.264 video + AAC audio, duration ~HH:MM, size ~X MB; verified with ffprobe

## Files

- `metadata.info.json` — yt-dlp metadata sidecar
- `video.en.vtt` — downloaded English captions (if available)
- `transcript.txt` — cleaned transcript (from captions or Groq)
- `video.mp4` — 1280x720 HD downloaded audio+video evidence file
- `visuals/frame_*.jpg` — [N] frames sampled at 1 frame / 30 seconds
- `visual_contact_sheet.jpg` — tiled overview of sampled frames

## Status

Raw only. Run `ingest finance` in Claude Code to generate the wiki report at:
`wiki/finance/reports/YYYYMMDD_[Analyst]_[Topic].md`
```

**Transcript-only mode:**
```markdown
# [Full video title]

- **Source URL:** [URL]
- **YouTube ID:** [id]
- **Channel:** [channel name]
- **Upload date:** YYYYMMDD
- **Duration:** HH:MM
- **Mode:** transcript_only — transcript + metadata, no video/frames

## Files

- `metadata.info.json` — yt-dlp metadata sidecar
- `video.en.vtt` — downloaded English captions (if available)
- `transcript.txt` — cleaned transcript (from captions or Groq)

## Status

Raw only. Run `ingest finance` in Claude Code to generate the wiki report at:
`wiki/finance/reports/YYYYMMDD_[Analyst]_[Topic].md`
```

---

## Step 5 — Cleanup

```bash
rm -rf /tmp/hermes_ingest/
```

---

## Step 6 — Vault commit and backup

```bash
git -C ~/vault add "$ARCHIVE"
git -C ~/vault diff --cached --stat
git -C ~/vault commit --only -m "raw: acquire [channel] [topic] video" -- "$ARCHIVE"
git -C ~/vault push
bash ~/vault/scripts/backup_to_drive.sh
```

---

## Report to user

- Video title + channel
- Mode used (full / transcript_only)
- Transcript source (captions / Groq single / Groq chunked — N chunks)
- Raw archive path (relative from vault root)
- Frame count + video size (full mode only)
- Next step: **run `ingest finance` in Claude Code to generate the wiki report**

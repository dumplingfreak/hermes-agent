---
name: yt-channel-watch
description: "Check watched YouTube channels for new videos and auto-ingest into vault. Channels include Gareth Soloway, Benjamin Cowen, Scott Melker, Krown."
---

# Skill: YouTube Channel Watch + Auto-Ingest

Check configured finance channels for videos published in the last 3 days and ingest one new video into the vault using the video-ingest pipeline.

## CRITICAL RULES
- **No confirmation steps** — run the full pipeline autonomously start to finish
- **Skip already-ingested videos** — check BOTH state.json AND the vault (existing reports under `~/vault/wiki/finance/reports/`) before ingesting. State.json can be stale if a prior run wrote the report but crashed before updating state.
- **Do not keyword-skip finance videos** — channels are curated by Gia; treat recent videos from these channels as relevant unless they are shorts/ads/trailers under 3 min or already ingested
- **Ingest one new video per run** — if the first candidate is already ingested or skipped for a hard reason, continue down the candidate list until one new eligible video is ingested or no candidates remain
- **Only consider last 3 days** — ignore videos older than the rolling `lookback_days` window in `channels.json`
- **Update state.json** after every successful ingest — and repair it if you find stale entries (video already has a vault report but is missing from state.json)
- **Do not advance last_checked past pending videos** — only mark a video/channel processed after a successful ingest or confirmed existing vault report

---

## PITFALLS
- **State.json desync**: `state.json` is the source of truth for *what was processed this session*, not for what exists in the vault. Always search `~/vault/wiki/finance/reports/` for existing reports matching a video's title or upload date before declaring it "new." If a report already exists, add the video_id to state.json's `ingested` list rather than re-ingesting.
- **Flat-playlist loses upload dates**: `--flat-playlist --print %(upload_date)s` returns "NA" for many channels. After identifying candidate videos, fetch actual dates via `--dump-json` on each before deciding which is newest.
- **Backup may timeout**: `scripts/backup_to_drive.sh` can hang. Run it in background (notify_on_complete=true) or skip if it's non-critical. The vault is the source of truth; Drive is a mirror.

---

## Step 1 — Load config and state

```bash
python3 - << 'EOF'
import json, os
config = json.load(open(os.path.expanduser('~/.hermes/skills/yt-channel-watch/channels.json')))
state = json.load(open(os.path.expanduser('~/.hermes/skills/yt-channel-watch/state.json')))
for ch in config['channels']:
    cid = ch['id']
    print(f"CHANNEL: {ch['name']} | last_checked: {state[cid]['last_checked']} | ingested: {len(state[cid]['ingested'])} videos")
EOF
```

---

## Step 2 — Fetch recent videos from each channel

For each channel, get the last 5 videos:

```bash
python3 - << 'EOF'
import subprocess, json, os
from datetime import datetime, timedelta

config = json.load(open(os.path.expanduser('~/.hermes/skills/yt-channel-watch/channels.json')))
state = json.load(open(os.path.expanduser('~/.hermes/skills/yt-channel-watch/state.json')))

new_videos = []
lookback_days = int(config.get('lookback_days', 3))
cutoff = (datetime.now() - timedelta(days=lookback_days)).strftime('%Y%m%d')
print(f"Only considering videos uploaded since {cutoff} ({lookback_days} days)")

for ch in config['channels']:
    cid = ch['id']
    last_checked = state[cid]['last_checked']
    already_ingested = state[cid]['ingested']
    
    print(f"\nChecking {ch['name']}...")
    
    result = subprocess.run([
        'yt-dlp',
        '--flat-playlist',
        '--playlist-items', '1-5',
        '--print', '%(id)s\t%(title)s\t%(upload_date)s\t%(duration)s',
        f"https://www.youtube.com/channel/{cid}/videos"
    ], capture_output=True, text=True)
    
    for line in result.stdout.strip().split('\n'):
        if not line.strip():
            continue
        parts = line.split('\t')
        if len(parts) < 4:
            continue
        vid_id, title, upload_date, duration = parts[0], parts[1], parts[2], parts[3]
        
        # Skip already ingested (state.json check)
        if vid_id in already_ingested:
            print(f"  SKIP (state says ingested): {title[:60]}")
            continue
        
        # Skip if outside rolling lookback window.
        if upload_date and upload_date != 'NA' and upload_date < cutoff:
            print(f"  SKIP (older than {lookback_days} days): {title[:60]}")
            continue
        
        # Skip shorts (under 3 min = 180s)
        try:
            if float(duration) < 180:
                print(f"  SKIP (too short {duration}s): {title[:60]}")
                continue
        except:
            pass
        
        print(f"  CANDIDATE: {title[:60]} [{upload_date}]")
        new_videos.append({
            'channel_id': cid,
            'channel_name': ch['name'],
            'video_id': vid_id,
            'title': title,
            'url': f"https://www.youtube.com/watch?v={vid_id}",
            'upload_date': upload_date,
            'domain': ch['domain']
        })

# Save candidates to temp file
with open('/tmp/yt_new_videos.json', 'w') as f:
    json.dump(new_videos, f, indent=2)

print(f"\nTotal candidate videos: {len(new_videos)}")
for v in new_videos:
    print(f"  - [{v['channel_name']}] {v['title'][:60]}")
EOF
```

---

## Step 3 — Vault consistency check (prevent re-ingest)

Before running the full video-ingest pipeline, check if any candidate videos already have reports in the vault. State.json desync is common.

```bash
python3 - << 'EOF'
import json, os, glob, re

with open('/tmp/yt_new_videos.json') as f:
    candidates = json.load(f)

reports_dir = os.path.expanduser('~/vault/wiki/finance/reports/')
existing_reports = os.listdir(reports_dir)

truly_new = []
stale_entries = []

for v in candidates:
    vid = v['video_id']
    title_slug = re.sub(r'[^a-zA-Z0-9]', '', v['title'].lower()[:40])
    found = False
    for rep in existing_reports:
        rep_lower = rep.lower()
        if vid[:8] in rep or title_slug[:20] in rep_lower:
            print(f"  VAULT-EXISTS: {v['title'][:60]} (report: {rep})")
            found = True
            stale_entries.append(v)
            break
    
    if not found:
        truly_new.append(v)

print(f"\nTruly new: {len(truly_new)} | Stale (report exists, state missing): {len(stale_entries)}")

# Repair state.json for stale entries
if stale_entries:
    state_path = os.path.expanduser('~/.hermes/skills/yt-channel-watch/state.json')
    state = json.load(open(state_path))
    for v in stale_entries:
        cid = v['channel_id']
        if v['video_id'] not in state[cid]['ingested']:
            state[cid]['ingested'].append(v['video_id'])
            print(f"  Repaired state: added {v['video_id']} to {v['channel_name']}")
    with open(state_path, 'w') as f:
        json.dump(state, f, indent=2)

# Overwrite candidates with truly new only
with open('/tmp/yt_new_videos.json', 'w') as f:
    json.dump(truly_new, f, indent=2)

print(f"Final videos to ingest: {len(truly_new)}")
EOF
```

---

## Step 4 — Run video-ingest for the selected video

Take the first truly-new candidate from `/tmp/yt_new_videos.json`. Read its `mode` field from the channel config (already included in the candidate dict from Step 2). Run the `video-ingest` skill for that video, passing the mode explicitly.

```python
# Pseudocode — run as part of the agent's video-ingest skill invocation:
video = candidates[0]
mode = video['mode']   # "full" or "transcript_only" — comes from channels.json
url  = video['url']
# → invoke video-ingest skill with url=url and mode=mode
```

**Mode routing:**
- `full` → video-ingest runs Steps 0–6 (transcript + video + frames + archive)
- `transcript_only` → video-ingest runs Steps 0–1, 3–6 only (transcript + metadata, skip Step 2)

After video-ingest completes, continue to Step 5.

---

## Step 5 — Update state.json after ingest

After each successful ingest, update only that video's entry in state.json. Do not advance `last_checked` for channels that still have pending videos.

```bash
python3 - << 'EOF'
import json, os
state_path = os.path.expanduser('~/.hermes/skills/yt-channel-watch/state.json')
state = json.load(open(state_path))

# Fill these from the video just ingested or confirmed already present in the vault.
channel_id = "CHANNEL_ID"
video_id = "VIDEO_ID"

if video_id not in state[channel_id]['ingested']:
    state[channel_id]['ingested'].append(video_id)

with open(state_path, 'w') as f:
    json.dump(state, f, indent=2)
print(f"State updated for {channel_id}: {video_id}")
EOF
```

---

## Step 6 — Summary report

After all ingests:
- How many new videos found per channel
- Which were ingested vs skipped (with reason)
- How many stale entries repaired from state.json
- Mode used per ingested video (full / transcript_only)
- Transcript source (captions / Groq / Groq-chunked)
- Any errors
- Next step for Gia: run `ingest finance` in Claude Code for each raw archive listed

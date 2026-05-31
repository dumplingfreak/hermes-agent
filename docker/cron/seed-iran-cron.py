#!/usr/bin/env python3
"""
Seed or merge the iran-sentiment-daily cron job into HERMES_HOME/cron/jobs.json.
Idempotent — safe to run on every boot. Only writes if the job is not already present.
Called from stage2-hook.sh.

Unlike decision-engine-tier1 (a --no-agent --script job), this is an AGENT job:
it runs the iran-sentiment-daily skill, which collects the cross-spectrum bundle,
interprets it, updates the actor-model sentiment log, and delivers a briefing.
"""
import json, os, random, string, sys
from datetime import datetime, timezone, timedelta

HERMES_HOME = os.environ.get("HERMES_HOME", "/opt/data")
JOBS_FILE = os.path.join(HERMES_HOME, "cron", "jobs.json")
JOB_NAME = "iran-sentiment-daily"

now = datetime.now(timezone.utc)
# 07:50 UTC daily — 20 min before the 08:10 finance run, so the geopolitical read lands first.
next_run = now.replace(hour=7, minute=50, second=0, microsecond=0)
if next_run <= now:
    next_run += timedelta(days=1)

PROMPT = (
    "Run the iran-sentiment-daily skill now. Collect today's cross-spectrum Iran/war signal "
    "(native Persian/Arabic/Hebrew/English outlets + the Twitter/X scan), interpret where the "
    "camps diverge and whether it's escalation or face-saving noise, grade how realistic each "
    "outcome (stalemate / war resumes / face-saving fudge) now looks, append a dated entry to the "
    "actor-model sentiment log, update iran_us_nuclear.md only if the picture materially shifts, "
    "commit+push the vault, and deliver the briefing."
)

NEW_JOB = {
    "id": "".join(random.choices(string.ascii_lowercase + string.digits, k=12)),
    "name": JOB_NAME,
    "prompt": PROMPT,
    "skills": ["iran-sentiment-daily"],
    "skill": "iran-sentiment-daily",
    "model": None,
    "provider": "openrouter",
    "base_url": None,
    "script": None,
    "no_agent": False,
    "context_from": None,
    "schedule": {"kind": "cron", "expr": "50 7 * * *", "display": "50 7 * * *"},
    "schedule_display": "50 7 * * *",
    "repeat": {"times": None, "completed": 0},
    "enabled": True,
    "state": "scheduled",
    "paused_at": None,
    "paused_reason": None,
    "created_at": now.isoformat(timespec="microseconds"),
    "next_run_at": next_run.isoformat(timespec="seconds"),
    "last_run_at": None,
    "last_status": None,
    "last_error": None,
    "last_delivery_error": None,
    "deliver": "telegram:2141911152",
    "origin": None,
    "enabled_toolsets": None,
    "workdir": os.path.join(HERMES_HOME, "vault"),
    "profile": None,
}

data = {"jobs": [], "updated_at": now.isoformat(timespec="microseconds")}
if os.path.exists(JOBS_FILE):
    try:
        with open(JOBS_FILE) as f:
            data = json.load(f)
    except Exception as e:
        print(f"[seed-iran-cron] Warning: could not parse {JOBS_FILE}: {e}")

if any(j.get("name") == JOB_NAME for j in data.get("jobs", [])):
    print(f"[seed-iran-cron] {JOB_NAME} already registered — skipping")
    sys.exit(0)

data.setdefault("jobs", []).append(NEW_JOB)
data["updated_at"] = now.isoformat(timespec="microseconds")

os.makedirs(os.path.dirname(JOBS_FILE), exist_ok=True)
with open(JOBS_FILE, "w") as f:
    json.dump(data, f, indent=2)

print(f"[seed-iran-cron] Registered {JOB_NAME} — schedule: 50 7 * * * UTC (15:50 Bali), agent + skill, deliver: Telegram")

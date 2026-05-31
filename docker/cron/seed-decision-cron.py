#!/usr/bin/env python3
"""
Seed or merge the decision-engine-tier1 cron job into HERMES_HOME/cron/jobs.json.
Idempotent — safe to run on every boot. Only writes if the job is not already present.
Called from stage2-hook.sh.
"""
import json, os, random, string, sys
from datetime import datetime, timezone, timedelta

HERMES_HOME = os.environ.get("HERMES_HOME", "/opt/data")
JOBS_FILE = os.path.join(HERMES_HOME, "cron", "jobs.json")
JOB_NAME = "decision-engine-tier1"

now = datetime.now(timezone.utc)
next_run = now.replace(hour=8, minute=10, second=0, microsecond=0)
if next_run <= now:
    next_run += timedelta(days=1)

NEW_JOB = {
    "id": "".join(random.choices(string.ascii_lowercase + string.digits, k=12)),
    "name": JOB_NAME,
    "prompt": "",
    "skills": [],
    "skill": None,
    "model": None,
    "provider": "openrouter",
    "base_url": None,
    "script": "decision-engine-tier1.sh",
    "no_agent": True,
    "context_from": None,
    "schedule": {"kind": "cron", "expr": "10 8 * * *", "display": "10 8 * * *"},
    "schedule_display": "10 8 * * *",
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
        print(f"[seed-decision-cron] Warning: could not parse {JOBS_FILE}: {e}")

if any(j.get("name") == JOB_NAME for j in data.get("jobs", [])):
    print(f"[seed-decision-cron] {JOB_NAME} already registered — skipping")
    sys.exit(0)

data.setdefault("jobs", []).append(NEW_JOB)
data["updated_at"] = now.isoformat(timespec="microseconds")

os.makedirs(os.path.dirname(JOBS_FILE), exist_ok=True)
with open(JOBS_FILE, "w") as f:
    json.dump(data, f, indent=2)

print(f"[seed-decision-cron] Registered {JOB_NAME} — schedule: 10 8 * * * UTC (16:10 Bali), deliver: Telegram")

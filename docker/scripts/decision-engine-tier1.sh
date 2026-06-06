#!/bin/sh
# Decision Engine Tier-1 daily runner — Railway Hermes VPS
# Registered as: hermes cron create "10 8 * * *" --no-agent --script decision-engine-tier1.sh
# Runs at 08:10 UTC (= 16:10 Bali time) daily.
# Output is delivered verbatim to Telegram (--deliver telegram).
set -eu

HERMES_HOME="${HERMES_HOME:-/opt/data}"
VAULT="$HERMES_HOME/vault"
SKILL_DIR="$HERMES_HOME/skills/decision-engine-tier1"
OUTPUT_DIR="$VAULT/03_BRAIN/finance/decision_engine/data_inputs"

echo "[tier1] $(date -u +%Y-%m-%dT%H:%M:%SZ) — starting"

# 1. Pull vault so we have the latest trigger state
if [ -d "$VAULT/.git" ]; then
    git -C "$VAULT" pull --rebase --quiet 2>&1 || echo "[tier1] warn: vault pull failed, continuing with cached state"
else
    echo "[tier1] warn: vault not found at $VAULT — output will not be git-synced"
fi

mkdir -p "$OUTPUT_DIR"

# 2. Run the collector (stdlib only, no pip required)
RESULT=$(python3 "$SKILL_DIR/tier1_collect.py" --output-dir "$OUTPUT_DIR" 2>&1)
echo "$RESULT"

# 3. Commit + push whatever changed in the output dir
if [ -d "$VAULT/.git" ]; then
    CHANGED=$(git -C "$VAULT" status --porcelain "$OUTPUT_DIR" 2>/dev/null)
    if [ -n "$CHANGED" ]; then
        TRIGGER=$(echo "$RESULT" | grep "^trigger_fired=" | cut -d= -f2 || echo "False")
        if [ "$TRIGGER" = "True" ]; then
            COMMIT_MSG="hermes: decision-engine tier1 TRIGGER $(date -u +%Y-%m-%d)"
        else
            COMMIT_MSG="hermes: decision-engine tier1 $(date -u +%Y-%m-%d)"
        fi
        git -C "$VAULT" add "$OUTPUT_DIR" 2>/dev/null && \
        git -C "$VAULT" commit -m "$COMMIT_MSG" 2>/dev/null && \
        git -C "$VAULT" push 2>/dev/null || echo "[tier1] warn: vault push failed"
        echo "[tier1] vault synced: $COMMIT_MSG"
    else
        echo "[tier1] no vault changes to commit"
    fi
fi

echo "[tier1] $(date -u +%Y-%m-%dT%H:%M:%SZ) — done"

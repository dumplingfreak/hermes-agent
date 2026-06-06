---
name: decision-engine-tier1
description: "Run the Decision Engine Tier-1 daily data collector: pull vault, fetch spot prices + headlines, check triggers, commit results to vault."
---

# Skill: Decision Engine Tier-1 Collector

Run the daily data collector for Gia's Investment Decision Engine. Deterministic — no interpretation or LLM cost.

## What it does
1. `git pull --rebase` vault
2. Fetches spot prices (BTC, ETH, Gold, Silver, Oil, DXY, 10y yield, S&P 500, Nasdaq, USD/IDR, EUR/USD, VIX) from Yahoo Finance
3. Fetches RSS headlines (CoinDesk, Fed press, MarketWatch)
4. Runs edge-triggered checks: move-% thresholds per asset class + BTC watch levels ($73k, $78k)
5. Writes `~/vault/03_BRAIN/finance/decision_engine/data_inputs/YYYY-MM-DD.md`
6. `git add + commit + push` the result

## Run manually

```bash
HERMES_HOME="${HERMES_HOME:-/opt/data}"
python3 "$HERMES_HOME/skills/decision-engine-tier1/tier1_collect.py" \
    --output-dir "$HERMES_HOME/vault/03_BRAIN/finance/decision_engine/data_inputs"
```

Then check output for `trigger_fired=True`.

## Output files
- `YYYY-MM-DD.md` — daily data file: prices table, headlines, trigger verdict
- `_feed_log.md` — running log of every run (date | trigger | note)
- `_trigger_state.json` — edge-trigger state (do not edit manually)

## After collection
If `trigger_fired: true` → draft a decision card in `~/vault/03_BRAIN/finance/decision_engine/decision_cards/`. Pull analyst views from `~/vault/03_BRAIN/finance/` (master_index → relevant people/concept files) before interpreting. Do not invent prices — the data file is the only ground truth.

## Cron schedule (Railway)
Runs daily at 08:10 UTC (= 16:10 Bali / WITA). Registered automatically by stage2-hook.sh on first boot. Uses `--no-agent` mode — no LLM cost.

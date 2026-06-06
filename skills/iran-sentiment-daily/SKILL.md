---
name: iran-sentiment-daily
description: Daily cross-spectrum Iran/war sentiment sweep. Collects native-language headlines (Persian/Arabic/Hebrew/English) + a Twitter/X scan, interprets where the camps diverge, and grades how realistic each outcome (stalemate / war / face-saving fudge) looks — feeding the decision-engine actor model.
---

# Iran / War Cross-Spectrum Sentiment — daily

Goal: keep an honest, falsifiable read on **how realistic the next US–Iran outcome is** by reading what every camp tells its *own* audience — Iranian hardline/regime, the Arab pro-Resistance axis, the Gulf, Israel, and the US left/right — not just the Western wire. You read Arabic and Persian natively; use that.

This is the geopolitical sibling of the `decision-engine-tier1` finance cron. Keep the same discipline: collection is deterministic, interpretation is yours, and every read ties to an observable indicator.

## Step 1 — Collect (deterministic)
Run the collector, which writes today's raw multi-source bundle:

```
python3 "${HERMES_HOME:-$HOME}/skills/iran-sentiment-daily/collect_sources.py"
```

It prints `bundle_written=<path>`. Read that file. It groups headlines by camp (Persian hardline / regime / state / reformist; Arabic pro-Iran / Qatar / Gulf; Israeli; Western wire / right / left) plus a Twitter/X scan via Apify.

- If a source shows "_(feed unavailable)_" **and it matters today** (especially **Tasnim**, **Kayhan**, **Israel Hayom**, which the collector sometimes misses), fill the gap yourself with a quick web fetch in that outlet's own language.
- If the Twitter line says the Apify token isn't set, just proceed without it — note it once at the end.

## Step 2 — Interpret (this is the value)
Read the source bundle against the actor model `03_BRAIN/finance/decision_engine/actor_models/iran_us_nuclear.md` (pull the vault first). Produce a read that a smart, busy reader could not get from any single outlet:

1. **What each camp is signaling** — separate the Persian hardline (Tasnim/Fars/Kayhan) from the regime-security channel (Nour News / SNSC) and the reformist press; the pro-Iran Arab axis (Al Mayadeen/Al-Akhbar) from the Gulf (Al Arabiya/Asharq); Israeli domestic (Ynet/Israel Hayom, Hebrew) from its English hawk press; US right from US left.
2. **Where the framings diverge** — the gap between what Tehran tells Iranians and what Israel/Gulf/West say is itself the signal. Flag genuine divergence vs everyone converging.
3. **Escalation vs face-saving noise** — distinguish real movement (something signed, a strike, Hormuz traffic, enrichment-level statements, IRGC posture) from rhetoric and domestic posturing.
4. **Grade the outcome probabilities.** The model's current split is **Stalemate ~50% / War resumes ~30% / Ambiguous fudge ~20%**, plus Gia's gut call ("no clean deal, war resumes"). State whether today's cross-spectrum signal **supports, weakens, or leaves unchanged** each, and your updated rough split. Be conservative — do not thrash the numbers on one day of noise; move them only on a real shift and say which observable drove it.

## Step 3 — Write to the vault
- **Always** append a dated entry to `03_BRAIN/finance/decision_engine/actor_models/iran_us_nuclear_sentiment_log.md` (create it if missing, newest entries at top): date, one-line state classification (De-escalation / Stalemate / Breakdown-risk), the per-camp read (2–4 lines), the graded probability line, and the single indicator to watch next.
- **Only when the signal materially shifts the picture**, also edit the probability table / Gia's-gut-call calibration in `iran_us_nuclear.md`, and add a one-line dated changelog note there explaining why.
- Commit and push the vault with a message like `hermes: iran-sentiment <date>` (pull --rebase first; the finance cron writes here too).

## Step 4 — Deliver the briefing
Output a tight Telegram briefing (this message is delivered verbatim), under ~250 words:

1. **Headline** — one line: current state classification + which outcome is gaining/losing.
2. **The divergence** — 2–3 lines: where Tehran's domestic story differs from Israel/Gulf/West, and what that tells us.
3. **Probability read** — the updated Stalemate / War / Fudge split and what moved it (or "unchanged — why").
4. **Watch next** — the single most informative indicator for tomorrow (a signing, a strike, Hormuz traffic, an enrichment statement, an IRGC/SNSC line).

End with the date/time (UTC). This is analysis, not advice. Keep it skimmable; lead with the answer.

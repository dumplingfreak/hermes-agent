#!/usr/bin/env python3
"""
Tier-1 daily feed collector for the Decision Engine.

Deterministic data collection only — no interpretation. Pulls spot prices
(Yahoo Finance chart API) and headlines (RSS), computes 1d moves, runs the
trigger check, and writes data_inputs/YYYY-MM-DD.md.

Stdlib only (urllib, json, xml.etree) — no pip installs. Runs under any python3.

Usage:
    python3 tier1_collect.py                          # write today's file (default output dir)
    python3 tier1_collect.py --dry-run                # print to stdout, write nothing
    python3 tier1_collect.py --output-dir /some/path  # write to explicit dir (Railway)

Interpretation (decision cards) is a SEPARATE step that only runs when
trigger_fired is true. This script never drafts a card.
"""

import argparse, json, os, re, sys, urllib.request, urllib.error, urllib.parse
import xml.etree.ElementTree as ET
from datetime import datetime, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
UA = {"User-Agent": "Mozilla/5.0 (decision-engine tier1 collector)"}
CANONICAL_REL = os.path.join("vault", "03_BRAIN", "finance", "decision_engine", "data_inputs")
RETIRED_MARKERS = (
    os.path.join("vault", "wiki", "finance", "decision_engine"),
    os.path.join("vault", "raw", "finance", "decision_engine"),
)


def canonical_output_dir():
    home = os.environ.get("HERMES_HOME") or os.environ.get("HOME") or os.path.expanduser("~")
    return os.path.normpath(os.path.join(home, CANONICAL_REL))


def resolve_output_dir(requested):
    """Force v2 decision-engine writes into 03_BRAIN even if an old cron arg remains."""
    canonical = canonical_output_dir()
    if not requested:
        return canonical

    out_dir = os.path.normpath(requested)
    for marker in RETIRED_MARKERS:
        if marker in out_dir:
            print(f"[warn] retired output path requested: {out_dir}", file=sys.stderr)
            print(f"[warn] redirecting to canonical v2 path: {canonical}", file=sys.stderr)
            return canonical

    return out_dir


def load_config():
    with open(os.path.join(HERE, "tier1_config.json")) as f:
        return json.load(f)


def http_get(url, timeout=15):
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()


def fetch_price(symbol):
    """Return (last, prev_close) from Yahoo chart API, or (None, None)."""
    url = f"https://query1.finance.yahoo.com/v8/finance/chart/{urllib.parse.quote(symbol)}?range=2d&interval=1d"
    try:
        data = json.loads(http_get(url))
        meta = data["chart"]["result"][0]["meta"]
        last = meta.get("regularMarketPrice")
        prev = meta.get("chartPreviousClose") or meta.get("previousClose")
        return last, prev
    except Exception as e:
        sys.stderr.write(f"[warn] price fetch failed {symbol}: {e}\n")
        return None, None


def fetch_news(feeds, limit):
    items = []
    per_feed = max(2, limit // max(1, len(feeds)))
    for feed in feeds:
        try:
            raw = http_get(feed["url"])
            root = ET.fromstring(raw)
            count = 0
            for it in root.iter("item"):
                title = (it.findtext("title") or "").strip()
                link = (it.findtext("link") or "").strip()
                pub = (it.findtext("pubDate") or "").strip()
                if title:
                    items.append({"src": feed["name"], "title": title, "link": link, "pub": pub})
                    count += 1
                    if count >= per_feed:
                        break
        except Exception as e:
            sys.stderr.write(f"[warn] news fetch failed {feed['name']}: {e}\n")
    return items[:limit]


def safe_float(value, default=None):
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def decode_json_list(value):
    if isinstance(value, list):
        return value
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
            return parsed if isinstance(parsed, list) else []
        except Exception:
            return []
    return []


def market_text(market):
    parts = [
        market.get("question", ""),
        market.get("slug", ""),
        market.get("description", ""),
        market.get("groupItemTitle", ""),
    ]
    return " ".join(str(p).lower() for p in parts if p)


def passes_market_filter(text, watch):
    any_terms = [str(t).lower() for t in watch.get("any", [])]
    all_terms = [str(t).lower() for t in watch.get("all", [])]
    exclude_terms = [str(t).lower() for t in watch.get("exclude", [])]

    def has_term(term):
        if " " in term:
            return term in text
        return re.search(r"\b" + re.escape(term) + r"\b", text) is not None

    if any_terms and not any(has_term(t) for t in any_terms):
        return False
    if all_terms and not all(has_term(t) for t in all_terms):
        return False
    if exclude_terms and any(has_term(t) for t in exclude_terms):
        return False
    return True


def fetch_polymarket(cfg, state):
    """Fetch a small fixed watchlist from the free Polymarket Gamma API."""
    pm_cfg = cfg.get("polymarket", {})
    if not pm_cfg.get("enabled"):
        return []

    base = pm_cfg.get("api_base", "https://gamma-api.polymarket.com").rstrip("/")
    limit = int(pm_cfg.get("page_limit", 100))
    pages = int(pm_cfg.get("pages", 12))
    min_liq = float(pm_cfg.get("min_liquidity", 2000))
    watchlist = pm_cfg.get("watchlist", [])
    previous = state.get("polymarket", {}) if isinstance(state, dict) else {}
    rows = []
    seen = set()

    for page in range(pages):
        url = f"{base}/markets?active=true&closed=false&limit={limit}&offset={page * limit}"
        try:
            markets = json.loads(http_get(url, timeout=15))
        except Exception as e:
            sys.stderr.write(f"[warn] polymarket page fetch failed offset={page * limit}: {e}\n")
            break
        if not isinstance(markets, list) or not markets:
            break

        for market in markets:
            slug = market.get("slug") or market.get("conditionId") or market.get("questionID")
            if not slug or slug in seen:
                continue
            text = market_text(market)
            matched_watch = None
            for watch in watchlist:
                if passes_market_filter(text, watch):
                    matched_watch = watch
                    break
            if not matched_watch:
                continue

            liq = safe_float(market.get("liquidityNum"), safe_float(market.get("liquidity"), 0)) or 0
            if liq < min_liq:
                continue

            prices = [safe_float(p) for p in decode_json_list(market.get("outcomePrices"))]
            outcomes = decode_json_list(market.get("outcomes"))
            if not prices:
                continue

            yes_price = None
            for outcome, price in zip(outcomes, prices):
                if str(outcome).lower() == "yes":
                    yes_price = price
                    break
            if yes_price is None:
                yes_price = prices[0]
            if yes_price is None:
                continue

            yes_pct = round(yes_price * 100, 1)
            no_pct = round((1 - yes_price) * 100, 1)
            change_raw = safe_float(market.get("oneDayPriceChange"))
            if change_raw is not None:
                change_24h_pp = round(change_raw * 100, 1)
            else:
                prev = previous.get(slug, {}).get("yes_pct") if isinstance(previous.get(slug), dict) else None
                change_24h_pp = round(yes_pct - prev, 1) if isinstance(prev, (int, float)) else None

            seen.add(slug)
            rows.append({
                "label": matched_watch.get("label", "Watchlist"),
                "question": market.get("question") or slug,
                "slug": slug,
                "yes_pct": yes_pct,
                "no_pct": no_pct,
                "change_24h_pp": change_24h_pp,
                "liquidity": int(liq),
            })

    rows.sort(key=lambda r: (r["label"], -r["liquidity"]))
    max_per_topic = int(pm_cfg.get("max_results_per_topic", 3))
    kept = []
    topic_counts = {}
    for row in rows:
        label = row["label"]
        count = topic_counts.get(label, 0)
        if count >= max_per_topic:
            continue
        kept.append(row)
        topic_counts[label] = count + 1
    max_rows = int(pm_cfg.get("max_rows", 12))
    return kept[:max_rows]


def geopolitical_sentiment(news, cfg):
    geo_cfg = cfg.get("geopolitical_sentiment", {})
    keywords = [str(k).lower() for k in geo_cfg.get("keywords", [])]
    if not keywords:
        return {"intensity": 0, "matching_count": 0, "matches": []}

    matches = []
    intensity = 0
    for item in news:
        title = item.get("title", "")
        text = title.lower()
        hits = [k for k in keywords if re.search(r"\b" + re.escape(k) + r"\b", text)]
        if hits:
            intensity += len(hits)
            matches.append({**item, "hits": hits})

    max_headlines = int(geo_cfg.get("max_headlines", 3))
    return {
        "intensity": intensity,
        "matching_count": len(matches),
        "matches": matches[:max_headlines],
    }


def pct(last, prev):
    if last is None or prev in (None, 0):
        return None
    return (last - prev) / prev * 100.0


def fmt_num(x):
    if x is None:
        return "—"
    if abs(x) >= 1000:
        return f"{x:,.0f}"
    if abs(x) >= 1:
        return f"{x:,.2f}"
    return f"{x:.4f}"


def load_state(out_dir):
    try:
        with open(os.path.join(out_dir, "_trigger_state.json")) as f:
            return json.load(f)
    except Exception:
        return {}


def save_state(out_dir, state):
    try:
        with open(os.path.join(out_dir, "_trigger_state.json"), "w") as f:
            json.dump(state, f, indent=2)
    except Exception as e:
        sys.stderr.write(f"[warn] state save failed: {e}\n")


def evaluate_triggers(rows, pm_rows, cfg, state, date_str):
    """Edge-triggered. Returns (fired, new_state).

    - Stale (closed-market) rows have pct=None, so they cannot fire a move trigger.
    - Watch levels fire ONLY on a CROSS, or near AND moved >= watch_level_min_move_pct
      since the last card. Mere lingering near a level does not re-fire (no daily spam).
    """
    fired = []
    thr = cfg["move_triggers_pct"]
    for r in rows:
        p = r["pct"]
        t = thr.get(r["class"])
        if p is not None and t is not None and abs(p) >= t:
            fired.append(f"{r['label']} moved {p:+.1f}% (>= {t}% {r['class']} threshold)")

    pm_trigger = float(cfg.get("polymarket", {}).get("trigger_move_pp", 10.0))
    for r in pm_rows:
        change = r.get("change_24h_pp")
        if change is not None and abs(change) >= pm_trigger:
            fired.append(
                f"Polymarket {r['label']} moved {change:+.1f} pts in 24h: "
                f"{r['question']} ({r['yes_pct']}% yes)"
            )

    by_symbol = {r["symbol"]: r for r in rows}
    prev_prices = state.get("last_prices", {})
    last_card = dict(state.get("last_card", {}))
    min_move = cfg.get("watch_level_min_move_pct", 3.0)

    for wl in cfg.get("watch_levels", []):
        r = by_symbol.get(wl["symbol"])
        if not r or r["last"] is None or not r.get("live", True):
            continue
        cur, L, prox = r["last"], wl["level"], wl.get("proximity_pct", 1.5)
        near = abs((cur - L) / L * 100.0) <= prox
        prev = prev_prices.get(wl["symbol"])
        crossed = prev is not None and ((prev - L) * (cur - L) < 0)
        lc = last_card.get(wl["symbol"])
        moved = (lc is None) or (abs((cur - lc) / lc * 100.0) >= min_move)
        if crossed:
            fired.append(f"{wl['label']} {fmt_num(cur)} CROSSED {L:,} — {wl['note']}")
        elif near and moved:
            since = "" if lc is None else f" (moved {(cur - lc) / lc * 100.0:+.1f}% since last card)"
            fired.append(f"{wl['label']} {fmt_num(cur)} within {prox}% of {L:,}{since} — {wl['note']}")

    new_state = {
        "last_run": date_str,
        "last_prices": {r["symbol"]: r["last"] for r in rows if r["last"] is not None},
        "last_card": last_card,
        "polymarket": {
            r["slug"]: {"yes_pct": r["yes_pct"], "date": date_str}
            for r in pm_rows
            if r.get("slug") and r.get("yes_pct") is not None
        },
    }
    if fired:
        new_state["last_card_date"] = date_str
        for wl in cfg.get("watch_levels", []):
            r = by_symbol.get(wl["symbol"])
            if r and r["last"] is not None:
                new_state["last_card"][wl["symbol"]] = r["last"]
    return fired, new_state


def render_polymarket(pm_rows):
    lines = ["## Polymarket"]
    if not pm_rows:
        lines.append("- (no watchlist markets matched/fetched)")
        return lines
    lines.append("| Topic | Market | Yes | 24h | Liquidity |")
    lines.append("|---|---|---:|---:|---:|")
    for r in pm_rows:
        question = r["question"][:90] + "..." if len(r["question"]) > 90 else r["question"]
        change = "—" if r.get("change_24h_pp") is None else f"{r['change_24h_pp']:+.1f} pts"
        lines.append(
            f"| {r['label']} | {question} | {r['yes_pct']:.1f}% | {change} | ${r['liquidity']:,} |"
        )
    return lines


def render_geopolitical_sentiment(geo):
    lines = ["## Geopolitical Sentiment (no-agent)"]
    lines.append(f"- Intensity: {geo['intensity']} keyword hits across {geo['matching_count']} matching RSS headlines")
    if geo["matches"]:
        lines.append("- Matching headlines:")
        for item in geo["matches"]:
            hits = ", ".join(item.get("hits", []))
            lines.append(f"  - {item['title']} ({item['src']}; hits: {hits})")
    else:
        lines.append("- Matching headlines: none")
    return lines


def render(rows, news, pm_rows, geo, fired, date_str, now_iso):
    lines = []
    lines.append("---")
    lines.append(f"date: {date_str}")
    lines.append("status: raw")
    lines.append(f"trigger_fired: {'true' if fired else 'false'}")
    lines.append(f"collected_at: {now_iso}")
    lines.append("source: tier1_collect.py (Yahoo prices + RSS + Polymarket Gamma)")
    lines.append("---")
    lines.append("")
    weekend = datetime.strptime(date_str, "%Y-%m-%d").weekday() >= 5
    lines.append(f"# Daily Market Input — {date_str}")
    lines.append("")
    lines.append("> Auto-collected ground truth. Manual fields (macro releases, Fed odds, statements) still need filling until those sources are wired.")
    if weekend:
        lines.append("")
        lines.append("> ⚠️ **Weekend — TradFi markets closed.** Only **crypto prices and headlines/politics are live.** Closed-market rows below show Friday's close with `1d %` suppressed — do NOT build equity/gold/oil/FX narratives on them or call them 'today's move'.")
    lines.append("")
    lines.append("## Prices (auto)")
    lines.append("| Asset | Level | 1d % |")
    lines.append("|---|---|---|")
    for r in rows:
        if r.get("live", True):
            p = f"{r['pct']:+.2f}%" if r["pct"] is not None else "—"
            lvl = fmt_num(r["last"])
        else:
            p = "— *(closed)*"
            lvl = f"{fmt_num(r['last'])} *(Fri close)*"
        lines.append(f"| {r['label']} | {lvl} | {p} |")
    lines.append("")
    lines.append("## Tier-1 still-manual (fill if a release/statement landed today)")
    lines.append("- Macro release surprise vs expected: —")
    lines.append("- Fed-path odds (cut/hold/hike) + current chair: — (see actor_models/fed.md)")
    lines.append("- Official statements: —")
    lines.append("")
    lines.append("## Headlines (auto)")
    for n in news:
        lines.append(f"- {n['title']}  ([{n['src']}]({n['link']}))")
    if not news:
        lines.append("- (none fetched)")
    lines.append("")
    lines.extend(render_polymarket(pm_rows))
    lines.append("")
    lines.extend(render_geopolitical_sentiment(geo))
    lines.append("")
    lines.append("## Trigger check")
    if fired:
        lines.append("**TRIGGER FIRED → write a decision card.**")
        for f in fired:
            lines.append(f"- ⚠️ {f}")
    else:
        lines.append("No trigger. Logged; no card today.")
    lines.append("")
    return "\n".join(lines)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--output-dir", default=None,
                    help="Override output directory (absolute path). "
                         "Defaults to the data_inputs/ dir relative to the script.")
    args = ap.parse_args()

    cfg = load_config()
    now = datetime.now(timezone.utc)
    date_str = now.strftime("%Y-%m-%d")
    weekend = now.weekday() >= 5

    rows = []
    for a in cfg["watchlist"]:
        last, prev = fetch_price(a["symbol"])
        live = (a["class"] == "crypto") or not weekend
        rows.append({"label": a["label"], "symbol": a["symbol"], "class": a["class"],
                     "last": last, "prev": prev,
                     "pct": pct(last, prev) if live else None,
                     "live": live})

    out_dir = resolve_output_dir(args.output_dir)
    state = load_state(out_dir)
    news = fetch_news(cfg.get("news_feeds", []), cfg.get("news_limit", 8))
    pm_rows = fetch_polymarket(cfg, state)
    geo = geopolitical_sentiment(news, cfg)
    fired, new_state = evaluate_triggers(rows, pm_rows, cfg, state, date_str)

    md = render(rows, news, pm_rows, geo, fired, date_str, now.isoformat(timespec="seconds"))

    if args.dry_run:
        print(md)
        print(f"\n[dry-run] trigger_fired={bool(fired)} weekend={weekend}", file=sys.stderr)
        return

    os.makedirs(out_dir, exist_ok=True)
    out_path = os.path.join(out_dir, f"{date_str}.md")
    with open(out_path, "w") as f:
        f.write(md)
    save_state(out_dir, new_state)

    log = os.path.join(out_dir, "_feed_log.md")
    new = not os.path.exists(log)
    with open(log, "a") as f:
        if new:
            f.write("# Tier-1 Feed Log\n\n| date | trigger | note |\n|---|---|---|\n")
        note = "; ".join(fired) if fired else "no trigger"
        f.write(f"| {date_str} | {'YES' if fired else 'no'} | {note} |\n")

    print(f"wrote {out_path}")
    print(f"trigger_fired={bool(fired)}")
    if fired:
        print("ACTION: a trigger fired — draft a decision card from this file.")
        for x in fired:
            print(f"  - {x}")


if __name__ == "__main__":
    main()

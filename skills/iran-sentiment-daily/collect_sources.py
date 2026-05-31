#!/usr/bin/env python3
"""
Iran / war cross-spectrum sentiment collector for the Decision Engine.

DETERMINISTIC collection only — no interpretation. Pulls recent headlines
from native-language outlets across the spectrum (Persian / Arabic / Hebrew /
English) via Google News RSS site: search, plus a Twitter/X scan via Apify.
Writes a dated raw bundle the agent then reads and interprets.

Native-language design: each outlet is queried in its OWN language (hl=fa/ar/he)
so we capture the DOMESTIC framing — what each camp tells its own audience —
not the West-facing English editions.

Stdlib only (urllib, json, xml.etree) — no pip installs. Runs under any python3.
Every network call is best-effort: a failed source is logged and skipped, never
fatal, so the agent always gets a bundle to work with.

Usage:
    python3 collect_sources.py                      # write today's bundle to default vault path
    python3 collect_sources.py --output-dir DIR     # override output directory
    python3 collect_sources.py --dry-run            # print to stdout, write nothing
"""

import argparse, json, os, sys, urllib.request, urllib.error, urllib.parse
import xml.etree.ElementTree as ET
from datetime import datetime, timezone, timedelta

HERE = os.path.dirname(os.path.abspath(__file__))
UA = {"User-Agent": "Mozilla/5.0 (decision-engine iran-sentiment collector)"}
DEFAULT_OUTPUT = os.path.expanduser(
    os.environ.get("HERMES_HOME", "~") + "/vault/raw/finance/decision_engine/iran_sentiment"
)


def load_config():
    with open(os.path.join(HERE, "sources_config.json")) as f:
        return json.load(f)


def http_get(url, timeout=20):
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()


def http_post_json(url, payload, timeout=120):
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        url, data=data, headers={**UA, "Content-Type": "application/json"}, method="POST"
    )
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


def parse_pubdate(text):
    """Best-effort RFC-822 -> aware datetime. Returns None on failure."""
    if not text:
        return None
    for fmt in ("%a, %d %b %Y %H:%M:%S %Z", "%a, %d %b %Y %H:%M:%S %z"):
        try:
            dt = datetime.strptime(text.strip(), fmt)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt
        except ValueError:
            continue
    return None


def gnews_url(src, lookback_days):
    """Build a Google News RSS search URL scoped to one outlet, in its language.
    Site-only + when: window — topic filtering happens in Python (see fetch_source).
    Putting keywords in the query acts as AND and zeroes most non-English feeds."""
    q = f'site:{src["domain"]} when:{lookback_days}d'
    params = {
        "q": q,
        "hl": src.get("hl", "en"),
        "gl": src.get("gl", "US"),
        "ceid": src.get("ceid", "US:en"),
    }
    return "https://news.google.com/rss/search?" + urllib.parse.urlencode(params)


def _collect_items(raw, terms, cutoff, max_items):
    """Parse an RSS body, topic-filter by OR-match, return list of item dicts."""
    root = ET.fromstring(raw)
    out = []
    for item in root.iter("item"):
        title = (item.findtext("title") or "").strip()
        link = (item.findtext("link") or "").strip()
        pub = parse_pubdate(item.findtext("pubDate"))
        if not title:
            continue
        if pub and pub < cutoff:
            continue
        if terms and not any(t in title.lower() for t in terms):
            continue
        out.append({
            "title": title,
            "link": link,
            "pub": pub.strftime("%Y-%m-%d %H:%M") if pub else "?",
        })
        if len(out) >= max_items:
            break
    return out


def fetch_source(src, topic_terms, lookback_days, max_items):
    """Return a list of {title, link, pub} dicts for one outlet, or {error}.

    Strategy: if the source has a direct native RSS feed, try it first (best —
    true domestic-language feed). Fall back to Google News site: search. Titles
    are topic-filtered (OR-match) against the outlet's language terms PLUS the
    English terms, so English-edition headlines (e.g. Mehr) aren't dropped."""
    cutoff = datetime.now(timezone.utc) - timedelta(days=lookback_days)
    terms = [t.lower() for t in topic_terms.get(src.get("hl", "en"), [])]
    terms += [t.lower() for t in topic_terms.get("en", []) if src.get("hl") != "en"]

    # 1. Direct native RSS, if configured.
    if src.get("rss"):
        try:
            items = _collect_items(http_get(src["rss"]), terms, cutoff, max_items)
            if items:
                return items
        except Exception:
            pass  # fall through to Google News

    # 2. Google News site: search fallback.
    try:
        return _collect_items(http_get(gnews_url(src, lookback_days)), terms, cutoff, max_items)
    except Exception as e:
        return {"error": f"{type(e).__name__}: {e}"}


def fetch_twitter(cfg):
    """Scan X via Apify. Returns (items, note). Never raises."""
    tw = cfg.get("twitter", {})
    if not tw.get("enabled"):
        return [], "twitter disabled in config"
    token = os.environ.get("APIFY_API_TOKEN") or os.environ.get("APIFY_TOKEN")
    if not token:
        return [], "APIFY_API_TOKEN not set — Twitter scan skipped"
    actor = tw.get("apify_actor", "apidojo~tweet-scraper")
    handles = [h["handle"] for h in tw.get("handles", [])]
    lean = {h["handle"]: h.get("lean", "") for h in tw.get("handles", [])}
    if not handles:
        return [], "no handles configured"
    url = (
        f"https://api.apify.com/v2/acts/{actor}/run-sync-get-dataset-items"
        f"?token={urllib.parse.quote(token)}"
    )
    payload = {
        "twitterHandles": handles,
        "maxItems": tw.get("max_items", 60),
        "sort": "Latest",
        "tweetLanguage": None,
    }
    try:
        items = http_post_json(url, payload)
    except Exception as e:
        return [], f"Apify call failed ({type(e).__name__}: {e}) — Twitter scan skipped"
    # Normalize + cap per handle so one loud account can't dominate.
    per_handle, out = {}, []
    cap = tw.get("max_per_handle", 3)
    for it in items if isinstance(items, list) else []:
        author = (it.get("author") or {})
        uname = author.get("userName") or it.get("username") or it.get("user", {}).get("screen_name") or "?"
        text = it.get("text") or it.get("full_text") or ""
        if not text:
            continue
        if per_handle.get(uname, 0) >= cap:
            continue
        per_handle[uname] = per_handle.get(uname, 0) + 1
        out.append({
            "handle": uname,
            "lean": lean.get(uname, ""),
            "text": text.replace("\n", " ").strip()[:400],
            "likes": it.get("likeCount") or it.get("favorite_count") or 0,
        })
    return out, f"{len(out)} tweets across {len(per_handle)} accounts via {actor}"


def build_bundle(cfg):
    now = datetime.now(timezone.utc)
    topic_terms = cfg.get("topic_terms", {})
    lookback = cfg.get("lookback_days", 2)
    max_items = cfg.get("max_items_per_source", 6)

    # Group sources by bucket, preserving config order.
    buckets = {}
    feed_log = []
    for src in cfg.get("sources", []):
        res = fetch_source(src, topic_terms, lookback, max_items)
        b = src["bucket"]
        buckets.setdefault(b, [])
        if isinstance(res, dict) and "error" in res:
            feed_log.append(f"  - ⚠ {src['name']}: {res['error']}")
            buckets[b].append((src, []))
        else:
            feed_log.append(f"  - ✓ {src['name']}: {len(res)} items")
            buckets[b].append((src, res))

    tweets, tw_note = fetch_twitter(cfg)
    feed_log.append(f"  - twitter: {tw_note}")

    # --- Render markdown bundle ---
    L = []
    L.append(f"# Iran/War Cross-Spectrum Signal — {now.strftime('%Y-%m-%d')}")
    L.append("")
    L.append(f"_Collected {now.strftime('%Y-%m-%d %H:%M UTC')} — raw signal, no interpretation. "
             f"Native-language headlines per outlet (Persian/Arabic/Hebrew/English)._")
    L.append("")

    bucket_titles = {
        "persian_hardline":  "🇮🇷 Persian — IRGC / hardline (Tasnim, Fars, Kayhan)",
        "persian_regime":    "🇮🇷 Persian — regime/security signal (Nour News / SNSC)",
        "persian_state":     "🇮🇷 Persian — state baseline (IRNA, Mehr)",
        "persian_reformist": "🇮🇷 Persian — reformist lean (ISNA)",
        "arabic_proiran":    "☪️ Arabic — pro-Resistance / Iran axis (Al Mayadeen, Al-Akhbar)",
        "arabic_qatar":      "☪️ Arabic — Qatar (Al Jazeera Arabic)",
        "arabic_gulf":       "🛢️ Arabic — Gulf / anti-Iran (Al Arabiya, Asharq)",
        "israeli":           "🇮🇱 Israeli — Hebrew domestic (Ynet, Israel Hayom)",
        "israeli_en":        "🇮🇱 Israeli — English (Times of Israel)",
        "western_wire":      "📰 Western wire — facts (Reuters, BBC)",
        "western_right":     "📰 Western — US right (WSJ, Fox)",
        "western_left":      "📰 Western — US left / anti-war (Guardian, Intercept)",
    }
    for bucket in bucket_titles:
        if bucket not in buckets:
            continue
        L.append(f"## {bucket_titles.get(bucket, bucket)}")
        for src, items in buckets[bucket]:
            L.append(f"**{src['name']}**")
            if not items:
                L.append("  - _(no recent items / feed unavailable — agent: fill via web if material)_")
            for it in items:
                L.append(f"  - [{it['pub']}] {it['title']}")
            L.append("")
        L.append("")

    L.append("## 🐦 Twitter/X scan (Apify)")
    if tweets:
        for t in tweets:
            tag = f" ({t['lean']})" if t['lean'] else ""
            L.append(f"  - @{t['handle']}{tag}: {t['text']}")
    else:
        L.append(f"  - _{tw_note}_")
    L.append("")

    L.append("---")
    L.append("### collection log")
    L.extend(feed_log)
    L.append("")

    return "\n".join(L)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--output-dir", default=DEFAULT_OUTPUT)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    cfg = load_config()
    bundle = build_bundle(cfg)

    if args.dry_run:
        print(bundle)
        return

    os.makedirs(args.output_dir, exist_ok=True)
    fname = datetime.now(timezone.utc).strftime("%Y-%m-%d") + ".md"
    path = os.path.join(args.output_dir, fname)
    with open(path, "w") as f:
        f.write(bundle)
    print(f"bundle_written={path}")
    print(f"bytes={len(bundle)}")


if __name__ == "__main__":
    main()

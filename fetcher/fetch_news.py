#!/usr/bin/env python3
"""SignalWire news fetcher.

Pulls RSS/Atom feeds (and a few JSON APIs) per channel, sanitizes all text
(feed content is untrusted input), deduplicates, merges with previously
stored items and writes data/ticker/{channel}.json plus data/ticker/all.json.

Feed entries in feeds.json are either a bare URL string or an object:

  {
    "url": "...",
    "type": "rss" | "json",          # default rss
    "source": "Display name",        # default: feed title
    "kind": "news" | "advisory" | "release" | "weekly" | "paper" | "regulatory",
    "tags": ["standards"],           # fixed tags; tag_rules add more by title
    "include": "regex", "exclude": "regex",   # matched against title + summary
    "max_items": 10,                 # cap on items taken from this feed per run
    # json only:
    "path": "vulnerabilities",       # dotted path to the record array
    "title": "KEV: {cveID} ...",     # str.format templates; nested fields as {a[b]}
    "link": "https://.../{cveID}",
    "date": "dateAdded"              # field holding the record date
  }

Designed to run as a GitHub Actions cron job. No secrets required.
"""

from __future__ import annotations

import calendar
import hashlib
import html
import io
import json
import re
import sys
import time
from datetime import datetime, timezone
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urlparse

import feedparser
import requests

ROOT = Path(__file__).resolve().parent.parent
CONFIG = json.loads((ROOT / "feeds.json").read_text(encoding="utf-8"))
OUT_DIR = ROOT / "data" / "ticker"

USER_AGENT = "SignalWireAggregator/1.0 (+https://github.com/BabarZKhan/signalware; personal news aggregator)"

KINDS = {"news", "advisory", "release", "weekly", "paper", "regulatory"}
TAG_RULES = [
    (tag, re.compile(pattern, re.IGNORECASE))
    for tag, pattern in CONFIG.get("tag_rules", {}).items()
]


class _TagStripper(HTMLParser):
    """Strip all HTML tags, keep text content only."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []

    def handle_data(self, data: str) -> None:
        self.parts.append(data)


def sanitize_text(raw: str, max_len: int = 300) -> str:
    """Feed titles are untrusted: strip tags, unescape entities, collapse
    whitespace, cap length. Output is plain text only."""
    if not raw:
        return ""
    stripper = _TagStripper()
    try:
        stripper.feed(raw)
        text = "".join(stripper.parts)
    except Exception:
        text = re.sub(r"<[^>]*>", "", raw)
    text = html.unescape(text)
    text = re.sub(r"\s+", " ", text).strip()
    return text[:max_len]


def sanitize_url(raw: str) -> str | None:
    """Only http(s) URLs survive. Anything else (javascript:, data:, ...)
    is dropped so it can never become a clickable link."""
    if not raw:
        return None
    try:
        parsed = urlparse(raw.strip())
    except ValueError:
        return None
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        return None
    return parsed.geturl()[:2000]


def fetch_feed(url: str, timeout: int, max_bytes: int) -> bytes | None:
    """Fetch with timeout and a hard response-size cap."""
    try:
        with requests.get(
            url,
            timeout=timeout,
            stream=True,
            headers={"User-Agent": USER_AGENT},
        ) as resp:
            resp.raise_for_status()
            buf = io.BytesIO()
            for chunk in resp.iter_content(chunk_size=65536):
                buf.write(chunk)
                if buf.tell() > max_bytes:
                    print(f"  ! {url}: exceeded size cap, truncating", file=sys.stderr)
                    break
            return buf.getvalue()
    except requests.RequestException as exc:
        print(f"  ! {url}: {exc}", file=sys.stderr)
        return None


def entry_timestamp(entry) -> int:
    for key in ("published_parsed", "updated_parsed"):
        parsed = entry.get(key)
        if parsed:
            try:
                return int(calendar.timegm(parsed) * 1000)
            except (TypeError, ValueError, OverflowError):
                continue
    return int(time.time() * 1000)


def parse_date(raw: str) -> int:
    """Best-effort date parsing for JSON APIs: ISO 8601, YYYY-MM-DD or
    YYYYMMDD. Falls back to now so a record is never dropped for its date."""
    text = raw.strip() if isinstance(raw, str) else ""
    if text:
        try:
            iso = text[:-1] + "+00:00" if text.endswith("Z") else text
            iso = re.sub(r"(\.\d{6})\d+", r"\1", iso)  # trim sub-microsecond digits
            parsed = datetime.fromisoformat(iso)
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=timezone.utc)
            return int(parsed.timestamp() * 1000)
        except ValueError:
            pass
        for fmt in ("%Y%m%d", "%Y-%m-%d", "%m/%d/%Y"):
            try:
                parsed = datetime.strptime(text, fmt).replace(tzinfo=timezone.utc)
                return int(parsed.timestamp() * 1000)
            except ValueError:
                continue
    return int(time.time() * 1000)


def dedupe_key(title: str) -> str:
    normalized = re.sub(r"[^a-z0-9]+", "", title.lower())
    return hashlib.sha256(normalized.encode()).hexdigest()[:16]


# ── feed spec handling ─────────────────────────────────────────────────
def normalize_feed(spec) -> dict:
    if isinstance(spec, str):
        spec = {"url": spec}
    spec = dict(spec)
    spec.setdefault("type", "rss")
    spec.setdefault("tags", [])
    spec.setdefault("max_items", 40)
    if spec.get("kind") not in KINDS:
        spec["kind"] = "news"
    for key in ("include", "exclude"):
        pattern = spec.get(key)
        spec[key] = re.compile(pattern, re.IGNORECASE) if pattern else None
    return spec


def passes_filters(spec: dict, text: str) -> bool:
    if spec["include"] and not spec["include"].search(text):
        return False
    if spec["exclude"] and spec["exclude"].search(text):
        return False
    return True


def infer_tags(spec: dict, title: str) -> list[str]:
    tags = list(spec["tags"])
    for tag, pattern in TAG_RULES:
        if tag not in tags and pattern.search(title):
            tags.append(tag)
    return tags


def make_item(channel_id: str, spec: dict, source: str, title: str, url: str, ts: int) -> dict:
    now_ms = int(time.time() * 1000)
    item = {
        "id": dedupe_key(title),
        "topic": channel_id,
        "source": source,
        "title": title,
        "ts": min(ts, now_ms),  # clamp future-dated items
        "url": url,
        "kind": spec["kind"],
    }
    tags = infer_tags(spec, title)
    if tags:
        item["tags"] = tags
    return item


# ── RSS / Atom ─────────────────────────────────────────────────────────
def entry_summary(entry) -> str:
    text = entry.get("summary", "") or ""
    if not text:
        for block in entry.get("content", []) or []:
            text = block.get("value", "") or ""
            if text:
                break
    return sanitize_text(text, 2000)


def parse_entries(channel_id: str, raw: bytes, spec: dict) -> list[dict]:
    parsed = feedparser.parse(raw)
    source = spec.get("source") or sanitize_text(parsed.feed.get("title", ""), 80) or "unknown"
    items = []
    for entry in parsed.entries[:80]:
        title = sanitize_text(entry.get("title", ""))
        url = sanitize_url(entry.get("link", ""))
        if not title or not url:
            continue
        if not passes_filters(spec, title + " " + entry_summary(entry)):
            continue
        items.append(make_item(channel_id, spec, source, title, url, entry_timestamp(entry)))
        if len(items) >= spec["max_items"]:
            break
    return items


# ── JSON APIs (CISA KEV, openFDA, ...) ─────────────────────────────────
class _Fields(dict):
    """Record wrapper for str.format_map. Nested objects are reached with
    Python's index syntax, {a[b][c]}; missing keys and nulls render as empty
    strings and lists join with commas, so a template never raises."""

    def __getitem__(self, key: str):
        return self._render(dict.get(self, key))

    def __format__(self, spec: str) -> str:
        return ""  # a nested object used as a whole renders as nothing

    def __str__(self) -> str:
        return ""

    @staticmethod
    def _render(value):
        if value is None or isinstance(value, dict):
            return _Fields(value or {})
        if isinstance(value, list):
            return ", ".join(str(v) for v in value if not isinstance(v, (dict, list)))
        return str(value)


def parse_json_records(channel_id: str, raw: bytes, spec: dict) -> list[dict]:
    try:
        data = json.loads(raw)
    except ValueError as exc:
        print(f"  ! {spec['url']}: invalid JSON ({exc})", file=sys.stderr)
        return []
    records = data
    for part in (spec.get("path") or "").split("."):
        if part:
            records = records.get(part, []) if isinstance(records, dict) else []
    if not isinstance(records, list):
        return []
    source = spec.get("source") or "unknown"
    items = []
    for record in records:
        if not isinstance(record, dict):
            continue
        fields = _Fields(record)
        try:
            title = sanitize_text(spec["title"].format_map(fields), 220)
            url = sanitize_url(spec["link"].format_map(fields))
        except (KeyError, IndexError, TypeError, AttributeError, ValueError) as exc:
            print(f"  ! {spec['url']}: bad title/link template ({exc})", file=sys.stderr)
            break
        if not title or not url:
            continue
        if not passes_filters(spec, title):
            continue
        ts = parse_date(fields[spec.get("date", "")])
        items.append(make_item(channel_id, spec, source, title, url, ts))
    items.sort(key=lambda item: item["ts"], reverse=True)
    return items[: spec["max_items"]]


# ── storage ────────────────────────────────────────────────────────────
def load_existing(channel_id: str) -> list[dict]:
    path = OUT_DIR / f"{channel_id}.json"
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data.get("items", [])
    except (json.JSONDecodeError, OSError):
        return []


def merge(existing: list[dict], fresh: list[dict], cap: int, per_source_cap: int) -> list[dict]:
    by_id: dict[str, dict] = {item["id"]: item for item in existing}
    for item in fresh:
        by_id[item["id"]] = item  # fresh wins (source may have fixed a title)
    ranked = sorted(by_id.values(), key=lambda item: item["ts"], reverse=True)
    # Newest first, but no single source may crowd out the rest: a firehose
    # trade feed gets at most per_source_cap slots before low-volume primary
    # sources (standards bodies, regulators, journals) are considered.
    kept: list[dict] = []
    overflow: list[dict] = []
    per_source: dict[str, int] = {}
    for item in ranked:
        source = item.get("source", "")
        if per_source.get(source, 0) < per_source_cap:
            per_source[source] = per_source.get(source, 0) + 1
            kept.append(item)
        else:
            overflow.append(item)
    kept.extend(overflow[: max(0, cap - len(kept))])
    kept = kept[:cap]
    kept.sort(key=lambda item: item["ts"], reverse=True)
    return kept


def main() -> int:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    timeout = CONFIG["fetch_timeout_seconds"]
    max_bytes = CONFIG["max_response_bytes"]
    cap = CONFIG["max_items_per_channel"]
    per_source_cap = CONFIG.get("max_items_per_source", 6)
    all_cap = CONFIG.get("max_items_all", 500)
    now_ms = int(time.time() * 1000)
    everything: list[dict] = []
    # per-feed item counts (None = fetch failed); written next to the data so
    # a dead or over-filtered feed is visible without digging through CI logs
    report: dict[str, dict[str, int | None]] = {}

    for channel in CONFIG["channels"]:
        cid = channel["id"]
        print(f"[{cid}]")
        fresh: list[dict] = []
        report[cid] = {}
        for raw_spec in channel["feeds"]:
            spec = normalize_feed(raw_spec)
            raw = fetch_feed(spec["url"], timeout, max_bytes)
            if not raw:
                report[cid][spec["url"]] = None
                continue
            if spec["type"] == "json":
                entries = parse_json_records(cid, raw, spec)
            else:
                entries = parse_entries(cid, raw, spec)
            print(f"  {spec['url']} -> {len(entries)} items")
            report[cid][spec["url"]] = len(entries)
            fresh.extend(entries)
        merged = merge(load_existing(cid), fresh, cap, per_source_cap)
        payload = {"updated": now_ms, "topic": cid, "items": merged}
        (OUT_DIR / f"{cid}.json").write_text(
            json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8"
        )
        everything.extend(merged)

    everything.sort(key=lambda item: item["ts"], reverse=True)
    all_payload = {"updated": now_ms, "items": everything[:all_cap]}
    (OUT_DIR / "all.json").write_text(
        json.dumps(all_payload, ensure_ascii=False, indent=1), encoding="utf-8"
    )
    (OUT_DIR.parent / "fetch_report.json").write_text(
        json.dumps({"updated": now_ms, "channels": report}, ensure_ascii=False, indent=1),
        encoding="utf-8",
    )
    print(f"wrote {len(everything[:all_cap])} items to all.json")
    return 0


if __name__ == "__main__":
    sys.exit(main())

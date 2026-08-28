#!/usr/bin/env python3
"""SignalWire survey fetcher.

Queries the arXiv API per channel for survey/review papers, optionally
enriches with Semantic Scholar citation counts (graceful failure), and
writes data/surveys/{channel}.json plus data/surveys/all.json.

Runs daily via GitHub Actions. No API keys required.
"""

from __future__ import annotations

import json
import re
import sys
import time
from pathlib import Path
from urllib.parse import quote

import feedparser
import requests

# reuse sanitizers from the news fetcher
sys.path.insert(0, str(Path(__file__).resolve().parent))
from fetch_news import entry_timestamp, sanitize_text, sanitize_url  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
CONFIG = json.loads((ROOT / "feeds.json").read_text(encoding="utf-8"))
OUT_DIR = ROOT / "data" / "surveys"

ARXIV_API = "https://export.arxiv.org/api/query"
S2_BATCH_API = "https://api.semanticscholar.org/graph/v1/paper/batch"
USER_AGENT = "SignalWireAggregator/1.0 (personal research aggregator)"

ARXIV_ID_RE = re.compile(r"(\d{4}\.\d{4,5})(v\d+)?")


def arxiv_id_from_link(link: str) -> str | None:
    match = ARXIV_ID_RE.search(link or "")
    return match.group(1) if match else None


def fetch_arxiv(query: str, max_results: int) -> list[dict]:
    url = (
        f"{ARXIV_API}?search_query={quote(query)}"
        f"&sortBy=submittedDate&sortOrder=descending&max_results={max_results}"
    )
    try:
        resp = requests.get(url, timeout=30, headers={"User-Agent": USER_AGENT})
        resp.raise_for_status()
    except requests.RequestException as exc:
        print(f"  ! arXiv query failed: {exc}", file=sys.stderr)
        return []
    parsed = feedparser.parse(resp.content)
    papers = []
    for entry in parsed.entries:
        title = sanitize_text(entry.get("title", ""), 400)
        link = sanitize_url(entry.get("link", ""))
        if not title or not link:
            continue
        authors = [
            sanitize_text(a.get("name", ""), 80)
            for a in entry.get("authors", [])[:6]
            if a.get("name")
        ]
        papers.append(
            {
                "id": arxiv_id_from_link(link) or link,
                "title": title,
                "url": link,
                "authors": authors,
                "abstract": sanitize_text(entry.get("summary", ""), 600),
                "ts": entry_timestamp(entry),
                "citations": None,
            }
        )
    return papers


def enrich_citations(papers: list[dict]) -> None:
    """Best-effort Semantic Scholar lookup; the site works fine without it."""
    ids = [f"ARXIV:{p['id']}" for p in papers if ARXIV_ID_RE.fullmatch(str(p["id"]))]
    if not ids:
        return
    try:
        resp = requests.post(
            S2_BATCH_API,
            params={"fields": "citationCount,externalIds"},
            json={"ids": ids},
            timeout=30,
            headers={"User-Agent": USER_AGENT},
        )
        resp.raise_for_status()
        results = resp.json()
    except (requests.RequestException, ValueError) as exc:
        print(f"  ! Semantic Scholar enrichment skipped: {exc}", file=sys.stderr)
        return
    counts: dict[str, int] = {}
    for record in results or []:
        if not record:
            continue
        ext = record.get("externalIds") or {}
        arxiv_id = ext.get("ArXiv")
        if arxiv_id is not None:
            counts[arxiv_id] = record.get("citationCount") or 0
    for paper in papers:
        if paper["id"] in counts:
            paper["citations"] = counts[paper["id"]]


def rank(papers: list[dict], cap: int) -> list[dict]:
    """Newest first, but citation velocity pulls papers up: a survey with
    real citations outranks a same-month one with none."""

    def score(p: dict) -> float:
        age_days = max(1.0, (time.time() * 1000 - p["ts"]) / 86400000)
        cites = p["citations"] or 0
        return cites / age_days * 30 - age_days / 90

    return sorted(papers, key=score, reverse=True)[:cap]


def main() -> int:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    cap = CONFIG["max_surveys_per_channel"]
    now_ms = int(time.time() * 1000)
    everything: list[dict] = []

    for channel in CONFIG["channels"]:
        cid = channel["id"]
        query = channel.get("arxiv_query")
        if not query:
            continue
        print(f"[{cid}]")
        papers = fetch_arxiv(query, max_results=25)
        print(f"  arXiv -> {len(papers)} papers")
        enrich_citations(papers)
        ranked = rank(papers, cap)
        for paper in ranked:
            paper["topic"] = cid
        payload = {"updated": now_ms, "topic": cid, "papers": ranked}
        (OUT_DIR / f"{cid}.json").write_text(
            json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8"
        )
        everything.extend(ranked)
        time.sleep(3)  # be polite to the arXiv API

    everything.sort(key=lambda p: p["ts"], reverse=True)
    (OUT_DIR / "all.json").write_text(
        json.dumps({"updated": now_ms, "papers": everything}, ensure_ascii=False, indent=1),
        encoding="utf-8",
    )
    print(f"wrote {len(everything)} papers to all.json")
    return 0


if __name__ == "__main__":
    sys.exit(main())

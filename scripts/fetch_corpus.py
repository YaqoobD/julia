"""Fetch the curated corpus URL list and extract clean main-content as markdown.

Reads URLs from data/corpus/sources.txt (one per line; blank lines and `#` comments OK).
For each URL: fetches with a polite User-Agent, runs trafilatura to strip nav/footer/
sidebar/scripts and produce clean markdown, then writes data/corpus/<slug>.md with
header comments for source_url and source_title.

By default, URLs whose output file already exists are SKIPPED — re-running only
fetches new URLs you've added to sources.txt. Pass --force to re-fetch everything
(useful if a source page has been updated upstream).

Run from project root:
    .venv/bin/python scripts/fetch_corpus.py            # only fetch new URLs
    .venv/bin/python scripts/fetch_corpus.py --force    # re-fetch all URLs
"""

from __future__ import annotations

import argparse
import re
import sys
import time
from pathlib import Path
from urllib.parse import urlparse

import httpx
import trafilatura

PROJECT_ROOT = Path(__file__).resolve().parent.parent
SOURCES_PATH = PROJECT_ROOT / "data" / "corpus" / "sources.txt"
CORPUS_DIR = PROJECT_ROOT / "data" / "corpus"

USER_AGENT = "Julia/0.1 (educational demo; please contact site owner if this scraper is unwelcome)"
REQUEST_TIMEOUT_S = 30.0
REQUEST_DELAY_S = 1.0


def slug_from_url(url: str) -> str:
    """Derive a filesystem-safe slug from a URL's path."""
    parsed = urlparse(url)
    path = parsed.path.strip("/").replace("/", "-")
    if not path:
        path = parsed.netloc.replace(".", "-")
    slug = re.sub(r"[^a-z0-9-]+", "-", path.lower()).strip("-")
    return slug or "index"


def read_sources(path: Path) -> list[str]:
    if not path.exists():
        sys.exit(
            f"sources.txt not found at {path.relative_to(PROJECT_ROOT)}. "
            "Create it with one URL per line."
        )
    urls: list[str] = []
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        urls.append(line)
    return urls


def fetch_html(url: str) -> str | None:
    try:
        resp = httpx.get(
            url,
            headers={"User-Agent": USER_AGENT},
            timeout=REQUEST_TIMEOUT_S,
            follow_redirects=True,
        )
        resp.raise_for_status()
        return resp.text
    except httpx.HTTPError as exc:
        print(f"  ! fetch failed: {exc}")
        return None


def extract_title(html: str, fallback: str) -> str:
    metadata = trafilatura.extract_metadata(html)
    if metadata is not None and metadata.title:
        return metadata.title.strip()
    # Fallback: <title> tag
    match = re.search(r"<title>(.*?)</title>", html, re.IGNORECASE | re.DOTALL)
    if match:
        return re.sub(r"\s+", " ", match.group(1)).strip()
    return fallback


def extract_markdown(html: str, url: str) -> str | None:
    md = trafilatura.extract(
        html,
        output_format="markdown",
        include_comments=False,
        include_tables=True,
        url=url,
    )
    if not md or not md.strip():
        print("  ! trafilatura returned no content")
        return None
    return md.strip()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--force",
        action="store_true",
        help="Re-fetch URLs whose output file already exists (default: skip them).",
    )
    args = parser.parse_args()

    urls = read_sources(SOURCES_PATH)
    if not urls:
        sys.exit("sources.txt is empty (or only comments). Add some URLs.")

    print(f"reading {len(urls)} URL(s) from {SOURCES_PATH.relative_to(PROJECT_ROOT)}")
    if args.force:
        print("--force given: existing output files will be overwritten")
    CORPUS_DIR.mkdir(parents=True, exist_ok=True)

    success = 0
    skipped = 0
    failed: list[str] = []
    total_chars = 0

    for i, url in enumerate(urls, start=1):
        slug = slug_from_url(url)
        out_path = CORPUS_DIR / f"{slug}.md"

        if out_path.exists() and not args.force:
            print(f"[{i}/{len(urls)}] {url}")
            print(f"  ↺ skipped — {out_path.relative_to(PROJECT_ROOT)} already exists (use --force to re-fetch)")
            skipped += 1
            continue

        print(f"[{i}/{len(urls)}] {url}")

        html = fetch_html(url)
        if html is None:
            failed.append(url)
            time.sleep(REQUEST_DELAY_S)
            continue

        title = extract_title(html, fallback=url)
        markdown = extract_markdown(html, url)
        if markdown is None:
            failed.append(url)
            time.sleep(REQUEST_DELAY_S)
            continue

        header = f"<!-- source_url: {url} -->\n<!-- source_title: {title} -->\n\n"
        out_path.write_text(header + markdown + "\n", encoding="utf-8")

        chars = len(markdown)
        total_chars += chars
        print(f"  ✓ {out_path.relative_to(PROJECT_ROOT)} ({chars:,} chars)  «{title}»")
        success += 1

        time.sleep(REQUEST_DELAY_S)

    print()
    summary = f"done: {success} new, {skipped} skipped"
    if total_chars:
        summary += f", {total_chars:,} chars extracted"
    print(summary)
    if failed:
        print("failed URLs:")
        for u in failed:
            print(f"  - {u}")
        sys.exit(1)


if __name__ == "__main__":
    main()

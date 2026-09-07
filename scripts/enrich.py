#!/usr/bin/env python3
"""Scrape wp.stolaf.edu for the building detail ArcGIS does not carry.

ArcGIS gives a name, an abbreviation, a type, a footprint and a paragraph of
prose. Carleton's dataset also carries floor plans, addresses, departments and
photos. This fills the one of those gaps that St. Olaf's web actually answers.

**Floor plans.** Every residence hall has a page under
`wp.stolaf.edu/residencelife/<slug>/` carrying labelled floor-plan PDFs, and
each building's ArcGIS `Information` field links to its own page. So the set of
pages to fetch is discovered from the scraped data rather than hardcoded: no
list to maintain, and a hall that gains or loses a page follows automatically.

**Addresses are deliberately not scraped**, though they look scrapeable. Every
page on the site shows `1520 St. Olaf Avenue, Northfield, MN 55057` — in the
sidebar as the Residence Life office's mailing address, and again in the site
footer as the college's. It is not the building's address, and no St. Olaf
building has one; taking it would have stamped the same wrong address onto all
twelve halls with nothing to reveal the error. Buildings are found by name here,
not by street address, which is why `address` stays null in the output.

**Departments are not scraped either** — they do not need to be. A building's
`Information` HTML already links to the departments it houses, with the
department name as the link text, so `build.py` reads them straight out of the
prose without a request.
"""

from __future__ import annotations

import html
import json
import re
import sys
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"

USER_AGENT = "StoDevX/campus-map-data (+https://github.com/StoDevX/campus-map-data)"

RESIDENCE_PAGE = re.compile(
    r"https://wp\.stolaf\.edu/residencelife/[a-z0-9-]+/?", re.IGNORECASE
)
LINK = re.compile(r"(?is)<a[^>]+href=\"([^\"]+\.pdf)\"[^>]*>(.*?)</a>")
HEADING = re.compile(r"(?is)<h1[^>]*>(.*?)</h1>")

# Link text that describes the link rather than the floor. WordPress's file
# block emits a second "Download" anchor for every file, and several pages label
# an alternate rendering "Printable" — neither is a floor label, and both point
# at a PDF already listed under its real name.
GENERIC_LABELS = frozenset({"download", "printable", "here", "click here", "link", ""})

# Room-photo PDFs live alongside the floor plans and are not floor plans.
PHOTO_HINT = re.compile(r"room[ _-]?photos?", re.IGNORECASE)


def text_of(markup: str) -> str:
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", markup))).strip()


def fetch(url: str) -> str | None:
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            return response.read().decode("utf-8", errors="replace")
    except (urllib.error.URLError, TimeoutError, OSError) as error:
        # A page that has moved should not fail the build. The building keeps
        # whatever it had, and the omission shows up as a diff.
        print(f"  ! {url}: {error}", file=sys.stderr)
        return None


def residence_pages() -> list[str]:
    """Residence-life pages linked from the scraped buildings, deduplicated."""
    buildings = json.loads((DATA / "buildings.geojson").read_text())
    found = set()
    for feature in buildings["features"]:
        information = (feature["properties"] or {}).get("Information") or ""
        for match in RESIDENCE_PAGE.finditer(information):
            found.add(match.group(0).rstrip("/") + "/")
    return sorted(found)


def parse(markup: str) -> dict:
    heading = HEADING.search(markup)
    floors: list[dict] = []
    photos: list[str] = []
    seen: set[str] = set()

    for match in LINK.finditer(markup):
        href, label_markup = match.group(1), match.group(2)
        if href in seen:
            continue
        label = text_of(label_markup)
        if PHOTO_HINT.search(label) or PHOTO_HINT.search(href):
            seen.add(href)
            photos.append(href)
            continue
        if label.casefold() in GENERIC_LABELS:
            continue
        seen.add(href)
        floors.append({"label": label, "href": href})

    return {
        "name": text_of(heading.group(1)) if heading else None,
        "floors": floors,
        "photos": sorted(photos),
    }


def main() -> int:
    pages = residence_pages()
    if not pages:
        print(
            "No residence-life pages linked from data/buildings.geojson",
            file=sys.stderr,
        )
        return 1

    scraped: dict[str, dict] = {}
    for url in pages:
        markup = fetch(url)
        if markup is None:
            continue
        record = parse(markup)
        scraped[url] = record
        print(
            f"{url}: {len(record['floors'])} floor plans, {len(record['photos'])} photo sets",
            file=sys.stderr,
        )

    (DATA / "residence-life.json").write_text(
        json.dumps(scraped, indent=2, ensure_ascii=False, sort_keys=True) + "\n"
    )
    print(f"\n{len(scraped)} of {len(pages)} pages scraped", file=sys.stderr)

    # Same reasoning as scrape.py: a wholesale failure means the site moved, and
    # committing the empty result would delete real data.
    if not scraped:
        print("ERROR: every page failed", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

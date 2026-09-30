#!/usr/bin/env python3
"""Keep a snapshot of what selected parts of a page say.

A watch in watches.yaml names a page and the CSS selectors whose contents
matter -- the Wellness Walks' titles and guides, say. This writes each watch's
matches to data/watches/<name>.txt; the watch workflow opens a pull request
when a snapshot changes, and the watch's `why` says what to do about it.

Selectors are run by `htmlq`, pinned in mise.toml, rather than a Python HTML
library: the scripts keep to two runtime dependencies.

`--check` writes nothing and exits 1 when any snapshot would change.
"""

from __future__ import annotations

import subprocess
import sys
import urllib.request
from pathlib import Path

import yaml
from enrich import USER_AGENT

ROOT = Path(__file__).resolve().parent.parent
SNAPSHOTS = ROOT / "data" / "watches"


def run_htmlq(html: str, css: str, attribute: str | None) -> list[str]:
    """Every match of `css` in `html`: its text, or its `attribute`."""
    args = (
        ["htmlq", "--attribute", attribute, css]
        if attribute
        else ["htmlq", "--text", css]
    )
    result = subprocess.run(
        args, input=html, capture_output=True, text=True, check=True
    )
    return result.stdout.splitlines()


def snapshot(html: str, select: list[dict], query=run_htmlq) -> str:
    lines = []
    for selector in select:
        css, attribute = selector["css"], selector.get("attribute")
        matches = [m.strip() for m in query(html, css, attribute) if m.strip()]
        if not matches:
            raise SystemExit(f"  {css} matches nothing: was the page redesigned?")
        lines.append(f"# {css}" + (f" @{attribute}" if attribute else ""))
        lines.extend(matches)
    return "\n".join(lines) + "\n"


def fetch(url: str) -> str:
    """The page, or the run fails: unlike enrich.py's fetch, a watch that cannot
    read its page has nothing to report, and must not report "unchanged"."""
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(request, timeout=60) as response:
        return response.read().decode("utf-8", errors="replace")


def main() -> int:
    check = "--check" in sys.argv
    watches = yaml.safe_load((ROOT / "watches.yaml").read_text()) or []
    SNAPSHOTS.mkdir(parents=True, exist_ok=True)
    changed = []
    for watch in watches:
        path = SNAPSHOTS / f"{watch['name']}.txt"
        text = snapshot(fetch(watch["url"]), watch["select"])
        if path.exists() and path.read_text() == text:
            print(f"  {watch['name']}: unchanged")
            continue
        changed.append(watch["name"])
        print(f"  {watch['name']}: changed -- {watch.get('why', '')}")
        if not check:
            path.write_text(text)
    return 1 if check and changed else 0


if __name__ == "__main__":
    raise SystemExit(main())

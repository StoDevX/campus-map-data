#!/usr/bin/env python3
"""Print the name for today's release: the date, or the date with a suffix.

A release is tagged data-<name>, and a tag can only exist once. Two changes can
land on the same day -- a scrape and a merged pull request -- so the second
gets -2, the third -3, and so on, rather than failing to publish.

Usage: release_name.py YYYY-MM-DD
"""

from __future__ import annotations

import subprocess
import sys

TAG_PREFIX = "data-"


def next_name(date: str, taken: set[str]) -> str:
    name = date
    n = 1
    while name in taken:
        n += 1
        name = f"{date}-{n}"
    return name


def taken_names() -> set[str]:
    tags = subprocess.run(
        ["git", "tag", "-l", f"{TAG_PREFIX}*"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout.split()
    return {tag.removeprefix(TAG_PREFIX) for tag in tags}


if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.exit(__doc__)
    print(next_name(sys.argv[1], taken_names()))

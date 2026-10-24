#!/usr/bin/env python3
"""Print the name for a release: the date, or the date with a suffix.

A release is tagged data-<name>, and a tag can only exist once. Two changes can
land on the same day -- a scrape and a merged pull request -- so the second
gets -2, the third -3, and so on, rather than failing to publish.

Given the commit the release is for, a commit that already has a release keeps
its name, so running the release again for it makes no second release.

Usage: release_name.py YYYY-MM-DD [COMMIT]
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


def release_name(date: str, taken: set[str], on_commit: set[str]) -> str:
    """The commit's existing release name, else the next free one for `date`."""
    if on_commit:
        return min(on_commit)
    return next_name(date, taken)


def tag_names(*args: str) -> set[str]:
    tags = subprocess.run(
        ["git", "tag", "-l", f"{TAG_PREFIX}*", *args],
        capture_output=True,
        text=True,
        check=True,
    ).stdout.split()
    return {tag.removeprefix(TAG_PREFIX) for tag in tags}


if __name__ == "__main__":
    if len(sys.argv) not in (2, 3):
        sys.exit(__doc__)
    on_commit = tag_names("--points-at", sys.argv[2]) if len(sys.argv) == 3 else set()
    print(release_name(sys.argv[1], tag_names(), on_commit))

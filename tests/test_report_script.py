"""The change report's hover script, run in headless Chrome.

The script is the one part of the page the Python tests cannot see work, so
these load a rendered page in a real browser, replay the events a pointer
sends, and read back what the page did. They skip where no Chrome is installed;
GitHub's Ubuntu runners have one.
"""

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest
from report_page import render
from test_report import DATE, fixture

CHROME = next(
    (
        path
        for path in (
            shutil.which("google-chrome"),
            shutil.which("chromium"),
            "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
        )
        if path and Path(path).exists()
    ),
    None,
)

# Replays what a browser sends when the pointer leaves card A for the gap
# beside it, then either reaches card B or stops on the background, and
# records whether the map stayed dimmed ("focusing") at each step.
PROBE = """
<script>
window.addEventListener("load", () => {
  const sheet = document.getElementById("sheet");
  const cards = [...document.querySelectorAll(".card")].filter((card) =>
    document.querySelector(`.place[data-place="${card.dataset.place}"]`));
  const [a, b] = cards;
  const enter = (el) => el.dispatchEvent(new MouseEvent("mouseenter"));
  const leave = (el) => el.dispatchEvent(new MouseEvent("mouseleave"));
  const dimmed = () => sheet.classList.contains("focusing");
  const result = {};
  enter(a);
  result.onA = dimmed();
  leave(a);
  result.inGap = dimmed();
  enter(b);
  result.onB = dimmed() && b.classList.contains("hl") && !a.classList.contains("hl");
  leave(b);
  setTimeout(() => {
    result.restingOnBackground = dimmed();
    const out = document.createElement("pre");
    out.id = "probe";
    out.textContent = JSON.stringify(result);
    document.body.append(out);
  }, 500);
});
</script>
"""


def probe(tmp_path: Path) -> dict:
    page = render(*fixture(), DATE).replace("</body>", PROBE + "</body>")
    path = tmp_path / "page.html"
    path.write_text(page, encoding="utf-8")
    dom = subprocess.run(
        [
            CHROME,
            "--headless=new",
            "--disable-gpu",
            "--no-sandbox",
            "--virtual-time-budget=2000",
            "--dump-dom",
            path.as_uri(),
        ],
        capture_output=True,
        text=True,
        timeout=60,
        check=True,
    ).stdout
    return json.loads(re.search(r'<pre id="probe">([^<]*)</pre>', dom).group(1))


@pytest.mark.skipif(CHROME is None, reason="no Chrome to run the page in")
def test_crossing_the_gap_between_cards_keeps_the_map_dimmed(tmp_path):
    result = probe(tmp_path)
    assert result["onA"]
    assert result["inGap"]
    assert result["onB"]


@pytest.mark.skipif(CHROME is None, reason="no Chrome to run the page in")
def test_resting_on_the_background_undims_the_map(tmp_path):
    assert not probe(tmp_path)["restingOnBackground"]

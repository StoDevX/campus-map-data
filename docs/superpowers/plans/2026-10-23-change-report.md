# Change Report Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Every change to the published campus data gets a deterministic before/after report page and a GitHub release that links to it.

**Architecture:** `scripts/changes.py` reads two states of the published files (a git revision, or the working tree) and computes a `ChangeSet`. `scripts/report.py` renders a `ChangeSet` to one self-contained HTML page with inline SVG, plus a plain summary and release notes, and is the CLI the workflows call. A new `release.yml` owns committing the page, dispatching the tile build, and publishing the release; `scrape.yml` builds the report on every run and dispatches `release.yml` after it commits.

**Tech Stack:** Python 3.13 stdlib (`dataclasses`, `difflib`, `html`, `json`, `subprocess`), the repo's `scripts/geometry.py`, pytest, GitHub Actions with `gh`.

**Spec:** `docs/superpowers/specs/2026-10-23-change-report-design.md`

## Global Constraints

- Python 3.13, stdlib only beyond `pyyaml` and `pmtiles`; no new dependencies.
- Reuse `scripts/geometry.py` (`distance_m`, `length_m`, `polygons`, `positions`, `round_coords`); do not reimplement geometry.
- Same before and after inputs → byte-identical HTML. No clock reads, no timestamps; the date is an argument.
- A move is a stretch more than **3 m** from the other shape; changes within 3 m are listed as "geometry changed, under 3 m", never hidden.
- Changes within about **150 m** of each other share a close-up.
- The page is one HTML file: inline CSS, inline SVG, inline vanilla JS; the only external request allowed is an optional Google Fonts stylesheet, and the page must render without it. Light and dark themes; works at phone width.
- Release tag `data-<YYYY-MM-DD>`, title `Campus data <YYYY-MM-DD>`; page `site/changes/<YYYY-MM-DD>.html`; a second change the same UTC day gets `-2`, then `-3`, on both.
- `routing.json` changes alone are not a change; with other changes they get one line.
- Nothing after the scrape's data push may undo it; report, dispatch and release failures show as failed steps only.
- Workflows pin actions by SHA, as the existing ones do. Match the repo's style: docstrings that say why, comments for what and why, `ruff` clean.

## Review Focus

1. **A same-day second change** — the page and tag get `-2` rather than overwriting the morning's. Pinned by Task 7's `next_name` test.
2. **A place whose geometry type changes or appears** (a point becomes a polygon; a place had no shape) — reported as a changed shape with both drawn, not a crash. Pinned in Task 3.
3. **A sweeping change** (a new 36-point layer; every place nudged by a rounding change) — one overview, at most 12 close-ups with "and N more areas", page under 2 MB. Pinned in Task 5.
4. **A text-only change** (one description rewritten) — a card and no close-up; **a routing-only change** — no report at all. Pinned in Tasks 2 and 5.
5. **Markup in the college's text** (`&`, `<b>`, quotes in names and descriptions) — escaped in the page, never interpreted. Pinned in Task 5.

---

### Task 1: Read a state of the published files

**Files:**
- Create: `scripts/changes.py`
- Test: `tests/test_changes.py`

**Interfaces:**
- Produces:
  - `@dataclass(frozen=True) class State: places: dict[str, dict]` (id → `map.json` record), `shapes: dict[str, dict]` (id → GeoJSON geometry: the feature's first non-Point member of its GeometryCollection, else its Point), `context: dict[str, list[dict]]` (slug → geometries, for every `Source` with `role == "context"` plus `water`), `routing: bytes` (raw `routing.json`, `b""` when absent).
  - `read_state(root: Path, revision: str | None) -> State` — `None` reads the working tree; a revision reads each file with `git -C root show <revision>:<path>`. A file missing at that revision reads as empty (no places, no features).

- [ ] **Step 1: Write the failing tests** — each builds a real git repo in `tmp_path` (`git init`, write `map.json`, `map.geojson`, `data/walkways.geojson`, commit), no mocks.

```python
def test_a_revision_reads_the_files_as_committed(tmp_path): ...
    # commit one place "om", then change its name in the working tree
    assert read_state(tmp_path, "HEAD").places["om"]["name"] == "Old Main"
    assert read_state(tmp_path, None).places["om"]["name"] == "Old Main Hall"

def test_a_place_shape_is_its_geometry_not_its_label_point(tmp_path): ...
    # feature geometries: [Polygon, Point]
    assert read_state(tmp_path, None).shapes["om"]["type"] == "Polygon"

def test_a_point_place_shape_is_its_point(tmp_path): ...
    assert read_state(tmp_path, None).shapes["bike-rack-x"]["type"] == "Point"

def test_context_holds_the_context_layers(tmp_path): ...
    assert len(read_state(tmp_path, None).context["walkways"]) == 2

def test_a_file_missing_at_a_revision_reads_as_empty(tmp_path): ...
    assert read_state(tmp_path, "HEAD").context["campus-roads"] == []
```

- [ ] **Step 2: Run** `uv run pytest tests/test_changes.py -q` — expect ImportError / FAIL.
- [ ] **Step 3: Implement `State` and `read_state` in `scripts/changes.py`**, with a module docstring saying it computes what changed between two states of the published files, and why it reads exactly the published files (the spec's "a report that lies" anti-goal). Context slugs come from `sources.SOURCES`.
- [ ] **Step 4: Run** the tests — PASS; `uv run ruff check scripts tests && uv run ruff format --check scripts tests` clean.
- [ ] **Step 5: Commit** `git commit -m "Read a state of the published campus data from git or the working tree"`

### Task 2: Compare two states

**Files:**
- Modify: `scripts/changes.py`
- Test: `tests/test_changes.py`

**Interfaces:**
- Consumes: `State`, `moved` (Task 3 — write Task 2 so `PlaceChange.move` is `None` until Task 3 fills it; Task 3 adds the call).
- Produces:
  - `@dataclass(frozen=True) class FieldChange: field: str; before: object; after: object`
  - `@dataclass(frozen=True) class PlaceChange: kind: str` (`"added" | "removed" | "renamed" | "changed"`)`; before_id: str | None; after_id: str | None; name: str` (after name, else before)`; fields: tuple[FieldChange, ...]; before_shape: dict | None; after_shape: dict | None; move: "Move | None"`
  - `@dataclass(frozen=True) class ContextChange: slug: str; added: tuple[dict, ...]; removed: tuple[dict, ...]`
  - `@dataclass(frozen=True) class ChangeSet: places: tuple[PlaceChange, ...]; context: tuple[ContextChange, ...]; routing_changed: bool` with property `is_empty -> bool` (true when `places` and `context` are empty, whatever `routing_changed` says).
  - `compare(before: State, after: State) -> ChangeSet` — places sorted by `(kind order added, removed, renamed, changed; then name; then id)`; fields sorted by name; context by slug.
  - `RENAME_REACH_M = 25`

- [ ] **Step 1: Write the failing tests**

```python
def test_identical_states_have_no_changes(): assert compare(s, s).is_empty
def test_a_new_id_is_added(): ... kind == "added", after_id == "bike-rack-x"
def test_a_missing_id_is_removed(): ... kind == "removed"
def test_a_changed_field_is_listed_before_and_after():
    # description "A" -> "B"
    assert change.fields == (FieldChange("description", "A", "B"),)
# Ids derive from names, so a rename arrives as one removed and one added.
def test_a_removed_and_added_place_side_by_side_are_a_rename():
    # same categories, centers 10 m apart
    assert [c.kind for c in changes.places] == ["renamed"]
    assert (change.before_id, change.after_id) == ("hilleboehall", "hillboehall")
def test_places_far_apart_are_not_a_rename():  # 200 m apart -> one added, one removed
def test_places_of_different_categories_are_not_a_rename():
def test_a_context_line_that_appears_is_added(): ... context[0].slug == "walkways", len(added) == 1
def test_only_routing_changed_is_empty():
    assert changes.routing_changed and changes.is_empty
def test_comparing_twice_gives_the_same_order(): assert compare(a, b) == compare(a, b)
```

- [ ] **Step 2: Run** `uv run pytest tests/test_changes.py -q` — new tests FAIL.
- [ ] **Step 3: Implement the dataclasses and `compare`.** Renames pair each removed place with the nearest added place whose `categories` keys are equal and whose `center` is within `RENAME_REACH_M` (distance via `geometry.distance_m` on lon-first points; `center` is lat-first). Pairing is greedy in id order so it is deterministic. Context compares geometries as `json.dumps(g, sort_keys=True)` sets.
- [ ] **Step 4: Run** — PASS, ruff clean.
- [ ] **Step 5: Commit** `git commit -m "Compare two states of the campus data place by place"`

### Task 3: Measure moves and diff words

**Files:**
- Modify: `scripts/changes.py`
- Test: `tests/test_changes.py`

**Interfaces:**
- Produces:
  - `@dataclass(frozen=True) class Move: dropped: tuple[tuple[Position, ...], ...]; added: tuple[tuple[Position, ...], ...]; dropped_m: float; added_m: float` with property `visible -> bool` (any stretch dropped or added).
  - `MOVE_REACH_M = 3.0`
  - `moved(before: dict | None, after: dict | None) -> Move` — stretches of `before`'s lines or rings more than `MOVE_REACH_M` from `after` are dropped, and the reverse added, judged at each stretch's midpoint with `geometry.distance_m`. A point counts as a one-position stretch. A missing side makes the whole other shape dropped or added.
  - `word_diff(before: str, after: str) -> tuple[tuple[str, str], ...]` — `("same" | "removed" | "added", text)` runs over whitespace-split words, from `difflib.SequenceMatcher`.
  - `compare` now sets `PlaceChange.move = moved(before_shape, after_shape)` for `"changed"` and `"renamed"` places whose shapes differ, and adds a `FieldChange("geometry", ..., ...)` only when they differ.

- [ ] **Step 1: Write the failing tests**

```python
def test_a_line_redrawn_13_m_away_is_dropped_and_added():
    move = moved(line_at(44.4600), line_at(44.46012))  # ~13 m north
    assert move.visible and round(move.dropped_m) == round(move.added_m) == 111
def test_a_line_nudged_under_3_m_is_not_a_move():
    assert not moved(line_at(44.4600), line_at(44.46001)).visible
def test_a_shortened_line_drops_only_its_end(): ... dropped_m ≈ cut length, added_m == 0
def test_a_point_that_became_a_polygon_is_a_move():  # Review Focus 2
    move = moved(POINT, square_far_away)
    assert move.visible
def test_a_shape_that_appeared_is_all_added():  # Review Focus 2
    assert moved(None, POINT).added and not moved(None, POINT).dropped
def test_word_diff_marks_the_changed_words():
    assert word_diff("a red barn", "a blue barn") == (
        ("same", "a"), ("removed", "red"), ("added", "blue"), ("same", "barn"))
def test_a_changed_place_carries_its_move(): ... change.move.visible
```

- [ ] **Step 2: Run** — FAIL.
- [ ] **Step 3: Implement `Move`, `moved`, `word_diff`, and wire `moved` into `compare`.** Rings of polygons are treated as closed lines; the prototype's `off_runs` (in the Heath Creek page's generator) is the reference algorithm.
- [ ] **Step 4: Run** — PASS, ruff clean.
- [ ] **Step 5: Commit** `git commit -m "Measure what moved more than 3 m, and diff changed prose by word"`

### Task 4: Group changes into areas, and summarise them

**Files:**
- Create: `scripts/report.py`
- Test: `tests/test_report.py`

**Interfaces:**
- Consumes: `ChangeSet`, `PlaceChange`, `Move` (Tasks 2–3).
- Produces:
  - `CLUSTER_REACH_M = 150`; `MAX_CLOSEUPS = 12`
  - `areas(changes: ChangeSet) -> list[list[PlaceChange]]` — places with a visible move, grouped so any two within `CLUSTER_REACH_M` (by `geometry.distance_m` between their moved stretches) share a group; groups ordered north to south, then west to east, by their first position. A group of more than 20 places that are all `"added"` points (a new layer) is one area.
  - `summary(changes: ChangeSet) -> str` — one plain paragraph built from counts by kind and by first category, naming up to three places, e.g. `"36 bike racks added. 4 trails changed shape: Big Woods Trail, Burr Oak Trail, Lower Heath Creek Trail and 1 more."` When `routing_changed`, append `"The walking routes were rebuilt to match."`
  - `release_notes(changes: ChangeSet, report_url: str, commit_url: str) -> str` — Markdown: the summary, a counts table (added, removed, renamed, changed), the report link, the commit link.

- [ ] **Step 1: Write the failing tests**

```python
def test_moves_near_each_other_share_an_area(): assert len(areas(two_100m_apart)) == 1
def test_moves_far_apart_get_their_own_areas(): assert len(areas(two_600m_apart)) == 2
def test_a_text_only_change_has_no_area(): assert areas(description_only) == []  # Review Focus 4
def test_a_new_layer_of_points_is_one_area(): assert len(areas(thirty_six_racks)) == 1
def test_summary_counts_and_names_the_places():
    assert summary(changes) == "36 bike racks added. 4 trails changed shape: Big Woods Trail, Burr Oak Trail, Lower Heath Creek Trail and 1 more."
def test_release_notes_link_the_report_and_commit():
    notes = release_notes(changes, "https://r", "https://c")
    assert "https://r" in notes and "https://c" in notes
```

- [ ] **Step 2: Run** `uv run pytest tests/test_report.py -q` — FAIL.
- [ ] **Step 3: Implement `areas`, `summary`, `release_notes` in `scripts/report.py`.** Module docstring: what the report is for (spec "Why"), that it is deterministic, and that it draws only from the published files. The category word in the summary comes from a small map (`bike-rack` → "bike rack", `trail` → "trail", `building` → "building", `parking` → "parking lot", …), falling back to the category slug with hyphens as spaces.
- [ ] **Step 4: Run** — PASS, ruff clean.
- [ ] **Step 5: Commit** `git commit -m "Group changes into areas and summarise them for a release"`

### Task 5: Render the page

**Files:**
- Modify: `scripts/report.py`
- Create: `tests/golden/report.html` (generated in Step 4, then reviewed by eye and committed)
- Test: `tests/test_report.py`

**Interfaces:**
- Consumes: `areas`, `summary` (Task 4); `State` (Task 1) for the background (water, walkways from `after.context`, building outlines from `after.shapes` of places with category `building`).
- Produces: `render(changes: ChangeSet, before: State, after: State, date: str) -> str` — the whole HTML page.

Page contents, in order (spec "The page"): title `Campus data changes, <date>`; the summary; counts; the overview SVG of every changed shape; one close-up SVG per area (at most `MAX_CLOSEUPS`, then a line "and N more areas, shown on the overview"); the cards grouped added / removed / renamed / changed, each with its id(s), name and changed fields before → after (prose as `word_diff` with `<del>`/`<ins>`, lists as items added and removed, lengths in m); a note for context changes per layer, and for routing when it changed. Before shapes dashed, after solid, dropped stretches and added stretches in two distinct colours. Checkboxes toggle before and after; hovering or focusing a card highlights its shapes and vice versa, by a shared `data-place` attribute. SVG coordinates rounded to 1 decimal. The Heath Creek page (`https://claude.ai/artifact/MiZrP9DN1ot8quWwu2tJkM`) is the visual reference.

- [ ] **Step 1: Write the failing tests**

```python
def test_rendering_twice_is_byte_identical(): assert render(c, b, a, D) == render(c, b, a, D)
def test_the_page_matches_the_golden_file():
    assert render(*FIXTURE, "2026-11-01") == (GOLDEN := Path("tests/golden/report.html")).read_text()
def test_markup_in_the_colleges_text_is_escaped():  # Review Focus 5
    page = render(changes_with_description('<b>Fish & "Chips"</b>'), ...)
    assert "<b>Fish" not in page and "&lt;b&gt;Fish &amp;" in page
def test_a_text_only_change_draws_no_closeup():  # Review Focus 4
    assert 'class="closeup"' not in render(description_only, ...)
def test_close_ups_stop_at_twelve():  # Review Focus 3
    page = render(fifteen_far_apart_moves, ...)
    assert page.count('class="closeup"') == 12 and "and 3 more areas" in page
def test_a_sweeping_change_stays_under_two_megabytes():  # Review Focus 3
    assert len(render(every_place_nudged_4_m, ...).encode()) < 2_000_000
def test_the_page_makes_no_requests_but_fonts():
    assert set(re.findall(r'(?:src|href)="(https?://[^/"]+)', page)) <= {"https://fonts.googleapis.com"}
```

- [ ] **Step 2: Run** — FAIL.
- [ ] **Step 3: Implement `render`** with the theme token pattern (light on `:root`, dark under `prefers-color-scheme` and `[data-theme="dark"]`), and a flat local projection like the reference page's `View`. Escape every string from the data with `html.escape`.
- [ ] **Step 4: Generate the golden file** with `UPDATE_GOLDEN=1 uv run pytest tests/test_report.py -q` (the golden test writes the file when that variable is set), open it in a browser, check light and dark and phone width by eye, then run without the variable — PASS.
- [ ] **Step 5: Commit** `git commit -m "Render a change report page: before and after, close-ups and cards"`

### Task 6: The command line

**Files:**
- Modify: `scripts/report.py`
- Test: `tests/test_report.py`

**Interfaces:**
- Consumes: `read_state`, `compare` (Tasks 1–3); `render`, `summary`, `release_notes` (Tasks 4–5).
- Produces: `main(argv: list[str] | None = None) -> int`, run as
  `uv run scripts/report.py --before REV [--after REV] --date YYYY-MM-DD --out PATH [--notes PATH --report-url URL --commit-url URL] [--root DIR]`.
  No `--after` means the working tree. When the states differ it writes `--out` (and `--notes` when given) and prints the summary; when they do not, it writes nothing and prints `No change to the published data.` Exit 0 either way; nonzero only on a real error (a bad revision).

- [ ] **Step 1: Write the failing tests** (real temp git repo, as in Task 1)

```python
def test_a_change_writes_the_page_and_notes(tmp_path): ... assert out.exists() and notes.exists()
def test_no_change_writes_nothing(tmp_path, capsys):
    assert main([...]) == 0 and not out.exists()
    assert "No change to the published data." in capsys.readouterr().out
def test_a_bad_revision_fails(tmp_path): assert main(["--before", "nope", ...]) != 0
```

- [ ] **Step 2: Run** — FAIL.
- [ ] **Step 3: Implement `main` with `argparse`**, and `if __name__ == "__main__": raise SystemExit(main())`, like `scrape.py`. `--root` defaults to the repo root, `Path(__file__).resolve().parent.parent`; tests pass their temp repo.
- [ ] **Step 4: Run** the tests, then the real end-to-end check from the repo: on branch `natural-lands-segments`, `uv run scripts/report.py --before main --after HEAD --date 2026-10-23 --out /tmp/r.html` — expect a page showing the four Heath Creek trails, Robin's east end and Big Woods' dropped stretch, and a summary naming them. Do the same against `bike-racks` — expect one area and 36 added cards.
- [ ] **Step 5: Commit** `git commit -m "Add the change report command the workflows call"`

### Task 7: The release workflow

**Files:**
- Create: `.github/workflows/release.yml`
- Create: `scripts/release_name.py`
- Modify: `build-tiles.sh` (after line 408, `cp "$ROOT/site/.nojekyll" ...`)
- Modify: `.github/workflows/tiles.yml:4-7` (the comment claiming the scrape's push triggers it)
- Test: `tests/test_release_name.py`

**Interfaces:**
- Consumes: `report.py` CLI (Task 6).
- Produces:
  - `next_name(date: str, taken: set[str]) -> str` in `scripts/release_name.py` — `"2026-11-01"` if free, else `"2026-11-01-2"`, `"-3"`…; `taken` holds names already used (from `git tag -l 'data-*'` with the prefix stripped). CLI prints the name.
  - `release.yml`, triggered by `push` to `main` with `paths: [map.json, map.geojson, "data/**"]` (range `github.event.before`..`github.sha`), by `workflow_dispatch` with inputs `before` and `after` (the scrape), and by `pull_request` on its own files as a dry run that builds the report and stops.

- [ ] **Step 1: Write the failing tests**

```python
def test_a_free_date_is_its_own_name(): assert next_name("2026-11-01", set()) == "2026-11-01"
def test_a_second_change_that_day_gets_2():  # Review Focus 1
    assert next_name("2026-11-01", {"2026-11-01"}) == "2026-11-01-2"
def test_a_third_gets_3(): assert next_name("2026-11-01", {"2026-11-01", "2026-11-01-2"}) == "2026-11-01-3"
```

- [ ] **Step 2: Run** — FAIL; **Step 3:** implement `next_name`; **Step 4:** run — PASS.
- [ ] **Step 5: Write `release.yml`.** Permissions `contents: write`, `actions: write`; concurrency group `release`, not cancelled. Steps: checkout with `fetch-depth: 0`; mise and `uv sync --locked` as `scrape.yml` does; set `date=$(date -u +%F)`, `name=$(uv run scripts/release_name.py "$date")`; run `report.py --before <before> --after <after> --date "$date" --out "site/changes/$name.html" --notes "$RUNNER_TEMP/notes.md" --report-url "https://stolaf.dev/campus-map-data/changes/$name.html" --commit-url "$GITHUB_SERVER_URL/$GITHUB_REPOSITORY/commit/<after>"`; stop if no page was written; on `pull_request`, stop here. Otherwise commit `site/changes/$name.html` as `github-actions[bot]` with message `Add the change report for $name`, push with the scrape's three-attempt rebase loop; `gh workflow run tiles.yml --ref main`; `gh release create "data-$name" --target <after> --title "Campus data $name" --notes-file "$RUNNER_TEMP/notes.md"`.
- [ ] **Step 6:** In `build-tiles.sh`, copy `site/changes/` into `$DIST/changes/` when it exists. In `tiles.yml`, rewrite the push-trigger comment to say a person's push lands here, and a scrape's data arrives through `release.yml`'s dispatch, because a `GITHUB_TOKEN` push starts no workflow.
- [ ] **Step 7: Verify** — `uv run pytest -q` passes; `actionlint` and `shellcheck build-tiles.sh` pass in CI (neither is installed locally; install with `brew install actionlint shellcheck` to run them first).
- [ ] **Step 8: Commit** `git commit -m "Publish a release and a report page for every change to the data"`

### Task 8: Report from the scrape

**Files:**
- Modify: `.github/workflows/scrape.yml` (after "Build the routing graph"; the Verify step; Summarise; Commit; permissions)
- Modify: `README.md` (the "commit history is the change log" section, "What is here")

**Interfaces:**
- Consumes: `report.py` CLI (Task 6), `release.yml`'s `workflow_dispatch` inputs (Task 7).

- [ ] **Step 1:** Give Verify an `id: verify` and `continue-on-error: true`, and add a final step that fails the job when `steps.verify.outcome != 'success'`, so the report steps between them still run on a failing verify.
- [ ] **Step 2:** Add "Build the change report": `report.py --before <HEAD, or origin/${{ github.base_ref }} on a PR> --date "$(date -u +%F)" --out "$RUNNER_TEMP/report.html"`, `continue-on-error: true`. On a PR, fetch the base branch first, and have the Summarise step say the report compares the base branch with data regenerated from today's live layers, so it can include college edits that are not this PR's (the spec's PR note; a fixed sentence, not a computed one).
- [ ] **Step 3:** Add "Upload the change report" with `actions/upload-artifact` (pinned SHA) when the page exists and (`github.event_name == 'pull_request'` or verify failed). The Summarise step gains a line pointing to the artifact, or saying there was no change.
- [ ] **Step 4:** Make the Commit step run only when verify passed; after its push succeeds, record `before` (the `HEAD` it started from) and `after` (the pushed commit) and run `gh workflow run release.yml --ref main -f before=... -f after=...`, with `GH_TOKEN: ${{ github.token }}`, as a separate step with `continue-on-error: true`. Add `actions: write` to the workflow's permissions.
- [ ] **Step 5:** README: in the change-log section, a paragraph saying every change to the published data gets a release, "Campus data <date>", with a before/after report at `stolaf.dev/campus-map-data/changes/`, and that watching the repo for releases is how to hear about changes; add `site/changes/` to "What is here".
- [ ] **Step 6: Verify** — `uv run pytest -q` passes; push the branch and open the PR; confirm in the PR's checks that the scrape dry run uploads a report artifact (the live data has drifted since `main`, or the PR's own data changes) and that `release.yml`'s dry run builds its report and stops; actionlint passes.
- [ ] **Step 7: Commit** `git commit -m "Build a change report on every scrape and hand changes to the release workflow"`

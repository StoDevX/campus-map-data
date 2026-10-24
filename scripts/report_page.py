#!/usr/bin/env python3
"""Render a change report as one self-contained HTML page.

The page is for someone who did not watch the change happen: it shows every
changed place on a map, before dashed and after solid, with the stretches that
were dropped and added picked out, then a close-up of each area of change and a
card per place saying what changed in plain words and metres.

Everything is inline — styles, SVG and script — so the page can be committed,
attached to a failed run or opened offline, and still render. The only request
it makes is an optional Google Fonts stylesheet, with system fonts behind it.

Rendering is deterministic: the same changes give the same bytes. Places and
areas arrive sorted from `changes` and `report`, background shapes are drawn in
file order, coordinates are rounded to a tenth of a pixel and nothing reads the
clock; the date is an argument.
"""

import collections
import html
import json
import math

from changes import (
    MOVE_REACH_M,
    ChangeSet,
    ContextChange,
    FieldChange,
    PlaceChange,
    State,
    word_diff,
)
from geometry import (
    METRES_PER_DEGREE_LAT,
    METRES_PER_DEGREE_LON_AT_EQUATOR,
    distance_m,
    lines,
    polygons,
    positions,
)
from report import MAX_CLOSEUPS, area_geometry, areas, names_in, summary
from sources import SOURCES

OVERVIEW_WIDTH = 900
CLOSEUP_WIDTH = 640
OVERVIEW_PAD_M = 40
CLOSEUP_PAD_M = 30

# A view is never narrower than this, so a single moved point still shows the
# buildings around it.
MIN_VIEW_M = 120

# Views are kept between wide and square: a long north-south trail gets more of
# its surroundings rather than a page-length map.
MAX_HEIGHT_RATIO = 1.0
MIN_HEIGHT_RATIO = 0.45

# Background beyond a view's edge by this many pixels is left out.
BACKGROUND_MARGIN_PX = 40

# Points closer than this to the line through their neighbours are dropped. The
# move stretches are sampled every metre, and a straight edge drawn as hundreds
# of collinear points is page weight with nothing to see.
SIMPLIFY_PX = 0.3

POINT_RADIUS_PX = 4.5

SCALE_BAR_STEPS_M = [5, 10, 20, 50, 100, 200, 500, 1000, 2000]

KIND_HEADINGS = {
    "added": "Added",
    "removed": "Removed",
    "renamed": "Renamed",
    "changed": "Changed",
}

# Fields whose values are prose, shown as a word-by-word difference.
PROSE_FIELDS = {"description", "rules"}

# Fields whose values are dicts of flags, which read as a list of their keys.
FLAG_FIELDS = {"categories"}

FIELD_LABELS = {"geometry": "Shape"}

LAYER_TITLES = {source.slug: source.title for source in reversed(SOURCES)}


def escape(value: object) -> str:
    return html.escape(str(value), quote=True)


def number(value: float) -> str:
    """A coordinate at one decimal, with no negative zero."""
    return f"{round(value, 1) + 0.0:.1f}"


def metres(value: float) -> str:
    return f"{round(value):,} m"


def simplified(points: list[tuple[float, float]]) -> list[tuple[float, float]]:
    """Douglas-Peucker: keep only points that bend the line by `SIMPLIFY_PX`."""
    if len(points) < 3:
        return points
    keep = [False] * len(points)
    keep[0] = keep[-1] = True
    stack = [(0, len(points) - 1)]
    while stack:
        first, last = stack.pop()
        (ax, ay), (bx, by) = points[first], points[last]
        span = math.hypot(bx - ax, by - ay)
        farthest, index = 0.0, None
        for i in range(first + 1, last):
            px, py = points[i]
            if span == 0:
                off = math.hypot(px - ax, py - ay)
            else:
                off = abs((bx - ax) * (ay - py) - (ax - px) * (by - ay)) / span
            if off > farthest:
                farthest, index = off, i
        if index is not None and farthest > SIMPLIFY_PX:
            keep[index] = True
            stack += [(first, index), (index, last)]
    return [p for p, kept in zip(points, keep, strict=True) if kept]


class View:
    """A flat projection of a patch of campus onto an SVG of a fixed width.

    Across a campus a few kilometres wide, degrees scaled by the cosine of the
    latitude are off by far less than a pixel, so no map projection is needed.
    """

    def __init__(self, points: list, pad_m: float, width: int):
        lons = [p[0] for p in points]
        lats = [p[1] for p in points]
        latitude = (min(lats) + max(lats)) / 2
        self.kx = math.cos(math.radians(latitude)) * METRES_PER_DEGREE_LON_AT_EQUATOR
        self.ky = METRES_PER_DEGREE_LAT
        width_m = max((max(lons) - min(lons)) * self.kx + 2 * pad_m, MIN_VIEW_M)
        height_m = (max(lats) - min(lats)) * self.ky + 2 * pad_m
        width_m = max(width_m, height_m / MAX_HEIGHT_RATIO)
        height_m = max(height_m, width_m * MIN_HEIGHT_RATIO)
        middle_x = (min(lons) + max(lons)) / 2 * self.kx
        middle_y = (min(lats) + max(lats)) / 2 * self.ky
        self.left = middle_x - width_m / 2
        self.top = middle_y + height_m / 2
        self.width_m = width_m
        self.scale = width / width_m
        self.width = width
        self.height = round(height_m * self.scale)

    def xy(self, position) -> tuple[float, float]:
        return (
            round((position[0] * self.kx - self.left) * self.scale, 1),
            round((self.top - position[1] * self.ky) * self.scale, 1),
        )

    def overlaps(self, points: list) -> bool:
        """Whether a shape's bounding box reaches into the view, with a margin."""
        if not points:
            return False
        xs, ys = zip(*(self.xy(p) for p in points), strict=True)
        margin = BACKGROUND_MARGIN_PX
        return (
            max(xs) >= -margin
            and min(xs) <= self.width + margin
            and max(ys) >= -margin
            and min(ys) <= self.height + margin
        )

    def path(self, line: list, closed: bool = False) -> str:
        points: list[tuple[float, float]] = []
        for position in line:
            point = self.xy(position)
            if not points or point != points[-1]:
                points.append(point)
        drawn = " ".join(f"{number(x)},{number(y)}" for x, y in simplified(points))
        return f"M{drawn}{'Z' if closed else ''}"


def shape_marks(view: View, geometry: dict | None, css: str) -> list[str]:
    """SVG elements drawing a geometry: rings and lines as paths, else dots."""
    marks = [
        f'<path class="{css}" d="{view.path(ring, closed=True)}"/>'
        for rings in polygons(geometry)
        for ring in rings
    ]
    marks += [
        f'<path class="{css}" d="{view.path(line)}"/>' for line in lines(geometry)
    ]
    if not marks:
        for position in positions(geometry):
            x, y = view.xy(position)
            marks.append(
                f'<circle class="{css}" cx="{number(x)}" cy="{number(y)}" '
                f'r="{POINT_RADIUS_PX}"/>'
            )
    return marks


def stretch_marks(view: View, stretches, css: str) -> list[str]:
    """Moved stretches: runs as paths, a lone moved point as a dot."""
    marks = []
    for stretch in stretches:
        if len(stretch) > 1:
            marks.append(f'<path class="{css}" d="{view.path(list(stretch))}"/>')
        else:
            marks += shape_marks(
                view, {"type": "Point", "coordinates": list(stretch[0])}, css
            )
    return marks


def place_id(change: PlaceChange) -> str:
    return change.after_id or change.before_id or ""


def place_marks(view: View, change: PlaceChange) -> str:
    """One changed place: before dashed, after solid, and what moved on top.

    A removed place is all dropped and an added place all added, so they take
    the same highlight colours as a redrawn stretch.
    """
    before = shape_marks(view, change.before_shape, "before layer-before")
    after = shape_marks(view, change.after_shape, "after layer-after")
    if change.kind == "removed":
        moved = shape_marks(view, change.before_shape, "dropped layer-before")
    elif change.kind == "added":
        moved = shape_marks(view, change.after_shape, "added layer-after")
    elif change.move is not None:
        moved = stretch_marks(view, change.move.dropped, "dropped layer-before")
        moved += stretch_marks(view, change.move.added, "added layer-after")
    else:
        moved = []
    label = escape(change.name)
    return (
        f'<g class="place" data-place="{escape(place_id(change))}">'
        f"<title>{label}</title>{''.join(before + after + moved)}</g>"
    )


def shapes_of(state: State, category: str) -> list[dict]:
    """The shapes of every place in a category, in id order."""
    return [
        state.shapes[place_id]
        for place_id in sorted(state.shapes)
        if category in state.places.get(place_id, {}).get("categories", {})
    ]


def outer_rings(view: View, geometries: list[dict], css: str) -> list[str]:
    """The outer ring of each polygon reaching into the view, filled."""
    return [
        f'<path class="{css}" d="{view.path(rings[0], closed=True)}"/>'
        for geometry in geometries
        for rings in polygons(geometry)
        if view.overlaps(rings[0])
    ]


def background(view: View, after: State) -> list[str]:
    """Water, walkways and buildings as they are after the change.

    Unnamed ponds are background and named ones are places, so water is drawn
    from both.
    """
    water = [*after.context.get("water", []), *shapes_of(after, "water")]
    marks = outer_rings(view, water, "water")
    for geometry in after.context.get("walkways", []):
        for line in lines(geometry):
            if view.overlaps(line):
                marks.append(f'<path class="walkway" d="{view.path(line)}"/>')
    marks += outer_rings(view, shapes_of(after, "building"), "building")
    return marks


def context_marks(view: View, context: tuple[ContextChange, ...]) -> list[str]:
    """Background shapes that disappeared or appeared, to be drawn faintly.

    They take the dropped and added colours of a place's move, and sit with the
    before and after layers so the toggles hide them too. Only those reaching
    into the view are drawn; the notes below the map count every one.
    """
    marks = []
    for change in context:
        for geometries, css in (
            (change.removed, "context-dropped layer-before"),
            (change.added, "context-added layer-after"),
        ):
            for geometry in geometries:
                if view.overlaps(positions(geometry)):
                    marks += shape_marks(view, geometry, css)
    return marks


def scale_bar(view: View) -> str:
    """A bar about a fifth of the view wide, at a round number of metres."""
    target = view.width_m / 5
    length = min(SCALE_BAR_STEPS_M, key=lambda step: abs(step - target))
    px = length * view.scale
    x, y = 16, view.height - 14
    return (
        f'<g class="scale"><path d="M{x},{y - 5} V{y} H{number(x + px)} V{y - 5}"/>'
        f'<text x="{number(x + px / 2)}" y="{y - 8}">{length} m</text></g>'
    )


def area_label(view: View, number_shown: int, area: list[PlaceChange]) -> str:
    """The area's number, at its northernmost point, matching its close-up."""
    found = [p for change in area for p in positions(area_geometry(change))]
    x, y = view.xy(min(found, key=lambda p: (-p[1], p[0])))
    return (
        f'<g class="area-number"><circle cx="{number(x)}" cy="{number(y - 14)}" '
        f'r="10"/><text x="{number(x)}" y="{number(y - 10)}">{number_shown}</text></g>'
    )


def svg(
    view: View,
    label: str,
    after: State,
    shown: list[PlaceChange],
    numbered: list[list[PlaceChange]] = (),
    context: tuple[ContextChange, ...] = (),
) -> str:
    places = [
        place_marks(view, change)
        for change in shown
        if view.overlaps(positions(change.before_shape) + positions(change.after_shape))
    ]
    labels = [area_label(view, i, area) for i, area in enumerate(numbered, 1)]
    return (
        f'<svg viewBox="0 0 {view.width} {view.height}" role="img" '
        f'aria-label="{escape(label)}">'
        f"{''.join(background(view, after))}{''.join(context_marks(view, context))}"
        f"{''.join(places)}{''.join(labels)}"
        f"{scale_bar(view)}</svg>"
    )


def area_points(area: list[PlaceChange]) -> list:
    return [p for change in area for p in positions(area_geometry(change))]


def point_moved_m(change: PlaceChange) -> float | None:
    """How far a point place moved, or `None` if it is not a point that moved.

    A point has no length, so its move is a distance, not metres of outline
    dropped and added.
    """
    before, after = change.before_shape, change.after_shape
    if change.move is None or not change.move.visible:
        return None
    if before is None or after is None:
        return None
    if before["type"] != "Point" or after["type"] != "Point":
        return None
    return distance_m(before["coordinates"], after)


def area_caption(number_shown: int, area: list[PlaceChange]) -> str:
    """The area's places, and how much was dropped, added or moved."""
    facts = []
    added = sum(c.kind == "added" for c in area)
    removed = sum(c.kind == "removed" for c in area)
    if added:
        facts.append(f"{added} {'place' if added == 1 else 'places'} added")
    if removed:
        facts.append(f"{removed} {'place' if removed == 1 else 'places'} removed")
    point_moves = [m for m in map(point_moved_m, area) if m is not None]
    if len(point_moves) == 1:
        facts.append(f"moved {metres(point_moves[0])}")
    elif point_moves:
        facts.append(
            f"{len(point_moves)} places moved, up to {metres(max(point_moves))}"
        )
    moves = [
        c.move
        for c in area
        if c.move is not None and c.move.visible and point_moved_m(c) is None
    ]
    if moves:
        dropped = sum(move.dropped_m for move in moves)
        gained = sum(move.added_m for move in moves)
        facts.append(f"{metres(dropped)} dropped, {metres(gained)} added")
    said = "; ".join(facts)
    return (
        f"<figcaption><strong>{number_shown}. {escape(names_in(area))}.</strong> "
        f"{escape(said[:1].upper() + said[1:])}.</figcaption>"
    )


def closeups(groups: list[list[PlaceChange]], shown, after: State) -> str:
    """A close-up per area, up to `MAX_CLOSEUPS`.

    A single area gets none: the overview is already framed on it.
    """
    if len(groups) <= 1:
        return ""
    figures = []
    for i, area in enumerate(groups[:MAX_CLOSEUPS], 1):
        view = View(area_points(area), CLOSEUP_PAD_M, CLOSEUP_WIDTH)
        drawing = svg(view, f"Close-up of area {i}: {names_in(area)}", after, shown)
        figures.append(
            f'<figure class="closeup">{drawing}{area_caption(i, area)}</figure>'
        )
    rest = len(groups) - MAX_CLOSEUPS
    more = ""
    if rest > 0:
        word = "area" if rest == 1 else "areas"
        more = f'<p class="more">… and {rest} more {word}, shown on the overview.</p>'
    return f'<div class="closeups">{"".join(figures)}</div>{more}'


def list_items(value: object) -> list[object]:
    """A list field's items; categories arrive as a dict of flags."""
    if isinstance(value, dict):
        return list(value)
    if isinstance(value, list):
        return value
    return [] if value is None else [value]


def item_html(item: object) -> str:
    """A list item: a labelled link as a link, anything else as text."""
    if isinstance(item, dict) and "label" in item:
        href = item.get("href")
        if isinstance(href, str) and href.startswith(("https://", "http://")):
            return f'<a href="{escape(href)}">{escape(item["label"])}</a>'
        return escape(item["label"])
    if isinstance(item, str):
        return escape(item)
    return escape(json.dumps(item, sort_keys=True))


def list_change(field: FieldChange) -> str:
    """The items removed and added, counting repeats; a list whose items are
    all still there, the same number of times, can only have been reordered."""

    def counted(value):
        return collections.Counter(
            json.dumps(i, sort_keys=True) for i in list_items(value)
        )

    old, new = counted(field.before), counted(field.after)
    removed = [
        f'<li class="del">{item_html(json.loads(k))}</li>'
        for k in (old - new).elements()
    ]
    added = [
        f'<li class="ins">{item_html(json.loads(k))}</li>'
        for k in (new - old).elements()
    ]
    if not removed and not added:
        return "<p>The same items, in a new order.</p>"
    return f'<ul class="items">{"".join(removed + added)}</ul>'


def dict_change(field: FieldChange) -> str:
    """Each key whose value changed, before and after."""
    old, new = field.before or {}, field.after or {}
    rows = [
        f"<li>{escape(key)}: {plain(old.get(key))} → {plain(new.get(key))}</li>"
        for key in sorted(old.keys() | new.keys())
        if old.get(key) != new.get(key)
    ]
    return f'<ul class="items">{"".join(rows)}</ul>'


def prose(value: object) -> str:
    """Prose as one string; a list of rules reads as rules separated by dots."""
    if isinstance(value, list):
        return " · ".join(str(item) for item in value)
    return "" if value is None else str(value)


def prose_change(field: FieldChange) -> str:
    tags = {"removed": "del", "added": "ins"}
    runs = [
        f"<{tags[kind]}>{escape(words)}</{tags[kind]}>"
        if kind in tags
        else escape(words)
        for kind, words in word_diff(prose(field.before), prose(field.after))
    ]
    return f'<p class="prose">{" ".join(runs)}</p>'


def plain(value: object) -> str:
    if value is None:
        return '<span class="none">none</span>'
    if isinstance(value, str):
        return escape(value)
    return escape(json.dumps(value, sort_keys=True))


def length_change(field: FieldChange) -> str:
    def shown(value):
        return '<span class="none">none</span>' if value is None else metres(value)

    text = f"{shown(field.before)} → {shown(field.after)}"
    if field.before is not None and field.after is not None:
        text += f" ({round(field.after - field.before):+,} m)"
    return f"<p>{text}</p>"


def geometry_change(change: PlaceChange) -> str:
    move = change.move
    if move is None or not move.visible:
        return f"<p>Redrawn slightly: no part moved more than {MOVE_REACH_M:g} m.</p>"
    distance = point_moved_m(change)
    if distance is not None:
        return f"<p>Moved {metres(distance)}.</p>"
    return f"<p>{metres(move.dropped_m)} dropped, {metres(move.added_m)} added.</p>"


def field_html(change: PlaceChange, field: FieldChange) -> str:
    """One changed field, shown in the way that suits its kind of value."""
    if field.field == "geometry":
        body = geometry_change(change)
    elif field.field in PROSE_FIELDS:
        body = prose_change(field)
    elif field.field == "length":
        body = length_change(field)
    elif field.field not in FLAG_FIELDS and any(
        isinstance(v, dict) for v in (field.before, field.after)
    ):
        body = dict_change(field)
    elif any(isinstance(v, list | dict) for v in (field.before, field.after)):
        body = list_change(field)
    else:
        body = f"<p>{plain(field.before)} → {plain(field.after)}</p>"
    label = FIELD_LABELS.get(field.field, field.field.replace("_", " ").capitalize())
    return f"<dt>{escape(label)}</dt><dd>{body}</dd>"


def layer_words(change: PlaceChange) -> str:
    return ", ".join(slug.replace("-", " ") for slug in change.categories) or "place"


def card(change: PlaceChange) -> str:
    ids = escape(place_id(change))
    if change.kind == "renamed":
        ids = f"{escape(change.before_id)} → {escape(change.after_id)}"
    # A renamed place's ids are already in its heading line.
    fields = "".join(
        field_html(change, field) for field in change.fields if field.field != "id"
    )
    details = f"<dl>{fields}</dl>" if fields else ""
    return (
        f'<article class="card" data-place="{escape(place_id(change))}" tabindex="0">'
        f"<h3>{escape(change.name)}</h3>"
        f'<p class="meta"><code>{ids}</code> · {escape(layer_words(change))}</p>'
        f"{details}</article>"
    )


def cards(changes: ChangeSet) -> str:
    sections = []
    for kind, heading in KIND_HEADINGS.items():
        members = [c for c in changes.places if c.kind == kind]
        if members:
            sections.append(
                f'<section class="kind"><h2>{heading} '
                f'<span class="count">{len(members)}</span></h2>'
                f'<div class="cards">{"".join(card(c) for c in members)}</div>'
                "</section>"
            )
    return "".join(sections)


def context_note(change: ContextChange) -> str:
    title = LAYER_TITLES.get(change.slug, change.slug.replace("-", " ").capitalize())
    parts = []
    for count, verb in ((len(change.added), "added"), (len(change.removed), "removed")):
        if count:
            parts.append(f"{count} {'shape' if count == 1 else 'shapes'} {verb}")
    return f"<li>{escape(title)} layer: {', '.join(parts)}.</li>"


def notes(changes: ChangeSet) -> str:
    items = [context_note(change) for change in changes.context]
    if changes.routing_changed:
        items.append("<li>Walking directions were rebuilt from the new paths.</li>")
    if not items:
        return ""
    return (
        '<section class="notes"><h2>Also changed</h2>'
        f"<ul>{''.join(items)}</ul></section>"
    )


def counts(changes: ChangeSet) -> str:
    """Tiles counting places by kind; none when no place changed, rather than
    four zeros above a map of background changes counted in the notes."""
    if not changes.places:
        return ""
    tiles = [
        f'<div class="tile"><span class="n">'
        f"{sum(c.kind == kind for c in changes.places)}</span>{heading.lower()}</div>"
        for kind, heading in KIND_HEADINGS.items()
    ]
    return f'<div class="counts">{"".join(tiles)}</div>'


def context_points(context: tuple[ContextChange, ...]) -> list:
    return [
        p
        for change in context
        for geometry in (*change.removed, *change.added)
        for p in positions(geometry)
    ]


def overview(
    groups: list[list[PlaceChange]],
    shown,
    context: tuple[ContextChange, ...],
    after: State,
) -> str:
    """Every changed place on one map, with the background shapes that changed
    within it.

    Framed on the places when any changed: a background layer re-cut across
    the whole campus would otherwise shrink them into a corner. Only a change
    to the background alone is framed on the background.
    """
    if not groups and not context:
        return (
            '<p class="quiet">Nothing moved on the map: every change is to a '
            "place's details.</p>"
        )
    framed_on = [p for area in groups for p in area_points(area)]
    view = View(framed_on or context_points(context), OVERVIEW_PAD_M, OVERVIEW_WIDTH)
    # One area needs no number: there is no close-up to match it to.
    numbered = groups if len(groups) > 1 else []
    label = "Overview of every changed place" if groups else "Overview of the changes"
    drawing = svg(view, label, after, shown, numbered, context)
    sentences = []
    if groups:
        caption = "Every place that moved, was added or was removed"
        if len(groups) > MAX_CLOSEUPS:
            caption += (
                f", in {len(groups)} numbered areas; the first {MAX_CLOSEUPS} have "
                "close-ups below"
            )
        elif numbered:
            caption += f", in {len(groups)} numbered areas; each has a close-up below"
        sentences.append(f"{caption}.")
    if context:
        sentences.append(
            "Faint lines are background shapes that appeared or disappeared."
        )
    return (
        f'<figure class="overview">{drawing}{legend(bool(context))}'
        f"<figcaption><strong>Overview.</strong> {' '.join(sentences)}"
        "</figcaption></figure>"
    )


def legend(with_context: bool) -> str:
    faint = (
        '<span><i class="key faint dropped"></i><i class="key faint added"></i>'
        "background shapes gone and new</span>"
        if with_context
        else ""
    )
    return (
        '<div class="legend">'
        '<span><i class="key before"></i>before</span>'
        '<span><i class="key after"></i>after</span>'
        '<span><i class="key dropped"></i>dropped: no longer there</span>'
        '<span><i class="key added"></i>added: not there before</span>'
        f"{faint}"
        '<span><i class="key building"></i>buildings, walkways and water</span>'
        "</div>"
    )


CONTROLS = (
    '<div class="controls" role="group" aria-label="Show">'
    '<label><input type="checkbox" id="show-before" checked> Before (dashed)</label>'
    '<label><input type="checkbox" id="show-after" checked> After (solid)</label>'
    "</div>"
)


def render(changes: ChangeSet, after: State, date: str) -> str:
    """The whole report page for `changes`, dated `date`.

    The changes carry every before shape the page draws, and the background is
    drawn from `after`, the campus as it will be published.
    """
    groups = areas(changes)
    shown = [change for area in groups for change in area]
    title = f"Campus data changes, {escape(date)}"
    maps = overview(groups, shown, changes.context, after)
    if groups or changes.context:
        maps = CONTROLS + maps + closeups(groups, shown, after)
    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{title}</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Public+Sans:wght@400;600;700&amp;family=IBM+Plex+Mono:wght@400;500&amp;display=swap">
<style>{STYLE}</style>
</head>
<body>
<main id="sheet">
<header class="lede">
<div class="eyebrow">St. Olaf campus map data</div>
<h1>{title}</h1>
<p>{escape(summary(changes))}</p>
{counts(changes)}
</header>
{maps}
{cards(changes)}
{notes(changes)}
</main>
<script>{SCRIPT}</script>
</body>
</html>
"""


# Light on `:root`; dark when the reader's system asks for it, unless the page
# is pinned light, and whenever it is pinned dark.
DARK_TOKENS = """
  --bg: #161a17; --panel: #1d221e; --fg: #e6e9e3; --muted: #9aa49b;
  --rule: #313830; --walk: #3a4139; --building: #2a302b; --water: #22343b;
  --after: #7fa6ea; --before: #9aa49b; --dropped: #ff6fa3; --added: #ffffff;
  --del-bg: #4a2232; --ins-bg: #1f3a2b; color-scheme: dark;"""

STYLE = (
    """
:root {
  --bg: #f3f4ef; --panel: #fbfbf8; --fg: #1f2620; --muted: #5d675e;
  --rule: #d5d9cf; --walk: #c9ccc2; --building: #e3e5dd; --water: #cfdde3;
  --after: #3a5f9e; --before: #5d675e; --dropped: #d1336b; --added: #111111;
  --del-bg: #f7d9e4; --ins-bg: #dcebe1;
  --display: "Public Sans", "Helvetica Neue", Arial, sans-serif;
  --mono: "IBM Plex Mono", ui-monospace, Menlo, monospace;
}
@media (prefers-color-scheme: dark) { :root:not([data-theme="light"]) {"""
    + DARK_TOKENS
    + """ } }
:root[data-theme="dark"] {"""
    + DARK_TOKENS
    + """ }
* { box-sizing: border-box; }
body { margin: 0; background: var(--bg); color: var(--fg);
  font: 15px/1.55 var(--display); padding: 28px 16px 48px; }
main { max-width: 1040px; margin: 0 auto; display: grid; gap: 28px; }
h1 { font-size: 1.7rem; margin: 0; letter-spacing: -0.01em; text-wrap: balance; }
h2 { font-size: 1.05rem; margin: 0 0 12px; }
h3 { font-size: 1rem; margin: 0; overflow-wrap: anywhere; }
p { margin: 0; max-width: 68ch; }
code { font: 0.82rem var(--mono); overflow-wrap: anywhere; }
.lede { display: grid; gap: 10px; }
.eyebrow { font: 500 0.72rem var(--mono); letter-spacing: 0.08em;
  text-transform: uppercase; color: var(--muted); }
.counts { display: flex; flex-wrap: wrap; gap: 8px; }
.tile { background: var(--panel); border: 1px solid var(--rule); border-radius: 6px;
  padding: 6px 12px; color: var(--muted); font-size: 0.85rem; }
.tile .n { font: 600 1.15rem var(--display); color: var(--fg); margin-right: 6px;
  font-variant-numeric: tabular-nums; }
figure { margin: 0; background: var(--panel); border: 1px solid var(--rule);
  border-radius: 6px; padding: 14px; display: grid; gap: 10px; min-width: 0; }
figcaption, .more, .quiet { color: var(--muted); font-size: 0.88rem; }
figcaption strong { color: var(--fg); }
svg { width: 100%; height: auto; display: block; }
.closeups { display: grid; gap: 16px;
  grid-template-columns: repeat(auto-fit, minmax(min(300px, 100%), 1fr)); }
.controls { display: flex; flex-wrap: wrap; gap: 8px 18px; font-size: 0.9rem; }
.controls label { display: inline-flex; gap: 6px; align-items: center; cursor: pointer; }
.controls input:focus-visible { outline: 2px solid var(--fg); outline-offset: 2px; }
.legend { display: flex; flex-wrap: wrap; gap: 6px 18px; font-size: 0.85rem;
  color: var(--muted); }
.legend span { display: inline-flex; gap: 6px; align-items: center; }
.key { width: 26px; height: 0; display: inline-block; border-top: 4px solid; }
.key.before { border-top: 3px dashed var(--before); }
.key.after { border-color: var(--after); }
.key.dropped { border-color: var(--dropped); border-top-width: 6px; }
.key.added { border-color: var(--added); border-top-width: 3px; }
.key.building { border-color: var(--walk); border-top-width: 6px; }
.key.faint { width: 13px; border-top-width: 2px; opacity: 0.55; }
.water { fill: var(--water); stroke: none; }
.walkway { fill: none; stroke: var(--walk); stroke-width: 1.2; }
.building { fill: var(--building); stroke: var(--walk); stroke-width: 1; }
.before { fill: none; stroke: var(--before); stroke-width: 2.5;
  stroke-dasharray: 5 5; opacity: 0.85; }
.after { fill: none; stroke: var(--after); stroke-width: 4; opacity: 0.8;
  stroke-linecap: round; stroke-linejoin: round; }
.dropped { fill: none; stroke: var(--dropped); stroke-width: 6; opacity: 0.9;
  stroke-linecap: round; stroke-linejoin: round; }
.added { fill: none; stroke: var(--added); stroke-width: 2.5;
  stroke-linecap: round; stroke-linejoin: round; }
.context-dropped, .context-added { fill: none; stroke-width: 1.5; opacity: 0.55;
  stroke-linecap: round; stroke-linejoin: round; }
.context-dropped { stroke: var(--dropped); }
.context-added { stroke: var(--added); }
circle.context-dropped { fill: var(--dropped); stroke: none; }
circle.context-added { fill: var(--added); stroke: none; }
circle.after { fill: var(--after); }
circle.added { fill: var(--added); stroke: none; r: 2px; }
.place { cursor: pointer; transition: opacity 0.15s; }
.focusing .place:not(.hl) { opacity: 0.2; }
.place.hl .after, .place.hl .before { stroke-width: 6; opacity: 1; }
.scale text, .area-number text { font: 500 12px var(--mono); fill: var(--fg);
  text-anchor: middle; }
.scale path { fill: none; stroke: var(--fg); stroke-width: 1.5; }
.area-number circle { fill: var(--panel); stroke: var(--fg); stroke-width: 1.5; }
.hide-before .layer-before, .hide-after .layer-after { display: none; }
.kind .count { color: var(--muted); font-weight: 400; }
.cards { display: grid; gap: 12px;
  grid-template-columns: repeat(auto-fill, minmax(min(300px, 100%), 1fr)); }
.card { background: var(--panel); border: 1px solid var(--rule); border-radius: 6px;
  padding: 12px 14px; display: grid; gap: 8px; align-content: start; min-width: 0; }
.card.hl, .card:focus-visible { border-color: var(--fg); outline: none;
  box-shadow: 0 0 0 1px var(--fg); }
.meta { color: var(--muted); font-size: 0.82rem; }
dl { margin: 0; display: grid; gap: 6px; }
dt { font: 500 0.72rem var(--mono); letter-spacing: 0.06em; text-transform: uppercase;
  color: var(--muted); }
dd { margin: 0; font-size: 0.9rem; overflow-wrap: anywhere; }
del { background: var(--del-bg); text-decoration: line-through; }
ins { background: var(--ins-bg); text-decoration: none; }
.items { margin: 0; padding: 0; list-style: none; display: grid; gap: 2px; }
.items .del::before { content: "− "; color: var(--dropped); }
.items .ins::before { content: "+ "; color: var(--muted); }
.items .del { text-decoration: line-through; }
.none { color: var(--muted); font-style: italic; }
a { color: var(--after); }
.notes ul { margin: 8px 0 0; padding-left: 20px; }
"""
)

# Before and after toggle the two layers; pointing at a place on a map or at its
# card highlights it everywhere it appears, and clicking a shape finds its card.
# A focused card keeps its place highlighted once the pointer leaves, and a
# place with nothing on the map highlights its card without dimming the map.
SCRIPT = """
const sheet = document.getElementById("sheet");
for (const [id, cls] of [["show-before", "hide-before"], ["show-after", "hide-after"]]) {
  const box = document.getElementById(id);
  if (box) box.addEventListener("change", () => sheet.classList.toggle(cls, !box.checked));
}
const marked = document.querySelectorAll("[data-place]");
const drawn = new Set([...document.querySelectorAll(".place")].map((el) => el.dataset.place));
let hovered = null;
let focused = null;
// Leaving a card or shape waits this long before letting go of it, so crossing
// the gap to the next one goes straight from one highlight to the other instead
// of undimming the map for a few frames; stopping on the background still
// lets go.
const LET_GO_MS = 100;
let letGo = null;
function highlight() {
  const place = hovered ?? focused;
  sheet.classList.toggle("focusing", drawn.has(place));
  for (const el of marked) el.classList.toggle("hl", el.dataset.place === place);
}
for (const el of marked) {
  const place = el.dataset.place;
  el.addEventListener("mouseenter", () => {
    clearTimeout(letGo);
    hovered = place;
    highlight();
  });
  el.addEventListener("mouseleave", () => {
    clearTimeout(letGo);
    letGo = setTimeout(() => { hovered = null; highlight(); }, LET_GO_MS);
  });
  el.addEventListener("focus", () => { focused = place; highlight(); });
  el.addEventListener("blur", () => { focused = null; highlight(); });
  if (el.classList.contains("place")) {
    el.addEventListener("click", () => {
      const card = [...document.querySelectorAll(".card")]
        .find((c) => c.dataset.place === place);
      if (card) { card.scrollIntoView({ block: "center" }); card.focus(); }
    });
  }
}
"""

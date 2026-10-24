"""Tests for reading a state of the published files from git or the working tree."""

import json
import subprocess
from pathlib import Path

from changes import read_state

POLYGON = {
    "type": "Polygon",
    "coordinates": [[[0, 0], [0, 1], [1, 1], [0, 0]]],
}
LABEL = {"type": "Point", "coordinates": [0.5, 0.5]}


def git(root: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(root), *args], check=True, capture_output=True)


def write_json(root: Path, path: str, data) -> None:
    (root / path).parent.mkdir(parents=True, exist_ok=True)
    (root / path).write_text(json.dumps(data))


def feature(place_id: str, geometries: list[dict]) -> dict:
    return {
        "type": "Feature",
        "id": place_id,
        "geometry": {"type": "GeometryCollection", "geometries": geometries},
        "properties": {},
    }


def make_repo(root: Path, name: str = "Old Main", *, walkways: int = 2) -> None:
    git(root, "init", "-q")
    git(root, "config", "user.name", "Test")
    git(root, "config", "user.email", "test@example.com")
    write_json(root, "map.json", [{"id": "om", "name": name}])
    write_json(
        root,
        "map.geojson",
        {
            "type": "FeatureCollection",
            "features": [
                feature("om", [POLYGON, LABEL]),
                feature("bike-rack-x", [LABEL]),
            ],
        },
    )
    write_json(
        root,
        "data/walkways.geojson",
        {
            "type": "FeatureCollection",
            "features": [
                {"type": "Feature", "geometry": POLYGON, "properties": {}}
                for _ in range(walkways)
            ],
        },
    )
    git(root, "add", ".")
    git(root, "commit", "-q", "-m", "init")


def test_a_revision_reads_the_files_as_committed(tmp_path):
    make_repo(tmp_path)
    write_json(tmp_path, "map.json", [{"id": "om", "name": "Old Main Hall"}])
    assert read_state(tmp_path, "HEAD").places["om"]["name"] == "Old Main"
    assert read_state(tmp_path, None).places["om"]["name"] == "Old Main Hall"


def test_a_place_shape_is_its_geometry_not_its_label_point(tmp_path):
    make_repo(tmp_path)
    assert read_state(tmp_path, None).shapes["om"]["type"] == "Polygon"


def test_a_point_place_shape_is_its_point(tmp_path):
    make_repo(tmp_path)
    assert read_state(tmp_path, None).shapes["bike-rack-x"]["type"] == "Point"


def test_context_holds_the_context_layers(tmp_path):
    make_repo(tmp_path)
    assert len(read_state(tmp_path, None).context["walkways"]) == 2


def test_a_file_missing_at_a_revision_reads_as_empty(tmp_path):
    make_repo(tmp_path)
    state = read_state(tmp_path, "HEAD")
    assert state.context["campus-roads"] == []
    assert state.routing == b""

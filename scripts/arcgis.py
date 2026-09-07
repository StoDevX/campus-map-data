"""A small, deterministic ArcGIS Feature Service client.

Everything this repo scrapes comes from one ArcGIS Online organisation
(`stolaf.maps.arcgis.com`) through the public Feature Service REST API. The
services are shared publicly and advertise `Query` capability, so no token is
involved and no credential belongs in this repo.

Two properties matter more than speed here, because the output is committed and
its diff is the change log:

1. **Every feature, every time.** A Feature Service silently truncates a query
   at `maxRecordCount` and sets `exceededTransferLimit` to say so. The layers
   here are small (138 features at the largest, against a limit of 2000) but a
   truncated scrape that looked successful would quietly delete features from
   the committed data, so pagination is implemented rather than assumed.

2. **No incidental churn.** Coordinates arrive at full float precision and
   feature order is whatever the service feels like. Both are normalised in
   `scrape.py`; this module's job is just to hand back the raw features.
"""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.parse
import urllib.request

USER_AGENT = "StoDevX/campus-map-data (+https://github.com/StoDevX/campus-map-data)"

# Retries exist for the scheduled run, which has nobody watching it. ArcGIS
# Online returns 5xx and connection resets often enough that a single transient
# failure should not fail a build; it should not mask a real outage either, so
# the ceiling is low and the failure is loud.
RETRIES = 4
BACKOFF_SECONDS = 2.0


class ArcGISError(RuntimeError):
    pass


def _get(url: str, params: dict[str, str]) -> dict:
    query = urllib.parse.urlencode(params)
    full = f"{url}?{query}"
    last: Exception | None = None

    for attempt in range(RETRIES):
        if attempt:
            time.sleep(BACKOFF_SECONDS * (2 ** (attempt - 1)))
        request = urllib.request.Request(full, headers={"User-Agent": USER_AGENT})
        try:
            with urllib.request.urlopen(request, timeout=60) as response:
                body = response.read()
        except (urllib.error.URLError, TimeoutError, OSError) as error:
            last = error
            continue

        try:
            payload = json.loads(body)
        except json.JSONDecodeError as error:
            # A Feature Service answers a bad layer id with an HTML error page
            # and a 200, so this is a real and non-obvious failure mode.
            last = ArcGISError(f"{full} did not return JSON: {error}")
            continue

        # The other one: ArcGIS reports application errors *inside* a 200.
        if isinstance(payload, dict) and "error" in payload:
            message = payload["error"].get("message", payload["error"])
            raise ArcGISError(f"{full}: {message}")

        return payload

    raise ArcGISError(f"{full} failed after {RETRIES} attempts: {last}")


def layer_metadata(layer_url: str) -> dict:
    """The layer's own description — name, geometry type, field list."""
    return _get(layer_url, {"f": "json"})


def query_features(layer_url: str) -> list[dict]:
    """Every feature in a layer, as GeoJSON features in WGS84.

    `f=geojson` and `outSR=4326` do the projection server-side: the services
    store Web Mercator (EPSG:3857), and reprojecting here would mean carrying a
    geodesy dependency to reproduce what the service already does exactly.
    """
    features: list[dict] = []
    offset = 0

    while True:
        page = _get(
            f"{layer_url}/query",
            {
                "where": "1=1",
                "outFields": "*",
                "outSR": "4326",
                "f": "geojson",
                "resultOffset": str(offset),
                # Below every layer's maxRecordCount, so the service decides the
                # real page size and `exceededTransferLimit` stays meaningful.
                "resultRecordCount": "1000",
            },
        )
        batch = page.get("features") or []
        features.extend(batch)

        # `exceededTransferLimit` is the service saying "there is more". Some
        # layers report it as a sibling of `features`, others (GeoJSON output
        # especially) omit it entirely and just return a short page.
        if not page.get("exceededTransferLimit") or not batch:
            break
        offset += len(batch)

    return features

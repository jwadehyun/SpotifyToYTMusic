"""Reads Spotify playlists/albums without an API key, via the public embed page.

The embed page only includes the first 100 tracks; Spotify rejects its anonymous
token for anything more (QUOTA_EXCEEDED), so longer playlists are cut off.
"""

import json
import re

import requests

EMBED_LIMIT = 100
_URL_RE = re.compile(r"(playlist|album)[/:]([A-Za-z0-9]{22})")
_NEXT_DATA_RE = re.compile(r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>', re.S)


class SpotifyError(Exception):
    pass


def parse_link(link: str) -> tuple[str, str]:
    link = link.strip()
    m = _URL_RE.search(link)
    if m:
        return m.group(1), m.group(2)
    if re.fullmatch(r"[A-Za-z0-9]{22}", link):
        return "playlist", link
    raise SpotifyError("That doesn't look like a Spotify playlist or album link.")


def fetch(link: str) -> dict:
    kind, spotify_id = parse_link(link)
    try:
        res = requests.get(
            f"https://open.spotify.com/embed/{kind}/{spotify_id}",
            headers={"User-Agent": "Mozilla/5.0", "Accept-Language": "en"},
            timeout=15,
        )
    except requests.RequestException as e:
        raise SpotifyError(f"Couldn't reach Spotify: {e}") from e
    if res.status_code == 404:
        raise SpotifyError("Spotify couldn't find that playlist. Is it public?")
    if not res.ok:
        raise SpotifyError(f"Spotify returned an error ({res.status_code}).")

    m = _NEXT_DATA_RE.search(res.text)
    try:
        entity = json.loads(m.group(1))["props"]["pageProps"]["state"]["data"]["entity"]
    except (AttributeError, KeyError, TypeError, json.JSONDecodeError) as e:
        raise SpotifyError("Spotify changed its page format; the playlist couldn't be read.") from e

    tracks = []
    for t in entity.get("trackList") or []:
        if t.get("entityType", "track") != "track":
            continue  # podcast episodes can't be matched to songs
        subtitle = (t.get("subtitle") or "").replace(" ", " ")
        tracks.append(
            {
                "id": (t.get("uri") or "").split(":")[-1],
                "title": t.get("title") or "",
                # Artists come joined by ", ". Names with commas ("Tyler, The Creator")
                # get split too, so matching also checks against the full string.
                "artists": [a.strip() for a in subtitle.split(",") if a.strip()],
                "artistText": subtitle,
                "durationMs": t.get("duration") or 0,
                "explicit": bool(t.get("isExplicit")),
            }
        )

    sources = (entity.get("coverArt") or {}).get("sources") or []
    return {
        "kind": kind,
        "id": spotify_id,
        "name": entity.get("name") or entity.get("title") or "Spotify playlist",
        "owner": entity.get("subtitle") or "",
        "image": max(sources, key=lambda s: s.get("width") or 0)["url"] if sources else None,
        "tracks": tracks,
        "maybeTruncated": len(entity.get("trackList") or []) >= EMBED_LIMIT,
    }

"""Finds the YouTube Music equivalent of a Spotify track.

Each candidate is scored on title, artist and duration, and penalized when its
version differs (live, remix, sped up, ...). Official "song" results are tried
first; music videos are the fallback when no song matches.
"""

import re
import unicodedata
from difflib import SequenceMatcher

from ytmusicapi import YTMusic

# Words that mark a different recording from the original.
VERSION_TAGS = [
    "live", "acoustic", "remix", "instrumental", "karaoke", "sped up", "slowed", "reverb",
    "piano", "cover", "demo", "nightcore", "8d", "lofi", "lo-fi", "orchestral", "a cappella",
    "extended", "unplugged", "inst", "re recorded", "re record", "rerecorded",
]
_FEAT_RE = re.compile(r"[\(\[]\s*(feat|ft|with|prod)\.?\s[^\)\]]*[\)\]]|\s-?\s*\b(feat|ft)\.?\s.*$", re.I)
# Spotify suffixes like " - Remastered 2011" / " - 2015 Remaster" / " - Radio Edit"
_REMASTER_RE = re.compile(r"[\(\[\-]\s*(\d{4}\s*)?(digital(ly)?\s*)?remaster(ed)?(\s*\d{4})?(\s*version)?\s*[\)\]]?", re.I)
# Music video title junk
_VIDEO_JUNK_RE = re.compile(
    r"[\(\[\|【]\s*(official|offical|m/?v|music video|lyric|lyrics|audio|visuali[sz]er|hd|4k|mv|performance video|video)[^\)\]】]*[\)\]】]?"
    r"|\b(official\s+(music\s+)?(video|audio|mv)|m/v|lyric video|lyrics)\b",
    re.I,
)


def norm(s: str) -> str:
    s = unicodedata.normalize("NFKD", s)
    s = "".join(c for c in s if not unicodedata.combining(c)).lower()
    s = s.replace("&", " and ").replace("’", "'")
    s = re.sub(r"[^\w\s]", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def core_title(title: str) -> str:
    t = _FEAT_RE.sub(" ", title)
    t = _REMASTER_RE.sub(" ", t)
    return norm(t)


def version_tags(title: str) -> set[str]:
    n = f" {norm(title)} "
    return {tag for tag in VERSION_TAGS if f" {norm(tag)} " in n}


def title_similarity(a: str, b: str) -> float:
    if not a or not b:
        return 0.0
    if a == b:
        return 1.0
    ratio = SequenceMatcher(None, a, b).ratio()
    # "song" vs "song from the movie x": containment is a strong signal
    if f" {a} " in f" {b} " or f" {b} " in f" {a} ":
        ratio = max(ratio, 0.88)
    return ratio


def artist_match(track: dict, names: list[str]) -> tuple[bool, bool]:
    """Returns (primary artist matched, any artist matched)."""
    yt = [norm(n) for n in names if n]
    sp = [norm(a) for a in track["artists"]]
    full = norm(track["artistText"])
    if not yt or not sp:
        return False, False

    def same(a: str, b: str) -> bool:
        return a == b or (len(a) > 2 and len(b) > 2 and (f" {a} " in f" {b} " or f" {b} " in f" {a} "))

    primary = any(same(sp[0], y) for y in yt)
    any_match = primary or any(same(s, y) for s in sp for y in yt) or any(len(y) > 2 and f" {y} " in f" {full} " for y in yt)
    return primary, any_match


def _candidate(r: dict, kind: str) -> dict:
    thumbs = r.get("thumbnails") or []
    return {
        "videoId": r["videoId"],
        "title": r.get("title") or "",
        "artists": [a["name"] for a in (r.get("artists") or []) if a.get("name")],
        "album": (r.get("album") or {}).get("name"),
        "duration": r.get("duration_seconds"),
        "explicit": r.get("isExplicit"),
        "type": kind,
        "thumbnail": thumbs[-1]["url"] if thumbs else None,
    }


def score_song(track: dict, c: dict) -> tuple[float, bool]:
    sp_title = core_title(track["title"])
    yt_title = core_title(c["title"])
    tsim = title_similarity(sp_title, yt_title)
    primary, any_artist = artist_match(track, c["artists"])
    tag_mismatch = version_tags(track["title"]) != version_tags(c["title"])
    diff = abs((c["duration"] or 0) - track["durationMs"] / 1000) if c["duration"] and track["durationMs"] else None

    score = tsim * 50
    score += 30 if primary else 15 if any_artist else 0
    if diff is not None:
        score += 20 if diff <= 3 else 12 if diff <= 8 else 4 if diff <= 20 else -10
    if tag_mismatch:
        score -= 30
    if c["explicit"] is not None and c["explicit"] == track["explicit"]:
        score += 3

    c["durationDiff"] = diff
    exact = tsim >= 0.85 and primary and not tag_mismatch and (diff is None or diff <= 5)
    return score, exact


def score_video(track: dict, c: dict) -> tuple[float, bool]:
    # Video titles are usually "Artist - Title (Official Video)".
    raw = _VIDEO_JUNK_RE.sub(" ", c["title"])
    title_n = core_title(raw)
    artist_in_title = False
    for a in track["artists"] + [track["artistText"]]:
        a_n = norm(a)
        if a_n and f" {a_n} " in f" {title_n} ":
            title_n = f" {title_n} ".replace(f" {a_n} ", " ").strip()
            artist_in_title = True
    tsim = title_similarity(core_title(track["title"]), title_n)
    primary, any_artist = artist_match(track, c["artists"])
    tag_mismatch = version_tags(track["title"]) != version_tags(raw)
    diff = abs((c["duration"] or 0) - track["durationMs"] / 1000) if c["duration"] and track["durationMs"] else None

    score = tsim * 50
    score += 30 if primary else 20 if (any_artist or artist_in_title) else 0
    # Music videos often have intros/outros, so length counts for less.
    if diff is not None:
        score += 10 if diff <= 10 else 4 if diff <= 45 else 0
    if tag_mismatch:
        score -= 30

    good = tsim >= 0.8 and (primary or any_artist or artist_in_title) and not tag_mismatch
    return score, good


class Matcher:
    def __init__(self):
        self.yt = YTMusic()  # searching doesn't need an account

    def _search(self, query: str, kind: str, limit: int) -> list[dict]:
        results = self.yt.search(query, filter=f"{kind}s", limit=limit)
        return [_candidate(r, kind) for r in results if r.get("videoId") and r.get("resultType") == kind][:limit]

    def match(self, track: dict) -> dict:
        title = core_title(track["title"]) or track["title"]
        primary_artist = track["artists"][0] if track["artists"] else ""
        songs: dict[str, dict] = {}

        def add_songs(query: str):
            for c in self._search(query, "song", 8):
                c["score"], c["exact"] = score_song(track, c)
                songs.setdefault(c["videoId"], c)

        add_songs(f"{track['title']} {track['artistText']}")
        best = max(songs.values(), key=lambda c: c["score"], default=None)
        if not (best and best["exact"]):
            # A simpler query often finds songs whose title has extra Spotify-only text.
            add_songs(f"{title} {primary_artist}")

        ranked_songs = sorted(songs.values(), key=lambda c: c["score"], reverse=True)
        best = ranked_songs[0] if ranked_songs else None

        if best and best["exact"]:
            return {"status": "exact", "choice": best["videoId"], "candidates": ranked_songs[:6]}

        videos = []
        for c in self._search(f"{title} {primary_artist}", "video", 5):
            c["score"], c["exact"] = score_video(track, c)
            videos.append(c)
        videos.sort(key=lambda c: c["score"], reverse=True)
        candidates = ranked_songs[:6] + videos[:4]

        # A song with a very different length is likely an extended/edited cut.
        if best and best["score"] >= 70 and (best["durationDiff"] is None or best["durationDiff"] <= 20):
            return {"status": "likely", "choice": best["videoId"], "candidates": candidates}
        if videos and videos[0]["exact"]:
            return {"status": "video", "choice": videos[0]["videoId"], "candidates": candidates}
        return {"status": "none", "choice": None, "candidates": candidates}

import html
import json
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

import spotify
from matcher import Matcher
from youtube import AuthError, GoogleAccount, QuotaExceeded

ROOT = Path(__file__).parent
PORT = 8765
ALLOWED_HOSTS = {f"localhost:{PORT}", f"127.0.0.1:{PORT}"}

app = FastAPI(title="Spotify to YouTube Music", docs_url=None, redoc_url=None, openapi_url=None)
matcher = Matcher()
google = GoogleAccount()

CSP = (
    "default-src 'self'; img-src 'self' data: https:; style-src 'self'; script-src 'self'; "
    "connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'"
)


@app.middleware("http")
async def local_only(request: Request, call_next):
    # Rejects DNS-rebinding tricks: only requests addressed to this local server get through.
    if request.headers.get("host") not in ALLOWED_HOSTS:
        return JSONResponse({"detail": "Forbidden host"}, status_code=403)
    # Other websites can't make your browser change anything here.
    if request.method not in ("GET", "HEAD"):
        origin = request.headers.get("origin")
        if origin is None or origin.split("://", 1)[-1] not in ALLOWED_HOSTS:
            return JSONResponse({"detail": "Forbidden origin"}, status_code=403)
    response = await call_next(request)
    response.headers["Content-Security-Policy"] = CSP
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "no-referrer"
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Cache-Control"] = "no-store"
    return response


# ---------- Spotify + matching ----------


@app.get("/api/spotify")
def get_playlist(url: str):
    try:
        return spotify.fetch(url)
    except spotify.SpotifyError as e:
        raise HTTPException(400, str(e))


class Track(BaseModel):
    id: str
    title: str
    artists: list[str]
    artistText: str
    durationMs: int
    explicit: bool


# Plain `def` endpoints run in FastAPI's thread pool, so several matches run at once.
@app.post("/api/match")
def match(track: Track):
    try:
        return matcher.match(track.model_dump())
    except Exception as e:
        raise HTTPException(502, f"YouTube Music search failed: {e}")


@app.get("/api/video/{video_id}")
def lookup_video(video_id: str):
    """Details for a link pasted by hand in the review table."""
    try:
        d = matcher.yt.get_song(video_id)["videoDetails"]
    except Exception:
        raise HTTPException(404, "Couldn't find that video.")
    thumbs = (d.get("thumbnail") or {}).get("thumbnails") or []
    return {
        "videoId": video_id,
        "title": d.get("title", ""),
        "artists": [d.get("author", "")],
        "album": None,
        "duration": int(d.get("lengthSeconds") or 0) or None,
        "type": "song" if d.get("musicVideoType") == "MUSIC_VIDEO_TYPE_ATV" else "video",
        "thumbnail": thumbs[-1]["url"] if thumbs else None,
    }


# ---------- Google sign-in ----------


@app.get("/api/auth")
def auth_status():
    return {"configured": google.configured, "connected": google.connected, **(google.channel or {})}


@app.get("/oauth/start")
def oauth_start(request: Request):
    try:
        return RedirectResponse(google.start(f"http://{request.headers['host']}/oauth/callback"))
    except AuthError as e:
        return _popup_result(False, str(e))


@app.get("/oauth/callback")
def oauth_callback(state: str = "", code: str = "", error: str = ""):
    if error:
        return _popup_result(False, "Sign-in was cancelled." if error == "access_denied" else f"Sign-in failed ({error}).")
    try:
        google.finish(state, code)
    except AuthError as e:
        return _popup_result(False, str(e))
    except Exception:
        return _popup_result(False, "Sign-in failed. Please try again.")
    return _popup_result(True, "Signed in. You can close this window.")


def _popup_result(ok: bool, message: str) -> HTMLResponse:
    """Sign-in runs in a popup so the review table isn't lost; this page reports back and closes."""
    payload = html.escape(json.dumps({"type": "google-auth", "ok": ok, "message": message}), quote=True)
    return HTMLResponse(
        f"""<!doctype html><meta charset="utf-8"><title>Sign-in</title>
<link rel="stylesheet" href="/static/style.css">
<body class="popup" data-result="{payload}"><p>{html.escape(message)}</p>
<script src="/static/oauth-done.js"></script></body>"""
    )


@app.post("/api/auth/disconnect")
def disconnect():
    google.disconnect()
    return auth_status()


# ---------- Playlist creation ----------


class CreateIn(BaseModel):
    title: str = Field(max_length=150)
    description: str = Field("", max_length=5000)
    privacy: str = "PRIVATE"


@app.post("/api/playlist")
def create_playlist(body: CreateIn):
    if body.privacy not in ("PRIVATE", "UNLISTED", "PUBLIC"):
        raise HTTPException(400, "Invalid privacy setting.")
    try:
        playlist_id = google.create_playlist(body.title.strip() or "Untitled", body.description, body.privacy.lower())
    except AuthError as e:
        raise HTTPException(401, str(e))
    except QuotaExceeded:
        raise HTTPException(429, "Google's daily YouTube limit is used up. Try again tomorrow.")
    except Exception as e:
        raise HTTPException(502, f"Couldn't create the playlist: {e}")
    return {"playlistId": playlist_id, "url": f"https://music.youtube.com/playlist?list={playlist_id}"}


class AddIn(BaseModel):
    videoIds: list[str] = Field(max_length=25)


@app.post("/api/playlist/{playlist_id}/add")
def add_songs(playlist_id: str, body: AddIn):
    """Adds songs in order. The page sends small batches so it can show progress."""
    added, failed = [], []
    for i, vid in enumerate(body.videoIds):
        try:
            google.add_video(playlist_id, vid)
            added.append(vid)
        except QuotaExceeded:
            return {"added": added, "failed": failed, "quotaExceeded": True, "remaining": body.videoIds[i:]}
        except AuthError as e:
            raise HTTPException(401, str(e))
        except Exception as e:
            failed.append({"videoId": vid, "reason": str(e)})
    return {"added": added, "failed": failed, "quotaExceeded": False, "remaining": []}


app.mount("/static", StaticFiles(directory=ROOT / "static"), name="static")


@app.get("/")
def index():
    return FileResponse(ROOT / "static" / "index.html")

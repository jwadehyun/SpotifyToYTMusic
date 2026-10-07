"""Google sign-in (OAuth 2.0 with PKCE) and playlist creation via the official YouTube Data API.

Security notes:
- Tokens live only in this process's memory. Nothing is written to disk, and
  restarting the server signs you out.
- Tokens are never sent to the browser; the page only learns the channel name.
- `state` and PKCE stop forged or intercepted sign-in callbacks.
- Disconnecting revokes the token at Google, not just locally.
"""

import base64
import hashlib
import json
import secrets
import threading
import time
from pathlib import Path
from urllib.parse import urlencode

import requests

CLIENT_FILE = Path(__file__).parent / "client_secret.json"
# The narrowest scope that allows creating playlists. Google has no playlist-only scope.
SCOPE = "https://www.googleapis.com/auth/youtube"
AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_URL = "https://oauth2.googleapis.com/token"
REVOKE_URL = "https://oauth2.googleapis.com/revoke"
API = "https://www.googleapis.com/youtube/v3"
# A sign-in must be completed within this many seconds of starting it.
PENDING_TTL = 600


class AuthError(Exception):
    pass


class QuotaExceeded(Exception):
    pass


def _load_client() -> dict | None:
    if not CLIENT_FILE.exists():
        return None
    data = json.loads(CLIENT_FILE.read_text())
    client = data.get("installed") or data.get("web")
    if not client or not client.get("client_id"):
        raise AuthError("client_secret.json isn't a Google OAuth client file.")
    return client


class GoogleAccount:
    def __init__(self):
        self._lock = threading.Lock()
        self._pending: dict[str, tuple[str, str, float]] = {}  # state -> (verifier, redirect_uri, created)
        self._token: dict | None = None
        self.channel: dict | None = None

    @property
    def configured(self) -> bool:
        try:
            return _load_client() is not None
        except (AuthError, json.JSONDecodeError):
            return False

    @property
    def connected(self) -> bool:
        return self._token is not None

    def start(self, redirect_uri: str) -> str:
        client = _load_client()
        if not client:
            raise AuthError("Google sign-in isn't set up yet (client_secret.json is missing).")
        state = secrets.token_urlsafe(32)
        verifier = secrets.token_urlsafe(64)
        challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
        with self._lock:
            now = time.time()
            self._pending = {s: v for s, v in self._pending.items() if now - v[2] < PENDING_TTL}
            self._pending[state] = (verifier, redirect_uri, now)
        return AUTH_URL + "?" + urlencode(
            {
                "client_id": client["client_id"],
                "redirect_uri": redirect_uri,
                "response_type": "code",
                "scope": SCOPE,
                "state": state,
                "code_challenge": challenge,
                "code_challenge_method": "S256",
                "access_type": "online",  # no long-lived refresh token is requested
                "prompt": "select_account",
            }
        )

    def finish(self, state: str, code: str):
        with self._lock:
            pending = self._pending.pop(state, None)
        if not pending or time.time() - pending[2] > PENDING_TTL:
            raise AuthError("This sign-in link expired or wasn't started here. Please try again.")
        verifier, redirect_uri, _ = pending
        client = _load_client()
        res = requests.post(
            TOKEN_URL,
            data={
                "client_id": client["client_id"],
                "client_secret": client.get("client_secret", ""),
                "code": code,
                "code_verifier": verifier,
                "grant_type": "authorization_code",
                "redirect_uri": redirect_uri,
            },
            timeout=15,
        )
        if not res.ok:
            raise AuthError("Google rejected the sign-in. Please try again.")
        tok = res.json()
        if SCOPE not in tok.get("scope", "").split():
            raise AuthError("YouTube access wasn't granted. Tick the YouTube permission when signing in.")
        tok["expires_at"] = time.time() + int(tok.get("expires_in", 3600)) - 60
        self._token = tok
        try:
            items = self._get("/channels", {"part": "snippet", "mine": "true"}).get("items") or []
        except Exception:
            items = []
        if not items:
            self._token = None
            raise AuthError(
                "This Google account has no YouTube channel. Open youtube.com once with it to create one, then try again."
            )
        snippet = items[0]["snippet"]
        self.channel = {
            "name": snippet.get("title"),
            "photo": ((snippet.get("thumbnails") or {}).get("default") or {}).get("url"),
        }

    def disconnect(self):
        tok, self._token, self.channel = self._token, None, None
        if tok:
            try:
                requests.post(REVOKE_URL, data={"token": tok["access_token"]}, timeout=10)
            except requests.RequestException:
                pass  # token is gone locally either way and expires within the hour

    def _headers(self) -> dict:
        tok = self._token
        if not tok:
            raise AuthError("Sign in with Google first.")
        if time.time() > tok["expires_at"]:
            # access_type=online means no refresh token, so an expired sign-in just ends.
            self._token = self.channel = None
            raise AuthError("Your Google sign-in expired. Sign in again to continue.")
        return {"Authorization": f"Bearer {tok['access_token']}"}

    def _check(self, res: requests.Response) -> dict:
        if res.ok:
            return res.json()
        try:
            err = res.json()["error"]
            reason = (err.get("errors") or [{}])[0].get("reason", "")
            message = err.get("message", "")
        except Exception:
            reason, message = "", res.text[:200]
        if reason in ("quotaExceeded", "rateLimitExceeded", "dailyLimitExceeded"):
            raise QuotaExceeded(message)
        if res.status_code == 401:
            self._token = self.channel = None
            raise AuthError("Your Google sign-in expired. Sign in again to continue.")
        raise RuntimeError(message or f"YouTube API error {res.status_code}")

    def _get(self, path: str, params: dict) -> dict:
        return self._check(requests.get(API + path, params=params, headers=self._headers(), timeout=15))

    def _post(self, path: str, params: dict, body: dict) -> dict:
        return self._check(requests.post(API + path, params=params, json=body, headers=self._headers(), timeout=15))

    def create_playlist(self, title: str, description: str, privacy: str) -> str:
        res = self._post(
            "/playlists",
            {"part": "snippet,status"},
            {"snippet": {"title": title[:150], "description": description[:5000]}, "status": {"privacyStatus": privacy}},
        )
        return res["id"]

    def add_video(self, playlist_id: str, video_id: str):
        self._post(
            "/playlistItems",
            {"part": "snippet"},
            {"snippet": {"playlistId": playlist_id, "resourceId": {"kind": "youtube#video", "videoId": video_id}}},
        )

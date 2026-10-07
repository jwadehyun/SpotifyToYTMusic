# Spotify to YouTube Music

Essentially copies a public Spotify playlist (or album) to a new YouTube Music playlist.

As a YouTube music user, one thing about Spotify that that tempts me the most is definitely their amazing playlists. Not only from the users too. Did you know that Spotify now has Ticketmaster posting the setlists for a bunch of concerts? Well now you can use this to seamlessly copy the spotify playlist to youtube music. 
Easily verify that you got the correct songs with the verification page. Choose different alternatives if the songs don't match but so far it's been pretty accurate. 
You can also choose whether or not to make the playlist private. 

## Run

```sh
./start.sh
```

Then open http://localhost:8765. The first run sets up Python packages automatically (needs Python 3.10+; `brew install python@3.12`).

## How it works

1. **Read Spotify.** The app reads the playlist from Spotify's public embed page, so no Spotify account or key is needed. That page only includes the **first 100 songs**, and the app warns you when a playlist may be cut off.
2. **Match.** For each song it searches YouTube Music's *Songs* results (using [ytmusicapi](https://github.com/sigma67/ytmusicapi)) and scores every result on:
   - title (ignoring "feat." and "Remastered" tags)
   - artist
   - length
   - version: live / remix / sped up / piano / re-recorded… count as different songs

   Each match gets one of these labels:
   - **Exact song:** title and main artist match, and the length is within 5 seconds.
   - **Close match: check:** probably right, but something differs a little.
   - **Video version:** no official song was found, so the official music video is used instead.
   - **Not found:** skipped unless you pick something.
3. **Review.** Use each row's dropdown to pick another result, paste a YouTube link, or skip the song. The **Needs review** filter shows only the rows to check.
4. **Create.** Sign in with Google and create the playlist. It's private by default. It's created on your YouTube account, so it appears in YouTube Music as well.

## Set up Google sign-in (one time, about 10 minutes)

The app needs its own Google "OAuth client" so Google can ask you for permission. It's free.

1. Go to https://console.cloud.google.com and create a project, e.g. "Playlist Mover".
2. **APIs & Services → Library** → search **YouTube Data API v3** → **Enable**.
3. **Google Auth Platform** (also listed as "OAuth consent screen") → **Get started**:
   - App name: Playlist Mover. Support email: yours.
   - Audience: **External**.
   - Contact email: yours → **Create**.
4. **Audience → Test users → Add users** → add the Google account(s) that will use the app.
5. **Clients → Create client** → Application type **Desktop app** → **Create** → **Download JSON**.
6. Save the file as `client_secret.json` in this folder, next to `app.py`, then restart `./start.sh`.

While signing in, Google shows "Google hasn't verified this app". That's expected for your own app in testing mode: click **Continue**. Only the test users you added can sign in. To open it to anyone, Google requires an app review.

**Daily limit:** Google gives each project 10,000 free YouTube API units per day. Creating a playlist costs 50 and each song costs 50, so that's **about 200 songs per day**. Searching doesn't count, because it uses YouTube Music's search. If you hit the limit, the app keeps your place; press **Add remaining songs** the next day.

## Security

- **Sign-in in memory only:** your Google sign-in is held only in the server's memory. It's never written to disk, and it's gone when the server stops. No long-lived refresh token is requested; a sign-in lasts about an hour.
- **Sign-in never reaches the page:** the web page only receives your channel name and picture.
- **Protected sign-in:** sign-in uses Google's recommended protections against intercepted or forged sign-ins (PKCE and a one-time `state` code).
- **Only your computer:** the server only listens on this computer (127.0.0.1). It rejects requests from other websites and addresses (DNS rebinding), and it sends strict security headers.
- **Disconnect revokes access:** **Disconnect** revokes the app's access at Google, not just locally.
- **Credentials file:** `client_secret.json` identifies your Google app and is excluded from git. Don't publish it. It doesn't grant access to your account by itself, but if it leaks, delete it in the Cloud console and create a new one.
- **Broad permission:** Google has no playlist-only permission, so the app asks for "Manage your YouTube account", the smallest one that allows creating playlists. The app only ever calls three endpoints: read your channel name, create a playlist, and add a video to it (see `youtube.py`).

## Files

- `spotify.py`: reads the playlist from Spotify's embed page
- `matcher.py`: YouTube Music search and match scoring
- `youtube.py`: Google sign-in and YouTube playlist creation
- `app.py`: local web server (FastAPI) and security checks
- `static/`: the web page

"""
Spotify for the desktop window: your library, album / artist / playlist pages, search and the player,
shaped into small plain dicts the window can draw. Uses the same Spotify endpoints as the iPhone app.

`sp` is the DJ engine's spotipy client; the app sets it once Spotify is connected.
"""
import re
import threading
import time
from html import unescape

import requests

# Everything the window needs: your library and playlists, liking songs, following, and the player.
SCOPES = " ".join([
    "user-read-playback-state", "user-modify-playback-state", "user-read-currently-playing",
    "user-read-recently-played", "user-top-read", "user-library-read", "user-library-modify",
    "playlist-read-private", "playlist-read-collaborative", "playlist-modify-private", "playlist-modify-public",
    "user-follow-read", "user-follow-modify",
    "streaming", "user-read-email", "user-read-private",     # the built-in player (Spotify's Web Playback SDK)
])

sp = None
_lock = threading.Lock()


# ---------------------------------------------------------------- talking to Spotify
def get(path, **q):
    return sp._get(path, **q)


def put(path, payload=None, **q):
    return sp._put(path, payload=payload, **q)


def post(path, payload=None, **q):
    return sp._post(path, payload=payload, **q)


def delete(path, payload=None, **q):
    return sp._delete(path, payload=payload, **q)


def safe(fn, default=None):
    """Runs one Spotify request; a failure gives `default` (and a line in Activity) instead of an error."""
    try:
        return fn()
    except Exception as e:
        status = getattr(e, "http_status", None)
        print(f"Spotify said no ({status or 'no answer'}): {str(e)[:200]}")
        return default


# ---------------------------------------------------------------- shapes
def pick(images):
    """(largest, a mid-size one around 300px) image URLs."""
    found = sorted(((i.get("url"), i.get("width") or 0) for i in (images or []) if i.get("url")), key=lambda x: -x[1])
    if not found:
        return "", ""
    mid = found[0][0]
    for url, w in found:
        if w >= 250:
            mid = url
    return found[0][0], mid


def track(d, album=None):
    if not isinstance(d, dict) or not d.get("name"):
        return None
    if d.get("type") and d.get("type") != "track":
        return None
    arts = [{"id": a.get("id") or "", "name": a.get("name") or ""} for a in d.get("artists") or []]
    t = {
        "id": d.get("id") or "",
        "uri": d.get("uri") or "",
        "title": d["name"],
        "artist": arts[0]["name"] if arts else "",
        "artists": arts,
        "artistLine": ", ".join(a["name"] for a in arts if a["name"]),
        "duration": d.get("duration_ms") or 0,
        "explicit": bool(d.get("explicit")),
        "number": d.get("track_number") or 0,
        "local": bool(d.get("is_local")),
    }
    al = d.get("album")
    if isinstance(al, dict):
        large, mid = pick(al.get("images"))
        t.update(album=al.get("name") or "", albumId=al.get("id") or "", art=large, artMid=mid,
                 year=(al.get("release_date") or "")[:4])
    elif album:
        t.update(album=album["name"], albumId=album["id"], art=album["art"], artMid=album["artMid"], year=album["year"])
    else:
        t.update(album="", albumId="", art="", artMid="", year="")
    return t


def album(d):
    if not isinstance(d, dict) or not d.get("id") or not d.get("name"):
        return None
    large, mid = pick(d.get("images"))
    arts = d.get("artists") or []
    kind = (d.get("album_type") or "album").lower()
    total = d.get("total_tracks") or 0
    label = {"single": "Single" if total <= 3 else "EP", "compilation": "Compilation"}.get(kind, "Album")
    return {
        "id": d["id"], "uri": d.get("uri") or "spotify:album:" + d["id"], "name": d["name"],
        "artist": ", ".join(a.get("name") or "" for a in arts), "artistId": (arts[0].get("id") if arts else "") or "",
        "art": large, "artMid": mid, "release": d.get("release_date") or "", "year": (d.get("release_date") or "")[:4],
        "type": label, "total": total,
    }


def artist(d):
    if not isinstance(d, dict) or not d.get("id") or not d.get("name"):
        return None
    large, mid = pick(d.get("images"))
    return {"id": d["id"], "uri": d.get("uri") or "spotify:artist:" + d["id"], "name": d["name"],
            "image": large, "imageMid": mid, "genres": d.get("genres") or [],
            "followers": (d.get("followers") or {}).get("total") or 0}


def _plain(s):
    return unescape(re.sub(r"<[^>]+>", "", s or "")).strip()


def playlist(d):
    if not isinstance(d, dict) or not d.get("id") or not d.get("name"):
        return None
    large, mid = pick(d.get("images"))
    o = d.get("owner") or {}
    ref = d.get("items") if isinstance(d.get("items"), dict) else d.get("tracks")   # Spotify renamed "tracks" to "items"
    return {"id": d["id"], "uri": d.get("uri") or "spotify:playlist:" + d["id"], "name": d["name"],
            "owner": o.get("display_name") or o.get("id") or "", "ownerId": o.get("id") or "",
            "image": large, "imageMid": mid, "about": _plain(d.get("description")),
            "total": (ref or {}).get("total") or 0, "collaborative": bool(d.get("collaborative"))}


def device(d):
    return {"id": d.get("id") or "", "name": d.get("name") or "", "type": (d.get("type") or "").lower(),
            "active": bool(d.get("is_active")), "restricted": bool(d.get("is_restricted")),
            "volume": d.get("volume_percent")}


def _rows(items, key):
    out = []
    for r in items or []:
        x = r.get("item") or r.get(key) if isinstance(r, dict) else None
        out.append(x)
    return out


# ---------------------------------------------------------------- you and your library
_me = {"at": 0, "value": None}


def me():
    if _me["value"] and time.time() - _me["at"] < 3600:
        return _me["value"]
    j = safe(lambda: get("me"))
    if j:
        large, mid = pick(j.get("images"))
        _me.update(at=time.time(), value={"id": j.get("id") or "", "name": j.get("display_name") or j.get("id") or "", "image": mid})
    return _me["value"]


def my_playlists(offset=0):
    j = safe(lambda: get("me/playlists", limit=50, offset=offset)) or {}
    items = [p for p in (playlist(x) for x in j.get("items") or []) if p]
    return {"items": items, "total": j.get("total") or len(items)}


def liked(offset=0):
    j = safe(lambda: get("me/tracks", limit=50, offset=offset)) or {}
    items = [t for t in (track(x) for x in _rows(j.get("items"), "track")) if t]
    return {"items": items, "total": j.get("total") or len(items)}


def saved_albums(offset=0):
    j = safe(lambda: get("me/albums", limit=50, offset=offset)) or {}
    items = [a for a in (album(r.get("album")) for r in j.get("items") or []) if a]
    return {"items": items, "total": j.get("total") or len(items)}


def followed_artists(after=None):
    q = {"type": "artist", "limit": 50}
    if after:
        q["after"] = after
    j = safe(lambda: get("me/following", **q)) or {}
    page = j.get("artists") or {}
    items = [a for a in (artist(x) for x in page.get("items") or []) if a]
    return {"items": items, "after": (page.get("cursors") or {}).get("after")}


def top_tracks():
    j = safe(lambda: get("me/top/tracks", limit=20, time_range="short_term")) or {}
    return [t for t in (track(x) for x in j.get("items") or []) if t]


def top_artists():
    j = safe(lambda: get("me/top/artists", limit=20, time_range="medium_term")) or {}
    return [a for a in (artist(x) for x in j.get("items") or []) if a]


def recently_played():
    j = safe(lambda: get("me/player/recently-played", limit=50)) or {}
    return [t for t in (track(r.get("track")) for r in j.get("items") or []) if t]


def recent_albums(tracks):
    seen, out = set(), []
    for t in tracks:
        if t["albumId"] and t["albumId"] not in seen:
            seen.add(t["albumId"])
            out.append({"id": t["albumId"], "uri": "spotify:album:" + t["albumId"], "name": t["album"], "artist": t["artist"],
                        "artistId": (t["artists"][0]["id"] if t["artists"] else ""), "art": t["art"], "artMid": t["artMid"],
                        "year": t["year"], "type": "Album", "total": 0, "release": ""})
            if len(out) >= 14:
                break
    return out


_home = {"at": 0, "value": None}


def home(force=False):
    """Everything on Home (and the sidebar's playlists), cached for a few minutes."""
    with _lock:
        if not force and _home["value"] and time.time() - _home["at"] < 300:
            return _home["value"]
    out = {}

    def run(key, fn):
        out[key] = fn()

    jobs = [("me", me), ("recent", recently_played), ("top", top_tracks), ("artists", top_artists),
            ("playlists", my_playlists), ("liked", lambda: liked(0)), ("albums", lambda: saved_albums(0)),
            ("following", lambda: followed_artists(None))]
    threads = [threading.Thread(target=run, args=j, daemon=True) for j in jobs]
    for t in threads:
        t.start()
    for t in threads:
        t.join(30)
    pls = out.get("playlists") or {"items": [], "total": 0}
    lk = out.get("liked") or {"items": [], "total": 0}
    al = out.get("albums") or {"items": [], "total": 0}
    fo = out.get("following") or {"items": [], "after": None}
    value = {
        "me": out.get("me"),
        "recentAlbums": recent_albums(out.get("recent") or []),
        "topTracks": out.get("top") or [],
        "topArtists": out.get("artists") or [],
        "playlists": pls["items"], "playlistsTotal": pls["total"],
        "liked": lk["items"], "likedTotal": lk["total"],
        "albums": al["items"], "albumsTotal": al["total"],
        "artists": fo["items"], "artistsAfter": fo["after"],
    }
    with _lock:
        _home.update(at=time.time(), value=value)
    return value


def forget_home():
    with _lock:
        _home.update(at=0, value=None)


# ---------------------------------------------------------------- liking, saving, playlists
def contains(uris):
    uris = [u for u in uris if u][:40]
    if not uris:
        return []
    r = safe(lambda: get("me/library/contains", uris=",".join(uris)))
    return r if isinstance(r, list) else [False] * len(uris)


def set_saved(uri, on):
    """Like a song, save an album, follow an artist or a playlist (or undo it)."""
    if on:
        ok = safe(lambda: put("me/library", uris=uri) or True, False)
    else:
        ok = safe(lambda: delete("me/library", uris=uri) or True, False)
    if ok:
        forget_home()
    return bool(ok)


def create_playlist(name):
    j = safe(lambda: post("me/playlists", payload={"name": name, "public": False, "description": "Made with Cara DJ"}))
    forget_home()
    return playlist(j) if j else None


def add_to_playlist(pid, uris):
    ok = safe(lambda: post(f"playlists/{pid}/items", payload={"uris": uris}) or True, False)
    if ok:
        forget_home()
    return bool(ok)


# ---------------------------------------------------------------- pages
def album_page(aid):
    j = safe(lambda: get(f"albums/{aid}"))
    a = album(j)
    if not a:
        return None
    page = j.get("tracks") or {}
    tracks = [t for t in (track(x, a) for x in page.get("items") or []) if t]
    total = page.get("total") or len(tracks)
    while len(tracks) < total:
        more = safe(lambda: get(f"albums/{aid}/tracks", limit=50, offset=len(tracks))) or {}
        got = [t for t in (track(x, a) for x in more.get("items") or []) if t]
        if not got:
            break
        tracks += got
    copyright = ((j.get("copyrights") or [{}])[0] or {}).get("text") or ""
    saved = contains([a["uri"]] + [t["uri"] for t in tracks[:39]])
    return {"album": a, "tracks": tracks, "copyright": copyright, "saved": bool(saved and saved[0]),
            "liked": dict(zip([t["uri"] for t in tracks[:39]], (saved or [])[1:]))}


def artist_page(aid):
    a = artist(safe(lambda: get(f"artists/{aid}")))
    if not a:
        return None
    out = {}

    def run(key, fn):
        out[key] = fn()

    jobs = [("top", lambda: artist_top(a)),
            ("albums", lambda: [x for x in (album(i) for i in (safe(lambda: get(f"artists/{aid}/albums", include_groups="album", limit=10)) or {}).get("items") or []) if x]),
            ("singles", lambda: [x for x in (album(i) for i in (safe(lambda: get(f"artists/{aid}/albums", include_groups="single", limit=10)) or {}).get("items") or []) if x]),
            ("bio", lambda: artist_bio(a["name"])),
            ("following", lambda: contains([a["uri"]]))]
    threads = [threading.Thread(target=run, args=j, daemon=True) for j in jobs]
    for t in threads:
        t.start()
    for t in threads:
        t.join(30)
    f = out.get("following") or [False]
    return {"artist": a, "top": out.get("top") or [], "albums": out.get("albums") or [],
            "singles": out.get("singles") or [], "bio": out.get("bio") or "", "following": bool(f and f[0])}


def artist_top(a):
    """Spotify took "Top Songs" away from apps like this one, so search for the artist's best-known songs instead."""
    j = safe(lambda: get("search", q=f'artist:"{a["name"]}"', type="track", limit=10)) or {}
    items = [t for t in (track(x) for x in (j.get("tracks") or {}).get("items") or []) if t]
    theirs = [t for t in items if any(x["id"] == a["id"] for x in t["artists"])]
    return theirs or items


def playlist_page(pid):
    j = safe(lambda: get(f"playlists/{pid}"))
    p = playlist(j)
    if not p:
        return None
    page = j.get("items") if isinstance(j.get("items"), dict) else j.get("tracks")
    rows = (page or {}).get("items")
    tracks = [t for t in (track(x) for x in _rows(rows, "track")) if t]
    total = (page or {}).get("total") or p["total"]
    saved = contains([p["uri"]])
    return {"playlist": p, "tracks": tracks, "total": total, "canList": rows is not None, "rows": len(rows or []),
            "saved": bool(saved and saved[0])}


def playlist_more(pid, offset):
    j = safe(lambda: get(f"playlists/{pid}/items", limit=50, offset=offset)) or {}
    rows = j.get("items") or []
    return {"tracks": [t for t in (track(x) for x in _rows(rows, "track")) if t], "rows": len(rows)}


def liked_more(offset):
    return liked(offset)


# ---------------------------------------------------------------- search and browse
GENRES = [
    {"id": "pop", "title": "Pop", "tracks": "genre:pop", "playlists": "pop hits", "hue": 0.93},
    {"id": "hiphop", "title": "Hip-Hop", "tracks": "genre:hip-hop", "playlists": "hip hop", "hue": 0.08},
    {"id": "dance", "title": "Dance", "tracks": "genre:dance", "playlists": "dance hits", "hue": 0.78},
    {"id": "rnb", "title": "R&B", "tracks": "genre:r-n-b", "playlists": "r&b", "hue": 0.62},
    {"id": "rock", "title": "Rock", "tracks": "genre:rock", "playlists": "rock classics", "hue": 0.02},
    {"id": "indie", "title": "Indie", "tracks": "genre:indie", "playlists": "indie", "hue": 0.33},
    {"id": "latin", "title": "Latin", "tracks": "genre:latin", "playlists": "latin hits", "hue": 0.12},
    {"id": "kpop", "title": "K-Pop", "tracks": "genre:k-pop", "playlists": "k-pop", "hue": 0.85},
    {"id": "country", "title": "Country", "tracks": "genre:country", "playlists": "country hits", "hue": 0.1},
    {"id": "2000s", "title": "2000s", "tracks": "year:2000-2009", "playlists": "2000s pop", "hue": 0.55},
    {"id": "2010s", "title": "2010s", "tracks": "year:2010-2019", "playlists": "2010s hits", "hue": 0.7},
    {"id": "chill", "title": "Chill", "tracks": "genre:chill", "playlists": "chill vibes", "hue": 0.5},
    {"id": "workout", "title": "Workout", "tracks": "genre:work-out", "playlists": "workout", "hue": 0.04},
    {"id": "party", "title": "Party", "tracks": "genre:party", "playlists": "party", "hue": 0.9},
]


def search(q, types="track,artist,album,playlist", offset=0):
    """Spotify only sends 10 results per kind at a time to apps like this one."""
    j = safe(lambda: get("search", q=q, type=types, limit=10, offset=offset)) or {}
    return {
        "tracks": [t for t in (track(x) for x in (j.get("tracks") or {}).get("items") or []) if t],
        "artists": [a for a in (artist(x) for x in (j.get("artists") or {}).get("items") or []) if a],
        "albums": [a for a in (album(x) for x in (j.get("albums") or {}).get("items") or []) if a],
        "playlists": [p for p in (playlist(x) for x in (j.get("playlists") or {}).get("items") or []) if p],
    }


def genre_page(gid):
    g = next((x for x in GENRES if x["id"] == gid), None)
    if not g:
        return None
    a = search(g["tracks"], "track")
    tracks = a["tracks"]
    if len(tracks) >= 10:
        tracks += search(g["tracks"], "track", 10)["tracks"]
    return {"genre": g, "tracks": tracks, "playlists": search(g["playlists"], "playlist")["playlists"]}


# ---------------------------------------------------------------- the player
def playback():
    """What's playing (None when Spotify couldn't be asked, {} when nothing is)."""
    t0 = time.time()
    try:
        j = get("me/player", additional_types="track")
    except Exception as e:
        raise e
    t1 = time.time()
    if not j:
        return {}
    item = j.get("item") if (j.get("currently_playing_type") or "track") == "track" else None
    dev = j.get("device") or {}
    return {
        "playing": bool(j.get("is_playing")),
        "progress": j.get("progress_ms") or 0,
        "stamp": int((t0 + t1) / 2 * 1000),
        "shuffle": bool(j.get("shuffle_state")),
        "repeat": j.get("repeat_state") or "off",
        "device": device(dev) if dev else None,
        "context": (j.get("context") or {}).get("uri") or "",
        "rawContext": j.get("context"),
        "track": track(item) if item else None,
    }


def devices():
    j = safe(lambda: get("me/player/devices")) or {}
    return [device(d) for d in j.get("devices") or []]


def up_next():
    j = safe(lambda: get("me/player/queue")) or {}
    return [t for t in (track(x) for x in j.get("queue") or []) if t][:30]


_names = {}


def context_name(uri):
    """The playlist / album / artist something is playing from."""
    if not uri:
        return ""
    if uri.endswith(":collection"):
        return "Liked Songs"
    if uri in _names:
        return _names[uri]
    parts = uri.split(":")
    name = ""
    if len(parts) >= 3:
        kind, cid = parts[-2], parts[-1]
        if kind == "playlist":
            name = (safe(lambda: get(f"playlists/{cid}", fields="name")) or {}).get("name") or ""
        elif kind == "album":
            name = (safe(lambda: get(f"albums/{cid}")) or {}).get("name") or ""
        elif kind == "artist":
            name = (safe(lambda: get(f"artists/{cid}")) or {}).get("name") or ""
    _names[uri] = name
    return name


# ---------------------------------------------------------------- lyrics and the story of the song
_lyrics = {}


def lyrics(title, artist_name, album_name="", duration_ms=0):
    """Timed lines from LRCLIB (a free, open lyrics database) when there are some, else plain text."""
    key = f"{title}|{artist_name}"
    if key in _lyrics:
        return _lyrics[key]
    synced = plain = ""
    head = {"User-Agent": "NonStopPopDJ (https://github.com/frankfigueroa1999-del/cara-dj)"}
    try:
        q = {"track_name": title, "artist_name": artist_name}
        if album_name:
            q["album_name"] = album_name
        if duration_ms:
            q["duration"] = max(1, int(duration_ms // 1000))
        r = requests.get("https://lrclib.net/api/get", params=q, headers=head, timeout=10)
        if r.ok:
            j = r.json()
            synced, plain = j.get("syncedLyrics") or "", j.get("plainLyrics") or ""
        if not synced and not plain:
            r = requests.get("https://lrclib.net/api/search", params={"track_name": title, "artist_name": artist_name}, headers=head, timeout=10)
            if r.ok:
                for item in r.json() or []:
                    if item.get("syncedLyrics"):
                        synced = item["syncedLyrics"]
                        break
                    if not plain and item.get("plainLyrics"):
                        plain = item["plainLyrics"]
    except Exception as e:
        print(f"[lyrics lookup failed: {e}]")
        return {"lines": [], "plain": "", "message": "Couldn't reach the lyrics right now."}
    lines = []
    for raw in synced.splitlines():
        stamps = re.findall(r"\[(\d+):(\d+(?:\.\d+)?)\]", raw)
        text = re.sub(r"^(\[[^\]]*\])+", "", raw).strip()
        for m, s in stamps:
            lines.append({"t": int(int(m) * 60000 + float(s) * 1000), "text": text})
    lines.sort(key=lambda x: x["t"])
    out = {"lines": lines, "plain": "" if lines else plain.strip(),
           "message": "" if (lines or plain) else "No lyrics found for this song."}
    if len(_lyrics) > 300:
        _lyrics.clear()
    _lyrics[key] = out
    return out


def artist_bio(name):
    try:
        import live_dj_free as dj
        return dj._wiki_lookup(f"{name} musician band singer", [name]) or ""
    except Exception:
        return ""


def about(t):
    """The story of the song and the artist, from Wikipedia, plus the artist's genres and picture."""
    if not t or not t.get("title"):
        return {}
    clean = re.sub(r"\s*[(\[-].*$", "", t["title"]).strip() or t["title"]
    first = (t.get("artist") or "").split(" ")[0]
    out = {}

    def run(key, fn):
        try:
            out[key] = fn()
        except Exception:
            out[key] = None

    import live_dj_free as dj
    aid = ((t.get("artists") or [{}])[0] or {}).get("id") or ""
    jobs = [("song", lambda: dj._wiki_lookup(f'"{clean}" {t.get("artist", "")} song', [clean, first])),
            ("bio", lambda: artist_bio(t.get("artist", ""))),
            ("artist", lambda: artist(safe(lambda: get(f"artists/{aid}"))) if aid else None)]
    threads = [threading.Thread(target=run, args=j, daemon=True) for j in jobs]
    for th in threads:
        th.start()
    for th in threads:
        th.join(20)
    bio = out.get("bio") or ""
    song = out.get("song") or ""
    a = out.get("artist") or {}
    return {"song": "" if song == bio else song, "bio": bio, "genres": (a.get("genres") or [])[:4],
            "artistImage": a.get("image") or "", "followers": a.get("followers") or 0}

"""
Spotify for the desktop window: your library, album / artist / playlist pages, search and the player,
shaped into small plain dicts the window can draw. Uses the same Spotify endpoints as the iPhone app.

`sp` is the DJ engine's spotipy client; the app sets it once Spotify is connected.
"""
import json
import os
import random
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
    except SlowDown:
        return default                                   # waiting out Spotify's limit: said once already
    except Exception as e:
        status = getattr(e, "http_status", None)
        print(f"Spotify said no ({status or 'no answer'}): {str(e)[:200]}")
        return default


# ---------------------------------------------------------------- when Spotify says "slow down"
# Spotify counts each developer app's requests over a rolling half minute: one count for everyone using the same
# Client ID. When it answers 429 ("too many requests"), every request from this app (and from Cara) waits: as long as
# Spotify says, at least 30 seconds, and longer each time it happens again soon after. Asking anyway only keeps the
# limit going. What's playing is also asked for once and shared, rather than separately by the app and by Cara.
_limit = {"until": 0.0, "strikes": 0, "last": 0.0}
_shared = {"at": 0.0, "value": None}


class SlowDown(Exception):
    http_status = 429

    def __init__(self, wait):
        super().__init__(f"Spotify asked this app to slow down; asking again in {int(wait) + 1} s")
        self.wait = wait


def limited():
    """Seconds left before the app may ask Spotify anything again (0 when it can)."""
    return max(0.0, _limit["until"] - time.time())


def _note_limit(retry_after):
    now = time.time()
    if now - _limit["last"] > 900:
        _limit["strikes"] = 0                            # it's been a while: start over
    _limit["strikes"] = min(_limit["strikes"] + 1, 6)
    _limit["last"] = now
    wait = min(max(retry_after, 30 * 2 ** (_limit["strikes"] - 1)), 3600)    # 30 s, 1, 2, 4, 8, 16 minutes
    if now + wait > _limit["until"]:
        _limit["until"] = now + wait
        print(f"Spotify asked the app to slow down (too many requests): waiting {int(wait)} s before asking again.")


def guard(client):
    """Every request (the app's and Cara's) goes through here."""
    if getattr(client, "_nsp_guarded", False):
        return client
    orig = client._internal_call

    def call(method, url, payload, params):
        left = limited()
        if left:
            raise SlowDown(left)
        mine = method == "GET" and url.rstrip("/").endswith("me/player")
        if mine and time.time() - _shared["at"] < 0.9 and _shared["value"] is not None:
            return _shared["value"]                      # asked a moment ago (by the app or by Cara): the same answer
        try:
            got = orig(method, url, payload, params)
        except Exception as e:
            # (spotipy also reports Spotify's servers failing three times running as a 429, "too many 502 error responses")
            if getattr(e, "http_status", None) == 429 and "too many 5" not in str(getattr(e, "reason", "") or ""):
                try:
                    ra = int((getattr(e, "headers", None) or {}).get("Retry-After") or 0)
                except (TypeError, ValueError):
                    ra = 0
                _note_limit(ra)
            raise
        if mine:
            _shared.update(at=time.time(), value=got)
        return got

    client._internal_call = call
    client._nsp_guarded = True
    return client


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


def _with_added(rows, key="track"):
    """Songs from a playlist's (or Liked Songs') rows, each with when it was added (for sorting by date added)."""
    out = []
    for r in rows or []:
        if not isinstance(r, dict):
            continue
        t = track(r.get("item") or r.get(key))
        if t:
            t["added"] = r.get("added_at") or ""
            out.append(t)
    return out


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
    items = _with_added(j.get("items"))
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
    """What you played lately, newest first; each song says what it was played from (playlist, album...)."""
    j = safe(lambda: get("me/player/recently-played", limit=50)) or {}
    out = []
    for r in j.get("items") or []:
        t = track(r.get("track"))
        if t:
            t["playedFrom"] = (r.get("context") or {}).get("uri") or ""
            t["playedAt"] = r.get("played_at") or ""
            out.append(t)
    return out


def _id_of(uri, kind):
    parts = (uri or "").split(":")
    return parts[parts.index(kind) + 1] if kind in parts and parts.index(kind) + 1 < len(parts) else ""


def shortcuts(recent, pls, artists_known, albums, limit=8, fill=True):
    """The tiles at the top of Home: what you played lately (playlists, albums, artists, Liked Songs), newest first."""
    mine = {p["id"]: p for p in pls}
    known = {a["id"]: a for a in artists_known if a and a.get("id")}
    out, seen, lookups = [], set(), 0
    for t in recent:
        ctx = t.get("playedFrom") or ""
        if "collection" in ctx.split(":"):
            key = "liked"
        elif _id_of(ctx, "playlist"):
            key = "playlist:" + _id_of(ctx, "playlist")
        elif _id_of(ctx, "artist"):
            key = "artist:" + _id_of(ctx, "artist")
        elif t.get("albumId"):
            key = "album:" + t["albumId"]                  # an album, or a single played on its own
        else:
            continue
        if key in seen:
            continue
        seen.add(key)
        kind, _, xid = key.partition(":")
        if kind == "liked":
            out.append({"kind": "liked", "name": "Liked Songs", "uri": ""})
        elif kind == "playlist":
            p = mine.get(xid)
            if not p and lookups < 4:
                lookups += 1
                p = playlist(safe(lambda: get(f"playlists/{xid}", fields="id,uri,name,images,owner(display_name,id),collaborative,description,tracks(total)")))
            if p:
                out.append({"kind": "playlist", "id": p["id"], "uri": p["uri"], "name": p["name"], "art": p.get("imageMid") or p.get("image") or ""})
        elif kind == "artist":
            a = known.get(xid)
            if not a and lookups < 4:
                lookups += 1
                a = artist(safe(lambda: get(f"artists/{xid}")))
            if a:
                out.append({"kind": "artist", "id": a["id"], "uri": a["uri"], "name": a["name"], "art": a.get("imageMid") or a.get("image") or ""})
        else:
            out.append({"kind": "album", "id": xid, "uri": "spotify:album:" + xid, "name": t.get("album") or "", "art": t.get("artMid") or t.get("art") or ""})
        if len(out) >= limit:
            break
    if not fill:
        return out
    for p in pls:                                          # not much played lately: your playlists fill in
        if len(out) >= 8:
            break
        if "playlist:" + p["id"] not in seen:
            seen.add("playlist:" + p["id"])
            out.append({"kind": "playlist", "id": p["id"], "uri": p["uri"], "name": p["name"], "art": p.get("imageMid") or p.get("image") or ""})
    for a in albums:
        if len(out) >= 8:
            break
        if "album:" + a["id"] not in seen:
            seen.add("album:" + a["id"])
            out.append({"kind": "album", "id": a["id"], "uri": a["uri"], "name": a["name"], "art": a.get("artMid") or a.get("art") or ""})
    if "liked" not in seen and len(out) < 8:
        out.insert(0, {"kind": "liked", "name": "Liked Songs", "uri": ""})
    return out[:8]


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
    """Everything on Home (and the sidebar's playlists), cached for a few minutes (and never redone more often than
    every 20 seconds, however often something changes)."""
    with _lock:
        age = time.time() - _home["at"]
        if _home["value"] and (age < 20 or (not force and age < 300)):
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
        "shortcuts": shortcuts(out.get("recent") or [], pls["items"], (out.get("artists") or []) + fo["items"], al["items"]),
        "_recent": out.get("recent") or [],
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
    tracks = _with_added(rows)
    total = (page or {}).get("total") or p["total"]
    saved = contains([p["uri"]])
    return {"playlist": p, "tracks": tracks, "total": total, "canList": rows is not None, "rows": len(rows or []),
            "saved": bool(saved and saved[0])}


def playlist_more(pid, offset):
    j = safe(lambda: get(f"playlists/{pid}/items", limit=50, offset=offset)) or {}
    rows = j.get("items") or []
    return {"tracks": _with_added(rows), "rows": len(rows)}


def all_tracks(kind, xid=""):
    """Every song in a playlist you made (or in Liked Songs), in order, fetched a few pages at a time."""
    if kind == "liked":
        first = safe(lambda: get("me/tracks", limit=50, offset=0)) or {}
        path = "me/tracks"
    else:
        first = safe(lambda: get(f"playlists/{xid}/items", limit=50, offset=0)) or {}
        path = f"playlists/{xid}/items"
    total = min(first.get("total") or 0, 5000)
    pages = {0: _with_added(first.get("items"))}
    offsets = list(range(50, total, 50))
    lock = threading.Lock()

    def fetch():
        while True:
            with lock:
                if not offsets:
                    return
                off = offsets.pop(0)
            j = safe(lambda: get(path, limit=50, offset=off)) or {}
            pages[off] = _with_added(j.get("items"))

    workers = [threading.Thread(target=fetch, daemon=True) for _ in range(min(6, len(offsets)))]
    for w in workers:
        w.start()
    for w in workers:
        w.join(60)
    return [t for off in sorted(pages) for t in pages[off]]


def tracks_of(kind, xid):
    """The songs in an album, a playlist you made, Liked Songs or one of the app's mixes."""
    if kind == "album":
        return (album_page(xid) or {}).get("tracks") or []
    if kind in ("playlist", "liked"):
        return all_tracks(kind, xid)
    if kind == "mix":
        return (mix(xid) or {}).get("tracks") or []
    return []


def edit_playlist(pid, name):
    ok = safe(lambda: put(f"playlists/{pid}", payload={"name": name}) or True, False)
    if ok:
        forget_home()
    return bool(ok)


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


# ---------------------------------------------------------------- song radio
def song_radio(tid):
    """A station built from one song: the song, more by its artists, the artists they work with, and their genres.
    (Spotify's own song radio isn't open to apps like this one.)"""
    seed = track(safe(lambda: get(f"tracks/{tid}")))
    if not seed:
        return None
    mains = [a for a in seed["artists"] if a.get("name")][:2]
    main_ids = {a["id"] for a in seed["artists"] if a.get("id")}
    pools = {}

    def run(key, q, offset=0):
        j = safe(lambda: get("search", q=q, type="track", limit=10, offset=offset)) or {}
        pools[key] = [t for t in (track(x) for x in (j.get("tracks") or {}).get("items") or []) if t and not t["local"]]

    def wave(jobs):
        threads = [threading.Thread(target=run, args=j, daemon=True) for j in jobs]
        for th in threads:
            th.start()
        for th in threads:
            th.join(20)

    first = [(f"a{i}{o}", f'artist:"{a["name"]}"', o) for i, a in enumerate(mains) for o in (0, 10)]
    lead = artist(safe(lambda: get(f"artists/{mains[0]['id']}"))) if mains and mains[0].get("id") else None
    genres = ((lead or {}).get("genres") or [])[:3]
    first += [(f"g{i}", f'genre:"{g}"', 0) for i, g in enumerate(genres)]
    wave(first)
    # the artists they make songs with (featured, or featuring them)
    seen_with = {}
    for key, items in pools.items():
        if key.startswith("a"):
            for t in items:
                if main_ids & {x["id"] for x in t["artists"]}:
                    for x in t["artists"]:
                        if x.get("id") and x["id"] not in main_ids and x.get("name"):
                            seen_with[x["name"]] = seen_with.get(x["name"], 0) + 1
    friends = sorted(seen_with, key=lambda n: -seen_with[n])[:4]
    wave([(f"c{i}", f'artist:"{n}"', 0) for i, n in enumerate(friends)])
    # mix: the song first, then take turns between the sources, never the same song (or version) twice
    order = ["a00", "c0", "g0", "a10", "c1", "g1", "a010", "c2", "g2", "a110", "c3"]
    queues = [list(pools.get(k) or []) for k in order]
    out, keys = [seed], {seed["uri"], (_bare(seed["title"]) + "|" + seed["artist"]).lower()}
    while len(out) < 50 and any(queues):
        for q in queues:
            while q:
                t = q.pop(0)
                k = (_bare(t["title"]) + "|" + t["artist"]).lower()
                if t["uri"] in keys or k in keys:
                    continue
                keys.update((t["uri"], k))
                out.append(t)
                break
    return {"seed": seed, "title": f"{seed['title']} Radio", "tracks": out[:50],
            "artists": [a["name"] for a in mains], "genres": genres, "friends": friends}


# ---------------------------------------------------------------- credits
MB = "https://musicbrainz.org/ws/2"
MB_AGENT = "NonStopPopDJ/3 ( https://github.com/frankfigueroa1999-del/cara-dj )"   # MusicBrainz asks apps to say who they are
_mb_lock = threading.Lock()
_mb_last = [0.0]
_credits = {}


def _mb(path, **params):
    """One MusicBrainz request (they ask for no more than one a second)."""
    with _mb_lock:
        wait = 1.05 - (time.time() - _mb_last[0])
        if wait > 0:
            time.sleep(wait)
        _mb_last[0] = time.time()
    params["fmt"] = "json"
    r = requests.get(f"{MB}/{path}", params=params, timeout=12, headers={"User-Agent": MB_AGENT, "Accept": "application/json"})
    if r.status_code == 404:
        return {}
    r.raise_for_status()
    return r.json()


def _bare(title):
    """ "Song (feat. X) - Remastered 2011" -> "Song" """
    return re.sub(r"\s*[(\[][^)\]]*[)\]]", "", title.split(" - ")[0]).strip() or title


def _pick_recording(recs, t, searched=False):
    """The recording that's this song: close in length, the right title (and a confident match, when searched)."""
    want = (t.get("duration") or 0) / 1000
    title = _bare(t["title"]).lower()
    best, best_score = None, -1e9
    for r in recs or []:
        if searched and int(r.get("score") or 0) < 85:
            continue
        score = 0.0
        if (r.get("title") or "").lower() == title or _bare(r.get("title") or "").lower() == title:
            score += 3
        length = (r.get("length") or 0) / 1000
        if want and length:
            gap = abs(length - want)
            if gap > 12:
                continue
            score -= gap / 4
        if score > best_score:
            best, best_score = r, score
    return best.get("id") if best else None


ROLE_NAMES = {"mix": "Mixing", "engineer": "Engineering", "recording": "Recording", "audio": "Engineering",
              "mastering": "Mastering", "programming": "Programming", "arranger": "Arrangement",
              "instrument arranger": "Arrangement", "vocal arranger": "Vocal arrangement", "performer": "Performance",
              "remixer": "Remix", "conductor": "Conductor", "chorus master": "Chorus master"}


def credits(tid):
    """Who made a song. Performers, album, label and copyrights come from Spotify; writers, producers and the rest
    from MusicBrainz, the open music encyclopedia (Spotify's own credits aren't open to apps like this one)."""
    if tid in _credits:
        return _credits[tid]
    j = safe(lambda: get(f"tracks/{tid}")) or {}
    t = track(j)
    if not t:
        return None
    al = j.get("album") or {}
    out = {"performers": [a["name"] for a in t["artists"] if a["name"]], "writers": [], "producers": [], "more": [],
           "album": al.get("name") or "", "released": al.get("release_date") or "", "label": "", "copyrights": [],
           "musicbrainz": ""}
    if al.get("id"):
        a = safe(lambda: get(f"albums/{al['id']}")) or {}
        out["label"] = a.get("label") or ""
        out["copyrights"] = [c["text"] for c in a.get("copyrights") or [] if c.get("text")][:2]
    try:
        rid = None
        isrc = (j.get("external_ids") or {}).get("isrc") or ""
        if isrc:
            rid = _pick_recording(_mb(f"isrc/{isrc}").get("recordings"), t)
        if not rid:
            q = f'recording:"{_bare(t["title"]).replace(chr(92), "").replace(chr(34), "")}" AND artist:"{t["artist"].replace(chr(34), "")}"'
            rid = _pick_recording(_mb("recording", query=q, limit=10).get("recordings"), t, searched=True)
        if rid:
            rec = _mb(f"recording/{rid}", inc="artist-credits+artist-rels+work-rels+work-level-rels")
            _roles(rec, out)
            out["musicbrainz"] = f"https://musicbrainz.org/recording/{rid}"
    except Exception as e:
        print(f"[credits: MusicBrainz didn't answer ({e})]")
        return out                                     # not kept, so the next look can try again
    if len(_credits) > 200:
        _credits.clear()
    _credits[tid] = out
    return out


def _roles(rec, out):
    roles = {}

    def add(role, name):
        if name and name not in roles.setdefault(role, []):
            roles[role].append(name)

    for rel in rec.get("relations") or []:
        kind = rel.get("type") or ""
        attrs = [a for a in rel.get("attributes") or [] if isinstance(a, str)]
        if rel.get("target-type") == "artist":
            name = (rel.get("artist") or {}).get("name")
            if kind == "producer":
                add("Producer", name)
            elif kind == "vocal":
                add((attrs[0] if attrs else "vocals").capitalize(), name)
            elif kind == "instrument":
                add((attrs[0] if attrs else "instruments").capitalize(), name)
            elif kind in ROLE_NAMES:
                add(ROLE_NAMES[kind], name)
        elif rel.get("target-type") == "work":
            for wr in (rel.get("work") or {}).get("relations") or []:
                if wr.get("target-type") == "artist" and wr.get("type") in ("composer", "lyricist", "writer", "librettist"):
                    add("Writer", (wr.get("artist") or {}).get("name"))
    out["writers"] = roles.pop("Writer", [])
    out["producers"] = roles.pop("Producer", [])
    out["more"] = [{"role": r, "names": n} for r, n in roles.items()][:10]


# ---------------------------------------------------------------- the Now Playing view's extras
WIKIDATA = "https://query.wikidata.org/sparql"
_extras = {}


def _wd(query):
    r = requests.get(WIKIDATA, params={"query": query, "format": "json"}, timeout=20,
                     headers={"User-Agent": MB_AGENT, "Accept": "application/sparql-results+json"})
    r.raise_for_status()
    return (r.json().get("results") or {}).get("bindings") or []


def _wv(b, k):
    return ((b.get(k) or {}).get("value")) or ""


def _lit(s):
    return '"' + (s or "").replace("\\", "\\\\").replace('"', '\\"').replace("\n", " ") + '"'


def _yt(v):
    v = (v or "").strip()
    return v if re.fullmatch(r"[A-Za-z0-9_-]{11}", v) else ""


def now_extras(t):
    """Besides the song itself, the Now Playing view shows its music video, the artist's other music videos and any
    tour they're on. They come from Wikidata (the free knowledge base behind Wikipedia, which keeps the YouTube ids of
    official videos); the videos play from YouTube."""
    tid = (t or {}).get("id") or ""
    arts = (t or {}).get("artists") or []
    aid = (arts[0] or {}).get("id") if arts else ""
    if not tid or not aid:
        return {}
    hit = _extras.get(tid)
    if hit and time.time() - hit["at"] < 3600:
        return hit["value"]
    title = _bare(t.get("title") or "").lower()
    queries = {
        "video": f"""SELECT ?yt WHERE {{
            {{ ?song wdt:P2207 {_lit(tid)} . }} UNION {{ ?artist wdt:P1902 {_lit(aid)} . ?song wdt:P175 ?artist ; rdfs:label ?l .
               FILTER(LANG(?l) = "en" && LCASE(STR(?l)) = {_lit(title)}) }}
            ?song wdt:P1651 ?yt . }} LIMIT 3""",
        "videos": f"""SELECT ?song ?yt ?label ?date WHERE {{
            ?artist wdt:P1902 {_lit(aid)} . ?song wdt:P175 ?artist ; wdt:P1651 ?yt ; rdfs:label ?label . FILTER(LANG(?label) = "en")
            OPTIONAL {{ ?song wdt:P577 ?date . }} }} ORDER BY DESC(?date) LIMIT 40""",
        "tours": f"""SELECT ?label ?start ?end ?article WHERE {{
            ?artist wdt:P1902 {_lit(aid)} . ?tour wdt:P175 ?artist ; wdt:P31 wd:Q1573906 ; rdfs:label ?label . FILTER(LANG(?label) = "en")
            OPTIONAL {{ ?tour wdt:P580 ?start . }} OPTIONAL {{ ?tour wdt:P582 ?end . }}
            OPTIONAL {{ ?article schema:about ?tour ; schema:isPartOf <https://en.wikipedia.org/> . }} }} LIMIT 60""",
    }
    got = {}

    def run(k, q):
        try:
            got[k] = _wd(q)
        except Exception as e:
            got[k] = None
            print(f"[now playing: Wikidata didn't answer ({str(e)[:80]})]")

    threads = [threading.Thread(target=run, args=kq, daemon=True) for kq in queries.items()]
    for th in threads:
        th.start()
    for th in threads:
        th.join(25)
    out = {"video": None, "videos": [], "tours": []}
    for b in got.get("video") or []:
        if _yt(_wv(b, "yt")):
            out["video"] = {"id": _yt(_wv(b, "yt"))}
            break
    seen = {out["video"]["id"]} if out["video"] else set()
    for b in got.get("videos") or []:
        yt, label = _yt(_wv(b, "yt")), _wv(b, "label")
        if yt and yt not in seen and _bare(label).lower() != title:
            seen.add(yt)
            out["videos"].append({"id": yt, "title": label, "year": _wv(b, "date")[:4]})
            if len(out["videos"]) >= 8:
                break
    today = time.strftime("%Y-%m-%d")
    lately = f"{int(today[:4]) - 1}{today[4:]}"
    tours = {}
    for b in got.get("tours") or []:
        start, end, name = _wv(b, "start")[:10], _wv(b, "end")[:10], _wv(b, "label")
        if name and ((end and end >= today) or (not end and start and start >= lately)):
            tours[name] = {"name": name, "start": start, "end": end, "url": _wv(b, "article")}
    out["tours"] = sorted(tours.values(), key=lambda x: x["start"] or "9999")[:3]
    if all(got.get(k) is not None for k in queries):          # only kept when Wikidata answered every question
        if len(_extras) > 300:
            _extras.clear()
        _extras[tid] = {"at": time.time(), "value": out}
    return out


# ---------------------------------------------------------------- Home's sections beyond your library
# Spotify keeps its own Daily Mixes, Discover Weekly, Release Radar and artist radios away from apps like this one,
# so the app makes its own from your listening: mixes from your top artists (grouped by sound), new releases from
# artists you follow and play, stations, and "more like" rows. A mix's songs are gathered when you open or play it.
# Since February 2026 Spotify no longer tells apps an artist's genres, so the genres (and who sounds like whom) come
# from Wikidata, matched by Spotify artist ID.
HUES = [0.52, 0.14, 0.99, 0.80, 0.33, 0.62, 0.07, 0.90, 0.45, 0.72, 0.25, 0.58]
GENERIC = {"pop", "rock", "rap", "hip", "hop", "music", "indie", "alternative", "modern", "classic", "new", "art", "dance", "contemporary"}
BROAD = ("pop music", "rock music", "hip hop music", "electronic music", "rhythm and blues", "jazz", "folk music",
         "country music", "soul music", "alternative rock", "pop rock", "classical music", "dance music", "singing",
         "contemporary music", "popular music", "world music", "instrumental music", "song", "vocal music")
GENRE_NAMES = {"electronic dance music": "edm", "rhythm and blues": "r&b", "contemporary r&b": "contemporary r&b",
               "hip hop music": "hip hop", "pop music": "pop", "rock music": "rock"}
DAY_PARTS = (("night", 0, 5), ("morning", 5, 12), ("afternoon", 12, 17), ("evening", 17, 21), ("night", 21, 24))
_feed = {"at": 0, "value": None}
_mixdefs = {}
_mixes = {}
_genre_cache = {}          # Spotify artist id -> genres (Wikidata's, when Spotify sends none)
_wd_rest = {"until": 0}    # Wikidata didn't answer: leave it be for a while rather than wait on it every time
_alike_cache = {}          # Spotify artist id -> [{"id", "name"}] artists who share their sound (Wikidata)


def _norm(s):
    import unicodedata
    s = unicodedata.normalize("NFKD", s or "")
    return re.sub(r"[^a-z0-9]+", " ", "".join(c for c in s if not unicodedata.combining(c)).lower()).strip()


def _gtitle(g):
    return " ".join({"Edm": "EDM", "Uk": "UK", "Us": "US", "Dj": "DJ", "Lgbtq+": "LGBTQ+"}.get(w, w) for w in g.title().split())


def _words(genres):
    return {w for g in genres for w in g.replace("-", " ").split() if w not in GENERIC}


def _genre_name(label):
    g = re.sub(r"\s+", " ", (label or "").strip().lower())
    if g in GENRE_NAMES:
        return GENRE_NAMES[g]
    return g[:-6] if g.endswith(" music") and len(g) > 10 else g


def _fill_genres(arts):
    """Gives each artist its genres: Spotify's when it sends them, otherwise Wikidata's (one question for everyone)."""
    need = [a for a in arts if a and not a.get("genres")]
    for a in need:
        if a["id"] in _genre_cache:
            a["genres"] = list(_genre_cache[a["id"]])
    ask = [a for a in need if a["id"] not in _genre_cache and re.fullmatch(r"[A-Za-z0-9]{22}", a["id"])][:80]
    if not ask or time.time() < _wd_rest["until"]:
        return
    ids = " ".join(_lit(a["id"]) for a in ask)
    try:
        rows = _wd(f'SELECT ?sid ?gl WHERE {{ VALUES ?sid {{ {ids} }} ?a wdt:P1902 ?sid; wdt:P136 ?g. '
                   f'?g rdfs:label ?gl. FILTER(LANG(?gl) = "en") }}')
    except Exception as e:
        print(f"Wikidata didn't answer about genres: {str(e)[:120]}")
        _wd_rest["until"] = time.time() + 600
        return
    found = {}
    for b in rows:
        g = _genre_name(_wv(b, "gl"))
        lst = found.setdefault(_wv(b, "sid"), [])
        if g and g not in lst:
            lst.append(g)
    for a in ask:
        # the most specific genres first ("dance-pop" says more than "pop")
        gs = sorted(found.get(a["id"]) or [], key=lambda g: (g in {"pop", "rock", "hip hop", "r&b", "electronic"}, -len(g)))[:8]
        _genre_cache[a["id"]] = gs
        a["genres"] = list(gs)


def _alike(aid):
    """Artists who share this one's sound (several of the same genres on Wikidata), the best known first."""
    if aid in _alike_cache:
        return _alike_cache[aid]
    if not re.fullmatch(r"[A-Za-z0-9]{22}", aid or "") or time.time() < _wd_rest["until"]:
        return []
    broad = ", ".join(_lit(x) for x in BROAD)
    q = f'''SELECT ?sid (SAMPLE(?nm) AS ?name) (COUNT(DISTINCT ?g) AS ?shared) (MAX(?links) AS ?fame) WHERE {{
      ?seed wdt:P1902 {_lit(aid)}; wdt:P136 ?g.
      ?g rdfs:label ?gl. FILTER(LANG(?gl) = "en" && !(STR(?gl) IN ({broad})))
      ?o wdt:P136 ?g; wdt:P1902 ?sid; wikibase:sitelinks ?links.
      FILTER(?o != ?seed && ?links >= 12)
      OPTIONAL {{ ?o rdfs:label ?nm. FILTER(LANG(?nm) = "en") }}
    }} GROUP BY ?sid ORDER BY DESC(?shared) DESC(?fame) LIMIT 24'''
    try:
        rows = _wd(q)
    except Exception as e:
        print(f"Wikidata didn't answer about similar artists: {str(e)[:120]}")
        _wd_rest["until"] = time.time() + 600
        return []
    out, seen = [], set()
    for b in rows:
        sid, nm = _wv(b, "sid"), _wv(b, "name")
        if sid and nm and sid != aid and nm.lower() not in seen and re.fullmatch(r"[A-Za-z0-9]{22}", sid):
            seen.add(nm.lower())
            out.append({"id": sid, "name": nm})
    if len(_alike_cache) > 200:
        _alike_cache.clear()
    _alike_cache[aid] = out
    return out


def _groups(arts, n=6, size=4):
    """Your top artists in groups that sound alike (by genre), the best-loved first."""
    left, out = list(arts), []
    while left and len(out) < n:
        seed = left.pop(0)
        g, w = set(seed["genres"]), _words(seed["genres"])
        group = [seed]
        for a in sorted(left, key=lambda a: -(2 * len(g & set(a["genres"])) + len(w & _words(a["genres"])))):
            if len(group) >= size:
                break
            if (g & set(a["genres"])) or (w & _words(a["genres"])) or not g:
                group.append(a)
        if len(group) == 1 and left:                  # nothing alike: the next favourite keeps it company
            group.append(left[0])
        for a in group[1:]:
            if a in left:
                left.remove(a)
        out.append(group)
    return out


def _pic(a):
    return (a.get("imageMid") or a.get("image") or "") if a else ""


def _mixdef(mid, kind, name, seeds, hue, genre="", label="", num="", **extra):
    m = {"id": mid, "kind": kind, "name": name, "seeds": [{"id": a.get("id") or "", "name": a["name"]} for a in seeds],
         "artists": [a["name"] for a in seeds], "image": next((_pic(a) for a in seeds if _pic(a)), ""),
         "hue": hue, "genre": genre, "label": label, "num": num}
    m.update(extra)
    _mixdefs[mid] = m
    return m


def _day_part(hour):
    return next(p for p, a, b in DAY_PARTS if a <= hour < b)


def _local_hour(stamp):
    try:
        from datetime import datetime
        return datetime.fromisoformat(stamp.replace("Z", "+00:00")).astimezone().hour
    except Exception:
        return None


def _who_played(recent, known, part=None):
    """The artists in what you played lately (only at this time of day, if `part`), the most played first."""
    count, names = {}, {}
    for t in recent:
        if part:
            h = _local_hour(t.get("playedAt") or "")
            if h is None or _day_part(h) != part:
                continue
        for a in (t.get("artists") or [])[:1]:
            if a.get("id"):
                count[a["id"]] = count.get(a["id"], 0) + 1
                names[a["id"]] = a.get("name") or ""
    order = sorted(count, key=lambda k: -count[k])
    return [known.get(k) or {"id": k, "name": names[k], "genres": []} for k in order]


def _artist_albums(aid, group, limit):
    r = safe(lambda: get(f"artists/{aid}/albums", include_groups=group, limit=limit)) or {}
    return [x for x in (album(i) for i in r.get("items") or []) if x]


def _parallel(jobs, timeout=20, workers=3):
    """Runs (key, fn) jobs a few at a time (not all at once: Spotify counts requests); gives {key: result}."""
    out, todo, lock = {}, list(jobs), threading.Lock()

    def run():
        while True:
            with lock:
                if not todo:
                    return
                k, fn = todo.pop(0)
            try:
                out[k] = fn()
            except Exception:
                out[k] = None

    threads = [threading.Thread(target=run, daemon=True) for _ in range(min(workers, len(todo)) or 1)]
    for th in threads:
        th.start()
    end = time.time() + timeout * max(1, len(jobs) / max(1, workers) / 4)
    for th in threads:
        th.join(max(0.1, end - time.time()))
    return out


CACHE_DIR = None      # the app sets this: Home's sections are kept on disk for a few hours (opening the app asks less)


def _feed_file():
    return os.path.join(CACHE_DIR, "home-feed.json") if CACHE_DIR else None


def _save_feed(value):
    path = _feed_file()
    if not path:
        return
    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump({"at": time.time(), "user": (me() or {}).get("id"), "value": value, "mixdefs": _mixdefs}, f)
    except Exception:
        pass


def _load_feed():
    path = _feed_file()
    try:
        with open(path, encoding="utf-8") as f:
            j = json.load(f)
    except Exception:
        return None
    if time.time() - (j.get("at") or 0) > 6 * 3600 or not j.get("value") or j.get("user") != (me() or {}).get("id"):
        return None
    _mixdefs.update(j.get("mixdefs") or {})
    with _lock:
        _feed.update(at=j["at"], value=j["value"])
    return j["value"]


def home_feed(force=False):
    """Home's sections: Made For (Daily Mixes, Discover Weekly, Release Radar), a mix for this time of day, Recents,
    New releases for you, Your top mixes, More like..., Recommended Stations and the big "made for you" cards."""
    with _lock:
        if not force and _feed["value"] and time.time() - _feed["at"] < 1800:
            return _feed["value"]
    if not force and not _feed["value"]:
        saved = _load_feed()
        if saved:
            return saved
    h = home()
    j = safe(lambda: get("me/top/artists", limit=40, time_range="medium_term")) or {}
    arts = [a for a in (artist(x) for x in j.get("items") or []) if a]
    if len(arts) < 6:                                   # not much listening yet: lean on recent favourites
        j = safe(lambda: get("me/top/artists", limit=40, time_range="short_term")) or {}
        arts += [a for a in (artist(x) for x in j.get("items") or []) if a and a["id"] not in {b["id"] for b in arts}]
    followed = h.get("artists") or []
    if not arts:                                        # no top artists at all: the ones you follow stand in
        arts = list(followed[:20])
    _fill_genres(arts + followed)
    known = {a["id"]: a for a in followed + arts}
    recent = h.get("_recent") or []

    daily = [_mixdef(f"daily{i + 1}", "daily", f"Daily Mix {i + 1}", grp, HUES[i], label="Daily Mix", num=f"{i + 1:02d}")
             for i, grp in enumerate(_groups(arts, 6, 4))]
    made = list(daily)
    if arts:
        made.append(_mixdef("discover", "discover", "Discover Weekly", arts[:6], 0.74, label="Discover Weekly"))

    # your top genres, weighted by how much you play each artist
    weight = {}
    for r, a in enumerate(arts):
        for g in a["genres"]:
            weight[g] = weight.get(g, 0) + 1 / (1 + r * 0.15)
    tops = []
    for g in sorted(weight, key=lambda k: -weight[k]):
        if any(_words([g]) and _words([g]) <= _words([t]) for t in tops):
            continue                                    # "uk metalcore" adds nothing after "metalcore"
        tops.append(g)
        if len(tops) >= 10:
            break
    top_mixes = []
    for i, g in enumerate(tops):
        members = [a for a in arts if g in a["genres"]][:5]
        if members:
            top_mixes.append(_mixdef("genre:" + g, "genre", f"{_gtitle(g)} Mix", members, HUES[(i + 3) % len(HUES)], genre=g, label=f"{_gtitle(g)} Mix"))
    if len(top_mixes) < 4:                              # few genres known: a mix per favourite artist fills in
        for i, a in enumerate(arts[:8]):
            if len(top_mixes) >= 8:
                break
            top_mixes.append(_mixdef("artist:" + a["id"], "artist", f"{a['name']} Mix", [a] + [b for b in arts if b is not a][:2],
                                     HUES[(i + 5) % len(HUES)], label=f"{a['name']} Mix"))

    stations = []
    for i, a in enumerate(arts[:12]):
        friends = [b for b in arts if b is not a and set(b["genres"]) & set(a["genres"])][:3]
        stations.append(_mixdef("station:" + a["id"], "station", f"{a['name']} Radio", [a], HUES[(i * 5) % len(HUES)], label="Radio",
                                **{"with": [b["name"] for b in friends], "withArt": [_pic(b) for b in friends if _pic(b)][:2],
                                   "artist": {"id": a["id"], "name": a["name"], "image": _pic(a)}}))

    # a mix for this time of day, from the artists you play around now
    hour = time.localtime().tm_hour
    part = _day_part(hour)
    day = time.strftime("%A")
    then = _who_played(recent, known, part)
    lately = _who_played(recent, known)
    timed = []
    if len(then) >= 2 or lately:
        seeds = (then if len(then) >= 2 else lately)[:5]
        timed.append(_mixdef("time", "time", f"{day} {part.title()}", seeds, 0.08 if part == "morning" else 0.6 if part == "night" else 0.95,
                             label=f"{day} {part}", part=part, day=day))
    timed.append(_mixdef("onrepeat", "onrepeat", "On Repeat", arts[:3], 0.92, label="On Repeat"))
    timed.append(_mixdef("rewind", "rewind", "Repeat Rewind", arts[3:6] or arts[:3], 0.55, label="Repeat Rewind"))
    if lately:
        timed.append(_mixdef("recent", "recent", "Recently Played Mix", lately[:5], 0.3, label="Recent Mix"))
    for m in top_mixes:                                 # and your top mixes that sound like right now
        if len(timed) >= 6:
            break
        if {s["id"] for s in m["seeds"]} & {a["id"] for a in then}:
            timed.append(m)

    out = {"made": made, "timed": timed, "part": part, "day": day, "topMixes": top_mixes, "stations": stations,
           "recents": shortcuts(recent, h.get("playlists") or [], arts + followed, h.get("albums") or [], 16, fill=False),
           "newReleases": [], "moreLike": [], "big": []}

    # new releases from artists you follow and play most (the last few months): their latest singles and albums
    who = []
    for a in followed[:5] + arts:
        if a["id"] not in {b["id"] for b in who}:
            who.append(a)
        if len(who) >= 10:
            break
    jobs = [((a["id"], "single"), (lambda a=a: _artist_albums(a["id"], "single", 3))) for a in who]
    jobs += [((a["id"], "album"), (lambda a=a: _artist_albums(a["id"], "album", 4))) for a in who]
    likes = [a for a in arts[:2]]
    got = _parallel(jobs + [(("alike", a["id"]), (lambda a=a: _alike(a["id"]))) for a in likes])
    since = time.strftime("%Y-%m-%d", time.localtime(time.time() - 120 * 86400))
    seen, fresh = set(), []
    found = [x for k, v in got.items() if k[1] in ("single", "album") for x in (v or [])]
    for x in sorted(found, key=lambda x: x["release"] or "", reverse=True):
        k = (_bare(x["name"]).lower(), x["artistId"])
        if x["release"] >= since and k not in seen:
            seen.add(k)
            fresh.append(x)
    out["newReleases"] = fresh[:14]
    if fresh:
        made.append(_mixdef("radar", "radar", "Release Radar", [known[x["artistId"]] for x in fresh if x["artistId"] in known][:4] or arts[:3],
                            0.2, label="Release Radar", albums=[x["id"] for x in fresh[:15]],
                            albumInfo={x["id"]: x for x in fresh[:15]}))

    # "More like ..." your two favourite artists: their station, their latest albums, and artists who sound alike
    def more_like(a):
        albums = (got.get((a["id"], "album")) or [])[:3]
        mine = [b for b in arts if b is not a and set(b["genres"]) & set(a["genres"])][:4]
        ids = {a["id"]} | {b["id"] for b in mine}
        extra = [x for x in (got.get(("alike", a["id"])) or []) if x["id"] not in ids and x["id"] not in known][:3]
        looked = _parallel([(x["id"], (lambda x=x: artist(safe(lambda: get(f"artists/{x['id']}"))))) for x in extra], 15)
        alike = [looked[x["id"]] for x in extra if looked.get(x["id"])]
        st = next((m for m in stations if m["id"] == "station:" + a["id"]), None)
        return {"artist": {"id": a["id"], "name": a["name"], "image": _pic(a)}, "station": st, "albums": albums,
                "artists": (alike[:2] + mine[:2] + alike[2:] + mine[2:])[:8]}

    out["moreLike"] = [more_like(a) for a in likes]
    # the big cards: made for you, for fans of one of your favourites, and from what you played lately
    big = []
    disc = _mixdefs.get("discover")
    if disc and disc in made:
        big.append({"caption": "Made for you", "mix": disc})
    fan = stations[2] if len(stations) > 2 else stations[0] if stations else None
    if fan:
        big.append({"caption": f"For fans of {fan['artist']['name']}", "mix": fan})
    if _mixdefs.get("recent") in timed:
        big.append({"caption": "Based on your recent listening", "mix": _mixdefs["recent"]})
    out["big"] = big
    with _lock:
        _feed.update(at=time.time(), value=out)
    _save_feed(out)
    return out


def forget_feed():
    with _lock:
        _feed.update(at=0, value=None)
    _mixes.clear()
    try:
        os.remove(_feed_file() or "")
    except OSError:
        pass


def _top_tracks(span):
    j = safe(lambda: get("me/top/tracks", limit=50, time_range=span)) or {}
    return [t for t in (track(x) for x in j.get("items") or []) if t and not t["local"]]


def _songs_by(name, offset=0):
    j = safe(lambda: get("search", q=f'artist:"{name}"', type="track", limit=10, offset=offset)) or {}
    # the search also finds songs that only mention the name: keep the artist's own
    low = _norm(name)
    return [t for t in (track(x) for x in (j.get("tracks") or {}).get("items") or [])
            if t and not t["local"] and any(_norm(a["name"]) == low for a in t["artists"])]


def _interleave(queues, cap=50, skip=None):
    out, keys = [], set(skip or ())
    while len(out) < cap and any(queues):
        for q in queues:
            while q:
                t = q.pop(0)
                k = (_bare(t["title"]) + "|" + t["artist"]).lower()
                if t["uri"] in keys or k in keys:
                    continue
                keys.update((t["uri"], k))
                out.append(t)
                break
            if len(out) >= cap:
                break
    return out


def _known_songs(h):
    keys = set()
    for t in (h.get("topTracks") or []) + (h.get("liked") or []) + (h.get("_recent") or []):
        keys.update((t["uri"], (_bare(t["title"]) + "|" + t["artist"]).lower()))
    return keys


def _discover(m, h):
    """New to you: songs by artists who sound like your favourites, but that you don't play yet."""
    seeds = m["seeds"][:6]
    mine = {a["id"] for a in (h.get("topArtists") or []) + (h.get("artists") or [])} | {s["id"] for s in seeds}
    mine_names = {a["name"].lower() for a in (h.get("topArtists") or []) + (h.get("artists") or [])} | {s["name"].lower() for s in seeds}
    got = _parallel([(s["id"], (lambda s=s: _alike(s["id"]))) for s in seeds])
    pools = [[x for x in (got.get(s["id"]) or []) if x["id"] not in mine and x["name"].lower() not in mine_names] for s in seeds]
    picks, names = [], set()
    while len(picks) < 12 and any(pools):
        for p in pools:
            while p:
                x = p.pop(0)
                if x["name"].lower() not in names:
                    names.add(x["name"].lower())
                    picks.append(x["name"])
                    break
    if len(picks) < 6:                                   # Wikidata had little to say: your less-played favourites
        for a in (h.get("topArtists") or [])[8:]:
            if a["name"].lower() not in names and len(picks) < 10:
                names.add(a["name"].lower())
                picks.append(a["name"])
    found = _parallel([(n, (lambda n=n: _songs_by(n)[:4])) for n in picks])
    known = _known_songs(h)
    queues = [[t for t in (found.get(n) or []) if t["uri"] not in known][:3] for n in picks]
    out = _interleave(queues, 36, known)
    random.shuffle(out)
    return out


def _radar(m):
    """The newest songs from the artists you follow and play: a song or two from each new release."""
    info = m.get("albumInfo") or {}

    def songs(aid):
        al = info.get(aid) or {}
        r = safe(lambda: get(f"albums/{aid}/tracks", limit=3 if al.get("type") != "Album" else 2)) or {}
        return [t for t in (track(x, album=al) for x in r.get("items") or []) if t and not t["local"]] if al else []

    got = _parallel([(aid, (lambda aid=aid: songs(aid))) for aid in m.get("albums") or []])
    queues = [list(got.get(aid) or []) for aid in m.get("albums") or []]
    out = []
    for q in queues:                                     # newest release first, then a second song from each
        out += q[:1]
    for q in queues:
        out += q[1:3]
    return _interleave([out], 40)


def _artist_mix(m, h):
    """A mix's songs: the artists' own, the ones you already play first, then their others (and their genre's)."""
    ids = {s["id"] for s in m["seeds"]}
    mine = [t for t in (h.get("topTracks") or []) + (h.get("liked") or []) if ids & {a["id"] for a in t["artists"]}]
    jobs = [(f"a{i}", (lambda s=s: _songs_by(s["name"]))) for i, s in enumerate(m["seeds"][:5])]
    if m["kind"] == "station" and m["seeds"]:
        lead = m["seeds"][0]
        friends = list(m.get("with") or [])
        if len(friends) < 4:
            friends += [x["name"] for x in _alike(lead["id"]) if x["name"] not in friends][:4 - len(friends)]
        jobs.append(("a0b", lambda: _songs_by(lead["name"], 10)))
        jobs += [(f"w{i}", (lambda nm=nm: _songs_by(nm))) for i, nm in enumerate(friends[:4])]
    if m.get("genre"):
        g = m["genre"]
        jobs += [("g0", lambda: [t for t in (track(x) for x in ((safe(lambda: get("search", q=f'genre:"{g}"', type="track", limit=10)) or {}).get("tracks") or {}).get("items") or []) if t and not t["local"]])]
    got = _parallel(jobs)
    queues = [list(mine)] + [list(got.get(k) or []) for k, _ in jobs]
    if m["kind"] == "station":                           # the lead artist comes round every other song
        lead_q = (got.get("a0") or []) + (got.get("a0b") or [])
        others = [list(got.get(k) or []) for k, _ in jobs if k not in ("a0", "a0b")]
        out = _interleave([list(mine)[:4], lead_q] + others, 50)
        return out
    out = _interleave(queues, 50)
    head, rest = out[:1], out[1:]
    random.shuffle(rest)                                 # a mix, not a discography
    return head + rest


def mix(mid):
    """A mix's songs, gathered now (kept for half an hour)."""
    hit = _mixes.get(mid)
    if hit and time.time() - hit["at"] < 1800:
        return hit["value"]
    m = _mixdefs.get(mid)
    if not m:
        home_feed()
        m = _mixdefs.get(mid)
    if not m and mid.startswith("station:"):          # any artist's radio (from an artist's ••• menu)
        a = artist(safe(lambda: get(f"artists/{mid[8:]}")))
        if a:
            _fill_genres([a])
            m = _mixdef(mid, "station", f"{a['name']} Radio", [a], HUES[len(a['name']) % len(HUES)], label="Radio",
                        **{"with": [], "withArt": [], "artist": {"id": a["id"], "name": a["name"], "image": _pic(a)}})
    if not m:
        return None
    h = home()
    kind = m["kind"]
    if kind == "onrepeat":
        tracks = _top_tracks("short_term") or list(h.get("topTracks") or [])
    elif kind == "rewind":
        now_ = {t["uri"] for t in _top_tracks("short_term")}
        tracks = [t for t in _top_tracks("long_term") if t["uri"] not in now_] or _top_tracks("medium_term")
    elif kind == "radar":
        tracks = _radar(m)
    elif kind == "discover":
        tracks = _discover(m, h)
    else:
        tracks = _artist_mix(m, h)
    value = dict(m, tracks=tracks[:50], description=_mix_line(m))
    value.pop("albumInfo", None)
    if tracks:
        if len(_mixes) > 40:
            _mixes.clear()
        _mixes[mid] = {"at": time.time(), "value": value}
    return value


def _mix_line(m):
    names = m["artists"][:3]
    kind = m["kind"]
    if kind == "station":
        w = m.get("with") or []
        return f"With {', '.join(w)} and more" if w else f"Songs by {names[0]} and artists like them"
    if kind == "discover":
        return "Songs from artists who sound like your favourites, picked by this app from your listening."
    if kind == "radar":
        return "The newest songs from artists you follow and play."
    if kind == "onrepeat":
        return "The songs you can't stop playing lately."
    if kind == "rewind":
        return "Songs you loved a while back and haven't played lately."
    if kind == "time":
        return f"Your {m.get('part', 'day')} sound: " + (", ".join(names) + " and more" if names else "the artists you play around now")
    if kind == "recent":
        return "From what you played lately: " + ", ".join(names) + " and more"
    return (", ".join(names) + " and more") if names else ""

"""
Cara's brain for the PC app, ported from the iPhone app.

What she talks about (43 topics), how she says it (styles, openings, landings, voice tags), how long she talks,
a memory that keeps her from repeating herself (saved on this PC), the station named after whatever's playing,
and breaks where her co-host Scratch joins her. The lists themselves live in brain_data.json, shared with the iPhone app.
"""
import datetime
import json
import os
import random
import re
import sys
import tempfile
import threading
import time
import wave
from urllib.parse import quote, quote_plus

import feedparser
import requests

dj = None  # the engine (live_dj_free); set by attach()


def attach(engine):
    global dj
    dj = engine


def _here(name):
    base = getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(base, name)


def _app_dir():
    d = os.environ.get("DJ_APP_DIR") or os.path.join(os.environ.get("APPDATA") or os.path.expanduser("~"), "NonStopPopDJ")
    os.makedirs(d, exist_ok=True)
    return d


with open(_here("brain_data.json"), encoding="utf-8") as _f:
    D = json.load(_f)

SEGMENTS = D["segments"]
FORMATS = D["formats"]          # [id, how]
OPENINGS = D["openings"]        # [id, how, needs]
ENDINGS = D["endings"]
TAGS = D["tags"]
PERSONA = D["persona"]
RULES = D["rules"]
CO_NAME = D["coName"]                       # "MC Scratch"
CO_SHORT = D.get("coShort") or CO_NAME      # what everyone calls him: "Scratch"
CO_LABEL = D.get("coLabel") or CO_NAME.upper()   # his label in their scripts: "SCRATCH"
CO_PERSONA = D["coPersona"]
CO_BIBLE = D["coBible"]
CO_DEFAULT_VOICE = D["coDefaultVoice"]
CO_LANGUAGE_ON = D.get("coLanguageOn", "")
CO_LANGUAGE_OFF = D.get("coLanguageOff") or "HIS LANGUAGE: clean, no swearing. Cara never swears either."
_STRONG_SWEARS = {"ass", "asses", "asshole", "assholes", "bitch", "bitches", "bastard", "bastards",
                  "piss", "pissed", "damn", "damned", "dammit", "goddamn", "goddammit"}
_MASKED = re.compile(r"[A-Za-z]\*+[A-Za-z]|\b[A-Za-z]\*{2,}")


def co_language():
    """How Scratch talks: gritty when his cursing is on (the default), clean when it's off (the app's CO-HOST row)."""
    return CO_LANGUAGE_ON if getattr(dj, "COHOST_SWEARS", True) and CO_LANGUAGE_ON else CO_LANGUAGE_OFF


def swears(text):
    """The curse words in a line (to keep Cara clean, and Scratch too when his cursing is off)."""
    out = []
    for w in words(text):
        w = w.strip("'")
        if w.startswith(("fuck", "motherfuck", "shit", "bullshit")) or w in _STRONG_SWEARS:
            out.append(w)
    return out


def too_far(text):
    """Words he doesn't use even with his cursing on: classy, not crude."""
    return any(w.startswith(("bitch", "motherfuck")) for w in swears(text))

MOOD_LINES = {
    "chill": "CHILL: laid-back, warm and smooth, with dry wit and fewer exclamation marks. Still playful, never sleepy.",
    "normal": "NORMAL: her usual bubbly, cheeky, quick self.",
    "unhinged": "UNHINGED: maximum playful chaos. Over-the-top drama, absurd tangents, gleeful mock outrage and silly voices described in words, but never mean.",
}
SITUATIONS = {
    "silent": "The music has stopped and the floor is all hers. She launches straight into her segment with confidence (never mention the silence or the music stopping) and brings the next song in at the end.",
    "intro": "The next song has just started and she's talking over its opening. A quick, punchy drop-in (the song may kick in straight away, so never ramble), and she brings the song in at the end.",
    "talkover": "The current song is fading out under her voice. She rides the ending and rolls straight into the next song, no goodbyes or sign-offs.",
    "fadeout": "The current song is fading down under her voice. She takes over and rolls straight into the next song, no goodbyes or sign-offs.",
}
DUO_SITUATIONS = {
    "silent": "The music has stopped and the studio is theirs. They dive straight in (never mention the silence or the music stopping) and bring the next song in at the end.",
    "intro": "The next song has just started and they're talking over its opening. A quick exchange (the song may kick in straight away, so never ramble), then they let it play.",
    "talkover": "The current song is fading out under them. They wrap up as it ends and roll straight into the next song, no goodbyes.",
    "fadeout": "The current song is fading down under them. They roll straight into the next song at the end, no goodbyes.",
}

DEATH = re.compile(r"\b(?:kill|killed|killing|dead|death|deaths|deadly|die|dies|died|dying|fatal|fatally|fatality|fatalities|passed away|passes away|obituary|obituaries|funeral|memorial|vigil|mourn|mourning|mourners|grief|coroner|autopsy|remains|body|bodies|drowned|drowning|perished|lost (?:his|her|their) life|tragic|tragedy|injured|injuries|injury|hospitalized|crash|crashed|collision|rip)\b", re.I)
GOSSIP_SKIP = re.compile(r"\b(?:lawsuit|sues|sued|suing|court|divorce|rehab|hospital|hospitalized|hospitalised|affair|cheating|leak|leaked|nude|naked|racist|sexual|allegations|alleged|accused|custody|restraining|lawsuits|scandal|feud|passes|obituary|tribute|mourning|grief|funeral)\b", re.I)


def mentions_death(t):
    return bool(DEATH.search(t or ""))


def chattiness():
    return getattr(dj, "CHATTINESS", "chatty") if dj else "chatty"


def expressive():
    try:
        return bool(dj.eleven_expressive()) and getattr(dj, "TTS_ENGINE", "") == "elevenlabs"
    except Exception:
        return False


# ---------------------------------------------------------------- memory (saved on this PC)
class Memory:
    KEEP = {"segments": 40, "formats": 20, "openings": 20, "endings": 20, "popins": 20, "tags": 12}

    def __init__(self):
        self.path = os.path.join(_app_dir(), "cara-memory.json")
        self.lock = threading.RLock()
        self.s = {"breaks": [], "segments": [], "formats": [], "openings": [], "endings": [], "tags": [],
                  "popins": [], "facts": {}, "used": {}}
        try:
            with open(self.path, encoding="utf-8") as f:
                self.s.update(json.load(f))
        except Exception:
            pass

    def save(self):
        with self.lock:
            try:
                tmp = self.path + ".tmp"
                with open(tmp, "w", encoding="utf-8") as f:
                    json.dump(self.s, f)
                os.replace(tmp, self.path)
            except Exception:
                pass

    @property
    def recent(self):
        return list(self.s["breaks"])

    @property
    def last_break(self):
        return self.s["breaks"][-1] if self.s["breaks"] else None

    def fresh(self, name, items):
        """Something from the list she hasn't used yet (the list starts over once she's been through it)."""
        with self.lock:
            used = set(self.s["used"].get(name, []))
            if len(used) >= len(items):
                used = set()
            left = [i for i in range(len(items)) if i not in used] or [0]
            i = random.choice(left)
            used.add(i)
            self.s["used"][name] = sorted(used)
            return items[i]

    def is_fresh(self, key, days=3):
        t = self.s["facts"].get(key.lower())
        return t is None or time.time() - t > days * 86400

    def mark_used(self, key):
        with self.lock:
            self.s["facts"][key.lower()] = time.time()
            cutoff = time.time() - 14 * 86400
            self.s["facts"] = {k: v for k, v in self.s["facts"].items() if v > cutoff}

    def last(self, kind, n):
        return self.s.get(kind, [])[-n:]

    def remember(self, text, segment=None, fmt=None, opening=None, ending=None, tags=(), popin=None):
        with self.lock:
            self.s["breaks"] = (self.s["breaks"] + [text])[-80:]
            for kind, v in (("segments", segment), ("formats", fmt), ("openings", opening), ("endings", ending), ("popins", popin)):
                if v:
                    self.s[kind] = (self.s[kind] + [v])[-self.KEEP[kind]:]
            for t in tags:
                self.s["tags"] = (self.s["tags"] + [t])[-self.KEEP["tags"]:]
        self.save()


MEM = Memory()


# ---------------------------------------------------------------- never repeating herself
STOP_WORDS = set(D["stopWords"])


def words(t):
    t = re.sub(r"\[[^\]]*\]", " ", (t or "").lower())
    return [w for w in re.split(r"[^\w']+", t) if w and w != "'"]


def grams(w, n, skip):
    out = set()
    for i in range(0, len(w) - n + 1):
        g = w[i:i + n]
        if all(x in STOP_WORDS for x in g) or any(x in skip for x in g):
            continue
        out.add(" ".join(g))
    return out


def opener(t):
    return " ".join(words(t)[:2])


def worn_out(recent, skip):
    count = {}
    for r in recent[-40:]:
        for g in grams(words(r), 3, skip):
            count[g] = count.get(g, 0) + 1
    return [g for g, c in sorted(count.items(), key=lambda kv: -kv[1]) if c >= 2][:30]


def says_label(text):
    """'Slogan' and friends only show up when she reads out what she was asked to do."""
    for w in words(text):
        if w in ("slogan", "slogans", "tagline", "taglines"):
            return w
    return None


def problem(text, recent, skip):
    """Why a draft can't be used (None when it's fine)."""
    w = words(text)
    if not w:
        return "It was empty."
    if mentions_death(text):
        return "It mentioned death or dying (even as a figure of speech). Leave that out completely."
    first = w[0]
    if first in ("whoa", "woah", "wow", "oh", "ooh", "ah", "shh", "shhh"):
        return f"It opened with '{first}'. Open with a real word instead."
    if "gasp" in w or "sigh" in w or "sighs" in w:
        return "It used 'gasp' or 'sigh'. Leave those out."
    if any(x.startswith(("snort", "sniff")) for x in w):
        return "It had a snort or a sniff in it. Leave nose noises out completely."
    lab = says_label(text)
    if lab:
        return f"It said the word '{lab}'. Never call anything a slogan or tagline: just say the line itself."
    op = opener(text)
    if op and op in [opener(r) for r in recent[-30:]]:
        return f'It opened with "{op}", which you\'ve used before. Open completely differently.'
    if first in [(words(r) or [""])[0] for r in recent[-6:]]:
        return f'It started with the same first word ("{first}") as a recent break. Start differently.'
    mine = grams(w, 4, skip)
    for r in recent[-40:]:
        hit = mine & grams(words(r), 4, skip)
        if hit:
            return f'It reused the phrase "{next(iter(hit))}" from an earlier break. Say it in completely new words.'
    return None


def memory_block(skip):
    recent = MEM.recent
    if not recent:
        return "- This is your first break today. Make it count."
    lines = ["- Your most recent breaks, newest first. Never reuse their openings, jokes, phrases, angles, facts or structure:"]
    for i, b in enumerate(reversed(recent[-12:])):
        lines.append(f'  {i + 1}. "{b}"')
    ops = list(dict.fromkeys(o for o in (opener(r) for r in recent[-30:]) if o))[:30]
    if ops:
        lines.append("- Never start with any of these: " + ", ".join(f'"{o}"' for o in ops))
    worn = worn_out(recent, skip)
    if worn:
        lines.append("- Phrases you've worn out (don't use them): " + ", ".join(f'"{x}"' for x in worn))
    return "\n".join(lines)


def reusable(ctx, extra=()):
    s = {"non", "stop", "pop", "cara", "fm", "station"} | set(extra)
    s |= set(words(STATION.name()))
    for t in (ctx.get("next"), ctx.get("last")):
        if t:
            s |= {w for w in words(" ".join([t.get("title", ""), t.get("artist", ""), t.get("album", "")])) if len(w) > 2}
    s |= set(words(getattr(dj, "CITY", "")))
    return s


def clean_tags(text, allowed):
    used = []

    def keep(m):
        tag = m.group(1).strip().lower()
        if tag in allowed:
            used.append(tag)
            return "[" + tag + "]"
        return " "

    out = re.sub(r"\[([^\]]*)\]", keep, text or "")
    out = re.sub(r"\s{2,}", " ", out).strip().strip('"').strip()
    return out, used


# snorts and sniffs written as a voice tag or a stage direction ("[snorts]", "(sniffs)", "*snort*")
NOSE = re.compile(r"[\[\(\*]\s*(?:a |one |little |small |loud |quick )?(?:snort|sniff)\w*(?:[- ]\w+){0,3}\s*[\]\)\*]\s*", re.I)


def tidy(t):
    t = NOSE.sub("", t or "")
    try:
        return dj.tidy(t)
    except Exception:
        return re.sub(r"\s{2,}", " ", t).strip()


def gemini(prompt):
    key = os.environ.get("GEMINI_API_KEY")
    if not key or getattr(dj, "MODE", "gemini") != "gemini":
        return None
    models = getattr(dj, "GEMINI_MODELS", None) or ["gemini-3.5-flash-lite", "gemini-3.6-flash", "gemini-3.5-flash"]
    for model in models:
        try:
            r = requests.post(
                f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent",
                headers={"x-goog-api-key": key, "Content-Type": "application/json"},
                # on-air banter (Scratch's shade, a roast battle) can read like harassment to the filter; only block the clear cases
                json={"contents": [{"parts": [{"text": prompt}]}],
                      "safetySettings": [{"category": "HARM_CATEGORY_HARASSMENT", "threshold": "BLOCK_ONLY_HIGH"}]},
                timeout=30,
            )
            r.raise_for_status()
            j = r.json()
            c = (j.get("candidates") or [{}])[0]
            parts = (c.get("content") or {}).get("parts") or []
            text = (parts[0].get("text") or "").strip() if parts else ""
            if text:
                return text
            # nothing came back: say why, so a held-back draft shows up in the log
            why = c.get("finishReason") or (j.get("promptFeedback") or {}).get("blockReason") or "empty reply"
            print(f"Gemini model {model} wrote nothing ({why})")
        except Exception as e:
            print(f"Gemini model {model} failed: {e}")
    return None


# ---------------------------------------------------------------- reading the room
# Now and then a DJ makes one quick, playful guess about the listener from the song they picked
# ("Who hurt you, Yakima?"), going by its title and what its lyrics are about. The lyrics come from
# LRCLIB, once per song, and are never read out or quoted.
_LYRICS = {}
_LYRICS_LOCK = threading.Lock()


def song_lyrics(info):
    """A song's lyrics as plain text ("" when there are none)."""
    if not info or not info.get("title"):
        return ""
    key = info["title"] + "|" + (info.get("artist") or "")
    with _LYRICS_LOCK:
        if key in _LYRICS:
            return _LYRICS[key]
    synced = plain = ""
    try:
        q = {"track_name": info["title"], "artist_name": info.get("artist") or ""}
        if info.get("album"):
            q["album_name"] = info["album"]
        r = requests.get("https://lrclib.net/api/get", params=q, timeout=8,
                         headers={"User-Agent": "NonStopPopDJ (https://github.com/frankfigueroa1999-del/cara-dj)"})
        if r.ok:
            j = r.json()
            synced, plain = j.get("syncedLyrics") or "", j.get("plainLyrics") or ""
        if not synced and not plain:
            r = requests.get("https://lrclib.net/api/search", params={"track_name": info["title"], "artist_name": info.get("artist") or ""}, timeout=8,
                             headers={"User-Agent": "NonStopPopDJ (https://github.com/frankfigueroa1999-del/cara-dj)"})
            if r.ok:
                for item in r.json() or []:
                    if item.get("plainLyrics"):
                        plain = item["plainLyrics"]
                        break
                    if not synced and item.get("syncedLyrics"):
                        synced = item["syncedLyrics"]
    except Exception as e:
        print(f"[lyrics lookup failed: {e}]")
        return ""          # not cached, so the next break can try again
    if not plain and synced:
        plain = "\n".join(re.sub(r"^(\[[^\]]*\])+", "", l).strip() for l in synced.splitlines())
    plain = "\n".join(l.strip() for l in plain.splitlines() if l.strip())
    with _LYRICS_LOCK:
        if len(_LYRICS) > 200:
            _LYRICS.clear()
        _LYRICS[key] = plain
    return plain


_EXCLUDED = {"mtime": None, "keys": set()}


def taste_excluded(info):
    """Songs the listener took out of their taste profile (right-click a song in the app): the DJs read nothing into them."""
    path = os.path.join(_app_dir(), "taste-excluded.json")
    try:
        mtime = os.path.getmtime(path)
    except OSError:
        return False
    if mtime != _EXCLUDED["mtime"]:
        try:
            with open(path, encoding="utf-8") as f:
                _EXCLUDED["keys"] = {k for k in json.load(f) if isinstance(k, str)}
        except Exception:
            _EXCLUDED["keys"] = set()
        _EXCLUDED["mtime"] = mtime
    return f'{info.get("title") or ""}|{info.get("artist") or ""}'.lower() in _EXCLUDED["keys"]


def song_read(info):
    """The song the listener picked, and what it's about. None when there's no song to read."""
    if not info or not info.get("title"):
        return None
    if taste_excluded(info):
        return None
    full = song_lyrics(info)
    if full and mentions_death(full):
        full = ""          # a heavy song: they go on the title alone
    excerpt = " / ".join(full.splitlines())[:900] if full else ""
    return {"info": info, "lyrics": full, "excerpt": excerpt}


def town_name():
    city = getattr(dj, "CITY", "Yakima, Washington")
    return city.split(",")[0].strip() or city


def read_block(r, which, duo=False):
    """What the prompt says when they read the room. `which` is "the song that just played", "the song that's starting" and so on."""
    town = town_name()
    i = r["info"]
    song = f'"{i["title"]}" by {i.get("artist") or "someone"}'
    who = "One of them opens" if duo else "Open"
    after = "the other piles on or sticks up for them, then they move on" if duo else "then move straight on"
    s = (f"\n- Read the room: the listener picked {which}, {song}. {who} with ONE quick, playful jab about what that choice says about them, "
         f"going by the title and what the song's about (a heartbreak song: \"Who hurt you, {town}?\"; a revenge anthem: \"Remind me never to cross you\"; "
         f"a love song: \"Somebody's got a crush\"; a hype song: \"Somebody's feeling dangerous today\"), in brand-new words; {after}.")
    s += ("\n- Keep the read light and affectionate, like a friend clocking your playlist: love life, mood, being in your feelings, main-character energy, "
          "harmless mischief. Never guess at anything heavy or personal (mental health, drinking or drugs, money trouble, bodies, anything sexual).")
    if r["excerpt"]:
        s += f"\n- What the song's about, from its lyrics (only so you know; never quote, sing or closely paraphrase a line): {r['excerpt']}"
    return s


def quotes_lyrics(text, lyrics, title):
    """True when a draft quotes the song: five words in a row from its lyrics (the title doesn't count)."""
    lw, w = words(lyrics), words(text)
    if len(lw) < 5 or len(w) < 5:
        return False
    grams = {" ".join(lw[i:i + 5]) for i in range(len(lw) - 4)}
    in_title = " " + " ".join(words(title)) + " "
    for i in range(len(w) - 4):
        g = w[i:i + 5]
        if all(x in STOP_WORDS for x in g):
            continue
        j = " ".join(g)
        if j in grams and f" {j} " not in in_title:
            return True
    return False


def time_to_read():
    """Whether this break reads the room: about 3 in 10, never two in a row."""
    return random.random() < 0.3 and "read" not in MEM.last("openings", 2) and "read" not in MEM.last("popins", 1)


def read_which(style, ctx):
    if style == "intro":
        return "the song that's starting"
    return "the song that just played" if ctx.get("last") else "the song coming up next"


def fresh_draft(prompt, skip, read=None):
    """Asks Gemini, checks the draft against her memory and rules, rewrites up to twice."""
    feedback, best = "", None
    for attempt in range(3):
        ask = prompt if not feedback else prompt + f"\n\nYour previous draft can't be used: {feedback} Write a completely new one."
        raw = gemini(ask)
        if raw is None:
            break
        text, used = clean_tags(tidy(raw), set(TAGS))
        if not text:
            continue
        if read and read["lyrics"] and quotes_lyrics(text, read["lyrics"], read["info"]["title"]):
            print(f"[rewrite {attempt + 1}: quoted the lyrics]")
            feedback = "It quoted the song's lyrics. Never quote them: react to what the song's about in your own words."
            continue
        why = problem(text, MEM.recent, skip)
        if why:
            print(f"[rewrite {attempt + 1}: {why}]")
            feedback = why
            if best is None and not mentions_death(text) and not says_label(text):
                best = (text, used)
            continue
        return text, used
    return best


# ---------------------------------------------------------------- the station is named after whatever's playing
FALLBACK = "Non Stop Pop"


def _clean_name(raw):
    out = []
    for ch in raw or "":
        o = ord(ch)
        emoji = o >= 0x1F000 or 0x2600 <= o <= 0x27BF or o in (0xFE0F, 0x200D) or 0x2190 <= o <= 0x21FF
        if not emoji and (ch.isalnum() or ch in "'’&!?.,-+:/()$#@%"):
            out.append("'" if ch == "’" else ch)
        else:
            out.append(" ")
    name = " ".join("".join(out).split()).strip(" -:/.,+")
    if len(name) > 40:
        kept = []
        for w in name.split(" "):
            if len(" ".join(kept + [w])) > 36:
                break
            kept.append(w)
        name = " ".join(kept) if kept else name[:36]
    return name if any(c.isalnum() for c in name) else ""


def full_name(name):
    last = name.split(" ")[-1].lower() if name else ""
    return name if last in ("fm", "am", "radio", "station") else name + " FM"


def _key(uri):
    parts = (uri or "").split(":")
    if "collection" in parts:
        return "spotify:collection"
    for kind in ("playlist", "album", "artist", "show"):
        if kind in parts:
            i = parts.index(kind)
            if i + 1 < len(parts):
                return f"spotify:{kind}:{parts[i + 1]}"
    return uri or ""


class Station:
    def __init__(self):
        self.uri = ""
        self.raw = ""
        self.at_last_break = ""
        self.path = os.path.join(_app_dir(), "context-names.json")
        self.lock = threading.Lock()
        self.ulock = threading.Lock()
        try:
            with open(self.path, encoding="utf-8") as f:
                self.names = json.load(f)
        except Exception:
            self.names = {}
        self.tries = 0
        self.retry_at = 0.0

    def update(self, context):
        """Called with Spotify's playback 'context' each time the app checks what's playing."""
        uri = (context or {}).get("uri") or ""
        with self.ulock:
            if uri != self.uri:
                was = self.full()
                self.uri, self.tries, self.retry_at = uri, 0, 0.0
                self.raw = self.names.get(_key(uri), "Liked Songs" if _key(uri) == "spotify:collection" else "")
                if uri and not self.raw:
                    self.retry_at = time.time() + 15
                    threading.Thread(target=self._look_up, args=(uri,), daemon=True).start()
                elif self.full() != was:
                    print(f"[station: {self.full()}]")
            elif uri and not self.raw and self.tries < 4 and time.time() >= self.retry_at:
                self.retry_at = time.time() + 15
                threading.Thread(target=self._look_up, args=(uri,), daemon=True).start()

    def remember(self, uri, name):
        """A name we already know (you picked it in the app), so there's nothing to look up."""
        k, name = _key(uri), (name or "").strip()
        if not k or not name:
            return
        with self.lock:
            if len(self.names) >= 150:
                self.names = {}
            self.names[k] = name
            try:
                with open(self.path, "w", encoding="utf-8") as f:
                    json.dump(self.names, f)
            except Exception:
                pass

    def _look_up(self, uri):
        self.tries += 1
        self.retry_at = time.time() + (15 if self.tries < 3 else 120)
        k = _key(uri)
        name = ""
        try:
            kind, cid = k.split(":")[1], k.split(":")[2]
            if kind == "playlist":
                name = dj.sp.playlist(cid, fields="name").get("name", "")
            elif kind == "album":
                name = dj.sp.album(cid).get("name", "")
            elif kind == "artist":
                name = dj.sp.artist(cid).get("name", "")
        except Exception:
            name = ""
        if uri == self.uri and name:
            self.raw = name
            with self.lock:
                if len(self.names) >= 150:
                    self.names = {}
                self.names[k] = name
                try:
                    with open(self.path, "w", encoding="utf-8") as f:
                        json.dump(self.names, f)
                except Exception:
                    pass
            print(f"[station: {self.full()}]")

    def name(self):
        n = _clean_name(self.raw) if self.uri else ""
        return n or FALLBACK

    def full(self):
        return full_name(self.name())

    def note(self):
        n = self.name()
        if n == FALLBACK:
            return ""
        kind = _key(self.uri).split(":")[1] if ":" in _key(self.uri) else ""
        return {"playlist": f'the playlist "{n}"', "album": f'the album "{n}"', "artist": f"songs by {n}",
                "collection": "the listener's Liked Songs"}.get(kind, f'"{n}"')

    def switched_from(self):
        """The station's old name if it changed since her last break (she welcomes you to the new one)."""
        now, before = self.name(), self.at_last_break
        self.at_last_break = now
        if before and before != now and FALLBACK not in (before, now):
            return before
        return None


STATION = Station()


def bible():
    if STATION.name() == FALLBACK:
        now = "worked at a string of terrible stations there, and now broadcasts Non Stop Pop FM to listeners far from the coast"
    else:
        now = ("worked at a string of terrible stations there before making her name on Non Stop Pop FM, and these days runs her own "
               "station far from the coast, which always takes the name of whatever the listener puts on")
    return ("Her backstory (fixed, never contradict it or add big new facts): she's British, moved to Los Santos years ago chasing fame, "
            f"{now}. She misses and mocks Los Santos in equal measure (Vinewood, Vespucci Beach, Del Perro Pier, Rockford Hills, "
            "Sandy Shores, Mount Chiliad, the endless freeway traffic), and only ever talks about it as a place from her past.")


def station_line():
    if STATION.name() == FALLBACK:
        return "THE STATION: Non Stop Pop FM."
    frm = STATION.note() or f'"{STATION.name()}"'
    return (f'THE STATION: it\'s named after whatever the listener is playing, which right now is {frm}, so on air it\'s '
            f'"{STATION.full()}". Use the name when it fits (dropping it in like a real DJ, bragging about it, a cheeky comment '
            "on the name), not in every break. Never call it Non Stop Pop: that was her old station, back in Los Santos.")


# ---------------------------------------------------------------- finding something to talk about
def gnews(q):
    return "https://news.google.com/rss/search?q=" + quote(q) + "&hl=en-US&gl=US&ceid=US:en"


def gtopic(t):
    return f"https://news.google.com/rss/headlines/section/topic/{t}?hl=en-US&gl=US&ceid=US:en"


WORLD_FEEDS = [gnews('bizarre OR weird OR quirky OR "world record" OR viral'), "https://rss.upi.com/news/odd_news.rss",
               "https://feeds.bbci.co.uk/news/science_and_environment/rss.xml"]
GOSSIP_FEEDS = [gtopic("ENTERTAINMENT"), "https://feeds.bbci.co.uk/news/entertainment_and_arts/rss.xml",
                gnews('"pop star" OR singer OR "red carpet" OR "new album" OR tour')]
MUSIC_FEEDS = ["https://www.billboard.com/feed/", "https://pitchfork.com/feed/feed-news/rss",
               gnews('"new single" OR "new album" OR "tour dates" OR Billboard OR Grammy OR "chart" music')]
_feed_cache = {}


def local_feeds():
    city = getattr(dj, "CITY", "Yakima, Washington")
    f = [gnews(city.replace(",", " "))]
    if city.lower().startswith("yakima"):
        f.append(gnews("Yakima Valley"))
    return f


def headlines(feeds, extra=None, per_feed=6):
    out = []
    for url in feeds:
        c = _feed_cache.get(url)
        if c and time.time() - c[0] < 600:
            titles = c[1]
        else:
            try:
                titles = [dj.clean_title(e.title) for e in feedparser.parse(url).entries]
            except Exception:
                titles = []
            _feed_cache[url] = (time.time(), titles)
        for t in titles[:per_feed]:
            if t and dj.is_safe(t, extra) and not mentions_death(t):
                out.append(t)
    return out


def fresh_headline(feeds, extra=None):
    h = [x for x in headlines(feeds, extra) if MEM.is_fresh(x)]
    if not h:
        return None
    pick = random.choice(h)
    MEM.mark_used(pick)
    return pick


def forecast():
    try:
        d = requests.get("https://api.open-meteo.com/v1/forecast", params={
            "latitude": dj.LAT, "longitude": dj.LON, "daily": "temperature_2m_max,temperature_2m_min,precipitation_probability_max",
            "temperature_unit": "fahrenheit", "timezone": "auto", "forecast_days": 1}, timeout=8).json()["daily"]
        return round(d["temperature_2m_max"][0]), round(d["temperature_2m_min"][0]), round(d.get("precipitation_probability_max", [0])[0] or 0)
    except Exception:
        return None


def on_this_day():
    try:
        today = datetime.date.today()
        url = f"https://en.wikipedia.org/api/rest_v1/feed/onthisday/events/{today.month:02d}/{today.day:02d}"
        events = requests.get(url, timeout=8, headers={"User-Agent": "NonStopPopDJ/1.0"}).json().get("events", [])
    except Exception:
        return []
    light = re.compile(r"\b(?:album|song|single|band|film|movie|television|tv|series|premiere|premiered|released|launch|launched|record|game|video game|invented|opened|first|festival|concert|chart|toy|cartoon|comic|museum|zoo|park|space|satellite|moon|computer|internet|website)\b", re.I)
    out = []
    for e in events:
        text, year = e.get("text", ""), e.get("year")
        if not text or not year or len(text) >= 240 or not dj.is_safe(text) or mentions_death(text) or not light.search(text):
            continue
        out.append(f"On this day in {year}: {text}")
    return out


_stats = {"at": 0, "artists": [], "tracks": []}


def listening_stats():
    if time.time() - _stats["at"] < 6 * 3600:
        return _stats["artists"], _stats["tracks"]
    _stats["at"] = time.time()
    try:
        _stats["artists"] = [a["name"] for a in dj.sp.current_user_top_artists(limit=5, time_range="short_term")["items"]]
        _stats["tracks"] = [f'"{t["name"]}" by {t["artists"][0]["name"]}' for t in dj.sp.current_user_top_tracks(limit=5, time_range="short_term")["items"]]
    except Exception:
        _stats["artists"], _stats["tracks"] = [], []
    return _stats["artists"], _stats["tracks"]


def song_facts(t, when):
    f = f'{when} song is "{t["title"]}" by {t["artist"]}'
    if t.get("album"):
        f += f', from the album "{t["album"]}"'
    if t.get("year"):
        f += f' ({t["year"]})'
    return f + ". Use only these facts about it (the album or year are fine), never claim anything else about the artist or song."


def topic(label, facts, plain=None, name=None):
    seg = next((s for s in SEGMENTS if s["id"] == label), None)
    return {"label": label, "facts": facts, "plain": plain, "name": name or (seg["name"] if seg else label)}


def topic_for(sid, ctx):
    """The facts for one segment, or None when there's nothing usable right now (she picks something else)."""
    nxt, last = ctx.get("next"), ctx.get("last")
    song = nxt or last
    city = getattr(dj, "CITY", "Yakima, Washington")
    T = lambda f, plain=None: topic(sid, f, plain)
    if sid == "next_intro":
        if not nxt:
            return None
        return T(song_facts(nxt, "The NEXT") + " Introduce it with real excitement and one playful quip about the title, the artist's name or the vibe.",
                 f"Up next, {nxt['artist']} with {nxt['title']}.")
    if sid == "last_verdict":
        if not last:
            return None
        return T(song_facts(last, "The song that JUST played") + " Give it a verdict on a ridiculous scale she invents on the spot (like 'nine out of ten rubber ducks') and explain it in one cheeky line.",
                 f"That was {last['artist']} with {last['title']}.")
    if sid in ("trivia", "artist_story"):
        for t in [x for x in (nxt, last) if x]:
            when = "The NEXT" if t is nxt else "The song that JUST played"
            try:
                tr = dj.get_trivia(t)
            except Exception:
                tr = None
            if not tr or mentions_death(tr[1]):
                continue
            subject, text = tr
            if sid == "artist_story" and subject != t["artist"]:
                try:
                    bio = dj._wiki_lookup(f"{t['artist']} musician band singer", [t["artist"]])
                except Exception:
                    bio = None
                if not bio or mentions_death(bio):
                    continue
                return T(f'{when} song is "{t["title"]}" by {t["artist"]}. Real background on {t["artist"]} (from Wikipedia): """{bio}""" '
                         "Share ONE surprising, specific detail about the artist from that text, in your own words, like you just remembered it. "
                         "Nothing that isn't in the text; skip anything sad, dark or about scandals.")
            key = "wiki|" + t["artist"] + "|" + text[:60]
            if not MEM.is_fresh(key, 2):
                continue
            MEM.mark_used(key)
            sentences = [s for s in re.split(r"(?<=[.!?])\s+", re.sub(r"\s*\([^)]*\)", "", text))[1:] if 40 < len(s) < 200 and not mentions_death(s)]
            return T(f'{when} song is "{t["title"]}" by {t["artist"]}. Real background on {subject} (from Wikipedia): """{text}""" '
                     "Share exactly ONE interesting, specific fact from that text that you haven't used before, in your own words. "
                     "Never add anything that isn't in the text; skip anything sad, dark, or about scandals or lawsuits.",
                     ("Fun fact about " + t["artist"] + ": " + random.choice(sentences)) if sentences else None)
        return None
    if sid == "time_machine":
        if not song or not str(song.get("year", "")).isdigit():
            return None
        y = int(song["year"])
        ago = datetime.date.today().year - y
        when = "The NEXT" if song is nxt else "The song that JUST played"
        age = "it came out this year" if ago <= 0 else ("that's one year ago" if ago == 1 else f"that's {ago} years ago")
        return T(f'{when} song, "{song["title"]}" by {song["artist"]}, came out in {y} ({age}). Riff on how long ago that feels and what the listener was probably up to back then, playful and general. Invent nothing about the artist.')
    if sid == "your_stats":
        artists, tracks = listening_stats()
        if not artists and not tracks:
            return None
        f = "From the listener's own Spotify listening:"
        if artists:
            f += f" their most-played artists lately are {', '.join(artists[:3])}."
        if tracks:
            f += f" A song they've had on heavy rotation lately: {tracks[0]}."
        return T(f + " Tease them lovingly about it, like she's caught them red-handed. Pick ONE of these to focus on.")
    if sid == "hot_take":
        if not song:
            return None
        return T(f'A playful hot take about the vibe of "{song["title"]}" by {song["artist"]}: what weather it belongs to, what it would smell like, or what it\'s secretly about. It\'s an opinion, so invent nothing factual about the artist.')
    if sid == "sing_along":
        if not nxt:
            return None
        return T(f'Dare the listener to sing along to the next song, "{nxt["title"]}" by {nxt["artist"]}, at full volume. Don\'t quote any lyrics.')
    if sid == "music_news":
        h = fresh_headline(MUSIC_FEEDS, GOSSIP_SKIP)
        return T("A music-industry headline (tell it in your own words, add no claims beyond it): " + h, h + ".") if h else None
    if sid == "local_news":
        h = fresh_headline(local_feeds())
        return T(f"One headline from around {city} (say what it says, in your own words, then react; add nothing): " + h, h + ".") if h else None
    if sid == "weather_now":
        w = dj.get_weather()
        if not w:
            return None
        temp, rain = w
        return T(f"The weather in {city} right now: {temp} degrees Fahrenheit, {'and it is raining' if rain and rain > 0 else 'no rain'}. Turn it into a playful little forecast for the listener's mood or plans.",
                 f"It's {temp} degrees out there{' and wet' if rain and rain > 0 else ''}.")
    if sid == "forecast":
        w = forecast()
        if not w:
            return None
        hi, lo, rain = w
        return T(f"Today's forecast for {city}: a high of {hi} and a low of {lo} degrees Fahrenheit, with a {rain} percent chance of rain. Deliver it with personality and one cheeky bit of advice.",
                 f"Today: a high of {hi}, a low of {lo}.")
    if sid == "time_check":
        t = datetime.datetime.now().strftime("%A %I:%M %p").replace(" 0", " ")
        return T(f"It's {t}. Riff on what this exact time of day is really for.", f"It's {t}.")
    if sid == "day_vibe":
        d = datetime.date.today()
        season = {12: "winter", 1: "winter", 2: "winter", 3: "spring", 4: "spring", 5: "spring", 6: "summer", 7: "summer", 8: "summer"}.get(d.month, "autumn")
        f = f"It's {d.strftime('%A')}, in {season}."
        hol = D["holidays"].get(d.strftime("%m-%d"))
        if hol:
            f += f" Today is {hol}."
        if d.month == 10:
            f += " It's October, which means spooky season and pumpkin-spice everything."
        if d.month == 11 and d.weekday() == 3 and 22 <= d.day <= 28:
            f += " It's Thanksgiving in the US."
        return T(f + " Riff on the vibe of the day in one fresh, playful way.")
    if sid == "hometown":
        if city.lower().startswith("yakima"):
            fact = MEM.fresh("yakima", D["yakima"])
            return T(f"A fact about {city} (true, you can say it): {fact} Give the town some playful love.", fact)
        return T(f"Give {city} some playful love: what makes its people great, said generally and warmly. Invent no facts, places or names.")
    if sid == "traffic_joke":
        return T("A totally fake, obviously silly 'travel report' about the listener's life (the queue for the kettle, a jam in the sock drawer, delays on the road to bed). Never mention real roads, accidents or delays.")
    news = {
        "weird_news": ([*WORLD_FEEDS], None, lambda h: f"A weird-but-true story from somewhere in the world (NOT from {city}): {h} Tell it in your own words, then react."),
        "science": (["https://feeds.bbci.co.uk/news/science_and_environment/rss.xml", gnews("scientists discover")], None, lambda h: "A science headline (say only what it says): " + h),
        "space": ([gnews('NASA OR astronomers OR telescope OR "space station" OR planet')], None, lambda h: "A space headline (say only what it says): " + h),
        "tech": ([gtopic("TECHNOLOGY"), gnews('gadget OR "new device" OR robot')], None, lambda h: "A gadgets-and-tech headline (say only what it says): " + h),
        "animals": ([gnews('zoo OR "animal rescue" OR wildlife OR puppy OR kitten OR "baby animal"')], None, lambda h: "A heart-warming animal story (say only what it says): " + h),
        "food": ([gnews('"food trend" OR snack OR "new menu" OR chef OR bakery')], None, lambda h: "A food headline (say only what it says): " + h),
        "sports": ([gtopic("SPORTS")], None, lambda h: "A sports headline (say only what it says, keep it light and fun): " + h),
        "showbiz": (GOSSIP_FEEDS, GOSSIP_SKIP, lambda h: "A showbiz headline (say ONLY what it says, add no rumours, tease affectionately, never mock anyone's looks or private life): " + h),
        "screen": ([gnews('trailer OR "new series" OR "box office" OR premiere OR sequel')], GOSSIP_SKIP, lambda h: "A films-and-TV headline (say only what it says): " + h),
    }
    if sid in news:
        feeds, extra, make = news[sid]
        h = fresh_headline(feeds, extra)
        return T(make(h), h + ".") if h else None
    if sid == "on_this_day":
        events = [e for e in on_this_day() if MEM.is_fresh(e, 300)]
        if not events:
            return None
        e = random.choice(events)
        MEM.mark_used(e)
        return T(e + " Share it in your own words and react.", e)
    if sid == "fun_fact":
        f = MEM.fresh("funFacts", D["funFacts"])
        return T("A true fun fact (say it in your own words): " + f, "Fun fact: " + f)
    if sid == "word":
        w, m = MEM.fresh("words", D["words"])
        return T(f"Word of the day: '{w}', meaning {m}. Teach it, then use it in a silly example about the listener.", f"Word of the day: {w}. It means {m}.")
    if sid == "lore":
        return T("A quick first-person story from her Los Santos days, told with a punchline (use only these details, add reactions but no big new facts): " + MEM.fresh("lore", D["lore"]))
    if sid == "confession":
        return T("A silly confession about herself: " + MEM.fresh("confessions", D["confessions"]) + ". Own it dramatically.")
    if sid == "opinion":
        return T("Her strong, ridiculous opinion on this burning question: " + MEM.fresh("opinions", D["opinions"]) + " Pick a side, defend it absurdly, and dare the listener to disagree.")
    if sid == "fake_ad":
        return T("A short parody advert, read by Cara, for this totally made-up product: " + MEM.fresh("fakeAds", D["fakeAds"]) + " Include a ridiculous catchphrase for it and a fake 'terms and conditions' line at top speed.")
    if sid == "station_hype":
        return T(f"Hype the station, {STATION.full()}, itself in a fresh, absurd way: what it'd be if it were a person, a food or a weather system, or a ridiculous line about it, said dead straight as if it's always been the station's motto.")
    if sid == "roast":
        return T("A playful roast. " + MEM.fresh("roasts", D["roasts"]))
    if sid == "compliment":
        return T("Give the listener a backhanded compliment that's really a tease, then a sincere one.")
    if sid == "horoscope":
        return T(f"A completely made-up, obviously silly horoscope for {random.choice(D['signs'])}, with a weirdly specific prediction about snacks, socks, songs or parking.")
    if sid == "advice":
        return T("Terrible-but-harmless agony-aunt advice for the listener's dilemma: " + MEM.fresh("dilemmas", D["dilemmas"]))
    if sid == "pep_talk":
        return T("An over-the-top motivational speech about " + MEM.fresh("pepTalks", D["pepTalks"]) + ", like it's the biggest moment of the listener's life.")
    if sid == "hypothetical":
        return T("Picture this: " + MEM.fresh("hypotheticals", D["hypotheticals"]) + ". Paint the scene in a few vivid, silly strokes.")
    if sid == "would_you_rather":
        q = MEM.fresh("wyr", D["wouldYouRather"])
        return T(f"Ask the listener: would you rather {q}? Then give her own answer, with a ridiculous reason.", f"Would you rather {q}?")
    if sid == "pop_quiz":
        q, a = MEM.fresh("quiz", D["quiz"])
        return T(f"A pop quiz for the listener. Question: {q} Answer: {a}. Ask it, give them a few seconds of fake suspense, then reveal the answer.",
                 f"Quick quiz: {q} The answer? {a[:1].upper() + a[1:]}.")
    if sid == "debate":
        return T("Start a silly debate: " + MEM.fresh("opinions", D["opinions"]) + " Argue BOTH sides like two people, then declare yourself the winner.")
    if sid == "challenge":
        return T("A car-safe challenge for the listener (voice only): " + MEM.fresh("challenges", D["challenges"]))
    return None


def _available(sid, ctx):
    if sid in ("next_intro", "sing_along"):
        return bool(ctx.get("next"))
    if sid == "last_verdict":
        return bool(ctx.get("last"))
    if sid in ("trivia", "artist_story", "time_machine", "hot_take"):
        return bool(ctx.get("next") or ctx.get("last"))
    return True


def pick_topic(ctx):
    recent = set(MEM.last("segments", 10))
    last_seg = MEM.last("segments", 1)
    last_family = next((s["family"] for s in SEGMENTS if last_seg and s["id"] == last_seg[0]), None)
    pool = {s["id"]: (max(1, s["weight"] // 3) if s["family"] == last_family else s["weight"])
            for s in SEGMENTS if _available(s["id"], ctx) and s["id"] not in recent}
    while pool:
        sid = random.choices(list(pool), weights=list(pool.values()))[0]
        try:
            t = topic_for(sid, ctx)
        except Exception as e:
            print(f"[{sid} failed: {e}]")
            t = None
        if t:
            return t
        del pool[sid]
    return topic_for("fun_fact", ctx)


def current_mood():
    m = getattr(dj, "DJ_MOOD", "normal")
    return random.choice(["chill", "normal", "unhinged"]) if m == "mixed" else m


def word_range(style):
    c = chattiness()
    if style == "intro":   # over the start of a song she keeps it to a quick drop-in, so a song that kicks in straight away isn't buried
        return {"quick": (8, 14), "normal": (10, 18)}.get(c, (12, 22))
    silent = style == "silent"
    if c == "quick":
        return (20, 40) if silent else (12, 28)
    if c == "normal":
        return (35, 60) if silent else (20, 40)
    return (55, 95) if silent else (30, 55)


def describe(t):
    return f"{t['title']} by {t['artist']}" if t else "(unknown)"


# ---------------------------------------------------------------- Cara on her own
def write_break(style, ctx):
    """One break: what she talks about, how she says it, how she opens and lands, how long, in which mood."""
    ctx = dict(ctx or {})
    topic_ = pick_topic(ctx)
    mood = current_mood()
    print(f"[segment: {topic_['name']}] [mood: {mood}] [{chattiness()}]")
    fmt = random.choice([f for f in FORMATS if f[0] not in MEM.last("formats", 8)] or FORMATS)
    have_song = bool(ctx.get("next") or ctx.get("last"))
    opens = [o for o in OPENINGS if o[0] not in MEM.last("openings", 8)
             and (o[2] != "song" or have_song) and (o[2] != "last" or ctx.get("last"))]
    # now and then she reads the room: one quick jab about what the listener's song says about them, then on with the break
    read = song_read(ctx.get("next") if style == "intro" else (ctx.get("last") or ctx.get("next"))) if time_to_read() else None
    opening = ["read", "Open by reading the room (see below).", ""] if read else random.choice(opens or OPENINGS)
    room_line = read_block(read, read_which(style, ctx)) if read else ""
    if read:
        print(f"[reading the room: {read['info']['title']}{'' if read['lyrics'] else ', title only'}]")
    ending = random.choice([e for e in ENDINGS if e not in MEM.last("endings", 5)] or ENDINGS)
    tag_choices = random.sample([t for t in TAGS if t not in MEM.last("tags", 4)] or TAGS, 2)
    lo, hi = word_range(style)
    skip = reusable(ctx)
    switched = STATION.switched_from()
    print(f"[style: {fmt[0]} · opening: {opening[0]} · {lo}-{hi} words]")
    tag_line = (f"She may use up to two emotion tags, ONLY [{'] or ['.join(tag_choices)}], each placed mid-sentence right before the words it colours (never first, never on its own). Or none."
                if expressive() else "Don't use any square-bracket tags.")
    switch_line = (f'\n- Fresh news: the listener just switched stations, from "{full_name(switched)}" to "{STATION.full()}". Welcome them to the new one somewhere in this break, in one quick, playful line (new name, same Cara).'
                   if switched else "")
    prompt = f"""You are Cara, the DJ on {STATION.full()}, broadcasting to {getattr(dj, 'CITY', 'Yakima, Washington')}.
{PERSONA}
{bible()}
{station_line()}

THIS BREAK
- What's happening: {SITUATIONS.get(style, SITUATIONS['talkover'])}{switch_line}
- Length: {lo} to {hi} words.
- Talk about: {topic_['facts']}{room_line}
- Delivery: {fmt[1]}
- Mood: {MOOD_LINES.get(mood, MOOD_LINES['normal'])}
- Opening: {opening[1]}
- Landing: {ending}
- Voice: {tag_line}
- It's {dj.time_of_day()} for the listener.

NEVER REPEAT YOURSELF
{memory_block(skip)}

{RULES}

Song that's just finishing: {describe(ctx.get('last'))}
Next song: {describe(ctx.get('next'))}
(She may name the next song if it looks like a real song. If it looks like an advert, a radio clip or is unknown, she doesn't mention it.)
Write only the words Cara says."""
    d = fresh_draft(prompt, skip, read)
    if d:
        MEM.remember(d[0], topic_["label"], fmt[0], opening[0], ending, d[1])
        return d[0]
    t = template_break(topic_, ctx)
    MEM.remember(t, topic_["label"])
    return t


def template_break(topic_, ctx):
    """No Gemini key (or Gemini is down): simple lines, still varied."""
    city = getattr(dj, "CITY", "Yakima, Washington")
    openers = ["Cara here, keeping you company.", f"{STATION.full()}, Cara on the mic.", f"Hello, {city}!",
               "Cara again. Did you miss me?", "This is Cara, live-ish and lovely.", "Guess who's back.",
               "Your favourite voice, reporting for duty.", "Cara checking in.", "Here's Cara, with absolutely no notes.",
               "It's me, the voice in your speakers.", f"{STATION.name()}, and I'm still here.", "Cara, back by popular demand."]
    closers = ["Back to the music.", "Here's the next one.", "Turn it up for this.", "Stay right there.",
               "Don't go anywhere.", "More pop, coming right up.", "You're in good hands.", "Off we go.",
               "Right, on with the show.", "This next one's a goodie."]
    middle = topic_.get("plain") or ""
    if not middle:
        n = ctx.get("next")
        middle = f"Up next, {n['artist']}, with {n['title']}." if n else "More of the good stuff, coming up."
    return MEM.fresh("tplOpen", openers) + " " + middle + " " + MEM.fresh("tplClose", closers)


def write_popin(info):
    """A quick drop-in a few seconds into a song."""
    title = (info or {}).get("title") or "this one"
    artist = (info or {}).get("artist") or ""
    name = f'"{title}" by {artist}' if artist else f'"{title}"'
    fact = ""
    try:
        t = dj.get_trivia(info) if info else None
        if t and not mentions_death(t[1]):
            fact = "A real fact you may use (never invent others): " + t[1][:400]
    except Exception:
        pass
    kinds = [k for k in D["popinKinds"] if k[0] not in MEM.last("popins", 4)
             and (k[0] != "fact" or fact) and (k[0] != "callback" or MEM.last_break)
             and (k[0] != "read" or (info and info.get("title") and "read" not in MEM.last("openings", 1)))]
    kind = random.choice(kinds or D["popinKinds"])
    read = song_read(info) if kind[0] == "read" else None
    room_line = read_block(read, "the song that's playing") if read else ""
    c = chattiness()
    lo, hi = (8, 16) if c == "quick" else ((10, 22) if c == "normal" else (14, 30))
    tag = random.choice([t for t in TAGS if t not in MEM.last("tags", 4)] or TAGS)
    ctx = {"next": info}
    skip = reusable(ctx)
    print(f"[pop-in style: {kind[0]}]")
    prompt = f"""You are Cara, the DJ on {STATION.full()}, broadcasting to {getattr(dj, 'CITY', 'Yakima, Washington')}.
{PERSONA}

The song {name} started a few seconds ago, and she pops back in over it.
- What to do: {kind[1]}{room_line}
- Length: {lo} to {hi} words.
{('- ' + fact) if fact else ''}
{('- Her last break was: "' + (MEM.last_break or '') + '"') if kind[0] == 'callback' else ''}
- Voice: {('She may use one emotion tag, ONLY [' + tag + '], mid-sentence. Or none.') if expressive() else 'No square-bracket tags.'}
- High energy, quick, no goodbye or sign-off.

NEVER REPEAT YOURSELF
{memory_block(skip)}

{RULES}
Write only the words Cara says."""
    d = fresh_draft(prompt, skip, read)
    if d:
        MEM.remember(d[0], tags=d[1], popin=kind[0])
        return d[0]
    line = MEM.fresh("popinTemplates", [
        f"That's {name}. Turn it up, I'll wait.",
        f"{name}, and your taste is getting suspiciously good.",
        f"Still with me? Course you are. This is {name}.",
        f"{name}. Hum along, nobody's judging. I am, a bit.",
        f"Right in the middle of {name}, and I've got no notes.",
        f"Quick one: {name}. Carry on, superstar.",
    ])
    MEM.remember(line, popin="template")
    return line


# ---------------------------------------------------------------- Cara and Scratch together
def pick_duo_topic(ctx):
    recent = set(MEM.last("segments", 8))
    pool = {s["id"]: s["weight"] for s in D["duoSegments"] if "duo_" + s["id"] not in recent}
    if STATION.name() == FALLBACK:
        pool.pop("station_name", None)
    while pool:
        sid = random.choices(list(pool), weights=list(pool.values()))[0]
        seg = next(s for s in D["duoSegments"] if s["id"] == sid)
        t = None
        try:
            t = _duo_topic_for(seg, ctx)
        except Exception as e:
            print(f"[{sid} failed: {e}]")
        if t:
            return t
        del pool[sid]
    roast = next(s for s in D["duoSegments"] if s["id"] == "roast_battle")
    return {"label": "duo_roast_battle", "facts": roast["angle"], "plain": None, "name": roast["name"]}


def _duo_topic_for(seg, ctx):
    facts, plain = "", None
    if seg["base"]:
        t = topic_for(seg["base"], ctx)
        if not t:
            return None
        facts, plain = t["facts"], t.get("plain")
    else:
        sid = seg["id"]
        if sid == "story_swap":
            facts = (f"{CO_SHORT}'s story from his Los Santos days (use only these details): " + MEM.fresh("coLore", D["coLore"])
                     + " Cara's story to top it (use only these details): " + MEM.fresh("lore", D["lore"]))
        elif sid == "debate":
            facts = "The burning question: " + MEM.fresh("opinions", D["opinions"])
        elif sid == "would_you_rather":
            facts = "Would you rather " + MEM.fresh("wyr", D["wouldYouRather"]) + "?"
        elif sid == "quiz":
            q, a = MEM.fresh("quiz", D["quiz"])
            facts = f"Question: {q} Answer: {a}."
        elif sid == "advice":
            facts = "A listener's dilemma: " + MEM.fresh("dilemmas", D["dilemmas"])
        elif sid == "fake_ad":
            facts = "A totally made-up product: " + MEM.fresh("fakeAds", D["fakeAds"])
        elif sid == "station_name":
            facts = f'The station is named after {STATION.note() or chr(34) + STATION.name() + chr(34)}, so on air it\'s "{STATION.full()}".'
    return {"label": "duo_" + seg["id"], "facts": (facts + " " + seg["angle"]) if facts else seg["angle"], "plain": plain, "name": seg["name"]}


def parse_duo(raw):
    """Reads 'CARA: ...' / 'SCRATCH: ...' lines (anything else joins the line before it)."""
    out = []
    for piece in (raw or "").splitlines():
        line = piece.replace("*", "").strip().lstrip("-•").strip()
        if not line:
            continue
        if ":" in line:
            who, text = line.split(":", 1)
            who = who.strip().upper()
            if who in ("CARA", CO_LABEL, CO_NAME.upper()):
                if text.strip():
                    out.append(["CARA" if who == "CARA" else CO_LABEL, text.strip()])
                continue
        if out:
            out[-1][1] += " " + line
    return out


def write_duo(style, ctx):
    """One Cara-and-Scratch exchange, checked against their memory like her solo breaks. [] if it couldn't."""
    ctx = dict(ctx or {})
    topic_ = pick_duo_topic(ctx)
    mood = current_mood()
    print(f"[segment: {topic_['name']} (with {CO_NAME})] [mood: {mood}] [{chattiness()}]")
    silent = style == "silent"
    c = chattiness()
    if silent:
        lo, hi, most = {"quick": (3, 4, 50), "normal": (4, 6, 80)}.get(c, (5, 8, 110))
    elif style == "intro":   # over the start of a song: a quick two-liner, so a song that kicks in straight away isn't buried
        lo, hi, most = {"quick": (2, 2, 18), "normal": (2, 2, 22)}.get(c, (2, 3, 26))
    else:
        lo, hi, most = {"quick": (2, 2, 28), "normal": (2, 3, 38)}.get(c, (2, 4, 48))
    first = random.choice(["Cara", CO_SHORT])
    ending = random.choice([e for e in D["duoEndings"] if e not in MEM.last("endings", 5)] or D["duoEndings"])
    tag_choices = random.sample([t for t in TAGS if t not in MEM.last("tags", 4)] or TAGS, 3)
    skip = reusable(ctx, extra=set(words(CO_NAME)) | {"london", "vinyl"})
    song_words = set(words(" ".join(describe(t) for t in (ctx.get("last"), ctx.get("next")) if t)))
    co_move = MEM.fresh("coMoves", D.get("coMoves") or ["Meets Cara's chaos with slow, unbothered cool, then lands one perfect comeback."])
    switched = STATION.switched_from()
    # now and then one of them reads the room: a quick jab about what the listener's song says about them, then on with it
    read = song_read(ctx.get("next") if style == "intro" else (ctx.get("last") or ctx.get("next"))) if time_to_read() else None
    room_line = read_block(read, read_which(style, ctx), duo=True) if read else ""
    if read:
        print(f"[reading the room: {read['info']['title']}{'' if read['lyrics'] else ', title only'}]")
    print(f"[duo: {lo}-{hi} lines, {first} first]")
    tag_line = (f"Each line may use one emotion tag, ONLY [{'] or ['.join(tag_choices)}], placed mid-sentence right before the words it colours (never first). Most lines have none."
                if expressive() else "Don't use any square-bracket tags.")
    switch_line = (f'\n- Fresh news: the listener just switched stations, from "{full_name(switched)}" to "{STATION.full()}". One of them welcomes the listener to the new one in a quick, playful line.'
                   if switched else "")
    up = CO_LABEL
    prompt = f"""You write a short on-air exchange between the two DJs of {STATION.full()}, broadcasting to {getattr(dj, 'CITY', 'Yakima, Washington')}.
CARA: {PERSONA}
{bible()}
{up}: {CO_PERSONA}
{co_language()}
{CO_BIBLE}
{D.get("coIdentity", "")}
{station_line()}
{D.get("whoIsWho", "")}

THIS BREAK
- What's happening: {DUO_SITUATIONS.get(style, DUO_SITUATIONS['talkover'])}{switch_line}
- Talk about: {topic_['facts']}{room_line}
- {CO_SHORT}'s move this time (work it in naturally): {co_move}
- Shape: a quick back-and-forth between two DJs and old friends who've done a thousand shows together: teasing, interruptions, callbacks, each firing back at the other. Every line is short (3 to 22 words) and sounds spoken, not written.
- Length: {lo} to {hi} lines and {most} words at most in total. {first} speaks first and they take turns.
- Mood: {MOOD_LINES.get(mood, MOOD_LINES['normal'])}
- Landing: {ending}
- Voice: {tag_line}
- It's {dj.time_of_day()} for the listener.

NEVER REPEAT YOURSELVES
{memory_block(skip)}

{RULES}
- {CO_SHORT} is the one exception to the no-invented-characters rule: he's her co-host, in the studio with her. Nobody else joins them.
- {CO_SHORT} follows every rule too. Neither of them is a real radio host: never mention, name or imitate real DJs or presenters, and never claim to know celebrities personally.

Song that's just finishing: {describe(ctx.get('last'))}
Next song: {describe(ctx.get('next'))}
(They may name the next song if it looks like a real song. If it looks like an advert, a radio clip or is unknown, they don't mention it.)
Write ONLY the dialogue: one line per turn, each starting with CARA: or {up}:"""
    feedback, best = "", None
    for attempt in range(3):
        raw = gemini(prompt if not feedback else prompt + f"\n\nYour previous draft can't be used: {feedback} Write a completely new one.")
        if raw is None:
            break
        # a masked curse ("sh*t") gets read out as nonsense, so he says it in full or not at all
        if _MASKED.search(raw):
            feedback = "It hid a word behind asterisks. Write every word out in full, or pick a different word."
            print(f"[rewrite {attempt + 1}: masked word]")
            continue
        lines, used = [], []
        for who, text in parse_duo(raw):
            t, tg = clean_tags(tidy(text), set(TAGS))
            if t:
                lines.append((who, t))
                used += tg
        joined = " ".join(t for _, t in lines)
        said = set(words(joined))
        if "grandpa" in said or "gramps" in said:
            feedback = f"Cara gave him an old-man nickname. She only ever calls him {CO_SHORT}."
            print(f"[rewrite {attempt + 1}: old-man nickname]")
            continue
        if "alex" in said and "alex" not in song_words:
            feedback = f"It called him Alex. His name is {CO_NAME}, {CO_SHORT} for short."
            print(f"[rewrite {attempt + 1}: wrong name]")
            continue
        if any(w == "CARA" and swears(t) for w, t in lines):
            feedback = f"Cara swore. Only {CO_SHORT} curses; Cara keeps it clean."
            print(f"[rewrite {attempt + 1}: Cara swore]")
            continue
        if not getattr(dj, "COHOST_SWEARS", True) and swears(joined):
            feedback = "Keep it clean this time: no swearing from either of them."
            print(f"[rewrite {attempt + 1}: swearing]")
            continue
        if too_far(joined):
            feedback = f'{CO_SHORT} went too far. He can curse where it lands, but keep it classy: never "bitch" or "motherfucker".'
            print(f"[rewrite {attempt + 1}: too crude]")
            continue
        if len(lines) < 2 or not any(w == up for w, _ in lines) or not any(w == "CARA" for w, _ in lines):
            feedback = f"It has to be a conversation: at least two lines, with both CARA: and {up}: speaking."
            print(f"[rewrite {attempt + 1}: not a conversation]")
            continue
        if read and read["lyrics"] and quotes_lyrics(joined, read["lyrics"], read["info"]["title"]):
            feedback = "It quoted the song's lyrics. Never quote them: react to what the song's about in your own words."
            print(f"[rewrite {attempt + 1}: quoted the lyrics]")
            continue
        why = problem(joined, MEM.recent, skip)
        if why:
            print(f"[rewrite {attempt + 1}: {why}]")
            feedback = why
            if best is None and not mentions_death(joined) and not says_label(joined):
                best = lines
            continue
        MEM.remember(joined, topic_["label"], opening="read" if read else None, ending=ending, tags=used)
        return lines
    if best:
        MEM.remember(" ".join(t for _, t in best), topic_["label"], opening="read" if read else None, ending=ending)
        return best
    return []


def _eleven(text, path, voice):
    key = os.environ.get("ELEVENLABS_API_KEY")
    if not key or not voice:
        raise RuntimeError("ElevenLabs isn't set up")
    settings = ({"stability": dj.ELEVEN_V4_STABILITY, "similarity_boost": dj.ELEVEN_V4_SIMILARITY}
                if dj.eleven_expressive() else dj.ELEVENLABS_SETTINGS)
    r = requests.post(f"https://api.elevenlabs.io/v1/text-to-speech/{voice}", params={"output_format": "mp3_44100_128"},
                      headers={"xi-api-key": key, "Content-Type": "application/json"},
                      json={"text": text, "model_id": dj.eleven_model(), "voice_settings": settings}, timeout=60)
    if not r.ok:
        raise RuntimeError(f"{r.status_code} {r.text[:160]}")
    with open(path, "wb") as f:
        f.write(r.content)


def _samples(path):
    """An mp3 or wav as float mono samples at the mixer's rate (decoded by pygame)."""
    import numpy as np
    import pygame
    import pygame.sndarray as sndarray
    raw = sndarray.array(pygame.mixer.Sound(path))
    if raw.dtype.kind == "f":
        a = raw.astype(np.float32)
    elif raw.dtype.kind == "u":
        half = (np.iinfo(raw.dtype).max + 1) / 2.0
        a = (raw.astype(np.float32) - half) / half
    else:
        a = raw.astype(np.float32) / float(np.iinfo(raw.dtype).max + 1)
    if a.ndim == 2:
        a = a.mean(axis=1)
    return a


def _active_db(x, rate):
    import numpy as np
    hop = int(0.02 * rate)
    if len(x) < hop:
        return -120.0
    n = len(x) // hop
    fr = np.sqrt((x[:n * hop].reshape(n, hop) ** 2).mean(axis=1))
    top = fr.max()
    if top <= 0:
        return -120.0
    loud = fr[fr > top * 10 ** (-35 / 20)]
    return float(10 * np.log10((loud ** 2).mean() + 1e-12))


def _trim(x):
    import numpy as np
    a = np.abs(x)
    if not len(a) or a.max() <= 0:
        return x[:0]
    idx = np.where(a > a.max() * 10 ** (-45 / 20))[0]
    return x[max(0, idx[0] - 400): min(len(x), idx[-1] + 1300)]


def render_duo(lines):
    """Each line in its own voice, levelled, Cara a touch left and Scratch a touch right, joined into one WAV."""
    import numpy as np
    import pygame
    rate = (pygame.mixer.get_init() or (44100,))[0]
    stamp = f"{int(time.time())}{random.randint(0, 999)}"
    cara_voice = os.environ.get("ELEVENLABS_VOICE_ID")
    co_voice = (getattr(dj, "COHOST_VOICE", "") or "").strip() or CO_DEFAULT_VOICE
    parts, files = [], []
    try:
        for i, (who, text) in enumerate(lines):
            path = os.path.join(tempfile.gettempdir(), f"duo_{stamp}_{i}.mp3")
            _eleven(text, path, co_voice if who != "CARA" else cara_voice)
            files.append(path)
            v = _trim(_samples(path))
            if len(v):
                v = v * (10 ** ((-19 - _active_db(v, rate)) / 20))
                parts.append((who != "CARA", v))
    finally:
        for f in files:
            try:
                os.remove(f)
            except OSError:
                pass
    if not parts:
        raise RuntimeError("no voices came back")
    pos, placed = int(0.1 * rate), []
    for co, v in parts:
        placed.append((pos, co, v))
        pos += len(v) + int(random.uniform(0.12, 0.26) * rate)
    total = max(p + len(v) for p, _, v in placed) + int(0.2 * rate)
    left, right = np.zeros(total, np.float32), np.zeros(total, np.float32)
    for p, co, v in placed:
        gl, gr = (0.86, 1.0) if co else (1.0, 0.86)
        left[p:p + len(v)] += v * gl
        right[p:p + len(v)] += v * gr
    peak = max(float(np.abs(left).max()), float(np.abs(right).max()), 1e-9)
    if peak > 0.97:
        left *= 0.97 / peak
        right *= 0.97 / peak
    out = os.path.join(tempfile.gettempdir(), f"duo_{stamp}.wav")
    pcm = (np.clip(np.stack([left, right], axis=1), -1, 1) * 32767).astype("<i2")
    with wave.open(out, "wb") as w:
        w.setnchannels(2)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(pcm.tobytes())
    return out


def duo_ready():
    """Scratch needs the ElevenLabs voice and a Gemini key."""
    return (getattr(dj, "TTS_ENGINE", "") == "elevenlabs" and bool(os.environ.get("GEMINI_API_KEY"))
            and getattr(dj, "MODE", "gemini") == "gemini")


def wants_duo():
    """Rolled when a break is planned: is this one Cara and Scratch together? (They always talk between songs.)"""
    if not getattr(dj, "COHOST_ENABLED", True) or random.random() >= getattr(dj, "COHOST_CHANCE", 0.4):
        return False
    if not duo_ready():
        print(f"[{CO_NAME} needs the ElevenLabs voice (VOICE: elevenlabs) and a Gemini key, so Cara takes it solo]")
        return False
    return True


def maybe_duo(style, ctx, force=False):
    """Sometimes it's Cara and Scratch together: returns the clip (a WAV), or None for a solo Cara break."""
    if not force and not (getattr(dj, "COHOST_ENABLED", True) and random.random() < getattr(dj, "COHOST_CHANCE", 0.4)):
        return None
    solo = "" if force else ", so Cara takes it solo"
    if getattr(dj, "TTS_ENGINE", "") != "elevenlabs" or not os.environ.get("GEMINI_API_KEY") or getattr(dj, "MODE", "gemini") != "gemini":
        print(f"[{CO_NAME} needs the ElevenLabs voice (VOICE: elevenlabs) and a Gemini key{solo}]")
        return None
    lines = write_duo(style, ctx)
    if len(lines) < 2:
        print(f"[{CO_NAME} couldn't make it this time{solo}]")
        return None
    shown = "\n".join(("Cara" if w == "CARA" else CO_NAME) + ": " + t for w, t in lines)
    print(f"[DUO:{style}]\n{shown}")
    try:
        return render_duo(lines)
    except Exception as e:
        print(f"Could not make their voices ({e}){solo}.")
        return None

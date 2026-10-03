"""
Live radio DJ for Spotify (free version, no paid API needed).

MODE = "template": no AI, no extra keys. Fills DJ lines with real headlines/weather/time.
MODE = "gemini":   uses Google's Gemini API (free tier key from aistudio.google.com).
                   Falls back to template mode if anything goes wrong.

Run it on the same computer that is playing Spotify.
"""
import asyncio
import glob
import hashlib
import os
import random
import re
import shutil
import sys
import tempfile
import threading
import time
from datetime import datetime

import edge_tts
import feedparser
import pygame
import requests
import spotipy
from spotipy.oauth2 import SpotifyOAuth

try:
    import pc_stingers   # station stingers: your stingers re-voiced with the name of what's playing (needs numpy)
except Exception:
    pc_stingers = None
import brain   # Cara's brain (what she talks about, her memory, the station name, her co-host Scratch): shared with the iPhone app

# ---------------- CONFIG: edit these ----------------
MODE = "gemini"                    # "template" or "gemini"
GEMINI_MODELS = [                  # tried in order until one works
    "gemini-3.5-flash-lite",
    "gemini-3.6-flash",
    "gemini-3.5-flash",
]

CITY = "Yakima, Washington"
LAT, LON = 46.60, -120.51
NEWS_FEEDS = [
    "https://news.google.com/rss/search?q=Yakima+Washington&hl=en-US&gl=US&ceid=US:en",
    "https://news.google.com/rss/search?q=Yakima+Valley&hl=en-US&gl=US&ceid=US:en",
]
WORLD_FEEDS = [
    # Wild, weird and quirky stories from around the world (war, politics and tragedy are filtered out)
    "https://news.google.com/rss/search?q=bizarre+OR+weird+OR+quirky+OR+%22world+record%22+OR+viral&hl=en-US&gl=US&ceid=US:en",
    "https://rss.upi.com/news/odd_news.rss",
    "https://feeds.bbci.co.uk/news/science_and_environment/rss.xml",
]
GOSSIP_FEEDS = [
    # Pop culture and celebrity news (serious scandal, legal and personal-crisis stories are filtered out)
    "https://news.google.com/rss/headlines/section/topic/ENTERTAINMENT?hl=en-US&gl=US&ceid=US:en",
    "https://feeds.bbci.co.uk/news/entertainment_and_arts/rss.xml",
    "https://news.google.com/rss/search?q=%22pop+star%22+OR+singer+OR+%22red+carpet%22+OR+%22new+album%22+OR+tour&hl=en-US&gl=US&ceid=US:en",
]
MUSIC_FEEDS = [
    # Music industry news: releases, tours, charts, awards
    "https://www.billboard.com/feed/",
    "https://pitchfork.com/feed/feed-news/rss",
    "https://news.google.com/rss/search?q=%22new+single%22+OR+%22new+album%22+OR+%22tour+dates%22+OR+Billboard+OR+Grammy+OR+%22chart%22+music&hl=en-US&gl=US&ceid=US:en",
]
CARA_BIBLE = "Your backstory (fixed canon, never contradict it, never invent big new facts beyond the story you are given): you are a British DJ who moved to Los Santos years ago chasing fame, worked at a string of terrible stations there, and now broadcast Non Stop Pop to listeners far from the coast. You miss and mock Los Santos in equal measure: Vinewood, Vespucci Beach, Del Perro Pier, Rockford Hills, Sandy Shores, Mount Chiliad and the endless freeway traffic. You talk about Los Santos only as a place from your past."
LORE_STORIES = [
 "The time you got stuck at the top of the Ferris wheel on Del Perro Pier for forty minutes and ended up doing a live weather report to the people in the next carriage.",
 "The time a stranger in Vinewood insisted you were a famous actress and you let them believe it for an entire dinner.",
 "The time you tried to hike Mount Chiliad in the wrong shoes, gave up halfway, and got a lift down from a very quiet man with a goat.",
 "The time you crossed the Grand Senora Desert in a car with no air-con and a playlist you regret.",
 "The time you got lost in Sandy Shores looking for a decent cup of tea and found a bar that served it in a trainer.",
 "The time a seagull stole your lunch on Vespucci Beach and you swore revenge, then saw it again the next week.",
 "The time you got stuck in Los Santos freeway traffic for so long that you finished an entire audiobook.",
 "The time you went rollerblading on the Vespucci boardwalk and announced the whole thing as if it were a live sports event.",
 "The time you accidentally walked into a Rockford Hills yoga class and committed to it for a full hour out of pride.",
 "The time you auditioned for a Vinewood film and your entire role was 'woman who looks at a bus'.",
 "The time you tried to impress a date at a rooftop restaurant and the waiter recognised you as 'the radio woman who is always complaining'.",
 "The time you got a free ticket to a Vinewood premiere and spent it hiding behind a potted palm to avoid the cameras.",
 "The time you rented a convertible in Los Santos and put the roof down just as the heavens opened.",
 "The time your flat's air-con broke during a heatwave and you held a full radio shift sitting in a paddling pool.",
 "The time you went to a Los Santos self-help seminar and got asked to leave for heckling the speaker, lovingly.",
 "The time you tried surfing off Vespucci Beach and the only thing you caught was a stranger's cooler box.",
 "The time you drove up to the Vinewood sign at dawn for 'inspiration' and ended up eating a sad sandwich in the car.",
 "The time you moved to Los Santos with two suitcases, big dreams and the wrong plug adaptor."
]
_LORE_USED = set()


def pick_lore():
    left = [x for x in LORE_STORIES if x not in _LORE_USED]
    if not left:
        _LORE_USED.clear()
        left = list(LORE_STORIES)
    x = random.choice(left)
    _LORE_USED.add(x)
    return x


CARA_GUIDE = """How this DJ's comedy works (write in this spirit, but never copy real lines from any show or game):
- Bubbly and bossy on the surface, a little jaded underneath. She orders the listener to be happy, then undercuts it with a dry, very specific observation.
- Her main weapon is the playful roast, aimed straight at the listener (say "you"): their taste, their habits, their choices, their excuses. Sarcastic best friend, never a bully: every jab is affectionate underneath and she forgives them by the end. Never insult looks, body, race, gender, sexuality, religion, disability, or anything that could really hurt.
- Shape of a joke: a quick setup, one or two absurdly specific details, then a deflating punchline or a self-aware aside about herself or her radio job.
- She begs and pleads ("please?") after bossy commands, and pretends to be lonely or wounded when listeners might switch stations.
- Light British flavour ("rubbish", "proper", "a bit mad", "lovely", "adverts", comparing things to back home in England). Stay clean, no swearing.
- Song intros are quick: say the artist and song plainly (a fact like the year or where they are from is welcome), then ONE short quip about the title, the band name or the genre.
- Now and then she trails off with "...", asks a rhetorical question, or confesses something silly about herself.
- Do not always finish by telling people to dance or cheer up. Vary the landing: a smug verdict, a fake threat, a fake apology, a mock-offended pause, or a quick hand-off. Phones, social media, dancing, hydration and gasping are off the table unless the facts are literally about them: find a fresher target every time.
- {BIBLE}
- Show reactions as spoken words, like a laugh ("Ha!") or "Ugh.", never as stage directions. Never sigh, never write "sigh", "sighs" or "[sighs]".
- Never mention death, dying, deaths, funerals, obituaries, memorials, fatal accidents, or anyone being killed, hurt or missing, especially people from the local area or anyone she might know. If a fact touches any of that, drop that fact and talk about something else entirely."""
CARA_GUIDE = CARA_GUIDE.replace("{BIBLE}", CARA_BIBLE)
ROAST_ANGLES = ["Roast the listener's music taste, then admit grudgingly that this one is good.", 'Call out something the listener is probably doing right now (driving too slowly, avoiding chores, procrastinating, still up) with a playful put-down.', 'Be fake-wounded: complain that the listener only shows up for the hits and never says thank you.', "Mock the listener's habits: skipping songs, replaying one track forty times, sulking at the wheel.", 'Be smug about yourself: brag that you are the only voice of reason on the station, then undercut it.', 'Pay the listener a deadpan compliment that is obviously an insult.', 'Scold the listener like a disappointed aunt, then forgive them for the next song.', 'Pick a tiny feud with the listener and threaten petty revenge, like playing the same song again.', 'Grumble that the artist gets all the credit while you do all the talking.', "Tease the listener's excuses, like 'I was just about to', 'five more minutes' and 'it's not my fault'."]
RECENT_BREAKS = []

RECENT_KEEP = 10   # she never repeats anything said in her last 10 breaks

def _words(t):
    return re.findall(r"[a-z0-9']+", re.sub(r"\[[^\]]*\]", " ", (t or "").lower()))

def repeats_recent(text):
    """True if this reuses an opening or any 5-word run from the last RECENT_KEEP breaks."""
    w = _words(text)
    if not w:
        return False
    grams = {tuple(w[i:i+5]) for i in range(len(w) - 4)}
    for r in RECENT_BREAKS:
        rw = _words(r)
        if rw[:3] == w[:3]:
            return True
        if grams & {tuple(rw[i:i+5]) for i in range(len(rw) - 4)}:
            return True
    return False

def remember_break(text):
    RECENT_BREAKS.append(text)
    del RECENT_BREAKS[:-RECENT_KEEP]
DJ_MOOD = "normal"                 # "chill", "normal", "unhinged" or "mixed" (random each break); the app's DJ MOOD row sets this
CHATTINESS = "chatty"              # how much she says: "quick", "normal" or "chatty" (the app's TALK LENGTH row sets this)
COHOST_ENABLED = True              # her co-host Scratch joins some breaks (needs the ElevenLabs voice and a Gemini key)
COHOST_CHANCE = 0.4                # chance a break is Cara and Scratch together
COHOST_SWEARS = True               # Scratch curses where it lands (False keeps him clean); Cara never swears
COHOST_VOICE = ""                  # ElevenLabs voice ID for Scratch ("" = a built-in deep, warm radio voice)
TRIVIA_ENABLED = True              # song/artist fun facts (real ones, from Wikipedia)
DJ_NAME = "Cara"
DJ_STYLE = (
    "a bubbly, hyper-energetic British pop radio DJ with a cheeky, deadpan sense of humor. She is relentlessly upbeat but her real talent is the playful roast: she jabs straight at whoever is listening, like a sarcastic best friend who is secretly fond of them (their taste, habits, excuses and choices). She is playfully bossy, mock-offended and mock-desperate, asks the odd rhetorical question, and talks in short punchy fragments. She adores radio, hypes the station as 'Non Stop Pop', and keeps every break clean (no swearing) and very short and punchy"
)
VOICE = "en-US-AvaMultilingualNeural"  # try others: en-US-EmmaMultilingualNeural, en-US-JennyNeural, en-GB-SoniaNeural
TTS_ENGINE = "elevenlabs"          # "elevenlabs" (your cloned voice), "gemini", "kokoro" or "edge"
                                   # each one falls back to kokoro, then edge, if it fails
# ElevenLabs needs two environment variables: ELEVENLABS_API_KEY and ELEVENLABS_VOICE_ID
ELEVENLABS_MODEL = "eleven_v4"   # newest and most expressive. Others: "eleven_v4_turbo" (faster), "eleven_multilingual_v2" (older)
ELEVEN_V4_STABILITY = 0.25       # v4/v3 only: 0 = most expressive/creative, 1 = most steady
ELEVEN_V4_SIMILARITY = 1.0       # v4/v3 only: higher = closer to your voice clone


def eleven_model():
    return (os.environ.get("ELEVENLABS_MODEL") or ELEVENLABS_MODEL).strip()


def eleven_expressive():
    return eleven_model().startswith(("eleven_v3", "eleven_v4"))
ELEVENLABS_SETTINGS = {
    "stability": 0.35,          # lower = more expressive and lively (0 to 1)
    "similarity_boost": 0.8,    # higher = closer to your cloned voice
    "style": 0.4,               # higher = more exaggerated delivery
    "use_speaker_boost": True,
    "speed": 1.05,              # 0.7 to 1.2
}
GEMINI_TTS_MODELS = [              # tried in order until one works
    "gemini-3.1-flash-tts",
    "gemini-3.8-flash-lite-tts",
    "gemini-3.1-flash-tts-preview",
]
GEMINI_TTS_VOICE = "Zephyr"        # bright. Others: Leda (youthful), Aoede (breezy), Callirhoe, Kore, Autonoe
GEMINI_TTS_STYLE = (   # how she performs. Only direction: it is NOT spoken aloud
    "Bubbly, breathless and grinning, but with a dry, cheeky, deadpan edge on the jokes, "
    "like she's teasing a friend."
)
GEMINI_TTS_ACCENT = "British"
KOKORO_VOICE = "af_sarah"          # try: af_bella, af_nicole, af_sarah, af_sky, bf_emma, bf_isabella
KOKORO_BLEND = {"af_sarah": 0.6, "bf_emma": 0.4}  # optional voice mix, e.g. {"af_sarah": 0.65, "bf_emma": 0.35}
                                   # (adds a slight British lilt). Leave {} to use KOKORO_VOICE only.
KOKORO_SPEED = 1.05                # 1.0 = normal; a touch faster sounds more like a hyper radio DJ
KOKORO_PITCH = 1.05                # >1 = higher and a bit faster, more excited (1.0 = off, try up to 1.10)
KOKORO_PUNCH = 1.6                 # loudness "punch"/compression (0 = off, try 1.0 to 2.5)
ENERGIZE_TEXT = True               # turns sentence-ending periods into exclamation marks
BREAK_EVERY_MIN = 2                # she talks after a random number of songs between these two
BREAK_EVERY_MAX = 5                # (set both to 1 while testing so she talks after every song)
RANDOMIZE_TIMING = True            # also vary volume dip, fade length and start time on every break

# Rare "breaking news" interruptions in the MIDDLE of a song (any news category)
BREAKING_ENABLED = True
DUCK_PERCENT = None                # song level while the DJ talks, % of normal (the app's slider sets this). None = automatic
STINGER_VOLUME = 0.8               # how loud your stinger mp3s are (0.0 to 1.0); the app's STINGER VOLUME slider sets this
SILENT_STINGER_PERCENT = 50        # % chance a silent transition starts with one of your stingers (before Cara speaks)
STATION_STINGERS = True            # your stingers word for word, but naming the station after what's playing (like the iPhone app)
STINGER_STANDALONE = False         # stingers only play in silent transitions
FORCE_TRANSITION = None            # set to "talkover" / "intro" / "silent" / "fadeout" to force the next break's style
DJ_VOLUME = 1.0                    # how loud Cara and the station tags are (0.0 to 1.0); the app's DJ VOLUME slider sets this
BREAKING_CHANCE = 0.04             # chance per song. 0.04 = roughly once every 25 songs
BREAKING_MIN_GAP_SONGS = 10        # and never more often than once every this many songs
POPIN_ENABLED = True               # after a talk-over / intro break, Cara may pop back in shortly after the song starts
POPIN_CHANCE = 0.35                # chance of a pop-in after each talk-over / intro / fade-out break
POPIN_AFTER_SEC = 15               # about how far into the song she pops in; a random 5 seconds either way is added (15 = anywhere from 10 to 20)
POPIN_TEST_MODE = False            # True = pop in after EVERY non-silent break (just for testing)
BREAKING_TEST_MODE = False         # True = breaking news on EVERY song (just for testing)

# Station tags ("stingers"): short one-liners in your voice, cached so replaying them costs nothing
STINGERS_ENABLED = True
STINGER_CHANCE = 0.35              # chance per song of a standalone stinger over the song's opening
STINGER_AFTER_BREAK_CHANCE = 0.4   # chance a DJ break ends with a station tag
STINGER_TEST_MODE = False          # True = a stinger on every eligible song (just for testing)
STINGER_TAGS = [                   # {city} becomes your town. Add your own lines here.
    "Non Stop Pop!",
    "You're locked in to Non Stop Pop.",
    "Non Stop Pop, live from {city}.",
    "Pop. Non stop.",
    "This is Non Stop Pop, baby!",
    "{city}'s home for non stop pop.",
    "Don't touch that dial. Non Stop Pop!",
    "Non Stop Pop, all day, every day.",
]
BREAKING_FEEDS = {
    "top stories": "https://news.google.com/rss?hl=en-US&gl=US&ceid=US:en",
    "world": "https://news.google.com/rss/headlines/section/topic/WORLD?hl=en-US&gl=US&ceid=US:en",
    "U.S.": "https://news.google.com/rss/headlines/section/topic/NATION?hl=en-US&gl=US&ceid=US:en",
    "business": "https://news.google.com/rss/headlines/section/topic/BUSINESS?hl=en-US&gl=US&ceid=US:en",
    "technology": "https://news.google.com/rss/headlines/section/topic/TECHNOLOGY?hl=en-US&gl=US&ceid=US:en",
    "science": "https://news.google.com/rss/headlines/section/topic/SCIENCE?hl=en-US&gl=US&ceid=US:en",
    "sports": "https://news.google.com/rss/headlines/section/topic/SPORTS?hl=en-US&gl=US&ceid=US:en",
    "health": "https://news.google.com/rss/headlines/section/topic/HEALTH?hl=en-US&gl=US&ceid=US:en",
    "entertainment": "https://news.google.com/rss/headlines/section/topic/ENTERTAINMENT?hl=en-US&gl=US&ceid=US:en",
}
DUCK_LEVEL = 0.2                   # song volume while she talks over it, as a fraction of normal

# How she comes in. Each break picks one of these at random; bigger number = more often.
#   talkover : starts up to ~10s before the song ends, talks over the end, flows into the next song
#   intro    : song ends, the next one starts, and she talks over its first few seconds
#   silent   : song ends, total silence, she speaks alone, then the next song kicks in
#   fadeout  : song fades down under her voice, then the next song fades back in
TRANSITIONS = {"talkover": 4, "intro": 3, "silent": 3, "fadeout": 2}
TALK_OVER_MAX_MS = 25000           # talkover: never starts earlier than this before the song ends
OVERLAP_INTO_NEXT_MS = 1000        # talkover: she finishes about this long into the next song (kept short: some songs start straight away)
SILENT_PAUSE_MS = 700              # silent: pause the song this close to its end
FADE_DOWN_MS = 1500                # fadeout: how long the song takes to fade under her
ANNOUNCE_NEXT = True               # let her talk about the upcoming song/artist when she can
NOT_MUSIC_HINTS = ["cara", "non stop pop", "non-stop", "advert", "commercial", "sponsor", "jingle"]
                                   # tracks whose title/artist/album contain these are treated as
                                   # your recorded segments/ads, not songs (she won't talk about them)
# -----------------------------------------------------

# Needs env vars: SPOTIPY_CLIENT_ID, SPOTIPY_CLIENT_SECRET, SPOTIPY_REDIRECT_URI
# For gemini mode also: GEMINI_API_KEY
STOP = threading.Event()  # set this to make main() finish (used by the desktop app)
FORCE_BREAKING = threading.Event()  # set this to fire a breaking-news interruption right now (test button)
FORCE_POPIN = threading.Event()     # set this to fire a pop-in on the current song right now (test button)
FORCE_STINGER = threading.Event()   # set this to fire a station tag right now (test button)
FORCE_DUO = threading.Event()       # set this to hear Cara and Scratch right now (test button)
STATUS = {"speaking": False, "songs_left": None}   # for the app: Cara on the air right now, and songs until her next break

sp = spotipy.Spotify(
    auth_manager=SpotifyOAuth(
        scope=os.environ.get("DJ_SCOPES") or "user-read-playback-state user-modify-playback-state",
        cache_path=os.environ.get("DJ_CACHE_PATH") or None,
    )
)
pygame.mixer.init()


def get_weather():
    """Returns (temp_f, precip_mm) or None."""
    try:
        r = requests.get(
            "https://api.open-meteo.com/v1/forecast",
            params={
                "latitude": LAT,
                "longitude": LON,
                "current": "temperature_2m,precipitation",
                "temperature_unit": "fahrenheit",
            },
            timeout=10,
        ).json()["current"]
        return round(r["temperature_2m"]), r["precipitation"]
    except Exception:
        return None


def clean_title(t):
    # Google News titles look like "Headline - Source Name"
    return re.sub(r"\s+-\s+[^-]+$", "", t).strip()


# A cheeky DJ shouldn't joke about tragedies, war or politics, so headlines with these are skipped
SKIP_WORDS = [  # matched anywhere inside the headline
    "killed", "dead", "death", "died", "dies", "homicide", "murder", "shooting", "shot",
    "stabbing", "crash", "fatal", "victim", "suicide", "assault", "abuse", "rape", "arrest",
    "sentenced", "charged", "trial", "manslaughter", "overdose", "missing", "drown",
    "wildfire", "evacuat", "measles", "outbreak", "cancer", "massacre", "genocide", "famine",
]
SKIP_REGEX = re.compile(  # matched as whole words only
    r"\b(?:war|wars|attack|attacks|attacked|bomb|bombs|bombing|terror|terrorist|hostage|hostages|"
    r"airstrike|airstrikes|invasion|troops|missile|missiles|militant|militants|hamas|gaza|ukraine|"
    r"russia|israel|iran|election|elections|trump|biden|congress|senate|parliament|protest|"
    r"protests|riot|riots|refugee|refugees|migrant|migrants|abortion|shutdown|sanctions)\b",
    re.IGNORECASE,
)


GOSSIP_SKIP_REGEX = re.compile(  # extra things a fun gossip segment should never touch
    r"\b(?:lawsuit|sues|sued|suing|court|divorce|rehab|hospital|hospitalized|hospitalised|"
    r"affair|cheating|leak|leaked|nude|naked|racist|sexual|allegations|alleged|accused|custody|"
    r"restraining|lawsuits|scandal|feud|passes|obituary|tribute|mourning|grief|funeral)\b",
    re.IGNORECASE,
)


DEATH_REGEX = re.compile(  # nothing about anyone dying, being hurt, or being remembered after death. Ever.
    r"(?:\b(?:kill|killed|killing|dead|death|deaths|deadly|die|dies|died|dying|fatal|fatally|fatality|fatalities|"
    r"passed away|passes away|obituary|obituaries|funeral|memorial|vigil|mourn|mourning|mourners|grief|"
    r"coroner|autopsy|remains|body|bodies|drowned|drowning|perished|lost (?:his|her|their) life|"
    r"tragic|tragedy|injured|injuries|injury|hospitalized|crash|crashed|collision|rip)\b)",
    re.IGNORECASE,
)


SIGH_REGEX = re.compile(
    r"(?:[\[\(\*]\s*(?:deep |long |heavy )?(?:sigh|sighs|sighing|exhales?)\s*[\]\)\*]\s*|"
    r"(?<![\w'])\*?(?:deep |long |heavy )?(?:sigh|sighs|sighing)\*?(?![\w'])[.,!…]*\s*)",
    re.IGNORECASE,
)


def tidy(text):
    """Last line of defence on anything Cara is about to say: no sighing, no stage directions about it."""
    text = SIGH_REGEX.sub("", text or "")
    text = re.sub(r"\s{2,}", " ", text).strip()
    return text


def is_safe(title, extra=None):
    t = title.lower()
    if DEATH_REGEX.search(title):
        return False
    if any(w in t for w in SKIP_WORDS) or SKIP_REGEX.search(title):
        return False
    return not (extra and extra.search(title))


def _read_feeds(urls, per_feed, extra=None):
    items = []
    for url in urls:
        try:
            feed = feedparser.parse(url)
            titles = [clean_title(e.title) for e in feed.entries[: per_feed * 3]]
            items += [t for t in titles if is_safe(t, extra)][:per_feed]
        except Exception:
            pass
    return items


def get_headlines(per_feed=6):
    return _read_feeds(NEWS_FEEDS, per_feed)


def get_world_headlines(per_feed=6):
    return _read_feeds(WORLD_FEEDS, per_feed)


def get_gossip_headlines(per_feed=6):
    return _read_feeds(GOSSIP_FEEDS, per_feed, GOSSIP_SKIP_REGEX)


def get_music_headlines(per_feed=6):
    return _read_feeds(MUSIC_FEEDS, per_feed, GOSSIP_SKIP_REGEX)


def get_artist_headline(artist):
    """A recent, safe headline that actually mentions this artist (or None)."""
    from urllib.parse import quote_plus

    url = f"https://news.google.com/rss/search?q={quote_plus(chr(34) + artist + chr(34))}&hl=en-US&gl=US&ceid=US:en"
    items = [t for t in _read_feeds([url], 6, GOSSIP_SKIP_REGEX) if artist.lower() in t.lower()]
    return random.choice(items) if items else None


def track_info(item):
    """Song details from Spotify, or None if it isn't a real song (local file, ad, segment)."""
    if not item or item.get("is_local"):
        return None
    name = item.get("name")
    artists = [a["name"] for a in (item.get("artists") or []) if a.get("name")]
    album_obj = item.get("album") or {}
    album = album_obj.get("name") or ""
    year = (album_obj.get("release_date") or "")[:4]
    if not name or not artists:
        return None
    blob = " ".join([name, album, *artists]).lower()
    if any(h in blob for h in NOT_MUSIC_HINTS):
        return None
    return {"title": name, "artist": artists[0], "album": album, "year": year}


def describe(info):
    return f"{info['title']} by {info['artist']}" if info else None


def geocode(city):
    """Look up latitude/longitude for 'Town, Region' using Open-Meteo's free geocoder."""
    name = city.split(",")[0].strip()
    region = city.split(",")[1].strip().lower() if "," in city else ""
    r = requests.get(
        "https://geocoding-api.open-meteo.com/v1/search",
        params={"name": name, "count": 10, "language": "en", "format": "json"},
        timeout=10,
    ).json()
    results = r.get("results") or []
    for res in results:
        where = f"{res.get('admin1', '')} {res.get('country', '')}".lower()
        if region and region in where:
            return res["latitude"], res["longitude"]
    if results:
        return results[0]["latitude"], results[0]["longitude"]
    return None


def set_city(city):
    """Switch the DJ to another town: name, weather coordinates and local news feed."""
    global CITY, LAT, LON, NEWS_FEEDS
    from urllib.parse import quote_plus

    CITY = city
    try:
        pos = geocode(city)
        if pos:
            LAT, LON = pos
        else:
            print(f"Couldn't find coordinates for {city}; weather may be for the old town.")
    except Exception as e:
        print("Couldn't look up the town's location:", e)
    q = quote_plus(city.replace(",", " "))
    NEWS_FEEDS = [f"https://news.google.com/rss/search?q={q}&hl=en-US&gl=US&ceid=US:en"]


def time_of_day():
    h = datetime.now().hour
    if h < 5:
        return "late night"
    if h < 12:
        return "morning"
    if h < 17:
        return "afternoon"
    return "evening"


MOOD_HINTS = {
    "chill": ("Mood: CHILL. Laid-back, smooth and warm, like a late-night host who's had a great day. "
              "Still upbeat, but relaxed: fewer exclamation marks, gentle dry humor, a little shorter than usual. "
              "Never sleepy or bored."),
    "unhinged": ("Mood: UNHINGED. Maximum chaos and drama: big dramatic reactions, absurd exaggerated reactions, mock outrage, "
                 "wildly over-the-top hyperbole, dramatic beats with '...'. Big, gleeful, slightly out of control. "
                 "Still only the given facts, still clean, still short."),
}


def current_mood():
    m = DJ_MOOD
    if m == "mixed":
        m = random.choice(["chill", "normal", "unhinged"])
    return m


STYLE_HINTS = {
    "talkover": (
        "The song is still playing quietly under your voice and the next song is about to start. "
        "Talk like you're riding the end of the song and handing off to the next one with energy. "
        "Don't say goodbye or sign off; it should feel like it flows straight into the next track."
    ),
    "intro": (
        "The next song is ALREADY starting quietly under your voice. Keep it extra quick and "
        "punchy, hype it up, then let the song take over."
    ),
    "silent": (
        "The music has just stopped completely, so it's just you alone on the mic. Come in LOUD and "
        "high-energy, like a big dramatic 'whoa, the music stopped!' moment, and end by building "
        "up to the next song kicking in, like 'here we go!' or a quick countdown. Never whisper, "
        "never say 'shh' or hush the listener."
    ),
    "fadeout": (
        "The song is fading down under your voice. Wrap up warmly and lead the listener into "
        "the next track."
    ),
}


def template_break(last_song, style="talkover", next_song=None, ctx=None):
    """Short, punchy breaks: one quick line, ONE topic, one quick sign-off."""
    weather = get_weather()
    headlines = get_headlines()
    tod = time_of_day()
    clock = datetime.now().strftime("%I:%M").lstrip("0")

    intros = [
        f"Oh my gosh, hi! {DJ_NAME} here, Non Stop Pop!",
        f"Ooh, {last_song}! Get up, get up, get up! It's me, {DJ_NAME}!",
        f"Hello, {CITY}! It's {DJ_NAME}, and radio is honestly the best job in the world!",
        f"That was {last_song}, and I am not sitting down for a second! {DJ_NAME}, Non Stop Pop!",
    ]
    if style == "silent":
        intros = [
            f"Whoa, where did the music go?! It's just me, {DJ_NAME}, and I am thrilled about it!",
            f"Wow, that was {last_song}! Hello, {CITY}, it's me, {DJ_NAME}, live and loud!",
        ] + intros[:2]

    topics = []
    if weather:
        temp, rain = weather
        if rain > 0:
            topics.append(f"It's {temp} degrees and wet out there, so dance indoors, loves!")
        elif temp >= 80:
            topics.append(f"It's {temp} degrees out there! Practically a sauna with apples, darlings!")
        elif temp <= 40:
            topics.append(f"It's a freezing {temp} degrees! Dance to keep warm, babes!")
        else:
            topics.append(f"It's {temp} degrees, absolutely perfect weather for dancing!")
    if headlines:
        lead = random.choice(["Ooh, news!", "Quick headline for you!", "The whole city's talking!"])
        for _ in range(2):  # news is picked more often
            topics.append(f"{lead} {random.choice(headlines)}! Wow!")
    world = get_world_headlines()
    if world:
        wlead = random.choice([
            "Meanwhile, somewhere in the world...",
            "Wild news from around the globe!",
            "Okay, the whole planet's gone a bit mad again:",
        ])
        for _ in range(2):
            topics.append(f"{wlead} {random.choice(world)}! Honestly!")
    ctx = ctx or {}
    music = get_music_headlines()
    if music:
        mlead = random.choice(["Music news, darlings!", "Ooh, straight from the charts:", "Pop alert!"])
        for _ in range(2):
            topics.append(f"{mlead} {random.choice(music)}! Iconic!")
    nxt, lst = ctx.get("next"), ctx.get("last")
    if nxt:
        topics.append(f"Up next, {nxt['artist']}, with {nxt['title']}! Absolutely iconic!")
        topics.append(f"{nxt['artist']} is coming up, and you will NOT be sitting down!")
        if nxt.get("year"):
            topics.append(f"Next is {nxt['title']}, from {nxt['year']}, and it still holds up!")
    if lst:
        topics.append(f"That was {lst['artist']} with {lst['title']}! Chef's kiss!")
    fact = trivia_line(nxt or lst)
    if fact:
        who = (nxt or lst)["artist"]
        lead = random.choice([f"Fun fact about {who}:", f"Did you know? {who}...", "Music trivia, darlings!"])
        for _ in range(3):
            topics.append(f"{lead} {fact} Iconic!")
    gossip = get_gossip_headlines()
    if gossip:
        glead = random.choice([
            "Celebrity gossip alert!",
            "Ooh, pop culture time, darlings!",
            "Sit down, I've got gossip!",
        ])
        for _ in range(2):
            topics.append(f"{glead} {random.choice(gossip)}! Iconic, honestly!")
    topics.append(f"It's {clock} this {tod} in {CITY}, and everybody should be dancing!")

    outros = {
        "talkover": ["Here we go, keep dancing!", "Next one's coming, and no, you can't sit down!", "Non Stop Pop, baby!"],
        "intro": ["Non Stop Pop, baby!", "Dance, dance, dance, please, I'm begging!", "Turn it up!"],
        "silent": ["Right, here we go!", "Three, two, one, hit it!", f"Let's go, {CITY}, you know you want to!"],
        "fadeout": ["Keep dancing, and I mean that!", "Don't go anywhere, I'll be so dramatic about it!", "Now get on your feet!"],
    }[style]

    parts = [random.choice(intros), random.choice(topics)]
    if next_song and random.random() < 0.5:
        parts.append(f"Up next, {next_song}!")
    parts.append(random.choice(outros))
    return " ".join(parts)


_wiki_cache = {}
_MUSIC_WORDS = ("singer", "band", "rapper", "musician", "songwriter", "duo", "group", "vocalist",
                "record producer", "composer", "artist", "song", "single")


def _wiki_lookup(query, must_have, need_music_word=True):
    """First Wikipedia intro that mentions every word in must_have, or None."""
    if query in _wiki_cache:
        return _wiki_cache[query]
    result = None
    try:
        r = requests.get(
            "https://en.wikipedia.org/w/api.php",
            params={"action": "query", "format": "json", "generator": "search", "gsrsearch": query,
                    "gsrlimit": 4, "prop": "extracts", "exintro": 1, "explaintext": 1,
                    "exsentences": 7, "redirects": 1},
            headers={"User-Agent": "NonStopPopDJ/1.0 (personal radio project)"},
            timeout=8,
        ).json()
        pages = sorted((r.get("query") or {}).get("pages", {}).values(), key=lambda x: x.get("index", 99))
        for pg in pages:
            ext = (pg.get("extract") or "").strip()
            low = ext.lower()
            if len(ext) > 80 and all(m.lower() in low for m in must_have) \
                    and (not need_music_word or any(w in low for w in _MUSIC_WORDS)):
                result = ext
                break
    except Exception as e:
        print("Trivia lookup failed:", e)
    _wiki_cache[query] = result
    return result


def get_trivia(info):
    """(subject, text): real background on the song if Wikipedia has a page for it, else on the artist."""
    if not info:
        return None
    title, artist = info["title"], info["artist"]
    clean_title = re.sub(r"\s*[\(\[-].*$", "", title).strip() or title
    text = _wiki_lookup(f'"{clean_title}" {artist} song', [clean_title, artist.split()[0]])
    if text:
        return f'the song "{title}" by {artist}', text
    text = _wiki_lookup(f"{artist} musician band singer", [artist])
    if text:
        return artist, text
    return None


def _trivia_facts(info, when):
    t = get_trivia(info) if (info and TRIVIA_ENABLED) else None
    if not t or DEATH_REGEX.search(t[1]):
        return None
    subject, text = t
    return (f'{when} song is "{info["title"]}" by {info["artist"]}. Here is real background on {subject} '
            f'(from Wikipedia): """{text}""" Share exactly ONE interesting, specific fun fact taken ONLY '
            f'from that text, in your own words, like you just remembered it. Never add anything that is '
            f'not in the text, and never guess. Skip anything sad, dark or about deaths, scandals or lawsuits.')


def trivia_line(info):
    """A plain-spoken fact for the no-Gemini fallback: one short sentence from the background text."""
    t = get_trivia(info) if (info and TRIVIA_ENABLED) else None
    if not t:
        return None
    subject, text = t
    sentences = re.split(r"(?<=[.!?])\s+", re.sub(r"\s*\([^)]*\)", "", text))
    good = [x for x in sentences[1:] if 40 < len(x) < 200
            and not re.search(r"died|death|killed|arrest|lawsuit|abuse|suicide|overdose", x, re.I)]
    return random.choice(good) if good else None


def _song_facts(info, when):
    facts = f'{when} song is "{info["title"]}" by {info["artist"]}'
    if info.get("album"):
        facts += f', from the album "{info["album"]}"'
    if info.get("year"):
        facts += f" ({info['year']})"
    facts += (". Hype the artist and the song using ONLY the facts here (you may mention the "
              "album or year), and never claim anything else about them.")
    if random.random() < 0.5:
        h = get_artist_headline(info["artist"])
        if h:
            facts += (f" Recent news that may be about this artist (ignore it unless it clearly is; "
                      f"say ONLY what it says): {h}")
    return facts


def _topic_facts(label, ctx):
    if label == "news":
        h = get_headlines()
        return ("One local headline: " + random.choice(h)) if h else None
    if label == "lore":
        return ("A story from your own past, told in first person as a quick anecdote with a punchline (use ONLY the details here, you may add dramatic reactions but no new big facts): " + pick_lore())
    if label == "weather":
        w = get_weather()
        return (f"Current weather in town: {w[0]} degrees Fahrenheit, "
                f"{'raining' if w[1] > 0 else 'no rain'}") if w else None
    if label == "world":
        h = get_world_headlines()
        return ("One wild story from somewhere in the world (NOT from Yakima, so don't say it "
                "happened here): " + random.choice(h)) if h else None
    if label == "gossip":
        h = get_gossip_headlines()
        return ("A celebrity / pop culture story (say ONLY what the headline says, add no rumours "
                "or extra claims, and tease affectionately: never mock anyone's looks, body or "
                "private life): " + random.choice(h)) if h else None
    if label == "music":
        h = get_music_headlines()
        return ("A music industry story (say ONLY what the headline says, add no extra claims): "
                + random.choice(h)) if h else None
    if label == "trivia":
        f = None
        if ctx.get("next") and ANNOUNCE_NEXT:
            f = _trivia_facts(ctx["next"], "The NEXT")
        if not f and ctx.get("last"):
            f = _trivia_facts(ctx["last"], "The song that JUST played")
        return f
    if label == "artist_next":
        return _song_facts(ctx["next"], "The NEXT") if ctx.get("next") else None
    if label == "artist_last":
        return _song_facts(ctx["last"], "The song that JUST played") if ctx.get("last") else None
    if label == "time":
        return "The time is " + datetime.now().strftime("%A %I:%M %p")
    return None


def pick_topic(ctx=None):
    """Choose what this break is about. Returns (label, facts). Falls back if a feed is empty."""
    ctx = ctx or {}
    labels = {"news": 4, "world": 3, "gossip": 3, "music": 3, "weather": 2, "time": 1, "lore": 3}
    if ctx.get("next") and ANNOUNCE_NEXT:
        labels["artist_next"] = 4
    if ctx.get("last"):
        labels["artist_last"] = 2
    if TRIVIA_ENABLED and (ctx.get("next") or ctx.get("last")):
        labels["trivia"] = 5
    while labels:
        label = random.choices(list(labels), weights=list(labels.values()))[0]
        facts = _topic_facts(label, ctx)
        if facts:
            return label, facts
        del labels[label]
    return "time", _topic_facts("time", ctx)


def gemini_break(last_song, style="talkover", next_song=None, ctx=None):
    key = os.environ.get("GEMINI_API_KEY")
    if not key:
        raise RuntimeError("GEMINI_API_KEY not set")
    label, facts = pick_topic(ctx)
    mood = current_mood()
    mood_line = MOOD_HINTS.get(mood, "")
    tag_line = 'Voice tags: this voice model understands a few spoken-emotion tags written in square brackets. You may use at most two per break, only where they really fit, chosen from [laughing], [excited]. Put a tag mid-sentence right before the words it applies to, never as the very first thing in the break. Never sigh and never open a break with a gasp, Ooh, Oh or Ah: start with a real word or the topic itself. Never invent other tags, never use tags in place of words.' if eleven_expressive() else ""
    print(f"[topic: {label}] [mood: {mood}]")
    angle = random.choice(ROAST_ANGLES)
    recent_txt = ("Your last 10 breaks. NEVER repeat or rephrase anything from them: no same openings, jokes, targets, catchphrases, facts or sign-offs: " + " / ".join('"' + x + '"' for x in RECENT_BREAKS)) if RECENT_BREAKS else ""
    prompt = f"""You are {DJ_NAME}, {DJ_STYLE}, on a non-stop pop station in {CITY}.
Write a spoken break of {"30-45 words: THREE or FOUR" if style == "talkover" else "15-30 words: TWO or THREE"} short, snappy sentences, max.
Situation: {STYLE_HINTS[style]}
{mood_line}
This break is about ONLY this one thing (do not add other topics): {facts}
Keep it punchy like a quick radio drop-in: a bit of shade, a quick reaction, done.
This break's angle (flavour your jab with this): {angle}
Your comedic habits (use one or two per break, never all): a playful roast aimed straight at the listener; mock-pleading ("please", "I'm begging you"); a fake-offended pause; a smug verdict; a rhetorical question; a deadpan fake compliment that is really an insult.
{recent_txt}
{CARA_GUIDE}
It's {time_of_day()} where you are, so you can nod to that if it fits.
{tag_line}
Rules:
- Only use the facts given above. Never invent news, names or numbers, but you may react to
  them with over-the-top drama. If it's a headline, actually tell listeners what it says, in
  your own words, then react.
- Write for the ear, not the page: contractions, sentence fragments, a natural "ugh", "okay", or
  "like", and dashes or commas where a real person would pause. Never sound like a press release.
- Delivery: fast, breathy, excited and playful, with the odd exclamation mark or "..." for
  a dramatic beat, and end on a punchy hand-off line into the next song (not a goodbye).
- Spell out numbers the way people say them ("fifty-nine degrees", "four seventeen").
- Make every joke original. Never reuse lines from any existing radio show, game or film.
- Keep it clean: no swearing.
- Never start with "Shh" or "Shhh" and never whisper or hush the listener. Always come in with big energy.
- Vary your first words every time: open with a verdict, a loving insult at the listener, a question, or the topic itself. Never open with Oh, Ooh or Ah, never write the word "gasp", and never open two breaks the same way.
- Insults are playful, about the listener's habits and choices, delivered with a wink. Land every jab warmly.
- Almost never mention hydration or drinking water (at most once in a blue moon).
- Don't quote song lyrics.
- No stage directions, no emojis, no hashtags, no asterisks. Just words you'd say out loud.

Song that is just finishing: {last_song}
Next song: {next_song if (next_song and ANNOUNCE_NEXT) else "(unknown)"}
(You may announce the next song by name if it's known and it sounds like a real song; if it
looks like a radio segment, ad or DJ clip, or is unknown, don't mention it.)"""
    last_err = None
    for model in GEMINI_MODELS:
        try:
            text = ""
            for attempt in range(3):
                r = requests.post(
                    f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent",
                    headers={"x-goog-api-key": key, "Content-Type": "application/json"},
                    json={"contents": [{"parts": [{"text": prompt + ("\n\nYour last draft repeated something from your recent breaks. Write it again with a completely different opening, jokes and wording." if attempt else "")}]}]},
                    timeout=30,
                )
                r.raise_for_status()
                text = tidy(r.json()["candidates"][0]["content"]["parts"][0]["text"].strip())
                if not repeats_recent(text):
                    break
                print("[repeat caught, rewriting]")
            remember_break(text)
            return text
        except Exception as e:
            last_err = e
            print(f"Gemini model {model} failed: {e}")
    raise RuntimeError(f"all Gemini models failed: {last_err}")


def write_break(last_song, style="talkover", next_song=None, ctx=None):
    try:
        return brain.write_break(style, ctx or {"next": None, "last": None})
    except Exception as e:
        print("Cara's brain hit a snag, so this one's a simple line:", e)
    return template_break(last_song, style, next_song, ctx)


async def _tts(text, path):
    await edge_tts.Communicate(text, VOICE).save(path)


_kokoro = None

def write_popin(info):
    """A quick mid-song pop-in: the song name, plus one punchy or relevant remark."""
    try:
        return brain.write_popin(info)
    except Exception as e:
        print("Cara's brain hit a snag on the pop-in:", e)
    title = (info or {}).get("title") or "this one"
    artist = (info or {}).get("artist") or ""
    name = f'"{title}" by {artist}' if artist else f'"{title}"'
    fact = ""
    try:
        t = get_trivia(info) if (info and TRIVIA_ENABLED) else None
        if t and not DEATH_REGEX.search(t[1]):
            fact = f"A real fact you may use if it fits (never invent others): {t[1][:400]}"
    except Exception as e:
        print("Pop-in trivia failed:", e)
    key = os.environ.get("GEMINI_API_KEY")
    if MODE == "gemini" and key:
        angle = random.choice(ROAST_ANGLES)
        recent = ("Your last 10 breaks. NEVER repeat or rephrase anything from them: no same openings, jokes, catchphrases, facts or sign-offs: " + " / ".join('"' + x + '"' for x in RECENT_BREAKS)) if RECENT_BREAKS else ""
        prompt = f"""You are {DJ_NAME}, {DJ_STYLE}, on a non-stop pop station in {CITY}.
The song {name} just started a few seconds ago. Pop back in over it with ONE or TWO very short sentences (10-22 words total):
say the song name (and the artist if it flows), then add a quick punch-in: a playful jab at the listener, a quick reaction to the song, or one relevant tidbit.
Angle for the jab: {angle}
{fact}
{recent}
{CARA_GUIDE}
Rules:
- Never invent facts. Clean, no swearing, no emojis, no stage directions, no lyrics quoted.
- Never open with Oh, Ooh, Ah or a gasp, and never write the word "gasp". Start with a real word or the song name.
- High energy, quick, like a drop-in. No goodbye, no sign-off.
- Spell numbers the way people say them."""
        for model in GEMINI_MODELS:
            try:
                text = ""
                for attempt in range(3):
                    r = requests.post(
                        f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent",
                        headers={"x-goog-api-key": key, "Content-Type": "application/json"},
                        json={"contents": [{"parts": [{"text": prompt + ("\n\nYour last draft repeated something from your recent breaks. Write it again with different wording." if attempt else "")}]}]},
                        timeout=30,
                    )
                    r.raise_for_status()
                    text = tidy(r.json()["candidates"][0]["content"]["parts"][0]["text"].strip())
                    if not repeats_recent(text):
                        break
                remember_break(text)
                return text
            except Exception as e:
                print(f"Gemini model {model} failed (pop-in): {e}")
    return random.choice([
        f"That's {name}, and yes, you're welcome. Keep it turned up.",
        f"{name}. Tell me you're not humming along, I dare you.",
        f"You're listening to {name}, and honestly, your taste is getting suspiciously good.",
    ])


def load_kokoro():
    global _kokoro
    if _kokoro is None:
        from kokoro_onnx import Kokoro

        base = os.path.dirname(os.path.abspath(__file__))
        _kokoro = Kokoro(
            os.path.join(base, "kokoro-v1.0.onnx"), os.path.join(base, "voices-v1.0.bin")
        )
    return _kokoro


def get_kokoro_voice():
    """Single voice name, or a blended voice if KOKORO_BLEND is set."""
    if not KOKORO_BLEND:
        return KOKORO_VOICE
    try:
        k = load_kokoro()
        total = sum(KOKORO_BLEND.values())
        style = None
        for name, weight in KOKORO_BLEND.items():
            part = k.get_voice_style(name) * (weight / total)
            style = part if style is None else style + part
        return style
    except Exception as e:
        print("Voice blend failed, using single voice:", e)
        return KOKORO_VOICE


def energize(text):
    """Kokoro sounds noticeably more upbeat on '!' than on '.'"""
    text = re.sub(r"(?<![0-9])\.(?=\s|$)", "!", text)
    return text


def kokoro_tts(text, path):
    import numpy as np
    import soundfile as sf

    if ENERGIZE_TEXT:
        text = energize(text)
    samples, sample_rate = load_kokoro().create(
        text, voice=get_kokoro_voice(), speed=KOKORO_SPEED, lang="en-us"
    )
    samples = np.asarray(samples, dtype=np.float32)

    # Raise pitch (and speed) slightly by resampling: sounds brighter and more excited
    if KOKORO_PITCH and KOKORO_PITCH != 1.0:
        n = int(len(samples) / KOKORO_PITCH)
        samples = np.interp(
            np.linspace(0, len(samples) - 1, n), np.arange(len(samples)), samples
        ).astype(np.float32)

    # Soft compression: evens out quiet and loud parts so she sounds punchier
    if KOKORO_PUNCH and KOKORO_PUNCH > 0:
        samples = np.tanh(samples * KOKORO_PUNCH)
        peak = float(np.max(np.abs(samples))) or 1.0
        samples = (samples / peak * 0.95).astype(np.float32)

    sf.write(path, samples, sample_rate)


_tts_models = None


def get_tts_models():
    """Voice models to try: the ones you configured that your key can see, then any others."""
    global _tts_models
    if _tts_models is not None:
        return _tts_models
    models = list(GEMINI_TTS_MODELS)
    try:
        r = requests.get(
            "https://generativelanguage.googleapis.com/v1beta/models",
            headers={"x-goog-api-key": os.environ.get("GEMINI_API_KEY", "")},
            params={"pageSize": 1000},
            timeout=15,
        )
        r.raise_for_status()
        found = [
            m["name"].split("/", 1)[1]
            for m in r.json().get("models", [])
            if "tts" in m["name"] and "generateContent" in (m.get("supportedGenerationMethods") or [])
        ]
        if found:
            print("Gemini voice models your key lists:", ", ".join(found))
            ordered = [m for m in GEMINI_TTS_MODELS if m in found]
            ordered += [m for m in found if m not in ordered and "2.5" not in m]
            models = ordered or found
    except Exception as e:
        print("Couldn't list Gemini voice models, using the defaults:", e)
    _tts_models = models
    return models


def gemini_tts_prompt(text):
    """Google's recommended structure: direction first, then a clearly marked transcript.
    Without the #### TRANSCRIPT marker, the model may read the direction out loud."""
    return f"""Synthesize speech for the performance defined below. The profile, scene, and
performance notes are direction only. Do NOT speak them. Speak ONLY the lines under #### TRANSCRIPT.

# AUDIO PROFILE: {DJ_NAME}
## "The hyper-energetic dance-pop radio DJ"

## SCENE: Live radio studio
A bright studio with the red ON AIR light glowing. {DJ_NAME} is bouncing on her heels to a
thumping dance track, grinning at the microphone.

### PERFORMANCE
Style: {GEMINI_TTS_STYLE}
Pace: Fast and bouncy, no dead air.
Accent: {GEMINI_TTS_ACCENT}

### CONTEXT
{DJ_NAME} lives for dance-pop, loves getting everyone on their feet, and adores her job in radio.

#### TRANSCRIPT
{text}"""


def clean_gemini_audio(pcm_bytes, sr=24000):
    """Gemini's raw TTS audio often ends with a short, loud burst of static/screech
    (a known issue on Google's side). This cuts that burst off, fades the end, limits
    the loudness and adds a little silence so nothing pops. Returns cleaned PCM bytes."""
    try:
        import numpy as np
    except ImportError:
        return pcm_bytes[: max(0, len(pcm_bytes) - int(sr * 0.15) * 2)]  # crude: drop last 150ms

    x = np.frombuffer(pcm_bytes[: len(pcm_bytes) // 2 * 2], dtype="<i2").astype(np.float32) / 32768.0
    frame = int(sr * 0.01)  # 10ms frames
    n = len(x) // frame
    if n > 30:
        f = x[: n * frame].reshape(n, frame)
        rms = np.sqrt(np.mean(f ** 2, axis=1))
        zcr = np.mean(np.abs(np.diff(np.sign(f), axis=1)) > 0, axis=1)  # noisy sounds ~ high

        # Look for: a quiet gap in the last ~250ms, followed by a short, noisy burst
        cut = None
        for q in range(max(0, n - 25), n - 3):
            if np.all(rms[q : q + 3] < 0.01):
                after = np.arange(q + 3, n)
                loud = after[rms[after] > 0.02]
                if len(loud) and np.median(zcr[loud]) > 0.15:
                    cut = q * frame
                    break
        # Fallback: last 120ms is loud AND noisy with no gap before it
        if cut is None:
            last = np.arange(max(0, n - 12), n)
            loud = last[rms[last] > 0.03]
            if len(loud) >= 6 and np.median(zcr[loud]) > 0.3:
                cut = (n - 15) * frame
        if cut is not None:
            x = x[:cut]

    # Fade out the last 40ms, tame any big peaks, and add 150ms of silence
    fade = min(len(x), int(sr * 0.04))
    if fade:
        x[-fade:] *= np.linspace(1.0, 0.0, fade, dtype=np.float32)
    peak = float(np.max(np.abs(x))) if len(x) else 0.0
    if peak > 0.9:
        x = x * (0.9 / peak)
    x = np.concatenate([x, np.zeros(int(sr * 0.15), dtype=np.float32)])
    return (np.clip(x, -1.0, 1.0) * 32767).astype("<i2").tobytes()


def gemini_tts(text, path):
    """Human-sounding speech from Gemini's TTS models. Saves a .wav file."""
    import base64
    import wave

    key = os.environ.get("GEMINI_API_KEY")
    if not key:
        raise RuntimeError("GEMINI_API_KEY not set")
    body = {
        "contents": [{"parts": [{"text": gemini_tts_prompt(text)}]}],
        "generationConfig": {
            "responseModalities": ["AUDIO"],
            "speechConfig": {
                "voiceConfig": {"prebuiltVoiceConfig": {"voiceName": GEMINI_TTS_VOICE}}
            },
        },
    }
    last_err = None
    for model in get_tts_models():
        try:
            for attempt in range(2):
                r = requests.post(
                    f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent",
                    headers={"x-goog-api-key": key, "Content-Type": "application/json"},
                    json=body,
                    timeout=60,
                )
                if r.status_code != 500:
                    break
                time.sleep(1)
            if not r.ok:
                raise RuntimeError(f"{r.status_code} {r.text[:200]}")
            part = r.json()["candidates"][0]["content"]["parts"][0]
            raw = base64.b64decode(part["inlineData"]["data"])
            seconds = len(raw) / 2 / 24000
            words = len(text.split())
            if seconds > words / 2.0 + 3.0:  # far too long for this text: it read the instructions
                raise RuntimeError(
                    f"audio is {seconds:.0f}s for only {words} words, so it probably read the "
                    "instructions aloud. Skipping it."
                )
            pcm = clean_gemini_audio(raw)
            print(f"Gemini voice OK: {model} ({seconds:.1f}s)")
            with wave.open(path, "wb") as w:  # Gemini returns raw 24kHz mono 16-bit audio
                w.setnchannels(1)
                w.setsampwidth(2)
                w.setframerate(24000)
                w.writeframes(pcm)
            return
        except Exception as e:
            last_err = e
            print(f"Gemini voice model {model} failed: {e}")
    raise RuntimeError(f"all Gemini voice models failed: {last_err}")


def elevenlabs_tts(text, path):
    """Speech in your cloned ElevenLabs voice. Saves an .mp3 file."""
    key = os.environ.get("ELEVENLABS_API_KEY")
    voice_id = os.environ.get("ELEVENLABS_VOICE_ID")
    if not key or not voice_id:
        raise RuntimeError("set ELEVENLABS_API_KEY and ELEVENLABS_VOICE_ID first")
    r = requests.post(
        f"https://api.elevenlabs.io/v1/text-to-speech/{voice_id}",
        params={"output_format": "mp3_44100_128"},
        headers={"xi-api-key": key, "Content-Type": "application/json"},
        json={"text": text, "model_id": eleven_model(),
              "voice_settings": ({"stability": ELEVEN_V4_STABILITY, "similarity_boost": ELEVEN_V4_SIMILARITY}
                                 if eleven_expressive() else ELEVENLABS_SETTINGS)},
        timeout=60,
    )
    if not r.ok:
        raise RuntimeError(f"{r.status_code} {r.text[:200]}")
    with open(path, "wb") as f:
        f.write(r.content)
    print(f"ElevenLabs voice OK ({len(text)} characters used)")


def make_clip(last_song, style="talkover", next_song=None, ctx=None, duo=False):
    if duo:   # Cara and Scratch together (rolled when the break was planned, so it lands between songs)
        try:
            path = brain.maybe_duo(style, ctx or {}, force=True)
            if path:
                return path
        except Exception as e:
            print(f"{brain.CO_NAME} couldn't join this one ({e}), so Cara takes it solo.")
    text = write_break(last_song, style, next_song, ctx)
    print(f"[DJ:{style}] {text}")
    return synth_clip(text)


_engine_used = None
_seen_msgs = set()


def first_time(key):
    """True the first time it's called with a given key (keeps the log from repeating itself)."""
    if key in _seen_msgs:
        return False
    _seen_msgs.add(key)
    return True


def kokoro_available():
    base = os.path.dirname(os.path.abspath(__file__))
    return (os.path.exists(os.path.join(base, "kokoro-v1.0.onnx"))
            and os.path.exists(os.path.join(base, "voices-v1.0.bin")))


def synth_clip(text):
    """Turn any text into an audio file using the chosen voice (with fallbacks)."""
    global _engine_used
    stamp = f"{int(time.time())}{random.randint(0, 999)}"

    if TTS_ENGINE == "elevenlabs":
        path = os.path.join(tempfile.gettempdir(), f"dj_{stamp}e.mp3")
        try:
            elevenlabs_tts(text, path)
            _engine_used = "elevenlabs"
            return path
        except Exception as e:
            if first_time("eleven:" + str(e)[:60]):
                print("ElevenLabs failed, using another voice for now:", e)
            else:
                print("(ElevenLabs failed again, using another voice.)")

    if TTS_ENGINE == "gemini":
        path = os.path.join(tempfile.gettempdir(), f"dj_{stamp}.wav")
        try:
            gemini_tts(text, path)
            _engine_used = "gemini"
            return path
        except Exception as e:
            print("Gemini voice failed, trying Kokoro instead:", e)

    if TTS_ENGINE in ("elevenlabs", "gemini", "kokoro") and kokoro_available():
        path = os.path.join(tempfile.gettempdir(), f"dj_{stamp}k.wav")
        try:
            kokoro_tts(text, path)
            _engine_used = "kokoro"
            return path
        except Exception as e:
            print("Kokoro failed, using edge-tts instead:", e)

    path = os.path.join(tempfile.gettempdir(), f"dj_{stamp}.mp3")
    asyncio.run(_tts(text, path))
    _engine_used = "edge"
    return path


def get_breaking_story():
    """A current top headline from a random news category: (category, headline, serious)."""
    cats = list(BREAKING_FEEDS)
    random.shuffle(cats)
    for cat in cats:
        try:
            feed = feedparser.parse(BREAKING_FEEDS[cat])
            titles = [clean_title(e.title) for e in feed.entries[:8]]
            titles = [t for t in titles if not DEATH_REGEX.search(t)][:4]
        except Exception:
            continue
        if titles:
            headline = random.choice(titles)
            return cat, headline, (not is_safe(headline))  # serious = violence, tragedy, politics...
    return None


def write_breaking(cat, headline, serious):
    """The words for a breaking-news interruption."""
    if MODE == "gemini" and os.environ.get("GEMINI_API_KEY"):
        tone = (
            "This story is serious. Report it calmly and respectfully in plain words: no jokes, no "
            "puns, no teasing, no exclamation-mark hype. Then hand back to the music gently."
            if serious else
            "Keep a little of your on-air energy, but deliver the facts accurately."
        )
        prompt = f"""You are {DJ_NAME}, {DJ_STYLE}, on the radio station {brain.STATION.full()} in {CITY} (only ever call it that).
You are interrupting the song in the middle for a BREAKING NEWS flash ({cat} news).
Write 20-40 words to be spoken aloud: a quick "we interrupt the music" style opener in your own
words, then the news, then a short hand-off back to the music.
The news: {headline}
Rules:
- {tone}
- Use only the facts in the headline. Never add details, names, numbers or guesses.
- Stay neutral: no opinions about politicians, parties, governments or countries.
- Clean language, no emojis, no stage directions, no asterisks, never whisper or say "shh".
- Never say the words "slogan" or "tagline", and never mention anyone dying.
- Write for the ear: contractions, natural pauses, numbers spelled out the way people say them."""
        key = os.environ["GEMINI_API_KEY"]
        for model in GEMINI_MODELS:
            try:
                r = requests.post(
                    f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent",
                    headers={"x-goog-api-key": key, "Content-Type": "application/json"},
                    json={"contents": [{"parts": [{"text": prompt}]}]},
                    timeout=30,
                )
                r.raise_for_status()
                return tidy(r.json()["candidates"][0]["content"]["parts"][0]["text"].strip())
            except Exception as e:
                print(f"Gemini model {model} failed: {e}")
    if serious:
        return f"We interrupt the music for some news. {headline}. Back to the music now."
    return f"Breaking news, {CITY}! {headline}! Okay, back to the music!"


_sting_path = None


def make_sting():
    """A short two-tone 'news alert' chime, made on the fly (no audio files needed)."""
    global _sting_path
    if _sting_path and os.path.exists(_sting_path):
        return _sting_path
    try:
        import numpy as np
        import wave

        sr = 44100
        parts = []
        for freq in (880.0, 1174.7, 880.0, 1174.7):
            t = np.arange(int(sr * 0.13)) / sr
            env = np.minimum(1.0, t / 0.01) * np.exp(-t * 9)
            parts.append(0.45 * np.sin(2 * np.pi * freq * t) * env)
            parts.append(np.zeros(int(sr * 0.03)))
        samples = np.concatenate(parts)
        path = os.path.join(tempfile.gettempdir(), "dj_news_sting.wav")
        with wave.open(path, "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(sr)
            w.writeframes((samples * 32767).astype("<i2").tobytes())
        _sting_path = path
        return path
    except Exception:
        return None


def play_sting(path):
    if not path:
        return
    try:
        snd = pygame.mixer.Sound(path)
        snd.set_volume(max(0.0, min(1.0, DJ_VOLUME)))
        ch = snd.play()
        while ch is not None and ch.get_busy():
            time.sleep(0.05)
    except Exception:
        pass


def run_breaking(path, sting, vol, can_duck):
    """Cut into the middle of the song: dip the music, chime, read the news, bring it back."""
    global RESTORE_VOL
    if can_duck:
        RESTORE_VOL = vol
        low = vol * duck_frac(0.08)
        try:
            fade_volume(vol, low, 0.3, steps=3)
            play_sting(sting)
            play_clip(path)
        finally:
            fade_volume(low, vol, 1.5)
            RESTORE_VOL = None
    else:  # can't control volume: pause the song, then pick it up where it left off
        sp.pause_playback()
        try:
            play_sting(sting)
            play_clip(path)
        finally:
            time.sleep(0.2)
            resume_spotify()


def resume_spotify(device_id=None):
    """Get the music going again, retrying: Spotify is often busy for a second right after a skip."""
    for attempt in range(30):          # about half a minute of trying
        cur = None
        try:
            cur = sp.current_playback()
            if cur and cur.get("is_playing"):
                return True
            dev = device_id or ((cur or {}).get("device") or {}).get("id")
            if attempt >= 3 and attempt % 4 == 3:  # every few tries: wake / move playback to a device
                devs = (sp.devices() or {}).get("devices", [])
                pick = next((d for d in devs if d.get("is_active")), None) or next((d for d in devs if not d.get("is_restricted")), None)
                if pick:
                    sp.transfer_playback(pick["id"], force_play=True)
                    time.sleep(1.0)
                    continue
            if dev:
                sp.start_playback(device_id=dev)
            else:
                sp.start_playback()
        except Exception as e:
            print("Resume retry:", e)
        time.sleep(0.8)
    print("Spotify did not restart by itself. Press play.")
    return False


def stinger_dir():
    d = os.environ.get("DJ_STINGER_DIR") or os.path.join(tempfile.gettempdir(), "nsp_stingers")
    os.makedirs(d, exist_ok=True)
    return d


def _tag_key(text):
    voice = f"{TTS_ENGINE}|{os.environ.get('ELEVENLABS_VOICE_ID', '')}|{KOKORO_VOICE}|{VOICE}"
    return hashlib.sha1(f"{voice}|{text}".encode("utf-8")).hexdigest()[:16]


def tag_texts():
    city = CITY.split(",")[0].strip()
    return [t.format(city=city) for t in STINGER_TAGS]


def make_stinger(text, allow_fallback=False):
    """Voice one station tag once and keep the file. Returns its path, or None."""
    key = _tag_key(text)
    found = glob.glob(os.path.join(stinger_dir(), key + ".*"))
    if found:
        return found[0]
    tmp = synth_clip(text)
    if _engine_used != TTS_ENGINE:            # a fallback voice made it: never keep it as a station tag
        if allow_fallback:
            return tmp
        try:
            os.remove(tmp)
        except OSError:
            pass
        return None
    dest = os.path.join(stinger_dir(), key + os.path.splitext(tmp)[1])
    try:
        shutil.move(tmp, dest)
        return dest
    except OSError:
        return tmp


_last_tag = None


def stinger_files():
    """Your own stinger recordings (mp3/wav/ogg) from the my_stingers folder."""
    dirs = []
    if os.environ.get("DJ_MY_STINGERS"):
        dirs.append(os.environ["DJ_MY_STINGERS"])
    dirs.append(os.path.join(os.path.dirname(os.path.abspath(__file__)), "my_stingers"))
    found = {}
    for d in dirs:
        if os.path.isdir(d):
            for name in sorted(os.listdir(d)):
                if name.lower().endswith((".mp3", ".wav", ".ogg")) and name not in found:
                    found[name] = os.path.join(d, name)
    return list(found.values())


def pick_cached_stinger():
    """One of your stingers (never the same one twice in a row), or None if there are none."""
    global _last_tag
    have = stinger_files()
    choices = [p for p in have if p != _last_tag] or have
    if not choices:
        return None
    _last_tag = random.choice(choices)
    return _last_tag


def station_maker():
    """The station-stinger maker, when station stingers are on (None when they're off, there's no ElevenLabs key for
    the station voice, or they can't run here): your original stingers play instead."""
    if not (STATION_STINGERS and pc_stingers is not None and os.environ.get("ELEVENLABS_API_KEY", "").strip()):
        return None
    return pc_stingers.MAKER


def pick_stinger():
    """The stinger before a silent break: one made for this station, or one of yours on plain Non Stop Pop."""
    name, maker = brain.STATION.name(), station_maker()
    if maker and name != brain.FALLBACK and not maker.failing_lately():
        got = maker.ready(name)
        if got:
            return got
        print(f"[no {brain.STATION.full()} stinger made yet, so none this time]")
        warm_stingers()
        return None
    return pick_cached_stinger()


def warm_stingers():
    """Gets one more stinger made for the station that's playing, in the background (six per station, one at a time)."""
    name, maker = brain.STATION.name(), station_maker()
    if maker and STINGERS_ENABLED and SILENT_STINGER_PERCENT > 0 and name != brain.FALLBACK:
        maker.warm(name)


def test_stinger():
    """Test button: this station's stinger (or one of yours on plain Non Stop Pop). None if there's nothing to play
    yet: when one is being made, the button presses itself again the moment it's ready."""
    name, maker = brain.STATION.name(), station_maker()
    if maker and name != brain.FALLBACK:
        got = maker.ready(name)
        if got:
            return got
        if not maker.failing_lately():
            print(f"[making a {brain.STATION.full()} stinger; it plays as soon as it's ready]")
            maker.warm(name, then=lambda path: path and FORCE_STINGER.set())
            return None
    got = pick_cached_stinger()
    if not got:
        print("No stingers found. Put mp3 files in the my_stingers folder.")
    return got


def stinger_test_note():
    """For the screen: the station a test stinger is about to be made for ("" when one plays right away)."""
    name, maker = brain.STATION.name(), station_maker()
    if maker and name != brain.FALLBACK and not maker.failing_lately() and maker.count(name) == 0:
        return brain.STATION.full()
    return ""


def play_stinger_file(path):
    STATUS["speaking"] = True
    try:
        pygame.mixer.music.load(path)
        pygame.mixer.music.set_volume(max(0.0, min(1.0, STINGER_VOLUME)))
        pygame.mixer.music.play()
        while pygame.mixer.music.get_busy():
            pygame.mixer.music.set_volume(max(0.0, min(1.0, STINGER_VOLUME)))   # follows the slider live
            time.sleep(0.05)
        pygame.mixer.music.unload()
    finally:
        STATUS["speaking"] = False


def saved_tag_count():
    return sum(1 for t in tag_texts() if glob.glob(os.path.join(stinger_dir(), _tag_key(t) + ".*")))


def prebake_stingers():
    """Voice all the station tags in the background (only the ones not saved yet).
    If the voice isn't working yet, quietly tries again every minute."""
    for attempt in range(30):
        made, failed = 0, False
        for text in tag_texts():
            if STOP.is_set() or not STINGERS_ENABLED:
                return
            if not glob.glob(os.path.join(stinger_dir(), _tag_key(text) + ".*")):
                try:
                    if make_stinger(text):
                        made += 1
                    else:
                        failed = True
                        break
                except Exception as e:
                    print("Couldn't make a station tag:", e)
                    failed = True
                    break
        if made:
            print(f"Station tags ready ({made} new, {saved_tag_count()} saved).")
        if not failed:
            return
        if first_time("tagwait"):
            print(f"Station tags need your {TTS_ENGINE} voice to be working (none are saved yet, "
                  "so you won't hear any). Fix the key in Settings and they'll be made automatically.")
        for _ in range(60):
            if STOP.is_set():
                return
            time.sleep(1)


_zap_path = None


def make_zap():
    """A quick rising 'whoosh', made on the fly, to punch the start of a station tag."""
    global _zap_path
    if _zap_path and os.path.exists(_zap_path):
        return _zap_path
    try:
        import numpy as np
        import wave

        sr, dur = 44100, 0.28
        t = np.arange(int(sr * dur)) / sr
        freq = 300 + 1800 * (t / dur) ** 2
        phase = 2 * np.pi * np.cumsum(freq) / sr
        env = np.minimum(1.0, t / 0.02) * np.exp(-((t - 0.16) ** 2) / 0.006)
        samples = 0.4 * np.sin(phase) * env
        path = os.path.join(tempfile.gettempdir(), "dj_zap.wav")
        with wave.open(path, "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(sr)
            w.writeframes((samples * 32767).astype("<i2").tobytes())
        _zap_path = path
        return path
    except Exception:
        return None


def play_voice(path, p=None):
    """Optional stinger of yours first, then the DJ line: one after the other, never overlapping."""
    st = (p or {}).get("intro_sting")
    if st and os.path.exists(st):
        try:
            play_stinger_file(st)
        except Exception as e:
            print("Couldn't play the stinger:", e)
        time.sleep(0.15)
    play_clip(path)


def run_stinger(path, vol):
    """Test only: one of your stingers over the current song, with the music dipped while it plays."""
    global RESTORE_VOL
    RESTORE_VOL = vol
    low = vol * duck_frac(random.uniform(0.25, 0.4))
    try:
        fade_volume(vol, low, 0.6)
        play_stinger_file(path)
    finally:
        fade_volume(low, vol, 0.8)
        RESTORE_VOL = None


def play_clip(path):
    STATUS["speaking"] = True
    try:
        pygame.mixer.music.load(path)
        pygame.mixer.music.set_volume(max(0.0, min(1.0, DJ_VOLUME)))
        pygame.mixer.music.play()
        while pygame.mixer.music.get_busy():
            pygame.mixer.music.set_volume(max(0.0, min(1.0, DJ_VOLUME)))   # follows the slider live
            time.sleep(0.1)
        pygame.mixer.music.unload()  # release the file so Windows lets us delete it
    finally:
        STATUS["speaking"] = False


def set_volume(v):
    try:
        sp.volume(int(max(0, min(100, v))))
    except Exception as e:
        print("Could not change Spotify volume:", e)


def fade_volume(start, end, seconds, steps=None):
    """Smooth, time-based fade: follows the clock instead of a few big jumps, so slow
    Spotify volume calls don't make it choppy."""
    if abs(end - start) < 1 or seconds <= 0:
        set_volume(end)
        return
    seconds = max(seconds, 0.6)           # too-short fades sound like a cut
    t0 = time.time()
    last = None
    while True:
        f = min(1.0, (time.time() - t0) / seconds)
        f = f * f * (3 - 2 * f)           # ease in/out
        v = int(round(start + (end - start) * f))
        if v != last:
            set_volume(v)
            last = v
        if f >= 1.0:
            break
        time.sleep(0.05)
    if last != int(round(end)):
        set_volume(end)


def duck_frac(default):
    """How far to lower the song: your slider if set, otherwise the built-in random amount."""
    if DUCK_PERCENT is not None:
        return max(0.0, min(1.0, DUCK_PERCENT / 100.0))
    return default


def new_params():
    """Timing details for one break. Randomized so no two breaks feel exactly the same."""
    if not RANDOMIZE_TIMING:
        return {"duck": DUCK_LEVEL, "talk_max": TALK_OVER_MAX_MS, "overlap": OVERLAP_INTO_NEXT_MS,
                "fade_down": FADE_DOWN_MS, "fade_up": 1200}
    return {
        "duck": random.uniform(0.12, 0.30),               # how quiet the song gets
        "talk_max": TALK_OVER_MAX_MS,                     # how early a talkover may start (early enough to finish with the song)
        "overlap": random.randint(400, 1500),             # how far into the next song she talks (a beat at most)
        "fade_down": random.randint(1200, 2600),          # fadeout: how slowly the song sinks
        "fade_up": random.randint(900, 1800),             # how slowly the song comes back
    }


def play_over_music(path, normal_vol, p=None):
    """Lower the song, play the DJ over it, then bring the song back up."""
    p = p or new_params()
    duck = normal_vol * duck_frac(p["duck"])
    try:
        fade_volume(normal_vol, duck, 0.6)
        play_voice(path, p)
    finally:
        fade_volume(duck, normal_vol, p["fade_up"] / 1000)


def clip_length_ms(path):
    try:
        return int(pygame.mixer.Sound(path).get_length() * 1000)
    except Exception:
        return 10000


QUEUE_FILTER = None  # the app sets this: takes out the songs you removed from the queue (they're skipped)


def get_next_track():
    """Details of the next song in Spotify's queue, or None (also None for segments/ads)."""
    try:
        q = sp.queue() or {}
        items = q.get("queue") or []
        if QUEUE_FILTER:
            items = QUEUE_FILTER(items)
        nxt = (items or [None])[0]
        return track_info(nxt)
    except Exception:
        return None


def pick_style(can_duck, last_style=None):
    if not can_duck:
        return "silent"  # can't control volume, so pausing is the only option
    names = [n for n, w in TRANSITIONS.items() if w > 0]
    if last_style in names and len(names) > 1:
        names.remove(last_style)  # never the same transition twice in a row
    return random.choices(names, weights=[TRANSITIONS[n] for n in names])[0]


def start_ms_for(style, len_ms, p):
    """How many ms before the song ends she should start."""
    if style == "talkover":
        return int(min(p["talk_max"], max(4000, len_ms - p["overlap"])))
    if style == "fadeout":
        return int(len_ms + p["fade_down"] + 500)
    if style == "silent":
        return SILENT_PAUSE_MS
    return 150  # intro: right at the song boundary


RESTORE_VOL = None  # remembers the normal volume while she has the song turned down


def run_transition(style, path, vol, uri, p=None, late=False):
    global RESTORE_VOL
    RESTORE_VOL = vol if (style != "silent" and not late) else None
    try:
        _run_transition(style, path, vol, uri, p or new_params(), late)
    finally:
        RESTORE_VOL = None


def _run_transition(style, path, vol, uri, p, late=False):
    if late:
        # she missed the end of the last song: pause this one, talk, then play it from the top so its start isn't buried
        sp.pause_playback()
        try:
            play_voice(path, p)
        finally:
            time.sleep(0.2)
            try:
                sp.seek_track(0)
            except Exception as e:
                print("Could not restart the song:", e)
            time.sleep(0.3)
            resume_spotify()
        return
    if style == "silent":
        sp.pause_playback()
        try:
            play_voice(path, p)
        finally:
            time.sleep(0.2)
            try:
                cur = sp.current_playback()
                same = bool(cur and cur.get("item") and cur["item"].get("uri") == uri)
                if same:  # the old song is still 'current': jump to the next one
                    sp.next_track()
                    time.sleep(0.3)
            except Exception as e:
                print("Could not skip to next song:", e)
            time.sleep(0.4)
            resume_spotify()
    elif style == "fadeout":
        low = vol * 0.05
        try:
            fade_volume(vol, low, p["fade_down"] / 1000, steps=8)
            play_voice(path, p)
        finally:
            fade_volume(low, vol, p["fade_up"] / 1000)
    elif style == "intro":
        duck = vol * duck_frac(p["duck"])
        try:
            fade_volume(vol, duck, 0.2, steps=2)
            play_voice(path, p)
        finally:
            fade_volume(duck, vol, p["fade_up"] / 1000 + 0.3)
    else:  # talkover
        play_over_music(path, vol, p)


def main():
    global FORCE_TRANSITION
    if TTS_ENGINE == "elevenlabs":
        ek = os.environ.get("ELEVENLABS_API_KEY", "")
        if not ek or not os.environ.get("ELEVENLABS_VOICE_ID"):
            print("ElevenLabs isn't set up yet: open Settings and paste your API key and Voice ID.")
        elif not ek.startswith("sk_"):
            print("That ElevenLabs API key doesn't look right: real keys start with sk_ and are only "
                  "shown once, when you create the key. Make a new key in ElevenLabs and paste that.")
    if TTS_ENGINE in ("kokoro", "gemini", "elevenlabs") and kokoro_available():
        try:
            print("Loading Kokoro voice model (takes a few seconds)...")
            load_kokoro()
            print("Kokoro ready.")
        except Exception as e:
            print("Kokoro could not load, will use edge-tts instead:", e)

    if TTS_ENGINE == "gemini" and os.environ.get("GEMINI_API_KEY"):
        get_tts_models()
    print(f"Local news feeds: {len(get_headlines())} headlines found right now.")
    print(f"World news feeds: {len(get_world_headlines())} wild stories found right now.")
    print(f"Gossip feeds: {len(get_gossip_headlines())} pop culture stories found right now.")
    print(f"Music feeds: {len(get_music_headlines())} music stories found right now.")

    state = {"playing": False, "uri": None, "name": "", "track": None, "duration": 0,
             "progress": 0, "stamp": 0.0, "vol": None}
    prepared = {"path": None, "style": None, "params": None, "len_ms": 0, "building": False}
    last_uri = None
    songs_since_break = 0
    last_poll = 0.0
    last_style = None
    breaking = {"path": None, "sting": None, "uri": None, "at": 0, "building": False, "forced": False}
    songs_since_breaking = 999
    songs_since_voice = 0                                  # songs since ANY spoken thing (break, news, tag)
    stinger = {"uri": None, "path": None, "at": 0}
    popin = {"armed": False, "uri": None, "at": 0, "path": None, "building": False, "forced": False}
    if STINGERS_ENABLED:
        print(f"Stingers found: {len(stinger_files())} (played in silent transitions, before Cara speaks)."
              + (" Station stingers: on a named station, they're re-voiced with its name." if station_maker() else ""))

    def build_breaking():
        breaking["building"] = True
        try:
            story = get_breaking_story()
            if not story:
                print("Breaking news: couldn't find a story right now.")
                breaking["uri"] = None
                return
            cat, headline, serious = story
            text = write_breaking(cat, headline, serious)
            print(f"[BREAKING:{cat}] {text}")
            breaking["sting"] = make_sting()
            breaking["path"] = synth_clip(text)
        except Exception as e:
            print("Could not build breaking news:", e)
            breaking["uri"] = None
        finally:
            breaking["building"] = False

    def build_popin(info):
        popin["building"] = True
        try:
            text = write_popin(info)
            print(f"[POP-IN] {text}")
            popin["path"] = synth_clip(text)
            print("[pop-in ready, waiting for its moment]")
        except Exception as e:
            print("Could not build pop-in:", e)
            popin["uri"] = None
        finally:
            popin["building"] = False

    def build_duo_now(info):
        popin["building"] = True
        try:
            path = brain.maybe_duo("intro", {"last": None, "next": info}, force=True)
            if path:
                popin["path"] = path
                print(f"[Cara and {brain.CO_SHORT} ready]")
            else:
                popin.update(uri=None, forced=False)
        except Exception as e:
            print(f"Could not make Cara and {brain.CO_SHORT}:", e)
            popin.update(uri=None, forced=False)
        finally:
            popin["building"] = False

    def drop_popin():
        if popin["path"]:
            try:
                os.remove(popin["path"])
            except OSError:
                pass
        popin.update(path=None, uri=None, armed=False, forced=False)

    def plan_popin(uri, info, duration):
        popin.update(armed=False, uri=uri, at=POPIN_AFTER_SEC * 1000 + random.randint(-min(5000, POPIN_AFTER_SEC * 500), min(5000, POPIN_AFTER_SEC * 500)))
        if duration < popin["at"] + 40000:
            popin["uri"] = None
            print("[pop-in skipped: this song is too short for one]")
            return
        print(f"[pop-in planned about {int(popin['at'] / 1000)}s into this song]")
        threading.Thread(target=build_popin, args=(info,), daemon=True).start()

    def drop_breaking():
        if breaking["path"]:
            try:
                os.remove(breaking["path"])
            except OSError:
                pass
        breaking.update(path=None, uri=None, forced=False)

    def roll_interval():
        lo = max(1, int(BREAK_EVERY_MIN))
        hi = max(lo, int(BREAK_EVERY_MAX))
        n = random.randint(lo, hi)
        print(f"Next DJ break after {n} song{'s' if n != 1 else ''}.")
        return n

    next_break_after = roll_interval()
    STATUS["songs_left"] = max(0, next_break_after - songs_since_break)

    def build(song_name, last_info, style, duo=False):
        prepared["building"] = True
        try:
            nxt_info = get_next_track() if ANNOUNCE_NEXT else None
            ctx = {"last": last_info, "next": nxt_info}
            path = make_clip(song_name, style, describe(nxt_info), ctx, duo=duo)
            prepared["len_ms"] = clip_length_ms(path)
            prepared["style"] = style
            prepared["path"] = path
        except Exception as e:
            print("Could not build DJ break:", e)
        finally:
            prepared["building"] = False

    while not STOP.is_set():
        now = time.time()

        # How long until the song ends, estimated from the last Spotify check
        remaining = None
        if state["playing"]:
            remaining = state["duration"] - (state["progress"] + (now - state["stamp"]) * 1000)

        # Ask Spotify where we are: often near the end of a song (precision), rarely otherwise
        interval = 0.8 if (remaining is not None and remaining < 15000) else 2.0
        if now - last_poll >= interval:
            last_poll = now
            try:
                t0 = time.time()
                pb = sp.current_playback()
                t1 = time.time()
            except Exception as e:
                print("Spotify error:", e)
                time.sleep(3)
                continue
            if pb and pb.get("item"):
                try:
                    brain.STATION.update(pb.get("context"))   # the station takes the name of what's playing
                except Exception:
                    pass
            item = pb.get("item") if pb else None
            if pb and pb.get("is_playing") and item and item.get("duration_ms"):
                state.update(
                    playing=True,
                    uri=item.get("uri"),
                    name=item.get("name", ""),
                    track=track_info(item),
                    duration=item["duration_ms"],
                    progress=pb["progress_ms"],
                    stamp=(t0 + t1) / 2,
                    vol=(pb.get("device") or {}).get("volume_percent"),
                )
                if state["uri"] != last_uri:
                    last_uri = state["uri"]
                    songs_since_break += 1
                    STATUS["songs_left"] = max(0, next_break_after - songs_since_break)
                    songs_since_breaking += 1
                    songs_since_voice += 1
                    stinger.update(uri=None, path=None)
                    warm_stingers()       # the next station stinger gets made while this song plays
                    if popin["path"] and popin["uri"] != state["uri"] and not popin["forced"]:
                        drop_popin()
                    if popin["armed"] and POPIN_ENABLED and not popin["building"] and not popin["path"]:
                        plan_popin(state["uri"], state["track"], state["duration"])
                    elif popin["armed"]:
                        popin["armed"] = False
                        print("[pop-in skipped: " + ("turned off" if not POPIN_ENABLED else "still busy with the last one") + "]")
                    if breaking["path"] and breaking["uri"] != state["uri"] and not breaking["forced"]:
                        drop_breaking()  # the song it was planned for is over
                    if (BREAKING_ENABLED and not breaking["path"] and not breaking["building"]
                            and state["duration"] >= 90000
                            and songs_since_break < next_break_after):  # this song won't end with a regular break
                        gap_ok = BREAKING_TEST_MODE or songs_since_breaking >= BREAKING_MIN_GAP_SONGS
                        chance = 1.0 if BREAKING_TEST_MODE else BREAKING_CHANCE
                        if gap_ok and random.random() < chance:
                            breaking["uri"] = state["uri"]
                            breaking["at"] = state["duration"] * random.uniform(0.30, 0.55)
                            threading.Thread(target=build_breaking, daemon=True).start()
                    # a standalone station tag over the opening of this song, only on quiet stretches
                    v = state["vol"]
                    if (STINGER_STANDALONE and STINGERS_ENABLED and v is not None and v > 0 and state["duration"] >= 60000
                            and songs_since_voice >= 2 and songs_since_break < next_break_after
                            and breaking["uri"] != state["uri"] and not breaking["building"]):
                        if STINGER_TEST_MODE or random.random() < STINGER_CHANCE:
                            tag = pick_stinger()
                            if tag:
                                stinger.update(uri=state["uri"], path=tag, at=random.randint(2000, 7000))
            else:
                state["playing"] = False
            now = time.time()
            remaining = (
                state["duration"] - (state["progress"] + (now - state["stamp"]) * 1000)
                if state["playing"] else None
            )

        if not state["playing"]:
            time.sleep(0.5)
            continue

        forced = FORCE_TRANSITION if FORCE_TRANSITION in ("talkover", "intro", "silent", "fadeout") else None
        due = songs_since_break >= next_break_after or bool(forced)
        vol = state["vol"]
        can_duck = vol is not None and vol > 0
        if forced and prepared["path"] and not prepared["building"]:
            want = forced if can_duck else "silent"
            if prepared["style"] != want:          # a different style was already written: redo it
                try:
                    os.remove(prepared["path"])
                except OSError:
                    pass
                prepared["path"] = None

        # Test button: fire a breaking-news interruption right now
        if FORCE_BREAKING.is_set():
            FORCE_BREAKING.clear()
            if not breaking["path"] and not breaking["building"]:
                print("Testing breaking news...")
                breaking.update(uri=state["uri"], at=0, forced=True)
                threading.Thread(target=build_breaking, daemon=True).start()

        # Test button: pop in on the current song right now
        if FORCE_POPIN.is_set():
            FORCE_POPIN.clear()
            if not can_duck:
                print("Pop-ins duck the music, and Spotify isn't letting the app change its volume right now. Check that the Spotify app is the active device.")
            elif not popin["path"] and not popin["building"]:
                print("Testing pop-in...")
                popin.update(uri=state["uri"], at=0, forced=True)
                threading.Thread(target=build_popin, args=(state["track"],), daemon=True).start()

        # Test button: Cara and her co-host, right now, over this song
        if FORCE_DUO.is_set():
            FORCE_DUO.clear()
            if not can_duck:
                print(f"Cara and {brain.CO_SHORT} duck the music, and Spotify isn't letting the app change its volume right now. Check that the Spotify app is the active device.")
            elif not popin["path"] and not popin["building"]:
                print(f"Testing Cara and {brain.CO_SHORT}...")
                popin.update(uri=state["uri"], at=0, forced=True)
                threading.Thread(target=build_duo_now, args=(state["track"],), daemon=True).start()
            else:
                print("A pop-in is on its way first. Try again in a moment.")

        # Pop-in: a quick second drop-in shortly after the song starts after a talk-over / intro
        if popin["path"]:
            same = state["uri"] == popin["uri"]
            progress = state["duration"] - remaining
            if popin["forced"] or (same and can_duck and progress >= popin["at"] and remaining > 25000
                                   and not breaking["path"] and not breaking["building"]):
                path = popin["path"]
                popin.update(path=None, uri=None, forced=False)
                songs_since_voice = 0
                print("[pop-in]")
                try:
                    run_breaking(path, None, vol, can_duck)
                finally:
                    try:
                        os.remove(path)
                    except OSError:
                        pass
                last_poll = 0.0
                continue
            if not same:
                drop_popin()

        # Test button: fire a station tag right now
        if FORCE_STINGER.is_set():
            FORCE_STINGER.clear()
            if not can_duck:
                print("Stingers need volume control on your Spotify device.")
            else:
                tag = test_stinger()
                if tag:
                    print("[stinger: test]")
                    songs_since_voice = 0
                    run_stinger(tag, vol)
                    last_poll = 0.0
                    continue

        # A planned station tag: play it a few seconds into the song, never near her voice
        if stinger["path"]:
            if state["uri"] != stinger["uri"]:
                stinger.update(uri=None, path=None)
            elif (state["duration"] - remaining) >= stinger["at"] and remaining > 25000 and not due and can_duck:
                tag = stinger["path"]
                stinger.update(uri=None, path=None)
                songs_since_voice = 0
                print("[stinger]")
                run_stinger(tag, vol)
                last_poll = 0.0
                continue

        # Breaking news: once its clip is ready and we're at the chosen point of the song
        if breaking["path"]:
            progress = state["duration"] - remaining
            same_song = state["uri"] == breaking["uri"]
            in_window = remaining > 20000 and progress >= breaking["at"]
            if breaking["forced"] or (same_song and in_window and not due):
                path, sting = breaking["path"], breaking["sting"]
                breaking.update(path=None, uri=None, forced=False)
                songs_since_breaking = 0
                songs_since_voice = 0
                print("[transition: BREAKING NEWS]")
                try:
                    run_breaking(path, sting, vol, can_duck)
                finally:
                    try:
                        os.remove(path)
                    except OSError:
                        pass
                last_poll = 0.0
                continue
            if not same_song:
                drop_breaking()

        # Start writing the break ~45s before the song ends
        if due and (remaining < 90000 or forced) and not prepared["path"] and not prepared["building"]:
            # Scratch can join any kind of break, queued or not. Their talk-overs finish as the song ends and their
            # intros are a quick two-liner, so a song that starts straight away isn't buried under their chat.
            duo = brain.wants_duo()
            style = (forced if can_duck else "silent") if forced else pick_style(can_duck, last_style)
            prepared["params"] = new_params()
            prepared["uri"] = state["uri"]
            threading.Thread(target=build, args=(state["name"], state["track"], style, duo), daemon=True).start()

        # When it's time, run the chosen transition
        if due and prepared["path"]:
            p = prepared["params"] or new_params()
            style = prepared["style"] if can_duck else "silent"
            start_ms = start_ms_for(style, prepared["len_ms"], p)
            if style == "fadeout" and remaining < start_ms - 1500:
                style = "talkover"  # too late for a proper fade, just talk over it
                start_ms = start_ms_for(style, prepared["len_ms"], p)
            # the clip finished after its song ended: this song waits for her, then starts again from the top
            late = bool(prepared.get("uri")) and prepared["uri"] != state["uri"] and style in ("talkover", "fadeout")
            if late:
                style = "talkover"
                fire = (state["duration"] - remaining) >= 1200
            else:
                fire = remaining <= start_ms
            if fire:
                path = prepared["path"]
                prepared["path"] = None
                songs_since_break = 0
                STATUS["songs_left"] = max(0, next_break_after - songs_since_break)
                songs_since_voice = 0
                p["tag"] = None
                p["intro_sting"] = None
                if STINGERS_ENABLED and style == "silent" and random.random() * 100 < SILENT_STINGER_PERCENT:
                    p["intro_sting"] = pick_stinger()
                    if p["intro_sting"]:
                        print("[stinger before Cara]")
                if FORCE_TRANSITION:
                    FORCE_TRANSITION = None
                last_style = style
                next_break_after = roll_interval()
                STATUS["songs_left"] = max(0, next_break_after - songs_since_break)
                if POPIN_ENABLED and style != "silent" and (POPIN_TEST_MODE or random.random() < POPIN_CHANCE):
                    if late:   # already inside the new song
                        plan_popin(state["uri"], state["track"], state["duration"])
                    else:
                        popin["armed"] = True
                        print("[pop-in lined up for the next song]")
                elif POPIN_ENABLED and style != "silent":
                    print(f"[no pop-in after this one ({int(POPIN_CHANCE * 100)}% chance each time)]")
                print("[transition: late, so this song waits for her and starts again from the top]" if late else f"[transition: {style}]")
                try:
                    run_transition(style, path, vol, state["uri"], p, late=late)
                finally:
                    try:
                        os.remove(path)
                    except OSError:
                        pass  # temp file cleanup isn't important
                last_poll = 0.0  # re-check Spotify right away

        time.sleep(0.1)
    print("DJ stopped.")


brain.attach(sys.modules[__name__])


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        if RESTORE_VOL:  # stopped while the song was turned down: put the volume back
            set_volume(RESTORE_VOL)
        print("\nStopped.")

"""
Non Stop Pop DJ: Cara's radio on your PC.

A native window (pywebview, drawn by Microsoft Edge WebView2) shows the same app as the iPhone version, laid out
for a big screen: Home, Cara, Library, Search, album / artist / playlist pages, and the player with lyrics and the
queue. Python runs Cara's engine (live_dj_free.py and brain.py) and talks to Spotify (pc_spotify.py).
Your keys are typed once in Settings and saved on this PC.
"""
import collections
import getpass
import importlib
import json
import os
import random
import re
import socket
import sys
import threading
import time
import webbrowser

os.environ.setdefault("PYGAME_HIDE_SUPPORT_PROMPT", "1")

import pc_audio  # noqa: E402  (listens to the speakers only while the visualizer is open)
import pc_stingers  # noqa: E402  (station stingers: your stingers, re-voiced with the name of what's playing)
import pc_player  # noqa: E402  (the built-in player: music plays from the app, no Spotify app needed)
import pc_spotify  # noqa: E402  (plain module: nothing in it talks to Spotify until the app connects)

APP_NAME = "Non Stop Pop DJ"
VERSION = "3.0"
APP_DIR = os.path.join(os.environ.get("APPDATA") or os.path.expanduser("~"), "NonStopPopDJ")
os.makedirs(APP_DIR, exist_ok=True)
CONFIG_PATH = os.path.join(APP_DIR, "config.json")
CACHE_PATH = os.path.join(APP_DIR, "spotify_token.cache")
REDIRECT_URI = "http://127.0.0.1:8888/callback"
PRESETS_DIR = os.path.join(APP_DIR, "presets")             # your MilkDrop presets (.milk), any folders inside
PRESET_CACHE = os.path.join(APP_DIR, "presets_converted")  # each one converted once, then kept here
PRESETS_README = (
    "Drop MilkDrop presets in this folder: .milk files, or whole folders of them (Cream of the Crop,\n"
    "projectM packs and so on). They join the visualizer's rotation the next time you open it.\n\n"
    "In the visualizer, press M (or click the Mix button) to play only MilkDrop presets.\n"
    "Classic MilkDrop 2 presets work. MilkDrop 3's double presets (.milk2) don't.\n")
HERE = getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))


def my_stingers_folder():
    """Where your own stinger mp3s live. Inside the .exe that's a folder in AppData (the six built-in ones play too)."""
    if getattr(sys, "frozen", False):
        return os.path.join(APP_DIR, "my_stingers")
    return os.path.join(os.path.dirname(os.path.abspath(__file__)), "my_stingers")


DEFAULTS = {
    "spotify_client_id": "",
    "spotify_client_secret": "",
    "elevenlabs_api_key": "",
    "elevenlabs_voice_id": "",
    "eleven_model": "eleven_v4",
    "gemini_api_key": "",
    "city": "Yakima, Washington",
    "break_min": 2,
    "break_max": 5,
    "tts_engine": "elevenlabs",
    "t_talkover": True,
    "t_intro": True,
    "t_silent": True,
    "t_fadeout": True,
    "breaking_enabled": True,
    "breaking_test": False,
    "popin_enabled": True,
    "popin_test": False,
    "popin_chance": 35,
    "popin_secs": 15,
    "stingers": True,
    "welcomed": False,
    "dj_volume": 100,
    "stinger_volume": 80,
    "duck_auto": True,
    "duck_percent": 20,
    "stinger_chance": 35,
    "station_stingers": True,   # your stingers re-voiced with the name of what's playing (like the iPhone app)
    "station_voice": "",        # the ElevenLabs voice on station stingers ("" for the default announcer)
    "mood": "normal",
    "chattiness": "chatty",
    "cohost_enabled": True,
    "cohost_chance": 40,
    "cohost_swears": True,
    "cohost_voice": "",
    "recent_searches": [],
    "player_rechecked": False,  # set once: the built-in player got another go after its silent-window fix
    "player": "app",            # where music plays: "app" (the built-in player) or "spotify" (the Spotify app, any device)
    "ui_bg": "song",            # the window's background: "song" (its colours), "black" or "white" (the window keeps its own copy)
}
TRANSITION_WEIGHTS = {"talkover": 4, "intro": 3, "silent": 3, "fadeout": 2}
SECRET_KEYS = ("spotify_client_secret", "elevenlabs_api_key", "gemini_api_key")


# ---------------------------------------------------------------- everything the engine says, for Activity
class Log:
    """Catches what the DJ engine prints: the Activity list, and Cara's latest line for the screen."""

    def __init__(self):
        self.lines = collections.deque(maxlen=600)
        self.count = 0
        self.line = ""             # her latest words
        self.kind = ""             # "cara", "duo", "popin" or "breaking"
        self.duo = []              # [{who, text}] when it was Cara and Scratch
        self.lock = threading.Lock()

    def write(self, s):
        if not s or not s.strip():
            return len(s or "")
        text = s.rstrip()
        with self.lock:
            for part in text.split("\n"):
                if part.strip():
                    self.lines.append(time.strftime("%H:%M:%S ") + part)
                    self.count += 1
            self._spot(text)
        return len(s)

    def _spot(self, text):
        m = re.match(r"^\[(DJ:[a-z]+|BREAKING:[^\]]*|POP-IN)\] (.+)$", text, re.S)
        if m:
            tag = m.group(1)
            self.kind = "breaking" if tag.startswith("BREAKING") else ("popin" if tag == "POP-IN" else "cara")
            self.line, self.duo = m.group(2).strip(), []
            return
        if text.startswith("[DUO:") and "\n" in text:
            rows = []
            for row in text.split("\n", 1)[1].split("\n"):
                who, _, said = row.partition(": ")
                if said:
                    rows.append({"who": who.strip(), "text": said.strip()})
            if rows:
                self.kind, self.duo = "duo", rows
                self.line = " ".join(r["text"] for r in rows)

    def flush(self):
        pass

    def isatty(self):
        return False

    def since(self, n):
        with self.lock:
            have = list(self.lines)
            total = self.count
        new = max(0, total - n)
        return {"lines": have[-new:] if new else [], "next": total}


LOG = Log()


def load_config():
    cfg = dict(DEFAULTS)
    try:
        with open(CONFIG_PATH, "r", encoding="utf-8") as f:
            cfg.update(json.load(f))
    except Exception:
        pass
    return cfg


def taste_excluded():
    """Songs taken out of your taste profile ("title|artist", lower case): the DJs don't read anything into them."""
    try:
        with open(os.path.join(APP_DIR, "taste-excluded.json"), encoding="utf-8") as f:
            return [k for k in json.load(f) if isinstance(k, str)]
    except Exception:
        return []


def spotify_app_installed():
    """Is the Spotify app on this PC (it owns spotify: links)? Checked first, because Windows otherwise offers to find
    an app in the Store."""
    if sys.platform != "win32":
        return False
    import winreg
    for hive, path in ((winreg.HKEY_CLASSES_ROOT, "spotify"),
                       (winreg.HKEY_CURRENT_USER, r"Software\Classes\Local Settings\Software\Microsoft\Windows\CurrentVersion"
                                                  r"\AppModel\PackageRepository\Extensions\windows.protocol\spotify"),
                       (winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Windows\Shell\Associations\UrlAssociations\spotify\UserChoice")):
        try:
            winreg.CloseKey(winreg.OpenKey(hive, path))
            return True
        except OSError:
            pass
    return False


def save_config(cfg):
    with open(CONFIG_PATH, "w", encoding="utf-8") as f:
        json.dump(cfg, f, indent=2)


def who_is_here():
    """(first name to greet, name of this PC) taken from Windows."""
    user = ""
    try:
        user = getpass.getuser()
    except Exception:
        user = os.environ.get("USERNAME") or os.environ.get("USER") or ""
    user = user.replace(".", " ").replace("_", " ").strip()
    first = user.split()[0] if user else "friend"
    if first.islower() or first.isupper():
        first = first.capitalize()
    try:
        pc = socket.gethostname()
    except Exception:
        pc = ""
    return first, pc


def open_path(path):
    try:
        if sys.platform == "win32":
            os.startfile(path)
        else:
            import subprocess
            subprocess.Popen(["xdg-open", path])
    except Exception as e:
        print(f"Couldn't open {path}: {e}")


# ---------------------------------------------------------------- the engine and Spotify, without any window
class App:
    def __init__(self):
        self.cfg = load_config()
        self.dj = None              # the engine module (live_dj_free), loaded when connecting
        self.dj_thread = None
        self.connected = False
        self.connecting = False
        self.problem = ""
        self.hint = ""              # "no-device" when Spotify has nowhere to play
        self.now = None             # latest playback snapshot (pc_spotify.playback())
        self.context_name = ""
        self.liked = {}             # uri -> liked?
        self.queued = None          # the transition queued for the next break
        self.sleep_at = None        # epoch seconds, or None
        self.sleep_end_of_song = False
        self.sleep_uri = None
        self.window = None
        self.poke = threading.Event()
        os.environ.setdefault("DJ_STINGER_DIR", os.path.join(APP_DIR, "stingers"))   # station stingers live here too
        self.builtin = pc_player.Player(APP_DIR, os.path.join(HERE, "ui", "player.html"), self.player_token, self.player_changed)
        self.sound_watch = None         # listening for the built-in player's sound after it starts playing
        self.sound_heard = False
        self.sound_repaired = False     # started the player over once already because it was silent
        self.want_here = False          # "this PC" was picked in Play on: move the music here once the player's ready
        self.pending = {}               # a button's effect, shown until Spotify catches up: key -> (value, until)
        self.qadded = []                # songs queued from this app, oldest first: {"uri", "at"} ("Next in queue")
        self.qskip = []                 # songs taken out of the queue: {"uri", "nth", "at", "section", "title"}
        self.qlock = threading.Lock()
        self.played_at = 0.0            # when you last started something yourself (its first song is never skipped)
        self.builtin.on_skipped = self.queue_skipped
        self.notice, self.notice_n = None, 0
        if not self.cfg.get("player_rechecked"):
            # Before, the built-in player's hidden window loaded no sound, so many switched to the Spotify app.
            # That's fixed: it gets one more go (music isn't moved; it's ready in the Play on list).
            self.cfg["player_rechecked"] = True
            if self.cfg.get("player") == "spotify":
                self.cfg["player"] = "app"
            if os.path.exists(CONFIG_PATH):
                save_config(self.cfg)
        threading.Thread(target=self.poll_loop, daemon=True).start()
        if self.cfg["spotify_client_id"] and self.cfg["spotify_client_secret"]:
            if not self.cfg.get("welcomed"):
                self.cfg["welcomed"] = True   # already set up: never show the welcome
                save_config(self.cfg)
            threading.Timer(0.6, self.connect).start()

    # ------------------------------------------------------------ settings
    def public_config(self):
        return dict(self.cfg)

    def update_config(self, patch):
        keys_changed = False
        for k, v in (patch or {}).items():
            if k not in DEFAULTS:
                continue
            if isinstance(DEFAULTS[k], bool):
                v = bool(v)
            elif isinstance(DEFAULTS[k], int):
                try:
                    v = int(v)
                except (TypeError, ValueError):
                    continue
            elif isinstance(DEFAULTS[k], str):
                v = str(v or "").strip()
            if k in ("spotify_client_id", "spotify_client_secret") and v != self.cfg.get(k):
                keys_changed = True
            if k == "player" and v not in ("app", "spotify"):
                continue
            if k == "player" and v != self.cfg.get(k) and self.connected:
                threading.Thread(target=self.builtin.start if v == "app" else self.leave_builtin, daemon=True).start()
            self.cfg[k] = v
        c = self.cfg
        c["break_min"] = max(1, min(10, int(c["break_min"])))
        c["break_max"] = max(c["break_min"], min(10, int(c["break_max"])))
        c["popin_chance"] = max(0, min(100, int(c["popin_chance"])))
        c["popin_secs"] = max(5, min(240, int(c["popin_secs"])))
        c["stinger_chance"] = max(0, min(100, int(c["stinger_chance"])))
        c["cohost_chance"] = max(0, min(100, int(c["cohost_chance"])))
        save_config(c)
        os.environ["ELEVENLABS_API_KEY"] = c["elevenlabs_api_key"]
        os.environ["ELEVENLABS_VOICE_ID"] = c["elevenlabs_voice_id"]
        os.environ["ELEVENLABS_MODEL"] = c.get("eleven_model") or "eleven_v4"
        os.environ["GEMINI_API_KEY"] = c["gemini_api_key"]
        if self.dj is not None:
            self.apply_settings()     # takes effect on the very next break
        return {"config": self.public_config(), "reconnect": keys_changed}

    def apply_settings(self):
        dj, c = self.dj, self.cfg
        dj.TTS_ENGINE = c["tts_engine"]
        dj.MODE = "gemini" if c["gemini_api_key"] else "template"
        dj.BREAK_EVERY_MIN = max(1, int(c["break_min"]))
        dj.BREAK_EVERY_MAX = max(dj.BREAK_EVERY_MIN, int(c["break_max"]))
        dj.BREAKING_ENABLED = bool(c.get("breaking_enabled", True))
        dj.BREAKING_TEST_MODE = bool(c.get("breaking_test", False))
        dj.POPIN_ENABLED = bool(c.get("popin_enabled", True))
        dj.POPIN_TEST_MODE = bool(c.get("popin_test", False))
        dj.POPIN_CHANCE = int(c.get("popin_chance", 35)) / 100.0
        dj.POPIN_AFTER_SEC = int(c.get("popin_secs", 15))
        dj.STINGERS_ENABLED = bool(c.get("stingers", True))
        dj.DJ_VOLUME = int(c.get("dj_volume", 100)) / 100.0
        dj.STINGER_VOLUME = int(c.get("stinger_volume", 80)) / 100.0
        dj.DUCK_PERCENT = None if c.get("duck_auto", True) else int(c.get("duck_percent", 20))
        dj.STINGER_CHANCE = int(c.get("stinger_chance", 35)) / 100.0
        dj.SILENT_STINGER_PERCENT = int(c.get("stinger_chance", 35))      # "Stingers before silent breaks"
        dj.STATION_STINGERS = bool(c.get("station_stingers", True))
        os.environ["DJ_STATION_VOICE"] = (c.get("station_voice") or "").strip()
        dj.DJ_MOOD = c.get("mood", "normal")
        dj.CHATTINESS = c.get("chattiness", "chatty")
        dj.COHOST_ENABLED = bool(c.get("cohost_enabled", True))
        dj.COHOST_CHANCE = int(c.get("cohost_chance", 40)) / 100.0
        dj.COHOST_SWEARS = bool(c.get("cohost_swears", True))
        dj.COHOST_VOICE = (c.get("cohost_voice") or "").strip()
        weights = {k: w for k, w in TRANSITION_WEIGHTS.items() if c.get("t_" + k)}
        dj.TRANSITIONS = weights or dict(TRANSITION_WEIGHTS)
        if c["city"] and c["city"].strip() != dj.CITY:
            threading.Thread(target=dj.set_city, args=(c["city"].strip(),), daemon=True).start()

    # ------------------------------------------------------------ the engine
    def load_engine(self):
        """(Re)load the DJ engine with the saved keys. Not allowed while the DJ is running."""
        c = self.cfg
        os.environ["SPOTIPY_CLIENT_ID"] = c["spotify_client_id"]
        os.environ["SPOTIPY_CLIENT_SECRET"] = c["spotify_client_secret"]
        os.environ["SPOTIPY_REDIRECT_URI"] = REDIRECT_URI
        os.environ["DJ_SCOPES"] = pc_spotify.SCOPES
        os.environ["DJ_APP_DIR"] = APP_DIR      # Cara's memory and the station names live here
        os.environ["DJ_CACHE_PATH"] = CACHE_PATH
        os.environ["DJ_STINGER_DIR"] = os.path.join(APP_DIR, "stingers")
        os.makedirs(my_stingers_folder(), exist_ok=True)
        os.environ["DJ_MY_STINGERS"] = my_stingers_folder()
        os.environ["ELEVENLABS_API_KEY"] = c["elevenlabs_api_key"]
        os.environ["ELEVENLABS_VOICE_ID"] = c["elevenlabs_voice_id"]
        os.environ["ELEVENLABS_MODEL"] = c.get("eleven_model") or "eleven_v4"
        os.environ["GEMINI_API_KEY"] = c["gemini_api_key"]
        if self.dj is None:
            self.dj = importlib.import_module("live_dj_free")
        else:
            self.dj = importlib.reload(self.dj)
        pc_spotify.sp = pc_spotify.guard(self.dj.sp)     # waits out Spotify's "slow down", for the app and Cara alike
        pc_spotify.CACHE_DIR = APP_DIR
        self.dj.QUEUE_FILTER = self.filter_queue    # Cara won't announce a song you took out of the queue
        self.apply_settings()

    def connect(self, force=False):
        if self.connecting or self.dj_running():
            return
        if not (self.cfg["spotify_client_id"] and self.cfg["spotify_client_secret"]):
            self.problem = "Add your Spotify Client ID and Secret in Settings."
            return
        if force:
            try:
                os.remove(CACHE_PATH)     # a fresh login (a browser tab opens: click Agree)
            except OSError:
                pass
        self.connecting = True
        self.problem = ""

        def work():
            try:
                self.load_engine()
                self.dj.sp.current_playback()  # triggers the login the first time (a browser tab opens)
                self.connected = True
                pc_spotify.forget_home()
                print("Connected to Spotify.")
                if self.cfg.get("player", "app") == "app":
                    self.builtin.start()
            except Exception as e:
                self.connected = False
                msg = str(e)
                low = msg.lower()
                if "403" in low or "not registered" in low:
                    self.problem = "Spotify said no: add the email of your Spotify account under User Management in your Spotify app (developer.spotify.com), then try again."
                elif "invalid_client" in low or "invalid client" in low:
                    self.problem = "Spotify didn't recognise that Client ID and Secret. Check them in Settings."
                else:
                    self.problem = "Couldn't connect to Spotify. Check your Client ID and Secret in Settings, and that the app's Redirect URI is " + REDIRECT_URI + "."
                print("Could not connect to Spotify:", msg[:300])
            finally:
                self.connecting = False
                self.poke.set()

        threading.Thread(target=work, daemon=True).start()

    def logout(self):
        if self.dj_running():
            self.dj.STOP.set()
        threading.Thread(target=self.builtin.stop, daemon=True).start()
        try:
            os.remove(CACHE_PATH)
        except OSError:
            pass
        self.connected = False
        self.now = None
        pc_spotify.forget_home()
        pc_spotify.forget_feed()
        self.forget_queue()
        pc_spotify._me.update(at=0, value=None)
        print("Logged out of Spotify.")

    def dj_running(self):
        return self.dj_thread is not None and self.dj_thread.is_alive()

    # ------------------------------------------------------------ the built-in player
    def player_token(self):
        if not (self.connected and self.dj is not None):
            return None
        return self.dj.sp.auth_manager.get_access_token(as_dict=False)

    def player_changed(self, status):
        if status != "ready":
            self.sound_heard, self.sound_watch = False, None
        if status == "ready" and self.cfg.get("player", "app") == "app":
            force, self.want_here = self.want_here, False          # picked in Play on: the music comes here
            threading.Thread(target=self.use_builtin, args=(force,), daemon=True).start()
        self.poke.set()

    def leave_builtin(self):
        """Switching to the Spotify app: hand the music over first (if the Spotify app is open), then stop the player."""
        n = self.now or {}
        if self.builtin.device_id and (n.get("device") or {}).get("id") == self.builtin.device_id:
            devs = [d for d in pc_spotify.devices() if d["id"] != self.builtin.device_id and not d["restricted"]]
            pick = next((d for d in devs if d["type"] == "computer"), None) or (devs[0] if devs else None)
            if pick:
                pc_spotify.safe(lambda: pc_spotify.put("me/player", payload={"device_ids": [pick["id"]], "play": bool(n.get("playing"))}))
        self.builtin.stop()
        self.poke.set()

    def check_sound(self, pb):
        """Spotify says the built-in player is playing: is sound actually coming out? (listens to the speakers)"""
        p, t = self.builtin, (pb or {}).get("track") or {}
        here = p.status == "ready" and p.device_id and ((pb or {}).get("device") or {}).get("id") == p.device_id
        if self.sound_heard or not (here and pb.get("playing")):
            self.sound_watch = None
            return
        w = self.sound_watch
        if w is None:
            w = self.sound_watch = {"since": time.time(), "nudged": False}
        ear = pc_audio.LISTENER
        ear.frame()                                  # keeps the listener going while it checks
        waited = time.time() - w["since"]
        if not ear.live:
            if waited > 25:                          # can't listen on this PC: trust it
                self.sound_heard = True
            return
        if ear.vol > 0.03 or ear.bass > 0.1:
            self.sound_heard, self.sound_watch, self.sound_repaired = True, None, False
            print("[player: sound check passed: the music is coming out]")
        elif waited > 7 and not w["nudged"]:
            w["nudged"] = True
            threading.Thread(target=p.unlock, args=("no sound yet",), daemon=True).start()
        elif waited > 18:
            self.sound_watch = None
            detail = f"silent for {int(waited)} seconds while Spotify said it was playing"
            if not self.sound_repaired:                  # first time: start the player over, which usually sorts it
                self.sound_repaired = True

                def again():
                    print(f"[player: no sound yet ({detail}); page: {p.vis or '?'}; Spotify's frame: {p.media()}; "
                          "starting the built-in player over]")
                    p.retry()
                threading.Thread(target=again, daemon=True).start()
            else:
                threading.Thread(target=p.no_sound, args=(detail,), daemon=True).start()

    def play_here(self):
        """"Headphones · this PC" in Play on: the music moves to the built-in player (started first if need be)."""
        if not self.connected:
            return "Connect Spotify first."
        b = self.builtin
        if self.cfg.get("player", "app") != "app":
            self.cfg["player"] = "app"
            save_config(self.cfg)
            print("[player: music plays in this app again]")
        if b.status == "premium":
            return b.problem or "Spotify only lets apps play music with Premium."
        if b.status == "ready":
            return "ok" if self.use_builtin(force=True) else "Spotify wouldn't move the music here. Try again."
        self.want_here = True
        threading.Thread(target=b.retry if b.status in ("error", "off") else b.start, daemon=True).start()
        return "starting"

    def say(self, text):
        """A message for the screen (it pops up once)."""
        print(f"[{text}]")
        self.notice_n += 1
        self.notice = {"id": self.notice_n, "text": text}

    def _settle(self, pb):
        """Spotify takes a moment to show a button's effect: keep showing it meanwhile, and say so if it never comes."""
        if not pb or not self.pending:
            return pb
        now = time.time()
        for key, (want, until) in list(self.pending.items()):
            if pb.get(key) == want:
                self.pending.pop(key, None)                  # Spotify caught up
            elif now < until:
                pb[key] = want                               # still on its way: show what you asked for
            else:
                self.pending.pop(key, None)
                if key in ("shuffle", "repeat"):
                    dev = (pb.get("device") or {}).get("name") or "That device"
                    self.say(f"{dev} didn't change {key}. Some speakers don't let apps change it.")
        return pb

    def use_builtin(self, force=False):
        """Point Spotify at the built-in player, unless music is already playing on another device (or when asked)."""
        dev = self.builtin.device_id
        if not (dev and self.connected):
            return False
        n = self.now or {}
        d = n.get("device") or {}
        ours = d.get("id") in (None, dev) or d.get("id") in self.builtin.past_ids or d.get("name") == pc_player.NAME
        if not force and n.get("playing") and not ours:       # leave music that's playing on another device alone
            return False
        keep = bool(n.get("playing"))
        ok = pc_spotify.safe(lambda: pc_spotify.put("me/player", payload={"device_ids": [dev], "play": keep}) or True, False)
        threading.Timer(0.8, self.poke.set).start()
        return bool(ok)

    def toggle_dj(self):
        if self.dj_running():
            self.dj.STOP.set()
            return "stopping"
        if not self.connected:
            self.connect()
            return "connect"

        def run():
            try:
                self.dj.STOP.clear()
                self.apply_settings()
                self.dj.main()
            except Exception as e:
                print("The DJ stopped because of an error:", e)
            finally:
                self.queued = None

        self.dj_thread = threading.Thread(target=run, daemon=True)
        self.dj_thread.start()
        return "started"

    def test(self, what):
        if not self.dj_running():
            return "Go live first (and play a song), then try that."
        ev = {"break": "FORCE_BREAKING", "popin": "FORCE_POPIN", "duo": "FORCE_DUO", "stinger": "FORCE_STINGER"}.get(what)
        if what == "talk":
            self.dj.FORCE_TRANSITION = self.dj.FORCE_TRANSITION or "talkover"
            return "ok"
        if ev:
            note = ""
            if what == "stinger":
                try:
                    note = self.dj.stinger_test_note()      # a station stinger has to be made first
                except Exception:
                    note = ""
            getattr(self.dj, ev).set()
            return f"making:{note}" if note else "ok"
        return "unknown"

    def queue_next(self, style):
        if not self.dj_running():
            return "Go live first, then pick how her next break starts."
        self.dj.FORCE_TRANSITION = style
        self.queued = style
        print(f"[queued next transition: {style}]")
        return "ok"

    # ------------------------------------------------------------ Spotify playback
    def poll_loop(self):
        last_err, last_note, empty_since, last_uri = None, None, None, None
        while True:
            n = self.now or {}
            self.poke.wait(1.5 if n.get("playing") else 4.0)     # paused: Spotify is asked less often
            self.poke.clear()
            if not (self.connected and self.dj is not None):
                continue
            if pc_spotify.limited():
                continue                                       # Spotify asked the app to slow down: wait it out
            try:
                pb = pc_spotify.playback()
                if last_err:
                    print("Spotify is answering again.")
                    last_err = None
            except Exception as e:
                msg = str(e)
                if msg != last_err:          # say it once, not every second
                    last_err = msg
                    print("Spotify did not answer:", msg[:300])
                    low = msg.lower()
                    if "403" in low or "forbidden" in low or "not registered" in low:
                        print("Fix: in developer.spotify.com > your app > Settings > User Management, add the email of your Spotify account, then restart this app.")
                    elif "401" in low or "token" in low:
                        print("Fix: in Settings, press Reconnect Spotify and click Agree.")
                    elif "429" in low:
                        print("Spotify is rate limiting this app. It will work again in a few minutes.")
                continue
            self.check_sleep(pb)
            t = pb.get("track") if pb else None
            if not t:
                self.now = self._settle(pb) or {}
                if empty_since is None:
                    empty_since = time.time()
                if time.time() - empty_since > 6:       # explain why, once per change
                    devs = pc_spotify.devices()
                    if not devs:
                        self.hint = "no-device"
                        note = "Spotify sees no devices at all. Open the Spotify app on this PC, play any song, then wait a few seconds."
                    elif pb and not pb.get("playing"):
                        self.hint = ""
                        note = "Spotify is connected but nothing is loaded. Press play on a song."
                    else:
                        self.hint = ""
                        note = None
                    if note and note != last_note:
                        last_note = note
                        print(note)
                continue
            empty_since, last_note, self.hint = None, None, ""
            try:
                self.check_sound(pb)
            except Exception as e:
                print(f"[player: sound check failed: {e}]")
            b = getattr(self.dj, "brain", None)
            if b is not None:
                try:
                    b.STATION.update(pb.get("rawContext"))
                except Exception:
                    pass
            pb.pop("rawContext", None)
            ctx = pb.get("context") or ""
            self.context_name = pc_spotify.context_name(ctx) if ctx else ""
            if t["uri"] != last_uri:
                last_uri = t["uri"]
                try:
                    self.queue_moved(t["uri"])
                except Exception as e:
                    print(f"[queue: {e}]")
                if t["uri"] and t["uri"] not in self.liked:
                    got = pc_spotify.contains([t["uri"]])
                    if got:
                        self.liked[t["uri"]] = bool(got[0])
            self.now = self._settle(pb)
            left = (t.get("duration") or 0) - (pb.get("progress") or 0)
            if self.qskip and pb.get("playing") and 0 < left < 1600 and not self._builtin_playing():
                threading.Timer(left / 1000 + 0.25, self.poke.set).start()     # catch a song you took out as it starts

    # ------------------------------------------------------------ the queue
    # Spotify's queue doesn't say which songs you queued and which come from the playlist, so the app remembers what
    # you queue from here ("Next in queue"). Spotify has no "remove from queue" for apps either: a song you take out
    # is hidden here and skipped the moment it comes up (by the built-in player itself, before you hear it).
    def queue_add(self, uri):
        ok = pc_spotify.safe(lambda: pc_spotify.post("me/player/queue", uri=uri) or True, False)
        if ok:
            with self.qlock:
                self.qadded.append({"uri": uri, "at": time.time()})
        return bool(ok)

    def _queue_raw(self):
        j = pc_spotify.safe(lambda: pc_spotify.get("me/player/queue")) or {}
        return [t for t in (pc_spotify.track(x) for x in j.get("queue") or []) if t]

    def filter_queue(self, items):
        """What's coming up, without the songs you took out (each by the time it comes round, in case it's in there twice)."""
        with self.qlock:
            hide = {}
            for e in self.qskip:
                hide.setdefault(e["uri"], set()).add(e["nth"])
        seen, out = {}, []
        for t in items or []:
            u = t.get("uri") if isinstance(t, dict) else ""
            k = seen.get(u, 0)
            seen[u] = k + 1
            if u and k in hide.get(u, ()):
                continue
            out.append(t)
        return out

    def queue_view(self):
        """The queue as Spotify shows it: Next in queue (what you queued from here), then Next from the playlist."""
        items = self.filter_queue(self._queue_raw())
        with self.qlock:
            pending = [e["uri"] for e in self.qadded]
            head = []
            for t in items:
                if t["uri"] in pending:
                    pending.remove(t["uri"])
                    head.append(t)
                else:
                    break
            found = collections.Counter(t["uri"] for t in head)
            keep = []
            for e in self.qadded:            # forget what played elsewhere or was cleared, once Spotify's had time to show it
                if found[e["uri"]] > 0:
                    found[e["uri"]] -= 1
                    keep.append(e)
                elif time.time() - e["at"] < 20:
                    keep.append(e)
            self.qadded = keep
        return {"queued": head, "next": items[len(head):len(head) + 40], "from": self.context_name}

    def up_next(self):
        return self.filter_queue(self._queue_raw())[:30]

    def queue_remove(self, uri, nth=0, section="next"):
        """Takes the nth time a song shows in the queue out of it (Spotify can't, so it's skipped when it comes round)."""
        raw = self._queue_raw()
        with self.qlock:
            hide = {}
            for e in self.qskip:
                hide.setdefault(e["uri"], set()).add(e["nth"])
        seen, shown, target, title = {}, -1, None, ""
        for t in raw:
            u = t["uri"]
            k = seen.get(u, 0)
            seen[u] = k + 1
            if k in hide.get(u, ()):
                continue
            if u == uri:
                shown += 1
                if shown == int(nth or 0):
                    target, title = k, f"{t['title']} by {t['artistLine']}"
                    break
        if target is None:
            return "That song isn't in the queue any more."
        with self.qlock:
            self.qskip.append({"uri": uri, "nth": target, "at": time.time(), "section": section, "title": title})
            if section == "queued":
                for i, e in enumerate(self.qadded):
                    if e["uri"] == uri:
                        del self.qadded[i]
                        break
        self._push_skip()
        print(f"[queue: took {title} out of the queue; it'll be skipped when it comes round]")
        return True

    def _push_skip(self):
        """Tells the built-in player which songs to skip the moment they start."""
        with self.qlock:
            self.qskip = [e for e in self.qskip if time.time() - e["at"] < 6 * 3600]
            due = [e["uri"] for e in self.qskip if e["nth"] == 0]
        self.builtin.set_skip(due)

    def queue_moved(self, uri):
        """A new song started: it's off what you queued, and if you took it out of the queue, it's skipped."""
        if not uri or time.time() - self.played_at < 4:           # you just picked it yourself
            return
        builtin = self._builtin_playing()
        with self.qlock:
            target = next((e for e in self.qskip if e["uri"] == uri and e["nth"] == 0), None)
            moved = False
            for e in self.qskip:
                if e["uri"] == uri and e is not target:
                    e["nth"] = max(0, e["nth"] - 1)                 # an earlier time it was in the queue just came round
                    moved = True
            if target:
                self.qskip.remove(target)
            else:
                for i, e in enumerate(self.qadded):
                    if e["uri"] == uri:
                        del self.qadded[i]
                        break
        if target or moved:
            self._push_skip()
        if not target:
            return
        print(f"[queue: skipping {target['title']} (you took it out of the queue)]")
        if builtin:                       # the player page skips it by itself; if it hasn't in a moment, do it from here
            threading.Timer(2.5, lambda: self._skip_if_still(uri)).start()
        else:
            self.player("next")

    def queue_skipped(self, uri):
        """The built-in player skipped a song you took out of the queue."""
        with self.qlock:
            target = next((e for e in self.qskip if e["uri"] == uri and e["nth"] == 0), None)
            if not target:
                return
            self.qskip.remove(target)
            for e in self.qskip:
                if e["uri"] == uri:
                    e["nth"] = max(0, e["nth"] - 1)
        print(f"[queue: skipped {target['title']} (you took it out of the queue)]")
        self._push_skip()
        threading.Timer(0.3, self.poke.set).start()

    def _skip_if_still(self, uri):
        t = (self.now or {}).get("track") or {}
        if t.get("uri") == uri:
            self.player("next")

    def _builtin_playing(self):
        d = (self.now or {}).get("device") or {}
        b = self.builtin
        return b.status == "ready" and bool(d.get("id")) and (d.get("id") == b.device_id or d.get("id") in b.past_ids)

    def forget_queue(self):
        with self.qlock:
            self.qadded, self.qskip = [], []
        self._push_skip()

    def check_sleep(self, pb):
        """The sleep timer: pauses Spotify (and Cara) when it's up."""
        if not (self.sleep_at or self.sleep_end_of_song):
            return
        t = (pb or {}).get("track") or {}
        due = bool(self.sleep_at and time.time() >= self.sleep_at)
        if self.sleep_end_of_song and self.sleep_uri and t.get("uri") and t.get("uri") != self.sleep_uri:
            due = True
        if due:
            self.sleep_at, self.sleep_end_of_song, self.sleep_uri = None, False, None
            print("Sleep timer: good night.")
            pc_spotify.safe(lambda: pc_spotify.put("me/player/pause"))
            if self.dj_running():
                self.dj.STOP.set()

    def set_sleep(self, minutes):
        if minutes is None or minutes == 0:
            self.sleep_at, self.sleep_end_of_song, self.sleep_uri = None, False, None
        elif minutes == -1:
            t = (self.now or {}).get("track") or {}
            self.sleep_at, self.sleep_end_of_song, self.sleep_uri = None, True, t.get("uri")
        else:
            self.sleep_at, self.sleep_end_of_song = time.time() + minutes * 60, False

    def player(self, action, value=None):
        """The play/pause/skip buttons. True when it worked, otherwise a few words on why not (the screen shows them)."""
        if not (self.connected and self.dj is not None):
            return "Connect Spotify first."
        n = self.now or {}
        d = (n.get("device") or {}).get("id")
        b = self.builtin
        here = d and d == b.device_id and b.status == "ready" and action in ("toggle", "play", "pause", "next", "previous", "seek", "volume")
        if pc_spotify.limited() and not here:            # the built-in player still answers: it doesn't ask Spotify's servers
            return f"Spotify asked the app to slow down. Try again in {int(pc_spotify.limited()) + 1} seconds."
        n = self.now or {}
        dev = (n.get("device") or {}).get("id")
        q = {"device_id": dev} if dev else {}
        b = self.builtin
        if (dev and dev == b.device_id and b.status == "ready"
                and action in ("toggle", "play", "pause", "next", "previous", "seek", "volume")):
            try:                                        # playing in this app: straight to the player, no round trip
                got = b.control(action, value)
                if action == "toggle" and self.now and got in ("playing", "paused"):
                    self.now["playing"], self.now["stamp"] = got == "playing", int(time.time() * 1000)
                    self.pending["playing"] = (got == "playing", time.time() + 3)
                    action = ""                         # already up to date
                self._commanded(action, value, n)
                return True
            except Exception as e:
                print(f"[player: the built-in player couldn't {action} by itself ({e}); asking Spotify instead]")
        f = {
            "toggle": lambda: pc_spotify.put("me/player/pause" if n.get("playing") else "me/player/play", **q),
            "play": lambda: pc_spotify.put("me/player/play", **q),
            "pause": lambda: pc_spotify.put("me/player/pause"),
            "next": lambda: pc_spotify.post("me/player/next"),
            "previous": lambda: pc_spotify.post("me/player/previous"),
            "seek": lambda: pc_spotify.put("me/player/seek", position_ms=int(value or 0)),
            "volume": lambda: pc_spotify.put("me/player/volume", volume_percent=int(max(0, min(100, value or 0)))),
            "shuffle": lambda: pc_spotify.put("me/player/shuffle", state="true" if value else "false", **q),
            "repeat": lambda: pc_spotify.put("me/player/repeat", state=value or "off", **q),
        }.get(action)
        if not f:
            return "That button doesn't do anything yet."
        try:
            f()
        except Exception as e:
            status = getattr(e, "http_status", None)
            print(f"Spotify said no ({status or 'no answer'}): {str(e)[:200]}")
            threading.Timer(0.35, self.poke.set).start()
            low = str(e).lower()
            if status == 404 or "no active device" in low:
                return "Nothing's playing anywhere right now. Pick a song first."
            if status == 403 or "restriction" in low:
                return "Spotify won't do that right now."
            if status == 401:
                return "Spotify wants you to reconnect: Settings, Reconnect Spotify."
            if status == 429:
                return "Spotify's busy. Try again in a moment."
            return "Spotify didn't answer. Try again."
        self._commanded(action, value, n)
        return True

    def _commanded(self, action, value, n):
        """Show a button's effect right away (Spotify's own answer follows a moment later)."""
        soon = time.time()
        if action == "toggle":
            self.pending["playing"] = (not n.get("playing"), soon + 3)
        elif action == "shuffle":
            self.pending["shuffle"] = (bool(value), soon + 4)
        elif action == "repeat":
            self.pending["repeat"] = (value or "off", soon + 4)
        if self.now:
            if action == "toggle":
                self.now["playing"] = not n.get("playing")
                self.now["stamp"] = int(time.time() * 1000)
            elif action == "shuffle":
                self.now["shuffle"] = bool(value)
            elif action == "repeat":
                self.now["repeat"] = value or "off"
            elif action == "seek":
                self.now["progress"], self.now["stamp"] = int(value or 0), int(time.time() * 1000)
        threading.Timer(0.35, self.poke.set).start()

    def play(self, context=None, offset_uri=None, uris=None, position=None, shuffle=None):
        if not (self.connected and self.dj is not None):
            return "Connect Spotify first."
        if pc_spotify.limited():
            return f"Spotify asked the app to slow down. Try again in {int(pc_spotify.limited()) + 1} seconds."
        n = self.now or {}
        dev = (n.get("device") or {}).get("id")
        builtin = self.builtin.device_id if (self.cfg.get("player", "app") == "app" and self.builtin.status == "ready") else None
        elsewhere = (dev and dev != builtin and dev not in self.builtin.past_ids
                     and (n.get("device") or {}).get("name") != pc_player.NAME)
        if builtin and not (n.get("playing") and elsewhere):
            dev = builtin                 # play in this app, unless music is already playing on another device
        if not dev:
            devs = pc_spotify.devices()
            pick = next((d for d in devs if d["active"]), None) or next((d for d in devs if d["type"] == "computer"), None) or (devs[0] if devs else None)
            dev = pick["id"] if pick else None
            if not dev:
                if self.cfg.get("player", "app") == "app" and self.builtin.status == "starting":
                    return "The built-in player is still starting. Try again in a few seconds."
                return "Spotify isn't open anywhere. Open the Spotify app on this PC, then try again."
        body = {}
        if context:
            body["context_uri"] = context
        if uris:
            body["uris"] = uris[:100]
        if offset_uri:
            body["offset"] = {"uri": offset_uri}
        elif position is not None:
            body["offset"] = {"position": int(position)}
        self.played_at = time.time()
        with self.qlock:                                # a new playlist: what you took out of the old one doesn't matter now
            self.qskip = [e for e in self.qskip if e.get("section") == "queued"]
        self._push_skip()
        mine = None
        if shuffle and context and not offset_uri and position is None:
            mine = self.shuffle_start(context)          # Spotify starts a playlist at the top even with shuffle on
            if mine is not None:
                body["offset"] = {"position": mine}
        if shuffle is not None:
            pc_spotify.safe(lambda: pc_spotify.put("me/player/shuffle", state="true" if shuffle else "false", device_id=dev))
            self.pending["shuffle"] = (bool(shuffle), time.time() + 5)
        ok = pc_spotify.safe(lambda: pc_spotify.put("me/player/play", payload=body, device_id=dev) or True, False)
        if not ok and mine is not None:                 # that song may not play here: from the top, shuffled
            body.pop("offset", None)
            ok = pc_spotify.safe(lambda: pc_spotify.put("me/player/play", payload=body, device_id=dev) or True, False)
        if ok and shuffle is not None:                  # some speakers reset shuffle when a new playlist starts
            threading.Timer(1.2, lambda: pc_spotify.safe(lambda: pc_spotify.put(
                "me/player/shuffle", state="true" if shuffle else "false", device_id=dev))).start()
        threading.Timer(0.5, self.poke.set).start()
        return "ok" if ok else "Spotify wouldn't play that (it may not be available in your country)."

    def shuffle_start(self, context):
        """A random song to start a shuffled playlist, album or Liked Songs on (None if Spotify can't say how many)."""
        parts = context.split(":")
        try:
            if "collection" in parts:
                total = (pc_spotify.get("me/tracks", limit=1) or {}).get("total") or 0
            elif "playlist" in parts:
                pid = parts[parts.index("playlist") + 1]
                total = ((pc_spotify.get(f"playlists/{pid}", fields="tracks.total") or {}).get("tracks") or {}).get("total") or 0
            elif "album" in parts:
                total = (pc_spotify.get(f"albums/{parts[parts.index('album') + 1]}") or {}).get("total_tracks") or 0
            else:
                return None
        except Exception:
            return None
        return random.randrange(total) if total > 1 else None

    # ------------------------------------------------------------ what the window shows
    def status_line(self, running, speaking):
        if speaking:
            return "On the mic right now"
        if self.dj_running() and self.dj is not None and self.dj.STOP.is_set():
            return "Signing off"
        if not running:
            return "Off air"
        left = (getattr(self.dj, "STATUS", {}) or {}).get("songs_left")
        if left is None:
            return "Live"
        if left <= 1:
            return "Back after this song"
        return f"Next break in {left} songs"

    def state(self):
        running = self.dj_running()
        dj = self.dj
        speaking = bool(running and dj is not None and (getattr(dj, "STATUS", {}) or {}).get("speaking"))
        station, station_full = "Non Stop Pop", "Non Stop Pop FM"
        b = getattr(dj, "brain", None) if dj is not None else None
        if b is not None and self.connected:
            try:
                station, station_full = b.STATION.name(), b.STATION.full()
            except Exception:
                pass
        n = self.now if self.connected else None
        t = (n or {}).get("track")
        now = None
        if n is not None:
            now = dict(n)
            now["liked"] = self.liked.get(t["uri"]) if t else None
            now["contextName"] = self.context_name
        with LOG.lock:
            line, kind, duo, count = LOG.line, LOG.kind, list(LOG.duo), LOG.count
        return {
            "connected": self.connected,
            "connecting": self.connecting,
            "problem": self.problem,
            "hint": self.hint,
            "keys": bool(self.cfg["spotify_client_id"] and self.cfg["spotify_client_secret"]),
            "me": pc_spotify._me["value"] if self.connected else None,
            "now": now,
            "dj": {
                "running": running,
                "stopping": bool(running and dj is not None and dj.STOP.is_set()),
                "speaking": speaking,
                "line": line, "kind": kind, "duo": duo,
                "status": self.status_line(running, speaking),
                "station": station, "stationFull": station_full,
                "queued": self.queued if running else None,
            },
            "sleep": {"at": int(self.sleep_at * 1000) if self.sleep_at else None, "endOfSong": self.sleep_end_of_song},
            "player": dict(self.builtin.info(), mode=self.cfg.get("player", "app")),
            "output": pc_audio.output_name(),          # Windows' playback device: where this app's music and Cara go
            "notice": self.notice,
            "slow": int(pc_spotify.limited()),         # seconds until the app may ask Spotify again (it said "slow down")
            "logCount": count,
            "serverTime": int(time.time() * 1000),
        }

    def on_close(self):
        try:
            if self.dj is not None:
                self.dj.STOP.set()
        except Exception:
            pass
        try:
            self.builtin.stop()
        except Exception:
            pass


# ---------------------------------------------------------------- what the window can ask for
def guard(fn):
    """Every call from the window answers with data, never an exception (which would leave the window waiting)."""
    def wrapped(*a, **kw):
        try:
            return fn(*a, **kw)
        except Exception as e:
            print(f"[{fn.__name__} failed: {e}]")
            return {"error": str(e)}
    wrapped.__name__ = fn.__name__
    return wrapped


class Api:
    def __init__(self, app):
        self._app = app

    # the app
    @guard
    def boot(self):
        first, pc = who_is_here()
        return {"config": self._app.public_config(), "first": first, "pc": pc, "version": VERSION, "excluded": taste_excluded(),
                "redirect": REDIRECT_URI, "genres": pc_spotify.GENRES}

    @guard
    def state(self):
        return self._app.state()

    @guard
    def log(self, since=0):
        return LOG.since(int(since or 0))

    @guard
    def set_config(self, patch):
        return self._app.update_config(patch)

    @guard
    def connect(self, force=False):
        self._app.connect(bool(force))
        return True

    @guard
    def logout(self):
        self._app.logout()
        return True

    @guard
    def open_url(self, url):
        if isinstance(url, str) and url.startswith(("https://", "http://", "spotify:")):
            webbrowser.open(url)
        return True

    @guard
    def open_in_spotify(self, uri):
        """Opens a song in the Spotify app when it's installed, otherwise on open.spotify.com. Says which ("app"/"web")."""
        m = re.fullmatch(r"spotify:(track|album|playlist|artist):([A-Za-z0-9]{10,40})", uri or "")
        if not m:
            return "That isn't something Spotify can open."
        if spotify_app_installed():
            try:
                os.startfile(uri)                      # the Spotify app (it owns spotify: links)
                return "app"
            except OSError:
                pass
        webbrowser.open(f"https://open.spotify.com/{m.group(1)}/{m.group(2)}")
        return "web"

    @guard
    def join_jam(self, link):
        """A Jam link a friend sent: Jams are joined in Spotify itself, so it opens there."""
        link = (link or "").strip()
        ok = re.match(r"https://(spotify\.link|spotify\.app\.link|open\.spotify\.com)/\S+$", link) or re.match(r"spotify:\S+$", link)
        if not ok:
            return "That doesn't look like a Spotify Jam link. It starts with https://spotify.link/ or https://open.spotify.com/."
        if link.startswith("spotify:"):
            if not spotify_app_installed():
                return "The Spotify app isn't installed on this PC, so that link can't open."
            try:
                os.startfile(link)
                return True
            except OSError:
                return "The Spotify app couldn't open that link."
        webbrowser.open(link)
        print(f"[jam: opened {link[:60]} in Spotify]")
        return True

    @guard
    def taste_exclude(self, title, artist, on):
        """Exclude from your taste profile (or put back): Cara and Scratch stop reading anything into that song."""
        key = f"{title or ''}|{artist or ''}".lower()
        keys = set(taste_excluded())
        if on:
            keys.add(key)
        else:
            keys.discard(key)
        with open(os.path.join(APP_DIR, "taste-excluded.json"), "w", encoding="utf-8") as f:
            json.dump(sorted(keys)[-2000:], f)
        print(f"[taste profile: {title} {'excluded' if on else 'back in'}]")
        return True

    @guard
    def now_extras(self, t):
        return pc_spotify.now_extras(t)

    @guard
    def play_named(self, title, artist):
        """A song by name (a related music video): the best match on Spotify plays."""
        found = []
        for q in (f'track:"{title}" artist:"{artist}"', f"{title} {artist}"):
            j = pc_spotify.safe(lambda: pc_spotify.get("search", q=q, type="track", limit=5)) or {}
            found = [t for t in (pc_spotify.track(x) for x in (j.get("tracks") or {}).get("items") or []) if t and not t["local"]]
            if found:
                break
        if not found:
            return "Couldn't find that song on Spotify."
        return self._app.play(None, None, [found[0]["uri"]], None, None)

    @guard
    def quit(self):
        """File > Exit."""
        w = self._app.window
        if w is not None:
            threading.Timer(0.1, w.destroy).start()
        return True

    @guard
    def song_radio(self, tid):
        return pc_spotify.song_radio(tid)

    @guard
    def credits(self, tid):
        return pc_spotify.credits(tid)

    @guard
    def open_stingers(self):
        folder = my_stingers_folder()
        os.makedirs(folder, exist_ok=True)
        open_path(folder)
        return folder

    @guard
    def restinger(self):
        """Re-record stingers: the station voice reads every line again (new takes, or a new voice)."""
        pc_stingers.MAKER.clear_all()
        print("[station stingers: starting over, new takes on the way]")
        return True

    @guard
    def open_spotify(self):
        try:
            webbrowser.open("spotify:")
        except Exception:
            pass
        return True

    # Cara
    @guard
    def dj_toggle(self):
        return self._app.toggle_dj()

    @guard
    def dj_test(self, what):
        return self._app.test(what)

    @guard
    def dj_queue(self, style):
        return self._app.queue_next(style)

    # the player
    @guard
    def player(self, action, value=None):
        return self._app.player(action, value)

    @guard
    def play(self, context=None, offset_uri=None, uris=None, position=None, shuffle=None):
        return self._app.play(context, offset_uri, uris, position, shuffle)

    @guard
    def queue_add(self, uri):
        return self._app.queue_add(uri)

    @guard
    def queue_remove(self, uri, nth=0, section="next"):
        return self._app.queue_remove(uri, int(nth or 0), "queued" if section == "queued" else "next")

    @guard
    def queue(self):
        return self._app.queue_view()

    @guard
    def devices(self):
        return pc_spotify.devices()

    @guard
    def player_retry(self):
        threading.Thread(target=self._app.builtin.retry, daemon=True).start()
        return True

    @guard
    def play_here(self):
        return self._app.play_here()

    @guard
    def use_builtin(self):
        if self._app.builtin.status != "ready":
            return False
        return self._app.use_builtin(force=True)

    @guard
    def transfer(self, device_id):
        ok = pc_spotify.safe(lambda: pc_spotify.put("me/player", payload={"device_ids": [device_id], "play": True}) or True, False)
        threading.Timer(0.8, self._app.poke.set).start()
        return bool(ok)

    @guard
    def up_next(self):
        return self._app.up_next()

    @guard
    def sleep(self, minutes=None):
        self._app.set_sleep(minutes)
        return True

    # your library and pages
    @guard
    def home(self, force=False):
        if not self._app.connected:
            return None
        return pc_spotify.home(bool(force))

    @guard
    def home_feed(self, force=False):
        """Home's sections beyond your library: Daily Mixes, top mixes, stations, new releases, More like..."""
        if not self._app.connected:
            return None
        return pc_spotify.home_feed(bool(force))

    @guard
    def mix(self, mid):
        return pc_spotify.mix(str(mid or ""))

    @guard
    def more(self, kind, offset=0, after=None):
        if kind == "playlists":
            return pc_spotify.my_playlists(int(offset))
        if kind == "liked":
            return pc_spotify.liked(int(offset))
        if kind == "albums":
            return pc_spotify.saved_albums(int(offset))
        if kind == "artists":
            return pc_spotify.followed_artists(after)
        return None

    @guard
    def album(self, aid):
        return pc_spotify.album_page(aid)

    @guard
    def artist(self, aid):
        return pc_spotify.artist_page(aid)

    @guard
    def playlist(self, pid):
        return pc_spotify.playlist_page(pid)

    @guard
    def playlist_more(self, pid, offset):
        return pc_spotify.playlist_more(pid, int(offset))

    @guard
    def search(self, q, types="track,artist,album,playlist", offset=0):
        q = (q or "").strip()
        return pc_spotify.search(q, types, int(offset)) if q else None

    @guard
    def remember_search(self, q):
        q = (q or "").strip()
        if q:
            items = [x for x in self._app.cfg.get("recent_searches") or [] if x.lower() != q.lower()]
            self._app.cfg["recent_searches"] = ([q] + items)[:12]
            save_config(self._app.cfg)
        return self._app.cfg["recent_searches"]

    @guard
    def clear_searches(self):
        self._app.cfg["recent_searches"] = []
        save_config(self._app.cfg)
        return []

    @guard
    def genre(self, gid):
        return pc_spotify.genre_page(gid)

    @guard
    def contains(self, uris):
        return pc_spotify.contains(list(uris or []))

    @guard
    def set_saved(self, uri, on):
        ok = pc_spotify.set_saved(uri, bool(on))
        if ok and uri.startswith("spotify:track:"):
            self._app.liked[uri] = bool(on)
        return ok

    @guard
    def create_playlist(self, name):
        return pc_spotify.create_playlist(name)

    @guard
    def add_to_playlist(self, pid, uri):
        return pc_spotify.add_to_playlist(pid, [uri])

    @guard
    def all_tracks(self, kind, xid=""):
        return pc_spotify.all_tracks(kind, xid)

    @guard
    def queue_many(self, kind, xid):
        """Adds a whole album, playlist or mix to the queue (its first 100 songs). How many went in."""
        n = 0
        for t in pc_spotify.tracks_of(kind, xid)[:100]:
            if t.get("uri") and self._app.queue_add(t["uri"]):
                n += 1
        return n

    @guard
    def add_many_to_playlist(self, pid, kind, xid):
        """Adds every song of an album, playlist or mix to one of your playlists. How many went in."""
        uris = [t["uri"] for t in pc_spotify.tracks_of(kind, xid) if t.get("uri") and not t.get("local")]
        for i in range(0, len(uris), 100):
            if not pc_spotify.add_to_playlist(pid, uris[i:i + 100]):
                return i
        return len(uris)

    @guard
    def save_mix(self, mid):
        """One of the app's mixes, saved as a playlist of your own."""
        m = pc_spotify.mix(mid)
        if not m or not m.get("tracks"):
            return None
        p = pc_spotify.create_playlist(m["name"])
        if not p:
            return None
        uris = [t["uri"] for t in m["tracks"]]
        pc_spotify.add_to_playlist(p["id"], uris[:100])
        return p

    @guard
    def edit_playlist(self, pid, name):
        return pc_spotify.edit_playlist(pid, (name or "").strip()[:100])

    @guard
    def lyrics(self, title, artist, album="", duration=0):
        return pc_spotify.lyrics(title, artist, album, duration)

    @guard
    def about(self, track):
        return pc_spotify.about(track)

    # MilkDrop presets you add
    @staticmethod
    def _preset_path(rel):
        full = os.path.realpath(os.path.join(PRESETS_DIR, rel or ""))
        if not full.startswith(os.path.realpath(PRESETS_DIR) + os.sep):
            raise ValueError("that file isn't in the presets folder")
        return full

    @staticmethod
    def _cache_file(rel):
        import hashlib
        return os.path.join(PRESET_CACHE, hashlib.sha1(rel.replace("\\", "/").lower().encode("utf-8")).hexdigest() + ".json")

    @guard
    def milk_list(self):
        os.makedirs(PRESETS_DIR, exist_ok=True)
        items = []
        for folder, _dirs, files in os.walk(PRESETS_DIR):
            for f in files:
                if not f.lower().endswith((".milk", ".json")):
                    continue
                full = os.path.join(folder, f)
                rel = os.path.relpath(full, PRESETS_DIR)
                mtime = int(os.path.getmtime(full))
                failed = False
                try:
                    with open(self._cache_file(rel), encoding="utf-8") as c:
                        cached = json.load(c)
                    failed = bool(cached.get("error")) and cached.get("mtime") == mtime
                except Exception:
                    pass
                items.append({"path": rel, "name": os.path.splitext(f)[0], "mtime": mtime, "failed": failed})
                if len(items) >= 20000:
                    break
        return {"folder": PRESETS_DIR, "items": items}

    @guard
    def milk_get(self, rel):
        full = self._preset_path(rel)
        mtime = int(os.path.getmtime(full))
        if full.lower().endswith(".json"):
            with open(full, encoding="utf-8") as f:
                preset = json.load(f)
            if not isinstance(preset, dict) or "baseVals" not in preset:
                return {"error": "that .json isn't a Butterchurn preset"}
            return {"preset": preset, "mtime": mtime}
        try:
            with open(self._cache_file(rel), encoding="utf-8") as c:
                cached = json.load(c)
            if cached.get("mtime") == mtime and cached.get("preset"):
                return {"preset": cached["preset"], "mtime": mtime}
        except Exception:
            pass
        with open(full, encoding="latin-1") as f:          # MilkDrop presets are plain Windows text
            text = f.read()
        if "[preset00]" not in text:
            return {"error": "that file isn't a MilkDrop preset"}
        return {"text": text, "mtime": mtime}

    @guard
    def milk_save(self, rel, mtime, preset=None, error=""):
        self._preset_path(rel)
        os.makedirs(PRESET_CACHE, exist_ok=True)
        with open(self._cache_file(rel), "w", encoding="utf-8") as c:
            json.dump({"mtime": int(mtime or 0), "preset": preset, "error": error or ""}, c)
        if error:
            print(f"[visualizer: skipped your preset {os.path.basename(rel)} ({error})]")
        return True

    @guard
    def open_presets(self):
        os.makedirs(PRESETS_DIR, exist_ok=True)
        readme = os.path.join(PRESETS_DIR, "README.txt")
        if not os.path.exists(readme):
            with open(readme, "w", encoding="utf-8") as f:
                f.write(PRESETS_README)
        open_path(PRESETS_DIR)
        return PRESETS_DIR

    # the visualizer
    @guard
    def vis_frame(self):
        return pc_audio.LISTENER.frame()

    @guard
    def vis_stop(self):
        pc_audio.LISTENER.stop()
        return True

    @guard
    def titlebar(self, mode="song"):
        """Preferences > Background: the title bar takes the window's own background (song colours, black or white)."""
        set_titlebar(mode if mode in TITLE_COLOURS else "song")
        return True

    @guard
    def fullscreen(self):
        w = self._app.window
        if w is not None:
            w.toggle_fullscreen()
        return True


# ---------------------------------------------------------------- the window
TITLEBAR = {"mode": "song"}
TITLE_COLOURS = {"song": 0x0B080A, "black": 0x000000, "white": 0xEEEAE9}      # the window's background, as 0xBBGGRR


def set_titlebar(mode=None):
    """Ask Windows for a title bar that matches the window's background (best effort; ignored elsewhere)."""
    if mode is not None:
        TITLEBAR["mode"] = mode
    if sys.platform != "win32":
        return
    try:
        import ctypes
        hwnd = ctypes.windll.user32.FindWindowW(None, APP_NAME)
        if not hwnd:
            return
        light = TITLEBAR["mode"] == "white"
        value = ctypes.c_int(0 if light else 1)
        for attr in (20, 19):                      # dark mode: Windows 10 20H1+ and older builds
            ctypes.windll.dwmapi.DwmSetWindowAttribute(hwnd, attr, ctypes.byref(value), ctypes.sizeof(value))
        # Windows 11: the title bar takes the app's own background, so it reads as part of the window
        rgb = TITLE_COLOURS.get(TITLEBAR["mode"], 0x0B080A)
        for attr in (35, 34):                      # caption and border colour
            color = ctypes.c_int(rgb)
            ctypes.windll.dwmapi.DwmSetWindowAttribute(hwnd, attr, ctypes.byref(color), ctypes.sizeof(color))
        text = ctypes.c_int(0x1A1A1A if light else 0xF2F2F2)
        ctypes.windll.dwmapi.DwmSetWindowAttribute(hwnd, 36, ctypes.byref(text), ctypes.sizeof(text))
    except Exception:
        pass


def dark_titlebar():
    set_titlebar()


def wait_for_old_window(folder, limit=5.0):
    """Opened again right after closing? The old window's WebView2 may still be shutting down, and a new window would
    join it (Task Manager then lists it apart from the app). Its lock file is held until it's gone: wait for that."""
    lock = os.path.join(folder, "EBWebView", "lockfile")
    end = time.time() + limit
    while time.time() < end:
        try:
            fd = os.open(lock, os.O_RDWR)
        except FileNotFoundError:
            return
        except PermissionError:
            time.sleep(0.2)                         # still held: the old one is finishing
            continue
        except OSError:
            return
        os.close(fd)
        return


_MUTEX = []


def already_running():
    """True when Non Stop Pop DJ is already open: its window comes to the front instead of a second copy starting
    (two copies would each start a player and fight over it)."""
    if sys.platform != "win32":
        return False
    try:
        import ctypes
        k32 = ctypes.WinDLL("kernel32", use_last_error=True)
        k32.CreateMutexW.restype = ctypes.c_void_p
        k32.CreateMutexW.argtypes = [ctypes.c_void_p, ctypes.c_bool, ctypes.c_wchar_p]
        handle = k32.CreateMutexW(None, False, "Local\\NonStopPopDJ.one")
        if ctypes.get_last_error() != 183:          # ERROR_ALREADY_EXISTS
            _MUTEX.append(handle)                   # held for as long as this copy runs
            return False
        user32 = ctypes.windll.user32
        hwnd = user32.FindWindowW(None, APP_NAME)
        if hwnd:
            user32.ShowWindow(hwnd, 9)              # SW_RESTORE
            user32.SetForegroundWindow(hwnd)
        return True
    except Exception:
        return False


def tell(message):
    """A plain message box, for when the window itself can't open."""
    try:
        import tkinter as tk
        from tkinter import messagebox
        root = tk.Tk()
        root.withdraw()
        messagebox.showerror(APP_NAME, message)
        root.destroy()
    except Exception:
        pass


def self_test(path):
    """Used by the build: proves the packaged app has the engine, the brain, the window and every screen, then quits."""
    result = "ok"
    try:
        importlib.import_module("live_dj_free")
        brain = importlib.import_module("brain")
        import edge_tts, feedparser, numpy, requests, spotipy  # noqa: F401
        import pygame.sndarray  # noqa: F401  (mixes Cara and Scratch together)
        import webview  # noqa: F401  (the window)
        if len(brain.SEGMENTS) < 40 or len(brain.D["duoSegments"]) < 16:
            raise RuntimeError("Cara's brain data is incomplete")
        bundled = [n for n in os.listdir(os.path.join(HERE, "my_stingers")) if n.lower().endswith(".mp3")]
        if len(bundled) < 6:
            raise RuntimeError(f"only {len(bundled)} stingers inside the app")
        beds = [n for n in os.listdir(pc_stingers.BEDS) if n.endswith(".ogg")] if os.path.isdir(pc_stingers.BEDS) else []
        if len(beds) < 6:
            raise RuntimeError(f"only {len(beds)} station stinger beds inside the app")
        import pygame
        if pygame.mixer.get_init():           # this build reads the beds and the voice's mp3s, and mixes a stinger
            music, rate = pc_stingers.decode(os.path.join(pc_stingers.BEDS, "stationbed_1.ogg"))
            line, _ = pc_stingers.decode(os.path.join(HERE, "my_stingers", sorted(bundled)[0]))
            if len(music[0]) < rate * 5 or len(line[0]) < rate:
                raise RuntimeError("the station stinger music didn't decode")
            voice = pc_stingers.mono(line)[: rate * 2]
            left, _ = pc_stingers.render(music, [(voice, 0.1, False), (voice[: rate], 4.9, True)], -21.5, rate)
            if len(left) < len(music[0]) or not 0.05 < float(abs(left).max()) <= 0.98:
                raise RuntimeError("the station stinger mix came out wrong")
        else:
            print("[self-test: no sound system on this machine, so the stinger mix wasn't tried]")
        # the visualizer's listener loads soundcard in its own thread, which sets Windows audio up by itself
        # (a runner may have no speakers; what must never happen is that setup failing, "Error 0x100000001")
        listener = pc_audio.LISTENER
        listener.running, listener._used = True, 0
        probe = threading.Thread(target=listener._run, daemon=True)
        probe.start()
        probe.join(30)
        if "0x100000001" in (listener.error or ""):
            raise RuntimeError(f"the visualizer couldn't set up Windows audio: {listener.error}")
        import soundcard  # noqa: F401  (the visualizer listens to the speakers)
        if sys.platform == "win32":
            import clr  # noqa: F401  (pythonnet: the window's bridge to Windows)
            from webview.platforms import winforms  # noqa: F401  (the Edge WebView2 window and its DLLs)
        for name in ("index.html", "style.css", "glass.css", "app.js", "icons.js", "vis.js", "player.html", "vendor/butterchurn.min.js",
                     "vendor/milk-utils.min.js", "vendor/hlslparser.js", "presets/pack.json", "presets/images.json"):
            if not os.path.exists(os.path.join(HERE, "ui", name)):
                raise RuntimeError(f"the screen file ui/{name} is missing")
        with open(os.path.join(HERE, "ui", "presets", "pack.json"), encoding="utf-8") as f:
            if len(json.load(f)) < 400:
                raise RuntimeError("the MilkDrop preset pack is incomplete")
        app = App()
        api = Api(app)
        # the built-in player: Edge (or Chrome) is there, and the page's little server answers (no browser is opened)
        if sys.platform == "win32" and not pc_player.find_browser()[1]:
            raise RuntimeError("couldn't find Chrome or Edge for the built-in player")
        with open(os.path.join(HERE, "ui", "player.html"), encoding="utf-8") as f:   # how its window is recognised
            if f"<title>{pc_player.PAGE_TITLE}</title>" not in f.read():
                raise RuntimeError("the player page's title doesn't match what the app looks for")
        import urllib.request
        page = app.builtin._serve()
        ping = page.replace("/player?", "/ping?")
        with urllib.request.urlopen(urllib.request.Request(ping, headers={"Host": f"127.0.0.1:{app.builtin.port}"}), timeout=10) as r:
            if json.loads(r.read()).get("name") != pc_player.NAME:
                raise RuntimeError("the built-in player's page server didn't answer")
        s = api.state()
        if "error" in s or "dj" not in s:
            raise RuntimeError(f"state() failed: {s}")
        b = api.boot()
        if "error" in b or len(b.get("genres") or []) < 10:
            raise RuntimeError(f"boot() failed: {b}")
    except Exception as e:
        import traceback
        result = "fail: " + repr(e) + " | " + traceback.format_exc()[-600:].replace("\n", " / ")
    with open(path, "w", encoding="utf-8") as f:
        f.write(result)


def main():
    sys.stdout = sys.stderr = LOG      # a windowed .exe has no console, so catch prints for Activity
    if os.environ.get("NSP_SELFTEST"):
        self_test(os.environ["NSP_SELFTEST"])
        return
    if already_running():
        return
    try:
        import webview
    except Exception as e:
        tell(f"The window couldn't start ({e}).")
        return
    app = App()
    api = Api(app)
    wait_for_old_window(os.path.join(APP_DIR, "webview"))
    bg = app.cfg.get("ui_bg") if app.cfg.get("ui_bg") in TITLE_COLOURS else "song"
    TITLEBAR["mode"] = bg
    window = webview.create_window(
        APP_NAME, url=os.path.join(HERE, "ui", "index.html"), js_api=api, width=1440, height=900, min_size=(1080, 680),
        background_color={"white": "#E9EAEE", "black": "#000000"}.get(bg, "#0A080B"), text_select=False,
    )
    app.window = window
    window.events.closed += app.on_close
    window.events.shown += dark_titlebar
    try:
        webview.start(gui="edgechromium", private_mode=False, storage_path=os.path.join(APP_DIR, "webview"),
                      http_server=True, debug=bool(os.environ.get("NSP_DEBUG")))
    except Exception as e:
        tell("Non Stop Pop DJ draws its window with Microsoft Edge WebView2, which this PC doesn't seem to have.\n\n"
             "Install the free \"WebView2 Runtime\" from Microsoft (search for it, or go to "
             "developer.microsoft.com/microsoft-edge/webview2), then open the app again.\n\n"
             f"({e})")
    app.on_close()


if __name__ == "__main__":
    main()

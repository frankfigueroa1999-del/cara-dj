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
    "player": "app",            # where music plays: "app" (the built-in player) or "spotify" (the Spotify app, any device)
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
        pc_spotify.sp = self.dj.sp
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
            threading.Thread(target=self.use_builtin, daemon=True).start()
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
            self.poke.wait(1.5)
            self.poke.clear()
            if not (self.connected and self.dj is not None):
                continue
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
                self.now = pb or {}
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
                if t["uri"] and t["uri"] not in self.liked:
                    got = pc_spotify.contains([t["uri"]])
                    if got:
                        self.liked[t["uri"]] = bool(got[0])
            self.now = pb

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
        dev = (n.get("device") or {}).get("id")
        q = {"device_id": dev} if dev else {}
        b = self.builtin
        if (dev and dev == b.device_id and b.status == "ready"
                and action in ("toggle", "play", "pause", "next", "previous", "seek", "volume")):
            try:                                        # playing in this app: straight to the player, no round trip
                got = b.control(action, value)
                if action == "toggle" and self.now and got in ("playing", "paused"):
                    self.now["playing"], self.now["stamp"] = got == "playing", int(time.time() * 1000)
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
            "shuffle": lambda: pc_spotify.put("me/player/shuffle", state="true" if value else "false"),
            "repeat": lambda: pc_spotify.put("me/player/repeat", state=value or "off"),
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
        if shuffle is not None:
            pc_spotify.safe(lambda: pc_spotify.put("me/player/shuffle", state="true" if shuffle else "false", device_id=dev))
        ok = pc_spotify.safe(lambda: pc_spotify.put("me/player/play", payload=body, device_id=dev) or True, False)
        threading.Timer(0.5, self.poke.set).start()
        return "ok" if ok else "Spotify wouldn't play that (it may not be available in your country)."

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
        return {"config": self._app.public_config(), "first": first, "pc": pc, "version": VERSION,
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
        return bool(pc_spotify.safe(lambda: pc_spotify.post("me/player/queue", uri=uri) or True, False))

    @guard
    def devices(self):
        return pc_spotify.devices()

    @guard
    def player_retry(self):
        threading.Thread(target=self._app.builtin.retry, daemon=True).start()
        return True

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
        return pc_spotify.up_next()

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
    def fullscreen(self):
        w = self._app.window
        if w is not None:
            w.toggle_fullscreen()
        return True


# ---------------------------------------------------------------- the window
def dark_titlebar():
    """Ask Windows for a dark title bar (best effort; ignored elsewhere)."""
    if sys.platform != "win32":
        return
    try:
        import ctypes
        hwnd = ctypes.windll.user32.FindWindowW(None, APP_NAME)
        value = ctypes.c_int(1)
        for attr in (20, 19):                      # Windows 10 20H1+ and older builds
            ctypes.windll.dwmapi.DwmSetWindowAttribute(hwnd, attr, ctypes.byref(value), ctypes.sizeof(value))
    except Exception:
        pass


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
        for name in ("index.html", "style.css", "app.js", "icons.js", "vis.js", "player.html", "vendor/butterchurn.min.js",
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
    try:
        import webview
    except Exception as e:
        tell(f"The window couldn't start ({e}).")
        return
    app = App()
    api = Api(app)
    window = webview.create_window(
        APP_NAME, url=os.path.join(HERE, "ui", "index.html"), js_api=api,
        width=1360, height=880, min_size=(1040, 680), background_color="#09090D", text_select=False,
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

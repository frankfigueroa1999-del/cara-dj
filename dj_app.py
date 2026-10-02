"""
Non Stop Pop DJ: a small desktop app around the live DJ engine (live_dj_free.py).

Shows what's playing in Spotify, lets you play/pause/skip, and starts/stops the DJ.
Your keys are typed once in Settings and saved on this PC.
"""
import getpass
import importlib
import json
import os
import queue
import socket
import sys
import threading
import time
import tkinter as tk
from tkinter import messagebox, ttk

os.environ.setdefault("PYGAME_HIDE_SUPPORT_PROMPT", "1")

APP_NAME = "Non Stop Pop DJ"
APP_DIR = os.path.join(os.environ.get("APPDATA") or os.path.expanduser("~"), "NonStopPopDJ")
os.makedirs(APP_DIR, exist_ok=True)
CONFIG_PATH = os.path.join(APP_DIR, "config.json")
CACHE_PATH = os.path.join(APP_DIR, "spotify_token.cache")
REDIRECT_URI = "http://127.0.0.1:8888/callback"


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
    "mood": "normal",
    "chattiness": "chatty",
    "cohost_enabled": True,
    "cohost_chance": 40,
    "cohost_swears": True,
    "cohost_voice": "",
}
TRANSITION_WEIGHTS = {"talkover": 4, "intro": 3, "silent": 3, "fadeout": 2}

# Minimal, monochrome, utilitarian, dark: plain type, hairlines, no color, no rounded corners
BG, FG, MUTED, LINE, WHITE, EDGE = "#0b0b0b", "#f2f2f0", "#8a8a86", "#2b2b29", "#151515", "#6b6b68"
FONT, MONO = "Arial", "Consolas"

log_q = queue.Queue()


class LogWriter:
    """Catches everything the DJ engine prints so it can be shown in the window."""

    def write(self, s):
        if s and s.strip():
            log_q.put(s.rstrip())
        return len(s) if s else 0

    def flush(self):
        pass

    def isatty(self):
        return False


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


def fmt_time(ms):
    s = max(0, int(ms // 1000))
    return f"{s // 60}:{s % 60:02d}"


class Slider(tk.Canvas):
    """A thin monochrome 0-100 slider: hairline track, square handle."""

    def __init__(self, parent, **kw):
        super().__init__(parent, height=16, bg=BG, highlightthickness=0, bd=0, cursor="hand2", **kw)
        self._v = 60.0
        self.bind("<Configure>", lambda e: self._draw())
        self.bind("<Button-1>", self._click)
        self.bind("<B1-Motion>", self._click)

    def set(self, v):
        self._v = max(0.0, min(100.0, float(v)))
        self._draw()

    def get(self):
        return self._v

    def _click(self, e):
        w = max(1, self.winfo_width() - 12)
        self._v = max(0.0, min(100.0, (e.x - 6) / w * 100))
        self._draw()

    def _draw(self):
        self.delete("all")
        w = max(1, self.winfo_width())
        x = 6 + (w - 12) * self._v / 100
        self.create_rectangle(0, 7, w, 9, fill=LINE, width=0)
        self.create_rectangle(0, 7, x, 9, fill=FG, width=0)
        self.create_rectangle(x - 5, 3, x + 5, 13, fill=FG, width=0)


def dark_titlebar(win):
    """Ask Windows for a dark title bar (best effort; ignored on other systems)."""
    if sys.platform != "win32":
        return
    try:
        import ctypes
        win.update_idletasks()
        hwnd = ctypes.windll.user32.GetParent(win.winfo_id())
        value = ctypes.c_int(1)
        for attr in (20, 19):                      # Windows 10 20H1+ and older builds
            ctypes.windll.dwmapi.DwmSetWindowAttribute(hwnd, attr, ctypes.byref(value), ctypes.sizeof(value))
    except Exception:
        pass


def _mix(c1, c2, t):
    """Blend two #rrggbb colours (t=0 gives c1, t=1 gives c2)."""
    a = [int(c1[i:i + 2], 16) for i in (1, 3, 5)]
    b = [int(c2[i:i + 2], 16) for i in (1, 3, 5)]
    return "#%02x%02x%02x" % tuple(round(x + (y - x) * t) for x, y in zip(a, b))


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


class Welcome:
    """First start-up: the window blurs, greets you by name, then walks you through the keys."""

    STEPS = [
        ("Spotify", "Make a free app at developer.spotify.com, then paste its two codes here.",
         [("CLIENT ID", "spotify_client_id", False), ("CLIENT SECRET", "spotify_client_secret", True)], True),
        ("Her voice", "Your ElevenLabs key and the ID of your cloned voice. You can add these later.",
         [("API KEY", "elevenlabs_api_key", True), ("VOICE ID", "elevenlabs_voice_id", False)], False),
        ("Her words", "Your Gemini key writes what Cara says. You can add this later too.",
         [("GEMINI API KEY", "gemini_api_key", True)], False),
        ("Your town", "So Cara can talk about the weather and the news where you are.",
         [("TOWN", "city", False)], False),
    ]

    def __init__(self, app):
        self.app = app
        self.root = app.root
        self.alive = True
        self.step = 0
        self.entries = {}
        self.frames = []
        self.shot = None
        self.root.update()
        self._prepare_blur()
        self.cover = tk.Label(self.root, bg=BG, bd=0, highlightthickness=0)
        self.cover.place(x=0, y=0, relwidth=1, relheight=1)
        self.stage = tk.Frame(self.cover, bg=BG)
        self.stage.place(relx=0.5, rely=0.5, anchor="center")
        self.stage.configure(bg=BG)
        self._blur_in(0)

    # ---- blur
    def _prepare_blur(self):
        try:
            from PIL import ImageGrab, ImageFilter, ImageEnhance, Image, ImageTk
            r = self.root
            x, y, w, h = r.winfo_rootx(), r.winfo_rooty(), r.winfo_width(), r.winfo_height()
            img = ImageGrab.grab(bbox=(x, y, x + w, y + h)).convert("RGB")
            soft = img.filter(ImageFilter.GaussianBlur(14))
            soft = ImageEnhance.Brightness(soft).enhance(0.6)
            self.frames = [ImageTk.PhotoImage(Image.blend(img, soft, t / 7)) for t in range(8)]
        except Exception:
            self.frames = []

    def _blur_in(self, i):
        if not self.frames:
            self.root.after(150, self.intro)
            return
        self.cover.configure(image=self.frames[i])
        self.cover.image = self.frames[i]
        if i < len(self.frames) - 1:
            self.root.after(45, lambda: self._blur_in(i + 1))
        else:
            self.root.after(250, self.intro)

    def _blur_out(self, i, done):
        if not self.frames:
            done()
            return
        self.cover.configure(image=self.frames[i])
        self.cover.image = self.frames[i]
        if i > 0:
            self.root.after(45, lambda: self._blur_out(i - 1, done))
        else:
            done()

    # ---- text helpers (fade by blending the text colour into the background)
    def _bg(self):
        return BG

    def _fade(self, widgets, start, end, ms, done=None):
        steps = max(1, ms // 28)
        def tick(n=0):
            if not self.alive:
                return
            t = start + (end - start) * min(1.0, n / steps)
            for w, col in widgets:
                try:
                    w.configure(fg=_mix(self._bg(), col, t))
                except tk.TclError:
                    return
            if n < steps:
                self.root.after(28, lambda: tick(n + 1))
            elif done:
                done()
        tick()

    def _clear(self):
        for w in self.stage.winfo_children():
            w.destroy()

    def _say(self, big, small, hold, then):
        """Show a line of text, fade it in, hold it, fade it out, then carry on."""
        self._clear()
        a = tk.Label(self.stage, text=big, bg=BG, fg=BG, font=(FONT, 22, "bold"), wraplength=400, justify="center")
        a.pack()
        items = [(a, FG)]
        if small:
            b = tk.Label(self.stage, text=small, bg=BG, fg=BG, font=(FONT, 9), wraplength=400, justify="center")
            b.pack(pady=(12, 0))
            items.append((b, MUTED))
        self._fade(items, 0, 1, 700,
                   lambda: self.root.after(hold, lambda: self._fade(items, 1, 0, 600, then)))

    # ---- the flow
    def intro(self):
        first, pc = who_is_here()
        line2 = f"on {pc}" if pc else ""
        self._say(f"Welcome, {first},\nto the DJ app.", line2, 1700,
                  lambda: self._say("I'll help you set up.", "", 1100, self.show_step))

    def show_step(self):
        self._clear()
        title, hint, fields, required = self.STEPS[self.step]
        n = len(self.STEPS)
        items = []
        def lab(text, size, col, bold=False, pady=(0, 0), wrap=380):
            w = tk.Label(self.stage, text=text, bg=BG, fg=BG, font=(FONT, size, "bold" if bold else "normal"),
                         wraplength=wrap, justify="left" if size < 12 else "center", anchor="w")
            w.pack(fill="x", pady=pady)
            items.append((w, col))
            return w
        lab(f"{self.step + 1} OF {n}", 8, MUTED)
        lab(title, 20, FG, True, (4, 0))
        lab(hint, 9, MUTED, False, (6, 6))
        if self.step == 0:
            lab(f"Redirect URI to enter in that Spotify app:  {REDIRECT_URI}", 8, MUTED, False, (4, 0))
        self.entries = {}
        for label, key, secret in fields:
            lab(label, 8, MUTED, False, (14, 4))
            e = tk.Entry(self.stage, show="*" if secret else "", bg=WHITE, fg=FG, insertbackground=FG, relief="flat",
                         highlightthickness=1, highlightbackground=EDGE, highlightcolor=FG, bd=0, font=(FONT, 11), width=38)
            e.insert(0, str(self.app.cfg.get(key, "")))
            e.pack(fill="x", ipady=6)
            e.bind("<Return>", lambda _e: self.next())
            self.entries[key] = e
        self.msg = lab("", 8, FG, False, (10, 0))
        row = tk.Frame(self.stage, bg=BG)
        row.pack(fill="x", pady=(18, 0))
        last = self.step == n - 1
        tk.Button(row, text="FINISH" if last else "NEXT", command=self.next, bg=FG, fg=BG, activebackground=FG,
                  activeforeground=BG, relief="flat", bd=0, padx=26, pady=9, font=(FONT, 9, "bold"),
                  cursor="hand2").pack(side="left")
        if self.step > 0:
            back = tk.Button(row, text="BACK", command=self.back, bg=BG, fg=MUTED, activebackground=BG,
                             activeforeground=FG, relief="flat", bd=0, padx=16, pady=9, font=(FONT, 9, "bold"),
                             cursor="hand2")
            back.pack(side="left", padx=(8, 0))
        self._fade(items, 0, 1, 450)
        first = next(iter(self.entries.values()))
        first.focus_set()

    def _swap(self, fn):
        items = [(w, FG) for w in self.stage.winfo_children() if isinstance(w, tk.Label)]
        self._fade(items, 1, 0, 220, fn)

    def next(self):
        title, hint, fields, required = self.STEPS[self.step]
        for key, e in self.entries.items():
            self.app.cfg[key] = e.get().strip()
        if required and not all(self.app.cfg[k] for _l, k, _s in fields):
            self.msg.configure(text="Both of these are needed so Cara can reach your Spotify.", fg=FG)
            return
        if self.step < len(self.STEPS) - 1:
            self.step += 1
            self._swap(self.show_step)
        else:
            save_config(self.app.cfg)
            self._swap(self.connect)

    def back(self):
        for key, e in self.entries.items():
            self.app.cfg[key] = e.get().strip()
        self.step = max(0, self.step - 1)
        self._swap(self.show_step)

    def connect(self):
        self._clear()
        lbl = tk.Label(self.stage, text="Connecting to Spotify...", bg=BG, fg=BG, font=(FONT, 14, "bold"))
        lbl.pack()
        sub = tk.Label(self.stage, text="A browser tab may open. Click Agree, then come back here.",
                       bg=BG, fg=BG, font=(FONT, 9), wraplength=380, justify="center")
        sub.pack(pady=(10, 0))
        self._fade([(lbl, FG), (sub, MUTED)], 0, 1, 500)
        self.app.connected = False
        self.app.connect()
        started = time.time()

        def check():
            if not self.alive:
                return
            if self.app.connected:
                self._say("Thank you.\nEnjoy the DJ app.", "", 1500, self.finish)
            elif not self.app.connecting and time.time() - started > 1.5:
                self._failed()
            else:
                self.root.after(400, check)
        self.root.after(1200, check)

    def _failed(self):
        self._clear()
        a = tk.Label(self.stage, text="That didn't connect.", bg=BG, fg=BG, font=(FONT, 16, "bold"))
        a.pack()
        b = tk.Label(self.stage, text="Check the Spotify Client ID and Secret, and that the redirect URI matches.",
                     bg=BG, fg=BG, font=(FONT, 9), wraplength=380, justify="center")
        b.pack(pady=(10, 16))
        btn = tk.Button(self.stage, text="BACK TO SPOTIFY", command=self._retry, bg=FG, fg=BG, activebackground=FG,
                        activeforeground=BG, relief="flat", bd=0, padx=26, pady=9, font=(FONT, 9, "bold"), cursor="hand2")
        btn.pack()
        self._fade([(a, FG), (b, MUTED)], 0, 1, 450)

    def _retry(self):
        self.step = 0
        self.show_step()

    def finish(self):
        self.alive = False
        self.stage.destroy()
        def done():
            self.cover.destroy()
        self._blur_out(len(self.frames) - 1, done) if self.frames else done()


class App:
    def __init__(self, root):
        self.root = root
        self.cfg = load_config()
        self.dj = None  # the engine module (live_dj_free), loaded when connecting
        self.dj_thread = None
        self.connected = False
        self.connecting = False
        self.now = None  # latest Spotify snapshot
        self.dragging_volume = False
        self.ui_q = queue.Queue()
        self.view = "player"

        root.title(APP_NAME)
        root.configure(bg=BG)
        root.geometry("500x1000")
        root.minsize(460, 740)
        self.build_ui()
        dark_titlebar(root)
        root.protocol("WM_DELETE_WINDOW", self.on_close)

        threading.Thread(target=self.poll_loop, daemon=True).start()
        self.root.after(200, self.pump)

        if self.cfg["spotify_client_id"] and self.cfg["spotify_client_secret"]:
            if not self.cfg.get("welcomed"):
                self.cfg["welcomed"] = True   # already set up: never show the welcome
                save_config(self.cfg)
            self.root.after(400, self.connect)
        else:
            if self.cfg.get("welcomed"):
                self.root.after(400, lambda: self.open_settings(first_run=True))   # seen the welcome already
            else:
                self.cfg["welcomed"] = True   # the welcome only ever plays once
                save_config(self.cfg)
                self.root.after(300, lambda: Welcome(self))

    # ---------------------------------------------------------------- UI
    # small helpers so every element looks the same
    def _label(self, parent, text="", var=None, size=8, color=MUTED, bold=False, italic=False, **kw):
        weight = "bold" if bold else "normal"
        if italic:
            weight += " italic"
        opts = dict(bg=BG, fg=color, font=(FONT, size, weight), anchor="w", justify="left", bd=0)
        opts.update(kw)
        if var is not None:
            return tk.Label(parent, textvariable=var, **opts)
        return tk.Label(parent, text=text, **opts)

    def _link(self, parent, text, command, size=8):
        b = tk.Button(parent, text=text, command=command, bg=BG, fg=FG, activebackground=BG,
                      activeforeground=MUTED, relief="flat", bd=0, highlightthickness=0, cursor="hand2",
                      font=(FONT, size), padx=0, pady=0)
        b.bind("<Enter>", lambda e: b.configure(fg=MUTED))
        b.bind("<Leave>", lambda e: b.configure(fg=FG))
        return b

    def _rule(self, parent, pady=(16, 16)):
        tk.Frame(parent, height=1, bg=LINE).pack(fill="x", pady=pady)

    def _check(self, parent, text, var):
        return tk.Checkbutton(parent, text=text, variable=var, command=self.on_option_change, bg=BG, fg=FG,
                              activebackground=BG, activeforeground=FG, selectcolor=WHITE, bd=0,
                              highlightthickness=0, font=(FONT, 8), anchor="w", cursor="hand2")

    def build_ui(self):
        style = ttk.Style()
        try:
            style.theme_use("clam")
        except tk.TclError:
            pass
        style.configure("TCombobox", fieldbackground=WHITE, background=WHITE, foreground=FG, arrowcolor=FG,
                        bordercolor=EDGE, lightcolor=WHITE, darkcolor=WHITE, selectbackground=WHITE,
                        selectforeground=FG, padding=2)
        style.map("TCombobox", fieldbackground=[("readonly", WHITE)], foreground=[("readonly", FG)],
                  selectbackground=[("readonly", WHITE)], selectforeground=[("readonly", FG)])
        style.configure("Vertical.TScrollbar", background=LINE, troughcolor=BG, bordercolor=BG,
                        arrowcolor=FG, relief="flat")

        outer = tk.Frame(self.root, bg=BG)
        outer.pack(fill="both", expand=True, padx=36, pady=(30, 26))

        # ---- header
        head = tk.Frame(outer, bg=BG)
        head.pack(fill="x")
        self._link(head, "SETTINGS", self.open_settings).pack(side="right")
        self.brand_var = tk.StringVar(value="NON STOP POP")
        self._label(head, var=self.brand_var, size=12, color=FG, bold=True).pack(side="left")
        self.station_var = tk.StringVar(value="")
        self._label(outer, var=self.station_var, size=8).pack(fill="x", pady=(4, 0))

        # ---- navigation between the player, your playlists, and search
        nav = tk.Frame(outer, bg=BG)
        nav.pack(fill="x", pady=(16, 0))
        self.nav_btns = {}
        for key, text in (("player", "PLAYER"), ("playlists", "PLAYLISTS"), ("search", "SEARCH")):
            b = tk.Button(nav, text=text, command=lambda k=key: self.show_view(k), bg=BG, fg=MUTED,
                          activebackground=BG, activeforeground=FG, relief="flat", bd=0, highlightthickness=0,
                          cursor="hand2", font=(FONT, 8, "bold"), padx=0, pady=0)
            b.pack(side="left", padx=(0, 24))
            self.nav_btns[key] = b
        holder = tk.Frame(outer, bg=BG)
        holder.pack(fill="both", expand=True)
        self.views = {k: tk.Frame(holder, bg=BG) for k in ("player", "playlists", "search")}
        pv = self.views["player"]

        self._rule(pv, pady=(18, 18))

        # ---- now playing
        self._label(pv, "NOW PLAYING", size=8).pack(fill="x")
        self.title_var = tk.StringVar(value="Not connected")
        self.artist_var = tk.StringVar(value="")
        self._label(pv, var=self.title_var, size=17, color=FG, wraplength=420).pack(fill="x", pady=(8, 0))
        self._label(pv, var=self.artist_var, size=10, wraplength=420).pack(fill="x", pady=(2, 0))

        self.progress = tk.Canvas(pv, height=2, bg=LINE, highlightthickness=0, bd=0)
        self.progress.pack(fill="x", pady=(18, 4))
        times = tk.Frame(pv, bg=BG)
        times.pack(fill="x")
        self.t_left = tk.StringVar(value="0:00")
        self.t_right = tk.StringVar(value="0:00")
        self._label(times, var=self.t_left, size=8).pack(side="left")
        self._label(times, var=self.t_right, size=8).pack(side="right")

        controls = tk.Frame(pv, bg=BG)
        controls.pack(fill="x", pady=(14, 0))
        for text, cmd in (("PREV", self.do_prev), ("PLAY / PAUSE", self.do_toggle), ("NEXT", self.do_next)):
            self._link(controls, text, cmd, size=9).pack(side="left", padx=(0, 22))

        vol = tk.Frame(pv, bg=BG)
        vol.pack(fill="x", pady=(14, 0))
        self._label(vol, "VOLUME", size=8, width=22).pack(side="left")
        self.vol_scale = Slider(vol)
        self.vol_scale.set(60)
        self.vol_scale.pack(side="left", fill="x", expand=True, padx=(12, 0))
        self.vol_scale.bind("<ButtonPress-1>", lambda e: setattr(self, "dragging_volume", True), add="+")
        self.vol_scale.bind("<ButtonRelease-1>", self.on_volume_release, add="+")

        self._rule(pv, pady=(20, 20))

        # ---- DJ
        self._label(pv, "DJ", size=8).pack(fill="x")
        top = tk.Frame(pv, bg=BG)
        top.pack(fill="x", pady=(10, 0))
        self.dj_button = tk.Button(top, text="START DJ", command=self.toggle_dj, bg=BG, fg=FG,
                                   activebackground=BG, activeforeground=FG, relief="flat", bd=0,
                                   highlightthickness=1, highlightbackground=FG, highlightcolor=FG,
                                   font=(FONT, 9, "bold"), padx=22, pady=8, cursor="hand2")
        self.dj_button.pack(side="left")
        self.status_var = tk.StringVar(value="DJ IS OFF")
        self._label(top, var=self.status_var, size=8).pack(side="left", padx=16)

        self.line_var = tk.StringVar(value="")
        self._label(pv, var=self.line_var, size=9, color=FG, italic=True, wraplength=420).pack(fill="x", pady=(14, 0))

        opts = tk.Frame(pv, bg=BG)
        opts.pack(fill="x", pady=(18, 0))
        self._label(opts, "TALKS EVERY", size=8).pack(side="left")
        self.min_var = tk.IntVar(value=int(self.cfg["break_min"]))
        self.max_var = tk.IntVar(value=int(self.cfg["break_max"]))
        for var in (self.min_var, self.max_var):
            sb = tk.Spinbox(opts, from_=1, to=10, width=3, textvariable=var, command=self.on_option_change,
                            bg=WHITE, fg=FG, buttonbackground=BG, relief="flat", highlightthickness=1,
                            highlightbackground=EDGE, highlightcolor=FG, font=(FONT, 8), bd=0)
            sb.bind("<FocusOut>", lambda e: self.on_option_change())
            sb.bind("<Return>", lambda e: self.on_option_change())
            sb.pack(side="left", padx=8)
            if var is self.min_var:
                self._label(opts, "TO", size=8).pack(side="left")
        self._label(opts, "SONGS (RANDOM)", size=8).pack(side="left")

        row_v = tk.Frame(pv, bg=BG)
        row_v.pack(fill="x", pady=(12, 0))
        self._label(row_v, "VOICE", size=8, width=14).pack(side="left")
        self.engine_var = tk.StringVar(value=self.cfg["tts_engine"])
        combo = ttk.Combobox(row_v, textvariable=self.engine_var, width=12, state="readonly",
                             values=["elevenlabs", "gemini", "kokoro", "edge"])
        combo.pack(side="left")
        combo.bind("<<ComboboxSelected>>", lambda e: self.on_option_change())

        trans = tk.Frame(pv, bg=BG)
        trans.pack(fill="x", pady=(12, 0))
        self._label(trans, "TRANSITIONS", size=8, width=14).pack(side="left")
        self.t_vars = {}
        for key, text in (("talkover", "TALK OVER"), ("intro", "OVER INTRO"),
                          ("silent", "SILENT"), ("fadeout", "FADE OUT")):
            v = tk.BooleanVar(value=bool(self.cfg["t_" + key]))
            self.t_vars[key] = v
            self._check(trans, text, v).pack(side="left", padx=(0, 6))

        brk = tk.Frame(pv, bg=BG)
        brk.pack(fill="x", pady=(10, 0))
        self._label(brk, "BREAKING NEWS", size=8, width=14).pack(side="left")
        self.brk_var = tk.BooleanVar(value=bool(self.cfg["breaking_enabled"]))
        self._check(brk, "RARE, MID-SONG", self.brk_var).pack(side="left", padx=(0, 6))
        self.brk_test_var = tk.BooleanVar(value=bool(self.cfg["breaking_test"]))
        self._check(brk, "TEST MODE", self.brk_test_var).pack(side="left", padx=(0, 10))
        self._link(brk, "TEST NOW", self.test_breaking).pack(side="left")

        pop = tk.Frame(pv, bg=BG)
        pop.pack(fill="x", pady=(10, 0))
        self._label(pop, "POP-IN", size=8, width=14).pack(side="left")
        self.pop_var = tk.BooleanVar(value=bool(self.cfg.get("popin_enabled", True)))
        self._check(pop, "CARA POPS BACK IN", self.pop_var).pack(side="left", padx=(0, 6))
        self.pop_test_var = tk.BooleanVar(value=bool(self.cfg.get("popin_test", False)))
        self._check(pop, "TEST MODE", self.pop_test_var).pack(side="left", padx=(0, 10))
        self._link(pop, "TEST NOW", self.test_popin).pack(side="left")
        pop2 = tk.Frame(pv, bg=BG)
        pop2.pack(fill="x", pady=(6, 0))
        self._label(pop2, "", size=8, width=14).pack(side="left")
        self._label(pop2, "CHANCE %", size=8).pack(side="left", padx=(0, 4))
        self.pop_chance_var = tk.StringVar(value=str(self.cfg.get("popin_chance", 35)))
        tk.Entry(pop2, textvariable=self.pop_chance_var, width=4).pack(side="left", padx=(0, 12))
        self._label(pop2, "SECONDS INTO SONG", size=8).pack(side="left", padx=(0, 4))
        self.pop_sec_var = tk.StringVar(value=str(self.cfg.get("popin_secs", 15)))
        tk.Entry(pop2, textvariable=self.pop_sec_var, width=4).pack(side="left")

        co = tk.Frame(pv, bg=BG)
        co.pack(fill="x", pady=(10, 0))
        self._label(co, "CO-HOST", size=8, width=14).pack(side="left")
        self.co_var = tk.BooleanVar(value=bool(self.cfg.get("cohost_enabled", True)))
        self._check(co, "SCRATCH JOINS IN", self.co_var).pack(side="left", padx=(0, 6))
        self._label(co, "CHANCE %", size=8).pack(side="left", padx=(0, 4))
        self.co_chance_var = tk.StringVar(value=str(self.cfg.get("cohost_chance", 40)))
        co_entry = tk.Entry(co, textvariable=self.co_chance_var, width=4)
        co_entry.pack(side="left", padx=(0, 12))
        co_entry.bind("<FocusOut>", lambda e: self.on_option_change())
        co_entry.bind("<Return>", lambda e: self.on_option_change())
        self.co_swear_var = tk.BooleanVar(value=bool(self.cfg.get("cohost_swears", True)))
        self._check(co, "HE CAN CURSE", self.co_swear_var).pack(side="left", padx=(0, 12))
        self._link(co, "TEST NOW", self.test_duo).pack(side="left")

        tags = tk.Frame(pv, bg=BG)
        tags.pack(fill="x", pady=(10, 0))
        self._label(tags, "STATION TAGS", size=8, width=14).pack(side="left")
        self.sting_var = tk.BooleanVar(value=bool(self.cfg.get("stingers", True)))
        self._check(tags, "IN MY VOICE", self.sting_var).pack(side="left", padx=(0, 10))
        self._link(tags, "TEST NOW", self.test_stinger).pack(side="left")

        # ---- volumes
        def slider_row(text, key, default):
            row = tk.Frame(pv, bg=BG)
            row.pack(fill="x", pady=(12, 0))
            self._label(row, text, size=8, width=22).pack(side="left")
            sl = Slider(row)
            sl.pack(side="left", fill="x", expand=True, padx=(0, 10))
            sl.set(self.cfg.get(key, default))
            val = tk.StringVar(value=f"{int(sl.get())}%")
            self._label(row, var=val, size=8, color=FG, width=5).pack(side="left")
            def released(_e=None, sl=sl, key=key, val=val):
                val.set(f"{int(sl.get())}%")
                self.cfg[key] = int(sl.get())
                self.on_option_change()
            sl.bind("<B1-Motion>", lambda e, sl=sl, val=val: (sl._click(e), val.set(f"{int(sl.get())}%")))
            sl.bind("<ButtonRelease-1>", released)
            return sl, row

        self.djvol_sl, self.djvol_row = slider_row("DJ VOLUME", "dj_volume", 100)
        self.stvol_sl, self.stvol_row = slider_row("STINGER VOLUME", "stinger_volume", 80)
        self.duck_sl, duck_row = slider_row("SONG WHILE SHE TALKS", "duck_percent", 20)
        self.duck_auto_var = tk.BooleanVar(value=bool(self.cfg.get("duck_auto", True)))
        self._check(duck_row, "AUTO", self.duck_auto_var).pack(side="left", padx=(8, 0))
        # park all three right under the main VOLUME slider
        prev = vol
        for r in (self.djvol_row, self.stvol_row, duck_row):
            r.pack_forget()
            r.pack(fill="x", pady=(10, 0), after=prev)
            prev = r

        st2 = tk.Frame(pv, bg=BG)
        st2.pack(fill="x", pady=(12, 0))
        self._label(st2, "STINGERS", size=8, width=14).pack(side="left")
        self._label(st2, "CHANCE %", size=8).pack(side="left", padx=(0, 4))
        self.st_chance_var = tk.StringVar(value=str(self.cfg.get("stinger_chance", 35)))
        tk.Entry(st2, textvariable=self.st_chance_var, width=4).pack(side="left", padx=(0, 14))
        self._link(st2, "OPEN FOLDER", self.open_stinger_folder).pack(side="left")

        # ---- queue next
        qn = tk.Frame(pv, bg=BG)
        qn.pack(fill="x", pady=(14, 0))
        self._label(qn, "QUEUE NEXT", size=8, width=14).pack(side="left")
        self.queue_btns = {}
        for key, text in (("talkover", "TALK OVER"), ("intro", "OVER INTRO"), ("silent", "SILENT"), ("fadeout", "FADE OUT")):
            b = self._link(qn, text, lambda k=key: self.queue_next(k))
            b.pack(side="left", padx=(0, 12))
            self.queue_btns[key] = b
        self.queue_note = tk.StringVar(value="")
        self._label(pv, var=self.queue_note, size=7).pack(fill="x", padx=(0, 0), pady=(4, 0))

        # ---- mood
        md = tk.Frame(pv, bg=BG)
        md.pack(fill="x", pady=(12, 0))
        self._label(md, "DJ MOOD", size=8, width=14).pack(side="left")
        self.mood_btns = {}
        for key in ("chill", "normal", "unhinged", "mixed"):
            b = tk.Button(md, text=key.upper(), command=lambda k=key: self.set_mood(k), bg=BG, fg=MUTED,
                          activebackground=BG, activeforeground=FG, relief="flat", bd=0, highlightthickness=0,
                          cursor="hand2", font=(FONT, 8), padx=0, pady=0)
            b.pack(side="left", padx=(0, 12))
            self.mood_btns[key] = b
        self.set_mood(self.cfg.get("mood", "normal"), save=False)

        tl = tk.Frame(pv, bg=BG)
        tl.pack(fill="x", pady=(12, 0))
        self._label(tl, "TALK LENGTH", size=8, width=14).pack(side="left")
        self.talk_btns = {}
        for key in ("quick", "normal", "chatty"):
            b = tk.Button(tl, text=key.upper(), command=lambda k=key: self.set_chattiness(k), bg=BG, fg=MUTED,
                          activebackground=BG, activeforeground=FG, relief="flat", bd=0, highlightthickness=0,
                          cursor="hand2", font=(FONT, 8), padx=0, pady=0)
            b.pack(side="left", padx=(0, 12))
            self.talk_btns[key] = b
        self.set_chattiness(self.cfg.get("chattiness", "chatty"), save=False)

        self._rule(pv, pady=(20, 14))

        # ---- activity
        self._label(pv, "ACTIVITY", size=8).pack(fill="x", pady=(0, 6))
        logf = tk.Frame(pv, bg=BG)
        logf.pack(fill="both", expand=True)
        self.log = tk.Text(logf, height=7, bg=WHITE, fg=FG, insertbackground=FG, relief="flat", wrap="word",
                           font=(MONO, 8), state="disabled", highlightthickness=1, highlightbackground=LINE,
                           highlightcolor=LINE, bd=0, padx=8, pady=6)
        sb = ttk.Scrollbar(logf, command=self.log.yview)
        self.log.configure(yscrollcommand=sb.set)
        sb.pack(side="right", fill="y")
        self.log.pack(side="left", fill="both", expand=True)

        self._build_browse()
        self.show_view("player")

    # ------------------------------------------------------------ playlists + search (plays through the Spotify app)
    def _mini_strip(self, parent):
        """One line showing what's playing, with basic controls, for the list screens."""
        row = tk.Frame(parent, bg=BG)
        row.pack(fill="x", pady=(14, 0))
        self._label(row, var=self.mini_var, size=8, color=FG).pack(side="left")
        for text, cmd in (("NEXT", self.do_next), ("PLAY / PAUSE", self.do_toggle), ("PREV", self.do_prev)):
            self._link(row, text, cmd, size=8).pack(side="right", padx=(14, 0))

    def _list(self, parent):
        f = tk.Frame(parent, bg=BG)
        f.pack(fill="both", expand=True, pady=(12, 0))
        lb = tk.Listbox(f, bg=WHITE, fg=FG, selectbackground=FG, selectforeground=BG, relief="flat", bd=0,
                        highlightthickness=1, highlightbackground=LINE, highlightcolor=EDGE, activestyle="none",
                        font=(FONT, 10), exportselection=False)
        sb = ttk.Scrollbar(f, command=lb.yview)
        lb.configure(yscrollcommand=sb.set)
        sb.pack(side="right", fill="y")
        lb.pack(side="left", fill="both", expand=True)
        return lb

    def _build_browse(self):
        self.mini_var = tk.StringVar(value="")
        self.pl_items, self.search_items = [], []
        self.pl_loaded = self.pl_loading = False

        pl = self.views["playlists"]
        tk.Frame(pl, height=1, bg=LINE).pack(fill="x", pady=(16, 0))
        self._mini_strip(pl)
        self.pl_status = tk.StringVar(value="")
        self._label(pl, var=self.pl_status, size=8).pack(fill="x", pady=(14, 0))
        self.pl_list = self._list(pl)
        self.pl_list.bind("<Double-Button-1>", lambda e: self.play_selected(self.pl_list, self.pl_items))
        self.pl_list.bind("<Return>", lambda e: self.play_selected(self.pl_list, self.pl_items))
        acts = tk.Frame(pl, bg=BG)
        acts.pack(fill="x", pady=(12, 0))
        self._link(acts, "PLAY SELECTED", lambda: self.play_selected(self.pl_list, self.pl_items), size=8).pack(side="left")
        self._link(acts, "REFRESH", self.load_playlists, size=8).pack(side="left", padx=(20, 0))
        self._label(acts, "DOUBLE-CLICK TO PLAY", size=7).pack(side="right")

        se = self.views["search"]
        tk.Frame(se, height=1, bg=LINE).pack(fill="x", pady=(16, 0))
        self._mini_strip(se)
        row = tk.Frame(se, bg=BG)
        row.pack(fill="x", pady=(14, 0))
        self.search_var = tk.StringVar()
        self.search_entry = tk.Entry(row, textvariable=self.search_var, bg=WHITE, fg=FG, insertbackground=FG,
                                     relief="flat", highlightthickness=1, highlightbackground=EDGE,
                                     highlightcolor=FG, bd=0, font=(FONT, 10))
        self.search_entry.pack(side="left", fill="x", expand=True, ipady=5)
        self.search_entry.bind("<Return>", lambda e: self.run_search())
        self._link(row, "SEARCH", self.run_search, size=8).pack(side="left", padx=(14, 0))
        self.search_status = tk.StringVar(value="ARTISTS, ALBUMS, SINGLES, SONGS. PRESS ENTER")
        self._label(se, var=self.search_status, size=8).pack(fill="x", pady=(12, 0))
        self.search_list = self._list(se)
        self.search_list.bind("<Double-Button-1>", lambda e: self.play_selected(self.search_list, self.search_items))
        self.search_list.bind("<Return>", lambda e: self.play_selected(self.search_list, self.search_items))
        acts2 = tk.Frame(se, bg=BG)
        acts2.pack(fill="x", pady=(12, 0))
        self._link(acts2, "PLAY SELECTED", lambda: self.play_selected(self.search_list, self.search_items), size=8).pack(side="left")
        self._label(acts2, "DOUBLE-CLICK TO PLAY", size=7).pack(side="right")

    def show_view(self, name):
        self.view = name
        for f in self.views.values():
            f.pack_forget()
        self.views[name].pack(fill="both", expand=True)
        for k, b in self.nav_btns.items():
            b.configure(fg=FG if k == name else MUTED, font=(FONT, 8, "bold underline" if k == name else "bold"))
        if name == "playlists":
            self.maybe_load_playlists()
        if name == "search":
            self.search_entry.focus_set()

    def maybe_load_playlists(self):
        if self.connected and not self.pl_loaded and not self.pl_loading:
            self.load_playlists()
        elif not self.connected:
            self.pl_status.set("CONNECT TO SPOTIFY FIRST (SETTINGS)")

    def load_playlists(self):
        if not self.connected or self.dj is None:
            self.pl_status.set("CONNECT TO SPOTIFY FIRST (SETTINGS)")
            return
        self.pl_loading = True
        self.pl_status.set("LOADING...")

        def work():
            items, err = [], ""
            try:
                sp, offset = self.dj.sp, 0
                while offset < 300:
                    page = sp.current_user_playlists(limit=50, offset=offset)
                    for p in page.get("items", []):
                        if p and p.get("uri"):
                            items.append({"label": p.get("name") or "Untitled", "uri": p["uri"], "name": p.get("name") or ""})
                    if not page.get("next"):
                        break
                    offset += 50
            except Exception as e:
                err = str(e)
            self.ui_q.put(lambda: self._fill_playlists(items, err))

        threading.Thread(target=work, daemon=True).start()

    def _fill_playlists(self, items, err):
        self.pl_loading = False
        self.pl_loaded = not err
        self.pl_items = items
        self.pl_list.delete(0, "end")
        for it in items:
            self.pl_list.insert("end", it["label"])
        if err:
            self.pl_status.set("COULDN'T LOAD PLAYLISTS. SPOTIFY MAY NEED YOU TO APPROVE ACCESS AGAIN (SETTINGS > SAVE).")
        else:
            self.pl_status.set("" if items else "NO PLAYLISTS FOUND")

    def run_search(self):
        q = self.search_var.get().strip()
        if not q:
            return
        if not self.connected or self.dj is None:
            self.search_status.set("CONNECT TO SPOTIFY FIRST (SETTINGS)")
            return
        self.search_status.set("SEARCHING...")

        def work():
            items, err = [], ""
            try:
                r = self.dj.sp.search(q=q, type="track,album,artist", limit=10)
                songs, albums, singles, artists = [], [], [], []
                for t in (r.get("tracks") or {}).get("items", []):
                    if t:
                        who = ", ".join(a["name"] for a in t.get("artists", []))
                        songs.append({"label": f"{t['name']}  —  {who}   ·   SONG", "uri": t["uri"]})
                for a in (r.get("albums") or {}).get("items", []):
                    if a:
                        who = ", ".join(x["name"] for x in a.get("artists", []))
                        single = (a.get("album_type") or "").lower() in ("single", "ep")
                        (singles if single else albums).append(
                            {"label": f"{a['name']}  —  {who}   ·   {'SINGLE' if single else 'ALBUM'}", "uri": a["uri"], "name": a["name"]})
                for a in (r.get("artists") or {}).get("items", []):
                    if a:
                        artists.append({"label": f"{a['name']}   ·   ARTIST", "uri": a["uri"], "name": a["name"]})
                items = songs + albums + singles + artists
            except Exception as e:
                err = str(e)
            self.ui_q.put(lambda: self._fill_search(q, items, err))

        threading.Thread(target=work, daemon=True).start()

    def _fill_search(self, q, items, err):
        if q != self.search_var.get().strip():
            return                                    # you've already typed something else
        self.search_items = items
        self.search_list.delete(0, "end")
        for it in items:
            self.search_list.insert("end", it["label"])
        self.search_status.set("SEARCH FAILED. TRY AGAIN." if err else ("" if items else "NO RESULTS"))

    def play_selected(self, lb, items):
        sel = lb.curselection()
        if sel and sel[0] < len(items):
            self.start_uri(items[sel[0]]["uri"], items[sel[0]].get("name"))

    def start_uri(self, uri, name=None):
        """Start a song, album, artist or playlist in the Spotify app on this PC."""
        if not self.connected or self.dj is None:
            return
        is_song = uri.startswith("spotify:track:")
        b = getattr(self.dj, "brain", None)
        if b is not None and name and not is_song:
            b.STATION.remember(uri, name)       # the station takes this name as soon as it starts

        def work():
            sp = self.dj.sp
            kw = {"uris": [uri]} if is_song else {"context_uri": uri}
            try:
                sp.start_playback(**kw)
            except Exception:
                try:                                   # no active device: wake the Spotify app on this PC
                    devices = sp.devices().get("devices", [])
                    pick = next((d for d in devices if d.get("type") == "Computer"), devices[0] if devices else None)
                    if not pick:
                        print("Open the Spotify app on this PC, then try again.")
                        return
                    sp.start_playback(device_id=pick["id"], **kw)
                except Exception as e:
                    print("Spotify wouldn't start that:", e)

        threading.Thread(target=work, daemon=True).start()

    def set_progress(self, fraction):
        c = self.progress
        c.delete("all")
        w = max(1, c.winfo_width())
        c.create_rectangle(0, 0, w * max(0.0, min(1.0, fraction)), 3, fill=FG, width=0)

    # ------------------------------------------------------------ settings
    def open_settings(self, first_run=False):
        win = tk.Toplevel(self.root)
        win.title("Settings")
        win.configure(bg=BG)
        win.geometry("460x700")
        win.transient(self.root)
        dark_titlebar(win)
        fields = [
            ("SPOTIFY CLIENT ID", "spotify_client_id", False),
            ("SPOTIFY CLIENT SECRET", "spotify_client_secret", True),
            ("ELEVENLABS API KEY", "elevenlabs_api_key", True),
            ("ELEVENLABS VOICE ID", "elevenlabs_voice_id", False),
            ("GEMINI API KEY (WRITES THE DJ LINES)", "gemini_api_key", True),
            ("TOWN (LIKE: YAKIMA, WASHINGTON)", "city", False),
            ("SCRATCH'S VOICE ID (OPTIONAL, ELEVENLABS)", "cohost_voice", False),
        ]
        entries = {}
        body = tk.Frame(win, bg=BG)
        body.pack(fill="both", expand=True, padx=32, pady=26)
        self._label(body, "SETTINGS", size=12, color=FG, bold=True).pack(fill="x")
        if first_run:
            self._label(body, "Enter your keys once. They are saved on this PC.", size=9, color=FG,
                        wraplength=390).pack(fill="x", pady=(10, 0))
        self._label(body, f"SPOTIFY REDIRECT URI (SET THIS IN YOUR SPOTIFY APP)\n{REDIRECT_URI}", size=8,
                    wraplength=390).pack(fill="x", pady=(12, 0))
        for label, key, secret in fields:
            self._label(body, label, size=8).pack(fill="x", pady=(14, 4))
            e = tk.Entry(body, show="*" if secret else "", bg=WHITE, fg=FG, insertbackground=FG, relief="flat",
                         highlightthickness=1, highlightbackground=EDGE, highlightcolor=FG, bd=0, font=(FONT, 10))
            e.insert(0, str(self.cfg.get(key, "")))
            e.pack(fill="x", ipady=5)
            entries[key] = e

        def save():
            if self.dj_running():
                messagebox.showinfo(APP_NAME, "Stop the DJ first, then save your settings.", parent=win)
                return
            for key, e in entries.items():
                self.cfg[key] = e.get().strip()
            save_config(self.cfg)
            win.destroy()
            self.connected = False
            self.connect()

        tk.Button(body, text="SAVE", command=save, bg=FG, fg=BG, activebackground=FG, activeforeground=BG,
                  relief="flat", bd=0, padx=26, pady=9, font=(FONT, 9, "bold"), cursor="hand2").pack(anchor="w", pady=(24, 8))
        self._label(body, "Keys are stored as plain text in your user folder. Don't share that folder.",
                    size=7, wraplength=390).pack(fill="x")

    def on_option_change(self):
        try:
            lo, hi = int(self.min_var.get()), int(self.max_var.get())
        except (tk.TclError, ValueError):
            lo, hi = self.cfg["break_min"], self.cfg["break_max"]
        lo, hi = max(1, min(lo, hi)), max(1, max(lo, hi))
        self.cfg["break_min"], self.cfg["break_max"] = lo, hi
        self.cfg["tts_engine"] = self.engine_var.get()
        for key, v in self.t_vars.items():
            self.cfg["t_" + key] = bool(v.get())
        self.cfg["breaking_enabled"] = bool(self.brk_var.get())
        self.cfg["breaking_test"] = bool(self.brk_test_var.get())
        self.cfg["popin_enabled"] = bool(self.pop_var.get())
        self.cfg["popin_test"] = bool(self.pop_test_var.get())
        try:
            self.cfg["popin_chance"] = max(0, min(100, int(self.pop_chance_var.get())))
            self.cfg["popin_secs"] = max(5, min(240, int(self.pop_sec_var.get())))
        except ValueError:
            pass
        self.cfg["stingers"] = bool(self.sting_var.get())
        self.cfg["dj_volume"] = int(self.djvol_sl.get())
        self.cfg["stinger_volume"] = int(self.stvol_sl.get())
        self.cfg["duck_percent"] = int(self.duck_sl.get())
        self.cfg["duck_auto"] = bool(self.duck_auto_var.get())
        try:
            self.cfg["stinger_chance"] = max(0, min(100, int(self.st_chance_var.get())))
        except ValueError:
            pass
        self.cfg["cohost_enabled"] = bool(self.co_var.get())
        self.cfg["cohost_swears"] = bool(self.co_swear_var.get())
        try:
            self.cfg["cohost_chance"] = max(0, min(100, int(self.co_chance_var.get())))
        except ValueError:
            pass
        save_config(self.cfg)
        if self.dj_running():
            self.apply_settings()  # takes effect on the very next break

    # -------------------------------------------------------------- engine
    def dj_running(self):
        return self.dj_thread is not None and self.dj_thread.is_alive()

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
        dj.DJ_MOOD = c.get("mood", "normal")
        dj.CHATTINESS = c.get("chattiness", "chatty")
        dj.COHOST_ENABLED = bool(c.get("cohost_enabled", True))
        dj.COHOST_CHANCE = int(c.get("cohost_chance", 40)) / 100.0
        dj.COHOST_SWEARS = bool(c.get("cohost_swears", True))
        dj.COHOST_VOICE = (c.get("cohost_voice") or "").strip()
        weights = {k: w for k, w in TRANSITION_WEIGHTS.items() if c.get("t_" + k)}
        dj.TRANSITIONS = weights or dict(TRANSITION_WEIGHTS)
        if c["city"] and c["city"].strip() != dj.CITY:
            dj.set_city(c["city"].strip())

    def load_engine(self):
        """(Re)load the DJ engine with the saved keys. Not allowed while the DJ is running."""
        c = self.cfg
        os.environ["SPOTIPY_CLIENT_ID"] = c["spotify_client_id"]
        os.environ["SPOTIPY_CLIENT_SECRET"] = c["spotify_client_secret"]
        os.environ["SPOTIPY_REDIRECT_URI"] = REDIRECT_URI
        os.environ["DJ_SCOPES"] = "user-read-playback-state user-modify-playback-state playlist-read-private user-top-read"
        os.environ["DJ_APP_DIR"] = APP_DIR      # Cara's memory and the station names live here
        os.environ["DJ_CACHE_PATH"] = CACHE_PATH
        os.environ["DJ_STINGER_DIR"] = os.path.join(APP_DIR, "stingers")
        os.makedirs(my_stingers_folder(), exist_ok=True)
        os.environ["DJ_MY_STINGERS"] = my_stingers_folder()
        os.environ["ELEVENLABS_API_KEY"] = c["elevenlabs_api_key"]
        os.environ["ELEVENLABS_VOICE_ID"] = c["elevenlabs_voice_id"]
        os.environ["GEMINI_API_KEY"] = c["gemini_api_key"]
        if self.dj is None:
            self.dj = importlib.import_module("live_dj_free")
        else:
            self.dj = importlib.reload(self.dj)
        self.apply_settings()

    def connect(self):
        if self.connecting or self.dj_running():
            return
        self.connecting = True
        self.title_var.set("Connecting to Spotify...")
        self.artist_var.set("A browser tab may open the first time: click Agree.")

        def work():
            try:
                self.load_engine()
                self.dj.sp.current_playback()  # triggers the login the first time
                self.connected = True
                print("Connected to Spotify.")
            except Exception as e:
                self.connected = False
                print("Could not connect to Spotify:", e)
                print("Check your Client ID/Secret in Settings, and that the redirect URI matches.")
            finally:
                self.connecting = False

        threading.Thread(target=work, daemon=True).start()

    def test_breaking(self):
        if not self.dj_running():
            messagebox.showinfo(APP_NAME, "Start the DJ and play a song first, then press Test now.")
            return
        self.dj.FORCE_BREAKING.set()

    def test_popin(self):
        if not self.dj_running():
            messagebox.showinfo(APP_NAME, "Start the DJ and play a song first, then press Test now.")
            return
        self.dj.FORCE_POPIN.set()

    def queue_next(self, key):
        if not self.dj_running():
            messagebox.showinfo(APP_NAME, "Start the DJ first, then pick how the next break should start.")
            return
        self.dj.FORCE_TRANSITION = key
        for k, b in self.queue_btns.items():
            b.configure(font=(FONT, 8, "bold underline" if k == key else "normal"))
        names = {"talkover": "talk over", "intro": "over the intro", "silent": "silent", "fadeout": "fade out"}
        self.queue_note.set(f"Queued: the next break will be {names[key]}, at the end of this song.")
        print(f"[queued next transition: {key}]")

    def test_duo(self):
        if not self.dj_running():
            messagebox.showinfo(APP_NAME, "Start the DJ and play a song first, then press Test now.")
            return
        self.dj.FORCE_DUO.set()

    def set_chattiness(self, key, save=True):
        self.cfg["chattiness"] = key
        for k, b in self.talk_btns.items():
            b.configure(fg=FG if k == key else MUTED, font=(FONT, 8, "bold underline" if k == key else "normal"))
        if save:
            save_config(self.cfg)
        if self.dj is not None:
            self.dj.CHATTINESS = key

    def set_mood(self, key, save=True):
        self.cfg["mood"] = key
        for k, b in self.mood_btns.items():
            b.configure(fg=FG if k == key else MUTED, font=(FONT, 8, "bold underline" if k == key else "normal"))
        if save:
            save_config(self.cfg)
        if self.dj is not None:
            self.dj.DJ_MOOD = key

    def open_stinger_folder(self):
        folder = my_stingers_folder()
        os.makedirs(folder, exist_ok=True)
        try:
            if sys.platform == "win32":
                os.startfile(folder)
            else:
                import subprocess
                subprocess.Popen(["xdg-open", folder])
        except Exception as e:
            messagebox.showinfo(APP_NAME, f"Your stingers folder is:\n{folder}\n\n({e})")

    def test_stinger(self):
        if not self.dj_running():
            messagebox.showinfo(APP_NAME, "Start the DJ and play a song first, then press Test now.")
            return
        self.dj.FORCE_STINGER.set()

    def toggle_dj(self):
        if self.dj_running():
            self.dj.STOP.set()
            self.status_var.set("STOPPING...")
            return
        if not self.connected:
            messagebox.showinfo(APP_NAME, "Connect to Spotify first (check Settings).")
            self.connect()
            return

        def run():
            try:
                self.dj.STOP.clear()
                self.apply_settings()
                self.dj.main()
            except Exception as e:
                print("The DJ stopped because of an error:", e)

        self.dj_thread = threading.Thread(target=run, daemon=True)
        self.dj_thread.start()

    # ------------------------------------------------------------ Spotify
    def poll_loop(self):
        last_err, last_note, empty_since = None, None, None
        while True:
            time.sleep(2)
            if not (self.connected and self.dj is not None):
                continue
            try:
                pb = self.dj.sp.current_playback(additional_types="track,episode")
                if last_err:
                    print("Spotify is answering again.")
                    last_err = None
            except Exception as e:
                msg = str(e)
                if msg != last_err:          # say it once, not every two seconds
                    last_err = msg
                    print("Spotify did not answer:", msg[:300])
                    low = msg.lower()
                    if "403" in low or "forbidden" in low or "not registered" in low or "user may not be registered" in low:
                        print("Fix: in developer.spotify.com > your app > Settings > User Management, add the email of your Spotify account, then restart this app.")
                    elif "401" in low or "token" in low:
                        print("Fix: close this app, delete spotify_token.cache in %APPDATA%\\NonStopPopDJ, open it again and click Agree.")
                    elif "429" in low:
                        print("Spotify is rate limiting this app. It will work again in a few minutes.")
                continue
            item = pb.get("item") if pb else None
            if not item:
                self.now = None
                if empty_since is None:
                    empty_since = time.time()
                if time.time() - empty_since > 6:       # explain why, once per change
                    try:
                        devs = (self.dj.sp.devices() or {}).get("devices", [])
                    except Exception:
                        devs = None
                    if devs is None:
                        note = None
                    elif not devs:
                        note = "Spotify sees no devices at all. Open the Spotify app on this PC, play any song, then wait a few seconds."
                    elif pb and not pb.get("is_playing"):
                        note = "Spotify is connected but nothing is loaded. Press play on a song."
                    else:
                        names = ", ".join(f"{d.get('name')} ({'active' if d.get('is_active') else 'idle'})" for d in devs)
                        note = f"Spotify sees: {names}. Press play on one of them (or start a playlist here) and it will show up."
                    if note and note != last_note:
                        last_note = note
                        print(note)
                continue
            empty_since = None
            last_note = None
            b = getattr(self.dj, "brain", None)
            if b is not None:
                try:
                    b.STATION.update(pb.get("context"))
                except Exception:
                    pass
            artists = ", ".join(a["name"] for a in (item.get("artists") or []) if a.get("name"))
            self.now = {
                "title": item.get("name", ""),
                "artist": artists or ((item.get("show") or {}).get("name", "")),
                "playing": bool(pb.get("is_playing")),
                "progress": pb.get("progress_ms") or 0,
                "duration": item.get("duration_ms") or 1,
                "stamp": time.time(),
                "volume": (pb.get("device") or {}).get("volume_percent"),
            }

    def spotify_call(self, fn):
        def work():
            try:
                fn(self.dj.sp)
            except Exception as e:
                print("Spotify said no:", e)
        if self.connected and self.dj is not None:
            threading.Thread(target=work, daemon=True).start()

    def do_prev(self):
        self.spotify_call(lambda sp: sp.previous_track())

    def do_next(self):
        self.spotify_call(lambda sp: sp.next_track())

    def do_toggle(self):
        playing = bool(self.now and self.now["playing"])
        self.spotify_call(lambda sp: sp.pause_playback() if playing else sp.start_playback())

    def on_volume_release(self, _event):
        v = int(self.vol_scale.get())
        self.spotify_call(lambda sp: sp.volume(v))
        self.root.after(1500, lambda: setattr(self, "dragging_volume", False))

    # --------------------------------------------------------- UI refresh
    def pump(self):
        # activity log
        try:
            while True:
                line = log_q.get_nowait()
                self.log.configure(state="normal")
                self.log.insert("end", line + "\n")
                self.log.see("end")
                self.log.configure(state="disabled")
                if line.startswith("[DJ:") or line.startswith("[BREAKING"):
                    self.line_var.set('"' + line.split("] ", 1)[-1] + '"')
                elif line.startswith("[DUO:") and "\n" in line:
                    self.line_var.set(line.split("\n", 1)[1])
        except queue.Empty:
            pass

        # things worker threads want drawn (playlist and search results)
        try:
            while True:
                self.ui_q.get_nowait()()
        except queue.Empty:
            pass
        if self.view == "playlists" and self.connected and not self.pl_loaded and not self.pl_loading:
            self.load_playlists()

        # now playing
        n = self.now
        if n and self.connected:
            self.title_var.set(n["title"])
            self.artist_var.set(n["artist"])
            line = f"{n['title']}  —  {n['artist']}"
            self.mini_var.set(line if len(line) <= 44 else line[:43] + "…")
            prog = n["progress"] + ((time.time() - n["stamp"]) * 1000 if n["playing"] else 0)
            prog = min(prog, n["duration"])
            self.set_progress(prog / n["duration"])
            self.t_left.set(fmt_time(prog))
            self.t_right.set(fmt_time(n["duration"]))
            if n.get("volume") is not None and not self.dragging_volume:
                self.vol_scale.set(n["volume"])
        elif self.connected:
            self.mini_var.set("NOTHING PLAYING")
            self.title_var.set("Nothing playing")
            self.artist_var.set("Start a playlist in Spotify")

        # DJ button + status
        if self.dj_running():
            live = self.dj is not None and not self.dj.STOP.is_set()
            self.dj_button.configure(text="STOP DJ" if live else "STOPPING...", bg=FG, fg=BG,
                                     activebackground=FG, activeforeground=BG)
            if live:
                self.status_var.set("DJ IS LIVE")
        else:
            self.dj_button.configure(text="START DJ", bg=BG, fg=FG, activebackground=BG, activeforeground=FG)
            self.status_var.set("DJ IS OFF")
        self.station_var.set(f"LIVE FROM {self.cfg['city'].upper()}")
        b = getattr(self.dj, "brain", None) if self.dj is not None else None
        name = "NON STOP POP"
        if b is not None and self.connected:
            try:
                if b.STATION.name() != b.FALLBACK:
                    name = b.STATION.full().upper()
            except Exception:
                pass
        if len(name) > 30:
            name = name[:29].rstrip() + "…"
        if self.brand_var.get() != name:
            self.brand_var.set(name)
        self.root.after(250, self.pump)

    def on_close(self):
        try:
            if self.dj is not None:
                self.dj.STOP.set()
        except Exception:
            pass
        self.root.destroy()


def self_test(path):
    """Used by the build: proves the packaged app can load the DJ engine and everything it needs, then quits."""
    result = "ok"
    try:
        importlib.import_module("live_dj_free")
        brain = importlib.import_module("brain")
        import edge_tts, feedparser, numpy, requests, spotipy  # noqa: F401
        import pygame.sndarray  # noqa: F401  (mixes Cara and Scratch together)
        if len(brain.SEGMENTS) < 40 or len(brain.D["duoSegments"]) < 16:
            raise RuntimeError("Cara's brain data is incomplete")
        here = getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))
        bundled = [n for n in os.listdir(os.path.join(here, "my_stingers")) if n.lower().endswith(".mp3")]
        if len(bundled) < 6:
            result = f"fail: only {len(bundled)} stingers inside the app"
        root = tk.Tk()
        root.withdraw()
        app = App(root)                 # builds the whole window: every row, button and setting
        app.set_chattiness("quick", save=False)
        app.pump()
        root.update_idletasks()
        root.destroy()
    except Exception as e:
        import traceback
        result = "fail: " + repr(e) + " | " + traceback.format_exc()[-600:].replace("\n", " / ")
    with open(path, "w", encoding="utf-8") as f:
        f.write(result)


def main():
    sys.stdout = sys.stderr = LogWriter()  # a windowed .exe has no console, so catch prints
    if os.environ.get("NSP_SELFTEST"):
        self_test(os.environ["NSP_SELFTEST"])
        return
    root = tk.Tk()
    try:
        here = getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))
        icon = os.path.join(here, "app.ico")
        if os.path.exists(icon):
            root.iconbitmap(default=icon)   # the waveform icon on the window and taskbar
    except Exception:
        pass
    App(root)
    root.mainloop()


if __name__ == "__main__":
    main()

"""
The built-in player: music plays from this app, without the Spotify app.

Spotify's own Web Playback SDK turns a web page into a Spotify Connect speaker. It needs Spotify Premium and a browser
with Widevine (Spotify's copy protection). The app's window (Microsoft WebView2) has no Widevine, so the player page
runs in a hidden Microsoft Edge window (Edge is part of Windows; Chrome is used if Edge isn't there), with its own
profile so it never touches your normal browsing. The page gets Spotify tokens from this app through a small server
on 127.0.0.1 guarded by a random key, says when it's ready, and closes itself if the app goes away. Windows also ends
it with the app (a job object), even if the app crashes.
"""
import ctypes
import hmac
import http.server
import json
import os
import secrets
import shutil
import subprocess
import sys
import threading
import time
import urllib.parse

NAME = "Non Stop Pop DJ"          # the speaker's name in Spotify's device list


def find_browser():
    """(name, path) of Edge or Chrome, or (None, None)."""
    places = []
    for env in ("PROGRAMFILES(X86)", "PROGRAMFILES", "LOCALAPPDATA"):
        base = os.environ.get(env)
        if base:
            places.append(("Edge", os.path.join(base, "Microsoft", "Edge", "Application", "msedge.exe")))
    for env in ("PROGRAMFILES", "PROGRAMFILES(X86)", "LOCALAPPDATA"):
        base = os.environ.get(env)
        if base:
            places.append(("Chrome", os.path.join(base, "Google", "Chrome", "Application", "chrome.exe")))
    for name, path in places:
        if os.path.isfile(path):
            return name, path
    try:
        import winreg
        for name, exe in (("Edge", "msedge.exe"), ("Chrome", "chrome.exe")):
            for hive in (winreg.HKEY_LOCAL_MACHINE, winreg.HKEY_CURRENT_USER):
                try:
                    with winreg.OpenKey(hive, rf"SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths\{exe}") as k:
                        path = winreg.QueryValue(k, None)
                        if path and os.path.isfile(path):
                            return name, path
                except OSError:
                    pass
    except Exception:
        pass
    return None, None


def _seed_widevine(profile, browser):
    """Copy the Widevine module from your normal Edge/Chrome profile, so the first start doesn't wait for a download."""
    dst = os.path.join(profile, "WidevineCdm")
    if os.path.isdir(dst) and os.listdir(dst):
        return
    local = os.environ.get("LOCALAPPDATA", "")
    src = os.path.join(local, *(("Microsoft", "Edge") if browser == "Edge" else ("Google", "Chrome")), "User Data", "WidevineCdm")
    if os.path.isdir(src):
        try:
            shutil.copytree(src, dst, dirs_exist_ok=True)
        except Exception as e:
            print(f"[player: couldn't copy Widevine from {browser} ({e}); it will download it instead]")


# ---------------------------------------------------------------- Windows plumbing
class _BasicLimits(ctypes.Structure):
    _fields_ = [("PerProcessUserTimeLimit", ctypes.c_int64), ("PerJobUserTimeLimit", ctypes.c_int64),
                ("LimitFlags", ctypes.c_uint32), ("MinimumWorkingSetSize", ctypes.c_size_t),
                ("MaximumWorkingSetSize", ctypes.c_size_t), ("ActiveProcessLimit", ctypes.c_uint32),
                ("Affinity", ctypes.c_size_t), ("PriorityClass", ctypes.c_uint32), ("SchedulingClass", ctypes.c_uint32)]


class _IoCounters(ctypes.Structure):
    _fields_ = [(n, ctypes.c_uint64) for n in ("ReadOperationCount", "WriteOperationCount", "OtherOperationCount",
                                               "ReadTransferCount", "WriteTransferCount", "OtherTransferCount")]


class _ExtendedLimits(ctypes.Structure):
    _fields_ = [("BasicLimitInformation", _BasicLimits), ("IoInfo", _IoCounters), ("ProcessMemoryLimit", ctypes.c_size_t),
                ("JobMemoryLimit", ctypes.c_size_t), ("PeakProcessMemoryUsed", ctypes.c_size_t), ("PeakJobMemoryUsed", ctypes.c_size_t)]


def _tie_to_app(proc):
    """Put the browser in a job that Windows closes when this app exits, ending the browser with it."""
    try:
        k32 = ctypes.windll.kernel32
        k32.CreateJobObjectW.restype = ctypes.c_void_p
        k32.SetInformationJobObject.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_void_p, ctypes.c_uint32]
        k32.AssignProcessToJobObject.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
        job = k32.CreateJobObjectW(None, None)
        info = _ExtendedLimits()
        info.BasicLimitInformation.LimitFlags = 0x2000          # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        k32.SetInformationJobObject(job, 9, ctypes.byref(info), ctypes.sizeof(info))
        if k32.AssignProcessToJobObject(job, ctypes.c_void_p(int(proc._handle))):
            return job
    except Exception as e:
        print(f"[player: couldn't tie the player to the app ({e})]")
    return None


def _hide_windows(pid):
    """Hide the player's window (and anything else that browser opens). Returns how many it hid."""
    try:
        user32 = ctypes.windll.user32
        hidden = []
        proc_t = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)

        def each(hwnd, _):
            owner = ctypes.c_ulong()
            user32.GetWindowThreadProcessId(ctypes.c_void_p(hwnd), ctypes.byref(owner))
            if owner.value == pid and user32.IsWindowVisible(ctypes.c_void_p(hwnd)):
                user32.ShowWindow(ctypes.c_void_p(hwnd), 0)        # SW_HIDE: not on screen, not on the taskbar
                hidden.append(hwnd)
            return True

        user32.EnumWindows(proc_t(each), 0)
        return len(hidden)
    except Exception:
        return 0


# ---------------------------------------------------------------- the page's server
class _Handler(http.server.BaseHTTPRequestHandler):
    player = None

    def log_message(self, *a):
        pass

    def _ok(self):
        p = self.player
        if self.headers.get("Host") != f"127.0.0.1:{p.port}":         # no other names (DNS rebinding)
            return None
        url = urllib.parse.urlparse(self.path)
        key = (urllib.parse.parse_qs(url.query).get("k") or [""])[0]
        if not hmac.compare_digest(key, p.key):
            return None
        return url.path

    def _send(self, code, body, kind="application/json"):
        data = body if isinstance(body, bytes) else json.dumps(body).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", kind)
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        path = self._ok()
        p = self.player
        if path is None:
            return self._send(403, {"error": "no"})
        if path == "/player":
            with open(p.page, "rb") as f:
                return self._send(200, f.read(), "text/html; charset=utf-8")
        if path == "/token":
            try:
                token = p.token_fn()
            except Exception as e:
                token = None
                print(f"[player: no Spotify token for the player ({e})]")
            return self._send(200 if token else 503, {"token": token})
        if path == "/ping":
            p.seen = time.time()
            return self._send(200, {"stop": not p.wanted, "name": NAME})
        return self._send(404, {"error": "unknown"})

    def do_POST(self):
        path = self._ok()
        if path is None:
            return self._send(403, {"error": "no"})
        try:
            body = json.loads(self.rfile.read(int(self.headers.get("Content-Length") or 0) or 0) or b"{}")
        except Exception:
            body = {}
        if path == "/event":
            self.player.event(body)
            return self._send(200, {"ok": True})
        return self._send(404, {"error": "unknown"})


class Player:
    def __init__(self, app_dir, page, token_fn, on_change=None):
        self.profile = os.path.join(app_dir, "player-browser")
        self.page = page
        self.token_fn = token_fn
        self.on_change = on_change or (lambda what: None)
        self.key = secrets.token_urlsafe(24)
        self.port = None
        self.server = None
        self.proc = None
        self.job = None
        self.browser = None
        self.wanted = False
        self.status = "off"            # off, starting, ready, premium, error
        self.problem = ""
        self.device_id = None
        self.seen = 0.0
        self.started = 0.0
        self.retries = []
        self.lock = threading.Lock()

    # ------------------------------------------------------------ what the app sees
    def info(self):
        return {"status": self.status, "problem": self.problem, "browser": self.browser or "", "ready": self.status == "ready",
                "deviceId": self.device_id, "name": NAME}

    def _set(self, status, problem=""):
        changed = (status, problem) != (self.status, self.problem)
        self.status, self.problem = status, problem
        if changed:
            self.on_change(status)

    # ------------------------------------------------------------ starting and stopping
    def start(self):
        with self.lock:
            self.wanted = True
            if self.proc is not None and self.proc.poll() is None:
                return
            if sys.platform != "win32":
                return self._set("error", "The built-in player needs Windows.")
            name, path = find_browser()
            if not path:
                return self._set("error", "The built-in player needs Microsoft Edge or Google Chrome, and neither was found.")
            self.browser = name
            self._serve()
            os.makedirs(self.profile, exist_ok=True)
            _seed_widevine(self.profile, name)
            args = [path, f"--user-data-dir={self.profile}", f"--app=http://127.0.0.1:{self.port}/player?k={self.key}",
                    "--window-size=420,280", "--window-position=-32000,-32000", "--no-first-run", "--no-default-browser-check",
                    "--disable-sync", "--no-service-autorun", "--disable-extensions", "--autoplay-policy=no-user-gesture-required",
                    "--disable-background-timer-throttling", "--disable-renderer-backgrounding",
                    "--disable-backgrounding-occluded-windows", "--hide-crash-restore-bubble", "--disable-session-crashed-bubble",
                    "--component-updater=fast-update",
                    "--disable-features=CalculateNativeWinOcclusion,Translate,msEdgeSidebarV2,msImplicitSignin,msEdgeOnRampFRE"]
            try:
                self.proc = subprocess.Popen(args, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            except Exception as e:
                return self._set("error", f"Couldn't start {name} for the built-in player ({e}).")
            self.job = _tie_to_app(self.proc)
            self.started = self.seen = time.time()
            self.device_id = None
            self._set("starting")
            print(f"[player: starting the built-in player in a hidden {name} window]")

    def _serve(self):
        """The small server the player page talks to (127.0.0.1 only, random port, random key)."""
        if self.server is None:
            _Handler.player = self
            self.server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
            self.port = self.server.server_address[1]
            threading.Thread(target=self.server.serve_forever, daemon=True).start()
            threading.Thread(target=self._watch, daemon=True).start()
        return f"http://127.0.0.1:{self.port}/player?k={self.key}"

    def stop(self):
        with self.lock:
            self.wanted = False
            proc, self.proc = self.proc, None
            self.device_id = None
            self._set("off")
        if proc is not None and proc.poll() is None:
            time.sleep(0.6)                       # the page sees "stop" on its next ping and disconnects cleanly
            try:
                proc.terminate()
            except Exception:
                pass

    def restart(self, why, wait=4.0):
        now = time.time()
        self.retries = [t for t in self.retries if now - t < 600] + [now]
        if len(self.retries) > 4:
            self._set("error", f"The built-in player keeps stopping ({why}). Music plays through the Spotify app for now.")
            self.wanted = False
            return
        print(f"[player: restarting the built-in player ({why})]")
        proc, self.proc = self.proc, None
        if proc is not None and proc.poll() is None:
            try:
                proc.terminate()
            except Exception:
                pass
        threading.Timer(wait, lambda: self.wanted and self.start()).start()

    def _watch(self):
        while True:
            time.sleep(1.5)
            proc = self.proc
            if not self.wanted or proc is None:
                continue
            if time.time() - self.started < 40 or self.status != "ready":
                _hide_windows(proc.pid)               # keep it out of sight while it settles in
            elif int(time.time()) % 10 == 0:
                _hide_windows(proc.pid)
            if proc.poll() is not None:
                self.restart("the browser closed")
            elif time.time() - self.seen > 25:
                self.restart("the player page stopped answering")

    # ------------------------------------------------------------ what the page says
    def event(self, e):
        kind = e.get("type")
        if kind == "ready":
            self.device_id = e.get("device_id")
            self.retries = []
            print("[player: the built-in player is ready]")
            self._set("ready")
        elif kind == "not_ready":
            self.device_id = None
            self._set("starting")
        elif kind == "error":
            what, msg = e.get("kind"), str(e.get("message") or "")[:200]
            print(f"[player: {what} error: {msg}]")
            if what == "account":
                self.wanted = False
                self.device_id = None
                self._set("premium", "Spotify only lets apps play music with Premium. Music plays through the Spotify app instead.")
            elif what == "auth":
                self._set("error", "Spotify needs your OK for the built-in player: press Reconnect in Settings and click Agree.")
            elif what == "init":
                # usually Widevine isn't there yet: the browser downloads it the first time; the page reloads and retries
                self._set("starting", "Getting the player ready (the first time can take a minute)…")
        elif kind == "init_gave_up":
            self.restart("its copy protection wasn't ready", wait=3.0)
        elif kind == "log":
            print(f"[player: {str(e.get('message'))[:200]}]")

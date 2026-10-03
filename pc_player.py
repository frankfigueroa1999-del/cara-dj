"""
The built-in player: music plays from this app, without the Spotify app.

Spotify's own Web Playback SDK turns a web page into a Spotify Connect speaker. It needs Spotify Premium and a browser
with Widevine (Spotify's copy protection). The app's window (Microsoft WebView2) has no Widevine, so the player page
runs in its own Google Chrome window (Chrome carries Widevine; Microsoft Edge, part of Windows, is used when Chrome
isn't installed), with its own profile so it never touches your normal browsing. The page gets Spotify tokens from
this app through a small server on 127.0.0.1 guarded by a random key, says when it's ready, and closes itself if the
app goes away. Windows also ends it with the app (a job object), even if the app crashes.

The window is kept off-screen and off the taskbar, but never hidden: Chrome and Edge don't load any sound in a page
that's hidden and has never been seen (they wait until you look at it), so a hidden player plays silence.
"""
import base64
import ctypes
import hmac
import http.server
import json
import os
import secrets
import shutil
import socket
import struct
import subprocess
import sys
import threading
import time
import urllib.parse
import urllib.request

NAME = "Non Stop Pop DJ"          # the speaker's name in Spotify's device list


def find_browser():
    """(name, path) of Chrome or Edge, or (None, None). Chrome first: it ships with Widevine, while Edge downloads it."""
    places = []
    for env in ("PROGRAMFILES", "PROGRAMFILES(X86)", "LOCALAPPDATA"):
        base = os.environ.get(env)
        if base:
            places.append(("Chrome", os.path.join(base, "Google", "Chrome", "Application", "chrome.exe")))
    for env in ("PROGRAMFILES(X86)", "PROGRAMFILES", "LOCALAPPDATA"):
        base = os.environ.get(env)
        if base:
            places.append(("Edge", os.path.join(base, "Microsoft", "Edge", "Application", "msedge.exe")))
    for name, path in places:
        if os.path.isfile(path):
            return name, path
    try:
        import winreg
        for name, exe in (("Chrome", "chrome.exe"), ("Edge", "msedge.exe")):
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


class _Rect(ctypes.Structure):
    _fields_ = [("left", ctypes.c_long), ("top", ctypes.c_long), ("right", ctypes.c_long), ("bottom", ctypes.c_long)]


PAGE_TITLE = "Non Stop Pop DJ player"     # player.html's <title>: how the player's own window is recognised
_FAR = -32000                            # off every screen
_SWP = 0x0001 | 0x0004 | 0x0010          # SWP_NOSIZE | SWP_NOZORDER | SWP_NOACTIVATE


def _tuck_windows(pid, tucked):
    """Keep the browser's windows out of sight WITHOUT hiding them (a hidden page loads no sound): each one moves
    off-screen and becomes a tool window, which keeps it off the taskbar and out of Alt+Tab. `tucked` remembers the
    ones already done. If the player's own window got hidden or minimized, it's shown again (still off-screen).
    Returns True when the player's window is in place."""
    seen = False
    try:
        user32 = ctypes.windll.user32
        get_ex = getattr(user32, "GetWindowLongPtrW", None) or user32.GetWindowLongW
        set_ex = getattr(user32, "SetWindowLongPtrW", None) or user32.SetWindowLongW
        get_ex.restype, get_ex.argtypes = ctypes.c_ssize_t, [ctypes.c_void_p, ctypes.c_int]
        set_ex.restype, set_ex.argtypes = ctypes.c_ssize_t, [ctypes.c_void_p, ctypes.c_int, ctypes.c_ssize_t]
        proc_t = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)

        def each(hwnd, _):
            nonlocal seen
            h = ctypes.c_void_p(hwnd)
            owner = ctypes.c_ulong()
            user32.GetWindowThreadProcessId(h, ctypes.byref(owner))
            if owner.value != pid:
                return True
            title = ctypes.create_unicode_buffer(256)
            user32.GetWindowTextW(h, title, 256)
            mine = PAGE_TITLE in title.value
            visible = bool(user32.IsWindowVisible(h))
            if hwnd not in tucked:
                if not visible:
                    return True                                   # the browser's own invisible helper windows
                ex = get_ex(h, -20)                               # GWL_EXSTYLE
                set_ex(h, -20, (ex | 0x80 | 0x08000000) & ~0x40000)   # + tool window, + no-activate, - app window
                user32.ShowWindow(h, 0)                           # the taskbar lets go of it while it's away...
                user32.SetWindowPos(h, None, _FAR, _FAR, 0, 0, _SWP)
                user32.ShowWindow(h, 4)                           # ...and it's back (SW_SHOWNOACTIVATE), off-screen
                tucked.add(hwnd)
            else:
                if not visible and not mine:
                    return True                                   # a bubble the browser closed: leave it be
                if mine and (not visible or user32.IsIconic(h)):
                    user32.ShowWindow(h, 4)                       # hidden or minimized: sound would stop loading
                r = _Rect()
                user32.GetWindowRect(h, ctypes.byref(r))
                if r.left > _FAR // 2 or r.top > _FAR // 2:
                    user32.SetWindowPos(h, None, _FAR, _FAR, 0, 0, _SWP)
            seen = seen or mine
            return True

        user32.EnumWindows(proc_t(each), 0)
    except Exception:
        pass
    return seen


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
        self.query = urllib.parse.parse_qs(url.query)
        key = (self.query.get("k") or [""])[0]
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
            p.page_seen((self.query.get("vis") or [""])[0])
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


# ---------------------------------------------------------------- DevTools (the browser's own remote control)
def _devtools_call(ws_url, method, params, timeout=8.0):
    """One DevTools command over a websocket (just enough of the protocol for that), returning its result."""
    u = urllib.parse.urlparse(ws_url)
    sock = socket.create_connection((u.hostname, u.port), timeout=timeout)
    try:
        key = base64.b64encode(os.urandom(16)).decode()
        sock.sendall((f"GET {u.path} HTTP/1.1\r\nHost: {u.hostname}:{u.port}\r\nUpgrade: websocket\r\nConnection: Upgrade\r\n"
                      f"Sec-WebSocket-Key: {key}\r\nSec-WebSocket-Version: 13\r\n\r\n").encode())
        buf = b""
        while b"\r\n\r\n" not in buf:
            chunk = sock.recv(4096)
            if not chunk:
                raise ConnectionError("DevTools closed the connection")
            buf += chunk
        head, buf = buf.split(b"\r\n\r\n", 1)
        if b" 101 " not in head.split(b"\r\n", 1)[0]:
            raise ConnectionError(head.split(b"\r\n", 1)[0].decode(errors="replace"))
        msg = json.dumps({"id": 1, "method": method, "params": params}).encode()
        frame = bytearray([0x81])
        n = len(msg)
        if n < 126:
            frame.append(0x80 | n)
        elif n < 65536:
            frame += bytes([0x80 | 126]) + struct.pack(">H", n)
        else:
            frame += bytes([0x80 | 127]) + struct.pack(">Q", n)
        mask = os.urandom(4)
        frame += mask + bytes(b ^ mask[i % 4] for i, b in enumerate(msg))
        sock.sendall(bytes(frame))

        def take(k):
            nonlocal buf
            while len(buf) < k:
                chunk = sock.recv(65536)
                if not chunk:
                    raise ConnectionError("DevTools closed the connection")
                buf += chunk
            out, buf = buf[:k], buf[k:]
            return out

        data = b""
        deadline = time.time() + timeout
        while time.time() < deadline:
            b1, b2 = take(2)
            size = b2 & 0x7F
            if size == 126:
                size = struct.unpack(">H", take(2))[0]
            elif size == 127:
                size = struct.unpack(">Q", take(8))[0]
            if b2 & 0x80:
                take(4)
            payload = take(size)
            if b1 & 0x0F == 8:
                break
            data += payload
            if b1 & 0x80:
                reply = json.loads(data or b"{}")
                data = b""
                if reply.get("id") == 1:
                    if "error" in reply:
                        raise RuntimeError(reply["error"].get("message", "DevTools error"))
                    return reply.get("result") or {}
        raise TimeoutError("DevTools didn't answer")
    finally:
        sock.close()


class Player:
    def __init__(self, app_dir, page, token_fn, on_change=None):
        self.app_dir = app_dir
        self.profile = os.path.join(app_dir, "player-browser")     # set again at start: each browser has its own
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
        self.past_ids = set()          # every device id this player has had (a restart gets a new one)
        self.seen = 0.0
        self.started = 0.0
        self.retries = []
        self.lock = threading.Lock()
        self.unlocked = False          # the page has had its "click" (browsers want one before audio plays)
        self.diag = {}
        self.tucked = set()            # the browser windows already moved out of sight
        self.vis = ""                  # what the page says: "visible" or "hidden" (a hidden page loads no sound)
        self.hidden_since = None
        self.said_hidden = False

    # ------------------------------------------------------------ what the app sees
    def info(self):
        return {"status": self.status, "problem": self.problem, "browser": self.browser or "", "ready": self.status == "ready",
                "deviceId": self.device_id, "name": NAME, "unlocked": self.unlocked, "diag": self.diag, "page": self.vis}

    def page_seen(self, vis):
        """Each ping says whether the page counts as on screen."""
        if vis not in ("visible", "hidden"):
            return
        if vis == "hidden":
            self.hidden_since = self.hidden_since or time.time()
        else:
            if self.said_hidden:
                print("[player: the player page is visible again, so its sound can load]")
            self.hidden_since, self.said_hidden = None, False
        self.vis = vis

    def no_sound(self, detail):
        """The sound check heard nothing while Spotify says this player is playing."""
        print(f"[player: no sound from the built-in player ({detail}); page: {self.vis or '?'}; "
              f"Spotify's frame: {self.media()}; checks: {self.diag}]")
        self._set("error", "Spotify says it's playing in the app, but no sound came out. Try again, or use the Spotify app.")

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
            # a profile per browser (Chrome can't open one Edge has used, and the other way round)
            self.profile = os.path.join(self.app_dir, "player-chrome" if name == "Chrome" else "player-browser")
            self._serve()
            os.makedirs(self.profile, exist_ok=True)
            _seed_widevine(self.profile, name)
            try:
                os.remove(os.path.join(self.profile, "DevToolsActivePort"))     # a stale one would point at nothing
            except OSError:
                pass
            args = [path, f"--user-data-dir={self.profile}", f"--app=http://127.0.0.1:{self.port}/player?k={self.key}",
                    "--window-size=420,280", "--window-position=-32000,-32000", "--no-first-run", "--no-default-browser-check",
                    "--disable-sync", "--no-service-autorun", "--disable-extensions", "--autoplay-policy=no-user-gesture-required",
                    "--disable-background-timer-throttling", "--disable-renderer-backgrounding",
                    "--disable-backgrounding-occluded-windows", "--hide-crash-restore-bubble", "--disable-session-crashed-bubble",
                    "--component-updater=fast-update", "--remote-debugging-port=0",
                    "--disable-features=CalculateNativeWinOcclusion,Translate,msEdgeSidebarV2,msImplicitSignin,msEdgeOnRampFRE"]
            try:
                self.proc = subprocess.Popen(args, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            except Exception as e:
                return self._set("error", f"Couldn't start {name} for the built-in player ({e}).")
            self.job = _tie_to_app(self.proc)
            self.started = self.seen = time.time()
            self.device_id = None
            self.unlocked = False
            self.tucked = set()
            self.vis, self.hidden_since, self.said_hidden = "", None, False
            self._set("starting")
            threading.Thread(target=self._tuck_early, args=(self.proc,), daemon=True).start()
            print(f"[player: starting the built-in player in its own {name} window, kept off-screen]")

    def _tuck_early(self, proc):
        """While the browser opens, move its windows out of sight as soon as they appear."""
        end = time.time() + 25
        while time.time() < end and proc is self.proc and proc.poll() is None:
            _tuck_windows(proc.pid, self.tucked)
            time.sleep(0.08)

    def _serve(self):
        """The small server the player page talks to (127.0.0.1 only, random port, random key)."""
        if self.server is None:
            _Handler.player = self
            self.server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
            self.port = self.server.server_address[1]
            threading.Thread(target=self.server.serve_forever, daemon=True).start()
            threading.Thread(target=self._watch, daemon=True).start()
        return f"http://127.0.0.1:{self.port}/player?k={self.key}"

    def _targets(self):
        port = None
        for _ in range(30):
            try:
                with open(os.path.join(self.profile, "DevToolsActivePort"), encoding="utf-8") as f:
                    port = int(f.readline().strip())
                break
            except (OSError, ValueError):
                time.sleep(0.3)
        if not port:
            raise RuntimeError("the browser's DevTools port never showed up")
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/json/list", timeout=5) as r:
            return json.loads(r.read())

    def devtools(self, expression, gesture=True, frame=None, timeout=8.0):
        """Runs JavaScript in the player page (or, with frame=, in Spotify's frame inside it) as if you'd clicked there."""
        targets = self._targets()
        if frame:
            target = next((t for t in targets if t.get("type") == "iframe" and frame in (t.get("url") or "")), None)
        else:
            target = next((t for t in targets if t.get("type") == "page" and f"127.0.0.1:{self.port}/player" in (t.get("url") or "")), None)
        if not target:
            raise RuntimeError(f"couldn't find the {'Spotify frame' if frame else 'player page'}")
        res = _devtools_call(target["webSocketDebuggerUrl"], "Runtime.evaluate",
                             {"expression": expression, "userGesture": gesture, "awaitPromise": True, "returnByValue": True},
                             timeout=timeout)
        if res.get("exceptionDetails"):
            d = res["exceptionDetails"]
            raise RuntimeError(((d.get("exception") or {}).get("description") or d.get("text") or "script error").split("\n")[0])
        return (res.get("result") or {}).get("value")

    def control(self, action, value=None):
        """Play, pause, skip, seek or set the volume right in the player (no round trip through Spotify's servers).
        Returns what the player says ("playing", "paused" or "ok"); raises if the player can't do it."""
        if self.status != "ready":
            raise RuntimeError("the built-in player isn't ready")
        got = self.devtools(f"window.__ctl ? window.__ctl({json.dumps(action)}, {json.dumps(value)}) : 'not loaded'", timeout=7.0)
        if got in ("not loaded", "no player", "unknown"):
            raise RuntimeError(f"the player page can't do that ({got})")
        return got

    def media(self):
        """How the sound itself is doing inside Spotify's frame, in a few words (for Activity)."""
        try:
            return self.devtools("(() => { const ms = [...document.querySelectorAll('audio,video')];"
                                 " if (!ms.length) return 'no sound element yet';"
                                 " return ms.map(m => (m.readyState === 0 && !m.paused ? 'waiting to load' : m.paused ? 'paused' : 'playing')"
                                 " + ` at ${m.currentTime.toFixed(1)}s` + (m.muted ? ', muted' : '') + (m.error ? `, error ${m.error.code}` : '')).join('; '); })()",
                                 frame="scdn.co")
        except Exception as e:
            return f"not reachable ({e})"

    def unlock(self, why):
        """Give the page its click, nudge playback (pause and resume), and click inside Spotify's own frame too."""
        if self.proc is not None:
            _tuck_windows(self.proc.pid, self.tucked)        # makes sure the page is "on screen" (if hidden, no sound loads)
        try:
            got = self.devtools("window.__unlock ? window.__unlock() : 'the player is still loading'")
            self.unlocked = True
        except Exception as e:
            print(f"[player: couldn't unlock the sound ({why}): {e}]")
            return False
        try:
            self.devtools("[...document.querySelectorAll('audio,video')].forEach(m => { if (m.paused && m.readyState > 0) m.play().catch(() => {}); }); true",
                          frame="scdn.co")
        except Exception:
            pass
        print(f"[player: sound unlocked ({why}): {got}; page: {self.vis or '?'}; Spotify's frame: {self.media()}]")
        return True

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

    def retry(self):
        """Try again button: a fresh start (new page, new click), forgetting earlier failures."""
        self.retries, self.problem, self.wanted = [], "", True
        proc, self.proc = self.proc, None
        if proc is not None and proc.poll() is None:
            try:
                proc.terminate()
                proc.wait(5)
            except Exception:
                pass
        self.start()

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
            _tuck_windows(proc.pid, self.tucked)       # out of sight, never hidden (and shown again if something hid it)
            if self.hidden_since and time.time() - self.hidden_since > 6 and not self.said_hidden:
                self.said_hidden = True
                print("[player: the browser says the player page is hidden, so it won't load any sound; "
                      "showing its window again, off-screen]")
            if proc.poll() is not None:
                self.restart("the browser closed")
            elif time.time() - self.seen > 25:
                self.restart("the player page stopped answering")

    # ------------------------------------------------------------ what the page says
    def event(self, e):
        kind = e.get("type")
        if kind == "ready":
            self.device_id = e.get("device_id")
            self.past_ids.add(self.device_id)
            self.retries = []
            print("[player: the built-in player is ready]")
            self._set("ready")
            threading.Thread(target=self.unlock, args=("ready",), daemon=True).start()
        elif kind == "diag":
            self.diag = {k: str(v)[:80] for k, v in e.items() if k != "type"}
            print("[player: " + ", ".join(f"{k}: {v}" for k, v in self.diag.items()) + "]")
        elif kind == "autoplay":
            print("[player: the browser held back the sound until a click; unlocking]")
            threading.Thread(target=self.unlock, args=("autoplay",), daemon=True).start()
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

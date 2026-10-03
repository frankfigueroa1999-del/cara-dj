"""
Station stingers, made the same way as in the iPhone app: your six Non Stop Pop stingers word for word, with the
station's name swapped for whatever's playing ("Late Night Drives FM").

The music, swooshes and hits are the originals with the voice lifted out (station_beds/). The station voice (an
ElevenLabs voice) reads the same lines; each read gets the originals' radio EQ, compression and echo, then goes in
where the old line sat. Each line is recorded once and kept. Each station's six stingers are made one at a time in
the background while you listen.
"""
import hashlib
import math
import os
import random
import shutil
import tempfile
import threading
import time
import wave

import numpy as np

DEFAULT_VOICE = "EXAVITQu4vr4xnSDxMaL"      # the announcer when you haven't picked one (one of ElevenLabs' own voices)
BEDS = os.path.join(os.path.dirname(os.path.abspath(__file__)), "station_beds")

# The lines with the station's name in them ({name} = "Late Night Drives", {full} = "Late Night Drives FM")
NAME, SHOUT, THIS_IS, LOVE, FREQ_NAME = ("{name}.", "{name}!", "This is {name}.", "You know you love {full}.",
                                         "One hundred point seven FM, {name}.")
# For each original: how loud its voice sat in the music (dBFS while talking), then each line's spot (seconds in),
# its words, and whether it echoes.
TEMPLATES = {
    1: (-21.5, [(0.10, "Classic pop hits from the last thirty years.", False), (2.45, "It's the best music.", False),
                (3.60, "One hundred point seven FM.", False), (4.90, SHOUT, True)]),
    2: (-21.6, [(0.10, "All your favorite pop hits from the eighties,", False), (3.30, "nineties,", False),
                (4.00, "noughties,", True), (5.20, "and today.", False), (6.40, "One hundred point seven FM.", False),
                (8.40, NAME, False)]),
    3: (-23.0, [(0.05, "Dance pop classics that", False), (2.85, "never stop,", True),
                (4.85, "On one hundred point seven FM.", False), (7.00, "The music is awesome.", False),
                (8.60, NAME, False)]),
    4: (-22.5, [(0.15, "The music", True), (2.40, "that has really moved you.", False), (4.60, THIS_IS, False),
                (6.60, "They're the best.", True)]),
    5: (-22.4, [(0.10, "Everyone was happy once in their lives.", False),
                (3.10, "This is music from that special time for you.", False), (6.20, FREQ_NAME, False),
                (8.70, "Contemporary nostalgia is the best.", False)]),
    6: (-20.6, [(0.10, "Give in to the music.", True), (3.20, "This is when you were happy.", False), (4.60, LOVE, False),
                (7.10, "Don't be an elitist snotbag.", True)]),
}


def full_name(name):
    """On air: "Late Night Drives" becomes "Late Night Drives FM"; names that already sound like a station keep theirs."""
    last = name.split(" ")[-1].lower() if name else ""
    return name if last in ("fm", "am", "radio", "station") else name + " FM"


def words(say, station):
    return say.format(name=station, full=full_name(station)) if "{" in say else say


# ---------------------------------------------------------------- the station voice
def tts(text, voice, model):
    """One line in the station voice (mp3 bytes): a steadier, punchier read than Cara's."""
    import requests
    key = (os.environ.get("ELEVENLABS_API_KEY") or "").strip()
    if not key:
        raise RuntimeError("add your ElevenLabs API key in Settings")
    expressive = model.startswith(("eleven_v4", "eleven_v3"))
    settings = ({"stability": 0.5, "similarity_boost": 0.85} if expressive else
                {"stability": 0.45, "similarity_boost": 0.8, "style": 0.55, "use_speaker_boost": True, "speed": 1.1})
    r = requests.post(f"https://api.elevenlabs.io/v1/text-to-speech/{voice}", params={"output_format": "mp3_44100_128"},
                      headers={"xi-api-key": key, "Content-Type": "application/json"},
                      json={"text": text, "model_id": model, "voice_settings": settings}, timeout=60)
    if not r.ok:
        hint = " (use the secret key that starts with sk_, not the key ID)" if "invalid_api_key" in r.text else ""
        raise RuntimeError(f"ElevenLabs {r.status_code}: {r.text[:120]}{hint}")
    return r.content


class StationStingers:
    """Makes and keeps the stingers for each station."""

    def __init__(self):
        self.lock = threading.Lock()
        self.making = {}            # station folder -> what to do when the one being made is ready
        self.last = {}              # station folder -> the stinger played last (never the same twice in a row)
        self.failed_at = 0.0

    # settings come from the environment, like the rest of the engine
    @staticmethod
    def voice():
        return (os.environ.get("DJ_STATION_VOICE") or "").strip() or DEFAULT_VOICE

    @staticmethod
    def model():
        return (os.environ.get("ELEVENLABS_MODEL") or "eleven_v4").strip()

    @staticmethod
    def root():
        base = os.environ.get("DJ_STINGER_DIR") or os.path.join(tempfile.gettempdir(), "nsp_stingers")
        return os.path.join(base, "station")

    def folder(self, station):
        """One folder of finished stingers per station, voice and model."""
        slug = "".join(c if c.isalnum() else "-" for c in station.lower())[:24]
        key = hashlib.sha1(f"{station}|{self.voice()}|{self.model()}".encode("utf-8")).hexdigest()[:10]
        return os.path.join(self.root(), "stations", f"{slug}-{key}")

    @staticmethod
    def made(folder):
        try:
            return sorted(os.path.join(folder, n) for n in os.listdir(folder) if n.endswith(".wav"))
        except OSError:
            return []

    def count(self, station):
        return len(self.made(self.folder(station)))

    def failing_lately(self):
        """Making them went wrong in the last ten minutes (your original stingers stand in meanwhile)."""
        return time.time() - self.failed_at < 600

    def is_making(self, station):
        return os.path.basename(self.folder(station)) in self.making

    def ready(self, station):
        """A finished stinger for this station (never the same one twice in a row), or None if none is made yet."""
        folder = self.folder(station)
        have = self.made(folder)
        if not have:
            return None
        key = os.path.basename(folder)
        pick = random.choice([p for p in have if p != self.last.get(key)] or have)
        self.last[key] = pick
        try:
            os.utime(folder)                 # recently used: kept when old stations are tidied away
        except OSError:
            pass
        return pick

    def warm(self, station, then=None):
        """Makes one more for this station in the background, if it still needs one. then(path) runs when it's done."""
        if not station or self.failing_lately():
            return False
        folder = self.folder(station)
        key = os.path.basename(folder)
        with self.lock:
            if key in self.making:
                if then:
                    self.making[key].append(then)
                return True
            if not then and len(self.made(folder)) >= len(TEMPLATES):
                return False
            self.making[key] = [then] if then else []
        threading.Thread(target=self._make_in_background, args=(station, key), daemon=True).start()
        return True

    def _make_in_background(self, station, key):
        path = None
        try:
            path = self.make(station)
        except Exception as e:
            print(f"[couldn't make the stinger: {e}]")
        finally:
            with self.lock:
                waiting = self.making.pop(key, [])
            for fn in waiting:
                try:
                    fn(path)
                except Exception:
                    pass

    def make(self, station):
        """Makes one more stinger for this station now (takes a few seconds). Returns its file, or None."""
        folder = self.folder(station)
        os.makedirs(folder, exist_ok=True)
        done = {os.path.splitext(os.path.basename(p))[0] for p in self.made(folder)}
        todo = [i for i in TEMPLATES if f"stinger_{i}" not in done]
        if not todo:
            return self.ready(station)
        tid = random.choice(todo)
        voice_db, lines = TEMPLATES[tid]
        texts = [words(say, station) for _, say, _ in lines]
        print(f"[making a {full_name(station)} stinger: {' '.join(texts)}]")
        try:
            reads = [self.read(t) for t in texts]           # lines read before are reused
        except Exception as e:
            self.failed_at = time.time()
            print(f"[couldn't make the stinger: {e}]")
            return None
        bed = os.path.join(BEDS, f"stationbed_{tid}.ogg")
        if not os.path.exists(bed):
            print("[the stinger music is missing from the app]")
            return None
        out = os.path.join(folder, f"stinger_{tid}.wav")
        try:
            music, rate = decode(bed)
            voices = [(mono(decode(f)[0]), at, wet) for f, (at, _, wet) in zip(reads, lines)]
            left, right = render(music, voices, voice_db, rate)
            write_wav(out, left, right, rate)
        except Exception as e:
            self.failed_at = time.time()
            print(f"[couldn't mix the stinger: {e}]")
            return None
        self.failed_at = 0.0
        self.prune()
        print(f"[stinger ready: {len(self.made(folder))} of {len(TEMPLATES)} for {full_name(station)}]")
        return out

    def read(self, text):
        """One line in the station voice, recorded once and kept."""
        voice, model = self.voice(), self.model()
        folder = os.path.join(self.root(), "lines")
        os.makedirs(folder, exist_ok=True)
        path = os.path.join(folder, hashlib.sha1(f"{voice}|{model}|{text}".encode("utf-8")).hexdigest()[:20] + ".mp3")
        if os.path.exists(path) and os.path.getsize(path) > 0:
            try:
                os.utime(path)                  # the lines every station uses stay
            except OSError:
                pass
            return path
        data = tts(text, voice, model)
        with open(path + ".part", "wb") as f:
            f.write(data)
        os.replace(path + ".part", path)
        return path

    def prune(self):
        """Keeps the twenty stations used most recently, and the reads they need."""
        def newest_first(folder):
            try:
                found = [os.path.join(folder, n) for n in os.listdir(folder)]
            except OSError:
                return []
            return sorted(found, key=lambda p: os.path.getmtime(p) if os.path.exists(p) else 0, reverse=True)
        for p in newest_first(os.path.join(self.root(), "stations"))[20:]:
            shutil.rmtree(p, ignore_errors=True)
        for p in newest_first(os.path.join(self.root(), "lines"))[400:]:
            try:
                os.remove(p)
            except OSError:
                pass

    def clear_all(self):
        """Starts over: the station voice records every line again."""
        shutil.rmtree(self.root(), ignore_errors=True)
        self.last = {}
        self.failed_at = 0.0


MAKER = StationStingers()


# ---------------------------------------------------------------- the mixing desk
def decode(path):
    """A sound file as float channels (at most two) at the sound system's rate: ([left, right], rate)."""
    import pygame
    got = pygame.mixer.get_init()
    if not got:
        raise RuntimeError("the sound system isn't running")
    a = pygame.sndarray.array(pygame.mixer.Sound(path))
    if a.dtype.kind == "f":
        x = a.astype(np.float32)
    elif a.dtype.kind == "u":
        half = float(2 ** (8 * a.dtype.itemsize - 1))
        x = (a.astype(np.float32) - half) / half
    else:
        x = a.astype(np.float32) / float(2 ** (8 * a.dtype.itemsize - 1))
    if x.ndim == 1:
        x = x[:, None]
    return [np.ascontiguousarray(x[:, c]) for c in range(min(2, x.shape[1]))], int(got[0])


def mono(chans):
    return chans[0] if len(chans) == 1 else (sum(chans) / len(chans)).astype(np.float32)


def trim(x, rate):
    """Cuts the quiet before and after the words."""
    if not len(x):
        return x
    peak = float(np.max(np.abs(x)))
    if peak <= 0:
        return x[:0]
    loud = np.nonzero(np.abs(x) > peak * 10 ** (-45 / 20))[0]
    s, e = max(0, loud[0] - int(0.01 * rate)), min(len(x), loud[-1] + int(0.03 * rate))
    return x[s:e]


def stretch(x, speed, rate):
    """Reads a line faster without making it squeaky (overlap-add with the best-matching bit of each step)."""
    if speed <= 1.01 or len(x) < int(0.1 * rate):
        return x
    size = int(0.03 * rate) // 2 * 2
    out_hop = size // 2
    in_hop = out_hop * speed
    tol = int(0.008 * rate)
    win = np.hanning(size).astype(np.float32)
    want = int(len(x) / speed)
    pad = np.concatenate([np.zeros(tol, np.float32), x, np.zeros(2 * size + 2 * tol, np.float32)])
    out = np.zeros(want + size, np.float32)
    norm = np.zeros(want + size, np.float32)
    prev, k = 0, 0
    while k * out_hop < want:
        aim = int(round(k * in_hop))
        if k == 0:
            pos = 0
        else:
            ref = pad[tol + prev + out_hop: tol + prev + out_hop + size]           # how the last piece carries on
            seg = pad[aim: aim + 2 * tol + size]                                    # candidates around the aim
            pos = aim - tol + int(np.argmax(np.correlate(seg, ref, mode="valid")))
        pos = max(0, min(pos, len(x) - 1))
        out[k * out_hop: k * out_hop + size] += pad[tol + pos: tol + pos + size] * win
        norm[k * out_hop: k * out_hop + size] += win
        prev, k = pos, k + 1
    norm[norm < 1e-3] = 1.0
    return (out / norm)[:want]


def _iir(x, stages):
    """Runs the samples through a chain of biquad filters."""
    s = x.astype(np.float64).tolist()
    for b0, b1, b2, a1, a2 in stages:
        x1 = x2 = y1 = y2 = 0.0
        for i, v in enumerate(s):
            y = b0 * v + b1 * x1 + b2 * x2 - a1 * y1 - a2 * y2
            x2, x1, y2, y1 = x1, v, y1, y
            s[i] = y
    return np.array(s, np.float32)


def _norm(b0, b1, b2, a0, a1, a2):
    return b0 / a0, b1 / a0, b2 / a0, a1 / a0, a2 / a0


def high_pass(f, rate, q=0.707):
    w = 2 * math.pi * f / rate
    c, al = math.cos(w), math.sin(w) / (2 * q)
    return _norm((1 + c) / 2, -(1 + c), (1 + c) / 2, 1 + al, -2 * c, 1 - al)


def peak_eq(f, db, q, rate):
    a, w = 10 ** (db / 40), 2 * math.pi * f / rate
    c, al = math.cos(w), math.sin(w) / (2 * q)
    return _norm(1 + al * a, -2 * c, 1 - al * a, 1 + al / a, -2 * c, 1 - al / a)


def high_shelf(f, db, rate):
    a, w = 10 ** (db / 40), 2 * math.pi * f / rate
    c, s = math.cos(w), math.sin(w)
    al = s / 2 * math.sqrt(2.0)
    sq = 2 * math.sqrt(a) * al
    return _norm(a * ((a + 1) + (a - 1) * c + sq), -2 * a * ((a - 1) + (a + 1) * c), a * ((a + 1) + (a - 1) * c - sq),
                 (a + 1) - (a - 1) * c + sq, 2 * ((a - 1) - (a + 1) * c), (a + 1) - (a - 1) * c - sq)


def compress(x, rate, threshold=-20.0, ratio=3.0, attack=0.004, release=0.09):
    ca, cr = math.exp(-1 / (attack * rate)), math.exp(-1 / (release * rate))
    env, envs = 0.0, []
    for a in np.abs(x).astype(np.float64).tolist():
        env = ca * env + (1 - ca) * a if a > env else cr * env + (1 - cr) * a
        envs.append(env)
    level = 20 * np.log10(np.array(envs) + 1e-9)
    gain = np.where(level > threshold, 10 ** ((threshold - level) * (1 - 1 / ratio) / 20), 1.0)
    return (x * gain).astype(np.float32)


def voice_chain(x, rate):
    """The radio sound the original voice has: less mud, more sparkle, evened out."""
    x = _iir(x, [high_pass(100, rate), peak_eq(400, -4, 0.8, rate), high_shelf(2500, 4.5, rate)])
    return compress(x, rate)


def echo(x, delay, rate, feedback=0.45, wet=0.55, low_pass=4500):
    """The echo on the key words: repeats that fade and darken."""
    d = max(1, int(delay * rate))
    n = len(x) + d * 6
    c = math.exp(-2 * math.pi * low_pass / rate)
    src = x.astype(np.float64).tolist() + [0.0] * (n - len(x))
    line, y, z = [0.0] * n, [0.0] * n, 0.0
    for i in range(n):
        z = (1 - c) * (line[i - d] if i >= d else 0.0) + c * z
        line[i] = src[i] + feedback * z
        y[i] = src[i] + wet * z
    return np.array(y, np.float32)


def active_db(x, rate):
    """How loud it is while there's talking (dBFS)."""
    hop = max(1, int(0.02 * rate))
    n = len(x) // hop
    if n == 0:
        return -120.0
    frames = np.sqrt(np.mean(x[:n * hop].astype(np.float64).reshape(n, hop) ** 2, axis=1))
    top = float(frames.max())
    if top <= 0:
        return -120.0
    loud = frames[frames > top * 10 ** (-35 / 20)]
    return float(10 * np.log10(np.mean(loud ** 2) + 1e-12))


def render(music, voices, voice_db, rate):
    """The new voice laid over the original music: voices are (samples, seconds in, echo). Returns (left, right)."""
    bed_l = music[0]
    bed_r = music[1] if len(music) > 1 else bed_l
    bed_seconds = len(bed_l) / rate
    placed, prev_end = [], 0.0
    for i, (v, at, wet) in enumerate(voices):
        v = trim(v, rate)
        if not len(v):
            continue
        next_at = voices[i + 1][1] if i + 1 < len(voices) else bed_seconds
        room = max(0.4, next_at - at - 0.05)
        length = len(v) / rate
        if length > room * 1.08:                       # a line much longer than its spot gets read a touch faster
            v = stretch(v, min(1.3, length / room), rate)
        v = voice_chain(v, rate)
        dry = len(v) / rate
        start = max(at, prev_end + 0.04)
        if wet:
            v = echo(v, dry + 0.04 if dry <= 0.75 else 0.42, rate)
        placed.append((int(start * rate), v))
        prev_end = start + dry
    if not placed:
        raise RuntimeError("there was no voice to mix")
    total = max(len(bed_l), max(s + len(v) for s, v in placed) + int(0.05 * rate))
    voice = np.zeros(total, np.float32)
    for s, v in placed:
        voice[s:s + len(v)] += v[:total - s]
    voice *= 10 ** ((voice_db - active_db(voice, rate)) / 20)    # exactly as loud in the music as the old voice
    left, right = voice.copy(), voice.copy()
    left[:len(bed_l)] += bed_l
    right[:len(bed_r)] += bed_r
    peak = max(float(np.max(np.abs(left))), float(np.max(np.abs(right))))
    if peak > 0.97:
        left *= 0.97 / peak
        right *= 0.97 / peak
    fade = min(total, int(0.03 * rate))
    if fade:
        ramp = np.arange(fade - 1, -1, -1, dtype=np.float32) / fade
        left[total - fade:] *= ramp
        right[total - fade:] *= ramp
    return left, right


def write_wav(path, left, right, rate):
    pcm = np.empty(len(left) * 2, np.int16)
    pcm[0::2] = np.clip(left, -1, 1) * 32767
    pcm[1::2] = np.clip(right, -1, 1) * 32767
    tmp = path + ".part"
    with wave.open(tmp, "wb") as w:
        w.setnchannels(2)
        w.setsampwidth(2)
        w.setframerate(int(rate))
        w.writeframes(pcm.tobytes())
    os.replace(tmp, path)

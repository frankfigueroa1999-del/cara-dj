"""
What's coming out of the speakers, for the visualizer: the waveform, a 64-band spectrum, bass / mids / treble,
loudness and the beat. It listens to Windows' "loopback" (whatever is playing, Spotify included), so nothing has to
be routed anywhere. It only runs while the visualizer is open.
"""
import base64
import threading
import time
import warnings

import numpy as np

RATE = 48000
FFT = 2048
BANDS = 64


def _band_edges():
    """64 bands spaced like hearing is (log), 30 Hz to 16 kHz, as FFT bin ranges."""
    freqs = np.geomspace(30, 16000, BANDS + 1)
    bins = np.clip((freqs / (RATE / FFT)).astype(int), 1, FFT // 2)
    edges = []
    for i in range(BANDS):
        a, b = int(bins[i]), int(bins[i + 1])
        edges.append((a, max(a + 1, b)))
    return edges


class Listener:
    def __init__(self):
        self.lock = threading.Lock()
        self.running = False
        self.live = False                 # real audio is coming in
        self.error = ""
        self.ring = np.zeros(FFT * 2, np.float32)
        self.hann = np.hanning(FFT).astype(np.float32)
        self.edges = _band_edges()
        self.spec = np.zeros(BANDS, np.float32)
        self.wave = np.zeros(512, np.float32)
        self.bass = self.mid = self.treb = self.vol = 0.0
        self.beats = 0
        self._avg = 0.0
        self._last_beat = 0.0
        self._peak = 0.05
        self._used = 0.0
        self._fed = 0
        self._failed_at = 0.0

    # ------------------------------------------------------------ capture
    def start(self):
        self._used = time.time()
        if self.running or time.time() - self._failed_at < 15:     # it just failed: try again in a while, not 30 times a second
            return
        self.running = True
        threading.Thread(target=self._run, daemon=True).start()

    def stop(self):
        self.running = False

    def _run(self):
        try:
            try:
                import ctypes
                ctypes.windll.ole32.CoInitializeEx(None, 0)      # this thread talks to Windows audio (COM)
            except Exception:
                pass
            import soundcard as sc
            warnings.filterwarnings("ignore", category=getattr(sc, "SoundcardRuntimeWarning", RuntimeWarning))
            speaker = sc.default_speaker()
            mic = sc.get_microphone(id=str(speaker.name), include_loopback=True)
            with mic.recorder(samplerate=RATE, channels=2, blocksize=1024) as rec:
                if not self.live or self.error:
                    print(f"[visualizer: listening to {speaker.name}]")
                self.live, self.error = True, ""
                last = checked = time.time()
                while self.running and time.time() - self._used < 6:     # stops once nobody's watching
                    if time.time() - checked > 3:                         # headphones plugged in? follow the new speakers
                        checked = time.time()
                        try:
                            if sc.default_speaker().name != speaker.name:
                                threading.Timer(0.2, self.start).start()
                                break
                        except Exception:
                            pass
                    data = rec.record(numframes=None)
                    if data is not None and len(data):
                        self.push(data)
                    now = time.time()
                    if now - last >= 1 / 60:
                        last = now
                        self.analyse()
                    if data is None or not len(data):
                        time.sleep(0.008)
        except Exception as e:
            self._failed_at = time.time()
            if str(e) != self.error:
                print(f"[visualizer: couldn't listen to the speakers ({e}), so it dreams along instead]")
            self.error = str(e)
        finally:
            self.live = False
            self.running = False

    def push(self, data):
        mono = data.mean(axis=1) if data.ndim == 2 else data
        mono = np.asarray(mono, np.float32)[-len(self.ring):]
        n = len(mono)
        with self.lock:
            self.ring = np.roll(self.ring, -n)
            self.ring[-n:] = mono
            self._fed += n

    # ------------------------------------------------------------ analysis
    def analyse(self):
        with self.lock:
            buf = self.ring[-FFT:].copy()
        level = float(np.sqrt(np.mean(buf * buf)))
        self._peak = max(level, self._peak * 0.995, 0.02)               # slow auto-gain, so quiet songs still dance
        mag = np.abs(np.fft.rfft(buf * self.hann)) / (FFT / 4)
        bands = np.array([mag[a:b].max() for a, b in self.edges], np.float32)
        db = 20 * np.log10(bands + 1e-9)
        norm = np.clip((db + 62) / 52, 0, 1)
        spec = np.maximum(norm, self.spec * 0.84)                        # bars fall back gently
        bass = float(norm[:16].mean())          # 30-150 Hz: kicks and bass lines
        mid = float(norm[16:45].mean())         # up to about 2.5 kHz: voices, snares, synths
        treb = float(norm[45:].mean())          # hats, air, sparkle
        now = self._fed / RATE                   # audio time, so a busy PC can't fake or swallow beats
        beat = bass > self._avg * 1.25 + 0.03 and bass > 0.22 and now - self._last_beat > 0.22
        self._avg = self._avg * 0.93 + bass * 0.07
        wave = buf[-1024::2] / (self._peak * 3.2)
        with self.lock:
            self.spec = spec
            self.bass = self.bass * 0.5 + bass * 0.5
            self.mid = self.mid * 0.6 + mid * 0.4
            self.treb = self.treb * 0.6 + treb * 0.4
            self.vol = min(1.0, level / self._peak * 0.6)
            self.wave = np.clip(wave, -1, 1)
            if beat:
                self.beats += 1
                self._last_beat = now

    def frame(self):
        """One frame for the window: small numbers, and the waveform and spectrum as bytes (base64)."""
        self._used = time.time()
        if not self.running:
            self.start()
        with self.lock:
            wave = ((self.wave + 1) * 127.5).astype(np.uint8).tobytes()
            spec = (self.spec * 255).astype(np.uint8).tobytes()
            return {"live": self.live, "bass": round(self.bass, 3), "mid": round(self.mid, 3), "treb": round(self.treb, 3),
                    "vol": round(self.vol, 3), "beats": self.beats,
                    "wave": base64.b64encode(wave).decode(), "spec": base64.b64encode(spec).decode()}


LISTENER = Listener()

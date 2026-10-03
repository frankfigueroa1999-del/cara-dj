"""
What's coming out of the speakers, for the visualizer: the waveform, a 64-band spectrum, bass / mids / treble,
loudness and the beat. It listens to Windows' "loopback" (whatever is playing, Spotify and Cara included), so nothing
has to be routed anywhere. It only runs while the visualizer is open.

Levels are measured the way MilkDrop does it: against the last second or so of the same song, not against a fixed
scale. A loud, heavily mastered track and a quiet acoustic one both swing from low to high, and the Spotify volume
doesn't matter. Beats come from sudden jumps in the low end (kicks, bass hits), with a threshold that follows the song.
"""
import base64
import collections
import sys
import threading
import time
import warnings

import numpy as np

RATE = 48000
FFT = 2048
BANDS = 64


def _bin(hz):
    return int(np.clip(round(hz / (RATE / FFT)), 1, FFT // 2))


def _band_edges():
    """64 bands spaced like hearing is (log), 30 Hz to 16 kHz, as FFT bin ranges."""
    freqs = np.geomspace(30, 16000, BANDS + 1)
    bins = np.clip((freqs / (RATE / FFT)).astype(int), 1, FFT // 2)
    return [(int(bins[i]), max(int(bins[i]) + 1, int(bins[i + 1]))) for i in range(BANDS)]


class Listener:
    def __init__(self):
        self.lock = threading.Lock()
        self.running = False
        self.live = False                 # real audio is coming in
        self.error = ""
        self.device = ""
        self.ring = np.zeros(FFT * 2, np.float32)
        self.hann = np.hanning(FFT).astype(np.float32)
        self.edges = _band_edges()
        self.ranges = [(_bin(30), _bin(150)), (_bin(150), _bin(2500)), (_bin(2500), _bin(16000))]
        self.onset = (_bin(30), _bin(250))
        self.spec = np.zeros(BANDS, np.float32)
        self.wave = np.zeros(512, np.float32)
        self.wave1k = np.zeros(1024, np.float32)       # full-rate, for MilkDrop presets
        self.bass = self.mid = self.treb = self.vol = 0.0
        self.beats = 0
        self._reset()
        self._used = 0.0
        self._fed = 0
        self._failed_at = 0.0

    def _reset(self):
        self._avg = None                         # the last second or so of bass / mid / treble energy
        self._short = None                       # the last quarter second of bass energy
        self._loud = 1e-4                        # the loudest the music has been lately
        self._top = -60.0                        # the loudest spectrum band lately, in dB
        self._band_avg = None                    # each band's usual level, in dB
        self._prev_log = None
        self._flux = collections.deque(maxlen=60)
        self._armed = True
        self._last_beat = -1.0

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
            if "soundcard" in sys.modules:
                # soundcard set Windows audio (COM) up in the thread that first imported it; this new thread needs
                # its own. The first import does it by itself, and doing it twice makes that import fail.
                try:
                    import ctypes
                    ctypes.windll.ole32.CoInitializeEx(None, 0)
                except Exception:
                    pass
            import soundcard as sc
            warnings.filterwarnings("ignore", category=getattr(sc, "SoundcardRuntimeWarning", RuntimeWarning))
            speaker = sc.default_speaker()
            mic = sc.get_microphone(id=str(speaker.id), include_loopback=True)     # by id: a headset's mic has the same name
            with mic.recorder(samplerate=RATE, channels=2, blocksize=1024) as rec:
                if not self.live or self.error or self.device != speaker.name:
                    print(f"[visualizer: listening to {speaker.name}]")
                self.live, self.error, self.device = True, "", str(speaker.name)
                with self.lock:
                    self._reset()
                last = checked = time.time()
                while self.running and time.time() - self._used < 6:     # stops once nobody's watching
                    if time.time() - checked > 3:                         # headphones plugged in? follow the new speakers
                        checked = time.time()
                        try:
                            if sc.default_speaker().id != speaker.id:
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
            self.error = str(e) or type(e).__name__
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
            now = self._fed / RATE                       # audio time, so a busy PC can't fake or swallow beats
        rms = float(np.sqrt(np.mean(buf * buf)))
        self._loud = max(rms, self._loud * 0.9993, 1e-4)                 # follows the song's loud parts (about 25 s)
        floor = min(1.0, max(0.0, (20 * np.log10(rms + 1e-9) + 66) / 14))   # under about -60 dB is silence or hiss
        present = floor * min(1.0, rms / (self._loud * 0.12))             # 0 in silence, 1 for anything real
        mag = np.abs(np.fft.rfft(buf * self.hann)) / (FFT / 4)
        power = mag * mag

        # bass, mids and treble, against their own last second (about 1 = usual, 2 = twice as strong)
        e = np.array([power[a:b].sum() for a, b in self.ranges]) + 1e-12
        if self._avg is None:
            self._avg, self._short = e.copy(), e[0]
        rel = e / self._avg
        self._avg = self._avg * 0.983 + e * 0.017
        self._short = self._short * 0.8 + e[0] * 0.2
        bass, mid, treb = (present * (1.0 - np.exp(-0.65 * rel))).tolist()

        # the beat: a sudden jump in the low end, above what's normal for this song lately
        low = np.log1p(mag[self.onset[0]:self.onset[1]] / self._loud * 10.0)      # the same at any volume
        flux = 0.0 if self._prev_log is None else float(np.maximum(low - self._prev_log, 0.0).sum())
        self._prev_log = low
        hist = self._flux
        thr = (np.mean(hist) + 2.0 * np.std(hist)) if len(hist) >= 20 else 1e9
        hist.append(flux)
        kick = self._short / self._avg[0]
        hit = flux > thr or (kick > 1.8 and flux > thr * 0.6)
        beat = hit and self._armed and present > 0.3 and flux > 0.2 and now - self._last_beat > 0.3
        if beat:
            self._armed = False                                          # one beat per hit...
        elif flux < thr * 0.6 and kick < 1.25:
            self._armed = True                                           # ...until the low end settles again

        # the spectrum: its shape against the loudest band lately, plus how each band moves against its own normal
        bands = np.array([mag[a:b].max() for a, b in self.edges], np.float32)
        db = 20 * np.log10(bands + 1e-9)
        self._top = max(float(db.max()), self._top - 0.06)
        if self._band_avg is None:
            self._band_avg = db.copy()
        shape = np.clip((db - (self._top - 50)) / 50, 0, 1)
        swing = np.clip(0.5 + (db - self._band_avg) / 14, 0, 1)
        self._band_avg = self._band_avg * 0.97 + db * 0.03
        norm = (0.55 * shape + 0.45 * swing) * present
        spec = np.maximum(norm, self.spec * 0.84)                        # bars fall back gently
        wave = buf[-1024::2] / (self._loud * 3.0)
        wave1k = buf[-1024:] / (self._loud * 3.5)
        with self.lock:
            self.spec = spec.astype(np.float32)
            self.bass = self.bass * 0.4 + bass * 0.6
            self.mid = self.mid * 0.5 + mid * 0.5
            self.treb = self.treb * 0.5 + treb * 0.5
            self.vol = min(1.0, rms / self._loud) ** 0.7 * present
            self.wave = np.clip(wave, -1, 1)
            self.wave1k = np.clip(wave1k, -1, 1)
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
            wave1k = ((self.wave1k + 1) * 127.5).astype(np.uint8).tobytes()
            spec = (np.clip(self.spec, 0, 1) * 255).astype(np.uint8).tobytes()
            return {"live": self.live, "bass": round(self.bass, 3), "mid": round(self.mid, 3), "treb": round(self.treb, 3),
                    "vol": round(self.vol, 3), "beats": self.beats, "device": self.device, "problem": self.error,
                    "wave": base64.b64encode(wave).decode(), "spec": base64.b64encode(spec).decode(),
                    "wave1k": base64.b64encode(wave1k).decode()}


LISTENER = Listener()

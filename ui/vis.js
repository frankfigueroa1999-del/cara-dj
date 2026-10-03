// The visualizer: a light show drawn on the graphics card, built into the app (nothing to install).
// It listens to whatever the speakers play (pc_audio.py: Windows loopback, so Spotify and Cara both count)
// and, when it can't hear anything, dreams along to a steady beat. Ten scenes in the spirit of MilkDrop and
// the old console visualizers: every frame is the last one warped, recoloured and faded, with fresh shapes
// drawn on top from the music. Scenes change on the beat.
// It also plays MilkDrop presets (the classic Winamp visualizer) through Butterchurn, an open-source WebGL MilkDrop:
// 481 come built in, and any .milk files you drop in the presets folder are converted and join the rotation.
'use strict';

const Vis = (() => {
  // ---------------------------------------------------------------- the shaders
  const VERT = `#version 300 es
out vec2 vUv;
void main() {
  vec2 p = vec2(float((gl_VertexID << 1) & 2), float(gl_VertexID & 2));
  vUv = p;
  gl_Position = vec4(p * 2.0 - 1.0, 0.0, 1.0);
}`;

  // shared by every scene: the music (uBass, uSpec, uWave...), the last frame (uPrev) and some helpers
  const HEAD = `#version 300 es
precision highp float;
in vec2 vUv;
out vec4 fragColor;
uniform sampler2D uPrev, uWave, uSpec;
uniform vec2 uRes;
uniform float uTime, uBass, uMid, uTreb, uVol, uBeat, uBeats, uFade, uHue, uSeed, uEnergy, uStep;
#define PI 3.14159265359
#define TAU 6.28318530718
vec2 toP(vec2 uv) { return (uv - 0.5) * vec2(uRes.x / uRes.y, 1.0); }
vec2 toUv(vec2 p) { return p / vec2(uRes.x / uRes.y, 1.0) + 0.5; }
mat2 rot(float a) { float c = cos(a), s = sin(a); return mat2(c, s, -s, c); }
vec3 hsv(float h, float s, float v) { vec3 k = clamp(abs(mod(h * 6.0 + vec3(0.0, 4.0, 2.0), 6.0) - 3.0) - 1.0, 0.0, 1.0); return v * mix(vec3(1.0), k, s); }
vec3 pal(float t) { return 0.5 + 0.5 * cos(TAU * (t + vec3(0.0, 0.33, 0.67))); }
float spec(float x) { return texture(uSpec, vec2(clamp(x, 0.0, 1.0), 0.5)).r; }
float wave(float x) { return texture(uWave, vec2(x, 0.5)).r * 2.0 - 1.0; }
float hash(vec2 p) { vec3 q = fract(vec3(p.xyx) * 0.1031); q += dot(q, q.yzx + 33.33); return fract((q.x + q.y) * q.z); }
float noise(vec2 p) { vec2 i = floor(p), f = fract(p), u = f * f * (3.0 - 2.0 * f);
  return mix(mix(hash(i), hash(i + vec2(1.0, 0.0)), u.x), mix(hash(i + vec2(0.0, 1.0)), hash(i + vec2(1.0, 1.0)), u.x), u.y); }
float fbm(vec2 p) { float s = 0.0, a = 0.5; for (int i = 0; i < 4; i++) { s += a * noise(p); p = rot(0.5) * p * 2.03 + 7.3; a *= 0.5; } return s; }
vec3 hueRot(vec3 c, float a) { const vec3 k = vec3(0.57735); float ca = cos(a); return c * ca + cross(k, c) * sin(a) + k * dot(k, c) * (1.0 - ca); }
vec3 back(vec2 p) { return textureLod(uPrev, toUv(p), 0.0).rgb; }
vec3 backSoft(vec2 p) { vec2 uv = toUv(p), d = vec2(0.0016 * uRes.y / uRes.x, 0.0016);
  return (textureLod(uPrev, uv, 0.0).rgb * 2.0 + textureLod(uPrev, uv + vec2(d.x, 0.0), 0.0).rgb + textureLod(uPrev, uv - vec2(d.x, 0.0), 0.0).rgb
        + textureLod(uPrev, uv + vec2(0.0, d.y), 0.0).rgb + textureLod(uPrev, uv - vec2(0.0, d.y), 0.0).rgb) / 6.0; }
vec2 kaleido(vec2 p, float n) { float a = atan(p.y, p.x), r = length(p), s = TAU / n; a = mod(a, s); a = abs(a - 0.5 * s); return vec2(cos(a), sin(a)) * r; }
float line(float d, float w) { float g = w / (abs(d) + w); return g * g; }
vec3 fade(vec3 c, float k) { return max(c * pow(k, uStep) - 0.003 * uStep, 0.0); }
vec2 zoom(vec2 p, float z, float a) { return rot(a * uStep) * p / pow(z, uStep); }
vec3 shift(vec3 c, float a) { return hueRot(c, a * uStep); }
void done(vec3 c) { fragColor = vec4(min(c, vec3(8.0)), 1.0); }
`;

  const SCENES = [
    { name: 'Neon Tunnel', src: `
void main() {
  vec2 p = toP(vUv);
  float r = length(p), a = atan(p.y, p.x);
  vec3 c = fade(shift(back(zoom(p, 1.03 + 0.04 * uBass + 0.03 * uBeat, 0.006 + 0.018 * sin(uTime * 0.21 + uSeed))), 0.012 + 0.03 * uTreb), 0.955);
  float u = abs(fract(a / PI * 1.5) * 2.0 - 1.0);
  float s = spec(0.03 + u * 0.6);
  float R = 0.07 + 0.04 * uBass + 0.08 * s * s;
  float pulse = 0.1 + 1.4 * uBeat * uBeat + 0.5 * uBass * uBass;
  vec3 col = hsv(uHue + u * 0.35 + uTime * 0.11, 0.9, 1.0) * line(r - R, 0.0035) * pulse;
  col += hsv(uHue + 0.5 + uTime * 0.11, 0.6, 1.0) * line(r - 0.03, 0.003) * uBeat * uBeat;
  done(c + col * uFade);
}` },
    { name: 'Kaleido Melt', src: `
void main() {
  vec2 p = toP(vUv);
  float r = length(p);
  float n = 6.0 + 2.0 * mod(floor(uBeats / 8.0 + uSeed), 4.0);
  vec2 k = kaleido(rot(uTime * 0.07 + uSeed) * p, n);
  vec2 q = zoom(k, 1.0 / (0.985 - 0.02 * uBass - 0.015 * uBeat), 0.03 * sin(uTime * 0.3) + 0.12 * r * (0.3 + uMid));
  vec3 c = fade(shift(backSoft(q), 0.025 + 0.05 * uBeat), 0.935);
  vec2 w = k * 3.2;
  float f = fbm(w + 1.7 * vec2(fbm(w + uTime * 0.23 + uSeed), fbm(w - uTime * 0.19 + 3.1)));
  float ink = smoothstep(0.6, 0.86, f) * (0.08 + 0.45 * uBass + 0.3 * uBeat);
  vec3 col = pal(f * 1.6 + uHue + r * 0.7) * ink;
  col += hsv(uHue + 0.3 + r, 0.75, 1.0) * line(length(k - vec2(0.22 + 0.08 * uMid, 0.0)) - 0.025 - 0.04 * uBass, 0.003) * (0.25 + 0.6 * uVol);
  done(c + col * uFade);
}` },
    { name: 'Fractal Bloom', src: `
void main() {
  vec2 p = toP(vUv);
  vec2 z = rot(uTime * 0.025 + uSeed) * p * (0.78 - 0.12 * uBass);
  vec2 c = vec2(0.9 + 0.06 * sin(uTime * 0.09 + uSeed), 0.6 + 0.07 * cos(uTime * 0.071 + uSeed * 1.7)) + 0.015 * uBeat;
  float R = 0.6 + 0.12 * uMid, m = 9.0, mi = 0.0;
  for (int i = 0; i < 9; i++) {
    z = abs(z) / clamp(dot(z, z), 0.06, 40.0) - c;
    float t = abs(length(z) - R);
    if (t < m) { m = t; mi = float(i); }
  }
  vec3 col = hsv(uHue + mi * 0.07, 0.85, 1.0) * exp(-m * 18.0) * (0.55 + 0.5 * uVol + 0.4 * uBeat);
  vec3 b = fade(shift(backSoft(zoom(p, 1.0 / 0.994, -0.003)), 0.01), 0.5);
  done(b + col * uFade * 0.45);
}` },
    { name: 'Mandala', src: `
void main() {
  vec2 p = toP(vUv);
  float r = length(p), a = atan(p.y, p.x);
  float n = 5.0 + 2.0 * mod(floor(uBeats / 8.0 + uSeed * 3.0), 4.0);
  float dir = mod(floor(uBeats / 16.0 + uSeed), 2.0) * 2.0 - 1.0;
  vec3 c = fade(shift(back(zoom(p, 1.012 + 0.035 * uBass + 0.02 * uBeat, 0.008 * dir)), 0.018), 0.95);
  float pet = abs(cos(a * n * 0.5 + uTime * 0.15 * dir));
  float s = spec(0.02 + pet * 0.5);
  float R = 0.08 + 0.05 * uBass + 0.13 * s * pet;
  float pulse = 0.12 + 1.3 * uBeat * uBeat + 0.45 * uBass * uBass;
  vec3 col = hsv(uHue + pet * 0.25 + uTime * 0.09, 0.85, 1.0) * line(r - R, 0.0035) * pulse;
  float pet2 = abs(cos(a * n - uTime * 0.4 * dir));
  float R2 = 0.035 + 0.05 * uTreb * pet2 + 0.012 * uBeat;
  col += hsv(uHue + 0.5 + uTime * 0.09, 0.7, 1.0) * line(r - R2, 0.003) * (0.1 + 0.5 * uTreb * uTreb);
  done(c + col * uFade);
}` },
    { name: 'Plasma Storm', src: `
void main() {
  vec2 p = toP(vUv);
  float t = uTime * (0.3 + 0.3 * uEnergy);
  vec2 w = p * (2.4 + 0.6 * sin(uTime * 0.05 + uSeed));
  w += 0.1 * vec2(wave(vUv.y * 0.5 + 0.25), wave(vUv.x * 0.5 + 0.25)) * (0.3 + uVol);
  float v = sin(w.x * 3.1 + t) + sin(w.y * 2.7 - t * 1.3) + sin((w.x + w.y) * 2.2 + t * 0.7 + uSeed)
          + sin(length(w + vec2(sin(t * 0.6), cos(t * 0.5))) * 4.2 - t * 1.8);
  float bands = pow(0.5 + 0.5 * sin(v * 3.0 + uTime * 1.5 + uBass * 2.5), 4.0);
  vec3 col = pal(v * 0.16 + uHue + uTime * 0.03) * bands * (0.12 + 0.3 * uBass + 0.15 * uBeat);
  vec2 q = p + 0.004 * uStep * vec2(sin(p.y * 9.0 + uTime), cos(p.x * 9.0 - uTime));
  vec3 c = fade(shift(back(zoom(q, 1.0 / 0.995, 0.003)), 0.015), 0.88);
  done(c + col * uFade);
}` },
    { name: 'Hyperspace', src: `
void main() {
  vec2 p = toP(vUv);
  float r = length(p), a = atan(p.y, p.x);
  vec3 c = fade(shift(back(zoom(p, 1.03 + 0.05 * uBass + 0.12 * uBeat, 0.002 + 0.004 * sin(uTime * 0.2 + uSeed))), 0.008 + 0.05 * uBeat), 0.9);
  vec2 g = p * 60.0;
  float h = hash(floor(g) + mod(floor(uTime * 24.0), 997.0) * 7.13);
  float star = step(0.995 - 0.003 * uTreb - 0.02 * uBeat * uBeat, h) * smoothstep(0.45, 0.0, length(fract(g) - 0.5)) * smoothstep(0.5, 0.08, r);
  vec3 col = hsv(uHue + a / TAU + r * 0.5, 0.5, 1.0) * star * (1.8 + 3.0 * uBeat * uBeat);
  col += hsv(uHue + 0.6, 0.8, 1.0) * (line(r - 0.02 - 0.3 * (1.0 - uBeat), 0.006) * 1.2 + exp(-r * 9.0) * 0.25) * uBeat;
  done(c + col * uFade);
}` },
    { name: 'Aurora', src: `
void main() {
  vec2 p = toP(vUv);
  vec3 col = vec3(0.0);
  for (int i = 0; i < 5; i++) {
    float fi = float(i);
    float x = p.x;
    float y = 0.15 * sin(x * (1.2 + 0.25 * fi) + uTime * (0.35 + 0.07 * fi) + fi * 1.7 + uSeed)
            + 0.06 * sin(x * (3.1 - 0.3 * fi) - uTime * (0.6 + 0.1 * fi) + fi)
            + 0.05 * wave(fract(x * 0.15 + 0.5 + fi * 0.13)) * (0.4 + uVol)
            + (fi - 2.0) * 0.065 * (1.0 + 0.5 * uBass);
    float d = p.y - y;
    col += hsv(uHue + fi * 0.09 + x * 0.1, 0.75, 1.0) * (line(d, 0.0025 + 0.002 * uMid + 0.003 * uBeat) + 0.03 / (1.0 + 500.0 * d * d)) * (0.35 + 0.6 * uVol + 0.9 * uBeat);
  }
  vec3 c = fade(backSoft(zoom(p, 1.0 / 0.997, 0.0) + vec2(0.0, -0.0015 * uStep)), 0.9);
  done(c + col * uFade);
}` },
    { name: 'Liquid Mirror', src: `
void main() {
  vec2 p = toP(vUv);
  float r = length(p);
  vec2 m = abs(p);
  float tw = 0.05 * sin(uTime * 0.17 + uSeed) + 0.2 * uBeat * sin(uSeed + uBeats);
  vec3 c = fade(shift(back(zoom(p, 1.0 / (1.012 + 0.02 * uBass), 0.01 + tw * r)), 0.012), 0.95);
  float a = atan(m.y, m.x) / (PI * 0.5);
  float R = 0.18 + 0.1 * wave(a * 0.5 + 0.25) * (0.4 + uVol) + 0.04 * uBass;
  vec3 col = hsv(uHue + a * 0.3, 0.8, 1.0) * line(r - R, 0.0035);
  float y = 0.08 * wave(m.x * 0.8 + 0.1) * (0.4 + uVol);
  col += hsv(uHue + 0.5 + m.x, 0.8, 1.0) * line(m.y - 0.3 - y, 0.003) * 0.8;
  done(c + col * uFade * (0.6 + uVol));
}` },
    { name: 'Third Eye', src: `
float poly(vec2 p, float n) { float a = atan(p.y, p.x), s = TAU / n; return cos(floor(0.5 + a / s) * s - a) * length(p); }
void main() {
  vec2 p = toP(vUv);
  float r = length(p);
  vec3 c = fade(shift(back(zoom(p, 1.028 + 0.04 * uBass, 0.004 + 0.008 * sin(uTime * 0.1 + uSeed))), 0.022), 0.94);
  vec3 col = vec3(0.0);
  for (int i = 0; i < 4; i++) {
    float fi = float(i);
    float spin = uTime * (0.2 + 0.1 * fi) * (mod(fi, 2.0) * 2.0 - 1.0);
    float d = poly(rot(spin) * p, 3.0 + fi) - (0.045 + 0.032 * fi) * (1.0 + 0.6 * uBass);
    col += hsv(uHue + fi * 0.18 + uTime * 0.08, 0.8, 1.0) * line(d, 0.003);
  }
  col *= 0.1 + 1.2 * uBeat * uBeat + 0.4 * uBass * uBass;
  col += hsv(uHue + 0.1, 0.35, 1.0) * line(r - 0.012 - 0.02 * uBeat, 0.004) * 0.6;
  done(c + col * uFade);
}` },
    { name: 'Hex Tunnel', src: `
vec4 hexCell(vec2 p) {
  const vec2 s = vec2(1.0, 1.7320508);
  vec4 c = floor(vec4(p, p - vec2(0.5, 1.0)) / s.xyxy) + 0.5;
  vec4 h = vec4(p - c.xy * s, p - (c.zw + 0.5) * s);
  return dot(h.xy, h.xy) < dot(h.zw, h.zw) ? vec4(h.xy, c.xy) : vec4(h.zw, c.zw + 0.5);
}
void main() {
  vec2 p = toP(vUv);
  float r = length(p);
  float a = atan(p.y, p.x) + 0.25 * sin(uTime * 0.3 + uSeed) * r;
  float z = 0.35 / max(r, 0.015) + uTime * (0.9 + 0.8 * uEnergy) + 0.3 * uBeat;
  vec4 h = hexCell(vec2(a / TAU * 9.0, z * 1.2));
  vec2 ah = abs(h.xy);
  float edge = 0.5 - max(dot(ah, vec2(0.5, 0.8660254)), ah.x);
  float id = hash(h.zw);
  float s = spec(fract(h.z / 9.0) * 0.8);
  vec3 tile = pal(id * 0.6 + uHue + z * 0.05) * (0.15 + 0.85 * s * s) * smoothstep(0.0, 0.1, edge);
  vec3 rim = hsv(uHue + 0.5 + id * 0.2, 0.7, 1.0) * line(edge, 0.02) * (0.4 + uTreb);
  vec3 col = (tile * (0.3 + 0.9 * uBass) + rim) * smoothstep(0.0, 0.25, r);
  vec3 c = fade(back(zoom(p, 1.01, 0.0)), 0.55);
  done(c + col * uFade * 0.9);
}` },
  ];

  // the last pass, onto the screen: glow, a little colour fringing on the beat, tone, vignette and grain
  const POST = `#version 300 es
precision highp float;
in vec2 vUv;
out vec4 fragColor;
uniform sampler2D uTex;
uniform vec2 uTexRes, uRes;
uniform vec3 uLod;
uniform float uTime, uBeat, uBass, uFlash, uAber;
vec3 tap(vec2 uv, float lod) { return textureLod(uTex, uv, lod).rgb; }
vec3 blur(vec2 uv, float lod) {
  vec2 d = 0.9 * exp2(lod) / uTexRes;
  return (tap(uv, lod) * 2.0 + tap(uv + d, lod) + tap(uv - d, lod) + tap(uv + vec2(d.x, -d.y), lod) + tap(uv + vec2(-d.x, d.y), lod)) / 6.0;
}
float hash(vec2 p) { vec3 q = fract(vec3(p.xyx) * 0.1031); q += dot(q, q.yzx + 33.33); return fract((q.x + q.y) * q.z); }
void main() {
  vec2 uv = vUv, d = uv - 0.5;
  float ab = uAber * (1.0 + 2.5 * uBeat);
  vec3 col = vec3(tap(uv + d * ab, 0.0).r, tap(uv, 0.0).g, tap(uv - d * ab, 0.0).b);
  vec3 bloom = blur(uv, uLod.x) * 0.35 + blur(uv, uLod.y) * 0.3 + blur(uv, uLod.z) * 0.25;
  col += bloom * (0.45 + 0.35 * uBass);
  col += uFlash * vec3(0.9, 0.8, 1.0);
  col = 1.0 - exp(-col * 1.3);
  col *= mix(0.6, 1.0, smoothstep(0.95, 0.2, length(d * vec2(1.0, 0.8))));
  col += (hash(uv * uRes + fract(uTime * 7.0) * 113.0) - 0.5) * 0.028;
  fragColor = vec4(col, 1.0);
}`;

  const UNIFORMS = ['uPrev', 'uWave', 'uSpec', 'uRes', 'uTime', 'uBass', 'uMid', 'uTreb', 'uVol', 'uBeat', 'uBeats', 'uFade', 'uHue',
    'uSeed', 'uEnergy', 'uStep', 'uTex', 'uTexRes', 'uFlash', 'uAber', 'uLod'];

  // ---------------------------------------------------------------- what it hears (or dreams)
  const A = {
    wave: new Uint8Array(512).fill(128), spec: new Uint8Array(64),
    live: false, gotAt: 0, raw: { bass: 0, mid: 0, treb: 0, vol: 0 }, pyBeats: null, pending: false,
    bass: 0, mid: 0, treb: 0, vol: 0, energy: 0.3, beat: 0, beats: 0, quiet: 0, dreamBeat: -1, dreaming: true,
    bassSlow: 0, pulse: 0, step: 1, device: '', problem: '', lastBeatAt: 0,
    hue: Math.random(), hueTo: 0,
  };
  A.hueTo = A.hue;

  let root = null, canvas = null, gl = null, c2d = null, mode = '';
  let vert = null, post = null, progs = [], fbo = [null, null], cur = 0, waveTex = null, specTex = null, pcExt = null, half = false;
  let isOpen = false, raf = 0, lastNow = 0, clock = 0, lastWarm = 0;
  let scene = -1, seen = [], sceneAt = 0, sceneBeats = 0, sceneLen = 32, seed = 0, fadeIn = 0, flash = 0, auto = true;
  let quality = 0.72, sizeKey = '', fw = 0, fh = 0;
  const perf = { t: 0, n: 0 };
  let state = null, songUri = null, fsOn = false;
  let source = 'mix';                                  // which scenes rotate: mix, milk (MilkDrop) or mine (the ten above)
  const M = { viz: null, starting: null, broken: '', canvas: null, pack: {}, names: [], user: [], active: false, name: '',
    bad: new Set(), watch: null, cut: false, w: 0, h: 0, t1k: new Uint8Array(1024).fill(128), live1k: new Uint8Array(1024).fill(128), ph: [0, 0, 0],
    checkAt: 0, loading: false, converter: null };
  let uiTimer = 0, songTimer = 0, nameTimer = 0, pollTimer = 0, polling = false, noteTimer = 0;

  const esc = s => String(s ?? '').replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
  const bridge = () => (window.pywebview && window.pywebview.api) || null;
  const hearing = () => A.live && performance.now() - A.gotAt < 600;

  function unpack(b64, out) {
    const s = atob(b64), n = Math.min(s.length, out.length);
    for (let i = 0; i < n; i++) out[i] = s.charCodeAt(i);
  }
  async function poll() {
    pollTimer = 0;
    if (!isOpen) return;
    const api = bridge();
    if (api && api.vis_frame && !polling) {
      polling = true;
      try {
        const f = await api.vis_frame();
        if (f && !f.error && isOpen) take(f);
      } catch (e) { /* the window is closing */ }
      polling = false;
    }
    if (isOpen) pollTimer = setTimeout(poll, 33);
  }
  function take(f) {
    A.live = !!f.live;
    A.device = f.device || ''; A.problem = f.problem || '';
    A.raw = { bass: +f.bass || 0, mid: +f.mid || 0, treb: +f.treb || 0, vol: +f.vol || 0 };
    if (A.live) {
      A.gotAt = performance.now();
      if (!A.dreaming) { if (f.wave) unpack(f.wave, A.wave); if (f.spec) unpack(f.spec, A.spec); if (f.wave1k) unpack(f.wave1k, M.live1k); }
    }
    if (A.pyBeats != null && f.beats > A.pyBeats) A.pending = true;
    A.pyBeats = f.beats;
  }

  // a steady 118 bpm when there's nothing to hear (or the music is playing on another device)
  function dream(t, playing) {
    const len = 60 / 118, n = Math.floor(t / len), ph = t / len - n;
    const lvl = playing ? 1 : 0.35;
    const kick = Math.exp(-ph * 6) * (n % 4 === 3 ? 0.75 : 1);
    const out = {
      bass: (0.15 + 0.65 * kick) * lvl,
      mid: (0.35 + 0.18 * Math.sin(t * 0.9) + 0.1 * Math.sin(t * 2.3)) * lvl,
      treb: (0.25 + 0.15 * Math.sin(t * 1.7 + 1) + 0.15 * Math.exp(-((ph + 0.5) % 1) * 10)) * lvl,
      vol: (0.4 + 0.35 * kick) * lvl, beat: false,
    };
    if (playing && n !== A.dreamBeat) { out.beat = A.dreamBeat >= 0; A.dreamBeat = n; }
    for (let i = 0; i < 64; i++) {
      const x = i / 63;
      const v = (0.62 - 0.38 * x) + kick * Math.max(0, 1 - x * 4) * 0.45 + 0.16 * Math.sin(i * 0.45 + t * 2.1) * Math.sin(i * 0.13 - t * 0.7)
        + (x > 0.6 ? 0.08 * Math.sin(t * 9 + i * 1.7) : 0);
      A.spec[i] = Math.max(0, Math.min(255, v * lvl * 255));
    }
    for (let i = 0; i < 512; i++) {
      const s = Math.sin(i * 0.05 + t * 7) * 0.55 * out.bass + Math.sin(i * 0.23 - t * 4.3) * 0.3 * out.mid + Math.sin(i * 0.71 + t * 11) * 0.15 * out.treb;
      A.wave[i] = Math.max(0, Math.min(255, (s + 1) * 127.5));
    }
    return out;
  }

  function tick(dt) {
    clock += dt;
    const playing = !!(state && state.now && state.now.playing);
    const paused = !!(state && state.connected && state.now && !state.now.playing);   // only calm down when we know
    if (hearing()) A.quiet = A.raw.vol < 0.03 && A.raw.bass < 0.08 ? A.quiet + dt : 0;
    A.dreaming = !hearing() || (playing && A.quiet > 4);
    let target, beat = false;
    if (A.dreaming) { target = dream(clock, !paused); beat = target.beat; }
    else { target = A.raw; beat = A.pending; }
    A.pending = false;
    const ease = (v, to, up, down) => v + (to - v) * (1 - Math.exp(-dt * (to > v ? up : down)));
    A.bass = ease(A.bass, target.bass, 30, 8);
    A.mid = ease(A.mid, target.mid, 20, 6);
    A.treb = ease(A.treb, target.treb, 25, 7);
    A.vol = ease(A.vol, target.vol, 20, 5);
    A.energy = ease(A.energy, A.vol, 0.6, 0.3);
    A.beat = beat ? 1 : A.beat * Math.exp(-dt * 5);
    A.bassSlow = ease(A.bassSlow, A.bass, 2.2, 2.2);
    A.pulse = Math.max(A.beat, Math.min(1, Math.max(0, (A.bass - A.bassSlow) * 2.8)));   // every real bass hit, beat or not
    A.step = Math.max(0.25, Math.min(3, dt * 60));
    if (beat) A.lastBeatAt = clock;
    if (beat) { A.beats++; sceneBeats++; if (A.beats % 8 === 0) A.hueTo += 0.12 + Math.random() * 0.2; }
    A.hueTo += dt * 0.012;
    A.hue += (A.hueTo - A.hue) * (1 - Math.exp(-dt * 1.5));
    fadeIn = Math.min(1, fadeIn + dt / 1.6);
    flash *= Math.exp(-dt * 7);
    const age = clock - sceneAt;
    if (auto && mode === 'gl' && !M.loading && ((beat && sceneBeats >= sceneLen && age > 12) || age > 40)) next();
  }

  // ---------------------------------------------------------------- the graphics card
  function compile(type, src) { const s = gl.createShader(type); gl.shaderSource(s, src); gl.compileShader(s); return s; }
  function program(src, name) {
    const fs = compile(gl.FRAGMENT_SHADER, src), prog = gl.createProgram();
    gl.attachShader(prog, vert); gl.attachShader(prog, fs); gl.linkProgram(prog);
    return { prog, fs, name, ok: false, bad: false, u: null };
  }
  function ready(p) {
    if (!p || p.bad) return false;
    if (p.ok) return true;
    if (pcExt && !gl.getProgramParameter(p.prog, pcExt.COMPLETION_STATUS_KHR)) return false;
    if (!gl.getProgramParameter(p.prog, gl.LINK_STATUS)) {
      console.error(`[visualizer] ${p.name} didn't compile:`, gl.getShaderInfoLog(p.fs) || gl.getProgramInfoLog(p.prog));
      p.bad = true;
      return false;
    }
    p.u = {};
    for (const n of UNIFORMS) p.u[n] = gl.getUniformLocation(p.prog, n);
    gl.useProgram(p.prog);
    gl.uniform1i(p.u.uPrev, 0); gl.uniform1i(p.u.uWave, 1); gl.uniform1i(p.u.uSpec, 2); gl.uniform1i(p.u.uTex, 0);
    p.ok = true;
    return true;
  }
  function dataTex(w, data, wrap) {
    const t = gl.createTexture();
    gl.bindTexture(gl.TEXTURE_2D, t);
    gl.texImage2D(gl.TEXTURE_2D, 0, gl.R8, w, 1, 0, gl.RED, gl.UNSIGNED_BYTE, data);
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MIN_FILTER, gl.LINEAR);
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MAG_FILTER, gl.LINEAR);
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_S, wrap);
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_T, gl.CLAMP_TO_EDGE);
    return t;
  }
  // a picture to draw into: half-float when the card can, so trails fade smoothly; mipmapped for the glow
  function target(w, h) {
    const tex = gl.createTexture();
    gl.bindTexture(gl.TEXTURE_2D, tex);
    gl.texStorage2D(gl.TEXTURE_2D, Math.floor(Math.log2(Math.max(w, h))) + 1, half ? gl.RGBA16F : gl.RGBA8, w, h);
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MIN_FILTER, gl.LINEAR_MIPMAP_LINEAR);
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MAG_FILTER, gl.LINEAR);
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_S, gl.MIRRORED_REPEAT);
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_T, gl.MIRRORED_REPEAT);
    const fb = gl.createFramebuffer();
    gl.bindFramebuffer(gl.FRAMEBUFFER, fb);
    gl.framebufferTexture2D(gl.FRAMEBUFFER, gl.COLOR_ATTACHMENT0, gl.TEXTURE_2D, tex, 0);
    const complete = gl.checkFramebufferStatus(gl.FRAMEBUFFER) === gl.FRAMEBUFFER_COMPLETE;
    gl.clearColor(0, 0, 0, 1);
    gl.clear(gl.COLOR_BUFFER_BIT);
    gl.bindFramebuffer(gl.FRAMEBUFFER, null);
    gl.generateMipmap(gl.TEXTURE_2D);
    return { tex, fb, complete };
  }
  function drop(t) { if (t) { gl.deleteFramebuffer(t.fb); gl.deleteTexture(t.tex); } }

  function setupGL() {
    pcExt = gl.getExtension('KHR_parallel_shader_compile');
    half = !!gl.getExtension('EXT_color_buffer_float');
    if (half) { const t = target(4, 4); if (!t.complete) half = false; drop(t); }
    gl.pixelStorei(gl.UNPACK_ALIGNMENT, 1);
    waveTex = dataTex(512, A.wave, gl.REPEAT);
    specTex = dataTex(64, A.spec, gl.CLAMP_TO_EDGE);
    vert = compile(gl.VERTEX_SHADER, VERT);
    post = program(POST, 'Glow');
    progs = SCENES.map(s => program(HEAD + s.src, s.name));
    fbo = [null, null]; fw = fh = 0; sizeKey = '';
    mode = 'gl';
  }
  function init() {
    canvas = root.querySelector('canvas');
    try {
      gl = canvas.getContext('webgl2', { alpha: false, antialias: false, depth: false, stencil: false, premultipliedAlpha: false,
        preserveDrawingBuffer: false, powerPreference: 'high-performance' });
    } catch (e) { gl = null; }
    if (gl) {
      try { setupGL(); } catch (e) { console.error('[visualizer]', e); gl = null; }
      canvas.addEventListener('webglcontextlost', e => { e.preventDefault(); mode = 'lost'; });
      canvas.addEventListener('webglcontextrestored', () => { try { setupGL(); } catch (e) { mode = 'lost'; } });
    }
    if (!gl) { c2d = canvas.getContext('2d', { alpha: false }); mode = c2d ? '2d' : 'none'; root.classList.add('simple'); }
  }

  function sizeUp() {
    const dpr = Math.min(window.devicePixelRatio || 1, 1.25);
    let W = Math.max(2, Math.round(root.clientWidth * dpr)), H = Math.max(2, Math.round(root.clientHeight * dpr));
    const big = W * H / 2.5e6;
    if (big > 1) { W = Math.round(W / Math.sqrt(big)); H = Math.round(H / Math.sqrt(big)); }
    const shrink = Math.max(0.5, Math.min(1, quality / 0.72));          // a slow PC also gets a smaller screen pass
    const w = Math.max(2, Math.round(W * shrink)), h = Math.max(2, Math.round(H * shrink));
    let a = Math.max(2, Math.round(W * quality)), b = Math.max(2, Math.round(H * quality));
    const over = a * b / 1.5e6;
    if (over > 1) { a = Math.round(a / Math.sqrt(over)); b = Math.round(b / Math.sqrt(over)); }
    const key = `${w}x${h}:${a}x${b}`;
    if (key === sizeKey) return;
    sizeKey = key;
    canvas.width = w; canvas.height = h;
    if (a !== fw || b !== fh || !fbo[0]) {
      drop(fbo[0]); drop(fbo[1]);
      fw = a; fh = b;
      fbo = [target(fw, fh), target(fw, fh)];
      cur = 0;
    }
  }

  // shaders compile in the background; check on the next one now and then (without blocking a frame)
  function warm() {
    if (!pcExt && clock - lastWarm < 0.4) return;
    lastWarm = clock;
    for (const p of progs) {
      if (p.ok || p.bad) continue;
      ready(p);
      if (!pcExt) break;
    }
  }

  function drawGL(raw) {
    if (gl.isContextLost() || !ready(post)) return;
    warm();
    if (scene < 0 || !progs[scene].ok) {
      const i = progs.findIndex(p => p.ok);
      if (i < 0) return;
      go(i);
    }
    sizeUp();
    const p = progs[scene], u = p.u, src = fbo[cur], dst = fbo[1 - cur];
    gl.activeTexture(gl.TEXTURE1); gl.bindTexture(gl.TEXTURE_2D, waveTex);
    gl.texSubImage2D(gl.TEXTURE_2D, 0, 0, 0, 512, 1, gl.RED, gl.UNSIGNED_BYTE, A.wave);
    gl.activeTexture(gl.TEXTURE2); gl.bindTexture(gl.TEXTURE_2D, specTex);
    gl.texSubImage2D(gl.TEXTURE_2D, 0, 0, 0, 64, 1, gl.RED, gl.UNSIGNED_BYTE, A.spec);
    gl.activeTexture(gl.TEXTURE0); gl.bindTexture(gl.TEXTURE_2D, src.tex);

    // 1: the scene, into the next picture
    gl.bindFramebuffer(gl.FRAMEBUFFER, dst.fb);
    gl.viewport(0, 0, fw, fh);
    gl.useProgram(p.prog);
    gl.uniform2f(u.uRes, fw, fh);
    gl.uniform1f(u.uTime, clock);
    gl.uniform1f(u.uBass, A.bass); gl.uniform1f(u.uMid, A.mid); gl.uniform1f(u.uTreb, A.treb); gl.uniform1f(u.uVol, A.vol);
    gl.uniform1f(u.uBeat, A.pulse); gl.uniform1f(u.uBeats, A.beats % 4096); gl.uniform1f(u.uEnergy, A.energy);
    gl.uniform1f(u.uStep, A.step);
    gl.uniform1f(u.uFade, fadeIn); gl.uniform1f(u.uHue, A.hue % 1); gl.uniform1f(u.uSeed, seed);
    gl.drawArrays(gl.TRIANGLES, 0, 3);

    // 2: onto the screen
    gl.bindFramebuffer(gl.FRAMEBUFFER, null);
    gl.bindTexture(gl.TEXTURE_2D, dst.tex);
    gl.generateMipmap(gl.TEXTURE_2D);
    gl.viewport(0, 0, canvas.width, canvas.height);
    gl.useProgram(post.prog);
    const v = post.u;
    gl.uniform2f(v.uTexRes, fw, fh); gl.uniform2f(v.uRes, canvas.width, canvas.height);
    gl.uniform1f(v.uTime, clock); gl.uniform1f(v.uBeat, A.pulse); gl.uniform1f(v.uBass, A.bass);
    gl.uniform1f(v.uFlash, flash); gl.uniform1f(v.uAber, 0.004);
    const lod = f => Math.max(0, Math.log2(f * fh));
    gl.uniform3f(v.uLod, lod(0.008), lod(0.025), lod(0.06));
    gl.drawArrays(gl.TRIANGLES, 0, 3);
    cur = 1 - cur;

    // too slow for this PC? draw fewer pixels
    if (raw < 0.25) {
      perf.t += raw; perf.n++;
      if (perf.t > 2.5) {
        const avg = perf.t / perf.n;
        perf.t = perf.n = 0;
        if (avg > 1 / 38 && quality > 0.36) { quality = Math.max(0.35, quality * 0.8); sizeKey = ''; }
      }
    }
  }

  // the fallback when the graphics card can't do WebGL 2: spectrum rays with canvas feedback
  function draw2D() {
    const w = Math.max(2, Math.round(root.clientWidth * 0.6)), h = Math.max(2, Math.round(root.clientHeight * 0.6));
    if (canvas.width !== w || canvas.height !== h) { canvas.width = w; canvas.height = h; }
    const g = c2d;
    g.globalCompositeOperation = 'source-over';
    g.save(); g.globalAlpha = 0.92; g.translate(w / 2, h / 2); g.rotate(0.006 + A.bass * 0.01);
    const z = 1.02 + A.bass * 0.04; g.scale(z, z); g.drawImage(canvas, -w / 2, -h / 2); g.restore();
    g.fillStyle = 'rgba(0,0,0,0.08)'; g.fillRect(0, 0, w, h);
    g.globalCompositeOperation = 'lighter';
    g.lineWidth = Math.max(1.5, h / 300);
    const cx = w / 2, cy = h / 2, R = h * (0.08 + 0.05 * A.bass);
    for (let i = 0; i < 64; i++) {
      const v = A.spec[i] / 255, ang = (i / 64) * Math.PI, L = R + v * h * 0.22;
      g.strokeStyle = `hsla(${((A.hue + i / 128) * 360) % 360}, 100%, 55%, ${0.15 + v * 0.45})`;
      for (const s of [1, -1]) {
        const a = Math.PI / 2 + s * ang;
        g.beginPath(); g.moveTo(cx + Math.cos(a) * R, cy + Math.sin(a) * R); g.lineTo(cx + Math.cos(a) * L, cy + Math.sin(a) * L); g.stroke();
      }
    }
  }

  function frame(now) {
    raf = requestAnimationFrame(frame);
    const raw = (now - lastNow) / 1000;
    lastNow = now;
    const dt = Math.min(0.1, Math.max(0, raw));
    tick(dt);
    if (mode === 'gl') { if (M.active && M.viz) drawMilk(dt, raw); else drawGL(raw); }
    else if (mode === '2d') draw2D();
  }

  // ---------------------------------------------------------------- MilkDrop (Butterchurn)
  const call = (name, ...args) => { const api = bridge(); return api && api[name] ? Promise.resolve(api[name](...args)) : Promise.resolve(null); };
  function milkStart() {
    if (M.starting) return M.starting;
    M.starting = (async () => {
      const lib = (await import('./vendor/butterchurn.min.js')).default;
      const [pack, images] = await Promise.all([
        fetch('presets/pack.json').then(r => r.json()),
        fetch('presets/images.json').then(r => r.json()).catch(() => null)]);
      M.pack = pack; M.names = Object.keys(pack);
      milkSize(true);
      M.viz = lib.createVisualizer(null, M.canvas, { width: M.w, height: M.h, pixelRatio: 1, textureRatio: 1 });
      if (images) M.viz.loadExtraImages(images);
      const g = M.viz.gl, compile = g && g.compileShader.bind(g);          // notice a preset's shader failing to compile
      if (compile) g.compileShader = sh => { compile(sh); if (M.watch && !M.watch.failed && !g.getShaderParameter(sh, g.COMPILE_STATUS)) M.watch.failed = (g.getShaderInfoLog(sh) || 'compile error').split('\n')[0].slice(0, 160); };
      milkUser();
      return true;
    })().catch(e => { M.broken = String(e && e.message || e); console.error('[visualizer] MilkDrop could not start:', e); return false; });
    return M.starting;
  }
  async function milkUser() {
    const r = await call('milk_list').catch(() => null);
    M.user = (r && Array.isArray(r.items)) ? r.items.filter(x => !x.failed) : [];
  }
  function milkSize(force) {
    const dpr = Math.min(window.devicePixelRatio || 1, 1.25);
    let w = Math.max(2, Math.round(root.clientWidth * dpr)), h = Math.max(2, Math.round(root.clientHeight * dpr));
    const big = w * h / (quality < 0.6 ? 0.8e6 : 1.6e6);
    if (big > 1) { w = Math.round(w / Math.sqrt(big)); h = Math.round(h / Math.sqrt(big)); }
    if (!force && w === M.w && h === M.h) return;
    M.w = w; M.h = h; M.canvas.width = w; M.canvas.height = h;
    if (M.viz) M.viz.setRendererSize(w, h);
  }
  // turns a .milk file into what Butterchurn plays: the equations stay as they are, the shaders go HLSL to GLSL
  async function milkConvert(text) {
    const script = src => new Promise((ok, fail) => {
      const sc = document.createElement('script');
      sc.src = src; sc.onload = ok; sc.onerror = () => fail(new Error(`couldn't load ${src}`));
      document.head.appendChild(sc);
    });
    if (!M.converter) {
      M.converter = Promise.all([script('vendor/milk-utils.min.js'), script('vendor/hlslparser.js')])
        .then(() => window.HLSLParserModule())
        .then(mod => ({ utils: window.MilkUtils, parse: mod.cwrap('parseHLSL', 'string', ['string', 'string', 'string']) }));
    }
    const { utils, parse } = await M.converter;
    const parts = utils.splitPreset(text);
    const shader = (src, kind) => {
      if (!src || !src.trim()) return '';
      const name = 'main_shader_sentinel';
      const out = parse(utils.prepareShader(src).replace('float4 shader_body (', `float4 ${name} (`), name, 'fs');
      if (/^(parsing|code generation) failed/.test(out)) throw new Error(`its shader didn't convert (${out})`);
      return fitShader(utils.processUnOptimizedShader(out), kind);
    };
    return {
      version: parts.presetVersion, baseVals: parts.baseVals, warp: shader(parts.warp, 'warp'), comp: shader(parts.comp, 'comp'),
      init_eqs_eel: parts.presetInit || '', frame_eqs_eel: parts.perFrame || '', pixel_eqs_eel: parts.perVertex || '',
      shapes: parts.shapes.map(x => ({ baseVals: x.baseVals, init_eqs_eel: x.init_eqs_str || '', frame_eqs_eel: x.frame_eqs_str || '' })),
      waves: parts.waves.map(x => ({ baseVals: x.baseVals, init_eqs_eel: x.init_eqs_str || '', frame_eqs_eel: x.frame_eqs_str || '',
        point_eqs_eel: x.point_eqs_str || '' })),
    };
  }
  // Butterchurn writes MilkDrop's per-pixel inputs (rad, ang, ...) inside its own main() and declares the standard
  // textures itself; the converted helper code lives outside main(). This makes the two fit together.
  const BUILT_IN_SAMPLERS = new Set(['sampler_main', 'sampler_fw_main', 'sampler_fc_main', 'sampler_pw_main', 'sampler_pc_main',
    'sampler_blur1', 'sampler_blur2', 'sampler_blur3', 'sampler_noise_lq', 'sampler_noise_lq_lite', 'sampler_noise_mq',
    'sampler_noise_hq', 'sampler_pw_noise_lq', 'sampler_noisevol_lq', 'sampler_noisevol_hq']);
  const TYPES = 'float|int|uint|bool|vec[234]|ivec[234]|uvec[234]|bvec[234]|mat[234]';
  function fitShader(text, kind) {
    const at = text.indexOf('shader_body');
    if (at < 0) return text;
    let head = text.slice(0, at);
    const rest = text.slice(at), body = rest.slice(rest.indexOf('{') + 1, rest.lastIndexOf('}'));
    const first = [], inits = [];
    // the standard textures are declared already; any other texture has to be a uniform
    head = head.replace(/^[ \t]*(?:uniform\s+)?(sampler2D|sampler3D)\s+(\w+)\s*;[ \t]*$/gm,
      (m, type, n) => (BUILT_IN_SAMPLERS.has(n) ? '' : `uniform ${type} ${n};`));
    // globals set from anything but constants: declare them out here, set them at the top of the body
    let depth = 0;
    head = head.split('\n').map(line => {
      const was = depth;
      depth += (line.match(/{/g) || []).length - (line.match(/}/g) || []).length;
      if (was !== 0) return line;
      const m = line.match(new RegExp(`^\\s*(?:static\\s+)?((?:(?:lowp|mediump|highp)\\s+)?(?:${TYPES}))\\s+(\\w+)\\s*=\\s*(.+);\\s*$`));
      if (!m || /^\s*const\b/.test(line)) return line;
      inits.push(`${m[2]} = ${m[3]};`);
      return `${m[1]} ${m[2]};`;
    }).join('\n');
    // rad, ang, hue_shader and uv_orig: helper code gets copies the body fills in first
    const types = { rad: 'float', ang: 'float', hue_shader: 'vec3', uv_orig: 'vec2' };
    const used = (kind === 'comp' ? ['rad', 'ang', 'hue_shader', 'uv_orig'] : ['rad', 'ang']).filter(n => new RegExp(`\\b${n}\\b`).test(head));
    for (const n of used) {
      head = head.replace(new RegExp(`\\b${n}\\b`, 'g'), `md_${n}`);
      first.push(`md_${n} = ${n};`);
    }
    if (used.length) head = used.map(n => `${types[n]} md_${n};`).join('\n') + '\n' + head;
    // MilkDrop lets shaders write to q1-q32; here they're uniforms (q1 is a macro for _qa.x): use writable copies
    let main = body;
    const letters = 'abcdefgh', vecs = new Set();
    for (let n = 1; n <= 32; n++) if (new RegExp(`\\bq${n}\\b`).test(head + body)) vecs.add(letters[(n - 1) >> 2]);
    for (const c of letters) if (new RegExp(`\\b_q${c}\\b`).test(head + body)) vecs.add(c);
    let defs = '';
    for (const c of vecs) {
      defs += `vec4 md_q${c};\n`;
      for (let k = 0; k < 4; k++) { const n = letters.indexOf(c) * 4 + k + 1; defs += `#undef q${n}\n#define q${n} md_q${c}.${'xyzw'[k]}\n`; }
      const re = new RegExp(`\\b_q${c}\\b`, 'g');
      head = head.replace(re, `md_q${c}`); main = main.replace(re, `md_q${c}`);
      first.push(`md_q${c} = _q${c};`);
    }
    head = defs + head;
    return `${head}shader_body {\n${first.concat(inits).join('\n')}\n${main}\n}`;
  }
  async function milkPreset(item) {
    if (!item.user) return M.pack[item.name];
    const got = await call('milk_get', item.path);
    if (!got || got.error) throw new Error((got && got.error) || 'the preset file went missing');
    if (got.preset) return got.preset;
    const preset = await milkConvert(got.text);
    call('milk_save', item.path, got.mtime, preset, '');
    return preset;
  }
  const pretty = name => name.replace(/\.(milk|json)$/i, '').replace(/^[_$\s]+/, '').replace(/\s+/g, ' ').trim();
  async function goMilk(item, back = false) {
    M.loading = true;
    try {
      if (!(await milkStart())) throw new Error(M.broken || 'MilkDrop is unavailable');
      const preset = await milkPreset(item);
      if (!preset) throw new Error('empty preset');
      const blend = M.active && !M.cut ? 2.7 : 0;
      M.cut = false;
      M.watch = item.user ? { failed: '' } : null;
      await M.viz.loadPreset(preset, blend);
      const failed = M.watch && M.watch.failed;
      M.watch = null;
      if (failed) throw new Error(`its shader has an error (${failed})`);
      M.active = true; M.name = item.name;
      root.classList.add('milk');
      M.checkAt = item.user ? performance.now() + 300 : 0;
      if (!back) { seen.push(item); if (seen.length > 40) seen.shift(); }
      sceneAt = clock; sceneBeats = 0; sceneLen = 24 + 8 * Math.floor(Math.random() * 4);
      if (!blend) flash = 0.3;
      say(pretty(item.name));
    } catch (e) {
      console.warn('[visualizer] skipped a MilkDrop preset:', item.name, e);
      M.bad.add(item.key);
      if (item.user) call('milk_save', item.path, item.mtime || 0, null, String(e && e.message || e));
      M.loading = false; M.cut = true;
      if (!M.active && scene < 0) return;
      return next();
    }
    M.loading = false;
  }
  function milkWave(dt) {
    if (!A.dreaming) return M.live1k;
    const t = M.t1k, f = [55, 440, 3520], amp = [0.6 * A.bass, 0.25 * A.mid, 0.12 * A.treb];
    for (let k = 0; k < 3; k++) M.ph[k] = (M.ph[k] + dt * f[k] * 6.2832) % 6.2832;
    for (let i = 0; i < 1024; i++) {
      const tt = i / 48000;
      let v = 0;
      for (let k = 0; k < 3; k++) v += Math.sin(6.2832 * f[k] * tt + M.ph[k]) * amp[k];
      t[i] = Math.max(0, Math.min(255, 128 + 120 * v));
    }
    return t;
  }
  function drawMilk(dt, raw) {
    milkSize(false);
    const wave = milkWave(dt);
    try {
      M.viz.render({ audioLevels: { timeByteArray: wave, timeByteArrayL: wave, timeByteArrayR: wave }, elapsedTime: dt || 1 / 60 });
    } catch (e) { console.warn('[visualizer] MilkDrop stumbled:', e); M.bad.add('milk:' + M.name); next(); return; }
    if (M.checkAt && performance.now() > M.checkAt) {          // one of yours didn't compile? skip it
      M.checkAt = 0;
      const err = M.viz.gl && M.viz.gl.getError();
      if (err) {
        const item = seen[seen.length - 1];
        console.warn('[visualizer] a converted preset has a graphics error, skipping:', M.name, err);
        if (item && item.user) { M.bad.add(item.key); call('milk_save', item.path, item.mtime || 0, null, 'graphics error ' + err); }
        next();
      }
    }
    if (raw < 0.25) {
      perf.t += raw; perf.n++;
      if (perf.t > 2.5) {
        const avg = perf.t / perf.n;
        perf.t = perf.n = 0;
        if (avg > 1 / 38 && quality > 0.36) { quality = Math.max(0.35, quality * 0.8); sizeKey = ''; milkSize(true); }
      }
    }
  }
  function milkPick() {
    const recent = new Set(seen.slice(-12).filter(x => x && x.key).map(x => x.key));
    const mine = M.user.filter(x => !M.bad.has('user:' + x.path) && !recent.has('user:' + x.path));
    if (mine.length && Math.random() < 0.5) {                 // your own presets come up half the time
      const x = mine[Math.floor(Math.random() * mine.length)];
      return { key: 'user:' + x.path, name: x.name, path: x.path, mtime: x.mtime, user: true };
    }
    const names = M.names.filter(n => !M.bad.has('milk:' + n) && !recent.has('milk:' + n));
    if (!names.length) return null;
    const n = names[Math.floor(Math.random() * names.length)];
    return { key: 'milk:' + n, name: n };
  }
  function setSource(s) {
    source = s;
    const b = root.querySelector('[data-v=source]');
    b.textContent = { mix: 'Mix', milk: 'MilkDrop', mine: 'Cara' }[s];
    say({ mix: 'Mixing MilkDrop and Cara scenes', milk: 'MilkDrop presets only', mine: "Cara's scenes only" }[s]);
    if ((s === 'milk' && !M.active) || (s === 'mine' && M.active)) next();
  }

  // ---------------------------------------------------------------- scenes
  function go(i, back = false) {
    if (M.active) { M.active = false; root.classList.remove('milk'); }
    scene = i;
    if (!back) { seen.push(i); if (seen.length > 40) seen.shift(); }
    sceneAt = clock; sceneBeats = 0; sceneLen = 24 + 8 * Math.floor(Math.random() * 4);
    seed = Math.random() * 6.283; fadeIn = 0; flash = 0.3;
    say(SCENES[i].name);
  }
  function next() {
    if (M.loading) return;
    const milkOk = M.viz && !M.broken && M.names.length;
    const wantMilk = source === 'milk' ? 1 : source === 'mine' ? 0 : 0.6;
    if (mode === 'gl' && Math.random() < wantMilk) {
      if (milkOk) { const item = milkPick(); if (item) return goMilk(item); }
      else if (!M.broken && source === 'milk') { milkStart().then(ok => ok && next()); return; }
    }
    const ok = progs.map((p, i) => (p.ok ? i : -1)).filter(i => i >= 0);
    if (!ok.length) return;
    const recent = seen.filter(x => typeof x === 'number').slice(-Math.min(5, ok.length - 1));
    const pool = ok.filter(i => !recent.includes(i));
    go((pool.length ? pool : ok)[Math.floor(Math.random() * (pool.length || ok.length))]);
  }
  function prev() {
    if (M.loading) return;
    if (seen.length < 2) return next();
    seen.pop();
    const x = seen[seen.length - 1];
    if (typeof x === 'number') go(x, true); else goMilk(x, true);
  }
  function setAuto(on) {
    auto = on;
    const b = root.querySelector('[data-v=auto]');
    b.classList.toggle('on', on);
    say(on ? 'Changing with the music' : 'Holding this scene');
  }

  // ---------------------------------------------------------------- the overlay
  function say(text) {
    const el = root.querySelector('.vis-scene');
    el.textContent = text; el.classList.add('on');
    clearTimeout(nameTimer); nameTimer = setTimeout(() => el.classList.remove('on'), 2400);
  }
  function note() {
    const how = mode === '2d' ? "Simple mode: this PC's graphics can't run the full show"
      : mode !== 'gl' ? "This PC's graphics can't draw the visualizer"
      : !A.dreaming ? `Listening to ${A.device || 'your speakers'}`
      : A.live ? `Dreaming along: no sound from ${A.device || 'this PC'} right now`
      : A.problem ? `Dreaming along: couldn't listen to the speakers (${A.problem})`
      : 'Dreaming along: starting to listen…';
    const milk = M.names.length ? `  ·  ${M.names.length} MilkDrop presets${M.user.length ? ` + ${M.user.length} of yours` : ''}` : '';
    root.querySelector('.vis-note').textContent = mode === 'gl' ? `${how}${milk}  ·  ← → scenes  ·  F full screen  ·  Esc close` : `${how}  ·  Esc close`;
  }
  function showUI() {
    root.classList.add('ui');
    note();
    clearTimeout(uiTimer);
    uiTimer = setTimeout(function hideUI() {
      if (root.querySelector('.vis-top').matches(':hover')) { uiTimer = setTimeout(hideUI, 1200); return; }
      root.classList.remove('ui');
    }, 2600);
  }
  function song(t) {
    const el = root.querySelector('.vis-song'), url = t.art || t.artMid;
    el.querySelector('.art').innerHTML = url ? `<img src="${esc(url)}" alt="" onload="this.classList.add('ok')">` : '';
    el.querySelector('b').textContent = t.title || '';
    el.querySelector('small').textContent = t.artistLine || t.artist || '';
    el.classList.add('on');
    clearTimeout(songTimer); songTimer = setTimeout(() => el.classList.remove('on'), 7000);
  }
  function caption(d) {
    const el = root.querySelector('.vis-cara');
    let html = '';
    if (d && d.speaking) {
      if (d.kind === 'duo' && d.duo && d.duo.length) html = d.duo.slice(-3).map(r => `<div><b>${esc(r.who)}</b> ${esc(r.text)}</div>`).join('');
      else if (d.line) html = esc(d.line.replace(/\[[^\]]*\]/g, '').replace(/\s+/g, ' ').trim());
    }
    if (html) {
      if (el.dataset.html !== html) { el.innerHTML = html; el.dataset.html = html; }
      el.classList.add('on');
    } else el.classList.remove('on');
  }
  async function fullscreen() {
    const api = bridge();
    if (api && api.fullscreen) { fsOn = !fsOn; try { await api.fullscreen(); } catch (e) { /* ignore */ } }
    else if (document.fullscreenElement) document.exitFullscreen().catch(() => {});
    else if (root.requestFullscreen) root.requestFullscreen().catch(() => {});
  }

  function build() {
    if (root) return;
    const ic = name => (typeof icon === 'function' ? icon(name, 20) : '');
    root = document.createElement('div');
    root.id = 'vis';
    root.innerHTML = `<canvas></canvas><canvas class="milk"></canvas>
      <div class="vis-scene"></div>
      <div class="vis-song"><div class="art"></div><div class="ell"><b class="ell"></b><small class="ell"></small></div></div>
      <div class="vis-cara"></div>
      <div class="vis-ui">
        <div class="vis-note"></div>
        <div class="vis-top">
          <button class="chip" data-v="source" title="Which scenes play: MilkDrop presets, Cara's own, or a mix (M)">Mix</button>
          <button class="round" data-v="folder" title="Your MilkDrop presets folder: drop .milk files in it">${ic('folder')}</button>
          <button class="chip on" data-v="auto" title="Change scenes with the music (A)">Auto</button>
          <button class="round" data-v="prev" title="Previous scene (←)">${ic('back')}</button>
          <button class="round" data-v="next" title="Next scene (→)">${ic('forward')}</button>
          <button class="round" data-v="full" title="Full screen (F)">${ic('fullscreen')}</button>
          <button class="round" data-v="close" title="Close (Esc)">${ic('close')}</button>
        </div>
      </div>`;
    document.body.appendChild(root);
    root.addEventListener('mousemove', showUI);
    root.addEventListener('mousedown', showUI);
    M.canvas = root.querySelector('canvas.milk');
    root.querySelectorAll('canvas').forEach(c => c.addEventListener('dblclick', fullscreen));
    root.addEventListener('click', e => {
      const b = e.target.closest('[data-v]');
      if (!b) return;
      e.stopPropagation();
      const v = b.dataset.v;
      if (v === 'close') hide();
      else if (v === 'next') next();
      else if (v === 'prev') prev();
      else if (v === 'full') fullscreen();
      else if (v === 'auto') setAuto(!auto);
      else if (v === 'source') setSource({ mix: 'milk', milk: 'mine', mine: 'mix' }[source]);
      else if (v === 'folder') call('open_presets').then(() => milkUser());
    });
    document.addEventListener('keydown', e => {
      if (!isOpen || e.ctrlKey || e.altKey || e.metaKey) return;
      if (/INPUT|TEXTAREA|SELECT/.test((document.activeElement && document.activeElement.tagName) || '')) return;
      const k = e.key.toLowerCase();
      if (k === 'arrowright' || k === 'n') { next(); showUI(); }
      else if (k === 'arrowleft' || k === 'p') { prev(); showUI(); }
      else if (k === 'f') { e.preventDefault(); fullscreen(); }
      else if (k === 'a') { setAuto(!auto); showUI(); }
      else if (k === 'm' && mode === 'gl') { setSource({ mix: 'milk', milk: 'mine', mine: 'mix' }[source]); showUI(); }
    });
    init();
  }

  function show() {
    if (isOpen) return;
    build();
    isOpen = true;
    root.classList.add('open');
    lastNow = performance.now();
    if (!clock) clock = 20 + Math.random() * 200;
    if (mode === 'gl') {
      ready(post);
      const start = Math.floor(Math.random() * SCENES.length);
      for (let k = 0; k < SCENES.length; k++) {
        const i = (start + k) % SCENES.length;
        if (ready(progs[i])) { go(i); break; }
      }
    }
    if (mode === 'gl') { milkStart(); if (M.viz) milkUser(); }
    cancelAnimationFrame(raf);
    raf = requestAnimationFrame(frame);
    poll();
    showUI();
    clearInterval(noteTimer);
    noteTimer = setInterval(() => { if (root.classList.contains('ui')) note(); }, 1000);
    songUri = null;
    update(state);
  }
  function hide() {
    if (!isOpen) return;
    isOpen = false;
    root.classList.remove('open', 'ui');
    cancelAnimationFrame(raf);
    clearTimeout(pollTimer); clearInterval(noteTimer);
    if (fsOn) fullscreen();
    const api = bridge();
    if (api && api.vis_stop) Promise.resolve().then(() => api.vis_stop()).catch(() => {});
  }
  function update(s) {
    state = s || state;
    if (!isOpen || !state) return;
    const t = state.now && state.now.track, uri = t ? t.uri : '';
    if (uri !== songUri) {
      songUri = uri;
      if (t && M.active && M.viz) { try { M.viz.launchSongTitleAnim(`${t.artistLine || t.artist} - ${t.title}`); } catch (e) { song(t); } }
      else if (t) song(t);
    }
    caption(state.dj);
  }

  return {
    open: show,
    close: hide,
    get isOpen() { return isOpen; },
    update,
    next: () => isOpen && next(),
    prev: () => isOpen && prev(),
    scenes: SCENES.map(s => s.name),
    pick: i => { if (isOpen && mode === 'gl' && ready(progs[i])) go(i); },
    quality: q => { quality = Math.max(0.2, Math.min(2, +q || quality)); sizeKey = ''; },
    // for testing: draw n frames right now at a fixed step, from a blank picture if asked
    step: (n, dt = 1 / 60, fresh = false, feed = null) => {
      if (mode !== 'gl') return;
      cancelAnimationFrame(raf);
      if (fresh) { drop(fbo[0]); drop(fbo[1]); fbo = [null, null]; sizeKey = ''; fw = fh = 0; }
      for (let i = 0; i < n; i++) {
        const f = feed && feed(i);
        if (f) { take(f); A.gotAt = performance.now(); } else A.gotAt = 0;
        tick(dt); drawGL(dt);
      }
      gl.readPixels(0, 0, 1, 1, gl.RGBA, gl.UNSIGNED_BYTE, new Uint8Array(4));
    },
    milk: async (name, user = false) => {
      if (!isOpen || mode !== 'gl') return false;
      if (!(await milkStart())) return false;
      const item = user ? M.user.find(x => x.name === name || x.path === name) : null;
      if (user && !item) return false;
      await goMilk(user ? { key: 'user:' + item.path, name: item.name, path: item.path, mtime: item.mtime, user: true }
        : { key: 'milk:' + name, name: name || M.names[Math.floor(Math.random() * M.names.length)] });
      return M.active;
    },
    milkNames: () => M.names.slice(),
    milkStep: (n, dt = 1 / 60, feed = null) => {
      if (!M.active || !M.viz) return false;
      cancelAnimationFrame(raf);
      for (let i = 0; i < n; i++) {
        const f = feed && feed(i);
        if (f) { take(f); A.gotAt = performance.now(); } else A.gotAt = 0;
        tick(dt); drawMilk(dt, dt);
      }
      return true;
    },
    info: () => ({ mode, scene: M.active ? 'MilkDrop: ' + M.name : scene >= 0 ? SCENES[scene].name : '', ready: progs.filter(p => p.ok).length,
      milk: { ready: !!M.viz, broken: M.broken, presets: M.names.length, yours: M.user.length, bad: [...M.bad], active: M.active, source },
      failed: progs.filter(p => p.bad).map(p => p.name), quality, size: [fw, fh], canvas: canvas ? [canvas.width, canvas.height] : null,
      dreaming: A.dreaming, live: A.live, half, beats: A.beats }),
  };
})();

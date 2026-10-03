# Non Stop Pop DJ for Windows

The desktop version of Cara DJ, packaged as one `NonStopPopDJ.exe`.

**Download:** the latest build is on the [Releases page](../../releases/tag/desktop-latest).
Double-click it. Windows may warn that it protected your PC because the app isn't signed:
click **More info**, then **Run anyway**.

Your keys (Spotify, ElevenLabs, Gemini) go in Settings once and are saved on your PC
(in `%APPDATA%\NonStopPopDJ`). Your six stingers are built in; drop extra mp3s into
the folder that **Open folder** shows.

It's the iPhone app laid out for a big screen: Home, Cara's page, your Library, Search, album /
artist / playlist pages, and a big player with synced lyrics, Up Next and About.

**Standalone:** music plays right in the app, no Spotify app needed. It uses Spotify's own Web
Playback SDK (Spotify Premium only) in a hidden Microsoft Edge window, because the app's window can't
play Spotify's protected audio (`pc_player.py`, `ui/player.html`). Settings > Where your music plays
switches back to the Spotify app.

**Visualizer:** press **V**. Ten trippy scenes of its own (tunnels, kaleidoscopes, fractals,
hyperspace) plus 481 MilkDrop presets through Butterchurn, all moving to whatever your speakers play.
Drop your own `.milk` presets (Cream of the Crop, projectM packs...) into its presets folder and they
join the rotation. `ui/vendor` holds Butterchurn, the preset tools and projectM's HLSL converter
(built by `.github/workflows/vendor.yml`, with a fix); `ui/presets` holds the bundled pack.

The window is drawn by Microsoft Edge WebView2 (`ui/` holds the page, its styles and scripts; `vis.js`
is the visualizer). Python (`dj_app.py`) runs Cara, talks to Spotify (`pc_spotify.py`) and listens to
the speakers for the visualizer (`pc_audio.py`).

Cara's brain (`brain.py`, with her lists in `brain_data.json`) is the same as the iPhone app's:
43 segments, her memory of what she's said (kept on your PC so she never repeats herself), the
station named after whatever you're playing, and breaks with her co-host MC Scratch.

Every push to this `desktop` branch rebuilds the .exe with GitHub Actions
(`.github/workflows/windows.yml`). The website lives on `main`.

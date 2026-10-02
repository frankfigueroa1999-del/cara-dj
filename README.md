# Non Stop Pop DJ for Windows

The desktop version of Cara DJ, packaged as one `NonStopPopDJ.exe`.

**Download:** the latest build is on the [Releases page](../../releases/tag/desktop-latest).
Double-click it. Windows may warn that it protected your PC because the app isn't signed:
click **More info**, then **Run anyway**.

Your keys (Spotify, ElevenLabs, Gemini) go in Settings once and are saved on your PC
(in `%APPDATA%\NonStopPopDJ`). Your six stingers are built in; drop extra mp3s into
the folder that **Open folder** shows.

Cara's brain (`brain.py`, with her lists in `brain_data.json`) is the same as the iPhone app's:
43 segments, her memory of what she's said (kept on your PC so she never repeats herself), the
station named after whatever you're playing, and breaks with her co-host Alex.

Every push to this `desktop` branch rebuilds the .exe with GitHub Actions
(`.github/workflows/windows.yml`). The website lives on `main`.

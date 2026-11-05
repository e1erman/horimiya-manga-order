> [!WARNING]
> **Every file in this repository – code, data, README, everything – was written by Claude (Anthropic's AI).
> No line was written by a human. Use at your own risk.**

---

# Horimiya – Manga Order

Cuts **Horimiya** (2021) and **Horimiya: The Missing Pieces** (2023) into the order of the manga,
**one video file per manga volume** (16 files).

The repository contains **no video, audio or subtitles** – only timestamps. You bring your own
episode files; the script cuts them on your computer.

The watch order is by *arashi* (u/iiStormBringer) from the fan document *"Everything Horimiya"*.

## Download

On the [Releases page](https://github.com/e1erman/horimiya-manga-order/releases):

- **`HorimiyaMangaOrder.exe`** – Windows, no Python needed. Just double-click it.
  Windows may warn that the app is unknown (it isn't signed): click *More info → Run anyway*.
- **`horimiya-manga-order-vX.Y.Z.zip`** – the Python scripts, for Windows, Linux and macOS.

## What you need

- [ffmpeg](https://ffmpeg.org/download.html), built **with chromaprint**. Either on your PATH
  (Windows: `winget install Gyan.FFmpeg`; Linux/macOS: most distro / Homebrew builds) or simply put
  `ffmpeg.exe` and `ffprobe.exe` in the same folder as the exe / scripts.
- [Python 3.10+](https://www.python.org/downloads/) – only for the zip version
- all 26 episodes: Horimiya 1–13 and The Missing Pieces 1–13

## Quick start

1. Download the exe (or the zip and unpack it).
2. Put all episodes in one folder. Each file name must contain `SxxEyy`:
   - Horimiya → `S01E01` … `S01E13`
   - The Missing Pieces → `S02E01` … `S02E13`

   e.g. `Horimiya - S01E01.mkv`, `Horimiya - S02E07 [1080p].mkv`. Anything else in the name is ignored.
3. Start the window: double-click **`HorimiyaMangaOrder.exe`** or **`horimiya_gui.pyw`**, or run

   ```bash
   python horimiya_order.py
   ```

4. Choose your episode folder, then:
   1. **Check episodes** – lists every episode, its detected opening and anything unusual.
   2. **Show cut list** – shows which parts go into which file, without cutting.
   3. **Cut** – makes the files. Tick only one volume first to test.

   Your settings are remembered for next time.

   The files land in `D:\Anime\Horimiya\manga-order\`, e.g.
   `Horimiya manga order - Volume 09 (Ch. 57-63.5).mkv`. Every part inside a file is a
   chapter marker ("Step 18: S1E10 - Ch. 58-60"), so you can see where each scene comes from.

### Command line

Everything in the window also works without it:

```bash
python horimiya_order.py check "D:\Anime\Horimiya"
python horimiya_order.py cut "D:\Anime\Horimiya" --only 1 --height 720
```

## Different releases (Crunchyroll, Blu-ray, …)

The guide's timestamps fit the **Crunchyroll** release (no logo in front). Other releases can be
shifted – e.g. **Wakanim** puts a 7–10 s "Watch it first on WAKANIM" logo before every episode.
The script handles this automatically:

- **Reference data.** `order.json` stores where the opening starts in the guide's release. Your
  episode's opening is found by audio, and the difference shifts all timestamps. "From the
  beginning" then starts after a logo/intro, so it is cut away.
- **Scene snapping.** The guide only has whole seconds. Each cut moves to the nearest scene
  change within ±1.5 s, so no cut lands in the middle of a shot (`--no-snap` turns this off).

If something is still off:

- **Opening detection.** Many cuts are "before/after the opening". The script finds the opening
  in *your* files by comparing the audio of the episodes with each other (chromaprint), so those
  cuts are always right.
- **`check`** lists for every episode its length, the detected opening, and which parts the order
  leaves out. Left-out parts should be openings, endings, previews or scenes skipped on purpose.
- **`--offset`** shifts the numeric timestamps if your release is off:

  ```bash
  python horimiya_order.py cut "D:\Anime\Horimiya" --offset S1=+5         # whole season 1
  python horimiya_order.py cut "D:\Anime\Horimiya" --offset S2E04=-1.5    # one episode
  ```

  If an opening is not found, set it by hand: `--op S1E10=2:30-4:00`.

Once someone has confirmed the guide's exact release, its opening times can be put into
`order.json` → `reference`; the script then corrects other releases automatically.

## Options

| Option | Meaning |
|---|---|
| `--split steps` | one file per guide step (56) instead of per volume |
| `--only 1-3,9` | only these volumes (or steps) |
| `--dry-run` | print the cut list, write nothing |
| `--audio-lang jpn` | which audio track to use (default: the file's default) |
| `--sub-lang eng` | which subtitle track to keep (signs/songs-only tracks are avoided) |
| `--no-subs` | no subtitles |
| `--burn-subs` | burn subtitles into the picture instead (needed for image subs like PGS) |
| `--height 720` | output resolution (default: largest source) |
| `--preset veryfast` | faster encoding, bigger files |
| `--vcodec h264_nvenc` | GPU encoding (NVIDIA) |

**Subtitles** stay a normal subtitle track you can switch on and off. The script cuts the chosen
track of every episode, joins the pieces into one track per file, keeps each episode's styles
(renamed per episode, e.g. `S1E10_Default`) and attaches all fonts. If two releases use different
subtitle resolutions, positions and sizes are scaled to match. Image subtitles (PGS/VobSub, some
Blu-ray rips) can't be joined that way – use `--burn-subs` or pick a text track with `--sub-lang`.

**Why re-encode?** A volume mixes scenes from different episodes and both seasons. Their files
often differ in resolution, frame rate and tracks, so they can't be joined losslessly. Re-encoding
makes every part identical. Expect the full run to take a while (CPU-bound).

## How the volumes are assigned

Volumes follow the chapter list on Wikipedia. Each step goes to the volume most of its chapters
belong to. Some steps contain chapters from two volumes; details are in `order.json` → `notes`.
You can change any assignment there (`"volume"` per step or per segment).

## Known quirks of the guide

- Where one part ends at X and the next starts at X+1 s, both are set to X+0.5 s.
- Two gaps exist in the guide itself and are kept: S2E04 20:18–20:28 and S2E12 16:45–17:57.

## Building the exe

```bash
pip install pyinstaller
python tools/build_exe.py
```

`dist/` then contains the exe and the release zip.

## Testing

`tools/make_test_episodes.py` (needs numpy) builds 26 fake episodes with a synthetic opening and
time-stamped subtitles, to test detection and cuts without any real episodes.

## Legal

This project ships timestamps only. Use it with episodes you own or have legally obtained.
Horimiya © HERO, Daisuke Hagiwara / SQUARE ENIX, Horimiya Project.

#!/usr/bin/env python3
"""Cut Horimiya (S1) and Horimiya: The Missing Pieces (S2) into manga order.

You bring your own episode files; this script only stores timestamps.
Needs Python 3.10+ and ffmpeg/ffprobe (with chromaprint) on PATH.

    python horimiya_order.py                  (opens a window)
    python horimiya_order.py check <folder>
    python horimiya_order.py cut   <folder> [-o <output folder>]
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from array import array
from dataclasses import dataclass, field
from pathlib import Path
from statistics import median

__version__ = "0.1.0"

# As .exe (PyInstaller) the bundled files live in a temp folder; the exe's own
# folder is where users can drop ffmpeg.exe or their own order.json.
FROZEN = getattr(sys, "frozen", False)
HERE = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))
APP_DIR = Path(sys.executable).resolve().parent if FROZEN else HERE
DEFAULT_ORDER = APP_DIR / "order.json" if (APP_DIR / "order.json").exists() else HERE / "order.json"
VIDEO_EXTS = {".mkv", ".mp4", ".m4v", ".avi", ".webm", ".ts", ".m2ts", ".mov"}
EPISODE_RE = re.compile(r"S0?([12])\s*E(\d{1,2})(?!\d)", re.IGNORECASE)
TEXT_SUBS = {"ass", "ssa", "subrip", "srt", "mov_text", "webvtt", "text"}
BITMAP_SUBS = {"hdmv_pgs_subtitle", "dvd_subtitle", "dvb_subtitle"}

# Chromaprint (ffmpeg muxer, algorithm 1): 11025 Hz, hop 1365 samples per item.
FP_STEP = 1365 / 11025
FP_SCAN_SECONDS = 720       # openings are searched in the first 12 minutes
FP_MAX_BITS = 6             # max differing bits for two items to count as equal
FP_MAX_GAP = round(3.5 / FP_STEP)
MIN_OP_SECONDS = 40
# Offsets between a fingerprint item and real time, measured on test material.
OP_START_CORRECTION = 0.2
OP_END_CORRECTION = 0.0


class UserError(Exception):
    pass


# ---------------------------------------------------------------- helpers

def tool(name: str) -> str:
    for candidate in (APP_DIR / f"{name}.exe", APP_DIR / name):   # next to the script/exe
        if candidate.is_file():
            return str(candidate)
    path = shutil.which(name)
    if not path:
        raise UserError(f"'{name}' was not found. Install ffmpeg and make sure it is on PATH.")
    return path


def parse_time(text: str) -> float:
    parts = text.strip().split(":")
    if not 1 <= len(parts) <= 3:
        raise ValueError(text)
    seconds = 0.0
    for part in parts:
        seconds = seconds * 60 + float(part)
    return seconds


def fmt_time(seconds: float | None) -> str:
    if seconds is None:
        return "-"
    sign = "-" if seconds < 0 else ""
    m, s = divmod(abs(seconds), 60)
    return f"{sign}{int(m)}:{s:04.1f}"


def parse_ranges(text: str) -> set[int]:
    result: set[int] = set()
    for part in text.split(","):
        part = part.strip()
        if "-" in part:
            a, b = part.split("-", 1)
            result.update(range(int(a), int(b) + 1))
        elif part:
            result.add(int(part))
    return result


def safe_name(text: str) -> str:
    return re.sub(r'[<>:"/\\|?*]', "_", text).strip()


def run_ffmpeg(args: list[str], cwd: Path | None = None) -> None:
    cmd = [tool("ffmpeg"), "-hide_banner", "-loglevel", "error", "-nostdin", "-y", *args]
    res = subprocess.run(cmd, capture_output=True, text=True, check=False, cwd=cwd)
    if res.returncode != 0:
        raise UserError("ffmpeg failed:\n" + " ".join(cmd) + "\n" + res.stderr.strip())


def probe_duration(path: Path) -> float:
    out = subprocess.run([tool("ffprobe"), "-v", "error", "-show_entries", "format=duration",
                          "-of", "csv=p=0", str(path)], capture_output=True, text=True, check=False)
    return float(out.stdout.strip())


# ---------------------------------------------------------------- episodes

@dataclass
class Episode:
    key: str                     # "S1E02"
    path: Path
    duration: float
    height: int
    audio: list[dict]
    subtitles: list[dict]
    attachments: list[dict]
    audio_pick: int = 0          # index among audio streams
    sub_pick: int | None = None  # index among subtitle streams
    op_start: float | None = None
    op_end: float | None = None
    op_source: str = ""
    offset: float = 0.0
    offset_source: str = ""
    prepared: dict = field(default_factory=dict)


def find_episodes(folder: Path) -> dict[str, Path]:
    folder = folder.resolve()
    if not folder.is_dir():
        raise UserError(f"Folder not found: {folder}")
    found: dict[str, list[Path]] = {}
    for path in sorted(folder.rglob("*")):
        if path.suffix.lower() not in VIDEO_EXTS or not path.is_file():
            continue
        if "manga-order" in path.parts:
            continue
        m = EPISODE_RE.search(path.stem)
        if m:
            found.setdefault(f"S{int(m.group(1))}E{int(m.group(2)):02d}", []).append(path)
    dupes = {k: v for k, v in found.items() if len(v) > 1}
    if dupes:
        lines = [f"  {k}: " + ", ".join(p.name for p in v) for k, v in dupes.items()]
        raise UserError("More than one file per episode:\n" + "\n".join(lines))
    return {k: v[0] for k, v in found.items()}


def probe(key: str, path: Path) -> Episode:
    out = subprocess.run(
        [tool("ffprobe"), "-v", "error", "-show_entries",
         "format=duration:stream=index,codec_type,codec_name,channels,height"
         ":stream_tags=language,title,filename,mimetype:stream_disposition=default,attached_pic",
         "-of", "json", str(path)],
        capture_output=True, text=True, check=False)
    if out.returncode != 0:
        raise UserError(f"ffprobe could not read {path.name}: {out.stderr.strip()}")
    data = json.loads(out.stdout)
    streams = data.get("streams", [])
    video = [s for s in streams if s.get("codec_type") == "video"
             and not s.get("disposition", {}).get("attached_pic")]
    audio = [s for s in streams if s.get("codec_type") == "audio"]
    subs = [s for s in streams if s.get("codec_type") == "subtitle"]
    attachments = [s for s in streams if s.get("codec_type") == "attachment"]
    if not video or not audio:
        raise UserError(f"{path.name} needs a video and an audio track.")
    return Episode(key, path, float(data["format"]["duration"]), int(video[0].get("height") or 1080),
                   audio, subs, attachments)


def pick_stream(streams: list[dict], lang: str | None, avoid_signs: bool) -> int | None:
    if not streams:
        return None
    idx = list(range(len(streams)))
    if lang:
        match = [i for i in idx if (streams[i].get("tags", {}).get("language") or "").lower() == lang.lower()]
        if match:
            idx = match
    if avoid_signs:
        full = [i for i in idx if not re.search(r"sign|song|forced",
                                                streams[i].get("tags", {}).get("title") or "", re.I)]
        idx = full or idx
    default = [i for i in idx if streams[i].get("disposition", {}).get("default")]
    return (default or idx)[0]


def describe(stream: dict | None) -> str:
    if stream is None:
        return "none"
    tags = stream.get("tags", {})
    return " ".join(x for x in (tags.get("language"), tags.get("title")) if x) or stream.get("codec_name", "?")


# ---------------------------------------------------------------- opening detection

def fingerprint(ep: Episode, seconds: float = FP_SCAN_SECONDS) -> array:
    out = subprocess.run(
        [tool("ffmpeg"), "-hide_banner", "-loglevel", "error", "-i", str(ep.path),
         "-t", str(seconds), "-map", f"0:a:{ep.audio_pick}", "-ac", "1",
         "-f", "chromaprint", "-fp_format", "raw", "-"],
        capture_output=True, check=False)
    if out.returncode != 0:
        msg = out.stderr.decode(errors="replace").strip()
        if "chromaprint" in msg.lower():
            msg += "\nYour ffmpeg build has no chromaprint support. Use a 'full' build."
        raise UserError(f"Could not fingerprint {ep.path.name}: {msg}")
    fp = array("I")
    fp.frombytes(out.stdout[: len(out.stdout) // 4 * 4])
    if sys.byteorder == "big":
        fp.byteswap()
    return fp


def _candidate_shifts(a: array, b: array, top: int = 8) -> list[int]:
    """Shifts s where a[i] == b[i - s] often (exact-value voting)."""
    index: dict[int, list[int]] = {}
    for j, v in enumerate(b):
        index.setdefault(v, []).append(j)
    votes: dict[int, int] = {}
    for i, v in enumerate(a):
        js = index.get(v)
        if js and len(js) <= 16:          # very common values are silence
            for j in js:
                votes[i - j] = votes.get(i - j, 0) + 1
    ranked = sorted(votes, key=votes.__getitem__, reverse=True)
    return [s for s in ranked[:top] if votes[s] >= 5]


def _longest_run(a: array, b: array, shift: int) -> tuple[int, int] | None:
    """Longest stretch (in a-indices, inclusive) where a[i] ~ b[i - shift]."""
    lo, hi = max(0, shift), min(len(a), len(b) + shift)
    best: tuple[int, int] | None = None
    start = last = None
    for i in range(lo, hi):
        if (a[i] ^ b[i - shift]).bit_count() <= FP_MAX_BITS:
            if start is None or i - last > FP_MAX_GAP:
                if start is not None and (best is None or last - start > best[1] - best[0]):
                    best = (start, last)
                start = i
            last = i
    if start is not None and (best is None or last - start > best[1] - best[0]):
        best = (start, last)
    return best


def common_segment(a: array, b: array) -> tuple[int, int, int] | None:
    """Longest shared audio: (a_start, a_end, shift); b index = a index - shift."""
    best = None
    for shift in _candidate_shifts(a, b):
        run = _longest_run(a, b, shift)
        if run and (best is None or run[1] - run[0] > best[1] - best[0]):
            best = (run[0], run[1], shift)
    return best


def detect_openings(episodes: dict[str, Episode], log) -> None:
    """Find each season's opening by comparing its episodes with each other."""
    for season in ("S1", "S2"):
        keys = sorted(k for k in episodes if k.startswith(season))
        if len(keys) < 2:
            continue
        fps = {}
        for k in keys:
            log(f"  fingerprinting {k} ...")
            fps[k] = fingerprint(episodes[k])

        # 1) A template from pairs of episodes (the opening is what they share).
        pairs = list(zip(keys, keys[1:])) + list(zip(keys, keys[2:]))
        runs = []
        for k1, k2 in pairs:
            seg = common_segment(fps[k1], fps[k2])
            if seg and (seg[1] - seg[0]) * FP_STEP >= MIN_OP_SECONDS:
                runs.append((k1, seg))
        if not runs:
            log(f"  {season}: no shared opening found between episodes.")
            continue
        med = median((s[1] - s[0]) for _, s in runs)
        k_ref, seg = min(runs, key=lambda r: abs((r[1][1] - r[1][0]) - med))
        template = fps[k_ref][seg[0]: seg[1] + 1]
        log(f"  {season}: opening is {len(template) * FP_STEP:.0f} s long (taken from {k_ref})")

        # 2) Find the template in every episode.
        for k in keys:
            ep = episodes[k]
            if ep.op_source == "manual":
                continue
            found = common_segment(template, fps[k])
            if not found or (found[1] - found[0]) < 0.5 * len(template):
                ep.op_source = "not found"
                continue
            t_start, t_end, shift = found
            ep.op_start = max(0.0, (t_start - shift) * FP_STEP + OP_START_CORRECTION)
            ep.op_end = (t_end - shift + 1) * FP_STEP + OP_END_CORRECTION
            ep.op_source = "detected"


# ---------------------------------------------------------------- plan

@dataclass
class Cut:
    step: int
    src: str
    start: float
    end: float | None      # None = until the end of the file
    label: str


@dataclass
class Job:
    name: str
    title: str
    cuts: list[Cut]


def resolve(token: str, ep: Episode, step_n: int) -> float | None:
    """Seconds in the file; None means 'until the end'."""
    if token == "start":  # skip a logo or intro the guide's release doesn't have
        return max(0.0, ep.offset)
    if token == "end":
        return None
    if token in ("op_start", "op_end"):
        value = ep.op_start if token == "op_start" else ep.op_end
        if value is None:
            raise UserError(
                f"Step {step_n} needs the opening of {ep.key}, but it was {ep.op_source or 'not detected'}.\n"
                f"Set it by hand, e.g. --op {ep.key}=1:30-3:00")
        return value
    return max(0.0, parse_time(token) + ep.offset)


SNAP_WINDOW = 1.5
_snap_cache: dict[tuple[str, float], float] = {}


def snap_to_scene(ep: Episode, t: float) -> float:
    """Move a cut to the nearest scene change within SNAP_WINDOW seconds.

    The guide only has whole seconds; real cuts sit up to a second later.
    """
    key = (ep.key, round(t, 3))
    if key not in _snap_cache:
        start = max(0.0, t - SNAP_WINDOW)
        out = subprocess.run(
            [tool("ffmpeg"), "-hide_banner", "-nostats", "-ss", f"{start:.3f}",
             "-t", f"{2 * SNAP_WINDOW:.3f}", "-i", str(ep.path), "-an",
             "-vf", "scale=320:-2,select='gt(scene,0.25)',showinfo", "-f", "null", "-"],
            capture_output=True, text=True, check=False)
        times = [start + float(x) for x in re.findall(r"pts_time:([\d.]+)", out.stderr)]
        near = min(times, key=lambda x: abs(x - t), default=None)
        # 10 ms before the new shot's first frame: it starts the next part, not this one.
        _snap_cache[key] = t if near is None or abs(near - t) > SNAP_WINDOW else max(0.0, near - 0.01)
    return _snap_cache[key]


def resolve_cut(token: str, ep: Episode, step_n: int, snap: bool) -> float | None:
    value = resolve(token, ep, step_n)
    if snap and value is not None and (token[:1].isdigit() or (token == "start" and value > 0.5)):
        value = snap_to_scene(ep, value)
    return value


def build_jobs(order: dict, episodes: dict[str, Episode], mode: str,
               only: set[int] | None, snap: bool = False) -> list[Job]:
    by_key: dict[str, Job] = {}
    for step in order["steps"]:
        n = step["n"]
        for seg in step["segments"]:
            vol = int(seg.get("volume", step.get("volume")))
            if only and (vol if mode == "volumes" else n) not in only:
                continue
            ep = episodes[seg["src"]]
            start = resolve_cut(seg["from"], ep, n, snap)
            end = resolve_cut(seg["to"], ep, n, snap)
            if end is not None and end >= ep.duration - 0.05:
                end = None
            if end is not None and end - start < 0.5:
                raise UserError(
                    f"Step {n}: {seg['src']} {seg['from']}->{seg['to']} resolves to "
                    f"{fmt_time(start)}-{fmt_time(end)}, which is empty. Check opening/offset.")
            label = f"Step {n}: {seg['src']} - Ch. {step['chapters']}"
            if mode == "volumes":
                key = f"{vol:02d}"
                title = f"Horimiya manga order - Volume {vol:02d} (Ch. {order['volumes'][str(vol)]})"
            else:
                key = f"{n:02d}"
                title = f"Horimiya manga order - Step {n:02d} (Ch. {step['chapters']})"
            job = by_key.setdefault(key, Job(safe_name(title) + ".mkv", title, []))
            job.cuts.append(Cut(n, seg["src"], start, end, label))
    return list(by_key.values())


# ---------------------------------------------------------------- subtitles

FONT_MIME = {".ttf": "application/x-truetype-font", ".ttc": "application/x-truetype-font",
             ".otf": "application/vnd.ms-opentype"}


@dataclass
class Ass:
    info: list[str]
    play_res: tuple[float, float]
    style_format: list[str]
    styles: list[list[str]]
    event_format: list[str]
    events: list[list[str]]       # Dialogue lines only, split by event_format


def ass_time(text: str) -> float:
    h, m, s = text.strip().split(":")
    return int(h) * 3600 + int(m) * 60 + float(s)


def fmt_ass_time(seconds: float) -> str:
    cs = max(0, round(seconds * 100))
    return f"{cs // 360000}:{cs // 6000 % 60:02d}:{cs // 100 % 60:02d}.{cs % 100:02d}"


def read_ass(path: Path) -> Ass:
    info, styles, events = [], [], []
    style_format: list[str] = []
    event_format: list[str] = []
    section = ""
    for line in path.read_text(encoding="utf-8-sig", errors="replace").splitlines():
        s = line.strip()
        if s.startswith("[") and s.endswith("]"):
            section = s.lower()
            continue
        if not s or s.startswith(";"):
            continue
        key, _, value = s.partition(":")
        if section == "[script info]":
            info.append(s)
        elif section in ("[v4+ styles]", "[v4 styles]"):
            if key == "Format":
                style_format = [f.strip() for f in value.split(",")]
            elif key == "Style":
                styles.append([f.strip() for f in value.split(",", len(style_format) - 1)])
        elif section == "[events]":
            if key == "Format":
                event_format = [f.strip() for f in value.split(",")]
            elif key == "Dialogue":
                events.append(value.lstrip().split(",", len(event_format) - 1))
    res = {k.strip().lower(): v.strip() for k, _, v in (i.partition(":") for i in info)}
    play_res = (float(res.get("playresx") or 384), float(res.get("playresy") or 288))
    return Ass(info, play_res, style_format, styles, event_format, events)


def _scale_numbers(args: str, factors: list[float]) -> str:
    parts = args.split(",")
    for i, f in enumerate(factors[: len(parts)]):
        try:
            parts[i] = f"{float(parts[i]) * f:g}"
        except ValueError:
            return args
    return ",".join(parts)


def rescale_text(text: str, sx: float, sy: float) -> str:
    """Rescale position/size override tags when two releases use different PlayRes."""
    text = re.sub(r"\\(pos|org)\(([^)]*)\)",
                  lambda m: f"\\{m[1]}({_scale_numbers(m[2], [sx, sy])})", text)
    text = re.sub(r"\\move\(([^)]*)\)",
                  lambda m: f"\\move({_scale_numbers(m[1], [sx, sy, sx, sy])})", text)
    text = re.sub(r"\\(i?clip)\(([-\d.\s]+,[-\d.\s]+,[-\d.\s]+,[-\d.\s]+)\)",
                  lambda m: f"\\{m[1]}({_scale_numbers(m[2], [sx, sy, sx, sy])})", text)
    text = re.sub(r"\\(fs|bord|shad|blur|be)([\d.]+)",
                  lambda m: f"\\{m[1]}{float(m[2]) * sy:g}", text)
    return re.sub(r"\\fsp([-\d.]+)", lambda m: f"\\fsp{float(m[1]) * sx:g}", text)


def prepare_subs(ep: Episode, tmp: Path) -> None:
    """Extract the chosen subtitle track and its fonts once per episode."""
    if ep.prepared or ep.sub_pick is None:
        return
    codec = ep.subtitles[ep.sub_pick].get("codec_name")
    if codec in TEXT_SUBS:
        sub = f"sub_{ep.key}.ass"
        fonts = tmp / f"fonts_{ep.key}"
        fonts.mkdir(exist_ok=True)
        run_ffmpeg(["-i", str(ep.path), "-map", f"0:s:{ep.sub_pick}", "-c:s", "ass", sub], cwd=tmp)
        # Fonts: dump each attachment under a safe name. ffmpeg complains about the
        # missing output file afterwards, but the attachments are written.
        dump = []
        for i, att in enumerate(ep.attachments):
            name = Path((att.get("tags", {}).get("filename") or f"font{i}").replace("\\", "/")).name
            if Path(name).suffix.lower() in FONT_MIME or "font" in (att.get("tags", {}).get("mimetype") or ""):
                dump += [f"-dump_attachment:t:{i}", safe_name(name) or f"font{i}.ttf"]
        if dump:
            subprocess.run([tool("ffmpeg"), "-hide_banner", "-loglevel", "quiet", "-nostdin", "-y",
                            *dump, "-i", str(ep.path)], cwd=fonts, capture_output=True, check=False)
        ep.prepared = {"kind": "text", "file": sub, "fonts": fonts.name, "ass": read_ass(tmp / sub)}
    elif codec in BITMAP_SUBS:
        ep.prepared = {"kind": "bitmap"}
    else:
        ep.prepared = {"kind": "unsupported", "codec": codec}


def merge_subs(job: "Job", lengths: list[float], episodes: dict[str, Episode], out: Path) -> bool:
    """One ASS track for the whole output file; styles are renamed per episode."""
    sources = [episodes[c.src].prepared.get("ass") for c in job.cuts]
    base = next((a for a in sources if a), None)
    if base is None:
        return False
    bx, by = base.play_res
    style_format = base.style_format
    i_name = style_format.index("Name")
    styles: list[list[str]] = []
    events: list[dict] = []
    added: set[str] = set()
    pos = 0.0
    for cut, ass, length in zip(job.cuts, sources, lengths):
        if ass is None:
            pos += length
            continue
        sx, sy = bx / ass.play_res[0], by / ass.play_res[1]
        rescale = abs(sx - 1) > 1e-3 or abs(sy - 1) > 1e-3
        prefix = cut.src + "_"
        names = {st[ass.style_format.index("Name")] for st in ass.styles}
        if cut.src not in added:
            added.add(cut.src)
            for st in ass.styles:
                row = dict(zip(ass.style_format, st))
                row["Name"] = prefix + row["Name"]
                if rescale:
                    for f, k in (("Fontsize", sy), ("Outline", sy), ("Shadow", sy), ("MarginV", sy),
                                 ("MarginL", sx), ("MarginR", sx), ("Spacing", sx)):
                        if f in row:
                            row[f] = f"{float(row[f]) * k:g}"
                styles.append([row.get(f, "0") for f in style_format])
            if "Default" not in names and ass.styles:   # events may fall back to Default
                fallback = list(styles[-1])
                fallback[i_name] = prefix + "Default"
                styles.append(fallback)
        fmt = ass.event_format
        i_start, i_end, i_style, i_text = (fmt.index(x) for x in ("Start", "End", "Style", "Text"))
        cut_end = cut.start + length
        for ev in ass.events:
            if len(ev) != len(fmt):
                continue
            a, b = ass_time(ev[i_start]), ass_time(ev[i_end])
            if b <= cut.start or a >= cut_end:
                continue
            ev = list(ev)
            ev[i_start] = fmt_ass_time(max(a, cut.start) - cut.start + pos)
            ev[i_end] = fmt_ass_time(min(b, cut_end) - cut.start + pos)
            style = ev[i_style].lstrip("*")
            ev[i_style] = prefix + (style if style in names else "Default")
            text = re.sub(r"\\r([^\\}]+)",
                          lambda m: f"\\r{prefix}{m[1]}" if m[1] in names else m[0], ev[i_text])
            ev[i_text] = rescale_text(text, sx, sy) if rescale else text
            events.append(dict(zip(fmt, ev)))
        pos += length

    event_format = ["Layer", "Start", "End", "Style", "Name", "MarginL", "MarginR", "MarginV", "Effect", "Text"]
    blank = {"Name": "", "Effect": "", "Text": ""}
    lines = ["[Script Info]", "; merged by horimiya_order.py"]
    lines += [i for i in base.info if not i.lower().startswith("title")]
    lines += ["Title: " + job.title, "", "[V4+ Styles]", "Format: " + ", ".join(style_format)]
    lines += ["Style: " + ",".join(s) for s in styles]
    lines += ["", "[Events]", "Format: " + ", ".join(event_format)]
    lines += ["Dialogue: " + ",".join(e.get(f, blank.get(f, "0")) for f in event_format) for e in events]
    out.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return True


def collect_fonts(job: "Job", episodes: dict[str, Episode], tmp: Path) -> list[Path]:
    fonts: dict[str, Path] = {}
    for src in dict.fromkeys(c.src for c in job.cuts):
        d = episodes[src].prepared.get("fonts")
        if d:
            for f in sorted((tmp / d).iterdir()):
                if f.is_file():
                    fonts.setdefault(f.name.lower(), f)
    return list(fonts.values())


# ---------------------------------------------------------------- encoding

def encode_cut(ep: Episode, cut: Cut, out: Path, tmp: Path, opts, height: int) -> None:
    width = round(height * 16 / 9 / 2) * 2
    scale = f"scale={width}:{height}:force_original_aspect_ratio=decrease"
    finish = f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2,setsar=1,fps=24000/1001,format={opts.pix_fmt}"
    # -copyts keeps the original timestamps so burned-in subtitles line up;
    # setpts then restarts the part at zero.
    args = ["-ss", f"{cut.start:.3f}"]
    if cut.end is not None:
        args += ["-t", f"{cut.end - cut.start:.3f}"]
    args += ["-copyts", "-i", str(ep.path)]

    subs = ep.prepared if opts.burn_subs else {}
    if subs.get("kind") == "text":   # render at output size for sharp text
        video = (f"[0:v:0]{scale},subtitles=filename={subs['file']}:fontsdir={subs['fonts']},"
                 f"setpts=PTS-STARTPTS,{finish}[v]")
    elif subs.get("kind") == "bitmap":
        video = f"[0:v:0][0:s:{ep.sub_pick}]overlay,{scale},setpts=PTS-STARTPTS,{finish}[v]"
    else:
        video = f"[0:v:0]{scale},setpts=PTS-STARTPTS,{finish}[v]"
    audio = f"[0:a:{ep.audio_pick}]asetpts=PTS-STARTPTS,aresample=48000,aformat=channel_layouts=stereo[a]"

    args += ["-filter_complex", f"{video};{audio}", "-map", "[v]", "-map", "[a]",
             "-c:v", opts.vcodec, "-c:a", "aac", "-b:a", "192k", "-ar", "48000"]
    if opts.vcodec == "libx264":
        args += ["-crf", str(opts.crf), "-preset", opts.preset]
    args += ["-avoid_negative_ts", "make_zero", "-max_muxing_queue_size", "4096", str(out)]
    run_ffmpeg(args, cwd=tmp)


def make_job(job: Job, episodes: dict[str, Episode], out_dir: Path, tmp: Path, opts,
             height: int, log) -> Path:
    parts = []
    for i, cut in enumerate(job.cuts):
        ep = episodes[cut.src]
        if not opts.no_subs:
            prepare_subs(ep, tmp)
            kind = ep.prepared.get("kind")
            if kind == "bitmap" and not opts.burn_subs and i == 0:
                log(f"    note: {ep.key} has image subtitles (PGS/VobSub); "
                    "they are only kept with --burn-subs.")
            elif kind == "unsupported" and i == 0:
                log(f"    note: {ep.key} subtitle format '{ep.prepared['codec']}' is not supported.")
        end = fmt_time(cut.end) if cut.end is not None else "end"
        log(f"    {cut.src} {fmt_time(cut.start)}-{end}")
        part = tmp / f"part_{i:03d}.mkv"
        encode_cut(ep, cut, part, tmp, opts, height)
        parts.append(part)

    lengths = [probe_duration(p) for p in parts]
    (tmp / "parts.txt").write_text("".join(f"file '{p.name}'\n" for p in parts), encoding="utf-8")
    meta = [";FFMETADATA1", f"title={job.title}"]
    pos = 0
    for length, cut in zip(lengths, job.cuts):
        ms = round(length * 1000)
        meta += ["[CHAPTER]", "TIMEBASE=1/1000", f"START={pos}", f"END={pos + ms}",
                 "title=" + re.sub(r"([=;#\\])", r"\\\1", cut.label)]
        pos += ms
    (tmp / "meta.txt").write_text("\n".join(meta) + "\n", encoding="utf-8")

    args = ["-f", "concat", "-safe", "0", "-i", "parts.txt", "-i", "meta.txt"]
    maps = ["-map", "0", "-map_metadata", "1", "-map_chapters", "1"]
    if not opts.no_subs and not opts.burn_subs and merge_subs(job, lengths, episodes, tmp / "subs.ass"):
        lang = next((episodes[c.src].subtitles[episodes[c.src].sub_pick].get("tags", {}).get("language")
                     for c in job.cuts if episodes[c.src].sub_pick is not None), None)
        args += ["-i", "subs.ass"]
        maps += ["-map", "2", "-disposition:s:0", "default"]
        if lang:
            maps += ["-metadata:s:s:0", f"language={lang}"]
        for i, font in enumerate(collect_fonts(job, episodes, tmp)):
            mime = FONT_MIME.get(font.suffix.lower(), "application/octet-stream")
            maps += ["-attach", str(font), f"-metadata:s:t:{i}", f"mimetype={mime}",
                     f"-metadata:s:t:{i}", f"filename={font.name}"]

    final = out_dir / job.name
    run_ffmpeg([*args, *maps, "-c", "copy", str(final)], cwd=tmp)
    for p in parts:
        p.unlink(missing_ok=True)
    return final


# ---------------------------------------------------------------- commands

def parse_overrides(values: list[str] | None, kind: str) -> dict[str, str]:
    result = {}
    for v in values or []:
        if "=" not in v:
            raise UserError(f"--{kind} expects KEY=VALUE, got '{v}'")
        k, val = v.split("=", 1)
        result[k.strip().upper()] = val.strip()
    return result


def prepare(opts, order: dict, log) -> dict[str, Episode]:
    paths = find_episodes(Path(opts.folder))
    needed = sorted({s["src"] for st in order["steps"] for s in st["segments"]})
    missing = [k for k in needed if k not in paths]
    if missing:
        raise UserError(
            "Missing episodes: " + ", ".join(missing) +
            "\nFile names must contain SxxEyy: S01 = Horimiya, S02 = The Missing Pieces.")

    episodes = {k: probe(k, paths[k]) for k in needed}
    for ep in episodes.values():
        ep.audio_pick = pick_stream(ep.audio, opts.audio_lang, False) or 0
        ep.sub_pick = None if opts.no_subs else pick_stream(ep.subtitles, opts.sub_lang, True)

    for k, val in parse_overrides(opts.op, "op").items():
        if k not in episodes or "-" not in val:
            raise UserError(f"--op {k}={val}: expected e.g. S1E10=1:30-3:00")
        a, b = val.split("-", 1)
        episodes[k].op_start, episodes[k].op_end = parse_time(a), parse_time(b)
        episodes[k].op_source = "manual"

    if not opts.no_detect:
        log("Detecting openings (reads the first minutes of every episode) ...")
        detect_openings(episodes, log)

    # Offsets: reference data first, command line wins.
    ref = order.get("reference", {}).get("episodes", {})
    for k, ep in episodes.items():
        r = ref.get(k) or {}
        if r.get("op_start") is not None and ep.op_start is not None and ep.op_source == "detected":
            ep.offset = ep.op_start - float(r["op_start"])
            ep.offset_source = "reference"
    for k, val in parse_overrides(opts.offset, "offset").items():
        targets = [e for e in episodes.values()
                   if k == "ALL" or e.key == k or e.key.startswith(k + "E")]
        if not targets:
            raise UserError(f"--offset {k}: no such episode or season")
        for ep in targets:
            ep.offset = float(val)
            ep.offset_source = "manual"
    return episodes


def coverage(order: dict, episodes: dict[str, Episode]) -> dict[str, list[str]]:
    """Parts of each episode the order does not use (or uses twice)."""
    used: dict[str, list[tuple[float, float]]] = {}
    for step in order["steps"]:
        for seg in step["segments"]:
            ep = episodes[seg["src"]]
            try:
                a = resolve(seg["from"], ep, step["n"])
                b = resolve(seg["to"], ep, step["n"])
            except UserError:
                continue
            used.setdefault(ep.key, []).append((a, ep.duration if b is None else b))
    report = {}
    for k, ranges in used.items():
        ranges.sort()
        notes, pos = [], 0.0
        for a, b in ranges:
            if a - pos > 1.5:
                notes.append(f"unused {fmt_time(pos)}-{fmt_time(a)}")
            elif pos - a > 1.5:
                notes.append(f"twice {fmt_time(a)}-{fmt_time(min(pos, b))}")
            pos = max(pos, b)
        if episodes[k].duration - pos > 1.5:
            notes.append(f"unused {fmt_time(pos)}-{fmt_time(episodes[k].duration)}")
        report[k] = notes
    return report


def cmd_check(opts, order: dict) -> int:
    episodes = prepare(opts, order, print)
    ref = order.get("reference", {}).get("episodes", {})
    cov = coverage(order, episodes)
    print()
    print(f"{'Ep':6} {'Length':>7} {'Ref':>7} {'Opening':>15} {'Offset':>6}  Audio / Subs  |  Not used")
    for k, ep in sorted(episodes.items()):
        r = ref.get(k) or {}
        op = (f"{fmt_time(ep.op_start)}-{fmt_time(ep.op_end)}" if ep.op_start is not None
              else ep.op_source or "-")
        off = f"{ep.offset:+.1f}" if ep.offset_source else "-"
        sub = ep.subtitles[ep.sub_pick] if ep.sub_pick is not None else None
        tracks = f"{describe(ep.audio[ep.audio_pick])} / {describe(sub)}"
        print(f"{k:6} {fmt_time(ep.duration):>7} {fmt_time(r.get('duration')):>7} {op:>15} {off:>6}  "
              f"{tracks}  |  " + "; ".join(cov.get(k, [])))
    print()
    print("The 'not used' parts should be openings, endings, previews or scenes the order")
    print("skips on purpose. If a cut lands mid-scene, your release is shifted against the")
    print("guide: fix it with --offset (see README).")
    return 0


def cmd_cut(opts, order: dict) -> int:
    only = parse_ranges(opts.only) if opts.only else None
    episodes = prepare(opts, order, print)
    if not opts.no_snap:
        print("Finding scene changes near the cut points ...")
    jobs = build_jobs(order, episodes, opts.split, only, snap=not opts.no_snap)
    if not jobs:
        raise UserError("Nothing to do (check --only).")
    out_dir = (Path(opts.output) if opts.output else Path(opts.folder) / "manga-order").resolve()
    height = opts.height or max(ep.height for ep in episodes.values())

    print()
    for job in jobs:
        total = sum((c.end if c.end is not None else episodes[c.src].duration) - c.start for c in job.cuts)
        print(f"{job.name}  [{fmt_time(total)}]")
        for c in job.cuts:
            end = fmt_time(c.end) if c.end is not None else "end"
            print(f"    step {c.step:2d}  {c.src} {fmt_time(c.start)}-{end}")
    if opts.dry_run:
        return 0

    out_dir.mkdir(parents=True, exist_ok=True)
    for old in out_dir.glob(".tmp-*"):   # left over from a stopped run
        shutil.rmtree(old, ignore_errors=True)
    with tempfile.TemporaryDirectory(dir=out_dir, prefix=".tmp-") as tmp:
        for i, job in enumerate(jobs, 1):
            print(f"\n[{i}/{len(jobs)}] {job.name}", flush=True)
            make_job(job, episodes, out_dir, Path(tmp), opts, height, print)
    print(f"\nDone: {out_dir}")
    return 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Cut Horimiya + Horimiya: The Missing Pieces into manga order.")
    p.add_argument("--version", action="version", version=__version__)
    sub = p.add_subparsers(dest="command", required=True)

    def common(sp):
        sp.add_argument("folder", help="folder with your episode files (names must contain S01E01 ... S02E13)")
        sp.add_argument("--order", default=str(DEFAULT_ORDER), help="order file (default: order.json)")
        sp.add_argument("--offset", action="append", metavar="EP=SEC",
                        help="shift timestamps, e.g. S1E02=+1.5, S2=-0.5 or all=+5 (repeatable)")
        sp.add_argument("--op", action="append", metavar="EP=FROM-TO",
                        help="set an opening by hand, e.g. S1E10=1:30-3:00 (repeatable)")
        sp.add_argument("--no-detect", action="store_true", help="skip automatic opening detection")
        sp.add_argument("--audio-lang", help="audio language to use, e.g. jpn or eng (default: file default)")
        sp.add_argument("--sub-lang", help="subtitle language to keep, e.g. eng or ger")
        sp.add_argument("--no-subs", action="store_true", help="no subtitles at all")

    c = sub.add_parser("check", help="show lengths, openings, chosen tracks and unused parts")
    common(c)
    c.set_defaults(func=cmd_check)

    k = sub.add_parser("cut", help="create the manga-order files")
    common(k)
    k.add_argument("-o", "--output", help="output folder (default: <folder>/manga-order)")
    k.add_argument("--split", choices=["volumes", "steps"], default="volumes",
                   help="one file per manga volume (default) or per guide step")
    k.add_argument("--only", help="only these volumes (or steps with --split steps), e.g. 1-3,9")
    k.add_argument("--no-snap", action="store_true",
                   help="cut exactly at the guide's times instead of the nearest scene change")
    k.add_argument("--dry-run", action="store_true", help="show the cut list, write nothing")
    k.add_argument("--height", type=int, help="output height, e.g. 720 (default: largest source)")
    k.add_argument("--vcodec", default="libx264", help="video encoder, e.g. h264_nvenc (default libx264)")
    k.add_argument("--crf", type=int, default=18, help="x264 quality, lower = better (default 18)")
    k.add_argument("--preset", default="medium", help="x264 preset, e.g. veryfast (default medium)")
    k.add_argument("--burn-subs", action="store_true",
                   help="burn subtitles into the picture instead of a subtitle track")
    k.add_argument("--pix-fmt", default="yuv420p", help="output pixel format (default yuv420p)")
    k.set_defaults(func=cmd_cut)

    if sys.stdout is None:   # windowed .exe started from a console
        sys.stdout = sys.stderr = open(os.devnull, "w")
    if argv is None and len(sys.argv) == 1:   # no arguments: open the window
        import runpy
        runpy.run_path(str(HERE / "horimiya_gui.pyw"), run_name="__main__")
        return 0
    opts = p.parse_args(argv)
    try:
        with open(opts.order, encoding="utf-8") as f:
            order = json.load(f)
        return opts.func(opts, order)
    except UserError as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    sys.exit(main())

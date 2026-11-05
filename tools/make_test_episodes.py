"""Create fake episodes for testing (needs numpy and ffmpeg).

Every season shares one synthetic "opening" and "ending" tune, placed at known
positions. Subtitles carry the source time ("S1E02 t=0123"), so cuts can be
checked exactly. No real episode content is involved.

    python tools/make_test_episodes.py <folder> [--shift S1=5]
"""
import argparse
import json
import subprocess
import sys
import wave
from pathlib import Path

import numpy as np

RATE = 11025
LENGTH = 1420.0
OP_LEN = {"S1": 89.0, "S2": 90.0}
OP_AT = {"S1": 60.0, "S2": 10.0}
OP_SPECIAL = {"S1E08": 30.0, "S1E10": 150.0, "S1E11": 120.0, "S1E12": 300.0, "S1E13": None,
              "S2E01": 20.0, "S2E08": 88.0, "S2E12": 368.0}
ED_AT = 1300.0


def tune(seed: int, seconds: float) -> np.ndarray:
    rng = np.random.default_rng(seed)
    out, total = [], 0
    n_total = int(seconds * RATE)
    while total < n_total:
        n = int(rng.uniform(0.2, 0.7) * RATE)
        t = np.arange(n) / RATE
        f1, f2 = rng.choice([110, 147, 165, 196, 220, 262, 294, 330, 392, 440, 523, 587, 659, 784], 2)
        env = np.minimum(1, np.minimum(t * 40, (n / RATE - t) * 40))
        out.append(env * (0.35 * np.sin(2 * np.pi * f1 * t) + 0.25 * np.sin(2 * np.pi * f2 * t)))
        total += n
    return np.concatenate(out)[:n_total]


def build(key: str, shift: float) -> tuple[np.ndarray, float | None]:
    season = key[:2]
    ep = int(key[3:])
    audio = tune(1000 * (season == "S2") + ep, LENGTH)
    audio += np.random.default_rng(ep + 77).normal(0, 0.02, audio.size)
    op_at = OP_SPECIAL.get(key, OP_AT[season])
    if op_at is not None:
        i = int(op_at * RATE)
        audio[i:i + int(OP_LEN[season] * RATE)] = tune(10 if season == "S1" else 20, OP_LEN[season])
    i = int(ED_AT * RATE)
    audio[i:i + int(90 * RATE)] = tune(11 if season == "S1" else 21, 90)
    if shift:  # simulate a release with an extra logo in front
        audio = np.concatenate([np.zeros(int(shift * RATE)), audio])
        op_at = None if op_at is None else op_at + shift
    return audio, op_at


def main():
    p = argparse.ArgumentParser()
    p.add_argument("folder")
    p.add_argument("--shift", action="append", default=[], help="e.g. S1=5 adds 5 s in front of S1")
    args = p.parse_args()
    shifts = {k: float(v) for k, v in (s.split("=") for s in args.shift)}
    out = Path(args.folder)
    out.mkdir(parents=True, exist_ok=True)
    truth = {}
    for season, count in (("S1", 13), ("S2", 13)):
        for ep in range(1, count + 1):
            key = f"{season}E{ep:02d}"
            shift = shifts.get(season, 0.0)
            audio, op_at = build(key, shift)
            truth[key] = {"op_start": op_at, "op_end": None if op_at is None else op_at + OP_LEN[season],
                          "shift": shift}
            wav = out / f"{key}.wav"
            with wave.open(str(wav), "wb") as w:
                w.setnchannels(1)
                w.setsampwidth(2)
                w.setframerate(RATE)
                w.writeframes((np.clip(audio, -1, 1) * 32000).astype("<i2").tobytes())
            dur = audio.size / RATE
            srt = out / f"{key}.srt"
            lines = []
            for n, t in enumerate(range(0, int(dur), 2), 1):
                src = t - shift
                a = f"{t // 3600:02d}:{t // 60 % 60:02d}:{t % 60:02d},000"
                b = f"{t // 3600:02d}:{t // 60 % 60:02d}:{t % 60 + 1:02d},500"
                lines.append(f"{n}\n{a} --> {b}\n{key} t={src:07.1f}\n")
            srt.write_text("\n".join(lines), encoding="utf-8")
            name = f"Horimiya - S0{season[1]}E{ep:02d} [test].mkv"
            cmd = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
                   "-f", "lavfi", "-i", f"testsrc=size=160x90:rate=10:duration={dur}",
                   "-i", str(wav), "-i", str(srt),
                   "-map", "0", "-map", "1", "-map", "2",
                   "-c:v", "libx264", "-preset", "ultrafast", "-g", "50",
                   "-c:a", "aac", "-b:a", "64k", "-c:s", "ass",
                   "-attach", r"C:\Windows\Fonts\arial.ttf",
                   "-metadata:s:t", "mimetype=application/x-truetype-font",
                   "-metadata:s:t", "filename=arial.ttf",
                   str(out / name)]
            subprocess.run(cmd, check=True)
            wav.unlink()
            srt.unlink()
            print(key, flush=True)
    (out / "truth.json").write_text(json.dumps(truth, indent=1))


if __name__ == "__main__":
    sys.exit(main())

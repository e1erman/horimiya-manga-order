"""Window for horimiya_order.py. Double-click this file (Windows) or run:

    python horimiya_gui.pyw

It only builds the command line and runs horimiya_order.py, showing its output.
"""
from __future__ import annotations

import json
import os
import queue
import re
import subprocess
import sys
import threading
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

HERE = Path(__file__).resolve().parent
SCRIPT = HERE / "horimiya_order.py"
SETTINGS = Path.home() / ".horimiya_manga_order.json"


def load_volumes() -> dict[str, str]:
    try:
        with open(HERE / "order.json", encoding="utf-8") as f:
            return json.load(f).get("volumes", {})
    except OSError:
        return {str(i): "" for i in range(1, 17)}


class App:
    def __init__(self, root: tk.Tk):
        self.root = root
        self.proc: subprocess.Popen | None = None
        self.lines: queue.Queue[str | None] = queue.Queue()
        self.out_dir: Path | None = None
        root.title("Horimiya – Manga Order")
        root.minsize(760, 640)

        s = self.load_settings()
        self.folder = tk.StringVar(value=s.get("folder", ""))
        self.output = tk.StringVar(value=s.get("output", ""))
        self.split = tk.StringVar(value=s.get("split", "volumes"))
        self.steps = tk.StringVar(value=s.get("steps", ""))
        self.height = tk.StringVar(value=s.get("height", "Largest source"))
        self.preset = tk.StringVar(value=s.get("preset", "medium"))
        self.subs = tk.StringVar(value=s.get("subs", "Subtitle track"))
        self.audio_lang = tk.StringVar(value=s.get("audio_lang", ""))
        self.sub_lang = tk.StringVar(value=s.get("sub_lang", ""))
        self.offset = tk.StringVar(value=s.get("offset", ""))
        self.snap = tk.BooleanVar(value=s.get("snap", True))
        self.vol_vars: dict[int, tk.BooleanVar] = {}

        pad = {"padx": 8, "pady": 4}
        main = ttk.Frame(root, padding=10)
        main.pack(fill="both", expand=True)
        main.columnconfigure(1, weight=1)

        # Folders
        ttk.Label(main, text="Episode folder").grid(row=0, column=0, sticky="w", **pad)
        ttk.Entry(main, textvariable=self.folder).grid(row=0, column=1, sticky="ew", **pad)
        ttk.Button(main, text="Browse…", command=self.pick_folder).grid(row=0, column=2, **pad)
        ttk.Label(main, text="Output folder").grid(row=1, column=0, sticky="w", **pad)
        ttk.Entry(main, textvariable=self.output).grid(row=1, column=1, sticky="ew", **pad)
        ttk.Button(main, text="Browse…", command=self.pick_output).grid(row=1, column=2, **pad)
        ttk.Label(main, text="Empty = <episode folder>\\manga-order. File names must contain "
                             "S01E01…S01E13 (Horimiya) and S02E01…S02E13 (Missing Pieces).",
                  foreground="gray").grid(row=2, column=0, columnspan=3, sticky="w", padx=8)

        # What to make
        what = ttk.LabelFrame(main, text="What to make", padding=8)
        what.grid(row=3, column=0, columnspan=3, sticky="ew", **pad)
        ttk.Radiobutton(what, text="One file per manga volume", variable=self.split,
                        value="volumes", command=self.update_mode).grid(row=0, column=0, sticky="w")
        ttk.Radiobutton(what, text="One file per guide step (56)", variable=self.split,
                        value="steps", command=self.update_mode).grid(row=0, column=1, sticky="w")
        grid = ttk.Frame(what)
        grid.grid(row=1, column=0, columnspan=4, sticky="w", pady=(6, 0))
        chosen = set(s.get("volumes", []))
        self.vol_checks = []
        for i, (vol, chapters) in enumerate(sorted(load_volumes().items(), key=lambda kv: int(kv[0]))):
            var = tk.BooleanVar(value=(not chosen) or int(vol) in chosen)
            self.vol_vars[int(vol)] = var
            cb = ttk.Checkbutton(grid, text=f"Vol. {int(vol):2d}  (Ch. {chapters})", variable=var)
            cb.grid(row=i % 4, column=i // 4, sticky="w", padx=(0, 16))
            self.vol_checks.append(cb)
        buttons = ttk.Frame(what)
        buttons.grid(row=2, column=0, columnspan=4, sticky="w", pady=(4, 0))
        self.all_btn = ttk.Button(buttons, text="All", command=lambda: self.set_all(True))
        self.all_btn.pack(side="left")
        self.none_btn = ttk.Button(buttons, text="None", command=lambda: self.set_all(False))
        self.none_btn.pack(side="left", padx=4)
        ttk.Label(buttons, text="   Steps:").pack(side="left")
        self.steps_entry = ttk.Entry(buttons, textvariable=self.steps, width=18)
        self.steps_entry.pack(side="left", padx=4)
        ttk.Label(buttons, text="e.g. 1-10,17 (empty = all)", foreground="gray").pack(side="left")

        # Options
        opt = ttk.LabelFrame(main, text="Options", padding=8)
        opt.grid(row=4, column=0, columnspan=3, sticky="ew", **pad)
        ttk.Label(opt, text="Resolution").grid(row=0, column=0, sticky="w")
        ttk.Combobox(opt, textvariable=self.height, width=14, state="readonly",
                     values=["Largest source", "1080", "720", "480"]).grid(row=0, column=1, sticky="w", padx=4)
        ttk.Label(opt, text="Speed").grid(row=0, column=2, sticky="w", padx=(16, 0))
        ttk.Combobox(opt, textvariable=self.preset, width=10, state="readonly",
                     values=["veryfast", "faster", "fast", "medium", "slow"]).grid(row=0, column=3, sticky="w", padx=4)
        ttk.Label(opt, text="(faster = bigger files)", foreground="gray").grid(row=0, column=4, sticky="w")
        ttk.Label(opt, text="Subtitles").grid(row=1, column=0, sticky="w", pady=(6, 0))
        ttk.Combobox(opt, textvariable=self.subs, width=14, state="readonly",
                     values=["Subtitle track", "Burn in", "None"]).grid(row=1, column=1, sticky="w", padx=4, pady=(6, 0))
        ttk.Label(opt, text="Audio language").grid(row=1, column=2, sticky="w", padx=(16, 0), pady=(6, 0))
        ttk.Entry(opt, textvariable=self.audio_lang, width=8).grid(row=1, column=3, sticky="w", padx=4, pady=(6, 0))
        ttk.Label(opt, text="Subtitle language").grid(row=1, column=4, sticky="w", pady=(6, 0))
        ttk.Entry(opt, textvariable=self.sub_lang, width=8).grid(row=1, column=5, sticky="w", padx=4, pady=(6, 0))
        ttk.Label(opt, text="Offset").grid(row=2, column=0, sticky="w", pady=(6, 0))
        ttk.Entry(opt, textvariable=self.offset, width=16).grid(row=2, column=1, sticky="w", padx=4, pady=(6, 0))
        ttk.Label(opt, text="only if cuts are off, e.g. S1=+5 or S2E04=-1.5",
                  foreground="gray").grid(row=2, column=2, columnspan=3, sticky="w", padx=(16, 0), pady=(6, 0))
        ttk.Checkbutton(opt, text="Snap cuts to the nearest scene change",
                        variable=self.snap).grid(row=3, column=0, columnspan=4, sticky="w", pady=(6, 0))

        # Actions
        act = ttk.Frame(main)
        act.grid(row=5, column=0, columnspan=3, sticky="ew", **pad)
        self.check_btn = ttk.Button(act, text="1. Check episodes", command=lambda: self.run("check"))
        self.check_btn.pack(side="left")
        self.dry_btn = ttk.Button(act, text="2. Show cut list", command=lambda: self.run("dry"))
        self.dry_btn.pack(side="left", padx=6)
        self.cut_btn = ttk.Button(act, text="3. Cut", command=lambda: self.run("cut"))
        self.cut_btn.pack(side="left")
        self.stop_btn = ttk.Button(act, text="Stop", command=self.stop, state="disabled")
        self.stop_btn.pack(side="left", padx=6)
        self.open_btn = ttk.Button(act, text="Open output folder", command=self.open_output, state="disabled")
        self.open_btn.pack(side="right")

        self.progress = ttk.Progressbar(main, mode="determinate")
        self.progress.grid(row=6, column=0, columnspan=3, sticky="ew", padx=8)
        self.status = ttk.Label(main, text="Ready.")
        self.status.grid(row=7, column=0, columnspan=3, sticky="w", padx=8, pady=(2, 4))

        log_frame = ttk.Frame(main)
        log_frame.grid(row=8, column=0, columnspan=3, sticky="nsew", padx=8, pady=(0, 4))
        main.rowconfigure(8, weight=1)
        self.log = tk.Text(log_frame, height=14, wrap="none", font=("Consolas", 9), state="disabled")
        scroll_y = ttk.Scrollbar(log_frame, orient="vertical", command=self.log.yview)
        scroll_x = ttk.Scrollbar(log_frame, orient="horizontal", command=self.log.xview)
        self.log.configure(yscrollcommand=scroll_y.set, xscrollcommand=scroll_x.set)
        self.log.grid(row=0, column=0, sticky="nsew")
        scroll_y.grid(row=0, column=1, sticky="ns")
        scroll_x.grid(row=1, column=0, sticky="ew")
        log_frame.columnconfigure(0, weight=1)
        log_frame.rowconfigure(0, weight=1)

        self.update_mode()
        root.protocol("WM_DELETE_WINDOW", self.close)
        root.after(100, self.poll)

    # ------------------------------------------------------------ settings

    def load_settings(self) -> dict:
        try:
            return json.loads(SETTINGS.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}

    def save_settings(self) -> None:
        data = {k: getattr(self, k).get() for k in
                ("folder", "output", "split", "steps", "height", "preset", "subs",
                 "audio_lang", "sub_lang", "offset", "snap")}
        data["volumes"] = [v for v, var in self.vol_vars.items() if var.get()]
        try:
            SETTINGS.write_text(json.dumps(data, indent=1), encoding="utf-8")
        except OSError:
            pass

    # ------------------------------------------------------------ widgets

    def pick_folder(self):
        d = filedialog.askdirectory(initialdir=self.folder.get() or None, title="Folder with your episodes")
        if d:
            self.folder.set(d)

    def pick_output(self):
        d = filedialog.askdirectory(initialdir=self.output.get() or self.folder.get() or None,
                                    title="Where the finished files go")
        if d:
            self.output.set(d)

    def set_all(self, value: bool):
        for var in self.vol_vars.values():
            var.set(value)

    def update_mode(self):
        volumes = self.split.get() == "volumes"
        for cb in self.vol_checks + [self.all_btn, self.none_btn]:
            cb.configure(state="normal" if volumes else "disabled")
        self.steps_entry.configure(state="disabled" if volumes else "normal")

    def write(self, text: str):
        self.log.configure(state="normal")
        self.log.insert("end", text)
        self.log.see("end")
        self.log.configure(state="disabled")

    # ------------------------------------------------------------ running

    def build_args(self, kind: str) -> list[str] | None:
        folder = self.folder.get().strip()
        if not folder or not Path(folder).is_dir():
            messagebox.showerror("Episode folder", "Please choose the folder with your episodes.")
            return None
        args = ["check" if kind == "check" else "cut", folder]
        if self.audio_lang.get().strip():
            args += ["--audio-lang", self.audio_lang.get().strip()]
        if self.sub_lang.get().strip():
            args += ["--sub-lang", self.sub_lang.get().strip()]
        if self.subs.get() == "None":
            args.append("--no-subs")
        for part in re.split(r"[\s,;]+", self.offset.get().strip()):
            if part:
                args += ["--offset", part]
        if kind == "check":
            return args

        if self.split.get() == "volumes":
            chosen = sorted(v for v, var in self.vol_vars.items() if var.get())
            if not chosen:
                messagebox.showerror("Volumes", "Please tick at least one volume.")
                return None
            if len(chosen) < len(self.vol_vars):
                args += ["--only", ",".join(map(str, chosen))]
        else:
            args += ["--split", "steps"]
            if self.steps.get().strip():
                args += ["--only", self.steps.get().strip().replace(" ", "")]
        if self.output.get().strip():
            args += ["-o", self.output.get().strip()]
        if self.height.get().isdigit():
            args += ["--height", self.height.get()]
        args += ["--preset", self.preset.get()]
        if self.subs.get() == "Burn in":
            args.append("--burn-subs")
        if not self.snap.get():
            args.append("--no-snap")
        if kind == "dry":
            args.append("--dry-run")
        return args

    def run(self, kind: str):
        if self.proc:
            return
        args = self.build_args(kind)
        if args is None:
            return
        self.save_settings()
        out = self.output.get().strip()
        self.out_dir = Path(out) if out else Path(self.folder.get().strip()) / "manga-order"
        self.log.configure(state="normal")
        self.log.delete("1.0", "end")
        self.log.configure(state="disabled")
        self.write("> horimiya_order.py " + " ".join(f'"{a}"' if " " in a else a for a in args) + "\n\n")
        self.progress.configure(value=0, maximum=1)
        self.status.configure(text="Working… (the first step reads every episode, this takes a moment)")
        for b in (self.check_btn, self.dry_btn, self.cut_btn):
            b.configure(state="disabled")
        self.stop_btn.configure(state="normal")

        python = sys.executable
        if python.lower().endswith("pythonw.exe"):   # child needs a normal interpreter for output
            candidate = python[:-len("pythonw.exe")] + "python.exe"
            python = candidate if os.path.exists(candidate) else python
        flags = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
        env = dict(os.environ, PYTHONUNBUFFERED="1", PYTHONIOENCODING="utf-8")
        try:
            self.proc = subprocess.Popen([python, str(SCRIPT), *args], stdout=subprocess.PIPE,
                                         stderr=subprocess.STDOUT, text=True, encoding="utf-8",
                                         errors="replace", cwd=HERE, env=env, creationflags=flags)
        except OSError as e:
            self.write(f"Could not start: {e}\n")
            self.finish(1)
            return
        threading.Thread(target=self.reader, args=(self.proc,), daemon=True).start()

    def reader(self, proc: subprocess.Popen):
        for line in proc.stdout:
            self.lines.put(line)
        proc.wait()
        self.lines.put(None)

    def poll(self):
        try:
            while True:
                line = self.lines.get_nowait()
                if line is None:
                    self.finish(self.proc.returncode if self.proc else 1)
                    continue
                self.write(line)
                m = re.match(r"\[(\d+)/(\d+)\] (.*)", line.strip())
                if m:
                    done, total = int(m[1]), int(m[2])
                    self.progress.configure(maximum=total, value=done - 1)
                    self.status.configure(text=f"Cutting {done} of {total}: {m[3]}")
                elif line.startswith("Done:"):
                    self.progress.configure(value=self.progress["maximum"])
        except queue.Empty:
            pass
        self.root.after(100, self.poll)

    def finish(self, code: int):
        self.proc = None
        for b in (self.check_btn, self.dry_btn, self.cut_btn):
            b.configure(state="normal")
        self.stop_btn.configure(state="disabled")
        if code == 0:
            self.status.configure(text="Finished.")
            if self.out_dir and self.out_dir.is_dir():
                self.open_btn.configure(state="normal")
        else:
            self.status.configure(text="Stopped with an error – see the log below.")

    def stop(self):
        if self.proc:
            self.write("\nStopping…\n")
            if sys.platform == "win32":   # also ends the running ffmpeg
                subprocess.run(["taskkill", "/F", "/T", "/PID", str(self.proc.pid)],
                               capture_output=True, creationflags=subprocess.CREATE_NO_WINDOW)
            else:
                self.proc.terminate()

    def open_output(self):
        if not self.out_dir or not self.out_dir.is_dir():
            return
        if sys.platform == "win32":
            os.startfile(self.out_dir)
        elif sys.platform == "darwin":
            subprocess.Popen(["open", str(self.out_dir)])
        else:
            subprocess.Popen(["xdg-open", str(self.out_dir)])

    def close(self):
        if self.proc and not messagebox.askyesno("Still working", "Stop the running job and close?"):
            return
        self.stop()
        self.save_settings()
        self.root.destroy()


def main():
    root = tk.Tk()
    App(root)
    root.mainloop()


if __name__ == "__main__":
    main()

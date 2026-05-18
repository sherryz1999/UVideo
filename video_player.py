#!/usr/bin/env python3
"""
CueLoop Video Player — adjustable speed, loop regions, and stem separation.

Requirements:
    pip install python-vlc demucs
    VLC media player must be installed (https://www.videolan.org)
"""

import sys
import os
import subprocess
import tempfile
import threading
import tkinter as tk
from tkinter import filedialog

# Auto-locate VLC on Windows before importing python-vlc bindings
if sys.platform == "win32":
    for _d in [
        r"C:\Program Files\VideoLAN\VLC",
        r"C:\Program Files (x86)\VideoLAN\VLC",
        os.path.join(os.environ.get("LOCALAPPDATA", ""), "Programs", "VideoLAN", "VLC"),
    ]:
        if os.path.isfile(os.path.join(_d, "libvlc.dll")):
            os.environ.setdefault("PYTHON_VLC_LIB_PATH", os.path.join(_d, "libvlc.dll"))
            os.environ["PATH"] = _d + os.pathsep + os.environ.get("PATH", "")
            try:
                os.add_dll_directory(_d)
            except AttributeError:
                pass
            break
    else:
        print("ERROR: VLC not found. Install from https://www.videolan.org")
        sys.exit(1)

import vlc

SPEEDS       = [0.50, 0.65, 0.75, 0.85, 0.90, 1.00]
SPEED_LABELS = ["50%", "65%", "75%", "85%", "90%", "100%"]
STEM_ORDER   = ["drums", "bass", "vocals", "other"]

BG      = "#111111"
BG2     = "#1a1a1a"
BTN     = "#2a2a2a"
BTN_ACT = "#c0392b"   # red — active/selected toggle
BTN_ON  = "#1a6b3a"   # green — stem enabled
FG      = "#ffffff"
FG_DIM  = "#888888"


def _ffmpeg():
    try:
        import imageio_ffmpeg
        return imageio_ffmpeg.get_ffmpeg_exe()
    except ImportError:
        return "ffmpeg"


def _parse_time(s):
    """'m:ss' or 'h:mm:ss' → seconds float. Returns None if blank/invalid."""
    s = s.strip()
    if not s:
        return None
    try:
        parts = s.split(":")
        return sum(float(p) * 60 ** i for i, p in enumerate(reversed(parts)))
    except Exception:
        return None


def _fmt(ms):
    s = max(0, ms // 1000)
    return f"{s // 60}:{s % 60:02d}"


class VideoPlayer:
    def __init__(self, root, filepath=None):
        self.root = root
        self.root.title("CueLoop Player")
        self.root.configure(bg=BG)
        self.root.minsize(740, 520)

        # --audio-time-stretch enables scaletempo: speed changes keep original pitch
        self.instance = vlc.Instance("--audio-time-stretch")
        self.player   = self.instance.media_player_new()

        self.current_speed = 1.0
        self._seeking      = False
        self._loop         = False
        self._src_path     = None   # original video file
        self._stems_dir    = None   # folder of separated .wav stems
        self._stem_paths   = {}     # stem name → wav path
        self._stem_active  = {}     # stem name → bool
        self._prev_mix     = None   # previous temp combined file to delete

        self._build_ui()
        self._poll()

        if filepath and os.path.isfile(filepath):
            self._load(filepath)

    # ── UI construction ───────────────────────────────────────────────────────

    def _build_ui(self):
        self.video_frame = tk.Frame(self.root, bg="black")
        self.video_frame.pack(fill=tk.BOTH, expand=True)

        panel = tk.Frame(self.root, bg=BG2, pady=8)
        panel.pack(fill=tk.X, side=tk.BOTTOM)

        # Seek bar
        self.seek_var = tk.DoubleVar()
        seek = tk.Scale(panel, variable=self.seek_var, from_=0, to=1000,
                        orient=tk.HORIZONTAL, bg=BG2, fg=FG,
                        troughcolor="#333", activebackground=BTN_ACT,
                        highlightthickness=0, showvalue=False, bd=0)
        seek.pack(fill=tk.X, padx=14, pady=(4, 0))
        seek.bind("<ButtonPress-1>",   lambda e: setattr(self, "_seeking", True))
        seek.bind("<ButtonRelease-1>", self._on_seek)

        self.time_lbl = tk.Label(panel, text="0:00 / 0:00",
                                  bg=BG2, fg=FG_DIM, font=("Consolas", 9))
        self.time_lbl.pack()

        # Row 1: Play | Begin [__] End [__] | Loop | Open
        r1 = tk.Frame(panel, bg=BG2)
        r1.pack(pady=(4, 2))

        self.play_btn = self._mkbtn(r1, "▶  Play", self._toggle_play)
        self.play_btn.pack(side=tk.LEFT, padx=4)

        tk.Label(r1, text="Begin", bg=BG2, fg=FG_DIM,
                 font=("Segoe UI", 9)).pack(side=tk.LEFT, padx=(10, 2))
        self.loop_start = tk.Entry(r1, width=6, bg=BTN, fg=FG,
                                    insertbackground=FG, relief=tk.FLAT,
                                    font=("Consolas", 9))
        self.loop_start.insert(0, "0:00")
        self.loop_start.pack(side=tk.LEFT)

        tk.Label(r1, text="End", bg=BG2, fg=FG_DIM,
                 font=("Segoe UI", 9)).pack(side=tk.LEFT, padx=(8, 2))
        self.loop_end = tk.Entry(r1, width=6, bg=BTN, fg=FG,
                                  insertbackground=FG, relief=tk.FLAT,
                                  font=("Consolas", 9))
        self.loop_end.pack(side=tk.LEFT)

        self.loop_btn = self._mkbtn(r1, "⟳  Loop", self._toggle_loop)
        self.loop_btn.pack(side=tk.LEFT, padx=(10, 4))

        self._mkbtn(r1, "Open File…", self._open_file).pack(side=tk.LEFT, padx=4)

        # Row 2: Speed buttons
        r2 = tk.Frame(panel, bg=BG2)
        r2.pack(pady=(2, 4))

        tk.Label(r2, text="Speed:", bg=BG2, fg=FG_DIM,
                 font=("Segoe UI", 9)).pack(side=tk.LEFT, padx=(0, 6))
        self.speed_btns = []
        for spd, lbl in zip(SPEEDS, SPEED_LABELS):
            b = tk.Button(r2, text=lbl, width=5,
                          command=lambda s=spd: self._set_speed(s),
                          bg=BTN, fg=FG, relief=tk.FLAT,
                          activebackground=BTN_ACT, activeforeground=FG,
                          font=("Segoe UI", 9), padx=6, pady=4, bd=0)
            b.pack(side=tk.LEFT, padx=2)
            self.speed_btns.append((spd, b))
        self._highlight_speed(1.0)

        # Row 3: Split Audio button + status label
        r3 = tk.Frame(panel, bg=BG2)
        r3.pack(pady=(2, 4))

        self.split_btn = self._mkbtn(r3, "⚡ Split Audio", self._start_separation)
        self.split_btn.pack(side=tk.LEFT, padx=4)
        self.split_lbl = tk.Label(r3, text="", bg=BG2, fg=FG_DIM,
                                   font=("Segoe UI", 9))
        self.split_lbl.pack(side=tk.LEFT, padx=4)

        # Row 4: Stem toggle buttons (hidden until separation is done)
        self.stem_row = tk.Frame(panel, bg=BG2)
        self.stem_btns = {}

    def _mkbtn(self, parent, text, cmd):
        return tk.Button(parent, text=text, command=cmd,
                         bg=BTN, fg=FG, relief=tk.FLAT,
                         activebackground="#444", activeforeground=FG,
                         font=("Segoe UI", 10), padx=12, pady=6, bd=0)

    # ── File loading ──────────────────────────────────────────────────────────

    def _open_file(self):
        path = filedialog.askopenfilename(
            title="Open video",
            filetypes=[("Video files", "*.mp4 *.mkv *.avi *.mov *.webm *.m4v"),
                       ("All files", "*.*")])
        if path:
            self._load(path)

    def _load(self, path, seek_ms=0):
        self._src_path    = path
        self._stems_dir   = None
        self._stem_paths  = {}
        self._stem_active = {}
        self.stem_row.pack_forget()
        self.split_lbl.config(text="")
        self.split_btn.config(state=tk.NORMAL)
        self._vlc_open(path, seek_ms)
        self.root.title(f"CueLoop — {os.path.basename(path)}")

    def _vlc_open(self, path, seek_ms=0):
        """Load path into the VLC player, optionally seeking to seek_ms after start."""
        media = self.instance.media_new(path)
        self.player.set_media(media)
        self.root.update()
        if sys.platform == "win32":
            self.player.set_hwnd(self.video_frame.winfo_id())
        elif sys.platform == "darwin":
            self.player.set_nsobject(self.video_frame.winfo_id())
        else:
            self.player.set_xwindow(self.video_frame.winfo_id())
        self.player.play()
        if seek_ms > 0:
            self.root.after(400, lambda: self.player.set_time(max(0, seek_ms)))
        self.root.after(100, lambda: self.player.set_rate(self.current_speed))
        self.play_btn.config(text="⏸  Pause")

    # ── Playback controls ─────────────────────────────────────────────────────

    def _toggle_play(self):
        if self.player.is_playing():
            self.player.pause()
            self.play_btn.config(text="▶  Play")
        else:
            self.player.play()
            self.player.set_rate(self.current_speed)
            self.play_btn.config(text="⏸  Pause")

    def _toggle_loop(self):
        self._loop = not self._loop
        self.loop_btn.config(bg=BTN_ACT if self._loop else BTN)

    def _set_speed(self, speed):
        self.current_speed = speed
        self.player.set_rate(speed)
        self._highlight_speed(speed)

    def _highlight_speed(self, speed):
        for s, b in self.speed_btns:
            b.config(bg=BTN_ACT if abs(s - speed) < 0.001 else BTN)

    def _on_seek(self, _e):
        if self.player.get_length() > 0:
            self.player.set_position(self.seek_var.get() / 1000.0)
        self._seeking = False

    # ── Stem separation ───────────────────────────────────────────────────────

    def _start_separation(self):
        if not self._src_path:
            return
        self.split_btn.config(state=tk.DISABLED)
        self.split_lbl.config(
            text="Separating audio — first run downloads model (~80 MB), may take 1-3 min…")
        threading.Thread(target=self._run_demucs, daemon=True).start()

    def _run_demucs(self):
        out_dir = tempfile.mkdtemp(prefix="cueloop_stems_")
        result = subprocess.run(
            [sys.executable, "-m", "demucs", "-o", out_dir, self._src_path],
            capture_output=True, text=True)

        if result.returncode != 0:
            lines = result.stderr.strip().splitlines()
            err = lines[-1] if lines else "demucs failed — is it installed? pip install demucs"
            self.root.after(0, lambda: (
                self.split_lbl.config(text=f"Error: {err}"),
                self.split_btn.config(state=tk.NORMAL),
            ))
            return

        # Locate stem wavs: out_dir/<model>/<trackname>/<stem>.wav
        stems_dir = None
        for root, _dirs, files in os.walk(out_dir):
            if any(f.endswith(".wav") for f in files):
                stems_dir = root
                break

        if not stems_dir:
            self.root.after(0, lambda: self.split_lbl.config(text="No stems found."))
            return

        self._stems_dir = stems_dir
        self.root.after(0, self._build_stem_ui)

    def _build_stem_ui(self):
        self.split_lbl.config(text="")
        for w in self.stem_row.winfo_children():
            w.destroy()
        self.stem_btns.clear()

        # Collect available stems
        self._stem_paths = {
            os.path.splitext(f)[0]: os.path.join(self._stems_dir, f)
            for f in os.listdir(self._stems_dir) if f.endswith(".wav")
        }
        self._stem_active = {s: True for s in self._stem_paths}

        tk.Label(self.stem_row, text="Instruments:", bg=BG2, fg=FG_DIM,
                 font=("Segoe UI", 9)).pack(side=tk.LEFT, padx=(4, 6))

        ordered = [s for s in STEM_ORDER if s in self._stem_paths]
        ordered += [s for s in self._stem_paths if s not in STEM_ORDER]

        for stem in ordered:
            b = tk.Button(self.stem_row, text=stem.capitalize(), width=8,
                          command=lambda s=stem: self._toggle_stem(s),
                          bg=BTN_ON, fg=FG, relief=tk.FLAT,
                          activebackground="#268a50", activeforeground=FG,
                          font=("Segoe UI", 9), padx=6, pady=4, bd=0)
            b.pack(side=tk.LEFT, padx=2)
            self.stem_btns[stem] = b

        self.stem_row.pack(pady=(0, 8))
        self._remix()

    def _toggle_stem(self, stem):
        self._stem_active[stem] = not self._stem_active[stem]
        self.stem_btns[stem].config(bg=BTN_ON if self._stem_active[stem] else BTN)
        self._remix()

    def _remix(self):
        active = [self._stem_paths[s] for s, on in self._stem_active.items() if on]
        if not active:
            return
        cur_ms      = self.player.get_time()
        was_playing = self.player.is_playing()
        threading.Thread(
            target=self._do_remix, args=(active, cur_ms, was_playing), daemon=True
        ).start()

    def _do_remix(self, stem_wavs, cur_ms, was_playing):
        ff = _ffmpeg()

        # Mix active stem wavs into one wav
        mix_out = tempfile.mktemp(suffix=".wav", prefix="cueloop_mix_")
        if len(stem_wavs) == 1:
            subprocess.run([ff, "-y", "-i", stem_wavs[0], mix_out],
                           capture_output=True)
        else:
            inputs = [arg for p in stem_wavs for arg in ("-i", p)]
            subprocess.run(
                [ff, "-y"] + inputs + [
                    "-filter_complex", f"amix=inputs={len(stem_wavs)}:normalize=0",
                    mix_out],
                capture_output=True)

        if not os.path.isfile(mix_out):
            return

        # Mux original video stream with new mixed audio
        combined = tempfile.mktemp(suffix=".mp4", prefix="cueloop_combined_")
        subprocess.run(
            [ff, "-y",
             "-i", self._src_path, "-i", mix_out,
             "-map", "0:v:0", "-map", "1:a:0",
             "-c:v", "copy", "-c:a", "aac", "-shortest",
             combined],
            capture_output=True)

        try:
            os.remove(mix_out)
        except OSError:
            pass

        if not os.path.isfile(combined):
            return

        prev, self._prev_mix = self._prev_mix, combined
        self.root.after(0, lambda: self._hot_swap(combined, cur_ms, was_playing, prev))

    def _hot_swap(self, path, seek_ms, was_playing, prev_path):
        self._vlc_open(path, seek_ms)
        if not was_playing:
            self.root.after(550, self.player.pause)
        if prev_path:
            self.root.after(1000, lambda: self._safe_remove(prev_path))

    @staticmethod
    def _safe_remove(path):
        try:
            os.remove(path)
        except OSError:
            pass

    # ── Polling ───────────────────────────────────────────────────────────────

    def _poll(self):
        if not self._seeking:
            pos    = self.player.get_position()
            cur_ms = self.player.get_time()
            len_ms = self.player.get_length()

            if pos >= 0:
                self.seek_var.set(pos * 1000)
            self.time_lbl.config(text=f"{_fmt(cur_ms)} / {_fmt(len_ms)}")

            if self._loop and self.player.is_playing() and len_ms > 0:
                start_s  = _parse_time(self.loop_start.get())
                end_s    = _parse_time(self.loop_end.get())
                start_ms = int(start_s * 1000) if start_s is not None else 0
                end_ms   = int(end_s   * 1000) if end_s   is not None else len_ms
                if cur_ms >= end_ms:
                    self.player.set_time(start_ms)
                    self.player.set_rate(self.current_speed)

            state = self.player.get_state()
            if state in (vlc.State.Ended, vlc.State.Stopped):
                if self._loop and state == vlc.State.Ended:
                    start_s  = _parse_time(self.loop_start.get())
                    start_ms = int(start_s * 1000) if start_s is not None else 0
                    self.player.set_time(start_ms)
                    self.player.play()
                    self.player.set_rate(self.current_speed)
                else:
                    self.play_btn.config(text="▶  Play")

        self.root.after(200, self._poll)


def main():
    root = tk.Tk()
    root.geometry("800x580")
    VideoPlayer(root, sys.argv[1] if len(sys.argv) > 1 else None)
    root.mainloop()


if __name__ == "__main__":
    main()

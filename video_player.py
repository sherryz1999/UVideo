#!/usr/bin/env python3
"""
CueLoop Video Player — play MP4 clips at adjustable speeds.

Requirements:
    pip install python-vlc
    VLC media player must be installed (https://www.videolan.org)
"""

import sys
import os
import tkinter as tk
from tkinter import filedialog

# Auto-locate VLC on Windows before importing python-vlc bindings
if sys.platform == "win32":
    _vlc_candidates = [
        r"C:\Program Files\VideoLAN\VLC",
        r"C:\Program Files (x86)\VideoLAN\VLC",
        os.path.join(os.environ.get("LOCALAPPDATA", ""), "Programs", "VideoLAN", "VLC"),
    ]
    for _d in _vlc_candidates:
        if os.path.isfile(os.path.join(_d, "libvlc.dll")):
            os.environ.setdefault("PYTHON_VLC_LIB_PATH", os.path.join(_d, "libvlc.dll"))
            os.environ["PATH"] = _d + os.pathsep + os.environ.get("PATH", "")
            try:
                os.add_dll_directory(_d)
            except AttributeError:
                pass
            break
    else:
        print("ERROR: VLC not found. Install it from https://www.videolan.org")
        sys.exit(1)

import vlc

SPEEDS = [0.50, 0.65, 0.75, 0.85, 0.90, 1.00]
SPEED_LABELS = ["50%", "65%", "75%", "85%", "90%", "100%"]

BG        = "#111111"
BG2       = "#1a1a1a"
BTN       = "#2a2a2a"
BTN_ACT   = "#c0392b"
FG        = "#ffffff"
FG_DIM    = "#888888"


class VideoPlayer:
    def __init__(self, root, filepath=None):
        self.root = root
        self.root.title("CueLoop Player")
        self.root.configure(bg=BG)
        self.root.minsize(640, 480)

        self.instance = vlc.Instance("--no-xlib")
        self.player   = self.instance.media_player_new()
        self.current_speed = 1.0
        self._seeking      = False

        self._build_ui()
        self._poll()

        if filepath and os.path.isfile(filepath):
            self._load(filepath)

    # ── UI ────────────────────────────────────────────────────────────────────

    def _build_ui(self):
        # Video canvas
        self.video_frame = tk.Frame(self.root, bg="black")
        self.video_frame.pack(fill=tk.BOTH, expand=True)

        # Controls panel
        panel = tk.Frame(self.root, bg=BG2, pady=8)
        panel.pack(fill=tk.X, side=tk.BOTTOM)

        # Seek bar
        self.seek_var = tk.DoubleVar()
        self.seek_bar = tk.Scale(
            panel, variable=self.seek_var, from_=0, to=1000,
            orient=tk.HORIZONTAL, bg=BG2, fg=FG,
            troughcolor="#333", activebackground=BTN_ACT,
            highlightthickness=0, showvalue=False, bd=0,
        )
        self.seek_bar.pack(fill=tk.X, padx=14, pady=(4, 0))
        self.seek_bar.bind("<ButtonPress-1>",   lambda e: setattr(self, "_seeking", True))
        self.seek_bar.bind("<ButtonRelease-1>", self._on_seek)

        # Time label
        self.time_lbl = tk.Label(
            panel, text="0:00 / 0:00",
            bg=BG2, fg=FG_DIM, font=("Consolas", 9),
        )
        self.time_lbl.pack()

        # Play / Open row
        row1 = tk.Frame(panel, bg=BG2)
        row1.pack(pady=(4, 2))

        self.play_btn = self._btn(row1, "▶  Play",   self._toggle_play)
        self._btn(row1, "Open File…", self._open_file)

        self.play_btn.pack(side=tk.LEFT, padx=4)
        list(row1.children.values())[-1].pack(side=tk.LEFT, padx=4)

        # Speed buttons row
        row2 = tk.Frame(panel, bg=BG2)
        row2.pack(pady=(2, 8))

        tk.Label(row2, text="Speed:", bg=BG2, fg=FG_DIM,
                 font=("Segoe UI", 9)).pack(side=tk.LEFT, padx=(0, 6))

        self.speed_btns = []
        for speed, label in zip(SPEEDS, SPEED_LABELS):
            b = tk.Button(
                row2, text=label, width=5,
                command=lambda s=speed: self._set_speed(s),
                bg=BTN, fg=FG, relief=tk.FLAT,
                activebackground=BTN_ACT, activeforeground=FG,
                font=("Segoe UI", 9), padx=6, pady=4, bd=0,
            )
            b.pack(side=tk.LEFT, padx=2)
            self.speed_btns.append((speed, b))

        self._highlight_speed(1.0)

    def _btn(self, parent, text, cmd):
        return tk.Button(
            parent, text=text, command=cmd,
            bg=BTN, fg=FG, relief=tk.FLAT,
            activebackground="#444", activeforeground=FG,
            font=("Segoe UI", 10), padx=14, pady=6, bd=0,
        )

    # ── File loading ──────────────────────────────────────────────────────────

    def _open_file(self):
        path = filedialog.askopenfilename(
            title="Open video",
            filetypes=[
                ("Video files", "*.mp4 *.mkv *.avi *.mov *.webm *.m4v"),
                ("All files",   "*.*"),
            ],
        )
        if path:
            self._load(path)

    def _load(self, path):
        media = self.instance.media_new(path)
        self.player.set_media(media)
        self.root.update()  # ensure winfo_id is valid before embedding

        if sys.platform == "win32":
            self.player.set_hwnd(self.video_frame.winfo_id())
        elif sys.platform == "darwin":
            self.player.set_nsobject(self.video_frame.winfo_id())
        else:
            self.player.set_xwindow(self.video_frame.winfo_id())

        self.player.play()
        self.player.set_rate(self.current_speed)
        self.play_btn.config(text="⏸  Pause")
        self.root.title(f"CueLoop — {os.path.basename(path)}")

    # ── Playback controls ─────────────────────────────────────────────────────

    def _toggle_play(self):
        if self.player.is_playing():
            self.player.pause()
            self.play_btn.config(text="▶  Play")
        else:
            self.player.play()
            self.player.set_rate(self.current_speed)
            self.play_btn.config(text="⏸  Pause")

    def _set_speed(self, speed):
        self.current_speed = speed
        self.player.set_rate(speed)
        self._highlight_speed(speed)

    def _highlight_speed(self, speed):
        for s, btn in self.speed_btns:
            btn.config(bg=BTN_ACT if abs(s - speed) < 0.001 else BTN)

    def _on_seek(self, _event):
        if self.player.get_length() > 0:
            self.player.set_position(self.seek_var.get() / 1000.0)
        self._seeking = False

    # ── Polling ───────────────────────────────────────────────────────────────

    def _fmt(self, ms):
        s = max(0, ms // 1000)
        return f"{s // 60}:{s % 60:02d}"

    def _poll(self):
        if not self._seeking:
            pos    = self.player.get_position()
            cur_ms = self.player.get_time()
            len_ms = self.player.get_length()

            if pos >= 0:
                self.seek_var.set(pos * 1000)
            self.time_lbl.config(text=f"{self._fmt(cur_ms)} / {self._fmt(len_ms)}")

            state = self.player.get_state()
            if state in (vlc.State.Ended, vlc.State.Stopped):
                self.play_btn.config(text="▶  Play")

        self.root.after(200, self._poll)


def main():
    root = tk.Tk()
    root.geometry("720x560")
    VideoPlayer(root, sys.argv[1] if len(sys.argv) > 1 else None)
    root.mainloop()


if __name__ == "__main__":
    main()

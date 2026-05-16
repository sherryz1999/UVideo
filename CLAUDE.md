# YouTube Ad Skipper

A YouTube video player that auto-detects and skips advertisements using browser automation, with two frontends: a Python CLI and a Godot 4 GUI.

## Project structure

```
C:\uvideo\
├── youtube_player.py   # Core Python backend (CLI + library)
├── main.gd             # Godot GUI logic (GDScript)
├── main.tscn           # Godot scene (all UI nodes declared here)
├── project.godot       # Godot 4.2 project config — window 660×560
└── requirements.txt    # pip dependencies
```

## Dependencies

```
pip install selenium webdriver-manager undetected-chromedriver
```

- **Chrome** must be installed. `undetected_chromedriver` downloads the matching ChromeDriver automatically.
- Current Chrome version is **147**. The driver is pinned with `version_main=147` in `build_driver()`. Update this when Chrome updates.

## Running

**CLI:**
```
python youtube_player.py "https://www.youtube.com/watch?v=..."
```

**Godot GUI:**
Open `project.godot` in Godot 4, press F5. The GUI launches `youtube_player.py` as a subprocess.

## Architecture

### youtube_player.py

- `build_driver()` — creates a Chrome WebDriver via `undetected_chromedriver` (bypasses YouTube's bot detection). Falls back to plain Selenium with `--disable-blink-features=AutomationControlled` if `uc` is not installed.
- `watch(url, poll_sec, status_file)` — main loop: navigates to URL, polls every 0.5 s for skip buttons, writes JSON status updates to `status_file` when provided.
- `_write_status(path, status, message)` — writes `{"status": ..., "message": ...}` to the status file for the GUI to read.
- `--status-file <path>` CLI argument — used by the Godot GUI for live status polling.

**Ad selector strategy:** YouTube changes CSS class names. Multiple selectors are tried in order:
```python
AD_SKIP_SELECTORS = [
    ".ytp-skip-ad-button",
    ".ytp-ad-skip-button",
    ".ytp-ad-skip-button-modern",
    ".ytp-ad-skip-button-slot button",
    "button[class*='skip-ad']",
]
```
If YouTube changes class names again, add the new selector to this list.

**Browser-closed detection:** `_browser_was_closed()` checks the exception message for known phrases (`invalid session id`, `disconnected`, `no such window`) so closing Chrome manually exits cleanly.

### Godot GUI (main.gd / main.tscn)

- All UI nodes are declared in `main.tscn`. Do **not** build UI in `_ready()` — Godot layout timing makes programmatically-added controls invisible.
- Node references use `@onready var x = $Path/To/Node`.
- `OS.create_process("python", [...])` launches the Python script non-blocking.
- A `Timer` polls every 0.5 s: reads the JSON status file and checks `OS.is_process_running(pid)`.
- Status file path: `ProjectSettings.globalize_path("user://yt_status.json")` — resolves to AppData at runtime.
- Window close (`NOTIFICATION_WM_CLOSE_REQUEST`) kills the Python process before quitting. Requires `get_tree().auto_accept_quit = false` (NOT `get_tree().root.auto_accept_quit`).

## Known Godot 4.2 quirks fixed

| Error | Fix |
|---|---|
| `OS.get_temp_dir()` not found | Use `ProjectSettings.globalize_path("user://...")` instead |
| `auto_accept_quit` on Window | Property belongs to `SceneTree`: `get_tree().auto_accept_quit = false` |
| UI not visible | Declare all nodes in `.tscn`; do not build UI programmatically in `_ready()` |

## Known download quirks fixed

| Issue | Root cause | Fix |
|---|---|---|
| Unlisted videos fail to download | yt-dlp reports `"Video unavailable"` but `AUTH_HINTS` only checked `"not available"` — not a substring of `"unavailable"` — so the Chrome cookie retry never triggered | Added `"unavailable"` to `AUTH_HINTS` in `record_clip()` |

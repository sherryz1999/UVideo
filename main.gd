extends Control

@onready var url_input   : LineEdit      = $Margin/VBox/URLRow/URLInput
@onready var profile_opt : OptionButton  = $Margin/VBox/ProfileRow/ProfileOpt
@onready var start_input : LineEdit      = $Margin/VBox/TimeRow/StartTime
@onready var end_input   : LineEdit      = $Margin/VBox/TimeRow/EndTime
@onready var record_btn  : Button        = $Margin/VBox/TimeRow/RecordBtn
@onready var play_btn    : Button        = $Margin/VBox/BtnRow/PlayBtn
@onready var stop_btn    : Button        = $Margin/VBox/BtnRow/StopBtn
@onready var status_dot  : ColorRect     = $Margin/VBox/StatusRow/DotWrap/StatusDot
@onready var status_lbl  : Label         = $Margin/VBox/StatusRow/StatusLbl
@onready var log_box     : RichTextLabel = $Margin/VBox/Scroll/LogBox
@onready var history_opt : OptionButton  = $Margin/VBox/HistRow/HistoryOpt
@onready var progress_bar : ProgressBar  = $Margin/VBox/ProgressBar

var process_pid     : int    = -1
var status_file     : String = ""
var history         : Array  = []
var prev_state      : String = ""
var poll_timer      : Timer
var chrome_profiles : Array  = []  # parallel array of profile dir names

const STATE_COLOR := {
	"idle"      : Color(0.55, 0.55, 0.55),
	"loading"   : Color(1.00, 0.80, 0.20),
	"playing"   : Color(0.22, 0.87, 0.22),
	"ad"        : Color(1.00, 0.47, 0.12),
	"skipped"   : Color(0.22, 0.87, 0.22),
	"recording" : Color(0.75, 0.20, 0.90),
	"done"      : Color(0.22, 0.87, 0.22),
	"error"     : Color(1.00, 0.07, 0.07),
}

# ── Lifecycle ──────────────────────────────────────────────────────────────────
func _ready() -> void:
	get_tree().auto_accept_quit = false

	url_input.connect("text_submitted", func(_t: String) -> void: _on_play())
	$Margin/VBox/URLRow/PasteBtn.connect("pressed", _on_paste)
	play_btn.connect("pressed", _on_play)
	stop_btn.connect("pressed", _on_stop)
	record_btn.connect("pressed", _on_record)
	history_opt.connect("item_selected", _on_history_selected)
	history_opt.add_item("— no history yet —")

	_load_chrome_profiles()

	poll_timer = Timer.new()
	poll_timer.wait_time = 0.5
	poll_timer.connect("timeout", _poll)
	add_child(poll_timer)

	_set_status("idle", "Idle")

func _notification(what: int) -> void:
	if what == NOTIFICATION_WM_CLOSE_REQUEST:
		_on_stop()
		get_tree().quit()

# ── Helpers ────────────────────────────────────────────────────────────────────
func _set_status(key: String, text: String) -> void:
	var c : Color = STATE_COLOR.get(key, Color(0.55, 0.55, 0.55))
	status_dot.color = c
	status_lbl.text  = text
	status_lbl.add_theme_color_override("font_color", c)

func _log(msg: String) -> void:
	var t := Time.get_time_string_from_system()
	log_box.append_text("[color=#666666][%s][/color] %s\n" % [t, msg])

func _load_chrome_profiles() -> void:
	var base := OS.get_environment("LOCALAPPDATA").path_join("Google/Chrome/User Data")
	if not DirAccess.dir_exists_absolute(base):
		profile_opt.add_item("Default  (Default)")
		chrome_profiles = ["Default"]
		return

	var found : Array = []
	var d := DirAccess.open(base)
	if d:
		d.list_dir_begin()
		var entry := d.get_next()
		while entry != "":
			if entry == "Default" or entry.begins_with("Profile "):
				var has_cookies := (
					FileAccess.file_exists(base.path_join(entry).path_join("Network/Cookies")) or
					FileAccess.file_exists(base.path_join(entry).path_join("Cookies"))
				)
				if has_cookies:
					var display := entry
					var pref := FileAccess.open(base.path_join(entry).path_join("Preferences"), FileAccess.READ)
					if pref:
						var prefs = JSON.parse_string(pref.get_as_text())
						pref.close()
						if typeof(prefs) == TYPE_DICTIONARY:
							var pd = prefs.get("profile", {})
							if typeof(pd) == TYPE_DICTIONARY:
								display = pd.get("name", entry)
					found.append({"dir": entry, "name": display})
			entry = d.get_next()
		d.list_dir_end()

	found.sort_custom(func(a, b):
		if a["dir"] == "Default": return true
		if b["dir"] == "Default": return false
		return a["dir"] < b["dir"]
	)

	if found.is_empty():
		profile_opt.add_item("Default  (Default)")
		chrome_profiles = ["Default"]
		return

	for p in found:
		profile_opt.add_item("%s  (%s)" % [p["name"], p["dir"]])
		chrome_profiles.append(p["dir"])

func _selected_profile() -> String:
	var idx := profile_opt.selected
	if idx >= 0 and idx < chrome_profiles.size():
		return chrome_profiles[idx]
	return "Default"

func _python_script() -> String:
	var p := ProjectSettings.globalize_path("res://youtube_player.py")
	if FileAccess.file_exists(p):
		return p
	return OS.get_executable_path().get_base_dir().path_join("youtube_player.py")

func _prepare_status_file() -> void:
	status_file = ProjectSettings.globalize_path("user://yt_status.json")
	if FileAccess.file_exists(status_file):
		DirAccess.remove_absolute(status_file)
	prev_state = ""

func _set_busy(busy: bool) -> void:
	play_btn.disabled   = busy
	record_btn.disabled = busy
	stop_btn.disabled   = not busy

# ── Event handlers ─────────────────────────────────────────────────────────────
func _on_paste() -> void:
	url_input.text = DisplayServer.clipboard_get()
	url_input.grab_focus()

func _on_play() -> void:
	var url := url_input.text.strip_edges()
	if url.is_empty():
		_log("[color=#ff5555]Please enter a YouTube URL.[/color]")
		return

	if process_pid != -1:
		_on_stop()

	_prepare_status_file()
	_log("Opening: [color=#aaaaff]%s[/color]" % url)
	_set_status("loading", "Loading…")

	process_pid = OS.create_process(
		"python",
		[_python_script(), url, "--status-file", status_file]
	)

	if process_pid == -1:
		_log("[color=#ff5555]Could not launch Python. Is Python installed and in PATH?[/color]")
		_set_status("error", "Launch failed")
		return

	_set_busy(true)
	_log("Browser launched (PID %d)." % process_pid)
	poll_timer.start()

	if url not in history:
		history.append(url)
		if history.size() == 1:
			history_opt.clear()
		history_opt.add_item(url)

func _on_record() -> void:
	var url   := url_input.text.strip_edges()
	var start := start_input.text.strip_edges()
	var end   := end_input.text.strip_edges()

	if url.is_empty():
		_log("[color=#ff5555]Please enter a YouTube URL.[/color]")
		return
	if end.is_empty():
		_log("[color=#ff5555]Please enter an end time.[/color]")
		return
	if start.is_empty():
		start = "0:00"

	if process_pid != -1:
		_on_stop()

	# Build output path: res://clips/clip_YYYYMMDD_HHMMSS.mp4
	var clips_dir := ProjectSettings.globalize_path("res://clips")
	DirAccess.make_dir_absolute(clips_dir)
	var dt := Time.get_datetime_string_from_system().replace("T", "_").replace(":", "").replace("-", "")
	var output_path := clips_dir.path_join("clip_%s.mp4" % dt)

	_prepare_status_file()
	progress_bar.value   = 0.0
	progress_bar.visible = true
	_log("Recording [color=#dd88ff]%s → %s[/color]" % [start, end])
	_set_status("recording", "Recording…")

	process_pid = OS.create_process(
		"python",
		[_python_script(), url,
		 "--record",
		 "--start-time",    start,
		 "--end-time",      end,
		 "--output",        output_path,
		 "--chrome-profile", _selected_profile(),
		 "--status-file",   status_file]
	)

	if process_pid == -1:
		_log("[color=#ff5555]Could not launch Python.[/color]")
		_set_status("error", "Launch failed")
		return

	_set_busy(true)
	_log("Downloading clip (PID %d)…" % process_pid)
	poll_timer.start()

func _on_stop() -> void:
	poll_timer.stop()
	if process_pid != -1:
		OS.kill(process_pid)
		process_pid = -1
		_log("Stopped by user.")
	_set_status("idle", "Idle")
	_set_busy(false)
	progress_bar.visible = false
	progress_bar.value   = 0.0

func _on_history_selected(index: int) -> void:
	url_input.text = history_opt.get_item_text(index)

# ── Polling ────────────────────────────────────────────────────────────────────
func _poll() -> void:
	_read_status_file()

	if process_pid != -1 and not OS.is_process_running(process_pid):
		poll_timer.stop()
		process_pid = -1
		_set_busy(false)
		# Only log "Session ended" if it wasn't a completed recording (handled in _read_status_file)
		if status_lbl.text != "Done" and status_lbl.text != "Idle":
			_log("Session ended.")
			_set_status("idle", "Idle")

func _read_status_file() -> void:
	if not FileAccess.file_exists(status_file):
		return
	var f := FileAccess.open(status_file, FileAccess.READ)
	if not f:
		return
	var text := f.get_as_text()
	f.close()

	var data = JSON.parse_string(text)
	if typeof(data) != TYPE_DICTIONARY:
		return

	var state    : String = data.get("status",   "")
	var message  : String = data.get("message",  "")
	var progress : float  = float(data.get("progress", -1.0))

	if state == "recording" and progress >= 0.0:
		progress_bar.value = progress

	if state == prev_state:
		return

	var from := prev_state
	prev_state = state

	match state:
		"loading":
			_set_status("loading", "Loading…")
			progress_bar.visible = false
		"playing":
			if from == "ad":
				_log("Ad finished — back to video.")
			_set_status("playing", "Playing")
			progress_bar.visible = false
		"ad":
			if from != "ad":
				_log("Ad detected.")
			_set_status("ad", ("Ad — %s" % message) if message != "" else "Ad playing")
		"skipped":
			_log("[color=#55ff55]Ad skipped![/color]")
			_set_status("playing", "Playing")
		"recording":
			_set_status("recording", "Recording…")
			progress_bar.visible = true
		"done":
			_log("[color=#dd88ff]Saved → clips/%s[/color]" % message)
			_set_status("done", "Done")
			progress_bar.visible = false
			progress_bar.value   = 0.0
		"error":
			_log("[color=#ff5555]Error: %s[/color]" % message)
			_set_status("error", "Error")
			progress_bar.visible = false
		"idle":
			_set_status("idle", "Idle")
			progress_bar.visible = false

extends Node
## Unit sound effects (autoload "Sfx"): each unit type's move and attack sounds -- the unit set's
## move_sound / attack_sound, files in res://assets/sounds (tools/make_sounds.py) -- played on their own
## "SFX" bus at the Settings' sound volume (or muted).
##
##   Sfx.play_units(["Armor", "Infantry"], "move")   # one sound per unit type, a little apart
##
## Several units of a type make one sound, not many; a burst of different types is capped (`limit`) and
## spaced out (`spacing`), and the same sound never starts twice within MIN_GAP_MS. `played` reports each
## sound as it is asked for (tests listen to it).

signal played(unit_type: String, kind: String)

const BUS := "SFX"
const VOICES := 8          # sounds that can play at once
const MIN_GAP_MS := 120    # the same sound doesn't restart sooner than this
const SOUND_DIR := "res://assets/sounds/"

var _players: Array = []
var _next_player := 0
var _streams := {}         # file -> AudioStreamWAV (null if it couldn't be loaded)
var _last_start := {}      # file -> msec it last started


func _ready() -> void:
	if AudioServer.get_bus_index(BUS) < 0:
		AudioServer.add_bus()
		AudioServer.set_bus_name(AudioServer.bus_count - 1, BUS)
		AudioServer.set_bus_send(AudioServer.bus_count - 1, "Master")
	for i in VOICES:
		var p := AudioStreamPlayer.new()
		p.bus = BUS
		add_child(p)
		_players.append(p)
	Settings.changed.connect(apply_volume)
	apply_volume()


## The Settings' volume (0-100) and mute, onto the SFX bus.
func apply_volume() -> void:
	var bus := AudioServer.get_bus_index(BUS)
	if bus < 0:
		return
	var level := clampf(float(Settings.sound_volume) / 100.0, 0.0, 1.0)
	AudioServer.set_bus_volume_db(bus, linear_to_db(maxf(level, 0.0001)))
	AudioServer.set_bus_mute(bus, Settings.sound_muted or level <= 0.0)


## One sound per distinct unit type in `types` (in order, at most `limit`), `spacing` seconds apart;
## `gain_db` makes them quieter (a brief background battle, say). `kind`: "move" or "attack".
func play_units(types: Array, kind: String, limit := 3, spacing := 0.18, gain_db := 0.0) -> void:
	var seen := {}
	var n := 0
	for raw in types:
		var unit_type := GameStore.base_type(str(raw))  # (a promoted stack key names its type first)
		if seen.has(unit_type) or n >= limit:
			continue
		seen[unit_type] = true
		if _file(unit_type, kind) == "":
			continue
		play_unit(unit_type, kind, n * spacing, gain_db)
		n += 1


func play_unit(unit_type: String, kind: String, delay := 0.0, gain_db := 0.0) -> void:
	var file := _file(unit_type, kind)
	if file == "":
		return
	played.emit(unit_type, kind)
	if delay > 0.0:
		await get_tree().create_timer(delay).timeout
	var now := Time.get_ticks_msec()
	if now - int(_last_start.get(file, -100000)) < MIN_GAP_MS:
		return
	var stream = _stream(file)
	if stream == null:
		return
	_last_start[file] = now
	var p: AudioStreamPlayer = _players[_next_player]
	_next_player = (_next_player + 1) % _players.size()
	p.stream = stream
	p.volume_db = gain_db
	p.play()


## The file a unit type names for `kind` ("" if none: a Transport has no attack sound).
func _file(unit_type: String, kind: String) -> String:
	var units: Dictionary = GameData.units.get("units", {})
	if not units.has(unit_type):
		return ""
	var f = units[unit_type].get("move_sound" if kind == "move" else "attack_sound")
	return str(f) if f != null else ""


func _stream(file: String):
	if not _streams.has(file):
		# (loaded from the file itself, so a sound synced in after the project was last imported still plays)
		_streams[file] = AudioStreamWAV.load_from_file(ProjectSettings.globalize_path(SOUND_DIR + file))
	return _streams[file]


## The volume controls (Settings, and the main menu's Display & Sound): a slider and Mute.
static func controls() -> VBoxContainer:
	var box := VBoxContainer.new()
	box.add_theme_constant_override("separation", 4)
	var row := HBoxContainer.new()
	row.add_theme_constant_override("separation", 8)
	box.add_child(row)
	row.add_child(HudStyle.label("Sound volume", 13))
	var value := HudStyle.label("%d%%" % int(Settings.sound_volume), 13, HudStyle.TEXT_DIM)
	var slider := HSlider.new()
	slider.min_value = 0
	slider.max_value = 100
	slider.step = 5
	slider.value = Settings.sound_volume
	slider.custom_minimum_size = Vector2(140, 20)
	slider.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	slider.focus_mode = Control.FOCUS_NONE
	slider.value_changed.connect(func(v: float):
		Settings.sound_volume = int(v)
		value.text = "%d%%" % int(v)
		Settings.commit())
	slider.drag_ended.connect(func(_changed: bool): Sfx.play_unit("Armor", "attack"))  # (a sample at the new level)
	row.add_child(slider)
	row.add_child(value)
	var mute := CheckBox.new()
	mute.text = "Mute"
	mute.focus_mode = Control.FOCUS_NONE
	mute.button_pressed = Settings.sound_muted
	mute.toggled.connect(func(on: bool):
		Settings.sound_muted = on
		Settings.commit())
	box.add_child(mute)
	Settings.changed.connect(func():  # (the other copy of these controls changed it)
		if is_instance_valid(slider):
			slider.set_value_no_signal(Settings.sound_volume)
			value.text = "%d%%" % int(Settings.sound_volume)
			mute.set_pressed_no_signal(Settings.sound_muted))
	return box

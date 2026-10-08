extends Node
## Unit sound effects (autoload "Sfx"): each unit type's move and attack sounds -- the unit set's
## move_sound / attack_sound, files in res://assets/sounds (tools/make_sounds.py) -- played on their own
## "SFX" bus at the Settings' sound volume (or muted).
##
##   Sfx.play_units(["Armor", "Infantry"], "move")   # one sound per unit type, a little apart
##   Sfx.play_phase(["Armor"], "move")                 # the same for a phase played back -- unless the last
##                                                     # phase's sounds are still playing: then none at all
##
## The interface's own sound -- a soft typewriter click (assets/sounds/ui/ui_click.wav) -- plays for every
## button, tab and list choice (hooked up here as each one is created), and for map clicks that aren't a
## unit's own sound (main.gd): Sfx.click(). Unit tiles stay silent: their units make their own sounds.
##
## Several units of a type make one sound, not many; a burst of different types is capped (`limit`) and
## spaced out (`spacing`), and the same sound never starts twice within MIN_GAP_MS. `played` reports each
## sound as it is asked for (tests listen to it).

signal played(unit_type: String, kind: String)

const BUS := "SFX"
const VOICES := 8          # sounds that can play at once
const MIN_GAP_MS := 120    # the same sound doesn't restart sooner than this
const SOUND_DIR := "res://assets/sounds/"
const CLICK := "ui/ui_click.wav"
const CLICK_GAIN_DB := -12.0  # played softly, under the unit sounds
const CLICK_GAP_MS := 45       # a press seen twice (a button inside a tab, say) clicks once

var _players: Array = []
var _next_player := 0
var _streams := {}         # file -> AudioStreamWAV (null if it couldn't be loaded)
var _last_start := {}      # file -> msec it last started
var _phase_until := 0      # msec when the last played-back phase's sounds end (play_phase)
var _click_player: AudioStreamPlayer
var _last_click := -100000


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
	_click_player = AudioStreamPlayer.new()
	_click_player.bus = BUS
	_click_player.volume_db = CLICK_GAIN_DB
	_click_player.max_polyphony = 3
	add_child(_click_player)
	Settings.changed.connect(apply_volume)
	apply_volume()
	get_tree().node_added.connect(_hook_clicks)


## The interface's click: soft, and never two at once for one press.
func click() -> void:
	var now := Time.get_ticks_msec()
	if now - _last_click < CLICK_GAP_MS:
		return
	_last_click = now
	var stream = _stream(CLICK)
	if stream == null:
		return
	_click_player.stream = stream
	_click_player.play()


## Every button (but a unit tile), tab bar and pop-up list clicks as it's pressed.
func _hook_clicks(node: Node) -> void:
	if node is UnitTile:
		return
	if node is BaseButton:
		(node as BaseButton).pressed.connect(click)
	elif node is TabBar:
		(node as TabBar).tab_clicked.connect(func(_tab: int): click())
	elif node is PopupMenu:
		(node as PopupMenu).index_pressed.connect(func(_i: int): click())


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


## A phase's sounds as the game plays it back (TurnStepper): play_units' -- but only once the previous phase's
## have finished. Phases going by faster than their sounds (nothing pausing them) would otherwise talk over
## each other, so a phase that comes while they still play is silent. Returns whether it played.
func play_phase(types: Array, kind: String, limit := 3, spacing := 0.18, gain_db := 0.0) -> bool:
	var now := Time.get_ticks_msec()
	if now < _phase_until:
		return false
	var longest := 0.0
	var n := 0
	var seen := {}
	for raw in types:
		var unit_type := GameStore.base_type(str(raw))
		var file := _file(unit_type, kind)
		if seen.has(unit_type) or n >= limit or file == "":
			continue
		seen[unit_type] = true
		var stream = _stream(file)
		if stream != null:
			longest = maxf(longest, n * spacing + float(stream.get_length()))
		n += 1
	if n == 0:
		return false
	_phase_until = now + int(longest * 1000.0)
	play_units(types, kind, limit, spacing, gain_db)
	return true


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
		# (read from the file itself, so a sound synced in after the project was last imported still plays --
		# through FileAccess, which also reads it from inside an exported game)
		var bytes := FileAccess.get_file_as_bytes(SOUND_DIR + file)
		_streams[file] = AudioStreamWAV.load_from_buffer(bytes) if not bytes.is_empty() else null
	return _streams[file]


## The volume controls (Settings, and the main menu's Settings): the unit sounds' slider and Mute, and the
## soundtrack's (Music) -- each its own volume, muted on its own.
static func controls() -> VBoxContainer:
	var box := VBoxContainer.new()
	box.add_theme_constant_override("separation", 4)
	_volume_row(box, "Sound volume", "Mute", "sound_volume", "sound_muted",
		func(): Sfx.play_unit("Armor", "attack"))  # (a sample at the new level)
	_volume_row(box, "Music volume", "Mute music", "music_volume", "music_muted", Callable())
	return box


## A volume slider and its Mute box, for Settings' `volume` / `muted` fields; `sample`: what to play after a
## drag (to hear the new level), if anything.
static func _volume_row(box: VBoxContainer, title: String, mute_text: String, volume: String, muted: String,
		sample: Callable) -> void:
	var row := HBoxContainer.new()
	row.add_theme_constant_override("separation", 8)
	box.add_child(row)
	var name_label := HudStyle.label(title, 13)
	name_label.custom_minimum_size = Vector2(96, 0)
	row.add_child(name_label)
	var value := HudStyle.label("%d%%" % int(Settings.get(volume)), 13, HudStyle.TEXT_DIM)
	var slider := HSlider.new()
	slider.min_value = 0
	slider.max_value = 100
	slider.step = 5
	slider.value = Settings.get(volume)
	slider.custom_minimum_size = Vector2(140, 20)
	slider.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	slider.focus_mode = Control.FOCUS_NONE
	slider.value_changed.connect(func(v: float):
		Settings.set(volume, int(v))
		value.text = "%d%%" % int(v)
		Settings.commit())
	if sample.is_valid():
		slider.drag_ended.connect(func(_changed: bool): sample.call())
	row.add_child(slider)
	row.add_child(value)
	var mute := CheckBox.new()
	mute.text = mute_text
	mute.focus_mode = Control.FOCUS_NONE
	mute.button_pressed = Settings.get(muted)
	mute.toggled.connect(func(on: bool):
		Settings.set(muted, on)
		Settings.commit())
	box.add_child(mute)
	Settings.changed.connect(func():  # (the other copy of these controls changed it)
		if is_instance_valid(slider):
			slider.set_value_no_signal(Settings.get(volume))
			value.text = "%d%%" % int(Settings.get(volume))
			mute.set_pressed_no_signal(Settings.get(muted)))

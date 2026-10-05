class_name MyGamesScreen
extends MenuScreen
## My Live Games (reference/GC Launcher_User Profile.odt): every game this player plays in or hosts,
## live or still forming -- its game type, the player's factions, the round, and whose move it is.
## Picking a live one enters it; a forming one, its lobby.

signal back_pressed
signal game_picked(game: Dictionary)

var _list: VBoxContainer
var _empty: Label
var _problem: Label


func _build() -> void:
	var center := CenterContainer.new()
	add_child(center)
	center.set_anchors_and_offsets_preset(Control.PRESET_FULL_RECT)
	var panel := MenuScreen.sheet(760, 10)
	center.add_child(panel)
	var v := MenuScreen.body(panel)
	v.add_child(HudStyle.heading("My Live Games", 22, true))
	var scroll := ScrollContainer.new()
	scroll.custom_minimum_size = Vector2(0, 380)
	scroll.horizontal_scroll_mode = ScrollContainer.SCROLL_MODE_DISABLED
	v.add_child(scroll)
	_list = VBoxContainer.new()
	_list.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	_list.add_theme_constant_override("separation", 6)
	scroll.add_child(_list)
	_empty = HudStyle.label("You're not in any games yet. Start one with New Game, or join one.", 14, HudStyle.TEXT_DIM)
	_empty.autowrap_mode = TextServer.AUTOWRAP_WORD_SMART
	v.add_child(_empty)
	_problem = MenuScreen.problem_label()
	v.add_child(_problem)
	var row := HBoxContainer.new()
	row.add_theme_constant_override("separation", 10)
	v.add_child(row)
	var back := Button.new()
	back.text = "Back"
	HudStyle.secondary(back)
	back.custom_minimum_size = Vector2(140, 40)
	back.pressed.connect(func(): back_pressed.emit())
	row.add_child(back)
	var refresh := Button.new()
	refresh.text = "Refresh"
	HudStyle.secondary(refresh)
	refresh.custom_minimum_size = Vector2(140, 40)
	refresh.pressed.connect(open)
	row.add_child(refresh)


## Shows the screen and asks the server for the list (the reply comes to set_games).
func open() -> void:
	_problem.visible = false
	visible = true
	Net.send_msg({"type": "my_games"})


func close() -> void:
	visible = false


func show_problem(message: String) -> void:
	_problem.text = message
	_problem.visible = true


func set_games(games: Array) -> void:
	for c in _list.get_children():
		c.queue_free()
	_empty.visible = games.is_empty()
	for g in games:
		_list.add_child(_row(g))


static func scenario_name(scenario: Dictionary) -> String:
	match str(scenario.get("kind", "")):
		"fixed":
			return "Global Cataclysm: 1972"
		"new":
			return "New Scenario"
	var name = scenario.get("name")
	return str(name) if name != null else "Scenario"


func _row(g: Dictionary) -> Control:
	var b := Button.new()
	b.custom_minimum_size = Vector2(0, 58)
	b.focus_mode = Control.FOCUS_NONE
	b.pressed.connect(func(): game_picked.emit(g))
	var h := HBoxContainer.new()
	h.add_theme_constant_override("separation", 14)
	h.mouse_filter = Control.MOUSE_FILTER_IGNORE
	b.add_child(h)
	h.set_anchors_and_offsets_preset(Control.PRESET_FULL_RECT)
	h.offset_left = 14
	h.offset_right = -14
	var name_box := VBoxContainer.new()
	name_box.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	name_box.alignment = BoxContainer.ALIGNMENT_CENTER
	name_box.add_child(_text(scenario_name(g.get("scenario", {})), 17, GCTheme.CREAM, true))
	name_box.add_child(_text("Host: %s   Code: %s" % [str(g.get("host_name", "")), str(g.get("code", ""))], 12, GCTheme.CREAM_DIM))
	h.add_child(name_box)
	var factions := HBoxContainer.new()
	factions.add_theme_constant_override("separation", 4)
	factions.alignment = BoxContainer.ALIGNMENT_CENTER
	for f in g.get("my_factions", []):
		if f == null:
			factions.add_child(_text("random", 13, GCTheme.CREAM_DIM))
		else:
			factions.add_child(FactionIcons.make(str(f), 26))
			factions.add_child(_text(str(f), 14, GCTheme.CREAM))
	if (g.get("my_factions", []) as Array).is_empty():
		factions.add_child(_text("watching", 13, GCTheme.CREAM_DIM))
	h.add_child(factions)
	var status := VBoxContainer.new()
	status.alignment = BoxContainer.ALIGNMENT_CENTER
	status.custom_minimum_size = Vector2(190, 0)
	var progress = g.get("progress")
	if str(g.get("status", "")) == "forming":
		status.add_child(_text("Forming", 15, GCTheme.CREAM, true))
		status.add_child(_text("%d open seat(s)" % int(g.get("open_seats", 0)), 12, GCTheme.CREAM_DIM))
	else:
		var round_text := "Round %d" % int(progress.get("round", 1)) if progress is Dictionary else "Starting"
		status.add_child(_text(round_text, 15, GCTheme.CREAM, true))
		var waiting: Array = progress.get("waiting_for", []) if progress is Dictionary else []
		var me := str(Account.user.get("id", ""))
		var line := "Your move" if waiting.has(me) else ("Waiting for a player" if not waiting.is_empty() else "Playing")
		if not bool(g.get("loadable", true)):
			line = "Can't be loaded"
		status.add_child(_text(line, 12, GCTheme.RED_LIGHT if waiting.has(me) else GCTheme.CREAM_DIM))
	h.add_child(status)
	return b


static func _text(s: String, size: int, color: Color, display := false) -> Label:
	var l := Label.new()
	l.text = s.to_upper() if display else s
	l.add_theme_font_size_override("font_size", size)
	l.add_theme_color_override("font_color", color)
	if display:
		l.add_theme_font_override("font", GCTheme.font("display"))
	l.mouse_filter = Control.MOUSE_FILTER_IGNORE
	return l

class_name AvailableGamesScreen
extends MenuScreen
## Join Game (reference/GC Launcher_User Profile.odt): every game still forming with an open seat -- its
## game type, host and seats available -- kept up to date by the server while this is open; a code box
## to go straight to a game; and the chat for everyone browsing. Picking a game opens its lobby.

signal back_pressed
signal game_picked(game_id: String)
signal code_entered(code: String)
signal chat_sent(text: String)

var _list: VBoxContainer
var _empty: Label
var _code: LineEdit
var _problem: Label
var chat: ChatPanel


func _build() -> void:
	var center := CenterContainer.new()
	add_child(center)
	center.set_anchors_and_offsets_preset(Control.PRESET_FULL_RECT)
	var panel := MenuScreen.sheet(1040, 10)
	center.add_child(panel)
	var v := MenuScreen.body(panel)
	v.add_child(HudStyle.heading("Available Games", 22, true))
	var cols := HBoxContainer.new()
	cols.add_theme_constant_override("separation", 18)
	v.add_child(cols)

	var left := VBoxContainer.new()
	left.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	left.add_theme_constant_override("separation", 8)
	cols.add_child(left)
	var scroll := ScrollContainer.new()
	scroll.custom_minimum_size = Vector2(600, 330)
	scroll.horizontal_scroll_mode = ScrollContainer.SCROLL_MODE_DISABLED
	left.add_child(scroll)
	_list = VBoxContainer.new()
	_list.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	_list.add_theme_constant_override("separation", 6)
	scroll.add_child(_list)
	_empty = HudStyle.label("No games are looking for players right now. Start one with New Game.", 14, HudStyle.TEXT_DIM)
	_empty.autowrap_mode = TextServer.AUTOWRAP_WORD_SMART
	left.add_child(_empty)
	var code_row := HBoxContainer.new()
	code_row.add_theme_constant_override("separation", 8)
	left.add_child(code_row)
	code_row.add_child(HudStyle.label("Game code:", 14, HudStyle.TEXT_DIM))
	_code = LineEdit.new()
	_code.max_length = 6
	_code.placeholder_text = "K7QM2X"
	_code.custom_minimum_size = Vector2(130, 34)
	_code.text_submitted.connect(func(_t): _join_code())
	code_row.add_child(_code)
	var join := Button.new()
	join.text = "Join by Code"
	HudStyle.secondary(join)
	join.pressed.connect(_join_code)
	code_row.add_child(join)
	_problem = MenuScreen.problem_label()
	left.add_child(_problem)

	chat = ChatPanel.new(300)
	chat.custom_minimum_size = Vector2(360, 0)
	chat.send.connect(func(t): chat_sent.emit(t))
	cols.add_child(chat)

	var back := Button.new()
	back.text = "Back"
	HudStyle.secondary(back)
	back.custom_minimum_size = Vector2(140, 40)
	back.pressed.connect(func(): back_pressed.emit())
	back.size_flags_horizontal = Control.SIZE_SHRINK_BEGIN
	v.add_child(back)


## Shows the screen; the server's "open_games" reply fills it (and keeps it up to date).
func open() -> void:
	_problem.visible = false
	_code.text = ""
	visible = true
	Net.send_msg({"type": "browse"})


func close() -> void:
	if visible:
		Net.send_msg({"type": "stop_browsing"})
	visible = false


func show_problem(message: String) -> void:
	_problem.text = message
	_problem.visible = true


## The server's "open_games": the list (and, the first time, the chat's history).
func set_games(msg: Dictionary) -> void:
	for c in _list.get_children():
		c.queue_free()
	var games: Array = msg.get("games", [])
	_empty.visible = games.is_empty()
	for g in games:
		_list.add_child(_row(g))
	if msg.has("chat"):
		chat.set_messages(msg["chat"])


func _join_code() -> void:
	var code := _code.text.strip_edges()
	if code != "":
		_problem.visible = false
		code_entered.emit(code)


func _row(g: Dictionary) -> Control:
	var b := Button.new()
	b.custom_minimum_size = Vector2(0, 56)
	b.focus_mode = Control.FOCUS_NONE
	b.pressed.connect(func(): game_picked.emit(str(g["id"])))
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
	name_box.add_child(MyGamesScreen._text(MyGamesScreen.scenario_name(g.get("scenario", {})), 17, GCTheme.CREAM, true))
	name_box.add_child(MyGamesScreen._text("Host: %s" % str(g.get("host_name", "")), 12, GCTheme.CREAM_DIM))
	h.add_child(name_box)
	var seats := VBoxContainer.new()
	seats.alignment = BoxContainer.ALIGNMENT_CENTER
	seats.custom_minimum_size = Vector2(150, 0)
	seats.add_child(MyGamesScreen._text("%d of %d open" % [int(g.get("open_seats", 0)), int(g.get("humans", 0))], 15, GCTheme.CREAM, true))
	seats.add_child(MyGamesScreen._text("human seats", 12, GCTheme.CREAM_DIM))
	h.add_child(seats)
	return b

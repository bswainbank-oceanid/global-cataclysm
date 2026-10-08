class_name AdminScreen
extends MenuScreen
## The Admin panel (admins only, from the main menu; server/admin.py): the server's numbers and build, the
## most players it takes, finding a player -- their details, games and last login -- and locking an account
## (a locked player can't log in, is logged out, and their seats in live games go to bots).

signal back_pressed
signal request(msg: Dictionary)   # a message for the server (admin_overview, admin_find, ...)

var _stats: GridContainer
var _max: SpinBox
var _query: LineEdit
var _results: VBoxContainer
var _problem: Label


func _build() -> void:
	var center := MenuScreen.scroll_center(self)  # (scroll bars when the window is too small for it)
	var panel := MenuScreen.sheet(940, 10)
	center.add_child(panel)
	var v := MenuScreen.body(panel)
	v.add_child(HudStyle.heading("Admin", 22, true))

	var top := HBoxContainer.new()
	top.add_theme_constant_override("separation", 40)
	v.add_child(top)
	_stats = GridContainer.new()
	_stats.columns = 2
	_stats.add_theme_constant_override("h_separation", 16)
	_stats.add_theme_constant_override("v_separation", 4)
	top.add_child(_stats)
	var settings := VBoxContainer.new()
	settings.add_theme_constant_override("separation", 6)
	top.add_child(settings)
	settings.add_child(HudStyle.label("Most players", 14, HudStyle.TEXT_DIM))
	var row := HBoxContainer.new()
	row.add_theme_constant_override("separation", 8)
	settings.add_child(row)
	_max = SpinBox.new()
	_max.min_value = 1
	_max.max_value = 100000
	_max.step = 1
	_max.custom_minimum_size = Vector2(120, 32)
	row.add_child(_max)
	var save := Button.new()
	save.text = "Save"
	HudStyle.secondary(save)
	save.pressed.connect(func(): request.emit({"type": "admin_set_max_users", "max": int(_max.value)}))
	row.add_child(save)
	settings.add_child(HudStyle.label("New players are turned away once there are this many.", 12, HudStyle.TEXT_DIM))

	v.add_child(HudStyle.heading("Find a player", 15))
	var find := HBoxContainer.new()
	find.add_theme_constant_override("separation", 8)
	v.add_child(find)
	_query = LineEdit.new()
	_query.placeholder_text = "player name, actual name or email (empty: everyone)"
	_query.custom_minimum_size = Vector2(420, 34)
	_query.text_submitted.connect(func(_t): _search())
	find.add_child(_query)
	var go := Button.new()
	go.text = "Search"
	HudStyle.secondary(go)
	go.pressed.connect(_search)
	find.add_child(go)
	var scroll := ScrollContainer.new()
	scroll.custom_minimum_size = Vector2(0, 300)
	scroll.horizontal_scroll_mode = ScrollContainer.SCROLL_MODE_DISABLED
	v.add_child(scroll)
	_results = VBoxContainer.new()
	_results.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	_results.add_theme_constant_override("separation", 4)
	scroll.add_child(_results)
	_problem = MenuScreen.problem_label()
	v.add_child(_problem)
	var back := Button.new()
	back.text = "Back"
	HudStyle.secondary(back)
	back.custom_minimum_size = Vector2(140, 40)
	back.size_flags_horizontal = Control.SIZE_SHRINK_BEGIN
	back.pressed.connect(func(): back_pressed.emit())
	v.add_child(back)


func open() -> void:
	_problem.visible = false
	visible = true
	request.emit({"type": "admin_overview"})
	_search()


func close() -> void:
	visible = false


func show_problem(message: String) -> void:
	_problem.text = message
	_problem.visible = true


func _search() -> void:
	request.emit({"type": "admin_find", "query": _query.text.strip_edges()})


## The server's "admin_overview".
func set_overview(msg: Dictionary) -> void:
	_problem.visible = false
	for c in _stats.get_children():
		c.queue_free()
	var s: Dictionary = msg.get("stats", {})
	for pair in [["Players", "%d of %d" % [int(s.get("players", 0)), int(s.get("max_users", 0))]],
			["Games played", str(int(s.get("games_played", 0)))], ["Games completed", str(int(s.get("games_completed", 0)))],
			["Live games", str(int(s.get("games_live", 0)))], ["Server", str(msg.get("build", {}).get("label", ""))]]:
		_stats.add_child(HudStyle.label(pair[0], 14, HudStyle.TEXT_DIM))
		_stats.add_child(HudStyle.label(pair[1], 15))
	_max.set_value_no_signal(int(s.get("max_users", 100)))


## The server's "admin_users": the lookup's results.
func set_users(users: Array) -> void:
	for c in _results.get_children():
		c.queue_free()
	if users.is_empty():
		_results.add_child(HudStyle.label("Nobody matches.", 13, HudStyle.TEXT_DIM))
	for u in users:
		_results.add_child(_row(u))


func _row(u: Dictionary) -> Control:
	var box := PanelContainer.new()
	box.add_theme_stylebox_override("panel", GCTheme.box(GCTheme.WHITE, GCTheme.NAVY, 2, -1, Vector4(10, 6, 10, 6)))
	var h := HBoxContainer.new()
	h.add_theme_constant_override("separation", 14)
	box.add_child(h)
	var who := VBoxContainer.new()
	who.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	var title := str(u.get("player_name", "")) + ("  (admin)" if bool(u.get("admin", false)) else "")
	who.add_child(HudStyle.label(title, 15, GCTheme.RED if bool(u.get("locked", false)) else HudStyle.TEXT))
	var actual := str(u.get("actual_name", ""))
	who.add_child(HudStyle.label("%s%s" % [str(u.get("email", "")), ("  -  " + actual) if actual != "" else ""], 12, HudStyle.TEXT_DIM))
	h.add_child(who)
	var g: Dictionary = u.get("games", {})
	var info := VBoxContainer.new()
	info.custom_minimum_size = Vector2(250, 0)
	info.add_child(HudStyle.label("Games: %d live, %d finished, %d forming" % [int(g.get("live", 0)), int(g.get("finished", 0)),
		int(g.get("forming", 0))], 12))
	var last = u.get("last_login")
	info.add_child(HudStyle.label("Joined %s  -  last login %s" % [str(u.get("created", "")).left(10),
		str(last).left(10) if last != null else "never"], 12, HudStyle.TEXT_DIM))
	h.add_child(info)
	var locked := bool(u.get("locked", false))
	var lock := Button.new()
	lock.text = "Unlock" if locked else "Lock"
	if locked:
		HudStyle.secondary(lock)
	else:
		HudStyle.primary(lock)
	lock.custom_minimum_size = Vector2(100, 32)
	lock.disabled = str(u.get("id", "")) == str(Account.user.get("id", ""))
	lock.tooltip_text = "A locked player can't log in; they're logged out now, and their seats in live games go to bots." if not locked else ""
	lock.pressed.connect(func(): request.emit({"type": "admin_lock", "user_id": u["id"], "locked": not locked}))
	h.add_child(lock)
	return box

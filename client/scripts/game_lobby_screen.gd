class_name GameLobbyScreen
extends MenuScreen
## The New Game Lobby (reference/GC Launcher_User Profile.odt): a forming game's type and all its settings
## (not editable), its seats -- players take their own, as many as they like; a Human seat's faction is
## shown when the settings name it -- its invitation code, and its chat. The host launches once every
## Human seat is taken, or cancels.

signal back_pressed
signal request(msg: Dictionary)   # a message for the server (take_seat, leave_seat, launch, cancel, chat)

const MODE_NAMES := {"HUMAN": "Human", "BOT": "Bot", "NEUTRAL": "Neutral", "NONCOMBATANT": "Noncombatant",
	"NOT_PLAYING": "Not playing"}

var game := {}  # the lobby as the server last described it (server/hub.py's _lobby_view)
var _title: Label
var _code: Label
var _host: Label
var _seats: GridContainer
var _rules: RichTextLabel
var _status: Label
var _problem: Label
var _launch: HoldButton
var _cancel: HoldButton
var chat: ChatPanel


func _build() -> void:
	var center := CenterContainer.new()
	add_child(center)
	center.set_anchors_and_offsets_preset(Control.PRESET_FULL_RECT)
	var panel := MenuScreen.sheet(1120, 10)
	center.add_child(panel)
	var v := MenuScreen.body(panel)
	var head := HBoxContainer.new()
	head.add_theme_constant_override("separation", 16)
	v.add_child(head)
	_title = HudStyle.label("", 24, HudStyle.GOLD)
	_title.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	head.add_child(_title)
	var code_box := VBoxContainer.new()
	code_box.add_theme_constant_override("separation", 0)
	code_box.add_child(HudStyle.label("Invitation code", 12, HudStyle.TEXT_DIM))
	_code = HudStyle.label("", 26, GCTheme.RED)
	_code.add_theme_font_override("font", GCTheme.font("display_black"))
	code_box.add_child(_code)
	head.add_child(code_box)
	var rule := HSeparator.new()
	rule.theme_type_variation = "RedRule"
	v.add_child(rule)
	_host = HudStyle.label("", 13, HudStyle.TEXT_DIM)
	v.add_child(_host)

	var cols := HBoxContainer.new()
	cols.add_theme_constant_override("separation", 18)
	v.add_child(cols)
	var left := VBoxContainer.new()
	left.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	left.add_theme_constant_override("separation", 8)
	cols.add_child(left)
	left.add_child(HudStyle.heading("Seats", 15))
	_seats = GridContainer.new()
	_seats.columns = 5
	_seats.add_theme_constant_override("h_separation", 14)
	_seats.add_theme_constant_override("v_separation", 6)
	left.add_child(_seats)
	left.add_child(HudStyle.heading("Settings", 15))
	_rules = RichTextLabel.new()
	_rules.bbcode_enabled = true
	_rules.fit_content = true
	_rules.custom_minimum_size = Vector2(640, 0)
	_rules.add_theme_font_size_override("normal_font_size", 13)
	_rules.add_theme_font_size_override("bold_font_size", 13)
	_rules.add_theme_color_override("default_color", GCTheme.NAVY)
	left.add_child(_rules)
	chat = ChatPanel.new(330)
	chat.custom_minimum_size = Vector2(380, 0)
	chat.send.connect(func(t): request.emit({"type": "chat", "room": str(game.get("id", "")), "text": t}))
	cols.add_child(chat)

	_status = HudStyle.label("", 14, HudStyle.TEXT)
	v.add_child(_status)
	_problem = MenuScreen.problem_label()
	v.add_child(_problem)
	var buttons := HBoxContainer.new()
	buttons.add_theme_constant_override("separation", 14)
	v.add_child(buttons)
	var back := Button.new()
	back.text = "Back"
	HudStyle.secondary(back)
	back.custom_minimum_size = Vector2(140, 44)
	back.pressed.connect(func():
		request.emit({"type": "leave_lobby"})
		back_pressed.emit())
	buttons.add_child(back)
	var gap := Control.new()
	gap.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	buttons.add_child(gap)
	_cancel = _hold("Cancel Game")
	_cancel.activated.connect(func(): request.emit({"type": "cancel"}))
	buttons.add_child(_cancel)
	_launch = _hold("Launch Game")
	HudStyle.primary(_launch)
	_launch.activated.connect(func(): request.emit({"type": "launch"}))
	buttons.add_child(_launch)


func _hold(text: String) -> HoldButton:
	var b := HoldButton.new()
	b.text = text
	b.hold_seconds = 1.0
	b.space_key = false
	b.custom_minimum_size = Vector2(200, 44)
	HudStyle.secondary(b)
	return b


func close() -> void:
	visible = false


func show_problem(message: String) -> void:
	_problem.text = message
	_problem.visible = true


## The server's "game_lobby": the lobby as it stands (sent again whenever it changes).
func show_game(msg: Dictionary) -> void:
	var first: bool = not visible or str(msg.get("game", {}).get("id", "")) != str(game.get("id", ""))
	game = msg.get("game", {})
	if first or msg.has("chat"):
		chat.set_messages(msg.get("chat", []))
	_problem.visible = false
	_title.text = MyGamesScreen.scenario_name(game.get("scenario", {})).to_upper()
	_code.text = str(game.get("code", ""))
	var me := str(Account.user.get("id", ""))
	var hosting := str(game.get("host_id", "")) == me
	_host.text = "Hosted by %s%s.  Share the code so others can join." % [str(game.get("host_name", "")), " (you)" if hosting else ""]
	_fill_seats(me)
	_rules.text = _settings_text(game.get("settings", {}))
	var open := int(game.get("open_seats", 0))
	_status.text = "Every human seat is taken: the host can launch." if open == 0 else \
		"Waiting for %d more player(s) to take a human seat." % open
	_launch.visible = hosting
	_cancel.visible = hosting
	_launch.disabled = open > 0
	visible = true


func add_chat(m: Dictionary) -> void:
	chat.add_message(m)


func _fill_seats(me: String) -> void:
	for c in _seats.get_children():
		c.queue_free()
	for h in ["Seat", "Type", "Faction", "Player", ""]:
		_seats.add_child(HudStyle.label(h, 12, HudStyle.TEXT_DIM))
	for s in game.get("seats", []):
		var mode := str(s.get("mode", ""))
		_seats.add_child(HudStyle.label(str(int(s.get("seat", 0))), 16, HudStyle.GOLD))
		_seats.add_child(HudStyle.label(MODE_NAMES.get(mode, mode), 14))
		var faction := str(s.get("faction", "random"))
		var fbox := HBoxContainer.new()
		fbox.add_theme_constant_override("separation", 4)
		if faction != "random" and GameData.factions.has(faction):
			fbox.add_child(FactionIcons.make(faction, 20, true))
			fbox.add_child(HudStyle.label(faction, 14))
		else:
			fbox.add_child(HudStyle.label("Random", 14, HudStyle.TEXT_DIM))
		_seats.add_child(fbox)
		var holder = s.get("user_id")
		var who := ""
		if mode == "HUMAN":
			who = str(s.get("player_name")) if holder != null else "open"
		_seats.add_child(HudStyle.label(who, 14, HudStyle.TEXT if holder != null else HudStyle.TEXT_DIM))
		if mode != "HUMAN":
			_seats.add_child(Control.new())
		elif holder == null:
			_seats.add_child(_seat_button("Take Seat", "take_seat", int(s["seat"])))
		elif str(holder) == me:
			_seats.add_child(_seat_button("Leave Seat", "leave_seat", int(s["seat"])))
		else:
			_seats.add_child(Control.new())


func _seat_button(text: String, kind: String, seat: int) -> Button:
	var b := Button.new()
	b.text = text
	if kind == "take_seat":
		HudStyle.primary(b)
	else:
		HudStyle.secondary(b)
	b.custom_minimum_size = Vector2(120, 30)
	b.pressed.connect(func(): request.emit({"type": kind, "seat": seat}))
	return b


## The game's settings in words (nothing here can be changed).
static func _settings_text(s: Dictionary) -> String:
	var lines := []
	var sc: Dictionary = s.get("scenario", {}) if s.get("scenario") is Dictionary else {}
	if str(sc.get("kind", "fixed")) == "new":
		lines.append("[b]A new scenario[/b], dealt afresh when the game starts.")
	var bots := []
	for seat in s.get("seats", []):
		if str(seat.get("mode", "")) == "BOT":
			bots.append("%s (%s, %s)" % [str(seat.get("faction", "random")).capitalize() if seat.get("faction", "random") == "random" else seat["faction"],
				str(seat.get("strategy", "random")), str(seat.get("behavior", "random"))])
	if not bots.is_empty():
		lines.append("[b]Bots:[/b] %s" % ", ".join(bots))
	var size := int(s.get("max_alliance_size", 3))
	lines.append("[b]Alliances:[/b] %s%s%s" % ["none" if size <= 1 else "up to %d members" % size,
		"" if size <= 1 else ("; may withdraw" if bool(s.get("can_withdraw", true)) else "; can't withdraw"),
		"" if size <= 1 or not bool(s.get("can_withdraw", true)) else ("; may rejoin" if bool(s.get("can_rejoin", false)) else "; can't rejoin")])
	lines.append("[b]Turn order:[/b] %s" % ("random" if bool(s.get("randomize_order", true)) else "in seat order"))
	lines.append("[b]First turn:[/b] combat moves %s, non-combat moves %s" % [
		"allowed" if bool(s.get("allow_combat_first_turn", false)) else "not allowed",
		"allowed" if bool(s.get("allow_noncombat_first_turn", true)) else "not allowed"])
	var rounds := int(s.get("armistice_rounds", 10))
	lines.append("[b]Armistice:[/b] %s" % ("never proposed by the game" if rounds == 0 else "proposed after %d rounds" % rounds))
	var hours := int(s.get("turn_hours", 0))
	lines.append("[b]Time for a turn:[/b] %s" % ("no limit" if hours == 0 else "%d hour%s, then a bot may take the seat" % [hours, "" if hours == 1 else "s"]))
	var opts: Dictionary = sc.get("options", {}) if sc.get("options") is Dictionary else {}
	if not opts.is_empty():
		var bits := []
		for k in opts:
			bits.append("%s %s" % [str(k).replace("_", " "), str(opts[k])])
		lines.append("[b]Scenario options:[/b] %s" % ", ".join(bits))
	return "\n".join(lines)

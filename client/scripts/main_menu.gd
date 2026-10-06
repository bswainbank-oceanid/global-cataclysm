class_name MainMenu
extends MenuScreen
## The main screen once logged in (reference/GC Launcher_User Profile.odt): the poster, the menu
## (Resume Game, Join Game, New Game, Scenarios for admins, History, Rules, Quit to Desktop), the player's profile in
## the upper right (names, Log Out), Settings (the in-game panel's options: playback, display, sound, the log), and
## the client's and server's build numbers in the lower right. Resume Game pulses while one of the player's
## games is waiting on them (their turn, or an answer): the menu asks for My Games when it opens and every
## POLL_SECONDS while it shows.

signal resume_pressed
signal join_pressed
signal new_game_pressed
signal scenarios_pressed
signal history_pressed
signal rules_pressed
signal admin_pressed

const POLL_SECONDS := 15.0

var _resume: Button
var _pulse: Tween
var _poll: Timer
var _scenarios: Button
var _admin: Button
var _profile_button: Button
var _profile_panel: PanelContainer
var _display_panel: PanelContainer
var _player_name: Label
var _actual_name: Label
var _email: Label
var _note: Label
var _builds: Label  # the client's and server's build numbers, bottom right


func _art_dim() -> float:
	return 0.15


func _build() -> void:
	# the menu: a paper column at the left, over the poster's tank
	var column := MarginContainer.new()
	column.add_theme_constant_override("margin_left", 48)
	column.add_theme_constant_override("margin_top", 60)
	add_child(column)
	column.set_anchors_and_offsets_preset(Control.PRESET_LEFT_WIDE)
	var holder := VBoxContainer.new()
	holder.alignment = BoxContainer.ALIGNMENT_CENTER
	column.add_child(holder)
	var panel := MenuScreen.sheet(300, 10)
	holder.add_child(panel)
	var v := MenuScreen.body(panel)
	v.add_child(HudStyle.heading("Command", 18, true))
	var buttons := [
		["Resume Game", resume_pressed, true], ["Join Game", join_pressed, false],
		["New Game", new_game_pressed, false], ["Scenarios", scenarios_pressed, false], ["Admin", admin_pressed, false],
		["History", history_pressed, false], ["Rules", rules_pressed, false]]
	for entry in buttons:
		var b := Button.new()
		b.text = entry[0]
		b.custom_minimum_size = Vector2(0, 46)
		if entry[2]:
			HudStyle.primary(b)
		else:
			HudStyle.secondary(b)
		var sig = entry[1]
		if sig == null:
			var what: String = entry[0]
			b.pressed.connect(func(): _say("%s is coming soon." % what))
		else:
			b.pressed.connect(func(): (sig as Signal).emit())
		v.add_child(b)
		if entry[0] == "Resume Game":
			_resume = b
		if entry[0] == "Scenarios":
			_scenarios = b
			b.tooltip_text = "Edit the shared scenarios (admins)"
		if entry[0] == "Admin":
			_admin = b
			b.tooltip_text = "The server's numbers, the most players, finding and locking players (admins)"
	_note = HudStyle.label("", 13, HudStyle.TEXT_DIM)
	_note.autowrap_mode = TextServer.AUTOWRAP_WORD_SMART
	v.add_child(_note)
	v.add_child(HSeparator.new())
	var quit := Button.new()
	quit.text = "Quit to Desktop"
	HudStyle.secondary(quit)
	quit.custom_minimum_size = Vector2(0, 46)
	quit.pressed.connect(func(): get_tree().quit())  # (the games carry on on the server)
	v.add_child(quit)

	# the upper right: Display Settings and the profile
	var corner := HBoxContainer.new()
	corner.add_theme_constant_override("separation", 8)
	add_child(corner)
	corner.set_anchors_and_offsets_preset(Control.PRESET_TOP_RIGHT)
	corner.grow_horizontal = Control.GROW_DIRECTION_BEGIN
	corner.position += Vector2(-20, 20)
	var settings_button := Button.new()
	settings_button.text = "Settings"
	HudStyle.secondary(settings_button)
	settings_button.pressed.connect(func(): _toggle(_display_panel))
	corner.add_child(settings_button)
	_profile_button = HudStyle.primary(Button.new())
	_profile_button.custom_minimum_size = Vector2(160, 0)
	_profile_button.pressed.connect(func(): _toggle(_profile_panel))
	corner.add_child(_profile_button)

	_profile_panel = _drop_panel()
	var p := MenuScreen.body(_profile_panel)
	p.add_child(HudStyle.heading("Profile", 15))
	_player_name = HudStyle.label("", 18)
	p.add_child(_player_name)
	_actual_name = HudStyle.label("", 13, HudStyle.TEXT_DIM)
	p.add_child(_actual_name)
	_email = HudStyle.label("", 13, HudStyle.TEXT_DIM)
	p.add_child(_email)
	var logout := Button.new()
	logout.text = "Log Out"
	HudStyle.secondary(logout)
	logout.pressed.connect(func():
		_profile_panel.visible = false
		Account.logout())
	p.add_child(logout)

	# Settings: the in-game panel's playback, display, sound and log options (not its game actions)
	var settings := SettingsPanel.new()
	settings.menu = true
	settings.visible = false
	add_child(settings)
	settings.set_anchors_and_offsets_preset(Control.PRESET_TOP_RIGHT)
	settings.grow_horizontal = Control.GROW_DIRECTION_BEGIN
	settings.position += Vector2(-20, 70)
	_display_panel = settings

	_builds = HudStyle.label("", 12, GCTheme.CREAM_DIM)
	_builds.mouse_filter = Control.MOUSE_FILTER_IGNORE
	add_child(_builds)
	_builds.set_anchors_and_offsets_preset(Control.PRESET_BOTTOM_RIGHT)
	_builds.grow_horizontal = Control.GROW_DIRECTION_BEGIN
	_builds.grow_vertical = Control.GROW_DIRECTION_BEGIN
	_builds.horizontal_alignment = HORIZONTAL_ALIGNMENT_RIGHT
	_builds.position += Vector2(-16, -12)
	_builds.add_theme_color_override("font_outline_color", GCTheme.NAVY_DARK)
	_builds.add_theme_constant_override("outline_size", 4)

	_poll = Timer.new()
	_poll.wait_time = POLL_SECONDS
	_poll.timeout.connect(_ask_games)
	add_child(_poll)


func open() -> void:
	refresh()
	_profile_panel.visible = false
	_display_panel.visible = false
	_note.text = ""
	visible = true
	_ask_games()
	_poll.start()


func close() -> void:
	visible = false
	_poll.stop()


func _ask_games() -> void:
	if Account.is_logged_in():
		Net.send_msg({"type": "my_games"})


## The server's "my_games": Resume Game pulses when any of them is waiting on this player.
func set_games(games: Array) -> void:
	var me := str(Account.user.get("id", ""))
	var waiting := false
	for g in games:
		var progress = g.get("progress")
		if g.get("status") == "live" and progress is Dictionary and (progress.get("waiting_for", []) as Array).has(me):
			waiting = true
	_set_pulse(waiting)


func _set_pulse(on: bool) -> void:
	if on == (_pulse != null):
		return
	if _pulse != null:
		_pulse.kill()
		_pulse = null
		_resume.modulate = Color.WHITE
		_resume.tooltip_text = ""
		return
	_resume.tooltip_text = "A game is waiting for you: your turn, or an answer"
	_pulse = create_tween().set_loops()
	_pulse.tween_property(_resume, "modulate", Color(1.45, 1.45, 1.45), 0.7).set_trans(Tween.TRANS_SINE)
	_pulse.tween_property(_resume, "modulate", Color.WHITE, 0.7).set_trans(Tween.TRANS_SINE)


## The logged-in player's names, and the admin-only Scenarios button.
func refresh() -> void:
	var u: Dictionary = Account.user
	_profile_button.text = str(u.get("player_name", "")).to_upper()
	_player_name.text = str(u.get("player_name", ""))
	var actual := str(u.get("actual_name", ""))
	_actual_name.text = actual if actual != "" else "(no actual name given)"
	_email.text = str(u.get("email", ""))
	_scenarios.visible = Account.is_admin()
	_admin.visible = Account.is_admin()
	var client := str(Account.client_build().get("label", "Build unknown"))
	var server := str(Account.server_build.get("label", "Build unknown"))
	var lines := ["Client " + client, "Server " + server]
	if Account.builds_differ():
		lines.append("The client and server builds differ: restart the game to update")
	_builds.text = "
".join(lines)
	_builds.add_theme_color_override("font_color", GCTheme.RED_LIGHT if Account.builds_differ() else GCTheme.CREAM)


func _say(text: String) -> void:
	_note.text = text


func _drop_panel() -> PanelContainer:
	var panel := MenuScreen.sheet(280, 8)
	panel.visible = false
	add_child(panel)
	panel.set_anchors_and_offsets_preset(Control.PRESET_TOP_RIGHT)
	panel.grow_horizontal = Control.GROW_DIRECTION_BEGIN
	panel.position += Vector2(-20, 70)
	return panel


func _toggle(panel: PanelContainer) -> void:
	var show := not panel.visible
	_profile_panel.visible = false
	_display_panel.visible = false
	panel.visible = show

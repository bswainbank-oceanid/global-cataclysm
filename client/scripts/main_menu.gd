class_name MainMenu
extends MenuScreen
## The main screen once logged in (reference/GC Launcher_User Profile.odt): the poster, the menu
## (Resume Game, Join Game, New Game, Scenarios for admins, History, Rules), the player's profile in
## the upper right (names, Log Out) and Display Settings (full screen).

signal resume_pressed
signal join_pressed
signal new_game_pressed
signal scenarios_pressed

var _scenarios: Button
var _profile_button: Button
var _profile_panel: PanelContainer
var _display_panel: PanelContainer
var _player_name: Label
var _actual_name: Label
var _email: Label
var _note: Label
var _fullscreen: CheckBox


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
		["New Game", new_game_pressed, false], ["Scenarios", scenarios_pressed, false],
		["History", null, false], ["Rules", null, false]]
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
		if entry[0] == "Scenarios":
			_scenarios = b
			b.tooltip_text = "Edit the shared scenarios (admins)"
	_note = HudStyle.label("", 13, HudStyle.TEXT_DIM)
	_note.autowrap_mode = TextServer.AUTOWRAP_WORD_SMART
	v.add_child(_note)

	# the upper right: Display Settings and the profile
	var corner := HBoxContainer.new()
	corner.add_theme_constant_override("separation", 8)
	add_child(corner)
	corner.set_anchors_and_offsets_preset(Control.PRESET_TOP_RIGHT)
	corner.grow_horizontal = Control.GROW_DIRECTION_BEGIN
	corner.position += Vector2(-20, 20)
	var display := Button.new()
	display.text = "Display"
	HudStyle.secondary(display)
	display.pressed.connect(func(): _toggle(_display_panel))
	corner.add_child(display)
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

	_display_panel = _drop_panel()
	var d := MenuScreen.body(_display_panel)
	d.add_child(HudStyle.heading("Display Settings", 15))
	_fullscreen = CheckBox.new()
	_fullscreen.text = "Full screen"
	_fullscreen.button_pressed = Settings.fullscreen
	_fullscreen.toggled.connect(func(on: bool):
		Settings.fullscreen = on
		Settings.commit())
	d.add_child(_fullscreen)


func open() -> void:
	refresh()
	_profile_panel.visible = false
	_display_panel.visible = false
	_note.text = ""
	visible = true


func close() -> void:
	visible = false


## The logged-in player's names, and the admin-only Scenarios button.
func refresh() -> void:
	var u: Dictionary = Account.user
	_profile_button.text = str(u.get("player_name", "")).to_upper()
	_player_name.text = str(u.get("player_name", ""))
	var actual := str(u.get("actual_name", ""))
	_actual_name.text = actual if actual != "" else "(no actual name given)"
	_email.text = str(u.get("email", ""))
	_scenarios.visible = Account.is_admin()
	_fullscreen.set_pressed_no_signal(Settings.fullscreen)


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

class_name SettingsPanel
extends PanelContainer
## The playback options popup (opened from the top bar's Settings button).
## Options that only matter when a human plays are greyed out while every
## faction in the game is a bot.

var _opp: OptionButton
var _opp_battle: CheckBox
var _opp_battle_mine: CheckBox
var _your_battle: CheckBox
var _note: Label


func _ready() -> void:
	custom_minimum_size = Vector2(320, 0)
	add_theme_stylebox_override("panel", HudStyle.box(HudStyle.GOLD, Color(0.07, 0.085, 0.11), 2))
	var v := VBoxContainer.new()
	v.add_theme_constant_override("separation", 6)
	add_child(v)

	v.add_child(HudStyle.label("Playback", 15, HudStyle.GOLD))
	v.add_child(HudStyle.label("When to wait for the Next button", 12, HudStyle.TEXT_DIM))
	v.add_child(HSeparator.new())

	v.add_child(HudStyle.label("Opponents' turn pausing:", 13))
	_opp = OptionButton.new()
	_opp.add_item("Never", Settings.OppPause.NEVER)
	_opp.add_item("Turn", Settings.OppPause.TURN)
	_opp.add_item("Phase", Settings.OppPause.PHASE)
	_opp.item_selected.connect(func(idx: int):
		Settings.opp_pause = _opp.get_item_id(idx)
		Settings.commit())
	v.add_child(_opp)
	_opp_battle = _check(v, "Pause for each battle", func(on): Settings.opp_pause_battle = on)
	_opp_battle_mine = _check(v, "Pause for each battle involving your units", func(on): Settings.opp_pause_battle_mine = on)

	v.add_child(HSeparator.new())
	v.add_child(HudStyle.label("Your turn:", 13))
	_your_battle = _check(v, "Pause for each battle", func(on): Settings.your_pause_battle = on)

	_note = HudStyle.label("No human player in this game: you're watching bots, so the options for your own units and turns do nothing.", 11, HudStyle.TEXT_DIM)
	_note.autowrap_mode = TextServer.AUTOWRAP_WORD_SMART
	v.add_child(_note)

	GameStore.state_changed.connect(_sync)
	_sync()


func _check(parent: Control, text: String, setter: Callable) -> CheckBox:
	var c := CheckBox.new()
	c.text = text
	c.focus_mode = Control.FOCUS_NONE
	for icon_colour in ["icon_normal_color", "icon_pressed_color", "icon_hover_color", "icon_hover_pressed_color", "icon_focus_color"]:
		c.add_theme_color_override(icon_colour, Color(0.95, 0.97, 1.0))  # the default tint is near-invisible on this dark panel
	c.toggled.connect(func(on: bool):
		setter.call(on)
		Settings.commit())
	parent.add_child(c)
	return c


func _sync() -> void:
	_opp.select(_opp.get_item_index(Settings.opp_pause))
	_opp_battle.set_pressed_no_signal(Settings.opp_pause_battle)
	_opp_battle_mine.set_pressed_no_signal(Settings.opp_pause_battle_mine)
	_your_battle.set_pressed_no_signal(Settings.your_pause_battle)
	var has_player := GameStore.has_player()
	_opp_battle_mine.disabled = not has_player
	_your_battle.disabled = not has_player
	_note.visible = not has_player

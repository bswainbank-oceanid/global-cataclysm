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
var _surrender_btn: HoldButton
var _armistice_btn: HoldButton
signal new_game_pressed

# Much longer than an ordinary Diplomacy hold (orders_panel.gd's 0.8s): these are
# irreversible and can end the game, so a stray click must never fire them.
const HOLD_SECONDS := 3.0


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

	v.add_child(HSeparator.new())
	v.add_child(HudStyle.label("Game", 13))
	v.add_child(HudStyle.label("Hold the whole way to confirm -- these can't be undone.", 11, HudStyle.TEXT_DIM))
	_surrender_btn = _hold_button(v, "Hold to surrender", Color(1.0, 0.55, 0.45))
	_surrender_btn.activated.connect(func(): Stepper.surrender())
	_armistice_btn = _hold_button(v, "Hold to propose armistice", HudStyle.GOLD)
	_armistice_btn.activated.connect(func(): Stepper.propose_armistice())

	v.add_child(HSeparator.new())
	var new_game := Button.new()
	new_game.text = "New game..."
	new_game.focus_mode = Control.FOCUS_NONE
	new_game.tooltip_text = "Back to the launch screen (the running game stays until you start another)"
	new_game.pressed.connect(func(): new_game_pressed.emit())
	v.add_child(new_game)

	GameStore.state_changed.connect(_sync)
	GameStore.armistice_changed.connect(_sync)
	_sync()


func _hold_button(parent: Control, text: String, colour: Color) -> HoldButton:
	var b := HoldButton.new()
	b.text = text
	b.hold_seconds = HOLD_SECONDS
	b.focus_mode = Control.FOCUS_NONE
	b.custom_minimum_size = Vector2(0, 36)
	b.add_theme_font_size_override("font_size", 13)
	b.add_theme_color_override("font_color", colour)
	b.add_theme_color_override("font_hover_color", Color.WHITE)
	b.add_theme_color_override("font_disabled_color", HudStyle.TEXT_DIM)
	b.add_theme_stylebox_override("normal", HudStyle.box(colour.darkened(0.3), Color(0.12, 0.14, 0.18), 2))
	b.add_theme_stylebox_override("hover", HudStyle.box(Color.WHITE, Color(0.2, 0.22, 0.27), 2))
	b.add_theme_stylebox_override("pressed", HudStyle.box(colour, Color(0.24, 0.26, 0.3), 2))
	b.add_theme_stylebox_override("disabled", HudStyle.box(HudStyle.EDGE, HudStyle.BG, 1))
	parent.add_child(b)
	return b


func _check(parent: Control, text: String, setter: Callable) -> CheckBox:
	var c := CheckBox.new()
	c.text = text
	c.focus_mode = Control.FOCUS_NONE
	HudStyle.style_checkbox(c)
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

	var me := GameStore.human_faction()
	var has_game: bool = not GameStore.state.is_empty()
	var over: bool = bool(GameStore.state.get("game_over", false))
	var eliminated: bool = me != "" and bool(GameStore.faction_state(me).get("eliminated", false))
	var proposal_in_flight: bool = not GameStore.armistice.is_empty()
	# Surrender: nothing left to give up once you're already out, the game's already over, or (a pure
	# spectator watching a bot-vs-bot game) there was never anything of your own to give up in the first place.
	_surrender_btn.disabled = me == "" or eliminated or over
	_surrender_btn.tooltip_text = "Hold for %ds to give up and leave the game at once." % int(HOLD_SECONDS)
	# Propose Armistice: stays available even after elimination (an eliminated human may still propose
	# one) and to a pure SPECTATOR with no faction of their own at all (me == "") -- only a live game
	# actually being in progress, the game being over, another proposal already in flight, or (server/
	# session.py's ARMISTICE_COOLDOWN_ROUNDS) still cooling down from having proposed one that was
	# declined, blocks it.
	var cooldown := GameStore.armistice_cooldown_remaining()
	_armistice_btn.disabled = not has_game or over or proposal_in_flight or cooldown > 0
	if cooldown > 0:
		_armistice_btn.tooltip_text = "You must wait %d more round(s) to propose an armistice again -- your last proposal was declined." % cooldown
	else:
		_armistice_btn.tooltip_text = "Hold for %ds to propose ending the game right here. Bots always accept; any human player is asked." % int(HOLD_SECONDS)

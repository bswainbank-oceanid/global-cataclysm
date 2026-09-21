class_name OrdersPanel
extends PanelContainer
## The middle panel of the right column: where the player's orders for the
## current phase are composed and submitted. During the human's Purchase phase
## it shows the selected space as a purchase site (a land territory the player
## controls, or a sea zone next to one), a row per unit type with - / + buttons
## and its price, and the MCP budget; the submit button (a HoldButton: hold to
## confirm) is always at the bottom. At any other time it is just the button.

signal add_requested(unit_type: String, tid: int)
signal remove_requested(unit_type: String, tid: int)

const UNIT_ORDER := ["Infantry", "Mechanized Infantry", "Armor", "Fighter", "Bomber", "Submarine", "Cruiser", "Aircraft Carrier"]

var button: HoldButton
var _content: VBoxContainer
var _target := -1


func _ready() -> void:
	add_theme_stylebox_override("panel", HudStyle.box())
	var v := VBoxContainer.new()
	v.add_theme_constant_override("separation", 4)
	add_child(v)
	_content = VBoxContainer.new()
	_content.add_theme_constant_override("separation", 1)
	v.add_child(_content)

	button = HoldButton.new()
	button.custom_minimum_size = Vector2(0, 46)
	button.focus_mode = Control.FOCUS_NONE
	button.add_theme_font_size_override("font_size", 15)
	button.add_theme_color_override("font_color", HudStyle.GOLD)
	button.add_theme_color_override("font_hover_color", Color.WHITE)
	button.add_theme_color_override("font_disabled_color", HudStyle.TEXT_DIM)
	button.add_theme_stylebox_override("normal", HudStyle.box(HudStyle.GOLD, Color(0.16, 0.14, 0.05), 2))
	button.add_theme_stylebox_override("hover", HudStyle.box(Color.WHITE, Color(0.24, 0.2, 0.06), 2))
	button.add_theme_stylebox_override("pressed", HudStyle.box(HudStyle.GOLD, Color(0.3, 0.25, 0.08), 2))
	button.add_theme_stylebox_override("disabled", HudStyle.box(HudStyle.EDGE, HudStyle.BG, 1))
	v.add_child(button)

	GameStore.purchase_changed.connect(_rebuild)
	GameStore.move_changed.connect(_rebuild)
	GameStore.alliance_changed.connect(_rebuild)
	GameStore.state_changed.connect(_rebuild)
	_rebuild()


## The space currently selected on the map (-1 = none).
func set_target(tid: int) -> void:
	_target = tid
	_rebuild()


func _rebuild() -> void:
	for c in _content.get_children():
		_content.remove_child(c)
		c.queue_free()
	if GameStore.human_alliance_active():
		_content.visible = true
		_add_alliance_options()
		return
	if GameStore.human_move_active():
		_content.visible = true
		_add_move_summary()
		return
	if not GameStore.human_purchase_active():
		_content.visible = false
		return
	_content.visible = true
	_content.add_child(HudStyle.label("Purchase", 14, HudStyle.GOLD))
	var info: Dictionary = GameStore.purchase_target(_target)
	if _target < 0 or info.is_empty():
		var hint := HudStyle.label("Select a territory you control, or a sea zone next to one, to buy units there.", 12, HudStyle.TEXT_DIM)
		hint.autowrap_mode = TextServer.AUTOWRAP_WORD_SMART
		_content.add_child(hint)
	else:
		_add_site(info)
	_add_budget()


const HOLD_SECONDS := 0.8   # a Diplomacy action is a long-click: hold the button until its ring is full

## Diplomacy: everything is a long-click action, carried out at once (the outcome goes in the Events
## box). One alliance action a turn -- invite a faction OR withdraw from the alliance you are in --
## and any number of surrender demands, before or after it. Next ends the phase.
func _add_alliance_options() -> void:
	var ha: Dictionary = GameStore.human_alliance
	_content.add_child(HudStyle.label("Diplomacy", 14, HudStyle.GOLD))
	var members: Array = ha["members"]
	var me := str(ha["faction"])
	var status: String
	if members.size() > 1:
		var others := []
		for m in members:
			if str(m) != me:
				others.append(str(m))
		status = "You are allied with: %s." % ", ".join(others)
	else:
		status = "You are not in an alliance."
	var rules := "Leaving alliances is %s; rejoining one you left is %s." % [
		"allowed" if GameStore.can_withdraw_from_alliances() else "off", "allowed" if GameStore.can_rejoin_alliances() else "off"]
	var status_l := HudStyle.label(status + "  Hold a button to act. One alliance action a turn: invite OR withdraw. " + rules, 11, HudStyle.TEXT_DIM)
	status_l.autowrap_mode = TextServer.AUTOWRAP_WORD_SMART
	_content.add_child(status_l)
	if ha["game_would_end"]:
		var warn := HudStyle.label("Every remaining faction is allied, or you are the only one left: the game ends at the end of this phase.", 11, Color(1.0, 0.6, 0.45))
		warn.autowrap_mode = TextServer.AUTOWRAP_WORD_SMART
		_content.add_child(warn)

	_content.add_child(HudStyle.label("Alliance", 12, HudStyle.TEXT))
	var used: bool = bool(ha["options"].get("alliance_action_used", false))
	if used:
		var done := HudStyle.label("You have used this turn's alliance action.", 11, HudStyle.TEXT_DIM)
		_content.add_child(done)
	if members.size() > 1:
		var can: bool = ha["options"]["can_withdraw"] and not used
		var why := "Hold to leave the alliance."
		if not GameStore.can_withdraw_from_alliances():
			why = "Withdrawing from alliances is turned off in this game."
		elif not ha["options"]["can_withdraw"]:
			why = "Not allowed now: a unit of yours is standing on an ally's Strategic Center."
		var text := "Withdraw from the alliance" if ha["options"]["can_withdraw"] else "Withdraw from the alliance  (not allowed)"
		_content.add_child(_choice(text, "", false, can, why, func(): Stepper.diplomacy_action("withdraw"), true))
	var targets: Array = ha["options"]["eligible_invite_targets"]
	for code in targets:
		var c := str(code)
		_content.add_child(_choice("Invite %s" % GameData.factions[c].name, c, false, not used,
			"Hold to invite them. They answer at once.", func(): Stepper.diplomacy_action("invite", c), true))
	if targets.is_empty() and members.size() <= 1:
		_content.add_child(HudStyle.label("No one can be invited right now.", 11, HudStyle.TEXT_DIM))
	for pair in GameStore.uninvitable_reasons(me, members, targets):
		var l := HudStyle.label("Can't invite %s: %s." % [pair[0], pair[1]], 11, HudStyle.TEXT_DIM)
		l.autowrap_mode = TextServer.AUTOWRAP_WORD_SMART
		_content.add_child(l)

	_content.add_child(HudStyle.label("Surrender", 12, HudStyle.TEXT))
	var demands: Array = ha.get("surrender", [])
	if demands.is_empty():
		var none := HudStyle.label("Nobody can be forced to surrender: you need more than twice a faction's income, or one of the Strategic Centers of a faction down to one or none.", 11, HudStyle.TEXT_DIM)
		none.autowrap_mode = TextServer.AUTOWRAP_WORD_SMART
		_content.add_child(none)
	for d in demands:
		var t := str(d["target"])
		var tip := "Hold to force %s to surrender: %s.\nIncome %d against %d.%s" % [
			GameData.factions[t].name, EventText.surrender_reasons(d["reasons"]), int(d["income"]["yours"]), int(d["income"]["theirs"]),
			"\nThey are your ally." if bool(d["allied"]) else ""]
		_content.add_child(_choice("Force %s to surrender%s" % [GameData.factions[t].name, "  (ally)" if bool(d["allied"]) else ""], t, false, true,
			tip, func(): Stepper.diplomacy_action("surrender", t), true))


## A row button. With `hold`, a long-click: it fires once its ring is full (HoldButton).
func _choice(text: String, faction: String, selected: bool, enabled: bool, tip: String, action: Callable, hold := false) -> Control:
	var b: Button = HoldButton.new() if hold else Button.new()
	if hold:
		(b as HoldButton).hold_seconds = HOLD_SECONDS
	b.text = "   " + text
	b.alignment = HORIZONTAL_ALIGNMENT_LEFT
	b.disabled = not enabled
	b.tooltip_text = tip
	b.focus_mode = Control.FOCUS_NONE
	b.custom_minimum_size = Vector2(0, 26)
	b.add_theme_font_size_override("font_size", 12)
	var edge := HudStyle.GOLD if selected else HudStyle.EDGE
	var bg := Color(0.2, 0.17, 0.06) if selected else Color(0.11, 0.13, 0.17)
	b.add_theme_stylebox_override("normal", HudStyle.box(edge, bg, 2 if selected else 1))
	b.add_theme_stylebox_override("hover", HudStyle.box(Color.WHITE, Color(0.2, 0.22, 0.27), 1))
	b.add_theme_stylebox_override("pressed", HudStyle.box(HudStyle.GOLD, bg, 2))
	b.add_theme_stylebox_override("disabled", HudStyle.box(Color(0.16, 0.19, 0.24), Color(0.09, 0.105, 0.135), 1))
	b.add_theme_color_override("font_color", HudStyle.GOLD if selected else HudStyle.TEXT)
	if hold:
		(b as HoldButton).activated.connect(action)
	else:
		b.pressed.connect(action)
	if faction != "":
		var chip := ColorRect.new()
		chip.color = GameData.factions[faction].color
		chip.custom_minimum_size = Vector2(6, 0)
		chip.mouse_filter = Control.MOUSE_FILTER_IGNORE
		chip.set_anchors_and_offsets_preset(Control.PRESET_LEFT_WIDE)
		chip.custom_minimum_size = Vector2(6, 20)
		b.add_child(chip)
		chip.position = Vector2(4, 3)
		chip.size = Vector2(6, 20)
	return b


## Combat / Non-Combat Move: what to do, and where the selection stands.
func _add_move_summary() -> void:
	var combat: bool = GameStore.human_move["kind"] == "combat"
	_content.add_child(HudStyle.label("Combat Move" if combat else "Non-Combat Move", 14, HudStyle.GOLD))
	var hint := HudStyle.label(
		"Pick a territory with your units. Its movable units start selected (click the faction banner to select all / none, or click units to toggle). Green spaces are where they can go: drag from the selected units or the territory onto one.",
		11, HudStyle.TEXT_DIM)
	hint.autowrap_mode = TextServer.AUTOWRAP_WORD_SMART
	_content.add_child(hint)
	var selected := GameStore.move_selected.size()
	var targets := GameStore.move_targets().size()
	var row := HBoxContainer.new()
	row.add_theme_constant_override("separation", 12)
	row.add_child(HudStyle.label("Selected %d" % selected, 13, Color.WHITE if selected > 0 else HudStyle.TEXT_DIM))
	row.add_child(HudStyle.label("Targets %d" % targets, 13, Color(0.55, 1.0, 0.6) if targets > 0 else HudStyle.TEXT_DIM))
	row.add_child(HudStyle.label("Queued %d" % GameStore.human_move["orders"].size(), 13, HudStyle.GOLD))
	_content.add_child(row)


func _add_site(info: Dictionary) -> void:
	var t: Dictionary = GameData.territories[_target]
	var is_sea: bool = t["type"] == "sea"
	_content.add_child(HudStyle.label("%s: %s" % ["Deploy into sea zone" if is_sea else "Buy at", t["name"]], 13))
	var note: String
	if is_sea:
		var names := []
		for s in info["sources"]:
			names.append(GameData.territories[int(s)]["name"])
		note = "Paid from adjacent: %s (SCs first)." % ", ".join(names)
	else:
		note = "Deploys here at Deploy & Income."
		if GameStore.human_purchase["contested"].has(_target):
			note += " Contested: Infantry only."
		if t.get("strategic_center", false):
			note += " SC prices."
	var lbl := HudStyle.label("%s Room for %d more." % [note, int(info["remaining"])], 11, HudStyle.TEXT_DIM)
	lbl.autowrap_mode = TextServer.AUTOWRAP_WORD_SMART
	_content.add_child(lbl)
	var left := GameStore.purchase_budget_left()
	for unit_type in UNIT_ORDER:
		_content.add_child(_unit_row(unit_type, info, is_sea, left))


func _unit_row(unit_type: String, info: Dictionary, is_sea: bool, budget_left: int) -> Control:
	var def: Dictionary = GameData.units["units"][unit_type]
	var cost := int(def["sc_cost"] if info["next_sc"] else def["cost"])
	var queued := GameStore.purchase_queued_at(_target, unit_type)
	var allowed := true
	var why := ""
	if not is_sea and str(def["category"]) == "Sea":
		allowed = false
		why = "Ships deploy into a sea zone: select an adjacent sea zone."
	elif is_sea and str(def["category"]) == "Land" and not _is_amphibious(def):
		allowed = false
		why = "Only Mechanized Infantry can deploy into a sea zone."
	elif GameStore.human_purchase["contested"].has(_target) and unit_type != "Infantry":
		allowed = false
		why = "Only Infantry may deploy into a contested territory."
	var can_add := allowed and int(info["remaining"]) > 0 and cost <= budget_left
	if allowed and int(info["remaining"]) <= 0:
		why = "No deployment capacity left here."
	elif allowed and cost > budget_left:
		why = "Not enough MCP."

	var row := HBoxContainer.new()
	row.add_theme_constant_override("separation", 5)
	row.tooltip_text = why
	var icon := TextureRect.new()
	icon.texture = UnitIcons.get_icon(unit_type)
	icon.custom_minimum_size = Vector2(20, 20)
	icon.expand_mode = TextureRect.EXPAND_IGNORE_SIZE
	icon.stretch_mode = TextureRect.STRETCH_KEEP_ASPECT_CENTERED
	row.add_child(icon)
	var name_l := HudStyle.label(unit_type, 12, HudStyle.TEXT if allowed else HudStyle.TEXT_DIM)
	name_l.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	row.add_child(name_l)
	row.add_child(HudStyle.label("%d MCP" % cost, 12, HudStyle.GOLD if info["next_sc"] else HudStyle.TEXT_DIM))
	row.add_child(_step_button("-", queued > 0, func(): remove_requested.emit(unit_type, _target)))
	var qty := HudStyle.label(str(queued), 13, Color.WHITE if queued > 0 else HudStyle.TEXT_DIM)
	qty.custom_minimum_size = Vector2(18, 0)
	qty.horizontal_alignment = HORIZONTAL_ALIGNMENT_CENTER
	row.add_child(qty)
	row.add_child(_step_button("+", can_add, func(): add_requested.emit(unit_type, _target)))
	return row


## Only amphibious land units (units.json's "Amphibious" ability: Mechanized Infantry) can enter the water.
static func _is_amphibious(def: Dictionary) -> bool:
	for a in def.get("special_abilities", []):
		if str(a).begins_with("Amphibious"):
			return true
	return false


func _step_button(text: String, enabled: bool, action: Callable) -> Button:
	var b := Button.new()
	b.text = text
	b.disabled = not enabled
	b.focus_mode = Control.FOCUS_NONE
	b.custom_minimum_size = Vector2(26, 20)
	b.add_theme_font_size_override("font_size", 14)
	b.add_theme_stylebox_override("normal", HudStyle.box(HudStyle.EDGE, Color(0.13, 0.16, 0.2), 1))
	b.add_theme_stylebox_override("hover", HudStyle.box(HudStyle.GOLD, Color(0.2, 0.17, 0.06), 1))
	b.add_theme_stylebox_override("pressed", HudStyle.box(HudStyle.GOLD, Color(0.3, 0.25, 0.08), 1))
	b.add_theme_stylebox_override("disabled", HudStyle.box(Color(0.16, 0.19, 0.24), Color(0.09, 0.105, 0.135), 1))
	b.pressed.connect(action)
	return b


func _add_budget() -> void:
	var hp: Dictionary = GameStore.human_purchase
	var left := GameStore.purchase_budget_left()
	var row := HBoxContainer.new()
	row.add_theme_constant_override("separation", 12)
	row.add_child(HudStyle.label("Budget %d MCP" % int(hp["treasury"]), 13))
	row.add_child(HudStyle.label("Queued %d" % int(hp["total_cost"]), 13, HudStyle.GOLD))
	row.add_child(HudStyle.label("Left %d" % left, 13, Color(0.55, 1.0, 0.6) if left > 0 else Color(1.0, 0.55, 0.5)))
	_content.add_child(row)

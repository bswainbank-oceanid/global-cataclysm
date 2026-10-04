class_name InvitationWindow
extends Control
## The window a player answers a bot's alliance invitation in. It opens when the
## phase queue says a bot has invited the player (the phase can't run until they
## answer) and closes on the answer. It does NOT dim or block the map: the player
## may want to look at the board before deciding.

signal answered(accept: bool)

var _title: Label
var _body: RichTextLabel
var _panel: PanelContainer


func _ready() -> void:
	z_index = 400
	mouse_filter = Control.MOUSE_FILTER_IGNORE
	visible = false
	set_anchors_and_offsets_preset(Control.PRESET_FULL_RECT)
	var center := CenterContainer.new()
	center.mouse_filter = Control.MOUSE_FILTER_IGNORE
	center.set_anchors_and_offsets_preset(Control.PRESET_FULL_RECT)
	add_child(center)
	_panel = PanelContainer.new()
	HudStyle.paper_sheet(_panel)
	_panel.custom_minimum_size = Vector2(430, 0)
	center.add_child(_panel)
	var v := VBoxContainer.new()
	v.add_theme_constant_override("separation", 8)
	_panel.add_child(v)
	_title = HudStyle.label("Alliance invitation", 18, HudStyle.GOLD)
	_title.horizontal_alignment = HORIZONTAL_ALIGNMENT_CENTER
	v.add_child(_title)
	_body = RichTextLabel.new()
	_body.bbcode_enabled = true
	_body.fit_content = true
	_body.custom_minimum_size = Vector2(400, 0)
	_body.add_theme_font_size_override("normal_font_size", 13)
	_body.add_theme_font_size_override("bold_font_size", 13)
	v.add_child(_body)
	var row := HBoxContainer.new()
	row.alignment = BoxContainer.ALIGNMENT_CENTER
	row.add_theme_constant_override("separation", 14)
	v.add_child(row)
	var accept := _button("Accept", true)
	accept.pressed.connect(func(): answered.emit(true))
	row.add_child(accept)
	var decline := _button("Decline", false)
	decline.pressed.connect(func(): answered.emit(false))
	row.add_child(decline)
	GameStore.alliance_changed.connect(_sync)
	_sync()


func _button(text: String, primary: bool) -> Button:
	var b := Button.new()
	b.text = text
	b.custom_minimum_size = Vector2(150, 40)
	if primary:
		HudStyle.primary(b)
	else:
		HudStyle.secondary(b)
	return b


func _sync() -> void:
	visible = GameStore.invitation_pending()
	if not visible:
		return
	if Dbg.args.has("shot"):
		print("[dbg] invitation window shown")
	var inv: Dictionary = GameStore.invitation
	var from := str(inv["from"])
	var me := str(inv["to"])
	var members: Array = inv["members"]
	var others := []
	for m in members:
		others.append("[color=#%s]%s[/color]" % [GameData.factions[str(m)].color.to_html(false), GameData.factions[str(m)].name])
	_title.text = "%s invites you to an alliance" % from
	var text := "[b]%s[/b] asks you to join [b]%s[/b]." % [GameData.factions[from].name, "their alliance" if members.size() > 1 else "an alliance with them"]
	text += "\n\nIf you accept, the alliance is: %s and [b]you[/b]." % ", ".join(others)
	text += "\n\nAllies don't fight each other and defend together. The game ends when every remaining faction is allied."
	if GameStore.can_withdraw_from_alliances():
		text += " You can withdraw later, on your own Alliances phase"
		text += " (and rejoin those you left)." if GameStore.can_rejoin_alliances() else " (but you couldn't rejoin those you left)."
	else:
		text += " In this game an alliance can't be left once joined."
	_body.text = text
	_panel.tooltip_text = "Answer to continue"

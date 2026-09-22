class_name ArmisticeWindow
extends Control
## The window a player answers a PROPOSED ARMISTICE in -- someone else has proposed
## ending the game right here, immediately, and it can't proceed until every human it
## names answers (bots already answered, instantly; see server/session.py). Since at
## most one human is ever seated (server/lobby.py), this only ever appears if a second
## human is playing via a direct GameSession (not the ordinary lobby-built game) --
## kept for that case and for whenever the one-human limit is lifted. Mirrors
## invitation_window.gd: it does NOT dim or block the map, so the player may look at
## the board before deciding.

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
	_panel.add_theme_stylebox_override("panel", HudStyle.box(HudStyle.GOLD, Color(0.07, 0.085, 0.11), 3))
	_panel.custom_minimum_size = Vector2(430, 0)
	center.add_child(_panel)
	var v := VBoxContainer.new()
	v.add_theme_constant_override("separation", 8)
	_panel.add_child(v)
	_title = HudStyle.label("Armistice proposed", 18, HudStyle.GOLD)
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
	var accept := _button("Accept", Color(0.5, 1.0, 0.6))
	accept.pressed.connect(func(): answered.emit(true))
	row.add_child(accept)
	var decline := _button("Decline", Color(1.0, 0.6, 0.5))
	decline.pressed.connect(func(): answered.emit(false))
	row.add_child(decline)
	GameStore.armistice_changed.connect(_sync)
	_sync()


func _button(text: String, colour: Color) -> Button:
	var b := Button.new()
	b.text = text
	b.focus_mode = Control.FOCUS_NONE
	b.custom_minimum_size = Vector2(150, 40)
	b.add_theme_font_size_override("font_size", 15)
	b.add_theme_color_override("font_color", colour)
	b.add_theme_color_override("font_hover_color", Color.WHITE)
	b.add_theme_stylebox_override("normal", HudStyle.box(colour.darkened(0.3), Color(0.12, 0.14, 0.18), 2))
	b.add_theme_stylebox_override("hover", HudStyle.box(Color.WHITE, Color(0.2, 0.22, 0.27), 2))
	b.add_theme_stylebox_override("pressed", HudStyle.box(colour, Color(0.24, 0.26, 0.3), 2))
	return b


func _sync() -> void:
	visible = GameStore.armistice_pending()
	if not visible:
		return
	if Dbg.args.has("shot"):
		print("[dbg] armistice window shown")
	var from := str(GameStore.armistice["from"])
	_title.text = "%s proposes an armistice" % from
	var text := "[b]%s[/b] proposes ending the game right here, immediately -- an armistice." % GameData.factions[from].name
	text += "\n\nNobody wins: the game simply stops, and the Game Over report shows how everyone stood when it did."
	text += "\n\nIf anyone declines, the proposal falls through and the game continues as normal."
	_body.text = text
	_panel.tooltip_text = "Answer to continue"

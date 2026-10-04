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
	HudStyle.paper_sheet(_panel)
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
	var accept := _button("Accept", true)
	accept.pressed.connect(func(): answered.emit(true))
	row.add_child(accept)
	var decline := _button("Decline", false)
	decline.pressed.connect(func(): answered.emit(false))
	row.add_child(decline)
	GameStore.armistice_changed.connect(_sync)
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
	visible = GameStore.armistice_pending()
	if not visible:
		return
	if Dbg.args.has("shot"):
		print("[dbg] armistice window shown")
	# "from" is null when a pure SPECTATOR proposed it (nobody's own faction) rather than a faction.
	var from = GameStore.armistice["from"]
	var proposer := "[b]%s[/b]" % GameData.factions[str(from)].name if from != null and GameData.factions.has(str(from)) else "A spectator"
	_title.text = "%s proposes an armistice" % (str(from) if from != null else "A spectator")
	var text := "%s proposes ending the game right here, immediately -- an armistice." % proposer
	var after = GameStore.armistice.get("automatic_after")
	if after != null:  # the game's own proposal (the setup's 'Rounds until armistice proposal')
		_title.text = "%d rounds played: armistice proposed" % int(after)
		text = "%d rounds have been played, so the game proposes ending it right here -- an armistice." % int(after)
	text += "\n\nNobody wins: the game simply stops, and the Game Over report shows how everyone stood when it did."
	text += "\n\nIf anyone declines, the proposal falls through and the game continues as normal." if after == null \
		else "\n\nThe bots have accepted. If you decline, the game continues, and an armistice is proposed again in 5 rounds."
	_body.text = text
	_panel.tooltip_text = "Answer to continue"

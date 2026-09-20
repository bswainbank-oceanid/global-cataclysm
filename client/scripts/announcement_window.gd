class_name AnnouncementWindow
extends Control
## The panel that announces significant game events in the middle of the screen:
## a faction eliminated, alliances formed or left, the game's end. Announcements
## queue up and are shown one at a time; the player acknowledges each with OK.
## It does NOT dim or block the map (the player may want to look at the board), but
## while one is up the game does not run on by itself (GameStore.announcement_open --
## see TurnStepper._process), so nothing scrolls past unread.

var _queue: Array = []   # [{title, body, color}]
var _title: Label
var _body: RichTextLabel
var _ok: Button
var _panel: PanelContainer
var _accent: ColorRect


func _ready() -> void:
	z_index = 420
	mouse_filter = Control.MOUSE_FILTER_IGNORE
	visible = false
	set_anchors_and_offsets_preset(Control.PRESET_FULL_RECT)
	var center := CenterContainer.new()
	center.mouse_filter = Control.MOUSE_FILTER_IGNORE
	center.set_anchors_and_offsets_preset(Control.PRESET_FULL_RECT)
	add_child(center)
	_panel = PanelContainer.new()
	_panel.custom_minimum_size = Vector2(460, 0)
	center.add_child(_panel)
	var v := VBoxContainer.new()
	v.add_theme_constant_override("separation", 8)
	_panel.add_child(v)
	_accent = ColorRect.new()
	_accent.custom_minimum_size = Vector2(0, 5)
	v.add_child(_accent)
	_title = HudStyle.label("", 20, HudStyle.GOLD)
	_title.horizontal_alignment = HORIZONTAL_ALIGNMENT_CENTER
	_title.autowrap_mode = TextServer.AUTOWRAP_WORD_SMART
	v.add_child(_title)
	_body = RichTextLabel.new()
	_body.bbcode_enabled = true
	_body.fit_content = true
	_body.custom_minimum_size = Vector2(430, 0)
	_body.add_theme_font_size_override("normal_font_size", 14)
	_body.add_theme_font_size_override("bold_font_size", 14)
	v.add_child(_body)
	var row := HBoxContainer.new()
	row.alignment = BoxContainer.ALIGNMENT_CENTER
	v.add_child(row)
	_ok = Button.new()
	_ok.focus_mode = Control.FOCUS_NONE
	_ok.custom_minimum_size = Vector2(170, 40)
	_ok.add_theme_font_size_override("font_size", 15)
	_ok.add_theme_color_override("font_color", HudStyle.GOLD)
	_ok.add_theme_color_override("font_hover_color", Color.WHITE)
	_ok.add_theme_stylebox_override("normal", HudStyle.box(HudStyle.GOLD, Color(0.16, 0.14, 0.05), 2))
	_ok.add_theme_stylebox_override("hover", HudStyle.box(Color.WHITE, Color(0.24, 0.2, 0.06), 2))
	_ok.add_theme_stylebox_override("pressed", HudStyle.box(HudStyle.GOLD, Color(0.3, 0.25, 0.08), 2))
	_ok.pressed.connect(acknowledge)
	row.add_child(_ok)
	Stepper.announced.connect(add)
	Stepper.game_reset.connect(clear)


## Queue announcements ({title, body, color}); the first shows at once if none is up.
func add(items: Array) -> void:
	_queue.append_array(items)
	if not visible:
		_show_next()


func clear() -> void:
	_queue.clear()
	_hide()


## OK: close this announcement, and show the next one if more are waiting.
func acknowledge() -> void:
	if not _queue.is_empty():
		_queue.pop_front()
	if _queue.is_empty():
		_hide()
	else:
		_show_next()


func _show_next() -> void:
	if _queue.is_empty():
		_hide()
		return
	var a: Dictionary = _queue[0]
	var col: Color = a.get("color", HudStyle.GOLD)
	_panel.add_theme_stylebox_override("panel", HudStyle.box(col, Color(0.07, 0.085, 0.11), 3))
	_accent.color = col
	_title.text = str(a["title"])
	_body.text = str(a["body"])
	var more := _queue.size() - 1
	_ok.text = "OK" if more == 0 else "OK  (%d more)" % more
	visible = true
	GameStore.announcement_open = true
	if Dbg.args.has("shot"):
		print("[dbg] announcement: ", a["title"])


func _hide() -> void:
	visible = false
	GameStore.announcement_open = false


func _unhandled_input(event: InputEvent) -> void:
	if visible and event is InputEventKey and event.pressed and not event.echo \
			and (event.keycode == KEY_ENTER or event.keycode == KEY_KP_ENTER or event.keycode == KEY_ESCAPE):
		acknowledge()
		get_viewport().set_input_as_handled()

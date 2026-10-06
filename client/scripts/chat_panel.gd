class_name ChatPanel
extends VBoxContainer
## A chat room's messages and a line to type in (Available Games' room, a game lobby's, and a game's Game
## Chat and Ally Chat -- `dark`, in the side panel). Sending emits `send`; the server's messages come back
## through add_message (the sender's own included).

signal send(text: String)

const MAX_TEXT := 500  # (server/chat.py's MAX_TEXT)

var _log: RichTextLabel
var _line: LineEdit


func _init(height := 220.0, dark := false) -> void:
	add_theme_constant_override("separation", 6)
	if not dark:
		add_child(HudStyle.heading("Chat", 15))
	_log = RichTextLabel.new()
	_log.bbcode_enabled = true
	_log.scroll_following = true
	_log.custom_minimum_size = Vector2(0, height)
	_log.size_flags_vertical = Control.SIZE_EXPAND_FILL
	_log.add_theme_font_size_override("normal_font_size", 13)
	_log.add_theme_font_size_override("bold_font_size", 13)
	if dark:
		_log.add_theme_font_size_override("normal_font_size", 12)
		_log.add_theme_font_size_override("bold_font_size", 12)
		_log.add_theme_color_override("default_color", HudStyle.TEXT)
	else:
		_log.add_theme_stylebox_override("normal", GCTheme.box(GCTheme.WHITE, GCTheme.NAVY, 2, -1, Vector4(8, 6, 8, 6)))
	add_child(_log)
	var row := HBoxContainer.new()
	row.add_theme_constant_override("separation", 6)
	add_child(row)
	_line = LineEdit.new()
	_line.max_length = MAX_TEXT
	_line.placeholder_text = "Say something"
	_line.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	_line.custom_minimum_size = Vector2(0, 30 if dark else 34)
	_line.text_submitted.connect(func(_t): _send())
	row.add_child(_line)
	var b := Button.new()
	b.text = "Send"
	HudStyle.secondary(b)
	b.pressed.connect(_send)
	row.add_child(b)


## Replaces the messages with a room's history ([{player_name, text, sent, ...}], oldest first).
func set_messages(messages: Array) -> void:
	_log.clear()
	_line.editable = true
	for m in messages:
		add_message(m)


## Shows `text` (dimmed) instead of any messages: why there's nothing to read or say here.
func set_note(text: String) -> void:
	_log.clear()
	_log.append_text("[i]%s[/i]\n" % text)
	_line.editable = false


func add_message(m: Dictionary) -> void:
	_line.editable = true
	var name := str(m.get("player_name", "?")).replace("[", "(")
	var text := str(m.get("text", "")).replace("[", "[lb]")
	_log.append_text("[b]%s:[/b] %s\n" % [name, text])


func _send() -> void:
	var text := _line.text.strip_edges()
	if text == "":
		return
	_line.text = ""
	send.emit(text)

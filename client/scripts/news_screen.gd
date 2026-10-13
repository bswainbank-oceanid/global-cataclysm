class_name NewsScreen
extends MenuScreen
## News (the main menu's): the rolling news, newest entry first -- the server's (server/news.py, from
## reference/GC72_News.odt by tools/import_news.py), on a paper sheet over the poster like the Rules. Opening
## it marks the newest entry seen (Settings.news_seen, kept with the account), which stops News pulsing.

signal back_pressed

const WIDTH := 860.0

var _title: Label
var _body: VBoxContainer
var _scroll: ScrollContainer
var _news: Dictionary = {}


func _build() -> void:
	var center := MenuScreen.scroll_center(self)  # (scroll bars when the window is too small for it)
	var panel := MenuScreen.sheet(WIDTH, 10)
	center.add_child(panel)
	var v := MenuScreen.body(panel)
	var head := HudStyle.heading("News", 22, true)
	v.add_child(head)
	_title = head.get_child(0) as Label  # (the heading's label, over its rule)
	_scroll = ScrollContainer.new()
	_scroll.horizontal_scroll_mode = ScrollContainer.SCROLL_MODE_DISABLED
	v.add_child(_scroll)
	_body = VBoxContainer.new()
	_body.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	_body.add_theme_constant_override("separation", 10)
	_scroll.add_child(_body)
	var back := Button.new()
	back.text = "Back"
	HudStyle.secondary(back)
	back.custom_minimum_size = Vector2(140, 40)
	back.size_flags_horizontal = Control.SIZE_SHRINK_BEGIN
	back.pressed.connect(func(): back_pressed.emit())
	v.add_child(back)
	get_viewport().size_changed.connect(_fit)
	_fit()
	_show()


func open() -> void:
	_scroll.scroll_vertical = 0
	_fit()
	visible = true
	mark_seen()


func close() -> void:
	visible = false


## The server's "news" ({title, entries}).
func set_news(doc: Dictionary) -> void:
	_news = doc
	if _body != null:
		_show()
	if visible:
		mark_seen()


## The newest entry's id ("" with no news).
func newest() -> String:
	var entries: Array = _news.get("entries", [])
	return str(entries[0].get("id", "")) if not entries.is_empty() else ""


## Whether there's an entry newer than the last one the player saw.
func unread() -> bool:
	return newest() != "" and newest() != Settings.news_seen


## Seen: the newest entry is remembered (with the account), so News stops pulsing.
func mark_seen() -> void:
	if unread():
		Settings.news_seen = newest()
		Settings.commit()


func _show() -> void:
	for c in _body.get_children():
		c.queue_free()
	_title.text = str(_news.get("title", "News")).to_upper()
	var entries: Array = _news.get("entries", [])
	if entries.is_empty():
		_body.add_child(_label("No news yet.", 15, HudStyle.TEXT_DIM))
	for i in entries.size():
		if i > 0:
			_body.add_child(HSeparator.new())
		_entry(entries[i])


func _entry(e: Dictionary) -> void:
	var box := VBoxContainer.new()
	box.add_theme_constant_override("separation", 6)
	_body.add_child(box)
	box.add_child(_label(str(e.get("date", "")), 13, HudStyle.TEXT_DIM))
	var h := Label.new()
	h.text = str(e.get("title", "")).to_upper()
	h.autowrap_mode = TextServer.AUTOWRAP_WORD_SMART
	h.add_theme_font_override("font", GCTheme.font("display_black"))
	h.add_theme_font_size_override("font_size", 22)
	h.add_theme_color_override("font_color", GCTheme.RED)
	box.add_child(h)
	for p in e.get("paragraphs", []):
		var t := RichTextLabel.new()
		t.bbcode_enabled = true
		t.fit_content = true
		t.scroll_active = false
		t.selection_enabled = true
		t.add_theme_font_size_override("normal_font_size", 15)
		t.text = str(p)
		t.meta_clicked.connect(func(meta): OS.shell_open(str(meta)))  # (a link: in the browser)
		box.add_child(t)


func _label(text: String, size: int, color: Color = HudStyle.TEXT) -> Label:
	var l := HudStyle.label(text, size, color)
	l.autowrap_mode = TextServer.AUTOWRAP_WORD_SMART
	return l


## The news scrolls inside a sheet that leaves the poster showing around it.
func _fit() -> void:
	if _scroll != null and is_inside_tree():
		_scroll.custom_minimum_size = Vector2(0, maxf(240.0, get_viewport_rect().size.y - 260.0))

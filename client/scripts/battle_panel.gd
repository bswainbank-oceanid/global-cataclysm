class_name BattlePanel
extends Control
## The battle board (reference/GC Battle Board Mockup.pdf): pops up in the middle
## of the screen when a battle pauses, and plays it out with Next Roll.
##
## Both sides' units sit in a chart of rows, one per defense value. Every unit
## stays in the row of its DEFENSE (Transports have defense 5 like most of the rest) and
## only moves if its defense changes -- a unit promoted mid-battle steps up a row.
## Each roll shows a top-down die in the Roll column; hit units get a "/" (or an
## "X" when eliminated). The Resolve options on each side choose how much one press
## of the button reveals (BattleModel), and with the finer ones each side also gets
## a pause of its own before it rolls. A text box under the chart says what the
## last pulse did and what the next will do, and the button is labelled to match.
## At the end every unit is back on the board, casualties marked, the battle
## summary is in the text box, and End Battle closes the board.
##
## The dice are already decided by the engine. The first Next Roll asks the
## server to fight the battle (roll_requested); its events arrive through
## receive_events(), and the press that asked for them then plays.

signal roll_requested   # the battle hasn't been fought yet: the server must do it
signal closed           # End Battle pressed

const TABLE_W := 940.0
const COL_DEF := 62.0
const COL_ROLL := 220.0
const HEADER_H := 26.0
const NARRATION_H := 104.0
const ROW_MIN := 56.0
const TILE_SCALE := 0.8
const GAP := 3.0
const SLIDE_SECONDS := 0.28

## The chart's rows: one per defense value. A defending Infantry with five promotions has
## a defense of 11 (Dig In is added on top of the cap of 10): when that ever happens the
## chart grows a row of its own for it, marked with a shimmering golden box.
const ROWS := [5, 6, 7, 8, 9, 10]
const TOP_DEFENSE := 11

var _model: BattleModel
var _tiles := {}            # unit_id -> UnitTile
var _dice: Array = []
var _unit_row := {}         # unit_id -> row index in the current arrangement
var _rows: Array = ROWS.duplicate()   # ROWS, plus the golden 11 while a unit has that defense
var _row_y: Array = []
var _row_h: Array = []
var _want_press := false

var _title: Label
var _side_labels := {}
var _table: Control
var _scroll: ScrollContainer
var _result: RichTextLabel
var _button: Button
var _panel: PanelContainer
var _frame_tag: Label  # names what the frame highlights (see _apply_frame)
var _frame_banner: PanelContainer  # ...on a solid band of the frame's colour
var _frame_colour := Color(0, 0, 0, 0)  # the special round's colour while one is on show (alpha 0: none)
var _title_row: HBoxContainer
var _owner_icon: Control  # the icon of the faction controlling the battle's territory (none at sea)
var _side_icons := {}  # "attacker" / "defender" -> HBoxContainer of that side's faction icons
# The frame's highlights: [colour, tag] per BattleModel.highlight() -- the air superiority round, and each
# first-round combat bonus by its reason (engine.engine's ROUND1_*), under the names players know them by.
const FRAMES := {
	"air": [Color("#2E6E9E"), "AIR SUPERIORITY ROUND"],
	"amphibious landing": [Color("#1F7A6A"), "AMPHIBIOUS LANDING BONUS"],
	"sea-deploy ambush": [Color("#B8620E"), "BLOCKADE BONUS"],
	"former-ally territory reclaim": [GCTheme.RED, "BETRAYAL BONUS"],
}


func _ready() -> void:
	visible = false
	mouse_filter = Control.MOUSE_FILTER_STOP
	z_index = 90
	get_viewport().size_changed.connect(_fit_to_screen)
	Settings.changed.connect(func():
		if visible and _model != null:  # a Resolve change alters what the button will do
			_refresh_texts())
	var dim := ColorRect.new()
	dim.color = Color(GCTheme.NAVY_DARK, 0.6)
	dim.set_anchors_preset(Control.PRESET_FULL_RECT)
	dim.mouse_filter = Control.MOUSE_FILTER_IGNORE
	add_child(dim)

	var center := CenterContainer.new()
	center.set_anchors_preset(Control.PRESET_FULL_RECT)
	center.mouse_filter = Control.MOUSE_FILTER_IGNORE
	add_child(center)
	_panel = PanelContainer.new()
	HudStyle.paper_sheet(_panel)
	center.add_child(_panel)

	var v := VBoxContainer.new()
	v.add_theme_constant_override("separation", 6)
	_panel.add_child(v)

	_frame_banner = PanelContainer.new()
	_frame_banner.visible = false
	v.add_child(_frame_banner)
	_frame_tag = HudStyle.label("", 20)
	_frame_tag.add_theme_color_override("font_color", GCTheme.WHITE)  # (white on the round's colour)
	_frame_tag.add_theme_font_override("font", GCTheme.font("display_black"))
	_frame_tag.horizontal_alignment = HORIZONTAL_ALIGNMENT_CENTER
	_frame_banner.add_child(_frame_tag)

	_title_row = HBoxContainer.new()  # the controlling faction's icon, then the title
	_title_row.alignment = BoxContainer.ALIGNMENT_CENTER
	_title_row.add_theme_constant_override("separation", 8)
	v.add_child(_title_row)
	_owner_icon = Control.new()
	_title_row.add_child(_owner_icon)
	_title = HudStyle.label("", 15, HudStyle.GOLD)
	_title.horizontal_alignment = HORIZONTAL_ALIGNMENT_CENTER
	_title_row.add_child(_title)

	var sides := HBoxContainer.new()
	sides.add_theme_constant_override("separation", 6)
	v.add_child(sides)
	for side in ["attacker", "defender"]:
		sides.add_child(_side_box(side))

	_scroll = ScrollContainer.new()
	_scroll.horizontal_scroll_mode = ScrollContainer.SCROLL_MODE_DISABLED
	_scroll.custom_minimum_size = Vector2(TABLE_W + 12, 200)
	v.add_child(_scroll)
	_table = Control.new()
	_table.custom_minimum_size = Vector2(TABLE_W, 400)
	_table.draw.connect(_draw_table)
	_scroll.add_child(_table)

	_result = RichTextLabel.new()
	_result.bbcode_enabled = true
	_result.fit_content = false
	_result.scroll_active = true
	_result.custom_minimum_size = Vector2(TABLE_W, NARRATION_H)
	_result.add_theme_font_size_override("normal_font_size", 13)
	_result.add_theme_font_size_override("bold_font_size", 13)
	_result.add_theme_stylebox_override("normal", GCTheme.box(GCTheme.WHITE, GCTheme.NAVY, 2))
	v.add_child(_result)

	_button = Button.new()
	_button.text = "Next Roll"
	_button.custom_minimum_size = Vector2(220, 40)
	_button.size_flags_horizontal = Control.SIZE_SHRINK_CENTER
	HudStyle.primary(_button)
	_button.pressed.connect(_on_button)
	v.add_child(_button)


## One side's header: who it is, and the Resolve radio options (remembered per side).
func _side_box(side: String) -> Control:
	var box := PanelContainer.new()
	box.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	box.add_theme_stylebox_override("panel", GCTheme.box(GCTheme.WHITE, GCTheme.NAVY, 2))
	var v := VBoxContainer.new()
	v.add_theme_constant_override("separation", 0)
	box.add_child(v)
	var head_row := HBoxContainer.new()  # the side's faction icons, then "Attacker - GPC"
	head_row.alignment = BoxContainer.ALIGNMENT_CENTER
	head_row.add_theme_constant_override("separation", 6)
	v.add_child(head_row)
	var icons := HBoxContainer.new()
	icons.add_theme_constant_override("separation", 3)
	head_row.add_child(icons)
	_side_icons[side] = icons
	var head := HudStyle.label("", 14)
	head.horizontal_alignment = HORIZONTAL_ALIGNMENT_CENTER
	head_row.add_child(head)
	_side_labels[side] = head
	var options := HBoxContainer.new()
	options.add_theme_constant_override("separation", 3)
	var caption := HudStyle.label("Resolve:", 12, HudStyle.TEXT_DIM)
	options.add_child(caption)
	v.add_child(options)
	var group := ButtonGroup.new()
	var current: int = Settings.resolve_attacker if side == "attacker" else Settings.resolve_defender
	for mode in BattleModel.RESOLVE_NAMES.size():
		var r := Button.new()  # radio behaviour: toggle buttons in one ButtonGroup
		r.text = BattleModel.RESOLVE_NAMES[mode]
		r.toggle_mode = true
		r.button_group = group
		r.focus_mode = Control.FOCUS_NONE
		r.size_flags_horizontal = Control.SIZE_EXPAND_FILL
		r.add_theme_font_size_override("font_size", 12)
		r.theme_type_variation = "RadioButton"
		r.button_pressed = mode == current
		r.pressed.connect(func():
			if side == "attacker":
				Settings.resolve_attacker = mode
			else:
				Settings.resolve_defender = mode
			Settings.commit())
		options.add_child(r)
	return box


# ---- opening / events -----------------------------------------------------

## This board covers the whole window (a dimmed backdrop with the board centred).
func _fit_to_screen() -> void:
	position = Vector2.ZERO
	size = get_viewport_rect().size


func open(preview: Dictionary) -> void:
	_fit_to_screen()
	_model = BattleModel.from_preview(preview)
	_want_press = false
	for t in _tiles.values():
		t.queue_free()
	_tiles.clear()
	for d in _dice:
		d.queue_free()
	_dice.clear()
	for id in _model.unit_order:
		var tile := UnitTile.for_battle(_model.units[id])
		tile.on_paper = true
		tile.toggle_mode = false
		tile.scale = Vector2(TILE_SCALE, TILE_SCALE)
		_table.add_child(tile)
		tile.size = tile.custom_minimum_size
		_tiles[id] = tile
	_set_header()
	_refresh_texts()
	_button.disabled = false
	_layout(false)
	visible = true


func receive_events(events: Array) -> void:
	if _model == null:
		return
	_model.load_events(events)
	_apply_frame()  # (the rounds are known now: an air superiority round first, say)
	if _want_press:
		_want_press = false
		_do_press()


func _set_header() -> void:
	var tid := _model.territory_id
	var t: Dictionary = GameData.territories[tid]
	var owner := GameStore.owner_of(tid)
	var lines := ["Battle: %d. %s" % [tid, t["name"]]]
	if t["type"] == "land":
		var value := int(t.get("value", 0))
		if GameStore.is_sc(tid):
			lines.append("Strategic Center - %d" % (value + GameData.sc_bonus))
		else:
			lines.append("Territory - value %d" % value)
		lines.append("%s Land Territory" % (owner if owner != "" else "Unowned"))
	else:
		lines.append("Sea Zone")
	_title.text = "\n".join(lines)
	# the controlling faction's icon beside the title (none at sea, or unowned)
	_owner_icon.get_parent().remove_child(_owner_icon)
	_owner_icon.queue_free()
	_owner_icon = FactionIcons.make(owner, 36, true) if owner != "" else Control.new()
	_title_row.add_child(_owner_icon)
	_title_row.move_child(_owner_icon, 0)
	var by_side := _model.factions_by_side()
	_side_labels["attacker"].text = "Attacker - " + " / ".join(by_side["attacker"])
	_side_labels["defender"].text = "Defender - " + " / ".join(by_side["defender"])
	for side in _side_icons:
		for c in _side_icons[side].get_children():
			c.queue_free()
		for code in by_side[side]:
			_side_icons[side].add_child(FactionIcons.make(str(code), 30, true))


## Dev/scripted: press the board's button `n` times, letting the server answer
## the first press.
func debug_press(n: int) -> void:
	for i in n:
		if _model == null or not visible:
			return
		_on_button()
		await get_tree().create_timer(0.7).timeout


# ---- the press --------------------------------------------------------------

func _on_button() -> void:
	if _model == null:
		return
	if _model.finished:
		visible = false
		closed.emit()
		return
	if not _model.events_loaded:
		_want_press = true
		_button.disabled = true
		_button.text = "ROLLING..."
		roll_requested.emit()
		return
	_do_press()


func _do_press() -> void:
	_model.press({"attacker": Settings.resolve_attacker, "defender": Settings.resolve_defender})
	_sync()


func _sync() -> void:
	var rolling := {}  # the units whose rolls are on show get highlighted
	for e in _model.last_rolls:
		rolling[int(e["unit_id"])] = true
	for id in _model.unit_order:
		var u: Dictionary = _model.units[id]
		_tiles[id].rolling = rolling.has(id)
		_tiles[id].update_from_battle(u)
		_tiles[id].visible = u["present"]
	_button.disabled = false
	_refresh_texts()
	_layout(true)
	_show_dice()
	_play_reactions()


## Every roll on show shakes the unit that made it; every hit bounces the unit that took it.
func _play_reactions() -> void:
	var n := 0
	for e in _model.last_rolls:
		var delay := minf(0.05 * n, 0.9)
		n += 1
		var id := int(e["unit_id"])
		if _tiles.has(id):
			_tiles[id].shake(delay)
		if bool(e.get("hit", false)) and e.get("target_unit_id") != null:
			var target := int(e["target_unit_id"])
			if _tiles.has(target):
				_tiles[target].bounce(delay + 0.3)


## The button's label and the text box: what the last pulse did, then what happens next
## (or, once the battle is over, its summary).
func _refresh_texts() -> void:
	_apply_frame()
	var action := _model.next_action({"attacker": Settings.resolve_attacker, "defender": Settings.resolve_defender})
	if not _button.disabled:
		_button.text = str(action["label"]).to_upper()
	var lines := []
	if not _model.prev_lines.is_empty():
		lines.append("[b]Last:[/b] " + "\n".join(_model.prev_lines))
	if _model.finished:
		lines.append(EventText.describe(_model.summary) if not _model.summary.is_empty() else "Result: " + _model.outcome.replace("_", " "))
	else:
		lines.append(str(action["text"]))
	_result.text = "\n".join(lines)
	# The end state is read from the bottom (the summary); otherwise from the top.
	_result.scroll_to_line(maxi(_result.get_line_count() - 1, 0) if _model.finished else 0)


## The frame shows what makes the round on show special: the air superiority round, or a first-round
## combat bonus (FRAMES) -- a heavy border in its colour, a banner of that colour naming it, and the chart's
## header band in it too; the sheet's navy frame otherwise.
func _apply_frame() -> void:
	var kind := _model.highlight() if _model != null else ""
	var style: Array = FRAMES.get(kind, [])
	_panel.remove_theme_stylebox_override("panel")  # the paper sheet's own navy frame
	_frame_colour = Color(0, 0, 0, 0)
	if not style.is_empty():
		var colour: Color = style[0]
		_frame_colour = colour
		_panel.add_theme_stylebox_override("panel", GCTheme.box(GCTheme.PAPER, colour, 12, 16, Vector4(18, 14, 18, 14)))
		_frame_banner.add_theme_stylebox_override("panel", GCTheme.box(colour, colour.darkened(0.35), 2, 4, Vector4(12, 6, 12, 6)))
		_frame_tag.text = (str(style[1]) if kind == "air" else "%s  -  %s, round 1" % [style[1], _model.bonus_holder()]).to_upper()
	_frame_banner.visible = not style.is_empty()
	_table.queue_redraw()  # (its header band takes the round's colour)


# ---- layout -------------------------------------------------------------------

func _row_of(u: Dictionary) -> int:
	return clampi(int(u["defense"]), 5, int(_rows.back())) - 5


## The rows for the units on the board now: the usual six, and a golden 11 if anyone has it.
func _rows_now() -> Array:
	var rows: Array = ROWS.duplicate()
	for id in _model.unit_order:
		var u: Dictionary = _model.units[id]
		if bool(u["present"]) and int(u["defense"]) >= TOP_DEFENSE:
			rows.append(TOP_DEFENSE)
			break
	return rows


func _process(_delta: float) -> void:
	if visible and _table != null and _rows.size() > ROWS.size():
		_table.queue_redraw()  # the golden box shimmers


func _units_col_w() -> float:
	return (TABLE_W - 2.0 * COL_DEF - COL_ROLL) * 0.5


func _flow(ids: Array, width: float) -> Dictionary:
	var pos := {}
	var x := 0.0
	var y := 0.0
	var line_h := 0.0
	for id in ids:
		var s: Vector2 = _tiles[id].custom_minimum_size * TILE_SCALE
		if x > 0.0 and x + s.x > width:
			x = 0.0
			y += line_h + GAP
			line_h = 0.0
		pos[id] = Vector2(x, y)
		x += s.x + GAP
		line_h = maxf(line_h, s.y)
	return {"pos": pos, "height": y + line_h}


func _layout(animate: bool) -> void:
	var uw := _units_col_w()
	# Which units sit in which row, per side.
	_rows = _rows_now()
	var cells := {"attacker": [], "defender": []}
	for side in cells:
		for i in _rows.size():
			cells[side].append([])
	_unit_row.clear()
	for id in _model.unit_order:
		var u: Dictionary = _model.units[id]
		if not u["present"]:
			continue
		var row := _row_of(u)
		cells[u["side"]][row].append(id)
		_unit_row[id] = row

	_row_y.clear()
	_row_h.clear()
	var y := HEADER_H * 2.0
	var flows := {"attacker": [], "defender": []}
	for i in _rows.size():
		var h := ROW_MIN
		for side in cells:
			var f := _flow(cells[side][i], uw - 6.0)
			flows[side].append(f)
			h = maxf(h, float(f["height"]) + 8.0)
		_row_y.append(y)
		_row_h.append(h)
		y += h
	_table.custom_minimum_size = Vector2(TABLE_W, y + 2.0)
	# The board must fit the window: whatever the fixed parts (title, Resolve
	# rows, result text, button) leave is the chart's scroll area.
	var fixed := 250.0 + NARRATION_H + 8.0
	_scroll.custom_minimum_size = Vector2(TABLE_W + 12, minf(y + 8.0, maxf(get_viewport_rect().size.y - fixed, 200.0)))

	var left_x := COL_DEF
	var right_x := left_x + uw + COL_ROLL
	for side in cells:
		var origin_x := left_x if side == "attacker" else right_x
		for i in _rows.size():
			var f: Dictionary = flows[side][i]
			for id in cells[side][i]:
				var target := Vector2(origin_x + 3.0, float(_row_y[i]) + 4.0) + (f["pos"][id] as Vector2)
				_place(_tiles[id], target, animate)
	_table.queue_redraw()


func _place(tile: Control, target: Vector2, animate: bool) -> void:
	if not animate or not tile.visible or tile.position == Vector2.ZERO:
		tile.position = target
		return
	var tw := tile.create_tween()
	tw.tween_property(tile, "position", target, SLIDE_SECONDS).set_trans(Tween.TRANS_CUBIC).set_ease(Tween.EASE_OUT)


func _show_dice() -> void:
	for d in _dice:
		d.queue_free()
	_dice.clear()
	var roll_x := COL_DEF + _units_col_w()
	var half := COL_ROLL * 0.5
	for side in ["attacker", "defender"]:
		# The dice each unit row rolled, in row order.
		var by_row := {}
		for e in _model.last_rolls:
			var id := int(e["unit_id"])
			if str(e["side"]) == side and _unit_row.has(id):
				var row: int = _unit_row[id]
				if not by_row.has(row):
					by_row[row] = []
				by_row[row].append(e)
		if by_row.is_empty():
			continue
		var rows: Array = by_row.keys()
		rows.sort()
		var origin_x := roll_x + (0.0 if side == "attacker" else half)
		var layout := _dice_layout(rows, by_row, half)
		for r in rows:
			var block: Dictionary = layout["blocks"][r]
			var cols: int = layout["cols"]
			var sc: float = layout["scale"]
			var step_x: float = (DieView.SIZE + 2.0) * sc
			var step_y: float = DieView.TOTAL_H * sc + 2.0
			var list: Array = by_row[r]
			var used_cols := mini(cols, list.size())
			var x0: float = origin_x + (half - float(used_cols) * step_x + 2.0 * sc) * 0.5
			for i in list.size():
				var e: Dictionary = list[i]
				var die := DieView.make(str(e["die"]), int(e["roll"]), bool(e["hit"]), bool(e.get("bypass_hit", false)) if e.get("bypass_hit") != null else false,
					GameStore.display_color(str(e["owner"])))
				die.scale = Vector2.ONE * sc
				die.position = Vector2(x0 + float(i % cols) * step_x, float(block["top"]) + float(i / cols) * step_y)
				die.tooltip_text = _describe_roll(e)
				die.modulate.a = 0.0
				_table.add_child(die)
				die.create_tween().tween_property(die, "modulate:a", 1.0, 0.15)
				_dice.append(die)


## Where one side's dice go: each unit row's dice fill a grid in that row's half of
## the Roll column, centred on the row. A grid taller than its cell spills into the
## rows above and below, and neighbouring grids are pushed apart so no two dice
## overlap; if the whole set still doesn't fit the table, the dice shrink until it
## does. Returns {"scale", "cols", "blocks": {row: {"top", "height"}}}.
func _dice_layout(rows: Array, by_row: Dictionary, half: float) -> Dictionary:
	var body_top := HEADER_H * 2.0 + 2.0
	var body_bottom: float = float(_row_y.back()) + float(_row_h.back()) - 2.0
	var result := {}
	for scale in [1.0, 0.85, 0.7, 0.55, 0.45]:
		var cols := maxi(1, int((half - 8.0) / ((DieView.SIZE + 2.0) * scale)))
		var step_y: float = DieView.TOTAL_H * scale + 2.0
		var tops := []
		var heights := []
		for r in rows:
			var lines := int(ceil(float((by_row[r] as Array).size()) / float(cols)))
			var h := float(lines) * step_y - 2.0
			heights.append(h)
			tops.append(float(_row_y[r]) + (float(_row_h[r]) - h) * 0.5)
		for i in tops.size():  # push down past the grid above...
			var floor_y: float = body_top if i == 0 else float(tops[i - 1]) + float(heights[i - 1]) + 4.0
			tops[i] = maxf(float(tops[i]), floor_y)
		var last := tops.size() - 1
		tops[last] = minf(float(tops[last]), body_bottom - float(heights[last]))
		for i in range(last - 1, -1, -1):  # ...then back up if that ran off the table
			tops[i] = minf(float(tops[i]), float(tops[i + 1]) - 4.0 - float(heights[i]))
		var fits: bool = float(tops[0]) >= body_top - 0.5
		var blocks := {}
		for i in rows.size():
			blocks[rows[i]] = {"top": tops[i], "height": heights[i]}
		result = {"scale": scale, "cols": cols, "blocks": blocks}
		if fits:
			break
	return result


func _describe_roll(e: Dictionary) -> String:
	var line := "%s (%s) rolled %d" % [e["unit_type"], e["die"], int(e["roll"])]
	if not bool(e["hit"]):
		return line + " - miss"
	var t: Dictionary = _model.units[int(e["target_unit_id"])]
	return line + " - hit %s for %d%s" % [t["unit_type"], int(e["damage"]), " (bypass, half damage)" if bool(e.get("bypass_hit", false)) else ""]


# ---- drawing the chart --------------------------------------------------------

func _draw_table() -> void:
	if _model == null or _row_y.is_empty():
		return
	var t := _table
	var line := GCTheme.NAVY
	var text := GCTheme.NAVY
	var uw := _units_col_w()
	# Start of each column, then the right edge: defL | unitsL | roll | unitsR | defR
	var col_x := [0.0, COL_DEF, COL_DEF + uw, COL_DEF + uw + COL_ROLL, COL_DEF + 2.0 * uw + COL_ROLL, TABLE_W]
	var body_top := HEADER_H * 2.0
	var body_bottom: float = _row_y.back() + _row_h.back()

	# Header rows.
	t.draw_rect(Rect2(0, 0, TABLE_W, HEADER_H), _frame_colour if _frame_colour.a > 0 else GCTheme.NAVY)
	t.draw_rect(Rect2(0, HEADER_H, TABLE_W, HEADER_H), GCTheme.CREAM)
	_centered(t, "ATTACKER", Rect2(col_x[0], 0, col_x[2] - col_x[0], HEADER_H), 16, GCTheme.CREAM, true)
	_centered(t, _model.round_title().to_upper(), Rect2(col_x[2], 0, col_x[3] - col_x[2], HEADER_H), 16, GCTheme.WHITE, true)
	_centered(t, "DEFENDER", Rect2(col_x[3], 0, col_x[5] - col_x[3], HEADER_H), 16, GCTheme.CREAM, true)
	var labels := ["DEFENSE", "UNITS", "ROLL", "UNITS", "DEFENSE"]
	for i in 5:
		_centered(t, labels[i], Rect2(col_x[i], HEADER_H, col_x[i + 1] - col_x[i], HEADER_H), 13, GCTheme.GRAY, true)

	# Body: row lines and the defense numbers.
	for i in _rows.size():
		var y: float = _row_y[i]
		var h: float = _row_h[i]
		if i == 0:
			t.draw_line(Vector2(0, y), Vector2(TABLE_W, y), line, 1.0)   # under the headers, all the way across
		else:  # the row lines stop at the Roll column: the dice there spill over rows freely
			t.draw_line(Vector2(0, y), Vector2(col_x[2], y), line, 1.0)
			t.draw_line(Vector2(col_x[3], y), Vector2(TABLE_W, y), line, 1.0)
		var d := str(_rows[i])
		if int(_rows[i]) >= TOP_DEFENSE:
			_golden_defense(t, Rect2(col_x[0], y, COL_DEF, h), d)
			_golden_defense(t, Rect2(col_x[4], y, COL_DEF, h), d)
			continue
		_centered(t, d, Rect2(col_x[0], y, COL_DEF, h), 20, text, true)
		_centered(t, d, Rect2(col_x[4], y, COL_DEF, h), 20, text, true)
	for x in col_x:
		t.draw_line(Vector2(x, body_top), Vector2(x, body_bottom), line, 1.0)
	t.draw_line(Vector2(0, body_bottom), Vector2(TABLE_W, body_bottom), line, 1.0)
	t.draw_rect(Rect2(1, 1, TABLE_W - 2, body_bottom - 1), line, false, 2.0)


## The defense box of an 11 -- a rare occasion, so a shimmering golden box: a light band sweeps
## across it and the rim pulses.
func _golden_defense(t: Control, cell: Rect2, label: String) -> void:
	var box := cell.grow(-4.0)
	var now := float(Time.get_ticks_msec()) / 1000.0
	t.draw_rect(box, Color(0.86, 0.62, 0.10))
	t.draw_rect(Rect2(box.position, Vector2(box.size.x, box.size.y * 0.5)), Color(1.0, 0.82, 0.30, 0.55))
	var centre := fposmod(now / 1.8, 1.0) * (box.size.x + 40.0) - 20.0   # the sweeping band, in box x
	var step := 2.0
	var x := 0.0
	while x < box.size.x:
		var a := exp(-pow(x - centre, 2.0) / (2.0 * 9.0 * 9.0)) * 0.75
		if a > 0.02:
			t.draw_rect(Rect2(box.position.x + x, box.position.y, step, box.size.y), Color(1.0, 0.98, 0.80, a))
		x += step
	var pulse := 0.5 + 0.5 * sin(now * TAU / 1.2)
	t.draw_rect(box, Color(1.0, 0.93, 0.55, 0.65 + 0.35 * pulse), false, 3.0)
	t.draw_rect(box.grow(-3.0), Color(0.45, 0.28, 0.0, 0.55), false, 1.0)
	_centered(t, label, box, 22, Color(0.22, 0.12, 0.0), true)


func _centered(t: Control, s: String, r: Rect2, size: int, col: Color, display := false) -> void:
	var font: Font = GCTheme.font("display") if display else GCTheme.font("body")
	var w := font.get_string_size(s, HORIZONTAL_ALIGNMENT_LEFT, -1, size).x
	t.draw_string(font, Vector2(r.position.x + (r.size.x - w) * 0.5, r.position.y + r.size.y * 0.5 + size * 0.35), s, HORIZONTAL_ALIGNMENT_LEFT, -1, size, col)

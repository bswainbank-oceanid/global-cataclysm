extends SceneTree
## Headless checks for the unit sounds (Sfx): which sounds a move or a battle asks for, that every unit
## type's sounds load, and the volume setting.
##   godot --headless --path client -s res://tests/sfx_test.gd
## Exits non-zero on any failure.

var _failures := 0
var _heard: Array = []


func _check(cond: bool, what: String) -> void:
	if not cond:
		_failures += 1
		printerr("FAIL: " + what)


func _initialize() -> void:
	await process_frame
	var sfx = root.get_node("Sfx")
	var settings = root.get_node("Settings")
	var data = root.get_node("GameData")
	var stepper = root.get_node("Stepper")
	sfx.played.connect(func(t, k): _heard.append("%s/%s" % [t, k]))

	# Every unit type's sounds load; a Transport has no attack sound.
	for unit_type in data.units["units"]:
		var move: String = sfx._file(unit_type, "move")
		_check(move != "" and sfx._stream(move) != null, "%s has a move sound that loads" % unit_type)
		var attack: String = sfx._file(unit_type, "attack")
		if unit_type == "Transport":
			_check(attack == "", "a Transport has no attack sound")
		else:
			_check(attack != "" and sfx._stream(attack) != null, "%s has an attack sound that loads" % unit_type)

	# One sound per unit type, a few at most; promoted stacks are their type.
	sfx.play_units(["Infantry", "Infantry", "Armor", "Infantry*2", "Fighter", "Bomber"], "move")
	_check(_heard == ["Infantry/move", "Armor/move", "Fighter/move"], "one per type, three at most: %s" % str(_heard))
	_heard.clear()
	sfx.play_units(["Transport", "Cruiser"], "attack")
	_check(_heard == ["Cruiser/attack"], "no attack sound for a Transport: %s" % str(_heard))

	# A move phase played back: its units' move sounds. A battle fought without the board: attack sounds.
	_heard.clear()
	stepper._play_result_sounds("COMBAT_MOVE", [{"kind": "combat_move", "orders": [
		{"unit_id": 1, "unit_type": "Armor"}, {"unit_id": 2, "unit_type": "Armor"}, {"unit_id": 3, "unit_type": "Mechanized Infantry"}]}])
	_check(_heard == ["Armor/move", "Mechanized Infantry/move"], "a combat move's sounds: %s" % str(_heard))
	_heard.clear()
	stepper._play_result_sounds("RETURN_TO_BASE", [{"kind": "return_to_base", "orders": [{"unit_id": 9, "unit_type": "Fighter"}]}])
	_check(_heard == ["Fighter/move"], "aircraft flying home: %s" % str(_heard))
	_heard.clear()
	stepper._play_result_sounds("COMBAT_RESOLUTION", [
		{"kind": "battle_event", "event_kind": "SIDE_START", "side": "attacker"},
		{"kind": "battle_event", "event_kind": "UNIT_ROLL", "unit_type": "Submarine"},
		{"kind": "battle_event", "event_kind": "UNIT_ROLL", "unit_type": "Cruiser"},
		{"kind": "battle_event", "event_kind": "UNIT_ROLL", "unit_type": "Submarine"}])
	_check(_heard == ["Submarine/attack", "Cruiser/attack"], "a battle without the board: %s" % str(_heard))
	_heard.clear()
	stepper._play_result_sounds("PURCHASE", [{"kind": "purchase", "orders": [{"unit_type": "Armor"}]}])
	_check(_heard.is_empty(), "a purchase makes no sound")

	# The interface click: it loads; every button clicks as it's pressed (once per press), a unit tile doesn't.
	_check(sfx._stream(sfx.CLICK) != null, "the click sound loads")
	var button := Button.new()
	root.add_child(button)
	sfx._last_click = -100000
	button.pressed.emit()
	var first: int = sfx._last_click
	_check(first > 0, "pressing a button clicks")
	button.pressed.emit()
	_check(sfx._last_click == first, "a second press at the same moment doesn't click again")
	var tile = load("res://scripts/unit_tile.gd").make({"unit_id": 1, "unit_type": "Armor", "owner": "PAF", "current_hp": 4})
	root.add_child(tile)
	sfx._last_click = -100000
	tile.pressed.emit()
	_check(sfx._last_click == -100000, "a unit tile makes no click")

	# Queued moves: the unit types come from the move options.
	var store = root.get_node("GameStore")
	store.human_move = {"options": {5: {"unit_type": "Bomber"}, 6: {"unit_type": "Fighter"}}}
	_check(stepper.order_unit_types([{"unit_id": 5, "path": [1, 2]}, {"unit_id": 6, "destination": 3}]) == ["Bomber", "Fighter"],
		"queued orders name their unit types")
	store.human_move = {}

	# The volume setting: the SFX bus follows it, and Mute (or 0) silences it.
	var bus := AudioServer.get_bus_index("SFX")
	_check(bus >= 0, "the SFX bus exists")
	settings.sound_volume = 50
	settings.sound_muted = false
	sfx.apply_volume()
	_check(absf(AudioServer.get_bus_volume_db(bus) - linear_to_db(0.5)) < 0.01 and not AudioServer.is_bus_mute(bus), "50% volume")
	settings.sound_muted = true
	sfx.apply_volume()
	_check(AudioServer.is_bus_mute(bus), "muted")
	settings.sound_muted = false
	settings.sound_volume = 0
	sfx.apply_volume()
	_check(AudioServer.is_bus_mute(bus), "0% is silent")
	quit(1 if _failures > 0 else 0)

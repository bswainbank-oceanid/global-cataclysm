extends SceneTree
## Headless checks for the zoomed-out badge's one icon (MapUnits._flag_icon): the unit type worth the most in
## the group -- unit value (purchase cost) added up per type, 5 more per promotion -- and, during the player's
## own move phase, only the units that can still move (unless none of them can):
##   godot --headless --path client -s res://tests/map_units_icon_test.gd
## Exits non-zero on any failure.

var _failures := 0
var store  # the GameStore autoload (not a compile-time name in a -s script)


func _check(cond: bool, what: String) -> void:
	if not cond:
		_failures += 1
		printerr("FAIL: " + what)


var _next_id := 100


func _unit(unit_type: String, owner := "NAA", promotions := 0) -> Dictionary:
	_next_id += 1
	return {"unit_id": _next_id, "unit_type": unit_type, "owner": owner, "promotions": promotions}


func _board(units_by_space: Dictionary) -> void:
	var territories := {}
	for tid in units_by_space:
		territories[str(tid)] = {"units": units_by_space[tid]}
	store.state = {"territories": territories}


func _initialize() -> void:
	await process_frame  # let the autoloads (GameData loads the map data in _ready) come up
	store = root.get_node("GameStore")
	var badges = load("res://scripts/map_units.gd").new()  # (loaded once the autoloads are up)
	# Costs (GC72_UnitSet): Infantry 3, Mechanized Infantry 6, Armor 8, Cruiser 12. Manchuria (33) is land,
	# the Sea of Okhotsk (25) a sea zone.

	_board({33: [_unit("Infantry"), _unit("Infantry"), _unit("Infantry"), _unit("Armor")]})
	_check(badges._flag_icon(33, "NAA", {})[0] == "Infantry", "3 Infantry (9) outweigh 1 Armor (8)")
	_board({33: [_unit("Infantry"), _unit("Infantry"), _unit("Armor")]})
	_check(badges._flag_icon(33, "NAA", {})[0] == "Armor", "1 Armor (8) outweighs 2 Infantry (6), though fewer")

	var veteran := _unit("Infantry", "NAA", 2)
	_board({33: [veteran, _unit("Infantry"), _unit("Armor")]})
	var top: Array = badges._flag_icon(33, "NAA", {})
	_check(top[0] == "Infantry" and top[1] == 2, "two promotions add 10: Infantry 16 over Armor 8, starred 2 (%s)" % [top])

	_board({25: [_unit("Mechanized Infantry"), _unit("Mechanized Infantry"), _unit("Mechanized Infantry"), _unit("Cruiser")]})
	_check(badges._flag_icon(25, "NAA", {})[0] == root.get_node("GameData").transport_unit("Mechanized Infantry"),
		"afloat, Mechanized Infantry show as Transports, worth what they are (18 over a Cruiser's 12)")

	_check(badges._flag_icon(-1, "NAA", {"Infantry": 2, "Armor": 1})[0] == "Armor", "queued purchases: by value too")

	# The player's own move phase: only what can still move counts -- unless nothing there can.
	var armor := _unit("Armor")
	_board({33: [_unit("Infantry"), _unit("Infantry"), _unit("Infantry"), armor, _unit("Cruiser", "UE"), _unit("Infantry", "UE")]})
	store.set_human_move("NAA", {"kind": "combat", "orders": [], "options": {
		str(armor["unit_id"]): {"unit_type": "Armor", "territory_id": 33, "destinations": {"34": [33, 34]}}}})
	_check(badges._flag_icon(33, "NAA", {})[0] == "Armor", "moving: only the Armor can move, so it shows")
	_check(badges._flag_icon(33, "UE", {})[0] == "Cruiser", "another faction's group there counts everything")
	store.set_human_move("NAA", {"kind": "combat", "orders": [], "options": {}})
	_check(badges._flag_icon(33, "NAA", {})[0] == "Infantry", "moving, but nothing there can: every unit counts")
	store.set_human_move("NAA", {})

	badges.free()
	quit(1 if _failures > 0 else 0)

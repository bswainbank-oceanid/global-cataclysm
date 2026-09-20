extends SceneTree
## Headless checks for store.move_targets (which spaces highlight for the
## selected units, and the orders a drop would queue):
##   godot --headless --path client -s res://tests/move_targets_test.gd
## Exits non-zero on any failure.

var _failures := 0
var store  # the GameStore autoload (not a compile-time name in a -s script)


func _check(cond: bool, what: String) -> void:
	if not cond:
		_failures += 1
		printerr("FAIL: " + what)


## An options block like the server's: {unit_id: {unit_type, territory_id, destinations{dest: path}}}.
func _block(kind: String, options: Dictionary) -> Dictionary:
	return {"kind": kind, "options": options, "orders": []}


func _select(ids: Array) -> void:
	store.move_selected.clear()
	for id in ids:
		store.move_selected[id] = true


func _initialize() -> void:
	await process_frame  # let the autoloads (GameData loads the map data in _ready) come up
	store = root.get_node("GameStore")
	# Land units in the Sea of Okhotsk (25) are transports; a Cruiser is with them.
	# Mech 1 can land on Manchuria (33) via the Sea of Japan (45) or go to 45;
	# the Cruiser can go to 45 and to the Gulf of Kamchatka (26); a Fighter can reach
	# Manchuria and 45 directly.
	var options := {
		"1": {"unit_type": "Mechanized Infantry", "territory_id": 25, "destinations": {"33": [25, 45, 33], "45": [25, 45]}},
		"2": {"unit_type": "Cruiser", "territory_id": 25, "destinations": {"45": [25, 45], "26": [25, 26]}},
		"3": {"unit_type": "Fighter", "territory_id": 25, "destinations": {"33": [25, 33], "45": [25, 45]}},
		"4": {"unit_type": "Infantry", "territory_id": 25, "destinations": {"33": [25, 45, 33]}},
	}
	store.set_human_move("NAA", _block("combat", options))

	# Strict intersection for a non-amphibious selection.
	_select([1, 3])
	var t: Dictionary = store.move_targets()
	_check(t.has(33) and t.has(45) and t.size() == 2, "land+air: the destinations both can reach (33, 45)")
	_check(t[33]["orders"].size() == 2, "both units get an order")

	# Sea units alone: only what the sea unit reaches.
	_select([2])
	t = store.move_targets()
	_check(t.has(45) and t.has(26) and not t.has(33), "cruiser alone: sea zones only")

	# Amphibious group (land + sea): a LAND target needs only the land units, and the
	# cruiser escorts to the last sea zone of the landing path (45).
	_select([1, 2])
	t = store.move_targets()
	_check(t.has(33), "amphibious: the landing target is highlighted though a cruiser can't reach it")
	var landing: Array = t[33]["orders"]
	_check(landing.size() == 2, "landing orders: the mech and its escort (%d)" % landing.size())
	var escort: Dictionary = landing.filter(func(o): return o["unit_id"] == 2)[0]
	_check(escort["path"] == [25, 45], "the cruiser escorts to the sea zone the landing crosses last: %s" % str(escort["path"]))
	var lander: Dictionary = landing.filter(func(o): return o["unit_id"] == 1)[0]
	_check(lander["path"] == [25, 45, 33], "the mech keeps its own landing path")
	# A sea-zone target is still a strict intersection: 45 yes, 26 (cruiser only) no.
	_check(t.has(45) and not t.has(26), "amphibious to a sea zone: everyone must reach it")

	# An escort that cannot reach the landing zone stays behind (no order for it).
	options["2"]["destinations"] = {"26": [25, 26]}
	store.set_human_move("NAA", _block("combat", options))
	_select([1, 2])
	t = store.move_targets()
	_check(t.has(33) and t[33]["orders"].size() == 1, "an escort that can't reach the landing zone is left behind")

	# Air in an amphibious group must itself reach the land target.
	options["3"]["destinations"] = {"45": [25, 45]}
	store.set_human_move("NAA", _block("combat", options))
	_select([1, 2, 3])
	t = store.move_targets()
	_check(not t.has(33), "an air unit that can't reach the land target makes it non-viable")

	# Non-combat: strict intersection, and orders carry destinations.
	var nc := {
		"1": {"unit_type": "Infantry", "territory_id": 21, "destinations": [13, 14]},
		"2": {"unit_type": "Armor", "territory_id": 21, "destinations": [13]},
	}
	store.set_human_move("NAA", _block("noncombat", nc))
	_select([1, 2])
	t = store.move_targets()
	_check(t.size() == 1 and t.has(13), "non-combat: only the shared destination")
	_check(t[13]["orders"][0].has("destination") and int(t[13]["orders"][0]["destination"]) == 13, "non-combat orders carry a destination")

	# Nothing selected -> nothing highlighted; committed units drop out of the selection.
	_select([])
	_check(store.move_targets().is_empty(), "no selection, no targets")
	store.set_human_move("NAA", {})
	_check(not store.human_move_active(), "leaving the move phase clears it")

	# Fighters on a carrier ride along with it (the engine sweeps them): a selected
	# carrier's targets don't depend on where the fighter could fly, and the
	# fighter gets no order of its own.
	var fleet := {
		"5": {"unit_type": "Aircraft Carrier", "territory_id": 58, "destinations": {"57": [58, 57], "59": [58, 59]}},
		"6": {"unit_type": "Fighter", "territory_id": 58, "destinations": {"10": [58, 10], "20": [58, 20]}},
		"7": {"unit_type": "Submarine", "territory_id": 58, "destinations": {"57": [58, 57]}},
	}
	store.set_human_move("NAA", _block("noncombat", fleet))
	store.move_origin = 58
	_select([5, 6, 7])
	t = store.move_targets()
	_check(t.has(57) and not t.has(59) and not t.has(10), "carrier group: targets are what the ships reach (57)")
	_check(t[57]["orders"].size() == 2 and t[57]["count"] == 3, "the fighter rides: no order of its own, still counted (%d/%d)" % [t[57]["orders"].size(), t[57]["count"]])
	_select([5])
	t = store.move_targets()
	_check(t.has(57) and t.has(59) and t[57]["count"] == 2, "a carrier alone still takes the fighter along")
	_select([6])
	t = store.move_targets()
	_check(t.has(10) and t.has(20) and t[10]["orders"].size() == 1, "the fighter alone flies on its own")
	# A stack is limited to the range of its slowest member: only the spaces EVERY selected
	# unit can reach are targets, whatever the faster ones could do alone.
	var stack := {
		"21": {"unit_type": "Infantry", "territory_id": 56, "destinations": {"10": [56, 10]}},                       # range 1
		"22": {"unit_type": "Mechanized Infantry", "territory_id": 56, "destinations": {"10": [56, 10], "13": [56, 10, 13]}},  # range 2
		"23": {"unit_type": "Armor", "territory_id": 56, "destinations": {"10": [56, 10], "20": [56, 20]}},           # range 1, elsewhere
	}
	store.set_human_move("NAA", _block("combat", stack))
	store.move_origin = 56
	_select([22])
	t = store.move_targets()
	_check(t.has(10) and t.has(13), "the Mech alone reaches two hops out")
	_select([21, 22])
	t = store.move_targets()
	_check(t.has(10) and not t.has(13) and t.size() == 1, "with an Infantry along, only its one-hop range is left: %s" % str(t.keys()))
	_check(t[10]["orders"].size() == 2, "and both units are ordered there")
	_select([21, 22, 23])
	t = store.move_targets()
	_check(t.has(10) and t.size() == 1, "a third unit that can't reach the other spaces narrows it further: %s" % str(t.keys()))
	store.set_human_move("NAA", _block("noncombat", {
		"31": {"unit_type": "Cruiser", "territory_id": 85, "destinations": {"86": [85, 86], "87": [85, 87]}},
		"32": {"unit_type": "Submarine", "territory_id": 85, "destinations": {"86": [85, 86]}}}))
	store.move_origin = 85
	_select([31, 32])
	t = store.move_targets()
	_check(t.size() == 1 and t.has(86), "the same holds for non-combat moves: %s" % str(t.keys()))

	print("move targets test: failures=%d" % _failures)
	quit(1 if _failures > 0 else 0)

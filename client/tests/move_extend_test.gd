extends SceneTree
## Headless checks for the "extend" flow: after committing a single unit's
## one-hop combat move that didn't itself trigger a battle (a Mechanized
## Infantry pass-through capture, typically), offering it a second, explicit
## hop -- GameStore.move_extend, turn_stepper.move_commit/move_extend_commit/
## move_recall, and MapArrows.from_events' multi-segment paths.
##   godot --headless --path client -s res://tests/move_extend_test.gd
## Exits non-zero on any failure. Drives the store/stepper directly (as
## move_targets_test.gd does), not through simulated mouse drags -- Net.send_msg
## safely no-ops with no live connection, so calling Stepper's real functions
## here never touches the network, only GameStore's observable state.

var _failures := 0
var store   # the GameStore autoload
var stepper # the Stepper autoload


func _check(cond: bool, what: String) -> void:
	if not cond:
		_failures += 1
		printerr("FAIL: " + what)


## An options block like the server's, one unit with a pass-through-capturable
## first hop (2, continuing on to 3) and a plain attack (5, no continuation).
func _block() -> Dictionary:
	return {"kind": "combat", "orders": [], "options": {
		"1": {"unit_type": "Mechanized Infantry", "territory_id": 1,
			"destinations": {"2": [1, 2], "5": [1, 5]}, "continuations": {"2": {"3": [1, 2, 3]}}},
		"2": {"unit_type": "Armor", "territory_id": 1, "destinations": {"2": [1, 2]}, "continuations": {}},
	}}


func _select(ids: Array) -> void:
	store.move_selected.clear()
	for id in ids:
		store.move_selected[id] = true


func _initialize() -> void:
	await process_frame  # let the autoloads (GameData loads the map data in _ready) come up
	store = root.get_node("GameStore")
	stepper = root.get_node("Stepper")

	# set_human_move parses 'continuations' alongside 'dests'.
	store.set_human_move("NAA", _block())
	var opts: Dictionary = store.human_move["options"]
	_check(opts[1]["continuations"] == {2: {3: [1, 2, 3]}}, "continuations parsed and int-keyed: %s" % str(opts[1]["continuations"]))
	_check(opts[2]["continuations"] == {}, "a unit with no pass-through-capable hop gets an empty continuations dict")

	# Committing a single unit to a continuable first hop offers the extend.
	_select([1])
	stepper.move_commit(2)
	_check(store.move_extend_active(), "committing to 2 (pass-through-capturable) offers an extend")
	_check(int(store.move_extend["unit_id"]) == 1 and int(store.move_extend["first_hop"]) == 2,
		"the offer names the right unit and first hop")
	_check(store.move_extend_targets() == {3: [1, 2, 3]}, "the offer's targets are the continuation's own: %s" % str(store.move_extend_targets()))

	# The server's refreshed queue excludes the now-staged unit entirely (has_moved_combat) --
	# the offer must survive that refresh; it's a client-remembered affordance, not server state.
	var refreshed := _block()
	refreshed["options"].erase("1")
	store.set_human_move("NAA", refreshed)
	_check(store.move_extend_active(), "the offer survives an unrelated queue refresh")

	# Picking the second hop clears the offer.
	stepper.move_extend_commit(3)
	_check(not store.move_extend_active(), "picking the second hop clears the offer")

	# An unrecognised destination is ignored, not silently accepted.
	store.set_move_extend({"unit_id": 1, "first_hop": 2, "targets": {3: [1, 2, 3]}})
	stepper.move_extend_commit(99)
	_check(store.move_extend_active(), "extending to a destination outside the offer is a no-op")
	store.set_move_extend({})

	# Committing to a plain attack (no continuation) never offers an extend.
	store.set_human_move("NAA", _block())
	_select([1])
	stepper.move_commit(5)
	_check(not store.move_extend_active(), "an ordinary attack destination offers no extend")

	# Committing more than one unit together never offers an extend, even though
	# one of them individually would have qualified for that same destination.
	store.set_human_move("NAA", _block())
	_select([1, 2])
	stepper.move_commit(2)
	_check(not store.move_extend_active(), "a multi-unit commit never offers an extend")

	# Recalling the offered unit's own move clears the offer.
	store.set_human_move("NAA", _block())
	_select([1])
	stepper.move_commit(2)
	_check(store.move_extend_active(), "setup: the offer is up before the recall")
	stepper.move_recall([1])
	_check(not store.move_extend_active(), "recalling the extend unit's move clears the offer")

	# Recalling some OTHER unit leaves an unrelated offer alone.
	store.set_human_move("NAA", _block())
	_select([1])
	stepper.move_commit(2)
	stepper.move_recall([2])
	_check(store.move_extend_active(), "recalling a different unit doesn't touch the offer")

	# Leaving the move phase entirely clears the offer.
	store.set_human_move("NAA", {})
	_check(not store.move_extend_active(), "leaving the move phase clears the offer")

	# MapArrows.from_events: a multi-hop combat move draws one arrow segment per
	# hop, not a single one skipping straight from origin to final destination.
	# Called on the loaded script resource, not the bare "MapArrows" global class
	# identifier (as orders_panel_chip_test.gd does for OrdersPanel), so this
	# doesn't force-compile map_arrows.gd -- and its own top-level GameStore
	# reference -- before this script's autoloads are all resolved.
	var events := [{"kind": "combat_move", "faction": "NAA", "orders": [{"unit_id": 1, "from": 1, "path": [1, 2, 3]}]}]
	var arrows: Array = load("res://scripts/map_arrows.gd").from_events(events)
	var pairs := {}
	for a in arrows:
		pairs["%d-%d" % [a["from"], a["to"]]] = true
	_check(pairs.has("1-2") and pairs.has("2-3") and not pairs.has("1-3"),
		"two segments (1->2, 2->3), not one collapsed 1->3: %s" % str(pairs.keys()))

	print("move extend test: failures=%d" % _failures)
	quit(1 if _failures > 0 else 0)

extends SceneTree
## Headless check of GameStore.pending_units (the territory window's "Awaiting deployment" units): the
## purchases confirmed this turn (the state's pending_deployment) and the queued purchase's orders for
## that space, as full-strength new units, until Deploy + Income places them.
##   godot --headless --path client -s res://tests/pending_units_test.gd

var _failures := 0


func _check(cond: bool, what: String) -> void:
	if not cond:
		_failures += 1
		printerr("FAIL: " + what)


func _initialize() -> void:
	await process_frame
	var store = root.get_node("GameStore")
	var confirmed := {"unit_id": 901, "unit_type": "Armor", "owner": "PAF", "current_hp": 4, "xp": 0, "promotions": 0}
	store.state = {"territories": {"93": {"pending_deployment": [confirmed]}, "14": {"pending_deployment": []}}}
	store.queued_purchase = {}
	var units: Array = store.pending_units(93)
	_check(units.size() == 1 and int(units[0]["unit_id"]) == 901, "a confirmed purchase is shown as it is: %s" % [units])
	_check(store.pending_units(14).is_empty(), "nothing waiting: no units")

	store.queued_purchase = {"faction": "PAF", "orders": [{"unit_type": "Infantry", "qty": 2, "deploy_at": 93},
		{"unit_type": "Cruiser", "qty": 1, "deploy_at": 14}]}
	units = store.pending_units(93)
	_check(units.size() == 3, "the queued purchase's units join the confirmed one: %d" % units.size())
	var queued := units.filter(func(u): return bool(u.get("queued", false)))
	_check(queued.size() == 2, "the two queued Infantry are marked queued")
	var full_hp := int(root.get_node("GameData").unit_def("Infantry")["hp"])
	for u in queued:
		_check(str(u["unit_type"]) == "Infantry" and str(u["owner"]) == "PAF" and int(u["unit_id"]) == -1
			and int(u["current_hp"]) == full_hp, "a queued unit is a new, full-strength unit of the buyer: %s" % [u])
	_check(str(store.pending_units(14)[0]["unit_type"]) == "Cruiser", "each space gets only its own orders")

	print("pending units test: failures=%d" % _failures)
	quit(1 if _failures > 0 else 0)

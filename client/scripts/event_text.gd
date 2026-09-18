class_name EventText
extends RefCounted
## Turns turn_log events (see engine/turn_log.py) into log lines with BBCode
## faction colouring. Returns "" for events not worth a line -- the
## roll-by-roll combat detail is for the (later) playback UI, not the log.


static func _fac(code: String) -> String:
	if GameData.factions.has(code):
		var c: Color = GameData.factions[code].color.lightened(0.4)
		return "[color=#%s]%s[/color]" % [c.to_html(false), code]
	return code


static func _terr(tid) -> String:
	var id := int(tid)
	return "%s" % GameData.territories[id]["name"] if GameData.territories.has(id) else str(id)


static func describe(e: Dictionary) -> String:
	match str(e.get("kind", "")):
		"purchase":
			var parts := []
			for o in e["orders"]:
				parts.append("%dx %s" % [int(o["qty"]), o["unit_type"]])
			return "%s buys %s (%d MPC)" % [_fac(e["faction"]), ", ".join(parts) if not parts.is_empty() else "nothing", int(e["total_cost"])]
		"combat_move":
			return "%s attacks with %d unit(s)" % [_fac(e["faction"]), e["orders"].size()] if not e["orders"].is_empty() else ""
		"noncombat_move":
			return "%s repositions %d unit(s)" % [_fac(e["faction"]), e["orders"].size()] if not e["orders"].is_empty() else ""
		"battle_event":
			if e.get("event_kind") == "BATTLE_END":
				return "  battle at %s: %s" % [_terr(e["territory_id"]), str(e["outcome"]).replace("_", " ")]
			return ""
		"territory_captured":
			return "%s captures %s (from %s)" % [_fac(e["faction"]), _terr(e["territory_id"]), _fac(str(e["previous_owner"]))]
		"unit_deployed":
			return "%s deploys %dx %s to %s" % [_fac(e["faction"]), int(e["qty"]), e["unit_type"], _terr(e["territory_id"])]
		"income_collected":
			return "%s collects %d MPC" % [_fac(e["faction"]), int(e["amount"])]
		"faction_eliminated":
			return "[b]%s is eliminated![/b]" % _fac(e["faction"])
		"alliance_joined":
			return "%s and %s form an alliance" % [_fac(e["faction"]), _fac(e["target"])]
		"alliance_withdrawal":
			return "%s withdraws from its alliance" % _fac(e["faction"])
	return ""

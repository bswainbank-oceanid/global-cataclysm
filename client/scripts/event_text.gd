class_name EventText
extends RefCounted
## Turns turn_log events (see engine/turn_log.py) into log text with BBCode
## faction colouring. describe() may return several lines. Roll-by-roll
## battle events return "" -- the battle_summary event (participants and
## casualties per battle) is what the log shows; the rolls are for a later
## playback UI.


static func _fac(code: String) -> String:
	if GameData.factions.has(code):
		var c: Color = GameData.factions[code].color.lightened(0.4)
		return "[color=#%s]%s[/color]" % [c.to_html(false), code]
	return code


static func _terr(tid) -> String:
	var id := int(tid)
	if not GameData.territories.has(id):
		return str(id)
	# A link: the side panel's log/queue centres the map on it when clicked.
	return "[url=%d][color=#9fd0ff]%s[/color][/url]" % [id, GameData.territories[id]["name"]]


## [{unit_type: ...}, ...] -> "3x Armor, 2x Infantry" (most numerous first).
static func _tally(units: Array) -> String:
	var counts := {}
	for u in units:
		counts[u["unit_type"]] = int(counts.get(u["unit_type"], 0)) + 1
	var types: Array = counts.keys()
	types.sort_custom(func(a, b): return counts[a] > counts[b] if counts[a] != counts[b] else str(a) < str(b))
	var parts := []
	for t in types:
		parts.append("%dx %s" % [counts[t], t])
	return ", ".join(parts) if not parts.is_empty() else "none"


## One line per owner: "AAC: 3x Armor, 2x Infantry". Empty list -> "none".
static func _by_owner(units: Array) -> String:
	if units.is_empty():
		return "none"
	var owners := []
	for u in units:
		if not owners.has(u["owner"]):
			owners.append(u["owner"])
	var parts := []
	for o in owners:
		var mine := units.filter(func(u): return u["owner"] == o)
		parts.append("%s %s" % [_fac(str(o)), _tally(mine)])
	return "; ".join(parts)


## Move orders grouped into "3x Armor: Panama -> Costa Rica" lines.
static func _move_lines(orders: Array, dest_of: Callable) -> String:
	var groups := {}
	var order_keys := []
	for o in orders:
		var unit_type := str(o.get("unit_type", "unit"))
		var from_name := _terr(o["from"]) if o.has("from") else "?"
		var dest: String = dest_of.call(o)
		var key := "%s|%s|%s" % [unit_type, from_name, dest]
		if not groups.has(key):
			groups[key] = {"type": unit_type, "from": from_name, "to": dest, "n": 0}
			order_keys.append(key)
		groups[key]["n"] += 1
	var lines := []
	for k in order_keys:
		var g: Dictionary = groups[k]
		lines.append("  %dx %s: %s > %s" % [g["n"], g["type"], g["from"], g["to"]])
	return "\n".join(lines)


## The player's own purchase queue: each order with its price and a "-" link
## that removes one unit (SidePanel turns "dec:<unit>:<territory>" links into
## purchase_remove).
static func describe_editable_purchase(orders: Array, total: int, treasury: int) -> String:
	if orders.is_empty():
		return "[color=#7f8ea0]  (nothing bought yet: pick a territory and use + )[/color]"
	var lines := []
	for o in orders:
		lines.append("  %dx %s at %s  [color=#c9a227]%d MCP[/color]  [url=dec:%s:%d][color=#ff8a7a][b] [ - ] [/b][/color][/url]" % [
			int(o["qty"]), o["unit_type"], _terr(o["deploy_at"]), int(o["cost"]), o["unit_type"], int(o["deploy_at"])])
	lines.append("[b]Total %d of %d MCP[/b]" % [total, treasury])
	return "\n".join(lines)


static func describe(e: Dictionary) -> String:
	match str(e.get("kind", "")):
		"purchase":
			if e["orders"].is_empty():
				return "%s buys nothing" % _fac(e["faction"])
			var lines := ["%s buys (%d MCP):" % [_fac(e["faction"]), int(e["total_cost"])]]
			for o in e["orders"]:
				lines.append("  %dx %s at %s" % [int(o["qty"]), o["unit_type"], _terr(o["deploy_at"])])
			return "\n".join(lines)
		"combat_move":
			if e["orders"].is_empty():
				return ""
			return "%s attacks:\n%s" % [_fac(e["faction"]), _move_lines(e["orders"], func(o):
				var path: Array = o["path"]
				var via := ""
				if path.size() > 2:
					var mids := []
					for i in range(1, path.size() - 1):
						mids.append(_terr(path[i]))
					via = " (via %s)" % ", ".join(mids)
				return _terr(path[path.size() - 1]) + via)]
		"noncombat_move":
			if e["orders"].is_empty():
				return ""
			return "%s repositions:\n%s" % [_fac(e["faction"]), _move_lines(e["orders"], func(o): return _terr(o["destination"]))]
		"return_to_base":
			if e["orders"].is_empty():
				return ""
			return "%s aircraft return to base:
%s" % [_fac(e["faction"]), _move_lines(e["orders"], func(o): return _terr(o["to"]))]
		"battle_summary":
			var lines := ["[b]Battle at %s[/b] (%s)" % [_terr(e["territory_id"]), e["battle_type"]]]
			lines.append("  Attackers: " + _by_owner(e["attackers"]))
			lines.append("  Defenders: " + _by_owner(e["defenders"]))
			lines.append("  Attacker losses: " + _by_owner(e["eliminated_attackers"]))
			lines.append("  Defender losses: " + _by_owner(e["eliminated_defenders"]))
			lines.append("  Result: " + str(e["outcome"]).replace("_", " "))
			return "\n".join(lines)
		"battle_preview":
			return "[b]Battle at %s[/b] (%s)\n  Attackers: %s\n  Defenders: %s" % [
				_terr(e["territory_id"]), e["battle_type"], _by_owner(e["attackers"]), _by_owner(e["defenders"])]
		"alliance_plan":
			match str(e["action"]):
				"invite":
					if e.get("accepts") == null:
						return "%s invites %s" % [_fac(e["faction"]), _fac(str(e["target"]))]
					return "%s invites %s (%s)" % [_fac(e["faction"]), _fac(str(e["target"])),
						"they would accept" if e["accepts"] else "they would decline"]
				"withdraw":
					return "%s withdraws from its alliance" % _fac(e["faction"])
			return "%s takes no alliance action" % _fac(e["faction"])
		"territory_captured":
			return "%s captures %s (from %s)" % [_fac(e["faction"]), _terr(e["territory_id"]), _fac(str(e["previous_owner"]))]
		"unit_deployed":
			return "%s deploys %dx %s at %s" % [_fac(e["faction"]), int(e["qty"]), e["unit_type"], _terr(e["territory_id"])]
		"income_collected":
			return "%s collects %d MCP" % [_fac(e["faction"]), int(e["amount"])]
		"faction_eliminated":
			return "[b]%s is eliminated![/b]" % _fac(e["faction"])
		"alliance_joined":
			return "%s and %s form an alliance" % [_fac(e["faction"]), _fac(e["target"])]
		"alliance_declined":
			return "%s declines %s's invitation" % [_fac(str(e["target"])), _fac(e["faction"])]
		"alliance_withdrawal":
			return "%s withdraws from its alliance" % _fac(e["faction"])
	return ""

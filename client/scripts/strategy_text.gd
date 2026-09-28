class_name StrategyText
extends RefCounted
## The Strategy Log tab's text (reference/GC Strategy Log.odt): the strategy_turn_start,
## strategy_phase and strategy_turn_end events a strategy bot's turn logs (see
## engine/bots/strategy_log.py), as BBCode. Each choice reads
## "choice -- #objective number objective name (target)".

const PHASES := {"PURCHASE": "Purchase", "COMBAT_MOVE": "Combat Moves", "NONCOMBAT_MOVE": "Non-Combat Moves"}
const STATS := [["territory_mpc", "Territory MPC"], ["unit_value", "Unit value"], ["unit_count", "Units"], ["scs", "SCs"]]
const DIM := "#7f8ea0"
const WARN := "#ff8a7a"


static func describe(e: Dictionary) -> String:
	match str(e.get("kind", "")):
		"strategy_turn_start":
			return "[b]%s: Turn start[/b]  (%s)\n  %s" % [EventText._fac(str(e["faction"])), _style(e),
				_stats(e["stats"], e.get("change"), "since last turn")]
		"strategy_phase":
			var lines := ["%s %s:" % [EventText._fac(str(e["faction"])), PHASES.get(str(e["phase"]), str(e["phase"]))]]
			lines.append_array(_choices(str(e["phase"]), e["choices"]))
			lines.append_array(_rejected(e["rejected"], str(e["phase"])))
			return "\n".join(lines)
		"strategy_turn_end":
			var r: Dictionary = e["review"]
			var lines := ["[b]%s: End of turn[/b]  (%s)" % [EventText._fac(str(e["faction"])), _style(e)],
				"  " + _stats(e["stats"], e.get("change"), "during turn"), "  [b]Turn review[/b]"]
			for p in [["PURCHASE", "purchase"], ["COMBAT_MOVE", "combat"], ["NONCOMBAT_MOVE", "noncombat"]]:
				lines.append("  %s:" % PHASES[p[0]])
				for line in _choices(p[0], r.get(p[1], [])):
					lines.append("  " + line)
			var rejected: Array = r.get("rejected", [])
			if not rejected.is_empty():
				lines.append("  Rejected by the rules:")
				for line in _rejected(rejected, ""):
					lines.append("  " + line)
			lines.append("  Units with no orders:")
			var idle: Array = r.get("idle_units", [])
			if idle.is_empty():
				lines.append("    [color=%s]none[/color]" % DIM)
			for u in idle:
				lines.append("    %s at %s" % [_count(u["unit_type"], int(u["count"])), EventText._terr(u["at"])])
			lines.append("  Objectives given no resources:")
			var none: Array = r.get("no_resources", [])
			if none.is_empty():
				lines.append("    [color=%s]none[/color]" % DIM)
			for n in none:
				lines.append("    " + _objective(n["objective"], null) + ":")
				for why in n["reasons"]:
					var where := "" if why["target"] == null else EventText._terr(why["target"]) + ": "
					lines.append("      [color=%s]%s%s[/color]" % [DIM, where, str(why["reason"])])
				if int(n.get("more", 0)) > 0:
					lines.append("      [color=%s](and %d more)[/color]" % [DIM, int(n["more"])])
			return "\n".join(lines)
	return ""


static func _style(e: Dictionary) -> String:
	return "strategy: %s" % str(e.get("style", "?"))


## "Territory MPC 31 (+2) · Unit value 148 (-9) · ..."
static func _stats(stats: Dictionary, change, since: String) -> String:
	var bits := []
	for s in STATS:
		var v := int(stats.get(s[0], 0))
		if change == null:
			bits.append("%s %d" % [s[1], v])
		else:
			var d := int(change.get(s[0], 0))
			var col := "#8fd18f" if d > 0 else WARN if d < 0 else DIM
			bits.append("%s %d [color=%s](%s%d)[/color]" % [s[1], v, col, "+" if d >= 0 else "", d])
	return "  ·  ".join(bits) + ("" if change != null else "  [color=%s](first turn)[/color]" % DIM) + \
		("" if change == null else "  [color=%s]%s[/color]" % [DIM, since])


## "#3 Capture Strategic Centers (Rome)"; `target` null: none shown.
static func _objective(o: Dictionary, target) -> String:
	var no = o.get("no")
	var head := ("#%d " % int(no)) if no != null else ""
	var t := "" if target == null else " (%s)" % EventText._terr(target)
	return "[color=#ffd23f]%s%s[/color]%s" % [head, str(o.get("name", o.get("id", "?"))), t]


static func _count(unit_type: String, n: int) -> String:
	return "%dx %s" % [n, unit_type] if n > 1 else unit_type


static func _units(units: Array) -> String:
	var bits := []
	for u in units:
		bits.append(_count(str(u["unit_type"]), int(u["count"])))
	return ", ".join(bits)


## One line per choice: purchases grouped by where and why, moves as "units: from > to".
static func _choices(phase: String, choices: Array) -> Array:
	var lines := []
	if choices.is_empty():
		return ["  [color=%s]none[/color]" % DIM]
	if phase == "PURCHASE":
		var groups := {}
		var order := []
		for c in choices:
			var key := "%s|%s|%s" % [str(c["at"]), str(c["objective"].get("id")), str(c["target"])]
			if not groups.has(key):
				groups[key] = {"c": c, "bits": []}
				order.append(key)
			groups[key]["bits"].append(_count(str(c["unit_type"]), int(c["qty"])))
		for k in order:
			var g: Dictionary = groups[k]
			lines.append("  Buy %s at %s -- %s" % [", ".join(g["bits"]), EventText._terr(g["c"]["at"]),
				_objective(g["c"]["objective"], g["c"]["target"])])
		return lines
	for c in choices:
		if bool(c.get("hold", false)):
			lines.append("  %s hold %s -- %s" % [_units(c["units"]), EventText._terr(c["from"]), _objective(c["objective"], c["target"])])
		else:
			lines.append("  %s: %s > %s -- %s" % [_units(c["units"]), _where(c["from"]), EventText._terr(c["to"]),
				_objective(c["objective"], c["target"])])
	return lines


static func _where(tid) -> String:
	return "?" if tid == null else EventText._terr(tid)


static func _rejected(rejected: Array, phase: String) -> Array:
	var lines := []
	for r in rejected:
		var p := str(r.get("phase", phase))
		var what: String
		if r.has("unit_type"):
			what = "Buy %s at %s" % [_count(str(r["unit_type"]), int(r["qty"])), EventText._terr(r["at"])]
		else:
			what = "%s: %s > %s -- %s" % [_units(r["units"]), _where(r["from"]), EventText._terr(r["to"]), _objective(r["objective"], r["target"])]
		lines.append("  [color=%s]Rejected%s:[/color] %s" % [WARN, "" if p == phase else " (%s)" % PHASES.get(p, p), what])
	return lines

class_name BattleModel
extends RefCounted
## The state and stepping logic behind the battle board (BattlePanel), with no
## drawing in it. Built from a battle_preview (who fights, with the numbers to
## place them by) and, once the server has fought the battle, its battle_event
## stream (per-round UNIT_STATS snapshots, SIDE_STARTs, UNIT_ROLLs, NO_TARGETS,
## casualties, the end). The dice were already rolled by the engine; this only
## reveals them, chunk by chunk, as the player presses the board's button (a "pulse").
##
## One press reveals a "chunk" of rolls whose size is the Resolve setting of the
## SIDE ABOUT TO ROLL (Unit < Unit Type < Side < Round < Entire Battle). With
## Unit, Unit Type or Side every side also starts with a pause of its own: a press
## that rolls nothing and only says what that side is about to do (Round and Entire
## Battle run straight through). A unit with no legal target does not roll -- the
## engine says so with NO_TARGETS -- and the model reports it. Units that are hit
## get a HIT mark ("/", until the next press) or, at 0 HP, a DEAD mark ("X") -- an
## X'd unit still rolls if its side hasn't yet this round, just as in the engine,
## where casualties are removed only when both sides have rolled. At a round's end
## XP, promotions and HP are applied; next round the dead are gone. When the battle
## ends every unit is shown again, X'd ones marked.
##
## Every pulse also leaves a plain-words account (prev_lines) and the model can say
## what the next press will do (next_action): the board's text box and button label.

enum Resolve { ENTIRE_BATTLE, ROUND, SIDE, UNIT_TYPE, UNIT }
const RESOLVE_NAMES := ["Entire Battle", "Round", "Side", "Unit Type", "Unit"]
enum Mark { NONE, HIT, DEAD }

var territory_id := -1
var battle_type := "land"
var units := {}            # unit_id -> {unit_id, unit_type, owner, side, die, defense, damage, hp, max_hp, xp, promoted, cargo, present, mark}
var unit_order: Array = [] # unit_ids in preview order (attackers, then defenders)
var rounds: Array = []     # [{round, start: [rows], rolls: [events], end: [rows], sides: [{side, from, to, skips, skip_next}]}]
var summary: Dictionary = {}
var outcome := ""
var end_reason := ""       # why the battle ended: eliminated | no_targets | rounds
var eliminated: Dictionary = {}  # unit_id -> true, from the battle's end

var events_loaded := false
var finished := false
var round_index := -1
var roll_index := 0
var side_index := 0        # which of the current round's sides is next to roll
var beat_pending := false  # the next side's start-of-side pause hasn't been taken yet
var rolling_side := ""     # the side placed by attack die right now; "" = everyone placed by defense
var last_rolls: Array = [] # the latest pulse's UNIT_ROLL events (the dice on show)
var last_skips: Array = [] # the latest pulse's NO_TARGETS events (units that did not roll)
var prev_lines: Array = [] # what the latest pulse did, as BBCode lines
var round_label := ""
var bonus_side := ""       # who gets the first-round combat bonus ("attacker" | "defender" | "")
var bonus_reason := ""     # ...and why (amphibious landing, sea-deploy ambush, ...)
var _damaged_this_round := {}  # unit_id -> true once it has dealt damage this round (its XP is in)
var _round_closed := false


static func from_preview(preview: Dictionary) -> BattleModel:
	var m := BattleModel.new()
	m.territory_id = int(preview["territory_id"])
	m.battle_type = str(preview["battle_type"])
	var bonus: Dictionary = preview.get("round1_bonus", {})
	if bonus.get("side") != null:
		m.bonus_side = str(bonus["side"])
		m.bonus_reason = str(bonus["reason"])
	for side_key in ["attackers", "defenders"]:
		for row in preview[side_key]:
			var u: Dictionary = (row as Dictionary).duplicate()
			u["present"] = true
			u["mark"] = Mark.NONE
			m.units[int(u["unit_id"])] = u
			m.unit_order.append(int(u["unit_id"]))
	return m


## Attach the fought battle's events (the battle_event / battle_summary entries).
func load_events(events: Array) -> void:
	var cur: Dictionary = {}
	for e in events:
		var kind := str(e.get("kind", ""))
		if kind == "battle_summary":
			summary = e
			continue
		if kind != "battle_event":
			continue
		match str(e["event_kind"]):
			"UNIT_STATS":
				if str(e["stats_phase"]) == "start":
					cur = {"round": int(e["round_number"]), "start": e["units"], "rolls": [], "end": [], "sides": []}
					rounds.append(cur)
				elif not cur.is_empty():
					cur["end"] = e["units"]
			"SIDE_START":
				if not cur.is_empty():
					_ensure_side(cur, str(e["side"]))
			"NO_TARGETS":
				if not cur.is_empty():
					var sd := _ensure_side(cur, str(e["side"]))
					sd["skips"].append({"pos": cur["rolls"].size(), "unit_id": int(e["unit_id"]), "unit_type": str(e["unit_type"]),
						"owner": str(e.get("owner", "")), "side": str(e["side"])})
			"UNIT_ROLL":
				if not cur.is_empty():
					_ensure_side(cur, str(e["side"]))
					cur["rolls"].append(e)
			"BATTLE_END":
				outcome = str(e["outcome"])
				end_reason = str(e.get("end_reason", ""))
				for uid in e["eliminated_attacker_ids"] + e["eliminated_defender_ids"]:
					eliminated[int(uid)] = true
	for r in rounds:
		var sides: Array = r["sides"]
		for i in sides.size():
			sides[i]["to"] = int(sides[i + 1]["from"]) if i + 1 < sides.size() else r["rolls"].size()
	events_loaded = true


## The round's current side if it is `side` (a SIDE_START, or the first roll of a
## recording that has none), else a new one starting at the next roll.
func _ensure_side(cur: Dictionary, side: String) -> Dictionary:
	var sides: Array = cur["sides"]
	if sides.is_empty() or str(sides.back()["side"]) != side:
		sides.append({"side": side, "from": cur["rolls"].size(), "to": 0, "skips": [], "skip_next": 0})
	return sides.back()


func unit_ids_on_side(side: String) -> Array:
	return unit_order.filter(func(id): return units[id]["side"] == side)


## The battle's factions per side, e.g. {"attacker": ["NAA"], "defender": ["GPC"]}.
func factions_by_side() -> Dictionary:
	var out := {"attacker": [], "defender": []}
	for id in unit_order:
		var u: Dictionary = units[id]
		if not out[u["side"]].has(u["owner"]):
			out[u["side"]].append(u["owner"])
	return out


func round_title() -> String:
	if finished:
		return "Battle over"
	if round_index < 0:
		return "Ready" + (" - %s in round 1" % _bonus_text() if bonus_side != "" else "")
	if bonus_side != "" and int(rounds[round_index]["round"]) == 1:
		return "%s - %s" % [round_label, _bonus_text()]
	return round_label


## "Defender (GPC) bonus: amphibious landing"
func _bonus_text() -> String:
	var who: Array = factions_by_side()[bonus_side]
	return "%s (%s) bonus: %s" % [bonus_side.capitalize(), " / ".join(who), bonus_reason]


# ---- the press ---------------------------------------------------------------------

## True for the Resolve modes that stop at the start of every side.
static func _fine(mode: int) -> bool:
	return mode == Resolve.UNIT or mode == Resolve.UNIT_TYPE or mode == Resolve.SIDE


## One press of the board's button. `resolve` = {"attacker": Resolve, "defender": Resolve}.
func press(resolve: Dictionary) -> void:
	if finished or not events_loaded:
		return
	# HIT marks and the dice last shown belong to the previous press.
	for id in unit_order:
		if units[id]["mark"] == Mark.HIT:
			units[id]["mark"] = Mark.NONE
	last_rolls = []
	last_skips = []
	prev_lines = []
	var new_round := false
	while true:
		# Make sure we're inside a round that still has sides to roll.
		if round_index < 0 or side_index >= rounds[round_index]["sides"].size():
			if round_index >= 0:
				_end_round()
			if round_index + 1 >= rounds.size():
				_finish()
				return
			_start_next_round()
			new_round = true
			continue
		var r: Dictionary = rounds[round_index]
		var sd: Dictionary = r["sides"][side_index]
		var mode: int = resolve[str(sd["side"])]
		if beat_pending:
			beat_pending = false
			if _fine(mode):
				_start_side_pulse(r, sd, new_round)
				return
		if int(sd["from"]) == int(sd["to"]):  # nothing to roll: only units without a target
			_reveal_skips(sd, 1 << 30)
			_say_chunk([], last_skips)
			side_index += 1
			beat_pending = true
			continue
		var stop := _chunk_end(r, roll_index, mode)
		if _fine(mode):
			stop = mini(stop, int(sd["to"]))
		rolling_side = str(sd["side"]) if _single_side(r, roll_index, stop) else ""
		var chunk: Array = r["rolls"].slice(roll_index, stop)
		for i in range(roll_index, stop):
			_apply_roll(r["rolls"][i])
		# The units passed over along the way (all of a side's once the chunk reaches its end).
		var k := side_index
		while k < r["sides"].size() and int(r["sides"][k]["from"]) <= stop:
			var whole: bool = stop >= int(r["sides"][k]["to"])
			_reveal_skips(r["sides"][k], (1 << 30) if whole else stop)
			if not whole:
				break
			k += 1
		_say_chunk(chunk, last_skips)
		roll_index = stop
		# Move on: past every side a coarse chunk ran through, or past the one a fine chunk finished.
		if not _fine(mode) and roll_index >= r["rolls"].size():
			side_index = r["sides"].size()
		elif roll_index >= int(sd["to"]):
			side_index += 1
			beat_pending = true
		if side_index >= r["sides"].size():
			_end_round()
		if mode != Resolve.ENTIRE_BATTLE:
			if side_index >= r["sides"].size() and round_index + 1 >= rounds.size():
				_finish()  # that was the battle's very last roll
			return
		# Entire Battle: keep going until the end.


func _start_next_round() -> void:
	round_index += 1
	var r: Dictionary = rounds[round_index]
	roll_index = 0
	side_index = 0
	beat_pending = round_index > 0  # the very first side is "Ready", which is its own pause
	last_rolls = []  # with Entire Battle only the final round's dice stay on show
	last_skips = []
	_damaged_this_round = {}
	for id in unit_order:
		if units[id]["mark"] == Mark.HIT:
			units[id]["mark"] = Mark.NONE
	_round_closed = false
	round_label = "Air Superiority" if int(r["round"]) == 0 else "Round %d" % int(r["round"])
	var fighting := {}
	for row in r["start"]:
		var u: Dictionary = units[int(row["unit_id"])]
		for k in ["die", "defense", "damage", "hp", "max_hp", "xp", "promoted", "promotions", "cargo"]:
			u[k] = row[k]
		fighting[int(row["unit_id"])] = true
	for id in unit_order:
		units[id]["present"] = fighting.has(id)
	rolling_side = ""


func _end_round() -> void:
	if _round_closed:
		return
	_round_closed = true
	var r: Dictionary = rounds[round_index]
	var lost := {"attacker": [], "defender": []}
	var promoted := []
	for row in r["end"]:
		var u: Dictionary = units[int(row["unit_id"])]
		var was_ranks: int = int(u.get("promotions", 0))
		for k in ["hp", "max_hp", "xp", "promoted", "promotions"]:
			u[k] = row[k]
		if int(u["promotions"]) > was_ranks and not bool(u["cargo"]):
			# +1 defense per new promotion at once, so the unit moves up a row on the
			# board now; the next round's start snapshot carries the same value (cap 10).
			u["defense"] = mini(int(u["defense"]) + int(u["promotions"]) - was_ranks, 10)
			promoted.append(u)
		if int(row["hp"]) <= 0:
			u["mark"] = Mark.DEAD
			lost[u["side"]].append(u)
	var bits := []
	for side in ["attacker", "defender"]:
		if not lost[side].is_empty():
			bits.append("%s lost %s" % [_side_name(side), _tally(lost[side])])
	var line := "[b]%s ends.[/b] " % round_label
	line += ("; ".join(bits) + ".") if not bits.is_empty() else "Nobody was destroyed."
	if not promoted.is_empty():
		var ranked := []
		for u in promoted:
			ranked.append("%s (%s)" % [u["unit_type"], "first promotion" if int(u["promotions"]) == 1 else "promotion %d" % int(u["promotions"])])
		line += " Promoted: %s." % ", ".join(ranked)
	prev_lines.append(line)


func _finish() -> void:
	finished = true
	rolling_side = ""
	for id in unit_order:
		var u: Dictionary = units[id]
		u["present"] = true  # bring every unit back
		if eliminated.has(id):
			u["mark"] = Mark.DEAD
		elif u["mark"] == Mark.HIT:
			u["mark"] = Mark.NONE
	prev_lines.append("[b]The battle is over:[/b] " + end_text())


## Why the battle ended, in words.
func end_text() -> String:
	match end_reason:
		"no_targets":
			return "neither side has a legal target left, so nothing more can happen."
		"rounds":
			return "the round limit was reached with both sides still standing."
	match outcome:
		"attacker_eliminated":
			return "the attacker was wiped out."
		"defender_eliminated":
			return "the defender was wiped out."
		"mutual_elimination":
			return "both sides were wiped out."
	return "the fighting is over."


func _apply_roll(e: Dictionary) -> void:
	last_rolls.append(e)
	if bool(e.get("hit", false)) and e.get("target_unit_id") != null:
		var t: Dictionary = units[int(e["target_unit_id"])]
		t["hp"] = maxi(int(e["target_hp_after"]), 0)
		t["mark"] = Mark.DEAD if int(e["target_hp_after"]) <= 0 else Mark.HIT
		# XP shows the moment it is earned (promotion still waits for the round's
		# end): +1 for a unit's first damage this round, and +1 more for the
		# killing blow on a promoted unit. The round-end snapshot then settles it.
		var hitter: Dictionary = units[int(e["unit_id"])]
		if not _damaged_this_round.has(hitter["unit_id"]):
			_damaged_this_round[hitter["unit_id"]] = true
			hitter["xp"] = int(hitter["xp"]) + 1
		if int(e["target_hp_after"]) <= 0 and bool(t["promoted"]) and not bool(t["cargo"]):
			hitter["xp"] = int(hitter["xp"]) + 1


## Reveal the units of `sd` that had no legal target and sit before roll position `before`.
func _reveal_skips(sd: Dictionary, before: int) -> void:
	var skips: Array = sd["skips"]
	while int(sd["skip_next"]) < skips.size() and int(skips[int(sd["skip_next"])]["pos"]) < before:
		last_skips.append(skips[int(sd["skip_next"])])
		sd["skip_next"] = int(sd["skip_next"]) + 1


## Exclusive end index of the chunk starting at `i` for this Resolve mode.
func _chunk_end(r: Dictionary, i: int, mode: int) -> int:
	var rolls: Array = r["rolls"]
	var n := rolls.size()
	var j := i + 1
	match mode:
		Resolve.UNIT:
			return j
		Resolve.UNIT_TYPE:
			while j < n and rolls[j]["side"] == rolls[i]["side"] and rolls[j]["unit_type"] == rolls[i]["unit_type"]:
				j += 1
			return j
		Resolve.SIDE:
			while j < n and rolls[j]["side"] == rolls[i]["side"]:
				j += 1
			return j
		_:
			return n


func _single_side(r: Dictionary, from: int, to: int) -> bool:
	for k in range(from, to):
		if r["rolls"][k]["side"] != r["rolls"][from]["side"]:
			return false
	return true


# ---- words -------------------------------------------------------------------------

## "Attacker (NAA)"
func _side_name(side: String) -> String:
	return "%s (%s)" % [side.capitalize(), " / ".join(factions_by_side()[side])]


static func _plural(unit_type: String, n: int) -> String:
	if n == 1 or ["Infantry", "Mechanized Infantry", "Armor"].has(unit_type):
		return unit_type
	return unit_type + "s"


## "2 Cruisers, 1 Submarine" for unit rows / roll events (anything with a "unit_type").
static func _tally(rows: Array) -> String:
	var counts := {}
	var order := []
	for u in rows:
		var t := str(u["unit_type"])
		if not counts.has(t):
			counts[t] = 0
			order.append(t)
		counts[t] += 1
	var parts := []
	for t in order:
		parts.append("%d %s" % [counts[t], _plural(t, counts[t])])
	return ", ".join(parts)


## "2 Infantry (D6), 1 Cruiser (D8)" for the rolls of a slice, in rolling order.
func _dice_tally(rolls: Array) -> String:
	var parts := []
	var i := 0
	while i < rolls.size():
		var j := i
		while j < rolls.size() and rolls[j]["unit_type"] == rolls[i]["unit_type"] and rolls[j]["die"] == rolls[i]["die"]:
			j += 1
		parts.append("%d %s (%s)" % [j - i, _plural(str(rolls[i]["unit_type"]), j - i), rolls[i]["die"]])
		i = j
	return ", ".join(parts)


func _skips_text(skips: Array) -> String:
	if skips.is_empty():
		return ""
	return "%s: no legal target, so they do not roll." % _tally(skips)


func _say(line: String) -> void:
	if line != "":
		prev_lines.append(line)


## What a pulse that revealed `chunk` (rolls) and `skips` did, one line per side.
func _say_chunk(chunk: Array, skips: Array) -> void:
	if not skips.is_empty():
		var by_side := {}
		for sk in skips:
			if not by_side.has(sk["side"]):
				by_side[sk["side"]] = []
			by_side[sk["side"]].append(sk)
		for side in by_side:
			_say("[b]%s[/b] %s" % [_side_name(str(side)), _skips_text(by_side[side])])
	var i := 0
	while i < chunk.size():
		var j := i
		while j < chunk.size() and chunk[j]["side"] == chunk[i]["side"]:
			j += 1
		_say_side_rolls(chunk.slice(i, j))
		i = j


func _say_side_rolls(rolls: Array) -> void:
	var rolled := []
	for e in rolls:
		rolled.append("%d%s" % [int(e["roll"]), " (hit)" if bool(e["hit"]) else ""])
	var line := "[b]%s[/b] rolled %s: %s." % [_side_name(str(rolls[0]["side"])), _dice_tally(rolls), ", ".join(rolled)]
	# What the hits did, per unit hit.
	var hurt := {}
	var hurt_order := []
	for e in rolls:
		if bool(e["hit"]) and e.get("target_unit_id") != null:
			var tid := int(e["target_unit_id"])
			if not hurt.has(tid):
				hurt_order.append(tid)
			hurt[tid] = int(e["target_hp_after"])
	if hurt_order.is_empty():
		line += " No hits."
	else:
		var bits := []
		for tid in hurt_order:
			var t: Dictionary = units[tid]
			bits.append("%s %s" % [t["unit_type"], "destroyed" if hurt[tid] <= 0 else "hurt (%d HP left)" % hurt[tid]])
		line += " Hits: " + ", ".join(bits) + "."
	prev_lines.append(line)


## The start-of-side pause: rolls nothing, says what the side is about to do.
func _start_side_pulse(r: Dictionary, sd: Dictionary, new_round: bool) -> void:
	rolling_side = ""
	var head := ""
	if new_round:
		head = "[b]%s begins.[/b] " % round_label
	prev_lines.append(head + "%s is up." % _side_name(str(sd["side"])))
	if int(sd["from"]) == int(sd["to"]):
		# Nobody on this side can roll: say so now and move on.
		_reveal_skips(sd, 1 << 30)
		_say_chunk([], last_skips)
		side_index += 1
		beat_pending = true


## What the next press will do: {"label": button text, "text": BBCode for the text box}.
func next_action(resolve: Dictionary) -> Dictionary:
	if finished:
		return {"label": "End Battle", "text": ""}
	if not events_loaded:
		return _first_action(resolve)
	# Find where the next press lands.
	var r: Dictionary
	var sd: Dictionary
	var new_round := false
	var beat := false
	if round_index >= 0 and side_index < rounds[round_index]["sides"].size():
		r = rounds[round_index]
		sd = r["sides"][side_index]
		beat = beat_pending
	else:
		var ri := round_index + 1
		if ri >= rounds.size():
			return {"label": "End Battle", "text": "Neither side has a legal target: the battle ends."}
		r = rounds[ri]
		if r["sides"].is_empty():
			return {"label": "Continue", "text": "Nobody rolls in %s." % _round_name(r)}
		sd = r["sides"][0]
		new_round = true
		beat = ri > 0
	var side := str(sd["side"])
	var mode: int = resolve[side]
	var start := int(sd["from"]) if new_round else roll_index
	if beat and _fine(mode):
		var label := "Start %s" % _round_name(r) if new_round else "Start %s's side" % side.capitalize()
		return {"label": label, "text": "Next: %s%s is up. %s" % [
			"%s begins. " % _round_name(r) if new_round else "", _side_name(side), _plan_text(r, sd, int(sd["from"]), int(sd["to"]))]}
	var rolls: Array = r["rolls"]
	var stop := _chunk_end(r, start, mode) if start < rolls.size() else start
	if _fine(mode):
		stop = mini(stop, int(sd["to"]))
	if start >= rolls.size() or int(sd["from"]) == int(sd["to"]):
		return {"label": "Continue", "text": "Next: %s" % _plan_text(r, sd, start, start)}
	var first: Dictionary = rolls[start]
	var label: String
	match mode:
		Resolve.UNIT:
			label = "Roll %s's %s" % [side.capitalize(), first["unit_type"]]
		Resolve.UNIT_TYPE:
			label = "Roll %s's %s" % [side.capitalize(), _plural(str(first["unit_type"]), 2)]
		Resolve.SIDE:
			label = "Roll %s's side" % side.capitalize()
		Resolve.ROUND:
			label = "Roll %s" % _round_name(r)
		_:
			label = "Roll to the end of the battle"
	var text := "Next: "
	match mode:
		Resolve.ROUND:
			text += "everything left in %s: %s" % [_round_name(r), _plan_text(r, sd, start, rolls.size())]
		Resolve.ENTIRE_BATTLE:
			text += "the rest of the battle, up to %d more round(s) if both sides survive." % (rounds.size() - maxi(round_index, 0))
		_:
			text += "%s rolls %s" % [_side_name(side), _plan_text(r, sd, start, stop)]
	return {"label": label, "text": text}


func _round_name(r: Dictionary) -> String:
	return "Air Superiority" if int(r["round"]) == 0 else "Round %d" % int(r["round"])


## "2 Infantry (D6), 1 Cruiser (D8)", plus who will be passed over for lack of a target.
func _plan_text(r: Dictionary, sd: Dictionary, from: int, to: int) -> String:
	var text := ""
	if to > from:
		text = _dice_tally(r["rolls"].slice(from, to)) + "."
	var pending_skips := []
	for sk in sd["skips"].slice(int(sd["skip_next"])):
		if int(sk["pos"]) <= to:
			pending_skips.append(sk)
	if not pending_skips.is_empty():
		text += " %s have no legal target and will not roll." % _tally(pending_skips)
	if text == "":
		text = "Nothing to roll."
	return text


## Before the dice have been fetched only the preview is known.
func _first_action(resolve: Dictionary) -> Dictionary:
	var f := factions_by_side()
	var label := ""
	match int(resolve["attacker"]):
		Resolve.UNIT:
			label = "Roll the first unit"
		Resolve.UNIT_TYPE:
			label = "Roll the first unit type"
		Resolve.SIDE:
			label = "Roll Attacker's side"
		Resolve.ROUND:
			label = "Roll the first round"
		_:
			label = "Roll to the end of the battle"
	var text := "The battle is ready: %d attacking and %d defending units. The attacker (%s) rolls first, then the defender (%s), for up to three rounds (preceded by an air superiority round when both sides have aircraft and a Fighter is among them)." % [
		unit_ids_on_side("attacker").size(), unit_ids_on_side("defender").size(),
		" / ".join(f["attacker"]), " / ".join(f["defender"])]
	if bonus_side != "":
		text += " " + _bonus_text() + " (round 1)."
	return {"label": label, "text": text}

class_name BattleModel
extends RefCounted
## The state and stepping logic behind the battle board (BattlePanel), with no
## drawing in it. Built from a battle_preview (who fights, with the numbers to
## place them by) and, once the server has fought the battle, its battle_event
## stream (per-round UNIT_STATS snapshots, UNIT_ROLLs, casualties, the end).
## The dice were already rolled by the engine; this only reveals them, chunk by
## chunk, as the player presses Next Roll.
##
## One press reveals a "chunk" of rolls whose size is the Resolve setting of the
## SIDE ABOUT TO ROLL (Unit < Unit Type < Side < Round < Entire Battle). Units
## that are hit get a HIT mark ("/", until the next press) or, at 0 HP, a DEAD
## mark ("X") -- an X'd unit still rolls if its side hasn't yet this round, just
## as in the engine, where casualties are removed only when both sides have
## rolled. At a round's end XP, promotions and HP are applied; next round the
## dead are gone. When the battle ends every unit is shown again, X'd ones
## marked, with the result.

enum Resolve { ENTIRE_BATTLE, ROUND, SIDE, UNIT_TYPE, UNIT }
const RESOLVE_NAMES := ["Entire Battle", "Round", "Side", "Unit Type", "Unit"]
enum Mark { NONE, HIT, DEAD }

var territory_id := -1
var battle_type := "land"
var units := {}            # unit_id -> {unit_id, unit_type, owner, side, die, defense, damage, hp, max_hp, xp, promoted, cargo, present, mark}
var unit_order: Array = [] # unit_ids in preview order (attackers, then defenders)
var rounds: Array = []     # [{round, start: [rows], rolls: [events], end: [rows]}]
var summary: Dictionary = {}
var outcome := ""
var eliminated: Dictionary = {}  # unit_id -> true, from the battle's end

var events_loaded := false
var finished := false
var round_index := -1
var roll_index := 0
var rolling_side := ""     # the side placed by attack die right now; "" = everyone placed by defense
var last_rolls: Array = [] # the latest chunk's UNIT_ROLL events (the dice on show)
var round_label := ""


static func from_preview(preview: Dictionary) -> BattleModel:
	var m := BattleModel.new()
	m.territory_id = int(preview["territory_id"])
	m.battle_type = str(preview["battle_type"])
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
					cur = {"round": int(e["round_number"]), "start": e["units"], "rolls": [], "end": []}
					rounds.append(cur)
				elif not cur.is_empty():
					cur["end"] = e["units"]
			"UNIT_ROLL":
				if not cur.is_empty():
					cur["rolls"].append(e)
			"BATTLE_END":
				outcome = str(e["outcome"])
				for uid in e["eliminated_attacker_ids"] + e["eliminated_defender_ids"]:
					eliminated[int(uid)] = true
	events_loaded = true


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
		return "Ready"
	return round_label


## One press of Next Roll. `resolve` = {"attacker": Resolve, "defender": Resolve}.
func press(resolve: Dictionary) -> void:
	if finished or not events_loaded:
		return
	# HIT marks and the dice last shown belong to the previous press.
	for id in unit_order:
		if units[id]["mark"] == Mark.HIT:
			units[id]["mark"] = Mark.NONE
	last_rolls = []
	while true:
		# Make sure we're inside a round that still has rolls to reveal.
		if round_index < 0 or roll_index >= rounds[round_index]["rolls"].size():
			if round_index >= 0:
				_end_round()
			if round_index + 1 >= rounds.size():
				_finish()
				return
			_start_next_round()
			continue
		var r: Dictionary = rounds[round_index]
		var side := str(r["rolls"][roll_index]["side"])
		var mode: int = resolve[side]
		var stop := _chunk_end(r, roll_index, mode)
		rolling_side = side if _single_side(r, roll_index, stop) else ""
		for i in range(roll_index, stop):
			_apply_roll(r["rolls"][i])
		roll_index = stop
		if roll_index >= r["rolls"].size():
			_end_round()
		if mode != Resolve.ENTIRE_BATTLE:
			if roll_index >= r["rolls"].size() and round_index + 1 >= rounds.size():
				_finish()  # that was the battle's very last roll
			return
		# Entire Battle: keep going until the end.


var _round_closed := false


func _start_next_round() -> void:
	round_index += 1
	var r: Dictionary = rounds[round_index]
	roll_index = 0
	last_rolls = []  # with Entire Battle only the final round's dice stay on show
	for id in unit_order:
		if units[id]["mark"] == Mark.HIT:
			units[id]["mark"] = Mark.NONE
	_round_closed = false
	round_label = "Air Superiority" if int(r["round"]) == 0 else "Round %d" % int(r["round"])
	var fighting := {}
	for row in r["start"]:
		var u: Dictionary = units[int(row["unit_id"])]
		for k in ["die", "defense", "damage", "hp", "max_hp", "xp", "promoted", "cargo"]:
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
	for row in r["end"]:
		var u: Dictionary = units[int(row["unit_id"])]
		for k in ["hp", "max_hp", "xp", "promoted"]:
			u[k] = row[k]
		if int(row["hp"]) <= 0:
			u["mark"] = Mark.DEAD


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


func _apply_roll(e: Dictionary) -> void:
	last_rolls.append(e)
	if bool(e.get("hit", false)) and e.get("target_unit_id") != null:
		var t: Dictionary = units[int(e["target_unit_id"])]
		t["hp"] = maxi(int(e["target_hp_after"]), 0)
		t["mark"] = Mark.DEAD if int(e["target_hp_after"]) <= 0 else Mark.HIT


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

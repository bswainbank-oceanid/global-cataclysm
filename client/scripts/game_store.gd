extends Node
## The client's copy of live game state (autoload "GameStore"). The single
## seam between the network layer and everything that draws: views read
## from here and listen to `state_changed`, and never talk to the socket.
## Until a server connects (step 6 of the map/HUD milestone), ownership
## falls back to the static starting owners in territories.json.

signal state_changed
signal alliance_changed  # the human player's Alliances options or a pending invitation changed
signal move_changed  # the human player's move queue, options or selection changed
signal purchase_changed  # the human player's purchase queue/options changed

var human_move := {}      # the human's Combat/Non-Combat Move in progress: {kind, faction, options{uid: {unit_type, origin, dests{dest: path}}}, orders[{unit_id, unit_type, from, dest, path?}]}
var tile_drag_armed := false  # a selected unit tile was pressed: a move drag may follow (see main.gd)
var move_origin := -1     # the space whose units are being picked to move
var move_selected := {}   # unit_id -> true: the units picked (all uncommitted ones by default)
var _unit_index := {}     # unit_id -> unit dict, rebuilt with every state
var human_alliance := {}  # the human's Alliances phase: {faction, members, options{eligible_invite_targets, can_withdraw}, staged{action, target?}, game_would_end}
var invitation := {}      # a bot's invitation awaiting the human's answer: {from, to, members, answered, accepts}
var human_purchase := {}  # the human's Purchase phase in progress: {faction, treasury, total_cost, targets{tid: {remaining, next_sc, sources}}, orders[], contested[]}
var queued_purchase := {}  # the purchase event awaiting execution, {} if none
var queued_attack := {}    # the combat_move event awaiting execution, {} if none
var state := {}  # last full GameState.to_dict() from the server, {} until one arrives


func set_state(new_state: Dictionary) -> void:
	state = new_state
	_unit_index.clear()
	for tid in state.get("territories", {}):
		for u in state["territories"][tid]["units"]:
			_unit_index[int(u["unit_id"])] = u
	state_changed.emit()


## The server's Alliances options for the human's Alliances phase ({} = not in one).
func set_human_alliance(faction: String, block: Dictionary) -> void:
	if block.is_empty() or str(block.get("kind", "")) != "alliance":
		if not human_alliance.is_empty():
			human_alliance = {}
			alliance_changed.emit()
		return
	human_alliance = {
		"faction": faction, "members": block["members"], "options": block["options"],
		"staged": block["staged"], "game_would_end": bool(block["game_would_end"]),
	}
	alliance_changed.emit()


## A bot's invitation to a player, from the phase queue ({} = none pending).
func set_invitation(inv: Dictionary) -> void:
	if inv == invitation:
		return
	invitation = inv
	alliance_changed.emit()


## This game's alliance rules (chosen on the launch screen), from the server's state.
func can_withdraw_from_alliances() -> bool:
	return bool(state.get("can_withdraw_from_alliances", true))


func can_rejoin_alliances() -> bool:
	return bool(state.get("can_rejoin_alliances", false))


## Active factions the player cannot invite right now, with why: [[code, reason], ...].
## (The server's eligible list is authoritative; this only explains the gaps.)
func uninvitable_reasons(me: String, members: Array, eligible: Array) -> Array:
	var out := []
	var mine: Array = members
	for code in active_factions():
		var c := str(code)
		if c == me or mine.has(c) or eligible.has(c):
			continue
		var f := faction_state(c)
		if f.get("alliance") != null:
			out.append([c, "already in an alliance"])
		elif not can_rejoin_alliances():
			var banned := []
			for m in f.get("former_allies", []):
				if mine.has(str(m)):
					banned.append(str(m))
			if not banned.is_empty():
				out.append([c, "left an alliance with %s (rejoining is off)" % ", ".join(banned)])
	return out


func human_alliance_active() -> bool:
	return not human_alliance.is_empty()


## True while a player must still answer an invitation before the phase can run.
func invitation_pending() -> bool:
	return not invitation.is_empty() and not bool(invitation.get("answered", false)) and is_player(str(invitation["to"]))


## Forget everything about the game in progress (a new game is starting).
func reset() -> void:
	state = {}
	_unit_index.clear()
	human_purchase = {}
	human_move = {}
	human_alliance = {}
	invitation = {}
	move_origin = -1
	move_selected.clear()
	queued_purchase = {}
	queued_attack = {}
	tile_drag_armed = false
	state_changed.emit()
	purchase_changed.emit()
	move_changed.emit()
	alliance_changed.emit()


## A unit's dict (as the server sent it) by id, {} if there is none.
func unit_of(unit_id: int) -> Dictionary:
	return _unit_index.get(unit_id, {})


## Neutral powers take no turns; their land is shown in cream rather than the
## faction colour. (Mode is only known once a server state has arrived.)
func is_neutral(code: String) -> bool:
	return faction_state(code).get("mode", "") == "NEUTRAL"


## A "player" faction is one in HUMAN mode; in a bot-vs-bot game there is none.
func is_player(code: String) -> bool:
	return faction_state(code).get("mode", "") == "HUMAN"


func has_player() -> bool:
	for code in state.get("factions", {}):
		if is_player(code):
			return true
	return false


const NEUTRAL_COLOR := Color(0.93, 0.87, 0.68)


## The colour a faction's land and markers are drawn in.
func display_color(code: String) -> Color:
	return NEUTRAL_COLOR if is_neutral(code) else GameData.factions[code].color


func owner_of(tid: int) -> String:
	if not state.is_empty():
		var t = state["territories"].get(str(tid))
		return "" if t == null or t["owner"] == null else str(t["owner"])
	var s = GameData.territories[tid]
	return str(s.get("faction", ""))


## Deployed units in a space grouped for display: owner -> {unit_type: count}.
## Excludes units still in pending_deployment (bought, not yet on the board).
## Suffix on a stacks() key that marks the promoted version of a unit type.
const PROMOTED_SUFFIX := "|P"


## The units in a space as {owner: {stack key: count}}. A stack key is the unit
## type, plus PROMOTED_SUFFIX for promoted units -- promoted and unpromoted
## units of a type are separate stacks.
func stacks(tid: int) -> Dictionary:
	var out := {}
	for u in units_at(tid):
		var by_type: Dictionary = out.get(u["owner"], {})
		# A land unit at sea is in Transport form: it shows as a Transport.
		var key: String = "Transport" if in_transport_form(tid, u) else u["unit_type"] + (PROMOTED_SUFFIX if u.get("promoted", false) else "")
		by_type[key] = by_type.get(key, 0) + 1
		out[u["owner"]] = by_type
	return out


## True for a Land-category unit sitting in a sea zone: there it is a Transport
## (rules.json water_movement_bonus_rule), one transport per unit.
func in_transport_form(tid: int, unit: Dictionary) -> bool:
	return GameData.territories[tid]["type"] == "sea" and GameData.units["units"][unit["unit_type"]]["category"] == "Land"


## Every unit in a space, as the server's unit dicts (unit_id, unit_type,
## owner, current_hp, xp, promoted, ...).
func units_at(tid: int) -> Array:
	if state.is_empty():
		return []
	var t = state["territories"].get(str(tid))
	return [] if t == null else t["units"]


static func base_type(key: String) -> String:
	return key.trim_suffix(PROMOTED_SUFFIX)


static func is_promoted(key: String) -> bool:
	return key.ends_with(PROMOTED_SUFFIX)


## Purchases waiting to be deployed, {owner: {unit_type: count}}: those already
## confirmed (the state's pending_deployment) plus the purchase currently
## queued for execution. Shown on the map as a second, darker box.
func pending(tid: int) -> Dictionary:
	var out := {}
	var t = state.get("territories", {}).get(str(tid))
	if t != null:
		for u in t["pending_deployment"]:
			_add_unit(out, u["owner"], u["unit_type"], 1)
	for o in queued_purchase.get("orders", []):
		if int(o["deploy_at"]) == tid:
			_add_unit(out, str(queued_purchase["faction"]), o["unit_type"], int(o["qty"]))
	return out


func _add_unit(out: Dictionary, owner: String, unit_type: String, qty: int) -> void:
	var by_type: Dictionary = out.get(owner, {})
	by_type[unit_type] = int(by_type.get(unit_type, 0)) + qty
	out[owner] = by_type


## Every space that is contested now or will be once the queued combat moves
## run (their destinations), as territory ids.
func contested_spaces() -> Array:
	var out := {}
	for key in state.get("territories", {}):
		if state["territories"][key].get("contested_by") != null:
			out[int(key)] = true
	for o in queued_attack.get("orders", []):
		out[int(o["path"][o["path"].size() - 1])] = true
	return out.keys()


## The combat_move event a phase_queue is holding for execution ({} = none).
func set_queued_attack(event: Dictionary) -> void:
	queued_attack = event
	state_changed.emit()


# ---- the human's move phases ---------------------------------------------------

## The server's move options and staged moves for the human's Combat/Non-Combat
## Move ({} = not in one). Destinations are normalised to {dest: path}; a
## non-combat move has no route, so its "path" is just [origin, dest].
func set_human_move(faction: String, block: Dictionary) -> void:
	if block.is_empty() or not block.has("kind"):
		if not human_move.is_empty():
			human_move = {}
			move_origin = -1
			move_selected.clear()
			move_changed.emit()
		return
	var combat: bool = block["kind"] == "combat"
	var options := {}
	for k in block["options"]:
		var o: Dictionary = block["options"][k]
		var origin := int(o["territory_id"])
		var dests := {}
		for d in o["destinations"]:
			if combat:
				var path := []
				for x in o["destinations"][d]:
					path.append(int(x))
				dests[int(d)] = path
			else:
				dests[int(d)] = [origin, int(d)]
		options[int(k)] = {"unit_type": o["unit_type"], "origin": origin, "dests": dests}
	var orders := []
	for o in block["orders"]:
		var order := {"unit_id": int(o["unit_id"]), "unit_type": str(o["unit_type"]), "from": int(o["from"])}
		if combat:
			var path := []
			for x in o["path"]:
				path.append(int(x))
			order["path"] = path
			order["dest"] = path[path.size() - 1]
		else:
			order["dest"] = int(o["destination"])
		orders.append(order)
	human_move = {"kind": block["kind"], "faction": faction, "options": options, "orders": orders}
	for uid in move_selected.keys():
		if not options.has(uid):
			move_selected.erase(uid)  # committed (or otherwise unable to move) now
	move_changed.emit()


func human_move_active() -> bool:
	return not human_move.is_empty()


func move_faction() -> String:
	return str(human_move.get("faction", ""))


## The units at a space that can still be ordered this phase (the server's
## options exclude committed ones).
func movable_ids_at(tid: int) -> Array:
	var out := []
	if human_move.is_empty():
		return out
	for u in units_at(tid):
		if u["owner"] == move_faction() and human_move["options"].has(int(u["unit_id"])):
			out.append(int(u["unit_id"]))
	return out


## Orders whose units start at `tid` (committed there).
func committed_from(tid: int) -> Array:
	var out := []
	for o in human_move.get("orders", []):
		if int(o["from"]) == tid:
			out.append(o)
	return out


## Orders whose units end at `tid` and start elsewhere.
func incoming_to(tid: int) -> Array:
	var out := []
	for o in human_move.get("orders", []):
		if int(o["dest"]) == tid and int(o["from"]) != tid:
			out.append(o)
	return out


## Pick the space whose units are being moved, all of its movable units selected.
## `silent`: don't emit move_changed (a caller mid-rebuild re-emits it deferred).
func select_move_origin(tid: int, silent := false) -> void:
	move_origin = tid
	move_selected.clear()
	for uid in movable_ids_at(tid):
		move_selected[uid] = true
	if not silent:
		move_changed.emit()


func toggle_move_unit(unit_id: int, on: bool) -> void:
	if on:
		move_selected[unit_id] = true
	else:
		move_selected.erase(unit_id)
	move_changed.emit()


func set_all_move_selected(on: bool) -> void:
	move_selected.clear()
	if on:
		for uid in movable_ids_at(move_origin):
			move_selected[uid] = true
	move_changed.emit()


func all_move_selected() -> bool:
	var ids := movable_ids_at(move_origin)
	return not ids.is_empty() and ids.all(func(uid): return move_selected.has(uid))


func _category(unit_type: String) -> String:
	return str(GameData.units["units"][unit_type]["category"])


## The space a path ends at, walking back to the last SEA zone before it (the
## zone an amphibious landing crosses last), or -1 if there is none.
func _last_sea_before_end(path: Array) -> int:
	for i in range(path.size() - 2, -1, -1):
		if GameData.territories[int(path[i])]["type"] == "sea":
			return int(path[i])
	return -1


func _order_for(kind: String, unit_id: int, path: Array) -> Dictionary:
	if kind == "combat":
		return {"unit_id": unit_id, "path": path}
	return {"unit_id": unit_id, "destination": int(path[path.size() - 1])}


## Where the selected units can go, and the orders that would send them:
## {dest: {"orders": [...], "count": n}}. Every selected unit must be able to reach
## the target -- except that in an AMPHIBIOUS group (land units together with sea
## units) a LAND target only needs the land (and air) units to reach it: the sea
## units that can then escort them to the last sea zone of the landing path.
func move_targets() -> Dictionary:
	var out := {}
	if human_move.is_empty() or move_selected.is_empty():
		return out
	var opts: Dictionary = human_move["options"]
	var kind: String = human_move["kind"]
	var ids := []
	for uid in move_selected:
		if opts.has(uid):
			ids.append(uid)
	if ids.is_empty():
		return out
	var has_land := false
	var has_sea := false
	for uid in ids:
		var cat := _category(opts[uid]["unit_type"])
		has_land = has_land or cat == "Land"
		has_sea = has_sea or cat == "Sea"
	var amphibious: bool = kind == "combat" and has_land and has_sea
	var core := []
	var escorts := []
	var lead := -1  # a land unit whose path decides where the escorts go
	for uid in ids:
		var cat := _category(opts[uid]["unit_type"])
		if amphibious and cat == "Sea":
			escorts.append(uid)
		else:
			core.append(uid)
			if cat == "Land" and lead < 0:
				lead = uid
	for d in opts[core[0]]["dests"]:
		var ok := true
		for uid in core:
			if not opts[uid]["dests"].has(d):
				ok = false
				break
		if not ok:
			continue
		var orders := []
		for uid in core:
			orders.append(_order_for(kind, uid, opts[uid]["dests"][d]))
		if amphibious and GameData.territories[d]["type"] == "land":
			var zone := _last_sea_before_end(opts[lead]["dests"][d])
			if zone >= 0:
				for uid in escorts:
					if opts[uid]["dests"].has(zone):
						orders.append(_order_for(kind, uid, opts[uid]["dests"][zone]))
		out[d] = {"orders": orders, "count": orders.size()}
	return out


## The staged moves in the shape stage_moves wants (unit id + path/destination).
func staged_orders_plain(except_units: Array = []) -> Array:
	var out := []
	for o in human_move.get("orders", []):
		if except_units.has(int(o["unit_id"])):
			continue
		out.append(_order_for(human_move["kind"], int(o["unit_id"]), o["path"] if human_move["kind"] == "combat" else [int(o["from"]), int(o["dest"])]))
	return out


## The server's purchase options for the human's Purchase phase ({} = not in one).
func set_human_purchase(faction: String, block: Dictionary) -> void:
	if block.is_empty():
		if not human_purchase.is_empty():
			human_purchase = {}
			purchase_changed.emit()
		return
	var targets := {}
	for k in block["targets"]:
		targets[int(k)] = block["targets"][k]
	var contested := []
	for t in block["contested"]:
		contested.append(int(t))
	human_purchase = {
		"faction": faction, "treasury": int(block["treasury"]), "total_cost": int(block["total_cost"]),
		"targets": targets, "orders": block["orders"], "contested": contested,
	}
	purchase_changed.emit()


func human_purchase_active() -> bool:
	return not human_purchase.is_empty()


## The purchase options at a space ({} if it isn't somewhere the player may buy).
func purchase_target(tid: int) -> Dictionary:
	return human_purchase.get("targets", {}).get(tid, {})


func purchase_budget_left() -> int:
	return int(human_purchase.get("treasury", 0)) - int(human_purchase.get("total_cost", 0))


## How many of a unit type are queued for deployment at a space.
func purchase_queued_at(tid: int, unit_type: String) -> int:
	var n := 0
	for o in human_purchase.get("orders", []):
		if int(o["deploy_at"]) == tid and str(o["unit_type"]) == unit_type:
			n += int(o["qty"])
	return n


## The purchase event a phase_queue is holding for execution ({} = none).
func set_queued_purchase(event: Dictionary) -> void:
	queued_purchase = event
	state_changed.emit()


# ---- derived HUD stats ------------------------------------------------------

const PHASE_LABELS := {
	"PURCHASE": "Purchase",
	"COMBAT_MOVE": "Combat Move",
	"COMBAT_RESOLUTION": "Combat Resolution",
	"NONCOMBAT_MOVE": "Non-Combat Move",
	"RETURN_TO_BASE": "Return to Base",  # a queue step of its own before Non-Combat Move
	"CAPTURE": "Capture Territory",
	"DEPLOY_INCOME": "Deploy & Income",
	"ALLIANCES": "Alliances",
}


## Factions that take turns (HUMAN or BOT, not eliminated), in turn order.
func active_factions() -> Array:
	var out := []
	if state.is_empty():
		return out
	for code in state["factions"]:
		var f: Dictionary = state["factions"][code]
		if (f["mode"] == "HUMAN" or f["mode"] == "BOT") and not f["eliminated"]:
			out.append(code)
	return out


## Every seated faction (HUMAN or BOT), eliminated ones included, in turn order.
func seated_factions() -> Array:
	var out := []
	if state.is_empty():
		return out
	for code in state["factions"]:
		var m: String = state["factions"][code]["mode"]
		if m == "HUMAN" or m == "BOT":
			out.append(code)
	return out


func faction_state(code: String) -> Dictionary:
	return state["factions"][code] if not state.is_empty() and state["factions"].has(code) else {}


## "You" for the human, "Bot N" (N in turn order among bots) for bots.
func seat_label(code: String) -> String:
	var f := faction_state(code)
	if f.is_empty():
		return ""
	if f["mode"] == "HUMAN":
		return "You"
	var n := 0
	for c in state["factions"]:
		if state["factions"][c]["mode"] == "BOT":
			n += 1
			if c == code:
				return "Bot %d" % n
	return "Bot"


func territory_count(code: String) -> int:
	var n := 0
	for tid in GameData.land_ids:
		if owner_of(tid) == code:
			n += 1
	return n


func sc_count(code: String) -> int:
	var n := 0
	for tid in GameData.land_ids:
		if owner_of(tid) == code and is_sc(tid):
			n += 1
	return n


## Whether the territory counts as a Strategic Center as shown: it is one on the
## map, and its owner can use it (a Defensive power has no Strategic Centers: it
## never buys or deploys; whoever captures the territory gets the SC).
func is_sc(tid: int) -> bool:
	if not GameData.territories[tid].get("strategic_center", false):
		return false
	return faction_state(owner_of(tid)).get("mode", "") != "DEFENSIVE"


## The territory's value as shown on the map: its value plus 2 for a Strategic Center.
func display_value(tid: int) -> int:
	return int(GameData.territories[tid].get("value", 0)) + (2 if is_sc(tid) else 0)


## Total purchase cost of the faction's deployed units (matches the bots'
## own strength measure, engine.bots.alliance_policy._total_unit_value).
func unit_value(code: String) -> int:
	var total := 0
	if state.is_empty():
		return 0
	for tid in state["territories"]:
		for u in state["territories"][tid]["units"]:
			if u["owner"] == code:
				var cost = GameData.units["units"][u["unit_type"]].get("cost")
				total += int(cost) if cost != null else 0
	return total


func allies_of(code: String) -> Array:
	var f := faction_state(code)
	var out := []
	if f.is_empty() or f["alliance"] == null:
		return out
	for c in state["factions"]:
		if c != code and state["factions"][c]["alliance"] == f["alliance"]:
			out.append(c)
	return out


## 1-based round number: every active faction has had one turn per round.
func round_number() -> int:
	var n := maxi(1, active_factions().size())
	return int(state.get("global_turn", 0)) / n + 1


func phase_label() -> String:
	return PHASE_LABELS.get(str(state.get("phase", "")), "")

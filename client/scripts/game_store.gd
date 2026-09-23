extends Node
## The client's copy of live game state (autoload "GameStore"). The single
## seam between the network layer and everything that draws: views read
## from here and listen to `state_changed`, and never talk to the socket.
## Until a server connects (step 6 of the map/HUD milestone), ownership
## falls back to the static starting owners in territories.json.

signal state_changed
signal alliance_changed  # the human player's Diplomacy options or a pending invitation changed
signal move_changed  # the human player's move queue, options or selection changed
signal purchase_changed  # the human player's purchase queue/options changed
signal armistice_changed  # a Propose Armistice proposal (in flight, or awaiting this human's own answer) changed
signal game_over_report_changed  # the Game Over report arrived, or its minimize/restore state was toggled

var human_move := {}      # the human's Combat/Non-Combat Move in progress: {kind, faction, options{uid: {unit_type, origin, dests{dest: path}, continuations{first_hop: {dest: path}}}}, orders[{unit_id, unit_type, from, dest, path?}]}
var tile_drag_armed := false  # a selected unit tile was pressed: a move drag may follow (see main.gd)
var move_origin := -1     # the space whose units are being picked to move
var move_selected := {}   # unit_id -> true: the units picked (all uncommitted ones by default)
## Offered right after committing a SINGLE unit's one-hop combat move that didn't
## itself trigger a battle (a Mechanized Infantry pass-through capture, typically):
## {unit_id, unit_type, first_hop, origin, targets{dest: path}} -- the further
## destinations it could still reach with whatever move budget is left over. {} =
## no such offer right now. Lets the player pick a route hop by hop -- see
## engine.movement.legal_combat_move_continuations' own docstring for why a
## single-shot search can't just offer every such route up front. Set by
## turn_stepper.move_commit right after a qualifying commit; cleared by the next
## commit of any kind, by recalling the offered unit's own move, or the phase
## ending. Deliberately NOT cleared by an ordinary refreshed queue arriving (the
## server's own options no longer include the now-staged unit at all, but this is
## purely a client-remembered affordance, independent of that).
var move_extend := {}
var _unit_index := {}     # unit_id -> unit dict, rebuilt with every state
var human_alliance := {}  # the human's Diplomacy phase: {faction, members, options{eligible_invite_targets, can_withdraw, alliance_action_used}, surrender[{target, reasons, allied, income{yours, theirs}}], game_would_end}
var invitation := {}      # a bot's invitation awaiting the human's answer: {from, to, members, answered, accepts}
var human_purchase := {}  # the human's Purchase phase in progress: {faction, treasury, total_cost, targets{tid: {remaining, next_sc, sources}}, orders[], contested[]}
var announcement_open := false  # an announcement panel is up: the game waits for the player to acknowledge it
var queued_step := ""  # a queue step that isn't a game phase (START_OF_TURN, RETURN_TO_BASE) while it is up, else ""
var queued_purchase := {}  # the purchase event awaiting execution, {} if none
var queued_attack := {}    # the combat_move event awaiting execution, {} if none
var state := {}  # last full GameState.to_dict() from the server, {} until one arrives
var armistice := {}  # a Propose Armistice proposal in flight, {} if none: {from, awaiting: [faction, ...]}
var game_over_report := []  # the Game Over report (server/report.py), one row per seat, [] until the game ends
var game_over_report_minimized := false  # the Game Over report panel's minimize/restore state
# The round THIS client's own next Propose Armistice becomes legal again, after its last one was
# declined (server/session.py's ARMISTICE_COOLDOWN_ROUNDS) -- -1 = no cooldown in effect. Set from
# armistice_resolved's cooldown_until_round, only when the declined proposal was this client's own
# (see TurnStepper._is_my_own_proposal).
var armistice_cooldown_until_round := -1


func set_state(new_state: Dictionary) -> void:
	state = new_state
	_unit_index.clear()
	for tid in state.get("territories", {}):
		for u in state["territories"][tid]["units"]:
			_unit_index[int(u["unit_id"])] = u
	state_changed.emit()


## The server's Diplomacy options for the human's Diplomacy phase ({} = not in one).
func set_human_alliance(faction: String, block: Dictionary) -> void:
	if block.is_empty() or str(block.get("kind", "")) != "diplomacy":
		if not human_alliance.is_empty():
			human_alliance = {}
			alliance_changed.emit()
		return
	human_alliance = {
		"faction": faction, "members": block["members"], "options": block["options"],
		"surrender": block["surrender"], "game_would_end": bool(block["game_would_end"]),
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
	armistice = {}
	armistice_cooldown_until_round = -1
	game_over_report = []
	game_over_report_minimized = false
	move_origin = -1
	move_selected.clear()
	queued_step = ""
	queued_purchase = {}
	queued_attack = {}
	tile_drag_armed = false
	state_changed.emit()
	purchase_changed.emit()
	move_changed.emit()
	alliance_changed.emit()
	armistice_changed.emit()
	game_over_report_changed.emit()


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


## The one HUMAN faction's code, "" if this is a bot-vs-bot game (has_player() is false then).
## At most one is ever seated (server/lobby.py), so there's no ambiguity to resolve.
func human_faction() -> String:
	for code in state.get("factions", {}):
		if is_player(code):
			return code
	return ""


# ---- Settings actions: Surrender / Propose Armistice ------------------------------

## A Propose Armistice proposal in flight, from the phase queue (independent of it,
## really -- it can happen in any phase) ({} = none pending).
func set_armistice(info: Dictionary) -> void:
	if info == armistice:
		return
	armistice = info
	armistice_changed.emit()


## True while a pending armistice proposal awaits THIS human's own answer -- someone
## else proposed it (proposing counts as agreeing, so a proposer is never asked to
## answer their own; see server/session.py's _handle_propose_armistice).
func armistice_pending() -> bool:
	return not armistice.is_empty() and (armistice.get("awaiting", []) as Array).has(human_faction())


func set_armistice_cooldown_until_round(round_num: int) -> void:
	armistice_cooldown_until_round = round_num
	armistice_changed.emit()


## Rounds left before THIS client's own next Propose Armistice is allowed again, 0 if none
## (server/session.py's ARMISTICE_COOLDOWN_ROUNDS, started by a declined proposal of its own).
func armistice_cooldown_remaining() -> int:
	return maxi(0, armistice_cooldown_until_round - round_number())


func set_game_over_report(report: Array) -> void:
	game_over_report = report
	game_over_report_minimized = false
	game_over_report_changed.emit()


func toggle_game_over_report_minimized() -> void:
	game_over_report_minimized = not game_over_report_minimized
	game_over_report_changed.emit()


## Whether the game that just ended finished via a unanimous Propose Armistice rather
## than the ordinary elimination/alliance victory condition (server/report.py's rows).
func game_ended_by_armistice() -> bool:
	for row in game_over_report:
		if row.get("victory_status") == "Armistice":
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
## Marker inside a stacks() key: "Armor|P2" is Armor with two promotions.
const PROMOTED_MARK := "|P"


## The units in a space as {owner: {stack key: count}}. A stack key is the unit
## type, plus PROMOTED_MARK and the rank for promoted units -- units of a type
## with different numbers of promotions are separate stacks.
func stacks(tid: int) -> Dictionary:
	var out := {}
	for u in units_at(tid):
		var by_type: Dictionary = out.get(u["owner"], {})
		# A land unit at sea is in Transport form: it shows as a Transport.
		var key: String = "Transport" if in_transport_form(tid, u) else u["unit_type"] + ("%s%d" % [PROMOTED_MARK, int(u.get("promotions", 0))] if int(u.get("promotions", 0)) > 0 else "")
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
	return key.get_slice(PROMOTED_MARK, 0)


static func is_promoted(key: String) -> bool:
	return key.contains(PROMOTED_MARK)


## The number of promotions a stack key stands for (0 if none).
static func rank_of(key: String) -> int:
	return int(key.get_slice(PROMOTED_MARK, 1)) if key.contains(PROMOTED_MARK) else 0


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
			move_extend = {}
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
		var continuations := {}
		for first_hop in o.get("continuations", {}):
			var group: Dictionary = o["continuations"][first_hop]
			var norm := {}
			for d in group:
				var path := []
				for x in group[d]:
					path.append(int(x))
				norm[int(d)] = path
			continuations[int(first_hop)] = norm
		options[int(k)] = {"unit_type": o["unit_type"], "origin": origin, "dests": dests, "continuations": continuations}
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


## Air units at the origin that ride along with a selected Aircraft Carrier: the
## engine sweeps every one of the mover's air units in the carrier's space along
## with it (rules.json carrier_ride_along), so they neither need to reach the
## target themselves nor can they be left behind by deselecting them. Air units
## that already have an order of their own are not in the options, so not here.
func ride_along_ids() -> Array:
	var out := []
	if human_move.is_empty() or move_selected.is_empty():
		return out
	var opts: Dictionary = human_move["options"]
	var carrier := false
	for uid in move_selected:
		if opts.has(uid) and opts[uid]["unit_type"] == "Aircraft Carrier":
			carrier = true
	if not carrier:
		return out
	for uid in opts:
		if int(opts[uid]["origin"]) == move_origin and _category(opts[uid]["unit_type"]) == "Air":
			out.append(uid)
	return out


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
	var riders := ride_along_ids()
	var ids := []
	for uid in move_selected:
		if opts.has(uid) and not riders.has(uid):
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
		out[d] = {"orders": orders, "count": orders.size() + riders.size()}
	return out


## See move_extend's own comment.
func move_extend_active() -> bool:
	return not move_extend.is_empty()


func move_extend_targets() -> Dictionary:
	return move_extend.get("targets", {})


func set_move_extend(info: Dictionary) -> void:
	move_extend = info
	move_changed.emit()


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
	"START_OF_TURN": "Start of Turn",  # a queue step of its own before Purchase
	"PURCHASE": "Purchase",
	"COMBAT_MOVE": "Combat Move",
	"COMBAT_RESOLUTION": "Combat Resolution",
	"NONCOMBAT_MOVE": "Non-Combat Move",
	"RETURN_TO_BASE": "Return to Base",  # a queue step of its own before Non-Combat Move
	"CAPTURE": "Capture Territory",
	"DEPLOY_INCOME": "Deploy & Income",
	"DIPLOMACY": "Diplomacy",
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


## What the faction's territory is worth: the value of every land territory it owns that
## isn't contested (Strategic Center bonus included) -- its income, not its cash on hand.
func territory_income(code: String) -> int:
	var total := 0
	for tid in GameData.land_ids:
		if owner_of(tid) != code:
			continue
		var contested = state["territories"][str(tid)].get("contested_by")
		if contested != null and not contested.is_empty():
			continue
		total += display_value(tid)
	return total


## Whether the territory is a Strategic Center in this game: it is one on the map, and
## the engine hasn't switched it off -- a territory that started out in a Defensive
## power's hands never is one (`sc_disabled`), whoever holds it now.
func is_sc(tid: int) -> bool:
	if not GameData.territories[tid].get("strategic_center", false):
		return false
	if state.is_empty():
		return true
	var t = state["territories"].get(str(tid))
	return t == null or not bool(t.get("sc_disabled", false))


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
## The authoritative round counter (GameState.round_number -- see its own docstring: a real, stored
## count of completed laps through the turn order, not derived from global_turn, which would give a
## wrong answer once an elimination has shrunk active_factions() partway through the game). Falls back
## to the old formula only for a state dict that predates this field (e.g. a stale --state fixture).
func round_number() -> int:
	if state.has("round_number"):
		return int(state["round_number"])
	var n := maxi(1, active_factions().size())
	return int(state.get("global_turn", 0)) / n + 1


func phase_label() -> String:
	if queued_step != "":
		return PHASE_LABELS.get(queued_step, "")
	return PHASE_LABELS.get(str(state.get("phase", "")), "")


func set_queued_step(step: String) -> void:
	if step != queued_step:
		queued_step = step
		state_changed.emit()

extends Node
## The client's copy of live game state (autoload "GameStore"). The single
## seam between the network layer and everything that draws: views read
## from here and listen to `state_changed`, and never talk to the socket.
## Until a server connects (step 6 of the map/HUD milestone), ownership
## falls back to the static starting owners in territories.json.

signal state_changed
signal purchase_changed  # the human player's purchase queue/options changed

var human_purchase := {}  # the human's Purchase phase in progress: {faction, treasury, total_cost, targets{tid: {remaining, next_sc, sources}}, orders[], contested[]}
var queued_purchase := {}  # the purchase event awaiting execution, {} if none
var queued_attack := {}    # the combat_move event awaiting execution, {} if none
var state := {}  # last full GameState.to_dict() from the server, {} until one arrives


func set_state(new_state: Dictionary) -> void:
	state = new_state
	state_changed.emit()


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
		if owner_of(tid) == code and GameData.territories[tid].get("strategic_center", false):
			n += 1
	return n


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

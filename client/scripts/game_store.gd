extends Node
## The client's copy of live game state (autoload "GameStore"). The single
## seam between the network layer and everything that draws: views read
## from here and listen to `state_changed`, and never talk to the socket.
## Until a server connects (step 6 of the map/HUD milestone), ownership
## falls back to the static starting owners in territories.json.

signal state_changed

var state := {}  # last full GameState.to_dict() from the server, {} until one arrives


func set_state(new_state: Dictionary) -> void:
	state = new_state
	state_changed.emit()


## A `your_turn` prompt is authoritative about whose turn/phase it is NOW.
## The server broadcasts `state` before it advances the turn, so after a
## turn ends the last `state` still names the faction that just finished;
## patching the two fields here keeps the HUD correct without waiting for a
## state message that (for a mid-turn phase change) never comes.
func note_prompt(faction: String, phase: String) -> void:
	if state.is_empty():
		return
	state["active_faction"] = faction
	state["phase"] = phase
	state_changed.emit()


func owner_of(tid: int) -> String:
	if not state.is_empty():
		var t = state["territories"].get(str(tid))
		return "" if t == null or t["owner"] == null else str(t["owner"])
	var s = GameData.territories[tid]
	return str(s.get("faction", ""))


## Deployed units in a space grouped for display: owner -> {unit_type: count}.
## Excludes units still in pending_deployment (bought, not yet on the board).
func stacks(tid: int) -> Dictionary:
	var out := {}
	if state.is_empty():
		return out
	var t = state["territories"].get(str(tid))
	if t == null:
		return out
	for u in t["units"]:
		var by_type: Dictionary = out.get(u["owner"], {})
		by_type[u["unit_type"]] = by_type.get(u["unit_type"], 0) + 1
		out[u["owner"]] = by_type
	return out


# ---- derived HUD stats ------------------------------------------------------

const PHASE_LABELS := {
	"PURCHASE": "Purchase",
	"COMBAT_MOVE": "Combat Move",
	"COMBAT_RESOLUTION": "Combat Resolution",
	"NONCOMBAT_MOVE": "Non-Combat Move",
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

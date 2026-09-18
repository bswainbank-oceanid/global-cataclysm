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

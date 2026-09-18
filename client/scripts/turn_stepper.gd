extends Node
## Steps through the game one "Next" press at a time (autoload "Stepper").
##
## Every server message lands in a queue. The queue is drained until it hits
## something that needs a press, then it waits:
##   - a `your_turn` prompt: the press submits the do-nothing decision for
##     that phase (empty orders / alliance "none") -- the per-phase decision
##     UIs aren't built yet, so stepping is how you watch the scenario play.
##   - a `bot_turn`: the server plays a bot's whole turn in one message, so
##     its events are split into the seven turn phases and revealed one per
##     press. Map/HUD numbers update when the turn's closing `state` is
##     applied, after the last phase (the server sends no mid-turn snapshots).
## Human phases the server resolves automatically (Combat Resolution,
## Capture, Deploy & Income) have no prompt, so they appear as state changes
## when the turn ends.

signal changed
signal log_line(text: String)
signal log_events(header: String, events: Array)

const PHASE_ORDER := [
	"PURCHASE", "COMBAT_MOVE", "COMBAT_RESOLUTION", "NONCOMBAT_MOVE",
	"CAPTURE", "DEPLOY_INCOME", "ALLIANCES",
]
const KIND_PHASE := {
	"purchase": "PURCHASE",
	"combat_move": "COMBAT_MOVE",
	"battle_event": "COMBAT_RESOLUTION",
	"noncombat_move": "NONCOMBAT_MOVE",
	"territory_captured": "CAPTURE",
	"faction_eliminated": "CAPTURE",
	"unit_deployed": "DEPLOY_INCOME",
	"income_collected": "DEPLOY_INCOME",
	"alliance_joined": "ALLIANCES",
	"alliance_withdrawal": "ALLIANCES",
}

var button_text := "Connecting..."
var button_enabled := false
var game_over := false

var _queue: Array = []
var _prompt: Dictionary = {}        # the your_turn awaiting a press ({} if none)
var _last_prompt: Dictionary = {}   # kept so a rejected decision can be retried
var _bot_faction := ""
var _bot_steps: Array = []          # [[phase, events], ...] still to reveal
var _awaiting_reply := false


func _ready() -> void:
	Net.raw_message.connect(_on_message)
	Net.disconnected.connect(func():
		log_line.emit("[color=#ff7060]disconnected from server[/color]")
		_refresh())


func _on_message(msg: Dictionary) -> void:
	_queue.append(msg)
	_pump()


## Drain the queue until something needs a press.
func _pump() -> void:
	while not _queue.is_empty() and _prompt.is_empty() and _bot_steps.is_empty() and not game_over:
		var msg: Dictionary = _queue.pop_front()
		match str(msg.get("type", "")):
			"state":
				GameStore.set_state(msg["game_state"])
			"your_turn":
				_awaiting_reply = false
				_prompt = msg
				GameStore.note_prompt(str(msg["faction"]), str(msg["phase"]))
			"bot_turn":
				_begin_bot_turn(msg)
			"combat_events":
				log_events.emit("Your battles", msg["events"])
			"error":
				log_line.emit("[color=#ff7060]server: %s[/color]" % str(msg.get("message", "")))
				if _awaiting_reply and not _last_prompt.is_empty():
					_awaiting_reply = false
					_prompt = _last_prompt  # the decision was rejected; let the player retry
			"game_over":
				game_over = true
				log_line.emit("[b]Game over[/b]")
	_refresh()


func _begin_bot_turn(msg: Dictionary) -> void:
	_bot_faction = str(msg["faction"])
	var by_phase := {}
	for e in msg["events"]:
		var phase: String = KIND_PHASE.get(str(e.get("kind", "")), "")
		if phase != "":
			if not by_phase.has(phase):
				by_phase[phase] = []
			by_phase[phase].append(e)
	_bot_steps.clear()
	for phase in PHASE_ORDER:
		_bot_steps.append([phase, by_phase.get(phase, [])])
	GameStore.note_prompt(_bot_faction, PHASE_ORDER[0])


func _phase_name(phase: String) -> String:
	return GameStore.PHASE_LABELS.get(phase, phase)


func _refresh() -> void:
	button_enabled = true
	if game_over:
		button_text = "Game over"
		button_enabled = false
	elif not _bot_steps.is_empty():
		button_text = "Next  >  %s: %s" % [_bot_faction, _phase_name(_bot_steps[0][0])]
	elif not _prompt.is_empty():
		button_text = "Next  >  You pass %s" % _phase_name(str(_prompt["phase"]))
	else:
		button_text = "Connecting..." if not Net.is_open() else "Waiting for server..."
		button_enabled = false
	changed.emit()


## One press: reveal the next bot phase, or submit the do-nothing decision
## for the pending prompt.
func advance() -> void:
	if not button_enabled:
		return
	if not _bot_steps.is_empty():
		var step: Array = _bot_steps.pop_front()
		var phase: String = step[0]
		GameStore.note_prompt(_bot_faction, phase)
		log_events.emit("%s - %s" % [_bot_faction, _phase_name(phase)], step[1])
		if _bot_steps.is_empty():
			_pump()  # release the turn's closing state and the next prompt
		else:
			_refresh()
		return
	var f := str(_prompt["faction"])
	var phase := str(_prompt["phase"])
	log_line.emit("[color=#9fb4c8]%s passes %s[/color]" % [f, _phase_name(phase)])
	match phase:
		"PURCHASE":
			Net.send_msg({"type": "purchase", "faction": f, "orders": []})
		"COMBAT_MOVE":
			Net.send_msg({"type": "combat_move", "faction": f, "orders": []})
		"NONCOMBAT_MOVE":
			Net.send_msg({"type": "noncombat_move", "faction": f, "orders": []})
		"ALLIANCES":
			Net.send_msg({"type": "alliance_action", "faction": f, "action": "none"})
	_last_prompt = _prompt
	_prompt = {}
	_awaiting_reply = true
	_refresh()


## Waits (bounded) until a press is possible, for scripted runs.
func wait_ready(max_seconds: float = 15.0) -> void:
	var waited := 0.0
	while not button_enabled and not game_over and waited < max_seconds:
		await get_tree().process_frame
		waited += get_process_delta_time()


## Dev/scripted: press Next `n` times, waiting for the server between presses.
func press(n: int) -> void:
	for i in n:
		await wait_ready()
		if not button_enabled:
			return
		advance()
	await get_tree().create_timer(0.5).timeout


## Dev/scripted: answer `n` of the human's prompts, revealing each bot turn
## in full in between, then stop at the next prompt.
func autoplay(n: int) -> void:
	var answered := 0
	var waited := 0.0
	while not game_over and waited < 45.0:
		if button_enabled:
			var was_prompt := _bot_steps.is_empty() and not _prompt.is_empty()
			if was_prompt:
				if answered >= n:
					break
				answered += 1
			advance()
		else:
			await get_tree().process_frame
			waited += get_process_delta_time()

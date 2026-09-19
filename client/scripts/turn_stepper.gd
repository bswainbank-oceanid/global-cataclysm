extends Node
## Drives the watcher (autoload "Stepper"). The server decides each phase's
## orders ahead of time and holds them QUEUED (`phase_queue`); this shows
## them, and the Next button sends `next`, which makes the server execute
## the queue (`phase_result`), send a fresh `state`, and queue the following
## phase. See server/stepper.py for the protocol.
##
## The same pattern will carry player turns: orders picked in the upper
## right panel land in the queue, and Next executes them.

signal changed
signal queue_shown(header: String, skipped: Array, events: Array)
signal executed(header: String, events: Array)
signal log_line(text: String)

var button_text := "Connecting..."
var button_enabled := false
var game_over := false

var _queued_faction := ""  # whose phase is queued now
var _queued_phase := ""
var _awaiting := false  # a `next` is in flight; the reply is the next queue


func _ready() -> void:
	Net.raw_message.connect(_on_message)
	Net.disconnected.connect(func():
		log_line.emit("[color=#ff7060]disconnected from server[/color]")
		_awaiting = false
		_refresh())


func _on_message(msg: Dictionary) -> void:
	match str(msg.get("type", "")):
		"state":
			GameStore.set_state(msg["game_state"])
		"phase_queue":
			_awaiting = false
			_queued_faction = str(msg["faction"])
			_queued_phase = str(msg["phase"])
			GameStore.set_queued_purchase(msg["events"][0] if _queued_phase == "PURCHASE" and not msg["events"].is_empty() else {})
			GameStore.set_queued_attack(msg["events"][0] if _queued_phase == "COMBAT_MOVE" and not msg["events"].is_empty() else {})
			queue_shown.emit(_header(_queued_faction, _queued_phase), msg.get("skipped", []), msg["events"])
		"phase_result":
			executed.emit(_header(str(msg["faction"]), str(msg["phase"])), msg["events"])
		"error":
			_awaiting = false
			log_line.emit("[color=#ff7060]server: %s[/color]" % str(msg.get("message", "")))
		"game_over":
			_awaiting = false
			game_over = true
			log_line.emit("[b]Game over[/b]")
	_refresh()


static func _header(faction: String, phase: String) -> String:
	return "%s - %s" % [faction, GameStore.PHASE_LABELS.get(phase, phase)]


func _refresh() -> void:
	button_enabled = false
	if game_over:
		button_text = "Game over"
	elif _queued_phase != "" and not _awaiting:
		button_text = "Next  >  Execute %s" % _header(_queued_faction, _queued_phase)
		button_enabled = true
	elif _awaiting:
		button_text = "Executing..."
	else:
		button_text = "Connecting..." if not Net.is_open() else "Waiting for server..."
	changed.emit()


## One press: execute the queued phase and move on to the next.
func advance() -> void:
	if not button_enabled:
		return
	_awaiting = true
	Net.send_msg({"type": "next"})
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
	await wait_ready()

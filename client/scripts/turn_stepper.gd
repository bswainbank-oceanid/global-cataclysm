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
signal battle_opened(preview: Dictionary)   # a battle paused: show the battle board
signal battle_result(events: Array)         # ...its fought events, for the board to reveal

var button_text := "Connecting..."
var button_enabled := false  # Next can execute the queued phase
var button_active := false   # the button can be pressed at all: Next, or Pause while playing
var game_over := false

var _queued_faction := ""  # whose phase is queued now
var _queued_phase := ""
var busy := Callable()  # set by main: true while arrows are still animating
var _last_queue: Dictionary = {}  # the queue message awaiting execution
var _playing := false  # running unpaused: the button offers Pause instead of Next
var _pause_requested := false  # Pause pressed mid-run; takes effect when the next phase is queued
var _auto := false  # the queued phase should run by itself (Settings say not to pause)
var _battle_open := false          # the battle board is up: hold everything it would spoil
var _held: Array = []              # messages received meanwhile (result, state, next queue)
var _held_result: Dictionary = {}
var _awaiting := false  # a `next` is in flight; the reply is the next queue


func _ready() -> void:
	Settings.changed.connect(func():
		if not _last_queue.is_empty() and not _awaiting and not _battle_open:
			_auto = not _should_pause(_last_queue)  # a live change applies to the phase waiting now
			_playing = _auto
			_refresh())
	Net.raw_message.connect(_on_message)
	Net.disconnected.connect(func():
		log_line.emit("[color=#ff7060]disconnected from server[/color]")
		_awaiting = false
		_refresh())


func _on_message(msg: Dictionary) -> void:
	if _battle_open and str(msg.get("type", "")) in ["state", "phase_queue"]:
		_held.append(msg)  # applying these would show the battle's outcome before the board does
		return
	match str(msg.get("type", "")):
		"state":
			GameStore.set_state(msg["game_state"])
		"phase_queue":
			_awaiting = false
			_last_queue = msg
			_auto = not _should_pause(msg) and not _pause_requested
			if _battle_pause(msg) and not _pause_requested:
				_open_battle(_battle_in(msg))  # sets _auto false; the board takes over from here
			_pause_requested = false  # a manual pause is held for exactly one phase; settings decide after
			_playing = _auto
			_queued_faction = str(msg["faction"])
			_queued_phase = str(msg["phase"])
			GameStore.set_queued_purchase(msg["events"][0] if _queued_phase == "PURCHASE" and not msg["events"].is_empty() else {})
			GameStore.set_queued_attack(msg["events"][0] if _queued_phase == "COMBAT_MOVE" and not msg["events"].is_empty() else {})
			var header := _header(_queued_faction, _queued_phase)
			if msg.has("battle"):
				header += " (battle %d of %d)" % [int(msg["battle"]["index"]) + 1, int(msg["battle"]["count"])]
			queue_shown.emit(header, msg.get("skipped", []), msg["events"])
		"phase_result":
			if _battle_open:
				_held_result = msg
				battle_result.emit(msg["events"])
			else:
				executed.emit(_header(str(msg["faction"]), str(msg["phase"])), msg["events"])
		"error":
			_awaiting = false
			log_line.emit("[color=#ff7060]server: %s[/color]" % str(msg.get("message", "")))
		"game_over":
			_awaiting = false
			_auto = false
			_playing = false
			game_over = true
			log_line.emit("[b]Game over[/b]")
	_refresh()


static func _header(faction: String, phase: String) -> String:
	return "%s - %s" % [faction, GameStore.PHASE_LABELS.get(phase, phase)]


func _refresh() -> void:
	button_enabled = false
	button_active = false
	if game_over:
		button_text = "Game over"
	elif _battle_open:
		button_text = "Battle in progress..."
	elif _playing:
		button_text = "Pausing..." if _pause_requested else "Pause"
		button_active = not _pause_requested
	elif _queued_phase != "" and not _awaiting:
		button_text = "Next  >  Execute %s" % _header(_queued_faction, _queued_phase)
		button_enabled = true
		button_active = true
	elif _awaiting:
		button_text = "Executing..."
	else:
		button_text = "Connecting..." if not Net.is_open() else "Waiting for server..."
	changed.emit()


## Whether the queued phase waits for the Next button, per Settings.
func _should_pause(msg: Dictionary) -> bool:
	var faction := str(msg["faction"])
	var battle := _battle_in(msg)
	if GameStore.is_player(faction):
		# A player's own turn needs their decisions, so every phase waits --
		# except Combat Resolution, which only waits per battle if asked.
		if str(msg["phase"]) == "COMBAT_RESOLUTION":
			return not battle.is_empty() and Settings.your_pause_battle
		return true
	match Settings.opp_pause:
		Settings.OppPause.PHASE:
			return true
		Settings.OppPause.TURN:
			if str(msg["phase"]) == "PURCHASE":
				return true  # one pause per turn, before it starts
	if battle.is_empty():
		return false
	if Settings.opp_pause_battle:
		return true
	if Settings.opp_pause_battle_mine:
		for side in ["attackers", "defenders"]:
			for u in battle[side]:
				if GameStore.is_player(str(u["owner"])):
					return true
	return false


## True when this queued battle should stop for the battle board, as opposed to
## an ordinary phase pause (which just offers the Next button).
func _battle_pause(msg: Dictionary) -> bool:
	var battle := _battle_in(msg)
	if battle.is_empty():
		return false
	if GameStore.is_player(str(msg["faction"])):
		return Settings.your_pause_battle
	if Settings.opp_pause_battle:
		return true
	if Settings.opp_pause_battle_mine:
		for side in ["attackers", "defenders"]:
			for u in battle[side]:
				if GameStore.is_player(str(u["owner"])):
					return true
	return false


func _open_battle(preview: Dictionary) -> void:
	_battle_open = true
	_held = []
	_held_result = {}
	_auto = false
	_playing = false
	battle_opened.emit(preview)


## The board asked for the battle to be fought (its first Next Roll).
func execute_open_battle() -> void:
	if _battle_open:
		_do_advance()


## End Battle: show what was held back -- the log entry, then the new state and
## whatever comes next -- exactly as if the battle had just been fought.
func release_battle() -> void:
	if not _battle_open:
		return
	_battle_open = false
	if not _held_result.is_empty():
		executed.emit(_header(str(_held_result["faction"]), str(_held_result["phase"])), _held_result["events"])
	_held_result = {}
	var pending := _held
	_held = []
	for m in pending:
		_on_message(m)
	_refresh()


## The battle_preview a Combat Resolution queue is holding ({} if none).
func _battle_in(msg: Dictionary) -> Dictionary:
	if str(msg["phase"]) != "COMBAT_RESOLUTION":
		return {}
	for e in msg["events"]:
		if str(e.get("kind", "")) == "battle_preview":
			if e["defenders"].is_empty():
				return {}  # walking into an empty territory: a capture, not a battle
			return e
	return {}


## Unpaused phases run themselves as soon as the previous phase's arrows have
## finished shortening -- no extra delay, and no waiting at all when there
## were no arrows.
func _process(_delta: float) -> void:
	if _auto and not _awaiting and not game_over and not _queued_phase.is_empty():
		if busy.is_null() or not busy.call():
			_auto = false
			_do_advance()


## The button's press: Pause while phases are running by themselves, else Next.
## Pause stops at the phase in hand (or, mid-execution, at the next one) and
## offers Next exactly like a scheduled pause; after that Settings apply again.
func button_pressed() -> void:
	if not _playing:
		advance()
		return
	if _auto and not _awaiting:
		_auto = false
		_playing = false
	else:
		_pause_requested = true
	_refresh()


## One press: execute the queued phase and move on to the next.
func advance() -> void:
	if not button_enabled:
		return
	_do_advance()


func _do_advance() -> void:
	_awaiting = true
	Net.send_msg({"type": "next"})
	_refresh()


## Waits (bounded) until a press is possible, for scripted runs.
func wait_ready(max_seconds: float = 15.0) -> void:
	var waited := 0.0
	while not button_enabled and not game_over and not _battle_open and waited < max_seconds:
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

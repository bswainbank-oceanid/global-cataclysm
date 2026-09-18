extends Node
## WebSocket connection to the game server (autoload "Net"). The only place
## that talks to the socket: it decodes messages, feeds `state` straight into
## GameStore, and re-emits everything else as signals for the UI. Sending goes
## through send_msg(); the per-phase message shapes are documented in
## server/session.py.
##
## Not connected until start() is called (main.gd does so when the client is
## launched with --server, or later from a menu).
##
## Dev-only autoplay: with `autoplay_prompts > 0`, answers each `your_turn`
## with the do-nothing decision for that phase (empty orders / alliance
## "none"), so live state updates and bot turns can be verified without a
## human. `autoplay_finished` fires once that many prompts were answered.

signal connected
signal disconnected
signal your_turn(msg: Dictionary)
signal bot_turn(msg: Dictionary)
signal combat_events(msg: Dictionary)
signal alliance_invite(msg: Dictionary)
signal server_error(text: String)
signal game_over
signal autoplay_finished

var url := "ws://localhost:8765"
var faction := "NAA"
var autoplay_prompts := 0

var _ws := WebSocketPeer.new()
var _open := false
var _enabled := false
var _answered := 0


func start(server_url: String = "", as_faction: String = "") -> void:
	if server_url != "":
		url = server_url
	if as_faction != "":
		faction = as_faction
	_enabled = true
	var err := _ws.connect_to_url(url)
	if err != OK:
		server_error.emit("could not connect to %s (error %d)" % [url, err])


func send_msg(d: Dictionary) -> void:
	if _open:
		_ws.send_text(JSON.stringify(d))


func _process(_delta: float) -> void:
	if not _enabled:
		return
	_ws.poll()
	match _ws.get_ready_state():
		WebSocketPeer.STATE_OPEN:
			if not _open:
				_open = true
				connected.emit()
				send_msg({"type": "join", "faction": faction})
			while _ws.get_available_packet_count() > 0:
				_handle(_ws.get_packet().get_string_from_utf8())
		WebSocketPeer.STATE_CLOSED:
			if _open:
				_open = false
				disconnected.emit()
			_enabled = false


func _handle(raw: String) -> void:
	var msg = JSON.parse_string(raw)
	if typeof(msg) != TYPE_DICTIONARY:
		return
	match str(msg.get("type", "")):
		"state":
			GameStore.set_state(msg["game_state"])
		"your_turn":
			GameStore.note_prompt(str(msg["faction"]), str(msg["phase"]))
			your_turn.emit(msg)
			_maybe_autoplay(msg)
		"bot_turn":
			bot_turn.emit(msg)
		"combat_events":
			combat_events.emit(msg)
		"alliance_invite":
			alliance_invite.emit(msg)
		"error":
			server_error.emit(str(msg.get("message", "")))
		"game_over":
			game_over.emit()
			if autoplay_prompts > 0:
				autoplay_finished.emit()


func _maybe_autoplay(msg: Dictionary) -> void:
	if autoplay_prompts <= 0 or _answered >= autoplay_prompts:
		return
	_answered += 1
	var f := str(msg["faction"])
	match str(msg["phase"]):
		"PURCHASE":
			send_msg({"type": "purchase", "faction": f, "orders": []})
		"COMBAT_MOVE":
			send_msg({"type": "combat_move", "faction": f, "orders": []})
		"NONCOMBAT_MOVE":
			send_msg({"type": "noncombat_move", "faction": f, "orders": []})
		"ALLIANCES":
			send_msg({"type": "alliance_action", "faction": f, "action": "none"})
	if _answered >= autoplay_prompts:
		# The reply's messages (state, bot_turn, ...) land over the next few
		# frames; give them a moment before announcing completion.
		get_tree().create_timer(1.5).timeout.connect(func(): autoplay_finished.emit())

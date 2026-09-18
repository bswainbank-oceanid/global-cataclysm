extends Node
## WebSocket transport to the game server (autoload "Net"). The only place
## that touches the socket: it decodes each incoming message into a
## Dictionary and emits `raw_message`; what to DO with the messages (apply
## state, prompt the player, step through a bot turn) is TurnStepper's job.
## Sending goes through send_msg(); the per-phase message shapes are
## documented in server/session.py.
##
## Not connected until start() is called (main.gd does so when the client is
## launched with --server, or later from a menu).

signal connected
signal disconnected
signal raw_message(msg: Dictionary)

var url := "ws://localhost:8765"
var faction := "NAA"

var _ws := WebSocketPeer.new()
var _open := false
var _enabled := false


func start(server_url: String = "", as_faction: String = "") -> void:
	if server_url != "":
		url = server_url
	if as_faction != "":
		faction = as_faction
	_enabled = true
	var err := _ws.connect_to_url(url)
	if err != OK:
		raw_message.emit({"type": "error", "message": "could not connect to %s (error %d)" % [url, err]})


func is_open() -> bool:
	return _open


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
				var msg = JSON.parse_string(_ws.get_packet().get_string_from_utf8())
				if typeof(msg) == TYPE_DICTIONARY:
					raw_message.emit(msg)
		WebSocketPeer.STATE_CLOSED:
			if _open:
				_open = false
				disconnected.emit()
			_enabled = false

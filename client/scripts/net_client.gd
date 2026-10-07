extends Node
## WebSocket transport to the game server (autoload "Net"). The only place
## that touches the socket: it decodes each incoming message into a
## Dictionary and emits `raw_message`; what to DO with the messages (apply
## state, show the queued phase) is TurnStepper's job. Sending goes through
## send_msg(); the message shapes are documented in server/stepper.py (watch
## mode) and server/session.py (joining as a faction).
##
## Not connected until start() is called (main.gd does so when the client is
## launched with --server, or later from a menu). With `auto_reconnect` on (the
## multi-player server: Account turns it on), a lost connection is tried again
## every RETRY_SECONDS until it comes back.

signal connected
signal disconnected
signal raw_message(msg: Dictionary)
signal reconnecting  # the connection was lost and will be tried again

const RETRY_SECONDS := 2.0

const RELEASE := "res://data/server.json"  # a release build's server, {"url": ...} (tools/build_client.py)

var url := "ws://localhost:8765"
var auto_watch := false  # send "watch" on connecting (resuming a running game); else the launch screen decides

var auto_reconnect := false

var _ws := WebSocketPeer.new()
var _open := false
var _enabled := false
var _retry_in := -1.0  # seconds until the next try to reconnect (-1: none planned)


## The server a release build connects to (tools/build_client.py writes it into the build), or "" for a
## development copy, which only connects when started with --server. In a browser (the web build) it is
## the page's own server -- wss://<the page's host>/ws (ws:// for a page served over plain http) -- unless
## the page's address says otherwise (?server=ws://..., for testing).
static func release_url() -> String:
	if OS.has_feature("web"):
		var asked := str(JavaScriptBridge.eval("new URLSearchParams(location.search).get('server') || ''", true))
		if asked != "":
			return asked
		var secure := str(JavaScriptBridge.eval("location.protocol", true)) == "https:"
		return "%s://%s/ws" % ["wss" if secure else "ws", str(JavaScriptBridge.eval("location.host", true))]
	if not FileAccess.file_exists(RELEASE):
		return ""
	var doc = JSON.parse_string(FileAccess.get_file_as_string(RELEASE))
	return str(doc.get("url", "")) if doc is Dictionary else ""


func start(server_url: String = "") -> void:
	if server_url != "":
		url = server_url
	_connect()


func _connect() -> void:
	_enabled = true
	_ws = WebSocketPeer.new()
	# The default 64 KB inbound buffer is smaller than a full game state (~80 KB with
	# four or more armies), and Godot drops the connection on an oversized message.
	_ws.inbound_buffer_size = 16 * 1024 * 1024
	_ws.outbound_buffer_size = 1024 * 1024
	_ws.max_queued_packets = 4096
	var err := _ws.connect_to_url(url)
	if err != OK:
		raw_message.emit({"type": "error", "message": "could not connect to %s (error %d)" % [url, err]})


func is_open() -> bool:
	return _open


func send_msg(d: Dictionary) -> void:
	if _open:
		_ws.send_text(JSON.stringify(d))


func _process(delta: float) -> void:
	if _retry_in >= 0.0:
		_retry_in -= delta
		if _retry_in < 0.0:
			_connect()
		return
	if not _enabled:
		return
	_ws.poll()
	match _ws.get_ready_state():
		WebSocketPeer.STATE_OPEN:
			if not _open:
				_open = true
				connected.emit()
				if auto_watch:
					send_msg({"type": "watch"})
			while _ws.get_available_packet_count() > 0:
				var msg = JSON.parse_string(_ws.get_packet().get_string_from_utf8())
				if typeof(msg) == TYPE_DICTIONARY:
					raw_message.emit(msg)
		WebSocketPeer.STATE_CLOSED:
			if _open:
				_open = false
				disconnected.emit()
			_enabled = false
			if auto_reconnect:
				_retry_in = RETRY_SECONDS
				reconnecting.emit()

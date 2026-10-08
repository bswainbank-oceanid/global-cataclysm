extends Node
## The player's login (autoload "Account") against the multi-player server (server/hub.py). A server
## in that mode greets a new connection with "hello"; the one-game launcher's server sends "lobby"
## instead, and then none of this applies (`multi` stays false).
##
## The login TOKEN is kept in user://login.cfg -- never the password -- and used to log back in on
## every start, until the player logs out. `--profile=<name>` keeps a separate one
## (user://login_<name>.cfg), so two clients on one machine can be two players. `--dev_user=<name>`
## logs in as <name>@dev.local (password "dev"), registering it the first time: for scripted runs.

signal changed                      # logged in, logged out, or the server's mode became known
signal reset_sent(email: String)       # a reset code was asked for (whether or not the email has an account)
signal problem(message: String)     # a login / registration refused, in the server's words

var multi := false     # the server is the multi-player one
var user := {}         # {id, player_name, actual_name, email, admin, created} once logged in
var token := ""
var server_build := {}  # the server's build number, from its hello (server/build.py): {build, commit, label}
var download_url := ""  # where to get the client, from the server's hello ("" when it doesn't say)
var _pending := ""     # the account request in flight: "token_login", "login", "register", or ""


func _ready() -> void:
	Net.raw_message.connect(_on_message)
	Net.disconnected.connect(func(): _pending = "")  # (still logged in: the client logs back in when it reconnects)
	var cfg := ConfigFile.new()
	if cfg.load(_path()) == OK:
		token = str(cfg.get_value("login", "token", ""))


func is_logged_in() -> bool:
	return not user.is_empty()


## A saved login is being checked with the server (show neither the login screen nor the menu yet).
func is_checking() -> bool:
	return _pending == "token_login" or (_pending == "login" and Dbg.args.has("dev_user"))


## This client's build number (client/data/build.json, written by the sync): {build, commit, label}.
static func client_build() -> Dictionary:
	var path := "res://data/build.json"
	if not FileAccess.file_exists(path):
		return {}
	var d = JSON.parse_string(FileAccess.get_file_as_string(path))
	return d if d is Dictionary else {}


## The client and the server were built from different commits (they may not understand each other).
func builds_differ() -> bool:
	var mine := client_build()
	if mine.is_empty() or server_build.is_empty():
		return false
	return str(mine.get("commit", "")).trim_suffix("+") != str(server_build.get("commit", "")).trim_suffix("+")


## The builds differ and this client is the older one (by build number): a newer client is to be had --
## reloading the page, or downloading it. (When the server is the older one, nothing the player does helps.)
func client_outdated() -> bool:
	return builds_differ() and int(client_build().get("build", 0)) < int(server_build.get("build", 0))


func is_admin() -> bool:
	return bool(user.get("admin", false))


## Whether the bots' inner workings are shown -- which AI a bot plays (its "bot type") and their
## Strategy Log: to admins on a multi-player server; always on the one-game server (no accounts).
func sees_bot_details() -> bool:
	return not multi or is_admin()


func login(email: String, password: String) -> void:
	_pending = "login"
	Net.send_msg({"type": "login", "email": email.strip_edges(), "password": password})


func register(player_name: String, actual_name: String, email: String, password: String) -> void:
	_pending = "register"
	Net.send_msg({"type": "register", "player_name": player_name.strip_edges(), "actual_name": actual_name.strip_edges(),
		"email": email.strip_edges(), "password": password})


## A forgotten password: has the server email a 6-digit code (reset_sent once it's asked).
func request_reset(email: String) -> void:
	_pending = "reset"
	Net.send_msg({"type": "request_reset", "email": email.strip_edges()})


## The code from that email, and a new password: logs in ("logged_in"), or says what's wrong (problem).
func reset_password(email: String, code: String, password: String) -> void:
	_pending = "reset"
	Net.send_msg({"type": "reset_password", "email": email.strip_edges(), "code": code.strip_edges(), "password": password})


func logout() -> void:
	Net.send_msg({"type": "logout"})
	_forget()


func _on_message(msg: Dictionary) -> void:
	match str(msg.get("type", "")):
		"hello":
			server_build = msg.get("build", {})
			download_url = str(msg.get("download_url", "")) if msg.get("download_url") != null else ""
			multi = true
			Net.auto_reconnect = true
			Net.auto_watch = false  # (the one-game server's; here a game is entered after logging in)
			user = {}
			if token != "":
				_pending = "token_login"
				Net.send_msg({"type": "token_login", "token": token})
			elif Dbg.args.has("dev_user"):
				var name := str(Dbg.args["dev_user"])
				login("%s@dev.local" % name.to_lower(), "dev")
			changed.emit()
		"logged_in":
			_pending = ""
			user = msg.get("user", {})
			token = str(msg.get("token", ""))
			_save()
			changed.emit()
		"reset_requested":
			_pending = ""
			reset_sent.emit(str(msg.get("email", "")))
		"error":
			# (refused before it got to be a login at all -- an older server that doesn't know the request,
			# say: the login screen is waiting, so say why rather than leave it waiting)
			if _pending in ["login", "register", "reset"]:
				_pending = ""
				problem.emit(str(msg.get("message", "")))
		"logged_out":
			_forget()
			if msg.has("reason"):  # (an admin locked the account, say)
				problem.emit(str(msg["reason"]))
		"login_failed":
			if _pending == "":
				return
			var was := _pending
			_pending = ""
			if was == "token_login":  # that login has ended (logged out elsewhere): log in afresh
				_forget()
			elif was == "login" and Dbg.args.has("dev_user") and not is_logged_in():
				var name := str(Dbg.args["dev_user"])
				register(name, "", "%s@dev.local" % name.to_lower(), "dev")
			else:
				problem.emit(str(msg.get("message", "")))


func _forget() -> void:
	user = {}
	token = ""
	_pending = ""
	_save()
	changed.emit()


func _save() -> void:
	var cfg := ConfigFile.new()
	cfg.set_value("login", "token", token)
	cfg.save(_path())


static func _path() -> String:
	var profile := str(Dbg.args.get("profile", ""))
	return "user://login.cfg" if profile == "" else "user://login_%s.cfg" % profile.validate_filename()

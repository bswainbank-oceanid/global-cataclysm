extends Node
## The player's login (autoload "Account") against the multi-player server (server/hub.py). A server
## in that mode greets a new connection with "hello"; the one-game launcher's server sends "lobby"
## instead, and then none of this applies (`multi` stays false).
##
## The login TOKEN is kept in user://login.cfg -- never the password -- and used to log back in on
## every start, until the player logs out. A server on this machine (a development copy's) has its own,
## user://login_local.cfg, so playing on it and on the real server don't log each other out. `--profile=<name>`
## keeps a separate one (user://login_<name>.cfg, login_local_<name>.cfg), so two clients on one machine can be
## two players. `--dev_user=<name>`
## logs in as <name>@dev.local (password "dev"), registering it the first time: for scripted runs.

signal changed                      # logged in, logged out, or the server's mode became known
signal reset_sent(email: String)       # a reset code was asked for (whether or not the email has an account)
signal problem(message: String)     # a login / registration refused, in the server's words
signal updated                      # an admin changed the account while logged in (locked or unlocked it)

# Where a locked player asks to have their account unlocked (server/accounts.py DISCORD).
const DISCORD := "https://discord.gg/Ys3xpKKP6M"

var multi := false     # the server is the multi-player one
var user := {}         # {id, player_name, actual_name, email, admin, created, locked, lock} once logged in
var token := ""
var server_build := {}  # the server's build number, from its hello (server/build.py): {build, commit, label}
var download_url := ""  # where to get the client, from the server's hello ("" when it doesn't say)
var _pending := ""     # the account request in flight: "token_login", "login", "register", or ""
var _settings_timer: Timer  # a Settings change waits a moment before it's saved with the account (a slider's drag)


func _ready() -> void:
	Net.raw_message.connect(_on_message)
	Net.disconnected.connect(func(): _pending = "")  # (still logged in: the client logs back in when it reconnects)
	_settings_timer = Timer.new()
	_settings_timer.one_shot = true
	_settings_timer.wait_time = 1.0
	_settings_timer.timeout.connect(_save_settings)
	add_child(_settings_timer)
	Settings.changed.connect(func():
		if multi and is_logged_in() and not Settings.applying_account():
			_settings_timer.start())
	# (the saved token is read when a server says hello: which file depends on which server it is)


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


## A locked account logs in, but can't host, join or resume games (server/hub.py refuses them).
func is_locked() -> bool:
	return bool(user.get("locked", false))


## Why the account is locked, and since when: "" if it isn't.
func lock_reason() -> String:
	if not is_locked():
		return ""
	var lock: Dictionary = user.get("lock", {}) if user.get("lock") is Dictionary else {}
	return reason_text(lock)


## A lock ({reason, at, by}) in words, for the profile and the Admin panel.
static func reason_text(lock: Dictionary) -> String:
	var when := str(lock.get("at", "")).left(10)
	var on := (" on " + when) if when != "" else ""
	if str(lock.get("reason", "")) == "user_limit":
		return "Locked%s: the server had all the players it takes when the account was made." % on
	var by = lock.get("by")
	return "Locked by an admin%s%s." % [(" (%s)" % str(by)) if by != null and str(by) != "" else "", on]


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
			if token == "":
				token = _saved_token()
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
			# The player's Settings, kept with the account: theirs on every device. An account from before they
			# were kept (null) takes this device's, and keeps them from now on.
			var kept = msg.get("preferences")
			if kept is Dictionary:
				Settings.apply_account(kept)
			elif msg.has("preferences"):
				_save_settings()
			changed.emit()
		"account_updated":
			if is_logged_in():
				user = msg.get("user", user)
				updated.emit()
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


## Keeps the player's Settings with their account (server/accounts.py save_preferences).
func _save_settings() -> void:
	if multi and is_logged_in():
		Net.send_msg({"type": "save_preferences", "settings": Settings.account_values()})


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


## The saved token for the server connected to ("" if none).
static func _saved_token() -> String:
	var cfg := ConfigFile.new()
	return str(cfg.get_value("login", "token", "")) if cfg.load(_path()) == OK else ""


## This server's saved-login file: login.cfg, or login_local.cfg for a server on this machine (each with the
## profile's name, if any).
static func _path() -> String:
	var profile := str(Dbg.args.get("profile", ""))
	var name := "login_local" if is_local_server(Net.url) else "login"
	return "user://%s.cfg" % name if profile == "" else "user://%s_%s.cfg" % [name, profile.validate_filename()]


## Whether `url` is a server on this machine (localhost, 127.0.0.1, ::1).
static func is_local_server(url: String) -> bool:
	var host := url.get_slice("://", 1).get_slice("/", 0)
	host = host.get_slice("]", 0).trim_prefix("[") if host.begins_with("[") else host.get_slice(":", 0)
	return host in ["localhost", "127.0.0.1", "::1"]

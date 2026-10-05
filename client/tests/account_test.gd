extends SceneTree
## Headless checks for the login keeper (Account, account.gd), against a multi-player server's messages:
##   godot --headless --path client -s res://tests/account_test.gd
## Exits non-zero on any failure.

var _failures := 0


func _check(cond: bool, what: String) -> void:
	if not cond:
		_failures += 1
		printerr("FAIL: " + what)


func _initialize() -> void:
	await process_frame
	var dbg = root.get_node("Dbg")
	dbg.args["profile"] = "account_test"  # (never the real user's saved login)
	var account = root.get_node("Account")
	DirAccess.remove_absolute(ProjectSettings.globalize_path(account._path()))
	account.token = ""
	var changes := [0]
	account.changed.connect(func(): changes[0] += 1)

	account._on_message({"type": "lobby"})
	_check(not account.multi, "a one-game server's lobby leaves multi-player off")
	account._on_message({"type": "hello", "logged_in": false})
	_check(account.multi and not account.is_logged_in() and not account.is_checking(), "hello with no saved login: log in")
	_check(changes[0] == 1, "and listeners hear of it")

	account.login("ann@x.com", "pw")
	var problems := []
	account.problem.connect(func(m): problems.append(m))
	account._on_message({"type": "login_failed", "message": "wrong email or password"})
	_check(problems == ["wrong email or password"], "a refused login is reported in the server's words")
	account._on_message({"type": "error", "message": "stray"})
	_check(problems.size() == 1, "an ordinary error is never the login's")

	account._on_message({"type": "logged_in", "user": {"id": "U_000001", "player_name": "Ann", "admin": true}, "token": "tok123"})
	_check(account.is_logged_in() and account.is_admin(), "logged in, with the admin flag")
	var cfg := ConfigFile.new()
	_check(cfg.load(account._path()) == OK and cfg.get_value("login", "token", "") == "tok123", "the token is saved")
	_check(not FileAccess.get_file_as_string(account._path()).contains("pw"), "the password is not")

	account.user = {}
	account._on_message({"type": "hello"})
	_check(account.is_checking(), "a saved token is tried on the next hello")
	account._on_message({"type": "error", "message": "log in first"})
	_check(account.is_checking(), "an ordinary error doesn't end the check")
	account._on_message({"type": "login_failed", "message": "that login has ended: log in again"})
	_check(account.token == "" and not account.is_checking() and not account.is_logged_in(), "an ended login is forgotten")
	cfg = ConfigFile.new()
	cfg.load(account._path())
	_check(cfg.get_value("login", "token", "x") == "", "...on disk too")

	account._on_message({"type": "logged_in", "user": {"id": "U_000001", "player_name": "Ann"}, "token": "tok456"})
	account.logout()
	_check(account.token == "" and not account.is_logged_in(), "logging out forgets the login")
	DirAccess.remove_absolute(ProjectSettings.globalize_path(account._path()))
	quit(1 if _failures > 0 else 0)

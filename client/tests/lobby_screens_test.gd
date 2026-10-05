extends SceneTree
## Headless checks for the multi-player lobby screens: the New Game Lobby (seats, host buttons, what it asks
## the server for), Available Games' list, and the chat panel.
##   godot --headless --path client -s res://tests/lobby_screens_test.gd
## Exits non-zero on any failure.

var _failures := 0
var _sent: Array = []


func _check(cond: bool, what: String) -> void:
	if not cond:
		_failures += 1
		printerr("FAIL: " + what)


func _lobby(open_seats: int, ann_seat = null) -> Dictionary:
	var seats := [
		{"seat": 1.0, "mode": "HUMAN", "faction": "random", "user_id": "U_000002", "player_name": "Bob"},
		{"seat": 2.0, "mode": "HUMAN", "faction": "GPC", "user_id": ann_seat, "player_name": "Ann" if ann_seat != null else null},
		{"seat": 3.0, "mode": "BOT", "faction": "random", "user_id": null, "player_name": null},
	]
	for i in 3:
		seats.append({"seat": 4.0 + i, "mode": "NONCOMBATANT", "faction": "random", "user_id": null, "player_name": null})
	return {"type": "game_lobby", "chat": [{"player_name": "Bob", "text": "hi [b]there[/b]"}], "game": {
		"id": "G_000004", "code": "6F78TB", "status": "forming", "host_id": "U_000002", "host_name": "Bob",
		"scenario": {"kind": "fixed", "id": "fixed", "name": null}, "humans": 2, "open_seats": open_seats,
		"settings": {"seats": [], "max_alliance_size": 2, "armistice_rounds": 0}, "seats": seats}}


func _buttons(node: Node) -> Array:
	var out := []
	for c in node.get_children():
		if c is Button and c.visible:
			out.append(c.text)
	return out


func _initialize() -> void:
	await process_frame
	var account = root.get_node("Account")
	account.user = {"id": "U_000001", "player_name": "Ann"}
	var lobby = load("res://scripts/game_lobby_screen.gd").new()
	root.add_child(lobby)
	await process_frame
	lobby.request.connect(func(m): _sent.append(m))

	# Ann, not the host, with an open seat: she can take it; no host buttons.
	lobby.show_game(_lobby(1))
	await process_frame
	_check(lobby.visible and lobby._code.text == "6F78TB", "the lobby shows with its code")
	_check(_buttons(lobby._seats) == ["TAKE SEAT"], "one seat to take: %s" % str(_buttons(lobby._seats)))
	_check(not lobby._launch.visible and not lobby._cancel.visible, "only the host launches or cancels")
	_check(lobby._rules.text.contains("never proposed") and lobby._rules.text.contains("up to 2 members"), "the settings in words")
	for c in lobby._seats.get_children():
		if c is Button:
			c.pressed.emit()
	_check(_sent.back() == {"type": "take_seat", "seat": 2}, "Take Seat asks for seat 2 (as a whole number)")
	lobby.chat.send.emit("hello")
	_check(_sent.back() == {"type": "chat", "room": "G_000004", "text": "hello"}, "chat goes to the lobby's room")
	_check(lobby.chat._log.get_parsed_text().contains("hi [b]there[/b]"), "chat text is shown as typed (no markup)")

	# Seated: she can leave it.
	lobby.show_game(_lobby(0, "U_000001"))
	await process_frame
	_check(_buttons(lobby._seats) == ["LEAVE SEAT"], "her seat to leave: %s" % str(_buttons(lobby._seats)))

	# The host: Launch only once every human seat is taken; Cancel always.
	account.user = {"id": "U_000002", "player_name": "Bob"}
	lobby.show_game(_lobby(1))
	await process_frame
	_check(lobby._launch.visible and lobby._launch.disabled and lobby._cancel.visible, "host: Launch waits for every seat")
	lobby.show_game(_lobby(0, "U_000001"))
	_check(not lobby._launch.disabled, "host: Launch once they are taken")
	lobby._launch.activated.emit()
	_check(_sent.back() == {"type": "launch"}, "Launch")
	lobby._cancel.activated.emit()
	_check(_sent.back() == {"type": "cancel"}, "Cancel")

	# Available Games: one row per game, the chat's history the first time.
	var available = load("res://scripts/available_games_screen.gd").new()
	root.add_child(available)
	await process_frame
	available.set_games({"games": [{"id": "G_000004", "scenario": {"kind": "shared", "name": "Duel"}, "host_name": "Bob",
		"open_seats": 1, "humans": 2}], "chat": [{"player_name": "Bob", "text": "anyone?"}]})
	await process_frame
	_check(available._list.get_child_count() == 1 and not available._empty.visible, "a row per open game")
	_check(available.chat._log.get_parsed_text().contains("anyone?"), "the browse room's history")
	available.set_games({"games": []})
	await process_frame
	_check(available._empty.visible, "an empty list says so")
	_check(available.chat._log.get_parsed_text().contains("anyone?"), "an update without chat keeps the chat")
	quit(1 if _failures > 0 else 0)

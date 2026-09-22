extends SceneTree
## Headless checks for the Settings "Surrender" and "Propose Armistice" actions, the
## armistice pop-up, the Game Over report panel, and post-elimination auto-spectate:
##   godot --headless --path client -s res://tests/settings_actions_test.gd

var _failures := 0


func _check(cond: bool, what: String) -> void:
	if not cond:
		_failures += 1
		printerr("FAIL: " + what)


func _state(factions: Dictionary, game_over := false) -> Dictionary:
	return {"global_turn": 0, "active_faction": "NAA", "phase": "PURCHASE", "game_over": game_over,
		"territories": {}, "factions": factions}


func _initialize() -> void:
	await process_frame
	var store = root.get_node("GameStore")
	var stepper = root.get_node("Stepper")
	var settings = root.get_node("Settings")

	store.set_state(_state({
		"NAA": {"code": "NAA", "mode": "HUMAN", "treasury_mpc": 0, "alliance": null, "eliminated": false},
		"UE": {"code": "UE", "mode": "BOT", "treasury_mpc": 0, "alliance": null, "eliminated": false},
	}))
	_check(store.human_faction() == "NAA", "the one HUMAN seat is found")

	# armistice_pending: only the human this proposal AWAITS is asked to answer.
	_check(not store.armistice_pending(), "nothing pending at first")
	store.set_armistice({"from": "UE", "awaiting": ["NAA"]})
	_check(store.armistice_pending(), "NAA is awaited, so it's pending for them")
	store.set_armistice({"from": "NAA", "awaiting": ["GPC"]})
	_check(not store.armistice_pending(), "NAA proposed it themselves: never asked to answer their own")
	store.set_armistice({})

	# ArmisticeWindow shows only while the human's own answer is awaited.
	var armistice_window = load("res://scripts/armistice_window.gd").new()
	root.add_child(armistice_window)
	await process_frame
	_check(not armistice_window.visible, "hidden with nothing pending")
	store.set_armistice({"from": "UE", "awaiting": ["NAA"]})
	_check(armistice_window.visible and str(armistice_window._title.text).contains("UE"), "shows and names the proposer once NAA is awaited")
	store.set_armistice({})
	_check(not armistice_window.visible, "hides once resolved")

	# TurnStepper's message handling.
	var announced_items := []
	stepper.announced.connect(func(items: Array): announced_items.append_array(items))

	stepper._on_message({"type": "self_surrender_result", "faction": "NAA", "events": [
		{"kind": "self_surrender", "turn": 1, "faction": "NAA"}, {"kind": "faction_eliminated", "faction": "NAA"}]})
	_check(announced_items.size() == 1 and str(announced_items[0]["title"]) == "NAA surrenders", "a self-surrender is announced once, not twice over")

	stepper._on_message({"type": "armistice_proposed", "from": "NAA", "awaiting": ["UE"]})
	_check(store.armistice.get("from") == "NAA" and store.armistice.get("awaiting") == ["UE"], "a proposal in flight is tracked")

	stepper._on_message({"type": "armistice_resolved", "from": "NAA", "accepted": false, "declined_by": "UE", "events": []})
	_check(store.armistice.is_empty(), "resolving clears the pending proposal")

	announced_items.clear()
	stepper._on_message({"type": "armistice_resolved", "from": "NAA", "accepted": false, "declined_by": "UE", "events": []})
	_check(announced_items.size() == 1 and str(announced_items[0]["title"]).contains("declined"), "a decline is announced, and the game isn't over")
	_check(not stepper.game_over, "...it really isn't")

	announced_items.clear()
	stepper._on_message({"type": "armistice_resolved", "from": "NAA", "accepted": true, "declined_by": null,
		"events": [{"kind": "armistice", "turn": 2, "faction": "NAA", "participants": ["NAA", "UE"]}]})
	_check(announced_items.size() == 1 and str(announced_items[0]["title"]) == "Armistice agreed", "acceptance is announced from its 'armistice' event")

	announced_items.clear()
	stepper.game_over = false
	stepper._on_message({"type": "game_over", "report": [{
		"faction": "NAA", "seat_type": "HUMAN", "victory_status": "Armistice", "elimination_reason": null,
		"strategic_centers": 3, "territory_mpc": 20, "units_produced": 5, "units_destroyed": 2,
		"alliance_history": [], "bot_type": null, "bot_strategy": null, "alliance_strategy": null, "alliance_behavior": null}]})
	_check(stepper.game_over and store.game_over_report.size() == 1, "the report lands in GameStore")
	_check(store.game_ended_by_armistice(), "...and is recognised as an armistice ending")
	_check(announced_items.size() == 1 and str(announced_items[0]["body"]).contains("armistice"), "an armistice-ended game says so, not 'last faction standing'")

	# GameOverReportPanel: opens with the report, and minimize/restore toggles what shows.
	_check(not store.game_over_report_minimized, "starts expanded")
	var report_panel = load("res://scripts/game_over_report_panel.gd").new()
	root.add_child(report_panel)
	await process_frame
	_check(report_panel.visible and report_panel._panel.visible and not report_panel._tab.visible, "the table shows once there's a report")
	store.toggle_game_over_report_minimized()
	await process_frame
	_check(store.game_over_report_minimized and report_panel.visible and not report_panel._panel.visible and report_panel._tab.visible,
		"minimized: still tracked, but the tab shows instead of the table")
	store.toggle_game_over_report_minimized()
	await process_frame
	_check(not store.game_over_report_minimized and report_panel._panel.visible and not report_panel._tab.visible, "restored: the table is back")
	store.set_game_over_report([])
	_check(not report_panel.visible, "a fresh game (no report) hides the whole panel")

	# Auto-spectate: the human's own elimination flips opp_pause to NEVER, once, session-only.
	settings.opp_pause = settings.OppPause.PHASE
	store.set_state(_state({
		"NAA": {"code": "NAA", "mode": "HUMAN", "treasury_mpc": 0, "alliance": null, "eliminated": false},
		"UE": {"code": "UE", "mode": "BOT", "treasury_mpc": 0, "alliance": null, "eliminated": false},
	}))
	stepper._check_auto_spectate()
	_check(settings.opp_pause == settings.OppPause.PHASE, "not eliminated yet: no change")
	store.set_state(_state({
		"NAA": {"code": "NAA", "mode": "HUMAN", "treasury_mpc": 0, "alliance": null, "eliminated": true},
		"UE": {"code": "UE", "mode": "BOT", "treasury_mpc": 0, "alliance": null, "eliminated": false},
	}))
	stepper._check_auto_spectate()
	_check(settings.opp_pause == settings.OppPause.NEVER, "eliminated: defaults to spectating unpaused")
	settings.opp_pause = settings.OppPause.TURN  # the player changes it back by hand...
	stepper._check_auto_spectate()  # ...still eliminated, but the override only ever applies once
	_check(settings.opp_pause == settings.OppPause.TURN, "the auto-spectate override doesn't refire every state update")

	# SettingsPanel: Surrender / Propose Armistice button enabling.
	store.armistice = {}
	var panel = load("res://scripts/settings_panel.gd").new()
	root.add_child(panel)
	await process_frame
	_check(panel._surrender_btn.hold_seconds >= 2.0 and panel._armistice_btn.hold_seconds >= 2.0,
		"both are a genuinely long hold, well past the ordinary 0.8s Diplomacy one")
	store.set_state(_state({
		"NAA": {"code": "NAA", "mode": "HUMAN", "treasury_mpc": 0, "alliance": null, "eliminated": false},
		"UE": {"code": "UE", "mode": "BOT", "treasury_mpc": 0, "alliance": null, "eliminated": false},
	}))
	_check(not panel._surrender_btn.disabled, "surrender is available to a live human player")
	_check(not panel._armistice_btn.disabled, "so is proposing an armistice")
	store.set_state(_state({
		"NAA": {"code": "NAA", "mode": "HUMAN", "treasury_mpc": 0, "alliance": null, "eliminated": true},
		"UE": {"code": "UE", "mode": "BOT", "treasury_mpc": 0, "alliance": null, "eliminated": false},
	}))
	_check(panel._surrender_btn.disabled, "an already-eliminated human has nothing left to surrender")
	_check(not panel._armistice_btn.disabled, "but can still propose an armistice, exactly as required")
	store.set_state(_state({
		"NAA": {"code": "NAA", "mode": "HUMAN", "treasury_mpc": 0, "alliance": null, "eliminated": true},
		"UE": {"code": "UE", "mode": "BOT", "treasury_mpc": 0, "alliance": null, "eliminated": false},
	}, true))
	_check(panel._armistice_btn.disabled, "the game is over: nothing left to propose")

	# A pure SPECTATOR (a bot-vs-bot game, no HUMAN seat at all -- human_faction() is ""): Propose
	# Armistice is still available; Surrender is not (nothing of their own to give up).
	store.set_state(_state({
		"NAA": {"code": "NAA", "mode": "BOT", "treasury_mpc": 0, "alliance": null, "eliminated": false},
		"UE": {"code": "UE", "mode": "BOT", "treasury_mpc": 0, "alliance": null, "eliminated": false},
	}))
	_check(store.human_faction() == "", "no HUMAN seat: a pure spectator")
	_check(panel._surrender_btn.disabled, "a spectator has no faction of their own to surrender")
	_check(not panel._armistice_btn.disabled, "but can still propose an armistice")
	store.set_state(_state({
		"NAA": {"code": "NAA", "mode": "BOT", "treasury_mpc": 0, "alliance": null, "eliminated": false},
		"UE": {"code": "UE", "mode": "BOT", "treasury_mpc": 0, "alliance": null, "eliminated": false},
	}, true))
	_check(panel._armistice_btn.disabled, "...except once the game is over")
	store.set_state({})
	_check(panel._armistice_btn.disabled, "...or before any game has even started")

	# A spectator's proposal carries no "faction" (null) -- server/session.py treats that as nobody's
	# own seat, asking/auto-accepting every active faction instead. The client must announce and
	# display that gracefully, never crash trying to look up a faction that doesn't exist.
	announced_items.clear()
	stepper._on_message({"type": "armistice_proposed", "from": null, "awaiting": ["NAA"]})
	_check(store.armistice.get("from") == null, "a spectator's proposal in flight has a null proposer")
	store.set_state(_state({"NAA": {"code": "NAA", "mode": "HUMAN", "treasury_mpc": 0, "alliance": null, "eliminated": false}}))
	var armistice_window2 = load("res://scripts/armistice_window.gd").new()
	root.add_child(armistice_window2)
	await process_frame
	_check(armistice_window2.visible and str(armistice_window2._title.text).contains("spectator"), "the window names a null proposer as a spectator, not a crash")
	stepper._on_message({"type": "armistice_resolved", "from": null, "accepted": false, "declined_by": "NAA", "events": []})
	_check(announced_items.size() == 1 and str(announced_items[0]["body"]).contains("spectator"), "declining a spectator's proposal is announced, naming them as such")
	announced_items.clear()
	stepper._on_message({"type": "armistice_resolved", "from": null, "accepted": true, "declined_by": null,
		"events": [{"kind": "armistice", "turn": 3, "faction": null, "participants": ["NAA", "UE"]}]})
	_check(announced_items.size() == 1 and str(announced_items[0]["body"]).contains("spectator"), "and accepting one names them too, from the 'armistice' event's null faction")

	print("settings actions test: failures=%d" % _failures)
	quit(1 if _failures > 0 else 0)

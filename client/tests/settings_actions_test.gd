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


## A real, injected mouse click at `pos` (root/window coordinates) -- press then release, one frame
## apart -- so a test exercises Godot's actual Control GUI input hit-testing, not just a direct script
## call (which would pass even if a real click were silently swallowed by an overlapping control).
func _click_at(pos: Vector2) -> void:
	var down := InputEventMouseButton.new()
	down.button_index = MOUSE_BUTTON_LEFT
	down.position = pos
	down.global_position = pos
	down.pressed = true
	root.push_input(down)
	await process_frame
	var up := InputEventMouseButton.new()
	up.button_index = MOUSE_BUTTON_LEFT
	up.position = pos
	up.global_position = pos
	up.pressed = false
	root.push_input(up)
	await process_frame


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

	# round_number(): reads GameState's own authoritative counter when the server sends one (every live
	# game does), not the old global_turn // len(active_factions()) guess -- see its own docstring for why
	# that guess is wrong once an elimination has shrunk active_factions() partway through the game.
	var s: Dictionary = store.state.duplicate(true)
	s["round_number"] = 7
	store.set_state(s)
	_check(store.round_number() == 7, "reads GameState.round_number directly: %d" % store.round_number())
	var s2: Dictionary = store.state.duplicate(true)
	s2.erase("round_number")
	s2["global_turn"] = 3
	store.set_state(s2)
	_check(store.round_number() == 3 / maxi(1, store.active_factions().size()) + 1, "falls back to the old formula only for a state that predates the field")

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
		"alliance_history": [], "bot_type": null, "bot_strategy": null, "alliance_strategy": null, "alliance_behavior": null,
		"rounds_in_game": 6, "round_eliminated": null, "eliminated_by": null}, {
		"faction": "UE", "seat_type": "BOT", "victory_status": "Forced to Surrender", "elimination_reason": ["Economic"],
		"strategic_centers": 0, "territory_mpc": 0, "units_produced": 2, "units_destroyed": 1,
		"alliance_history": [], "bot_type": "Random", "bot_strategy": null, "alliance_strategy": null, "alliance_behavior": null,
		"rounds_in_game": 6, "round_eliminated": 3, "eliminated_by": "NAA"}]})
	_check(stepper.game_over and store.game_over_report.size() == 2, "the report lands in GameStore")
	_check(store.game_ended_by_armistice(), "...and is recognised as an armistice ending")
	_check(announced_items.size() == 1 and str(announced_items[0]["body"]).contains("armistice"), "an armistice-ended game says so, not 'last faction standing'")

	# GameOverReportPanel: opens with the report, and minimize/restore toggles what shows.
	_check(not store.game_over_report_minimized, "starts expanded")
	var report_panel = load("res://scripts/game_over_report_panel.gd").new()
	root.add_child(report_panel)
	await process_frame
	_check(report_panel.visible and report_panel._panel.visible and not report_panel._tab.visible, "the table shows once there's a report")
	_check(str(report_panel._title.text).contains("6 rounds"), "the title names how many rounds the game lasted: %s" % report_panel._title.text)
	var cell_texts := []
	for c in report_panel._grid.get_children():
		if c is Label:
			cell_texts.append(str(c.text))
	_check(cell_texts.has("3") and cell_texts.has("NAA"), "round eliminated and who eliminated them show up as cells: %s" % str(cell_texts))
	store.toggle_game_over_report_minimized()
	await process_frame
	_check(store.game_over_report_minimized and report_panel.visible and not report_panel._panel.visible and report_panel._tab.visible,
		"minimized: still tracked, but the tab shows instead of the table")
	store.toggle_game_over_report_minimized()
	await process_frame
	_check(not store.game_over_report_minimized and report_panel._panel.visible and not report_panel._tab.visible, "restored: the table is back")

	# The report is non-modal: a significant-event popup (AnnouncementWindow) queued while it's up must
	# stay fully dismissible, and the report itself must keep showing throughout -- neither blocks the other.
	var announcement_window2 = load("res://scripts/announcement_window.gd").new()
	root.add_child(announcement_window2)
	await process_frame
	announcement_window2.add([{"title": "UE eliminated", "body": "...", "color": Color.WHITE}, {"title": "Game over", "body": "...", "color": Color.WHITE}])
	await process_frame
	_check(announcement_window2.visible, "a popup can come up over the open report")
	_check(report_panel.visible and report_panel._panel.visible, "...and the report stays open underneath it")
	announcement_window2.acknowledge()
	await process_frame
	_check(announcement_window2.visible and announcement_window2._ok.text == "OK", "dismissing the first popup works fine with the report open, and shows the next")
	_check(report_panel.visible and report_panel._panel.visible, "...the report is still right there")
	announcement_window2.acknowledge()
	await process_frame
	_check(not announcement_window2.visible, "dismissing the last popup closes it")
	_check(report_panel.visible and report_panel._panel.visible, "...and the report is untouched by any of it")

	# The above only calls acknowledge() directly (script state), which would pass even if real clicks
	# were silently swallowed -- Godot's Control GUI input hit-testing follows TREE order, not z_index,
	# for overlapping same-canvas-layer MOUSE_FILTER_STOP regions. This is the actual bug that was
	# reported: main.gd used to add the report AFTER its popup windows, so the report -- later in the
	# tree, despite its lower z_index -- silently won every overlapping click, and a popup could only be
	# dismissed after minimizing the report first. Reproduce it for real: inject an actual mouse click on
	# the popup's OK button, both with the buggy order (proving the test WOULD have caught it) and with
	# main.gd's fixed order (report added first).
	root.size = Vector2i(1280, 800)  # headless SceneTree.root defaults to a tiny 64x64 -- too small for real layout/clicks
	var report_row := [{"faction": "NAA", "seat_type": "HUMAN", "victory_status": "Winner", "elimination_reason": null,
		"strategic_centers": 1, "territory_mpc": 5, "units_produced": 0, "units_destroyed": 0, "alliance_history": [],
		"bot_type": null, "bot_strategy": null, "alliance_strategy": null, "alliance_behavior": null,
		"rounds_in_game": 2, "round_eliminated": null, "eliminated_by": null}]

	var buggy_root := Control.new()
	buggy_root.set_anchors_and_offsets_preset(Control.PRESET_FULL_RECT)
	root.add_child(buggy_root)
	var buggy_announce = load("res://scripts/announcement_window.gd").new()
	buggy_root.add_child(buggy_announce)   # added FIRST -- the bug: the report below wins the click anyway
	var buggy_report = load("res://scripts/game_over_report_panel.gd").new()
	buggy_root.add_child(buggy_report)     # added AFTER -- the buggy order main.gd used to have
	store.set_game_over_report(report_row)
	buggy_announce.add([{"title": "Game over", "body": "x", "color": Color.WHITE}])
	for i in 3:
		await process_frame  # let layout settle so the OK button has a real global_position/size
	_check(buggy_report.visible and buggy_report._panel.visible and buggy_announce.visible, "both showing, overlapping, in the buggy order")
	await _click_at(buggy_announce._ok.global_position + buggy_announce._ok.size / 2.0)
	_check(buggy_announce.visible, "confirms the bug: with the report added AFTER the popup, the popup's OK button can't be clicked")
	buggy_root.queue_free()
	store.set_game_over_report([])

	var click_root := Control.new()
	click_root.set_anchors_and_offsets_preset(Control.PRESET_FULL_RECT)
	root.add_child(click_root)
	var click_report = load("res://scripts/game_over_report_panel.gd").new()
	click_root.add_child(click_report)     # added FIRST, like main.gd's fixed order
	var click_announce = load("res://scripts/announcement_window.gd").new()
	click_root.add_child(click_announce)   # added AFTER -- must win any overlapping click
	store.set_game_over_report(report_row)
	click_announce.add([{"title": "Game over", "body": "x", "color": Color.WHITE}])
	for i in 3:
		await process_frame
	_check(click_report.visible and click_report._panel.visible, "the report is showing for the click test")
	_check(click_announce.visible, "so is the popup, overlapping it")
	await _click_at(click_announce._ok.global_position + click_announce._ok.size / 2.0)
	_check(not click_announce.visible, "with the fixed order, a REAL click on the popup's OK button dismisses it, even overlapping the open report")
	click_root.queue_free()

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

	# Armistice cooldown (server/session.py's ARMISTICE_COOLDOWN_ROUNDS): a decline of THIS client's own
	# proposal starts one; someone else's decline (a different proposer identity) never does.
	store.set_state(_state({
		"NAA": {"code": "NAA", "mode": "HUMAN", "treasury_mpc": 0, "alliance": null, "eliminated": false},
		"UE": {"code": "UE", "mode": "BOT", "treasury_mpc": 0, "alliance": null, "eliminated": false},
	}))
	var st: Dictionary = store.state.duplicate(true)
	st["round_number"] = 3
	store.set_state(st)
	_check(store.armistice_cooldown_remaining() == 0, "no cooldown in effect yet")
	stepper._on_message({"type": "armistice_resolved", "from": "NAA", "accepted": false, "declined_by": "UE",
		"events": [], "cooldown_until_round": 8})
	_check(store.armistice_cooldown_until_round == 8, "a decline of MY OWN proposal (from == human_faction()) records the cooldown")
	_check(store.armistice_cooldown_remaining() == 5, "5 rounds left (round 3 of a cooldown until round 8): %d" % store.armistice_cooldown_remaining())
	_check(panel._armistice_btn.disabled, "the button is disabled while on cooldown")
	_check(str(panel._armistice_btn.tooltip_text).contains("5"), "...and says how many rounds are left: %s" % panel._armistice_btn.tooltip_text)
	var st2: Dictionary = store.state.duplicate(true)
	st2["round_number"] = 8
	store.set_state(st2)
	_check(store.armistice_cooldown_remaining() == 0, "the cooldown lifts once round_number catches up")
	_check(not panel._armistice_btn.disabled, "...and the button is enabled again")

	store.set_armistice_cooldown_until_round(-1)  # reset before the next check
	stepper._on_message({"type": "armistice_resolved", "from": "UE", "accepted": false, "declined_by": "NAA",
		"events": [], "cooldown_until_round": 99})
	_check(store.armistice_cooldown_until_round == -1, "a decline of SOMEONE ELSE's proposal (UE, not me) never sets my own cooldown")

	print("settings actions test: failures=%d" % _failures)
	quit(1 if _failures > 0 else 0)

extends SceneTree
## Headless checks for playing back a multi-player game's feed (TurnStepper's feed mode), from a feed the
## server recorded (tests/fixtures/feed_steps.json: entering a game whose bots play first, then their
## first three steps as broadcast -- phase_result, phase_queue, state each, numbered):
##   godot --headless --path client -s res://tests/feed_mode_test.gd
## Exits non-zero on any failure.

var _failures := 0


func _check(cond: bool, what: String) -> void:
	if not cond:
		_failures += 1
		printerr("FAIL: " + what)


func _initialize() -> void:
	await process_frame
	var stepper = root.get_node("Stepper")
	var store = root.get_node("GameStore")
	var settings = root.get_node("Settings")
	settings.opp_pause = settings.OppPause.PHASE  # every phase waits for Next
	var data = JSON.parse_string(FileAccess.get_file_as_string("res://tests/fixtures/feed_steps.json"))
	var steps: Array = data["steps"]

	stepper.feed_mode = true
	stepper.reset()
	store.set_multi_game({"id": "G_000001"}, ["AAC"])
	stepper._on_raw(data["feed"])
	_check(not store.state.is_empty(), "entering applies the state")
	_check(stepper._queued_phase == "START_OF_TURN" and stepper._queued_faction == "UE", "...and the step waiting now")
	_check(store.is_player("AAC") and not store.is_player("UE"), "my factions are the ones I hold")
	_check(store.human_faction() == "AAC", "my faction")

	# The server runs ahead: three steps arrive at once, and none is shown until Next.
	for m in steps:
		stepper._on_raw(m)
	_check(stepper._inbox.size() == steps.size(), "every step waits in the inbox (%d)" % stepper._inbox.size())
	_check(stepper._queued_phase == "START_OF_TURN", "the board still shows where playback is")
	stepper._on_raw(steps[0])
	_check(stepper._inbox.size() == steps.size(), "a message seen already is ignored")

	stepper._do_advance()  # Next
	_check(stepper._queued_phase == "PURCHASE", "Next plays back one step: now %s" % stepper._queued_phase)
	_check(stepper._inbox.size() == steps.size() - 3, "...taking its three messages from the inbox")
	stepper._do_advance()
	_check(stepper._queued_phase == "COMBAT_RESOLUTION", "and the next (%s)" % stepper._queued_phase)
	stepper._do_advance()
	_check(stepper._queued_phase == "NONCOMBAT_MOVE" and stepper._inbox.is_empty(), "and the last that has arrived")

	# Nothing more has arrived: Next waits for it, and it plays at once when it comes.
	stepper._do_advance()
	_check(stepper._awaiting and stepper._queued_phase == "NONCOMBAT_MOVE", "Next with nothing arrived waits")
	var later := {"type": "phase_result", "faction": "UE", "phase": "NONCOMBAT_MOVE", "events": [], "seq": 10, "epoch": steps[0]["epoch"]}
	var queue := {"type": "phase_queue", "faction": "UE", "phase": "CAPTURE", "events": [], "skipped": [], "seq": 11, "epoch": steps[0]["epoch"]}
	stepper._on_raw(later)
	stepper._on_raw(queue)
	_check(stepper._queued_phase == "CAPTURE" and not stepper._awaiting, "a step arriving while Next waits is played at once")

	# Another player's decision: the game says it is waiting for them.
	stepper._on_raw({"type": "waiting", "for": ["UE"], "seq": 12, "epoch": steps[0]["epoch"]})
	_check(store.waiting_for == ["UE"], "waiting is recorded")

	# A reply meant for this client alone (no seq) goes straight through, inbox or not.
	stepper._on_raw({"type": "phase_result", "faction": "UE", "phase": "CAPTURE", "events": [], "seq": 13, "epoch": steps[0]["epoch"]})
	var errors := []
	stepper.log_line.connect(func(t): errors.append(t))
	stepper._on_raw({"type": "error", "message": "nope", "to_sender": true})
	_check(errors.size() == 1 and stepper._inbox.size() == 1, "an error isn't held behind the inbox")

	# Entering while an armistice proposal is pending: this player is asked (and the game holds still);
	# a player who isn't asked sees whose answer it is waiting for.
	var with_armistice: Dictionary = data["feed"].duplicate(true)
	with_armistice["epoch"] = "a later run"
	with_armistice["armistice"] = {"from": "UE", "awaiting": ["AAC"], "automatic_after": null}
	stepper._on_raw(with_armistice)
	_check(store.armistice_pending() and store.armistice_faction() == "AAC", "the proposal is put to the player entering")
	_check(stepper.button_text == "Answer the armistice proposal" and not stepper.button_enabled, "Next waits for the answer")
	store.my_factions = ["UE"]
	stepper._refresh()
	_check(stepper.button_text.begins_with("Armistice proposed") and not stepper.button_enabled, "the proposer's game holds still")
	store.my_factions = ["AAC"]
	stepper._on_raw({"type": "armistice_resolved", "accepted": false, "from": "UE", "declined_by": "AAC", "seq": 99, "epoch": "a later run"})
	_check(store.armistice.is_empty() and not stepper.button_text.begins_with("Armistice"), "resolved: the game goes on")

	# Leaving the game: back to the one-game behaviour.
	stepper.feed_mode = false
	stepper.reset()
	store.set_multi_game({}, [])
	_check(stepper._inbox.is_empty() and not store.multi_game, "leaving clears the feed")
	quit(1 if _failures > 0 else 0)

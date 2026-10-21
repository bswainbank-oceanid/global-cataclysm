extends SceneTree
## Headless checks that every alliance request (and every Cruiser bombardment) -- any faction's, bot or human -- and its answer is written to the
## Game Log (EventText.describe), highlighted:
##   godot --headless --path client -s res://tests/game_log_highlights_test.gd
## Exits non-zero on any failure.

var _failures := 0


func _check(cond: bool, what: String) -> void:
	if not cond:
		_failures += 1
		printerr("FAIL: " + what)


func _plain(bbcode: String) -> String:
	return RegEx.create_from_string("\\[[^\\]]*\\]").sub(bbcode, "", true)


func _initialize() -> void:
	await process_frame
	var event_text = load("res://scripts/event_text.gd")  # (loaded now: it needs the autoloads, which a test's compile doesn't have)
	var sent: String = event_text.describe({"kind": "alliance_invite_sent", "faction": "NAA", "target": "EU"})
	var joined: String = event_text.describe({"kind": "alliance_joined", "turn": 3, "faction": "NAA", "target": "EU",
		"tag": "ALLIANCE_1", "new_alliance": true})
	var declined: String = event_text.describe({"kind": "alliance_declined", "turn": 4, "faction": "GPC", "target": "PAF"})
	_check(_plain(sent) == "Alliance request: NAA invites EU -- awaiting EU's answer", "the request, waiting: %s" % _plain(sent))
	_check(_plain(joined) == "Alliance request: NAA invites EU -- EU accepts: NAA and EU are now allied",
		"the request and its yes: %s" % _plain(joined))
	_check(_plain(declined) == "Alliance request: GPC invites PAF -- PAF declines", "the request and its no: %s" % _plain(declined))
	for line in [sent, joined, declined]:
		_check(line.begins_with("[bgcolor=%s]" % event_text.ALLIANCE_HIGHLIGHT), "highlighted: %s" % line)

	# Every Cruiser bombardment: hit or miss, the roll and the unit it was aimed at -- highlighted too.
	var base := {"kind": "bombardment", "faction": "EU", "territory_id": -1, "cruiser_unit_id": 9, "die": "D10",
		"bypass_hit": false, "target_unit_id": 5, "target_unit_type": "Infantry"}
	var miss: String = event_text.describe(base.merged({"roll": 3, "hit": false, "damage": 0, "target_hp_after": null, "eliminated": false}))
	var hit: String = event_text.describe(base.merged({"roll": 8, "hit": true, "damage": 1, "target_hp_after": 1, "eliminated": false}))
	var kill: String = event_text.describe(base.merged({"roll": 10, "hit": true, "damage": 2, "target_hp_after": 0, "eliminated": true}))
	_check(_plain(miss) == "Bombardment: EU's Cruiser fires on -1 -- rolls 3 (D10): MISS -- aimed at the Infantry", "a miss: %s" % _plain(miss))
	_check(_plain(hit) == "Bombardment: EU's Cruiser fires on -1 -- rolls 8 (D10): HIT -- the Infantry takes 1 damage (1 HP left)",
		"a hit: %s" % _plain(hit))
	_check(_plain(kill) == "Bombardment: EU's Cruiser fires on -1 -- rolls 10 (D10): HIT -- the Infantry is destroyed", "a kill: %s" % _plain(kill))
	for line in [miss, hit, kill]:
		_check(line.begins_with("[bgcolor=%s]" % event_text.BOMBARD_HIGHLIGHT), "highlighted: %s" % line)

	# The queue box too: a queued invitation and a queued bombardment, highlighted the same way.
	var plan := {"kind": "alliance_plan", "faction": "NAA", "action": "invite", "target": "EU"}
	var will: String = event_text.describe(plan.merged({"accepts": true}))
	var wont: String = event_text.describe(plan.merged({"accepts": false}))
	var ask: String = event_text.describe(plan.merged({"accepts": null}))
	_check(_plain(will) == "Alliance request: NAA invites EU -- EU will accept", "queued, a yes: %s" % _plain(will))
	_check(_plain(wont) == "Alliance request: NAA invites EU -- EU will decline", "queued, a no: %s" % _plain(wont))
	_check(_plain(ask) == "Alliance request: NAA invites EU -- awaiting EU's answer", "queued, a human to answer: %s" % _plain(ask))
	var queued_fire: String = event_text.describe({"kind": "bombardment_preview", "territory_id": -1,
		"cruiser": {"owner": "EU", "unit_id": 9, "unit_type": "Cruiser"}, "defenders": []})
	_check(queued_fire.begins_with("[bgcolor=%s]" % event_text.BOMBARD_HIGHLIGHT)
		and _plain(queued_fire).begins_with("Bombardment: EU's Cruiser will fire on -1"), "a queued bombardment: %s" % _plain(queued_fire))
	# one human's invitation to another, awaiting its answer: its request line is added to the queued step
	var stepper_script = load("res://scripts/turn_stepper.gd")
	var shown: Array = stepper_script._with_invitation({"events": [], "invitation": {"from": "NAA", "to": "EU", "answered": false}})
	_check(shown.size() == 1 and str(shown[0]["kind"]) == "alliance_invite_sent", "a pending human invitation shows in the queue")
	_check(stepper_script._with_invitation({"events": [], "invitation": {"from": "NAA", "to": "EU", "answered": true}}).is_empty(),
		"...not once answered")
	quit(1 if _failures > 0 else 0)

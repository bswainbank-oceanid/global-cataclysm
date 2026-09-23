extends Node
## Drives the watcher (autoload "Stepper"). The server decides each phase's
## orders ahead of time and holds them QUEUED (`phase_queue`); this shows
## them, and the Next button sends `next`, which makes the server execute
## the queue (`phase_result`), send a fresh `state`, and queue the following
## phase. See server/stepper.py for the protocol.
##
## The same pattern will carry player turns: orders picked in the upper
## right panel land in the queue, and Next executes them.

signal changed
signal queue_shown(header: String, skipped: Array, events: Array)
signal executed(header: String, events: Array)
signal announced(items: Array)              # significant events to announce: [{title, body, color}]
signal log_line(text: String)
signal game_reset  # a new game is starting: clear whatever belonged to the old one
signal battle_focus(preview: Dictionary)    # a battle paused: zoom the map to it and select it
signal battle_opened(preview: Dictionary)   # the player asked for it: show the battle board
signal combat_resolution_ended              # the phase moved on after battles were focused: zoom back out
signal battle_result(events: Array)         # ...its fought events, for the board to reveal

var button_text := "Connecting..."
var button_enabled := false  # Next can execute the queued phase
var needs_hold := false      # the button is a hold-to-submit (an irreversible order from the player)
var button_active := false   # the button can be pressed at all: Next, or Pause while playing
var game_over := false

var _queued_faction := ""  # whose phase is queued now
var _queued_phase := ""
var busy := Callable()  # set by main: true while arrows are still animating
var _last_queue: Dictionary = {}  # the queue message awaiting execution
var _playing := false  # running unpaused: the button offers Pause instead of Next
var _pause_requested := false  # Pause pressed mid-run; takes effect when the next phase is queued
var _auto := false  # the queued phase should run by itself (Settings say not to pause)
var _battle_zoomed := false        # the map was auto-zoomed to a battle during this Combat Resolution
var _battle_pending: Dictionary = {}  # a battle is paused, map zoomed to it, awaiting the player's go-ahead
var _battle_open := false          # the battle board is up: hold everything it would spoil
var _held: Array = []              # messages received meanwhile (result, state, next queue)
var _held_result: Dictionary = {}
var _edit_orders: Array = []  # the human's staged purchase as last sent/received: [{unit_type, qty, deploy_at}]
var _awaiting := false  # a `next` is in flight; the reply is the next queue
var _was_human_eliminated := false  # tracks the transition for _check_auto_spectate (fires once, not every state)


func _ready() -> void:
	Settings.changed.connect(func():
		if not _last_queue.is_empty() and not _awaiting and not _battle_open and _battle_pending.is_empty():
			_auto = not _should_pause(_last_queue)  # a live change applies to the phase waiting now
			_playing = _auto
			_refresh())
	Net.raw_message.connect(_on_message)
	Net.disconnected.connect(func():
		log_line.emit("[color=#ff7060]disconnected from server[/color]")
		_awaiting = false
		_refresh())


func _on_message(msg: Dictionary) -> void:
	if _battle_open and str(msg.get("type", "")) in ["state", "phase_queue"]:
		_held.append(msg)  # applying these would show the battle's outcome before the board does
		return
	match str(msg.get("type", "")):
		"state":
			GameStore.set_state(msg["game_state"])
			_check_auto_spectate()
		"phase_queue":
			_awaiting = false
			_last_queue = msg
			var block: Dictionary = msg.get("human", {})
			var kind := str(block.get("kind", ""))
			GameStore.set_invitation(msg.get("invitation", {}))
			if kind == "diplomacy":
				GameStore.set_human_purchase("", {})
				GameStore.set_human_move("", {})
				GameStore.set_human_alliance(str(msg["faction"]), block)
			elif block.has("kind"):  # a move phase (Combat / Non-Combat Move)
				GameStore.set_human_purchase("", {})
				GameStore.set_human_alliance("", {})
				GameStore.set_human_move(str(msg["faction"]), block)
			else:
				GameStore.set_human_move("", {})
				GameStore.set_human_alliance("", {})
				GameStore.set_human_purchase(str(msg["faction"]), block)
			_edit_orders = []
			for o in GameStore.human_purchase.get("orders", []):
				_edit_orders.append({"unit_type": o["unit_type"], "qty": int(o["qty"]), "deploy_at": int(o["deploy_at"])})
			if _battle_zoomed and str(msg["phase"]) != "COMBAT_RESOLUTION":
				_battle_zoomed = false
				combat_resolution_ended.emit()
			_auto = not _should_pause(msg) and not _pause_requested
			if _battle_pause(msg) and not _pause_requested:
				_open_battle(_battle_in(msg))  # sets _auto false; the board takes over from here
			_pause_requested = false  # a manual pause is held for exactly one phase; settings decide after
			_playing = _auto
			_queued_faction = str(msg["faction"])
			_queued_phase = str(msg["phase"])
			GameStore.set_queued_step(_queued_phase if ["START_OF_TURN", "RETURN_TO_BASE"].has(_queued_phase) else "")
			GameStore.set_queued_purchase(msg["events"][0] if _queued_phase == "PURCHASE" and not msg["events"].is_empty() else {})
			GameStore.set_queued_attack(msg["events"][0] if _queued_phase == "COMBAT_MOVE" and not msg["events"].is_empty() else {})
			var header := _header(_queued_faction, _queued_phase)
			if msg.has("battle"):
				header += " (battle %d of %d)" % [int(msg["battle"]["index"]) + 1, int(msg["battle"]["count"])]
			queue_shown.emit(header, msg.get("skipped", []), msg["events"])
		"phase_result":
			if _battle_open:
				_held_result = msg
				battle_result.emit(msg["events"])
			else:
				executed.emit(_header(str(msg["faction"]), str(msg["phase"])), msg["events"])
				_announce(msg["events"])
		"diplomacy_result":
			# One of the player's own Diplomacy actions, carried out at once: its outcome goes in the Events box.
			executed.emit("%s - Diplomacy" % str(msg["faction"]), msg["events"])
			_announce(msg["events"])
		"self_surrender_result":
			# The Settings "Surrender" action, carried out at once, from wherever the game stood -- not
			# tied to any queued phase, so (unlike diplomacy_result) there's no phase to name in the header.
			executed.emit("%s - Surrender" % str(msg["faction"]), msg["events"])
			_announce(msg["events"])
		"armistice_proposed":
			# A Propose Armistice offer is out and awaiting an answer from every human it names (bots
			# already answered, synchronously, before this ever arrives -- see server/session.py). "from"
			# is null when a pure SPECTATOR proposed it (nobody's own faction -- see propose_armistice).
			GameStore.set_armistice({"from": msg["from"], "awaiting": msg["awaiting"]})
			log_line.emit("[color=#ffd23f]%s proposes an armistice -- awaiting: %s[/color]" % [_proposer_name(msg["from"]), ", ".join(msg["awaiting"])])
		"armistice_resolved":
			GameStore.set_armistice({})
			if bool(msg.get("accepted", false)):
				_announce(msg.get("events", []))  # the 'armistice' event itself carries the announcement
			else:
				if _is_my_own_proposal(msg.get("from")):
					GameStore.set_armistice_cooldown_until_round(int(msg.get("cooldown_until_round", -1)))
				var by := str(msg.get("declined_by", ""))
				announced.emit([{"title": "Armistice declined", "color": Color(1.0, 0.6, 0.5),
					"body": "%s declined %s's armistice proposal. The game goes on." % [_name(by), _proposer_name(msg.get("from"))]}])
		"error":
			_awaiting = false
			log_line.emit("[color=#ff7060]server: %s[/color]" % str(msg.get("message", "")))
		"game_started":
			reset()
		"game_over":
			_awaiting = false
			_auto = false
			_playing = false
			game_over = true
			GameStore.set_game_over_report(msg.get("report", []))
			log_line.emit("[b]Game over[/b]")
			announced.emit([game_over_announcement()])
	_refresh()


# ---- announcements ------------------------------------------------------------------

static func _name(code: String) -> String:
	return "[b]%s[/b] (%s)" % [GameData.factions[code].name, code] if GameData.factions.has(code) else code


static func _color(code: String) -> Color:
	return GameData.factions[code].color.lightened(0.2) if GameData.factions.has(code) else HudStyle.GOLD


## An armistice proposal's "from"/"faction" is null when a pure SPECTATOR proposed it (nobody's
## own faction, watching rather than playing -- see propose_armistice); this names either one.
static func _proposer_name(code) -> String:
	return _name(str(code)) if code != null else "a spectator"


## Whether an armistice_resolved's "from" was THIS client's own proposal -- its own human_faction()
## if it plays one, or null (a spectator's identity) if it doesn't. Used to know whether a decline's
## cooldown (armistice_resolved's cooldown_until_round) applies to this client, not someone else's.
static func _is_my_own_proposal(from) -> bool:
	var me := GameStore.human_faction()
	return from == null if me == "" else from == me


## Turn the significant events of an executed phase (an elimination, an alliance formed
## or left, the player's own invitation turned down) into announcements for the panel in
## the middle of the screen.
func _announce(events: Array) -> void:
	var items := []
	var surrendered := {}
	for e in events:
		var k := str(e.get("kind", ""))
		if k == "surrender":
			surrendered[str(e["target"])] = true
		elif k == "self_surrender":
			surrendered[str(e["faction"])] = true
	for e in events:
		match str(e.get("kind", "")):
			"surrender":
				var t := str(e["target"])
				items.append({"title": "%s surrenders" % t, "color": _color(t),
					"body": "%s has forced %s to surrender: %s.\n\n%s is out of the game and all of its units are removed from the board; its territory stays where it is." % [
						_name(str(e["faction"])), _name(t), EventText.surrender_reasons(e.get("reasons", [])), _name(t)]})
			"self_surrender":
				var f := str(e["faction"])
				items.append({"title": "%s surrenders" % f, "color": _color(f),
					"body": "%s has surrendered.\n\n%s is out of the game and all of its units are removed from the board; its territory stays where it is." % [_name(f), _name(f)]})
			"armistice":
				var names := []
				for p in e.get("participants", []):
					names.append(_name(str(p)))
				items.append({"title": "Armistice agreed", "color": HudStyle.GOLD,
					"body": "%s proposed an armistice, and everyone agreed: %s.\n\nThe game ends here; nobody is declared the winner." % [_proposer_name(e.get("faction")), ", ".join(names)]})
			"faction_eliminated":
				var f := str(e["faction"])
				if not surrendered.has(f):
					items.append({"title": "%s eliminated" % f, "color": _color(f),
						"body": "%s is out of the game and all of its units are removed from the board." % _name(f)})
			"alliance_joined":
				var f := str(e["faction"])
				var t := str(e["target"])
				if bool(e.get("new_alliance", true)):
					items.append({"title": "New alliance", "color": _color(f),
						"body": "%s and %s have formed an alliance.\n\nAllies don't fight each other and defend together. The game ends when every remaining faction is allied." % [_name(f), _name(t)]})
				else:
					items.append({"title": "%s joins an alliance" % t, "color": _color(t),
						"body": "%s has joined %s's alliance." % [_name(t), _name(f)]})
			"alliance_declined":
				# Only the player's own invitations: a bot being turned down is nobody's news.
				if GameStore.is_player(str(e["faction"])):
					var t := str(e["target"])
					items.append({"title": "%s declines your invitation" % t, "color": _color(t),
						"body": "%s has turned down your invitation to an alliance." % _name(t)})
			"alliance_withdrawal":
				var f := str(e["faction"])
				var others := []
				for m in e.get("former_members", []):
					if str(m) != f:
						others.append(_name(str(m)))
				items.append({"title": "%s leaves its alliance" % f, "color": _color(f),
					"body": "%s has withdrawn from its alliance%s." % [_name(f), " with " + ", ".join(others) if not others.is_empty() else ""]})
	if not items.is_empty():
		announced.emit(items)


## The game's end: who is left.
func game_over_announcement() -> Dictionary:
	if GameStore.game_ended_by_armistice():
		# The "Armistice agreed" announcement (above, from the 'armistice' turn_log event) already
		# named the participants; this one just marks that the game itself is over now.
		return {"title": "Game over", "color": HudStyle.GOLD,
			"body": "The game has ended by armistice: every remaining faction agreed to stop here. See the Game Over report for the final standings."}
	var left: Array = GameStore.active_factions()
	var body := ""
	var color := HudStyle.GOLD
	if left.size() == 1:
		body = "%s is the last faction standing, and wins the game." % _name(str(left[0]))
		color = _color(str(left[0]))
	elif left.is_empty():
		body = "No faction is left in play."
	else:
		var names := []
		for c in left:
			names.append(_name(str(c)))
		body = "Every remaining faction is allied: %s.\n\nWith nobody left to fight, the alliance wins." % ", ".join(names)
	return {"title": "Game over", "body": body, "color": color}


static func _header(faction: String, phase: String) -> String:
	return "%s - %s" % [faction, GameStore.PHASE_LABELS.get(phase, phase)]


func _refresh() -> void:
	button_enabled = false
	button_active = false
	needs_hold = false
	if game_over:
		button_text = "Game over"
	elif GameStore.invitation_pending():
		button_text = "Answer %s's alliance invitation" % str(GameStore.invitation["from"])
	elif _battle_open:
		button_text = "Battle in progress..."
	elif not _battle_pending.is_empty():
		button_text = "Next  >  Open battle board  (%s)" % GameData.territories[int(_battle_pending["territory_id"])]["name"]
		button_active = true
	elif _playing:
		button_text = "Pausing..." if _pause_requested else "Pause"
		button_active = not _pause_requested
	elif _queued_phase != "" and not _awaiting:
		needs_hold = GameStore.is_player(_queued_faction) and ["PURCHASE", "COMBAT_MOVE", "NONCOMBAT_MOVE", "DIPLOMACY"].has(_queued_phase)
		if needs_hold:
			button_text = "Hold to submit  -  %s" % _header(_queued_faction, _queued_phase)
		elif _queued_phase == "START_OF_TURN":
			button_text = "Next  >  Start %s's turn" % _queued_faction
		else:
			button_text = "Next  >  Execute %s" % _header(_queued_faction, _queued_phase)
		button_enabled = true
		button_active = true
	elif _awaiting:
		button_text = "Executing..."
	else:
		button_text = "Connecting..." if not Net.is_open() else "Waiting for server..."
	changed.emit()


## Whether the queued phase waits for the Next button, per Settings.
func _should_pause(msg: Dictionary) -> bool:
	var inv: Dictionary = msg.get("invitation", {})
	if not inv.is_empty() and not bool(inv.get("answered", false)) and GameStore.is_player(str(inv["to"])):
		return true  # the player has to answer an invitation first
	var faction := str(msg["faction"])
	var battle := _battle_in(msg)
	if GameStore.is_player(faction):
		# A player's own turn needs their decisions, so every phase waits --
		# except Combat Resolution, which only waits per battle if asked.
		if str(msg["phase"]) == "COMBAT_RESOLUTION":
			return not battle.is_empty() and Settings.your_pause_battle
		return true
	match Settings.opp_pause:
		Settings.OppPause.PHASE:
			return true
		Settings.OppPause.TURN:
			if str(msg["phase"]) == "START_OF_TURN":
				return true  # one pause per turn, before it starts
	if battle.is_empty():
		return false
	if Settings.opp_pause_battle:
		return true
	if Settings.opp_pause_battle_mine:
		for side in ["attackers", "defenders"]:
			for u in battle[side]:
				if GameStore.is_player(str(u["owner"])):
					return true
	return false


## True when this queued battle should stop for the battle board, as opposed to
## an ordinary phase pause (which just offers the Next button).
func _battle_pause(msg: Dictionary) -> bool:
	var battle := _battle_in(msg)
	if battle.is_empty():
		return false
	if GameStore.is_player(str(msg["faction"])):
		return Settings.your_pause_battle
	if Settings.opp_pause_battle:
		return true
	if Settings.opp_pause_battle_mine:
		for side in ["attackers", "defenders"]:
			for u in battle[side]:
				if GameStore.is_player(str(u["owner"])):
					return true
	return false


## A battle paused: zoom to it and select it, then WAIT -- the board opens only
## when the player says so (the Next button).
func _open_battle(preview: Dictionary) -> void:
	_battle_zoomed = true
	_battle_pending = preview
	_auto = false
	_playing = false
	battle_focus.emit(preview)


## Next was pressed on a paused battle: bring up its board.
func _show_battle_board() -> void:
	var preview := _battle_pending
	_battle_pending = {}
	_battle_open = true
	_held = []
	_held_result = {}
	battle_opened.emit(preview)
	_refresh()


## Dev/scripted: is a battle paused, waiting for its board to be opened?
func has_pending_battle() -> bool:
	return not _battle_pending.is_empty()


## The board asked for the battle to be fought (its first Next Roll).
func execute_open_battle() -> void:
	if _battle_open:
		_do_advance()


## End Battle: show what was held back -- the log entry, then the new state and
## whatever comes next -- exactly as if the battle had just been fought.
func release_battle() -> void:
	if not _battle_open:
		return
	_battle_open = false
	if not _held_result.is_empty():
		executed.emit(_header(str(_held_result["faction"]), str(_held_result["phase"])), _held_result["events"])
		_announce(_held_result["events"])
	_held_result = {}
	var pending := _held
	_held = []
	for m in pending:
		_on_message(m)
	_refresh()


## The battle_preview a Combat Resolution queue is holding ({} if none).
func _battle_in(msg: Dictionary) -> Dictionary:
	if str(msg["phase"]) != "COMBAT_RESOLUTION":
		return {}
	for e in msg["events"]:
		if str(e.get("kind", "")) == "battle_preview":
			if e["defenders"].is_empty():
				return {}  # walking into an empty territory: a capture, not a battle
			return e
	return {}


## Unpaused phases run themselves as soon as the previous phase's arrows have
## finished shortening -- no extra delay, and no waiting at all when there
## were no arrows.
func _process(_delta: float) -> void:
	if _auto and not _awaiting and not game_over and not _queued_phase.is_empty() and not GameStore.announcement_open:
		if busy.is_null() or not busy.call():
			_auto = false
			_do_advance()


## The button's press: Pause while phases are running by themselves, else Next.
## Pause stops at the phase in hand (or, mid-execution, at the next one) and
## offers Next exactly like a scheduled pause; after that Settings apply again.
func button_pressed() -> void:
	if not _battle_pending.is_empty():
		_show_battle_board()
		return
	if not _playing:
		advance()
		return
	if _auto and not _awaiting:
		_auto = false
		_playing = false
	else:
		_pause_requested = true
	_refresh()


## A new game replaces the running one: drop every trace of it.
func reset() -> void:
	game_over = false
	_queue_reset()
	GameStore.reset()
	game_reset.emit()
	_refresh()


func _queue_reset() -> void:
	_last_queue = {}
	_queued_faction = ""
	_queued_phase = ""
	_awaiting = false
	_auto = false
	_playing = false
	_pause_requested = false
	_battle_pending = {}
	_battle_open = false
	_battle_zoomed = false
	_held = []
	_held_result = {}
	_edit_orders = []
	GameStore.set_invitation({})
	_was_human_eliminated = false


## The player's purchase edits: send the whole staged list to the server, which
## validates it and answers with the refreshed queue (or an error and the old one).
func purchase_add(unit_type: String, tid: int) -> void:
	_change_purchase(unit_type, tid, 1)


func purchase_remove(unit_type: String, tid: int) -> void:
	_change_purchase(unit_type, tid, -1)


func _change_purchase(unit_type: String, tid: int, delta: int) -> void:
	if not GameStore.human_purchase_active():
		return
	var found := false
	for o in _edit_orders:
		if o["unit_type"] == unit_type and int(o["deploy_at"]) == tid:
			o["qty"] = int(o["qty"]) + delta
			found = true
			break
	if not found:
		if delta < 0:
			return
		_edit_orders.append({"unit_type": unit_type, "qty": 1, "deploy_at": tid})
	_edit_orders = _edit_orders.filter(func(o): return int(o["qty"]) > 0)
	Net.send_msg({"type": "stage_purchase", "faction": GameStore.human_purchase["faction"], "orders": _edit_orders})


## One of the player's Diplomacy actions, carried out at once: "invite" or "surrender" (a target
## faction), or "withdraw". The outcome comes back as a diplomacy_result for the Events box.
func diplomacy_action(action: String, target: String = "") -> void:
	if not GameStore.human_alliance_active():
		return
	var msg := {"type": "diplomacy_action", "faction": GameStore.human_alliance["faction"], "action": action}
	if target != "":
		msg["target"] = target
	Net.send_msg(msg)


## The player's answer to a bot's invitation.
func invitation_respond(accept: bool) -> void:
	if GameStore.invitation_pending():
		Net.send_msg({"type": "respond_invitation", "faction": GameStore.invitation["to"], "accept": accept})


# ---- Settings actions: Surrender / Propose Armistice -----------------------------
# Both are out-of-band: unlike diplomacy_action, neither needs a phase queued for the
# human at all -- they work from wherever the game currently stands (a very-long-press
# in Settings guards against a stray click; see settings_panel.gd's HoldButtons).

## Give up now: the human's own faction is eliminated at once, from wherever the game
## currently stands.
func surrender() -> void:
	var me := GameStore.human_faction()
	if me != "":
		Net.send_msg({"type": "surrender", "faction": me})


## Propose ending the game right here, immediately. Bots always accept at once; any
## other human player seated is asked (armistice_proposed / armistice_resolved). Also
## callable with no faction of your own at all -- a pure SPECTATOR (this client, in a
## game with no HUMAN seat: has_player() is false, human_faction() is "") -- in which
## case the server asks/auto-accepts every active faction, since there's no proposer's
## own seat to fold in for free.
func propose_armistice() -> void:
	var me := GameStore.human_faction()
	var msg := {"type": "propose_armistice"}
	if me != "":
		msg["faction"] = me
	Net.send_msg(msg)


## The human's own answer to someone ELSE's pending armistice proposal.
func respond_armistice(accept: bool) -> void:
	if GameStore.armistice_pending():
		Net.send_msg({"type": "respond_armistice", "faction": GameStore.human_faction(), "accept": accept})


## When the human's own faction is eliminated, default to letting the rest of the game
## play out unpaused (Settings.OppPause.NEVER) so there's nothing left to click through --
## a SESSION-ONLY override (never Settings.commit(), so it doesn't overwrite the player's
## saved preferences file), applied exactly once, the moment their faction is first seen
## eliminated. Propose Armistice stays available to them regardless (settings_panel.gd).
func _check_auto_spectate() -> void:
	var me := GameStore.human_faction()
	if me == "":
		return
	var eliminated: bool = bool(GameStore.faction_state(me).get("eliminated", false))
	if eliminated and not _was_human_eliminated:
		Settings.opp_pause = Settings.OppPause.NEVER
		Settings.changed.emit()
	_was_human_eliminated = eliminated


## Send the human's whole staged move list (the server validates it and answers
## with the refreshed queue, or an error and the old one).
func _send_moves(orders: Array) -> void:
	Net.send_msg({"type": "stage_moves", "faction": GameStore.move_faction(), "orders": orders})


## The selected units were dropped on `dest`: queue their move.
func move_commit(dest: int) -> void:
	var targets := GameStore.move_targets()
	if not targets.has(dest):
		return
	var committed: Array = targets[dest]["orders"]
	GameStore.set_move_extend(_extend_offer_for(committed, dest))
	var orders := GameStore.staged_orders_plain()
	orders.append_array(committed)
	GameStore.move_selected.clear()  # they are committed; the rest stay available
	_send_moves(orders)


## Whether the just-committed order(s) qualify to offer a second, explicit hop
## (see game_store.gd's move_extend): exactly one unit, whose own continuations
## (still visible in human_move's PRE-commit options -- gone from the server's
## own options the moment this unit is actually staged) list `dest` as a
## pass-through-capable first hop with further destinations of its own.
func _extend_offer_for(committed: Array, dest: int) -> Dictionary:
	if committed.size() != 1 or GameStore.human_move.get("kind", "") != "combat":
		return {}
	var uid := int(committed[0]["unit_id"])
	var opt: Dictionary = GameStore.human_move["options"].get(uid, {})
	var continuations: Dictionary = opt.get("continuations", {})
	if not continuations.has(dest):
		return {}
	return {"unit_id": uid, "unit_type": str(opt.get("unit_type", "")), "first_hop": dest,
		"origin": int(opt.get("origin", -1)), "targets": continuations[dest]}


## The extend offer's unit was dropped on `dest`: replace its already-queued
## one-hop order with the full route, and clear the offer.
func move_extend_commit(dest: int) -> void:
	var targets: Dictionary = GameStore.move_extend_targets()
	if not targets.has(dest):
		return
	var uid := int(GameStore.move_extend["unit_id"])
	var path := []
	for x in targets[dest]:
		path.append(int(x))
	var orders: Array = GameStore.staged_orders_plain([uid])  # drop the old one-hop order for this unit
	orders.append({"unit_id": uid, "path": path})
	GameStore.set_move_extend({})
	_send_moves(orders)


## Take units (by id) out of the queued moves.
func move_recall(unit_ids: Array) -> void:
	if not GameStore.human_move_active():
		return
	if GameStore.move_extend_active() and unit_ids.has(int(GameStore.move_extend["unit_id"])):
		GameStore.set_move_extend({})
	_send_moves(GameStore.staged_orders_plain(unit_ids))


## One press: execute the queued phase and move on to the next.
func advance() -> void:
	if not button_enabled:
		return
	_do_advance()


func _do_advance() -> void:
	_awaiting = true
	Net.send_msg({"type": "next"})
	_refresh()


## Waits (bounded) until a press is possible, for scripted runs.
func wait_ready(max_seconds: float = 15.0) -> void:
	var waited := 0.0
	while not button_enabled and not game_over and not _battle_open and _battle_pending.is_empty() and waited < max_seconds:
		await get_tree().process_frame
		waited += get_process_delta_time()


## Dev/scripted: press Next `n` times, waiting for the server between presses.
func press(n: int) -> void:
	for i in n:
		await wait_ready()
		if not button_enabled:
			return
		advance()
	await wait_ready()

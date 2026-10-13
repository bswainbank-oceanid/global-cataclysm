extends Node
## Playback settings (autoload "Settings"): when the watcher stops and waits
## for the Next button; and the Display Settings (full screen). Read by
## TurnStepper; edited in SettingsPanel (Display: MainMenu); kept in
## user://settings.cfg (scripted screenshot runs use the defaults and never
## write it) -- and, on the multi-player server, with the player's account
## (Account: every one but full screen, which belongs to the device), so each
## account has its own wherever it logs in.
##
## "Yours" means factions in HUMAN mode. In a bot-vs-bot game there are none,
## so the "involving your units" and "your turn" options can never trigger.

signal changed

enum OppPause { NEVER, TURN, PHASE }

const PATH := "user://settings.cfg"

var opp_pause := OppPause.PHASE          # opponents' turns: pause never / once per turn / every phase
var opp_pause_battle := false            # ...and before every battle
var opp_pause_battle_mine := true        # ...or only before battles involving your units
var your_pause_battle := true            # on your own turns: before every battle
# Battle board: how much one press of Next Roll reveals (BattleModel.Resolve),
# remembered per side of the board between battles.
var resolve_attacker := BattleModel.Resolve.UNIT_TYPE
var resolve_defender := BattleModel.Resolve.UNIT_TYPE
var fullscreen := false                  # Display Settings: the window fills the screen
var sound_volume := 50                   # the unit sounds' volume, 0-100 (Sfx)
var sound_muted := false
var music_volume := 50                   # the soundtrack's volume, 0-100 (Music)
var music_muted := false
var news_seen := ""                      # the newest News entry the player has seen (its id): News pulses for a newer one

# The ones kept with the account (not full screen: that's the device's), and their defaults -- a new
# account's, since the server keeps none for it yet.
const ACCOUNT_DEFAULTS := {"opp_pause": OppPause.PHASE, "opp_pause_battle": false, "opp_pause_battle_mine": true,
	"your_pause_battle": true, "resolve_attacker": BattleModel.Resolve.UNIT_TYPE,
	"resolve_defender": BattleModel.Resolve.UNIT_TYPE, "sound_volume": 50, "sound_muted": false,
	"music_volume": 50, "music_muted": false, "news_seen": ""}

var _from_account := false  # applying the account's: not a change of the player's to save back


func _ready() -> void:
	if Dbg.args.has("shot"):
		# Scripted runs: defaults, unless overridden (--pause=never|turn|phase, --pause_battle, --your_pause_battle).
		if Dbg.args.has("pause"):
			opp_pause = OppPause[str(Dbg.args["pause"]).to_upper()]
		opp_pause_battle = Dbg.args.has("pause_battle")
		opp_pause_battle_mine = false
		your_pause_battle = Dbg.args.has("your_pause_battle")
		if Dbg.args.has("resolve"):  # --resolve=entire|round|side|type|unit, for both sides
			var mode: int = {"entire": 0, "round": 1, "side": 2, "type": 3, "unit": 4}[str(Dbg.args["resolve"])]
			resolve_attacker = mode
			resolve_defender = mode
		return
	var cfg := ConfigFile.new()
	if cfg.load(PATH) != OK:
		return
	opp_pause = int(cfg.get_value("playback", "opp_pause", opp_pause))
	opp_pause_battle = bool(cfg.get_value("playback", "opp_pause_battle", opp_pause_battle))
	opp_pause_battle_mine = bool(cfg.get_value("playback", "opp_pause_battle_mine", opp_pause_battle_mine))
	your_pause_battle = bool(cfg.get_value("playback", "your_pause_battle", your_pause_battle))
	resolve_attacker = int(cfg.get_value("battle", "resolve_attacker", resolve_attacker))
	resolve_defender = int(cfg.get_value("battle", "resolve_defender", resolve_defender))
	fullscreen = bool(cfg.get_value("display", "fullscreen", fullscreen))
	sound_volume = clampi(int(cfg.get_value("sound", "volume", sound_volume)), 0, 100)
	sound_muted = bool(cfg.get_value("sound", "muted", sound_muted))
	music_volume = clampi(int(cfg.get_value("music", "volume", music_volume)), 0, 100)
	music_muted = bool(cfg.get_value("music", "muted", music_muted))
	news_seen = str(cfg.get_value("news", "seen", news_seen))
	_apply_display()


## The settings kept with the account, {name: value}.
func account_values() -> Dictionary:
	var out := {}
	for key in ACCOUNT_DEFAULTS:
		out[key] = get(key)
	return out


## The account's settings, as the server keeps them (any left out: the default), in place of this device's.
func apply_account(values: Dictionary) -> void:
	for key in ACCOUNT_DEFAULTS:
		var v = values.get(key, ACCOUNT_DEFAULTS[key])
		match typeof(ACCOUNT_DEFAULTS[key]):
			TYPE_BOOL:
				set(key, bool(v))
			TYPE_STRING:
				set(key, str(v))
			_:
				set(key, int(v))
	sound_volume = clampi(sound_volume, 0, 100)
	music_volume = clampi(music_volume, 0, 100)
	_from_account = true
	commit()
	_from_account = false


## Whether the change just committed came from the account itself (Account doesn't send those back).
func applying_account() -> bool:
	return _from_account


## Call after changing a field: notifies listeners and saves.
func commit() -> void:
	changed.emit()
	_apply_display()
	if Dbg.args.has("shot"):
		return
	var cfg := ConfigFile.new()
	cfg.set_value("playback", "opp_pause", opp_pause)
	cfg.set_value("playback", "opp_pause_battle", opp_pause_battle)
	cfg.set_value("playback", "opp_pause_battle_mine", opp_pause_battle_mine)
	cfg.set_value("playback", "your_pause_battle", your_pause_battle)
	cfg.set_value("battle", "resolve_attacker", resolve_attacker)
	cfg.set_value("battle", "resolve_defender", resolve_defender)
	cfg.set_value("display", "fullscreen", fullscreen)
	cfg.set_value("sound", "volume", sound_volume)
	cfg.set_value("sound", "muted", sound_muted)
	cfg.set_value("music", "volume", music_volume)
	cfg.set_value("music", "muted", music_muted)
	cfg.set_value("news", "seen", news_seen)
	cfg.save(PATH)


func _apply_display() -> void:
	if DisplayServer.get_name() == "headless":
		return
	var want := DisplayServer.WINDOW_MODE_FULLSCREEN if fullscreen else DisplayServer.WINDOW_MODE_WINDOWED
	if DisplayServer.window_get_mode() != want:
		DisplayServer.window_set_mode(want)

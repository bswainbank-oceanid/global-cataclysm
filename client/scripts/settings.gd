extends Node
## Playback settings (autoload "Settings"): when the watcher stops and waits
## for the Next button. Read by TurnStepper; edited in SettingsPanel; kept in
## user://settings.cfg (scripted screenshot runs use the defaults and never
## write it).
##
## "Yours" means factions in HUMAN mode. In a bot-vs-bot game there are none,
## so the "involving your units" and "your turn" options can never trigger.

signal changed

enum OppPause { NEVER, TURN, PHASE }

const PATH := "user://settings.cfg"

var opp_pause := OppPause.PHASE          # opponents' turns: pause never / once per turn / every phase
var opp_pause_battle := false            # ...and before every battle
var opp_pause_battle_mine := false       # ...or only before battles involving your units
var your_pause_battle := false           # on your own turns: before every battle


func _ready() -> void:
	if Dbg.args.has("shot"):
		# Scripted runs: defaults, unless overridden (--pause=never|turn|phase, --pause_battle).
		if Dbg.args.has("pause"):
			opp_pause = OppPause[str(Dbg.args["pause"]).to_upper()]
		opp_pause_battle = Dbg.args.has("pause_battle")
		return
	var cfg := ConfigFile.new()
	if cfg.load(PATH) != OK:
		return
	opp_pause = int(cfg.get_value("playback", "opp_pause", opp_pause))
	opp_pause_battle = bool(cfg.get_value("playback", "opp_pause_battle", opp_pause_battle))
	opp_pause_battle_mine = bool(cfg.get_value("playback", "opp_pause_battle_mine", opp_pause_battle_mine))
	your_pause_battle = bool(cfg.get_value("playback", "your_pause_battle", your_pause_battle))


## Call after changing a field: notifies listeners and saves.
func commit() -> void:
	changed.emit()
	if Dbg.args.has("shot"):
		return
	var cfg := ConfigFile.new()
	cfg.set_value("playback", "opp_pause", opp_pause)
	cfg.set_value("playback", "opp_pause_battle", opp_pause_battle)
	cfg.set_value("playback", "opp_pause_battle_mine", opp_pause_battle_mine)
	cfg.set_value("playback", "your_pause_battle", your_pause_battle)
	cfg.save(PATH)

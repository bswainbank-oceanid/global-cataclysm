extends Node
## Scripted screenshot mode (autoload "Dbg"), so UI can be verified without
## a human at the keyboard. Args come after `--` on the Godot command line:
##   --shot=<abs path.png>   render, save the frame, quit
##   --wait=<frames>         frames to let things settle first (default 8)
##   --cam=x,y,zoom          jump the map camera
##   --hover=<id>            force-hover a territory
##   --select=<id>           force-select a territory
##   --state=<abs path.json> load a GameState dict into GameStore
##   --badges=flag|strip|category   unit badge style
##   --server[=ws://host:port]  connect to the game server (watches the all-bot game)
##   --steps=<n>             press the Next button n times (needs --server)
##   --pause=never|turn|phase, --pause_battle   playback settings for this run
##   --resolve=entire|round|side|type|unit   battle board Resolve setting, both sides
##   --launch[=HUMAN:NAA,BOT,...]  show the launch screen (and play its Start with these seats)
##   --resume            go straight to the game running on the server
##   --start_allied, --no_withdraw, --rejoin   alliance options for a scripted launch
##   --fixed_order, --combat_first_turn   launch options for scripted runs
##   --seed=<n>          a game that replays exactly (setup, bots and dice); --max_alliance=<n>
##   --bot_ai=random|strategy   the AI every scripted bot seat plays (default strategy)
##   --alliance=none|withdraw|invite:UE   a scripted player's Alliances choice
##   --after_steps=<n>   with --invite_answer: press Next n more times afterwards
##   --invite_answer=accept|decline|wait  answer (or just wait for) a bot's invitation
##   --buy=Unit:territory,...  add units to the human's purchase queue (a scripted player)
##   --move_to=<id>     drop the selected units on that space (Combat/Non-Combat Move)
##   --recall=<unit,..> take units out of the queued moves
##   --select2=<id>     select another space afterwards
##   --hold=<seconds>   hold the submit button that long
##   --battle_rolls=<n>      press the open battle board's button n times
##   --bombardment_rolls=<n> press n times through paused bombardments (fire, then continue)
##   --wheel=x,y,steps       inject mouse-wheel steps at a screen point (+ = zoom in)
##   --drag=x1,y1,x2,y2      inject a left-button drag between two screen points
##   --click=x,y[;x,y;w30;...]  inject left clicks at screen points (w<n>: wait n frames between)
##   (injected input runs in that order: wheel, drag, click)
##   --strategy_log     show (and open) the Strategy Log tab
##   --profile=<name>   keep this client's login apart (user://login_<name>.cfg): two players on one machine
##   --dev_user=<name>  log in to a multi-player server as <name>@dev.local (registered the first time)
## Other scripts read `Dbg.args` for the scene-setup ones.

var args := {}
var injecting := false    # true while main.gd is injecting scripted input
var scene_ready := false  # main.gd sets this once scripted setup/input has finished


func _ready() -> void:
	for a in OS.get_cmdline_user_args():
		if a.begins_with("--"):
			var kv := a.substr(2).split("=", true, 1)
			args[kv[0]] = kv[1] if kv.size() > 1 else "true"
	_web_args()
	if args.has("shot"):
		_take_shot()


## In a browser (the web build) there's no command line: a page served from this machine takes the same
## options from its address instead (index.html?dev_user=Ann&click=...), for testing. Never a page from
## anywhere else, so a real server's players can't use them. (--shot can't save a file there.)
func _web_args() -> void:
	if not OS.has_feature("web"):
		return
	var host := str(JavaScriptBridge.eval("location.hostname", true))
	if host not in ["localhost", "127.0.0.1"]:
		return
	var query := str(JavaScriptBridge.eval("location.search", true)).trim_prefix("?")
	for pair in query.split("&", false):
		var kv := pair.split("=", true, 1)
		if kv[0] != "server":  # (net_client.gd's release_url reads that one itself)
			args[kv[0].uri_decode()] = kv[1].uri_decode() if kv.size() > 1 else "true"


func _take_shot() -> void:
	while not scene_ready:
		await get_tree().process_frame
	for i in int(args.get("wait", "8")):
		await get_tree().process_frame
	await RenderingServer.frame_post_draw
	var path: String = args["shot"]
	DirAccess.make_dir_recursive_absolute(path.get_base_dir())
	var img := get_viewport().get_texture().get_image()
	img.save_png(path)
	print("[dbg] saved %s (%s)" % [path, img.get_size()])
	get_tree().quit()

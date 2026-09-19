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
##   --buy=Unit:territory,...  add units to the human's purchase queue (a scripted player)
##   --move_to=<id>     drop the selected units on that space (Combat/Non-Combat Move)
##   --recall=<unit,..> take units out of the queued moves
##   --select2=<id>     select another space afterwards
##   --hold=<seconds>   hold the submit button that long
##   --battle_rolls=<n>      press the open battle board's button n times
##   --wheel=x,y,steps       inject mouse-wheel steps at a screen point (+ = zoom in)
##   --drag=x1,y1,x2,y2      inject a left-button drag between two screen points
##   --click=x,y             inject a left click at a screen point
##   (injected input runs in that order: wheel, drag, click)
## Other scripts read `Dbg.args` for the scene-setup ones.

var args := {}
var injecting := false    # true while main.gd is injecting scripted input
var scene_ready := false  # main.gd sets this once scripted setup/input has finished


func _ready() -> void:
	for a in OS.get_cmdline_user_args():
		if a.begins_with("--"):
			var kv := a.substr(2).split("=", true, 1)
			args[kv[0]] = kv[1] if kv.size() > 1 else "true"
	if args.has("shot"):
		_take_shot()


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

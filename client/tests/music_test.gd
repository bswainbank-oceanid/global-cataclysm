extends SceneTree
## Headless checks for the soundtrack (Music): which tracks play on the menus and in a game, that they load,
## and the music settings.
##   godot --headless --path client -s res://tests/music_test.gd

var _failures := 0


func _check(cond: bool, what: String) -> void:
	if not cond:
		_failures += 1
		printerr("FAIL: " + what)


func _initialize() -> void:
	await process_frame
	var music = root.get_node("Music")
	var store = root.get_node("GameStore")
	var tracks: Dictionary = music._tracks
	_check((tracks.get("base", []) as Array).size() == 2, "two base tracks: %s" % [tracks.get("base")])
	for code in ["NAA", "UE", "UER", "GPC", "PAF", "AAC"]:
		_check((tracks.get("factions", {}).get(code, []) as Array).size() == 2, "%s has two tracks" % code)

	# The menus: the base tracks, in turns, from either one.
	var starts := {}
	for i in 20:
		var p: Array = music.playlist_for([])
		_check(p.size() == 2 and p.has("base_1.mp3") and p.has("base_2.mp3"), "the menus play the base tracks: %s" % [p])
		starts[p[0]] = true
	_check(starts.size() == 2, "either base track may come first")
	# One faction: its own two, in turns.
	var naa: Array = music.playlist_for(["NAA"])
	_check(naa.size() == 2 and naa.has("NAA_1.mp3") and naa.has("NAA_2.mp3"), "a faction's own tracks: %s" % [naa])
	# Several: all of theirs, shuffled.
	var orders := {}
	for i in 30:
		var both: Array = music.playlist_for(["NAA", "GPC"])
		_check(both.size() == 4, "two factions: all four tracks")
		orders[",".join(both)] = true
	_check(orders.size() > 2, "several factions' tracks are shuffled")

	# Following the game: in it, the player's faction's; out of it (or eliminated), the base tracks.
	store.multi_game = true
	store.my_factions = ["NAA", "PAF"]
	store.state = {"factions": {"NAA": {"eliminated": false}, "PAF": {"eliminated": true}, "GPC": {"eliminated": false}}}
	music.set_in_game(true)
	_check(music._key == "NAA" and music._playlist.has("NAA_1.mp3"), "in a game: the player's faction still in it")
	music.set_in_game(false)
	_check(music._key == "base", "back on the menus: the base tracks")

	# A track loads (a desktop copy reads it from res://assets/music).
	music._load("base_1.mp3")
	_check(music._streams.get("base_1.mp3") is AudioStreamMP3, "a track loads as MP3")
	_check(music._streams["base_1.mp3"].get_length() > 60.0, "...the whole track")

	# The settings: their own volume and mute, on their own bus.
	var settings = root.get_node("Settings")
	settings.music_volume = 0
	music.apply_volume()
	_check(AudioServer.is_bus_mute(AudioServer.get_bus_index("Music")), "volume 0 mutes the music")
	settings.music_volume = 50
	settings.music_muted = false
	music.apply_volume()
	_check(not AudioServer.is_bus_mute(AudioServer.get_bus_index("Music")), "volume 50 plays")
	_check(not AudioServer.is_bus_mute(AudioServer.get_bus_index("SFX")) or settings.sound_muted, "the unit sounds are their own")

	print("music test: failures=%d" % _failures)
	quit(1 if _failures > 0 else 0)

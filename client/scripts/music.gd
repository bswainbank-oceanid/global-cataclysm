extends Node
## The soundtrack (autoload "Music"): res://data/music.json's tracks (tools/sync_client_data.py, from
## assets/sounds/music), on their own "Music" bus at the Settings' music volume (or muted).
##
## On the menus (and before a game): the base tracks, starting with one at random and then taking turns. In
## a game: the player's faction's own tracks, the same way -- or, playing several factions, all of theirs,
## shuffled (and shuffled again each time round). A spectator, or a player whose factions are all out,
## hears the base tracks. Never tied to whose turn it is. Tracks crossfade (FADE seconds).
##
## A desktop build reads the tracks from res://assets/music. The web build leaves them out of the game's own
## package (it would triple the first download) and fetches each one beside the page (music/<file>, put
## there by tools/build_client.py --web) the first time it's wanted; the browser keeps it after that.
##
##   Music.set_in_game(true)    # main.gd: entering a game (false: back on the menus)

const BUS := "Music"
const MANIFEST := "res://data/music.json"
const DIR := "res://assets/music/"
const FADE := 2.0

var _tracks := {"base": [], "factions": {}}
var _players: Array = []          # two AudioStreamPlayers, for crossfading
var _live := 0                    # which of them is playing now
var _playlist: Array = []         # the files the current setting rotates through
var _key := ""                    # what that playlist was made from (unchanged: it keeps playing)
var _index := 0
var _in_game := false
var _streams := {}                # file -> AudioStreamMP3
var _fetching := {}               # file -> HTTPRequest (web)
var _wanted := ""                 # the file waiting to start once it has downloaded (web)
var _enabled := true
var _rng := RandomNumberGenerator.new()


func _ready() -> void:
	_rng.randomize()
	if AudioServer.get_bus_index(BUS) < 0:
		AudioServer.add_bus()
		AudioServer.set_bus_name(AudioServer.bus_count - 1, BUS)
		AudioServer.set_bus_send(AudioServer.bus_count - 1, "Master")
	for i in 2:
		var p := AudioStreamPlayer.new()
		p.bus = BUS
		p.finished.connect(_on_finished.bind(p))
		add_child(p)
		_players.append(p)
	if FileAccess.file_exists(MANIFEST):
		var doc = JSON.parse_string(FileAccess.get_file_as_string(MANIFEST))
		if doc is Dictionary:
			_tracks = doc
	# (scripted screenshot runs and tests stay quiet unless --music asks)
	_enabled = not Dbg.args.has("shot") or Dbg.args.has("music")
	Settings.changed.connect(apply_volume)
	apply_volume()
	GameStore.state_changed.connect(_refresh)
	_refresh.call_deferred()


## The Settings' music volume (0-100) and mute, onto the Music bus.
func apply_volume() -> void:
	var bus := AudioServer.get_bus_index(BUS)
	if bus < 0:
		return
	var level := clampf(float(Settings.music_volume) / 100.0, 0.0, 1.0)
	AudioServer.set_bus_volume_db(bus, linear_to_db(maxf(level, 0.0001)))
	AudioServer.set_bus_mute(bus, Settings.music_muted or level <= 0.0)


## main.gd: the player has entered a game (true) or is back on the menus (false).
func set_in_game(on: bool) -> void:
	_in_game = on
	_refresh()


## The tracks for factions `codes` (the player's, still in the game): one faction's own in turns, starting
## at random; several factions' all shuffled; none -- the base tracks in turns, starting at random.
func playlist_for(codes: Array) -> Array:
	var files := []
	for code in codes:
		files.append_array(_tracks.get("factions", {}).get(code, []))
	if files.is_empty():
		files = (_tracks.get("base", []) as Array).duplicate()
		return _rotated(files)
	if codes.size() > 1:
		_shuffle(files)
		return files
	return _rotated(files)


func _rotated(files: Array) -> Array:
	if files.size() < 2:
		return files
	var start := _rng.randi_range(0, files.size() - 1)
	return files.slice(start) + files.slice(0, start)


func _shuffle(files: Array) -> void:
	for i in range(files.size() - 1, 0, -1):
		var j := _rng.randi_range(0, i)
		var t = files[i]
		files[i] = files[j]
		files[j] = t


## The player's factions still in the game (none on the menus, or for a spectator).
func _my_factions() -> Array:
	var out := []
	if not _in_game:
		return out
	for code in GameStore.state.get("factions", {}):
		if GameStore.is_player(code) and not bool(GameStore.faction_state(code).get("eliminated", false)):
			out.append(code)
	out.sort()
	return out


## Plays what the current setting calls for -- unless that's what is playing already.
func _refresh() -> void:
	var codes := _my_factions()
	var key := ",".join(codes) if not codes.is_empty() else "base"
	if key == _key:
		return
	_key = key
	_playlist = playlist_for(codes)
	_index = 0
	if not _playlist.is_empty():
		_start(str(_playlist[0]))


func _on_finished(player: AudioStreamPlayer) -> void:
	if player != _players[_live] or _playlist.is_empty():
		return
	_index += 1
	if _index >= _playlist.size():
		_index = 0
		if _key.contains(","):  # (several factions: a fresh shuffle each time round)
			_shuffle(_playlist)
	_start(str(_playlist[_index]))


## Crossfades into `file` (fetched first, on the web).
func _start(file: String) -> void:
	if not _enabled:
		return
	var stream = _streams.get(file)
	if stream == null:
		_wanted = file
		_load(file)
		return
	_wanted = ""
	var old: AudioStreamPlayer = _players[_live]
	_live = 1 - _live
	var new: AudioStreamPlayer = _players[_live]
	new.stream = stream
	new.volume_db = -40.0
	new.play()
	var tween := create_tween().set_parallel(true)
	tween.tween_property(new, "volume_db", 0.0, FADE).set_trans(Tween.TRANS_SINE)
	if old.playing:
		tween.tween_property(old, "volume_db", -40.0, FADE).set_trans(Tween.TRANS_SINE)
		tween.chain().tween_callback(old.stop)


func _load(file: String) -> void:
	if OS.has_feature("web"):
		if _fetching.has(file):
			return
		var req := HTTPRequest.new()
		add_child(req)
		_fetching[file] = req
		req.request_completed.connect(func(result: int, code: int, _h, body: PackedByteArray):
			_fetching.erase(file)
			req.queue_free()
			if result == HTTPRequest.RESULT_SUCCESS and code == 200:
				_ready_stream(file, body))
		# (beside the page: an absolute address, made from the page's own)
		req.request(str(JavaScriptBridge.eval("new URL('music/%s', location.href).href" % file, true)))
		return
	var bytes := FileAccess.get_file_as_bytes(DIR + file)
	if not bytes.is_empty():
		_ready_stream(file, bytes)


func _ready_stream(file: String, bytes: PackedByteArray) -> void:
	var stream := AudioStreamMP3.new()
	stream.data = bytes
	_streams[file] = stream
	if _wanted == file:
		_start(file)

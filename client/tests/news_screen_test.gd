extends SceneTree
## Headless checks for the News screen (NewsScreen) and what makes the main menu's News pulse:
##   godot --headless --path client -s res://tests/news_screen_test.gd
## Exits non-zero on any failure.

var _failures := 0


func _check(cond: bool, what: String) -> void:
	if not cond:
		_failures += 1
		printerr("FAIL: " + what)


func _initialize() -> void:
	await process_frame  # (the autoloads come up first)
	var settings = root.get_node("Settings")
	var was: String = settings.news_seen
	settings.news_seen = ""
	var screen = load("res://scripts/news_screen.gd").new()
	root.add_child(screen)
	await process_frame
	var welcome := {"id": "a1", "date": "10/13/2026", "title": "Welcome",
		"paragraphs": ["Hello.", "Join us: [url=https://discord.gg/x]https://discord.gg/x[/url]"]}
	screen.set_news({"title": "News from the Global Cataclysm", "entries": [welcome]})
	_check(screen.unread(), "a new player: the news is unread")
	_check(screen._title.text == "NEWS FROM THE GLOBAL CATACLYSM", "the title: %s" % screen._title.text)
	screen.open()
	_check(not screen.unread() and settings.news_seen == "a1", "opened: seen (%s)" % settings.news_seen)
	screen.close()
	var later := {"id": "b2", "date": "10/20/2026", "title": "Patch notes", "paragraphs": ["Fixes."]}
	screen.set_news({"title": "News", "entries": [later, welcome]})
	_check(screen.unread() and screen.newest() == "b2", "a newer entry on top: unread again")
	screen.set_news({"title": "News", "entries": []})
	_check(not screen.unread(), "no news: nothing to read")

	settings.news_seen = was  # (the machine's own, back)
	settings.commit()
	screen.queue_free()
	quit(1 if _failures > 0 else 0)

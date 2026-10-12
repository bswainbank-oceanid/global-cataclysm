class_name PopupOrder
## The game's pop-up windows that wait on the player -- an alliance invitation (InvitationWindow), an armistice
## proposal (ArmisticeWindow), an announcement (AnnouncementWindow) -- can be up at once, one over another.
## They share one layer (Z), and the one in front is the one just opened -- the newest thing to deal with;
## clicking a window that's partly covered brings it forward. (Siblings' order within a layer decides both what
## is drawn on top and which takes the click, so the two always agree.)

const Z := 420  # (over the game, under the menus)


## Puts `window` (whose clickable sheet is `panel`) in the shared layer and keeps it ordered.
static func manage(window: Control, panel: Control) -> void:
	window.z_index = Z
	window.visibility_changed.connect(func():
		if window.visible:
			window.move_to_front.call_deferred())
	panel.gui_input.connect(func(event: InputEvent):
		if event is InputEventMouseButton and event.pressed:
			window.move_to_front.call_deferred())

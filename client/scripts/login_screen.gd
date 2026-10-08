class_name LoginScreen
extends MenuScreen
## Log in, or make a new player (reference/GC Launcher_User Profile.odt): shown by a multi-player
## server when no login is saved on this client. A login is remembered (Account) until Log Out. "Forgot
## password?" emails a 6-digit code (server/hub.py request_reset) to set a new one with.

var _tabs := {}          # "login" / "new" -> its tab Button
var _forms := {}         # "login" / "new" / "reset" -> its form VBoxContainer
var _mode := "login"
var _email: LineEdit
var _password: LineEdit
var _new_name: LineEdit
var _new_actual: LineEdit
var _new_email: LineEdit
var _new_password: LineEdit
var _problem: Label
var _busy := false
var _reset_email: LineEdit
var _reset_code: LineEdit
var _reset_password: LineEdit
var _reset_note: Label
var _reset_step: VBoxContainer  # the code and the new password: shown once a code has been sent
var _send_code: Button


func _build() -> void:
	var center := CenterContainer.new()
	add_child(center)
	center.set_anchors_and_offsets_preset(Control.PRESET_FULL_RECT)
	var panel := MenuScreen.sheet(460, 12)
	center.add_child(panel)
	var v := MenuScreen.body(panel)
	v.add_child(MenuScreen.wordmark(30))

	var tabs := HBoxContainer.new()
	tabs.add_theme_constant_override("separation", 6)
	v.add_child(tabs)
	for key in ["login", "new"]:
		var b := Button.new()
		b.text = "Log In" if key == "login" else "New Player"
		b.size_flags_horizontal = Control.SIZE_EXPAND_FILL
		b.pressed.connect(func(): _show(key))
		tabs.add_child(b)
		_tabs[key] = b

	var login := VBoxContainer.new()
	login.add_theme_constant_override("separation", 10)
	v.add_child(login)
	_forms["login"] = login
	_email = MenuScreen.field(login, "Email")
	_password = MenuScreen.field(login, "Password", true)
	var go := HudStyle.primary(Button.new())
	go.text = "LOG IN"
	go.custom_minimum_size = Vector2(0, 44)
	go.pressed.connect(_submit)
	login.add_child(go)
	login.add_child(_link("Forgot password?", func():
		_reset_email.text = _email.text
		_show("reset")))

	var reset := VBoxContainer.new()
	reset.add_theme_constant_override("separation", 10)
	v.add_child(reset)
	_forms["reset"] = reset
	var intro := HudStyle.label("We'll email you a 6-digit code to set a new password with.", 13, HudStyle.TEXT_DIM)
	intro.autowrap_mode = TextServer.AUTOWRAP_WORD_SMART
	reset.add_child(intro)
	_reset_email = MenuScreen.field(reset, "Email")
	_send_code = HudStyle.secondary(Button.new())
	_send_code.text = "SEND CODE"
	_send_code.custom_minimum_size = Vector2(0, 40)
	_send_code.pressed.connect(func(): _ask_code())
	reset.add_child(_send_code)
	_reset_step = VBoxContainer.new()
	_reset_step.add_theme_constant_override("separation", 10)
	reset.add_child(_reset_step)
	_reset_note = HudStyle.label("", 13, HudStyle.TEXT)
	_reset_note.autowrap_mode = TextServer.AUTOWRAP_WORD_SMART
	_reset_step.add_child(_reset_note)
	_reset_code = MenuScreen.field(_reset_step, "Code", false, "the 6 digits from the email")
	_reset_password = MenuScreen.field(_reset_step, "New Password", true)
	var set_password := HudStyle.primary(Button.new())
	set_password.text = "SET PASSWORD"
	set_password.custom_minimum_size = Vector2(0, 44)
	set_password.pressed.connect(_submit)
	_reset_step.add_child(set_password)
	reset.add_child(_link("Back to Log In", func(): _show("login")))
	_reset_email.text_submitted.connect(func(_t): _ask_code())
	for edit in [_reset_code, _reset_password]:
		(edit as LineEdit).text_submitted.connect(func(_t): _submit())
	Account.reset_sent.connect(func(email: String):
		_busy = false
		_reset_note.text = ("If %s belongs to a player here, a code is on its way. It works once, for 30 minutes "
			+ "(check spam if it doesn't arrive).") % email
		_reset_step.visible = true
		_send_code.text = "SEND A NEW CODE"
		_reset_code.grab_focus.call_deferred())

	var form := VBoxContainer.new()
	form.add_theme_constant_override("separation", 10)
	v.add_child(form)
	_forms["new"] = form
	_new_name = MenuScreen.field(form, "Player Name", false, "how other players see you")
	_new_actual = MenuScreen.field(form, "Actual Name", false, "optional")
	_new_email = MenuScreen.field(form, "Email", false, "you log in with it")
	_new_password = MenuScreen.field(form, "Password", true)
	var create := HudStyle.primary(Button.new())
	create.text = "CREATE PLAYER"
	create.custom_minimum_size = Vector2(0, 44)
	create.pressed.connect(_submit)
	form.add_child(create)

	for edit in [_email, _password, _new_name, _new_actual, _new_email, _new_password]:
		(edit as LineEdit).text_submitted.connect(func(_t): _submit())

	_problem = MenuScreen.problem_label()
	v.add_child(_problem)
	if not OS.has_feature("web"):  # (in a browser the player closes the tab)
		v.add_child(HSeparator.new())
		var quit := HudStyle.secondary(Button.new())
		quit.text = "QUIT TO DESKTOP"
		quit.custom_minimum_size = Vector2(0, 40)
		quit.pressed.connect(func(): get_tree().quit())
		v.add_child(quit)
	Account.problem.connect(func(message: String):
		_busy = false
		_problem.text = message
		_problem.visible = message != "")
	_show("login")


func open() -> void:
	_busy = false
	_problem.visible = false
	_password.text = ""
	_new_password.text = ""
	visible = true
	(_email if _mode == "login" else _new_name).grab_focus.call_deferred()


func close() -> void:
	visible = false


func _show(mode: String) -> void:
	_mode = mode
	for key in _forms:
		_forms[key].visible = key == mode
	for key in _tabs:
		HudStyle.tab(_tabs[key], key == mode)
	_problem.visible = false
	if mode == "reset":
		_reset_step.visible = false
		_send_code.text = "SEND CODE"
		_reset_code.text = ""
		_reset_password.text = ""
		_reset_email.grab_focus.call_deferred()


## A small text button (Forgot password?, Back to Log In).
func _link(text: String, action: Callable) -> Button:
	var b := Button.new()
	b.text = text
	b.flat = true
	b.focus_mode = Control.FOCUS_NONE
	b.size_flags_horizontal = Control.SIZE_SHRINK_CENTER
	b.add_theme_font_size_override("font_size", 13)
	b.add_theme_color_override("font_color", GCTheme.NAVY)
	b.add_theme_color_override("font_hover_color", GCTheme.RED)
	b.mouse_default_cursor_shape = Control.CURSOR_POINTING_HAND
	b.pressed.connect(action)
	return b


func _ask_code() -> void:
	if _busy:
		return
	_problem.visible = false
	Account.request_reset(_reset_email.text)
	_busy = true


func _submit() -> void:
	if _busy:
		return
	_problem.visible = false
	if _mode == "login":
		Account.login(_email.text, _password.text)
	elif _mode == "reset":
		if not _reset_step.visible:
			_ask_code()
			return
		Account.reset_password(_reset_email.text, _reset_code.text, _reset_password.text)
	else:
		Account.register(_new_name.text, _new_actual.text, _new_email.text, _new_password.text)
	_busy = true

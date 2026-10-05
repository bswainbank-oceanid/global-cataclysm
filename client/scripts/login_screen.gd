class_name LoginScreen
extends MenuScreen
## Log in, or make a new player (reference/GC Launcher_User Profile.odt): shown by a multi-player
## server when no login is saved on this client. A login is remembered (Account) until Log Out.

var _tabs := {}          # "login" / "new" -> its tab Button
var _forms := {}         # "login" / "new" -> its form VBoxContainer
var _mode := "login"
var _email: LineEdit
var _password: LineEdit
var _new_name: LineEdit
var _new_actual: LineEdit
var _new_email: LineEdit
var _new_password: LineEdit
var _problem: Label
var _busy := false


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
		HudStyle.tab(_tabs[key], key == mode)
	_problem.visible = false


func _submit() -> void:
	if _busy:
		return
	_problem.visible = false
	if _mode == "login":
		Account.login(_email.text, _password.text)
	else:
		Account.register(_new_name.text, _new_actual.text, _new_email.text, _new_password.text)
	_busy = true

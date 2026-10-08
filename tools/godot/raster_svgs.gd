extends SceneTree
## Rasterises every SVG in a folder to PNG, about SIZE px on its longer side.   godot --headless -s raster_svgs.gd -- <in> <out>
const SIZE := 1200.0

func _initialize() -> void:
	var args := OS.get_cmdline_user_args()
	var src: String = args[0]
	var dst: String = args[1]
	for f in DirAccess.get_files_at(src):
		if not f.ends_with(".svg"):
			continue
		var text := FileAccess.get_file_as_string(src.path_join(f))
		var probe := Image.new()
		probe.load_svg_from_string(text, 1.0)
		var scale := SIZE / maxf(probe.get_width(), probe.get_height())
		var img := Image.new()
		if img.load_svg_from_string(text, scale) == OK:
			img.save_png(dst.path_join(f.get_basename() + ".png"))
			print(f, " ", img.get_size())
	quit()

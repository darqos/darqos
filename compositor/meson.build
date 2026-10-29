# darqos
# Copyright (C) 2026 David Arnold

# The DarqOS compositor: see doc/terminal.rst.

project('darq-compositor', 'c',
	version: '0.1',
	license: 'MIT',
	meson_version: '>=1.0.0',
	default_options: ['c_std=c11', 'warning_level=2'])

add_project_arguments([
	'-DWLR_USE_UNSTABLE',
	'-D_POSIX_C_SOURCE=200809L',
	'-Wno-unused-parameter',
	# Our xkb files, in the source tree for now.
	'-DDARQ_XKB_DIR="' + meson.current_source_dir() / 'xkb' + '"',
], language: 'c')

wlroots = dependency('wlroots-0.18')
wayland_server = dependency('wayland-server')
xkbcommon = dependency('xkbcommon')

# wlroots' headers include the server headers for the protocols they
# implement, which the compositor generates.
wayland_protocols = dependency('wayland-protocols')
wayland_scanner = find_program(
	dependency('wayland-scanner', native: true).get_variable('wayland_scanner'))
protocol_dir = wayland_protocols.get_variable('pkgdatadir')
protocols = {
	'xdg-shell': protocol_dir / 'stable/xdg-shell/xdg-shell.xml',
	'xdg-decoration-unstable-v1':
		protocol_dir / 'unstable/xdg-decoration/xdg-decoration-unstable-v1.xml',
}
protocol_headers = []
foreach name, path : protocols
	protocol_headers += custom_target(name.underscorify() + '_h',
		input: path,
		output: name + '-protocol.h',
		command: [wayland_scanner, 'server-header', '@INPUT@', '@OUTPUT@'])
endforeach

# The policy channel's messages: C generated from the DIDL, at build
# time, by didlc and didlcgen, with the DIPC runtime it uses.
darq_root = meson.current_source_dir() / '..'
didlc = find_program(darq_root / 'bin' / 'didlc')
didlcgen = find_program(darq_root / 'bin' / 'didlcgen')
policy_ir = custom_target('compositor_ir',
	input: darq_root / 'lib' / 'didl' / 'compositor.didl',
	output: 'compositor.dir',
	command: [didlc, '-o', '@OUTPUT@', '@INPUT@'])
policy_c = custom_target('compositor_c',
	input: policy_ir,
	output: ['darq_compositor.h', 'darq_compositor.c'],
	command: [didlcgen, '-d', '@OUTDIR@', '@INPUT@'])

# Not include_directories(): meson takes the path to be in this project's
# source tree, which it isn't.
executable('darq-compositor',
	['compositor.c', darq_root / 'lib' / 'c' / 'darq' / 'dipc.c', policy_c] + protocol_headers,
	dependencies: [wlroots, wayland_server, xkbcommon],
	c_args: ['-I' + (darq_root / 'lib' / 'c')],
	install: true)

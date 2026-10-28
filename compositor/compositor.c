// darqos
// Copyright (C) 2026 David Arnold

// The DarqOS compositor (see doc/terminal.rst).
//
// Forked from tinywl, the reference compositor in the wlroots 0.18.2
// source tree (CC0).  So far, it's tinywl with
//
// - The Terminal's Wayland connection on a file descriptor it's given
//   (-t), so its toplevels can be told apart from other clients'.
// - The policy channel to the Terminal, on another (-p): see policy.h.
//   It reports bound hotkeys and the window list, and focuses, raises
//   and closes windows; the compositor exits when it's closed, or when
//   the Terminal dies (PR_SET_PDEATHSIG).
// - Four layers: background, applications, overlay and lock.  The
//   Terminal's windows go in the layer their title names; everyone
//   else's in applications.  It starts locked: only the background and
//   lock layers are shown, and only the lock layer gets input.
// - Focus follows the mouse, and a click raises.  While Sys-w is held,
//   the focused application window moves with the mouse or the arrow
//   keys, and t, s, b and n resize it.
// - Server-side decorations (xdg-decoration), which it doesn't draw.
// - Our keymap: System, on Left Control, is a modifier of its own
//   (darq:system, in xkb/), and keys pressed with it never reach
//   clients.  Without a policy channel, Sys-Escape exits.
// - A public socket, darq-0.
// - A test keyboard and pointer (-k), driven by lines on stdin, for
//   running headless: "key <evdev keycode> <1|0>", "move <x> <y>" (in
//   layout coordinates) and "button <evdev button> <1|0>".  As nested in
//   another compositor, "hostkey <evdev keycode> <1|0>" doesn't update
//   the keyboard's state, and "hostmods <depressed> <latched> <locked>"
//   sets it, in the host's keymap.
// - A clean exit on SIGTERM and SIGINT, removing its socket.

#include <assert.h>
#include <errno.h>
#include <fcntl.h>
#include <getopt.h>
#include <signal.h>
#include <stdbool.h>
#include <stdlib.h>
#include <stdio.h>
#include <string.h>
#include <sys/prctl.h>
#include <sys/socket.h>
#include <time.h>
#include <unistd.h>
#include <wayland-server-core.h>
#include <wlr/backend.h>
#include <wlr/interfaces/wlr_keyboard.h>
#include <wlr/interfaces/wlr_pointer.h>
#include <wlr/render/allocator.h>
#include <wlr/render/wlr_renderer.h>
#include <wlr/types/wlr_cursor.h>
#include <wlr/types/wlr_compositor.h>
#include <wlr/types/wlr_data_device.h>
#include <wlr/types/wlr_input_device.h>
#include <wlr/types/wlr_keyboard.h>
#include <wlr/types/wlr_output.h>
#include <wlr/types/wlr_output_layout.h>
#include <wlr/types/wlr_pointer.h>
#include <wlr/types/wlr_scene.h>
#include <wlr/types/wlr_seat.h>
#include <wlr/types/wlr_subcompositor.h>
#include <wlr/types/wlr_xcursor_manager.h>
#include <wlr/types/wlr_xdg_decoration_v1.h>
#include <wlr/types/wlr_xdg_shell.h>
#include <wlr/util/log.h>
#include <xkbcommon/xkbcommon.h>

#include "policy.h"

/* How many hotkeys the Terminal can bind. */
#define MAX_HOTKEYS 64

/* How many messages can wait to be sent to the Terminal: more, and
 * it's assumed to be stuck. */
#define MAX_POLICY_QUEUE 4096

/* How far Sys-w's keys move or resize a window, in pixels, and their
 * repeat delay and rate (as the keyboard's), in milliseconds. */
#define WINDOW_STEP 16
#define REPEAT_DELAY 600
#define REPEAT_INTERVAL 40

/* The layers, bottom to top. */
enum darq_layer {
	LAYER_BACKGROUND,
	LAYER_APPS,
	LAYER_OVERLAY,
	LAYER_LOCK,
	NUM_LAYERS,
};

/* For brevity's sake, struct members are annotated where they are used. */
enum tinywl_cursor_mode {
	TINYWL_CURSOR_PASSTHROUGH,
	TINYWL_CURSOR_MOVE,
	TINYWL_CURSOR_RESIZE,
};

struct tinywl_server {
	struct wl_display *wl_display;
	struct wlr_backend *backend;
	struct wlr_renderer *renderer;
	struct wlr_allocator *allocator;
	struct wlr_scene *scene;
	struct wlr_scene_output_layout *scene_layout;

	/* The layers' scene trees, and whether it's locked: then, only the
	 * background and lock layers are shown, and only the lock layer
	 * gets input. */
	struct wlr_scene_tree *layers[NUM_LAYERS];
	bool locked;

	struct wlr_xdg_shell *xdg_shell;
	struct wl_listener new_xdg_toplevel;
	struct wl_listener new_xdg_popup;
	struct wl_list toplevels;

	struct wlr_cursor *cursor;
	struct wlr_xcursor_manager *cursor_mgr;
	struct wl_listener cursor_motion;
	struct wl_listener cursor_motion_absolute;
	struct wl_listener cursor_button;
	struct wl_listener cursor_axis;
	struct wl_listener cursor_frame;

	struct wlr_seat *seat;
	struct wl_listener new_input;
	struct wl_listener request_cursor;
	struct wl_listener request_set_selection;
	struct wl_list keyboards;
	enum tinywl_cursor_mode cursor_mode;
	struct tinywl_toplevel *grabbed_toplevel;
	double grab_x, grab_y;
	struct wlr_box grab_geobox;
	uint32_t resize_edges;

	struct wlr_output_layout *output_layout;
	struct wl_list outputs;
	struct wl_listener new_output;
	struct wl_listener layout_change;

	/* The Terminal's Wayland connection, if it was given one: NULL once
	 * it's gone, so another client can't be mistaken for it. */
	struct wl_client *terminal;
	struct wl_listener terminal_destroy;

	struct wlr_xdg_decoration_manager_v1 *decoration_manager;
	struct wl_listener new_decoration;

	/* The policy channel, if there is one (-p): -1 if not.  Messages
	 * the socket has no room for wait in the queue, of
	 * tinywl_policy_packet, until it's writable. */
	int policy_fd;
	struct wl_event_source *policy_source;
	struct wl_list policy_queue;
	int policy_queue_length;

	/* The hotkeys the Terminal has bound. */
	struct {
		xkb_keysym_t keysym;
		uint32_t modifiers;
	} hotkeys[MAX_HOTKEYS];
	int num_hotkeys;

	/* A bound hotkey has been pressed since System was. */
	bool system_chord;

	/* Keys whose press was the compositor's, so their release is too. */
	bool swallowed[256];

	/* The toplevel with the keyboard focus, if any.  The next window
	 * number, and the one last reported focused (0 for none). */
	struct tinywl_toplevel *focused;
	uint32_t next_window;
	uint32_t focused_window;

	/* While Sys-w is held: the focused window's size, as it's been
	 * asked to be, and the key repeating, if any. */
	bool window_mode;
	int window_width, window_height;
	xkb_keysym_t repeat_sym;
	struct wl_event_source *repeat_timer;

	/* The System modifier's mask, in the keymap's modifiers. */
	xkb_mod_mask_t system_mask;

	/* The test keyboard and pointer, and their input so far (-k). */
	struct wlr_keyboard test_keyboard;
	struct wlr_pointer test_pointer;
	struct wl_event_source *test_input_source;
	char test_input[256];
	size_t test_input_length;
};

struct tinywl_output {
	struct wl_list link;
	struct tinywl_server *server;
	struct wlr_output *wlr_output;
	struct wl_listener frame;
	struct wl_listener request_state;
	struct wl_listener destroy;
};

struct tinywl_toplevel {
	struct wl_list link;
	struct tinywl_server *server;
	struct wlr_xdg_toplevel *xdg_toplevel;
	struct wlr_scene_tree *scene_tree;
	struct wl_listener map;
	struct wl_listener unmap;
	struct wl_listener commit;
	struct wl_listener destroy;
	struct wl_listener request_move;
	struct wl_listener request_resize;
	struct wl_listener request_maximize;
	struct wl_listener request_fullscreen;

	struct wlr_xdg_toplevel_decoration_v1 *decoration;
	struct wl_listener decoration_destroy;

	/* Its window number, and whether it's reported to the Terminal:
	 * while it's mapped, if it isn't the Terminal's own. */
	uint32_t window;
	bool reported;

	/* Its layer, decided on its first commit.  Background and lock
	 * windows fill an output. */
	enum darq_layer layer;
	struct wl_listener set_title;
};

struct tinywl_policy_packet {
	struct wl_list link;
	size_t length;
	uint8_t data[];
};

struct tinywl_popup {
	struct wlr_xdg_popup *xdg_popup;
	struct wl_listener commit;
	struct wl_listener destroy;
};

struct tinywl_keyboard {
	struct wl_list link;
	struct tinywl_server *server;
	struct wlr_keyboard *wlr_keyboard;

	/* Its state, in our keymap, from its keys alone.  The wlr_keyboard's
	 * own isn't always: nested in another compositor, wlroots' Wayland
	 * backend takes the modifiers from the host, in its keymap, where
	 * Left Control is Control, not System. */
	struct xkb_state *state;

	struct wl_listener modifiers;
	struct wl_listener key;
	struct wl_listener destroy;
};

static int policy_writable(int fd, uint32_t mask, void *data);

static void policy_close(struct tinywl_server *server) {
	/* Without the Terminal, there's nothing for the compositor to do. */
	wlr_log(WLR_INFO, "the policy channel is closed: exiting");
	wl_event_source_remove(server->policy_source);
	server->policy_source = NULL;
	close(server->policy_fd);
	server->policy_fd = -1;
	while (!wl_list_empty(&server->policy_queue)) {
		struct tinywl_policy_packet *packet =
			wl_container_of(server->policy_queue.next, packet, link);
		wl_list_remove(&packet->link);
		free(packet);
	}
	server->policy_queue_length = 0;
	wl_display_terminate(server->wl_display);
}

static bool policy_try_send(struct tinywl_server *server,
		const uint8_t *data, size_t length) {
	/* Returns false if there's no room for it yet.  On an error, the
	 * channel is closed, and its queue emptied. */
	if (send(server->policy_fd, data, length, MSG_DONTWAIT | MSG_NOSIGNAL) >= 0) {
		return true;
	}
	if (errno != EAGAIN && errno != EWOULDBLOCK && errno != EINTR) {
		wlr_log_errno(WLR_ERROR, "can't send on the policy channel");
		policy_close(server);
		return true;
	}
	return false;
}

static void policy_send(struct tinywl_server *server, struct policy_buffer *buffer) {
	/* Send a message to the Terminal, or queue it, in order. */
	if (server->policy_fd < 0) {
		return;
	}
	if (wl_list_empty(&server->policy_queue) &&
			policy_try_send(server, buffer->data, buffer->length)) {
		return;
	}
	if (server->policy_queue_length == MAX_POLICY_QUEUE) {
		wlr_log(WLR_ERROR, "the Terminal isn't reading the policy channel");
		policy_close(server);
		return;
	}
	struct tinywl_policy_packet *packet = malloc(sizeof(*packet) + buffer->length);
	packet->length = buffer->length;
	memcpy(packet->data, buffer->data, buffer->length);
	wl_list_insert(server->policy_queue.prev, &packet->link);
	server->policy_queue_length++;
	wl_event_source_fd_update(server->policy_source,
		WL_EVENT_READABLE | WL_EVENT_WRITABLE);
}

static int policy_writable(int fd, uint32_t mask, void *data) {
	struct tinywl_server *server = data;
	while (!wl_list_empty(&server->policy_queue)) {
		struct tinywl_policy_packet *packet =
			wl_container_of(server->policy_queue.next, packet, link);
		if (!policy_try_send(server, packet->data, packet->length)) {
			return 0;
		}
		if (server->policy_fd < 0) {
			return 0;
		}
		wl_list_remove(&packet->link);
		server->policy_queue_length--;
		free(packet);
	}
	wl_event_source_fd_update(server->policy_source, WL_EVENT_READABLE);
	return 0;
}

static void report_focus(struct tinywl_server *server, uint32_t window) {
	if (window == server->focused_window) {
		return;
	}
	server->focused_window = window;
	struct policy_buffer buffer;
	policy_encode_window_focused(&buffer, window);
	policy_send(server, &buffer);
}

static void raise_toplevel(struct tinywl_toplevel *toplevel) {
	/* Move the toplevel to the front of its layer */
	wlr_scene_node_raise_to_top(&toplevel->scene_tree->node);
	wl_list_remove(&toplevel->link);
	wl_list_insert(&toplevel->server->toplevels, &toplevel->link);
}

static bool reachable(struct tinywl_toplevel *toplevel) {
	/* Whether a mapped toplevel can have the keyboard focus. */
	return !toplevel->server->locked || toplevel->layer == LAYER_LOCK;
}

static void deactivate_focused(struct tinywl_server *server) {
	/*
	 * Deactivate the previously focused surface. This lets the client know
	 * it no longer has focus and the client will repaint accordingly, e.g.
	 * stop displaying a caret.
	 */
	struct wlr_surface *prev_surface = server->seat->keyboard_state.focused_surface;
	if (prev_surface) {
		struct wlr_xdg_toplevel *prev_toplevel =
			wlr_xdg_toplevel_try_from_wlr_surface(prev_surface);
		if (prev_toplevel != NULL) {
			wlr_xdg_toplevel_set_activated(prev_toplevel, false);
		}
	}
}

static void focus_toplevel(struct tinywl_toplevel *toplevel) {
	/* Note: this function only deals with keyboard focus. */
	if (toplevel == NULL || !reachable(toplevel)) {
		return;
	}
	struct tinywl_server *server = toplevel->server;
	struct wlr_seat *seat = server->seat;
	struct wlr_surface *surface = toplevel->xdg_toplevel->base->surface;
	if (seat->keyboard_state.focused_surface == surface) {
		/* Don't re-focus an already focused surface. */
		return;
	}
	deactivate_focused(server);
	server->focused = toplevel;
	struct wlr_keyboard *keyboard = wlr_seat_get_keyboard(seat);
	/* Activate the new surface */
	wlr_xdg_toplevel_set_activated(toplevel->xdg_toplevel, true);
	/*
	 * Tell the seat to have the keyboard enter this surface. wlroots will keep
	 * track of this and automatically send key events to the appropriate
	 * clients without additional work on your part.
	 */
	if (keyboard != NULL) {
		struct wlr_keyboard_modifiers modifiers = keyboard->modifiers;
		modifiers.depressed &= ~server->system_mask;
		modifiers.latched &= ~server->system_mask;
		modifiers.locked &= ~server->system_mask;
		wlr_seat_keyboard_notify_enter(seat, surface,
			keyboard->keycodes, keyboard->num_keycodes, &modifiers);
	} else {
		wlr_seat_keyboard_notify_enter(seat, surface, NULL, 0, NULL);
	}
	report_focus(server, toplevel->reported ? toplevel->window : 0);
}

static void refocus(struct tinywl_server *server) {
	/* Focus the frontmost reachable toplevel, preferring the higher
	 * layers, or nothing, when the focused one's gone or unreachable. */
	struct tinywl_toplevel *best = NULL;
	struct tinywl_toplevel *toplevel;
	wl_list_for_each(toplevel, &server->toplevels, link) {
		if (reachable(toplevel) && (best == NULL || toplevel->layer > best->layer)) {
			best = toplevel;
		}
	}
	if (best != NULL) {
		focus_toplevel(best);
		return;
	}
	deactivate_focused(server);
	server->focused = NULL;
	wlr_seat_keyboard_notify_clear_focus(server->seat);
	report_focus(server, 0);
}

static void end_window_mode(struct tinywl_server *server);
static void reset_cursor_mode(struct tinywl_server *server);
static void process_cursor_motion(struct tinywl_server *server, uint32_t time);

static void set_locked(struct tinywl_server *server, bool locked) {
	/* While locked, the applications and overlay layers are neither
	 * shown, nor reachable, by the keyboard or the pointer. */
	if (locked == server->locked) {
		return;
	}
	server->locked = locked;
	end_window_mode(server);
	reset_cursor_mode(server);
	wlr_scene_node_set_enabled(&server->layers[LAYER_APPS]->node, !locked);
	wlr_scene_node_set_enabled(&server->layers[LAYER_OVERLAY]->node, !locked);
	if (server->focused != NULL && !reachable(server->focused)) {
		server->focused = NULL;
	}
	refocus(server);
	/* The pointer may now be over something else. */
	process_cursor_motion(server, 0);
}

static bool sync_modifiers(struct tinywl_keyboard *keyboard) {
	/* Make the wlr_keyboard's modifiers ours, if they aren't.  Returns
	 * whether they've changed: then, its modifiers event has been
	 * raised again, with ours. */
	struct xkb_state *state = keyboard->state;
	xkb_mod_mask_t depressed = xkb_state_serialize_mods(state, XKB_STATE_MODS_DEPRESSED);
	xkb_mod_mask_t latched = xkb_state_serialize_mods(state, XKB_STATE_MODS_LATCHED);
	xkb_mod_mask_t locked = xkb_state_serialize_mods(state, XKB_STATE_MODS_LOCKED);
	xkb_layout_index_t group = xkb_state_serialize_layout(state, XKB_STATE_LAYOUT_EFFECTIVE);
	struct wlr_keyboard_modifiers *current = &keyboard->wlr_keyboard->modifiers;
	if (current->depressed == depressed && current->latched == latched &&
			current->locked == locked && current->group == group) {
		return false;
	}
	wlr_keyboard_notify_modifiers(keyboard->wlr_keyboard, depressed, latched, locked, group);
	return true;
}

static void keyboard_handle_modifiers(
		struct wl_listener *listener, void *data) {
	/* This event is raised when a modifier key, such as shift or alt, is
	 * pressed. We simply communicate this to the client. */
	struct tinywl_keyboard *keyboard =
		wl_container_of(listener, keyboard, modifiers);
	/* Unless they're the host's, nested: they're replaced with ours,
	 * which raises this event again. */
	if (sync_modifiers(keyboard)) {
		return;
	}
	/*
	 * A seat can only have one keyboard, but this is a limitation of the
	 * Wayland protocol - not wlroots. We assign all connected keyboards to the
	 * same seat. You can swap out the underlying wlr_keyboard like this and
	 * wlr_seat handles this transparently.
	 */
	wlr_seat_set_keyboard(keyboard->server->seat, keyboard->wlr_keyboard);
	/* Send modifiers to the client, except System, which is ours. */
	struct wlr_keyboard_modifiers modifiers = keyboard->wlr_keyboard->modifiers;
	xkb_mod_mask_t system = keyboard->server->system_mask;
	modifiers.depressed &= ~system;
	modifiers.latched &= ~system;
	modifiers.locked &= ~system;
	wlr_seat_keyboard_notify_modifiers(keyboard->server->seat, &modifiers);
}

static uint32_t hotkey_modifiers(struct xkb_state *state) {
	/* The modifiers a hotkey is bound with, besides System. */
	static const struct {
		const char *name;
		uint32_t bit;
	} modifiers[] = {
		{XKB_MOD_NAME_SHIFT, POLICY_SHIFT},
		{XKB_MOD_NAME_ALT, POLICY_ALT},
		{XKB_MOD_NAME_CTRL, POLICY_CONTROL},
		{XKB_MOD_NAME_LOGO, POLICY_SUPER},
	};
	uint32_t bits = 0;
	for (size_t i = 0; i < sizeof(modifiers) / sizeof(modifiers[0]); i++) {
		if (xkb_state_mod_name_is_active(state, modifiers[i].name,
				XKB_STATE_MODS_EFFECTIVE) > 0) {
			bits |= modifiers[i].bit;
		}
	}
	return bits;
}

static void handle_system_key(struct tinywl_server *server,
		xkb_keysym_t sym, uint32_t modifiers) {
	/* A key pressed with System: the compositor's own, or a hotkey the
	 * Terminal has bound, or nothing. */
	if (server->policy_fd < 0) {
		/* Without a Terminal, there has to be a way out. */
		if (sym == XKB_KEY_Escape) {
			wl_display_terminate(server->wl_display);
		}
		return;
	}
	for (int i = 0; i < server->num_hotkeys; i++) {
		if (server->hotkeys[i].keysym == sym &&
				server->hotkeys[i].modifiers == modifiers) {
			struct policy_buffer buffer;
			policy_encode_hotkey_pressed(&buffer, sym, modifiers);
			policy_send(server, &buffer);
			server->system_chord = true;
			return;
		}
	}
	char name[64];
	xkb_keysym_get_name(sym, name, sizeof(name));
	wlr_log(WLR_DEBUG, "System key %s, modifiers %#x: not bound", name, modifiers);
}

static void stop_repeat(struct tinywl_server *server) {
	server->repeat_sym = XKB_KEY_NoSymbol;
	wl_event_source_timer_update(server->repeat_timer, 0);
}

static void end_window_mode(struct tinywl_server *server) {
	if (!server->window_mode) {
		return;
	}
	server->window_mode = false;
	stop_repeat(server);
	reset_cursor_mode(server);
}

static void begin_window_mode(struct tinywl_server *server) {
	/* Sys-w: only an application window moves, and only while it's
	 * unlocked. */
	struct tinywl_toplevel *toplevel = server->focused;
	if (server->window_mode || toplevel == NULL ||
			toplevel->layer != LAYER_APPS || server->locked) {
		return;
	}
	struct wlr_box geo_box;
	wlr_xdg_surface_get_geometry(toplevel->xdg_toplevel->base, &geo_box);
	server->window_mode = true;
	server->window_width = geo_box.width;
	server->window_height = geo_box.height;

	/* The mouse moves it, as an interactive move does. */
	server->grabbed_toplevel = toplevel;
	server->cursor_mode = TINYWL_CURSOR_MOVE;
	server->grab_x = server->cursor->x - toplevel->scene_tree->node.x;
	server->grab_y = server->cursor->y - toplevel->scene_tree->node.y;
}

static int clamp_size(int size, int min, int max) {
	/* Within the client's limits (0 for none), and at least a step. */
	if (max > 0 && size > max) {
		size = max;
	}
	if (size < min) {
		size = min;
	}
	return size < WINDOW_STEP ? WINDOW_STEP : size;
}

static bool window_step(struct tinywl_server *server, xkb_keysym_t sym) {
	/* In window mode, a step for an arrow key (move), or t, s, b and n
	 * (taller, shorter, broader, narrower).  Returns whether it was
	 * one. */
	struct tinywl_toplevel *toplevel = server->grabbed_toplevel;
	struct wlr_scene_node *node = &toplevel->scene_tree->node;
	int dx = 0, dy = 0, dw = 0, dh = 0;
	switch (sym) {
	case XKB_KEY_Left: dx = -WINDOW_STEP; break;
	case XKB_KEY_Right: dx = WINDOW_STEP; break;
	case XKB_KEY_Up: dy = -WINDOW_STEP; break;
	case XKB_KEY_Down: dy = WINDOW_STEP; break;
	case XKB_KEY_t: dh = WINDOW_STEP; break;
	case XKB_KEY_s: dh = -WINDOW_STEP; break;
	case XKB_KEY_b: dw = WINDOW_STEP; break;
	case XKB_KEY_n: dw = -WINDOW_STEP; break;
	default:
		return false;
	}
	if (dx || dy) {
		wlr_scene_node_set_position(node, node->x + dx, node->y + dy);
		/* Keep the mouse's hold on it. */
		server->grab_x -= dx;
		server->grab_y -= dy;
	} else {
		struct wlr_xdg_toplevel_state *current = &toplevel->xdg_toplevel->current;
		server->window_width = clamp_size(server->window_width + dw,
			current->min_width, current->max_width);
		server->window_height = clamp_size(server->window_height + dh,
			current->min_height, current->max_height);
		wlr_xdg_toplevel_set_size(toplevel->xdg_toplevel,
			server->window_width, server->window_height);
	}
	return true;
}

static int handle_repeat(void *data) {
	struct tinywl_server *server = data;
	if (server->window_mode && server->repeat_sym != XKB_KEY_NoSymbol) {
		window_step(server, server->repeat_sym);
		wl_event_source_timer_update(server->repeat_timer, REPEAT_INTERVAL);
	}
	return 0;
}

static bool handle_window_key(struct tinywl_server *server, xkb_keysym_t sym) {
	/* Sys-w is the compositor's, and so are its steps while it's held.
	 * Returns whether the key was one of them. */
	if (sym == XKB_KEY_w) {
		begin_window_mode(server);
		return true;
	}
	if (!server->window_mode || !window_step(server, sym)) {
		return false;
	}
	server->repeat_sym = sym;
	wl_event_source_timer_update(server->repeat_timer, REPEAT_DELAY);
	return true;
}

static void keyboard_handle_key(
		struct wl_listener *listener, void *data) {
	/* This event is raised when a key is pressed or released. */
	struct tinywl_keyboard *keyboard =
		wl_container_of(listener, keyboard, key);
	struct tinywl_server *server = keyboard->server;
	struct wlr_keyboard_key_event *event = data;
	struct wlr_seat *seat = server->seat;
	struct xkb_state *state = keyboard->state;

	/* Translate libinput keycode -> xkbcommon */
	uint32_t keycode = event->keycode + 8;
	/* The key's keysym on its first level, regardless of Shift, and so
	 * on, which is how hotkeys are bound. */
	const xkb_keysym_t *syms;
	int nsyms = xkb_keymap_key_get_syms_by_level(keyboard->wlr_keyboard->keymap,
		keycode, xkb_state_key_get_layout(state, keycode), 0, &syms);
	xkb_keysym_t sym = nsyms > 0 ? syms[0] : XKB_KEY_NoSymbol;
	bool pressed = event->state == WL_KEYBOARD_KEY_STATE_PRESSED;
	bool *swallowed = event->keycode < sizeof(server->swallowed) ?
		&server->swallowed[event->keycode] : NULL;

	/* The key event comes before the keyboard's state is updated, so
	 * System's own press and release are seen by its keysym. */
	bool handled = false;
	if (sym == XKB_KEY_Hyper_L) {
		handled = true;
		if (!pressed) {
			end_window_mode(server);
		}
		if (!pressed && server->system_chord) {
			server->system_chord = false;
			struct policy_buffer buffer;
			policy_encode_system_released(&buffer);
			policy_send(server, &buffer);
		}
	} else if (pressed && (xkb_state_serialize_mods(state, XKB_STATE_MODS_EFFECTIVE) &
			server->system_mask)) {
		/* Keys pressed with System never reach clients, nor do their
		 * releases. */
		handled = true;
		if (swallowed) {
			*swallowed = true;
		}
		if (!handle_window_key(server, sym)) {
			handle_system_key(server, sym, hotkey_modifiers(state));
		}
	} else if (!pressed && swallowed && *swallowed) {
		handled = true;
		*swallowed = false;
		if (sym == XKB_KEY_w) {
			end_window_mode(server);
		} else if (sym == server->repeat_sym) {
			stop_repeat(server);
		}
	}

	if (!handled) {
		/* Otherwise, we pass it along to the client. */
		wlr_seat_set_keyboard(seat, keyboard->wlr_keyboard);
		wlr_seat_keyboard_notify_key(seat, event->time_msec,
			event->keycode, event->state);
	}

	xkb_state_update_key(state, keycode, pressed ? XKB_KEY_DOWN : XKB_KEY_UP);
	sync_modifiers(keyboard);
}

static void keyboard_handle_destroy(struct wl_listener *listener, void *data) {
	/* This event is raised by the keyboard base wlr_input_device to signal
	 * the destruction of the wlr_keyboard. It will no longer receive events
	 * and should be destroyed.
	 */
	struct tinywl_keyboard *keyboard =
		wl_container_of(listener, keyboard, destroy);
	wl_list_remove(&keyboard->modifiers.link);
	wl_list_remove(&keyboard->key.link);
	wl_list_remove(&keyboard->destroy.link);
	wl_list_remove(&keyboard->link);
	xkb_state_unref(keyboard->state);
	free(keyboard);
}

static void server_new_keyboard(struct tinywl_server *server,
		struct wlr_input_device *device) {
	struct wlr_keyboard *wlr_keyboard = wlr_keyboard_from_input_device(device);

	struct tinywl_keyboard *keyboard = calloc(1, sizeof(*keyboard));
	keyboard->server = server;
	keyboard->wlr_keyboard = wlr_keyboard;

	/* Our keymap (see doc/terminal.rst).  Our xkb files come first, so
	 * our rules (which include the system's) can add our option. */
	struct xkb_context *context = xkb_context_new(XKB_CONTEXT_NO_DEFAULT_INCLUDES);
	const char *xkb_dir = getenv("DARQ_XKB_DIR");
	xkb_context_include_path_append(context, xkb_dir ? xkb_dir : DARQ_XKB_DIR);
	xkb_context_include_path_append_default(context);
	struct xkb_rule_names names = {
		.rules = "evdev",
		.model = "pc105",
		.layout = "us",
		.options = "darq:system,ctrl:nocaps,compose:rctrl,lv3:ralt_switch",
	};
	struct xkb_keymap *keymap = xkb_keymap_new_from_names(context, &names,
		XKB_KEYMAP_COMPILE_NO_FLAGS);
	if (keymap == NULL) {
		wlr_log(WLR_ERROR, "failed to compile the keymap");
		exit(1);
	}
	xkb_mod_index_t mod3 = xkb_keymap_mod_get_index(keymap, "Mod3");
	server->system_mask = 1u << mod3;

	wlr_keyboard_set_keymap(wlr_keyboard, keymap);
	keyboard->state = xkb_state_new(keymap);
	xkb_keymap_unref(keymap);
	xkb_context_unref(context);
	wlr_keyboard_set_repeat_info(wlr_keyboard, 25, 600);

	/* Here we set up listeners for keyboard events. */
	keyboard->modifiers.notify = keyboard_handle_modifiers;
	wl_signal_add(&wlr_keyboard->events.modifiers, &keyboard->modifiers);
	keyboard->key.notify = keyboard_handle_key;
	wl_signal_add(&wlr_keyboard->events.key, &keyboard->key);
	keyboard->destroy.notify = keyboard_handle_destroy;
	wl_signal_add(&device->events.destroy, &keyboard->destroy);

	wlr_seat_set_keyboard(server->seat, keyboard->wlr_keyboard);

	/* And add the keyboard to our list of keyboards */
	wl_list_insert(&server->keyboards, &keyboard->link);
}

static void server_new_pointer(struct tinywl_server *server,
		struct wlr_input_device *device) {
	/* We don't do anything special with pointers. All of our pointer handling
	 * is proxied through wlr_cursor. On another compositor, you might take this
	 * opportunity to do libinput configuration on the device to set
	 * acceleration, etc. */
	wlr_cursor_attach_input_device(server->cursor, device);
}

static void server_new_input(struct wl_listener *listener, void *data) {
	/* This event is raised by the backend when a new input device becomes
	 * available. */
	struct tinywl_server *server =
		wl_container_of(listener, server, new_input);
	struct wlr_input_device *device = data;
	switch (device->type) {
	case WLR_INPUT_DEVICE_KEYBOARD:
		server_new_keyboard(server, device);
		break;
	case WLR_INPUT_DEVICE_POINTER:
		server_new_pointer(server, device);
		break;
	default:
		break;
	}
	/* We need to let the wlr_seat know what our capabilities are, which is
	 * communiciated to the client. In TinyWL we always have a cursor, even if
	 * there are no pointer devices, so we always include that capability. */
	uint32_t caps = WL_SEAT_CAPABILITY_POINTER;
	if (!wl_list_empty(&server->keyboards)) {
		caps |= WL_SEAT_CAPABILITY_KEYBOARD;
	}
	wlr_seat_set_capabilities(server->seat, caps);
}

static void seat_request_cursor(struct wl_listener *listener, void *data) {
	struct tinywl_server *server = wl_container_of(
			listener, server, request_cursor);
	/* This event is raised by the seat when a client provides a cursor image */
	struct wlr_seat_pointer_request_set_cursor_event *event = data;
	struct wlr_seat_client *focused_client =
		server->seat->pointer_state.focused_client;
	/* This can be sent by any client, so we check to make sure this one is
	 * actually has pointer focus first. */
	if (focused_client == event->seat_client) {
		/* Once we've vetted the client, we can tell the cursor to use the
		 * provided surface as the cursor image. It will set the hardware cursor
		 * on the output that it's currently on and continue to do so as the
		 * cursor moves between outputs. */
		wlr_cursor_set_surface(server->cursor, event->surface,
				event->hotspot_x, event->hotspot_y);
	}
}

static void seat_request_set_selection(struct wl_listener *listener, void *data) {
	/* This event is raised by the seat when a client wants to set the selection,
	 * usually when the user copies something. wlroots allows compositors to
	 * ignore such requests if they so choose, but in tinywl we always honor
	 */
	struct tinywl_server *server = wl_container_of(
			listener, server, request_set_selection);
	struct wlr_seat_request_set_selection_event *event = data;
	wlr_seat_set_selection(server->seat, event->source, event->serial);
}

static struct tinywl_toplevel *desktop_toplevel_at(
		struct tinywl_server *server, double lx, double ly,
		struct wlr_surface **surface, double *sx, double *sy) {
	/* This returns the topmost node in the scene at the given layout coords.
	 * We only care about surface nodes as we are specifically looking for a
	 * surface in the surface tree of a tinywl_toplevel. */
	struct wlr_scene_node *node = wlr_scene_node_at(
		&server->scene->tree.node, lx, ly, sx, sy);
	if (node == NULL || node->type != WLR_SCENE_NODE_BUFFER) {
		return NULL;
	}
	struct wlr_scene_buffer *scene_buffer = wlr_scene_buffer_from_node(node);
	struct wlr_scene_surface *scene_surface =
		wlr_scene_surface_try_from_buffer(scene_buffer);
	if (!scene_surface) {
		return NULL;
	}

	*surface = scene_surface->surface;
	/* Find the node corresponding to the tinywl_toplevel at the root of this
	 * surface tree, it is the only one for which we set the data field. */
	struct wlr_scene_tree *tree = node->parent;
	while (tree != NULL && tree->node.data == NULL) {
		tree = tree->node.parent;
	}
	return tree->node.data;
}

static void reset_cursor_mode(struct tinywl_server *server) {
	/* Reset the cursor mode to passthrough. */
	server->cursor_mode = TINYWL_CURSOR_PASSTHROUGH;
	server->grabbed_toplevel = NULL;
}

static void process_cursor_move(struct tinywl_server *server, uint32_t time) {
	/* Move the grabbed toplevel to the new position. */
	struct tinywl_toplevel *toplevel = server->grabbed_toplevel;
	wlr_scene_node_set_position(&toplevel->scene_tree->node,
		server->cursor->x - server->grab_x,
		server->cursor->y - server->grab_y);
}

static void process_cursor_resize(struct tinywl_server *server, uint32_t time) {
	/*
	 * Resizing the grabbed toplevel can be a little bit complicated, because we
	 * could be resizing from any corner or edge. This not only resizes the
	 * toplevel on one or two axes, but can also move the toplevel if you resize
	 * from the top or left edges (or top-left corner).
	 *
	 * Note that some shortcuts are taken here. In a more fleshed-out
	 * compositor, you'd wait for the client to prepare a buffer at the new
	 * size, then commit any movement that was prepared.
	 */
	struct tinywl_toplevel *toplevel = server->grabbed_toplevel;
	double border_x = server->cursor->x - server->grab_x;
	double border_y = server->cursor->y - server->grab_y;
	int new_left = server->grab_geobox.x;
	int new_right = server->grab_geobox.x + server->grab_geobox.width;
	int new_top = server->grab_geobox.y;
	int new_bottom = server->grab_geobox.y + server->grab_geobox.height;

	if (server->resize_edges & WLR_EDGE_TOP) {
		new_top = border_y;
		if (new_top >= new_bottom) {
			new_top = new_bottom - 1;
		}
	} else if (server->resize_edges & WLR_EDGE_BOTTOM) {
		new_bottom = border_y;
		if (new_bottom <= new_top) {
			new_bottom = new_top + 1;
		}
	}
	if (server->resize_edges & WLR_EDGE_LEFT) {
		new_left = border_x;
		if (new_left >= new_right) {
			new_left = new_right - 1;
		}
	} else if (server->resize_edges & WLR_EDGE_RIGHT) {
		new_right = border_x;
		if (new_right <= new_left) {
			new_right = new_left + 1;
		}
	}

	struct wlr_box geo_box;
	wlr_xdg_surface_get_geometry(toplevel->xdg_toplevel->base, &geo_box);
	wlr_scene_node_set_position(&toplevel->scene_tree->node,
		new_left - geo_box.x, new_top - geo_box.y);

	int new_width = new_right - new_left;
	int new_height = new_bottom - new_top;
	wlr_xdg_toplevel_set_size(toplevel->xdg_toplevel, new_width, new_height);
}

static void process_cursor_motion(struct tinywl_server *server, uint32_t time) {
	/* If the mode is non-passthrough, delegate to those functions. */
	if (server->cursor_mode == TINYWL_CURSOR_MOVE) {
		process_cursor_move(server, time);
		return;
	} else if (server->cursor_mode == TINYWL_CURSOR_RESIZE) {
		process_cursor_resize(server, time);
		return;
	}

	/* Otherwise, find the toplevel under the pointer and send the event along. */
	double sx, sy;
	struct wlr_seat *seat = server->seat;
	struct wlr_surface *surface = NULL;
	struct tinywl_toplevel *toplevel = desktop_toplevel_at(server,
			server->cursor->x, server->cursor->y, &surface, &sx, &sy);
	if (!toplevel) {
		/* If there's no toplevel under the cursor, set the cursor image to a
		 * default. This is what makes the cursor image appear when you move it
		 * around the screen, not over any toplevels. */
		wlr_cursor_set_xcursor(server->cursor, server->cursor_mgr, "default");
	}
	if (surface) {
		/*
		 * Send pointer enter and motion events.
		 *
		 * The enter event gives the surface "pointer focus", which is distinct
		 * from keyboard focus. You get pointer focus by moving the pointer over
		 * a window.
		 *
		 * Note that wlroots will avoid sending duplicate enter/motion events if
		 * the surface has already has pointer focus or if the client is already
		 * aware of the coordinates passed.
		 */
		wlr_seat_pointer_notify_enter(seat, surface, sx, sy);
		wlr_seat_pointer_notify_motion(seat, time, sx, sy);
	} else {
		/* Clear pointer focus so future button events and such are not sent to
		 * the last client to have the cursor over it. */
		wlr_seat_pointer_clear_focus(seat);
	}

	/* Focus follows the mouse, sloppily: into application and overlay
	 * windows, but not into the background, or out of an overlay window,
	 * one of the Terminal's panels, which keeps it until it's gone. */
	struct tinywl_toplevel *focused = server->focused;
	if (toplevel && toplevel != focused &&
			(toplevel->layer == LAYER_APPS || toplevel->layer == LAYER_OVERLAY) &&
			!(focused && focused->layer == LAYER_OVERLAY)) {
		focus_toplevel(toplevel);
	}
}

static void server_cursor_motion(struct wl_listener *listener, void *data) {
	/* This event is forwarded by the cursor when a pointer emits a _relative_
	 * pointer motion event (i.e. a delta) */
	struct tinywl_server *server =
		wl_container_of(listener, server, cursor_motion);
	struct wlr_pointer_motion_event *event = data;
	/* The cursor doesn't move unless we tell it to. The cursor automatically
	 * handles constraining the motion to the output layout, as well as any
	 * special configuration applied for the specific input device which
	 * generated the event. You can pass NULL for the device if you want to move
	 * the cursor around without any input. */
	wlr_cursor_move(server->cursor, &event->pointer->base,
			event->delta_x, event->delta_y);
	process_cursor_motion(server, event->time_msec);
}

static void server_cursor_motion_absolute(
		struct wl_listener *listener, void *data) {
	/* This event is forwarded by the cursor when a pointer emits an _absolute_
	 * motion event, from 0..1 on each axis. This happens, for example, when
	 * wlroots is running under a Wayland window rather than KMS+DRM, and you
	 * move the mouse over the window. You could enter the window from any edge,
	 * so we have to warp the mouse there. There is also some hardware which
	 * emits these events. */
	struct tinywl_server *server =
		wl_container_of(listener, server, cursor_motion_absolute);
	struct wlr_pointer_motion_absolute_event *event = data;
	wlr_cursor_warp_absolute(server->cursor, &event->pointer->base, event->x,
		event->y);
	process_cursor_motion(server, event->time_msec);
}

static void server_cursor_button(struct wl_listener *listener, void *data) {
	/* This event is forwarded by the cursor when a pointer emits a button
	 * event. */
	struct tinywl_server *server =
		wl_container_of(listener, server, cursor_button);
	struct wlr_pointer_button_event *event = data;
	/* Notify the client with pointer focus that a button press has occurred */
	wlr_seat_pointer_notify_button(server->seat,
			event->time_msec, event->button, event->state);
	double sx, sy;
	struct wlr_surface *surface = NULL;
	struct tinywl_toplevel *toplevel = desktop_toplevel_at(server,
			server->cursor->x, server->cursor->y, &surface, &sx, &sy);
	if (event->state == WL_POINTER_BUTTON_STATE_RELEASED) {
		/* If you released any buttons, we exit interactive move/resize
		 * mode, unless it's Sys-w's. */
		if (!server->window_mode) {
			reset_cursor_mode(server);
		}
	} else if (toplevel != NULL && reachable(toplevel)) {
		/* Raise and focus that client if the button was _pressed_ */
		raise_toplevel(toplevel);
		focus_toplevel(toplevel);
	}
}

static void server_cursor_axis(struct wl_listener *listener, void *data) {
	/* This event is forwarded by the cursor when a pointer emits an axis event,
	 * for example when you move the scroll wheel. */
	struct tinywl_server *server =
		wl_container_of(listener, server, cursor_axis);
	struct wlr_pointer_axis_event *event = data;
	/* Notify the client with pointer focus of the axis event. */
	wlr_seat_pointer_notify_axis(server->seat,
			event->time_msec, event->orientation, event->delta,
			event->delta_discrete, event->source, event->relative_direction);
}

static void server_cursor_frame(struct wl_listener *listener, void *data) {
	/* This event is forwarded by the cursor when a pointer emits an frame
	 * event. Frame events are sent after regular pointer events to group
	 * multiple events together. For instance, two axis events may happen at the
	 * same time, in which case a frame event won't be sent in between. */
	struct tinywl_server *server =
		wl_container_of(listener, server, cursor_frame);
	/* Notify the client with pointer focus of the frame event. */
	wlr_seat_pointer_notify_frame(server->seat);
}

static void output_frame(struct wl_listener *listener, void *data) {
	/* This function is called every time an output is ready to display a frame,
	 * generally at the output's refresh rate (e.g. 60Hz). */
	struct tinywl_output *output = wl_container_of(listener, output, frame);
	struct wlr_scene *scene = output->server->scene;

	struct wlr_scene_output *scene_output = wlr_scene_get_scene_output(
		scene, output->wlr_output);

	/* Render the scene if needed and commit the output */
	wlr_scene_output_commit(scene_output, NULL);

	struct timespec now;
	clock_gettime(CLOCK_MONOTONIC, &now);
	wlr_scene_output_send_frame_done(scene_output, &now);
}

static void output_request_state(struct wl_listener *listener, void *data) {
	/* This function is called when the backend requests a new state for
	 * the output. For example, Wayland and X11 backends request a new mode
	 * when the output window is resized. */
	struct tinywl_output *output = wl_container_of(listener, output, request_state);
	const struct wlr_output_event_request_state *event = data;
	wlr_output_commit_state(output->wlr_output, event->state);
}

static void output_destroy(struct wl_listener *listener, void *data) {
	struct tinywl_output *output = wl_container_of(listener, output, destroy);

	wl_list_remove(&output->frame.link);
	wl_list_remove(&output->request_state.link);
	wl_list_remove(&output->destroy.link);
	wl_list_remove(&output->link);
	free(output);
}

static void server_new_output(struct wl_listener *listener, void *data) {
	/* This event is raised by the backend when a new output (aka a display or
	 * monitor) becomes available. */
	struct tinywl_server *server =
		wl_container_of(listener, server, new_output);
	struct wlr_output *wlr_output = data;

	/* Configures the output created by the backend to use our allocator
	 * and our renderer. Must be done once, before commiting the output */
	wlr_output_init_render(wlr_output, server->allocator, server->renderer);

	/* The output may be disabled, switch it on. */
	struct wlr_output_state state;
	wlr_output_state_init(&state);
	wlr_output_state_set_enabled(&state, true);

	/* Some backends don't have modes. DRM+KMS does, and we need to set a mode
	 * before we can use the output. The mode is a tuple of (width, height,
	 * refresh rate), and each monitor supports only a specific set of modes. We
	 * just pick the monitor's preferred mode, a more sophisticated compositor
	 * would let the user configure it. */
	struct wlr_output_mode *mode = wlr_output_preferred_mode(wlr_output);
	if (mode != NULL) {
		wlr_output_state_set_mode(&state, mode);
	}

	/* Atomically applies the new output state. */
	wlr_output_commit_state(wlr_output, &state);
	wlr_output_state_finish(&state);

	/* Allocates and configures our state for this output */
	struct tinywl_output *output = calloc(1, sizeof(*output));
	output->wlr_output = wlr_output;
	output->server = server;

	/* Sets up a listener for the frame event. */
	output->frame.notify = output_frame;
	wl_signal_add(&wlr_output->events.frame, &output->frame);

	/* Sets up a listener for the state request event. */
	output->request_state.notify = output_request_state;
	wl_signal_add(&wlr_output->events.request_state, &output->request_state);

	/* Sets up a listener for the destroy event. */
	output->destroy.notify = output_destroy;
	wl_signal_add(&wlr_output->events.destroy, &output->destroy);

	wl_list_insert(&server->outputs, &output->link);

	/* Adds this to the output layout. The add_auto function arranges outputs
	 * from left-to-right in the order they appear. A more sophisticated
	 * compositor would let the user configure the arrangement of outputs in the
	 * layout.
	 *
	 * The output layout utility automatically adds a wl_output global to the
	 * display, which Wayland clients can see to find out information about the
	 * output (such as DPI, scale factor, manufacturer, etc).
	 */
	struct wlr_output_layout_output *l_output = wlr_output_layout_add_auto(server->output_layout,
		wlr_output);
	struct wlr_scene_output *scene_output = wlr_scene_output_create(server->scene, wlr_output);
	wlr_scene_output_layout_add_output(server->scene_layout, l_output, scene_output);
}

static bool fills_output(struct tinywl_toplevel *toplevel) {
	return toplevel->layer == LAYER_BACKGROUND || toplevel->layer == LAYER_LOCK;
}

static void set_layer(struct tinywl_toplevel *toplevel) {
	/* The Terminal's windows go in the layer their title names, or the
	 * overlay; everyone else's go in the applications layer. */
	struct tinywl_server *server = toplevel->server;
	struct wl_client *client = wl_resource_get_client(toplevel->xdg_toplevel->resource);
	const char *title = toplevel->xdg_toplevel->title ? toplevel->xdg_toplevel->title : "";
	enum darq_layer layer = LAYER_APPS;
	if (client == server->terminal) {
		if (strcmp(title, "darq:background") == 0) {
			layer = LAYER_BACKGROUND;
		} else if (strcmp(title, "darq:lock") == 0) {
			layer = LAYER_LOCK;
		} else {
			layer = LAYER_OVERLAY;
		}
	}
	toplevel->layer = layer;
	wlr_scene_node_reparent(&toplevel->scene_tree->node, server->layers[layer]);
}

static struct wlr_output *cursor_output(struct tinywl_server *server) {
	struct wlr_output *output = wlr_output_layout_output_at(server->output_layout,
		server->cursor->x, server->cursor->y);
	return output ? output : wlr_output_layout_get_center_output(server->output_layout);
}

static void fit_to_output(struct tinywl_toplevel *toplevel, bool initial) {
	/* Fill the output the client asked for (Qt's showFullScreen does),
	 * or the one it's on, or, at first, the cursor's. */
	struct tinywl_server *server = toplevel->server;
	struct wlr_scene_node *node = &toplevel->scene_tree->node;
	struct wlr_output *output = toplevel->xdg_toplevel->requested.fullscreen_output;
	if (output == NULL || wlr_output_layout_get(server->output_layout, output) == NULL) {
		output = initial ? cursor_output(server) :
			wlr_output_layout_output_at(server->output_layout, node->x, node->y);
	}
	if (output == NULL) {
		output = wlr_output_layout_get_center_output(server->output_layout);
	}
	if (output == NULL) {
		/* No outputs yet: it's fitted when there are. */
		wlr_xdg_toplevel_set_size(toplevel->xdg_toplevel, 0, 0);
		return;
	}
	struct wlr_box box;
	wlr_output_layout_get_box(server->output_layout, output, &box);
	wlr_xdg_toplevel_set_fullscreen(toplevel->xdg_toplevel, true);
	wlr_xdg_toplevel_set_size(toplevel->xdg_toplevel, box.width, box.height);
	wlr_scene_node_set_position(node, box.x, box.y);
}

static void place_centred(struct tinywl_toplevel *toplevel) {
	/* In the middle of the cursor's output. */
	struct tinywl_server *server = toplevel->server;
	struct wlr_output *output = cursor_output(server);
	if (output == NULL) {
		return;
	}
	struct wlr_box box, geo_box;
	wlr_output_layout_get_box(server->output_layout, output, &box);
	wlr_xdg_surface_get_geometry(toplevel->xdg_toplevel->base, &geo_box);
	wlr_scene_node_set_position(&toplevel->scene_tree->node,
		box.x + (box.width - geo_box.width) / 2 - geo_box.x,
		box.y + (box.height - geo_box.height) / 2 - geo_box.y);
}

static void layout_change(struct wl_listener *listener, void *data) {
	/* Outputs were added, removed, or resized: refit the windows that
	 * fill them. */
	struct tinywl_server *server = wl_container_of(listener, server, layout_change);
	struct tinywl_toplevel *toplevel;
	wl_list_for_each(toplevel, &server->toplevels, link) {
		if (fills_output(toplevel)) {
			fit_to_output(toplevel, false);
		}
	}
}

static void xdg_toplevel_map(struct wl_listener *listener, void *data) {
	/* Called when the surface is mapped, or ready to display on-screen. */
	struct tinywl_toplevel *toplevel = wl_container_of(listener, toplevel, map);

	struct tinywl_server *server = toplevel->server;
	wl_list_insert(&server->toplevels, &toplevel->link);

	/* The Terminal's own windows aren't in the window list. */
	struct wl_client *client = wl_resource_get_client(toplevel->xdg_toplevel->resource);
	if (client != server->terminal) {
		toplevel->reported = true;
		struct policy_buffer buffer;
		policy_encode_window_added(&buffer, toplevel->window,
			toplevel->xdg_toplevel->title, toplevel->xdg_toplevel->app_id);
		policy_send(server, &buffer);
	}

	if (toplevel->layer == LAYER_APPS || toplevel->layer == LAYER_OVERLAY) {
		place_centred(toplevel);
	}
	raise_toplevel(toplevel);
	/* A new background doesn't take the focus from anything. */
	if (toplevel->layer != LAYER_BACKGROUND || server->focused == NULL) {
		focus_toplevel(toplevel);
	}
}

static void xdg_toplevel_unmap(struct wl_listener *listener, void *data) {
	/* Called when the surface is unmapped, and should no longer be shown. */
	struct tinywl_toplevel *toplevel = wl_container_of(listener, toplevel, unmap);

	/* Reset the cursor mode if the grabbed toplevel was unmapped. */
	struct tinywl_server *server = toplevel->server;
	if (toplevel == server->grabbed_toplevel) {
		end_window_mode(server);
		reset_cursor_mode(server);
	}

	wl_list_remove(&toplevel->link);

	if (toplevel->reported) {
		toplevel->reported = false;
		struct policy_buffer buffer;
		policy_encode_window_removed(&buffer, toplevel->window);
		policy_send(server, &buffer);
	}
	if (toplevel == server->focused) {
		server->focused = NULL;
		refocus(server);
	}
}

static void xdg_toplevel_set_title(struct wl_listener *listener, void *data) {
	struct tinywl_toplevel *toplevel = wl_container_of(listener, toplevel, set_title);
	if (toplevel->reported) {
		struct policy_buffer buffer;
		policy_encode_window_retitled(&buffer, toplevel->window,
			toplevel->xdg_toplevel->title);
		policy_send(toplevel->server, &buffer);
	}
}

static void xdg_toplevel_commit(struct wl_listener *listener, void *data) {
	/* Called when a new surface state is committed. */
	struct tinywl_toplevel *toplevel = wl_container_of(listener, toplevel, commit);

	if (toplevel->xdg_toplevel->base->initial_commit) {
		/* When an xdg_surface performs an initial commit, the compositor must
		 * reply with a configure so the client can map the surface.  The
		 * toplevel's title says its layer, by now, if it's the Terminal's.
		 * Background and lock windows fill an output; others pick their
		 * own size. */
		set_layer(toplevel);
		if (fills_output(toplevel)) {
			fit_to_output(toplevel, true);
		} else {
			wlr_xdg_toplevel_set_size(toplevel->xdg_toplevel, 0, 0);
		}
		if (toplevel->decoration) {
			wlr_xdg_toplevel_decoration_v1_set_mode(toplevel->decoration,
				WLR_XDG_TOPLEVEL_DECORATION_V1_MODE_SERVER_SIDE);
		}
	}
}

static void decoration_destroy(struct wl_listener *listener, void *data) {
	struct tinywl_toplevel *toplevel =
		wl_container_of(listener, toplevel, decoration_destroy);
	wl_list_remove(&toplevel->decoration_destroy.link);
	toplevel->decoration = NULL;
}

static void server_new_decoration(struct wl_listener *listener, void *data) {
	/* Decorations are always the server's, and it draws none.  The mode
	 * can only be set once the surface is initialized: if it isn't yet,
	 * it's set on its initial commit. */
	struct wlr_xdg_toplevel_decoration_v1 *decoration = data;
	struct wlr_scene_tree *tree = decoration->toplevel->base->data;
	struct tinywl_toplevel *toplevel = tree->node.data;
	toplevel->decoration = decoration;
	toplevel->decoration_destroy.notify = decoration_destroy;
	wl_signal_add(&decoration->events.destroy, &toplevel->decoration_destroy);
	if (decoration->toplevel->base->initialized) {
		wlr_xdg_toplevel_decoration_v1_set_mode(decoration,
			WLR_XDG_TOPLEVEL_DECORATION_V1_MODE_SERVER_SIDE);
	}
}

static void xdg_toplevel_destroy(struct wl_listener *listener, void *data) {
	/* Called when the xdg_toplevel is destroyed. */
	struct tinywl_toplevel *toplevel = wl_container_of(listener, toplevel, destroy);

	wl_list_remove(&toplevel->map.link);
	wl_list_remove(&toplevel->unmap.link);
	wl_list_remove(&toplevel->commit.link);
	wl_list_remove(&toplevel->destroy.link);
	wl_list_remove(&toplevel->request_move.link);
	wl_list_remove(&toplevel->request_resize.link);
	wl_list_remove(&toplevel->request_maximize.link);
	wl_list_remove(&toplevel->request_fullscreen.link);
	wl_list_remove(&toplevel->set_title.link);
	if (toplevel->decoration) {
		wl_list_remove(&toplevel->decoration_destroy.link);
	}

	free(toplevel);
}

static void begin_interactive(struct tinywl_toplevel *toplevel,
		enum tinywl_cursor_mode mode, uint32_t edges) {
	/* This function sets up an interactive move or resize operation, where the
	 * compositor stops propegating pointer events to clients and instead
	 * consumes them itself, to move or resize windows. */
	struct tinywl_server *server = toplevel->server;
	struct wlr_surface *focused_surface =
		server->seat->pointer_state.focused_surface;
	if (toplevel->xdg_toplevel->base->surface !=
			wlr_surface_get_root_surface(focused_surface)) {
		/* Deny move/resize requests from unfocused clients. */
		return;
	}
	server->grabbed_toplevel = toplevel;
	server->cursor_mode = mode;

	if (mode == TINYWL_CURSOR_MOVE) {
		server->grab_x = server->cursor->x - toplevel->scene_tree->node.x;
		server->grab_y = server->cursor->y - toplevel->scene_tree->node.y;
	} else {
		struct wlr_box geo_box;
		wlr_xdg_surface_get_geometry(toplevel->xdg_toplevel->base, &geo_box);

		double border_x = (toplevel->scene_tree->node.x + geo_box.x) +
			((edges & WLR_EDGE_RIGHT) ? geo_box.width : 0);
		double border_y = (toplevel->scene_tree->node.y + geo_box.y) +
			((edges & WLR_EDGE_BOTTOM) ? geo_box.height : 0);
		server->grab_x = server->cursor->x - border_x;
		server->grab_y = server->cursor->y - border_y;

		server->grab_geobox = geo_box;
		server->grab_geobox.x += toplevel->scene_tree->node.x;
		server->grab_geobox.y += toplevel->scene_tree->node.y;

		server->resize_edges = edges;
	}
}

static void xdg_toplevel_request_move(
		struct wl_listener *listener, void *data) {
	/* This event is raised when a client would like to begin an interactive
	 * move, typically because the user clicked on their client-side
	 * decorations. Note that a more sophisticated compositor should check the
	 * provided serial against a list of button press serials sent to this
	 * client, to prevent the client from requesting this whenever they want. */
	struct tinywl_toplevel *toplevel = wl_container_of(listener, toplevel, request_move);
	begin_interactive(toplevel, TINYWL_CURSOR_MOVE, 0);
}

static void xdg_toplevel_request_resize(
		struct wl_listener *listener, void *data) {
	/* This event is raised when a client would like to begin an interactive
	 * resize, typically because the user clicked on their client-side
	 * decorations. Note that a more sophisticated compositor should check the
	 * provided serial against a list of button press serials sent to this
	 * client, to prevent the client from requesting this whenever they want. */
	struct wlr_xdg_toplevel_resize_event *event = data;
	struct tinywl_toplevel *toplevel = wl_container_of(listener, toplevel, request_resize);
	begin_interactive(toplevel, TINYWL_CURSOR_RESIZE, event->edges);
}

static void xdg_toplevel_request_maximize(
		struct wl_listener *listener, void *data) {
	/* This event is raised when a client would like to maximize itself,
	 * typically because the user clicked on the maximize button on client-side
	 * decorations. tinywl doesn't support maximization, but to conform to
	 * xdg-shell protocol we still must send a configure.
	 * wlr_xdg_surface_schedule_configure() is used to send an empty reply.
	 * However, if the request was sent before an initial commit, we don't do
	 * anything and let the client finish the initial surface setup. */
	struct tinywl_toplevel *toplevel =
		wl_container_of(listener, toplevel, request_maximize);
	if (toplevel->xdg_toplevel->base->initialized) {
		wlr_xdg_surface_schedule_configure(toplevel->xdg_toplevel->base);
	}
}

static void xdg_toplevel_request_fullscreen(
		struct wl_listener *listener, void *data) {
	/* Just as with request_maximize, we must send a configure here. */
	struct tinywl_toplevel *toplevel =
		wl_container_of(listener, toplevel, request_fullscreen);
	if (!toplevel->xdg_toplevel->base->initialized) {
		return;
	}
	if (fills_output(toplevel)) {
		fit_to_output(toplevel, false);
	}
	wlr_xdg_surface_schedule_configure(toplevel->xdg_toplevel->base);
}

static void server_new_xdg_toplevel(struct wl_listener *listener, void *data) {
	/* This event is raised when a client creates a new toplevel (application window). */
	struct tinywl_server *server = wl_container_of(listener, server, new_xdg_toplevel);
	struct wlr_xdg_toplevel *xdg_toplevel = data;

	/* Allocate a tinywl_toplevel for this surface */
	struct tinywl_toplevel *toplevel = calloc(1, sizeof(*toplevel));
	toplevel->server = server;
	toplevel->xdg_toplevel = xdg_toplevel;
	toplevel->window = server->next_window++;
	toplevel->layer = LAYER_APPS;
	toplevel->scene_tree =
		wlr_scene_xdg_surface_create(server->layers[LAYER_APPS], xdg_toplevel->base);
	toplevel->scene_tree->node.data = toplevel;
	xdg_toplevel->base->data = toplevel->scene_tree;

	/* Listen to the various events it can emit */
	toplevel->map.notify = xdg_toplevel_map;
	wl_signal_add(&xdg_toplevel->base->surface->events.map, &toplevel->map);
	toplevel->unmap.notify = xdg_toplevel_unmap;
	wl_signal_add(&xdg_toplevel->base->surface->events.unmap, &toplevel->unmap);
	toplevel->commit.notify = xdg_toplevel_commit;
	wl_signal_add(&xdg_toplevel->base->surface->events.commit, &toplevel->commit);

	toplevel->destroy.notify = xdg_toplevel_destroy;
	wl_signal_add(&xdg_toplevel->events.destroy, &toplevel->destroy);

	/* cotd */
	toplevel->request_move.notify = xdg_toplevel_request_move;
	wl_signal_add(&xdg_toplevel->events.request_move, &toplevel->request_move);
	toplevel->request_resize.notify = xdg_toplevel_request_resize;
	wl_signal_add(&xdg_toplevel->events.request_resize, &toplevel->request_resize);
	toplevel->request_maximize.notify = xdg_toplevel_request_maximize;
	wl_signal_add(&xdg_toplevel->events.request_maximize, &toplevel->request_maximize);
	toplevel->request_fullscreen.notify = xdg_toplevel_request_fullscreen;
	wl_signal_add(&xdg_toplevel->events.request_fullscreen, &toplevel->request_fullscreen);
	toplevel->set_title.notify = xdg_toplevel_set_title;
	wl_signal_add(&xdg_toplevel->events.set_title, &toplevel->set_title);
}

static void xdg_popup_commit(struct wl_listener *listener, void *data) {
	/* Called when a new surface state is committed. */
	struct tinywl_popup *popup = wl_container_of(listener, popup, commit);

	if (popup->xdg_popup->base->initial_commit) {
		/* When an xdg_surface performs an initial commit, the compositor must
		 * reply with a configure so the client can map the surface.
		 * tinywl sends an empty configure. A more sophisticated compositor
		 * might change an xdg_popup's geometry to ensure it's not positioned
		 * off-screen, for example. */
		wlr_xdg_surface_schedule_configure(popup->xdg_popup->base);
	}
}

static void xdg_popup_destroy(struct wl_listener *listener, void *data) {
	/* Called when the xdg_popup is destroyed. */
	struct tinywl_popup *popup = wl_container_of(listener, popup, destroy);

	wl_list_remove(&popup->commit.link);
	wl_list_remove(&popup->destroy.link);

	free(popup);
}

static void server_new_xdg_popup(struct wl_listener *listener, void *data) {
	/* This event is raised when a client creates a new popup. */
	struct wlr_xdg_popup *xdg_popup = data;

	struct tinywl_popup *popup = calloc(1, sizeof(*popup));
	popup->xdg_popup = xdg_popup;

	/* We must add xdg popups to the scene graph so they get rendered. The
	 * wlroots scene graph provides a helper for this, but to use it we must
	 * provide the proper parent scene node of the xdg popup. To enable this,
	 * we always set the user data field of xdg_surfaces to the corresponding
	 * scene node. */
	struct wlr_xdg_surface *parent = wlr_xdg_surface_try_from_wlr_surface(xdg_popup->parent);
	assert(parent != NULL);
	struct wlr_scene_tree *parent_tree = parent->data;
	xdg_popup->base->data = wlr_scene_xdg_surface_create(parent_tree, xdg_popup->base);

	popup->commit.notify = xdg_popup_commit;
	wl_signal_add(&xdg_popup->base->surface->events.commit, &popup->commit);

	popup->destroy.notify = xdg_popup_destroy;
	wl_signal_add(&xdg_popup->events.destroy, &popup->destroy);
}

static const struct wlr_keyboard_impl test_keyboard_impl = {
	.name = "darq-test-keyboard",
};

static const struct wlr_pointer_impl test_pointer_impl = {
	.name = "darq-test-pointer",
};

static uint32_t now_msec(void) {
	struct timespec now;
	clock_gettime(CLOCK_MONOTONIC, &now);
	return now.tv_sec * 1000 + now.tv_nsec / 1000000;
}

static void test_input(struct tinywl_server *server, const char *line) {
	/* A line of test input: see the top of this file. */
	unsigned int code, pressed, latched, locked;
	double x, y;
	bool host = strncmp(line, "host", 4) == 0;
	if (sscanf(line, host ? "hostkey %u %u" : "key %u %u", &code, &pressed) == 2) {
		struct wlr_keyboard_key_event event = {
			.time_msec = now_msec(),
			.keycode = code,
			.update_state = !host,
			.state = pressed ? WL_KEYBOARD_KEY_STATE_PRESSED : WL_KEYBOARD_KEY_STATE_RELEASED,
		};
		wlr_keyboard_notify_key(&server->test_keyboard, &event);
	} else if (sscanf(line, "hostmods %u %u %u", &code, &latched, &locked) == 3) {
		wlr_keyboard_notify_modifiers(&server->test_keyboard, code, latched, locked, 0);
	} else if (sscanf(line, "move %lf %lf", &x, &y) == 2) {
		/* Absolute motion is from 0 to 1, across the layout. */
		struct wlr_box box;
		wlr_output_layout_get_box(server->output_layout, NULL, &box);
		if (wlr_box_empty(&box)) {
			return;
		}
		struct wlr_pointer_motion_absolute_event event = {
			.pointer = &server->test_pointer,
			.time_msec = now_msec(),
			.x = (x - box.x) / box.width,
			.y = (y - box.y) / box.height,
		};
		wl_signal_emit_mutable(&server->test_pointer.events.motion_absolute, &event);
		wl_signal_emit_mutable(&server->test_pointer.events.frame, &server->test_pointer);
	} else if (sscanf(line, "button %u %u", &code, &pressed) == 2) {
		struct wlr_pointer_button_event event = {
			.pointer = &server->test_pointer,
			.time_msec = now_msec(),
			.button = code,
			.state = pressed ? WL_POINTER_BUTTON_STATE_PRESSED : WL_POINTER_BUTTON_STATE_RELEASED,
		};
		wl_signal_emit_mutable(&server->test_pointer.events.button, &event);
		wl_signal_emit_mutable(&server->test_pointer.events.frame, &server->test_pointer);
	} else {
		wlr_log(WLR_ERROR, "test input: bad line '%s'", line);
	}
}

static int handle_test_input(int fd, uint32_t mask, void *data) {
	/* Lines of test input.  At its end, the test devices stay, unused. */
	struct tinywl_server *server = data;
	size_t room = sizeof(server->test_input) - server->test_input_length - 1;
	ssize_t n = read(fd, server->test_input + server->test_input_length, room);
	if (n <= 0) {
		if (n < 0 && errno == EINTR) {
			return 0;
		}
		wl_event_source_remove(server->test_input_source);
		server->test_input_source = NULL;
		return 0;
	}
	server->test_input_length += n;
	server->test_input[server->test_input_length] = '\0';

	char *line = server->test_input;
	char *end;
	while ((end = strchr(line, '\n')) != NULL) {
		*end = '\0';
		test_input(server, line);
		line = end + 1;
	}
	server->test_input_length -= line - server->test_input;
	memmove(server->test_input, line, server->test_input_length);
	if (server->test_input_length == sizeof(server->test_input) - 1) {
		/* A line too long to be one of ours. */
		server->test_input_length = 0;
	}
	return 0;
}

static int handle_signal(int signal, void *data) {
	/* Exit cleanly, so the socket is removed. */
	struct tinywl_server *server = data;
	wl_display_terminate(server->wl_display);
	return 0;
}

static struct tinywl_toplevel *find_window(struct tinywl_server *server, uint32_t window) {
	struct tinywl_toplevel *toplevel;
	wl_list_for_each(toplevel, &server->toplevels, link) {
		if (toplevel->reported && toplevel->window == window) {
			return toplevel;
		}
	}
	return NULL;
}

static void bind_hotkey(struct tinywl_server *server, struct policy_message *message,
		bool bind) {
	for (int i = 0; i < server->num_hotkeys; i++) {
		if (server->hotkeys[i].keysym == message->keysym &&
				server->hotkeys[i].modifiers == message->modifiers) {
			if (!bind) {
				server->hotkeys[i] = server->hotkeys[--server->num_hotkeys];
			}
			return;
		}
	}
	if (!bind) {
		return;
	}
	if (server->num_hotkeys == MAX_HOTKEYS) {
		wlr_log(WLR_ERROR, "policy: too many hotkeys bound");
		return;
	}
	server->hotkeys[server->num_hotkeys].keysym = message->keysym;
	server->hotkeys[server->num_hotkeys].modifiers = message->modifiers;
	server->num_hotkeys++;
}

static void handle_policy_message(struct tinywl_server *server,
		struct policy_message *message) {
	struct tinywl_toplevel *toplevel = find_window(server, message->window);
	switch (message->method) {
	case POLICY_BIND_HOTKEY:
	case POLICY_UNBIND_HOTKEY:
		bind_hotkey(server, message, message->method == POLICY_BIND_HOTKEY);
		break;
	case POLICY_LOCK:
	case POLICY_UNLOCK:
		set_locked(server, message->method == POLICY_LOCK);
		break;
	case POLICY_FOCUS_WINDOW:
		focus_toplevel(toplevel);
		break;
	case POLICY_RAISE_WINDOW:
		if (toplevel) {
			raise_toplevel(toplevel);
		}
		break;
	case POLICY_CLOSE_WINDOW:
		if (toplevel) {
			wlr_xdg_toplevel_send_close(toplevel->xdg_toplevel);
		}
		break;
	}
}

static int handle_policy(int fd, uint32_t mask, void *data) {
	struct tinywl_server *server = data;
	if (mask & WL_EVENT_WRITABLE) {
		policy_writable(fd, mask, data);
		if (server->policy_fd < 0) {
			return 0;
		}
	}
	if (mask & WL_EVENT_READABLE) {
		/* Everything waiting, one message per packet: MSG_TRUNC gives
		 * a packet's whole length. */
		while (server->policy_fd >= 0) {
			uint8_t packet[POLICY_MAX_MESSAGE];
			ssize_t n = recv(fd, packet, sizeof(packet), MSG_DONTWAIT | MSG_TRUNC);
			if (n < 0 && (errno == EAGAIN || errno == EWOULDBLOCK)) {
				return 0;
			}
			if (n < 0 && errno == EINTR) {
				continue;
			}
			if (n <= 0) {
				if (n < 0) {
					wlr_log_errno(WLR_ERROR, "can't read the policy channel");
				}
				policy_close(server);
				return 0;
			}
			struct policy_message message;
			const char *error = (size_t)n > sizeof(packet) ? "too long" :
				policy_decode(packet, n, &message);
			if (error) {
				wlr_log(WLR_ERROR, "policy: bad message: %s", error);
			} else {
				handle_policy_message(server, &message);
			}
		}
	} else if (mask & (WL_EVENT_HANGUP | WL_EVENT_ERROR)) {
		policy_close(server);
	}
	return 0;
}

static void terminal_destroy(struct wl_listener *listener, void *data) {
	struct tinywl_server *server = wl_container_of(listener, server, terminal_destroy);
	wlr_log(WLR_INFO, "the Terminal disconnected");
	wl_list_remove(&server->terminal_destroy.link);
	server->terminal = NULL;
}

int main(int argc, char *argv[]) {
	wlr_log_init(WLR_DEBUG, NULL);
	char *startup_cmd = NULL;
	int terminal_fd = -1;
	int policy_fd = -1;
	bool test_keyboard = false;
	const char *usage = "Usage: %s [-s startup command] [-t terminal fd] [-p policy fd] [-k]\n";

	int c;
	while ((c = getopt(argc, argv, "s:t:p:kh")) != -1) {
		switch (c) {
		case 's':
			startup_cmd = optarg;
			break;
		case 't':
			terminal_fd = atoi(optarg);
			break;
		case 'p':
			policy_fd = atoi(optarg);
			break;
		case 'k':
			test_keyboard = true;
			break;
		default:
			printf(usage, argv[0]);
			return 0;
		}
	}
	if (optind < argc) {
		printf(usage, argv[0]);
		return 0;
	}

	/* With a Terminal, die with it: the policy channel closing is how,
	 * usually, but this is in case it's stuck.  It may have gone
	 * already. */
	if (policy_fd >= 0) {
		pid_t parent = getppid();
		prctl(PR_SET_PDEATHSIG, SIGTERM);
		if (getppid() != parent) {
			return 1;
		}
	}

	struct tinywl_server server = {0};
	server.policy_fd = -1;
	server.locked = true;
	server.next_window = 1;
	wl_list_init(&server.policy_queue);
	/* The Wayland display is managed by libwayland. It handles accepting
	 * clients from the Unix socket, manging Wayland globals, and so on. */
	server.wl_display = wl_display_create();
	/* The backend is a wlroots feature which abstracts the underlying input and
	 * output hardware. The autocreate option will choose the most suitable
	 * backend based on the current environment, such as opening an X11 window
	 * if an X11 server is running. */
	server.backend = wlr_backend_autocreate(wl_display_get_event_loop(server.wl_display), NULL);
	if (server.backend == NULL) {
		wlr_log(WLR_ERROR, "failed to create wlr_backend");
		return 1;
	}

	/* Autocreates a renderer, either Pixman, GLES2 or Vulkan for us. The user
	 * can also specify a renderer using the WLR_RENDERER env var.
	 * The renderer is responsible for defining the various pixel formats it
	 * supports for shared memory, this configures that for clients. */
	server.renderer = wlr_renderer_autocreate(server.backend);
	if (server.renderer == NULL) {
		wlr_log(WLR_ERROR, "failed to create wlr_renderer");
		return 1;
	}

	wlr_renderer_init_wl_display(server.renderer, server.wl_display);

	/* Autocreates an allocator for us.
	 * The allocator is the bridge between the renderer and the backend. It
	 * handles the buffer creation, allowing wlroots to render onto the
	 * screen */
	server.allocator = wlr_allocator_autocreate(server.backend,
		server.renderer);
	if (server.allocator == NULL) {
		wlr_log(WLR_ERROR, "failed to create wlr_allocator");
		return 1;
	}

	/* This creates some hands-off wlroots interfaces. The compositor is
	 * necessary for clients to allocate surfaces, the subcompositor allows to
	 * assign the role of subsurfaces to surfaces and the data device manager
	 * handles the clipboard. Each of these wlroots interfaces has room for you
	 * to dig your fingers in and play with their behavior if you want. Note that
	 * the clients cannot set the selection directly without compositor approval,
	 * see the handling of the request_set_selection event below.*/
	wlr_compositor_create(server.wl_display, 5, server.renderer);
	wlr_subcompositor_create(server.wl_display);
	wlr_data_device_manager_create(server.wl_display);

	/* Creates an output layout, which a wlroots utility for working with an
	 * arrangement of screens in a physical layout. */
	server.output_layout = wlr_output_layout_create(server.wl_display);

	/* Configure a listener to be notified when new outputs are available on the
	 * backend. */
	wl_list_init(&server.outputs);
	server.new_output.notify = server_new_output;
	wl_signal_add(&server.backend->events.new_output, &server.new_output);

	/* Create a scene graph. This is a wlroots abstraction that handles all
	 * rendering and damage tracking. All the compositor author needs to do
	 * is add things that should be rendered to the scene graph at the proper
	 * positions and then call wlr_scene_output_commit() to render a frame if
	 * necessary.
	 */
	server.scene = wlr_scene_create();
	server.scene_layout = wlr_scene_attach_output_layout(server.scene, server.output_layout);

	/* The layers, bottom to top: it starts locked. */
	for (int i = 0; i < NUM_LAYERS; i++) {
		server.layers[i] = wlr_scene_tree_create(&server.scene->tree);
	}
	wlr_scene_node_set_enabled(&server.layers[LAYER_APPS]->node, false);
	wlr_scene_node_set_enabled(&server.layers[LAYER_OVERLAY]->node, false);
	server.layout_change.notify = layout_change;
	wl_signal_add(&server.output_layout->events.change, &server.layout_change);

	/* Set up xdg-shell version 3. The xdg-shell is a Wayland protocol which is
	 * used for application windows. For more detail on shells, refer to
	 * https://drewdevault.com/2018/07/29/Wayland-shells.html.
	 */
	wl_list_init(&server.toplevels);
	server.xdg_shell = wlr_xdg_shell_create(server.wl_display, 3);
	server.new_xdg_toplevel.notify = server_new_xdg_toplevel;
	wl_signal_add(&server.xdg_shell->events.new_toplevel, &server.new_xdg_toplevel);
	server.new_xdg_popup.notify = server_new_xdg_popup;
	wl_signal_add(&server.xdg_shell->events.new_popup, &server.new_xdg_popup);

	/*
	 * Creates a cursor, which is a wlroots utility for tracking the cursor
	 * image shown on screen.
	 */
	server.cursor = wlr_cursor_create();
	wlr_cursor_attach_output_layout(server.cursor, server.output_layout);

	/* Creates an xcursor manager, another wlroots utility which loads up
	 * Xcursor themes to source cursor images from and makes sure that cursor
	 * images are available at all scale factors on the screen (necessary for
	 * HiDPI support). */
	server.cursor_mgr = wlr_xcursor_manager_create(NULL, 24);

	/*
	 * wlr_cursor *only* displays an image on screen. It does not move around
	 * when the pointer moves. However, we can attach input devices to it, and
	 * it will generate aggregate events for all of them. In these events, we
	 * can choose how we want to process them, forwarding them to clients and
	 * moving the cursor around. More detail on this process is described in
	 * https://drewdevault.com/2018/07/17/Input-handling-in-wlroots.html.
	 *
	 * And more comments are sprinkled throughout the notify functions above.
	 */
	server.cursor_mode = TINYWL_CURSOR_PASSTHROUGH;
	server.cursor_motion.notify = server_cursor_motion;
	wl_signal_add(&server.cursor->events.motion, &server.cursor_motion);
	server.cursor_motion_absolute.notify = server_cursor_motion_absolute;
	wl_signal_add(&server.cursor->events.motion_absolute,
			&server.cursor_motion_absolute);
	server.cursor_button.notify = server_cursor_button;
	wl_signal_add(&server.cursor->events.button, &server.cursor_button);
	server.cursor_axis.notify = server_cursor_axis;
	wl_signal_add(&server.cursor->events.axis, &server.cursor_axis);
	server.cursor_frame.notify = server_cursor_frame;
	wl_signal_add(&server.cursor->events.frame, &server.cursor_frame);

	/*
	 * Configures a seat, which is a single "seat" at which a user sits and
	 * operates the computer. This conceptually includes up to one keyboard,
	 * pointer, touch, and drawing tablet device. We also rig up a listener to
	 * let us know when new input devices are available on the backend.
	 */
	wl_list_init(&server.keyboards);
	server.new_input.notify = server_new_input;
	wl_signal_add(&server.backend->events.new_input, &server.new_input);
	server.seat = wlr_seat_create(server.wl_display, "seat0");
	server.request_cursor.notify = seat_request_cursor;
	wl_signal_add(&server.seat->events.request_set_cursor,
			&server.request_cursor);
	server.request_set_selection.notify = seat_request_set_selection;
	wl_signal_add(&server.seat->events.request_set_selection,
			&server.request_set_selection);

	server.decoration_manager = wlr_xdg_decoration_manager_v1_create(server.wl_display);
	server.new_decoration.notify = server_new_decoration;
	wl_signal_add(&server.decoration_manager->events.new_toplevel_decoration,
		&server.new_decoration);

	/* Add a Unix socket to the Wayland display. */
	const char *socket = "darq-0";
	if (wl_display_add_socket(server.wl_display, socket) != 0) {
		wlr_log(WLR_ERROR, "can't listen on %s", socket);
		wlr_backend_destroy(server.backend);
		return 1;
	}

	if (terminal_fd >= 0) {
		/* Neither of the Terminal's descriptors is for the startup
		 * command. */
		fcntl(terminal_fd, F_SETFD, FD_CLOEXEC);
		server.terminal = wl_client_create(server.wl_display, terminal_fd);
		if (server.terminal == NULL) {
			wlr_log(WLR_ERROR, "can't use the Terminal's connection, fd %d", terminal_fd);
			return 1;
		}
		server.terminal_destroy.notify = terminal_destroy;
		wl_client_add_destroy_listener(server.terminal, &server.terminal_destroy);
	}

	/* Start the backend. This will enumerate outputs and inputs, become the DRM
	 * master, etc */
	if (!wlr_backend_start(server.backend)) {
		wlr_backend_destroy(server.backend);
		wl_display_destroy(server.wl_display);
		return 1;
	}

	struct wl_event_loop *loop = wl_display_get_event_loop(server.wl_display);
	wl_event_loop_add_signal(loop, SIGTERM, handle_signal, &server);
	wl_event_loop_add_signal(loop, SIGINT, handle_signal, &server);
	server.repeat_timer = wl_event_loop_add_timer(loop, handle_repeat, &server);

	if (policy_fd >= 0) {
		fcntl(policy_fd, F_SETFD, FD_CLOEXEC);
		server.policy_fd = policy_fd;
		server.policy_source = wl_event_loop_add_fd(loop, policy_fd,
			WL_EVENT_READABLE, handle_policy, &server);
		struct policy_buffer buffer;
		policy_encode_ready(&buffer);
		policy_send(&server, &buffer);
	}

	if (test_keyboard) {
		wlr_keyboard_init(&server.test_keyboard, &test_keyboard_impl, "test");
		server_new_keyboard(&server, &server.test_keyboard.base);
		wlr_pointer_init(&server.test_pointer, &test_pointer_impl, "test");
		server_new_pointer(&server, &server.test_pointer.base);
		wlr_seat_set_capabilities(server.seat,
			WL_SEAT_CAPABILITY_POINTER | WL_SEAT_CAPABILITY_KEYBOARD);
		server.test_input_source = wl_event_loop_add_fd(loop, STDIN_FILENO,
			WL_EVENT_READABLE, handle_test_input, &server);
	}

	/* Set the WAYLAND_DISPLAY environment variable to our socket and run the
	 * startup command if requested. */
	setenv("WAYLAND_DISPLAY", socket, true);
	if (startup_cmd) {
		if (fork() == 0) {
			execl("/bin/sh", "/bin/sh", "-c", startup_cmd, (void *)NULL);
		}
	}
	/* Run the Wayland event loop. This does not return until you exit the
	 * compositor. Starting the backend rigged up all of the necessary event
	 * loop configuration to listen to libinput events, DRM events, generate
	 * frame events at the refresh rate, and so on. */
	wlr_log(WLR_INFO, "Running Wayland compositor on WAYLAND_DISPLAY=%s",
			socket);
	wl_display_run(server.wl_display);

	/* Once wl_display_run returns, we destroy all clients then shut down the
	 * server. */
	wl_display_destroy_clients(server.wl_display);
	wlr_scene_node_destroy(&server.scene->tree.node);
	wlr_xcursor_manager_destroy(server.cursor_mgr);
	wlr_cursor_destroy(server.cursor);
	wlr_allocator_destroy(server.allocator);
	wlr_renderer_destroy(server.renderer);
	wlr_backend_destroy(server.backend);
	wl_display_destroy(server.wl_display);
	return 0;
}

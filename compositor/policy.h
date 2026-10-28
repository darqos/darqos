// darqos
// Copyright (C) 2026 David Arnold

// The policy channel's messages (see lib/didl/compositor.didl), encoded
// and decoded by hand, in the DIPC format (see darq/dipc.py).
//
// tests/test_compositor.py checks them against the classes didlpygen
// generates, through policy-check.c.

#ifndef DARQ_POLICY_H
#define DARQ_POLICY_H

#include <stddef.h>
#include <stdint.h>

/* The longest message either side sends: a WindowAdded, with a title
 * and app_id of POLICY_MAX_STRING bytes each. */
#define POLICY_MAX_STRING 1024
#define POLICY_MAX_MESSAGE (32 + 2 * POLICY_MAX_STRING)

/* Display: sent by the compositor. */
enum policy_display_method {
	POLICY_READY = 1,
	POLICY_HOTKEY_PRESSED = 2,
	POLICY_SYSTEM_RELEASED = 3,
	POLICY_WINDOW_ADDED = 4,
	POLICY_WINDOW_REMOVED = 5,
	POLICY_WINDOW_RETITLED = 6,
	POLICY_WINDOW_FOCUSED = 7,
};

/* Policy: sent by the Terminal. */
enum policy_method {
	POLICY_BIND_HOTKEY = 1,
	POLICY_UNBIND_HOTKEY = 2,
	POLICY_LOCK = 3,
	POLICY_UNLOCK = 4,
	POLICY_FOCUS_WINDOW = 5,
	POLICY_RAISE_WINDOW = 6,
	POLICY_CLOSE_WINDOW = 7,
};

/* Modifier bits, in a hotkey's modifiers. */
enum policy_modifier {
	POLICY_SHIFT = 1,
	POLICY_ALT = 2,
	POLICY_CONTROL = 4,
	POLICY_SUPER = 8,
};

/* An encoded message: at most POLICY_MAX_MESSAGE bytes. */
struct policy_buffer {
	uint8_t data[POLICY_MAX_MESSAGE];
	size_t length;
};

/* Encode a Display message.  Strings longer than POLICY_MAX_STRING are
 * cut short, at a character's boundary, and invalid UTF-8 in them is
 * replaced with U+FFFD; a NULL string is empty. */
void policy_encode_ready(struct policy_buffer *buffer);
void policy_encode_hotkey_pressed(struct policy_buffer *buffer,
	uint32_t keysym, uint32_t modifiers);
void policy_encode_system_released(struct policy_buffer *buffer);
void policy_encode_window_added(struct policy_buffer *buffer,
	uint32_t window, const char *title, const char *app_id);
void policy_encode_window_removed(struct policy_buffer *buffer, uint32_t window);
void policy_encode_window_retitled(struct policy_buffer *buffer,
	uint32_t window, const char *title);
void policy_encode_window_focused(struct policy_buffer *buffer, uint32_t window);

/* A decoded Policy message: the fields its method has are set. */
struct policy_message {
	enum policy_method method;
	uint32_t keysym;
	uint32_t modifiers;
	uint32_t window;
};

/* Decode a Policy message.  Returns NULL, or what's wrong with it. */
const char *policy_decode(const uint8_t *data, size_t length,
	struct policy_message *message);

#endif

// darqos
// Copyright (C) 2026 David Arnold

// The policy channel's messages: see policy.h.
//
// Every message starts with the DIPC header: uint32 txid (0, since
// they're all one-way), uint16 flags (0), and uint16 method.  The
// fields follow at the offsets didlc gives them; little-endian.  A
// string field is a uint32 offset of its value from the start of the
// message, and a uint32 length: the values follow the fixed part, in
// field order, each on an 8-byte boundary, and padded to a multiple of
// 8 bytes.

#include <string.h>

#include "policy.h"

#define HEADER_LENGTH 8

static void put16(uint8_t *p, uint16_t value) {
	p[0] = value;
	p[1] = value >> 8;
}

static void put32(uint8_t *p, uint32_t value) {
	p[0] = value;
	p[1] = value >> 8;
	p[2] = value >> 16;
	p[3] = value >> 24;
}

static uint16_t get16(const uint8_t *p) {
	return p[0] | p[1] << 8;
}

static uint32_t get32(const uint8_t *p) {
	return p[0] | p[1] << 8 | p[2] << 16 | (uint32_t)p[3] << 24;
}

/* Start a message: its header, and a zeroed fixed part. */
static void begin(struct policy_buffer *buffer, uint16_t method, size_t length) {
	memset(buffer->data, 0, length);
	put32(buffer->data, 0);
	put16(buffer->data + 4, 0);
	put16(buffer->data + 6, method);
	buffer->length = length;
}

/* The length of a valid UTF-8 sequence at s, of at most n bytes, or 0. */
static size_t utf8_sequence(const uint8_t *s, size_t n) {
	size_t length;
	uint32_t c;
	if (s[0] < 0x80) {
		return 1;
	} else if (s[0] >= 0xc2 && s[0] < 0xe0) {
		length = 2;
		c = s[0] & 0x1f;
	} else if (s[0] >= 0xe0 && s[0] < 0xf0) {
		length = 3;
		c = s[0] & 0x0f;
	} else if (s[0] >= 0xf0 && s[0] < 0xf5) {
		length = 4;
		c = s[0] & 0x07;
	} else {
		return 0;
	}
	if (length > n) {
		return 0;
	}
	for (size_t i = 1; i < length; i++) {
		if ((s[i] & 0xc0) != 0x80) {
			return 0;
		}
		c = c << 6 | (s[i] & 0x3f);
	}
	/* Overlong forms, surrogates, and beyond U+10FFFF. */
	if ((length == 3 && c < 0x800) || (length == 4 && c < 0x10000) ||
			(c >= 0xd800 && c < 0xe000) || c > 0x10ffff) {
		return 0;
	}
	return length;
}

/* Append a string's value, and set its descriptor at field. */
static void put_string(struct policy_buffer *buffer, size_t field, const char *string) {
	static const uint8_t replacement[] = {0xef, 0xbf, 0xbd};
	const uint8_t *s = (const uint8_t *)(string ? string : "");
	size_t n = strlen((const char *)s);
	size_t offset = (buffer->length + 7) & ~(size_t)7;
	uint8_t *out = buffer->data + offset;
	memset(buffer->data + buffer->length, 0, offset - buffer->length);
	size_t length = 0;

	while (n > 0) {
		size_t sequence = utf8_sequence(s, n);
		const uint8_t *from = sequence ? s : replacement;
		size_t size = sequence ? sequence : sizeof(replacement);
		if (length + size > POLICY_MAX_STRING) {
			break;
		}
		memcpy(out + length, from, size);
		length += size;
		s += sequence ? sequence : 1;
		n -= sequence ? sequence : 1;
	}

	size_t padded = (length + 7) & ~(size_t)7;
	memset(out + length, 0, padded - length);
	put32(buffer->data + field, offset);
	put32(buffer->data + field + 4, length);
	buffer->length = offset + padded;
}

void policy_encode_ready(struct policy_buffer *buffer) {
	begin(buffer, POLICY_READY, 8);
}

void policy_encode_hotkey_pressed(struct policy_buffer *buffer,
		uint32_t keysym, uint32_t modifiers) {
	begin(buffer, POLICY_HOTKEY_PRESSED, 16);
	put32(buffer->data + 8, keysym);
	put32(buffer->data + 12, modifiers);
}

void policy_encode_system_released(struct policy_buffer *buffer) {
	begin(buffer, POLICY_SYSTEM_RELEASED, 8);
}

void policy_encode_window_added(struct policy_buffer *buffer,
		uint32_t window, const char *title, const char *app_id) {
	begin(buffer, POLICY_WINDOW_ADDED, 28);
	put32(buffer->data + 8, window);
	put_string(buffer, 12, title);
	put_string(buffer, 20, app_id);
}

void policy_encode_window_removed(struct policy_buffer *buffer, uint32_t window) {
	begin(buffer, POLICY_WINDOW_REMOVED, 12);
	put32(buffer->data + 8, window);
}

void policy_encode_window_retitled(struct policy_buffer *buffer,
		uint32_t window, const char *title) {
	begin(buffer, POLICY_WINDOW_RETITLED, 20);
	put32(buffer->data + 8, window);
	put_string(buffer, 12, title);
}

void policy_encode_window_focused(struct policy_buffer *buffer, uint32_t window) {
	begin(buffer, POLICY_WINDOW_FOCUSED, 12);
	put32(buffer->data + 8, window);
}

const char *policy_decode(const uint8_t *data, size_t length,
		struct policy_message *message) {
	if (length < HEADER_LENGTH) {
		return "too short for the header";
	}
	if (get32(data) != 0) {
		return "txid isn't 0";
	}
	if (get16(data + 4) != 0) {
		return "flags aren't 0";
	}

	/* Every Policy message is fixed-length. */
	memset(message, 0, sizeof(*message));
	message->method = get16(data + 6);
	size_t expected;
	switch (message->method) {
	case POLICY_BIND_HOTKEY:
	case POLICY_UNBIND_HOTKEY:
		expected = 16;
		break;
	case POLICY_LOCK:
	case POLICY_UNLOCK:
		expected = 8;
		break;
	case POLICY_FOCUS_WINDOW:
	case POLICY_RAISE_WINDOW:
	case POLICY_CLOSE_WINDOW:
		expected = 12;
		break;
	default:
		return "unknown method";
	}
	if (length != expected) {
		return "wrong length for its method";
	}

	switch (message->method) {
	case POLICY_BIND_HOTKEY:
	case POLICY_UNBIND_HOTKEY:
		message->keysym = get32(data + 8);
		message->modifiers = get32(data + 12);
		break;
	case POLICY_FOCUS_WINDOW:
	case POLICY_RAISE_WINDOW:
	case POLICY_CLOSE_WINDOW:
		message->window = get32(data + 8);
		break;
	default:
		break;
	}
	return NULL;
}

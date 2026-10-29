// darqos
// Copyright (C) 2026 David Arnold

// DIPC messages in C: see dipc.h.

#include <stdlib.h>
#include <string.h>

#include "dipc.h"

static size_t align8(size_t n) {
	return (n + 7) & ~(size_t)7;
}

const char *darq_dipc_strerror(int status) {
	switch (status) {
	case DARQ_DIPC_OK: return "OK";
	case DARQ_DIPC_TOO_SMALL: return "no room in the message's buffer";
	case DARQ_DIPC_TOO_MANY_HANDLES: return "too many handles";
	case DARQ_DIPC_INVALID_VALUE: return "a string has a NUL in it, or isn't UTF-8";
	case DARQ_DIPC_MISALIGNED: return "message not 8-byte aligned";
	case DARQ_DIPC_SHORT: return "too short for the message";
	case DARQ_DIPC_BAD_FLAGS: return "wrong flags";
	case DARQ_DIPC_BAD_METHOD: return "wrong method";
	case DARQ_DIPC_VALUE_MISALIGNED: return "value not 8-byte aligned";
	case DARQ_DIPC_VALUE_OUT_OF_RANGE: return "value out of range";
	case DARQ_DIPC_VALUE_OVERLAP: return "value overlaps another";
	case DARQ_DIPC_NOT_UTF8: return "string value is not valid UTF-8";
	case DARQ_DIPC_NUL_IN_STRING: return "string value contains a NUL";
	case DARQ_DIPC_NO_NUL_AFTER_STRING: return "string value isn't followed by a NUL";
	case DARQ_DIPC_BAD_BOOL: return "bool value not 0 or 1";
	case DARQ_DIPC_BAD_ENUM: return "value not one of its enum's members";
	case DARQ_DIPC_BAD_PORT: return "port index past the handles";
	case DARQ_DIPC_EMPTY_ELEMENTS: return "sequence elements can't be empty";
	case DARQ_DIPC_NO_MEMORY: return "out of memory";
	default: return "unknown error";
	}
}

////////////////////////////////////////////////////////////////
// UTF-8

/* The length of a valid UTF-8 sequence at s, of at most n bytes, that
 * isn't NUL; or 0. */
static size_t utf8_sequence(const uint8_t *s, size_t n) {
	size_t length;
	uint32_t c;
	if (s[0] == 0) {
		return 0;
	} else if (s[0] < 0x80) {
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

bool darq_valid_utf8(const char *value, size_t length) {
	const uint8_t *s = (const uint8_t *)value;
	while (length > 0) {
		size_t n = utf8_sequence(s, length);
		if (n == 0) {
			return false;
		}
		s += n;
		length -= n;
	}
	return true;
}

////////////////////////////////////////////////////////////////
// Building

void darq_builder_init(struct darq_builder *builder, void *data, size_t capacity) {
	builder->data = data;
	builder->capacity = capacity;
	builder->length = 0;
	builder->num_handles = 0;
}

void *darq_builder_begin(struct darq_builder *builder, size_t length,
		uint32_t txid, uint16_t flags, uint16_t method) {
	builder->length = 0;
	builder->num_handles = 0;
	if ((uintptr_t)builder->data % 8 != 0 || length > builder->capacity) {
		return NULL;
	}
	memset(builder->data, 0, length);
	struct darq_header *header = (struct darq_header *)builder->data;
	header->txid = txid;
	header->flags = flags;
	header->method = method;
	builder->length = length;
	return builder->data;
}

/* Room for a value of size bytes after the message's fixed part, or
 * what's been appended: its offset, zeroed out to a multiple of 8, or
 * 0 if there's no room. */
static size_t reserve(struct darq_builder *builder, size_t size) {
	size_t offset = align8(builder->length);
	size_t end = offset + align8(size);
	if (end > builder->capacity || offset > UINT32_MAX || size > UINT32_MAX) {
		return 0;
	}
	memset(builder->data + builder->length, 0, end - builder->length);
	builder->length = end;
	return offset;
}

int darq_set_opaque(struct darq_builder *builder, struct darq_ref *field,
		const void *value, size_t length) {
	size_t offset = reserve(builder, length);
	if (offset == 0) {
		return DARQ_DIPC_TOO_SMALL;
	}
	memcpy(builder->data + offset, value, length);
	field->offset = offset;
	field->length = length;
	return DARQ_DIPC_OK;
}

int darq_set_string(struct darq_builder *builder, struct darq_ref *field,
		const char *value, size_t length) {
	if (!darq_valid_utf8(value, length)) {
		return DARQ_DIPC_INVALID_VALUE;
	}
	/* Followed by at least one NUL, from its zeroed padding. */
	size_t offset = reserve(builder, length + 1);
	if (offset == 0) {
		return DARQ_DIPC_TOO_SMALL;
	}
	memcpy(builder->data + offset, value, length);
	field->offset = offset;
	field->length = length;
	return DARQ_DIPC_OK;
}

int darq_set_cstring(struct darq_builder *builder, struct darq_ref *field, const char *value) {
	return darq_set_string(builder, field, value ? value : "", value ? strlen(value) : 0);
}

int darq_set_string_lossy(struct darq_builder *builder, struct darq_ref *field,
		const char *value, size_t length, size_t max_length) {
	static const uint8_t replacement[] = {0xef, 0xbf, 0xbd};
	const uint8_t *s = (const uint8_t *)(value ? value : "");
	if (value == NULL) {
		length = 0;
	}

	/* At most max_length, or 3 times the input (each byte may become a
	 * replacement character), plus the NUL. */
	size_t limit = length * 3 < max_length ? length * 3 : max_length;
	size_t offset = reserve(builder, limit + 1);
	if (offset == 0) {
		return DARQ_DIPC_TOO_SMALL;
	}
	uint8_t *out = builder->data + offset;
	size_t written = 0;
	while (length > 0) {
		size_t sequence = utf8_sequence(s, length);
		const uint8_t *from = sequence ? s : replacement;
		size_t size = sequence ? sequence : sizeof(replacement);
		if (written + size > limit) {
			break;
		}
		memcpy(out + written, from, size);
		written += size;
		s += sequence ? sequence : 1;
		length -= sequence ? sequence : 1;
	}

	/* Give back what wasn't used, past the NUL's block. */
	builder->length = offset + align8(written + 1);
	field->offset = offset;
	field->length = written;
	return DARQ_DIPC_OK;
}

void *darq_set_sequence(struct darq_builder *builder, struct darq_ref *field,
		uint32_t count, size_t element_size) {
	if (element_size != 0 && count > SIZE_MAX / element_size) {
		return NULL;
	}
	size_t offset = reserve(builder, count * element_size);
	if (offset == 0) {
		return NULL;
	}
	field->offset = offset;
	field->length = count;
	return builder->data + offset;
}

int darq_set_port(struct darq_builder *builder, uint16_t *field, uint64_t handle) {
	if (handle == 0) {
		*field = DARQ_DIPC_NO_PORT;
		return DARQ_DIPC_OK;
	}
	for (size_t i = 0; i < builder->num_handles; i++) {
		if (builder->handles[i] == handle) {
			*field = i;
			return DARQ_DIPC_OK;
		}
	}
	if (builder->num_handles == DARQ_DIPC_MAX_HANDLES) {
		return DARQ_DIPC_TOO_MANY_HANDLES;
	}
	builder->handles[builder->num_handles] = handle;
	*field = builder->num_handles++;
	return DARQ_DIPC_OK;
}

int darq_build_error_reply(struct darq_builder *builder, uint32_t txid, uint16_t method,
		uint32_t code, const char *description) {
	struct darq_error_reply *reply = darq_builder_begin(builder, 20, txid, DARQ_DIPC_ERROR, method);
	if (reply == NULL) {
		return DARQ_DIPC_TOO_SMALL;
	}
	reply->code = code;
	const char *text = description ? description : "";
	return darq_set_opaque(builder, &reply->description, text, strlen(text));
}

////////////////////////////////////////////////////////////////
// Verifying

int darq_verify_begin(struct darq_verifier *verifier, const void *data, size_t length,
		size_t fixed_length, uint16_t flags, uint16_t method, size_t num_handles) {
	verifier->used = NULL;
	if ((uintptr_t)data % 8 != 0) {
		return DARQ_DIPC_MISALIGNED;
	}
	if (length < fixed_length) {
		return DARQ_DIPC_SHORT;
	}
	const struct darq_header *header = data;
	if (header->flags != flags) {
		return DARQ_DIPC_BAD_FLAGS;
	}
	if (header->method != method) {
		return DARQ_DIPC_BAD_METHOD;
	}
	verifier->data = data;
	verifier->length = length;
	verifier->start = align8(fixed_length);
	verifier->num_handles = num_handles;

	/* A bit per 8-byte block, after the fixed part. */
	size_t blocks = length > verifier->start ? (length - verifier->start + 7) / 8 : 0;
	size_t bytes = (blocks + 7) / 8;
	if (bytes <= sizeof(verifier->inline_used)) {
		verifier->used = verifier->inline_used;
	} else if ((verifier->used = malloc(bytes)) == NULL) {
		return DARQ_DIPC_NO_MEMORY;
	}
	memset(verifier->used, 0, bytes);
	return DARQ_DIPC_OK;
}

void darq_verify_end(struct darq_verifier *verifier) {
	if (verifier->used != verifier->inline_used) {
		free(verifier->used);
	}
	verifier->used = NULL;
}

/* Check a value of size bytes, and claim the blocks it's in.  Values
 * start on block boundaries, so they overlap only if they share one. */
static int claim(struct darq_verifier *verifier, uint32_t offset, size_t size) {
	if (offset % 8 != 0) {
		return DARQ_DIPC_VALUE_MISALIGNED;
	}
	if (offset < verifier->start || offset > verifier->length || size > verifier->length - offset) {
		return DARQ_DIPC_VALUE_OUT_OF_RANGE;
	}
	size_t first = (offset - verifier->start) / 8;
	size_t last = first + (size + 7) / 8;
	for (size_t block = first; block < last; block++) {
		if (verifier->used[block / 8] & (1u << block % 8)) {
			return DARQ_DIPC_VALUE_OVERLAP;
		}
	}
	for (size_t block = first; block < last; block++) {
		verifier->used[block / 8] |= 1u << block % 8;
	}
	return DARQ_DIPC_OK;
}

int darq_verify_opaque(struct darq_verifier *verifier, struct darq_ref field) {
	return claim(verifier, field.offset, field.length);
}

int darq_verify_string(struct darq_verifier *verifier, struct darq_ref field) {
	int status = claim(verifier, field.offset, (size_t)field.length + 1);
	if (status != DARQ_DIPC_OK) {
		return status;
	}
	const char *value = (const char *)verifier->data + field.offset;
	if (value[field.length] != 0) {
		return DARQ_DIPC_NO_NUL_AFTER_STRING;
	}
	if (memchr(value, 0, field.length) != NULL) {
		return DARQ_DIPC_NUL_IN_STRING;
	}
	return darq_valid_utf8(value, field.length) ? DARQ_DIPC_OK : DARQ_DIPC_NOT_UTF8;
}

int darq_verify_sequence(struct darq_verifier *verifier, struct darq_ref field, size_t element_size) {
	if (element_size == 0) {
		return DARQ_DIPC_EMPTY_ELEMENTS;
	}
	return claim(verifier, field.offset, (size_t)field.length * element_size);
}

int darq_verify_bool(const void *field) {
	return *(const uint8_t *)field <= 1 ? DARQ_DIPC_OK : DARQ_DIPC_BAD_BOOL;
}

int darq_verify_port(struct darq_verifier *verifier, uint16_t field) {
	return field == DARQ_DIPC_NO_PORT || field < verifier->num_handles ? DARQ_DIPC_OK : DARQ_DIPC_BAD_PORT;
}

int darq_verify_error_reply(const void *data, size_t length) {
	struct darq_verifier verifier;
	const struct darq_header *header = data;
	if (length < DARQ_DIPC_HEADER_LENGTH || (uintptr_t)data % 8 != 0) {
		return length < DARQ_DIPC_HEADER_LENGTH ? DARQ_DIPC_SHORT : DARQ_DIPC_MISALIGNED;
	}
	int status = darq_verify_begin(&verifier, data, length, 20, DARQ_DIPC_ERROR, header->method, 0);
	if (status == DARQ_DIPC_OK) {
		status = darq_verify_opaque(&verifier, ((const struct darq_error_reply *)data)->description);
	}
	darq_verify_end(&verifier);
	return status;
}

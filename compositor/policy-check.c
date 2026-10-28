// darqos
// Copyright (C) 2026 David Arnold

// Drives policy.c for tests/test_compositor.py, a line at a time, so
// it can check the encoding against the classes didlpygen generates.
//
// Each line of input is a command, and gets a line of output:
//
//   ready | hotkey KEYSYM MODS | released | added WINDOW TITLE APP_ID
//   | removed WINDOW | retitled WINDOW TITLE | focused WINDOW
//       Encode a Display message, and print it in hex.  Strings are
//       given in hex too, "-" for an empty one.
//   decode HEX
//       Decode a Policy message: print "METHOD KEYSYM MODIFIERS WINDOW",
//       or "error: " and what's wrong with it.

#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "policy.h"

static size_t unhex(const char *hex, uint8_t *out, size_t size) {
	size_t n = 0;
	if (strcmp(hex, "-") == 0) {
		return 0;
	}
	for (; hex[0] && hex[1] && n < size; hex += 2) {
		unsigned int byte;
		sscanf(hex, "%2x", &byte);
		out[n++] = byte;
	}
	return n;
}

/* A hex string argument, as a C string. */
static char *string_arg(const char *hex) {
	static char strings[2][4 * POLICY_MAX_STRING + 1];
	static int next;
	char *s = strings[next++ % 2];
	size_t n = unhex(hex, (uint8_t *)s, sizeof(strings[0]) - 1);
	s[n] = '\0';
	return s;
}

int main(void) {
	char line[16384];
	while (fgets(line, sizeof(line), stdin)) {
		char *args[4] = {0};
		int nargs = 0;
		for (char *word = strtok(line, " \n"); word && nargs < 4; word = strtok(NULL, " \n")) {
			args[nargs++] = word;
		}
		if (nargs == 0) {
			continue;
		}

		struct policy_buffer buffer;
		const char *command = args[0];
		if (strcmp(command, "decode") == 0) {
			static uint8_t data[sizeof(line) / 2];
			size_t length = unhex(nargs > 1 ? args[1] : "-", data, sizeof(data));
			struct policy_message message;
			const char *error = policy_decode(data, length, &message);
			if (error) {
				printf("error: %s\n", error);
			} else {
				printf("%u %u %u %u\n", message.method, message.keysym,
					message.modifiers, message.window);
			}
			fflush(stdout);
			continue;
		}

		uint32_t a = nargs > 1 ? strtoul(args[1], NULL, 0) : 0;
		uint32_t b = nargs > 2 ? strtoul(args[2], NULL, 0) : 0;
		if (strcmp(command, "ready") == 0) {
			policy_encode_ready(&buffer);
		} else if (strcmp(command, "hotkey") == 0) {
			policy_encode_hotkey_pressed(&buffer, a, b);
		} else if (strcmp(command, "released") == 0) {
			policy_encode_system_released(&buffer);
		} else if (strcmp(command, "added") == 0 && nargs == 4) {
			char *title = string_arg(args[2]);
			policy_encode_window_added(&buffer, a, title, string_arg(args[3]));
		} else if (strcmp(command, "removed") == 0) {
			policy_encode_window_removed(&buffer, a);
		} else if (strcmp(command, "retitled") == 0 && nargs == 3) {
			policy_encode_window_retitled(&buffer, a, string_arg(args[2]));
		} else if (strcmp(command, "focused") == 0) {
			policy_encode_window_focused(&buffer, a);
		} else {
			printf("error: bad command\n");
			fflush(stdout);
			continue;
		}
		for (size_t i = 0; i < buffer.length; i++) {
			printf("%02x", buffer.data[i]);
		}
		printf("\n");
		fflush(stdout);
	}
	return 0;
}

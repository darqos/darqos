#! /usr/bin/env python3

import socket
import struct


def main():
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.connect(('localhost', 11000))

    # Request port
    fmt = '!BBBxL Q'
    buf = struct.pack(fmt, 1, 8, 1, 16, 2917)
    s.send(buf)
    print("sent open_port")

    # open_port response.
    buf = s.recv(65535)
    bits = struct.unpack('!BBBxL Q?', buf)
    port = bits[4]
    result = bits[5]
    print(f'Result {result}, port {port}')

    # Send a message to myself
    fmt = '!BBBxL QQL'
    body = "hello world".encode()
    buf = struct.pack(fmt, 1, 8, 5, struct.calcsize(fmt) + len(body),
                      port, port, len(body))
    buf += body
    # FIXME: pad final string?  no?
    s.send(buf)
    print("Sent message")

    # Receive message
    fmt = '!BBBxL QQL'
    header_len = struct.calcsize(fmt)

    buf = s.recv(65535)
    hdr = buf[:header_len]
    bits = struct.unpack(fmt, hdr)
    pkt_ver = bits[0]
    pkt_hdr_len = bits[1]
    pkt_type = bits[2]
    pkt_len = bits[3]

    pkt_src = bits[4]
    pkt_dst = bits[5]
    payload_len = bits[6]
    payload = buf[header_len:]
    print(f'Received ver {pkt_ver}, hdr_len {pkt_hdr_len}, len {pkt_len}, '
          f'type {pkt_type}, src {pkt_src}, dst {pkt_dst}, '
          f'body_len {payload_len} -- [{payload}]')

    s.close()


if __name__ == "__main__":
    main()

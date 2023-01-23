#! /usr/bin/env python3

import socket
import struct


def main():
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.connect(('localhost', 11000))

    # Request port
    fmt = '!BBBxL LxxxxQ'
    buf = struct.pack(fmt, 1, 8, 1, 24, 1, 2917)
    s.sendall(buf)
    print("sent open_port")

    # open_port response.
    buf = s.recv(65535)
    bits = struct.unpack('!BBBxL LBxxxQ', buf[:24])
    request_id = bits[4]
    result = bits[5]
    port = bits[6]

    print(f'Request {request_id} result {result}, port {port}')

    # Send a message to myself
    fmt = '!BBBxL QQLxxxx'
    body = "hello world".encode()
    buf = struct.pack(fmt, 1, 8, 5, struct.calcsize(fmt) + len(body), # FIXME: padding
                      port, port, len(body))
    buf += body
    # FIXME: pad final string?
    s.sendall(buf)
    print("Sent message")

    # Receive message
    fmt = '!BBBxL QQLxxxx'
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

    # Close port.
    fmt = '!BBBxL LxxxxQ'
    buf = struct.pack(fmt, 1, 8, 3, 24, 2, 2917)
    s.sendall(buf)
    print("sent close_poer")

    # close_port response.
    buf = s.recv(65535)
    bits = struct.unpack('!BBBxL LxxxxQ', buf[:24])
    request_id = bits[4]
    port = bits[5]
    assert port == 2917
    print("received port_closed")

    s.close()


if __name__ == "__main__":
    main()

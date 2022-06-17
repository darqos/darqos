#! /usr/bin/env python3

import socket
import struct


def main():
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.connect(('localhost', 11000))

    # Login request
    fmt = '!LBBxxQ'
    buf = struct.pack(fmt, 16, 1, 1, 11000)
    s.send(buf)
    print("sent login")

    # Login response
    buf = s.recv(65535)
    bits = struct.unpack('!LBBxxQ', buf)
    port = bits[3]
    print(f'Assigned port number is {port}')

    # Send a message to myself
    fmt = '!LBBxxQQL'
    body = "hello world".encode()
    buf = struct.pack(fmt, struct.calcsize(fmt) + len(body), 1, 7,
                      port, port, len(body))
    buf += body
    s.send(buf)
    print("Sent message")

    # Receive message
    fmt = '!LBBxxQQL'
    header_len = struct.calcsize(fmt)

    buf = s.recv(65535)
    bits = struct.unpack(fmt, buf[:header_len])
    pkt_len = bits[0]
    pkt_ver = bits[1]
    pkt_type = bits[2]
    pkt_src = bits[3]
    pkt_dst = bits[4]
    payload_len = bits[5]
    payload = buf[header_len:]
    print(f'Received len {pkt_len}, ver {pkt_ver}, type {pkt_type}, src {pkt_src}, dst {pkt_dst}, body_len {payload_len} -- [{payload}]')

    s.close()


if __name__ == "__main__":
    main()

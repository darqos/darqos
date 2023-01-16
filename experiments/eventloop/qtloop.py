#! /usr/bin/env python

import socket
import sys

from PyQt5.QtWidgets import QApplication, QWidget, QTextEdit, QPushButton, QVBoxLayout

import darq


class Server(QWidget):
    def __init__(self, *args):
        super().__init__(*args)

        self.text = QTextEdit(self)
        self.text.resize(500, 300)
        self.show()
        return

    def ui(self):
        self.socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.socket.setsockopt(socket.SO_REUSEADDR, 1)
        self.socket.bind(("127.0.0.1", 6789))
        self.socket.listen(10)

    def run(self, app):
        return app.exec()


class Client(QWidget, darq.SocketListener):
    def __init__(self, *args):
        super().__init__(*args)

        self.socket = None

        vbox = QVBoxLayout(self)

        self.button_connect = QPushButton("Connect", self)
        self.button_connect.clicked.connect(self.on_connect)
        vbox.addWidget(self.button_connect)

        self.button_send_a = QPushButton("Send A", self)
        self.button_send_a.clicked.connect(self.on_send_a)
        self.button_send_a.setEnabled(False)
        vbox.addWidget(self.button_send_a)

        self.button_send_b = QPushButton("Send B", self)
        self.button_send_b.clicked.connect(self.on_send_b)
        self.button_send_b.setEnabled((False))
        vbox.addWidget(self.button_send_b)

        self.button_disconnect = QPushButton("Disconnect", self)
        self.button_disconnect.clicked.connect(self.on_disconnect)
        self.button_disconnect.setEnabled(False)
        vbox.addWidget(self.button_disconnect)

        self.show()
        return

    def on_readable(self, sock: socket.socket):
        print(f"Socket [{sock}] readable")

        buffer = self.socket.recv(65536)
        if len(buffer) == 0:
            print("Error")
            self.button_connect.setEnabled(True)
            self.button_disconnect.setEnabled(False)
            self.button_send_a.setEnabled(False)
            self.button_send_b.setEnabled(False)

            darq.loop().cancel_socket(self.socket)
            self.socket.close()
            return

        # More than zero bytes read.
        print(f"Get data: {buffer}")
        return

    def on_writeable(self, sock: socket.socket):

        # Writeable means connected.
        self.button_connect.setEnabled(False)
        self.button_disconnect.setEnabled(True)
        self.button_send_a.setEnabled(True)
        self.button_send_b.setEnabled(True)

        # Ignore this for now
        return
        print(f"Socket [{sock}] writeable")


    def on_connect(self):
        print("on_connect")

        self.socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.socket.setblocking(False)
        darq.loop().add_socket(self.socket, self)

        try:
            self.socket.connect(("127.0.0.1", 6789))
        except BlockingIOError:
            pass
        return

    def on_send_a(self):
        print("on_send_a")
        self.socket.sendall(b"AAAA\n")

    def on_send_b(self):
        print("on_send_b")
        self.socket.sendall(b"BBBB\n")

    def on_disconnect(self):
        print("on_disconnect")
        darq.loop().cancel_socket(self.socket)
        self.socket.close()

        self.button_connect.setEnabled(True)
        self.button_disconnect.setEnabled(False)
        self.button_send_a.setEnabled(False)
        self.button_send_b.setEnabled(False)
        return

    def ui(self):
        self.socket.connect(("127.0.0.1", 6789))


    def run(self, app):
        return app.exec()


def usage():
    print(f"Usage: {sys.argv[0]} c|s")


if __name__ == "__main__":
    if len(sys.argv) != 2:
        usage()
        sys.exit(1)

    if sys.argv[1] == "s":
        app = QApplication(sys.argv)
        darq.init(darq.QtEventLoop())

        s = Server()
        result = s.run(app)
        sys.exit(result)

    elif sys.argv[1] == "c":
        app = QApplication(sys.argv)
        darq.init(darq.QtEventLoop())
        c = Client()
        result = c.run(app)
        sys.exit(result)

    else:
        usage()
        sys.exit(1)

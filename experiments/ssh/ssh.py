import logging
import sys
import asyncio
import PyQt6.QtCore as QtCore
from PyQt6.QtWidgets import QApplication, QMainWindow, QPlainTextEdit, QVBoxLayout, QWidget
from PyQt6.QtGui import QFont, QTextCursor
import qasync
import asyncssh
import asyncssh.logging
import pyte

class WaylandSSHClient(QMainWindow):
    def __init__(self, host, username, password=None):
        super().__init__()
        self.host = host
        self.username = username
        self.password = password
        
        # Terminal dimensions (standard 80x24)
        self.cols = 80
        self.rows = 24

        # Initialize Pyte terminal emulator state
        self.screen = pyte.Screen(self.cols, self.rows)
        self.stream = pyte.Stream(self.screen)

        self.init_ui()
        
        # Reference to the active SSH channel session
        self.session = None

    def init_ui(self):
        self.setWindowTitle(f"SSH Client - {self.username}@{self.host}")
        self.resize(700, 450)

        # Main Text Widget
        self.text_area = QPlainTextEdit(self)
        self.text_area.setReadOnly(True)
        
        # Turn off automatic PyQt text line wrapping! 
        # This stops the widget from squeezing the grid into a narrow 20-character column.
        self.text_area.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        
        # Completely strip internal margins and paddings
        # This aligns the text boundary perfectly with the actual widget frame.
        self.text_area.setViewportMargins(0, 0, 0, 0)
        document = self.text_area.document()
        document.setDocumentMargin(0)

        # Use a strict Monospace font so character positions match Pyte perfectly
        font = QFont("monospace", 11)
        font.setStyleHint(QFont.StyleHint.Monospace)
        self.text_area.setFont(font)
        
        # Calculate the exact pixel width and height of a single character cell
        font_metrics = self.text_area.fontMetrics()
        self.cell_width = font_metrics.horizontalAdvance("A")
        self.cell_height = font_metrics.height()
        
        # Dark theme styling
        self.text_area.setStyleSheet("""
            QPlainTextEdit {
                background-color: #1e1e1e;
                color: #ffffff;
                border: none;
            }
        """)

        # Catch user keystrokes on the widget
        self.text_area.keyPressEvent = self.handle_key_press

        # Layout setup
        layout = QVBoxLayout()
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.text_area)
        
        container = QWidget()
        container.setLayout(layout)
        self.setCentralWidget(container)

    def render_screen(self):
        """Converts Pyte's internal screen buffer into PyQt plain text."""
        # Join rows with newlines to form the screen layout
        screen_text = "\n".join(self.screen.display)
        
        # Prevent the user's cursor position from jumping while updating text
        cursor = self.text_area.textCursor()
        scroll_pos = self.text_area.verticalScrollBar().value()
        
        self.text_area.setPlainText(screen_text)
        
        # Optional: Sync PyQt visual cursor position with Pyte's virtual cursor
        cursor.setPosition(0)
        cursor.movePosition(QTextCursor.MoveOperation.Down, QTextCursor.MoveMode.MoveAnchor, self.screen.cursor.y)
        cursor.movePosition(QTextCursor.MoveOperation.Right, QTextCursor.MoveMode.MoveAnchor, self.screen.cursor.x)
        self.text_area.setTextCursor(cursor)
        self.text_area.verticalScrollBar().setValue(scroll_pos)

    async def connect_ssh(self):
        """Asynchronously establishes the SSH connection and runs the terminal read loop."""
        try:
            # Bypass known_hosts verification for local prototyping 
            # (In production, manage host keys properly!)
            conn = await asyncssh.connect(
                self.host, 
                username=self.username, 
                password=self.password, 
                known_hosts=None,
                public_key_auth=False,
                password_auth=True
            )
                
            # Request a pseudo-terminal (pty) to mimic a genuine terminal interface
            async with conn:
                async with conn.create_process(term_type="xterm", term_size=(self.cols, self.rows)) as process:
                    self.ssh_process = process
                    self.session = process.stdin

                    while not process.stdout.at_eof():
                        data = await process.stdout.read(1024)
                        if data:
                            self.stream.feed(data)
                            self.render_screen()

        except Exception as e:
            self.text_area.setPlainText(f"\n[SSH Connection Error]: {str(e)}")

    def resizeEvent(self, event):
        """Intercepts local window adjustments, re-calculates text grids,

        and updates both local pyte and remote SSH terminal structures.
        """
        # Call the default QMainWindow resize handler first
        super().resizeEvent(event)

        # Ensure our cell measurements have been initialized
        if not hasattr(self, 'cell_width') or self.cell_width == 0:
            return

        # Use the actual target size provided by the event object!
        # event.size() gives us the exact window dimensions PyQt is animating/resizing to.
        target_width = event.size().width()
        target_height = event.size().height()

        # Deduct some padding for window edges and layout margins
        usable_width = target_width - 20 
        usable_height = target_height - 20

        # Calculate columns and rows based on the target window size
        new_cols = usable_width // self.cell_width
        new_rows = usable_height // self.cell_height

        # Sanity check: Ensure we don't fall back to tiny grids on initial small frames
        # Standards typically drop to 80x24 as a baseline default
        new_cols = max(80, new_cols)
        new_rows = max(24, new_rows)

        # Only update if the dimensions have actually shifted
        if new_cols != self.cols or new_rows != self.rows:
            self.cols = new_cols
            self.rows = new_rows

            # 1. Resize the local Pyte terminal state machine
            self.screen.resize(self.rows, self.cols)

            # 2. Asynchronously broadcast the window change signal over SSH
            # Trigger the resize operation natively directly on the saved process object
            if hasattr(self, 'ssh_process') and self.ssh_process:
                try:
                    self.ssh_process.change_terminal_size(self.cols, self.rows)
                except Exception as resize_err:
                    print(f"Failed to transmit window geometry upstream: {resize_err}")

            # Instantly re-render the viewport with the updated geometry
            self.render_screen()
            # Instantly re-render the viewport with the updated geometry
            self.render_screen()

    def handle_key_press(self, event):
        """Captures local GUI keystrokes and routes them over the SSH tunnel 

        with full support for Ctrl, Alt, and navigation modifiers.
        """
        if not self.session:
            return

        text = event.text()
        key = event.key()
        modifiers = event.modifiers()

        # Extract active modifiers
        is_ctrl = bool(modifiers & QtCore.Qt.KeyboardModifier.ControlModifier)
        is_alt = bool(modifiers & QtCore.Qt.KeyboardModifier.AltModifier)

        data_to_send = ""

        # 1. Handle Control Modifier first (Emacs line-editing hotkeys)
        if is_ctrl:
            # Check if it's a standard alphabetical key (A-Z)
            if QtCore.Qt.Key.Key_A <= key <= QtCore.Qt.Key.Key_Z:
                # Math shortcut: 'A' is key code 65. Subtracting 64 gives ASCII 1 (\x01).
                # This automatically maps Ctrl+A to \x01, Ctrl+E to \x05, Ctrl+K to \x0b, etc.
                data_to_send = chr(key - 64)
            # Handle non-alpha Ctrl combinations common in terminal apps
            elif key == QtCore.Qt.Key.Key_Space:
                data_to_send = "\x00"  # Ctrl+Space (Set mark in Emacs)
            elif key == QtCore.Qt.Key.Key_Backslash:
                data_to_send = "\x1c"  # Ctrl+\ (SIGQUIT)
            elif key == QtCore.Qt.Key.Key_BracketLeft:
                data_to_send = "\x1b"  # Ctrl+[ (Equivalent to Escape)

        # 2. Handle Navigation and Standard Control Keys
        elif key == QtCore.Qt.Key.Key_Return or key == QtCore.Qt.Key.Key_Enter:
            data_to_send = "\r"
        elif key == QtCore.Qt.Key.Key_Backspace:
            data_to_send = "\x7f"
        elif key == QtCore.Qt.Key.Key_Escape:
            data_to_send = "\x1b"
        elif key == QtCore.Qt.Key.Key_Tab:
            data_to_send = "\t"
            
        # Arrow Keys (Standard xterm escape sequences)
        elif key == QtCore.Qt.Key.Key_Up:
            data_to_send = "\x1b[A"
        elif key == QtCore.Qt.Key.Key_Down:
            data_to_send = "\x1b[B"
        elif key == QtCore.Qt.Key.Key_Right:
            data_to_send = "\x1b[C"
        elif key == QtCore.Qt.Key.Key_Left:
            data_to_send = "\x1b[D"
            
        # 3. Fallback to normal text input if no modifiers applied
        else:
            data_to_send = text

        # 4. Handle Alt/Meta Modifier wrapper
        if data_to_send and is_alt:
            # Prepend the Escape character to turn 'x' into '\x1bx' (M-x in Emacs)
            data_to_send = "\x1b" + data_to_send

        # Transmit the byte data across the SSH session
        if data_to_send:
            self.session.write(data_to_send)


async def main(app):
    # Replace these with your target environment details
    HOST = 'localhost'
    USERNAME = 'd'
    PASSWORD = 'swi2_!ER'

    asyncssh.set_log_level(logging.DEBUG)
    asyncssh.set_debug_level(2)
    
    # Create an async event that signals when the PyQt application is closing
    app_close_event = asyncio.Event()
    app.aboutToQuit.connect(app_close_event.set)

    # Initialize and show your client window
    client = WaylandSSHClient(host=HOST, username=USERNAME, password=PASSWORD)
    client.show()

    # Schedule the background SSH connection task concurrently
    asyncio.create_task(client.connect_ssh())

    # Safely yield control back to the event loop until the user closes the app
    await app_close_event.wait()

if __name__ == "__main__":
    # Initialize the standard Qt Application first
    app = QApplication(sys.argv)
    
    # Run the main async block using qasync's QEventLoop as the factory handler
    asyncio.run(main(app), loop_factory=qasync.QEventLoop)

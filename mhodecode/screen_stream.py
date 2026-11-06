"""The live picture and the touch of the scope's screen, over the sockets of the instrument's own web page.

/ws/display sends H.264 (one NAL unit per binary message, 1024x600 at about 60 frames per second, measured)
and /ws/control takes the mouse as JSON. Both are read from the page control.html served by the instrument.
A second client of these sockets drops the first one, so this and the web page push each other out.
"""
from __future__ import annotations

import json
import threading
import time

import av
from PyQt5 import QtCore, QtGui, QtNetwork, QtWebSockets

SILENCE_LIMIT_S = 2.0  # as the web page: no data for this long means the stream has stopped
WATCH_INTERVAL_MS = 500
MOUSE_DOWN, MOUSE_MOVE, MOUSE_UP = "onmousedown", "onmousemove", "onmouseup"


class _Connection(QtCore.QObject):
    """Lives in the stream's thread, like its sockets."""

    def __init__(self, stream: "ScreenStream"):
        super().__init__()
        self.stream = stream
        self.codec = None
        self.last_data = 0.0
        self.display = QtWebSockets.QWebSocket(parent=self)
        self.display.connected.connect(self._display_connected)
        self.display.binaryMessageReceived.connect(self._received)
        self.control = QtWebSockets.QWebSocket(parent=self)
        self.watch = QtCore.QTimer(self)
        self.watch.setInterval(WATCH_INTERVAL_MS)
        self.watch.timeout.connect(self._check)
        self.watch.start()
        self._open()

    def _open(self):
        self.codec = av.CodecContext.create("h264", "r")  # a new connection starts with new parameters
        self.last_data = time.monotonic()
        self.display.abort()
        self.display.open(QtCore.QUrl(f"ws://{self.stream.host}/ws/display"))
        if self.control.state() != QtNetwork.QAbstractSocket.ConnectedState:
            self.control.abort()
            self.control.open(QtCore.QUrl(f"ws://{self.stream.host}/ws/control"))

    def _display_connected(self):
        self.display.sendTextMessage(json.dumps({"type": "SET_ENCODER", "size": None, "rate": None}))

    def _received(self, data):
        self.last_data = time.monotonic()
        last = None
        try:
            for packet in self.codec.parse(bytes(data)):
                for frame in self.codec.decode(packet):
                    last = frame
        except av.FFmpegError:
            return  # a damaged unit: the picture recovers with the next key frame
        self.stream.deliver(last)

    def _check(self):
        if time.monotonic() - self.last_data > SILENCE_LIMIT_S:
            self.stream.report_silence()
            self._open()

    @QtCore.pyqtSlot(str)
    def send_control(self, text):
        if self.control.state() == QtNetwork.QAbstractSocket.ConnectedState:
            self.control.sendTextMessage(text)

    def close(self):
        self.watch.stop()
        self.display.close()
        self.control.close()


class ScreenStream(QtCore.QThread):
    """Decodes every frame, since each builds on the one before, and offers the owner only the newest:
    a busy window skips pictures instead of falling behind."""
    frame_ready = QtCore.pyqtSignal()
    lost = QtCore.pyqtSignal(str)
    _control = QtCore.pyqtSignal(str)

    def __init__(self, host: str, parent=None):
        super().__init__(parent)
        self.host = host
        self.lock = threading.Lock()
        self.latest: QtGui.QImage | None = None
        self.silent = False

    def run(self):
        connection = _Connection(self)
        self._control.connect(connection.send_control, QtCore.Qt.QueuedConnection)
        self.exec_()
        connection.close()
        connection.deleteLater()

    def deliver(self, frame) -> None:
        """frame: the newest decoded one, or None."""
        if frame is None:
            return
        self.silent = False
        with self.lock:
            if self.latest is not None:
                return
        pixels = frame.to_ndarray(format="rgb24")
        image = QtGui.QImage(pixels.tobytes(), frame.width, frame.height, frame.width * 3, QtGui.QImage.Format_RGB888).copy()
        with self.lock:
            self.latest = image
        self.frame_ready.emit()

    def take(self) -> QtGui.QImage | None:
        with self.lock:
            image, self.latest = self.latest, None
        return image

    def report_silence(self) -> None:
        """A stopped scope with a still screen may also send nothing, so this is said only once per silence."""
        if not self.silent:
            self.silent = True
            self.lost.emit(self.host)

    def touch(self, kind: str, x: int, y: int) -> None:
        """x, y: pixels of the scope's screen."""
        self._control.emit(json.dumps({"type": kind, "downDelta": None, "clientX": x, "clientY": y}))

    def stop(self) -> None:
        self.quit()
        self.wait()

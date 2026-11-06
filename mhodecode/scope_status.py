"""One line with what the scope is doing and how it is set, in sight on every tab."""
from __future__ import annotations

from dataclasses import dataclass, fields

from PyQt5 import QtCore, QtWidgets


@dataclass(frozen=True)
class ScopeStatus:
    """The texts of the line, in its order. An empty one is left out, as a value not read yet or a holdoff not in use.
    What does not fit a narrow window is cut off at the end, so the holdoff comes early."""
    state: str = ""
    holdoff: str = ""
    channels: str = ""
    time_base: str = ""
    rate: str = ""
    depth: str = ""
    trigger: str = ""


class StatusLine(QtWidgets.QWidget):
    """Asks for no width of its own: a tool bar would otherwise move a widget that does not fit behind its overflow
    button, and with it everything this line tells."""

    def sizeHint(self):
        return QtCore.QSize(0, super().sizeHint().height())

    def minimumSizeHint(self):
        return QtCore.QSize(0, super().minimumSizeHint().height())


class ScopeStatusBar(QtWidgets.QToolBar):
    def __init__(self):
        super().__init__()
        self.setObjectName("scope_status")
        self.setMovable(False)
        self.setAllowedAreas(QtCore.Qt.TopToolBarArea)
        self.toggleViewAction().setVisible(False)
        line = StatusLine()
        line.setSizePolicy(QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Preferred)
        layout = QtWidgets.QHBoxLayout(line)
        layout.setContentsMargins(4, 0, 4, 0)
        self.labels: dict[str, QtWidgets.QLabel] = {}
        self.separators: dict[str, QtWidgets.QFrame] = {}
        for position, field in enumerate(fields(ScopeStatus)):
            if position:
                separator = QtWidgets.QFrame()
                separator.setFrameShape(QtWidgets.QFrame.VLine)
                separator.setFrameShadow(QtWidgets.QFrame.Sunken)
                self.separators[field.name] = separator
                layout.addWidget(separator)
            label = QtWidgets.QLabel()
            self.labels[field.name] = label
            layout.addWidget(label)
        layout.addStretch(1)
        self.addWidget(line)
        self.show_status(ScopeStatus())

    def show_status(self, status: ScopeStatus) -> None:
        for name, label in self.labels.items():
            text = getattr(status, name)
            label.setText(text)
            label.setVisible(bool(text))
            if name in self.separators:
                self.separators[name].setVisible(bool(text))

    def set_notice_color(self, color: str) -> None:
        """The holdoff stands out: it makes the scope skip signal that would otherwise trigger it."""
        self.labels["holdoff"].setStyleSheet(f"color: {color}; font-weight: bold;")

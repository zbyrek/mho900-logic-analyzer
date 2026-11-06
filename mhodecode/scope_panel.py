"""The tab that works like the instrument's web page: live screen, parameters read back, changes sent at once."""
from __future__ import annotations

import re
import threading
import time
from collections import deque

from PyQt5 import QtCore, QtGui, QtWidgets

from . import abort, generator, trigger
from .i18n import tr
from .scope import DIGITAL_PODS, ScopeError, format_si, vertical_scales
from .screen_stream import MOUSE_DOWN, MOUSE_MOVE, MOUSE_UP, ScreenStream

ANALOG_CHANNELS = (1, 2, 3, 4)
CONTROLS_MIN_WIDTH = 300  # the tab must not widen the window while another tab is showing
CONTROLS_WIDTH = 680
SCREEN_WIDTH = 760
# A query takes 25 ms on this instrument (measured), so one pass reads a few parameters by turns.
QUERIES_PER_PASS = 8
PASS_PAUSE_S = 0.2
IDLE_POLL_S = 0.1
RETRY_AFTER_LOSS_S = 2.0
# A value read just after a change can still be the old one, so it is not shown for this long.
HOLD_AFTER_CHANGE_S = 1.2
DEPTHS = (("Auto", "AUTO"), ("1 k", "1000"), ("10 k", "10000"), ("100 k", "100000"), ("1 M", "1000000"), ("10 M", "10000000"),
          ("25 M", "25000000"), ("50 M", "50000000"), ("100 M", "100000000"))
ACQUIRE_TYPES = (("Normal", "NORMal"), ("Peak", "PEAK"), ("Average", "AVERages"), ("High resolution", "HRESolution"))
COUPLINGS = (("DC", "DC"), ("AC", "AC"), ("GND", "GND"))
BANDWIDTHS = (("Full", "OFF"), ("20 MHz", "20M"), ("250 MHz", "250M"))
# What the Programming Guide lists as settable, beyond the ones on the list above.
OTHER_DEPTHS = (125e6, 200e6, 250e6, 500e6)
AUTO_DEPTH = "AUTO"
AUTO_TEXT = "Auto"
PROBE_RATIOS = (0.001, 0.002, 0.005, 0.01, 0.02, 0.05, 0.1, 0.2, 0.5, 1, 2, 5, 10, 15, 20, 50, 100, 150, 200, 500, 1000, 1500,
                2000, 5000, 10000, 15000, 20000, 50000)
PROBES = tuple((f"{ratio:g}×", f"{ratio:g}") for ratio in PROBE_RATIOS)
DEFAULT_PROBE = 1.0
OFFSET_STEP_DIVISIONS = 0.1  # one click of the time offset's arrows
TIME_OFFSET_DECIMALS = 12
TIME_OFFSET_DEFAULT_STEP = 1e-4
SI_FACTORS = {"": 1.0, "k": 1e3, "m": 1e-3, "u": 1e-6, "µ": 1e-6, "μ": 1e-6, "n": 1e-9, "p": 1e-12}
SECONDS_TEXT = re.compile(r"\s*([+-]?(?:\d+[.,]?\d*|[.,]\d+)(?:[eE][+-]?\d+)?)\s*([kmuµμnp]?)s?\s*")
SECONDS_PARTIAL = re.compile(r"[\s\d.,eE+kmuµμnps-]*")
STATE_NAMES = {"TD": "Triggered", "WAIT": "Waiting for trigger", "RUN": "Running", "AUTO": "Auto", "STOP": "Stopped"}
STATE_QUERY = ":TRIGger:STATus?"
RATE_QUERY = ":ACQuire:SRATe?"
DEPTH_QUERY = ":ACQuire:MDEPth?"
TIME_QUERY = ":TIMebase:MAIN:SCALe?"
MODE_QUERY = ":TRIGger:MODE?"


def format_volts(value: float) -> str:
    return f"{value * 1000:g} mV" if abs(value) < 1 else f"{value:g} V"


def format_points(points: float) -> str:
    for factor, prefix in ((1e6, " M"), (1e3, " k")):
        if points >= factor:
            return f"{points / factor:g}{prefix}"
    return f"{points:g}"


def parse_seconds(text: str) -> float:
    """"1,5 ms", "2us", "-3 ns" or "0.1", which is in seconds. Raises ValueError on anything else."""
    found = SECONDS_TEXT.fullmatch(text)
    if found is None:
        raise ValueError(text)
    return float(found.group(1).replace(",", ".")) * SI_FACTORS[found.group(2)]


class SecondsBox(QtWidgets.QDoubleSpinBox):
    """A time shown with an SI prefix. It also takes one when typed."""

    def __init__(self, limit):
        super().__init__()
        self.setDecimals(TIME_OFFSET_DECIMALS)
        self.setRange(-limit, limit)

    def textFromValue(self, value):
        return format_si(value, "s")

    def valueFromText(self, text):
        try:
            return parse_seconds(text)
        except ValueError:
            return self.value()

    def validate(self, text, position):
        try:
            state = QtGui.QValidator.Acceptable if self.minimum() <= parse_seconds(text) <= self.maximum() else QtGui.QValidator.Intermediate
        except ValueError:
            state = QtGui.QValidator.Intermediate if SECONDS_PARTIAL.fullmatch(text) else QtGui.QValidator.Invalid
        return state, text, position


class LiveLink(QtCore.QThread):
    """Sends what the tab queues and keeps reading the instrument, one exchange at a time.

    Commands go out before anything else, also from the thread of a job about to start (drain),
    so a capture always runs on the settings that were on screen when it was started.
    """
    value = QtCore.pyqtSignal(str, str)
    refused = QtCore.pyqtSignal(str, str)
    lost = QtCore.pyqtSignal(str)

    def __init__(self, scope, parent=None):
        super().__init__(parent)
        self.scope = scope
        self.commands = deque()
        self.urgent = deque()
        self.watched: list[str] = []
        self.position = 0
        self.pending_full_pass = 0
        self.active = False  # the tab is on screen
        self.busy = False  # a job has the link
        self.running = True
        self.drain_lock = threading.Lock()

    def send(self, text: str, then_read: str | None = None) -> None:
        self.commands.append(text)
        if then_read:
            self.urgent.append(then_read)

    def watch(self, queries: list[str]) -> None:
        self.watched = queries
        self.position = 0

    def read_all_once(self) -> None:
        """One pass over everything even with the tab hidden, for a fresh connection or after a job."""
        self.pending_full_pass = len(self.watched)

    def drain(self) -> None:
        """Send the queued commands now, and report any the instrument refused."""
        with self.drain_lock:
            sent = []
            while self.commands:
                sent.append(self.commands.popleft())
                self.scope.write(sent[-1])
            if sent:
                for error in self.scope.errors():
                    self.refused.emit(sent[-1], error)

    def stop(self) -> None:
        self.running = False
        self.wait()

    def _next_queries(self) -> list[str]:
        batch = []
        while self.urgent and len(batch) < QUERIES_PER_PASS:
            batch.append(self.urgent.popleft())
        if self.watched:
            while len(batch) < QUERIES_PER_PASS and (self.active or self.pending_full_pass):
                batch.append(self.watched[self.position % len(self.watched)])
                self.position += 1
                self.pending_full_pass = max(0, self.pending_full_pass - 1)
        return batch

    def _pass(self) -> None:
        self.drain()
        for query in self._next_queries():
            if self.busy or not self.running:
                return
            self.value.emit(query, self.scope.query(query))

    def run(self):
        reported = False
        while self.running:
            if self.busy:
                time.sleep(IDLE_POLL_S)
                continue
            try:
                self._pass()
                reported = False
                idle = not self.active and not self.pending_full_pass and not self.urgent and not self.commands
                time.sleep(IDLE_POLL_S if idle else PASS_PAUSE_S)
            except abort.OperationAborted:
                # A job's abort also stops an exchange of this thread; the job reconnects.
                time.sleep(IDLE_POLL_S)
            except ScopeError as exc:
                if not reported and not self.busy:
                    self.lost.emit(str(exc))
                    reported = True
                time.sleep(RETRY_AFTER_LOSS_S)


class ScreenView(QtWidgets.QWidget):
    """The scope's screen scaled to the available space. Its left mouse button works as a finger on that screen."""
    touched = QtCore.pyqtSignal(str, int, int)  # kind, pixels of the scope's screen

    def __init__(self):
        super().__init__()
        self.setMinimumSize(320, 200)
        self.setSizePolicy(QtWidgets.QSizePolicy.Ignored, QtWidgets.QSizePolicy.Ignored)
        self.image: QtGui.QImage | None = None
        self.pressed = False

    def show_image(self, image: QtGui.QImage) -> None:
        self.image = image
        self.update()

    def _target(self) -> QtCore.QRect:
        size = self.image.size().scaled(self.size(), QtCore.Qt.KeepAspectRatio)
        return QtCore.QRect(QtCore.QPoint((self.width() - size.width()) // 2, (self.height() - size.height()) // 2), size)

    def paintEvent(self, _event):
        if self.image is not None:
            painter = QtGui.QPainter(self)
            painter.setRenderHint(QtGui.QPainter.SmoothPixmapTransform)
            painter.drawImage(self._target(), self.image)

    def _screen_point(self, position) -> tuple[int, int]:
        """A position outside the picture counts as its nearest edge, as a finger cannot leave the screen."""
        target = self._target()
        x = (position.x() - target.x()) * self.image.width() // max(1, target.width())
        y = (position.y() - target.y()) * self.image.height() // max(1, target.height())
        return min(max(x, 0), self.image.width() - 1), min(max(y, 0), self.image.height() - 1)

    def mousePressEvent(self, event):
        if event.button() != QtCore.Qt.LeftButton or self.image is None:
            return
        if self._target().contains(event.pos()):
            self.pressed = True
            self.touched.emit(MOUSE_DOWN, *self._screen_point(event.pos()))

    def mouseMoveEvent(self, event):
        if self.pressed:
            self.touched.emit(MOUSE_MOVE, *self._screen_point(event.pos()))

    def mouseReleaseEvent(self, event):
        if self.pressed and event.button() == QtCore.Qt.LeftButton:
            self.pressed = False
            self.touched.emit(MOUSE_UP, *self._screen_point(event.pos()))


class Setting:
    """One parameter: its widget, how to read it and how to set it. Subclasses supply
    apply_reply (show a reply), current (the widget's value) and assign (put a value in the widget)."""

    def __init__(self, panel, read: str, widget, send):
        self.panel = panel
        self.read = read
        self.widget = widget
        self.send = send  # value of the widget -> SCPI command
        self.hold_until = 0.0

    def _editing(self) -> bool:
        return self.widget.hasFocus() and isinstance(self.widget, (QtWidgets.QAbstractSpinBox, QtWidgets.QLineEdit))

    def show(self, reply: str) -> None:
        if time.monotonic() < self.hold_until or self._editing():
            return
        blocked = self.widget.blockSignals(True)
        try:
            self.apply_reply(reply)
        except ValueError:
            pass  # a reply of another form leaves the last value on screen
        finally:
            self.widget.blockSignals(blocked)
        self.panel.setting_shown(self)

    def changed(self, *_args) -> None:
        self.hold_until = time.monotonic() + HOLD_AFTER_CHANGE_S
        self.panel.submit(self.send(self.current()), self.read)
        self.panel.setting_shown(self)

    def set_value(self, value) -> None:
        """A change from the program: same path as the operator's, including sending it."""
        blocked = self.widget.blockSignals(True)
        self.assign(value)
        self.widget.blockSignals(blocked)
        self.changed()


class ChoiceSetting(Setting):
    def __init__(self, panel, read, widget, send):
        super().__init__(panel, read, widget, send)
        widget.activated.connect(self.changed)

    def apply_reply(self, reply):
        position = trigger.find_choice(reply, [(self.widget.itemText(i), self.widget.itemData(i)) for i in range(self.widget.count())])
        if position is None:
            raise ValueError(reply)
        self.widget.setCurrentIndex(position)

    def current(self):
        return self.widget.currentData()

    def assign(self, value):
        self.widget.setCurrentIndex(max(0, self.widget.findData(value)))


class DepthSetting(ChoiceSetting):
    """The reply is a number of points also on Auto, and no reply tells Auto apart. A number no fixed depth has,
    as 500 k (measured), can only be Auto. Otherwise this program knows Auto from having set it."""
    auto = False

    def _fixed(self, points):
        listed = [self.widget.itemData(i) for i in range(self.widget.count()) if self.widget.itemData(i) != AUTO_DEPTH]
        return points in [float(value) for value in listed] or points in OTHER_DEPTHS

    def _show_auto(self, points=None):
        position = self.widget.findData(AUTO_DEPTH)
        name = tr(AUTO_TEXT)
        self.widget.setItemText(position, name if points is None else f"{name} ({format_points(points)})")
        self.widget.setCurrentIndex(position)

    def apply_reply(self, reply):
        points = float(reply)
        if self.auto or not self._fixed(points):
            self.auto = True
            self._show_auto(points)
        else:
            super().apply_reply(reply)

    def set_auto(self, auto):
        """Set by an acquisition, not from this list. What it shows comes with the next reply."""
        self.auto = auto
        if not auto:
            self.widget.setItemText(self.widget.findData(AUTO_DEPTH), tr(AUTO_TEXT))

    def changed(self, *_args):
        self.set_auto(self.current() == AUTO_DEPTH)
        if self.auto:
            self._show_auto()
        super().changed()


class ScaleSetting(Setting):
    """A 1-2-5 list of numbers. A value between its steps, set on the instrument, is shown for as long as it is set."""

    def __init__(self, panel, read, widget, send, values, label):
        super().__init__(panel, read, widget, send)
        self.label = label
        self.extra = None
        self._fill(values)
        widget.activated.connect(self.changed)

    def _fill(self, values):
        self.widget.clear()
        self.extra = None
        for value in values:
            self.widget.addItem(self.label(value), value)

    def _drop_extra(self):
        if self.extra is not None:
            self.widget.removeItem(self.widget.findData(self.extra))
            self.extra = None

    def apply_reply(self, reply):
        value = float(reply)
        if self.extra is not None and abs(self.extra - value) > abs(value) * 1e-3:
            self._drop_extra()
        for position in range(self.widget.count()):
            if abs(self.widget.itemData(position) - value) <= abs(value) * 1e-3:
                self.widget.setCurrentIndex(position)
                return
        place = next((i for i in range(self.widget.count()) if self.widget.itemData(i) > value), self.widget.count())
        self.widget.insertItem(place, self.label(value), value)
        self.widget.setCurrentIndex(place)
        self.extra = value

    def changed(self, *_args):
        if self.current() != self.extra:
            self._drop_extra()
        super().changed()

    def current(self):
        return self.widget.currentData()

    def assign(self, value):
        self.apply_reply(str(value))


class VoltScaleSetting(ScaleSetting):
    """The steps follow the channel's probe ratio. A step from this list turns the channel's fine adjustment off."""

    def __init__(self, panel, read, widget, send, head):
        super().__init__(panel, read, widget, send, vertical_scales(DEFAULT_PROBE), format_volts)
        self.head = head
        self.probe = DEFAULT_PROBE

    def set_probe(self, ratio):
        if ratio != self.probe:
            self.probe = ratio
            shown = self.current()
            self._fill(vertical_scales(ratio))
            if shown is not None:
                self.apply_reply(str(shown))

    def changed(self, *_args):
        self.panel.submit(f"{self.head}:VERNier OFF")
        super().changed()


class ProbeSetting(ChoiceSetting):
    """Its ratio also decides the steps of the channel's scale. A ratio set on the instrument may be its own."""

    def __init__(self, panel, read, widget, send, scale):
        super().__init__(panel, read, widget, send)
        self.scale = scale

    def apply_reply(self, reply):
        ratio = float(reply)
        count = self.widget.count()
        position = next((i for i in range(count) if abs(float(self.widget.itemData(i)) - ratio) <= abs(ratio) * 1e-3), None)
        if position is None:
            position = next((i for i in range(count) if float(self.widget.itemData(i)) > ratio), count)
            self.widget.insertItem(position, f"{ratio:g}×", f"{ratio:g}")
        self.widget.setCurrentIndex(position)
        self.scale.set_probe(ratio)

    def changed(self, *_args):
        self.scale.set_probe(float(self.current()))
        super().changed()
        if self.panel.link is not None:  # the scope has just changed the scale along with the ratio
            self.panel.link.urgent.append(self.scale.read)


class NumberSetting(Setting):
    def __init__(self, panel, read, widget, send):
        super().__init__(panel, read, widget, send)
        widget.setKeyboardTracking(False)
        widget.valueChanged.connect(self.changed)

    def apply_reply(self, reply):
        self.widget.setValue(float(reply))

    def current(self):
        return self.widget.value()

    def assign(self, value):
        self.widget.setValue(value)


class CheckSetting(Setting):
    def __init__(self, panel, read, widget, send):
        super().__init__(panel, read, widget, send)
        widget.clicked.connect(self.changed)

    def apply_reply(self, reply):
        self.widget.setChecked(reply.strip().upper() in ("1", "ON"))

    def current(self):
        return self.widget.isChecked()

    def assign(self, value):
        self.widget.setChecked(bool(value))


class PodSetting(CheckSetting):
    """A group of digital channels. The scope has them on as long as a group is on: the last group off turns :LA:ENABle
    off, and :LA:ENABle on brings back the groups it had before, so the other group is put right (measured)."""

    def __init__(self, panel, pod, widget):
        super().__init__(panel, f":LA:POD{pod}:DISPlay?", widget, lambda value, pod=pod: f":LA:POD{pod}:DISPlay {'ON' if value else 'OFF'}")
        self.pod = pod

    def changed(self, *_args):
        if self.current():
            self.panel.submit(":LA:ENABle ON")
        super().changed()
        if self.current():
            for pod, check in self.panel.pod_on.items():
                if pod != self.pod and not check.isChecked():
                    self.panel.submit(f":LA:POD{pod}:DISPlay OFF", f":LA:POD{pod}:DISPlay?")


class HexSetting(Setting):
    def __init__(self, panel, read, widget, send, maximum, bits=False):
        super().__init__(panel, read, widget, send)
        self.maximum = maximum
        self.bits = bits
        widget.editingFinished.connect(self._edited)

    def _edited(self):
        if self.widget.isModified():
            self.widget.setModified(False)
            self.changed()

    def changed(self, *_args):
        try:
            self.current()
        except ValueError as exc:
            self.panel.report(tr("ERROR: {message}", message=exc))
            return
        super().changed()

    def apply_reply(self, reply):
        value = trigger.parse_integer(reply, self.bits)
        self.widget.setText("" if value is None else f"{value:X}")

    def current(self):
        return trigger.parse_hex(self.widget.text(), self.maximum)

    def assign(self, value):
        self.widget.setText(f"{int(value):X}")


class BitsSetting(Setting):
    """A bit pattern. It is only sent: reading it back would take two exchanges per bit."""

    def __init__(self, panel, read, widget, send, width):
        super().__init__(panel, read, widget, send)
        self.width = width  # the current number of bits, which another field may change
        widget.setToolTip(tr("0, 1 or X (any), from bit 0. This field cannot be read from the scope."))
        widget.editingFinished.connect(self._edited)

    def _edited(self):
        if self.widget.isModified():
            self.widget.setModified(False)
            self.changed()

    def changed(self, *_args):
        try:
            trigger.bit_codes(self.widget.text(), self.width())
        except ValueError as exc:
            self.panel.report(tr("ERROR: {message}", message=exc))
            return
        super().changed()

    def current(self):
        return self.widget.text()

    def assign(self, value):
        self.widget.setText(value)


class ScopePanel(QtWidgets.QWidget):
    """The live screen on the left, every setting on the right. Needs the owner's translation binding."""
    message = QtCore.pyqtSignal(str)

    def __init__(self, owner):
        super().__init__()
        self.owner = owner
        self.link: LiveLink | None = None
        self.stream: ScreenStream | None = None
        self.settings: dict[str, Setting] = {}
        self.fixed_queries: list[str] = []
        self.trigger_settings: dict[str, dict[str, Setting]] = {}
        self.trigger_rows: dict[str, list] = {}
        self.action_buttons = []
        self.shown_form = None
        self._build()

    # ---- layout ---------------------------------------------------------
    def _build(self):
        outer = QtWidgets.QHBoxLayout(self)
        outer.setContentsMargins(2, 2, 2, 2)
        self.splitter = QtWidgets.QSplitter(QtCore.Qt.Horizontal)
        outer.addWidget(self.splitter)

        left = QtWidgets.QWidget()
        left_layout = QtWidgets.QVBoxLayout(left)
        left_layout.setContentsMargins(0, 0, 0, 0)
        bar = QtWidgets.QHBoxLayout()
        for text, command, tip in (("RUN", ":RUN", "Run continuously"), ("STOP", ":STOP", "Stop acquiring"),
                                   ("SINGLE", ":SINGle", "One acquisition"), ("FORCE", ":TFORce", "Force a trigger")):
            button = QtWidgets.QPushButton(text)
            button.setProperty("command", command)
            self.owner._bind(button.setToolTip, tip)
            button.clicked.connect(lambda _checked, command=command: self.submit(command, STATE_QUERY))
            bar.addWidget(button)
            self.action_buttons.append(button)
        self.state_label = QtWidgets.QLabel()
        self.state_label.setMinimumWidth(170)
        bar.addWidget(self.state_label)
        self.rate_label = QtWidgets.QLabel()
        bar.addWidget(self.rate_label)
        bar.addStretch(1)
        self.live_check = QtWidgets.QCheckBox()
        self.live_check.setChecked(True)
        self.owner._bind(self.live_check.setText, "Live view")
        self.owner._bind(self.live_check.setToolTip, "The scope's screen as it changes; clicks and drags work on it like touch. The scope's web page and this program push each other off this connection.")
        self.live_check.toggled.connect(self._live_toggled)
        bar.addWidget(self.live_check)
        left_layout.addLayout(bar)
        self.screen_view = ScreenView()
        self.screen_view.touched.connect(self._touched)
        left_layout.addWidget(self.screen_view, 1)
        self.splitter.addWidget(left)

        page = QtWidgets.QWidget()
        page_layout = QtWidgets.QVBoxLayout(page)
        page_layout.addWidget(self._horizontal_group())
        page_layout.addWidget(self._channels_group())
        page_layout.addWidget(self._digital_group())
        page_layout.addWidget(self._trigger_group())
        page_layout.addStretch(1)
        scroll = QtWidgets.QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setMinimumWidth(CONTROLS_MIN_WIDTH)
        scroll.setFrameShape(QtWidgets.QFrame.NoFrame)
        scroll.setWidget(page)
        self.controls = page
        self.splitter.addWidget(scroll)
        self.splitter.setStretchFactor(0, 1)
        self.splitter.setStretchFactor(1, 0)
        self.splitter.setSizes([SCREEN_WIDTH, CONTROLS_WIDTH])
        self.set_connected(False)

    def _group(self, title):
        group = QtWidgets.QGroupBox()
        self.owner._bind(group.setTitle, title)
        return group

    def _combo(self, items=()):
        combo = QtWidgets.QComboBox()
        combo.setSizeAdjustPolicy(QtWidgets.QComboBox.AdjustToMinimumContentsLengthWithIcon)
        combo.setMinimumContentsLength(6)
        combo.setMaxVisibleItems(20)
        if items:
            self.owner._fill_combo(combo, items)
        return combo

    def _spin(self, minimum, maximum, decimals, suffix=""):
        spin = QtWidgets.QDoubleSpinBox()
        spin.setRange(minimum, maximum)
        spin.setDecimals(decimals)
        spin.setSuffix(suffix)
        return spin

    def _add(self, name, setting, poll=True):
        self.settings[name] = setting
        if poll:
            self.fixed_queries.append(setting.read)
        return setting.widget

    def _horizontal_group(self):
        group = self._group("Time base and memory")
        form = QtWidgets.QFormLayout(group)
        self.time_scale = self._combo()
        self._add("time_scale", ScaleSetting(self, TIME_QUERY, self.time_scale, lambda value: f":TIMebase:MAIN:SCALe {value:g}",
                                             generator.time_scale_choices(), generator.format_time_scale))
        form.addRow(self.owner._label("Time base"), self.time_scale)
        self.time_offset = SecondsBox(1000.0)
        self.time_offset.setSingleStep(TIME_OFFSET_DEFAULT_STEP)
        self.owner._bind(self.time_offset.setToolTip, "Can be typed with a prefix, such as 1.5 ms, 20 µs or 20 us. The arrows move it by 0.1 division.")
        self._add("time_offset", NumberSetting(self, ":TIMebase:MAIN:OFFSet?", self.time_offset, lambda value: f":TIMebase:MAIN:OFFSet {value:.10g}"))
        form.addRow(self.owner._label("Time offset"), self.time_offset)
        self.depth = self._combo(DEPTHS)
        self._add("depth", DepthSetting(self, DEPTH_QUERY, self.depth, lambda value: f":ACQuire:MDEPth {value}"))
        form.addRow(self.owner._label("Memory depth"), self.depth)
        self.acquire_type = self._combo(ACQUIRE_TYPES)
        self._add("acquire_type", ChoiceSetting(self, ":ACQuire:TYPE?", self.acquire_type, lambda value: f":ACQuire:TYPE {value}"))
        form.addRow(self.owner._label("Acquisition mode"), self.acquire_type)
        return group

    def _channels_group(self):
        group = self._group("Analog channels")
        grid = QtWidgets.QGridLayout(group)
        grid.setVerticalSpacing(3)
        for column, title in enumerate(("", "V/div", "Offset", "Coupling", "Bandwidth", "Probe")):
            if title:
                grid.addWidget(self.owner._label(title), 0, column)
        self.channel_on, self.channel_scale, self.channel_offset = {}, {}, {}
        self.channel_coupling, self.channel_band, self.channel_probe = {}, {}, {}
        for row, channel in enumerate(ANALOG_CHANNELS, start=1):
            head = f":CHANnel{channel}"
            check = QtWidgets.QCheckBox(f"CH{channel}")
            self.owner._bind(check.setToolTip, "Turns the channel on or off on the scope")
            self.channel_on[channel] = self._add(f"on{channel}", CheckSetting(
                self, f"{head}:DISPlay?", check, lambda value, head=head: f"{head}:DISPlay {'ON' if value else 'OFF'}"))
            scale = self._combo()
            scale_setting = VoltScaleSetting(self, f"{head}:SCALe?", scale, lambda value, head=head: f"{head}:SCALe {value:g}", head)
            self.channel_scale[channel] = self._add(f"scale{channel}", scale_setting)
            offset = self._spin(-1000.0, 1000.0, 3, " V")
            offset.setSingleStep(0.1)
            self.channel_offset[channel] = self._add(f"offset{channel}", NumberSetting(
                self, f"{head}:OFFSet?", offset, lambda value, head=head: f"{head}:OFFSet {value:.6g}"))
            coupling = self._combo(COUPLINGS)
            self.channel_coupling[channel] = self._add(f"coupling{channel}", ChoiceSetting(
                self, f"{head}:COUPling?", coupling, lambda value, head=head: f"{head}:COUPling {value}"))
            band = self._combo(BANDWIDTHS)
            self.channel_band[channel] = self._add(f"band{channel}", ChoiceSetting(
                self, f"{head}:BWLimit?", band, lambda value, head=head: f"{head}:BWLimit {value}"))
            probe = self._combo(PROBES)
            self.channel_probe[channel] = self._add(f"probe{channel}", ProbeSetting(
                self, f"{head}:PROBe?", probe, lambda value, head=head: f"{head}:PROBe {value}", scale_setting))
            for column, widget in enumerate((check, scale, offset, coupling, band, probe)):
                grid.addWidget(widget, row, column)
        return group

    def _digital_group(self):
        group = self._group("Digital channels")
        grid = QtWidgets.QGridLayout(group)
        self.pod_on, self.pod_threshold = {}, {}
        for row, (pod, name) in enumerate(zip(DIGITAL_PODS, ("D0–D7", "D8–D15"))):
            check = QtWidgets.QCheckBox(name)
            self.owner._bind(check.setToolTip, "Turns this group of channels on on the scope.")
            self.pod_on[pod] = self._add(f"pod{pod}", PodSetting(self, pod, check))
            level = self._spin(-15.0, 15.0, 3, " V")
            level.setSingleStep(0.1)
            self.pod_threshold[pod] = self._add(f"threshold{pod}", NumberSetting(
                self, f":LA:POD{pod}:THReshold?", level, lambda value, pod=pod: f":LA:POD{pod}:THReshold {value:.4g}"))
            grid.addWidget(check, row, 0)
            grid.addWidget(self.owner._label("Threshold"), row, 1)
            grid.addWidget(level, row, 2)
        note = self.owner._label("The default threshold is 0 V: 0–3.3 V logic needs about 1.65 V, or the channels will not change state.")
        note.setWordWrap(True)
        grid.addWidget(note, len(DIGITAL_PODS), 0, 1, 3)
        return group

    def _trigger_group(self):
        group = self._group("Trigger")
        layout = QtWidgets.QVBoxLayout(group)
        form = QtWidgets.QFormLayout()
        self.trigger_mode = self._combo([(mode.label, mode.name) for mode in trigger.MODES])
        self._add("trigger_mode", ChoiceSetting(self, MODE_QUERY, self.trigger_mode, lambda value: f":TRIGger:MODE {value}"))
        form.addRow(self.owner._label("Type"), self.trigger_mode)
        self.sweep = self._combo(trigger.SWEEPS)
        self._add("sweep", ChoiceSetting(self, ":TRIGger:SWEep?", self.sweep, lambda value: f":TRIGger:SWEep {value}"))
        form.addRow(self.owner._label("Sweep"), self.sweep)
        layout.addLayout(form)
        self.trigger_pages = QtWidgets.QStackedWidget()
        for mode in trigger.MODES:
            self.trigger_pages.addWidget(self._trigger_page(mode))
        layout.addWidget(self.trigger_pages)
        self._refresh_trigger_form()
        return group

    def _trigger_page(self, mode):
        page = QtWidgets.QWidget()
        form = QtWidgets.QFormLayout(page)
        form.setContentsMargins(0, 0, 0, 0)
        settings, rows = {}, []
        for field in mode.fields:
            read = trigger.query(mode, field)
            send = lambda value, field=field: trigger.command(mode, field, value)
            if field.kind == trigger.CHOICE:
                widget = self._combo(field.choices)
                setting = ChoiceSetting(self, read, widget, send)
            elif field.kind == trigger.CHECK:
                widget = QtWidgets.QCheckBox()
                setting = CheckSetting(self, read, widget, send)
            elif field.kind == trigger.BITS:
                widget = QtWidgets.QLineEdit()
                width = (lambda source=field.width_from: int(settings[source].current())) if field.width_from else (
                    lambda width=field.width: width)
                send = lambda value, field=field, width=width: trigger.command(mode, field, value, width())
                setting = BitsSetting(self, read, widget, send, width)
            elif field.kind == trigger.HEX:
                widget = QtWidgets.QLineEdit("0")
                setting = HexSetting(self, read, widget, send, field.maximum, field.bits)
            else:
                widget = self._spin(field.minimum, field.maximum, field.decimals, field.suffix)
                setting = NumberSetting(self, read, widget, send)
            settings[field.key] = setting
            label = self.owner._label(field.label)
            form.addRow(label, widget)
            rows.append((field, label, widget))
        self.trigger_settings[mode.name] = settings
        self.trigger_rows[mode.name] = rows
        return page

    # ---- trigger form ---------------------------------------------------
    def current_mode(self) -> trigger.Mode:
        return next(mode for mode in trigger.MODES if mode.name == self.trigger_mode.currentData())

    def _condition(self, mode):
        settings = self.trigger_settings[mode.name]
        return settings["WHEN"].widget.currentData() if "WHEN" in settings else None

    def _visible_trigger_settings(self):
        mode = self.current_mode()
        condition = self._condition(mode)
        return [self.trigger_settings[mode.name][field.key] for field in mode.fields
                if not field.when or condition in field.when]

    def _refresh_trigger_form(self):
        """Show the page of the current type, with only the fields its condition uses, and read those."""
        self.trigger_pages.setCurrentIndex(self.trigger_mode.currentIndex())
        for mode in trigger.MODES:
            condition = self._condition(mode)
            for field, label, widget in self.trigger_rows[mode.name]:
                shown = not field.when or condition in field.when
                label.setVisible(shown)
                widget.setVisible(shown)
        self._update_watch()

    def setting_shown(self, setting):
        """Called for every change of a setting's widget, by the operator or from a reply.
        Every reading of the type or the condition comes here, so the form is redone only when
        one of them differs: restarting the round each time would starve the fields after them."""
        if setting is self.settings.get("time_scale") and setting.current():
            self.time_offset.setSingleStep(setting.current() * OFFSET_STEP_DIVISIONS)
        if setting is self.settings.get("trigger_mode") or setting in [s.get("WHEN") for s in self.trigger_settings.values()]:
            form = (self.trigger_mode.currentData(), self._condition(self.current_mode()))
            if form != self.shown_form:
                self.shown_form = form
                self._refresh_trigger_form()
                if self.link is not None:  # the fields just shown hold old values until read
                    self.link.urgent.extend(setting.read for setting in self._visible_trigger_settings() if setting.read)

    def _update_watch(self):
        if self.link is not None:
            self.link.watch([STATE_QUERY, RATE_QUERY] + self.fixed_queries + [s.read for s in self._visible_trigger_settings() if s.read])

    # ---- link -----------------------------------------------------------
    def attach(self, scope):
        self.link = LiveLink(scope, self)
        self.link.value.connect(self._value_received)
        self.link.refused.connect(self._refused)
        self.link.lost.connect(lambda text: self.report(tr("ERROR: {message}", message=text)))
        self.link.active = self.isVisible()
        self._update_watch()
        self.link.read_all_once()
        self.link.start()
        self.set_connected(True)
        self._update_stream()

    def detach(self):
        if self.link is not None:
            self.link.stop()
            self.link = None
        self._update_stream()
        self.set_connected(False)

    def set_connected(self, connected):
        self.controls.setEnabled(connected)
        for button in self.action_buttons:
            button.setEnabled(connected)
        if not connected:
            self.state_label.clear()
            self.rate_label.clear()

    def set_job_running(self, running):
        """A job has the link to itself. Afterwards everything is read again, since it may have changed settings."""
        if self.link is not None:
            self.link.busy = running
            if not running:
                self.link.read_all_once()
        for button in self.action_buttons:
            button.setEnabled(not running and self.link is not None)

    def set_active(self, active):
        if self.link is not None:
            self.link.active = active
        self._update_stream()

    def _live_toggled(self, _enabled):
        self._update_stream()

    def _update_stream(self):
        """The stream runs while the picture is on screen, also during a job: it does not use the SCPI link."""
        wanted = self.link is not None and self.link.active and self.live_check.isChecked()
        if wanted and self.stream is None:
            self.stream = ScreenStream(self.link.scope.host, self)
            self.stream.frame_ready.connect(self._frame_ready)
            self.stream.lost.connect(self._stream_silent)
            self.stream.start()
        elif not wanted and self.stream is not None:
            self.stream.stop()
            self.stream = None

    def _frame_ready(self):
        image = self.stream.take() if self.stream is not None else None
        if image is not None:
            self.screen_view.show_image(image)

    def _stream_silent(self, host):
        self.report(tr("Warning: the live view from {host} gets no picture. Connecting again.", host=host))

    def _touched(self, kind, x, y):
        if self.stream is not None:
            self.stream.touch(kind, x, y)

    def submit(self, command, read_back=None):
        if self.link is not None:
            self.link.send(command, read_back)

    def report(self, text):
        self.message.emit(text)

    def _refused(self, command, error):
        self.report(tr("ERROR: {message}", message=tr("the scope refused {command}: {error}", command=command, error=error)))

    def _value_received(self, query, reply):
        if query == STATE_QUERY:
            name = STATE_NAMES.get(reply.replace("'", "").upper())
            self.state_label.setText(tr(name) if name else reply)
            return
        if query == RATE_QUERY:
            try:
                self.rate_label.setText(f"{float(reply) / 1e6:g} MSa/s")
            except ValueError:
                pass
            return
        for setting in self.settings.values():
            if setting.read == query:
                setting.show(reply)
                return
        for settings in self.trigger_settings.values():
            for setting in settings.values():
                if setting.read == query:
                    setting.show(reply)
                    return

    def note_depth(self, depth) -> None:
        """An acquisition sets this depth, None for none."""
        if depth is not None:
            self.settings["depth"].set_auto(depth == AUTO_DEPTH)

    def depth_is_auto(self) -> bool:
        return self.settings["depth"].auto

    def edge_setting(self, key) -> Setting:
        return self.trigger_settings["EDGE"][key]

    def drain(self):
        if self.link is not None:
            self.link.drain()

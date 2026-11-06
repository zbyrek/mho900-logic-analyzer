"""The toolbar's scope settings, applied by an acquisition, and the window of one channel's row."""
from __future__ import annotations

from typing import Iterable

from PyQt5 import QtCore, QtWidgets

from . import generator, trigger
from .i18n import tr
from .scope import DIGITAL_CHANNELS, DIGITAL_PODS, channel_name, digital_channel, is_digital
from .scope_panel import DEPTHS

ANALOG = (1, 2, 3, 4)
DIGITAL = tuple(digital_channel(number) for number in range(DIGITAL_CHANNELS))
DIGITAL_COLUMNS = 8
DEFAULT_CHANNELS = "1"  # every further channel takes sample rate and memory from the others
READ_FORMATS = (("BYTE, 8 bits", "BYTE"), ("WORD, 16 bits", "WORD"), ("BIN file, 32-bit voltages", "FILE"))
NO_CHANGE = "No change"
VOLTS_LIMIT = 100000.0  # far beyond any input with any probe
VOLTS_DECIMALS = 6
SCALE_STEPS = 10  # one click of a spin box moves the limit by this part of the shown range
THRESHOLD_STEP = 0.1
POD_LABELS = {1: "Threshold D0–D7", 2: "Threshold D8–D15"}
POD_THRESHOLD_LIMIT = 15.0  # the range the Oscilloscope tab gives a group
PRETRIGGER_DEFAULT = 50
PRETRIGGER_STEP = 5
POD_THRESHOLD_DEFAULT = 1.65
PROTOCOL_LEVEL_DEFAULT = 1.65
# Where a protocol's field starts. The others start at their first choice or their minimum.
PROTOCOL_DEFAULTS = {"BAUD": 9600.0, "SPOint": 50.0, "SAMPlepoint": 50.0, "WIDTh": 8.0, "UWIDth": 8.0,
                     "TIMeout": 1e-6, "MAXid": 1023.0, "MAXCy": 63.0}
PROTOCOL_MIN_WIDTH = 340
PROTOCOL_MIN_HEIGHT = 300
NO_PROTOCOL = ""
TRIGGER_MARK = "Trig"  # says on a channel's row that what follows is a trigger, not a decoder


def _pairs(text: str) -> dict:
    """Stored as "channel:value channel:value"."""
    return dict(part.split(":", 1) for part in str(text).split() if ":" in part)


class ProtocolForm(QtWidgets.QWidget):
    """One protocol's trigger, kept in the settings and sent by an acquisition. D0 to D15 have no level
    of their own: they switch at their group's threshold, so a level field is not offered for them."""
    sources_changed = QtCore.pyqtSignal()

    def __init__(self, owner, mode: trigger.Mode):
        super().__init__()
        self.owner = owner
        self.settings = owner.settings
        self.mode = mode
        self.widgets: dict[str, QtWidgets.QWidget] = {}
        self.labels: dict[str, QtWidgets.QLabel] = {}
        form = QtWidgets.QFormLayout(self)
        form.setContentsMargins(0, 0, 0, 0)
        sources = 0
        for field in mode.fields:
            default = PROTOCOL_DEFAULTS.get(field.key)
            if trigger.is_source(field):
                widget = self._choice(field, trigger.SOURCES[sources][1])
                sources += 1
                widget.currentIndexChanged.connect(self._source_changed)
            elif field.kind == trigger.CHOICE:
                widget = self._choice(field, "" if default is None else f"{default:g}")
                if field.key == "WHEN":
                    widget.currentIndexChanged.connect(self._show_fields)
            elif field.kind == trigger.CHECK:
                widget = self._check(field)
            elif field.kind in (trigger.HEX, trigger.BITS):
                widget = self._text(field)
            else:
                widget = self._number(field, PROTOCOL_LEVEL_DEFAULT if trigger.is_level(field) else default)
            self.widgets[field.key] = widget
            self.labels[field.key] = owner._label(field.label)
            form.addRow(self.labels[field.key], widget)
        self._show_fields()

    def _key(self, field):
        return f"protocol_{self.mode.name}_{field.key.replace(':', '_')}"

    def _choice(self, field, default):
        combo = QtWidgets.QComboBox()
        combo.setMaxVisibleItems(20)
        self.owner._fill_combo(combo, field.choices)
        position = combo.findData(str(self.settings.value(self._key(field), default)))
        combo.setCurrentIndex(max(0, position))
        combo.currentIndexChanged.connect(lambda _index: self.settings.setValue(self._key(field), combo.currentData()))
        return combo

    def _check(self, field):
        box = QtWidgets.QCheckBox()
        box.setChecked(self.settings.value(self._key(field), False, type=bool))
        box.toggled.connect(lambda checked: self.settings.setValue(self._key(field), checked))
        return box

    def _text(self, field):
        line = QtWidgets.QLineEdit(str(self.settings.value(self._key(field), "0" if field.kind == trigger.HEX else "")))
        if field.kind == trigger.BITS:
            self.owner._bind(line.setToolTip, "0, 1 or X (any), the most significant bit first.")
        line.textChanged.connect(lambda text: self.settings.setValue(self._key(field), text))
        return line

    def _number(self, field, default):
        box = QtWidgets.QDoubleSpinBox()
        box.setRange(field.minimum, field.maximum)
        box.setDecimals(field.decimals)
        box.setSuffix(field.suffix)
        start = field.minimum if default is None else default
        box.setValue(self.settings.value(self._key(field), start, type=float))
        box.valueChanged.connect(lambda value: self.settings.setValue(self._key(field), value))
        return box

    def _source_changed(self, _index):
        self._show_fields()
        self.sources_changed.emit()

    def _shown(self, field) -> bool:
        when = self.widgets.get("WHEN")
        if field.when and when is not None and when.currentData() not in field.when:
            return False
        if trigger.is_level(field):
            return not is_digital(trigger.source_channel(self.widgets[trigger.source_of_level(self.mode, field).key].currentData()))
        return True

    def _show_fields(self, _index=0):
        for field in self.mode.fields:
            shown = self._shown(field)
            self.labels[field.key].setVisible(shown)
            self.widgets[field.key].setVisible(shown)

    def channels(self) -> set[int]:
        """The channels this trigger listens to."""
        return {trigger.source_channel(self.widgets[field.key].currentData()) for field in self.mode.fields
                if trigger.is_source(field) and self._shown(field)}

    def _value(self, field):
        widget = self.widgets[field.key]
        if field.kind == trigger.CHOICE:
            return widget.currentData()
        if field.kind == trigger.CHECK:
            return widget.isChecked()
        if field.kind == trigger.HEX:
            return trigger.parse_hex(widget.text(), field.maximum)
        if field.kind == trigger.BITS:
            return widget.text()
        return widget.value()

    def commands(self) -> list[str]:
        """Raises ValueError on a hex or bit field that does not parse."""
        commands = [f":TRIGger:MODE {self.mode.name}"]
        for field in self.mode.fields:
            if self._shown(field):
                width = int(self.widgets[field.width_from].value()) if field.width_from else None
                commands.append(trigger.command(self.mode, field, self._value(field), width))
        return commands


class ScopeSettings(QtWidgets.QPushButton):
    """What an acquisition sets on the scope, and nothing else does. Reading its memory changes none of it.

    A channel assigned to a decoder or used by the trigger stays on, since that use needs it.
    The owner supplies the settings store and the translation helpers.
    """
    changed = QtCore.pyqtSignal()
    thresholds_changed = QtCore.pyqtSignal()
    hide_requested = QtCore.pyqtSignal()
    message = QtCore.pyqtSignal(str)

    def __init__(self, owner):
        super().__init__()
        self.owner = owner
        self.settings = owner.settings
        self.chosen = {channel for channel in map(int, str(self.settings.value("read_channels", DEFAULT_CHANNELS)).split())
                       if channel in ANALOG or channel in DIGITAL}
        self.locked: set[int] = set()
        self.conditions = {int(channel): code for channel, code in _pairs(self.settings.value("channel_triggers", "")).items()
                           if code in trigger.allowed_conditions(int(channel))}
        self.thresholds = {int(channel): float(value)
                           for channel, value in _pairs(self.settings.value("channel_thresholds", "")).items()}
        self.boxes: dict[int, QtWidgets.QCheckBox] = {}
        self.pod_boxes: dict[int, tuple] = {}
        self.protocol_forms: dict[str, ProtocolForm] = {}
        menu = QtWidgets.QMenu(self)
        holder = QtWidgets.QWidgetAction(menu)
        holder.setDefaultWidget(self._build())
        menu.addAction(holder)
        self.setMenu(menu)
        owner._bind(self.setText, "Scope settings")
        owner._bind(lambda _text: self._refresh(), "Channels")
        self._show_trigger_form()

    # ---- layout ---------------------------------------------------------
    def _build(self):
        page = QtWidgets.QWidget()
        layout = QtWidgets.QHBoxLayout(page)
        left = QtWidgets.QVBoxLayout()
        left.addWidget(self._channels_group())
        left.addWidget(self._capture_group())
        left.addStretch(1)
        layout.addLayout(left)
        layout.addWidget(self._trigger_group())
        return page

    def _group(self, title):
        group = QtWidgets.QGroupBox()
        self.owner._bind(group.setTitle, title)
        return group

    def _channels_group(self):
        group = self._group("Channels")
        layout = QtWidgets.QVBoxLayout(group)
        layout.addLayout(self._channel_grid("Analog", ANALOG, "CH1–CH4, read over SCPI."))
        layout.addLayout(self._channel_grid("Digital", DIGITAL, "D0–D15, read more slowly, through a file of the scope's memory."))
        layout.addLayout(self._pod_thresholds())
        self.hide_unused = QtWidgets.QPushButton()
        self.owner._bind(self.hide_unused.setText, "Hide unused")
        self.owner._bind(self.hide_unused.setToolTip, "Unticks the channels without data in the last read. A channel ticked again shows again.")
        self.hide_unused.clicked.connect(self.hide_requested)
        layout.addWidget(self.hide_unused)
        return group

    def _channel_grid(self, title, channels, tip):
        layout = QtWidgets.QGridLayout()
        layout.setVerticalSpacing(2)
        head = QtWidgets.QHBoxLayout()
        label = QtWidgets.QLabel()
        self.owner._bind(label.setText, title)
        self.owner._bind(label.setToolTip, tip)
        head.addWidget(label)
        head.addStretch(1)
        for text, checked, button_tip in (("All", True, "Ticks all channels of this group."),
                                          ("Select none", False, "Unticks the channels of this group, except those used by decoders and the trigger.")):
            button = QtWidgets.QPushButton()
            self.owner._bind(button.setText, text)
            self.owner._bind(button.setToolTip, button_tip)
            button.clicked.connect(lambda _checked, checked=checked, channels=channels: self._set_group(channels, checked))
            head.addWidget(button)
        layout.addLayout(head, 0, 0, 1, DIGITAL_COLUMNS)
        for position, channel in enumerate(channels):
            box = QtWidgets.QCheckBox(channel_name(channel))
            box.toggled.connect(lambda checked, channel=channel: self._box_toggled(channel, checked))
            self.boxes[channel] = box
            layout.addWidget(box, 1 + position // DIGITAL_COLUMNS, position % DIGITAL_COLUMNS)
        return layout

    def _pod_thresholds(self):
        layout = QtWidgets.QGridLayout()
        tip = ("An acquisition sets this threshold if any channel of this group is on. Unticked: the threshold set on the scope stays. Reading the memory does not send it.")
        for row, pod in enumerate(DIGITAL_PODS):
            box = self._stored_check(f"pod_threshold_{pod}_on", False, POD_LABELS[pod], tip)
            spin = QtWidgets.QDoubleSpinBox()
            spin.setRange(-POD_THRESHOLD_LIMIT, POD_THRESHOLD_LIMIT)
            spin.setDecimals(2)
            spin.setSingleStep(THRESHOLD_STEP)
            spin.setSuffix(" V")
            spin.setValue(self.settings.value(f"pod_threshold_{pod}", POD_THRESHOLD_DEFAULT, type=float))
            spin.setEnabled(box.isChecked())
            spin.valueChanged.connect(lambda value, pod=pod: self.settings.setValue(f"pod_threshold_{pod}", value))
            box.toggled.connect(spin.setEnabled)
            self.pod_boxes[pod] = (box, spin)
            layout.addWidget(box, row, 0)
            layout.addWidget(spin, row, 1)
        layout.setColumnStretch(2, 1)
        return layout

    def pod_thresholds(self) -> dict:
        """The groups of digital channels whose threshold an acquisition sets."""
        return {pod: spin.value() for pod, (box, spin) in self.pod_boxes.items() if box.isChecked()}

    def _stored_combo(self, key, items, default=""):
        """A choice of (label, value) kept in the settings, as text since values may be numbers."""
        combo = QtWidgets.QComboBox()
        combo.setMaxVisibleItems(20)
        self.owner._fill_combo(combo, items)
        stored = str(self.settings.value(key, default))
        position = next((index for index in range(combo.count()) if str(combo.itemData(index) or "") == stored), 0)
        combo.setCurrentIndex(position)

        def remember(_index):
            self.settings.setValue(key, str(combo.currentData() or ""))
            self._refresh()

        combo.currentIndexChanged.connect(remember)
        return combo

    def _stored_check(self, key, default, text, tip):
        box = QtWidgets.QCheckBox()
        self.owner._bind(box.setText, text)
        self.owner._bind(box.setToolTip, tip)
        box.setChecked(self.settings.value(key, default, type=bool))
        box.toggled.connect(lambda checked: self.settings.setValue(key, checked))
        return box

    def _capture_group(self):
        group = self._group("Acquisition")
        layout = QtWidgets.QVBoxLayout(group)
        form = QtWidgets.QFormLayout()
        self.depth = self._stored_combo("capture_depth", ((NO_CHANGE, None), *DEPTHS))
        self.owner._bind(self.depth.setToolTip, "More points make a longer record and a longer read. Auto: the scope picks the number of points so that the record covers the screen. No change leaves what is set on the scope.")
        form.addRow(self.owner._label("Memory depth"), self.depth)
        scales = tuple((generator.format_time_scale(value), value) for value in generator.time_scale_choices())
        self.time_scale = self._stored_combo("capture_time_scale", ((NO_CHANGE, None), *scales))
        form.addRow(self.owner._label("Time base"), self.time_scale)
        form.addRow(*self._pretrigger_row())
        self.read_format = QtWidgets.QComboBox()
        self.owner._fill_combo(self.read_format, READ_FORMATS)
        self.owner._bind(self.read_format.setToolTip, "BYTE is the fastest. WORD is more accurate but 2× slower. Channels D0–D15 always go through the BIN file.")
        form.addRow(self.owner._label("Read format"), self.read_format)
        layout.addLayout(form)
        self.force_dc = self._stored_check("force_dc", True, "Force DC", "Force DC coupling on the channels in use")
        self.wait_trigger = self._stored_check("wait_trigger", False, "Wait for trigger",
                                               "Wait for the trigger with no limit (end with Abort). A trigger condition on a channel always waits.")
        row = QtWidgets.QHBoxLayout()
        row.addWidget(self.force_dc)
        row.addWidget(self.wait_trigger)
        row.addStretch(1)
        layout.addLayout(row)
        return group

    def _pretrigger_row(self):
        self.pretrigger_on = self._stored_check(
            "pretrigger_on", False, "Pretrigger",
            "An acquisition sets the time base offset on the scope so that this share of the memory lies before the trigger. Unchecked: the offset set on the scope stays. Reading the memory does not send it. The scope does not go below −5 divisions, so with a memory longer than the screen a large pretrigger cannot be reached and the log says so.")
        self.pretrigger_percent = QtWidgets.QSpinBox()
        self.pretrigger_percent.setRange(0, 100)
        self.pretrigger_percent.setSingleStep(PRETRIGGER_STEP)
        self.pretrigger_percent.setSuffix(" %")
        self.pretrigger_percent.setValue(self.settings.value("pretrigger_percent", PRETRIGGER_DEFAULT, type=int))
        self.pretrigger_percent.setEnabled(self.pretrigger_on.isChecked())
        self.pretrigger_percent.valueChanged.connect(lambda value: self.settings.setValue("pretrigger_percent", value))
        self.pretrigger_on.toggled.connect(self.pretrigger_percent.setEnabled)
        return self.pretrigger_on, self.pretrigger_percent

    def pretrigger(self) -> int | None:
        """The percent of the memory to lie before the trigger, None to leave it to the scope."""
        return self.pretrigger_percent.value() if self.pretrigger_on.isChecked() else None

    def _trigger_group(self):
        group = self._group("Trigger")
        layout = QtWidgets.QVBoxLayout(group)
        tabs = QtWidgets.QTabWidget()
        for position, (page, title) in enumerate(((self._conditions_page(), "Channels"), (self._advanced_page(), "Advanced"))):
            tabs.addTab(page, "")
            self.owner._bind(lambda text, position=position: tabs.setTabText(position, text), title)
        layout.addWidget(tabs, 1)
        self.trigger_summary = QtWidgets.QLabel()
        self.trigger_summary.setWordWrap(True)
        layout.addWidget(self.trigger_summary)
        return group

    def _conditions_page(self):
        page = QtWidgets.QWidget()
        form = QtWidgets.QFormLayout(page)
        self.trigger_channel = QtWidgets.QComboBox()
        self.trigger_channel.setMaxVisibleItems(20)
        for channel in ANALOG + DIGITAL:
            self.trigger_channel.addItem(channel_name(channel), channel)
        self.trigger_channel.currentIndexChanged.connect(self._show_trigger_form)
        form.addRow(self.owner._label("Channel"), self.trigger_channel)
        self.trigger_condition = QtWidgets.QComboBox()
        self.owner._fill_combo(self.trigger_condition, [(label, code) for label, code, _symbol in trigger.CONDITIONS])
        self.trigger_condition.activated.connect(self._condition_picked)
        form.addRow(self.owner._label("Condition"), self.trigger_condition)
        note = QtWidgets.QLabel()
        note.setWordWrap(True)
        self.owner._bind(note.setText, "D0–D15 have edges only: the scope's pattern command takes levels for CH1–CH4 alone. A pattern on D0–D15 can be set by clicking the screen in the Oscilloscope tab, and an acquisition with no conditions leaves it as it is.")
        form.addRow(note)
        return page

    def _advanced_page(self):
        page = QtWidgets.QWidget()
        layout = QtWidgets.QVBoxLayout(page)
        row = QtWidgets.QFormLayout()
        self.protocol = QtWidgets.QComboBox()
        modes = tuple(mode for mode in trigger.MODES if mode is not trigger.EDGE)
        self.owner._fill_combo(self.protocol, (("None", NO_PROTOCOL), *((mode.label, mode.name) for mode in modes)))
        row.addRow(self.owner._label("Protocol"), self.protocol)
        layout.addLayout(row)
        self.protocol_pages = QtWidgets.QStackedWidget()
        note = QtWidgets.QLabel()
        note.setWordWrap(True)
        note.setAlignment(QtCore.Qt.AlignTop | QtCore.Qt.AlignLeft)
        self.owner._bind(note.setText, "A trigger on a protocol, on CH1–CH4 and D0–D15. It replaces the conditions on channels, and an acquisition waits for it with no limit. D0–D15 switch at their group's threshold, set next to the channels. I2S, FlexRay and 1553B need options of the scope.")
        self.protocol_pages.addWidget(note)
        for mode in modes:
            form = ProtocolForm(self.owner, mode)
            form.sources_changed.connect(self._apply)
            self.protocol_forms[mode.name] = form
            self.protocol_pages.addWidget(form)
        scroll = QtWidgets.QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QtWidgets.QFrame.NoFrame)
        scroll.setMinimumSize(PROTOCOL_MIN_WIDTH, PROTOCOL_MIN_HEIGHT)
        scroll.setWidget(self.protocol_pages)
        layout.addWidget(scroll, 1)
        stored = str(self.settings.value("trigger_protocol", NO_PROTOCOL))
        self.protocol.setCurrentIndex(max(0, self.protocol.findData(stored)))
        self.protocol_pages.setCurrentIndex(self.protocol.currentIndex())
        self.protocol.currentIndexChanged.connect(self._protocol_picked)
        return page

    # ---- what was chosen ------------------------------------------------
    def selected(self) -> set[int]:
        return self.chosen | self.locked | set(self.conditions) | self.protocol_channels()

    def protocol_form(self) -> ProtocolForm | None:
        return self.protocol_forms.get(self.protocol.currentData())

    def protocol_channels(self) -> set[int]:
        form = self.protocol_form()
        return form.channels() if form else set()

    def trigger_is_waited_for(self) -> bool:
        """As in PulseView, a condition is waited for: forcing the trigger would ignore it."""
        return bool(self.conditions) or self.protocol_form() is not None

    def trigger_commands(self, levels: dict) -> list[str]:
        """What an acquisition sends. levels: channel -> volts, for the conditions. Raises ValueError on a bad field."""
        form = self.protocol_form()
        return form.commands() if form else trigger.condition_commands(self.conditions, levels)

    def set_locked(self, channels: Iterable[int]) -> None:
        self.locked = set(channels)
        self._refresh()

    def choose(self, channels: Iterable[int]) -> None:
        """A choice not made here, such as the channels of an opened file. It asks nobody to redraw."""
        self.chosen = set(channels)
        self._save_channels()
        self._refresh()

    def untick(self, channels: Iterable[int]) -> None:
        self.chosen -= set(channels)
        self._apply()

    def follow_the_scope(self) -> None:
        """Leave the timebase and the trigger to what is set on the scope itself."""
        self.time_scale.setCurrentIndex(0)
        self.pretrigger_on.setChecked(False)
        self.conditions = {}
        self._save_conditions()
        self.protocol.setCurrentIndex(0)
        self._apply()

    def set_condition(self, channel: int, code: str) -> None:
        if code == self.conditions.get(channel, trigger.NONE):
            return
        if code and self.protocol_form():
            self.message.emit(tr("Warning: a condition on a channel replaced the trigger on the protocol {protocol}.", protocol=self.protocol.currentText()))
            self.protocol.setCurrentIndex(0)
        dropped = trigger.conflicts(self.conditions, channel, code)
        for other in dropped:
            del self.conditions[other]
        if code:
            self.conditions[channel] = code
            self.chosen.add(channel)
        else:
            self.conditions.pop(channel, None)
        if dropped:
            self.message.emit(tr("Warning: the scope cannot combine these conditions, so the trigger on {channels} was removed.",
                                 channels=", ".join(map(channel_name, sorted(dropped)))))
        self._save_conditions()
        self._save_channels()
        self._apply()

    def set_threshold(self, channel: int, level: float | None) -> None:
        """None: the threshold comes from the signal's own swing."""
        if self.thresholds.get(channel) == level:
            return
        if level is None:
            del self.thresholds[channel]
        else:
            self.thresholds[channel] = level
        self.settings.setValue("channel_thresholds", " ".join(f"{key}:{value:g}" for key, value in sorted(self.thresholds.items())))
        self.changed.emit()
        self.thresholds_changed.emit()

    def symbol(self, channel: int) -> str:
        """What a channel's row shows of the trigger on it: its condition, or the protocol it is a source of."""
        if channel in self.protocol_channels():
            return f"{TRIGGER_MARK} {self.protocol.currentText()}"
        symbol = trigger.symbol(self.conditions.get(channel, trigger.NONE))
        return f"{TRIGGER_MARK} {symbol}" if symbol else ""

    # ---- changes --------------------------------------------------------
    def _box_toggled(self, channel, checked):
        (self.chosen.add if checked else self.chosen.discard)(channel)
        self._save_channels()
        self._apply()

    def _set_group(self, channels, checked):
        self.chosen = self.chosen | set(channels) if checked else self.chosen - set(channels)
        self._save_channels()
        self._apply()

    def _condition_picked(self, _index):
        self.set_condition(self.trigger_channel.currentData(), self.trigger_condition.currentData())

    def _protocol_picked(self, position):
        self.settings.setValue("trigger_protocol", self.protocol.currentData())
        self.protocol_pages.setCurrentIndex(position)
        if self.protocol_form() and self.conditions:
            self.message.emit(tr("Warning: the trigger on the protocol {protocol} replaced the conditions on the channels {channels}.",
                                 protocol=self.protocol.currentText(), channels=", ".join(map(channel_name, sorted(self.conditions)))))
            self.conditions = {}
            self._save_conditions()
        self._apply()

    def _apply(self, *_args):
        self._refresh()
        self.changed.emit()

    def _save_channels(self):
        self.settings.setValue("read_channels", " ".join(map(str, sorted(self.chosen))))

    def _save_conditions(self):
        self.settings.setValue("channel_triggers", " ".join(f"{key}:{value}" for key, value in sorted(self.conditions.items())))

    # ---- what is shown --------------------------------------------------
    def _show_trigger_form(self, _index=0):
        """The condition of the channel in the form. D0 to D15 have only the edges, see trigger.conflicts."""
        channel = self.trigger_channel.currentData()
        allowed = trigger.allowed_conditions(channel)
        model = self.trigger_condition.model()
        for position in range(self.trigger_condition.count()):
            model.item(position).setEnabled(self.trigger_condition.itemData(position) in allowed)
        self.trigger_condition.setCurrentIndex(self.trigger_condition.findData(self.conditions.get(channel, trigger.NONE)))

    def _trigger_text(self):
        if self.protocol_form():
            return self.protocol.currentText()
        return (", ".join(f"{channel_name(channel)} {trigger.symbol(code)}" for channel, code in sorted(self.conditions.items()))
                or tr("no change"))

    def _refresh(self):
        listened = self.protocol_channels()
        for channel, box in self.boxes.items():
            box.blockSignals(True)
            box.setChecked(channel in self.selected())
            box.setEnabled(channel not in self.locked and channel not in self.conditions and channel not in listened)
            if channel in self.conditions:
                box.setToolTip(tr("It has a trigger condition, so it stays on."))
            elif channel in listened:
                box.setToolTip(tr("It is a source of the protocol trigger, so it stays on."))
            elif channel in self.locked:
                box.setToolTip(tr("Used by a decoder, so always read."))
            else:
                box.setToolTip(tr("Acquisition turns a ticked channel on and the others off."))
            box.blockSignals(False)
        self._show_trigger_form()
        self.trigger_summary.setText(tr("Trigger: {trigger}", trigger=self._trigger_text()))
        self.setToolTip(tr("Applied by Acquisition. Reading the memory does not send them. Channels: {channels}. Trigger: {trigger}.",
                           channels=", ".join(map(channel_name, sorted(self.selected()))) or tr("none"),
                           trigger=self._trigger_text()))


class ChannelDialog(QtWidgets.QDialog):
    """A click on a row's label, like the popup of a trace in PulseView: trigger, threshold, vertical scale."""
    applied = QtCore.pyqtSignal(str, object, object)  # trigger condition, threshold or None, limits or None

    def __init__(self, parent, channel: int, condition: str, threshold: float | None, suggested: float,
                 limits: tuple | None = None, manual_limits: bool = False):
        super().__init__(parent)
        self.setAttribute(QtCore.Qt.WA_DeleteOnClose)
        self.setWindowTitle(channel_name(channel))
        self.channel = channel
        layout = QtWidgets.QVBoxLayout(self)
        layout.addWidget(self._trigger_group(channel, condition))
        self.threshold_auto = self.threshold = None
        if not is_digital(channel):
            layout.addWidget(self._threshold_group(threshold, suggested))
        self.scale_auto = self.high = self.low = None
        if limits is not None:
            layout.addWidget(self._scale_group(limits, manual_limits))
        buttons = QtWidgets.QDialogButtonBox()
        self.apply_button = buttons.addButton(tr("Apply"), QtWidgets.QDialogButtonBox.AcceptRole)
        buttons.addButton(tr("Cancel"), QtWidgets.QDialogButtonBox.RejectRole)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _trigger_group(self, channel, condition):
        group = QtWidgets.QGroupBox(tr("Trigger"))
        row = QtWidgets.QHBoxLayout(group)
        self.trigger_buttons = QtWidgets.QButtonGroup(self)
        self.trigger_codes: dict[str, QtWidgets.QToolButton] = {}
        allowed = trigger.allowed_conditions(channel)
        for label, code, symbol in trigger.CONDITIONS:
            button = QtWidgets.QToolButton()
            button.setText(symbol)
            button.setStyleSheet("QToolButton:checked { background: palette(highlight); color: palette(highlighted-text); }")
            button.setToolTip(tr(label) if code in allowed else tr("The scope takes a level in a pattern only on CH1–CH4."))
            button.setCheckable(True)
            button.setEnabled(code in allowed)
            button.setChecked(code == condition)
            self.trigger_buttons.addButton(button)
            self.trigger_codes[code] = button
            row.addWidget(button)
        row.addStretch(1)
        return group

    def _threshold_group(self, threshold, suggested):
        group = QtWidgets.QGroupBox(tr("Threshold"))
        group.setToolTip(tr("Separates the states in the Logic view, for decoders with an automatic threshold and for the trigger."))
        form = QtWidgets.QFormLayout(group)
        self.threshold_auto = QtWidgets.QCheckBox(tr("Automatic"))
        self.threshold_auto.setChecked(threshold is None)
        form.addRow(self.threshold_auto)
        self.threshold = self._volts(suggested if threshold is None else threshold, THRESHOLD_STEP)
        self.threshold.setEnabled(threshold is not None)
        self.threshold_auto.toggled.connect(self.threshold.setDisabled)
        form.addRow(tr("Level"), self.threshold)
        return group

    def _scale_group(self, limits, manual_limits):
        low, high = limits
        group = QtWidgets.QGroupBox(tr("Scale"))
        form = QtWidgets.QFormLayout(group)
        self.scale_auto = QtWidgets.QCheckBox(tr("Automatic"))
        self.scale_auto.setToolTip(tr("Fits the scale to the whole signal and keeps it until the next read."))
        self.scale_auto.setChecked(not manual_limits)
        form.addRow(self.scale_auto)
        self.high = self._volts(high, (high - low) / SCALE_STEPS)
        self.low = self._volts(low, (high - low) / SCALE_STEPS)
        form.addRow(tr("Top"), self.high)
        form.addRow(tr("Bottom"), self.low)
        for box in (self.high, self.low):
            box.setEnabled(manual_limits)
            self.scale_auto.toggled.connect(box.setDisabled)
            box.valueChanged.connect(self._limits_changed)
        self.scale_auto.toggled.connect(self._limits_changed)
        return group

    @staticmethod
    def _volts(value, step):
        box = QtWidgets.QDoubleSpinBox()
        box.setRange(-VOLTS_LIMIT, VOLTS_LIMIT)
        box.setDecimals(VOLTS_DECIMALS)
        box.setSingleStep(step)
        box.setSuffix(" V")
        box.setValue(value)
        return box

    def _limits_changed(self, *_args):
        self.apply_button.setEnabled(self.scale_auto.isChecked() or self.low.value() < self.high.value())

    def condition(self) -> str:
        return next(code for code, button in self.trigger_codes.items() if button.isChecked())

    def accept(self):
        threshold = None if self.threshold is None or self.threshold_auto.isChecked() else self.threshold.value()
        limits = None if self.high is None or self.scale_auto.isChecked() else (self.low.value(), self.high.value())
        self.applied.emit(self.condition(), threshold, limits)
        super().accept()

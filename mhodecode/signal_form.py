"""The form describing one signal for the AFG outputs: protocol, data, lines, levels and load."""
from __future__ import annotations

from PyQt5 import QtCore, QtWidgets

from . import generator
from .decoder_panel import clear_layout, compact, param_widget, set_widget_value, widget_value
from .i18n import tr

DATA_FIELD_TIP = "Hex: 0x48 0x65 or 48 65. ASCII: \"Hello\" or Hello."
DATA_FORMATS = (("Hex", "hex"), ("ASCII", "ascii"))


class SignalForm(QtWidgets.QWidget):
    changed = QtCore.pyqtSignal()  # anything that makes build() give another signal

    def __init__(self, owner):
        super().__init__()
        self.owner = owner
        self.param_widgets: dict[str, QtWidgets.QWidget] = {}
        self.line_combos: dict[str, QtWidgets.QComboBox] = {}
        self.levels: dict[int, tuple[QtWidgets.QDoubleSpinBox, QtWidgets.QDoubleSpinBox]] = {}
        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        form = QtWidgets.QFormLayout()
        self.protocol = QtWidgets.QComboBox()
        self.protocol.addItems(list(generator.PROTOCOLS))
        self.protocol.currentIndexChanged.connect(self._protocol_changed)
        self.data_format = QtWidgets.QComboBox()
        owner._fill_combo(self.data_format, DATA_FORMATS)
        self.data_format.currentIndexChanged.connect(self._format_changed)
        self.data = QtWidgets.QLineEdit("0x48 0x65 0x6C 0x6C 0x6F")
        owner._bind(self.data.setToolTip, DATA_FIELD_TIP)
        self.data.textChanged.connect(self.changed)
        self.format_shown = "hex"
        form.addRow(owner._label("Protocol"), self.protocol)
        form.addRow(owner._label("Data format"), self.data_format)
        form.addRow(owner._label("Data"), self.data)
        layout.addLayout(form)
        self.params_holder = QtWidgets.QVBoxLayout()
        layout.addLayout(self.params_holder)
        self.line_grid = QtWidgets.QGridLayout()
        self.line_grid.setColumnStretch(1, 1)
        layout.addLayout(self.line_grid)

        levels = QtWidgets.QGridLayout()
        levels.addWidget(owner._label("Low level"), 0, 1)
        levels.addWidget(owner._label("High level"), 0, 2)
        for row, afg in enumerate(generator.OUTPUTS, start=1):
            levels.addWidget(QtWidgets.QLabel(f"AFG{afg}"), row, 0)
            low, high = QtWidgets.QDoubleSpinBox(), QtWidgets.QDoubleSpinBox()
            for box in (low, high):
                box.setRange(-10, 10)
                box.setDecimals(3)
                box.setSingleStep(0.1)
                box.setSuffix(" V")
                box.valueChanged.connect(self.changed)
            levels.addWidget(low, row, 1)
            levels.addWidget(high, row, 2)
            self.levels[afg] = (low, high)
        layout.addLayout(levels)
        load_form = QtWidgets.QFormLayout()
        self.load = QtWidgets.QComboBox()
        owner._fill_combo(self.load, generator.LOADS)
        self.load.currentIndexChanged.connect(self.changed)
        load_form.addRow(owner._label("Output load"), self.load)
        layout.addLayout(load_form)
        self._protocol_changed()

    def _protocol_changed(self):
        self._rebuild_params()
        self._rebuild_lines()
        self.changed.emit()

    def _rebuild_params(self):
        clear_layout(self.params_holder)
        self.param_widgets = {}
        holder = QtWidgets.QWidget()
        form = QtWidgets.QFormLayout(holder)
        form.setContentsMargins(0, 0, 0, 0)
        for param in generator.PROTOCOL_PARAMS[self.protocol.currentText()]:
            widget = param_widget(param)
            self.param_widgets[param.key] = widget
            form.addRow(tr(param.label), widget)
            if param.key == "differential":
                widget.currentIndexChanged.connect(self._rebuild_lines)
            if isinstance(widget, QtWidgets.QComboBox):
                widget.currentIndexChanged.connect(self.changed)
            else:
                widget.valueChanged.connect(self.changed)
        self.params_holder.addWidget(holder)

    def params(self) -> dict:
        return {key: widget_value(widget) for key, widget in self.param_widgets.items()}

    def _rebuild_lines(self):
        """One row per signal line of the protocol, each picking the AFG output that carries it."""
        clear_layout(self.line_grid)
        self.line_combos = {}
        params = self.params()
        lines = generator.lines_of(self.protocol.currentText(), params)
        # An output with no line yet still gets usable levels, since a line can be moved to it.
        for afg in generator.OUTPUTS:
            low, high = generator.default_levels(lines[0][0], params)
            self.levels[afg][0].setValue(low)
            self.levels[afg][1].setValue(high)
        for row, (key, label) in enumerate(lines):
            combo = QtWidgets.QComboBox()
            compact(combo)
            combo.addItem(tr("not used"), None)
            for afg in generator.OUTPUTS:
                combo.addItem(f"AFG{afg}", afg)
            combo.setCurrentIndex(combo.findData(generator.OUTPUTS[row]))
            combo.currentIndexChanged.connect(self.changed)
            self.line_grid.addWidget(QtWidgets.QLabel(tr("Line {label}", label=label)), row, 0)
            self.line_grid.addWidget(combo, row, 1)
            self.line_combos[key] = combo
            low, high = generator.default_levels(key, params)
            self.levels[generator.OUTPUTS[row]][0].setValue(low)
            self.levels[generator.OUTPUTS[row]][1].setValue(high)

    def _format_changed(self):
        """Keep the data when switching between hex and ASCII."""
        new_format = self.data_format.currentData()
        try:
            data = generator.parse_data(self.data.text(), self.format_shown)
            self.data.setText(generator.format_data(data, new_format))
        except ValueError:
            self.data.clear()
        self.format_shown = new_format

    def current_levels(self) -> dict[int, tuple[float, float]]:
        return {afg: (low.value(), high.value()) for afg, (low, high) in self.levels.items()}

    def build(self) -> generator.Generated:
        protocol = self.protocol.currentText()
        data = generator.parse_data(self.data.text(), self.data_format.currentData())
        generated = generator.build(protocol, data, self.params())
        outputs = {key: combo.currentData() for key, combo in self.line_combos.items()}
        generator.assign(generated, outputs, self.current_levels(), self.load.currentData())
        return generated

    def retranslate(self):
        """The rows in words are made anew, with what was set in them."""
        params = self.params()
        lines = {key: combo.currentData() for key, combo in self.line_combos.items()}
        levels = self.current_levels()
        self.blockSignals(True)
        self._rebuild_params()
        for key, value in params.items():
            set_widget_value(self.param_widgets[key], value)
        self._rebuild_lines()
        for key, value in lines.items():
            if key in self.line_combos:
                combo = self.line_combos[key]
                combo.setCurrentIndex(max(0, combo.findData(value)))
        for afg, (low, high) in levels.items():
            self.levels[afg][0].setValue(low)
            self.levels[afg][1].setValue(high)
        self.blockSignals(False)
        self.changed.emit()

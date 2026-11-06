"""The decoder selector and the decoder's settings, built the way PulseView (GPLv3, sigrok.org) arranges them.

From PulseView: the searchable selector tree with an information box under it (pv/subwindows/
decoder_selector), one independent decoder trace after another, its settings opening from the trace's
label (pv/views/trace/decodetrace.cpp), each with its own channels and options, a group per decoder of
a stack with show/hide and delete (pv/widgets/decodergroupbox.cpp), a star on required channels, a Stack
Decoder button listing what fits on top, a menu to hide single rows, and decoding that restarts by itself
after each change. Here the settings are a window of their own, not a popup, as asked for this program.
"""
from __future__ import annotations

from PyQt5 import QtCore, QtWidgets

from .decoders import SIGROK
from .decoders.sigrok import option_param
from .i18n import tr
from .scope import DIGITAL_CHANNELS, digital_channel, is_digital
from .srd_loader import stackable_on

CHANNELS = (1, 2, 3, 4)
COMBO_MIN_CHARS = 8
DECODER_MIME = "application/x-mhodecode-decoder"
TREE_MIN_HEIGHT = 210
SORT_CATEGORY, SORT_PARENT = "category", "parent"
WINDOW_WIDTH = 460
WINDOW_MAX_HEIGHT = 720
WINDOW_EXTRA_HEIGHT = 40  # margins of the window around the form
RATE_MINIMUM = 1
RATE_MAXIMUM = 2_000_000_000
CUSTOM_RATE = None  # the list's entry for a rate typed by hand
# Upstream tags, grouped so that the long list reads like the menus of an analyzer.
DECODER_GROUPS = (
    ("Magistrale i interfejsy", (("Embedded/industrial", "Wbudowane i przemysłowe"), ("PC", "Komputery PC"),
                                 ("Networking", "Sieci"), ("Automotive", "Motoryzacja"))),
    ("Układy scalone", (("IC", "Układy scalone"), ("Memory", "Pamięci"), ("Sensor", "Czujniki"), ("Display", "Wyświetlacze"),
                        ("Analog/digital", "Przetworniki"), ("Clock/timing", "Zegary i czas"))),
    ("Radio i podczerwień", (("Wireless/RF", "Radio"), ("RFID", "RFID"), ("IR", "Podczerwień"), ("Remote Control", "Piloty"))),
    ("Narzędzia", (("Debug/trace", "Debugowanie"), ("Util", "Pomocnicze"), ("Encoding", "Kodowania"),
                   ("Security/crypto", "Bezpieczeństwo"))),
    ("Pozostałe", (("Audio", "Audio"), ("Lighting", "Oświetlenie"), ("Retro computing", "Retro"))),
)


def compact(combo):
    """Let a combo box get narrower than its longest item, so the side panel fits its width."""
    combo.setSizeAdjustPolicy(QtWidgets.QComboBox.AdjustToMinimumContentsLengthWithIcon)
    combo.setMinimumContentsLength(COMBO_MIN_CHARS)


def format_rate(rate):
    for factor, prefix in ((1_000_000, "M"), (1000, "k")):
        if rate % factor == 0:
            return f"{rate // factor} {prefix}bit/s"
    return f"{rate} bit/s"


class RateBox(QtWidgets.QWidget):
    """Typical rates of a protocol on a list. Its last entry opens a field for any other rate."""
    valueChanged = QtCore.pyqtSignal(int)

    def __init__(self, rates, default):
        super().__init__()
        layout = QtWidgets.QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self.list = QtWidgets.QComboBox()
        compact(self.list)
        self.list.setMaxVisibleItems(len(rates) + 1)
        for rate in rates:
            self.list.addItem(format_rate(rate), rate)
        self.list.addItem(tr("Własna…"), CUSTOM_RATE)
        self.custom = QtWidgets.QSpinBox()
        self.custom.setRange(RATE_MINIMUM, RATE_MAXIMUM)
        self.custom.setSuffix(" bit/s")
        self.custom.setKeyboardTracking(False)
        layout.addWidget(self.list, 1)
        layout.addWidget(self.custom, 1)
        self.setValue(default)
        self.list.activated.connect(self._picked)
        self.custom.valueChanged.connect(self.valueChanged)

    def _picked(self, _index):
        rate = self.list.currentData()
        self.custom.setVisible(rate == CUSTOM_RATE)
        if rate == CUSTOM_RATE:
            self.custom.setFocus()
        else:
            self.custom.setValue(rate)

    def value(self):
        return self.custom.value()

    def setValue(self, rate):
        position = self.list.findData(int(rate))
        self.list.setCurrentIndex(position if position >= 0 else self.list.count() - 1)
        self.custom.setVisible(position < 0)
        self.custom.setValue(int(rate))

    def setKeyboardTracking(self, tracking):
        self.custom.setKeyboardTracking(tracking)


def param_widget(param):
    if param.rates:
        widget = RateBox(param.rates, param.default)
    elif param.choices:
        widget = QtWidgets.QComboBox()
        compact(widget)
        for value, label in param.choices:
            widget.addItem(tr(label), value)
        widget.setCurrentIndex(max(0, widget.findData(param.default)))
    elif isinstance(param.default, str):
        widget = QtWidgets.QLineEdit(param.default)
    elif isinstance(param.default, int):
        widget = QtWidgets.QSpinBox()
        widget.setRange(int(param.minimum), int(min(param.maximum, 2_000_000_000)))
        if param.display_hex:
            widget.setDisplayIntegerBase(16)
            widget.setPrefix("0x")
        widget.setValue(param.default)
    else:
        widget = QtWidgets.QDoubleSpinBox()
        widget.setDecimals(param.decimals)
        widget.setRange(param.minimum, param.maximum)
        widget.setValue(param.default)
    if param.hint:
        widget.setToolTip(tr(param.hint))
    return widget


def widget_value(widget):
    if isinstance(widget, QtWidgets.QLineEdit):
        return widget.text()
    return widget.currentData() if isinstance(widget, QtWidgets.QComboBox) else widget.value()


def set_widget_value(widget, value):
    if isinstance(widget, QtWidgets.QLineEdit):
        widget.setText(value)
    elif isinstance(widget, QtWidgets.QComboBox):
        widget.setCurrentIndex(max(0, widget.findData(value)))
    else:
        widget.setValue(value)


def notify_on_change(widget, slot):
    """Call slot after a finished edit of the widget, whatever kind it is."""
    if isinstance(widget, QtWidgets.QComboBox):
        widget.currentIndexChanged.connect(slot)
    elif isinstance(widget, QtWidgets.QLineEdit):
        widget.editingFinished.connect(slot)
    elif isinstance(widget, QtWidgets.QCheckBox):
        widget.toggled.connect(slot)
    else:
        widget.setKeyboardTracking(False)
        widget.valueChanged.connect(slot)


def option_form():
    """A form whose long labels wrap, because decoder options can have long descriptions."""
    form = QtWidgets.QFormLayout()
    form.setRowWrapPolicy(QtWidgets.QFormLayout.WrapLongRows)
    return form


def add_option_row(form, text, widget):
    label = QtWidgets.QLabel(text)
    label.setWordWrap(True)
    form.addRow(label, widget)


def clear_layout(layout):
    while layout.count():
        item = layout.takeAt(0)
        widget = item.widget()
        if widget:
            widget.setParent(None)
            widget.deleteLater()
        elif item.layout():
            clear_layout(item.layout())


def all_channels():
    return (*CHANNELS, *(digital_channel(number) for number in range(DIGITAL_CHANNELS)))


class SignalRow:
    """Widgets assigning one decoder signal to a channel and a threshold."""

    def __init__(self, grid, row, spec, default_channel):
        self.spec = spec
        label = tr(spec.label) + (" *" if spec.required else "")
        self.widgets = [QtWidgets.QLabel(label)]
        self.channel = QtWidgets.QComboBox()
        compact(self.channel)
        if not spec.required:
            self.channel.addItem("—", None)
        for channel in CHANNELS:
            self.channel.addItem(f"CH{channel}", channel)
        for number in range(DIGITAL_CHANNELS):
            self.channel.addItem(f"D{number}", digital_channel(number))
        self.channel.setMaxVisibleItems(21)
        if default_channel:
            self.channel.setCurrentIndex(self.channel.findData(default_channel))
        self.auto = QtWidgets.QCheckBox(tr("próg auto"))
        self.auto.setToolTip(tr("Program sam dobiera próg logiczny z przebiegu. Po odznaczeniu wpisujesz go ręcznie."))
        self.auto.setChecked(True)
        self.level = QtWidgets.QDoubleSpinBox()
        self.level.setRange(-500, 500)
        self.level.setDecimals(3)
        self.level.setSuffix(" V")
        self.level.setValue(1.65)
        self.level.setVisible(False)
        self.auto.toggled.connect(lambda _checked: self.show_threshold())
        self.channel.currentIndexChanged.connect(lambda _index: self.show_threshold())
        self.widgets += [self.channel, self.auto, self.level]
        for column, widget in enumerate(self.widgets):
            grid.addWidget(widget, row, column)
        self.show_threshold()

    def show_threshold(self):
        """A digital channel has its threshold on the scope, set per group in the scope tab."""
        channel = self.channel.currentData()
        digital = channel is not None and is_digital(channel)
        self.auto.setVisible(not digital)
        self.level.setVisible(not digital and not self.auto.isChecked())


class StackEntry:
    """One decoder stacked on the base decoder, with its option widgets."""

    def __init__(self, pd_id, group, widgets):
        self.pd_id = pd_id
        self.group = group
        self.widgets = widgets

    def options(self):
        return {key: widget_value(widget) for key, widget in self.widgets.items()}


class DecoderTree(QtWidgets.QTreeWidget):
    """All decoders, by category or under the protocol they need. An item can be dragged onto the waveforms."""

    def __init__(self):
        super().__init__()
        self.setHeaderHidden(True)
        self.setDragEnabled(True)
        self.setDragDropMode(QtWidgets.QAbstractItemView.DragOnly)
        self.setMinimumHeight(TREE_MIN_HEIGHT)
        self.fits_group = None
        self.sort_mode = SORT_CATEGORY

    def mimeData(self, items):
        data = QtCore.QMimeData()
        data.setData(DECODER_MIME, items[0].data(0, QtCore.Qt.UserRole).encode("ascii"))
        return data

    def mimeTypes(self):
        return [DECODER_MIME]

    def _decoder_item(self, parent, decoder_id, name, tip):
        item = QtWidgets.QTreeWidgetItem(parent, [name])
        item.setData(0, QtCore.Qt.UserRole, decoder_id)
        item.setToolTip(0, tip)
        item.setFlags(QtCore.Qt.ItemIsEnabled | QtCore.Qt.ItemIsSelectable | QtCore.Qt.ItemIsDragEnabled)
        return item

    def _group_item(self, parent, title):
        item = QtWidgets.QTreeWidgetItem(parent, [title])
        item.setFlags(QtCore.Qt.ItemIsEnabled)
        return item

    def rebuild(self, stack_top):
        """The list of what fits the active decoder comes first and changes with it."""
        self.clear()
        self.fits_group = self._group_item(self, tr("Pasuje do stosu"))
        self.show_fitting(stack_top)
        if self.sort_mode == SORT_PARENT:
            self._fill_by_parent()
        else:
            self._fill_by_category()
        self.fits_group.setExpanded(True)

    def _fill_by_parent(self):
        """Each decoder under every protocol it can read, for example i2cdemux under I2C."""
        roots = sorted((info for info in SIGROK.values() if not info.needs_base), key=lambda info: info.name.lower())
        for info in roots:
            self._add_with_children(self, info, {info.id})

    def _add_with_children(self, parent, info, path):
        item = self._decoder_item(parent, info.id, info.name, f"{info.longname}. {info.desc}")
        for upper in stackable_on(info.id, SIGROK):
            if upper.id not in path:  # some decoders read and write the same kind of data
                self._add_with_children(item, upper, path | {upper.id})

    def _fill_by_category(self):
        for title, tags in DECODER_GROUPS:
            group = self._group_item(self, tr(title))
            for tag, label in tags:
                members = sorted((info for info in SIGROK.values() if tag in info.tags), key=lambda info: info.name.lower())
                if members:
                    subgroup = self._group_item(group, f"{tr(label)} ({len(members)})")
                    for info in members:
                        self._decoder_item(subgroup, info.id, info.name, f"{info.longname}. {info.desc}")
        untagged = sorted((info for info in SIGROK.values() if not info.tags), key=lambda info: info.name.lower())
        if untagged:
            group = self._group_item(self, tr("Bez kategorii"))
            for info in untagged:
                self._decoder_item(group, info.id, info.name, f"{info.longname}. {info.desc}")

    def show_fitting(self, stack_top):
        for child in self.fits_group.takeChildren():
            del child
        self.fits_group.setText(0, tr("Pasuje do stosu"))
        if stack_top is not None:
            self.fits_group.setText(0, f"{tr('Pasuje do stosu')}: {SIGROK[stack_top].name}")
            for info in stackable_on(stack_top, SIGROK):
                self._decoder_item(self.fits_group, info.id, info.name, f"{info.longname}. {info.desc}")
        self.fits_group.setHidden(self.fits_group.childCount() == 0)

    def filter_items(self, text):
        wanted = text.strip().lower()

        def walk(item):
            decoder_id = item.data(0, QtCore.Qt.UserRole)
            matches = False
            if decoder_id:
                info = SIGROK[decoder_id]
                matches = wanted in " ".join((info.id, info.name, info.longname, info.desc, *info.tags)).lower()
            below = False
            for position in range(item.childCount()):
                below = walk(item.child(position)) or below
            item.setHidden(not (matches or below))
            if wanted:
                item.setExpanded(below)
            return matches or below

        for position in range(self.topLevelItemCount()):
            walk(self.topLevelItem(position))


def dragged_decoder(event):
    data = event.mimeData()
    return bytes(data.data(DECODER_MIME)).decode("ascii") if data.hasFormat(DECODER_MIME) else None


class DecoderDropTarget:
    """Takes decoders dragged from the tree. Mixed into widgets of any kind."""
    decoder_dropped = None  # callable receiving the decoder's id

    def dragEnterEvent(self, event):
        if dragged_decoder(event):
            event.acceptProposedAction()

    def dragMoveEvent(self, event):
        if dragged_decoder(event):
            event.acceptProposedAction()

    def dropEvent(self, event):
        decoder_id = dragged_decoder(event)
        if decoder_id and self.decoder_dropped:
            event.acceptProposedAction()
            self.decoder_dropped(decoder_id)


class DecoderCard(DecoderDropTarget, QtWidgets.QFrame):
    """One decoder in use, with whatever is stacked on it. Any number of them work side by side,
    the same decoder several times too, each on its own channels and with its own options."""
    changed = QtCore.pyqtSignal()
    visibility_changed = QtCore.pyqtSignal()
    remove_requested = QtCore.pyqtSignal(object)
    activated = QtCore.pyqtSignal(object)

    def __init__(self, decoder, uid, default_channels):
        super().__init__()
        self.decoder = decoder
        self.uid = uid
        self.number = 1
        self.building = False
        self.hidden_rows: set[str] = set()
        self.known_rows: dict[str, str] = {}
        self.signal_rows: list[SignalRow] = []
        self.param_widgets: dict[str, QtWidgets.QWidget] = {}
        self.stack_entries: list[StackEntry] = []
        self.problem = ""  # why it gives nothing, also painted on its trace
        self.config_window = None
        self.setAcceptDrops(True)
        self.outer = QtWidgets.QVBoxLayout(self)
        self.outer.setContentsMargins(6, 4, 6, 6)
        self.header = QtWidgets.QHBoxLayout()
        self.outer.addLayout(self.header)
        self.body = QtWidgets.QWidget()
        self.body_layout = QtWidgets.QVBoxLayout(self.body)
        self.body_layout.setContentsMargins(0, 0, 0, 0)
        self.outer.addWidget(self.body)
        self.outer.addStretch(1)  # the form stays together at the top of a taller window
        self.building = True
        self._build(default_channels)
        self.building = False

    # ---- naming ---------------------------------------------------------
    @property
    def base_name(self) -> str:
        return self.decoder.name

    @property
    def caption(self) -> str:
        return self.base_name if self.number == 1 else f"{self.base_name} #{self.number}"

    def set_number(self, number):
        self.number = number
        self.name_label.setText(f"<b>{self.caption}</b>")
        if self.config_window is not None:
            self.config_window.setWindowTitle(self.caption)

    # ---- layout ---------------------------------------------------------
    def _build(self, default_channels):
        self.name_label = QtWidgets.QLabel(f"<b>{self.caption}</b>")
        self.name_label.setToolTip(f"{self.decoder.info.longname}:\n{self.decoder.info.desc}")
        self.shown = QtWidgets.QCheckBox(tr("Widoczny"))
        self.shown.setToolTip(tr("Pokaż lub ukryj wiersze tego dekodera"))
        self.shown.setChecked(True)
        self.shown.toggled.connect(lambda _checked: self.visibility_changed.emit())
        self.delete_button = QtWidgets.QPushButton(tr("Usuń dekoder"))
        self.delete_button.setObjectName("danger")
        self.delete_button.setToolTip(tr("Usuń ten dekoder"))
        self.delete_button.clicked.connect(lambda: self.remove_requested.emit(self))
        self.header.addWidget(self.name_label)
        self.header.addStretch(1)
        self.header.addWidget(self.shown)
        self.header.addWidget(self.delete_button)

        self.status = QtWidgets.QLabel()
        self.status.setWordWrap(True)
        self.status.hide()
        self.body_layout.addWidget(self.status)
        grid = QtWidgets.QGridLayout()
        grid.setColumnStretch(1, 1)
        self.body_layout.addLayout(grid)
        defaults = iter(default_channels)
        any_required = any(spec.required for spec in self.decoder.signals)
        for row, spec in enumerate(self.decoder.signals):
            # A decoder with optional signals only, as UART, still needs one to give anything.
            channel = next(defaults, None) if spec.required or (not any_required and row == 0) else None
            signal_row = SignalRow(grid, row, spec, channel)
            self.signal_rows.append(signal_row)
            notify_on_change(signal_row.channel, self._edited)
            notify_on_change(signal_row.auto, self._edited)
            notify_on_change(signal_row.level, self._edited)
        form = option_form()
        self.body_layout.addLayout(form)
        for param in self.decoder.params:
            widget = param_widget(param)
            self.param_widgets[param.key] = widget
            notify_on_change(widget, self._edited)
            add_option_row(form, tr(param.label), widget)
        self.stack_holder = QtWidgets.QVBoxLayout()
        self.body_layout.addLayout(self.stack_holder)
        footer = QtWidgets.QHBoxLayout()
        self.rows_button = QtWidgets.QToolButton()
        self.rows_button.setText(tr("Wiersze"))
        self.rows_button.setToolTip(tr("Wybiera, które wiersze tego dekodera są widoczne"))
        self.rows_button.setPopupMode(QtWidgets.QToolButton.InstantPopup)
        self.rows_menu = QtWidgets.QMenu(self)
        self.rows_menu.aboutToShow.connect(self._fill_rows_menu)
        self.rows_button.setMenu(self.rows_menu)
        footer.addWidget(self.rows_button)
        footer.addStretch(1)
        self.stack_button = QtWidgets.QToolButton()
        self.stack_button.setText(tr("Dodaj na stos"))
        self.stack_button.setToolTip(tr("Dołóż na ten dekoder dekoder wyższego poziomu"))
        self.stack_button.setPopupMode(QtWidgets.QToolButton.InstantPopup)
        self.stack_menu = QtWidgets.QMenu(self)
        self.stack_menu.aboutToShow.connect(self._fill_stack_menu)
        self.stack_button.setMenu(self.stack_menu)
        footer.addWidget(self.stack_button)
        self.body_layout.addLayout(footer)

    def mousePressEvent(self, event):
        self.activated.emit(self)
        super().mousePressEvent(event)

    def _edited(self, *_args):
        if not self.building:
            self.changed.emit()

    def _fill_rows_menu(self):
        self.rows_menu.clear()
        for key, label in self.known_rows.items():
            action = self.rows_menu.addAction(label)
            action.setCheckable(True)
            action.setChecked(key not in self.hidden_rows)
            action.toggled.connect(lambda checked, key=key: self.set_row_shown(key, checked))

    def set_row_shown(self, key, shown):
        if shown:
            self.hidden_rows.discard(key)
        else:
            self.hidden_rows.add(key)
        self.visibility_changed.emit()

    def _fill_stack_menu(self):
        self.stack_menu.clear()
        for info in stackable_on(self.stack_top(), SIGROK):
            action = self.stack_menu.addAction(info.name)
            action.setToolTip(f"{info.longname}. {info.desc}")
            action.triggered.connect(lambda _checked, pd_id=info.id: self.push(pd_id))

    def set_status(self, text="", color=None):
        self.status.setText(text)
        self.status.setStyleSheet(f"color: {color};" if color else "")
        self.status.setVisible(bool(text))

    # ---- stack ----------------------------------------------------------
    def stack_top(self):
        return self.stack_entries[-1].pd_id if self.stack_entries else self.decoder.info.id

    def fits(self, pd_id) -> bool:
        return any(info.id == pd_id for info in stackable_on(self.stack_top(), SIGROK))

    def push(self, pd_id, values=None):
        """Put a decoder on top of the stack."""
        info = SIGROK[pd_id]
        group = QtWidgets.QGroupBox(f"{len(self.stack_entries) + 2}. {info.name}")
        group.setToolTip(f"{info.longname}:\n{info.desc}")
        form = option_form()
        group.setLayout(form)
        widgets = {}
        for option in info.options:
            param = option_param(option, pd_id)
            widget = param_widget(param)
            if isinstance(widget, QtWidgets.QComboBox):
                compact(widget)
            if values and param.key in values:
                set_widget_value(widget, values[param.key])
            widgets[param.key] = widget
            notify_on_change(widget, self._edited)
            add_option_row(form, param.label, widget)
        remove = QtWidgets.QPushButton(tr("Usuń ze stosu (też dekodery powyżej)"))
        remove.setToolTip(tr("Dekodery powyżej biorą dane z tego, więc znikają razem z nim."))
        position = len(self.stack_entries)
        remove.clicked.connect(lambda: self.pop(position))
        form.addRow(remove)
        self.stack_holder.addWidget(group)
        self.stack_entries.append(StackEntry(pd_id, group, widgets))
        self._edited()

    def pop(self, position):
        """A decoder takes its input from the one below, so the ones above it have to go as well."""
        for entry in self.stack_entries[position:]:
            entry.group.setParent(None)
            entry.group.deleteLater()
        del self.stack_entries[position:]
        self._edited()

    # ---- values ---------------------------------------------------------
    def mapping(self) -> dict[str, int]:
        return {row.spec.key: row.channel.currentData() for row in self.signal_rows if row.channel.currentData() is not None}

    def params(self) -> dict:
        params = {key: widget_value(widget) for key, widget in self.param_widgets.items()}
        params["srd_stack"] = [(entry.pd_id, entry.options()) for entry in self.stack_entries]
        return params

    def chain(self) -> list[str]:
        return [self.decoder.info.id] + [entry.pd_id for entry in self.stack_entries]

    def visible_row_keys(self) -> list[str]:
        return [key for key in self.known_rows if key not in self.hidden_rows] if self.shown.isChecked() else []

    def retranslate(self):
        """Forms are built with translated text, so they are built again with the values kept."""
        channels = [(row.channel.currentData(), row.auto.isChecked(), row.level.value()) for row in self.signal_rows]
        params = {key: widget_value(widget) for key, widget in self.param_widgets.items()}
        stack = [(entry.pd_id, entry.options()) for entry in self.stack_entries]
        shown = self.shown.isChecked()
        self.building = True
        clear_layout(self.header)
        clear_layout(self.body_layout)
        self.rows_menu.deleteLater()
        self.stack_menu.deleteLater()
        self.signal_rows, self.param_widgets, self.stack_entries = [], {}, []
        self._build(())
        self.shown.setChecked(shown)
        for row, (channel, auto, level) in zip(self.signal_rows, channels):
            row.channel.setCurrentIndex(max(0, row.channel.findData(channel)))
            row.auto.setChecked(auto)
            row.level.setValue(level)
        for key, value in params.items():
            set_widget_value(self.param_widgets[key], value)
        for pd_id, options in stack:
            self.push(pd_id, options)
        self.building = False


class DecoderWindow(QtWidgets.QWidget):
    """A decoder's settings in a window of its own. It stays hidden until the decoder's trace is clicked."""

    def __init__(self, parent, card):
        super().__init__(parent, QtCore.Qt.Window)
        self.card = card
        card.config_window = self
        self.resize(WINDOW_WIDTH, WINDOW_MAX_HEIGHT)
        layout = QtWidgets.QVBoxLayout(self)
        scroll = QtWidgets.QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QtWidgets.QFrame.NoFrame)
        scroll.setHorizontalScrollBarPolicy(QtCore.Qt.ScrollBarAlwaysOff)
        scroll.setWidget(card)
        layout.addWidget(scroll, 1)
        self.note = QtWidgets.QLabel()
        layout.addWidget(self.note)
        self.retranslate()

    def retranslate(self):
        self.note.setText(f"<i>{tr('* Wymagane kanały')}</i>")

    def open(self):
        if not self.isVisible():
            self.card.adjustSize()
            needed = self.card.sizeHint().height() + self.note.sizeHint().height() + WINDOW_EXTRA_HEIGHT
            self.resize(WINDOW_WIDTH, min(needed, WINDOW_MAX_HEIGHT))
        self.show()
        self.raise_()
        self.activateWindow()



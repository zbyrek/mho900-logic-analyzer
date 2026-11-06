"""Main window of the MHO900 protocol decoder."""
from __future__ import annotations

import csv
import re
import time
from bisect import bisect_left, bisect_right
from dataclasses import replace

import numpy as np
import pyqtgraph as pg
from PyQt5 import QtCore, QtGui, QtWidgets
from pyqtgraph.util.garbage_collector import GarbageCollector

from . import __version__, abort, generator, rigol_files, segments, trigger
from .decoder_panel import (SORT_CATEGORY, SORT_PARENT, DecoderCard, DecoderDropTarget, DecoderTree, DecoderWindow, all_channels, clear_layout, compact,
                            dragged_decoder, param_widget, set_widget_value, widget_value)
from .decoders import DECODERS, SIGROK, SIGROK_ROOTS
from .decoders.base import DecodeResult
from .i18n import LANGUAGES, language, set_language, tr
from .channel_controls import ChannelDialog, ScopeSettings
from .cursors import CursorOverlay, CursorStrip, CursorView, LineMark, MarkOverlay, MarkView, span_text
from .scope import DEFAULT_FORMAT, CapturePlan, Scope, ScopeError, Waveform, channel_name, digital_channel, is_digital
from .scope_panel import ScopePanel
from .signal import Thresholds, auto_thresholds, digitize, manual_thresholds
from .srd_loader import base_chain
from .theme import DEFAULT_THEME, THEMES, apply_palette, window_style

DEFAULT_HOST = "192.0.2.1"
CHANNELS = (1, 2, 3, 4)
FILE_FORMAT = "FILE"  # the read format that goes through the memory file instead of :WAVeform:DATA?
DIGITAL_LEVELS = Thresholds(0.35, 0.65)  # digital samples are exactly 0 or 1
DISPLAY_MODES = (("Logic", "logic"), ("Analog", "analog"))
THEME_NAMES = (("Light", "light"), ("Dark", "dark"))
MAX_TEXT_ITEMS = 120
TRIGGER_LINE_LABEL = "Trig"
TRIGGER_LINE_WIDTH = 2
VERTICAL_LINE_Z = 50  # above the traces
TRIGGER_TIMES_KEY = "trigger_times"  # in a saved file; a file without it says nothing of the trigger
PANEL_WIDTH = 540
RESULTS_HEIGHT = 240
DATA_COLUMN = 4
TABLE_COLUMN_WIDTHS = (125, 110, 50, 150, 200, 170)
DECODE_DELAY_MS = 200  # changes coming quickly after one another give one decoding
# Heights in lines of text. A logic trace needs little room, and what is left goes to the decoder rows.
LOGIC_ROW_LINES = 2
ANALOG_ROW_LINES = 5
LANE_ROW_LINES = 3
TIME_AXIS_LINES = 3
NO_DATA_RANGE = (0.0, 1.0)  # the time axis of traces with nothing captured yet
NO_DATA_STEP = 1e-3
LANE_NOTE_MARGIN = 6
WIDE_AXIS_WIDTH = 190  # room for the names of annotation rows
ROW_LABEL_CHARS = 22
ROW_TABLE_COLUMN_WIDTHS = {2: 170}  # decoder rows have long names
TEXT_MARGIN_PIXELS = 6  # a text needs this much more room than its own width
CURSOR_STRIP_LINES = 2.3
MARK_STRIP_LINES = 1.2
TIME_BAR_STEPS = 1 << 30  # fine enough to show a few samples of the deepest memory; a scroll bar holds 32-bit integers
TIME_BAR_ARROW_STEP = 0.1  # of the visible time, for each click on an arrow
LOGIC_Y_RANGE = (-0.2, 1.2)
ANALOG_Y_MARGIN = 0.05  # of the swing, on both sides
MIN_Y_SPAN_SWINGS = 2  # in the smallest swing of a signal: noise must not fill the row and look like one
DETAIL_ALPHA = 150
BAR_OUTLINE_DARKER = 135
STATUS_TEXT_CHARS = 90
MIN_ZOOM_SAMPLES = 8
ZOOM_STEP = 2.0
FIRST_CURSOR, SECOND_CURSOR = 1 / 3, 2 / 3
HOVERED_CURSOR_WIDTH = 4
DATA_FIELD_TIP = "Hex: 0x48 0x65 or 48 65. ASCII: \"Hello\" or Hello."
LOG_LINES = 500
VERBOSE_LOG_LINES = 20000
SCOPE_TAB = 1
CAPTURE_DIVISIONS = 10
SINGLE_MARGIN_S = 10.0  # added to the capture length when waiting for a single acquisition
DATA_FORMATS = (("Hex", "hex"), ("ASCII", "ascii"))
LOADS = (("High impedance (High-Z)", "OMEG"), ("50 Ω load", "FIFTy"))
SETTINGS = ("MHO900Decoder", "MHO900Decoder")


class Worker(QtCore.QThread):
    """Runs one blocking job off the GUI thread."""
    done = QtCore.pyqtSignal(object)
    failed = QtCore.pyqtSignal(str)
    aborted = QtCore.pyqtSignal()
    progress = QtCore.pyqtSignal(str)

    def __init__(self, job, recover=None, parent=None):
        super().__init__(parent)
        self._job = job
        self._recover = recover

    def run(self):
        try:
            self.done.emit(self._job(self.progress.emit))
        except abort.OperationAborted:
            abort.clear()
            try:
                if self._recover:
                    self._recover()
                self.aborted.emit()
            except ScopeError as exc:
                self.failed.emit(str(exc))
        except (ScopeError, ValueError, OSError, MemoryError) as exc:
            self.failed.emit(str(exc))


class AnnotationModel(QtCore.QAbstractTableModel):
    HEADERS = ("Time [s]", "Length [s]", "Line", "Type", "Data", "Error")

    def __init__(self):
        super().__init__()
        self.rows = []
        self.positions = {}
        self.row_labels = {}  # row key -> what the Line column shows
        self.error_color = QtGui.QColor(200, 30, 30)
        self.control_color = QtGui.QColor(90, 90, 90)
        self.data_font = QtGui.QFont()
        self.data_font.setBold(True)

    def set_rows(self, rows):
        self.beginResetModel()
        self.rows = rows
        self.positions = {id(item): position for position, item in enumerate(rows)}
        self.endResetModel()

    def set_colors(self, error, control):
        self.error_color, self.control_color = QtGui.QColor(error), QtGui.QColor(control)
        self.layoutChanged.emit()

    def retranslate(self):
        self.headerDataChanged.emit(QtCore.Qt.Horizontal, 0, len(self.HEADERS) - 1)

    def rowCount(self, parent=QtCore.QModelIndex()):
        return 0 if parent.isValid() else len(self.rows)

    def columnCount(self, parent=QtCore.QModelIndex()):
        return 0 if parent.isValid() else len(self.HEADERS)

    def headerData(self, section, orientation, role=QtCore.Qt.DisplayRole):
        if role == QtCore.Qt.DisplayRole and orientation == QtCore.Qt.Horizontal:
            return tr(self.HEADERS[section])
        return None

    def data(self, index, role=QtCore.Qt.DisplayRole):
        item = self.rows[index.row()]
        if role == QtCore.Qt.DisplayRole:
            return (f"{item.start:.9f}", f"{item.end - item.start:.3e}", self.row_labels.get(item.row, item.row),
                    item.kind, item.text, item.error)[index.column()]
        if role == QtCore.Qt.ForegroundRole and item.error:
            return QtGui.QBrush(self.error_color)
        if role == QtCore.Qt.ForegroundRole and item.control:
            return QtGui.QBrush(self.control_color)
        if role == QtCore.Qt.FontRole and not item.control and index.column() == DATA_COLUMN:
            return self.data_font
        return None


class DropGraphics(DecoderDropTarget, pg.GraphicsLayoutWidget):
    def __init__(self):
        super().__init__()
        self.setAcceptDrops(True)

    def dropEvent(self, event):
        """The owner also gets the place: a decoder dropped on a trace may go on that trace's stack."""
        decoder_id = dragged_decoder(event)
        if decoder_id and self.decoder_dropped:
            event.acceptProposedAction()
            self.decoder_dropped(decoder_id, self.mapToScene(event.pos()))


class LogWindow(QtWidgets.QWidget):
    """The log in a window of its own, opened from the View menu."""

    def __init__(self, parent):
        super().__init__(parent, QtCore.Qt.Window)
        self.resize(760, 460)
        layout = QtWidgets.QVBoxLayout(self)
        bar = QtWidgets.QHBoxLayout()
        self.verbose = QtWidgets.QCheckBox()
        self.clear_button = QtWidgets.QPushButton()
        bar.addWidget(self.verbose)
        bar.addStretch(1)
        bar.addWidget(self.clear_button)
        layout.addLayout(bar)
        self.view = QtWidgets.QPlainTextEdit()
        self.view.setReadOnly(True)
        self.clear_button.clicked.connect(self.view.clear)
        layout.addWidget(self.view, 1)


class MainWindow(QtWidgets.QMainWindow):
    verbose_line = QtCore.pyqtSignal(str)

    def __init__(self):
        super().__init__()
        # pyqtgraph's advice: collection must stay in this thread, since workers allocate a lot here
        # and Qt objects may not be destroyed from another thread.
        self.garbage_collector = GarbageCollector()
        # Through the default format, so that tests can keep away from the user's real settings.
        self.settings = QtCore.QSettings(QtCore.QSettings.defaultFormat(), QtCore.QSettings.UserScope, *SETTINGS)
        set_language(self.settings.value("language", "en"))
        self.resize(1450, 900)
        self.theme_code = self.settings.value("theme", DEFAULT_THEME)
        if self.theme_code not in THEMES:
            self.theme_code = DEFAULT_THEME
        self.theme = THEMES[self.theme_code]
        self.scope: Scope | None = None
        self.worker: Worker | None = None
        self.job_started = 0.0
        self.job_label = ""
        self.waves: dict[int, Waveform] = {}
        self.result = DecodeResult()
        self.generated: generator.Generated | None = None
        self.last_compare_ok: bool | None = None
        self.spare_rows: list[pg.PlotItem] = []
        self.row_notes: list[QtWidgets.QGraphicsSimpleTextItem] = []
        self.connected_rows: list[pg.PlotItem] = []
        self.cards: list[DecoderCard] = []
        self.active_card: DecoderCard | None = None
        self.next_card_uid = 1
        self.decoding = False
        self.decode_pending = False
        self.gen_param_widgets: dict[str, QtWidgets.QWidget] = {}
        self.gen_line_combos: dict[str, QtWidgets.QComboBox] = {}
        self.lanes: dict[str, pg.PlotItem] = {}
        self.lane_text: dict[str, list] = {}
        self.lane_items: dict[str, dict[bool, list]] = {}
        self.lane_colors: dict[str, str] = {}
        self.plots: list[pg.PlotItem] = []
        self.channel_rows: dict[int, pg.PlotItem] = {}
        self.analog_rows: dict[int, pg.PlotItem] = {}
        self.signal_known: dict[int, bool] = {}  # per capture, since finding out takes long on a big one
        self.memory_read = False  # a capture or a file came in, so it is known which channels are empty
        self.absent_channels: set[int] = set()  # asked of the scope in the last read, but not in its memory
        self.auto_y_ranges: dict[int, tuple[float, float]] = {}  # fitted once per capture
        self.manual_y_ranges: dict[int, tuple[float, float]] = {}  # stay until set back to automatic
        self.full_range = (0.0, 1.0)
        self.cursor_positions: list[float] = []
        self.cursor_lines: list[list] = [[], []]
        self.syncing_cursors = False
        self.capture_starts: list[float] = []  # where each further capture kept by Repeat begins
        self.trigger_times: list[float] = []  # the trigger of each capture shown; empty when the signal's time says nothing of it
        self.after_job = None
        self.master: pg.PlotItem | None = None
        self.time_axis_plot: pg.PlotItem | None = None
        self.busy_widgets: list[QtWidgets.QWidget] = []
        self.bindings: list[tuple] = []
        self.verbose_line.connect(self._append_log)
        self.decode_timer = QtCore.QTimer(self)
        self.decode_timer.setSingleShot(True)
        self.decode_timer.setInterval(DECODE_DELAY_MS)
        self.decode_timer.timeout.connect(self._decode)
        self._build()
        self._generator_protocol_changed()
        self._apply_language()
        self.read_format.setCurrentIndex(self.read_format.findData(self.settings.value("read_format", DEFAULT_FORMAT)))
        self.read_format.currentIndexChanged.connect(
            lambda _index: self.settings.setValue("read_format", self.read_format.currentData()))
        self._apply_theme()
        geometry, state = self.settings.value("window_geometry"), self.settings.value("window_state")
        if geometry:
            self.restoreGeometry(geometry)
        if state:
            self.restoreState(state)

    # ---- translation helpers --------------------------------------------
    def _bind(self, setter, text):
        """Set a text now and again whenever the language changes."""
        self.bindings.append((setter, text))
        setter(tr(text))

    def _label(self, text):
        label = QtWidgets.QLabel()
        self._bind(label.setText, text)
        return label

    def _fill_combo(self, combo, items):
        """Fill a combo box with (source text, data) pairs, retranslated on a language change."""
        for text, data in items:
            combo.addItem(tr(text), data)

        def retranslate(_text):
            for index, (text, _data) in enumerate(items):
                combo.setItemText(index, tr(text))

        self.bindings.append((retranslate, ""))

    def _set_language(self, code):
        set_language(code)
        self.settings.setValue("language", code)
        self._apply_language()

    def _apply_language(self):
        self.setWindowTitle(tr("MHO900 Protocol Decoder {version}", version=__version__))
        for setter, text in self.bindings:
            setter(tr(text) if text else "")
        self.model.retranslate()
        if self.time_axis_plot is not None:
            self._plot()  # the time axis and the notes on the rows are in words
        else:
            self._show_placeholder()
        self._show_cursor_readout()
        gen_params = {key: widget_value(widget) for key, widget in self.gen_param_widgets.items()}
        gen_lines = {key: combo.currentData() for key, combo in self.gen_line_combos.items()}
        self.decoder_tree.rebuild(self._active_top())
        self._fill_decoder_sort()
        for card in self.cards:
            card.retranslate()
            card.config_window.retranslate()
            self._connect_card_drops(card)
        self._renumber_cards()
        levels = self._current_levels()
        self._rebuild_generator_form()
        for key, value in gen_params.items():
            set_widget_value(self.gen_param_widgets[key], value)
        self._rebuild_generator_lines()
        for key, value in gen_lines.items():
            if key in self.gen_line_combos:
                combo = self.gen_line_combos[key]
                combo.setCurrentIndex(max(0, combo.findData(value)))
        self._restore_levels(levels)
        self._show_status()

    # ---- layout ---------------------------------------------------------
    def _build(self):
        pg.setConfigOptions(antialias=False)
        self.graphics = DropGraphics()
        self.graphics.decoder_dropped = self._decoder_dropped_on_view
        self.graphics.scene().sigMouseClicked.connect(self._scene_clicked)
        self.cursor_strip = CursorStrip(self._nearest_cursor, self._drag_cursor)
        self.cursor_overlay = CursorOverlay(self._cursor_view)
        self.graphics.scene().addItem(self.cursor_overlay)
        self.cursor_overlay.hide()
        self.mark_strip = pg.GraphicsWidget()
        self.mark_overlay = MarkOverlay(self._mark_view)
        self.graphics.scene().addItem(self.mark_overlay)
        self.time_bar = QtWidgets.QScrollBar(QtCore.Qt.Horizontal)
        self._bind(self.time_bar.setToolTip, "Moves the view along the time axis.")
        self.time_bar.valueChanged.connect(self._scroll_time)
        waveforms = QtWidgets.QWidget()
        waveforms_layout = QtWidgets.QVBoxLayout(waveforms)
        waveforms_layout.setContentsMargins(0, 0, 0, 0)
        waveforms_layout.setSpacing(0)
        waveforms_layout.addWidget(self.graphics)
        waveforms_layout.addWidget(self.time_bar)
        self.center_tabs = QtWidgets.QTabWidget()
        self.center_tabs.addTab(waveforms, "")
        self._bind(lambda text: self.center_tabs.setTabText(0, text), "Waveforms")
        self.panel = ScopePanel(self)
        self.panel.message.connect(self._log)
        self.screen_view = self.panel.screen_view
        self.center_tabs.addTab(self.panel, "")
        self._bind(lambda text: self.center_tabs.setTabText(SCOPE_TAB, text), "Oscilloscope")
        self.center_tabs.currentChanged.connect(self._screen_tab_shown)
        self.setCentralWidget(self.center_tabs)

        self._build_log_window()
        self._build_toolbars()
        # The decoder forms are tall, so their panel runs the whole height of the window.
        self.setCorner(QtCore.Qt.BottomRightCorner, QtCore.Qt.RightDockWidgetArea)
        self._build_sidebar()
        self._build_results()
        self.resizeDocks([self.sidebar_dock], [PANEL_WIDTH], QtCore.Qt.Horizontal)
        self.resizeDocks([self.results_dock], [RESULTS_HEIGHT], QtCore.Qt.Vertical)
        self._build_status_bar()
        self._build_menu()
        self._build_lane_menu()
        self._show_status()

    def _build_lane_menu(self):
        """Right click on a decoder's trace. Shown with popup(), as pyqtgraph does with its own menus:
        the chosen action then runs after the scene has finished with the click, and deleting a
        decoder rebuilds the rows that the scene is still handling during the click."""
        self.lane_menu = QtWidgets.QMenu(self)
        self.lane_menu_card = None
        self.lane_settings_action = self.lane_menu.addAction("")
        self._bind(self.lane_settings_action.setText, "Decoder settings")
        self.lane_settings_action.triggered.connect(lambda: self.lane_menu_card.config_window.open())
        self.lane_delete_action = self.lane_menu.addAction("")
        self._bind(self.lane_delete_action.setText, "Delete decoder")
        self.lane_delete_action.triggered.connect(lambda: self.remove_decoder(self.lane_menu_card))

    def _build_log_window(self):
        self.log_window = LogWindow(self)
        self._bind(self.log_window.setWindowTitle, "Log")
        self.log = self.log_window.view
        self.verbose = self.log_window.verbose
        self._bind(self.verbose.setText, "Detailed (verbose)")
        self._bind(self.log_window.clear_button.setText, "Clear")
        self._bind(self.log_window.clear_button.setToolTip, "Clears the log window.")
        self._bind(self.verbose.setToolTip, "Adds every exchange with the scope to the log.")
        self.verbose.setChecked(self.settings.value("verbose", False, type=bool))
        self.verbose.toggled.connect(self._verbose_changed)
        self._verbose_changed(self.verbose.isChecked())

    def _toolbar(self, name, title):
        bar = self.addToolBar("")
        bar.setObjectName(name)
        bar.setMovable(False)
        self._bind(bar.setWindowTitle, title)
        return bar

    def _build_toolbars(self):
        style = self.style()
        device = self._toolbar("device_bar", "Connection")
        self.host = QtWidgets.QLineEdit(self.settings.value("host", DEFAULT_HOST))
        self.host.setMaximumWidth(150)
        self.connect_button = QtWidgets.QPushButton()
        self.connect_button.clicked.connect(self._toggle_connection)
        self._bind(self.connect_button.setToolTip, "Connects to the scope at the given IP address, or disconnects.")
        self.connection_state = QtWidgets.QLabel()
        device.addWidget(self._label("IP address:"))
        device.addWidget(self.host)
        device.addWidget(self.connect_button)
        device.addWidget(self.connection_state)
        device.addSeparator()

        self.scope_settings = ScopeSettings(self)
        self.read_format, self.force_dc, self.wait_trigger = (self.scope_settings.read_format, self.scope_settings.force_dc,
                                                              self.scope_settings.wait_trigger)
        self.scope_settings.changed.connect(self._plot)
        self.scope_settings.thresholds_changed.connect(self._schedule_decode)
        self.scope_settings.hide_requested.connect(self._hide_unused)
        self.scope_settings.message.connect(self._log)
        device.addWidget(self.scope_settings)
        self.single_button = self._busy_button("Run", self._acquire, needs_scope=True)
        self.single_button.setObjectName("primary")
        self.single_button.setIcon(style.standardIcon(QtWidgets.QStyle.SP_MediaPlay))
        self.single_button.setShortcut("F5")
        self._bind(self.single_button.setToolTip, "Sets the scope up from the Scope settings on the toolbar, runs one acquisition, reads and decodes (F5).")
        self.read_button = self._busy_button("Read memory", self._read_memory, needs_scope=True)
        self._bind(self.read_button.setToolTip, "Stops the scope and decodes what is in its memory, without changing its settings.")
        self.autoscale_button = self._busy_button("Autoscale", self._autoscale, needs_scope=True)
        self._bind(self.autoscale_button.setToolTip, "Fits the ranges of the channels in use and sets the trigger.")
        self.repeat_button = QtWidgets.QCheckBox()
        self._bind(self.repeat_button.setText, "Repeat")
        self._bind(self.repeat_button.setToolTip, "After each decoding another acquisition, until turned off or aborted. New samples go further along the time axis, behind a separator, and decoders read each acquisition on its own.")
        self.abort_button = QtWidgets.QPushButton()
        self.abort_button.setObjectName("abort")
        self.abort_button.setIcon(style.standardIcon(QtWidgets.QStyle.SP_MediaStop))
        self.abort_button.setShortcut("Esc")
        self._bind(self.abort_button.setText, "Abort")
        self._bind(self.abort_button.setToolTip, "Stops the running operation and turns Repeat off (Esc).")
        self.abort_button.setEnabled(False)
        self.abort_button.clicked.connect(self._abort)
        self.run_place = QtWidgets.QStackedWidget()  # Run gives way to Abort while something can be stopped
        self.run_place.addWidget(self.single_button)
        self.run_place.addWidget(self.abort_button)
        # A stacked widget takes all the free room of a toolbar otherwise.
        self.run_place.setSizePolicy(QtWidgets.QSizePolicy.Fixed, QtWidgets.QSizePolicy.Fixed)
        device.addWidget(self.run_place)
        device.addWidget(self.repeat_button)
        device.addSeparator()
        device.addWidget(self.read_button)
        device.addWidget(self.autoscale_button)
        device.addSeparator()

        self.zoom_in_button = self._view_button(device, "+", self._zoom_in, "Zoom in (mouse wheel)", "Ctrl+=")
        self.zoom_out_button = self._view_button(device, "−", self._zoom_out, "Zoom out (mouse wheel)", "Ctrl+-")
        self.zoom_fit_button = self._view_button(device, "", self.zoom_fit, "Show the whole capture", "Ctrl+0")
        self._bind(self.zoom_fit_button.setText, "Fit")
        self.display_mode = QtWidgets.QComboBox()
        self._fill_combo(self.display_mode, DISPLAY_MODES)
        self.display_mode.setCurrentIndex(max(0, self.display_mode.findData(self.settings.value("display_mode", "logic"))))
        self.display_mode.currentIndexChanged.connect(self._display_mode_changed)
        self._bind(self.display_mode.setToolTip, "Logic: states after the threshold, as in a logic analyzer. Analog: the scope's voltages.")
        device.addWidget(self.display_mode)
        self.cursors_button = QtWidgets.QPushButton()
        self._bind(self.cursors_button.setText, "Cursors")
        self._bind(self.cursors_button.setToolTip, "Two cursors: the arrow between them gives the time and frequency. Drag the lines or the A and B flags.")
        self.cursors_button.setCheckable(True)
        self.cursors_button.toggled.connect(self._cursors_toggled)
        device.addWidget(self.cursors_button)

    def _view_button(self, bar, text, slot, tip, shortcut):
        button = QtWidgets.QPushButton(text)
        button.clicked.connect(slot)
        button.setShortcut(shortcut)
        self._bind(button.setToolTip, tip)
        if text:
            button.setMaximumWidth(32)
        bar.addWidget(button)
        return button

    def _scrolled(self, page):
        scroll = QtWidgets.QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(QtCore.Qt.ScrollBarAlwaysOff)
        scroll.setFrameShape(QtWidgets.QFrame.NoFrame)
        scroll.setWidget(page)
        return scroll

    def _dock(self, name, title, widget, area):
        dock = QtWidgets.QDockWidget()
        dock.setObjectName(name)
        self._bind(dock.setWindowTitle, title)
        dock.setWidget(widget)
        self.addDockWidget(area, dock)
        return dock

    def _build_sidebar(self):
        self.sidebar_tabs = QtWidgets.QTabWidget()
        self.sidebar_tabs.setMinimumWidth(PANEL_WIDTH)
        pages = (("Decoder", self._decoder_controls()), ("Generator", self._generator_controls()))
        self.decoder_page, self.generator_page = pages[0][1], pages[1][1]
        for position, (title, page) in enumerate(pages):
            self.sidebar_tabs.addTab(self._scrolled(page), "")
            self._bind(lambda text, position=position: self.sidebar_tabs.setTabText(position, text), title)
            for combo in page.findChildren(QtWidgets.QComboBox):
                compact(combo)
        self.sidebar_dock = self._dock("sidebar", "Decoder and generator", self.sidebar_tabs, QtCore.Qt.RightDockWidgetArea)

    def _build_results(self):
        self.model = AnnotationModel()
        self.table = QtWidgets.QTableView()
        self.table.setModel(self.model)
        self.table.setAlternatingRowColors(True)
        self.table.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QtWidgets.QAbstractItemView.SingleSelection)
        self.table.horizontalHeader().setSectionResizeMode(DATA_COLUMN, QtWidgets.QHeaderView.Stretch)
        for column, column_width in enumerate(TABLE_COLUMN_WIDTHS):
            self.table.setColumnWidth(column, column_width)
        self.table.verticalHeader().setDefaultSectionSize(22)
        self.table.clicked.connect(self._row_clicked)

        page = QtWidgets.QWidget()
        layout = QtWidgets.QVBoxLayout(page)
        layout.setContentsMargins(4, 4, 4, 4)
        bar = QtWidgets.QHBoxLayout()
        self.result_search = QtWidgets.QLineEdit()
        self.result_search.setClearButtonEnabled(True)
        self._bind(self.result_search.setPlaceholderText, "Search the results")
        self.result_search.textChanged.connect(self._apply_row_filter)
        bar.addWidget(self.result_search, 2)
        self.row_filter_bar = QtWidgets.QWidget()
        filter_layout = QtWidgets.QHBoxLayout(self.row_filter_bar)
        filter_layout.setContentsMargins(0, 0, 0, 0)
        filter_layout.addWidget(self._label("Row:"))
        self.row_filter = QtWidgets.QComboBox()
        self.row_filter.currentIndexChanged.connect(self._apply_row_filter)
        filter_layout.addWidget(self.row_filter, 1)
        self.row_filter_bar.hide()
        bar.addWidget(self.row_filter_bar, 2)
        self.result_errors = QtWidgets.QCheckBox()
        self._bind(self.result_errors.setText, "Errors only")
        self._bind(self.result_errors.setToolTip, "Shows only the rows with an error in the table.")
        self.result_errors.toggled.connect(self._apply_row_filter)
        bar.addWidget(self.result_errors)
        self.result_count = QtWidgets.QLabel()
        bar.addWidget(self.result_count)
        layout.addLayout(bar)
        layout.addWidget(self.table, 1)
        self.results_dock = self._dock("results", "Decoding results", page, QtCore.Qt.BottomDockWidgetArea)

    def _build_status_bar(self):
        self.cursor_readout = QtWidgets.QLabel()
        self.statusBar().addPermanentWidget(self.cursor_readout)
        self.busy_bar = QtWidgets.QProgressBar()
        self.busy_bar.setRange(0, 0)
        self.busy_bar.setMaximumWidth(140)
        self.busy_bar.hide()
        self.statusBar().addPermanentWidget(self.busy_bar)
        # With the log in its own window, this keeps the latest message and any error in sight.
        self.last_message = QtWidgets.QPushButton()
        self.last_message.setFlat(True)
        self._bind(self.last_message.setToolTip, "Open the log")
        self.last_message.clicked.connect(self.show_log)
        self.statusBar().addPermanentWidget(self.last_message)

    def _build_menu(self):
        files = self.menuBar().addMenu("")
        self._bind(files.setTitle, "File")
        for text, slot, shortcut in (("Open waveforms", self._open_waves, "Ctrl+O"),
                                     ("Save waveforms", self._save_waves, "Ctrl+S"),
                                     ("Export results to CSV", self._export_csv, "Ctrl+E")):
            action = files.addAction("")
            self._bind(action.setText, text)
            action.setShortcut(shortcut)
            action.triggered.connect(slot)
        files.addSeparator()
        close = files.addAction("")
        self._bind(close.setText, "Exit")
        close.triggered.connect(self.close)

        view = self.menuBar().addMenu("")
        self._bind(view.setTitle, "View")
        view.addAction(self.sidebar_dock.toggleViewAction())
        view.addAction(self.results_dock.toggleViewAction())
        view.addSeparator()
        themes = view.addMenu("")
        self._bind(themes.setTitle, "Theme")
        theme_group = QtWidgets.QActionGroup(self)
        self.theme_actions = {}
        for name, code in THEME_NAMES:
            action = themes.addAction("")
            self._bind(action.setText, name)
            action.setCheckable(True)
            action.setChecked(code == self.theme_code)
            action.triggered.connect(lambda _checked, code=code: self.set_theme(code))
            theme_group.addAction(action)
            self.theme_actions[code] = action

        advanced = self.menuBar().addMenu("")
        self._bind(advanced.setTitle, "Advanced")
        self.test_action = advanced.addAction("")
        self._bind(self.test_action.setText, "Receiving and comparing data")
        self.test_action.triggered.connect(self.show_test_window)
        advanced.addSeparator()
        log = advanced.addAction("")
        self._bind(log.setText, "Log")
        log.setShortcut("Ctrl+L")
        log.triggered.connect(self.show_log)
        verbose = advanced.addAction("")
        self._bind(verbose.setText, "Detailed log (verbose)")
        verbose.setCheckable(True)
        verbose.setChecked(self.verbose.isChecked())
        verbose.toggled.connect(self.verbose.setChecked)
        self.verbose.toggled.connect(verbose.setChecked)

        languages = self.menuBar().addMenu("")
        self._bind(languages.setTitle, "Language")
        group = QtWidgets.QActionGroup(self)
        for code, name in LANGUAGES:
            action = languages.addAction(name)
            action.setCheckable(True)
            action.setChecked(code == language())
            action.triggered.connect(lambda _checked, code=code: self._set_language(code))
            group.addAction(action)
        help_menu = self.menuBar().addMenu("")
        self._bind(help_menu.setTitle, "Help")
        about = help_menu.addAction("")
        self._bind(about.setText, "About and licenses")
        about.triggered.connect(self._show_about)

    def show_test_window(self):
        self.test_window.show()
        self.test_window.raise_()
        self.test_window.activateWindow()

    def show_log(self):
        self.log_window.show()
        self.log_window.raise_()
        self.log_window.activateWindow()

    # ---- theme ----------------------------------------------------------
    def set_theme(self, code):
        self.theme_code = code
        self.settings.setValue("theme", code)
        self.theme_actions[code].setChecked(True)
        self._apply_theme()

    def _apply_theme(self):
        self.theme = THEMES[self.theme_code]
        apply_palette(self.theme)
        pg.setConfigOptions(background=self.theme.canvas, foreground=self.theme.canvas_text)
        self.graphics.setBackground(self.theme.canvas)
        self.setStyleSheet(window_style(self.theme))
        self.model.set_colors(self.theme.error_text, self.theme.control_text)
        self._style_comparison()
        self._show_status()
        self._style_last_message()
        self._plot()

    def _show_about(self):
        QtWidgets.QMessageBox.about(self, tr("About and licenses"), tr(
            "MHO900 Protocol Decoder {version}. Contains {count} decoders from the libsigrokdecode project (sigrok.org), unmodified, under the GPLv2+, GPLv3+, BSD and MIT licenses. The GPLv3 text and the list of authors are in the srd_decoders folder. The layout of the decoder panel follows PulseView (GPLv3).Distributed together with them, this program falls under the GNU GPL version 3 or later, without any warranty, and its recipients have a right to the source code.",
            version=__version__, count=len(SIGROK)))

    def _group(self, title):
        group = QtWidgets.QGroupBox()
        self._bind(group.setTitle, title)
        return group

    def _decoder_controls(self):
        page = QtWidgets.QWidget()
        layout = QtWidgets.QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)
        library = self._group("Decoders")
        library_layout = QtWidgets.QVBoxLayout(library)
        self.decoder_search = QtWidgets.QLineEdit()
        self.decoder_search.setClearButtonEnabled(True)
        self._bind(self.decoder_search.setPlaceholderText, "Search: name, description, category")
        library_layout.addWidget(self.decoder_search)
        sorting = QtWidgets.QHBoxLayout()
        sorting.addWidget(self._label("Sort:"))
        self.decoder_sort = QtWidgets.QComboBox()
        sorting.addWidget(self.decoder_sort, 1)
        library_layout.addLayout(sorting)
        self.decoder_tree = DecoderTree()
        self.decoder_tree.sort_mode = self.settings.value("decoder_sort", SORT_CATEGORY)
        self.decoder_tree.rebuild(None)
        self._fill_decoder_sort()
        self.decoder_sort.currentIndexChanged.connect(self._decoder_sort_changed)
        self.decoder_search.textChanged.connect(self.decoder_tree.filter_items)
        self.decoder_tree.itemDoubleClicked.connect(self._tree_activated)
        self.decoder_tree.currentItemChanged.connect(self._show_tree_details)
        library_layout.addWidget(self.decoder_tree, 1)
        self.decoder_details = QtWidgets.QLabel()
        self.decoder_details.setWordWrap(True)
        library_layout.addWidget(self.decoder_details)
        hint = self._label("Drag a decoder onto the waveforms or double-click it: each one is added as a further decoder. Dropped on the trace of a decoder it fits, it goes on that stack. Clicking a trace's label opens the decoder's settings, and right-clicking the trace gives a menu with the settings and deleting.")
        hint.setWordWrap(True)
        library_layout.addWidget(hint)
        layout.addWidget(library, 1)
        return page

    def _fill_decoder_sort(self):
        self.decoder_sort.blockSignals(True)
        self.decoder_sort.clear()
        self.decoder_sort.addItem(tr("Categories"), SORT_CATEGORY)
        self.decoder_sort.addItem(tr("Parent protocol"), SORT_PARENT)
        self.decoder_sort.setCurrentIndex(max(0, self.decoder_sort.findData(self.decoder_tree.sort_mode)))
        self.decoder_sort.blockSignals(False)

    def _decoder_sort_changed(self):
        self.decoder_tree.sort_mode = self.decoder_sort.currentData()
        self.settings.setValue("decoder_sort", self.decoder_tree.sort_mode)
        self.decoder_tree.rebuild(self._active_top())
        self.decoder_tree.filter_items(self.decoder_search.text())

    def _tree_activated(self, item, _column):
        decoder_id = item.data(0, QtCore.Qt.UserRole)
        if decoder_id:
            self.add_decoder(decoder_id)

    def _show_tree_details(self, item, _previous):
        decoder_id = item.data(0, QtCore.Qt.UserRole) if item else None
        if not decoder_id:
            self.decoder_details.clear()
            return
        info = SIGROK[decoder_id]
        lines = [f"{info.longname}. {info.desc}", tr("License: {license}", license=info.license)]
        chain = base_chain(info.id, SIGROK)
        if len(chain) > 1:
            lines.append(tr("Needs a base decoder. This stack will be built: {chain}.", chain=" → ".join(chain)))
        self.decoder_details.setText("\n".join(lines))

    # ---- decoders in use ------------------------------------------------
    def add_decoder(self, decoder_id, target=None):
        """From the tree. target: the decoder it was dropped on. A decoder that needs a base goes on the
        target or the active one if it fits there; anything else becomes a new decoder of its own."""
        card = target or (self.active_card if SIGROK[decoder_id].needs_base else None)
        if card is not None and card.fits(decoder_id):
            card.push(decoder_id)
            self.set_active_card(card)
            self._log(tr("Decoder stack: {chain}.", chain=" → ".join(card.chain())))
            return card
        return self.select_sigrok(decoder_id)

    def select_sigrok(self, pd_id):
        """A new decoder. One that needs a base gets the shortest stack leading to it."""
        chain = base_chain(pd_id, SIGROK)
        card = self.new_decoder(SIGROK_ROOTS[chain[0]].name)
        for upper in chain[1:]:
            card.push(upper)
        if len(chain) > 1:
            self._log(tr("Decoder stack: {chain}.", chain=" → ".join(chain)))
        self._show_fitting()
        return card

    def new_decoder(self, name):
        # A further decoder takes channels nothing uses yet, since it is there for another bus.
        used = {channel for card in self.cards for channel in card.mapping().values()}
        order = [channel for channel in all_channels() if channel not in used] + [channel for channel in all_channels() if channel in used]
        card = DecoderCard(DECODERS[name], self.next_card_uid, order)
        self.next_card_uid += 1
        card.changed.connect(self._card_changed)
        card.visibility_changed.connect(self._card_visibility_changed)
        card.remove_requested.connect(self.remove_decoder)
        card.activated.connect(self.set_active_card)
        self._connect_card_drops(card)
        DecoderWindow(self, card)
        self.cards.append(card)
        self._renumber_cards()
        self.set_active_card(card)
        self._schedule_decode()
        self._plot()  # the trace is there at once, as in PulseView, before any data
        return card

    def _decoder_dropped_on_view(self, decoder_id, position):
        return self.add_decoder(decoder_id, self._card_at(position))

    def _card_of_lane(self, key):
        uid = int(key.split("/", 1)[0])
        return next((card for card in self.cards if card.uid == uid), None)

    def _card_at(self, position):
        for key, lane in self.lanes.items():
            if lane.sceneBoundingRect().contains(position):
                return self._card_of_lane(key)
        return None

    def _connect_card_drops(self, card):
        card.decoder_dropped = lambda decoder_id, card=card: self.add_decoder(decoder_id, card)

    def remove_decoder(self, card):
        self.cards.remove(card)
        card.config_window.close()
        card.config_window.deleteLater()  # with the decoder's form inside
        self._renumber_cards()
        if self.active_card is card:
            self.set_active_card(self.cards[-1] if self.cards else None)
        self._schedule_decode()
        self._plot()

    def remove_all_decoders(self):
        for card in list(self.cards):
            self.remove_decoder(card)

    def _renumber_cards(self):
        seen = {}
        for card in self.cards:
            seen[card.decoder.name] = seen.get(card.decoder.name, 0) + 1
            card.set_number(seen[card.decoder.name])

    def set_active_card(self, card):
        self.active_card = card
        self._show_fitting()

    def _active_top(self):
        return self.active_card.stack_top() if self.active_card is not None else None

    def _show_fitting(self):
        self.decoder_tree.show_fitting(self._active_top())

    def _card_changed(self):
        self._show_fitting()
        self._schedule_decode()

    def _card_visibility_changed(self):
        if self.waves:
            self._apply_row_filter()
        self._plot()

    def _generator_controls(self):
        page = QtWidgets.QWidget()
        layout = QtWidgets.QVBoxLayout(page)

        signal = self._group("Generate a signal for the device")
        signal_layout = QtWidgets.QVBoxLayout(signal)
        form = QtWidgets.QFormLayout()
        self.gen_protocol = QtWidgets.QComboBox()
        self.gen_protocol.addItems(list(generator.PROTOCOLS))
        self.gen_protocol.currentIndexChanged.connect(self._generator_protocol_changed)
        self.gen_data_format = QtWidgets.QComboBox()
        self._fill_combo(self.gen_data_format, DATA_FORMATS)
        self.gen_data_format.currentIndexChanged.connect(self._gen_format_changed)
        self.gen_data = QtWidgets.QLineEdit("0x48 0x65 0x6C 0x6C 0x6F")
        self._bind(self.gen_data.setToolTip, DATA_FIELD_TIP)
        self._gen_data_format_shown = "hex"
        form.addRow(self._label("Protocol"), self.gen_protocol)
        form.addRow(self._label("Data format"), self.gen_data_format)
        form.addRow(self._label("Data"), self.gen_data)
        signal_layout.addLayout(form)
        self.gen_form_holder = QtWidgets.QVBoxLayout()
        signal_layout.addLayout(self.gen_form_holder)
        self.gen_line_grid = QtWidgets.QGridLayout()
        self.gen_line_grid.setColumnStretch(1, 1)
        signal_layout.addLayout(self.gen_line_grid)

        levels = QtWidgets.QGridLayout()
        self.gen_levels = {}
        levels.addWidget(self._label("Low level"), 0, 1)
        levels.addWidget(self._label("High level"), 0, 2)
        for row, afg in enumerate(generator.OUTPUTS, start=1):
            levels.addWidget(QtWidgets.QLabel(f"AFG{afg}"), row, 0)
            low, high = QtWidgets.QDoubleSpinBox(), QtWidgets.QDoubleSpinBox()
            for box in (low, high):
                box.setRange(-10, 10)
                box.setDecimals(3)
                box.setSingleStep(0.1)
                box.setSuffix(" V")
            levels.addWidget(low, row, 1)
            levels.addWidget(high, row, 2)
            self.gen_levels[afg] = (low, high)
        signal_layout.addLayout(levels)
        load_form = QtWidgets.QFormLayout()
        self.gen_load = QtWidgets.QComboBox()
        self._fill_combo(self.gen_load, LOADS)
        load_form.addRow(self._label("Output load"), self.gen_load)
        signal_layout.addLayout(load_form)
        note = self._label("The output repeats the record in a loop while it is on. Check the voltage levels before connecting a device.")
        note.setWordWrap(True)
        signal_layout.addWidget(note)
        self.gen_button = self._busy_button("Generate", self._generate, needs_scope=True)
        self._bind(self.gen_button.setToolTip, "Loads the signal onto the AFG outputs and turns them on.")
        self.afg_off_button = self._busy_button("Switch outputs off", self._afg_off, needs_scope=True)
        self._bind(self.afg_off_button.setToolTip, "Turns the AFG1 and AFG2 outputs off.")
        buttons = QtWidgets.QHBoxLayout()
        buttons.addWidget(self.gen_button)
        buttons.addWidget(self.afg_off_button)
        signal_layout.addLayout(buttons)
        layout.addWidget(signal)
        layout.addStretch(1)
        self._build_test_window()
        return page

    def _build_test_window(self):
        """Checking a device's response: a test of its own, reached from the Advanced menu."""
        self.test_window = QtWidgets.QWidget(self, QtCore.Qt.Window)
        self._bind(self.test_window.setWindowTitle, "Receiving and comparing data")
        layout = QtWidgets.QVBoxLayout(self.test_window)
        receive = self._group("Receive the response")
        receive_layout = QtWidgets.QVBoxLayout(receive)
        info = self._label("Receiving uses the decoder's channels and the Scope settings on the toolbar.")
        info.setWordWrap(True)
        receive_layout.addWidget(info)
        self.match_button = QtWidgets.QPushButton()
        self._bind(self.match_button.setText, "Set the decoder and time base from the generator")
        self._bind(self.match_button.setToolTip, "Sets the channel of each line by the number of its AFG output (AFG1 on CH1).")
        self.match_button.clicked.connect(self._match_decoder)
        receive_layout.addWidget(self.match_button)
        self.receive_button = self._busy_button("Receive", self._acquire, needs_scope=True)
        self._bind(self.receive_button.setToolTip, "One acquisition, reading and decoding, the same as the Run button.")
        receive_layout.addWidget(self.receive_button)
        layout.addWidget(receive)

        check = self._group("Compare with the expected data")
        check_layout = QtWidgets.QVBoxLayout(check)
        self.expect_sent = QtWidgets.QCheckBox()
        self._bind(self.expect_sent.setText, "Expect the data sent by the generator")
        self._bind(self.expect_sent.setToolTip, "When unchecked, you type the expected data in the field below.")
        self.expect_sent.setChecked(True)
        check_layout.addWidget(self.expect_sent)
        row = QtWidgets.QHBoxLayout()
        self.expected_format = QtWidgets.QComboBox()
        self._fill_combo(self.expected_format, DATA_FORMATS)
        self.expected_data = QtWidgets.QLineEdit()
        self._bind(self.expected_data.setToolTip, DATA_FIELD_TIP)
        self.expected_format.setEnabled(False)
        self.expected_data.setEnabled(False)
        self.expect_sent.toggled.connect(self.expected_format.setDisabled)
        self.expect_sent.toggled.connect(self.expected_data.setDisabled)
        row.addWidget(self.expected_format)
        row.addWidget(self.expected_data, 1)
        check_layout.addLayout(row)
        self.compare_button = QtWidgets.QPushButton()
        self._bind(self.compare_button.setText, "Compare")
        self._bind(self.compare_button.setToolTip, "Looks for the expected data in the results of all decoders.")
        self.compare_button.clicked.connect(self._compare)
        check_layout.addWidget(self.compare_button)
        self.compare_label = QtWidgets.QLabel()
        self.compare_label.setWordWrap(True)
        check_layout.addWidget(self.compare_label)
        layout.addWidget(check)
        layout.addStretch(1)

    def _busy_button(self, text, slot, needs_scope=False):
        button = QtWidgets.QPushButton()
        self._bind(button.setText, text)
        button.clicked.connect(slot)
        button.setProperty("needs_scope", needs_scope)
        self.busy_widgets.append(button)
        return button

    def _refresh_buttons(self):
        running = self.worker is not None
        for button in self.busy_widgets:
            needs_scope = button.property("needs_scope")
            button.setEnabled(not running and (not needs_scope or self.scope is not None))
        self.connect_button.setEnabled(not running)
        self.abort_button.setEnabled(running and self.abortable)
        self.run_place.setCurrentWidget(self.abort_button if running and self.abortable else self.single_button)
        self.connect_button.setText(tr("Disconnect") if self.scope else tr("Connect"))

    abortable = False
    last_triggered = None
    docks_shown: dict = {}

    def _show_status(self):
        self.statusBar().showMessage(tr("Ready") if self.scope else tr("Not connected"))
        self.connect_button.setText(tr("Disconnect") if self.scope else tr("Connect"))
        color = self.theme.ok_text if self.scope else self.theme.error_text
        self.connection_state.setText(f"● {tr('Connected') if self.scope else tr('Not connected')}")
        self.connection_state.setStyleSheet(f"color: {color};")

    # ---- generator forms ------------------------------------------------
    def _generator_protocol_changed(self):
        self._rebuild_generator_form()
        self._rebuild_generator_lines()

    def _rebuild_generator_form(self):
        clear_layout(self.gen_form_holder)
        self.gen_param_widgets = {}
        holder = QtWidgets.QWidget()
        form = QtWidgets.QFormLayout(holder)
        form.setContentsMargins(0, 0, 0, 0)
        for param in generator.PROTOCOL_PARAMS[self.gen_protocol.currentText()]:
            widget = param_widget(param)
            self.gen_param_widgets[param.key] = widget
            form.addRow(tr(param.label), widget)
            if param.key == "differential":
                widget.currentIndexChanged.connect(self._rebuild_generator_lines)
        self.gen_form_holder.addWidget(holder)

    def _gen_params(self) -> dict:
        return {key: widget_value(widget) for key, widget in self.gen_param_widgets.items()}

    def _rebuild_generator_lines(self):
        """One row per signal line of the protocol, each picking the AFG output that carries it."""
        clear_layout(self.gen_line_grid)
        self.gen_line_combos = {}
        params = self._gen_params()
        lines = generator.lines_of(self.gen_protocol.currentText(), params)
        # An output with no line yet still gets usable levels, since a line can be moved to it.
        for afg in generator.OUTPUTS:
            low, high = generator.default_levels(lines[0][0], params)
            self.gen_levels[afg][0].setValue(low)
            self.gen_levels[afg][1].setValue(high)
        for row, (key, label) in enumerate(lines):
            combo = QtWidgets.QComboBox()
            compact(combo)
            combo.addItem(tr("not used"), None)
            for afg in generator.OUTPUTS:
                combo.addItem(f"AFG{afg}", afg)
            combo.setCurrentIndex(combo.findData(generator.OUTPUTS[row]))
            self.gen_line_grid.addWidget(QtWidgets.QLabel(tr("Line {label}", label=label)), row, 0)
            self.gen_line_grid.addWidget(combo, row, 1)
            self.gen_line_combos[key] = combo
            low, high = generator.default_levels(key, params)
            self.gen_levels[generator.OUTPUTS[row]][0].setValue(low)
            self.gen_levels[generator.OUTPUTS[row]][1].setValue(high)

    def _current_levels(self):
        return {afg: (low.value(), high.value()) for afg, (low, high) in self.gen_levels.items()}

    def _restore_levels(self, levels):
        for afg, (low, high) in levels.items():
            self.gen_levels[afg][0].setValue(low)
            self.gen_levels[afg][1].setValue(high)

    def _gen_format_changed(self):
        """Keep the data when switching between hex and ASCII."""
        new_format = self.gen_data_format.currentData()
        try:
            data = generator.parse_data(self.gen_data.text(), self._gen_data_format_shown)
            self.gen_data.setText(generator.format_data(data, new_format))
        except ValueError:
            self.gen_data.clear()
        self._gen_data_format_shown = new_format

    def _build_generated(self) -> generator.Generated:
        protocol = self.gen_protocol.currentText()
        data = generator.parse_data(self.gen_data.text(), self.gen_data_format.currentData())
        generated = generator.build(protocol, data, self._gen_params())
        outputs = {key: combo.currentData() for key, combo in self.gen_line_combos.items()}
        generator.assign(generated, outputs, self._current_levels(), self.gen_load.currentData())
        return generated

    # ---- jobs -----------------------------------------------------------
    def _start(self, job, on_done, label, uses_scope=True, abortable=True):
        """uses_scope: an abort must also bring the scope's link back to a clean state."""
        self.statusBar().showMessage(label)
        self.job_label = label
        self.job_started = time.monotonic()
        self._verbose(tr("Start: {label}", label=label))
        abort.clear()
        self.abortable = abortable
        if uses_scope and self.panel.link is not None:
            self.panel.set_job_running(True)
            unwrapped = job

            def job(progress):
                # What was changed in the scope tab just before this reaches the instrument first.
                self.panel.drain()
                return unwrapped(progress)

        self.worker = Worker(job, self.scope.recover if uses_scope and self.scope else None, self)
        self.worker.done.connect(on_done)
        self.worker.failed.connect(self._job_failed)
        self.worker.aborted.connect(self._job_aborted)
        self.worker.progress.connect(self.statusBar().showMessage)
        self.worker.progress.connect(self._verbose)
        self.worker.finished.connect(self._job_finished)
        self.busy_bar.show()
        self._refresh_buttons()
        self.worker.start()

    def _abort(self):
        self.repeat_button.setChecked(False)
        abort.request()
        self.abort_button.setEnabled(False)
        self.statusBar().showMessage(tr("Aborting…"))

    def _job_failed(self, message):
        self.after_job = None
        self.repeat_button.setChecked(False)
        self._log(tr("ERROR: {message}", message=message))
        QtWidgets.QApplication.beep()

    def _job_aborted(self):
        self.after_job = None
        if not (self.decoding and self.decode_pending):  # a decoding replaced by a newer one is not news
            self._log(tr("Aborted: {label}", label=self.job_label))

    def _job_finished(self):
        self._verbose(tr("End: {label}, {seconds:.3f} s", label=self.job_label, seconds=time.monotonic() - self.job_started))
        self.worker = None
        self.decoding = False
        self.panel.set_job_running(False)
        self.busy_bar.hide()
        self._show_status()
        self._refresh_buttons()
        if self.after_job:
            action, self.after_job = self.after_job, None
            action()
        elif self.decode_pending:
            self._decode()

    def _log(self, text):
        self._append_log(text)
        shown = text if len(text) <= STATUS_TEXT_CHARS else text[:STATUS_TEXT_CHARS - 1] + "…"
        self.last_message.setText(shown)
        self.last_message_text = text
        self._style_last_message()

    def _append_log(self, text):
        self.log.appendPlainText(text)

    def _style_last_message(self):
        text = self.last_message_text
        # Both spellings: a message stays in the language it was written in after a switch.
        if text.startswith(("ERROR", "BŁĄD")):
            color = self.theme.error_text
        elif text.startswith(("Warning", "Uwaga")):
            color = self.theme.warning_text
        else:
            color = None
        self.last_message.setStyleSheet(f"color: {color}; font-weight: bold;" if color else "")

    last_message_text = ""

    def _verbose(self, text):
        if self.verbose.isChecked():
            self._append_log(f"{time.strftime('%H:%M:%S')} {text}")

    def _verbose_changed(self, enabled):
        self.settings.setValue("verbose", enabled)
        self.log.setMaximumBlockCount(VERBOSE_LOG_LINES if enabled else LOG_LINES)

    def _scope_trace(self, text):
        """Called from worker threads for every exchange with the scope."""
        if self.verbose.isChecked():
            self.verbose_line.emit(f"{time.strftime('%H:%M:%S')} {text}")

    def _toggle_connection(self):
        if self.scope:
            self.panel.detach()
            self.scope.close()
            self.scope = None
            self._show_status()
            self._refresh_buttons()
            return
        host = self.host.text().strip()
        self.settings.setValue("host", host)

        def job(_progress):
            scope = Scope(host)
            scope.trace = self._scope_trace
            return scope, scope.connect()

        self._start(job, self._connected, tr("Connecting to {host}…", host=host), uses_scope=False, abortable=False)

    def _connected(self, result):
        self.scope, identity = result
        self._log(tr("Connected: {identity}", identity=identity))
        self.panel.attach(self.scope)

    def _used_channels(self):
        """What is chosen on the toolbar and what the decoders need."""
        channels = self.scope_settings.selected() | {channel for card in self.cards for channel in card.mapping().values()}
        if not channels:
            raise ValueError(tr("Choose channels in the Scope settings on the toolbar or add a decoder."))
        return sorted(channels)

    def _read_job(self, channels, progress, sample_format):
        digital_wanted = [channel for channel in channels if is_digital(channel)]
        if digital_wanted or sample_format == FILE_FORMAT:
            waves = self._read_file_job(channels, digital_wanted, progress)
        else:
            waves = {}
            for channel in channels:
                if not self.scope.channel_enabled(channel):
                    continue
                started = time.monotonic()

                def report(done, total, channel=channel, started=started):
                    speed = done / max(time.monotonic() - started, 1e-3) / 1e6
                    progress(tr("Reading CH{channel}: {done:,} / {total:,} points, {speed:.2f} Mpt/s",
                                channel=channel, done=done, total=total, speed=speed))

                waves[channel] = self.scope.fetch(channel, progress=report, sample_format=sample_format)
        absent = [channel for channel in channels if channel not in waves]
        states = {channel: self.scope.channel_state(channel) for channel in waves if not is_digital(channel)}
        for channel, wave in waves.items():
            if not is_digital(channel):
                wave.volts_per_div = states[channel]["scale"]
        states["absent"] = absent
        if digital_wanted:
            states["thresholds"] = self.scope.digital_thresholds()
        return waves, states

    def _read_file_job(self, channels, digital_wanted, progress):
        """Everything the scope has in memory comes in one file, so all of it is kept, but of the
        digital channels only the assigned ones and those that change: 16 flat lines help nobody."""
        progress(tr("The scope is saving its memory to a file…"))
        data = self.scope.save_memory(lambda done: progress(tr("Downloading the file: {size:.1f} MB", size=done / 1e6)))
        return rigol_files.parse_bin(data, digital_wanted).waves

    def _acquire(self, keep_old=False):
        """keep_old: the new samples go after the ones already shown, as Repeat collects them."""
        try:
            channels = self._used_channels()
        except ValueError as exc:
            self._log(tr("ERROR: {message}", message=exc))
            return
        settings = self.scope_settings
        try:
            commands = settings.trigger_commands(self._trigger_levels())
        except ValueError as exc:
            self._log(tr("ERROR: {message}", message=exc))
            return
        plan = CapturePlan(set(channels), self.force_dc.isChecked(), settings.depth.currentData(), settings.time_scale.currentData(),
                           tuple(commands), settings.pod_thresholds(), settings.pretrigger())
        self.panel.note_depth(plan.depth)
        wait = self.wait_trigger.isChecked() or settings.trigger_is_waited_for()
        sample_format = self.read_format.currentData()

        def job(progress):
            scope = self.scope
            notes = scope.apply_plan(plan)
            progress(tr("Waiting for the trigger…") if wait else tr("Acquiring…"))
            if wait:
                triggered = scope.single(timeout=None, force_after=None)
            else:
                length = scope.query_float(":TIMebase:MAIN:SCALe?") * CAPTURE_DIVISIONS
                triggered = scope.single(timeout=length + SINGLE_MARGIN_S)
            waves, states = self._read_job(channels, progress, sample_format)
            states["notes"] = notes
            return triggered, waves, states

        self.after_job = self._decode
        self._start(job, lambda result: self._captured(result, keep_old), tr("Acquiring…"))

    def _append_capture(self, waves):
        """False: the new capture does not fit the old samples on one time axis, so it starts anew."""
        if not waves:
            return True
        if not segments.can_join(self.waves, waves):
            self._log(tr("Warning: other channels or another sample rate than in the previous acquisition, so the record starts anew."))
            return False
        if segments.kept_bytes(self.waves) + segments.kept_bytes(waves) > segments.MAX_KEPT_BYTES:
            self.repeat_button.setChecked(False)
            self._log(tr("Warning: Repeat turned off, as the samples would take over {limit:.0f} GB. The last acquisition was not added.",
                         limit=segments.MAX_KEPT_BYTES / 1e9))
            self.after_job = None
            return True
        joined, start, trigger_time = segments.join_captures(self.waves, waves)
        self._show_new_waves(joined, self.capture_starts + [start], self.trigger_times + [trigger_time])
        return True

    def _trigger_levels(self):
        """The threshold set on a channel, else the middle of what it last showed. With neither, the scope keeps its level."""
        levels = {}
        for channel in self.scope_settings.conditions:
            if is_digital(channel):
                continue  # the level of its whole group, set next to the channels or in the Oscilloscope tab
            level = self.scope_settings.thresholds.get(channel)
            if level is None and self._has_signal(channel):
                level = self._signal_middle(self.waves[channel])
            if level is not None:
                levels[channel] = level
        return levels

    @staticmethod
    def _signal_middle(wave):
        found = auto_thresholds(wave.volts, wave.min_swing)
        return (found.low + found.high) / 2 if found else float(np.mean(wave.volts))

    def _has_signal(self, channel):
        if channel not in self.waves:
            return False
        if channel not in self.signal_known:
            self.signal_known[channel] = self.waves[channel].has_signal()
        return self.signal_known[channel]

    def _read_memory(self):
        try:
            channels = self._used_channels()
        except ValueError as exc:
            self._log(tr("ERROR: {message}", message=exc))
            return
        sample_format = self.read_format.currentData()

        def job(progress):
            self.scope.stop()
            return True, *self._read_job(channels, progress, sample_format)

        self.after_job = self._decode
        self._start(job, self._captured, tr("Reading memory…"))

    def _captured(self, result, keep_old=False):
        triggered, waves, states = result
        self.last_triggered = triggered
        self.absent_channels = set(states["absent"])
        if not (keep_old and self.waves and self._append_capture(waves)):
            self._show_new_waves(waves, trigger_times=(0.0,))  # the scope's time zero is the trigger, also in a forced capture
        if not waves:
            self._schedule_decode()
        for note in states.get("notes", ()):
            self._log(tr("Warning: {note}", note=note))
        if not triggered:
            self._log(tr("Warning: no trigger within 1 s, the acquisition was forced. Check the trigger settings."))
        if states["absent"]:
            self._log(tr("Warning: the scope's memory has no channels {channels}. Turn them on and acquire.",
                         channels=", ".join(map(channel_name, states["absent"]))))
        for channel, wave in waves.items():
            if is_digital(channel):
                continue
            state = states[channel]
            self._log(tr("CH{channel}: {points:,} points, {rate:g} MSa/s, {scale:g} V/div, probe {probe:g}×, coupling {coupling}",
                         channel=channel, points=len(wave.volts), rate=1 / wave.dt / 1e6, scale=state["scale"],
                         probe=state["probe"], coupling=state["coupling"]))
            if wave.clipped > 0.001:
                self._log(tr("Warning: CH{channel} is clipped ({clipped:.0%} of samples at the end of the range). Use Autoscale or check the probe setting.", channel=channel, clipped=wave.clipped))
            if state["coupling"] != "DC":
                self._log(tr("Warning: CH{channel} has {coupling} coupling, the logic levels will be distorted.",
                             channel=channel, coupling=state["coupling"]))
        digital = [channel for channel in waves if is_digital(channel)]
        if digital:
            wave = waves[digital[0]]
            self._log(tr("Digital channels {channels}: {points:,} points, {rate:g} MSa/s",
                         channels=", ".join(channel_name(channel) for channel in digital), points=len(wave.volts),
                         rate=1 / wave.dt / 1e6))
        if states.get("thresholds") and not all(states["thresholds"].values()):
            self._log(tr("Warning: the digital channels' threshold is 0 V, so positive logic may not change state. Set it in Scope settings on the toolbar."))

    def _autoscale(self):
        try:
            channels = [channel for channel in self._used_channels() if not is_digital(channel)]
        except ValueError as exc:
            self._log(tr("ERROR: {message}", message=exc))
            return
        if not channels:
            self._log(tr("ERROR: {message}", message=tr("Autoscale works on analog channels only.")))
            return
        trigger_channel = None
        if self.panel.trigger_mode.currentData() == "EDGE":
            found = re.fullmatch(r"CHANnel(\d)", self.panel.edge_setting("SOURce").current())
            trigger_channel = int(found.group(1)) if found else None
        slope = self.panel.edge_setting("SLOPe").current()
        auto_depth = self.panel.depth_is_auto()

        def job(_progress):
            return self.scope.autoscale(channels, trigger_channel, slope, auto_depth)

        self._start(job, self._autoscaled, tr("Autoscale…"))

    def _autoscaled(self, levels):
        for channel, (low, high) in levels.items():
            self._log(tr("Autoscale CH{channel}: {low:.3f} V … {high:.3f} V", channel=channel, low=low, high=high))

    # ---- generator actions ----------------------------------------------
    def _generate(self):
        try:
            generated = self._build_generated()
        except ValueError as exc:
            self._log(tr("ERROR: {message}", message=exc))
            return

        def job(progress):
            progress(tr("Loading the AFG outputs…"))
            generator.play(self.scope, generated)
            return generated

        self._start(job, self._generate_done, tr("Generating {protocol}…", protocol=generated.protocol))

    def _generate_done(self, generated):
        self.generated = generated
        outputs = ", ".join(f"AFG{afg}={generated.labels[key]} {generated.levels[afg][0]:g}…{generated.levels[afg][1]:g} V"
                            for afg, key in sorted(generated.outputs.items()))
        self._log(tr("Generator {protocol}: {bytes} bytes, {outputs}, record period {period:.6g} s",
                     protocol=generated.protocol, bytes=len(generated.data), outputs=outputs, period=generated.period))
        self._verbose(f"{generated.sample_rate:.0f} Sa/s, AFG {generated.afg_frequency:.3f} Hz")

    def _afg_off(self):
        def job(_progress):
            self.scope.afg_enable(1, False)
            self.scope.afg_enable(2, False)

        self._start(job, lambda _result: self._log(tr("AFG outputs switched off.")), tr("Switching the AFG off…"))

    def _match_decoder(self):
        """Set the decoder and timebase for the generator's signal; AFGn is assumed on CHn."""
        try:
            generated = self.generated or self._build_generated()
        except ValueError as exc:
            self._log(tr("ERROR: {message}", message=exc))
            return
        card = next((card for card in ([self.active_card] if self.active_card else []) + self.cards
                     if card.decoder.name == generated.decoder), None) or self.new_decoder(generated.decoder)
        self.scope_settings.follow_the_scope()  # what is set just below must not be overwritten by the acquisition
        card.building = True
        channels = {key: afg for afg, key in generated.outputs.items()}
        for row in card.signal_rows:
            channel = channels.get(generated.signals.get(row.spec.key))
            row.channel.setCurrentIndex(max(0, row.channel.findData(channel)))
            row.auto.setChecked(True)
        self.panel.settings["trigger_mode"].set_value("EDGE")
        self.panel.edge_setting("SOURce").set_value(f"CHANnel{max(generated.outputs)}")
        self.panel.edge_setting("SLOPe").set_value("NEGative")
        defaults = DECODERS[generated.decoder].defaults()
        for key, widget in card.param_widgets.items():
            set_widget_value(widget, generated.decoder_params.get(key, defaults[key]))
        card.building = False
        self._schedule_decode()
        scale = generated.time_scale
        combo = self.panel.time_scale
        best = min(range(combo.count()), key=lambda index: abs(combo.itemData(index) - scale))
        self.panel.settings["time_scale"].set_value(combo.itemData(best))
        self._log(tr("Decoder {decoder}, time base {scale}.", decoder=generated.decoder,
                     scale=generator.format_time_scale(scale)))

    def _compare(self):
        self.last_compare_ok = False
        if self.expect_sent.isChecked():
            if self.generated is None:
                self._show_comparison(False, tr("Generate a signal first or enter the expected data."))
                return
            expected = self.generated.data
        else:
            try:
                expected = generator.parse_data(self.expected_data.text(), self.expected_format.currentData())
            except ValueError as exc:
                self._show_comparison(False, str(exc))
                return
        outcome = generator.compare(self.result.annotations, expected)
        self.last_compare_ok = outcome.matched
        if outcome.matched:
            text = tr("MATCH: {count} bytes, complete copies in the capture: {copies} (line {row}).",
                      count=outcome.expected, copies=outcome.occurrences, row=self._row_label(outcome.row))
        elif not outcome.received:
            text = tr("MISMATCH: nothing decoded. Receive the signal first.")
        else:
            text = tr("MISMATCH: received {received} values, the first {best} of {count} bytes match (line {row}).",
                      received=outcome.received, best=outcome.best_count, count=outcome.expected, row=self._row_label(outcome.row))
            if outcome.want is not None:
                got = tr("none") if outcome.got is None else f"0x{outcome.got:02X}"
                text += " " + tr("Byte {position}: received {got}, expected 0x{want:02X}.",
                                 position=outcome.position, got=got, want=outcome.want)
        self._show_comparison(outcome.matched, text)

    def _row_label(self, key):
        return self.result.rows.get(key, key)

    def _show_comparison(self, matched, text):
        self.compare_label.setText(text)
        self.compare_matched = matched
        self._style_comparison()
        self._log(text)

    compare_matched = None

    def _style_comparison(self):
        if self.compare_matched is not None:
            color = self.theme.ok_text if self.compare_matched else self.theme.error_text
            self.compare_label.setStyleSheet(f"color: {color}; font-weight: bold;")

    # ---- scope tab ------------------------------------------------------
    def _screen_tab_shown(self, index):
        self.panel.set_active(index == SCOPE_TAB)
        # The scope tab needs the width for its picture and settings; the decoder panels come back after it.
        if index == SCOPE_TAB:
            self.docks_shown = {dock: dock.isVisible() for dock in (self.sidebar_dock, self.results_dock)}
            for dock in self.docks_shown:
                dock.hide()
        else:
            for dock, shown in self.docks_shown.items():
                dock.setVisible(shown)
            self.docks_shown = {}

    # ---- decoding -------------------------------------------------------
    def _schedule_decode(self):
        """Every change of a decoder decodes again, as in PulseView, without any button."""
        if self.waves:
            self.decode_timer.start()
        else:  # no data to decode, while a trace still tells what it lacks
            for card in self.cards:
                self._decode_plan(card)
            self._plot()

    def _decode_plan(self, card):
        """What the job needs for one decoder, or None with the reason shown on the decoder."""
        mapping = card.mapping()
        missing = [tr(spec.label) for spec in card.decoder.signals if spec.required and spec.key not in mapping]
        card.problem = ""
        if missing or not mapping:
            names = missing or [tr(spec.label) for spec in card.decoder.signals]
            card.problem = tr("Assign channels: {signals}.", signals=", ".join(names))
            card.set_status(card.problem, self.theme.warning_text)
            return None
        absent = sorted({channel for channel in mapping.values() if channel not in self.waves})
        if absent:
            card.set_status(tr("No data for channels: {channels}. Acquire first.",
                               channels=", ".join(channel_name(channel) for channel in absent)), self.theme.warning_text)
            return None
        notes = []
        if len(set(mapping.values())) < len(mapping):
            notes.append(tr("Several signals of a decoder on one channel give a meaningless result."))
        waves = self._same_length({key: self.waves[channel] for key, channel in mapping.items()})
        thresholds = {key: DIGITAL_LEVELS for key, wave in waves.items() if wave.digital}
        for row in card.signal_rows:
            key = row.spec.key
            if key not in waves or key in thresholds:
                continue
            level = row.level.value() if not row.auto.isChecked() else self.scope_settings.thresholds.get(row.channel.currentData())
            if level is not None:
                thresholds[key] = manual_thresholds(level, waves[key].volts)
        flat = [key for key, wave in waves.items() if (wave.digital or key not in thresholds) and not wave.has_signal()]
        notes += [tr("Signal {key} does not change level.", key=key.upper()) for key in flat]
        card.set_status(" ".join(notes), self.theme.warning_text)
        if len(flat) == len(waves):
            # With no edge on any line nothing was sent, while CAN reads a constant low as endless frames.
            card.problem = " ".join(notes)
            return None
        return card, card.decoder, waves, thresholds, card.params()

    def _decode(self):
        self.decode_timer.stop()
        if not self.waves:
            return
        if self.worker is not None:
            # The running decoding is out of date, so it gives way. Any other job finishes first.
            self.decode_pending = True
            if self.decoding:
                abort.request()
            return
        self.decode_pending = False
        plans = [plan for plan in map(self._decode_plan, self.cards) if plan]
        starts = list(self.capture_starts)

        def job(_progress):
            outcomes = []
            for card, decoder, waves, thresholds, params in plans:
                abort.checkpoint()
                try:
                    outcomes.append((card, self._decode_captures(decoder, waves, thresholds, params, starts), None))
                except ValueError as exc:  # one decoder's trouble leaves the others working
                    outcomes.append((card, None, str(exc)))
            return outcomes

        self.decoding = True
        self._start(job, self._decoded, tr("Decoding…"), uses_scope=False)

    def _decode_captures(self, decoder, waves, thresholds, params, starts):
        """Each capture on its own: a frame cut off by the end of one must not run into the next."""
        results = []
        for part in segments.split_captures(waves, starts):
            # With no edge on any line nothing was sent, while CAN reads a constant low as endless frames.
            if starts and all((wave.digital or key not in thresholds) and not wave.has_signal() for key, wave in part.items()):
                continue
            traces = decoder.prepare(part, thresholds, **params)
            abort.checkpoint()
            for key, trace in traces.items():
                self._scope_trace(f"{key.upper()}: {len(trace.edges):,} edges, {trace.length:,} samples")
            results.append(decoder.decode(traces, **params))
            abort.checkpoint()
        return segments.merge_results(results)

    def _same_length(self, waves):
        """Digital channels may have fewer samples than analog ones, while a decoder needs one time base."""
        lengths = {len(wave.volts) for wave in waves.values()}
        if len(lengths) == 1:
            return waves
        self._log(tr("Warning: the decoder mixes analog and digital channels. On this scope digital channels lag analog ones by several samples (8 samples measured at 50 MSa/s), which can mislead the decoder."))
        finest = min(wave.dt for wave in waves.values())
        stretched = {}
        for key, wave in waves.items():
            factor = round(wave.dt / finest)
            stretched[key] = wave if factor == 1 else Waveform(
                wave.channel, np.repeat(wave.volts, factor), wave.dt / factor, wave.t0, digital=wave.digital,
                volts_per_div=wave.volts_per_div)
        # Joined captures can leave a channel a sample or so longer than whole steps of another give.
        shortest = min(len(wave.volts) for wave in stretched.values())
        return {key: replace(wave, volts=wave.volts[:shortest]) for key, wave in stretched.items()}

    def _decoded(self, outcomes):
        merged = DecodeResult()
        decoded_cards = {card.uid for card, _result, _error in outcomes}
        for card in self.cards:
            if card.uid not in decoded_cards:
                card.known_rows = {}
        for card, result, error in outcomes:
            if card not in self.cards:  # removed while the job ran
                continue
            if error:
                card.problem = tr("ERROR: {message}", message=error)
                card.set_status(card.problem, self.theme.error_text)
                card.known_rows = {}
                continue
            self._merge(merged, card, result)
        merged.annotations.sort(key=lambda item: item.start)
        self.result = merged
        rows = [item for item in merged.annotations if not item.detail and not item.hidden]
        errors = sum(1 for item in rows if item.error)
        self._fill_row_filter(merged)
        self._apply_row_filter()
        self._log(tr("Decoders: {decoders}, {rows} items, {errors} with an error.",
                     decoders=len(self.cards), rows=len(rows), errors=errors))
        self._plot()
        if self.repeat_button.isChecked() and self.scope is not None:
            self.after_job = lambda: self._acquire(True)

    def _merge(self, merged, card, result):
        """Row keys and labels get the decoder's number, so that equal decoders stay apart."""
        prefix = f"{card.uid}/"
        labels = {}
        present = {item.row for item in result.annotations if not item.hidden}
        for key, label in result.rows.items():
            if key in present:
                labels[prefix + key] = f"#{card.number} {label}" if card.number > 1 else label
        for item in result.annotations:
            item.row = prefix + item.row
        merged.annotations += result.annotations
        merged.rows.update(labels)
        card.known_rows = labels
        for note in result.notes:
            self._log(f"{card.caption}: {note}")

    def _fill_row_filter(self, result):
        present = {item.row for item in result.annotations}
        self.row_filter.blockSignals(True)
        self.row_filter.clear()
        self.row_filter.addItem(tr("All except bits"), None)
        for key, label in result.rows.items():
            if key in present:
                self.row_filter.addItem(label, key)
        self.row_filter.blockSignals(False)
        self.row_filter_bar.setVisible(len(result.rows) > 1)
        for column, column_width in enumerate(TABLE_COLUMN_WIDTHS):
            wide = ROW_TABLE_COLUMN_WIDTHS.get(column) if result.rows else None
            if column != DATA_COLUMN:
                self.table.setColumnWidth(column, wide or column_width)

    def _apply_row_filter(self):
        wanted = self.row_filter.currentData()
        shown = {key for card in self.cards for key in card.visible_row_keys()}
        if wanted is None:
            rows = [item for item in self.result.annotations if not item.detail and not item.hidden and item.row in shown]
        else:
            rows = [item for item in self.result.annotations if item.row == wanted]
        total = len(rows)
        needle = self.result_search.text().strip().lower()
        if needle:
            rows = [item for item in rows
                    if needle in " ".join((item.text, item.kind, self.result.rows.get(item.row, ""), item.error)).lower()]
        if self.result_errors.isChecked():
            rows = [item for item in rows if item.error]
        self.model.row_labels = self.result.rows
        self.model.set_rows(rows)
        self.result_count.setText(f"{len(rows):,} / {total:,}")

    # ---- plot -----------------------------------------------------------
    def _show_new_waves(self, waves, capture_starts=(), trigger_times=()):
        """A new capture has nothing in common with the old view or the old results."""
        self.waves = waves
        self.capture_starts = list(capture_starts)
        self.trigger_times = list(trigger_times)
        self.memory_read = True
        self.signal_known = {}
        self.auto_y_ranges = {}
        self.result = DecodeResult()
        self.master = None
        self._fill_row_filter(self.result)
        for card in self.cards:
            card.known_rows = {}
        self._apply_row_filter()
        self._plot()

    def _show_placeholder(self):
        self._release_rows()
        self.master = None
        self.plots = []
        self.lanes = {}
        self.time_axis_plot = None
        self.graphics.addLabel(tr("Choose channels in the Scope settings on the toolbar or add a decoder."), color=self.theme.canvas_text)
        self.graphics.ci.setGeometry(self.graphics.range)
        self._update_time_bar()

    def _display_mode_changed(self):
        self.settings.setValue("display_mode", self.display_mode.currentData())
        if self.waves:
            self._plot()

    def _style_row(self, plot, axis_width):
        plot.setMenuEnabled(False)
        plot.hideButtons()
        plot.setMouseEnabled(x=True, y=False)
        plot.hideAxis("bottom")
        plot.getAxis("left").setWidth(axis_width)
        low, high = self.full_range
        shortest = (min(wave.dt for wave in self.waves.values()) if self.waves else NO_DATA_STEP) * MIN_ZOOM_SAMPLES
        plot.vb.setLimits(xMin=low, xMax=high, minXRange=shortest)
        self.plots.append(plot)

    def _logic_row(self, plot, label, color, wave, threshold):
        trace = digitize(wave.volts, wave.dt, wave.t0, threshold, wave.min_swing)
        edges = np.asarray(trace.edges, dtype=np.float64)
        # Each edge gives two points, one at the old level and one at the new.
        x = np.empty(len(edges) * 2 + 2)
        x[0] = 0
        x[1:-1] = np.repeat(edges, 2)
        x[-1] = trace.length - 1
        y = np.empty(len(x))
        pairs = np.arange(len(x)) // 2
        y[:] = trace.initial ^ (pairs & 1)
        plot.plot(wave.t0 + x * wave.dt, y, pen=pg.mkPen(color, width=1))
        self._logic_axis(plot, label, color)

    @staticmethod
    def _logic_axis(plot, label, color):
        plot.setYRange(*LOGIC_Y_RANGE, padding=0)
        axis = plot.getAxis("left")
        axis.setStyle(hideOverlappingLabels=False)
        axis.setTextPen(color)
        axis.setTicks([[(0.5, label)]])

    def _analog_row(self, channel, plot, label, color, wave):
        plot.setLabel("left", label, units="V", color=color)
        times = wave.t0 + np.arange(len(wave.volts)) * wave.dt
        plot.plot(times, wave.volts, pen=pg.mkPen(color, width=1))
        plot.setYRange(*self._y_range(channel, wave), padding=0)
        self.analog_rows[channel] = plot

    def _y_range(self, channel, wave):
        """From the whole capture, so that looking at a part of it in time changes nothing."""
        if channel in self.manual_y_ranges:
            return self.manual_y_ranges[channel]
        if channel not in self.auto_y_ranges:
            low, high = float(wave.volts.min()), float(wave.volts.max())
            half = max(high - low, MIN_Y_SPAN_SWINGS * wave.min_swing) / 2 * (1 + 2 * ANALOG_Y_MARGIN)
            self.auto_y_ranges[channel] = ((low + high) / 2 - half, (low + high) / 2 + half)
        return self.auto_y_ranges[channel]

    def _open_channel(self, channel):
        settings = self.scope_settings
        wave = self.waves.get(channel)
        suggested = self._signal_middle(wave) if wave is not None and not wave.digital else 0.0
        limits = tuple(self.analog_rows[channel].viewRange()[1]) if channel in self.analog_rows else None
        dialog = ChannelDialog(self, channel, settings.conditions.get(channel, trigger.NONE), settings.thresholds.get(channel),
                               suggested, limits, channel in self.manual_y_ranges)
        dialog.applied.connect(lambda condition, threshold, new_limits: self._channel_applied(
            channel, condition, threshold, new_limits, limits is not None))
        dialog.open()  # does not wait, since the scene is still handling the click

    def _channel_applied(self, channel, condition, threshold, limits, has_scale):
        if has_scale:
            self._set_y_range(channel, limits)
        self.scope_settings.set_condition(channel, condition)
        if not is_digital(channel):
            self.scope_settings.set_threshold(channel, threshold)

    def _set_y_range(self, channel, limits):
        if limits is None:
            self.manual_y_ranges.pop(channel, None)
        else:
            self.manual_y_ranges[channel] = limits
        if channel in self.analog_rows:
            self.analog_rows[channel].setYRange(*self._y_range(channel, self.waves[channel]), padding=0)

    def _release_rows(self):
        """Take the rows off the view and keep them for the next plot. Destroying a row that has held
        cursor lines crashes a later paint of the scene (access violation in Qt5Widgets, 5.15.2), so
        rows are emptied and used again, and there are never more than the most ever shown."""
        self._remove_cursors()
        for note in self.row_notes:
            note.setParentItem(None)
            self.graphics.scene().removeItem(note)
        self.row_notes = []
        for plot in self.plots:  # decoder lanes are among them
            plot.clear()
            self.spare_rows.append(plot)
        self.graphics.clear()
        layout = self.graphics.ci.layout
        for row in range(layout.rowCount()):  # the free space's row is not at the same place next time
            layout.setRowStretchFactor(row, 0)

    def _new_row(self, row):
        if not self.spare_rows:
            return self.graphics.addPlot(row=row, col=0)
        plot = self.spare_rows.pop()
        self.graphics.ci.addItem(plot, row=row, col=0)
        plot.setXLink(None)
        plot.hideAxis("top")
        plot.setDownsampling(auto=False, ds=1)
        plot.setClipToView(False)
        for side in ("left", "top"):  # an axis keeps the colours it was given, even of another theme
            plot.getAxis(side).setPen()
            plot.getAxis(side).setTextPen()
        axis = plot.getAxis("left")
        axis.setLabel(None)
        axis.setTicks(None)
        axis.setToolTip("")
        axis.unsetCursor()
        axis.setStyle(showValues=True, hideOverlappingLabels=True)
        return plot

    def _channel_color(self, channel):
        if is_digital(channel):
            colors = self.theme.digital
            return colors[(channel - digital_channel(0)) % len(colors)]
        return self.theme.channels[channel]

    def _display_threshold(self, channel, wave):
        if wave.digital:
            return DIGITAL_LEVELS
        for card in self.cards:
            for row in card.signal_rows:
                if row.channel.currentData() == channel and not row.auto.isChecked():
                    return manual_thresholds(row.level.value(), wave.volts)
        level = self.scope_settings.thresholds.get(channel)
        return None if level is None else manual_thresholds(level, wave.volts)

    def _channel_names(self):
        """channel -> the signals assigned to it, in the order of the decoders."""
        names = {}
        for card in self.cards:
            for key, channel in card.mapping().items():
                names.setdefault(channel, [])
                if key.upper() not in names[channel]:
                    names[channel].append(key.upper())
        return names

    def _shown_channels(self, names):
        """Every chosen channel has its row, data or not. As in PulseView, the channels in use come first."""
        self.scope_settings.set_locked(names)
        wanted = self.scope_settings.selected()
        return [channel for channel in sorted(wanted) if channel in names] + [channel for channel in sorted(wanted) if channel not in names]

    def _channel_problem(self, channel):
        """Only a channel asked of the scope with nothing in its memory has something to say: a flat one draws its level."""
        if channel in self.absent_channels and channel not in self.waves:
            return tr("ERROR: no data. The channel was not on during the acquisition."), self.theme.error_text
        return None

    def _is_unused(self, channel):
        return channel not in self.waves or not self._has_signal(channel)

    def _hide_unused(self):
        """One click, so that a channel ticked again afterwards shows, with what is wrong with it."""
        settings = self.scope_settings
        if not (self.waves or self.memory_read):
            self._log(tr("There is no data yet, so it is not known which channels are empty. Acquire first."))
            return
        unused = {channel for channel in settings.selected() if self._is_unused(channel)}
        kept = unused & (settings.locked | set(settings.conditions))
        if unused - kept:
            self._log(tr("Hid the channels with no data or no level changes: {channels}.", channels=", ".join(map(channel_name, sorted(unused - kept)))))
        settings.untick(unused - kept)
        if kept:
            self._log(tr("Warning: channels {channels} have no data or no level changes but a decoder or the trigger uses them.",
                         channels=", ".join(map(channel_name, sorted(kept)))))

    def _row_note(self, plot, text, color):
        """Painted on the row's own view, as PulseView does on a trace."""
        note = QtWidgets.QGraphicsSimpleTextItem(text, plot.vb)
        note.setBrush(QtGui.QColor(color))
        note.setPos(LANE_NOTE_MARGIN, LANE_NOTE_MARGIN)
        self.row_notes.append(note)

    def _plot(self):
        previous = self.master.viewRange()[0] if self.master is not None else None
        self._release_rows()
        self.time_axis_plot = None
        self.master = None
        self.plots = []
        self.lanes = {}
        self.lane_text = {}
        self.lane_items = {}
        self.lane_colors = {}
        self.channel_rows = {}
        self.analog_rows = {}
        names = self._channel_names()
        shown = self._shown_channels(names)
        if not shown and not self.cards:
            self._show_placeholder()
            self._show_cursor_readout()
            return
        layout = self.graphics.ci.layout
        layout.setSpacing(0)
        line_height = self.fontMetrics().height()
        logic = self.display_mode.currentData() == "logic"
        self.full_range = NO_DATA_RANGE
        if self.waves:
            self.full_range = (min(wave.t0 for wave in self.waves.values()),
                               max(wave.t0 + (len(wave.volts) - 1) * wave.dt for wave in self.waves.values()))
        # As in PulseView, a decoder's trace always exists: its rows if they hold anything, else one empty row.
        lane_rows = []
        for card in self.cards:
            rows = [(key, self.result.rows[key]) for key in card.visible_row_keys() if key in self.result.rows]
            lane_rows += rows or [(f"{card.uid}/", card.caption)]
        # As in PulseView: first the channels, then the decoders one under another.
        row = 0
        if self.cursors_button.isChecked():
            strip_height = line_height * CURSOR_STRIP_LINES
            self.cursor_strip.setMinimumHeight(strip_height)
            self.cursor_strip.setMaximumHeight(strip_height)
            self.graphics.ci.addItem(self.cursor_strip, row=0, col=0)
            row = 1
        if self.trigger_times or self.capture_starts:
            strip_height = line_height * MARK_STRIP_LINES
            self.mark_strip.setMinimumHeight(strip_height)
            self.mark_strip.setMaximumHeight(strip_height)
            self.graphics.ci.addItem(self.mark_strip, row=row, col=0)
            row += 1
        master = None
        for channel in shown:
            wave = self.waves.get(channel)
            color = self._channel_color(channel)
            label = self._channel_label(" ".join(names.get(channel, ())), channel)
            plot = self._new_row(row)
            self._style_row(plot, WIDE_AXIS_WIDTH)
            plot.setDownsampling(auto=True, mode="peak")
            plot.setClipToView(True)
            drawn_as_logic = logic or wave is None or wave.digital
            if wave is None:
                self._logic_axis(plot, label, color)
            elif drawn_as_logic:
                self._logic_row(plot, label, color, wave, self._display_threshold(channel, wave))
            else:
                self._analog_row(channel, plot, label, color, wave)
            axis = plot.getAxis("left")
            axis.setCursor(QtCore.Qt.PointingHandCursor)
            axis.setToolTip(tr("Click: the channel's trigger, threshold and scale."))
            problem = self._channel_problem(channel)
            if problem:
                self._row_note(plot, *problem)
            self.channel_rows[channel] = plot
            lines = LOGIC_ROW_LINES if drawn_as_logic else ANALOG_ROW_LINES
            if master is None:
                master = plot
                lines += TIME_AXIS_LINES
            else:
                plot.setXLink(master)
            plot.setMaximumHeight(line_height * lines)
            row += 1
        for key, label in lane_rows:
            lane = self._add_lane(key, row, master, WIDE_AXIS_WIDTH, label)
            if master is None:
                master = lane
                lane.setMaximumHeight(line_height * (LANE_ROW_LINES + TIME_AXIS_LINES))
            row += 1
        # Rows keep their height and the free space stays below them, like in an analyzer.
        self.graphics.ci.addItem(pg.GraphicsWidget(), row=row, col=0)
        layout.setRowStretchFactor(row, 1)
        # Emptying the layout shrank it, and only a resize of the view would bring its size back.
        self.graphics.ci.setGeometry(self.graphics.range)
        master.showAxis("top")
        master.setLabel("top", tr("Time"), units="s")
        master.getAxis("top").setStyle(showValues=True)
        self.time_axis_plot = master
        self.master = master
        low, high = self.full_range
        keep = previous is not None and previous[0] < high and previous[1] > low
        master.setXRange(*(previous if keep else self.full_range), padding=0)
        if master not in self.connected_rows:  # a row used again keeps its connections
            self.connected_rows.append(master)
            master.sigXRangeChanged.connect(self._update_text)
            master.vb.sigResized.connect(self._update_text)
        self._mark_capture_starts()
        self._mark_triggers()
        self._update_text()
        self._place_cursors()

    def _channel_label(self, name, channel):
        """The channel and its trigger stay whole in a short row label: the signal's name gives way."""
        tail = " ".join((channel_name(channel), self.scope_settings.symbol(channel))).strip()
        room = ROW_LABEL_CHARS - len(tail) - 1
        if len(name) > max(room, 0):
            name = name[:room - 1] + "…" if room > 1 else ""
        return f"{name} {tail}".strip()

    def _mark_capture_starts(self):
        """A dashed line on every row where a kept capture ends and the next one begins."""
        for start in self.capture_starts:
            self._vertical_line(start, self._capture_pen())

    def _mark_triggers(self):
        for time in self.trigger_times:
            self._vertical_line(time, self._trigger_pen())

    def _capture_pen(self):
        return pg.mkPen(self.theme.canvas_text, style=QtCore.Qt.DashLine)

    def _trigger_pen(self):
        return pg.mkPen(self.theme.error_text, width=TRIGGER_LINE_WIDTH)  # the one colour no trace has

    def _vertical_line(self, position, pen):
        """A line through every row, over the traces. It does not widen the view."""
        for plot in self.plots:
            line = pg.InfiniteLine(position, angle=90, pen=pen, movable=False)
            line.setZValue(VERTICAL_LINE_Z)
            plot.addItem(line, ignoreBounds=True)

    def _mark_view(self):
        """The lines' names, on a strip of their own above the time axis, where they cover no data."""
        if self.master is None or not (self.trigger_times or self.capture_starts):
            return None
        marks = [LineMark(self._scene_x(time), TRIGGER_LINE_LABEL, self._trigger_pen(), self.theme.canvas, self.theme.error_text, False)
                 for time in self.trigger_times]
        marks += [LineMark(self._scene_x(start), tr("Acquisition {number}", number=position + 2), self._capture_pen(), self.theme.canvas_text, None, True)
                  for position, start in enumerate(self.capture_starts)]
        area = self.master.vb.sceneBoundingRect()
        strip = self.mark_strip.sceneBoundingRect()
        return MarkView(tuple(marks), area.left(), area.right(), strip.top(), strip.bottom(), area.top(), self.font())

    def _add_lane(self, key, row, master, axis_width, label):
        lane = self._new_row(row)
        self._style_row(lane, axis_width)
        lane.setXLink(master)
        lane.setYRange(0, 1, padding=0)
        lane.setMaximumHeight(self.fontMetrics().height() * LANE_ROW_LINES)
        self.lane_colors[key] = self.theme.rows[len(self.lanes) % len(self.theme.rows)]
        axis = lane.getAxis("left")
        short = label if len(label) <= ROW_LABEL_CHARS else label[:ROW_LABEL_CHARS - 1] + "…"
        axis.setStyle(hideOverlappingLabels=False)
        axis.setTicks([[(0.5, short)]])
        axis.setToolTip(label)
        self._fill_lane(key, lane)
        self.lanes[key] = lane
        card = self._card_of_lane(key)
        if card is not None and card.problem and key == f"{card.uid}/":
            self._row_note(lane, card.problem, self.theme.warning_text)
        return lane

    def _lane_band(self, key, detail):
        """Vertical span of one layer of a lane: half of it when both layers have items."""
        layers = self.lane_items[key]
        if not (layers.get(False) and layers.get(True)):
            return (0.0, 1.0)
        return (0.0, 0.48) if detail else (0.52, 1.0)

    def _fill_lane(self, key, lane):
        items = [item for item in self.result.annotations if item.row == key and not item.hidden]
        self.lane_items[key] = {}
        for detail in (False, True):
            self.lane_items[key][detail] = sorted((item for item in items if item.detail == detail),
                                                  key=lambda item: item.start)
        normal = QtGui.QColor(self.lane_colors[key])
        faded = QtGui.QColor(normal)
        faded.setAlpha(DETAIL_ALPHA)
        failed = QtGui.QColor(self.theme.error_bar)
        control = QtGui.QColor(self.theme.control_bar)
        faded_control = QtGui.QColor(control)
        faded_control.setAlpha(DETAIL_ALPHA)
        for detail in (False, True):
            part = self.lane_items[key][detail]
            if not part:
                continue
            y0, y1 = self._lane_band(key, detail)
            plain, field = (faded, faded_control) if detail else (normal, control)
            brushes = [failed if item.error else (field if item.control else plain) for item in part]
            # An outline of its own colour: a bar narrower than its outline must not turn invisible.
            outlines = {id(color): pg.mkPen(color.darker(BAR_OUTLINE_DARKER), width=1) for color in (plain, field, failed)}
            bars = pg.BarGraphItem(
                x0=np.array([item.start for item in part]),
                x1=np.array([max(item.end, item.start) for item in part]),
                y0=np.full(len(part), y0), y1=np.full(len(part), y1),
                brushes=brushes, pens=[outlines[id(color)] for color in brushes])
            lane.addItem(bars)

    def _update_text(self, *_args):
        self.cursor_overlay.refresh()
        self.mark_overlay.refresh()
        self._update_time_bar()
        if self.master is None:
            return
        data_font = QtGui.QFont(self.font())
        data_font.setBold(True)
        field_font = QtGui.QFont(self.font())
        fonts = {False: (data_font, QtGui.QFontMetricsF(data_font)), True: (field_font, QtGui.QFontMetricsF(field_font))}
        for key, lane in self.lanes.items():
            for text in self.lane_text.get(key, []):
                lane.removeItem(text)
            self.lane_text[key] = []
            # Linked views update after this signal, so only the master range is current.
            (x_low, x_high), _y = self.master.viewRange()
            width_pixels = max(lane.vb.width(), 1)
            span = max(x_high - x_low, 1e-30)
            for detail in (False, True):
                items = self.lane_items[key][detail]
                first = max(bisect_left(items, x_low, key=lambda a: a.start) - 1, 0)
                last = bisect_left(items, x_high, key=lambda a: a.start)
                visible = items[first:last]
                if len(visible) > MAX_TEXT_ITEMS:
                    continue
                y = sum(self._lane_band(key, detail)) / 2
                for item in visible:
                    start, end = max(item.start, x_low), min(item.end, x_high)
                    pixels = (end - start) / span * width_pixels
                    font, metrics = fonts[item.control]
                    # The longest form of the text that fits, as PulseView draws it. Text that fits in
                    # no form is left out, since it would run over the next bar.
                    forms = item.texts or (item.text,)
                    shown_text = next((form for form in forms
                                       if metrics.horizontalAdvance(form) + TEXT_MARGIN_PIXELS <= pixels), None)
                    if shown_text is None:
                        continue
                    text = pg.TextItem(shown_text, color=self.theme.bar_text, anchor=(0.5, 0.5))
                    text.setFont(font)
                    text.setPos((start + end) / 2, y)
                    lane.addItem(text)
                    self.lane_text[key].append(text)

    def _row_clicked(self, index):
        item = self.model.rows[index.row()]
        if self.master is None:
            return
        length = max(item.end - item.start, 1e-12)
        self.master.setXRange(item.start - length, item.end + length, padding=0)

    def _scene_clicked(self, event):
        if self.master is None or event.button() not in (QtCore.Qt.LeftButton, QtCore.Qt.RightButton):
            return
        position = event.scenePos()
        if event.button() == QtCore.Qt.LeftButton:
            for channel, plot in self.channel_rows.items():
                if plot.getAxis("left").sceneBoundingRect().contains(position):
                    self._open_channel(channel)
                    return
        for key, lane in self.lanes.items():
            on_label = lane.getAxis("left").sceneBoundingRect().contains(position)
            if not on_label and not lane.vb.sceneBoundingRect().contains(position):
                continue
            card = self._card_of_lane(key)
            self.set_active_card(card)
            if event.button() == QtCore.Qt.RightButton:
                self.lane_menu_card = card
                self.lane_menu.popup(event.screenPos().toPoint())
            elif on_label:
                card.config_window.open()
            else:
                self.select_annotation(key, lane.vb.mapSceneToView(position).x())
            return

    def select_annotation(self, key, moment):
        """Clicking a bar on the plot finds it in the table, the reverse of clicking a table row."""
        for detail in (False, True):
            items = self.lane_items[key][detail]
            position = bisect_right(items, moment, key=lambda a: a.start) - 1
            if position < 0 or items[position].end < moment:
                continue
            item = items[position]
            self.statusBar().showMessage(f"{item.kind}: {item.text} {item.error}".strip())
            found = self.model.positions.get(id(item))
            if found is not None:
                self.table.selectRow(found)
                self.table.scrollTo(self.model.index(found, 0))
            return item
        return None

    # ---- zoom -----------------------------------------------------------
    def _zoom(self, factor):
        if self.master is None:
            return
        low, high = self.master.viewRange()[0]
        middle, half = (low + high) / 2, (high - low) / 2 * factor
        self.master.setXRange(middle - half, middle + half, padding=0)

    def _zoom_in(self):
        self._zoom(1 / ZOOM_STEP)

    def _zoom_out(self):
        self._zoom(ZOOM_STEP)

    def zoom_fit(self):
        if self.master is not None:
            self.master.setXRange(*self.full_range, padding=0)

    def _update_time_bar(self):
        """The bar shows where the view is in the capture. With all of it in view there is nowhere to move."""
        low, high = self.full_range
        first, last = self.master.viewRange()[0] if self.master is not None else self.full_range
        whole = max(high - low, 1e-30)
        page = min(TIME_BAR_STEPS, max(1, round((last - first) / whole * TIME_BAR_STEPS)))
        with QtCore.QSignalBlocker(self.time_bar):
            self.time_bar.setPageStep(page)
            self.time_bar.setSingleStep(max(1, round(page * TIME_BAR_ARROW_STEP)))
            self.time_bar.setRange(0, TIME_BAR_STEPS - page)
            self.time_bar.setValue(round((first - low) / whole * TIME_BAR_STEPS))
        self.time_bar.setEnabled(page < TIME_BAR_STEPS)

    def _scroll_time(self, value):
        """Moves the view and keeps its width."""
        if self.master is None:
            return
        low, high = self.full_range
        first, last = self.master.viewRange()[0]
        span = last - first
        start = min(low + value / TIME_BAR_STEPS * (high - low), high - span)
        self.master.setXRange(start, start + span, padding=0)

    # ---- cursors --------------------------------------------------------
    def _cursors_toggled(self, enabled):
        if enabled and self.master is not None:
            low, high = self.master.viewRange()[0]
            self.cursor_positions = [low + (high - low) * FIRST_CURSOR, low + (high - low) * SECOND_CURSOR]
        self._plot()  # the strip above the time axis comes and goes with them

    def _scene_x(self, value):
        """Read from the master's range, since linked views update after its signal."""
        area = self.master.vb.sceneBoundingRect()
        low, high = self.master.viewRange()[0]
        return area.left() + (value - low) / max(high - low, 1e-30) * area.width()

    def _cursor_view(self):
        if self.master is None or not self.cursor_lines[0]:
            return None
        area = self.master.vb.sceneBoundingRect()
        strip = self.cursor_strip.sceneBoundingRect()
        font = QtGui.QFont(self.font())
        font.setBold(True)
        return CursorView(tuple(self._scene_x(value) for value in self.cursor_positions), area.left(), area.right(),
                          strip.top(), strip.bottom(), max(plot.vb.sceneBoundingRect().bottom() for plot in self.plots),
                          span_text(abs(self.cursor_positions[1] - self.cursor_positions[0])),
                          self.theme.cursor, self.theme.cursor_text, font)

    def _nearest_cursor(self, scene_x):
        return min((0, 1), key=lambda which: abs(self._scene_x(self.cursor_positions[which]) - scene_x))

    def _drag_cursor(self, which, scene_x):
        area = self.master.vb.sceneBoundingRect()
        low, high = self.master.viewRange()[0]
        self.cursor_lines[which][0].setValue(low + (scene_x - area.left()) / max(area.width(), 1) * (high - low))

    def _remove_cursors(self):
        self.cursor_overlay.hide()
        for lines in self.cursor_lines:
            for plot, line in zip(self.plots, lines):
                plot.removeItem(line)
        self.cursor_lines = [[], []]

    def _place_cursors(self):
        """Lines in every row, removed and created again, since a new plot brings new rows."""
        self._remove_cursors()
        if self.cursors_button.isChecked() and self.master is not None:
            low, high = self.full_range
            if not self.cursor_positions or not all(low <= value <= high for value in self.cursor_positions):
                self.cursor_positions = [low + (high - low) * FIRST_CURSOR, low + (high - low) * SECOND_CURSOR]
            hovered = pg.mkPen(self.theme.cursor, width=HOVERED_CURSOR_WIDTH)
            for which, value in enumerate(self.cursor_positions):
                for plot in self.plots:
                    # These only take the mouse and thicken under it: the overlay paints the whole cursor.
                    line = pg.InfiniteLine(pos=value, angle=90, movable=True, pen=pg.mkPen(None), hoverPen=hovered,
                                           bounds=self.full_range)
                    line.setCursor(QtCore.Qt.SizeHorCursor)
                    line.sigPositionChanged.connect(lambda moved, which=which: self._cursor_moved(which, moved))
                    plot.addItem(line, ignoreBounds=True)
                    self.cursor_lines[which].append(line)
            self.cursor_overlay.show()
            self.cursor_overlay.refresh()
        self._show_cursor_readout()

    def _cursor_moved(self, which, moved):
        if self.syncing_cursors:
            return
        self.syncing_cursors = True
        self.cursor_positions[which] = moved.value()
        for line in self.cursor_lines[which]:
            if line is not moved:
                line.setValue(moved.value())
        self.syncing_cursors = False
        self.cursor_overlay.update()
        self._show_cursor_readout()

    def _show_cursor_readout(self):
        if not self.cursor_lines[0]:
            self.cursor_readout.clear()
            return
        first, second = self.cursor_positions
        delta = abs(second - first)
        text = f"A {pg.siFormat(first, suffix='s')}   B {pg.siFormat(second, suffix='s')}   Δ {pg.siFormat(delta, suffix='s')}"
        if delta > 0:
            text += f"   1/Δ {pg.siFormat(1 / delta, suffix='Hz')}"
        self.cursor_readout.setText(text)

    # ---- files ----------------------------------------------------------
    def _export_csv(self):
        if not self.model.rows:
            self._log(tr("No results to export."))
            return
        path, _ = QtWidgets.QFileDialog.getSaveFileName(self, tr("Export results"), "decode.csv", "CSV (*.csv)")
        if not path:
            return
        self.export_csv_to(path)

    def export_csv_to(self, path):
        with open(path, "w", newline="", encoding="utf-8-sig") as handle:
            writer = csv.writer(handle, delimiter=";")
            writer.writerow(["start_s", "end_s", tr("line"), tr("type"), tr("data"), tr("value"), tr("error")])
            for item in self.model.rows:
                writer.writerow([f"{item.start:.12g}", f"{item.end:.12g}", item.row, item.kind,
                                 item.text, "" if item.value is None else item.value, item.error])
        self._log(tr("Saved {count} rows: {path}", count=len(self.model.rows), path=path))

    def _save_waves(self):
        if not self.waves:
            self._log(tr("No waveforms to save."))
            return
        path, _ = QtWidgets.QFileDialog.getSaveFileName(self, tr("Save waveforms"), "waves.npz", "NumPy (*.npz)")
        if not path:
            return
        self.save_waves_to(path)

    def save_waves_to(self, path):
        data = {}
        for channel, wave in self.waves.items():
            data[f"ch{channel}"] = wave.volts
            data[f"ch{channel}_timing"] = np.array([wave.dt, wave.t0, wave.volts_per_div])
        if self.trigger_times:
            data[TRIGGER_TIMES_KEY] = np.array(self.trigger_times)
        np.savez_compressed(path, **data)
        self._log(tr("Waveforms saved: {path}", path=path))

    def _open_waves(self):
        path, _ = QtWidgets.QFileDialog.getOpenFileName(
            self, tr("Open waveforms"), "", tr("Waveforms (*.npz *.bin *.wfm)"))
        if not path:
            return
        if path.lower().endswith(".wfm"):
            rate, accepted = QtWidgets.QInputDialog.getDouble(
                self, tr("Open waveforms"), tr("A WFM file has no sample rate. Enter it [MSa/s]:"),
                100.0, 1e-6, 1e5, 6)
            if accepted:
                self.open_wfm_from(path, rate * 1e6)
            return
        self.open_waves_from(path)

    def open_waves_from(self, path):
        try:
            if path.lower().endswith(".bin"):
                with open(path, "rb") as handle:
                    waves = rigol_files.parse_bin(handle.read()).waves
                trigger_times = (0.0,)  # the file keeps the scope's time, as its waveform data does
            else:
                waves = self._read_npz(path)
                with np.load(path) as data:
                    trigger_times = data[TRIGGER_TIMES_KEY].tolist() if TRIGGER_TIMES_KEY in data.files else ()
        except (OSError, ValueError, KeyError, EOFError) as exc:
            self._log(tr("ERROR reading {path}: {error}", path=path, error=exc))
            return
        self._show_opened(path, waves, trigger_times)

    def open_wfm_from(self, path, sample_rate):
        try:
            with open(path, "rb") as handle:
                waves = rigol_files.parse_wfm(handle.read(), 1 / sample_rate).waves
        except (OSError, ValueError) as exc:
            self._log(tr("ERROR reading {path}: {error}", path=path, error=exc))
            return
        self._show_opened(path, waves)

    def _read_npz(self, path):
        waves = {}
        with np.load(path) as data:
            for name in data.files:
                found = re.fullmatch(r"ch(\d+)", name)
                if found:
                    channel = int(found.group(1))
                    dt, t0, *scale = data[f"{name}_timing"]  # older files have no scale
                    waves[channel] = Waveform(channel, data[name], float(dt), float(t0), digital=is_digital(channel),
                                              volts_per_div=float(scale[0]) if scale else 0.0)
        return waves

    def _show_opened(self, path, waves, trigger_times=()):
        if not waves:
            self._log(tr("ERROR: {message}", message=tr("the file holds no waveforms.")))
            return
        self.scope_settings.choose(waves)
        self.absent_channels = set()
        self._show_new_waves(waves, trigger_times=trigger_times)
        self._log(tr("Opened {path}: channels {channels}", path=path,
                     channels=", ".join(channel_name(channel) for channel in sorted(waves))))
        self._decode()

    def closeEvent(self, event):
        self.center_tabs.setCurrentIndex(0)  # the scope tab hides the panels, and that must not be remembered
        self.settings.setValue("window_geometry", self.saveGeometry())
        self.settings.setValue("window_state", self.saveState())
        self.log_window.close()
        for card in self.cards:
            card.config_window.close()
        abort.request()
        if self.worker is not None:
            self.worker.wait()
        self.panel.detach()
        abort.clear()
        if self.scope:
            self.scope.close()
        super().closeEvent(event)

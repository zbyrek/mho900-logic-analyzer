"""Digital thresholds, protocol triggers, captures kept by Repeat and the screen stream, with no oscilloscope."""
import json
import os
from fractions import Fraction

import av
import numpy as np
import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt5 import QtCore, QtGui, QtNetwork, QtWebSockets, QtWidgets  # noqa: E402

from mhodecode import segments  # noqa: E402
from mhodecode.decoders.base import Annotation, DecodeResult  # noqa: E402
from mhodecode.scope import CapturePlan, ScopeError, Waveform, digital_channel  # noqa: E402
from mhodecode.scope_panel import ScreenView  # noqa: E402
from mhodecode.screen_stream import MOUSE_DOWN, MOUSE_MOVE, MOUSE_UP, ScreenStream  # noqa: E402
from test_gui import CaptureScope, PlanScope, app, run_job, square_wave, window  # noqa: E402, F401

D0, D9, D14 = digital_channel(0), digital_channel(9), digital_channel(14)


def spin(app, until, seconds=10.0):
    timer = QtCore.QElapsedTimer()
    timer.start()
    while not until() and timer.elapsed() < seconds * 1000:
        app.processEvents()
        QtCore.QThread.msleep(2)
    return until()


# ---- thresholds and the plan ------------------------------------------------
def test_a_plan_writes_the_chosen_threshold_only_on_a_group_in_use(monkeypatch):
    monkeypatch.setattr("mhodecode.scope.abort.sleep", lambda _seconds: None)
    scope = PlanScope()
    scope.apply_plan(CapturePlan({D9}, force_dc=False, thresholds={1: 3.0, 2: 1.2}))
    assert ":LA:POD2:THReshold 1.2" in scope.sent
    assert not any(command.startswith(":LA:POD1:THReshold") for command in scope.sent)


def test_the_menu_gives_only_ticked_thresholds_and_keeps_them(window):
    menu = window.scope_settings
    assert menu.pod_thresholds() == {}
    box, field = menu.pod_boxes[2]
    assert not field.isEnabled()
    box.setChecked(True)
    field.setValue(2.5)
    assert field.isEnabled() and menu.pod_thresholds() == {2: 2.5}
    assert window.settings.value("pod_threshold_2", type=float) == 2.5
    box.setChecked(False)


def test_a_refused_trigger_stops_the_acquisition_instead_of_waiting_for_ever():
    class Refusing(PlanScope):
        def write(self, command):
            super().write(command)
            self.refused = self.refused or command.startswith(":TRIGger")

        refused = False

        def errors(self):
            refused, self.refused = self.refused, False
            return ['-222,"Data out of range"'] if refused else []

    with pytest.raises(ScopeError):
        Refusing().apply_plan(CapturePlan({1}, trigger=(":TRIGger:MODE IIS",)))


def test_a_bit_field_goes_out_as_separate_commands():
    scope = PlanScope()
    scope.apply_plan(CapturePlan({1}, trigger=(":A 0\n:B 1",)))
    assert ":A 0" in scope.sent and ":B 1" in scope.sent


# ---- protocol triggers ------------------------------------------------------
def pick(window, name):
    menu = window.scope_settings
    menu.protocol.setCurrentIndex(menu.protocol.findData(name))
    return menu.protocol_form()


def choose(combo, data):
    combo.setCurrentIndex(combo.findData(data))


def test_every_protocol_gives_commands_the_trigger_module_knows(window):
    for name, form in window.scope_settings.protocol_forms.items():
        commands = pick(window, name).commands()
        assert commands[0] == f":TRIGger:MODE {name}" and all(line.startswith(":TRIGger:") for command in commands for line in command.split("\n"))
    pick(window, "")


def test_protocol_on_analog_channels_sends_levels_and_on_digital_ones_does_not(window):
    form = pick(window, "IIC")
    assert form.channels() == {1, 2}  # each source starts on a channel of its own
    assert any(command.startswith(":TRIGger:IIC:CLEVel 1.65") for command in form.commands())
    choose(form.widgets["SCL"], "D0")
    choose(form.widgets["SDA"], "D14")
    assert form.channels() == {D0, D14}
    commands = form.commands()
    assert ":TRIGger:IIC:SCL D0" in commands and ":TRIGger:IIC:SDA D14" in commands
    assert not any("LEVel" in command for command in commands)
    assert form.labels["CLEVel"].isHidden() and form.widgets["DLEVel"].isHidden()
    choose(form.widgets["SCL"], "CHANnel1")
    choose(form.widgets["SDA"], "CHANnel2")
    pick(window, "")


def test_fields_of_another_condition_are_neither_shown_nor_sent(window):
    form = pick(window, "IIC")
    choose(form.widgets["WHEN"], "STARt")
    assert form.widgets["ADDRess"].isHidden() and not any(":ADDRess" in command for command in form.commands())
    choose(form.widgets["WHEN"], "ADDRess")
    assert not form.widgets["ADDRess"].isHidden() and any(":ADDRess" in command for command in form.commands())
    choose(form.widgets["WHEN"], "STARt")
    pick(window, "")


def test_a_bad_field_is_logged_and_nothing_starts(window):
    form = pick(window, "IIC")
    choose(form.widgets["WHEN"], "ADDRess")
    form.widgets["ADDRess"].setText("XYZ")
    window.scope_settings.choose((1,))
    window.scope = CaptureScope()
    window._acquire()
    assert window.worker is None and "XYZ" in window.log.toPlainText()
    form.widgets["ADDRess"].setText("0")
    choose(form.widgets["WHEN"], "STARt")
    pick(window, "")
    window.scope = None


def test_protocol_and_channel_conditions_replace_each_other(window):
    menu = window.scope_settings
    messages = []
    menu.message.connect(messages.append)
    menu.set_condition(3, "R")
    pick(window, "RS232")
    assert menu.conditions == {} and len(messages) == 1
    menu.set_condition(3, "F")
    assert menu.protocol_form() is None and menu.conditions == {3: "F"} and len(messages) == 2
    menu.set_condition(3, "")
    pick(window, "RS232")
    menu.follow_the_scope()
    assert menu.protocol_form() is None


def test_a_source_of_the_protocol_stays_on_and_is_waited_for(window, app):
    menu = window.scope_settings
    menu.choose(())
    form = pick(window, "RS232")
    choose(form.widgets["SOURce"], "D9")
    assert menu.selected() == {D9} and not menu.boxes[D9].isEnabled()
    assert "RS232" in menu.toolTip() or "UART" in menu.toolTip()
    choose(form.widgets["SOURce"], "CHANnel1")
    window.scope = CaptureScope()
    window._acquire()
    run_job(window, app)
    sent = window.scope.sent
    assert ":TRIGger:MODE RS232" in sent and ":TRIGger:RS232:BAUD 9600" in sent and "single None None" in sent
    window.scope = None
    pick(window, "")


def test_the_form_comes_back_from_the_settings(window):
    form = pick(window, "SPI")
    form.widgets["DATA"].setText("AB")
    form.widgets["WIDTh"].setValue(12)
    choose(form.widgets["SCL"], "D0")
    settings = window.settings
    assert settings.value("trigger_protocol") == "SPI" and settings.value("protocol_SPI_DATA") == "AB"
    assert settings.value("protocol_SPI_WIDTh", type=float) == 12 and settings.value("protocol_SPI_SCL") == "D0"
    choose(form.widgets["SCL"], "CHANnel1")
    pick(window, "")


# ---- captures kept by Repeat ------------------------------------------------
def wave(channel, values, dt=1.0, t0=0.0, digital=False, **others):
    return Waveform(channel, np.array(values, dtype=np.float32), dt, t0, digital=digital, **others)


def test_a_new_capture_goes_right_after_the_old_samples():
    old = {1: wave(1, [1, 2, 3], t0=-1.0, clipped=0.5, volts_per_div=1.0)}
    new = {1: wave(1, [4, 5], t0=-7.0, clipped=0.1, volts_per_div=2.0)}
    joined, start, trigger_time = segments.join_captures(old, new)
    assert start == 2.0 and trigger_time == 9.0  # 7 after the start of the new capture
    assert joined[1].volts.tolist() == [1, 2, 3, 4, 5] and joined[1].t0 == -1.0
    assert joined[1].clipped == 0.5 and joined[1].volts_per_div == 2.0 and joined[1].volts.dtype == np.float32


def test_channels_keep_their_offset_and_a_short_one_holds_its_level():
    old = {1: wave(1, [0] * 8), D0: wave(D0, [1, 0], dt=2.0, digital=True)}
    new = {1: wave(1, [5] * 8, t0=0.0), D0: wave(D0, [1, 1], dt=2.0, t0=4.0, digital=True)}
    joined, start, _trigger = segments.join_captures(old, new)
    assert start == 8.0 and len(joined[1].volts) == 16
    assert joined[D0].volts.tolist() == [1, 0, 0, 0, 0, 0, 1, 1]


def test_a_digital_channel_flat_in_one_capture_is_not_lost():
    old = {1: wave(1, [0] * 4), D0: wave(D0, [1, 0, 1, 0], digital=True)}
    new = {1: wave(1, [0] * 4), D9: wave(D9, [1, 0, 1, 0], digital=True)}
    joined, _start, _trigger = segments.join_captures(old, new)
    assert joined[D0].volts.tolist() == [1, 0, 1, 0, 0, 0, 0, 0]
    assert joined[D9].volts.tolist() == [1, 1, 1, 1, 1, 0, 1, 0] and joined[D9].t0 == 0.0


def test_only_what_shares_a_time_axis_can_be_joined():
    one = {1: wave(1, [0, 1])}
    assert segments.can_join(one, {1: wave(1, [0, 1, 0])})
    assert not segments.can_join(one, {1: wave(1, [0, 1], dt=2.0)})
    assert not segments.can_join(one, {2: wave(2, [0, 1])})
    assert not segments.can_join(one, {1: wave(1, [0, 1]), 2: wave(2, [0, 1])})
    assert segments.can_join(one, {1: wave(1, [0, 1]), D0: wave(D0, [0, 1], dt=4.0, digital=True)})


def test_splitting_gives_back_each_capture_at_its_place():
    first, second = {1: wave(1, [1, 2, 3], t0=-1.0)}, {1: wave(1, [4, 5], t0=-9.0)}
    joined, start, _trigger = segments.join_captures(first, second)
    parts = segments.split_captures(joined, [start])
    assert [part[1].volts.tolist() for part in parts] == [[1, 2, 3], [4, 5]]
    assert [part[1].t0 for part in parts] == [-1.0, 2.0]
    assert segments.split_captures(joined, []) == [joined]


def test_results_of_captures_merge_without_repeating_notes():
    annotation = Annotation(0.0, 1.0, "row", "DATA", "x")
    merged = segments.merge_results([DecodeResult([annotation], ["n"], {"a": "A"}), DecodeResult([annotation], ["n", "m"], {"b": "B"})])
    assert len(merged.annotations) == 2 and merged.notes == ["n", "m"] and merged.rows == {"a": "A", "b": "B"}


def uart_capture(text, bit=100):
    levels = [1.0] * 20
    for byte in text:
        levels += [0.0] + [float(byte >> position & 1) for position in range(8)] + [1.0, 1.0]
    levels += [1.0] * 20
    return Waveform(1, np.repeat(levels, bit).astype(np.float32) * 3.3, 1e-6, -1e-3, volts_per_div=1.0)


class RepeatScope(CaptureScope):
    def __init__(self, waves):
        super().__init__()
        self.waves = list(waves)

    def fetch(self, channel, progress=None, sample_format="BYTE"):
        return self.waves.pop(0)


def uart_card(window):
    card = window.new_decoder("UART")
    card.signal_rows[0].channel.setCurrentIndex(card.signal_rows[0].channel.findData(1))
    card.param_widgets["baudrate"].setValue(10000)
    return card


def finish(window, app):
    assert spin(app, lambda: window.worker is None and not window.decode_timer.isActive() and not window.decode_pending)


def test_repeat_keeps_samples_marks_them_and_decodes_each_capture(window, app):
    uart_card(window)
    window.scope_settings.choose((1,))
    window.scope = RepeatScope([uart_capture(b"A"), uart_capture(b"B"), uart_capture(b"C")])
    window.repeat_button.setChecked(True)
    window._acquire()
    assert spin(app, lambda: len(window.capture_starts) == 2)
    window.repeat_button.setChecked(False)
    finish(window, app)
    single = len(uart_capture(b"A").volts)
    assert len(window.waves[1].volts) == 3 * single
    lines = [item for plot in window.plots for item in plot.items if isinstance(item, QtWidgets.QGraphicsObject) and type(item).__name__ == "InfiniteLine"]
    assert len(window.trigger_times) == 3 and window.trigger_times[0] == 0.0
    assert len(lines) == 5 * len(window.plots) and {line.value() for line in lines} == {*window.capture_starts, *window.trigger_times}
    assert all(start < trigger for start, trigger in zip(window.capture_starts, window.trigger_times[1:]))
    values = sorted((item.start, item.value) for item in window.result.annotations if item.value in (0x41, 0x42, 0x43))
    assert {value for _start, value in values} == {0x41, 0x42, 0x43}
    starts = [-1.0, *window.capture_starts, 1.0]
    for low, high, value in zip(starts, starts[1:], (0x41, 0x42, 0x43)):
        assert all(low <= start < high for start, found in values if found == value)
    assert not any(item.error for item in window.result.annotations)
    low, high = window.master.viewRange()[0]
    assert low == pytest.approx(window.full_range[0], abs=1e-3) and high == pytest.approx(window.full_range[1], abs=1e-3)
    window.scope = None


def test_a_manual_acquisition_and_a_memory_read_start_anew(window, app):
    window.scope_settings.choose((1,))
    window.waves = {1: square_wave(1)}
    window.capture_starts = [0.5]
    window.scope = CaptureScope()
    window._acquire(False)
    run_job(window, app)
    finish(window, app)
    assert window.capture_starts == [] and len(window.waves[1].volts) == len(square_wave(1).volts)
    window.capture_starts = [0.5]
    window._read_memory()
    run_job(window, app)
    finish(window, app)
    assert window.capture_starts == []
    window.scope = None


def test_a_capture_that_does_not_fit_starts_anew_with_a_note(window, app):
    window.scope_settings.choose((1,))
    window.waves = {1: wave(1, [0, 1], dt=1.0)}
    window.scope = CaptureScope()
    window._acquire(True)
    run_job(window, app)
    finish(window, app)
    assert window.capture_starts == [] and window.waves[1].dt == square_wave(1).dt
    assert "od nowa" in window.log.toPlainText()
    window.scope = None


def test_repeat_stops_at_the_memory_limit_and_keeps_what_it_has(window, app, monkeypatch):
    monkeypatch.setattr(segments, "MAX_KEPT_BYTES", square_wave(1).volts.nbytes)
    window.scope_settings.choose((1,))
    window.waves = {1: square_wave(1)}
    window.scope = CaptureScope()
    window.repeat_button.setChecked(True)
    window._acquire(True)
    run_job(window, app)
    finish(window, app)
    assert not window.repeat_button.isChecked() and window.capture_starts == []
    assert len(window.waves[1].volts) == len(square_wave(1).volts)
    window.scope = None


def test_joined_channels_of_different_rates_reach_a_decoder_at_one_length(window):
    analog = Waveform(1, np.zeros(4001, dtype=np.float32), 1e-6, 0.0, volts_per_div=1.0)
    digital = Waveform(D0, np.zeros(1001, dtype=np.float32), 4e-6, 0.0, digital=True)
    same = window._same_length({"a": analog, "b": digital})
    assert len(same["a"].volts) == len(same["b"].volts) == 4001 and same["b"].dt == pytest.approx(1e-6)


# ---- the screen -------------------------------------------------------------
def test_a_click_maps_to_the_pixels_of_the_scope_also_with_bars_around_the_picture(app):
    view = ScreenView()
    view.resize(1024, 800)  # bars of 100 above and below the 1024 x 600 picture
    touches = []
    view.touched.connect(lambda *touch: touches.append(touch))
    press = QtCore.Qt.LeftButton

    def mouse(kind, x, y, button=press):
        return QtGui.QMouseEvent(kind, QtCore.QPointF(x, y), button, press, QtCore.Qt.NoModifier)

    view.mousePressEvent(mouse(QtCore.QEvent.MouseButtonPress, 10, 10))
    assert touches == []  # no picture yet
    image = QtGui.QImage(1024, 600, QtGui.QImage.Format_RGB888)
    view.show_image(image)
    view.mousePressEvent(mouse(QtCore.QEvent.MouseButtonPress, 10, 50))
    view.mouseMoveEvent(mouse(QtCore.QEvent.MouseMove, 10, 50))
    assert touches == []  # on the bar
    view.mousePressEvent(mouse(QtCore.QEvent.MouseButtonPress, 100, 400))
    view.mouseMoveEvent(mouse(QtCore.QEvent.MouseMove, 2000, 400))
    view.mouseReleaseEvent(mouse(QtCore.QEvent.MouseButtonRelease, 2000, 5000))
    view.mouseMoveEvent(mouse(QtCore.QEvent.MouseMove, 5, 400))
    assert touches == [(MOUSE_DOWN, 100, 300), (MOUSE_MOVE, 1023, 300), (MOUSE_UP, 1023, 599)]
    view.mousePressEvent(mouse(QtCore.QEvent.MouseButtonPress, 100, 400, QtCore.Qt.RightButton))
    assert len(touches) == 3


def h264_units(count=10):
    """A short stream cut into NAL units, as the scope sends them."""
    encoder = av.CodecContext.create("libx264", "w")
    encoder.width, encoder.height, encoder.pix_fmt = 64, 48, "yuv420p"
    encoder.time_base = Fraction(1, 25)
    encoder.options = {"annexb": "1", "tune": "zerolatency", "preset": "ultrafast"}
    units = []
    for number in range(count):
        frame = av.VideoFrame.from_ndarray(np.full((48, 64, 3), number * 20, dtype=np.uint8), format="rgb24").reformat(format="yuv420p")
        frame.pts = number
        units += [bytes(packet) for packet in encoder.encode(frame)]
    units += [bytes(packet) for packet in encoder.encode(None)]
    return units


class FakeScreen:
    """Both sockets of the scope's web page on one local port."""

    def __init__(self, units):
        self.units = units
        self.texts = []
        self.controls = []
        self.displays = []
        self.server = QtWebSockets.QWebSocketServer("test", QtWebSockets.QWebSocketServer.NonSecureMode)
        assert self.server.listen(QtNetwork.QHostAddress.LocalHost)
        self.server.newConnection.connect(self._connected)
        self.host = f"127.0.0.1:{self.server.serverPort()}"

    def _connected(self):
        socket = self.server.nextPendingConnection()
        if socket.requestUrl().path() == "/ws/display":
            self.displays.append(socket)
            socket.textMessageReceived.connect(lambda text: self._asked(socket, text))
        else:
            socket.textMessageReceived.connect(self.controls.append)
            self.control_socket = socket

    def _asked(self, socket, text):
        self.texts.append(text)
        for unit in self.units:
            socket.sendBinaryMessage(unit)


def test_stream_asks_for_the_picture_shows_it_and_sends_touch(app):
    screen = FakeScreen(h264_units())
    stream = ScreenStream(screen.host)
    images = []
    stream.frame_ready.connect(lambda: images.append(stream.take()))
    stream.start()
    try:
        assert spin(app, lambda: images)
        assert json.loads(screen.texts[0]) == {"type": "SET_ENCODER", "size": None, "rate": None}
        assert images[-1].width() == 64 and images[-1].height() == 48
        assert spin(app, lambda: hasattr(screen, "control_socket"))
        stream.touch(MOUSE_DOWN, 260, 555)
        assert spin(app, lambda: screen.controls)
        assert json.loads(screen.controls[0]) == {"type": MOUSE_DOWN, "downDelta": None, "clientX": 260, "clientY": 555}
    finally:
        stream.stop()


def test_a_busy_window_gets_only_the_newest_picture(app):
    stream = ScreenStream("127.0.0.1:1")
    ready = []
    stream.frame_ready.connect(lambda: ready.append(1), QtCore.Qt.DirectConnection)
    first = av.VideoFrame.from_ndarray(np.zeros((4, 4, 3), dtype=np.uint8), format="rgb24")
    stream.deliver(first)
    stream.deliver(first)
    assert len(ready) == 1
    assert stream.take() is not None and stream.take() is None
    stream.deliver(None)
    assert stream.take() is None
    stream.deliver(first)
    assert len(ready) == 2


def test_damaged_data_does_not_end_the_stream_and_silence_reconnects(app, monkeypatch):
    monkeypatch.setattr("mhodecode.screen_stream.SILENCE_LIMIT_S", 0.2)
    screen = FakeScreen([b"\x00\x00\x00\x01\x65" + bytes(range(200))] * 3)
    stream = ScreenStream(screen.host)
    lost = []
    stream.lost.connect(lost.append)
    stream.start()
    try:
        assert spin(app, lambda: len(screen.displays) >= 2)  # connected again after the silence
        assert lost == [screen.host]  # and said so once
        assert stream.isRunning()
    finally:
        stream.stop()


def test_panel_runs_the_stream_only_with_the_tab_shown_and_the_box_ticked(window):
    panel = window.panel

    class Fake:
        host = "127.0.0.1:1"

        def query(self, _text):
            return "0"

        def write(self, _text):
            pass

        def errors(self):
            return []

    panel.attach(Fake())
    try:
        panel.set_active(False)
        assert panel.stream is None
        panel.set_active(True)
        assert panel.stream is not None
        panel.live_check.setChecked(False)
        assert panel.stream is None
        panel.live_check.setChecked(True)
        assert panel.stream is not None
    finally:
        panel.detach()
    assert panel.stream is None


# ---- the trigger's place -----------------------------------------------------
class PretriggerScope(PlanScope):
    """50 MSa/s and 1 M points, so 20 ms of memory. Limits the offset as the scope does at 1 ms per division."""
    limit = -5e-3
    offset = 0.0

    def write(self, command):
        super().write(command)
        if command.startswith(":TIMebase:MAIN:OFFSet "):
            self.offset = max(self.limit, float(command.split()[1]))

    def query(self, command):
        if command == ":ACQuire:SRATe?":
            self.sent.append(command)
            return "5.000000E+7"
        if command == ":TIMebase:MAIN:OFFSet?":
            return f"{self.offset:E}"
        return super().query(command)


@pytest.mark.parametrize("percent, offset", [(50, 0.0), (75, -5e-3), (25, 5e-3), (0, 10e-3), (60, -2e-3)])
def test_the_pretrigger_becomes_the_measured_offset(percent, offset):
    scope = PretriggerScope()
    assert scope.apply_plan(CapturePlan({1}, force_dc=False, pretrigger=percent)) == []
    assert scope.offset == pytest.approx(offset)


def test_the_sample_rate_is_read_only_after_the_scope_runs():
    scope = PretriggerScope()
    scope.apply_plan(CapturePlan({1}, force_dc=False, pretrigger=50))
    assert scope.sent.index(":RUN") < scope.sent.index(":ACQuire:SRATe?")
    assert scope.sent.index(":ACQuire:SRATe?") < next(index for index, command in enumerate(scope.sent) if command.startswith(":TIMebase:MAIN:OFFSet "))


def test_a_pretrigger_the_scope_limits_is_reported_with_what_came_out():
    scope = PretriggerScope()
    notes = scope.apply_plan(CapturePlan({1}, force_dc=False, pretrigger=90))
    assert len(notes) == 1 and "75 %" in notes[0] and "90 %" in notes[0]


def test_without_a_pretrigger_the_offset_is_left_alone():
    scope = PretriggerScope()
    scope.apply_plan(CapturePlan({1}, force_dc=False))
    assert not any("OFFSet" in command or command == ":RUN" for command in scope.sent)


def test_the_menu_gives_the_pretrigger_only_when_ticked_and_an_acquisition_sends_it(window, app):
    class Scope(PretriggerScope, CaptureScope):
        pass

    menu = window.scope_settings
    assert menu.pretrigger() is None and not menu.pretrigger_percent.isEnabled()
    menu.pretrigger_on.setChecked(True)
    menu.pretrigger_percent.setValue(75)
    assert menu.pretrigger() == 75 and menu.pretrigger_percent.isEnabled()
    assert window.settings.value("pretrigger_percent", type=int) == 75
    menu.choose((1,))
    window.scope = Scope()
    window._acquire()
    run_job(window, app)
    assert window.scope.offset == pytest.approx(-5e-3)
    menu.follow_the_scope()
    assert menu.pretrigger() is None


def vertical_lines(plot):
    return [item.value() for item in plot.items if type(item).__name__ == "InfiniteLine" and not item.movable]


def test_a_capture_marks_its_trigger_on_every_row(window, app):
    window.scope_settings.choose((1, 2))
    window.scope = CaptureScope()
    window._acquire()
    run_job(window, app)
    assert window.trigger_times == [0.0] and len(window.plots) >= 2
    assert all(vertical_lines(plot) == [0.0] for plot in window.plots)
    window._plot()  # rows are used again, and must not gather lines
    assert all(vertical_lines(plot) == [0.0] for plot in window.plots)


def test_signals_of_unknown_time_have_no_trigger_line(window):
    window._show_new_waves({1: square_wave(1)})
    assert window.trigger_times == [] and vertical_lines(window.plots[0]) == []


def test_a_saved_file_keeps_the_trigger_and_an_older_one_has_none(window, tmp_path):
    window._show_new_waves({1: square_wave(1)}, trigger_times=(0.0, 0.25))
    marked = tmp_path / "marked.npz"
    window.save_waves_to(str(marked))
    window._show_new_waves({1: square_wave(1)})
    plain = tmp_path / "plain.npz"
    window.save_waves_to(str(plain))
    window.open_waves_from(str(marked))
    assert window.trigger_times == [0.0, 0.25]
    window.open_waves_from(str(plain))
    assert window.trigger_times == []


def test_a_channel_row_names_the_protocol_it_triggers_on(window):
    menu = window.scope_settings
    assert menu.symbol(1) == ""
    menu.protocol.setCurrentIndex(menu.protocol.findData("IIC"))
    sources = menu.protocol_channels()
    assert sources and all(menu.symbol(channel) == "I2C" for channel in sources)
    assert all(menu.symbol(channel) == "" for channel in {1, 2, 3, 4} - sources)
    channel = min(sources)
    assert window._channel_label("", channel).endswith(" I2C")
    label = window._channel_label("a name much too long for a row", channel)
    assert label.endswith(" I2C") and "…" in label and len(label) <= 22
    menu.protocol.setCurrentIndex(0)
    assert menu.symbol(channel) == ""


def test_a_chosen_protocol_shows_at_once_on_the_row(window):
    menu = window.scope_settings
    window._show_new_waves({1: square_wave(1)})
    menu.protocol.setCurrentIndex(menu.protocol.findData("RS232"))
    form = menu.protocol_form()
    assert form is not None
    channel = min(menu.protocol_channels())
    assert channel in window.channel_rows
    ticks = window.channel_rows[channel].getAxis("left")
    assert "UART (RS232)" in str(ticks.labelText) + str(getattr(ticks, "_tickLevels", ""))
    menu.protocol.setCurrentIndex(0)

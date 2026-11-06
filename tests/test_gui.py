"""Window behaviour that needs no oscilloscope, on Qt's offscreen platform."""
import os
import re

import struct
import zlib

import numpy as np
import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt5 import QtCore, QtGui, QtWidgets  # noqa: E402

from mhodecode import generator, i18n, siggen  # noqa: E402
from mhodecode.decoders import SIGROK, SIGROK_ROOTS  # noqa: E402
from mhodecode import abort, rigol_files, trigger  # noqa: E402
from mhodecode.decoder_panel import DECODER_MIME  # noqa: E402
from mhodecode.gui import MainWindow  # noqa: E402
from mhodecode import trigger  # noqa: E402
from mhodecode.channel_controls import DIGITAL, ChannelDialog, ScopeSettings  # noqa: E402
from mhodecode.scope import CapturePlan, Scope, ScopeError, digital_channel  # noqa: E402
from mhodecode.signal import digitize  # noqa: E402
from mhodecode.scope_panel import LiveLink  # noqa: E402
from mhodecode.theme import THEMES  # noqa: E402
from mhodecode.scope import CapturePlan, Waveform  # noqa: E402

POLISH = re.compile("[ąćęłńóśźżĄĆĘŁŃÓŚŹŻ]")


@pytest.fixture(scope="module")
def app():
    yield QtWidgets.QApplication.instance() or QtWidgets.QApplication([])


@pytest.fixture()
def window(app, tmp_path):
    QtCore.QSettings.setPath(QtCore.QSettings.IniFormat, QtCore.QSettings.UserScope, str(tmp_path))
    QtCore.QSettings.setDefaultFormat(QtCore.QSettings.IniFormat)
    created = MainWindow()
    created._set_language("pl")
    yield created
    created._set_language("pl")
    created.close()
    created.deleteLater()
    QtWidgets.QApplication.sendPostedEvents(None, QtCore.QEvent.DeferredDelete)


DECODER_NAMES = {"I2C": "I²C", "I2S": "I²S"}  # as sigrok writes them


def use(window, name):
    window.remove_all_decoders()
    QtWidgets.QApplication.sendPostedEvents(None, QtCore.QEvent.DeferredDelete)
    return window.new_decoder(DECODER_NAMES.get(name, name))


def use_sigrok(window, pd_id):
    window.remove_all_decoders()
    return window.select_sigrok(pd_id)


def active(window):
    return window.active_card


def visible_texts(window):
    texts = [window.windowTitle()]
    for widget in window.findChildren(QtWidgets.QWidget):
        for getter in ("text", "title", "toolTip"):
            if hasattr(widget, getter) and not isinstance(widget, QtWidgets.QLineEdit):
                texts.append(getattr(widget, getter)())
        if isinstance(widget, QtWidgets.QComboBox):
            texts += [widget.itemText(index) for index in range(widget.count())]
        if isinstance(widget, QtWidgets.QTabWidget):
            texts += [widget.tabText(index) for index in range(widget.count())]
    texts += [action.text() for action in window.menuBar().actions()]
    return [text for text in texts if isinstance(text, str)]


def test_english_leaves_no_polish_text_in_any_protocol(window):
    window._set_language("en")
    for protocol in ("UART", "I2C", "SPI", "CAN"):
        use(window, protocol)
        for generated in range(window.gen_protocol.count()):
            window.gen_protocol.setCurrentIndex(generated)
            bad = [text for text in visible_texts(window) if POLISH.search(text)]
            assert not bad, bad
    use_sigrok(window, "eeprom24xx")
    bad = [text for text in visible_texts(window) if POLISH.search(text)]
    assert not bad, bad
    tree = window.decoder_tree
    groups = [tree.topLevelItem(position) for position in range(tree.topLevelItemCount())]
    bad = [item.text(0) for item in groups if POLISH.search(item.text(0))]
    bad += [item.child(position).text(0) for item in groups[2:] for position in range(item.childCount())
            if POLISH.search(item.child(position).text(0))]
    assert not bad, bad
    for mode in window.panel.trigger_rows.values():
        bad = [label.text() for _field, label, _widget in mode if POLISH.search(label.text())]
        assert not bad, bad


def test_language_switch_keeps_the_forms(window):
    use(window, "UART")
    active(window).param_widgets["parity"].setCurrentIndex(active(window).param_widgets["parity"].findData("odd"))
    active(window).signal_rows[0].channel.setCurrentIndex(active(window).signal_rows[0].channel.findData(3))
    window.gen_protocol.setCurrentText("I2C")
    window.gen_line_combos["scl"].setCurrentIndex(window.gen_line_combos["scl"].findData(2))
    window.gen_line_combos["sda"].setCurrentIndex(window.gen_line_combos["sda"].findData(1))
    window.gen_levels[1][1].setValue(1.8)
    window._set_language("en")
    assert active(window).param_widgets["parity"].currentData() == "odd"
    assert active(window).signal_rows[0].channel.currentData() == 3
    assert window.gen_line_combos["scl"].currentData() == 2
    assert window.gen_line_combos["sda"].currentData() == 1
    assert window.gen_levels[1][1].value() == pytest.approx(1.8)
    assert window.connect_button.text() == "Connect"


def test_time_base_is_a_1_2_5_menu(window):
    combo = window.panel.time_scale
    assert isinstance(combo, QtWidgets.QComboBox) and not combo.isEditable()
    labels = [combo.itemText(index) for index in range(5)]
    assert labels == ["500 ps/div", "1 ns/div", "2 ns/div", "5 ns/div", "10 ns/div"]


def test_generator_lines_follow_the_protocol(window):
    window.gen_protocol.setCurrentText("CAN FD")
    assert list(window.gen_line_combos) == ["can"]
    widget = window.gen_param_widgets["differential"]
    widget.setCurrentIndex(widget.findData(True))
    assert list(window.gen_line_combos) == ["can", "can_l"]
    assert window.gen_levels[1][0].value() == 2.5 and window.gen_levels[2][1].value() == 2.5
    assert window.gen_line_combos["can_l"].currentData() == 2


def test_switching_data_format_keeps_the_data(window):
    window.gen_data.setText("48 65 0D")
    window.gen_data_format.setCurrentIndex(window.gen_data_format.findData("ascii"))
    assert window.gen_data.text() == '"He\\r"'
    window.gen_data_format.setCurrentIndex(window.gen_data_format.findData("hex"))
    assert window.gen_data.text() == "0x48 0x65 0x0D"


def test_match_decoder_copies_generator_settings(window):
    window.gen_protocol.setCurrentText("CAN FD")
    window.gen_data.setText("01 02 03 04")
    window._match_decoder()
    assert active(window).decoder.name == "CAN"
    assert active(window).param_widgets["nominal_bitrate"].value() == 500000
    assert active(window).param_widgets["fast_bitrate"].value() == 2000000
    assert window.panel.settings["time_scale"].hold_until > 0
    assert window.panel.edge_setting("SOURce").current() == "CHANnel1"


def test_match_decoder_follows_the_chosen_output(window):
    window.gen_protocol.setCurrentText("UART")
    window.gen_line_combos["tx"].setCurrentIndex(window.gen_line_combos["tx"].findData(2))
    window._match_decoder()
    assert active(window).mapping()["tx"] == 2
    assert window.panel.edge_setting("SOURce").current() == "CHANnel2"


def test_compare_without_generation_says_what_to_do(window):
    window._compare()
    assert window.last_compare_ok is False
    assert window.compare_label.text() == i18n.tr("Najpierw wygeneruj sygnał albo podaj oczekiwane dane.")


def test_compare_with_own_expected_data(window):
    generated = generator.build("UART", b"Hi", {param.key: param.default for param in generator.PROTOCOL_PARAMS["UART"]})
    from mhodecode.signal import from_levels
    from mhodecode.decoders import DECODERS
    traces = {"tx": from_levels(generated.lines["tx"], 1 / generated.sample_rate)}
    window.result = DECODERS["UART"].decode(traces, **generated.decoder_params)
    window.expect_sent.setChecked(False)
    window.expected_format.setCurrentIndex(window.expected_format.findData("ascii"))
    window.expected_data.setText("Hi")
    window._compare()
    assert window.last_compare_ok is True
    window.expected_data.setText("Ho")
    window._compare()
    assert window.last_compare_ok is False
    assert "0x69" in window.compare_label.text() and "0x6F" in window.compare_label.text()


def test_buttons_need_a_connection_and_abort_needs_a_job(window):
    window._refresh_buttons()
    assert not window.gen_button.isEnabled()
    assert not window.receive_button.isEnabled()
    assert not window.panel.controls.isEnabled()
    assert not window.abort_button.isEnabled()
    assert window.compare_button.isEnabled()


def test_every_sigrok_root_builds_its_form(window):
    for pd_id, decoder in SIGROK_ROOTS.items():
        use(window, decoder.name)
        assert active(window).decoder.name == decoder.name
        params = active(window).params()
        for option in SIGROK[pd_id].options:
            assert params[option["id"]] == option["default"], (pd_id, option["id"])


def test_choosing_an_upper_decoder_builds_its_base(window):
    use_sigrok(window, "eeprom24xx")
    assert active(window).decoder.name == SIGROK_ROOTS["i2c"].name
    assert [entry.pd_id for entry in active(window).stack_entries] == ["eeprom24xx"]
    assert active(window).params()["srd_stack"][0][0] == "eeprom24xx"
    use_sigrok(window, "usb_request")
    assert active(window).decoder.name == SIGROK_ROOTS["usb_signalling"].name
    assert [entry.pd_id for entry in active(window).stack_entries] == ["usb_packet", "usb_request"]


def test_stack_add_and_remove(window):
    use(window, SIGROK_ROOTS["i2c"].name)
    choices = fitting(window)
    assert "eeprom24xx" in choices and "midi" not in choices
    window.add_decoder("i2cfilter")
    window.add_decoder("eeprom24xx")
    assert [entry.pd_id for entry in active(window).stack_entries] == ["i2cfilter", "eeprom24xx"]
    active(window).stack_entries[0].group.findChild(QtWidgets.QPushButton).click()
    assert active(window).stack_entries == []


def test_language_switch_keeps_the_stack(window):
    use_sigrok(window, "eeprom24xx")
    active(window).param_widgets["address_format"].setCurrentIndex(
        active(window).param_widgets["address_format"].findData("unshifted"))
    combo = active(window).stack_entries[0].widgets["chip"]
    chosen = combo.itemData(3)
    combo.setCurrentIndex(3)
    window._set_language("en")
    assert active(window).param_widgets["address_format"].currentData() == "unshifted"
    assert active(window).stack_entries[0].pd_id == "eeprom24xx"
    assert active(window).stack_entries[0].widgets["chip"].currentData() == chosen


def fitting(window):
    group = window.decoder_tree.fits_group
    return [group.child(position).data(0, QtCore.Qt.UserRole) for position in range(group.childCount())]


def tree_ids(tree):
    found = []

    def walk(item):
        decoder_id = item.data(0, QtCore.Qt.UserRole)
        if decoder_id and not item.isHidden():
            found.append(decoder_id)
        for position in range(item.childCount()):
            walk(item.child(position))

    for position in range(tree.topLevelItemCount()):
        walk(tree.topLevelItem(position))
    return found


def test_tree_has_every_decoder_in_categories_and_filters(window):
    tree = window.decoder_tree
    ids = tree_ids(tree)
    assert set(SIGROK) <= set(ids) and "ir_irmp" not in ids
    window.decoder_search.setText("eeprom")
    shown = tree_ids(tree)
    assert "eeprom24xx" in shown and "uart" not in shown
    window.decoder_search.clear()
    assert set(SIGROK) <= set(tree_ids(tree))


def test_tree_explains_a_decoder_that_needs_a_base(window):
    tree = window.decoder_tree
    item = tree.findItems("24xx", QtCore.Qt.MatchContains | QtCore.Qt.MatchRecursive)[0]
    tree.setCurrentItem(item)
    assert "i2c → eeprom24xx" in window.decoder_details.text()


def test_group_rows_cannot_be_dragged(window):
    tree = window.decoder_tree
    group = tree.topLevelItem(1)
    assert not group.flags() & QtCore.Qt.ItemIsDragEnabled
    leaf = group.child(0).child(0)
    assert leaf.flags() & QtCore.Qt.ItemIsDragEnabled
    assert bytes(tree.mimeData([leaf]).data(DECODER_MIME)).decode() == leaf.data(0, QtCore.Qt.UserRole)


def drop_on(target, decoder_id):
    data = QtCore.QMimeData()
    data.setData(DECODER_MIME, decoder_id.encode("ascii"))
    position = QtCore.QPoint(5, 5)
    enter = QtGui.QDragEnterEvent(position, QtCore.Qt.CopyAction, data, QtCore.Qt.LeftButton, QtCore.Qt.NoModifier)
    target.dragEnterEvent(enter)
    assert enter.isAccepted()
    target.dropEvent(QtGui.QDropEvent(QtCore.QPointF(position), QtCore.Qt.CopyAction, data, QtCore.Qt.LeftButton, QtCore.Qt.NoModifier))


def test_decoders_add_up_one_under_another(window):
    drop_on(window.graphics, "i2c")
    drop_on(window.graphics, "spi")
    drop_on(window.graphics, "uart")
    assert [card.decoder.name for card in window.cards] == [SIGROK_ROOTS["i2c"].name, "SPI", SIGROK_ROOTS["uart"].name]


def test_the_same_decoder_twice_has_its_own_channels_and_options(window):
    first = window.new_decoder("UART")
    second = window.new_decoder("UART")
    assert first.caption == "UART" and second.caption == "UART #2"
    assert first.signal_rows[0].channel.currentData() != second.signal_rows[0].channel.currentData()
    second.param_widgets["parity"].setCurrentIndex(second.param_widgets["parity"].findData("odd"))
    assert first.params()["parity"] != second.params()["parity"]


def test_drop_on_a_decoder_stacks_there_when_it_fits(window):
    first = use_sigrok(window, "i2c")
    second = window.select_sigrok("uart")
    drop_on(first, "eeprom24xx")
    assert [entry.pd_id for entry in first.stack_entries] == ["eeprom24xx"] and not second.stack_entries
    drop_on(second, "eeprom24xx")  # needs I2C: a decoder of its own instead of a wrong stack
    assert len(window.cards) == 3 and window.cards[2].chain() == ["i2c", "eeprom24xx"]


def test_double_click_stacks_on_the_active_decoder_only_when_it_fits(window):
    use_sigrok(window, "uart")
    item = window.decoder_tree.findItems("24xx", QtCore.Qt.MatchContains | QtCore.Qt.MatchRecursive)[0]
    window._tree_activated(item, 0)
    assert len(window.cards) == 2 and window.cards[1].chain() == ["i2c", "eeprom24xx"]
    window.set_active_card(window.cards[0])
    window.add_decoder("midi")
    assert window.cards[0].chain() == ["uart", "midi"] and len(window.cards) == 2


def test_stack_button_lists_what_fits_and_adds_it(window):
    card = use_sigrok(window, "i2c")
    card._fill_stack_menu()
    action = next(action for action in card.stack_menu.actions() if "24xx" in action.text())
    action.trigger()
    assert card.chain() == ["i2c", "eeprom24xx"]


def test_removing_a_decoder_keeps_the_rest_and_numbers_again(window):
    first, second = window.new_decoder("UART"), window.new_decoder("UART")
    first.delete_button.click()
    assert window.cards == [second] and second.caption == "UART" and window.active_card is second
    second.delete_button.click()
    assert window.cards == [] and window.active_card is None


def two_uart_waves():
    fast, slow = siggen.uart(b"AB", 115200, 16), siggen.uart(b"Z" * 8, 9600, 16)
    return {1: Waveform(1, np.asarray(fast.lines["tx"], dtype=float) * 3.3, 1 / fast.sample_rate, 0.0),
            2: Waveform(2, np.asarray(slow.lines["tx"], dtype=float) * 3.3, 1 / slow.sample_rate, 0.0)}


def values_of(window, card):
    return [item.value for item in window.result.annotations
            if item.row.startswith(f"{card.uid}/") and item.row.endswith(":bin:rx") and item.value is not None]


def test_two_equal_decoders_decode_their_own_channels(window):
    window.waves = two_uart_waves()
    first, second = window.new_decoder("UART"), window.new_decoder("UART")
    first.signal_rows[0].channel.setCurrentIndex(first.signal_rows[0].channel.findData(1))
    second.signal_rows[0].channel.setCurrentIndex(second.signal_rows[0].channel.findData(2))
    second.param_widgets["baudrate"].setValue(9600)
    run_decode(window)
    assert values_of(window, first) == [0x41, 0x42] and values_of(window, second) == [0x5A] * 8
    assert len({key for key in window.lanes}) >= 2
    labels = set(window.result.rows.values())
    assert any(label.startswith("#2 UART") for label in labels)


def test_a_change_decodes_by_itself_and_only_once(window):
    window.waves = two_uart_waves()
    card = window.new_decoder("UART")
    assert window.decode_timer.isActive()
    finish_decoding(window)
    assert values_of(window, card) == [0x41, 0x42]
    card.signal_rows[0].channel.setCurrentIndex(card.signal_rows[0].channel.findData(2))
    assert window.decode_timer.isActive()
    card.param_widgets["baudrate"].setValue(9600)
    finish_decoding(window)
    assert values_of(window, card) == [0x5A] * 8


def test_a_new_decoding_replaces_one_that_is_running(window):
    window.waves = two_uart_waves()
    card = window.new_decoder("UART")
    finish_decoding(window)
    window.decoding, window.worker = True, object()
    abort.clear()
    try:
        window._decode()
        assert window.decode_pending and abort.requested()
    finally:
        abort.clear()
        window.worker, window.decoding, window.decode_pending = None, False, False
    assert card in window.cards


def test_one_decoder_without_channels_leaves_the_others_working(window):
    window.waves = two_uart_waves()
    good = window.new_decoder("UART")
    good.signal_rows[0].channel.setCurrentIndex(good.signal_rows[0].channel.findData(1))
    bad = window.new_decoder("UART")
    bad.signal_rows[0].channel.setCurrentIndex(bad.signal_rows[0].channel.findData(digital_channel(5)))
    run_decode(window)
    assert values_of(window, good) == [0x41, 0x42]
    assert "D5" in bad.status.text() and not bad.status.isHidden()


def test_hiding_a_decoder_or_a_row_takes_it_off_the_view(window):
    window.waves = two_uart_waves()
    first, second = window.new_decoder("UART"), window.new_decoder("UART")
    first.signal_rows[0].channel.setCurrentIndex(first.signal_rows[0].channel.findData(1))
    second.signal_rows[0].channel.setCurrentIndex(second.signal_rows[0].channel.findData(2))
    run_decode(window)
    second.shown.setChecked(False)
    assert [key for key in window.lanes if key.startswith(f"{second.uid}/")] == [f"{second.uid}/"]  # the trace stays, empty
    assert all(item.row.startswith(f"{first.uid}/") for item in window.model.rows)
    second.shown.setChecked(True)
    key = next(key for key in window.lanes if key.startswith(f"{second.uid}/"))
    second.set_row_shown(key, False)
    assert key not in window.lanes
    second._fill_rows_menu()
    assert any(not action.isChecked() for action in second.rows_menu.actions())


def test_no_decode_button_any_more(window):
    assert not hasattr(window, "decode_button")
    assert i18n.tr("Dekoduj ponownie") not in visible_texts(window)


def test_language_switch_keeps_all_decoders(window):
    first = use(window, "UART")
    first.param_widgets["parity"].setCurrentIndex(first.param_widgets["parity"].findData("odd"))
    first.shown.setChecked(False)
    second = window.select_sigrok("eeprom24xx")
    window._set_language("en")
    assert window.cards == [first, second]
    assert first.params()["parity"] == "odd" and not first.shown.isChecked()
    assert second.chain() == ["i2c", "eeprom24xx"]


def test_repeat_acquires_again_after_decoding_until_stopped(window):
    window.scope = object()
    window.repeat_button.setChecked(True)
    try:
        window._decoded([])
        assert window.after_job is not None
        window.after_job = None
        window.repeat_button.setChecked(False)
        window._decoded([])
        assert window.after_job is None
        window.repeat_button.setChecked(True)
        window._job_failed("x")
        assert not window.repeat_button.isChecked()
    finally:
        window.scope = None


def test_view_takes_a_drop_through_its_viewport(window):
    data = QtCore.QMimeData()
    data.setData(DECODER_MIME, b"can")
    viewport = window.graphics.viewport()
    assert viewport.acceptDrops()
    position = QtCore.QPoint(5, 5)
    enter = QtGui.QDragEnterEvent(position, QtCore.Qt.CopyAction, data, QtCore.Qt.LeftButton, QtCore.Qt.NoModifier)
    QtWidgets.QApplication.sendEvent(viewport, enter)
    assert enter.isAccepted()
    drop = QtGui.QDropEvent(QtCore.QPointF(position), QtCore.Qt.CopyAction, data, QtCore.Qt.LeftButton, QtCore.Qt.NoModifier)
    QtWidgets.QApplication.sendEvent(viewport, drop)
    assert active(window).decoder.name == "CAN"


def test_scope_tab_hides_the_panels_and_brings_them_back(window):
    window.show()
    window.center_tabs.setCurrentIndex(1)
    assert not window.sidebar_dock.isVisible() and not window.results_dock.isVisible()
    window.center_tabs.setCurrentIndex(0)
    assert window.sidebar_dock.isVisible() and window.results_dock.isVisible()


def test_closing_on_the_scope_tab_does_not_remember_hidden_panels(window):
    window.show()
    window.center_tabs.setCurrentIndex(1)
    window.close()
    assert window.center_tabs.currentIndex() == 0 and not window.sidebar_dock.isHidden()


def test_other_drags_are_not_accepted(window):
    data = QtCore.QMimeData()
    data.setText("eeprom24xx")
    enter = QtGui.QDragEnterEvent(QtCore.QPoint(1, 1), QtCore.Qt.CopyAction, data, QtCore.Qt.LeftButton, QtCore.Qt.NoModifier)
    enter.ignore()
    window.graphics.dragEnterEvent(enter)
    assert not enter.isAccepted()


def finish_decoding(window):
    while window.decode_timer.isActive() or window.worker is not None or window.decode_pending:
        QtWidgets.QApplication.processEvents()
    QtWidgets.QApplication.processEvents()


def run_decode(window):
    window._decode()
    while window.worker is not None:
        window.worker.wait(10)
        QtWidgets.QApplication.processEvents()
    QtWidgets.QApplication.processEvents()


def uart_wave(data, rate):
    signal = siggen.uart(data, rate, 16)
    volts = np.asarray(signal.lines["tx"], dtype=float) * 3.3
    return {1: Waveform(1, volts, 1 / signal.sample_rate, 0.0)}


def test_stack_decodes_and_plots_through_the_window(window):
    window.waves = uart_wave(bytes([0x90, 0x3C, 0x40]), 31250)
    use_sigrok(window, "midi")
    assert active(window).decoder.name == SIGROK_ROOTS["uart"].name
    next(row for row in active(window).signal_rows if row.spec.key == "rx").channel.setCurrentIndex(1)
    active(window).param_widgets["baudrate"].setValue(31250)
    run_decode(window)
    assert any("note on" in item.text for item in window.model.rows)
    assert any("/midi:" in key for key in window.lanes)
    filters = [window.row_filter.itemData(index) for index in range(window.row_filter.count())]
    assert filters[0] is None and all(key in window.result.rows for key in filters[1:])
    assert window.row_filter_bar.isVisibleTo(window)
    wanted = next(key for key in filters[1:] if "/midi:" in key)
    window.row_filter.setCurrentIndex(window.row_filter.findData(wanted))
    assert window.model.rows and all(item.row == wanted for item in window.model.rows)


def test_panel_fits_its_width_with_every_decoder(window):
    # Against the built-in decoder's width, because offscreen fonts are much wider than real ones.
    use(window, "UART")
    limit = window.decoder_page.minimumSizeHint().width()
    too_wide = {}
    for pd_id, decoder in SIGROK_ROOTS.items():
        use(window, decoder.name)
        needed = window.decoder_page.minimumSizeHint().width()
        if needed > limit:
            too_wide[pd_id] = needed
        for upper in fitting(window):
            active(window).push(upper)
            needed = window.decoder_page.minimumSizeHint().width()
            if needed > limit:
                too_wide[f"{pd_id} + {upper}"] = needed
            active(window).pop(0)
    assert not too_wide, too_wide


def i2c_waves():
    signal = siggen.i2c([(0x50, False, b"\x10\xab")], 400000)
    dt = 1 / signal.sample_rate
    return {1: Waveform(1, np.asarray(signal.lines["scl"], dtype=float) * 3.3, dt, 0.0),
            2: Waveform(2, np.asarray(signal.lines["sda"], dtype=float) * 3.3, dt, 0.0)}


def decoded_uart(window):
    window.waves = uart_wave(b"AB", 9600)
    use(window, "UART").param_widgets["baudrate"].setValue(9600)
    run_decode(window)


def curve_points(plot):
    return plot.listDataItems()[0].getOriginalDataset()


def test_log_lives_in_its_own_window_opened_from_the_menu(window):
    assert window.log_window.isWindow() and not window.log_window.isVisible()
    assert window.log_window.isAncestorOf(window.log) and not window.centralWidget().isAncestorOf(window.log)
    menus = {action.text(): action.menu() for action in window.menuBar().actions()}
    assert "Log" not in [action.text() for action in menus["Widok"].actions()]
    entry = next(action for action in menus["Zaawansowane"].actions() if action.text() == "Log")
    entry.trigger()
    assert window.log_window.isVisible()
    window.log_window.close()
    assert not window.log_window.isVisible()


def test_status_bar_shows_errors_from_the_closed_log(window):
    window._log(i18n.tr("BŁĄD: {message}", message="x"))
    assert window.last_message.text() == "BŁĄD: x"
    assert THEMES[window.theme_code].error_text in window.last_message.styleSheet()
    window._log("zwykły")
    assert window.last_message.styleSheet() == ""
    assert window.log.toPlainText().splitlines()[-1] == "zwykły"


def test_english_error_is_marked_as_well(window):
    window._set_language("en")
    window._log(i18n.tr("BŁĄD: {message}", message="x"))
    assert THEMES[window.theme_code].error_text in window.last_message.styleSheet()


def test_theme_switch_changes_palette_and_plot_and_is_remembered(window):
    window.waves = uart_wave(b"A", 9600)
    use(window, "UART")
    run_decode(window)
    window.master.setXRange(0.0, 1e-4, padding=0)
    window.set_theme("dark")
    app = QtWidgets.QApplication.instance()
    assert app.palette().window().color().name() == THEMES["dark"].palette["Window"]
    assert window.graphics.backgroundBrush().color().name() == THEMES["dark"].canvas
    assert window.master.viewRange()[0] == pytest.approx([0.0, 1e-4])
    assert window.settings.value("theme") == "dark"
    window.set_theme("light")
    assert app.palette().window().color().name() != THEMES["dark"].palette["Window"]
    assert MainWindow().theme_code == "light"


def test_saved_theme_is_used_by_a_new_window(window):
    window.set_theme("dark")
    other = MainWindow()
    assert other.theme_code == "dark" and other.theme_actions["dark"].isChecked()
    window.set_theme("light")


def test_logic_view_draws_the_thresholded_levels(window):
    window.waves = uart_wave(b"A", 9600)
    use(window, "UART")
    window.display_mode.setCurrentIndex(window.display_mode.findData("logic"))
    run_decode(window)
    x, y = curve_points(window.plots[0])
    wave = window.waves[1]
    assert set(np.unique(y)) == {0.0, 1.0}
    assert x[0] == pytest.approx(wave.t0) and x[-1] == pytest.approx(wave.t0 + (len(wave.volts) - 1) * wave.dt)
    assert np.all(np.diff(x) >= 0)
    # Every drawn point agrees with the samples at that time.
    indices = np.round((x - wave.t0) / wave.dt).astype(int)
    # At an edge the two points share one time, so only the points between edges are compared.
    middles = (indices[::2] + indices[1::2]) // 2
    assert np.array_equal(wave.volts[middles] > 1.65, y[::2] == 1.0)


def test_logic_view_of_a_flat_signal_is_a_line(window):
    window.waves = {1: Waveform(1, np.full(1000, 3.3, dtype=np.float32), 1e-6, 0.0)}
    use(window, "UART")
    run_decode(window)
    x, y = curve_points(window.plots[0])
    assert list(x) == pytest.approx([0.0, 999e-6]) and set(y) == {1.0}


def test_analog_view_draws_the_samples(window):
    window.waves = uart_wave(b"A", 9600)
    use(window, "UART")
    window.display_mode.setCurrentIndex(window.display_mode.findData("analog"))
    run_decode(window)
    _x, y = curve_points(window.plots[0])
    assert np.array_equal(y, window.waves[1].volts)
    window.display_mode.setCurrentIndex(window.display_mode.findData("logic"))


def test_channels_without_a_signal_are_shown_too(window):
    waves = uart_wave(b"A", 9600)
    waves[3] = Waveform(3, waves[1].volts, waves[1].dt, waves[1].t0)
    window.waves = waves
    window.scope_settings.choose((1, 3))
    use(window, "UART")
    run_decode(window)
    # RX, CH3 with no signal assigned, and the UART's lanes.
    assert len(window.plots) == 2 + len(window.lanes) and window.lanes


def test_new_capture_shows_waves_and_clears_old_results(window):
    decoded_uart(window)
    assert window.model.rows
    window.result_search.setText("zzz")
    window.result_search.clear()
    window.scope_settings.choose((1, 2))
    window._show_new_waves(i2c_waves())
    assert window.model.rows == [] and list(window.lanes) == [f"{window.cards[0].uid}/"]  # an empty trace stays
    assert len(window.plots) == 3 and window.master is not None


def test_view_stays_where_it_was_after_decoding_again(window):
    decoded_uart(window)
    window.master.setXRange(1e-4, 2e-4, padding=0)
    run_decode(window)
    assert window.master.viewRange()[0] == pytest.approx([1e-4, 2e-4])
    window._show_new_waves(i2c_waves())
    assert window.master.viewRange()[0] == pytest.approx(list(window.full_range))


def test_zoom_buttons(window):
    decoded_uart(window)
    low, high = window.full_range
    window.zoom_in_button.click()
    (first, last), _y = window.master.viewRange()
    assert last - first == pytest.approx((high - low) / 2)
    assert (first + last) / 2 == pytest.approx((low + high) / 2)
    window.zoom_fit_button.click()
    assert window.master.viewRange()[0] == pytest.approx([low, high])
    window.zoom_out_button.click()
    assert window.master.viewRange()[0] == pytest.approx([low, high])  # the capture's ends are the limit


def test_cursors_measure_and_follow_a_new_plot(window):
    decoded_uart(window)
    window.master.setXRange(0.0, 3e-3, padding=0)
    window.cursors_button.setChecked(True)
    assert len(window.cursor_lines[0]) == len(window.plots)
    assert window.cursor_positions == pytest.approx([1e-3, 2e-3])
    window.cursor_lines[1][0].setValue(1.5e-3)
    assert [line.value() for line in window.cursor_lines[1]] == pytest.approx([1.5e-3] * len(window.plots))
    assert "Δ 500 µs" in window.cursor_readout.text() and "1/Δ 2 kHz" in window.cursor_readout.text()
    run_decode(window)
    assert len(window.cursor_lines[0]) == len(window.plots)
    assert window.cursor_positions == pytest.approx([1e-3, 1.5e-3])
    window.cursors_button.setChecked(False)
    assert window.cursor_lines == [[], []] and window.cursor_readout.text() == ""


def test_plotting_again_uses_the_same_rows(window):
    decoded_uart(window)
    window.cursors_button.setChecked(True)  # a row destroyed after holding cursors crashed a later paint
    rows = {id(plot) for plot in window.plots}
    assert set(map(id, window.lanes.values())) <= rows
    for mode in ("analog", "logic", "analog"):
        window.display_mode.setCurrentIndex(window.display_mode.findData(mode))
        shown = window.plots
        assert len(shown) + len(window.spare_rows) == len(rows)  # no row twice, and none lost
        assert {id(plot) for plot in (*shown, *window.spare_rows)} == rows
        assert [plot.getAxis("top").isVisible() for plot in shown] == [True] + [False] * (len(shown) - 1)
        assert all(plot is window.master or plot.getViewBox().linkedView(0) is window.master.getViewBox()
                   for plot in shown)
    window.plots[0].getViewBox().updateAutoRange()  # otherwise done at the next paint
    assert window.plots[0].getViewBox().viewRange()[1][1] > 3.0  # analog again: volts, not the logic row's range
    window.cursors_button.setChecked(False)


def test_rows_keep_their_height_after_plotting_again(window):
    window.resize(1600, 1000)
    window.show()
    decoded_uart(window)
    for mode in ("logic", "analog", "logic"):
        window.display_mode.setCurrentIndex(window.display_mode.findData(mode))
        for _ in range(10):
            QtWidgets.QApplication.processEvents()
        assert window.graphics.ci.size().height() == pytest.approx(window.graphics.height(), abs=2)
        assert [plot.size().height() for plot in window.plots] == pytest.approx(
            [plot.maximumHeight() for plot in window.plots], abs=2)
    window.hide()


def test_rows_used_again_keep_their_labels_readable_in_the_new_theme(window):
    decoded_uart(window)
    window.display_mode.setCurrentIndex(window.display_mode.findData("analog"))
    window.display_mode.setCurrentIndex(window.display_mode.findData("logic"))
    for code in ("dark", "light"):
        window.set_theme(code)
        text = QtGui.QColor(window.theme.canvas_text).name()
        for lane in window.lanes.values():
            pen = lane.getAxis("left").textPen()
            assert pen.style() != QtCore.Qt.NoPen and pen.color().name() == text
        assert window.master.getAxis("top").textPen().color().name() == text
        assert window.master.getAxis("top").pen().color().name() == text
    window.set_theme("light")


class FakeClick:
    def __init__(self, button, position):
        self._button, self._position = button, position

    def button(self):
        return self._button

    def scenePos(self):
        return self._position

    def screenPos(self):
        return QtCore.QPointF(0, 0)


def test_a_decoder_has_its_trace_at_once_without_any_data(window):
    assert not window.waves and not window.lanes
    card = window.new_decoder("UART")
    assert list(window.lanes) == [f"{card.uid}/"] and window.lanes[f"{card.uid}/"] in window.plots
    assert window.lanes[f"{card.uid}/"].getAxis("left")._tickLevels == [[(0.5, "UART")]]
    second = window.new_decoder("UART")
    assert f"{second.uid}/" in window.lanes and window.lanes[f"{second.uid}/"].getAxis("left")._tickLevels == [[(0.5, "UART #2")]]
    window.remove_decoder(second)
    assert list(window.lanes) == [f"{card.uid}/"]
    window.remove_decoder(card)
    assert not window.lanes


def test_a_trace_says_what_its_decoder_lacks(window):
    window.new_decoder("I²C").signal_rows[0].channel  # required channels are assigned by default
    card = window.select_sigrok("uart")
    for row in card.signal_rows:
        row.channel.setCurrentIndex(row.channel.findData(None))
    lacking = card.problem
    assert lacking and any(note.text() == lacking for note in window.row_notes)
    card.signal_rows[0].channel.setCurrentIndex(1)
    assert not card.problem and not any(note.text() == lacking for note in window.row_notes)


def test_settings_open_in_a_window_of_their_own_from_the_trace(window):
    window.resize(1400, 900)
    window.show()
    card = window.new_decoder("UART")
    lane = window.lanes[f"{card.uid}/"]
    for _ in range(10):
        QtWidgets.QApplication.processEvents()
    assert card.config_window.isWindow() and not card.config_window.isVisible()
    assert card.config_window.windowTitle() == "UART"
    window._scene_clicked(FakeClick(QtCore.Qt.RightButton, lane.vb.sceneBoundingRect().center()))
    assert window.lane_menu.isVisible() and not card.config_window.isVisible()
    window.lane_settings_action.trigger()
    window.lane_menu.close()
    assert card.config_window.isVisible()
    card.config_window.close()
    window._scene_clicked(FakeClick(QtCore.Qt.LeftButton, lane.vb.sceneBoundingRect().center()))
    assert not card.config_window.isVisible()  # left on the trace itself picks an annotation
    window._scene_clicked(FakeClick(QtCore.Qt.LeftButton, lane.getAxis("left").sceneBoundingRect().center()))
    assert card.config_window.isVisible()  # left on the label, the way PulseView opens them
    card.delete_button.click()
    assert window.cards == [] and not window.lanes
    window.hide()


def test_a_real_right_click_on_the_trace_can_delete_its_decoder(window):
    from PyQt5 import QtTest
    window.resize(1400, 900)
    window.show()
    card = window.new_decoder("UART")
    for _ in range(10):
        QtWidgets.QApplication.processEvents()
    lane = window.lanes[f"{card.uid}/"]
    place = window.graphics.mapFromScene(lane.vb.sceneBoundingRect().center())
    QtTest.QTest.mouseClick(window.graphics.viewport(), QtCore.Qt.RightButton, pos=place)
    for _ in range(10):
        QtWidgets.QApplication.processEvents()
    assert window.lane_menu.isVisible() and window.lane_menu_card is card
    window.lane_delete_action.trigger()
    window.lane_menu.close()
    for _ in range(10):
        QtWidgets.QApplication.processEvents()
    assert window.cards == [] and not window.lanes
    window.hide()


def test_the_trace_menu_deletes_the_decoder_it_was_opened_on(window):
    window.resize(1400, 900)
    window.show()
    first = window.new_decoder("UART")
    second = window.new_decoder("I²C")
    for _ in range(10):
        QtWidgets.QApplication.processEvents()
    lane = window.lanes[f"{first.uid}/"]
    window._scene_clicked(FakeClick(QtCore.Qt.RightButton, lane.getAxis("left").sceneBoundingRect().center()))
    assert window.lane_menu.isVisible() and not first.config_window.isVisible()  # on the label as well
    window.lane_delete_action.trigger()
    window.lane_menu.close()
    assert window.cards == [second] and list(window.lanes) == [f"{second.uid}/"]
    window.hide()


def drag(view, start, end):
    """Press, several moves and a release, as a hand does: a drag starts only after some distance,
    and pyqtgraph drops moves that come faster than its rate limit."""
    def send(kind, place, button, buttons):
        QtWidgets.QApplication.sendEvent(view.viewport(), QtGui.QMouseEvent(
            kind, QtCore.QPointF(place), QtCore.QPointF(view.viewport().mapToGlobal(place)), button, buttons, QtCore.Qt.NoModifier))

    left = QtCore.Qt.LeftButton
    send(QtCore.QEvent.MouseButtonPress, start, left, left)
    for step in range(1, 11):
        QtCore.QThread.msleep(15)
        send(QtCore.QEvent.MouseMove, start + (end - start) * step / 10, QtCore.Qt.NoButton, left)
    send(QtCore.QEvent.MouseButtonRelease, end, left, QtCore.Qt.NoButton)


def cursors_on(window):
    window.resize(1400, 900)
    window.show()
    window.waves = {1: Waveform(1, np.zeros(1000, dtype=np.float32), 3e-6, 0.0)}
    window.new_decoder("UART")
    window.zoom_fit()
    window.cursors_button.setChecked(True)
    for _ in range(10):
        QtWidgets.QApplication.processEvents()


def test_cursors_have_a_strip_with_time_and_frequency_between_them(window):
    from mhodecode.cursors import span_text
    cursors_on(window)
    view = window._cursor_view()
    strip = window.cursor_strip.sceneBoundingRect()
    assert window.cursor_strip.scene() is window.graphics.scene() and window.cursor_overlay.isVisible()
    assert strip.bottom() <= window.master.getAxis("top").sceneBoundingRect().top() + 1  # above the time axis
    assert view.strip_top == strip.top()
    assert view.text == span_text(window.cursor_positions[1] - window.cursor_positions[0])
    assert span_text(1e-3) == "1 ms / 1 kHz" and span_text(0.0) == "0 s"
    assert view.positions[0] < view.positions[1] and view.left < view.positions[0] and view.positions[1] < view.right
    assert view.bottom == max(plot.vb.sceneBoundingRect().bottom() for plot in window.plots)
    window.grab()  # the painting itself runs
    window.cursors_button.setChecked(False)
    assert window.cursor_strip.scene() is None and not window.cursor_overlay.isVisible() and window._cursor_view() is None
    window.hide()


def test_cursor_lines_and_flags_can_be_dragged_through_the_overlay(window):
    cursors_on(window)
    view = window.graphics
    first = window.cursor_positions[0]
    second_x = window._scene_x(window.cursor_positions[1])
    lane = window.lanes[next(iter(window.lanes))].vb.sceneBoundingRect()
    drag(view, view.mapFromScene(QtCore.QPointF(second_x, lane.center().y())),
         view.mapFromScene(QtCore.QPointF(second_x + 100, lane.center().y())))
    assert window._scene_x(window.cursor_positions[1]) == pytest.approx(second_x + 100, abs=3)
    assert window.cursor_positions[0] == first
    assert [line.value() for line in window.cursor_lines[1]] == pytest.approx([window.cursor_positions[1]] * len(window.plots))
    strip_y = window.cursor_strip.sceneBoundingRect().center().y()
    first_x = window._scene_x(first)
    drag(view, view.mapFromScene(QtCore.QPointF(first_x, strip_y)), view.mapFromScene(QtCore.QPointF(first_x - 60, strip_y)))
    assert window._scene_x(window.cursor_positions[0]) == pytest.approx(first_x - 60, abs=3)
    window.hide()


def test_flags_of_close_cursors_stand_side_by_side():
    from mhodecode.cursors import flag_centers
    assert flag_centers((100.0, 200.0), 10.0) == (100.0, 200.0)
    assert flag_centers((100.0, 104.0), 10.0) == (92.0, 112.0)
    assert flag_centers((104.0, 100.0), 10.0) == (112.0, 92.0)  # A stays on its own side
    assert flag_centers((100.0, 100.0), 10.0) == (90.0, 110.0)


def test_a_cursor_is_not_dashed_or_thin(window):
    cursors_on(window)
    assert window.cursor_lines[0][0].hoverPen.width() > 2
    from mhodecode.cursors import LINE_WIDTH
    assert LINE_WIDTH == 2
    window.hide()


def test_data_and_protocol_fields_differ_on_bars_and_in_the_table(window):
    window.waves = uart_wave(b"A", 9600)
    use(window, "UART").param_widgets["baudrate"].setValue(9600)
    run_decode(window)
    items = [item for item in window.result.annotations if not item.hidden and not item.detail]
    data = next(item for item in items if item.kind == "rx-data")
    start = next(item for item in items if item.kind == "rx-start")
    assert not data.control and start.control and data.text == "0x41"
    model = window.model
    data_row, start_row = model.positions[id(data)], model.positions[id(start)]
    assert model.data(model.index(data_row, 4), QtCore.Qt.FontRole).bold()
    assert model.data(model.index(start_row, 4), QtCore.Qt.FontRole) is None
    assert model.data(model.index(start_row, 4), QtCore.Qt.ForegroundRole).color() == QtGui.QColor(window.theme.control_text)
    assert model.data(model.index(data_row, 4), QtCore.Qt.ForegroundRole) is None


def test_text_that_fits_in_no_form_is_left_out(window):
    window.resize(1400, 900)
    window.show()
    window.waves = uart_wave(b"AB", 9600)
    use(window, "UART").param_widgets["baudrate"].setValue(9600)
    run_decode(window)
    for _ in range(10):
        QtWidgets.QApplication.processEvents()
    width = window.master.vb.width()
    full = window.master.viewRange()[0]
    for key, texts in window.lane_text.items():
        for text in texts:
            assert text.textItem.boundingRect().width() <= width
    window.master.setXRange(*full, padding=0)
    window.resize(500, 900)
    for _ in range(10):
        QtWidgets.QApplication.processEvents()
    window._update_text()
    for key, lane in window.lanes.items():
        for text in window.lane_text[key]:
            middle = text.pos().x()
            items = window.lane_items[key][False] + window.lane_items[key][True]
            owner = next(item for item in items if item.start <= middle <= item.end)
            room = (owner.end - owner.start) / (full[1] - full[0]) * lane.vb.width()
            assert QtGui.QFontMetricsF(text.textItem.font()).horizontalAdvance(text.toPlainText()) <= room
    window.hide()


def test_the_response_check_moved_from_the_generator_to_the_advanced_menu(window):
    generator_buttons = window.generator_page.findChildren(QtWidgets.QPushButton)
    for button in (window.receive_button, window.compare_button, window.match_button):
        assert button not in generator_buttons
    assert window.gen_button in generator_buttons and window.afg_off_button in generator_buttons
    advanced = next(action.menu() for action in window.menuBar().actions() if action.text() == "Zaawansowane")
    assert window.test_action in advanced.actions()
    assert window.test_window.isWindow() and not window.test_window.isVisible()
    window.test_action.trigger()
    assert window.test_window.isVisible()
    for button in (window.receive_button, window.compare_button, window.match_button):
        assert window.test_window.isAncestorOf(button)
    window.test_window.close()


def test_delete_button_says_what_it_does_in_words(window):
    card = window.new_decoder("UART")
    assert isinstance(card.delete_button, QtWidgets.QPushButton)
    assert card.delete_button.text() == "Usuń dekoder" and card.delete_button.icon().isNull()
    card.delete_button.click()
    assert window.cards == []


def test_every_button_says_what_it_does(window):
    """A check box without text describes a form row, so its label beside it says that already.
    The buttons left out belong to Qt's own widgets: tab scrolling, clearing a field, the table's corner."""
    card = window.new_decoder("I²C")
    window.add_decoder("i2cdemux", card)
    assert card.chain() == ["i2c", "i2cdemux"]
    silent = []
    for root in (window, window.log_window, card.config_window):
        for button in root.findChildren(QtWidgets.QAbstractButton):
            if button.objectName().startswith("qt_") or isinstance(
                    button.parent(), (QtWidgets.QTabBar, QtWidgets.QLineEdit, QtWidgets.QTableView)):
                continue
            if isinstance(button, QtWidgets.QCheckBox) and not button.text():
                continue
            if not button.toolTip():
                silent.append(button.text() or button.objectName() or type(button).__name__)
    assert not silent, silent


def test_button_tips_change_with_the_language(window):
    try:
        window._set_language("en")
        assert window.connect_button.toolTip() == "Connects to the scope at the given IP address, or disconnects."
        assert window.lane_delete_action.text() == "Delete decoder"
    finally:
        window._set_language("pl")
    assert window.lane_delete_action.text() == "Usuń dekoder"


def test_a_decoder_dropped_on_a_trace_goes_on_its_stack(window):
    window.resize(1400, 900)
    window.show()
    first = window.new_decoder("I²C")
    other = window.new_decoder("UART")
    for _ in range(10):
        QtWidgets.QApplication.processEvents()
    window._decoder_dropped_on_view("i2cdemux", window.lanes[f"{first.uid}/"].vb.sceneBoundingRect().center())
    assert first.chain() == ["i2c", "i2cdemux"] and other.chain() == ["uart"]
    window.hide()


def test_tree_sorts_by_parent_protocol_and_remembers_it(window):
    tree = window.decoder_tree
    window.decoder_sort.setCurrentIndex(window.decoder_sort.findData("parent"))
    assert tree.sort_mode == "parent" and window.settings.value("decoder_sort") == "parent"
    i2c = next(tree.topLevelItem(index) for index in range(tree.topLevelItemCount())
               if tree.topLevelItem(index).data(0, QtCore.Qt.UserRole) == "i2c")
    children = {i2c.child(index).data(0, QtCore.Qt.UserRole) for index in range(i2c.childCount())}
    assert {"i2cdemux", "eeprom24xx"} <= children
    roots = {tree.topLevelItem(index).data(0, QtCore.Qt.UserRole) for index in range(tree.topLevelItemCount())}
    assert "i2cdemux" not in roots
    window.decoder_search.setText("eeprom24")
    assert not i2c.isHidden() and i2c.isExpanded()
    window.decoder_search.clear()
    window.decoder_sort.setCurrentIndex(window.decoder_sort.findData("category"))
    assert tree.sort_mode == "category"


def test_clicking_a_bar_selects_its_table_row(window):
    decoded_uart(window)
    item = window.model.rows[0]
    found = window.select_annotation(item.row, (item.start + item.end) / 2)
    assert found is item
    assert window.table.selectionModel().selectedRows()[0].row() == window.model.rows.index(item)


def test_clicking_between_bars_selects_nothing(window):
    window.waves = uart_wave(b"AB", 9600)
    use(window, "UART")
    run_decode(window)
    key, lane = next(iter(window.lanes.items()))
    assert window.select_annotation(key, window.full_range[0] - 1) is None


def test_results_search_and_errors_only(window):
    decoded_uart(window)
    total = len(window.model.rows)
    assert total > 1
    wanted = window.model.rows[0].kind
    window.result_search.setText(wanted.upper())
    assert window.model.rows and all(wanted == item.kind or wanted.lower() in item.text.lower()
                                     for item in window.model.rows)
    assert window.result_count.text().endswith(f"/ {total:,}")
    window.result_search.setText("no such text")
    assert window.model.rows == [] and window.result_count.text() == f"0 / {total:,}"
    window.result_search.clear()
    assert len(window.model.rows) == total
    window.result_errors.setChecked(True)
    assert window.model.rows == []
    window.result_errors.setChecked(False)
    assert len(window.model.rows) == total


def test_threshold_level_appears_only_for_a_manual_threshold(window):
    window.show()
    row = use(window, "UART").signal_rows[0]
    assert row.level.isHidden()
    row.auto.setChecked(False)
    assert not row.level.isHidden()
    window.hide()


def test_manual_threshold_is_used_by_the_logic_view(window):
    volts = np.concatenate((np.full(500, 0.0), np.full(500, 1.0), np.full(500, 3.3))).astype(np.float32)
    window.waves = {1: Waveform(1, volts, 1e-6, 0.0)}
    use(window, "UART")
    row = active(window).signal_rows[0]
    row.channel.setCurrentIndex(row.channel.findData(1))
    row.auto.setChecked(False)
    row.level.setValue(0.5)
    window._plot()
    x, y = curve_points(window.plots[0])
    assert list(y) == [0.0, 0.0, 1.0, 1.0]
    assert x[1] == pytest.approx(500e-6, abs=2e-6)


def test_menu_has_the_file_actions(window):
    files = next(action.menu() for action in window.menuBar().actions() if action.text() == "Plik")
    texts = [action.text() for action in files.actions()]
    assert {"Otwórz przebiegi", "Zapisz przebiegi", "Eksport wyników CSV", "Zakończ"} <= set(texts)


# ---- digital channels ---------------------------------------------------------------
def digital_wave(number, levels, dt=2e-6):
    channel = digital_channel(number)
    return Waveform(channel, np.asarray(levels, dtype=np.uint8), dt, 0.0, digital=True)


def test_signal_rows_offer_digital_channels_and_hide_the_threshold(window):
    use(window, "UART")
    row = active(window).signal_rows[0]
    row.channel.setCurrentIndex(row.channel.findData(digital_channel(9)))
    assert row.channel.currentText() == "D9"
    assert row.auto.isHidden()
    row.channel.setCurrentIndex(row.channel.findData(1))
    assert not row.auto.isHidden()


def test_wide_bus_defaults_run_on_to_digital_channels(window):
    use(window, SIGROK_ROOTS["lpc"].name)
    required = [row.channel.currentText() for row in active(window).signal_rows if row.spec.required]
    assert required[:6] == ["CH1", "CH2", "CH3", "CH4", "D0", "D1"]


def test_digital_channel_decodes_and_is_drawn(window):
    signal = siggen.uart(b"Hi", 115200, 16)
    wave = digital_wave(3, signal.lines["tx"], 1 / signal.sample_rate)
    window.waves = {wave.channel: wave}
    use(window, "UART")
    row = active(window).signal_rows[0]
    row.channel.setCurrentIndex(row.channel.findData(wave.channel))
    window.display_mode.setCurrentIndex(window.display_mode.findData("analog"))
    run_decode(window)
    assert values_of(window, active(window)) == [0x48, 0x69]
    assert window.plots


def test_mixing_analog_and_digital_warns_and_matches_lengths(window):
    analog = Waveform(1, np.zeros(8, dtype=np.float32), 1e-6, 0.0)
    wave = digital_wave(0, [0, 1, 0, 1], 2e-6)
    joined = window._same_length({"a": analog, "b": wave})
    assert len(joined["b"].volts) == 8 and joined["b"].dt == 1e-6 and list(joined["b"].volts[:4]) == [0, 0, 1, 1]
    assert window.last_message_text.startswith(("Uwaga", "Warning"))


def test_saved_waves_keep_digital_channels(window, tmp_path):
    wave = digital_wave(15, [0, 1, 1, 0])
    window.waves = {wave.channel: wave, 1: Waveform(1, np.zeros(4, dtype=np.float32), 1e-6, 0.0)}
    path = str(tmp_path / "w.npz")
    window.save_waves_to(path)
    window.waves = {}
    assert window._read_npz(path)[wave.channel].digital


# ---- compact rows ---------------------------------------------------------------------
def test_traces_are_low_and_leave_room_for_decoder_rows(window):
    window.waves = uart_wave(b"A", 9600)
    window.display_mode.setCurrentIndex(window.display_mode.findData("logic"))
    use(window, "UART")
    run_decode(window)
    line = window.fontMetrics().height()
    assert window.master.maximumHeight() == line * 5  # two lines of trace, three of time axis
    assert next(iter(window.lanes.values())).maximumHeight() == line * 3


# ---- scope tab ------------------------------------------------------------------------
class RecordingLink:
    urgent = []

    def watch(self, queries):
        self.watched = queries

    def __init__(self):
        self.sent = []

    def send(self, command, then_read=None):
        self.sent.append(command)


def test_every_choice_sends_at_once(window):
    window.panel.link = link = RecordingLink()
    try:
        window.panel.settings["depth"].set_value("10000")
        window.panel.settings["time_scale"].set_value(1e-3)
        window.panel.settings["scale2"].set_value(0.5)
        window.panel.settings["coupling1"].set_value("AC")
        window.panel.settings["threshold2"].set_value(1.65)
        assert link.sent == [":ACQuire:MDEPth 10000", ":TIMebase:MAIN:SCALe 0.001", ":CHANnel2:VERNier OFF", ":CHANnel2:SCALe 0.5",
                             ":CHANnel1:COUPling AC", ":LA:POD2:THReshold 1.65"]
    finally:
        window.panel.link = None


def test_reply_updates_the_widget_without_sending(window):
    panel = window.panel
    panel.link = link = RecordingLink()
    try:
        panel._value_received(":ACQuire:MDEPth?", "1.0000E+05")
        assert panel.depth.currentData() == "100000"
        panel._value_received(":CHANnel1:COUPling?", "AC")
        assert panel.channel_coupling[1].currentData() == "AC"
        panel._value_received(":TRIGger:MODE?", "IIC")
        assert panel.trigger_mode.currentData() == "IIC" and panel.trigger_pages.currentIndex() == 2
        panel._value_received(":TRIGger:STATus?", "T'D")
        assert panel.state_label.text() == i18n.tr("Wyzwolono")
        assert link.sent == []
    finally:
        panel.link = None


def test_value_just_changed_is_not_overwritten_by_an_old_reply(window):
    panel = window.panel
    panel.link = RecordingLink()
    try:
        panel.settings["depth"].set_value("10000")
        panel._value_received(":ACQuire:MDEPth?", "1.0000E+05")
        assert panel.depth.currentData() == "10000"
    finally:
        panel.link = None


def test_time_scale_between_steps_is_added_to_the_list(window):
    window.panel._value_received(":TIMebase:MAIN:SCALe?", "3.000000E-3")
    assert window.panel.time_scale.currentData() == pytest.approx(3e-3)


def test_trigger_form_shows_only_what_the_condition_uses(window):
    panel = window.panel
    panel.trigger_mode.setCurrentIndex(panel.trigger_mode.findData("IIC"))
    panel.trigger_settings["IIC"]["WHEN"].widget.setCurrentIndex(0)
    panel._refresh_trigger_form()
    rows = {field.key: widget for field, _label, widget in panel.trigger_rows["IIC"]}
    assert rows["ADDRess"].isHidden() and rows["DATA"].isHidden()
    panel.link = RecordingLink()
    try:
        panel.trigger_settings["IIC"]["WHEN"].set_value("ADATa")
        assert not rows["ADDRess"].isHidden() and not rows["DATA"].isHidden()
        assert panel.link.sent == [":TRIGger:IIC:WHEN ADATa"]
    finally:
        panel.link = None


def test_hex_field_rejects_bad_values_without_sending(window):
    panel = window.panel
    panel.link = link = RecordingLink()
    try:
        setting = panel.trigger_settings["RS232"]["DATA"]
        setting.widget.setText("xyz")
        setting.changed()
        setting.widget.setText("100")  # above 0xFF
        setting.changed()
        assert link.sent == []
        setting.widget.setText("A5")
        setting.changed()
        assert link.sent == [":TRIGger:RS232:DATA 165"]
    finally:
        panel.link = None


def test_lin_data_reply_in_bits(window):
    setting = window.panel.trigger_settings["LIN"]["DATA"]
    setting.show("00101010")
    assert setting.widget.text() == "2A"
    setting.show("XXXXXXXX")
    assert setting.widget.text() == ""


def test_live_link_sends_before_reading_and_reports_refusals():
    class FakeScope:
        def __init__(self):
            self.log = []

        def write(self, text):
            self.log.append(text)

        def errors(self):
            self.log.append("errors")
            return ['-222,"Data out of range"']

        def query(self, text):
            self.log.append(text)
            return "1"

    fake = FakeScope()
    link = LiveLink(fake)
    refused = []
    link.refused.connect(lambda command, error: refused.append((command, error)), QtCore.Qt.DirectConnection)
    link.watch([":A?", ":B?"])
    link.active = True
    link.send(":X 1", ":X?")
    link._pass()
    assert fake.log[:3] == [":X 1", "errors", ":X?"]
    assert fake.log[3:5] == [":A?", ":B?"]
    assert refused == [(":X 1", '-222,"Data out of range"')]


def test_live_link_stays_quiet_during_a_job():
    class FakeScope:
        def query(self, text):
            raise AssertionError(text)

    link = LiveLink(FakeScope())
    link.watch([":A?"])
    link.active = True
    link.busy = True
    link._pass()


def test_hidden_tab_reads_only_one_round():
    link = LiveLink(None)
    link.watch([":A?", ":B?"])
    assert link._next_queries() == []
    link.read_all_once()
    assert link._next_queries() == [":A?", ":B?"]
    assert link._next_queries() == []


# ---- trigger definitions -----------------------------------------------------------------
def test_replies_match_their_choices():
    assert trigger.same_choice("POS", "POSitive") and trigger.same_choice("CHAN1", "CHANnel1")
    assert trigger.same_choice("IDFR", "IDFRame") and trigger.same_choice("8.000000E0", "8") and trigger.same_choice("1.5", "1.5")
    assert not trigger.same_choice("NEG", "POSitive")
    assert trigger.find_choice("D15", trigger.SOURCES) == 19


def test_commands_are_formed_per_kind():
    mode = trigger.RS232
    fields = {field.key: field for field in mode.fields}
    assert trigger.command(mode, fields["DATA"], 0xA5) == ":TRIGger:RS232:DATA 165"
    assert trigger.command(mode, fields["BAUD"], 115200.0) == ":TRIGger:RS232:BAUD 115200"
    assert trigger.command(mode, fields["WHEN"], "STARt") == ":TRIGger:RS232:WHEN STARt"
    extended = next(field for field in trigger.CAN.fields if field.key == "EXTended")
    assert trigger.command(trigger.CAN, extended, True) == ":TRIGger:CAN:EXTended ON"
    assert trigger.query(mode, fields["LEVel"]) == ":TRIGger:RS232:LEVel?"


# ---- memory files ------------------------------------------------------------------------
def bin_record(kind, label, points, payload, step=2e-8, origin=-1e-3, declared=None, width=4):
    header = struct.pack("<IIIIIfdddii", 140, kind, 1, points, 0, 0.002, origin, step, origin, 2, 1)
    header += b"\0" * (112 - len(header)) + label.ljust(16, b"\0") + b"\0" * 12
    data_header = struct.pack("<IHHQ", 16, 5 if kind == 6 else 1, width, declared if declared is not None else len(payload))
    return header + data_header + payload


def bin_file(*records):
    body = b"".join(records)
    return b"RG03" + struct.pack("<QI", len(body) + 16 + 12345, len(records)) + body


def test_bin_analog_and_digital_with_the_overstated_size():
    analog = np.linspace(0, 3.3, 8, dtype="<f4")
    words = np.array([0x8201, 0x0001, 0x8201, 0x0001], dtype="<u4")
    data = bin_file(bin_record(1, b"CH1", 8, analog.tobytes()), bin_record(1, b"CH2", 8, analog.tobytes()),
                    bin_record(6, b"LA", 4, words.tobytes(), declared=8 * 4))
    parsed = rigol_files.parse_bin(data, [digital_channel(5)])
    assert parsed.digital_factor == 2
    assert np.array_equal(parsed.waves[2].volts, analog) and parsed.waves[1].dt == 2e-8 and parsed.waves[1].t0 == -1e-3
    assert set(parsed.waves) == {1, 2, digital_channel(9), digital_channel(15), digital_channel(5)}
    wave = parsed.waves[digital_channel(9)]
    assert wave.digital and list(wave.volts) == [1, 0, 1, 0] and wave.dt == 4e-8
    assert not parsed.waves[digital_channel(5)].volts.any()
    assert digital_channel(0) not in parsed.waves  # constant at 1, not asked for


def test_bin_digital_only_uses_the_declared_size_for_the_ratio():
    words = np.array([0, 1, 0, 1], dtype="<u4")
    parsed = rigol_files.parse_bin(bin_file(bin_record(6, b"LA", 4, words.tobytes(), declared=16 * 4)))
    assert parsed.digital_factor == 4 and parsed.waves[digital_channel(0)].dt == pytest.approx(8e-8)


def test_bin_refuses_foreign_and_cut_files():
    with pytest.raises(ValueError):
        rigol_files.parse_bin(b"NOPE" + b"0" * 100)
    good = bin_file(bin_record(1, b"CH1", 8, np.zeros(8, "<f4").tobytes()))
    with pytest.raises(ValueError):
        rigol_files.parse_bin(good[:-4])


def wfm_file(channels, samples, slots=None, probes=None):
    """channels: channel -> (scale in nV, offset in nV); samples: interleaved codes."""
    probes = probes or {}
    parts = [b"\x02\0\0\0" + b"\0" * 28]
    for channel, (scale, offset) in channels.items():
        block = (b"\x01" + struct.pack("<q", scale) + b"\0" * 29 + struct.pack("<q", offset) + b"$\x03\0\0\0CH%d" % channel
                 + struct.pack("<q", probes.get(channel, 1_000_000_000)) + b"\0" * 20)
        parts.append(zlib.compress(block))
    points = len(samples) // (slots or len(channels))
    parts.append(struct.pack("<IIIII", 77, points, points, 0, 0))
    parts.append(np.asarray(samples, dtype="<u2").tobytes())
    return b"".join(parts)


def test_wfm_scales_codes_and_splits_channels():
    data = wfm_file({1: (1_000_000_000, -1_650_000_000), 2: (500_000_000, 0)}, [32768, 32768, 32768 + 7500, 32768 + 7500])
    waves = rigol_files.parse_wfm(data, 1e-6).waves
    assert list(waves[1].volts) == pytest.approx([1.65, 2.65], abs=1e-5)
    assert list(waves[2].volts) == pytest.approx([0.0, 0.5], abs=1e-5)
    assert waves[1].dt == 1e-6 and waves[1].t0 == pytest.approx(-1e-6)


def test_wfm_second_channel_alone_sits_in_the_second_slot():
    data = wfm_file({2: (1_000_000_000, 0)}, [9, 32768, 9, 32768 + 7500], slots=2)
    waves = rigol_files.parse_wfm(data, 1e-6).waves
    assert list(waves) == [2] and list(waves[2].volts) == pytest.approx([0.0, 1.0], abs=1e-5)


def test_wfm_probe_ratio_scales_volts_and_offset():
    data = wfm_file({1: (500_000_000, -1_000_000_000)}, [32768, 32768 + 7500], probes={1: 10_000_000_000})
    waves = rigol_files.parse_wfm(data, 1e-6).waves
    assert list(waves[1].volts) == pytest.approx([10.0, 15.0], abs=1e-4)


def test_wfm_refuses_foreign_files():
    with pytest.raises(ValueError):
        rigol_files.parse_wfm(b"XXXX" + b"0" * 64, 1e-6)
    with pytest.raises(ValueError):
        rigol_files.parse_wfm(b"\x02\0\0\0" + b"0" * 64, 1e-6)


def test_opening_a_bin_file_shows_its_channels(window, tmp_path):
    signal = siggen.uart(b"A", 115200, 16)
    levels = np.asarray(signal.lines["tx"], dtype="<u4")
    path = tmp_path / "x.bin"
    path.write_bytes(bin_file(bin_record(6, b"LA", len(levels), levels.tobytes(), step=1 / signal.sample_rate, declared=len(levels) * 4)))
    use(window, "UART")
    row = active(window).signal_rows[0]
    row.channel.setCurrentIndex(row.channel.findData(digital_channel(0)))
    window.open_waves_from(str(path))
    assert digital_channel(0) in window.waves
    while window.worker is not None:
        window.worker.wait(10)
        QtWidgets.QApplication.processEvents()
    QtWidgets.QApplication.processEvents()
    assert values_of(window, active(window)) == [0x41]


def test_opening_a_bad_file_says_so(window, tmp_path):
    path = tmp_path / "x.bin"
    path.write_bytes(b"junk")
    window.open_waves_from(str(path))
    assert not window.waves and window.last_message_text.startswith(("BŁĄD", "ERROR"))


def test_repeated_readings_do_not_restart_the_round(window):
    panel = window.panel
    link = LiveLink(None)
    panel.link = link
    try:
        panel._value_received(":TRIGger:MODE?", "RS232")
        reads = [setting.read for setting in panel._visible_trigger_settings()]
        assert ":TRIGger:RS232:WIDTh?" in link.urgent and link.watched[-len(reads):] == reads
        link.urgent.clear()
        link.position = 7
        panel._value_received(":TRIGger:MODE?", "RS232")
        panel._value_received(":TRIGger:RS232:WHEN?", panel.trigger_settings["RS232"]["WHEN"].widget.currentData())
        assert link.position == 7 and not link.urgent
    finally:
        panel.link = None


def test_bit_field_sends_one_pair_per_bit():
    field = next(field for field in trigger.M1553.fields if field.key == "DRTA")
    sent = trigger.command(trigger.M1553, field, "10x").split("\n")
    assert sent[:6] == [":TRIGger:M1553:DRTA 0", ":TRIGger:M1553:CODE 1", ":TRIGger:M1553:DRTA 1", ":TRIGger:M1553:CODE 0",
                        ":TRIGger:M1553:DRTA 2", ":TRIGger:M1553:CODE 255"]
    assert len(sent) == 2 * field.width and sent[-1] == ":TRIGger:M1553:CODE 255"
    # Measured on the scope: the indices above these stand for hex digits and overwrite groups of bits.
    assert {field.key: field.width for field in trigger.M1553.fields if field.kind == trigger.BITS} == {
        "DMIN": 16, "DMAX": 16, "DRTA": 5, "DBIT": 11}
    with pytest.raises(ValueError):
        trigger.bit_codes("102", 7)
    with pytest.raises(ValueError):
        trigger.bit_codes("1" * 8, 7)
    assert trigger.query(trigger.M1553, field) is None
    data = next(field for field in trigger.I2S.fields if field.key == "DMIN")
    assert trigger.command(trigger.I2S, data, "10", 8).count("CODE") == 8


def test_optional_triggers_are_offered(window):
    panel = window.panel
    names = [panel.trigger_mode.itemData(index) for index in range(panel.trigger_mode.count())]
    assert {"IIS", "FLEXray", "M1553"} <= set(names)
    panel.link = RecordingLink()
    try:
        panel.settings["trigger_mode"].set_value("M1553")
        panel.trigger_settings["M1553"]["WHEN"].set_value("CMD")
        assert ":TRIGger:M1553:DRTA?" not in panel.link.urgent
        panel.trigger_settings["M1553"]["DRTA"].widget.setText("9")
        panel.trigger_settings["M1553"]["DRTA"].changed()
        assert all(":DRTA" not in text for text in panel.link.sent)
        panel.trigger_settings["IIS"]["WIDTh"].set_value(16)
        panel.trigger_settings["IIS"]["UWIDth"].set_value(8)
        panel.link.sent.clear()
        panel.trigger_settings["IIS"]["DMIN"].set_value("101")
        assert panel.link.sent[-1].count(":TRIGger:IIS:CODE") == 8  # one per used bit, no more
        panel.link.sent.clear()
        panel.trigger_settings["IIS"]["DMIN"].set_value("1" * 9)
        assert not panel.link.sent
    finally:
        panel.link = None


# ---- channels to read, empty channels, vertical scale ---------------------------------------
def noise_wave(channel=3, volts_per_div=5.0, points=20000):
    """Nothing connected: in BYTE at 5 V/div the scope gave these codes (measured swing 0.34 V)."""
    generator_ = np.random.default_rng(1)
    volts = generator_.choice([-0.1707, 0.0, 0.1707], points, p=[0.15, 0.7, 0.15]).astype(np.float32)
    return Waveform(channel, volts, 1e-6, 0.0, volts_per_div=volts_per_div)


def square_wave(channel=1, volts_per_div=5.0, high=3.3, points=20000):
    volts = np.where(np.arange(points) // 50 % 2, high, 0.0).astype(np.float32)
    return Waveform(channel, volts, 1e-6, 0.0, volts_per_div=volts_per_div)


def test_noise_of_an_empty_channel_is_no_signal():
    noise = noise_wave()
    assert not noise.has_signal()
    assert digitize(noise.volts, noise.dt, noise.t0, None, noise.min_swing).edges == []
    assert Waveform(3, noise.volts, 1e-6, 0.0).has_signal()  # without a known scale only the fixed limit is left


def test_a_real_signal_on_a_coarse_scale_stays_a_signal():
    wave = square_wave()
    assert wave.has_signal()
    assert len(digitize(wave.volts, wave.dt, wave.t0, None, wave.min_swing).edges) == 399


def test_a_decoder_on_an_empty_channel_gets_no_edges_and_says_so(window):
    window.waves = {1: noise_wave(1)}
    card = use(window, "UART")
    run_decode(window)
    assert window.result.annotations == []
    assert "nie zmienia poziomu" in card.problem


class ChannelScope:
    """Answers as the instrument does: a channel that is off keeps the waveform source where it was."""

    def __init__(self, on):
        self.on, self.source, self.commands = set(on), "CHAN1", []

    def channel_enabled(self, channel):
        return channel in self.on

    def fetch(self, channel, progress=None, sample_format="BYTE"):
        assert channel in self.on, "the app must not read a channel that is off"
        return square_wave(channel, 1.0)

    def channel_state(self, channel):
        return {"scale": 0.5, "offset": 0.0, "probe": 1.0, "coupling": "DC"}


def test_fetch_refuses_a_channel_the_scope_will_not_switch_to():
    scope = Scope("nowhere")
    sent = []
    scope.write = sent.append
    scope.query = lambda _command: "CHAN3"
    with pytest.raises(ScopeError, match="CH4"):
        scope.fetch(4)
    assert sent == [":WAVeform:SOURce CHANnel4"]


def test_reading_memory_leaves_out_channels_that_are_off_and_tells(window):
    window.scope = ChannelScope({1})
    waves, states = window._read_job([1, 4], lambda _text: None, "BYTE")
    assert list(waves) == [1] and states["absent"] == ["CH4"]
    assert waves[1].volts_per_div == 0.5
    window._captured((True, waves, states))
    assert "CH4" in window.last_message_text or "CH4" in window.log.toPlainText()
    waves, states = window._read_job([2], lambda _text: None, "BYTE")  # nothing at all is a finding as well
    assert waves == {} and states["absent"] == ["CH2"]
    window.scope = None


def test_the_saved_scale_comes_back_from_a_file(window, tmp_path):
    window.waves = {1: square_wave()}
    path = str(tmp_path / "waves.npz")
    window.save_waves_to(path)
    window.waves = {}
    window.open_waves_from(path)
    assert window.waves[1].volts_per_div == 5.0


def test_the_toolbar_picks_the_channels_to_read(window):
    picker = window.scope_settings
    assert picker.selected() == {1} and window._used_channels() == [1]
    picker.boxes[3].setChecked(True)
    assert window._used_channels() == [1, 3] and "CH3" in picker.toolTip()
    picker.boxes[digital_channel(5)].setChecked(True)
    assert window._used_channels() == [1, 3, digital_channel(5)]
    assert window.settings.value("read_channels") == "1 3 105"
    picker.choose(())
    with pytest.raises(ValueError):
        window._used_channels()


def test_the_scope_tab_does_not_decide_what_is_read(window):
    window.panel.channel_on[2].setChecked(True)
    assert window._used_channels() == [1]


def test_a_channel_of_a_decoder_is_read_and_cannot_be_unticked(window):
    picker = window.scope_settings
    picker.choose(())
    card = window.new_decoder("UART")
    channel = next(iter(card.mapping().values()))
    assert channel in window._used_channels() and channel in picker.selected()
    assert picker.boxes[channel].isChecked() and not picker.boxes[channel].isEnabled()
    window.remove_decoder(card)
    assert not picker.boxes[channel].isChecked() and picker.boxes[channel].isEnabled()


def test_group_buttons_tick_and_untick_at_once(window):
    picker = window.scope_settings
    changes = []
    picker.changed.connect(lambda: changes.append(1))
    picker._set_group(DIGITAL, True)
    assert all(picker.boxes[channel].isChecked() for channel in DIGITAL) and len(changes) == 1
    picker._set_group(DIGITAL, False)
    assert picker.selected() == {1}


def test_unticking_a_channel_takes_its_row_away_at_once(window):
    picker = window.scope_settings
    picker.choose((1, 3))
    window.waves = {1: square_wave(1), 3: square_wave(3)}
    window._plot()
    assert len(window.plots) == 2
    picker.boxes[3].setChecked(False)
    assert len(window.plots) == 1 and 3 in window.waves
    picker.boxes[3].setChecked(True)
    assert len(window.plots) == 2


def test_all_channels_unticked_leaves_a_word_and_no_crash(window):
    window.waves = {1: square_wave(1)}
    window._plot()
    window.scope_settings.boxes[1].setChecked(False)
    assert window.master is None and window.plots == []
    window._set_language("en")  # goes through the placeholder too
    window._set_language("pl")
    window.scope_settings.boxes[1].setChecked(True)
    assert window.master is not None


def test_a_chosen_channel_always_has_its_row_with_what_is_wrong_on_it(window):
    picker = window.scope_settings
    picker.choose((1, 3, 4))
    window._show_new_waves({1: square_wave(1), 3: noise_wave(3)})
    assert sorted(window.channel_rows) == [1, 3, 4]
    notes = [note.text() for note in window.row_notes]
    assert len(notes) == 2 and any("brak sygnału" in note for note in notes) and any("brak danych" in note for note in notes)


def test_rows_are_there_before_any_capture(window):
    window.scope_settings.choose((1, 2))
    window._plot()
    assert sorted(window.channel_rows) == [1, 2] and window.master is not None
    assert all(note.text().startswith("Brak danych") for note in window.row_notes)


def test_hide_unused_unticks_what_has_no_data_and_a_tick_brings_it_back(window):
    picker = window.scope_settings
    picker.choose((1, 3, 4))
    window._show_new_waves({1: square_wave(1), 3: noise_wave(3)})
    picker.hide_unused.click()
    assert picker.selected() == {1} and sorted(window.channel_rows) == [1]
    picker.boxes[4].setChecked(True)
    assert sorted(window.channel_rows) == [1, 4] and window.row_notes


def test_hide_unused_leaves_what_a_decoder_or_the_trigger_uses(window):
    picker = window.scope_settings
    picker.choose((3, 4))
    picker.set_condition(4, "R")
    card = window.new_decoder("UART")
    channel = next(iter(card.mapping().values()))
    window._show_new_waves({3: noise_wave(3)})
    picker.hide_unused.click()
    assert 4 in picker.selected() and channel in picker.selected() and "4" in window.log.toPlainText()
    window.remove_decoder(card)
    picker.set_condition(4, "")


def test_hide_unused_before_any_capture_only_tells(window):
    window.scope_settings.choose((1, 2))
    window.scope_settings.hide_unused.click()
    assert window.scope_settings.selected() == {1, 2} and "akwizycj" in window.last_message_text.lower()


def test_conditions_the_scope_cannot_combine_replace_each_other():
    assert trigger.conflicts({1: "R"}, 2, "F") == {1}  # one edge in a pattern
    assert trigger.conflicts({1: "R"}, 2, "H") == set()
    assert trigger.conflicts({1: "H", 2: "L"}, 3, "E") == {1, 2}  # either edge has no place in a pattern
    assert trigger.conflicts({1: "E"}, 2, "H") == {1}
    assert trigger.conflicts({1: "H"}, digital_channel(3), "R") == {1}  # a digital channel goes alone
    assert trigger.conflicts({digital_channel(3): "R"}, 1, "H") == {digital_channel(3)}
    assert trigger.conflicts({1: "R"}, 2, "") == set()
    assert "H" not in trigger.allowed_conditions(digital_channel(0)) and "H" in trigger.allowed_conditions(1)


def test_commands_of_the_conditions():
    assert trigger.condition_commands({}, {}) == []
    assert trigger.condition_commands({2: "F"}, {2: 1.5}) == [
        ":TRIGger:MODE EDGE", ":TRIGger:EDGE:SOURce CHANnel2", ":TRIGger:EDGE:SLOPe NEGative", ":TRIGger:EDGE:LEVel 1.5"]
    assert trigger.condition_commands({digital_channel(3): "E"}, {}) == [
        ":TRIGger:MODE EDGE", ":TRIGger:EDGE:SOURce D3", ":TRIGger:EDGE:SLOPe RFALl"]
    assert trigger.condition_commands({1: "H", 2: "R"}, {1: 1.0}) == [
        ":TRIGger:MODE PATTern", ":TRIGger:PATTern:PATTern H,R,X,X", ":TRIGger:PATTern:LEVel CHANnel1,1"]
    assert trigger.condition_commands({3: "L"}, {})[1] == ":TRIGger:PATTern:PATTern X,X,L,X"


def test_setting_a_condition_drops_the_other_edge_and_tells(window):
    picker = window.scope_settings
    picker.set_condition(1, "R")
    picker.set_condition(2, "F")
    assert picker.conditions == {2: "F"} and "CH1" in window.last_message_text
    assert window.settings.value("channel_triggers") == "2:F"
    picker.set_condition(2, "")
    assert picker.conditions == {} and 2 in picker.selected()  # the row does not go with the condition


def test_a_channel_with_a_condition_cannot_be_unticked(window):
    picker = window.scope_settings
    picker.set_condition(2, "H")
    assert not picker.boxes[2].isEnabled() and picker.boxes[2].isChecked()
    assert "↑" not in window.channel_rows[2].getAxis("left")._tickLevels[0][0][1] and "H" in window.channel_rows[2].getAxis("left")._tickLevels[0][0][1]
    picker.set_condition(2, "")
    assert picker.boxes[2].isEnabled()


def test_the_menu_form_edits_the_same_condition(window):
    picker = window.scope_settings
    picker.trigger_channel.setCurrentIndex(picker.trigger_channel.findData(digital_channel(1)))
    model = picker.trigger_condition.model()
    assert not model.item(picker.trigger_condition.findData("H")).isEnabled()
    picker.trigger_condition.setCurrentIndex(picker.trigger_condition.findData("F"))
    picker.trigger_condition.activated.emit(picker.trigger_condition.currentIndex())
    assert picker.conditions == {digital_channel(1): "F"} and "D1" in picker.toolTip()
    picker.set_condition(digital_channel(1), "")


class PlanScope:
    """Takes down what a job sends. Channel 1 and D0 hold data."""

    def __init__(self, depth="1.0E+6", la="0"):
        self.sent = []
        self.depth = depth
        self.la = la
        self.timeout = 1

    def write(self, command):
        self.sent.append(command)

    def query(self, command):
        self.sent.append(command)
        return {":ACQuire:MDEPth?": self.depth, ":LA:ENABle?": self.la, "*OPC?": "1", ":SYSTem:ERRor?": '0,"No error"'}.get(command, "0")

    query_float = Scope.query_float
    errors = Scope.errors
    set_channel = Scope.set_channel
    set_dc_coupling = Scope.set_dc_coupling
    set_memory_depth = Scope.set_memory_depth
    set_time_scale = Scope.set_time_scale
    enable_digital = Scope.enable_digital
    _apply_digital = Scope._apply_digital
    _apply_trigger = Scope._apply_trigger
    _apply_pretrigger = Scope._apply_pretrigger
    apply_plan = Scope.apply_plan


def test_a_plan_turns_chosen_channels_on_and_the_others_off():
    scope = PlanScope()
    assert scope.apply_plan(CapturePlan({2}, force_dc=False)) == []
    assert ":CHANnel2:DISPlay ON" in scope.sent and ":CHANnel1:DISPlay OFF" in scope.sent and ":CHANnel4:DISPlay OFF" in scope.sent
    assert scope.sent.index(":CHANnel2:DISPlay ON") < scope.sent.index(":CHANnel1:DISPlay OFF")
    assert not any(command.startswith((":ACQuire:MDEPth ", ":TIMebase", ":TRIGger", ":CHANnel2:COUPling", ":LA:ENABle ")) for command in scope.sent)


def test_a_plan_sets_what_was_chosen_in_a_working_order():
    scope = PlanScope()
    scope.apply_plan(CapturePlan({1}, True, "1000000", 1e-3, (":TRIGger:MODE EDGE",)))
    for command in (":CHANnel1:COUPling DC", ":ACQuire:MDEPth 1000000", ":TIMebase:MAIN:SCALe 0.001", ":TRIGger:MODE EDGE"):
        assert command in scope.sent
    assert scope.sent.index(":CHANnel4:DISPlay OFF") < scope.sent.index(":ACQuire:MDEPth 1000000")


def test_a_plan_reports_a_depth_that_did_not_come_out():
    scope = PlanScope(depth="2.5E+7")
    notes = scope.apply_plan(CapturePlan({1}, depth="100000000"))
    assert len(notes) == 1 and "100,000,000" in notes[0]
    assert scope.apply_plan(CapturePlan({1}, depth="AUTO")) == []


def test_a_plan_with_digital_channels_sets_their_groups(monkeypatch):
    monkeypatch.setattr("mhodecode.scope.abort.sleep", lambda _seconds: None)
    scope = PlanScope()
    scope.apply_plan(CapturePlan({digital_channel(9)}, force_dc=False))
    assert ":LA:ENABle ON" in scope.sent and ":LA:POD1:DISPlay OFF" in scope.sent and ":LA:POD2:DISPlay ON" in scope.sent
    assert ":LA:DIGital:ENABle D8,OFF" in scope.sent and ":LA:DIGital:ENABle D9,OFF" not in scope.sent
    # the last digital channel going off would turn the digital channels off as a whole
    assert scope.sent.index(":LA:POD2:DISPlay ON") < scope.sent.index(":LA:POD1:DISPlay OFF")
    assert ":LA:POD2:THReshold 0" in scope.sent and not any(command.startswith(":LA:POD1:THReshold ") for command in scope.sent)
    assert not any(command.startswith(":LA:DIGital:ENABle D0") for command in scope.sent)
    assert ":CHANnel1:DISPlay OFF" in scope.sent
    scope = PlanScope(la="1")
    scope.apply_plan(CapturePlan({1}, force_dc=False))
    assert ":LA:ENABle OFF" in scope.sent


class CaptureScope(PlanScope):
    """Enough of an instrument for the whole of an acquisition or a memory read."""

    def stop(self):
        self.sent.append(":STOP")

    def recover(self):
        self.sent.append("recover")

    def close(self):
        self.sent.append("close")

    def channel_enabled(self, channel):
        return True

    def channel_state(self, channel):
        return {"scale": 1.0, "offset": 0.0, "probe": 1.0, "coupling": "DC"}

    def single(self, timeout=None, force_after=None):
        self.sent.append(f"single {timeout} {force_after}")
        return True

    def fetch(self, channel, progress=None, sample_format="BYTE"):
        return square_wave(channel)


def run_job(window, app):
    while window.worker is not None:
        app.processEvents()
    app.processEvents()


def test_acquisition_applies_the_settings_and_reading_the_memory_does_not(window, app):
    picker = window.scope_settings
    picker.choose((1,))
    picker.depth.setCurrentIndex(picker.depth.findData("1000000"))
    picker.time_scale.setCurrentIndex(1)
    picker.set_condition(1, "R")
    window.scope = CaptureScope()
    window._read_memory()
    run_job(window, app)
    changing = [command for command in window.scope.sent
                if command.startswith((":CHANnel", ":ACQuire:MDEPth ", ":TIMebase", ":TRIGger", ":LA"))]
    assert changing == [] and ":STOP" in window.scope.sent and 1 in window.waves
    window._acquire()
    run_job(window, app)
    sent = window.scope.sent
    assert ":ACQuire:MDEPth 1000000" in sent and ":TRIGger:MODE EDGE" in sent and ":CHANnel2:DISPlay OFF" in sent
    assert any(command.startswith(":TRIGger:EDGE:LEVel") for command in sent)
    assert "single None None" in sent  # a condition is waited for, not forced
    window.scope = None
    picker.set_condition(1, "")


def test_aborting_an_acquisition_is_logged_even_with_a_decoding_waiting(window, app):
    class Waiting(CaptureScope):
        def single(self, timeout=None, force_after=None):
            while True:
                abort.sleep(0.01)

    window.scope_settings.choose((1,))
    window.waves = {1: square_wave(1)}
    window.scope = Waiting()
    window._acquire()
    window._decode()  # a threshold changed while waiting asks for this, and it has to wait as well
    assert window.decode_pending and not abort.requested()
    window._abort()
    run_job(window, app)
    assert "Przerwano" in window.log.toPlainText()
    window.scope = None


def test_acquisition_without_a_condition_leaves_the_trigger_alone(window, app):
    window.scope_settings.choose((1,))
    window.scope = CaptureScope()
    window._acquire()
    run_job(window, app)
    assert not any(command.startswith(":TRIGger") for command in window.scope.sent)
    assert not any(command.startswith(":ACQuire:MDEPth ") for command in window.scope.sent)
    window.scope = None


def test_matching_the_generator_gives_the_timing_back_to_the_scope(window):
    picker = window.scope_settings
    picker.time_scale.setCurrentIndex(1)
    picker.set_condition(1, "R")
    window._match_decoder()
    assert picker.time_scale.currentData() is None and picker.conditions == {}


def test_the_run_button_is_gone_from_the_toolbar(window):
    assert not hasattr(window, "run_button") and not hasattr(window, "_run_scope")
    assert [button.text() for button in window.panel.action_buttons][0] == "RUN"  # the scope tab's own control stays


def test_a_threshold_on_a_channel_decides_the_logic_view_and_auto_decoders(window, app):
    picker = window.scope_settings
    picker.choose((1,))
    volts = np.where(np.arange(20000) % 2000 < 1000, 0.0, 3.3).astype(np.float32)
    volts[8000:9000] = 1.0  # a step in the middle of the swing: which side it falls on depends on the threshold
    wave = Waveform(1, volts, 1e-6, 0.0, volts_per_div=1.0)
    window._show_new_waves({1: wave})
    assert window._display_threshold(1, wave) is None
    picker.set_threshold(1, 0.5)
    low = window._display_threshold(1, wave)
    assert low is not None and low.low < 0.5 < low.high
    card = window.new_decoder("UART")
    plan = window._decode_plan(card)
    assert plan is not None
    key = next(key for key, channel in card.mapping().items() if channel == 1)
    assert plan[3][key].low < 0.5 < plan[3][key].high
    row = next(row for row in card.signal_rows if row.spec.key == key)
    row.auto.setChecked(False)
    row.level.setValue(2.0)
    plan = window._decode_plan(card)  # the decoder's own level goes before the channel's
    assert plan[3][key].low < 2.0 < plan[3][key].high
    window.remove_decoder(card)
    picker.set_threshold(1, None)
    assert window._display_threshold(1, wave) is None


def test_a_threshold_sets_the_trigger_level_else_the_middle_of_the_signal(window):
    picker = window.scope_settings
    picker.choose((1, 2))
    window._show_new_waves({1: square_wave(1, 0.0, 3.0), 2: noise_wave(2)})
    picker.set_condition(1, "R")
    assert window._trigger_levels()[1] == pytest.approx(1.5, abs=0.1)
    picker.set_threshold(1, 2.5)
    assert window._trigger_levels() == {1: 2.5}
    picker.set_threshold(1, None)
    picker.set_condition(1, "")
    picker.set_condition(2, "H")  # no signal to take a level from: the scope keeps its own
    assert window._trigger_levels() == {}
    picker.set_condition(2, "")
    picker.set_condition(digital_channel(1), "R")
    assert window._trigger_levels() == {}
    picker.set_condition(digital_channel(1), "")


def test_settings_survive_a_restart(window):
    picker = window.scope_settings
    picker.set_condition(1, "R")
    picker.set_threshold(1, 1.25)
    picker.depth.setCurrentIndex(picker.depth.findData("10000"))
    picker.time_scale.setCurrentIndex(2)
    picker.force_dc.setChecked(False)
    again = ScopeSettings(window)
    assert again.conditions == {1: "R"} and again.thresholds == {1: 1.25} and again.depth.currentData() == "10000"
    assert again.time_scale.currentIndex() == 2 and not again.force_dc.isChecked()
    picker.set_condition(1, "")
    picker.set_threshold(1, None)
    picker.force_dc.setChecked(True)


def test_opening_a_file_shows_its_channels(window, tmp_path):
    window.waves = {2: square_wave(2)}
    path = str(tmp_path / "waves.npz")
    window.save_waves_to(path)
    window.waves = {}
    window.open_waves_from(path)
    assert window.scope_settings.selected() == {2} and len(window.plots) == 1


def analog_view(window, wave=None):
    window.display_mode.setCurrentIndex(window.display_mode.findData("analog"))
    wave = wave if wave is not None else burst_wave()
    window.scope_settings.choose((1,))
    window._show_new_waves({1: wave})
    return window.analog_rows[1]


def burst_wave():
    """Most of it small, with one tall part: the range must stay the whole capture's."""
    volts = np.full(20000, 0.1, dtype=np.float32)
    volts[::2] = -0.1
    volts[15000:15100] = 5.0
    return Waveform(1, volts, 1e-6, 0.0, volts_per_div=1.0)


def test_y_scale_does_not_follow_the_time_scale(window, app):
    plot = analog_view(window)
    fitted = plot.viewRange()[1]
    assert fitted[0] < -0.1 and fitted[1] > 5.0
    window.master.setXRange(1e-3, 2e-3, padding=0)  # only the small part in view
    app.processEvents()
    assert plot.viewRange()[1] == pytest.approx(fitted)
    window.zoom_fit()
    app.processEvents()
    assert plot.viewRange()[1] == pytest.approx(fitted)
    window.display_mode.setCurrentIndex(window.display_mode.findData("logic"))


def test_y_scale_stays_through_a_redraw_and_is_fitted_again_on_new_data(window):
    plot = analog_view(window)
    fitted = plot.viewRange()[1]
    window._plot()
    assert window.analog_rows[1].viewRange()[1] == pytest.approx(fitted)
    window._show_new_waves({1: square_wave(1, 1.0, 1.0)})
    assert window.analog_rows[1].viewRange()[1][1] < 2.0
    window.display_mode.setCurrentIndex(window.display_mode.findData("logic"))


def click_on(view, scene_point):
    place = view.mapFromScene(scene_point)
    for kind, button, buttons in ((QtCore.QEvent.MouseButtonPress, QtCore.Qt.LeftButton, QtCore.Qt.LeftButton),
                                  (QtCore.QEvent.MouseButtonRelease, QtCore.Qt.LeftButton, QtCore.Qt.NoButton)):
        QtWidgets.QApplication.sendEvent(view.viewport(), QtGui.QMouseEvent(
            kind, QtCore.QPointF(place), QtCore.QPointF(view.viewport().mapToGlobal(place)), button, buttons, QtCore.Qt.NoModifier))


def channel_dialog(window):
    found = window.findChildren(ChannelDialog)
    return found[-1] if found else None


def test_noise_does_not_fill_its_row(window):
    plot = analog_view(window, noise_wave(1))
    low, high = plot.viewRange()[1]
    assert high - low >= 2 * window.waves[1].min_swing  # the noise itself spans about 0.34 V of it
    window.display_mode.setCurrentIndex(window.display_mode.findData("logic"))


def test_click_on_the_voltage_axis_sets_the_scale_by_hand(window, app):
    window.resize(1200, 800)
    window.show()
    plot = analog_view(window)
    app.processEvents()
    click_on(window.graphics, plot.getAxis("left").sceneBoundingRect().center())
    app.processEvents()
    dialog = channel_dialog(window)
    assert dialog is not None and dialog.isVisible() and dialog.scale_auto.isChecked()
    dialog.scale_auto.setChecked(False)
    dialog.high.setValue(1.0)
    dialog.low.setValue(-1.0)
    dialog.accept()
    assert window.analog_rows[1].viewRange()[1] == pytest.approx([-1.0, 1.0])
    window._plot()
    assert window.analog_rows[1].viewRange()[1] == pytest.approx([-1.0, 1.0])
    window._show_new_waves({1: square_wave(1)})  # a scale set by hand outlives the capture
    assert window.analog_rows[1].viewRange()[1] == pytest.approx([-1.0, 1.0])
    window._open_channel(1)
    dialog = channel_dialog(window)
    assert not dialog.scale_auto.isChecked()
    dialog.scale_auto.setChecked(True)
    dialog.accept()
    assert window.analog_rows[1].viewRange()[1][1] > 3.3
    window.display_mode.setCurrentIndex(window.display_mode.findData("logic"))


def test_applying_the_window_without_touching_the_scale_leaves_it_automatic(window):
    analog_view(window)
    window._open_channel(1)
    channel_dialog(window).accept()
    assert window.manual_y_ranges == {}
    window.display_mode.setCurrentIndex(window.display_mode.findData("logic"))


def test_a_scale_needs_its_top_above_its_bottom(window):
    analog_view(window)
    window._open_channel(1)
    dialog = channel_dialog(window)
    dialog.scale_auto.setChecked(False)
    dialog.high.setValue(-5.0)
    dialog.low.setValue(-4.0)
    assert not dialog.apply_button.isEnabled()
    dialog.low.setValue(-6.0)
    assert dialog.apply_button.isEnabled()
    dialog.reject()
    window.display_mode.setCurrentIndex(window.display_mode.findData("logic"))


def test_click_on_a_logic_row_gives_trigger_and_threshold_and_no_scale(window, app):
    window.resize(1200, 800)
    window.show()
    window.waves = {1: square_wave(1)}
    window._plot()
    app.processEvents()
    assert window.analog_rows == {}
    click_on(window.graphics, window.plots[0].getAxis("left").sceneBoundingRect().center())
    app.processEvents()
    dialog = channel_dialog(window)
    assert dialog is not None and dialog.high is None and dialog.threshold is not None
    assert dialog.threshold.value() == pytest.approx(window._signal_middle(window.waves[1]))
    dialog.trigger_codes["F"].setChecked(True)
    dialog.threshold_auto.setChecked(False)
    dialog.threshold.setValue(1.75)
    dialog.accept()
    assert window.scope_settings.conditions == {1: "F"} and window.scope_settings.thresholds == {1: 1.75}
    assert "↓" in window.channel_rows[1].getAxis("left")._tickLevels[0][0][1]
    window.scope_settings.set_condition(1, "")
    window.scope_settings.set_threshold(1, None)


def test_the_window_of_a_digital_channel_has_only_its_edges(window):
    channel = digital_channel(2)
    window.scope_settings.choose((channel,))
    window.waves = {channel: Waveform(channel, np.array([0, 1] * 100, dtype=np.uint8), 1e-6, 0.0, digital=True)}
    window._plot()
    window._open_channel(channel)
    dialog = channel_dialog(window)
    assert dialog.threshold is None and dialog.high is None
    assert not dialog.trigger_codes["H"].isEnabled() and dialog.trigger_codes["R"].isEnabled()
    dialog.reject()


def test_a_row_without_data_has_the_window_too(window):
    window.scope_settings.choose((2,))
    window._plot()
    window._open_channel(2)
    dialog = channel_dialog(window)
    assert dialog.threshold.value() == 0.0 and dialog.high is None
    dialog.reject()


def test_channel_tips_are_translated(window):
    window._set_language("en")
    picker = window.scope_settings
    assert picker.text() == "Scope settings" and "Applied by Acquisition" in picker.toolTip()
    assert picker.hide_unused.text() == "Hide unused"
    window._set_language("pl")
    assert picker.text() == "Ustawienia oscyloskopu"


def test_a_decoder_with_no_edge_on_any_line_is_not_run(window):
    """CAN reads a constant low as endless frames: 265443 annotations on 0.2 s of nothing."""
    window.waves = {3: Waveform(3, np.zeros(200000, dtype=np.float32), 1e-6, 0.0, volts_per_div=5.0)}
    card = use(window, "CAN")
    next(row for row in card.signal_rows if row.spec.required).channel.setCurrentIndex(
        next(row for row in card.signal_rows if row.spec.required).channel.findData(3))
    run_decode(window)
    assert window.result.annotations == []
    assert "nie zmienia poziomu" in card.problem
    assert any(note.text() == card.problem for note in window.row_notes)


def test_a_flat_line_beside_a_working_one_does_not_stop_the_decoder(window):
    decoded_uart(window)
    card = window.cards[0]
    window.waves[3] = Waveform(3, np.zeros_like(window.waves[1].volts), window.waves[1].dt, 0.0, volts_per_div=5.0)
    rows = {row.spec.key: row for row in card.signal_rows}
    rows["tx"].channel.setCurrentIndex(rows["tx"].channel.findData(1))
    rows["rx"].channel.setCurrentIndex(rows["rx"].channel.findData(3))
    run_decode(window)
    assert "RX nie zmienia poziomu" in card.status.text() and not card.problem
    assert [item for item in window.result.annotations if ":tx-" in item.row]
    assert not [item for item in window.result.annotations if ":rx-" in item.row]

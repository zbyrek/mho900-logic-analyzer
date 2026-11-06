"""Entry point. With --selftest HOST the window itself goes through every user-visible path
with the AFG outputs cabled to the inputs (AFG1 on CH1, AFG2 on CH2), saves a log and
screenshots, and exits 0 only if all of them worked."""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

ABORT_DEPTH = "10000000"  # long enough to abort in the middle of a read
NORMAL_DEPTH = "1000000"
ABORT_DELAY_MS = 1500
LIVE_WAIT_S = 4.0  # long enough for a full round of the scope tab's readings
LOGIC_THRESHOLD = 1.65


def run_selftest(window, app, host: str, output: Path) -> None:
    from PyQt5 import QtCore

    from mhodecode.scope import digital_channel

    outcomes: list[tuple[str, bool]] = []
    waiting = {"until": 0.0}
    checks = {}

    def set_generator(protocol, data_format, data, **params):
        window.remove_all_decoders()
        window.gen_protocol.setCurrentText(protocol)
        window.gen_data_format.setCurrentIndex(window.gen_data_format.findData(data_format))
        window.gen_data.setText(data)
        for key, value in params.items():
            widget = window.gen_param_widgets[key]
            if hasattr(widget, "findData"):
                widget.setCurrentIndex(widget.findData(value))
            else:
                widget.setValue(value)

    def compare_step(name):
        def step():
            window._compare()
            outcomes.append((name, bool(window.last_compare_ok)))
        return step

    def signal_steps(name, protocol, data_format, data, **params):
        return [
            lambda: set_generator(protocol, data_format, data, **params),
            window._generate,
            window._match_decoder,
            window._autoscale,
            window._acquire,
            compare_step(name),
        ]

    def swapped_output_steps():
        # The line goes out on AFG2 while AFG1 stays off, proving the output choice is honoured.
        def choose():
            set_generator("UART", "ascii", "swap\\n", rate=115200)
            combo = window.gen_line_combos["tx"]
            combo.setCurrentIndex(combo.findData(2))
        return [choose, window._generate, window._match_decoder, window._autoscale, window._acquire,
                compare_step("UART on AFG2")]

    def set_digital_groups(on):
        for pod in (1, 2):
            window.panel.settings[f"pod{pod}"].set_value(on)

    def sigrok_steps(name, protocol, data, pd_id, signals, options, wanted, shot, **params):
        """The generator's signal read back by a sigrok decoder with the stack that pd_id needs."""
        def choose():
            window.remove_all_decoders()
            card = window.select_sigrok(pd_id)
            for row in card.signal_rows:
                row.channel.setCurrentIndex(row.channel.findData(signals.get(row.spec.key)))
            for key, value in options.items():
                widget = card.param_widgets[key]
                widget.setValue(value)

        def check():
            texts = [item.text for item in window.result.annotations if not item.hidden]
            outcomes.append((name, any(wanted in text for text in texts)))
            window._compare()
            outcomes.append((f"{name}: comparison", bool(window.last_compare_ok)))
            window.grab().save(str(output / shot))
            window.row_filter.setCurrentIndex(window.row_filter.count() - 1)
            outcomes.append((f"{name}: row filter", all(item.row == window.row_filter.currentData()
                                                       for item in window.model.rows)))
            window.row_filter.setCurrentIndex(0)

        return [lambda: set_generator(protocol, "hex", data, **params), window._generate, window._match_decoder,
                choose, window._autoscale, window._acquire, check]

    def browser_shot():
        window.decoder_search.setText("eeprom")
        window.grab().save(str(output / "selftest_decoder_tree.png"))
        window.decoder_search.clear()
        outcomes.append(("decoder tree", window.decoder_tree.topLevelItemCount() > 3))

    def digital_steps(name, scl, sda):
        """The AFG signals read through D channels wired in parallel with CH1 and CH2."""
        def choose():
            set_generator("I2C", "hex", "48 65 6C 6C 6F")

        def assign():
            set_digital_groups(True)
            for pod in (1, 2):
                window.panel.settings[f"threshold{pod}"].set_value(LOGIC_THRESHOLD)
            rows = {row.spec.key: row for row in window.active_card.signal_rows}
            rows["scl"].channel.setCurrentIndex(rows["scl"].channel.findData(digital_channel(scl)))
            rows["sda"].channel.setCurrentIndex(rows["sda"].channel.findData(digital_channel(sda)))

        def check():
            window._compare()
            outcomes.append((name, bool(window.last_compare_ok)))
            window.grab().save(str(output / f"selftest_digital_{scl}_{sda}.png"))

        return [choose, window._generate, window._match_decoder, window._autoscale, assign, window._acquire, check]

    def uart_trigger_steps():
        """The protocol trigger set in the scope tab: fires on the byte sent, not on another."""
        def choose():
            set_generator("UART", "hex", "A5 3C", rate=115200)

        def set_trigger(data):
            def step():
                panel = window.panel
                panel.settings["trigger_mode"].set_value("RS232")
                fields = panel.trigger_settings["RS232"]
                fields["SOURce"].set_value("CHANnel1")
                fields["LEVel"].set_value(LOGIC_THRESHOLD)
                fields["BAUD"].set_value(115200)
                fields["WHEN"].set_value("DATA")
                fields["DATA"].set_value(data)
                panel.settings["sweep"].set_value("NORMal")
                window.panel.drain()
            return step

        def outcome(name, wanted):
            def step():
                outcomes.append((name, window.last_triggered is wanted))
            return step

        def restore():
            window.panel.settings["sweep"].set_value("AUTO")
            window.panel.settings["trigger_mode"].set_value("EDGE")

        return [choose, window._generate, window._match_decoder, window._autoscale,
                set_trigger(0xA5), window._acquire, outcome("UART trigger fires on A5", True),
                set_trigger(0x77), window._acquire, outcome("UART trigger silent on 77", False), restore]

    def check_wrong_data_rejected():
        window.expect_sent.setChecked(False)
        window.expected_format.setCurrentIndex(window.expected_format.findData("hex"))
        window.expected_data.setText("DE AD BE EF")
        window._compare()
        outcomes.append(("different data rejected", window.last_compare_ok is False))
        window.expect_sent.setChecked(True)

    def begin_abort_test():
        window.panel.settings["depth"].set_value(ABORT_DEPTH)
        window.waves = {}
        window._acquire()
        QtCore.QTimer.singleShot(ABORT_DELAY_MS, press_abort)

    def press_abort():
        outcomes.append(("abort available during read", window.abort_button.isEnabled()))
        window._abort()

    def check_abort():
        outcomes.append(("abort left no partial data", not window.waves))
        window.panel.settings["depth"].set_value(NORMAL_DEPTH)

    def screen_on():
        window.center_tabs.setCurrentIndex(1)
        window.screen_view.picture = None
        waiting["until"] = time.monotonic() + LIVE_WAIT_S

    def check_screen():
        outcomes.append(("live screen picture", window.screen_view.picture is not None))
        panel = window.panel
        scale = panel.settings["scale1"]
        before = scale.current()
        wanted = 2.0 if before != 2.0 else 1.0
        scale.set_value(wanted)
        checks["vertical"] = (wanted, before)
        waiting["until"] = time.monotonic() + LIVE_WAIT_S

    def check_setting_sent():
        wanted, before = checks["vertical"]
        scope = window.scope
        panel = window.panel
        panel.drain()
        outcomes.append(("setting reaches the scope at once", abs(scope.query_float(":CHANnel1:SCALe?") - wanted) < 1e-6))
        window.grab().save(str(output / "selftest_scope_tab.png"))
        panel.settings["scale1"].set_value(before)
        scope.write(":CHANnel1:SCALe " + str(before))
        scope.write(":TRIGger:MODE RS232")
        waiting["until"] = time.monotonic() + LIVE_WAIT_S

    def check_front_panel_change():
        outcomes.append(("change on the scope shows in the tab", window.panel.trigger_mode.currentData() == "RS232"))
        window.grab().save(str(output / "selftest_trigger_form.png"))
        window.panel.settings["trigger_mode"].set_value("EDGE")
        window.center_tabs.setCurrentIndex(0)

    def polish():
        window._set_language("pl")
        outcomes.append(("language switch", window.connect_button.text() == "Rozłącz"))

    def polish_shots():
        # After the layouts have settled: this runs on a later turn of the event loop.
        window.grab().save(str(output / "selftest_polish.png"))
        window.sidebar_tabs.setCurrentIndex(1)

    def polish_generator_shot():
        window.grab().save(str(output / "selftest_polish_generator.png"))
        window._set_language("en")

    def generator_shot():
        window.grab().save(str(output / "selftest_generator.png"))
        window.sidebar_tabs.setCurrentIndex(0)

    def view_steps():
        window.cursors_button.setChecked(True)
        outcomes.append(("cursors in every row", len(window.cursor_lines[0]) == len(window.plots) > 0))
        window.grab().save(str(output / "selftest_light_logic.png"))
        window.display_mode.setCurrentIndex(window.display_mode.findData("analog"))
        window.set_theme("dark")

    def dark_shot():
        window.grab().save(str(output / "selftest_dark_analog.png"))
        window.display_mode.setCurrentIndex(window.display_mode.findData("logic"))
        window.show_log()

    def log_shot():
        window.log_window.grab().save(str(output / "selftest_log.png"))
        window.grab().save(str(output / "selftest_dark_logic.png"))
        outcomes.append(("log in its own window", window.log_window.isVisible() and window.log_window.isWindow()))
        window.log_window.close()
        window.set_theme(theme_at_start)
        window.cursors_button.setChecked(False)

    def files():
        window.export_csv_to(str(output / "selftest.csv"))
        window.save_waves_to(str(output / "selftest.npz"))
        window.open_waves_from(str(output / "selftest.npz"))

    def check_files():
        window._compare()
        outcomes.append(("saved waves decode again", bool(window.last_compare_ok)))

    def zoom_to_first_row():
        window._row_clicked(window.model.index(0, 0))
        window.grab().save(str(output / "selftest_zoom.png"))

    theme_at_start = window.theme_code
    def many_decoders_steps():
        """Four decoders at once on one capture: two equal ones on other channels, a stack, and
        one with its own options. A changed option alone must decode again."""
        def add_others():
            first = window.active_card
            second = window.new_decoder("I²C")
            rows = {row.spec.key: row for row in second.signal_rows}
            rows["scl"].channel.setCurrentIndex(rows["scl"].channel.findData(digital_channel(0)))
            rows["sda"].channel.setCurrentIndex(rows["sda"].channel.findData(digital_channel(1)))
            third = window.select_sigrok("eeprom24xx")
            rows = {row.spec.key: row for row in third.signal_rows}
            rows["scl"].channel.setCurrentIndex(rows["scl"].channel.findData(digital_channel(9)))
            rows["sda"].channel.setCurrentIndex(rows["sda"].channel.findData(digital_channel(15)))
            fourth = window.new_decoder("UART")
            rows = {row.spec.key: row for row in fourth.signal_rows}
            checks["cards"] = (first, second, third, fourth)
            fourth.signal_rows[0].channel.setCurrentIndex(fourth.signal_rows[0].channel.findData(2))

        def check_all():
            first, second, third, _fourth = checks["cards"]
            data = {card: [item.value for item in window.result.annotations
                           if item.row.startswith(f"{card.uid}/") and item.kind == "DATA"] for card in (first, second)}
            outcomes.append(("two equal decoders agree", data[first] and data[first] == data[second]))
            outcomes.append(("stack among other decoders", any(item.row.startswith(f"{third.uid}/eeprom24xx") for item in window.result.annotations)))
            outcomes.append(("rows of four decoders", len({key.split("/")[0] for key in window.lanes}) >= 3))
            window.grab().save(str(output / "selftest_many_decoders.png"))
            third.config_window.open()
            outcomes.append(("settings in a window of their own", third.config_window.isVisible() and third.config_window.isWindow()))
            third.config_window.grab().save(str(output / "selftest_decoder_window.png"))
            third.config_window.close()
            checks["before"] = {item.row for item in window.result.annotations}
            third.param_widgets["address_format"].setCurrentIndex(third.param_widgets["address_format"].findData("unshifted"))
            checks["texts"] = [item.text for item in window.result.annotations if item.row.startswith(f"{third.uid}/")]
            checks["decode_started"] = window.decode_timer.isActive()

        def check_interactive():
            third = checks["cards"][2]
            texts = [item.text for item in window.result.annotations if item.row.startswith(f"{third.uid}/")]
            outcomes.append(("changing an option decodes by itself", checks["decode_started"] and texts != checks["texts"]))
            window.remove_decoder(checks["cards"][1])
            outcomes.append(("removing one decoder keeps the others", len(window.cards) == 3))

        return [choose_i2c, window._generate, window._match_decoder, window._autoscale, enable_digital, add_others,
                window._acquire, check_all, check_interactive]

    def choose_i2c():
        set_generator("I2C", "hex", "48 65 6C 6C 6F")

    def enable_digital():
        set_digital_groups(True)
        for pod in (1, 2):
            window.panel.settings[f"threshold{pod}"].set_value(LOGIC_THRESHOLD)

    def optional_trigger_steps():
        """I2S, FlexRay and 1553B are options: there is no signal for them here, so only the settings."""
        def send():
            panel = window.panel
            # Only CH1 and CH2 are cabled here; the instrument's own default may name another channel.
            for mode, field, value in (("IIS", "SOURce:CLOCk", "CHANnel1"), ("IIS", "SOURce:WSELect", "CHANnel2"), ("IIS", "SOURce:DATA", "CHANnel1"),
                                       ("IIS", "ALIGnment", "LJ"), ("IIS", "WIDTh", 16), ("IIS", "UWIDth", 8), ("IIS", "WHEN", "INRange"), ("IIS", "DMIN", "1010"), ("FLEXray", "SOURce", "CHANnel1"), ("FLEXray", "BAUD", "5000000"),
                                       ("M1553", "SOURce", "CHANnel1"), ("M1553", "WHEN", "CMD")):
                panel.settings["trigger_mode"].set_value(mode)
                panel.trigger_settings[mode][field].set_value(value)
            panel.trigger_settings["M1553"]["DRTA"].set_value("10X")
            panel.drain()
            scope = window.scope
            checks["optional"] = (scope.query(":TRIGger:IIS:ALIGnment?"), scope.query(":TRIGger:FLEXray:BAUD?"), scope.query(":TRIGger:M1553:WHEN?"))
            checks["sources"] = [scope.query(f":TRIGger:{path}?") for path in ("IIS:SOURce:CLOCk", "IIS:SOURce:WSELect", "IIS:SOURce:DATA",
                                                                               "FLEXray:SOURce", "M1553:SOURce")]
            checks["bits"] = []
            for position in range(5):
                scope.write(f":TRIGger:M1553:DRTA {position}")
                checks["bits"].append(float(scope.query(":TRIGger:M1553:CODE?")))
            checks["word"] = []
            for position in range(8):
                scope.write(f":TRIGger:IIS:DMIN {position}")
                checks["word"].append(float(scope.query(":TRIGger:IIS:CODE?")))
            checks["errors"] = scope.errors()

        def check():
            outcomes.append(("I2S bit field follows the used width", checks["word"] == [1, 0, 1, 0, 255, 255, 255, 255]))
            outcomes.append(("optional triggers accept their settings", checks["optional"] == ("LJ", "5000000", "CMD") and not checks["errors"]))
            outcomes.append(("optional triggers use cabled channels", checks["sources"] == ["CHAN1", "CHAN2", "CHAN1", "CHAN1", "CHAN1"]))
            outcomes.append(("trigger bit field reaches the scope", checks["bits"] == [1, 0, 255, 255, 255]))
            window.panel.settings["trigger_mode"].set_value("EDGE")

        return [send, check]

    def digital_off():
        set_digital_groups(False)
        window.read_format.setCurrentIndex(window.read_format.findData("BYTE"))

    def set_depth():
        window.panel.settings["depth"].set_value(NORMAL_DEPTH)

    def use_file_format():
        window.read_format.setCurrentIndex(window.read_format.findData("FILE"))

    def empty_trace():
        card = window.new_decoder("UART")
        outcomes.append(("a decoder has its trace before any data", list(window.lanes) == [f"{card.uid}/"]))
        window.decoder_sort.setCurrentIndex(window.decoder_sort.findData("parent"))
        window.grab()  # the first paint measures the axis text
        app.processEvents()
        window.grab().save(str(output / "selftest_empty_trace.png"))
        window.decoder_sort.setCurrentIndex(window.decoder_sort.findData("category"))
        window.remove_decoder(card)

    steps = [window._toggle_connection, set_depth, empty_trace]
    steps += signal_steps("I2C", "I2C", "hex", "48 65 6C 6C 6F")
    steps += signal_steps("SPI", "SPI", "ascii", "Hello")
    steps += signal_steps("CAN", "CAN", "hex", "11 22 33 44")
    steps += signal_steps("CAN differential", "CAN", "hex", "11 22 33 44", differential=True)
    steps += signal_steps("CAN FD", "CAN FD", "ascii", "CAN FD test 1234", rate=500000, data_rate=2000000)
    steps += signal_steps("CAN FD differential", "CAN FD", "hex", "01 02 03 04 05 06 07 08", differential=True)
    steps += signal_steps("UART", "UART", "ascii", "Hello\\r\\n", rate=115200)
    steps += [check_wrong_data_rejected]
    steps += [lambda: window.grab().save(str(output / "selftest.png")), zoom_to_first_row, files, check_files]
    steps += sigrok_steps("sigrok I2C + eeprom24xx", "I2C", "10 AB", "eeprom24xx", {"scl": 1, "sda": 2}, {},
                          "Byte write (addr=10, 1 byte): AB", "selftest_sigrok_i2c.png")
    steps += sigrok_steps("sigrok UART + MIDI", "UART", "90 3C 40", "midi", {"rx": 1}, {"baudrate": 31250},
                          "note on (note = 60", "selftest_sigrok_midi.png", rate=31250)
    steps += [browser_shot, view_steps, dark_shot, log_shot]
    steps += digital_steps("digital D0 and D1", 0, 1)
    steps += digital_steps("digital D9 and D15", 9, 15)
    steps += many_decoders_steps()
    steps += optional_trigger_steps()
    steps += [digital_off, use_file_format]
    steps += signal_steps("analog through the BIN file", "UART", "ascii", "file", rate=115200)
    steps += [digital_off]
    steps += uart_trigger_steps()
    steps += swapped_output_steps()
    steps += [begin_abort_test, check_abort]
    steps += signal_steps("after abort, UART still on AFG2", "UART", "hex", "A5 5A", rate=115200)
    steps += [screen_on, check_screen, check_setting_sent, check_front_panel_change, polish, polish_shots, polish_generator_shot, generator_shot, window._afg_off]
    total_steps = len(steps)

    def finish():
        for name, passed in outcomes:
            window._log(f"{'PASS' if passed else 'FAIL'}  {name}")
        (output / "selftest.log").write_text(window.log.toPlainText(), encoding="utf-8")
        window.verbose.setChecked(False)  # the option is remembered and the test turned it on
        passed = bool(outcomes) and all(item[1] for item in outcomes)
        window.close()  # ends the scope tab's thread
        app.exit(0 if passed else 1)

    def advance():
        if (window.worker is not None or window.after_job is not None or window.decode_timer.isActive()
                or window.decode_pending or time.monotonic() < waiting["until"]):
            QtCore.QTimer.singleShot(100, advance)
            return
        if not steps or (window.scope is None and len(steps) < total_steps):
            finish()
            return
        steps.pop(0)()
        QtCore.QTimer.singleShot(100, advance)

    QtCore.QTimer.singleShot(100, advance)


def enable_dpi_scaling() -> None:
    """Must run before the application object exists.

    Without it, on screens with different scaling, QPicture records at the primary screen's DPI and
    replays at the window's: pyqtgraph's time axis and annotation bars came out at 2/3 of their
    real size next to the traces (primary screen at 150%, window on one at 100%).
    """
    from PyQt5 import QtCore, QtGui

    QtCore.QCoreApplication.setAttribute(QtCore.Qt.AA_EnableHighDpiScaling, True)
    QtGui.QGuiApplication.setHighDpiScaleFactorRoundingPolicy(QtCore.Qt.HighDpiScaleFactorRoundingPolicy.PassThrough)


def main() -> int:
    parser = argparse.ArgumentParser(description="MHO900 protocol decoder")
    parser.add_argument("--selftest", metavar="HOST", help="go through the window's functions on the AFG loop-back and exit")
    parser.add_argument("--output", default=".", help="folder for the self-test log and screenshots")
    args = parser.parse_args()

    from PyQt5 import QtWidgets

    from mhodecode.gui import MainWindow

    enable_dpi_scaling()
    app = QtWidgets.QApplication(sys.argv)
    window = MainWindow()
    window.show()
    window._refresh_buttons()
    if args.selftest:
        window.host.setText(args.selftest)
        window.verbose.setChecked(True)
        run_selftest(window, app, args.selftest, Path(args.output))
    return app.exec_()


if __name__ == "__main__":
    sys.exit(main())

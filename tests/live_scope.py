"""End-to-end test on a real MHO98: AFG1 wired to CH1 and AFG2 wired to CH2.

Every case plays a bus signal from the AFG, captures it on the analog channels and
checks that the decoder returns exactly what was sent. A pass needs the sent
sequence to appear complete and error-free in the decoded stream.

Usage: python tests/live_scope.py [ip] [case-filter]
"""
from __future__ import annotations

import sys
import threading
import time
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from mhodecode import abort, generator, siggen  # noqa: E402
from mhodecode.decoders import DECODERS  # noqa: E402
from mhodecode.scope import Scope  # noqa: E402

HIGH_V = 3.3
TEXT = b"MHO98 test: Hello!"
DEPTH = 1_000_000


def play(scope: Scope, line1, line2, frequency, low1=0.0, high1=HIGH_V, low2=0.0, high2=HIGH_V):
    scope.afg_enable(1, False)
    scope.afg_enable(2, False)
    scope.afg_play(1, "mhodecode_afg1.csv", line1, frequency, low1, high1)
    if line2 is not None:
        scope.afg_play(2, "mhodecode_afg2.csv", line2, frequency, low2, high2)
        scope.afg_align()


def capture(scope: Scope, period: float, two_channels: bool, trigger_level=HIGH_V / 2,
            trigger_channel=1, slope="NEGative", vmin=0.0, vmax=HIGH_V, sample_format="BYTE"):
    """Capture about two record periods so one whole record is always inside."""
    scope.set_channel(1, True)
    scope.set_channel(2, two_channels)
    scale = (vmax - vmin) / 5
    for channel in (1, 2) if two_channels else (1,):
        scope.set_probe(channel, 1)  # AFG outputs are cabled 1:1
        scope.set_dc_coupling(channel)
        scope.set_vertical(channel, scale, -(vmax + vmin) / 2)
    scope.set_memory_depth(DEPTH)
    scope.set_time_scale(period / 5)
    scope.set_edge_trigger(trigger_channel, trigger_level, slope)
    time.sleep(0.4)
    scope.single()
    waves = [scope.fetch(1, sample_format=sample_format)]
    if two_channels:
        waves.append(scope.fetch(2, sample_format=sample_format))
    return waves


class NoSignal:
    """Fills the report columns of checks that are not about a bus signal."""
    bit_rate = 1.0
    afg_frequency = 0.0


def case_formats_agree(scope):
    """BYTE and WORD reads of one capture must give the same volts within one BYTE step."""
    signal = siggen.uart(TEXT, 115200, 16)
    play(scope, signal.lines["tx"], None, signal.afg_frequency)
    byte_wave, = capture(scope, 16384 / signal.sample_rate, False)
    word_wave = scope.fetch(1, sample_format="WORD")
    state = scope.channel_state(1)
    step = state["scale"] * 10 / 256  # the screen holds 10 divisions of 8-bit codes at most
    difference = float(np.max(np.abs(byte_wave.volts - word_wave.volts)))
    return difference <= 2 * step and len(byte_wave.volts) == len(word_wave.volts), byte_wave.dt, NoSignal, \
        f"max różnica {difference * 1000:.1f} mV, krok {step * 1000:.1f} mV"


def case_deep_read(scope, depth):
    """A read after shrinking from a deeper memory must return the whole new depth."""
    scope.set_channel(1, True)
    scope.set_channel(2, False)
    scope.set_memory_depth(depth)
    scope.set_time_scale(1e-3)
    time.sleep(0.4)
    scope.single(timeout=30)
    started = time.monotonic()
    wave = scope.fetch(1)
    elapsed = time.monotonic() - started
    return len(wave.volts) == depth, wave.dt, NoSignal, f"{len(wave.volts):,} punktów w {elapsed:.1f} s"


def case_abort_read(scope):
    """Aborting a long read must leave a working link and a clean read afterwards."""
    scope.set_channel(1, True)
    scope.set_channel(2, False)
    scope.set_memory_depth(10_000_000)
    scope.set_time_scale(1e-3)
    time.sleep(0.4)
    scope.single(timeout=30)
    timer = threading.Timer(1.5, abort.request)
    timer.start()
    started = time.monotonic()
    try:
        scope.fetch(1)
        aborted = False
    except abort.OperationAborted:
        aborted = True
    stopped_after = time.monotonic() - started
    recover_started = time.monotonic()
    scope.recover()
    recovered_after = time.monotonic() - recover_started
    scope.set_memory_depth(1_000_000)
    time.sleep(0.4)
    scope.single(timeout=30)
    wave = scope.fetch(1)
    ok = aborted and len(wave.volts) == 1_000_000 and stopped_after < 2.5
    return ok, wave.dt, NoSignal, f"zatrzymanie po {stopped_after:.2f} s, powrót {recovered_after:.2f} s"


def case_abort_waiting_for_trigger(scope):
    """Abort must end a capture waiting for a trigger that never comes and stop the scope."""
    scope.afg_enable(1, False)
    scope.afg_enable(2, False)
    scope.set_channel(1, True)
    scope.set_memory_depth(100_000)
    scope.set_time_scale(1e-5)
    scope.set_edge_trigger(1, 4.9, "POSitive")
    time.sleep(0.3)
    timer = threading.Timer(1.0, abort.request)
    timer.start()
    try:
        scope.single(timeout=None, force_after=None)
        aborted = False
    except abort.OperationAborted:
        aborted = True
    scope.recover()
    time.sleep(0.2)
    status = scope.query(":TRIGger:STATus?")
    return aborted and status == "STOP", 1e-8, NoSignal, f"status {status}"


def case_screenshot(scope):
    started = time.monotonic()
    picture = scope.screenshot()
    elapsed = time.monotonic() - started
    return picture[:8] == b"\x89PNG\r\n\x1a\n", 1e-8, NoSignal, f"{len(picture):,} B w {elapsed:.2f} s"


def case_generator_outputs(scope):
    """Line on AFG2 only, levels chosen by the user: CH2 toggles between them, CH1 stays silent."""
    params = {param.key: param.default for param in generator.PROTOCOL_PARAMS["UART"]}
    generated = generator.build("UART", TEXT, params)
    generator.assign(generated, {"tx": 2}, {1: (0.0, 3.3), 2: (0.5, 2.5)})
    generator.play(scope, generated)
    scope.set_channel(1, True)
    scope.set_channel(2, True)
    for channel in (1, 2):
        scope.set_probe(channel, 1)
        scope.set_dc_coupling(channel)
        scope.set_vertical(channel, 0.66, -1.65)
    scope.set_memory_depth(DEPTH)
    scope.set_time_scale(generated.time_scale)
    scope.set_edge_trigger(2, 1.5, "NEGative")
    time.sleep(0.4)
    scope.single()
    silent = scope.fetch(1)
    wave = scope.fetch(2)
    low, high = float(np.percentile(wave.volts, 1)), float(np.percentile(wave.volts, 99))
    swing_silent = float(np.percentile(silent.volts, 99) - np.percentile(silent.volts, 1))
    decoder = DECODERS["UART"]
    result = decoder.decode(decoder.prepare({"tx": wave}, {}), **generated.decoder_params)
    ok = (generator.compare(result.annotations, TEXT).matched and abs(low - 0.5) < 0.15 and abs(high - 2.5) < 0.15
          and swing_silent < 0.2)
    return ok, wave.dt, NoSignal, f"CH2 {low:.2f}…{high:.2f} V, CH1 zakres {swing_silent:.3f} V"


def case_generator_no_overvoltage(scope):
    """Starting the generator after a 5 V waveform must never put more than the new high level
    on the output: a device could already be connected."""
    params = {param.key: param.default for param in generator.PROTOCOL_PARAMS["UART"]}
    # Leave the output at a large amplitude, as earlier use could.
    loud = generator.build("UART", TEXT, params)
    generator.assign(loud, {"tx": 1}, {1: (0.0, 5.0), 2: (0.0, 3.3)})
    generator.play(scope, loud)
    scope.afg_enable(1, False)
    time.sleep(0.5)
    # Arm a trigger above the new high level before the quiet start.
    scope.set_channel(1, True)
    scope.set_channel(2, False)
    scope.set_probe(1, 1)
    scope.set_dc_coupling(1)
    scope.set_vertical(1, 1.0, -2.5)
    scope.set_memory_depth(100_000)
    scope.set_time_scale(1e-3)
    scope.set_edge_trigger(1, 2.5, "POSitive")
    scope.write(":TRIGger:SWEep NORMal")
    time.sleep(0.3)
    scope.write(":SINGle")
    time.sleep(1.0)
    quiet = generator.build("UART", TEXT, params)
    generator.assign(quiet, {"tx": 1}, {1: (0.0, 1.8), 2: (0.0, 3.3)})
    generator.play(scope, quiet)
    time.sleep(0.5)
    status = scope.query(":TRIGger:STATus?")
    # Control: the same armed trigger must fire when the output does go above 2.5 V.
    generator.assign(loud, {"tx": 1}, {1: (0.0, 3.3), 2: (0.0, 3.3)})
    generator.play(scope, loud)
    time.sleep(0.5)
    control = scope.query(":TRIGger:STATus?")
    scope.write(":TRIGger:SWEep AUTO")
    scope.stop()
    return status != "STOP" and control == "STOP", 1e-8, NoSignal, \
        f"wyzwalanie na 2,5 V: przy 1,8 V {status}, kontrola przy 3,3 V {control}"


CASES = [
    ("BYTE and WORD agree", case_formats_agree, ()),
    ("Read 10 M", case_deep_read, (10_000_000,)),
    ("Read 100 M", case_deep_read, (100_000_000,)),
    ("Read 1 M after 100 M", case_deep_read, (1_000_000,)),
    ("Abort a long read", case_abort_read, ()),
    ("Abort waiting for trigger", case_abort_waiting_for_trigger, ()),
    ("Screenshot", case_screenshot, ()),
    ("Generator output and levels", case_generator_outputs, ()),
    ("Generator no overvoltage", case_generator_no_overvoltage, ()),
]


def main() -> int:
    host = sys.argv[1] if len(sys.argv) > 1 else "192.0.2.1"
    pattern = sys.argv[2].lower() if len(sys.argv) > 2 else ""
    scope = Scope(host)
    print(scope.connect())
    failed = 0
    try:
        for name, function, args in CASES:
            if pattern not in name.lower():
                continue
            ok, dt, signal, info = function(scope, *args)
            failed += not ok
            per_bit = 1 / (signal.bit_rate * dt)
            print(f"{'PASS' if ok else 'FAIL'}  {name:32s} {per_bit:9.1f} próbek/bit  AFG {signal.afg_frequency:9.1f} Hz  {info}", flush=True)
    finally:
        abort.clear()
        scope.afg_enable(1, False)
        scope.afg_enable(2, False)
        scope.run()
        scope.close()
    print(f"Niepowodzenia: {failed}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())

"""The libsigrokdecode decoders on real captures from an MHO98: AFG1 wired to CH1, AFG2 to CH2.

Every case plays a bus signal, captures it on the analog channels and needs the sent data back
from a sigrok decoder, alone or with another decoder stacked on it.

Usage: python tests/live_sigrok.py <ip> [case-filter]
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import live_scope  # noqa: E402
from live_scope import capture, play  # noqa: E402
from mhodecode import generator, siggen  # noqa: E402
from mhodecode.decoders import SIGROK_ROOTS  # noqa: E402
from mhodecode.scope import Scope, Waveform  # noqa: E402
from mhodecode.signal import digitize  # noqa: E402

TEXT = live_scope.TEXT
RECORD = 16384


def decode(pd_id, waves, **params):
    decoder = SIGROK_ROOTS[pd_id]
    return decoder.decode(decoder.prepare(waves, {}), **params)


def matched(result, data):
    return generator.compare(result.annotations, data).matched


def case_uart(scope, baud, parity="none", data_bits=8):
    signal = siggen.uart(TEXT, baud, 16, data_bits, parity)
    play(scope, signal.lines["tx"], None, signal.afg_frequency)
    wave, = capture(scope, RECORD / signal.sample_rate, False)
    result = decode("uart", {"tx": wave}, baudrate=baud, parity=parity, data_bits=data_bits)
    return matched(result, TEXT), f"notes {result.notes}"


def case_i2c(scope, clock):
    data = b"\x10\x12\xff\xa5"
    signal = siggen.i2c([(0x50, False, data)], clock)
    play(scope, signal.lines["scl"], signal.lines["sda"], signal.afg_frequency)
    scl, sda = capture(scope, RECORD / signal.sample_rate, True)
    result = decode("i2c", {"scl": scl, "sda": sda})
    texts = [item.text for item in result.annotations]
    return matched(result, data) and "Address write: 0x50" in texts, f"notes {result.notes}"


def case_i2c_stack(scope):
    signal = siggen.i2c([(0x50, False, b"\x10\xab")], 400000)
    play(scope, signal.lines["scl"], signal.lines["sda"], signal.afg_frequency)
    scl, sda = capture(scope, RECORD / signal.sample_rate, True)
    result = decode("i2c", {"scl": scl, "sda": sda}, srd_stack=[("eeprom24xx", {})])
    upper = [item.text for item in result.annotations if item.row.startswith("eeprom24xx:")]
    return bool(upper), f"{len(upper)} eeprom24xx rows, first {upper[:3]}, notes {result.notes}"


def case_spi(scope, mode):
    words = [list(b"\xa5\x5a\xc3\x3c")]
    signal = siggen.spi(words, 1000000, 16, mode)
    play(scope, signal.lines["clk"], signal.lines["mosi"], signal.afg_frequency)
    clk, mosi = capture(scope, RECORD / signal.sample_rate, True, trigger_channel=1, slope="POSitive")
    params = dict(cpol=mode >> 1, cpha=mode & 1)
    from_start = matched(decode("spi", {"clk": clk, "mosi": mosi}, **params), bytes(words[0]))
    # Without CS the sigrok decoder counts bits from the first edge it sees and never realigns,
    # so the decoding starts in the longest pause of the clock, as a CS line would make it.
    edges = np.asarray(digitize(clk.volts, clk.dt, clk.t0).edges)
    gap = int(np.argmax(np.diff(edges)))
    start = int(edges[gap] + edges[gap + 1]) // 2
    cut = {key: Waveform(wave.channel, wave.volts[start:], wave.dt, wave.t0 + start * wave.dt)
           for key, wave in (("clk", clk), ("mosi", mosi))}
    result = decode("spi", cut, **params)
    return matched(result, bytes(words[0])), f"whole capture matched: {from_start}, notes {result.notes}"


def case_midi_stack(scope):
    data = bytes([0x90, 0x3C, 0x40])
    signal = siggen.uart(data, 31250, 16)
    play(scope, signal.lines["tx"], None, signal.afg_frequency)
    wave, = capture(scope, RECORD / signal.sample_rate, False)
    result = decode("uart", {"rx": wave}, baudrate=31250, srd_stack=[("midi", {})])
    upper = [item.text for item in result.annotations if item.row.startswith("midi:")]
    return any("note on (note = 60" in text for text in upper), f"midi {upper[:2]}, notes {result.notes}"


def case_can(scope, bitrate):
    signal = siggen.can([(0x123, b"\x11\x22\x33\x44", False)], bitrate, 16)
    play(scope, signal.lines["can"], None, signal.afg_frequency)
    wave, = capture(scope, RECORD / signal.sample_rate, False)
    result = decode("can", {"can_rx": wave}, nominal_bitrate=bitrate, sample_point=70.0)
    texts = [item.text for item in result.annotations]
    bytes_read = [text for text in texts if text.startswith("Data byte")]
    wanted = ["Data byte 0: 0x11", "Data byte 1: 0x22", "Data byte 2: 0x33", "Data byte 3: 0x44"]
    found = "Identifier: 291 (0x123)" in texts and all(text in bytes_read for text in wanted)
    # The record repeats and a capture cuts frames at its ends, so errors may sit only at the edges.
    return found, f"{len(bytes_read)} data bytes, notes {result.notes}"


def case_speed(scope):
    """A long capture on both decoders, for the speed of the Python core."""
    signal = siggen.uart(TEXT, 115200, 16)
    play(scope, signal.lines["tx"], None, signal.afg_frequency)
    previous = live_scope.DEPTH
    live_scope.DEPTH = 10_000_000
    try:
        wave, = capture(scope, RECORD / signal.sample_rate, False)
    finally:
        live_scope.DEPTH = previous
    started = time.monotonic()
    result = decode("uart", {"tx": wave}, baudrate=115200)
    return matched(result, TEXT), (f"{len(wave.volts):,} points, {len(result.annotations):,} annotations, "
                                   f"{time.monotonic() - started:.2f} s")


CASES = [
    ("UART 9600", lambda scope: case_uart(scope, 9600)),
    ("UART 115200", lambda scope: case_uart(scope, 115200)),
    ("UART 1 M", lambda scope: case_uart(scope, 1000000)),
    ("UART 115200 7E", lambda scope: case_uart(scope, 115200, "even", 7)),
    ("I2C 100 k", lambda scope: case_i2c(scope, 100000)),
    ("I2C 400 k", lambda scope: case_i2c(scope, 400000)),
    ("I2C 1 M", lambda scope: case_i2c(scope, 1000000)),
    ("SPI mode 0", lambda scope: case_spi(scope, 0)),
    ("SPI mode 1", lambda scope: case_spi(scope, 1)),
    ("SPI mode 2", lambda scope: case_spi(scope, 2)),
    ("SPI mode 3", lambda scope: case_spi(scope, 3)),
    ("CAN 125 k", lambda scope: case_can(scope, 125000)),
    ("CAN 500 k", lambda scope: case_can(scope, 500000)),
    ("stack I2C + eeprom24xx", case_i2c_stack),
    ("stack UART + MIDI", case_midi_stack),
    ("speed on 10 M points", case_speed),
]


def main():
    sys.stdout.reconfigure(encoding="utf-8", errors="backslashreplace")
    if len(sys.argv) < 2:
        sys.exit("Usage: python tests/live_sigrok.py <ip> [case-filter]")
    host = sys.argv[1]
    scope = Scope(host)
    print(scope.connect())
    failures = 0
    try:
        for name, case in CASES:
            if len(sys.argv) > 2 and sys.argv[2] not in name:
                continue
            passed, info = case(scope)
            failures += not passed
            print(f"{'PASS' if passed else 'FAIL'}  {name}: {info}")
    finally:
        scope.afg_enable(1, False)
        scope.afg_enable(2, False)
        scope.set_memory_depth(1_000_000)
        print("scope errors:", scope.errors())
        scope.run()
        scope.close()
    print(f"failed: {failures}")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())

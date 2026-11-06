"""Independent check against the scope's own bus decoder (:BUS1 commands, firmware decoder).

The generator and the decoder in this project share an author, so a shared misreading of
a protocol would pass the loop-back test. Here the same AFG signal also goes through the
instrument's decoder and both event lists are printed for comparison. CAN FD frames also
cover payloads that end the stuffed part on a run of five equal bits, where the fixed stuff
bit stands in for the dynamic one, and the two decoders must agree on those.

Usage: python tests/live_crosscheck.py [ip]
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from mhodecode import siggen  # noqa: E402
from mhodecode.decoders import DECODERS  # noqa: E402
from mhodecode.scope import Scope  # noqa: E402

THRESHOLDS = {"RS232": ["TX"], "IIC": ["SCL", "SDA"], "SPI": ["CLK", "MOSI"], "CAN": ["CAN"], "CAN FD": ["CAN"]}
CAN_COMMANDS = [":BUS1:MODE CAN", ":BUS1:CAN:SOURce CHANnel1", ":BUS1:CAN:STYPe CANL", ":BUS1:CAN:BAUD 500000",
                ":BUS1:CAN:SPOint 75", ":BUS1:CAN:FDBaud 2000000", ":BUS1:CAN:FDSPoint 75"]


def run_of_five_payloads(count: int) -> list[bytes]:
    """One-byte payloads whose CAN FD frame reaches the CRC field right after five equal bits."""
    found = []
    for value in range(256):
        data = bytes([value])
        stuffed, _ = siggen._dynamic_stuff(frame_bits(0x123, data))
        if len(set(stuffed[-5:])) == 1:
            found.append(data)
        if len(found) == count:
            break
    return found


def frame_bits(identifier: int, data: bytes) -> list[int]:
    bits = [0] + [(identifier >> index) & 1 for index in range(10, -1, -1)] + [0, 0, 1, 0, 1, 0]
    bits += [(len(data) >> index) & 1 for index in range(3, -1, -1)]
    for byte in data:
        bits += [(byte >> index) & 1 for index in range(7, -1, -1)]
    return bits


CASES = {
    "RS232": (lambda: siggen.uart(b"Hello", 115200), "UART", {"tx": 1}, {"baudrate": 115200}, [
        ":BUS1:MODE RS232", ":BUS1:RS232:TX CHANnel1", ":BUS1:RS232:BAUD 115200", ":BUS1:RS232:DBITs 8",
        ":BUS1:RS232:PARity NONE", ":BUS1:RS232:POLarity POSitive", ":BUS1:RS232:ENDian LSB"]),
    "IIC": (lambda: siggen.i2c([(0x50, False, b"\x12\x34\xfe")], 400e3), "I²C", {"scl": 1, "sda": 2}, {}, [
        ":BUS1:MODE IIC", ":BUS1:IIC:SCLK:SOURce CHANnel1", ":BUS1:IIC:SDA:SOURce CHANnel2", ":BUS1:IIC:ADDBits NORMal"]),
    "SPI": (lambda: siggen.spi([[0xA5, 0x3C, 0x0F]], 1e6), "SPI", {"clk": 1, "mosi": 2}, {"cpol": 0, "cpha": 0}, [
        ":BUS1:MODE SPI", ":BUS1:SPI:SCLK:SOURce CHANnel1", ":BUS1:SPI:MOSI:SOURce CHANnel2",
        ":BUS1:SPI:MODE TIMeout", ":BUS1:SPI:TIMeout:TIME 5e-6", ":BUS1:SPI:SCLK:SLOPe POSitive",
        ":BUS1:SPI:DBITs 8", ":BUS1:SPI:ENDian MSB", ":BUS1:SPI:MOSI:POLarity POSitive"]),
    "CAN": (lambda: siggen.can([(0x123, b"\x11\x22\x33\x44\x55\x66\x77\x88", False), (0x18DAF110, b"\xde\xad", True)], 500e3),
            "CAN", {"can_rx": 1}, {"nominal_bitrate": 500000}, CAN_COMMANDS),
    "CAN FD": (lambda: siggen.can_fd([(0x123, bytes(range(1, 9)), False), (0x18DAF110, bytes(range(0x20, 0x34)), True),
                                      (0x2AA, bytes(0xFF - index * 3 for index in range(64)), False)], 500e3, 2e6, 8),
               "CAN", {"can_rx": 1}, {"nominal_bitrate": 500000, "fast_bitrate": 2000000}, CAN_COMMANDS),
    "CAN FD run of five": (lambda: siggen.can_fd([(0x123, data, False) for data in run_of_five_payloads(6)], 500e3, 2e6, 8),
                           "CAN", {"can_rx": 1}, {"nominal_bitrate": 500000, "fast_bitrate": 2000000}, CAN_COMMANDS),
}


def main() -> int:
    scope = Scope(sys.argv[1] if len(sys.argv) > 1 else "192.0.2.1", timeout=8)
    print(scope.connect())
    try:
        for name, (make, decoder_name, mapping, params, commands) in CASES.items():
            signal = make()
            lines = list(signal.lines.values())
            scope.afg_enable(1, False)
            scope.afg_enable(2, False)
            for index, line in enumerate(lines, start=1):
                scope.afg_play(index, f"mhodecode_afg{index}.csv", line, signal.afg_frequency, 0.0, 3.3)
            if len(lines) == 2:
                scope.afg_align()
            channels = sorted(set(mapping.values()))
            scope.set_channel(1, True)
            scope.set_channel(2, 2 in channels)
            for channel in channels:
                scope.set_probe(channel, 1)
                scope.set_dc_coupling(channel)
                scope.set_vertical(channel, 0.66, -1.65)
            scope.set_memory_depth(1_000_000)
            scope.set_time_scale(len(lines[0]) / signal.sample_rate * 2 / 10)
            scope.set_edge_trigger(channels[-1], 1.65, "NEGative")
            for command in commands:
                scope.write(command)
            scope.write(":BUS1:FORMat HEX")
            scope.write(":BUS1:DISPlay ON")
            scope.write(":BUS1:EVENt ON")
            for threshold_type in THRESHOLDS[name.split(" run")[0]]:
                scope.write(f":BUS1:THReshold 1.65,{threshold_type.split()[0]}")
            time.sleep(0.5)
            print(f"\n=== {name}: command errors {scope.errors()}")
            scope.single()
            time.sleep(1.0)
            try:
                firmware = bytes(scope.query_block(":BUS1:DATA?", timeout=15)).decode("ascii", "replace")
            except Exception as exc:  # the report must continue with the next protocol
                firmware = f"<no event table: {exc}>"
            print("Oscilloscope:\n" + "\n".join(firmware.splitlines()[:14]))
            decoder = DECODERS[decoder_name]
            waves = {key: scope.fetch(channel) for key, channel in mapping.items()}
            result = decoder.decode(decoder.prepare(waves, {}), **params)
            items = [a for a in result.annotations if not a.detail]
            print("Application:")
            for item in items[:12]:
                print(f"  {item.start * 1e6:10.2f} us  {item.kind:6s} {item.text} {item.error}")
            crc_fields = [a.text for a in result.annotations if a.kind == "CRC"]
            if crc_fields:
                print("  CRC:", crc_fields[:6])
    finally:
        scope.write(":BUS1:DISPlay OFF")
        scope.afg_enable(1, False)
        scope.afg_enable(2, False)
        scope.run()
        scope.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())

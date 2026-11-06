"""Generators of bus test signals, used to drive the AFG outputs and by the tests.

Each function returns a TestSignal with one 0/1 sample array per line. The samples
are written straight into the AFG arbitrary-waveform file, so they fit its record.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from .i18n import tr

ARB_POINTS = 16384
FD_DLC_LENGTHS = (0, 1, 2, 3, 4, 5, 6, 7, 8, 12, 16, 20, 24, 32, 48, 64)
CRC17_POLY = 0x1685B
CRC21_POLY = 0x102899
FD_CRC_SWITCH_LENGTH = 16  # payloads up to this many bytes use CRC17, longer ones CRC21
FIXED_STUFF_PERIOD = 4


def crc15(bits) -> int:
    crc = 0
    for bit in bits:
        feedback = bit ^ ((crc >> 14) & 1)
        crc = (crc << 1) & 0x7FFF
        if feedback:
            crc ^= 0x4599
    return crc


def crc_fd(bits, data_length: int) -> int:
    """CRC17 or CRC21 of a CAN FD frame. The register starts with its top bit set (ISO 11898-1:2015)."""
    size, poly = (17, CRC17_POLY) if data_length <= FD_CRC_SWITCH_LENGTH else (21, CRC21_POLY)
    top = 1 << (size - 1)
    mask = (1 << size) - 1
    crc = top
    for bit in bits:
        feedback = bit ^ ((crc >> (size - 1)) & 1)
        crc = (crc << 1) & mask
        if feedback:
            crc ^= poly
    return crc


def stuff_count_bits(count: int) -> list[int]:
    """Stuff count modulo 8 as 3 Gray-coded bits followed by even parity."""
    value = count % 8
    gray = value ^ (value >> 1)
    bits = [(gray >> 2) & 1, (gray >> 1) & 1, gray & 1]
    return bits + [sum(bits) & 1]


@dataclass
class TestSignal:
    lines: dict[str, np.ndarray]
    bit_rate: float  # bits per second on the wire
    samples_per_bit: int

    @property
    def sample_rate(self) -> float:
        return self.bit_rate * self.samples_per_bit

    @property
    def afg_frequency(self) -> float:
        """AFG frequency which plays the whole record at sample_rate."""
        return self.sample_rate / len(next(iter(self.lines.values())))


def _finish(lines: dict[str, list[int]], bit_rate: float, samples_per_bit: int, length: int) -> TestSignal:
    arrays = {}
    for key, levels in lines.items():
        if len(levels) > length:
            raise ValueError(tr("Sygnał ma {samples} próbek, a rekord AFG mieści {limit}. Skróć dane.",
                                samples=len(levels), limit=length))
        arrays[key] = np.concatenate((
            np.asarray(levels, dtype=np.int8),
            np.full(length - len(levels), levels[-1], dtype=np.int8)))
    return TestSignal(arrays, bit_rate, samples_per_bit)


def uart(data: bytes, baud: float, samples_per_bit: int = 16, data_bits: int = 8, parity: str = "none",
         stop_bits: float = 1.0, msb_first: bool = False, length: int = ARB_POINTS) -> TestSignal:
    idle_samples = samples_per_bit * 8
    levels = [1] * idle_samples
    for value in data:
        bits = [(value >> index) & 1 for index in range(data_bits)]
        if msb_first:
            bits.reverse()
        frame = [0] + bits
        if parity != "none":
            ones = sum(bits)
            frame.append(ones % 2 if parity == "even" else 1 - ones % 2)
        for bit in frame:
            levels += [bit] * samples_per_bit
        levels += [1] * round(stop_bits * samples_per_bit)
    levels += [1] * idle_samples
    return _finish({"tx": levels}, baud, samples_per_bit, length)


def i2c(transactions: list[tuple[int, bool, bytes]], clock: float, samples_per_bit: int = 16,
        length: int = ARB_POINTS) -> TestSignal:
    """transactions: (7-bit address, read, payload). Every byte is ACKed except the last read."""
    if samples_per_bit % 4:
        raise ValueError("I2C: samples_per_bit musi być wielokrotnością 4")
    quarter = samples_per_bit // 4
    scl: list[int] = []
    sda: list[int] = []

    def hold(clock_level, data_level, quarters=1):
        scl.extend([clock_level] * (quarter * quarters))
        sda.extend([data_level] * (quarter * quarters))

    def send_bit(bit):
        hold(0, bit)       # data changes while SCL is low
        hold(1, bit, 2)
        hold(0, bit)

    hold(1, 1, 16)
    for address, read, payload in transactions:
        hold(1, 1, 2)
        hold(1, 0, 2)  # START
        hold(0, 0)
        for byte in [(address << 1) | int(read)]:
            for index in range(7, -1, -1):
                send_bit((byte >> index) & 1)
            send_bit(0)
        for position, byte in enumerate(payload):
            for index in range(7, -1, -1):
                send_bit((byte >> index) & 1)
            nack = read and position == len(payload) - 1
            send_bit(1 if nack else 0)
        hold(0, 0)
        hold(1, 0, 2)
        hold(1, 1, 2)  # STOP
    hold(1, 1, 16)
    return _finish({"scl": scl, "sda": sda}, clock, samples_per_bit, length)


def spi(words: list[list[int]], clock: float, samples_per_bit: int = 16, mode: int = 0, word_bits: int = 8,
        msb_first: bool = True, length: int = ARB_POINTS, line: str = "mosi") -> TestSignal:
    """words: list of transfers, each a list of words; transfers are split by an idle gap."""
    half = samples_per_bit // 2
    cpol, cpha = (mode >> 1) & 1, mode & 1
    clk: list[int] = []
    data: list[int] = []
    gap = samples_per_bit * 8
    clk += [cpol] * gap
    data += [0] * gap
    for transfer in words:
        for word in transfer:
            for index in range(word_bits):
                position = word_bits - 1 - index if msb_first else index
                bit = (word >> position) & 1
                # CPHA=0: data set up half a clock before the leading edge.
                # CPHA=1: data changes on the leading edge, sampled on the trailing one.
                clk += [cpol] * half if cpha == 0 else [1 - cpol] * half
                data += [bit] * half
                clk += [1 - cpol] * half if cpha == 0 else [cpol] * half
                data += [bit] * half
        clk += [cpol] * gap
        data += [0] * gap
    return _finish({"clk": clk, line: data}, clock, samples_per_bit, length)


def i2s(samples: list[tuple[int, int]], bit_clock: float, word_bits: int = 16, samples_per_bit: int = 8,
        fmt: str = "i2s", length: int = ARB_POINTS) -> TestSignal:
    """samples: (left, right) pairs. Slot size equals word size except rj (word in a 32-bit slot)."""
    half = samples_per_bit // 2
    slot = 32 if fmt == "rj" else word_bits
    serial: list[tuple[int, int]] = []  # (ws, sd) per clock
    for left, right in samples:
        for ws, value in ((0, left), (1, right)):
            pad = slot - word_bits
            for index in range(slot):
                if index < pad:
                    bit = 0
                else:
                    bit = (value >> (slot - 1 - index)) & 1
                serial.append((ws, bit))
    ws_line = [item[0] for item in serial]
    # In I2S format SD lags WS by one clock.
    sd_line = ([0] if fmt == "i2s" else []) + [item[1] for item in serial]
    # A further slot at the end so the last sample has a closing WS change.
    ws_line += [1 - ws_line[-1]] * (slot + 2)
    sd_line += [0] * (len(ws_line) - len(sd_line))
    # Leading idle clocks with WS at the state opposite to the first slot.
    idle = 4
    ws_line = [1] * idle + ws_line
    sd_line = [0] * idle + sd_line
    bclk: list[int] = []
    ws_out: list[int] = []
    sd_out: list[int] = []
    for ws, sd in zip(ws_line, sd_line):
        # Transmitter changes on the falling edge; the receiver samples on the rising one.
        bclk += [0] * half + [1] * half
        ws_out += [ws] * samples_per_bit
        sd_out += [sd] * samples_per_bit
    return _finish({"bclk": bclk, "ws": ws_out, "sd": sd_out}, bit_clock, samples_per_bit, length)


def _stuff(bits: list[int]) -> list[int]:
    output: list[int] = []
    run_level, run = None, 0
    for bit in bits:
        output.append(bit)
        if bit == run_level:
            run += 1
        else:
            run_level, run = bit, 1
        if run == 5:
            stuff = 1 - bit
            output.append(stuff)
            run_level, run = stuff, 1
    return output


def can_frame_bits(identifier: int, data: bytes, extended: bool = False, remote: bool = False,
                   ack: bool = True) -> list[int]:
    """All bits of one frame on the wire (dominant = 0), SOF to the end of EOF."""
    bits = [0]
    if extended:
        high = (identifier >> 18) & 0x7FF
        bits += [(high >> index) & 1 for index in range(10, -1, -1)]
        bits += [1, 1]  # SRR, IDE
        bits += [(identifier >> index) & 1 for index in range(17, -1, -1)]
        bits += [int(remote), 0, 0]  # RTR, r1, r0
    else:
        bits += [(identifier >> index) & 1 for index in range(10, -1, -1)]
        bits += [int(remote), 0, 0]  # RTR, IDE, r0
    dlc = len(data)
    bits += [(dlc >> index) & 1 for index in range(3, -1, -1)]
    if not remote:
        for byte in data:
            bits += [(byte >> index) & 1 for index in range(7, -1, -1)]
    crc = crc15(bits)
    bits += [(crc >> index) & 1 for index in range(14, -1, -1)]
    return _stuff(bits) + [1, 0 if ack else 1, 1] + [1] * 7


def can(frames: list[tuple[int, bytes, bool]], bitrate: float, samples_per_bit: int = 16,
        length: int = ARB_POINTS) -> TestSignal:
    """frames: (identifier, data, extended). Line is dominant-low; intermission between frames."""
    levels: list[int] = [1] * (samples_per_bit * 12)
    for identifier, data, extended in frames:
        for bit in can_frame_bits(identifier, data, extended):
            levels += [bit] * samples_per_bit
        levels += [1] * (samples_per_bit * 3)
    levels += [1] * (samples_per_bit * 12)
    return _finish({"can": levels}, bitrate, samples_per_bit, length)


def _dynamic_stuff(bits: list[int]) -> tuple[list[int], int]:
    """Stuff the part of an FD frame ahead of the CRC field and count the stuff bits.

    A stuff bit due right after the last bit is left out: the fixed stuff bit that
    opens the CRC field takes its place (no two consecutive stuff bits are allowed).
    """
    output: list[int] = []
    run_level, run, count = None, 0, 0
    for position, bit in enumerate(bits):
        output.append(bit)
        if bit == run_level:
            run += 1
        else:
            run_level, run = bit, 1
        if run == 5 and position < len(bits) - 1:
            stuff = 1 - bit
            output.append(stuff)
            count += 1
            run_level, run = stuff, 1
    return output, count


def can_fd_frame_bits(identifier: int, data: bytes, extended: bool = False, brs: bool = True, esi: bool = False,
                      ack: bool = True) -> list[tuple[int, str]]:
    """All bits of one CAN FD frame as (level, phase).

    Phase: "n" nominal rate, "b" the BRS bit (switches to the data rate at its sample
    point), "d" data rate, "c" the CRC delimiter (switches back at its sample point).
    """
    if len(data) not in FD_DLC_LENGTHS:
        raise ValueError(tr("Ramka CAN FD musi mieć jedną z długości: {lengths} bajtów.",
                            lengths=", ".join(str(item) for item in FD_DLC_LENGTHS)))
    bits = [0]
    if extended:
        bits += [(identifier >> index) & 1 for index in range(28, 17, -1)]
        bits += [1, 1]  # SRR, IDE
        bits += [(identifier >> index) & 1 for index in range(17, -1, -1)]
        bits += [0]  # RRS
    else:
        bits += [(identifier >> index) & 1 for index in range(10, -1, -1)]
        bits += [0, 0]  # RRS, IDE
    bits += [1, 0]  # FDF, res
    brs_position = len(bits)
    bits += [int(brs), int(esi)]
    dlc = FD_DLC_LENGTHS.index(len(data))
    bits += [(dlc >> index) & 1 for index in range(3, -1, -1)]
    for byte in data:
        bits += [(byte >> index) & 1 for index in range(7, -1, -1)]

    # Stuffing the frame up to BRS alone tells where BRS lands among the stuffed bits.
    head, _ = _dynamic_stuff(bits[:brs_position + 1])
    head_length = len(head)
    stuffed, count = _dynamic_stuff(bits)
    count_bits = stuff_count_bits(count)
    crc = crc_fd(stuffed + count_bits, len(data))
    crc_size = 17 if len(data) <= FD_CRC_SWITCH_LENGTH else 21
    field = count_bits + [(crc >> index) & 1 for index in range(crc_size - 1, -1, -1)]
    tail: list[int] = []
    previous = stuffed[-1]
    for index, bit in enumerate(field):
        if index % FIXED_STUFF_PERIOD == 0:
            previous = 1 - previous
            tail.append(previous)
        tail.append(bit)
        previous = bit
    data_phase = "d" if brs else "n"
    result = [(bit, "n") for bit in stuffed[:head_length - 1]]
    result.append((stuffed[head_length - 1], "b" if brs else "n"))
    result += [(bit, data_phase) for bit in stuffed[head_length:]]
    result += [(bit, data_phase) for bit in tail]
    result.append((1, "c" if brs else "n"))
    result += [(0 if ack else 1, "n"), (1, "n")]
    result += [(1, "n")] * 7
    return result


def can_fd(frames: list[tuple[int, bytes, bool]], bitrate: float, data_bitrate: float, samples_per_data_bit: int = 8,
           brs: bool = True, length: int = ARB_POINTS, sample_point: float = 0.75) -> TestSignal:
    """frames: (identifier, data, extended). Without BRS the whole frame runs at bitrate."""
    data_rate = data_bitrate if brs else bitrate
    sample_rate = data_rate * samples_per_data_bit
    nominal = sample_rate / bitrate
    if nominal < 1:
        raise ValueError(tr("Prędkość danych CAN FD nie może być mniejsza od prędkości nominalnej."))
    nominal_samples = round(nominal)
    widths = {
        "n": nominal_samples,
        "d": samples_per_data_bit,
        "b": round(nominal * sample_point) + round(samples_per_data_bit * (1 - sample_point)),
        "c": round(samples_per_data_bit * sample_point) + round(nominal * (1 - sample_point)),
    }
    levels: list[int] = [1] * (nominal_samples * 12)
    for identifier, data, extended in frames:
        for bit, phase in can_fd_frame_bits(identifier, data, extended, brs):
            levels += [bit] * widths[phase]
        levels += [1] * (nominal_samples * 3)
    levels += [1] * (nominal_samples * 12)
    return _finish({"can": levels}, data_rate, samples_per_data_bit, length)

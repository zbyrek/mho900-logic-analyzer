"""Bus signals for external devices: building them, playing them on the AFG outputs and
comparing what comes back with what was expected.

The AFG plays one record of ARB_POINTS samples in a loop: the programming guide has no
burst mode for it (:SOURce<n>:MOD offers AM, FM and PM), so a message repeats for as
long as the output stays on.
"""
from __future__ import annotations

import math
import re
from dataclasses import dataclass, field

import numpy as np

from . import siggen
from .decoders.base import ParamSpec
from .i18n import tr
from .scope import Scope, record_file

OUTPUTS = (1, 2)
CLASSIC_CAN_FRAME_BYTES = 8
FD_CAN_FRAME_BYTES = 64
SAMPLES_PER_BIT_CHOICES = (16, 8, 4)
FD_SAMPLES_PER_DATA_BIT_CHOICES = (8, 4)
# The fastest record rate confirmed on the scope (SPI 5 MHz at 16 samples per bit).
MAX_SAMPLE_RATE = 80e6
VALUE_KINDS = {"DATA", "WORD", "TX", "RX", "L", "R"}
RECORD_PERIODS_IN_CAPTURE = 2
CAPTURE_DIVISIONS = 10
MIN_TIME_SCALE = 5e-10
MAX_TIME_SCALE = 500.0
LOGIC_LEVELS = (0.0, 3.3)
CAN_HIGH_LEVELS = (2.5, 3.5)
CAN_LOW_LEVELS = (1.5, 2.5)
LOADS = (("High impedance (High-Z)", "OMEG"), ("50 Ω load", "FIFTy"))

_ID_PARAM = ParamSpec("identifier", "Identifier (hex)", 0x123, maximum=0x1FFFFFFF, display_hex=True)
_EXTENDED_PARAM = ParamSpec("extended", "Identifier format", False,
                            choices=((False, "Standard (11 bits)"), (True, "Extended (29 bits)")))
_DIFFERENTIAL_PARAM = ParamSpec("differential", "Signal", False,
                                choices=((False, "One line (dominant low)"), (True, "CAN_H and CAN_L pair")))
PROTOCOL_PARAMS: dict[str, tuple[ParamSpec, ...]] = {
    "UART": (
        ParamSpec("rate", "Bit rate [bit/s]", 115200, minimum=100, maximum=2e7),
        ParamSpec("data_bits", "Data bits", 8, choices=((5, "5"), (6, "6"), (7, "7"), (8, "8"))),
        ParamSpec("parity", "Parity", "none",
                  choices=(("none", "None"), ("even", "Even"), ("odd", "Odd"))),
        ParamSpec("stop_bits", "Stop bits", 1.0, choices=((1.0, "1"), (1.5, "1.5"), (2.0, "2"))),
    ),
    "I2C": (
        ParamSpec("rate", "SCL clock [Hz]", 400000, minimum=100, maximum=2e7),
        ParamSpec("address", "7-bit address (hex)", 0x50, maximum=0x7F, display_hex=True),
        ParamSpec("read", "Direction", False, choices=((False, "Write"), (True, "Read"))),
    ),
    "SPI": (
        ParamSpec("rate", "CLK clock [Hz]", 1000000, minimum=100, maximum=2e7),
        ParamSpec("mode", "Mode (CPOL/CPHA)", 0, choices=(
            (0, "0 (CPOL=0, CPHA=0)"), (1, "1 (CPOL=0, CPHA=1)"),
            (2, "2 (CPOL=1, CPHA=0)"), (3, "3 (CPOL=1, CPHA=1)"))),
    ),
    "CAN": (
        ParamSpec("rate", "Bit rate [bit/s]", 500000, minimum=100, maximum=2e7),
        _ID_PARAM, _EXTENDED_PARAM, _DIFFERENTIAL_PARAM,
    ),
    "CAN FD": (
        ParamSpec("rate", "Nominal rate [bit/s]", 500000, minimum=100, maximum=2e7),
        ParamSpec("data_rate", "Data rate [bit/s]", 2000000, minimum=100, maximum=2e7),
        ParamSpec("brs", "Bit rate switch (BRS)", True, choices=((True, "Yes"), (False, "No"))),
        _ID_PARAM, _EXTENDED_PARAM, _DIFFERENTIAL_PARAM,
    ),
}
PROTOCOLS = tuple(PROTOCOL_PARAMS)
ESCAPE = re.compile(r"\\(x[0-9a-fA-F]{2}|.)", re.DOTALL)
SIMPLE_ESCAPES = {"n": "\n", "r": "\r", "t": "\t", "0": "\0", "\\": "\\"}


def lines_of(protocol: str, params: dict) -> tuple[tuple[str, str], ...]:
    """Signal lines of a protocol as (key, label)."""
    if protocol == "UART":
        return (("tx", "TX"),)
    if protocol == "I2C":
        return (("scl", "SCL"), ("sda", "SDA"))
    if protocol == "SPI":
        return (("clk", "CLK"), ("mosi", "MOSI"))
    if params.get("differential"):
        return (("can", "CAN_H"), ("can_l", "CAN_L"))
    return (("can", "CAN"),)


def default_levels(key: str, params: dict) -> tuple[float, float]:
    if params.get("differential"):
        return CAN_LOW_LEVELS if key == "can_l" else CAN_HIGH_LEVELS
    return LOGIC_LEVELS


def parse_data(text: str, data_format: str) -> bytes:
    """Bytes from the data field. ASCII understands \\n \\r \\t \\0 \\\\ and \\xNN, in quotes or without them."""
    if data_format == "hex":
        cleaned = re.sub(r"0[xX]", "", text).replace(",", " ")
        try:
            data = bytes.fromhex(cleaned)
        except ValueError:
            raise ValueError(tr("Invalid hex data: {text}", text=text)) from None
    else:
        def replace(match):
            code = match.group(1)
            if code[0] == "x" and len(code) == 3:
                return chr(int(code[1:], 16))
            if code in SIMPLE_ESCAPES:
                return SIMPLE_ESCAPES[code]
            raise ValueError(tr("Unknown sequence \\{code} in the ASCII data.", code=code))

        if len(text) >= 2 and text[0] == text[-1] == '"':
            text = text[1:-1]
        converted = ESCAPE.sub(replace, text)
        try:
            data = converted.encode("latin-1")
        except UnicodeEncodeError:
            raise ValueError(tr("The ASCII data contain a character out of range: {text}", text=text)) from None
        if any(ord(char) > 127 for char in text):
            raise ValueError(tr("The ASCII data contain a character out of range: {text}", text=text))
    if not data:
        raise ValueError(tr("Enter the data to send."))
    return data


def format_data(data: bytes, data_format: str) -> str:
    """Inverse of parse_data."""
    if data_format == "hex":
        return " ".join(f"0x{byte:02X}" for byte in data)
    named = {10: "\\n", 13: "\\r", 9: "\\t", 92: "\\\\"}
    text = "".join(named.get(byte, chr(byte) if 32 <= byte < 127 else f"\\x{byte:02X}") for byte in data)
    return f'"{text}"'


def time_scale_125(seconds_per_div: float) -> float:
    """Smallest 1-2-5 scale at least this long, within the range of the instrument."""
    wanted = min(max(seconds_per_div, MIN_TIME_SCALE), MAX_TIME_SCALE)
    decade = math.floor(math.log10(wanted))
    for step in (1, 2, 5, 10):
        scale = step * 10.0 ** decade
        if scale >= wanted * (1 - 1e-9):
            return scale
    return MAX_TIME_SCALE


def time_scale_choices() -> list[float]:
    """Every 1-2-5 scale of the instrument (500 ps/div to 500 s/div, read back from it)."""
    scales = [5e-10]
    for exponent in range(-9, 3):
        scales += [step * 10.0 ** exponent for step in (1, 2, 5)]
    return [round(scale, 12) for scale in scales]


def format_time_scale(seconds: float) -> str:
    for limit, unit in ((1e-9, "ps"), (1e-6, "ns"), (1e-3, "µs"), (1.0, "ms")):
        if seconds < limit:
            return f"{seconds / limit * 1000:.0f} {unit}/div"
    return f"{seconds:.0f} s/div"


@dataclass
class Generated:
    protocol: str
    data: bytes
    lines: dict[str, np.ndarray]
    labels: dict[str, str]
    sample_rate: float
    afg_frequency: float
    period: float
    decoder: str
    decoder_params: dict
    signals: dict[str, str] = field(default_factory=dict)  # signal of the decoder -> line key
    outputs: dict[int, str] = field(default_factory=dict)  # AFG output -> line key
    levels: dict[int, tuple[float, float]] = field(default_factory=dict)
    impedance: str = "OMEG"

    @property
    def time_scale(self) -> float:
        return time_scale_125(self.period * RECORD_PERIODS_IN_CAPTURE / CAPTURE_DIVISIONS)


def _fitting(build, choices, rate):
    """The signal with the most samples per bit that fits the record and the sample-rate limit."""
    failure = None
    for samples in choices:
        if rate * samples > MAX_SAMPLE_RATE:
            failure = ValueError(tr("{rate:.0f} bit/s is beyond the generator's limit.", rate=rate))
            continue
        try:
            return build(samples)
        except ValueError as exc:
            failure = exc
    raise failure


def _frames(data: bytes, limit: int, identifier: int, extended: bool) -> list[tuple[int, bytes, bool]]:
    return [(identifier, data[start:start + limit], extended) for start in range(0, len(data), limit)]


def build(protocol: str, data: bytes, params: dict) -> Generated:
    rate = params["rate"]
    if protocol == "UART":
        signal = _fitting(lambda samples: siggen.uart(
            data, rate, samples, params["data_bits"], params["parity"], params["stop_bits"]), SAMPLES_PER_BIT_CHOICES, rate)
        lines = {"tx": signal.lines["tx"]}
        decoder = "UART"
        decoder_params = {"baudrate": rate, "data_bits": params["data_bits"], "parity": params["parity"],
                          "stop_bits": float(params["stop_bits"])}
        signals = {"tx": "tx"}
        mask = (1 << params["data_bits"]) - 1
        if any(byte > mask for byte in data):
            raise ValueError(tr("The data do not fit in {bits} bits per character.", bits=params["data_bits"]))
    elif protocol == "I2C":
        signal = _fitting(lambda samples: siggen.i2c([(params["address"], params["read"], data)], rate, samples),
                          SAMPLES_PER_BIT_CHOICES, rate)
        lines = {"scl": signal.lines["scl"], "sda": signal.lines["sda"]}
        decoder, decoder_params = "I²C", {}
        signals = {"scl": "scl", "sda": "sda"}
    elif protocol == "SPI":
        signal = _fitting(lambda samples: siggen.spi([list(data)], rate, samples, params["mode"]),
                          SAMPLES_PER_BIT_CHOICES, rate)
        lines = {"clk": signal.lines["clk"], "mosi": signal.lines["mosi"]}
        decoder, decoder_params = "SPI", {"cpol": params["mode"] >> 1, "cpha": params["mode"] & 1}
        signals = {"clk": "clk", "mosi": "mosi"}
    elif protocol == "CAN":
        frames = _frames(data, CLASSIC_CAN_FRAME_BYTES, params["identifier"], params["extended"])
        signal = _fitting(lambda samples: siggen.can(frames, rate, samples), SAMPLES_PER_BIT_CHOICES, rate)
        lines = _can_lines(signal.lines["can"], params)
        decoder, decoder_params = "CAN", {"nominal_bitrate": rate}
        signals = _can_signals(params)
    elif protocol == "CAN FD":
        brs = params["brs"]
        data_rate = params["data_rate"] if brs else rate
        if data_rate < rate:
            raise ValueError(tr("The CAN FD data rate cannot be below the nominal rate."))
        frames = _frames(data, FD_CAN_FRAME_BYTES, params["identifier"], params["extended"])
        if len(frames[-1][1]) not in siggen.FD_DLC_LENGTHS:
            raise ValueError(tr("A CAN FD frame must have one of these lengths: {lengths} bytes.",
                                lengths=", ".join(str(item) for item in siggen.FD_DLC_LENGTHS)))
        signal = _fitting(lambda samples: siggen.can_fd(frames, rate, data_rate, samples, brs),
                          FD_SAMPLES_PER_DATA_BIT_CHOICES, data_rate)
        lines = _can_lines(signal.lines["can"], params)
        decoder = "CAN"
        decoder_params = {"nominal_bitrate": rate, "fast_bitrate": data_rate}
        signals = _can_signals(params)
    else:
        raise ValueError(tr("The generator does not support {protocol}", protocol=protocol))
    length = len(next(iter(lines.values())))
    return Generated(protocol, data, lines, dict(lines_of(protocol, params)), signal.sample_rate,
                     signal.afg_frequency, length / signal.sample_rate, decoder, decoder_params, signals)


def _can_signals(params: dict) -> dict[str, str]:
    """The decoder reads one line where dominant is low, which of a differential pair is CAN_L."""
    return {"can_rx": "can_l" if params.get("differential") else "can"}


def _can_lines(line: np.ndarray, params: dict) -> dict[str, np.ndarray]:
    if params.get("differential"):
        return {"can": 1 - line, "can_l": line}
    return {"can": line}


def assign(generated: Generated, outputs: dict[str, int | None], levels: dict[int, tuple[float, float]],
           impedance: str = "OMEG") -> None:
    """Attach the line -> AFG output choice and the voltage levels of each output."""
    used = {}
    for key, afg in outputs.items():
        if afg is None:
            continue
        if afg in used:
            raise ValueError(tr("Output AFG{afg} is assigned to two lines.", afg=afg))
        used[afg] = key
    if not used:
        raise ValueError(tr("Assign at least one line to an AFG output."))
    for afg in used:
        low, high = levels[afg]
        if low >= high:
            raise ValueError(tr("AFG{afg}: the low level must be below the high level.", afg=afg))
    generated.outputs = used
    generated.levels = {afg: levels[afg] for afg in used}
    generated.impedance = impedance


def play(scope: Scope, generated: Generated) -> None:
    """Put the record on the assigned outputs and leave them playing; other outputs go off."""
    scope.errors()
    for afg in OUTPUTS:
        scope.afg_enable(afg, False)
    for afg, key in sorted(generated.outputs.items()):
        low, high = generated.levels[afg]
        scope.afg_play(afg, f"mhodecode_afg{afg}.csv", generated.lines[key], generated.afg_frequency, low, high,
                       generated.impedance)
    if len(generated.outputs) == 2:
        scope.afg_align()
    problems = scope.errors()
    if problems:
        for afg in OUTPUTS:
            scope.afg_enable(afg, False)
        raise ValueError(tr("The generator rejected the settings, outputs switched off: {errors}", errors="; ".join(problems)))


def file_stem(protocol: str) -> str:
    return protocol.lower().replace(" ", "_")


def file_names(generated: Generated, stem: str) -> dict[int, str]:
    """AFG output -> the name of the file with its record."""
    return {afg: f"{stem}_{generated.labels[key].lower()}.csv" for afg, key in sorted(generated.outputs.items())}


def record_files(generated: Generated, stem: str) -> dict[str, bytes]:
    """File name -> content, one file per line in use. Values 0 and 1 become the low and high level through the output's amplitude and offset."""
    names = file_names(generated, stem)
    return {names[afg]: record_file(generated.lines[key]) for afg, key in generated.outputs.items()}


def setup_text(generated: Generated, stem: str) -> str:
    """How to set the generator to play the files, in the order the program itself sets it in afg_play."""
    names = file_names(generated, stem)
    load = tr(next(text for text, code in LOADS if code == generated.impedance))
    blocks = []
    for afg, key in sorted(generated.outputs.items()):
        low, high = generated.levels[afg]
        blocks.append("\n".join((
            tr("AFG{afg}: line {line}", afg=afg, line=generated.labels[key]),
            tr("  Output load: {load}", load=load),
            tr("  Waveform: Arb, file {name}", name=names[afg]),
            tr("  Frequency: {frequency:.6f} Hz", frequency=generated.afg_frequency),
            tr("  Amplitude: {amplitude:g} Vpp", amplitude=high - low),
            tr("  Offset: {offset:g} V", offset=(high + low) / 2))))
    if len(generated.outputs) == 2:
        blocks.append(tr("Run Align Phase once both outputs are set, so that the two lines start together."))
    blocks.append(tr("Set the frequency, amplitude and offset after loading the file. The record has {samples} samples at {rate:.6g} MSa/s and repeats every {period:.6g} ms while the output is on.",
                     samples=len(next(iter(generated.lines.values()))), rate=generated.sample_rate / 1e6, period=generated.period * 1e3))
    blocks.append(tr("Put the files on a USB stick (D:/ on the scope) or in the scope's C:/arb folder (the arb folder over FTP)."))
    return "\n\n".join(blocks)


@dataclass
class Comparison:
    matched: bool
    expected: int
    received: int
    occurrences: int = 0  # complete, error-free copies of the expected data
    best_count: int = 0  # longest run of equal values from the start of the expected data
    position: int | None = None  # index of the first difference within the expected data
    got: int | None = None
    want: int | None = None
    row: str = ""


def values_by_row(annotations) -> dict[str, list]:
    rows: dict[str, list] = {}
    for item in annotations:
        if item.kind in VALUE_KINDS and item.value is not None:
            rows.setdefault(item.row, []).append(item)
    return rows


def compare(annotations, expected: bytes) -> Comparison:
    """Look for the expected bytes in each decoded line. The AFG repeats its record and a
    device may repeat a reply, so a match is a complete, error-free copy anywhere in the capture."""
    wanted = list(expected)
    rows = values_by_row(annotations)
    best = Comparison(False, len(wanted), 0)
    for row, items in rows.items():
        values = [item.value for item in items]
        best.received = max(best.received, len(values))
        occurrences = 0
        for start in range(len(values)):
            count = 0
            while (count < len(wanted) and start + count < len(values) and values[start + count] == wanted[count]
                   and not items[start + count].error):
                count += 1
            if count == len(wanted):
                occurrences += 1
            if count > best.best_count or (best.position is None and count == best.best_count):
                best.best_count = count
                best.row = row
                best.position = count
                best.got = values[start + count] if start + count < len(values) else None
                best.want = wanted[count] if count < len(wanted) else None
        if occurrences > best.occurrences:
            best.occurrences = occurrences
            best.row = row
    best.matched = best.occurrences > 0
    if best.matched:
        best.position = best.got = best.want = None
    return best

"""Common decoder interface. The GUI builds its forms from these descriptions."""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field

import numpy as np

from ..i18n import tr
from ..signal import DigitalTrace, Thresholds, digitize

# A bit needs this many samples to be located with any reliability.
MIN_SAMPLES_PER_BIT = 4


@dataclass
class Annotation:
    start: float
    end: float
    row: str  # signal key the annotation is drawn on
    kind: str  # e.g. DATA, START, ADDR
    text: str  # short text drawn on the plot
    value: int | None = None
    error: str = ""
    detail: bool = False  # True: frame sub-field, shown on the plot but not in the table
    hidden: bool = False  # True: only a value for the comparison, neither plotted nor listed
    texts: tuple = ()  # the same text at decreasing lengths, for the space on the plot
    control: bool = False  # True: a field of the protocol around the data, drawn apart from it


@dataclass
class DecodeResult:
    annotations: list[Annotation] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    rows: dict[str, str] = field(default_factory=dict)  # lanes of their own: row key -> label


@dataclass(frozen=True)
class SignalSpec:
    key: str
    label: str
    required: bool = True


@dataclass(frozen=True)
class ParamSpec:
    key: str
    label: str
    default: object
    choices: tuple | None = None  # (value, label) pairs
    minimum: float = 0
    maximum: float = 1e9
    hint: str = ""
    display_hex: bool = False
    decimals: int = 2
    rates: tuple = ()  # typical values to pick from, with any other one still possible


class Decoder(ABC):
    name = ""
    signals: tuple[SignalSpec, ...] = ()
    params: tuple[ParamSpec, ...] = ()

    def defaults(self) -> dict:
        return {param.key: param.default for param in self.params}

    def prepare(self, waves: dict, thresholds: dict[str, Thresholds | None], /, **params) -> dict[str, DigitalTrace]:
        """Digitize the assigned channels. waves maps signal key to a scope.Waveform."""
        check_lengths(waves)
        return {
            key: digitize(wave.volts, wave.dt, wave.t0, thresholds.get(key), wave.min_swing)
            for key, wave in waves.items()
        }

    @abstractmethod
    def decode(self, traces: dict[str, DigitalTrace], **params) -> DecodeResult:
        """Turn digital traces into annotations."""


def check_lengths(waves: dict) -> None:
    lengths = {len(wave.volts) for wave in waves.values()}
    if len(lengths) > 1:
        raise ValueError(tr("Kanały mają różną długość: {lengths}", lengths=sorted(lengths)))


def bits_to_int(bits, msb_first: bool = True) -> int:
    value = 0
    for bit in (bits if msb_first else reversed(bits)):
        value = (value << 1) | int(bit)
    return value


def width(bits: int) -> int:
    """Hex digits needed for a value of the given bit width."""
    return (bits + 3) // 4


def sampling_note(samples_per_bit: float, label: str) -> list[str]:
    if samples_per_bit < MIN_SAMPLES_PER_BIT:
        return [tr("Za mało próbek na bit ({label}): {samples:.1f} (minimum {minimum}). "
                   "Zwiększ częstotliwość próbkowania.",
                   label=label, samples=samples_per_bit, minimum=MIN_SAMPLES_PER_BIT)]
    return []


def median_period(edge_indices: np.ndarray) -> float:
    return float(np.median(np.diff(edge_indices))) if len(edge_indices) >= 2 else 0.0

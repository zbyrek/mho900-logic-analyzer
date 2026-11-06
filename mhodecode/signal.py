"""Conversion of analog samples to digital traces held as lists of edges."""
from __future__ import annotations

from bisect import bisect_left, bisect_right
from dataclasses import dataclass, field

import numpy as np

# Width of the Schmitt trigger band around the threshold, as a fraction of the
# signal swing. Wide enough to reject the ringing seen on AFG-driven edges.
HYSTERESIS_FRACTION = 0.3
# Swings below this are treated as a constant level (noise on an idle input).
MIN_SWING_V = 0.02


@dataclass
class DigitalTrace:
    dt: float
    t0: float
    length: int
    initial: int
    edges: list[int] = field(default_factory=list)  # index where each new level starts

    def level(self, index: float) -> int:
        return self.initial ^ (bisect_right(self.edges, index) & 1)

    def levels(self, indices: np.ndarray) -> np.ndarray:
        counts = np.searchsorted(np.asarray(self.edges, dtype=np.int64), indices, side="right")
        return (self.initial ^ (counts & 1)).astype(np.int8)

    def level_after_edge(self, position: int) -> int:
        return self.initial ^ ((position + 1) & 1)

    def time(self, index: float) -> float:
        return self.t0 + index * self.dt

    def inverted(self) -> "DigitalTrace":
        return DigitalTrace(self.dt, self.t0, self.length, self.initial ^ 1, self.edges)

    def next_edge(self, index: float, level: int | None = None) -> int | None:
        """First edge at or after index, optionally only edges going to level."""
        position = bisect_left(self.edges, index)
        if level is not None and position < len(self.edges):
            if self.level_after_edge(position) != level:
                position += 1
        return self.edges[position] if position < len(self.edges) else None


@dataclass(frozen=True)
class Thresholds:
    low: float
    high: float


def auto_thresholds(volts: np.ndarray, min_swing: float = MIN_SWING_V) -> Thresholds | None:
    """Thresholds from the signal swing, or None if the input does not toggle."""
    stride = max(1, len(volts) // 2_000_000)
    bottom, top = np.percentile(volts[::stride], [0.5, 99.5])
    swing = top - bottom
    if swing < min_swing:
        return None
    middle = (bottom + top) / 2
    return Thresholds(middle - swing * HYSTERESIS_FRACTION / 2, middle + swing * HYSTERESIS_FRACTION / 2)


def manual_thresholds(level: float, volts: np.ndarray) -> Thresholds:
    """Thresholds around a user level, band scaled to the measured swing."""
    swing = float(np.percentile(volts, 99.5) - np.percentile(volts, 0.5))
    half = max(swing, MIN_SWING_V) * HYSTERESIS_FRACTION / 2
    return Thresholds(level - half, level + half)


def digitize(volts: np.ndarray, dt: float, t0: float, thresholds: Thresholds | None = None,
             min_swing: float = MIN_SWING_V) -> DigitalTrace:
    """Schmitt-trigger the samples. Each edge is placed mid-way through the
    transition (between the last sample on the old side and the first on the new)."""
    if thresholds is None:
        thresholds = auto_thresholds(volts, min_swing)
    if thresholds is None:
        level = int(np.mean(volts) >= min_swing)
        return DigitalTrace(dt, t0, len(volts), level, [])
    above = volts >= thresholds.high
    definite = np.flatnonzero(above | (volts <= thresholds.low))
    if definite.size == 0:
        return DigitalTrace(dt, t0, len(volts), 0, [])
    states = above[definite]
    changes = np.flatnonzero(states[1:] != states[:-1])
    edges = (definite[changes] + definite[changes + 1] + 1) // 2
    return DigitalTrace(dt, t0, len(volts), int(states[0]), edges.tolist())


def from_levels(levels, dt: float = 1.0, t0: float = 0.0) -> DigitalTrace:
    array = np.asarray(levels, dtype=np.int8)
    edges = np.flatnonzero(array[1:] != array[:-1]) + 1
    return DigitalTrace(dt, t0, len(array), int(array[0]), edges.tolist())


def estimate_bit_time(traces: list[DigitalTrace]) -> float | None:
    """Bit time in seconds from the shortest cluster of pulse widths.

    Refined by treating every pulse as a whole number of bits, so long captures
    give a much better figure than the shortest pulse alone.
    """
    widths = []
    for trace in traces:
        if len(trace.edges) >= 2:
            widths.append(np.diff(np.asarray(trace.edges, dtype=np.int64)) * trace.dt)
    if not widths:
        return None
    all_widths = np.concatenate(widths)
    shortest = np.median(all_widths[all_widths <= all_widths.min() * 1.5])
    # Protocol pulses are short multiples of a bit; long gaps are idle time.
    usable = all_widths[all_widths <= shortest * 12]
    counts = np.maximum(np.round(usable / shortest), 1)
    return float(usable.sum() / counts.sum())


def snap_to_standard(rate: float, standard: tuple[int, ...], tolerance: float = 0.03) -> float:
    nearest = min(standard, key=lambda value: abs(value - rate))
    return float(nearest) if abs(nearest - rate) <= nearest * tolerance else rate

"""After the scope's Auto: a time scale at which one whole packet of the signal fits the screen."""
from __future__ import annotations

from dataclasses import dataclass

from .generator import time_scale_125
from .scope import HOLDOFF_MAX_S, Scope
from .signal import PacketSearch, auto_thresholds, digitize, longest_packet

SCREEN_DIVISIONS = 10
# Auto leaves the trigger at the centre of the screen, so a packet that the trigger starts has half a screen.
PACKET_SHARE_OF_SCREEN = 0.5
# The holdoff ends after the packet and before the next one can begin, which is at least a pause after this one's end.
HOLDOFF_SHARE_OF_PAUSE = 0.5
FIT_POINTS = 1_000_000  # about 0.55 s per channel over SCPI, and more than Auto's own depth (measured 500 k to 1 M)
FIT_FORCE_AFTER_S = 0.5  # a signal that does not trigger still gives a record to look for a packet in
WIDEN_FACTOR = 10
MAX_FIT_TIME_SCALE = 0.1  # seconds per division; past this a search on a signal with no packets only takes long


@dataclass(frozen=True)
class PacketFit:
    time_scale: float
    packet: float | None = None  # seconds; None: no whole packet found, the time scale is the one Auto set
    holdoff: float | None = None  # seconds; None: the one Auto set, which is none


def measure_packet(scope: Scope, channels: set[int]) -> tuple[PacketSearch, bool]:
    """One acquisition: the longest whole packet on these analog channels, and whether a longer record
    might show a longer one, since a packet was cut by the record's end or a channel had no edge in it. Auto leaves on
    only channels with a signal, so such a channel's packets lie outside this record."""
    scope.single(force_after=FIT_FORCE_AFTER_S)
    points = min(int(scope.query_float(":ACQuire:MDEPth?")), FIT_POINTS)  # a read window past the memory is refused
    traces = []
    for channel in sorted(channels):
        wave = scope.fetch(channel, points)
        wave.volts_per_div = scope.query_float(f":CHANnel{channel}:SCALe?")
        thresholds = auto_thresholds(wave.volts, wave.min_swing)
        if thresholds is not None:  # noise on a quiet channel would pass for very fast edges
            traces.append(digitize(wave.volts, wave.dt, wave.t0, thresholds))
    found = longest_packet(traces)
    return found, found.cut or any(not trace.edges for trace in traces) or len(traces) < len(channels)


def fit_time_to_packet(scope: Scope, channels: set[int]) -> PacketFit:
    """Widen the time scale until a record holds a whole packet of every channel, then fit the scale to the longest
    packet found, and the holdoff so that the scope does not trigger again inside a packet. A signal with no packets,
    such as a clock, gets the scale and the holdoff Auto set. Whatever happens the scope ends up running."""
    auto_scale = scope.query_float(":TIMebase:MAIN:SCALe?")
    fit = PacketFit(auto_scale)
    scale = auto_scale
    longest = 0.0
    try:
        while True:
            found, may_be_longer = measure_packet(scope, channels)
            if found.length is not None and found.length > longest:
                longest = found.length
                fit = PacketFit(time_scale_125(longest / (SCREEN_DIVISIONS * PACKET_SHARE_OF_SCREEN)), longest,
                                min(longest + found.pause * HOLDOFF_SHARE_OF_PAUSE, HOLDOFF_MAX_S))
            if (longest and not may_be_longer) or scale >= MAX_FIT_TIME_SCALE:
                return fit
            scale = time_scale_125(min(scale * WIDEN_FACTOR, MAX_FIT_TIME_SCALE))
            scope.set_time_scale(scale)
    finally:
        scope.set_time_scale(fit.time_scale)
        if fit.holdoff is not None:
            scope.set_holdoff(fit.holdoff)
        scope.run()

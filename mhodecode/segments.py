"""Captures kept one after another on one time axis, as Repeat collects them."""
from __future__ import annotations

import math
from dataclasses import replace

import numpy as np

from .decoders.base import DecodeResult
from .scope import Waveform

SAME_STEP_TOLERANCE = 1e-6
MAX_KEPT_BYTES = 2_000_000_000  # about 2 GB of samples: each further capture also redraws and decodes all of them


def _end_time(wave: Waveform) -> float:
    return wave.t0 + len(wave.volts) * wave.dt


def _analog(waves: dict) -> set:
    return {key for key, wave in waves.items() if not wave.digital}


def can_join(old: dict, new: dict) -> bool:
    """One time axis needs the same analog channels and the same step on every channel both captures have."""
    return _analog(old) == _analog(new) and all(math.isclose(old[key].dt, new[key].dt, rel_tol=SAME_STEP_TOLERANCE)
                                                for key in old.keys() & new.keys())


def kept_bytes(waves: dict) -> int:
    return sum(wave.volts.nbytes for wave in waves.values())


def _filled(count: int, value, like: np.ndarray) -> np.ndarray:
    return np.full(max(0, count), value, dtype=like.dtype)


def join_captures(old: dict, new: dict) -> tuple[dict, float, float]:
    """The new capture placed right after the old samples.

    Returns the joined waves, the time of the join and where the new capture's trigger went: time zero of a capture.

    A channel holds its level across what it lacks: a file lists only the digital channels that change,
    so one that is missing from a capture was flat in it.
    """
    start = max(map(_end_time, old.values()))
    shift = start - min(wave.t0 for wave in new.values())
    end = max(_end_time(wave) for wave in new.values()) + shift
    first = min(wave.t0 for wave in old.values())
    joined = {}
    for key in sorted(old.keys() | new.keys()):
        before, after = old.get(key), new.get(key)
        if after is None:
            count = round((end - before.t0) / before.dt) - len(before.volts)
            joined[key] = replace(before, volts=np.concatenate((before.volts, _filled(count, before.volts[-1], before.volts))))
        elif before is None:
            count = round((after.t0 + shift - first) / after.dt)
            joined[key] = replace(after, t0=first, volts=np.concatenate((_filled(count, after.volts[0], after.volts), after.volts)))
        else:
            count = round((after.t0 + shift - _end_time(before)) / before.dt)
            joined[key] = replace(before, clipped=max(before.clipped, after.clipped),
                                  volts_per_div=max(before.volts_per_div, after.volts_per_div),
                                  volts=np.concatenate((before.volts, _filled(count, before.volts[-1], before.volts), after.volts)))
    return joined, start, shift


def split_captures(waves: dict, starts: list[float]) -> list[dict]:
    """The captures of joined waves, each keeping its place in time. No starts: the waves as they are."""
    if not starts:
        return [waves]
    parts = []
    for begin, end in zip((None, *starts), (*starts, None)):
        part = {}
        for key, wave in waves.items():
            low = 0 if begin is None else round((begin - wave.t0) / wave.dt)
            high = len(wave.volts) if end is None else round((end - wave.t0) / wave.dt)
            part[key] = replace(wave, volts=wave.volts[low:high], t0=wave.t0 + low * wave.dt)
        parts.append(part)
    return parts


def merge_results(results: list[DecodeResult]) -> DecodeResult:
    merged = DecodeResult()
    for result in results:
        merged.annotations += result.annotations
        merged.rows.update(result.rows)
        merged.notes += [note for note in result.notes if note not in merged.notes]
    return merged

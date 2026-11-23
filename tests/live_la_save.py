"""Live check of the digital record in the scope's memory .bin file, for each number of analog channels on.

A report on this firmware (https://github.com/astoeckel/pymhoscope/blob/main/docs/mho900_la_save_bug/report.md)
says that with two or fewer analog channels on, the digital record of a saved .bin file repeats earlier
blocks past 500 000 samples (two channels) or 250 000 samples (none or one), while the screen is right.
The app reads the digital channels only through that file, so this checks it on the bench.

One AFG output plays a pseudo-random record slower than the capture, so no stretch of the signal repeats.
It must be wired in parallel to an analog channel and a digital channel. For each count of analog channels
the script saves the memory as the app does and:
- looks, block by block, for a stretch of the digital record found earlier in the record (a copy);
- when the analog channel is on, compares the digital channel block by block with the analog one read
  through :WAVeform:DATA?, after shifting by the delay measured at the record's start.

Usage: python tests/live_la_save.py <ip> <afg> <analog channel> <digital channel number> [depth]
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from mhodecode import rigol_files  # noqa: E402
from mhodecode.scope import ANALOG_CHANNELS, Scope, digital_channel, pod_members  # noqa: E402

RECORD = 16384
SEED = 1
HIGH = 3.3
LEVEL = HIGH / 2
TIME_PER_DIV = 1e-3  # 10 ms on screen
RECORD_PERIOD = 25e-3  # longer than the capture, so the signal does not repeat in it
BLOCK = 50_000  # digital samples per block in the report
SIGNATURE = 100_000  # digital samples from a block's start looked for elsewhere: at 1 GSa/s that is 65 levels of the record, so random levels do not recur by chance (20 000 did once)
SHIFT_SEARCH = 400  # digital samples searched each way for the delay
SHIFT_FIT = 200_000  # digital samples used to find the delay
MIN_EDGES = 1000  # the record has about 8000 level changes per period
BAD_BLOCK = 0.10 # share of differing samples from which a block is reported as wrong


def analog_sets(reference: int) -> list[list[int]]:
    others = [channel for channel in range(1, ANALOG_CHANNELS + 1) if channel != reference]
    return [[]] + [[reference] + others[:count - 1] for count in range(1, ANALOG_CHANNELS + 1)]


def copies(levels: np.ndarray) -> list[tuple[int, int]]:
    """(block start, where its first samples occur first) for blocks whose start occurs earlier."""
    data = levels.tobytes()
    found = []
    for start in range(BLOCK, len(levels) - SIGNATURE, BLOCK):
        first = data.find(data[start:start + SIGNATURE])
        if first != start:
            found.append((start, first))
    return found


def compare(digital, analog) -> tuple[int, list[tuple[int, float]]]:
    """(delay in digital samples, [(block start, share of differing samples)])."""
    times = digital.t0 + np.arange(len(digital.volts)) * digital.dt
    index = np.rint((times - analog.t0) / analog.dt).astype(np.int64)
    inside = (index >= 0) & (index < len(analog.volts))
    reference = np.zeros(len(index), np.uint8)
    reference[inside] = analog.volts[index[inside]] > LEVEL
    levels = digital.volts.astype(np.uint8)
    fit = slice(SHIFT_SEARCH, min(SHIFT_FIT, len(levels) - SHIFT_SEARCH))
    shifts = range(-SHIFT_SEARCH, SHIFT_SEARCH + 1)
    errors = [np.count_nonzero(levels[fit] != reference[fit.start - shift:fit.stop - shift]) for shift in shifts]
    shift = shifts[int(np.argmin(errors))]
    shifted = np.roll(reference, shift)
    blocks = []
    for start in range(0, len(levels), BLOCK):
        part = slice(max(start, SHIFT_SEARCH), min(start + BLOCK, len(levels) - SHIFT_SEARCH))
        valid = inside[part]
        if np.count_nonzero(valid) == 0:
            continue
        blocks.append((start, float(np.mean(levels[part][valid] != shifted[part][valid]))))
    return shift, blocks


def set_analog(scope: Scope, wanted: list[int]) -> None:
    for channel in wanted:  # on first, as the app does
        scope.set_channel(channel, True)
    for channel in range(1, ANALOG_CHANNELS + 1):
        if channel not in wanted:
            scope.set_channel(channel, False)


def main() -> int:
    if len(sys.argv) < 5:
        sys.exit("Usage: python tests/live_la_save.py <ip> <afg> <analog channel> <digital channel number> [depth]")
    afg, reference, number = int(sys.argv[2]), int(sys.argv[3]), int(sys.argv[4])
    depth = int(sys.argv[5]) if len(sys.argv) > 5 else 10_000_000
    scope = Scope(sys.argv[1], timeout=20)
    print(scope.connect())
    failures = 0
    try:
        values = np.random.default_rng(SEED).integers(0, 2, RECORD).astype(float)
        for other in (1, 2):
            scope.afg_enable(other, False)
        scope.afg_play(afg, "mhodecode_la_save.csv", values, 1 / RECORD_PERIOD, 0.0, HIGH)
        scope.set_channel(reference, True)
        scope.set_probe(reference, 1)
        scope.set_dc_coupling(reference)
        scope.set_vertical(reference, 0.5, -LEVEL)
        scope.enable_digital()
        pod = 1 if number in pod_members(1) else 2
        scope.write(f":LA:POD{pod}:DISPlay ON")
        scope.write(f":LA:POD{pod}:THReshold {LEVEL:g}")
        scope.set_edge_trigger(reference, LEVEL)
        scope.set_time_scale(TIME_PER_DIV)
        scope.query("*OPC?")
        print(f"setup errors: {scope.errors()}")
        for wanted in analog_sets(reference):
            set_analog(scope, wanted)
            scope.set_memory_depth(depth)
            scope.query("*OPC?")
            errors = scope.errors()
            scope.single(timeout=30)
            got_depth = scope.query_float(":ACQuire:MDEPth?")
            rate = scope.query_float(":ACQuire:SRATe?")
            memory = rigol_files.parse_bin(scope.save_memory(), [digital_channel(number)])
            digital = memory.waves[digital_channel(number)]
            print(f"\n=== analog channels on: {wanted or 'none'}; depth {got_depth:,.0f}, {rate / 1e6:g} MSa/s, "
                  f"digital samples {len(digital.volts):,}, analog per digital {memory.digital_factor}, errors {errors}")
            edges = int(np.count_nonzero(np.diff(digital.volts.astype(np.int8))))
            if edges < MIN_EDGES:
                # A flat line repeats itself everywhere, which proves nothing about the file.
                raise SystemExit(f"D{number} changes level {edges} times: check that AFG{afg} reaches it.")
            found = copies(digital.volts)
            print(f"copied blocks: {len(found)} of {len(digital.volts) // BLOCK}")
            for start, first in found[:6]:
                print(f"  block at {start:,} first occurs at {first:,}")
            bad = list(found)
            if reference in wanted:
                analog = scope.fetch(reference)
                shift, blocks = compare(digital, analog)
                wrong = [(start, share) for start, share in blocks if share > BAD_BLOCK]
                worst = max(share for _start, share in blocks)
                print(f"against CH{reference} by :WAVeform:DATA? ({len(analog.volts):,} points): delay {shift} digital samples, "
                      f"{len(wrong)} of {len(blocks)} blocks over {BAD_BLOCK:.0%} different, worst {worst:.1%}")
                for start, share in wrong[:6]:
                    print(f"  block at {start:,}: {share:.1%} different")
                if wrong:
                    print(f"  first wrong block starts at digital sample {wrong[0][0]:,}")
                bad += wrong
            print("RESULT:", "WRONG" if bad else "OK")
            failures += bool(bad)
    finally:
        scope.afg_enable(afg, False)
        scope.run()
        scope.close()
    print(f"\n{failures} of {len(analog_sets(reference))} channel counts gave a wrong digital record")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())

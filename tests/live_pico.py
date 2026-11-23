"""Live check of the digital channels and the decoders against a Raspberry Pi Pico protocol generator.

The Pico runs the firmware of the pico_generator project (a text command interface over USB that
plays 125 protocols with 5 ns resolution) and drives the scope's digital inputs, GPn on Dn. It is
plugged into another computer reached by ssh, where the project's tools/picogen.py drives it.
For each test case of that project (tests/cases.py) the script:
- simulates the case with the project's host build (in WSL) for its length, shortest pulse,
  initial levels and first edge;
- sets the case's pins to their initial levels on the Pico;
- sets the scope up through Scope.apply_plan as the app does: the case's digital channels only,
  an edge trigger on the first edge, a time base and memory depth for the case;
- starts the case on the Pico, saves the scope's memory as the app does and reads the digital
  channels from the .bin file;
- decodes them with the app's decoder engine, with the case's decoder stack and annotation filter
  (sigrok-cli -P and -A syntax), and compares the first text of each annotation with the case.

With --offline it skips the Pico and the scope and decodes the host build's own signal, which
checks this script and the decoder engine without the bench.

Usage: python tests/live_pico.py [--offline] [--boundary] [--push-pull] [--ssh user@host] [--scope host] [--project DIR] [--after NAME] [name-substring ...]
"""
from __future__ import annotations

import argparse
import math
import re
import subprocess
import tempfile
import sys
import time
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from mhodecode import rigol_files, sigrokdecode, srd_loader, trigger  # noqa: E402
from mhodecode.generator import time_scale_125  # noqa: E402
from mhodecode.scope import CapturePlan, Scope, digital_channel, pod_members  # noqa: E402
from mhodecode.signal import DigitalTrace, from_levels  # noqa: E402

DEFAULT_PROJECT = Path(r"D:\claude\pico_generator")
REMOTE_TOOL = "/tmp/live_pico_picogen.py"
THRESHOLD = 1.65  # the Pico drives 3.3 V
SPP = 50  # samples wanted in the shortest pulse, as in the project's own analyzer test
MARGIN = 1.25  # record length over the case length
PRE_SHARE = 0.05  # of the record, before the first edge on top of the case's own idle time
# Memory depths offered by the app, the largest first left out: saving takes about 7 s per million points.
DEPTHS = (1_000, 10_000, 100_000, 1_000_000, 10_000_000)
OFFLINE_RATE_LIMIT = 1e9  # samples per second at most when decoding the host build's signal

# Read on the remote computer from stdin: one "timeout<TAB>command" line per command, one reply line each.
SERVER = r"""
import sys, traceback
sys.path.insert(0, '/tmp')
import importlib.util
spec = importlib.util.spec_from_file_location('picogen', sys.argv[1])
picogen = importlib.util.module_from_spec(spec)
spec.loader.exec_module(picogen)
pico = picogen.Pico(picogen.find_port(), 60)
print('READY', flush=True)
for line in sys.stdin:
    timeout, command = line.rstrip('\n').split('\t', 1)
    try:
        print('R ' + ' | '.join(pico.cmd(command, timeout=float(timeout))), flush=True)
    except Exception as exc:
        print('E ' + repr(exc), flush=True)
"""


class RemotePico:
    """picogen.Pico on the computer the Pico is plugged into, over one ssh connection."""

    def __init__(self, target: str, tool: Path):
        copy = subprocess.run(["ssh", "-o", "BatchMode=yes", target, f"cat > {REMOTE_TOOL}"],
                              input=tool.read_bytes(), capture_output=True)
        if copy.returncode:
            raise SystemExit(f"copying picogen.py over ssh failed: {copy.stderr.decode(errors='replace').strip()}")
        self.process = subprocess.Popen(["ssh", "-o", "BatchMode=yes", target, "python3", "-u", "-c",
                                         _quote(SERVER), REMOTE_TOOL],
                                        stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True, encoding="utf-8",
                                        errors="replace")
        first = self.process.stdout.readline().strip()
        if first != "READY":
            raise SystemExit(f"the Pico server did not start: {first!r}")

    def cmd(self, command: str, timeout: float = 30.0) -> list[str]:
        self.process.stdin.write(f"{timeout:g}\t{command}\n")
        self.process.stdin.flush()
        reply = self.process.stdout.readline().rstrip("\n")
        if reply.startswith("R "):
            return reply[2:].split(" | ")
        raise RuntimeError(f"Pico: {reply or 'no reply'}")

    def close(self) -> None:
        self.process.stdin.close()
        self.process.wait(timeout=10)


def _quote(text: str) -> str:
    """One argument for the remote shell."""
    return "'" + text.replace("'", "'\"'\"'") + "'"


def wsl_path(path: Path) -> str:
    text = str(path).replace("\\", "/")
    return "/mnt/" + text[0].lower() + text[2:]


class Simulation:
    """The host build's signal of one case: edges per pin, in seconds."""

    def __init__(self, path: Path):
        timescale, ids = None, {}
        self.initial: dict[int, int] = {}
        self.edges: dict[int, list[float]] = {}
        self.end = 0.0
        now = None
        with open(path) as file:
            for line in file:
                line = line.strip()
                match = re.match(r"\$timescale\s+(\d+)\s*(s|ms|us|ns|ps)", line)
                if match:
                    timescale = int(match.group(1)) * {"s": 1, "ms": 1e-3, "us": 1e-6, "ns": 1e-9, "ps": 1e-12}[match.group(2)]
                    continue
                match = re.match(r"\$var\s+\S+\s+\d+\s+(\S+)\s+gp(\d+)", line)
                if match:
                    ids[match.group(1)] = int(match.group(2))
                    continue
                if line.startswith("#"):
                    now = int(line[1:]) * timescale
                    self.end = now
                elif now is not None and line[:1] in "01" and line[1:] in ids:
                    pin, level = ids[line[1:]], int(line[0])
                    if now == 0:
                        self.initial[pin] = level
                    elif level != self.level(pin):
                        self.edges.setdefault(pin, []).append(now)

    def level(self, pin: int) -> int:
        return self.initial.get(pin, 0) ^ (len(self.edges.get(pin, ())) & 1)

    def first_edge(self) -> tuple[float, int, int] | None:
        """(time, pin, new level) of the first change, or None without one."""
        found = [(times[0], pin) for pin, times in self.edges.items() if times]
        if not found:
            return None
        when, pin = min(found)
        return when, pin, 1 - self.initial.get(pin, 0)

    def shortest(self) -> float | None:
        """Shortest time between changes on any pin, not counting the idle time before the first one."""
        times = sorted({t for edges in self.edges.values() for t in edges})
        gaps = np.diff(times)
        return float(gaps[gaps > 0].min()) if np.any(gaps > 0) else None


def parse_stack(spec: str) -> list[tuple[str, dict]]:
    """sigrok-cli -P: decoders separated by commas, each id:key=value:..."""
    stack = []
    for part in spec.split(","):
        words = part.split(":")
        stack.append((words[0], dict(word.split("=", 1) for word in words[1:])))
    return stack


def parse_filter(spec: str | None) -> dict[str, set | None] | None:
    """sigrok-cli -A: decoder=class:class or row ids, decoders separated by commas; None shows all of one."""
    if not spec:
        return None
    shown = {}
    for part in spec.split(","):
        pd, _, names = part.partition("=")
        shown[pd] = set(names.split(":")) if names else None
    return shown


def typed(value: str, default):
    """An option value as sigrok-cli passes it: of the default's type."""
    if isinstance(default, bool):
        raise ValueError("boolean options are not used by sigrok decoders")
    if isinstance(default, int):
        return int(value, 10)
    if isinstance(default, float):
        return float(value)
    return value


def visible(info, cls: int, names: set | None) -> bool:
    if names is None:
        return True
    if info.annotations[cls][0] in names:
        return True
    return any(row[0] in names and cls in row[2] for row in info.annotation_rows)


def decode(case_spec, filter_spec, traces: dict[int, DigitalTrace], length: int, rate: float, decoders) -> tuple[list[str], list]:
    """First annotation texts as sigrok-cli prints them, and the decoders' exceptions."""
    stacks = case_spec if isinstance(case_spec, list) else [case_spec]
    shown = parse_filter(filter_spec)
    texts, problems = [], []
    for stack_spec in stacks:
        stack = parse_stack(stack_spec)
        session = sigrokdecode.Session(length, rate)
        wanted = shown if shown is not None else {stack[-1][0]: None}

        def on_annotation(instance, start, end, cls, annotation, wanted=wanted):
            info = decoders[type(instance).id]
            if info.id in wanted and visible(info, cls, wanted[info.id]) and annotation:
                # sigrok-cli prints "id: text"; the cases hold what follows ": " on each printed line.
                lines = f"{info.id}: {annotation[0]}".splitlines()
                texts.extend(line.split(": ", 1)[1] for line in lines if ": " in line)

        session.on_annotation = on_annotation
        for position, (pd, values) in enumerate(stack):
            info = decoders[pd]
            channel_ids = {item["id"] for item in info.channels + info.optional_channels}
            channels = {key: traces[int(value[2:])] for key, value in values.items() if key in channel_ids}
            defaults = {item["id"]: item.get("default") for item in info.options}
            options = {key: typed(value, defaults[key]) for key, value in values.items() if key not in channel_ids}
            unknown = set(options) - set(defaults)
            if unknown:
                raise ValueError(f"{pd} has no option {sorted(unknown)}")
            session.add(info.cls, options, channels=channels if position == 0 else None)
        session.run()
        problems += session.exceptions + session.problems
    return texts, problems


class NoDigitalRecord(Exception):
    """The saved file has no digital record. Measured on firmware 00.01.00 at 50 ms/div and slower: with no analog
    channel on the file is its 16-byte header alone, with one on it holds the analog record only; 20 ms/div is right."""


def pins_of(case) -> list[int]:
    specs = case[2] if isinstance(case[2], list) else [case[2]]
    return sorted({int(pin) for spec in specs for pin in re.findall(r"=gp(\d+)", spec)})


def matches(got: list[str], expected) -> bool:
    return sorted(got) == sorted(expected) if isinstance(expected, tuple) else got == list(expected)


def simulate(project: Path, case, work: Path) -> Simulation:
    timescale = case[5] if len(case) > 5 else 5
    vcd = work / "host.vcd"
    run = subprocess.run(["wsl", "--", wsl_path(project / "host" / "siggen_host"), "-o", wsl_path(vcd), "-t", str(timescale),
                          "-c", case[1]], capture_output=True, text=True, encoding="utf-8", errors="replace")
    if "DONE" not in run.stdout + run.stderr:
        raise RuntimeError(f"host build: {(run.stdout + run.stderr).strip()}")
    return Simulation(vcd)


def offline_traces(sim: Simulation, pins: list[int]) -> tuple[dict[int, DigitalTrace], int, float]:
    """The host build's signal sampled with SPP samples in its shortest pulse."""
    short = sim.shortest() or sim.end or 1e-6
    rate = float(round(min(SPP / short, OFFLINE_RATE_LIMIT)))  # sigrok sample rates are whole numbers
    first = sim.first_edge()[0]
    start = first - max(2 / rate, first)  # the case's own idle time before its first edge, as the project's test keeps it
    length = int(math.ceil((sim.end - start) * rate)) + 2
    traces = {}
    for pin in pins:
        edges = [int(round((t - start) * rate)) for t in sim.edges.get(pin, ())]
        traces[pin] = DigitalTrace(1 / rate, start, length, sim.initial.get(pin, 0), edges)
    return traces, length, rate


def capture_plan(sim: Simulation, pins: list[int]) -> tuple[CapturePlan, float, int]:
    """The plan, the record length in seconds and the memory depth for one case."""
    when, pin, level = sim.first_edge()
    span = sim.end - when  # from the trigger to the end of the case
    pre = when * 1.1  # the case's idle time before its first edge, and a little more
    scale = time_scale_125((pre + span) * MARGIN / 10)
    record = 10 * scale
    short = sim.shortest() or span
    # Digital samples in the record are a quarter of the memory depth with at most one analog channel on.
    depth = next((d for d in DEPTHS if d / 4 / record * short >= SPP), DEPTHS[-1])
    pretrigger = min(90.0, 100 * (pre / record + PRE_SHARE))
    conditions = {digital_channel(pin): trigger.RISING if level else trigger.FALLING}
    plan = CapturePlan(channels={digital_channel(p) for p in pins}, depth=str(depth), time_scale=scale,
                       trigger=tuple(trigger.condition_commands(conditions, {})),
                       thresholds={pod: THRESHOLD for pod in (1, 2) if any(p in pod_members(pod) for p in pins)},
                       pretrigger=pretrigger)
    return plan, record, depth


def arm(scope: Scope, timeout: float) -> None:
    """Start a single acquisition and wait until it waits for the trigger (Scope.single would force it after 1 s)."""
    scope.write(":RUN")
    started = time.monotonic()
    while scope.query(":TRIGger:STATus?") == "STOP":
        if time.monotonic() - started > timeout:
            raise RuntimeError("the acquisition did not start")
        time.sleep(0.02)
    scope.write(":SINGle")
    started = time.monotonic()
    while scope.query(":TRIGger:STATus?") != "WAIT":
        if time.monotonic() - started > timeout:
            raise RuntimeError(f"the scope did not wait for the trigger: {scope.query(':TRIGger:STATus?')}")
        time.sleep(0.02)


def wait_stop(scope: Scope, timeout: float) -> bool:
    started = time.monotonic()
    while time.monotonic() - started < timeout:
        if scope.query(":TRIGger:STATus?") == "STOP":
            return True
        time.sleep(0.05)
    scope.write(":STOP")
    return False


def live_traces(scope: Scope, pico: RemotePico, command: str, sim: Simulation, pins: list[int]) -> tuple[dict, int, float, str]:
    plan, record, depth = capture_plan(sim, pins)
    words = sum(sim.initial.get(pin, 0) << position for position, pin in enumerate(pins))
    reply = pico.cmd("parallel " + " ".join(f"d{position}={pin}" for position, pin in enumerate(pins)) + f" words={words:X}")
    if not reply[-1].startswith("DONE"):
        raise RuntimeError(f"setting the initial levels: {' | '.join(reply)}")
    notes = scope.apply_plan(plan)
    if notes:
        raise RuntimeError(f"scope setup: {notes}")
    arm(scope, 10)
    time.sleep(record * plan.pretrigger / 100 + 0.2)  # the record before the trigger fills first
    reply = pico.cmd(command, timeout=sim.end + 30)
    if not reply[-1].startswith("DONE"):
        scope.write(":STOP")
        raise RuntimeError(f"Pico: {' | '.join(reply)}")
    if not wait_stop(scope, record + 10):
        raise RuntimeError("the scope did not trigger")
    memory = rigol_files.parse_bin(scope.save_memory(), [digital_channel(pin) for pin in pins])
    if memory.digital_factor == 0:
        raise NoDigitalRecord(f"no digital record in the file at {plan.time_scale:g} s/div")
    waves = {pin: memory.waves[digital_channel(pin)] for pin in pins}
    dt = waves[pins[0]].dt
    levels = {pin: waves[pin].volts.astype(np.int8) for pin in pins}
    # Start the decoders the case's idle time before the first change, as the project's own test does.
    changes = [int(np.flatnonzero(np.diff(level))[0]) + 1 for level in levels.values() if np.any(np.diff(level))]
    if not changes:
        raise RuntimeError("no change on the case's pins in the record")
    lead = max(2, int(round(sim.first_edge()[0] / dt)))
    start = max(0, min(changes) - lead)
    traces = {pin: from_levels(level[start:], dt) for pin, level in levels.items()}
    length = len(levels[pins[0]]) - start
    short = sim.shortest()
    info = (f"{1 / dt / 1e6:g} MSa/s, depth {depth:,}, {short / dt if short else 0:.0f} samples in the shortest pulse, "
            f"factor {memory.digital_factor}")
    return traces, length, float(round(1 / dt)), info



def main() -> int:
    sys.stdout.reconfigure(errors="replace")
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--offline", action="store_true", help="decode the host build's signal, without Pico or scope")
    parser.add_argument("--ssh", help="user@host of the computer the Pico is plugged into")
    parser.add_argument("--scope", help="the scope's address")
    parser.add_argument("--project", type=Path, default=DEFAULT_PROJECT, help="the pico_generator project")
    parser.add_argument("--boundary", action="store_true", help="run the cases of pico_boundary_cases.py instead")
    parser.add_argument("--push-pull", action="store_true",
                        help="drive open-drain lines push-pull, for a bench without external pull-up resistors")
    parser.add_argument("--after", metavar="NAME", help="start with the case after this one")
    parser.add_argument("-v", action="store_true", help="print what was decoded")
    parser.add_argument("names", nargs="*", help="run only cases whose name contains one of these")
    args = parser.parse_args()
    if not args.offline and not (args.ssh and args.scope):
        parser.error("--ssh and --scope are needed unless --offline")

    if args.boundary:
        from pico_boundary_cases import CASES
    else:
        sys.path.insert(0, str(args.project / "tests"))
        from cases import CASES

    decoders, failed = srd_loader.load_all()
    work = Path(tempfile.mkdtemp(prefix="mhodecode_live_pico_"))  # its own, so two runs do not share a file
    pico = scope = None
    if not args.offline:
        pico = RemotePico(args.ssh, args.project / "tools" / "picogen.py")
        print("Pico:", " | ".join(pico.cmd("info")))
        scope = Scope(args.scope, timeout=30)
        print(scope.connect())
    passed = failures = skipped = limited = 0
    waiting = args.after is not None
    try:
        if waiting and args.after not in {case[0] for case in CASES}:
            raise SystemExit(f"no case named {args.after}")
        for case in CASES:
            name, command, spec, filter_spec, expected = case[:5]
            if args.push_pull and " drive=" not in command:
                command += " drive=pp"
            if waiting:
                waiting = name != args.after
                continue
            if args.names and not any(part in name for part in args.names):
                continue
            pins = pins_of(case)
            if expected is None or not pins:
                skipped += 1
                print(f"SKIP {name}")
                continue
            started = time.monotonic()
            try:
                missing = {pd for stack in (spec if isinstance(spec, list) else [spec]) for pd, _ in parse_stack(stack)} - set(decoders)
                if missing:
                    raise RuntimeError(f"decoders not loaded: {sorted(missing)} {[failed.get(pd) for pd in missing]}")
                sim = simulate(args.project, case, work)
                if sim.first_edge() is None:
                    raise RuntimeError("the host build changes no pin")
                if args.offline:
                    traces, length, rate = offline_traces(sim, pins)
                    info = f"{rate / 1e6:g} MSa/s"
                else:
                    traces, length, rate, info = live_traces(scope, pico, command, sim, pins)
                got, problems = decode(spec, filter_spec, traces, length, rate, decoders)
            except NoDigitalRecord as exc:
                limited += 1
                print(f"LIMIT {name}: {exc}")
                continue
            except Exception as exc:  # one broken case must not stop the others
                failures += 1
                print(f"FAIL {name}: {type(exc).__name__}: {exc}")
                continue
            took = time.monotonic() - started
            if args.v:
                print(f"  {got}")
            if matches(got, expected):
                passed += 1
                print(f"PASS {name} ({info}, {sim.end * 1e3:.2f} ms, {took:.1f} s)")
            else:
                failures += 1
                print(f"FAIL {name} ({info}, {sim.end * 1e3:.2f} ms):\n  expected {list(expected)}\n  got      {got}"
                      + (f"\n  problems {problems[:5]}" if problems else ""))
    finally:
        if pico:
            pico.close()
        if scope:
            scope.run()
            scope.close()
    print(f"\n{passed} passed, {failures} failed, {skipped} skipped, {limited} without a digital record in the scope's file")
    return 1 if failures or limited else 0


if __name__ == "__main__":
    sys.exit(main())

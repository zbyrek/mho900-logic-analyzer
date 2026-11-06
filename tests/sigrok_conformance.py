"""Runs the decoder test cases of the sigrok-test repository against mhodecode.sigrokdecode.

Usage: sigrok_conformance.py SIGROK_TEST_DIR SIGROK_DUMPS_DIR [decoder ...]
Each case compares annotation, Python and binary output with the reference files made by the
real libsigrokdecode. Exit code 0 only if every output matches.
"""
from __future__ import annotations

import configparser
import re
import sys
import time
import traceback
import zipfile
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from mhodecode import sigrokdecode, srd_loader  # noqa: E402
from mhodecode.signal import from_levels  # noqa: E402

SIZE_UNITS = {"khz": 1e3, "mhz": 1e6, "ghz": 1e9, "hz": 1.0}


def parse_samplerate(text):
    number, unit = re.fullmatch(r"([0-9.]+)\s*([a-zA-Z]*)", text.strip()).groups()
    return int(float(number) * SIZE_UNITS.get(unit.lower(), 1))


def load_capture(path):
    """The channels of a .sr file as level arrays, indexed from 0, and the sample rate."""
    with zipfile.ZipFile(path) as archive:
        config = configparser.ConfigParser(interpolation=None, strict=False)
        config.read_string(archive.read("metadata").decode())
        section = next(name for name in config.sections() if name.startswith("device"))
        device = config[section]
        unitsize = int(device["unitsize"])
        count = int(device["total probes"])
        capturefile = device.get("capturefile", "logic-1")
        names = sorted((name for name in archive.namelist() if name.startswith(capturefile)),
                       key=lambda name: int(name.rsplit("-", 1)[1]) if "-" in name[len(capturefile):] else 0)
        raw = b"".join(archive.read(name) for name in names)
        rate = parse_samplerate(device["samplerate"]) if "samplerate" in device else 0  # as runtc reports it
    data = np.frombuffer(raw, dtype=np.uint8)
    data = data[:len(data) // unitsize * unitsize].reshape(-1, unitsize)
    count = min(count, unitsize * 8)
    channels = [((data[:, channel // 8] >> (channel % 8)) & 1).astype(np.int8) for channel in range(count)]
    return channels, rate, len(data)


def parse_cases(path, decoder_dir):
    cases = []
    for line in path.read_text().split("\n"):
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        words = line.split()
        key = words.pop(0)
        if key == "test":
            cases.append({"name": words[0], "dir": decoder_dir, "pds": [], "outputs": [], "input": None})
        elif key == "protocol-decoder":
            spec = {"id": words.pop(0), "channels": {}, "options": {}, "initial": {}}
            for kind, value in zip(words[0::2], words[1::2]):
                name, value = value.split("=")
                if kind == "channel":
                    spec["channels"][name] = int(value)
                elif kind == "option":
                    spec["options"][name] = int(value) if value.isdigit() else value
                else:
                    spec["initial"][name] = int(value)
            cases[-1]["pds"].append(spec)
        elif key == "input":
            cases[-1]["input"] = words[0]
        elif key == "output":
            output = {"pd": words[0], "type": words[1], "class": None, "match": None}
            for kind, value in zip(words[2::2], words[3::2]):
                output[kind] = value
            cases[-1]["outputs"].append(output)
    return cases


def class_index(items, name):
    for position, item in enumerate(items):
        if item[0] == name:
            return position
    raise KeyError(name)


def run_case(case, decoders, dumps):
    channels, rate, length = load_capture(dumps / case["input"])
    traces = [from_levels(levels) for levels in channels]
    session = sigrokdecode.Session(length, rate)
    lines = []

    def on_annotation(instance, start, end, cls, texts):
        pd = type(instance).id
        name = type(instance).annotations[cls][0]
        quoted = "".join(f' "{text}"' for text in texts)
        lines.append((pd, "annotation", name, f"{start}-{end} {pd}: {name}:{quoted}"))

    def on_binary(instance, start, end, cls, data):
        pd = type(instance).id
        name = type(instance).binary[cls][0]
        text = "".join(f" {byte:02x}" for byte in data)
        lines.append((pd, "binary", name, f"{start}-{end} {pd}:{text}"))

    def on_python(instance, start, end, data):
        pd = type(instance).id
        lines.append((pd, "python", None, f"{start}-{end} {pd}: {data!r}"))

    session.on_annotation, session.on_binary, session.on_python = on_annotation, on_binary, on_python
    for position, spec in enumerate(case["pds"]):
        info = decoders[spec["id"]]
        kwargs = {}
        if position == 0:
            ids = [item["id"] for item in info.channels + info.optional_channels]
            given = spec["channels"]
            if given:
                kwargs["channels"] = {name: traces[number] for name, number in given.items()}
            else:
                kwargs["channels"] = {name: traces[number] for number, name in enumerate(ids) if number < len(traces)}
            kwargs["initial_pins"] = spec["initial"]
        session.add(info.cls, spec["options"], **kwargs)
    session.run()

    failures = []
    for output in case["outputs"]:
        expected_path = case["dir"] / output["match"]
        if output["type"] == "exception":
            names = [type(exc).__name__ for _pd, exc in session.exceptions]
            if output["match"] not in names:
                failures.append(f"{output}: exceptions were {names}")
            continue
        got = [text for pd, kind, name, text in lines
               if pd == output["pd"] and kind == output["type"] and (output["class"] is None or name == output["class"])]
        # An annotation can hold a line break, so the lines come from cutting at the same place.
        line_break = chr(10)
        expected = expected_path.read_bytes().decode("utf-8", "surrogateescape").replace(chr(13) + line_break, line_break).split(line_break)[:-1]
        got = line_break.join(got).split(line_break) if got else []
        if got != expected:
            first = next((i for i, (a, b) in enumerate(zip(got, expected)) if a != b), min(len(got), len(expected)))
            failures.append(f"{output['pd']} {output['type']} {output['class'] or ''} {output['match']}: "
                            f"{len(got)} lines, expected {len(expected)}; line {first}:\n"
                            f"    got  {got[first] if first < len(got) else None}\n"
                            f"    want {expected[first] if first < len(expected) else None}")
    if session.exceptions and not any(output["type"] == "exception" for output in case["outputs"]):
        failures.append(f"unexpected exceptions: {session.exceptions}")
    return failures, sum(1 for output in case["outputs"])


def main():
    sys.stdout.reconfigure(encoding="utf-8", errors="backslashreplace")
    test_root = Path(sys.argv[1]) / "decoder" / "test"
    dumps = Path(sys.argv[2])
    only = set(sys.argv[3:])
    decoders, failed = srd_loader.load_all()
    print(f"loaded {len(decoders)} decoders, failed {failed}")
    cases_total = cases_ok = outputs_total = 0
    bad = {}
    started = time.monotonic()
    for conf in sorted(test_root.glob("*/test.conf")):
        if only and conf.parent.name not in only:
            continue
        for case in parse_cases(conf, conf.parent):
            cases_total += 1
            label = f"{conf.parent.name}/{case['name']}"
            began = time.monotonic()
            try:
                failures, outputs = run_case(case, decoders, dumps)
                outputs_total += outputs
            except Exception as exc:  # one broken case must not hide the others
                failures = [f"harness error {type(exc).__name__}: {exc} " + traceback.format_exc(limit=-3)]
            if failures:
                bad[label] = failures
                print(f"FAIL {label} ({time.monotonic() - began:.1f} s)")
                for failure in failures[:3]:
                    print("  " + failure)
            else:
                cases_ok += 1
    print(f"{cases_ok}/{cases_total} cases passed, {outputs_total} outputs compared, {time.monotonic() - started:.0f} s")
    return 0 if cases_ok == cases_total else 1


if __name__ == "__main__":
    sys.exit(main())

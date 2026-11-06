"""The libsigrokdecode decoders on the replacement core. tests/sigrok_conformance.py compares
them with sigrok's own reference output; these tests need no extra repositories."""
import random

import numpy as np
import pytest

from mhodecode import abort, generator, siggen, sigrokdecode
from mhodecode.decoders import DECODERS, SIGROK, SIGROK_ALL, SIGROK_FAILED, SIGROK_ROOTS
from mhodecode.signal import from_levels
from mhodecode.srd_loader import base_chain, stackable_on


def traces_of(signal, keys=None):
    return {(keys or {}).get(key, key): from_levels(levels, 1 / signal.sample_rate)
            for key, levels in signal.lines.items()}


def binary_bytes(result, row):
    return bytes(item.value for item in result.annotations if item.row == row)


def texts_of(result, row):
    return [item.text for item in result.annotations if item.row == row]


def test_every_decoder_loads():
    assert not SIGROK_FAILED
    assert len(SIGROK_ALL) == 131
    assert set(SIGROK_ALL) - set(SIGROK) == {"ir_irmp"}
    assert len(DECODERS) == len(SIGROK_ROOTS)


def test_uart_gives_the_sent_bytes():
    signal = siggen.uart(b"Hello", 115200)
    result = SIGROK_ROOTS["uart"].decode(traces_of(signal, {"tx": "tx"}), baudrate=115200)
    assert binary_bytes(result, "uart:bin:tx") == b"Hello"
    assert not result.notes


def test_i2c_gives_address_and_data():
    signal = siggen.i2c([(0x50, False, b"\x12\x34")], 100000)
    result = SIGROK_ROOTS["i2c"].decode(traces_of(signal))
    assert "Address write: 0x50" in [item.text for item in result.annotations]
    assert binary_bytes(result, "i2c:bin:data-write") == b"\x12\x34"


def test_spi_gives_the_words():
    signal = siggen.spi([[0xA5, 0x5A]], 1000000)
    result = SIGROK_ROOTS["spi"].decode(traces_of(signal))
    assert binary_bytes(result, "spi:bin:mosi-data") or binary_bytes(result, "spi:bin:mosi")


def test_can_frame_is_read():
    signal = siggen.can([(0x123, b"\x11\x22", False)], 500000)
    trace = traces_of(signal)
    result = SIGROK_ROOTS["can"].decode({"can_rx": trace["can"]}, nominal_bitrate=500000, sample_point=70.0)
    joined = " ".join(item.text for item in result.annotations)
    assert "CRC-15 sequence" in joined and "Identifier: 291 (0x123)" in joined
    assert not [item for item in result.annotations if item.error]


def test_uart_values_are_marked_by_the_chosen_format():
    signal = siggen.uart(b"H\r", 115200)
    def shown(chosen):
        options = {"format": chosen} if chosen else {}
        result = SIGROK_ROOTS["uart"].decode(traces_of(signal), baudrate=115200, **options)
        return [item.text for item in result.annotations if item.kind == "tx-data"]
    assert shown(None) == shown("hex") == ["0x48", "0x0D"]
    assert shown("ascii") == ['"H"', "0x0D"]
    assert shown("dec") == ["72", "13"]  # the other formats stay as the decoder writes them


def test_every_form_of_a_value_gets_its_prefix():
    signal = siggen.i2c([(0x50, False, b"\x12")], 100000)
    result = SIGROK_ROOTS["i2c"].decode(traces_of(signal))
    data = next(item for item in result.annotations if item.kind == "data-write")
    assert data.texts == ("Data write: 0x12", "DW: 0x12", "0x12") and not data.control
    assert next(item for item in result.annotations if item.kind == "start").texts == ("Start", "S")


def test_protocol_fields_are_told_from_data():
    signal = siggen.can([(0x123, b"\x11", False)], 500000)
    result = SIGROK_ROOTS["can"].decode({"can_rx": traces_of(signal)["can"]}, nominal_bitrate=500000, sample_point=70.0)
    kinds = {item.kind: item.control for item in result.annotations if not item.hidden}
    assert kinds["data"] is False and kinds["sof"] is True and kinds["crc-sequence"] is True


def test_a_decoder_without_a_data_class_is_not_divided():
    from mhodecode.decoders.sigrok import _is_data
    info = SIGROK_ALL["guess_bitrate"]
    assert not any(_is_data(annotation[0]) for annotation in info.annotations)


def test_stacked_decoder_gets_the_base_output():
    signal = siggen.uart(bytes([0x90, 0x3C, 0x40]), 31250)
    result = SIGROK_ROOTS["uart"].decode(traces_of(signal), baudrate=31250, srd_stack=[("midi", {})])
    midi = [item.text for item in result.annotations if item.row.startswith("midi:")]
    assert any("note on" in text for text in midi), midi


def test_two_level_stack():
    # eeprom24xx on i2c: one write of a byte at an address.
    signal = siggen.i2c([(0x50, False, b"\x10\xAB")], 100000)
    result = SIGROK_ROOTS["i2c"].decode(traces_of(signal), srd_stack=[("i2cfilter", {}), ("eeprom24xx", {})])
    rows = {item.row for item in result.annotations}
    assert any(row.startswith("eeprom24xx:") for row in rows)


def test_comparison_works_on_binary_output():
    signal = siggen.uart(b"abc", 115200)
    result = SIGROK_ROOTS["uart"].decode(traces_of(signal), baudrate=115200)
    assert generator.compare(result.annotations, b"abc").matched
    assert not generator.compare(result.annotations, b"abd").matched


def test_annotations_are_plotted_in_their_rows():
    signal = siggen.uart(b"A", 115200)
    result = SIGROK_ROOTS["uart"].decode(traces_of(signal), baudrate=115200)
    assert "uart:tx-data-vals" in result.rows
    assert all(item.row in result.rows for item in result.annotations if not item.hidden)
    bits = [item for item in result.annotations if item.row == "uart:tx-data-bits"]
    assert bits and all(item.detail for item in bits)


def test_chain_for_a_decoder_that_needs_a_base():
    assert base_chain("eeprom24xx", SIGROK) == ["i2c", "eeprom24xx"]
    assert base_chain("usb_request", SIGROK) == ["usb_signalling", "usb_packet", "usb_request"]
    assert base_chain("uart", SIGROK) == ["uart"]


def test_every_upper_decoder_can_be_reached():
    for pd_id, info in SIGROK.items():
        chain = base_chain(pd_id, SIGROK)
        assert not SIGROK[chain[0]].needs_base, pd_id


def test_stack_choices_follow_the_output():
    assert "eeprom24xx" in [info.id for info in stackable_on("i2c", SIGROK)]
    assert "eeprom24xx" not in [info.id for info in stackable_on("uart", SIGROK)]


@pytest.mark.parametrize("pd_id", sorted(SIGROK_ROOTS))
def test_root_runs_on_any_pulse_train(pd_id):
    """Default options on arbitrary pulses: a decoder may give up in its own words, never by a bug in the core."""
    decoder = SIGROK_ROOTS[pd_id]
    generator_random = random.Random(pd_id)
    traces = {}
    for signal in decoder.signals:
        if signal.required or len(traces) < 4:
            edges = sorted(generator_random.sample(range(1, 20000), 400))
            traces[signal.key] = sigrokdecode_trace(edges)
    result = decoder.decode(traces, **decoder.defaults())
    bad = [note for note in result.notes if any(name in note for name in ("TypeError", "NameError", "IndexError: invalid channel"))]
    assert not bad, bad


def sigrokdecode_trace(edges):
    levels = np.zeros(20000, dtype=np.int8)
    for start, stop in zip(edges[0::2], edges[1::2]):
        levels[start:stop] = 1
    return from_levels(levels, 1e-6)


class _Probe(sigrokdecode.Decoder):
    id = "probe"
    channels = ({"id": "a", "name": "A", "desc": ""}, {"id": "b", "name": "B", "desc": ""})
    optional_channels = ({"id": "c", "name": "C", "desc": ""},)
    annotations = (("x", "X"),)
    conditions = ()

    def start(self):
        pass

    def decode(self):
        self.log = []
        for conds in self.conditions:
            try:
                pins = self.wait(conds) if conds != "none" else self.wait()
            except EOFError:
                self.log.append(("EOF", self.samplenum))
                return
            self.log.append((self.samplenum, pins, self.matched))


class _Reference:
    """The sample-by-sample search of instance.c, written as directly as possible."""

    def __init__(self, a, b, c=None):
        self.a, self.b, self.c = a, b, c
        self.cur = 0
        self.old = None
        self.had = False

    def pins(self, i):
        return (int(self.a[i]), int(self.b[i]), 0xFF if self.c is None else int(self.c[i]))

    def wait(self, conds):
        n = len(self.a)
        if conds == "none":
            count = 1 if self.cur or self.had else 0
            conds = [{"skip": count}]
        elif isinstance(conds, dict):
            conds = [conds]
        self.had = True
        skips = [dict(cond) for cond in conds]
        for cond in skips:
            if "skip" in cond:
                cond["seen"] = 0
        if self.old is None:
            self.old = self.pins(0)
        for i in range(self.cur, n):
            new = self.pins(i)
            matched = []
            for cond in skips:
                ok = True
                for key, value in list(cond.items()):
                    if key == "seen":
                        continue
                    if key == "skip":
                        if cond["seen"] == value:
                            continue
                        cond["seen"] += 1
                        ok = False
                        continue
                    o, p = self.old[key], new[key]
                    ok = ok and {"h": p == 1, "l": p == 0, "r": o == 0 and p == 1, "f": o == 1 and p == 0,
                                 "e": o != p, "n": o == p}[value]
                matched.append(ok)
            self.old = new
            if any(matched):
                self.cur = i
                return (i, new, tuple(matched))
        self.cur = n
        return ("EOF", n)


@pytest.mark.parametrize("seed", range(40))
def test_wait_matches_the_sample_by_sample_search(seed):
    rng = random.Random(seed)
    length = rng.choice((1, 2, 30, 200))
    def levels():
        out, level = [], rng.randint(0, 1)
        while len(out) < length:
            out += [level] * rng.choice((1, 1, 2, 3, 7))
            level ^= 1
        return np.array(out[:length], dtype=np.int8)
    a, b = levels(), levels()
    choices = []
    for _ in range(rng.randint(1, 60)):
        term = lambda: {rng.choice((0, 1)): rng.choice("hlrfen")}
        choices.append(rng.choice((
            "none", {"skip": rng.randint(0, 12)}, term(), [term()], [term(), term()],
            {0: rng.choice("hlrfen"), 1: rng.choice("hlrfen")},
            [{"skip": rng.randint(0, 9)}, term()], [{0: "e"}, {1: "e"}, {"skip": rng.randint(0, 5)}])))
    reference = _Reference(a, b)
    expected = []
    for conds in choices:
        step = reference.wait(conds)
        expected.append(step)
        if step[0] == "EOF":
            break
    session = sigrokdecode.Session(length, 1000)
    probe_class = type("P", (_Probe,), {"conditions": tuple(choices)})
    probe = session.add(probe_class, {}, {"a": from_levels(a), "b": from_levels(b)})
    session.run()
    assert not session.exceptions
    assert probe.log == expected


def test_wait_edge_against_an_initial_pin():
    level = np.array([1, 1, 1], dtype=np.int8)
    session = sigrokdecode.Session(3, 1000)
    probe = session.add(type("P", (_Probe,), {"conditions": ({0: "r"},)}), {},
                        {"a": from_levels(level), "b": from_levels(level)}, {"a": 0})
    session.run()
    assert probe.log == [(0, (1, 1, 0xFF), (True,))]


def test_required_channel_is_enforced():
    session = sigrokdecode.Session(3, 1000)
    with pytest.raises(ValueError):
        session.add(_Probe, {}, {"a": from_levels([0, 1, 0])})


def test_wrong_option_type_is_refused():
    with pytest.raises(ValueError):
        SIGROK_ROOTS["uart"].decode(traces_of(siggen.uart(b"A", 9600)), baudrate="fast")


def test_decoder_exception_keeps_earlier_results_and_is_reported():
    class Broken(sigrokdecode.Decoder):
        id = "broken"
        channels = ({"id": "a", "name": "A", "desc": ""},)
        annotations = (("x", "X"),)

        def start(self):
            self.out = self.register(sigrokdecode.OUTPUT_ANN)

        def decode(self):
            self.put(0, 1, self.out, [0, ["before"]])
            raise RuntimeError("boom")

    seen = []
    session = sigrokdecode.Session(3, 1000, on_annotation=lambda *args: seen.append(args[4]))
    session.add(Broken, {}, {"a": from_levels([0, 1, 0])})
    session.run()
    assert seen == [["before"]]
    assert isinstance(session.exceptions[0][1], RuntimeError)


def test_abort_stops_a_long_decode():
    signal = siggen.uart(b"x", 9600)
    signal.lines["tx"] = np.tile(signal.lines["tx"], 3000)
    abort.request()
    try:
        with pytest.raises(abort.OperationAborted):
            SIGROK_ROOTS["uart"].decode(traces_of(signal), baudrate=9600)
    finally:
        abort.clear()

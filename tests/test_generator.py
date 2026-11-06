"""Generator data parsing, output assignment, comparison and translations."""
import re
from pathlib import Path

import numpy as np
import pytest

from mhodecode import abort, generator, i18n
from mhodecode.decoders import DECODERS
from mhodecode.decoders.base import Annotation
from mhodecode.i18n_en import ENGLISH
from mhodecode.signal import from_levels

SOURCE = Path(__file__).resolve().parents[1] / "mhodecode"
LOWERCASE = re.compile(r"[a-ząćęłńóśźż]")


# ---- data field --------------------------------------------------------------
@pytest.mark.parametrize("text,expected", [
    ("48 65 6C", b"Hel"), ("48656C", b"Hel"), ("0x48, 0x65", b"He"), ("ff", b"\xff")])
def test_hex_data(text, expected):
    assert generator.parse_data(text, "hex") == expected


@pytest.mark.parametrize("text,expected", [
    ("Hello", b"Hello"), ("a\\r\\n", b"a\r\n"), ("\\x41\\t", b"A\t"), ("\\\\", b"\\"), ("\\0", b"\0")])
def test_ascii_data(text, expected):
    assert generator.parse_data(text, "ascii") == expected


@pytest.mark.parametrize("text,data_format", [("", "hex"), ("", "ascii"), ("4", "hex"), ("zz", "hex"),
                                              ("zażółć", "ascii"), ("\\q", "ascii")])
def test_bad_data_rejected(text, data_format):
    with pytest.raises(ValueError):
        generator.parse_data(text, data_format)


@pytest.mark.parametrize("data", [b"Hello", b"a\r\n\t\\", bytes(range(256))])
@pytest.mark.parametrize("data_format", ["hex", "ascii"])
def test_format_is_inverse_of_parse(data, data_format):
    assert generator.parse_data(generator.format_data(data, data_format), data_format) == data


# ---- 1-2-5 time scale ---------------------------------------------------------
def test_time_scale_choices_are_1_2_5():
    choices = generator.time_scale_choices()
    assert choices[0] == 5e-10 and choices[-1] == 500
    assert choices[:4] == [5e-10, 1e-9, 2e-9, 5e-9]
    assert all(later > earlier for earlier, later in zip(choices, choices[1:]))
    assert len(choices) == 37


@pytest.mark.parametrize("wanted,scale", [(1.1e-3, 2e-3), (2e-3, 2e-3), (3e-6, 5e-6), (6e-6, 1e-5),
                                          (1e-12, 5e-10), (1e4, 500.0)])
def test_time_scale_rounds_up(wanted, scale):
    assert generator.time_scale_125(wanted) == pytest.approx(scale)


def test_time_scale_labels():
    assert generator.format_time_scale(5e-10) == "500 ps/div"
    assert generator.format_time_scale(2e-6) == "2 µs/div"
    assert generator.format_time_scale(0.5) == "500 ms/div"
    assert generator.format_time_scale(200) == "200 s/div"


# ---- building and assigning ---------------------------------------------------
def defaults(protocol, **changes):
    values = {param.key: param.default for param in generator.PROTOCOL_PARAMS[protocol]}
    values.update(changes)
    return values


@pytest.mark.parametrize("protocol", generator.PROTOCOLS)
def test_built_signal_decodes_to_the_same_data(protocol):
    data = bytes(range(1, 9))
    generated = generator.build(protocol, data, defaults(protocol))
    decoder = DECODERS[generated.decoder]
    dt = 1 / generated.sample_rate
    traces = {signal: from_levels(generated.lines[line], dt) for signal, line in generated.signals.items()}
    result = decoder.decode(traces, **generated.decoder_params)
    assert generator.compare(result.annotations, data).matched, result.notes


def test_long_classic_can_data_is_split_into_frames():
    data = bytes(range(20))
    generated = generator.build("CAN", data, defaults("CAN"))
    traces = {"can_rx": from_levels(generated.lines["can"], 1 / generated.sample_rate)}
    result = DECODERS["CAN"].decode(traces, **generated.decoder_params)
    assert len([a for a in result.annotations if "Start of frame" in a.text]) == 3
    assert generator.compare(result.annotations, data).matched


def test_invalid_can_fd_length_rejected():
    with pytest.raises(ValueError):
        generator.build("CAN FD", bytes(9), defaults("CAN FD"))


def test_too_long_data_rejected():
    with pytest.raises(ValueError):
        generator.build("UART", bytes(5000), defaults("UART"))


def test_samples_per_bit_drop_to_fit_the_record():
    generated = generator.build("UART", bytes(150), defaults("UART"))
    assert generated.sample_rate == pytest.approx(115200 * 8)


def test_sample_rate_limit():
    with pytest.raises(ValueError):
        generator.build("UART", b"a", defaults("UART", rate=20_000_001))
    assert generator.build("SPI", b"a", defaults("SPI", rate=5_000_000)).sample_rate <= generator.MAX_SAMPLE_RATE


def test_differential_can_lines_are_complementary():
    generated = generator.build("CAN", b"\x55", defaults("CAN", differential=True))
    assert set(generated.lines) == {"can", "can_l"}
    assert np.all(generated.lines["can"] == 1 - generated.lines["can_l"])
    assert generator.default_levels("can", {"differential": True}) == (2.5, 3.5)
    assert generator.default_levels("can_l", {"differential": True}) == (1.5, 2.5)


def test_assign_rejects_bad_choices():
    generated = generator.build("I2C", b"a", defaults("I2C"))
    good = {1: (0.0, 3.3), 2: (0.0, 3.3)}
    with pytest.raises(ValueError):
        generator.assign(generated, {"scl": 1, "sda": 1}, good)
    with pytest.raises(ValueError):
        generator.assign(generated, {"scl": None, "sda": None}, good)
    with pytest.raises(ValueError):
        generator.assign(generated, {"scl": 1, "sda": 2}, {1: (3.3, 0.0), 2: (0.0, 3.3)})


def test_assign_follows_the_chosen_outputs():
    generated = generator.build("I2C", b"a", defaults("I2C"))
    generator.assign(generated, {"scl": 2, "sda": 1}, {1: (0.0, 1.8), 2: (0.0, 5.0)}, "FIFTy")
    assert generated.outputs == {2: "scl", 1: "sda"}
    assert generated.levels == {1: (0.0, 1.8), 2: (0.0, 5.0)}
    assert generated.impedance == "FIFTy"
    generator.assign(generated, {"scl": None, "sda": 2}, {1: (0.0, 1.8), 2: (0.0, 5.0)})
    assert generated.outputs == {2: "sda"} and set(generated.levels) == {2}


# ---- comparison ---------------------------------------------------------------
def items(values, row="tx", kind="TX", errors=()):
    return [Annotation(i, i + 1, row, kind, "", value, "bad" if i in errors else "") for i, value in enumerate(values)]


def test_compare_finds_copies_anywhere():
    outcome = generator.compare(items([9, 1, 2, 3, 9, 1, 2, 3]), bytes([1, 2, 3]))
    assert outcome.matched and outcome.occurrences == 2 and outcome.row == "tx"


def test_compare_reports_first_difference():
    outcome = generator.compare(items([1, 2, 7, 4]), bytes([1, 2, 3, 4]))
    assert not outcome.matched
    assert (outcome.best_count, outcome.position, outcome.got, outcome.want) == (2, 2, 7, 3)


def test_compare_reports_missing_end():
    outcome = generator.compare(items([1, 2]), bytes([1, 2, 3]))
    assert not outcome.matched and outcome.position == 2 and outcome.got is None and outcome.want == 3


def test_compare_refuses_a_copy_with_an_error():
    assert not generator.compare(items([1, 2, 3], errors={1}), bytes([1, 2, 3])).matched


def test_compare_with_nothing_decoded():
    outcome = generator.compare([], b"a")
    assert not outcome.matched and outcome.received == 0


def test_compare_ignores_addresses_and_frame_rows():
    annotations = items([0x50], "sda", "ADDR") + items([1, 2], "sda", "DATA")
    assert generator.compare(annotations, bytes([1, 2])).matched
    assert not generator.compare(annotations, bytes([0x50, 1, 2])).matched


def test_compare_looks_at_each_line():
    annotations = items([5, 6], "tx", "TX") + items([1, 2], "rx", "RX")
    outcome = generator.compare(annotations, bytes([1, 2]))
    assert outcome.matched and outcome.row == "rx"


# ---- abort --------------------------------------------------------------------
def test_abort_stops_a_decoder():
    generated = generator.build("UART", bytes(50), defaults("UART"))
    traces = {"tx": from_levels(generated.lines["tx"], 1 / generated.sample_rate)}
    abort.request()
    try:
        with pytest.raises(abort.OperationAborted):
            DECODERS["UART"].decode(traces, **generated.decoder_params)
    finally:
        abort.clear()


# ---- translations ------------------------------------------------------------
def translated_literals():
    """Every string passed to tr() in the source, joining adjacent literals."""
    found = set()
    pattern = re.compile(r'\btr\(\s*((?:"(?:[^"\\]|\\.)*"\s*)+)')
    for path in SOURCE.rglob("*.py"):
        for match in pattern.finditer(path.read_text(encoding="utf-8")):
            parts = re.findall(r'"((?:[^"\\]|\\.)*)"', match.group(1))
            found.add("".join(parts).encode().decode("unicode_escape").encode("latin-1").decode("utf-8"))
    return found


def spec_texts():
    specs = []
    for params in generator.PROTOCOL_PARAMS.values():
        for param in params:
            specs += [param.label, param.hint] + [label for _value, label in param.choices or ()]
    return {text for text in specs if text}


def gui_constants():
    from mhodecode import decoder_panel, gui, scope_panel, trigger
    texts = [label for label, _value in gui.DATA_FORMATS + gui.LOADS]
    texts += [label for label, _value in scope_panel.DEPTHS + scope_panel.ACQUIRE_TYPES + scope_panel.BANDWIDTHS + trigger.SWEEPS]
    texts += list(scope_panel.STATE_NAMES.values())
    for mode in trigger.MODES:
        texts.append(mode.label)
        for field in mode.fields:
            texts.append(field.label)
            if field.choices is not trigger.SOURCES:
                texts += [label for label, _value in field.choices]
    for _title, tags in decoder_panel.DECODER_GROUPS:
        texts.append(_title)
        texts += [label for _tag, label in tags]
    return set(texts) | set(gui.AnnotationModel.HEADERS)


def bound_texts():
    """Literals handed to the window's helpers that translate them later."""
    source = (SOURCE / "gui.py").read_text(encoding="utf-8")
    panel = (SOURCE / "scope_panel.py").read_text(encoding="utf-8")
    found = set(re.findall(r'self\._(?:label|group)\("([^"]+)"\)', source))
    found |= set(re.findall(r'self\._bind\(.*, "([^"]+)"\)', source))
    found |= set(re.findall(r'self\.(?:owner\._label|_group)\("([^"]+)"\)', panel))
    found |= set(re.findall(r'owner\._bind\(.*, "([^"]+)"\)', panel))
    found |= set(re.findall(r'self\._busy_button\("([^"]+)"', source))
    found |= set(re.findall(r'\("([^"]+)", "(?:NEGative|POSitive)"\)', source))
    # the two-part note on the generator page
    found.add("Wyjście powtarza rekord w pętli, dopóki jest włączone. Przed podłączeniem urządzenia sprawdź poziomy napięć.")
    found.discard("Wyjście powtarza rekord w pętli, dopóki jest włączone. ")
    return found


def needs_translation(text):
    return bool(LOWERCASE.search(text))


@pytest.mark.parametrize("text, expected", [('"Hi"', b"Hi"), ("Hi", b"Hi"), ('""a""', b'"a"'), ('"', b'"'), ('" "', b" ")])
def test_ascii_data_may_be_quoted(text, expected):
    assert generator.parse_data(text, "ascii") == expected


def test_data_is_shown_with_its_format_marked():
    assert generator.format_data(b"He", "hex") == "0x48 0x65"
    assert generator.format_data(b"He\r", "ascii") == '"He\\r"'


def test_every_visible_text_has_an_english_entry():
    texts = translated_literals() | spec_texts() | gui_constants() | bound_texts()
    missing = sorted(text for text in texts if needs_translation(text) and text not in ENGLISH)
    assert not missing, missing


def test_english_placeholders_match_polish():
    for source, target in ENGLISH.items():
        assert set(re.findall(r"\{(\w+)", source)) == set(re.findall(r"\{(\w+)", target)), source


def test_english_has_no_polish_letters():
    bad = [target for target in ENGLISH.values() if re.search("[ąćęłńóśźżĄĆĘŁŃÓŚŹŻ]", target)]
    assert not bad, bad


def test_language_switch():
    try:
        i18n.set_language("en")
        assert i18n.tr("Połącz") == "Connect"
        assert i18n.tr("BŁĄD: {message}", message="x") == "ERROR: x"
        i18n.set_language("pl")
        assert i18n.tr("Połącz") == "Połącz"
        i18n.set_language("xx")
        assert i18n.language() == "pl"
    finally:
        i18n.set_language("pl")

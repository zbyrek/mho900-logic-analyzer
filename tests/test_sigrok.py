"""The libsigrokdecode decoders on the replacement core. tests/sigrok_conformance.py compares
them with sigrok's own reference output; these tests need no extra repositories."""
import binascii
import random
import sys
from pathlib import Path

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


def can_fd_texts(data, esi=False, ack=True, rate=8):
    widths = {"n": 4 * rate, "d": rate, "b": 3 * rate + rate // 4, "c": rate - rate // 4 + rate}
    levels = [1] * 400
    for bit, phase in siggen.can_fd_frame_bits(0x123, data, esi=esi, ack=ack):
        levels += [bit] * widths[phase]
    trace = from_levels(np.array(levels + [1] * 400, dtype=bool), 1 / (2000000 * rate))
    result = SIGROK_ROOTS["can"].decode({"can_rx": trace}, nominal_bitrate=500000, fast_bitrate=2000000, sample_point=75.0)
    return bytes(item.value for item in result.annotations if item.row == "can:bin:data"), " ".join(
        item.text for item in result.annotations), [item.text for item in result.annotations if item.error]


@pytest.mark.parametrize("esi", [False, True])
@pytest.mark.parametrize("ack", [False, True])
@pytest.mark.parametrize("data", [b"", b"\x00", b"\x01", b"\x1f", b"\xff" * 8, bytes(range(12)), bytes(range(16)),
                                  bytes([0x55] * 15) + b"\xe0", bytes(range(20)), b"\xff" * 64, b"\x00" * 64])
def test_can_fd_frame_is_read_to_its_end(data, ack, esi):
    got, texts, errors = can_fd_texts(data, esi, ack)
    assert got == data
    assert not errors
    assert "End of frame" in texts and f"CRC-{17 if len(data) <= 16 else 21} sequence" in texts


def can_frame_texts(bits):
    trace = from_levels(np.repeat(np.array([1] * 12 + bits + [1] * 20, dtype=bool), 16), 1 / 8e6)
    result = SIGROK_ROOTS["can"].decode({"can_rx": trace}, nominal_bitrate=500000)
    return [item.text for item in result.annotations]


@pytest.mark.parametrize("extended", [False, True])
@pytest.mark.parametrize("length", [0, 1, 8])
def test_can_remote_frame_has_no_data(length, extended):
    texts = can_frame_texts(siggen.can_frame_bits(0x123, b"\x00" * length, extended=extended, remote=True))
    assert not [text for text in texts if text.startswith("Data byte")]
    assert any(text.startswith("CRC-15 sequence") for text in texts) and "End of frame" in texts


def uart_levels(data, stop_halves):
    """8N words back to back, 32 samples per bit."""
    levels = [1] * 400
    for byte in data:
        levels += [0] * 32
        for index in range(8):
            levels += [byte >> index & 1] * 32
        levels += [1] * (16 * stop_halves)
    return np.array(levels + [1] * 400, dtype=bool)


@pytest.mark.parametrize("halves", [1, 2, 3, 4])
def test_uart_half_stop_bits(halves):
    data = bytes([0x00, 0x01, 0x7F, 0x80, 0xFE, 0xFF, 0x55])
    trace = from_levels(uart_levels(data, halves), 1 / (115200 * 32))
    result = SIGROK_ROOTS["uart"].decode({"tx": trace}, baudrate=115200, stop_bits=halves / 2)
    assert binary_bytes(result, "uart:bin:tx") == data
    assert not [item for item in result.annotations if item.error]


@pytest.mark.parametrize("raw, celsius", [((0xC9, 0x00), "-55.0"), ((0xFF, 0x80), "-0.5"), ((0x00, 0x00), "0.0"),
                                          ((0x00, 0x80), "0.5"), ((0x7D, 0x00), "125.0")])
def test_lm75_temperature(raw, celsius):
    signal = siggen.i2c([(0x48, False, b"\x00"), (0x48, True, bytes(raw))], 100000)
    texts = [item.text for item in SIGROK_ROOTS["i2c"].decode(traces_of(signal), srd_stack=[("lm75", {})]).annotations]
    assert any(f"Temperature: {celsius} °C" in text for text in texts), texts


def decoder_module(name):
    return sys.modules[SIGROK_ALL[name].cls.__module__]


def run_stacked(name, events):
    """Feeds events of the parent protocol and gives the first annotation texts."""
    instance = SIGROK_ALL[name].cls()
    instance.out_ann = 0
    texts = []
    instance.put = lambda ss, es, out, data: texts.append(data[1][0])
    for position, event in enumerate(events):
        instance.decode(position, position + 1, event)
    return texts


def mlx90614_read_word(command, low, high):
    return [("START", None), ("ADDRESS WRITE", 0x5A), ("ACK", None), ("DATA WRITE", command), ("ACK", None),
            ("START REPEAT", None), ("ADDRESS READ", 0x5A), ("ACK", None), ("DATA READ", low), ("ACK", None),
            ("DATA READ", high), ("ACK", None), ("DATA READ", 0x00), ("NACK", None), ("STOP", None)]


@pytest.mark.parametrize("command, low, high, expected", [
    (0x06, 0x53, 0x3A, "25.47 °C"), (0x07, 0x00, 0x00, "-273.15 °C"), (0x08, 0xFF, 0x7F, "382.19 °C"),
    (0x07, 0x00, 0x80, None), (0x24, 0x53, 0x3A, None)])
def test_mlx90614_reads_the_smbus_word(command, low, high, expected):
    texts = run_stacked("mlx90614", mlx90614_read_word(command, low, high))
    assert texts[:1] == ([f"Temperature: {expected}"] if expected else [])


@pytest.mark.parametrize("first, second, value", [(0x7D, 0xF7, 0xEFBE), (0x80, 0x00, 0x0001), (0x00, 0x01, 0x8000)])
def test_x2444m_words_come_d0_first(first, second, value):
    events = [("CS-CHANGE", 0, 1), ("DATA", 0x86 | 3 << 3, 0xFF), ("DATA", 0, first), ("DATA", 0, second),
              ("CS-CHANGE", 1, 0)]
    assert run_stacked("x2444m", events) == [f"READ: 0x3 => 0x{value:04x}"]


@pytest.mark.parametrize("data, tail", [([0x0A, 0x00, 0x12, 0x34, 0x56, 0x78], ["12", "34", "56", "78"]),
                                        ([0x08, 0x00], [])])
def test_sipi_answers_carry_no_address(data, tail):
    crc = binascii.crc_hqx(bytes(data), 0xFFFF)
    frame = data + [crc >> 8, crc & 0xFF]
    instance = SIGROK_ALL["sipi"].cls()
    instance.out_ann = 0
    texts = []
    instance.put = lambda ss, es, out, item: texts.append(item[1][0])
    instance.decode(0, 1, [(8 * i, 8 * i + 8, value) for i, value in enumerate(frame)])
    assert texts[3:] == tail + ["CRC OK"]


@pytest.mark.parametrize("end, expected", [(1, "Stop bit"), (0, "Stop bit missing")])
def test_lfast_frame_ends_with_a_stop_bit(end, expected):
    bits = [0xA84B >> i & 1 for i in range(15, -1, -1)] + [0x28 >> i & 1 for i in range(7, -1, -1)]
    for value in (0xDE, 0xAD, 0xBE, 0xEF):
        bits += [value >> i & 1 for i in range(7, -1, -1)]
    levels = np.repeat(np.array([0] * 20 + bits + [end] + [0] * 60, dtype=np.uint8), 10)
    result = SIGROK_ROOTS["lfast"].decode({"data": from_levels(levels, 1e-8)})
    assert [item.text for item in result.annotations if item.kind == "sleep"] == [expected]


@pytest.mark.parametrize("mode, data_bit, pad_bit", [(1, 44, 110), (2, 40, 92), (3, 28, 70), (4, 26, 65)])
def test_pjdl_pad_bits_follow_the_specification(mode, data_bit, pad_bit):
    def sync():
        return [1] * pad_bit + [0] * data_bit

    levels = [0] * 2000 + sync() * 3
    for value in (0x2A, 0xFF, 0x80, 0x00):
        levels += sync()
        for i in range(8):
            levels += [value >> i & 1] * data_bit
    levels = np.repeat(np.array(levels + [0] * 2000, dtype=np.uint8), 4)
    result = SIGROK_ROOTS["pjdl"].decode({"data": from_levels(levels, 0.25e-6)}, mode=mode)
    assert [item.text for item in result.annotations if item.kind == "frame_bytes"] == ["INIT 2a ff 80 00"]


def i2c_texts(name, transactions):
    signal = siggen.i2c(transactions, 100000)
    result = SIGROK_ROOTS["i2c"].decode(traces_of(signal), srd_stack=[(name, {})])
    assert not result.notes, result.notes
    return [item.text for item in result.annotations]


def has_text(texts, part):
    return any(part in text for text in texts)


@pytest.mark.parametrize("name, transactions, expected", [
    ("tca6408a", [(0x20, False, b"\x04\x00")], "Unknown register 0x04"),
    ("ltc26x7", [(0x10, False, b"\x7F\x12\x34")], "Reserved"),
    ("ltc26x7", [(0x0C, False, b"\x3F\x12\x34")], "Not an LTC26x7 address"),
    ("eeprom24xx", [(0x50, True, b"\x05\x06")], "Sequential current address read (2 bytes): 05 06"),
    ("eeprom24xx", [(0x50, True, b"\x05")], "Current address read: 05"),
    ("atsha204a", [(0x64, False, b"\x03\x03\x00\x00")], "Frame too short")])
def test_i2c_devices(name, transactions, expected):
    texts = i2c_texts(name, transactions)
    assert has_text(texts, expected), texts


@pytest.mark.parametrize("register, value, expected", [
    (0x05, 0xAA, "Unknown register 0x05: 0xAA"), (0x00, 0x80, "XOUT: -128"), (0x01, 0x7F, "YOUT: 127")])
def test_mxc6225xu_registers(register, value, expected):
    texts = run_stacked("mxc6225xu", [
        ("START", None), ("ADDRESS WRITE", 0x15), ("ACK", None), ("DATA WRITE", register), ("ACK", None),
        ("START REPEAT", None), ("ADDRESS READ", 0x15), ("ACK", None), ("DATA READ", value), ("STOP", None)])
    assert expected in texts


def test_ds1307_shows_the_date_only_for_a_whole_date():
    clock = b"\x00\x56\x34\x12\x07\x21\x11\x26"
    texts = i2c_texts("ds1307", [(0x68, False, clock), (0x68, False, b"\x07\x10"), (0x68, False, b"\x08\x55")])
    assert sum("Written date/time" in text for text in texts) == 1


def test_nunchuk_reads_start_at_the_first_register():
    read = (0x52, True, b"\x10\x20\x30\x40\x50\x03")
    texts = i2c_texts("nunchuk", [(0x52, False, b"\x40\x00"), read, read])
    assert sum("Analog stick X position: 0x10" in text for text in texts) == 2


def test_xfp_follows_the_memory_address():
    texts = i2c_texts("xfp", [(0x50, False, b"\x60"), (0x50, True, b"\x23\x00" + bytes(12))])
    assert has_text(texts, "Module temperature: 35.0 C"), texts
    texts = i2c_texts("xfp", [(0x50, False, b"\x62"), (0x50, True, b"\x23\x00" + bytes(12))])
    assert not has_text(texts, "Module temperature"), texts


def spi_result(name, mosi, miso=None, **options):
    signal = siggen.spi([list(word) for word in mosi], 1000000)
    traces = traces_of(signal)
    if miso:
        second = siggen.spi([list(word) for word in miso], 1000000)
        traces["miso"] = from_levels(second.lines["mosi"], 1 / second.sample_rate)
        select = np.zeros_like(second.lines["mosi"])
        select[:64] = select[-8:] = 1
        traces["cs"] = from_levels(select, 1 / second.sample_rate)
    return SIGROK_ROOTS["spi"].decode(traces, srd_stack=[(name, options)])


@pytest.mark.parametrize("name, mosi, expected", [
    ("nrf905", b"\x8F\xFF", "PA_PWR = 3 (+10 dBm) | HFREQ_PLL = 1 (868 / 915 MHz) | CHN = 511"),
    ("rfm12", b"\xA6\x40", "F = 1600.0000"), ("rfm12", b"\xCE\xD4", "Pattern: 0x2DD4"),
    ("sdcard_spi", b"\x49\x00\x00\x00\x00\xAF", "CMD9")])
def test_spi_devices(name, mosi, expected):
    result = spi_result(name, [mosi], [bytes(len(mosi))])
    assert not result.notes, result.notes
    assert has_text([item.text for item in result.annotations], expected)


@pytest.mark.parametrize("chip, device_id, expected", [
    ("macronix_mx25l1605d", b"\xC2\x20\x15", "MX25L1605D"), ("macronix_mx25l3205d", b"\xC2\x20\x16", "MX25L3205D")])
def test_spiflash_names_the_device_from_all_id_bytes(chip, device_id, expected):
    result = spi_result("spiflash", [b"\x9F\x00\x00\x00"], [b"\x00" + device_id], chip=chip)
    assert not result.notes, result.notes
    assert has_text([item.text for item in result.annotations], f"Device = Macronix {expected}")


@pytest.mark.parametrize("name", ["cc1101", "nrf905", "st25r39xx_spi"])
def test_missing_spi_line_is_named(name):
    notes = spi_result(name, [b"AB"]).notes
    assert has_text(notes, "ChannelError") and not has_text(notes, "NameError"), notes


def test_lfast_header_and_payload_of_zeros():
    bits = [0xA84B >> i & 1 for i in range(15, -1, -1)] + [0] * 8 + [0] * 8
    levels = np.repeat(np.array([0] * 20 + bits + [1] + [0] * 60, dtype=np.uint8), 10)
    result = SIGROK_ROOTS["lfast"].decode({"data": from_levels(levels, 1e-8)})
    texts = [item.text for item in result.annotations]
    assert has_text(texts, "PING") and has_text(texts, "Stop bit"), texts


def lpc_levels(clocks):
    """LFRAME#, LCLK and LAD0-LAD3 of (lframe, lad) pairs, LAD set at the falling clock edge."""
    keys = ("lframe", "lclk", "lad0", "lad1", "lad2", "lad3")
    rows = [(1, 0, 15), (1, 1, 15)] * 2
    for lframe, lad in clocks:
        rows += [(lframe, 0, lad), (lframe, 1, lad)]
    rows += [(1, 0, 15), (1, 1, 15)] * 3
    table = [(f, c) + tuple(lad >> i & 1 for i in range(4)) for f, c, lad in rows]
    return {key: from_levels(np.repeat(np.array(column, dtype=np.uint8), 8), 1e-8)
            for key, column in zip(keys, zip(*table))}


@pytest.mark.parametrize("fields, expected", [
    ([0x2, 0, 0, 8, 0, 0xA, 0x5, 15, 15, 0, 15, 15], ["Cycle type: I/O write", "Address: 0x0080", "DATA: 0x5a"]),
    ([0x0, 0, 0, 6, 0, 15, 15, 5, 5, 0, 0xA, 0xF, 15, 15], ["Cycle type: I/O read", "Address: 0x0060", "DATA: 0xfa"]),
    ([0x4] + [0xF] * 7 + [0x0, 15, 15, 6, 0, 0x1, 0x2, 15, 15],
     ["Cycle type: Memory read", "Address: 0xfffffff0", "DATA: 0x21"])])
def test_lpc_cycles(fields, expected):
    result = SIGROK_ROOTS["lpc"].decode(lpc_levels([(0, 0)] + [(1, lad) for lad in fields]))
    texts = [item.text for item in result.annotations]
    assert [text for text in texts if text.startswith(("Cycle type", "Address", "DATA"))] == expected


def xy2_levels(frames):
    rows = [(0, 1, 0)] * 8
    for bits in frames:
        for i, bit in enumerate(bits):
            rows += [(1, int(i < 19), bit)] * 8 + [(0, int(i < 19), bit)] * 8
    rows += [(1, 0, 0)] * 8
    return {key: from_levels(np.array(column, dtype=np.uint8), 1e-7)
            for key, column in zip(("clk", "sync", "data"), zip(*rows))}


@pytest.mark.parametrize("position", [0, 1, -1, 131071, -131072])
def test_xy2_100_position_has_18_bits(position):
    bits = [1] + [position >> i & 1 for i in range(17, -1, -1)]
    bits.append(1 - sum(bits) % 2)
    result = SIGROK_ROOTS["xy2-100"].decode(xy2_levels([bits]))
    assert str(position) in [item.text for item in result.annotations], [i.text for i in result.annotations]


@pytest.mark.parametrize("pin", ["1", "1234", "1234567890123456"])
def test_pan1321_pin_of_any_length(pin):
    line = f"AT+JSEC=4,1,04,{len(pin):02d},{pin}\r\n".encode()
    texts = run_stacked("pan1321", [["DATA", 1, (byte, [])] for byte in line])
    assert has_text(texts, f'PIN to "{pin}"'), texts


def test_every_changed_decoder_names_its_change_and_is_listed():
    root = Path(__file__).resolve().parent.parent
    folder = root / "mhodecode" / "srd_decoders"
    changed = sorted({path.parent.name for path in folder.glob("*/*.py")
                      if "for MHO900 Logic Analyzer" in path.read_text(encoding="utf-8")})
    assert len(changed) > 10
    for file in ("README.md", "README.pl.md", "mhodecode/gui.py", "mhodecode/i18n_pl.py"):
        text = (root / file).read_text(encoding="utf-8")
        assert not [name for name in changed if name not in text], file


@pytest.mark.parametrize("entry", [True, False])
def test_swim_entry_low_is_not_a_synchronization_frame(entry):
    rate = 8_000_000  # one sample per SWIM clock
    levels = [1] * 200
    if entry:
        levels += [0] * 128 + ([1] * 4000 + [0] * 4000) * 4 + ([1] * 2000 + [0] * 2000) * 4 + [1] * 200
    levels += [0] * 128 + [1] * 400
    result = SIGROK_ROOTS["swim"].decode({"swim": from_levels(np.array(levels, dtype=np.uint8), 1 / rate)})
    texts = [item.text.lower() for item in result.annotations if item.kind == "enterseq"]
    assert texts == (["enter sequence"] if entry else []) + ["synchronization frame"]


@pytest.mark.parametrize("packet, before, state, address", [
    ([0x41], 0x1000, "arm", 0x1080), ([0x7F], 0x1000, "thumb", 0x107E),
    ([0x81, 0x80, 0x80, 0x80, 0x0C], 0, "arm", 0x80000000),
    ([0x81, 0x80, 0x80, 0x80, 0x1F], 0, "arm", 0xF0000000)])
@pytest.mark.parametrize("encoding", ["alternative", "original"])
def test_etmv3_branch_addresses(packet, before, state, address, encoding):
    result = decoder_module("arm_etmv3").parse_branch_addr(packet, before, state, encoding)
    assert result[0] == address


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

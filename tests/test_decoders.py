"""Decoder tests on synthetic signals, including noise and non-ideal analog levels."""
import numpy as np
import pytest

from mhodecode import siggen
from mhodecode.decoders import SIGROK_ROOTS
from mhodecode.siggen import crc15
from mhodecode.scope import Waveform
from mhodecode.signal import digitize, from_levels

RNG = np.random.default_rng(1234)


def analog(levels, noise=0.05, swing=3.3):
    """0/1 levels to volts with noise and a first-order rise, as seen on a real input."""
    volts = np.asarray(levels, dtype=float) * swing
    kernel = np.ones(3) / 3
    volts = np.convolve(volts, kernel, mode="same")
    volts[0], volts[-1] = levels[0] * swing, levels[-1] * swing
    return volts + RNG.normal(0, noise, len(volts))


def traces_of(signal, rename=None):
    dt = 1 / signal.sample_rate
    rename = rename or {}
    return {rename.get(key, key): digitize(analog(levels), dt, 0.0) for key, levels in signal.lines.items()}


def values(result, kinds=None):
    return [a.value for a in result.annotations if not a.detail and (kinds is None or a.kind in kinds)]


def errors(result):
    return [a for a in result.annotations if a.error]


# ---- signal layer -----------------------------------------------------------
def test_hysteresis_ignores_noise_near_threshold():
    levels = np.r_[np.zeros(100), np.ones(100), np.zeros(100)]
    volts = levels * 3.3
    volts[50:60] = 0.5
    volts[150:160] = 2.8
    trace = digitize(volts, 1e-6, 0.0)
    assert trace.edges == [100, 200]
    assert trace.initial == 0


def test_constant_input_gives_no_edges():
    trace = digitize(np.full(1000, 3.3) + RNG.normal(0, 0.001, 1000), 1e-6, 0.0)
    assert trace.edges == [] and trace.initial == 1
    trace = digitize(RNG.normal(0, 0.001, 1000), 1e-6, 0.0)
    assert trace.edges == [] and trace.initial == 0


# ---- the signals of this program read by the sigrok decoders ---------------------
def hidden_bytes(result, row):
    return bytes(item.value for item in result.annotations if item.row == row and item.hidden)


@pytest.mark.parametrize("parity", ["none", "even", "odd"])
def test_uart_signal_is_read(parity):
    signal = siggen.uart(b"Hello", 115200, 16, 8, parity, 1)
    result = SIGROK_ROOTS["uart"].decode(traces_of(signal), baudrate=115200, parity=parity)
    assert hidden_bytes(result, "uart:bin:tx") == b"Hello"


def test_i2c_signal_is_read():
    result = SIGROK_ROOTS["i2c"].decode(traces_of(siggen.i2c([(0x50, False, bytes([0x12, 0x34]))], 100000)))
    assert hidden_bytes(result, "i2c:bin:data-write") == bytes([0x12, 0x34])


@pytest.mark.parametrize("mode", [0, 1, 2, 3])
def test_spi_signal_is_read(mode):
    signal = siggen.spi([[0xA5, 0x5A]], 1000000, 16, mode)
    result = SIGROK_ROOTS["spi"].decode(traces_of(signal), cpol=mode >> 1, cpha=mode & 1)
    assert hidden_bytes(result, "spi:bin:mosi") == bytes([0xA5, 0x5A])


def test_i2s_signal_is_read():
    signal = siggen.i2s([(0x1234, 0x5678)], 1000000)
    names = {"bclk": "sck", "sd": "sd", "ws": "ws", "sck": "sck"}
    result = SIGROK_ROOTS["i2s"].decode(traces_of(signal, names))
    assert result.annotations


@pytest.mark.parametrize("data", [b"", bytes([0x11]), bytes(range(0x11, 0x19))])
def test_can_signal_gives_its_payload(data):
    trace = traces_of(siggen.can([(0x123, data, False)], 500000))["can"]
    result = SIGROK_ROOTS["can"].decode({"can_rx": trace}, nominal_bitrate=500000)
    assert hidden_bytes(result, "can:bin:data") == data
    assert not errors(result)


@pytest.mark.parametrize("length", [1, 8, 12, 64])
def test_can_fd_signal_gives_its_payload(length):
    data = bytes(range(length))
    signal = siggen.can_fd([(0x123, data, False)], 500000, 2000000, 8, True)
    result = SIGROK_ROOTS["can"].decode({"can_rx": traces_of(signal)["can"]}, nominal_bitrate=500000, fast_bitrate=2000000)
    assert hidden_bytes(result, "can:bin:data") == data


def test_crc15_known_vector():
    # CRC-15/CAN of the bytes of "123456789" is 0x059E (reveng catalogue).
    message = b"123456789"
    bits = [(byte >> index) & 1 for byte in message for index in range(7, -1, -1)]
    assert crc15(bits) == 0x059E

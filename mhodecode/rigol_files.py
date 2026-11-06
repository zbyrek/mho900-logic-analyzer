"""The memory files of the MHO900: .bin (RG03) and .wfm, read back without the instrument.

The programming guide does not describe either layout; this comes from files saved on firmware
00.01.00 with known signals on CH1, CH2 and the digital channels.

BIN holds the only fast copy of the digital channels. Each record has a 140-byte header and a
16-byte data header. Analog records are 32-bit floats in volts. The "LA" record has one 32-bit
word per sample, with D0 to D15 in bits 0 to 15; its words are identical to the columns of the CSV
file. Its header overstates things: the data size field and the file size field assume one word
per analog sample, but the record has one word every 1, 2 or 4 analog samples, and its sample
interval field is the analog one. The ratio follows the channel mode, in which the channels share
the ADC and the memory (measured: 4 channels 1, 2 channels 2, 1 or no channels 4; the same at any
sample rate from 5 MSa/s to 2 GSa/s), so it is taken from the file and never assumed.

WFM holds 16-bit samples of the analog channels only, after a chain of zlib-compressed blocks with
the settings. It has no digital data and no sample rate: files saved at 10 k and 100 k points on
the same time base differ in their settings only by the index of the depth. The samples are
interleaved in 1, 2 or 4 slots, which follow the channel mode and not the channels shown: CH2 alone
takes 2 slots, in the second one. CH1 and CH2 were checked against BIN; nothing was connected to CH3
and CH4, so their slots at 4 slots are assumed, and with fewer slots they are left out.
"""
from __future__ import annotations

import struct
import zlib
from dataclasses import dataclass

import numpy as np

from .i18n import tr
from .scope import DIGITAL_CHANNELS, Waveform, digital_channel

BIN_MAGIC = b"RG03"
BIN_FILE_HEADER = 16
BIN_ANALOG, BIN_LOGIC = 1, 6
BIN_WAVE_HEADER = struct.Struct("<IIIIIfdddii")  # size, type, buffers, points, count, x range, x display, x inc, x origin, units
BIN_DATA_HEADER = struct.Struct("<IHHQ")  # size, type, bytes per point, data size
BIN_LABEL_OFFSET, BIN_LABEL_SIZE = 112, 16

WFM_SCAN_LIMIT = 8192  # the settings end well before this (about 3 kB)
WFM_MAGIC = b"\x02\x00\x00\x00"
WFM_ZLIB = b"\x78\x9c"
WFM_DATA_HEADER = 20
WFM_ZERO_CODE = 32768
WFM_VOLTS_PER_CODE_PER_SCALE = 8 / 60000  # 8 divisions span 60000 codes (fits BIN floats to 50 nV rms)
NANO = 1e-9
WFM_SCALE_OFFSET, WFM_OFFSET_OFFSET = 1, 38
WFM_PROBE_FROM_END = 28  # scale and offset are at the input, so they are multiplied by the probe ratio
ANALOG_CHANNELS = (1, 2, 3, 4)


@dataclass
class MemoryFile:
    waves: dict  # channel number (digital ones from digital_channel()) -> Waveform
    digital_factor: int = 0  # analog samples per digital sample; 0 without digital data


def _analog_channel(label: bytes) -> int | None:
    text = label.split(b"\0")[0].decode("ascii", "replace")
    return int(text[2:]) if text.startswith("CH") and text[2:].isdigit() else None


def parse_bin(data, wanted_digital=()) -> MemoryFile:
    """Analog channels, and of the digital ones those in wanted_digital or with any edge."""
    buffer = memoryview(data)
    if bytes(buffer[:4]) != BIN_MAGIC:
        raise ValueError(tr("To nie jest plik BIN oscyloskopu."))
    waves, factor = {}, 0
    position = BIN_FILE_HEADER
    while position < len(buffer):
        if position + BIN_WAVE_HEADER.size > len(buffer):
            raise ValueError(tr("Plik BIN jest ucięty."))
        header_size, kind, _buffers, points, _count, _range, _display, step, origin, *_units = BIN_WAVE_HEADER.unpack_from(buffer, position)
        label = bytes(buffer[position + BIN_LABEL_OFFSET:position + BIN_LABEL_OFFSET + BIN_LABEL_SIZE])
        position += header_size
        data_header, _data_type, width, declared = BIN_DATA_HEADER.unpack_from(buffer, position)
        position += data_header
        size = points * width if kind == BIN_LOGIC else declared
        if position + size > len(buffer):
            raise ValueError(tr("Plik BIN jest ucięty."))
        if kind == BIN_ANALOG:
            channel = _analog_channel(label)
            if channel is not None:
                waves[channel] = Waveform(channel, np.frombuffer(buffer, "<f4", points, position), step, origin)
        elif kind == BIN_LOGIC:
            # declared / width is the analog sample count this record really spans.
            factor = max(1, round(declared / width / max(points, 1)))
            words = np.frombuffer(buffer, "<u4", points, position)
            toggling = int(np.bitwise_or.reduce(words ^ words[0])) if points else 0
            for number in range(DIGITAL_CHANNELS):
                channel = digital_channel(number)
                if channel in wanted_digital or toggling >> number & 1:
                    levels = ((words >> number) & 1).astype(np.uint8)
                    waves[channel] = Waveform(channel, levels, step * factor, origin, digital=True)
        position += size
    return MemoryFile(waves, factor)


def _wfm_data_start(data):
    """The data header repeats the point count, and the samples then fill the file exactly.
    Returns (position of the header, points per channel, channels stored)."""
    for position in range(0, min(len(data), WFM_SCAN_LIMIT) - WFM_DATA_HEADER):
        points, again = struct.unpack_from("<II", data, position + 4)
        if points and points == again:
            for channels in ANALOG_CHANNELS:
                if len(data) - position - WFM_DATA_HEADER == points * 2 * channels:
                    return position, points, channels
    raise ValueError(tr("Plik WFM ma nieznany układ."))


def _wfm_blocks(settings):
    """The compressed blocks of the part of the file before the samples."""
    position, blocks = 0, []
    while True:
        position = settings.find(WFM_ZLIB, position)
        if position < 0:
            return blocks
        stream = zlib.decompressobj()
        try:
            content = stream.decompress(settings[position:])
        except zlib.error:
            position += len(WFM_ZLIB)
            continue
        if not stream.eof:
            position += len(WFM_ZLIB)
            continue
        blocks.append(content)
        position = len(settings) - len(stream.unused_data)


def _wfm_channels(blocks):
    """channel -> (volts per division, offset in volts) of the channels shown when it was saved."""
    found = {}
    for content in blocks:
        for channel in ANALOG_CHANNELS:
            marker = b"$\x03\x00\x00\x00CH%d" % channel
            if marker in content and content[0]:
                scale, = struct.unpack_from("<q", content, WFM_SCALE_OFFSET)
                offset, = struct.unpack_from("<q", content, WFM_OFFSET_OFFSET)
                probe, = struct.unpack_from("<q", content, len(content) - WFM_PROBE_FROM_END)
                found[channel] = (scale * probe * NANO * NANO, offset * probe * NANO * NANO)
    return found


def parse_wfm(data, dt: float) -> MemoryFile:
    """Analog channels only. The file does not hold the sample interval, so the caller supplies it.
    Time zero is taken as the middle of the record, where the trigger is without a time offset."""
    data = bytes(data)
    if data[:4] != WFM_MAGIC:
        raise ValueError(tr("To nie jest plik WFM oscyloskopu."))
    start, points, stored = _wfm_data_start(data)
    channels = {channel: levels for channel, levels in _wfm_channels(_wfm_blocks(data[:start])).items() if channel <= stored}
    if not channels:
        raise ValueError(tr("Plik WFM ma nieznany układ."))
    samples = np.frombuffer(data, "<u2", points * stored, start + WFM_DATA_HEADER)
    waves = {}
    for channel, (scale, offset) in channels.items():
        volts = (samples[channel - 1::stored].astype(np.float64) - WFM_ZERO_CODE) * scale * WFM_VOLTS_PER_CODE_PER_SCALE - offset
        waves[channel] = Waveform(channel, volts.astype(np.float32), dt, -points * dt / 2, volts_per_div=scale)
    return MemoryFile(waves)

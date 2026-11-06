"""Presents a libsigrokdecode decoder, with the decoders stacked on it, as a Decoder of this program."""
from __future__ import annotations

import re

from .. import abort, sigrokdecode
from ..i18n import tr
from ..srd_loader import DecoderInfo
from .base import Annotation, DecodeResult, Decoder, ParamSpec, SignalSpec

# Annotation classes and rows carry no severity of their own, so it is read from their names.
_ERROR_NAME = re.compile(r"(^|[-_])(err|error|warn|warning)s?($|[-_])", re.IGNORECASE)
_BIT_ROW = re.compile(r"(^|[-_])bits?($|[-_])", re.IGNORECASE)
# The CAN decoder gives no binary output, while its payload is needed to compare with expected data.
_CAN_DATA_BYTE = re.compile(r"Data byte \d+: 0x([0-9a-f]{2})")
# Which classes carry data, as read from their names. A decoder with none of them is not divided.
_DATA_NAME = re.compile(r"(^|[-_])(data|payload|bytes?|values?|words?|samples?|chars?|text|packet|transfer|left|right)($|[-_])",
                        re.IGNORECASE)
# Decoders whose source shows that these classes print plain hex, without a prefix.
_HEX_CLASSES = {
    "i2c": {"address-read", "address-write", "data-read", "data-write"},
    "spi": {"miso-data", "mosi-data", "miso-transfer", "mosi-transfer"},
    "i2s": {"left", "right"},
}
_UART_VALUES = {"rx-data", "tx-data", "rx-packet", "tx-packet"}
_HEX_TOKEN = re.compile(r"(?<![0-9A-Za-z])[0-9A-Fa-f]{2,}(?![0-9A-Za-z])")
_UART_UNPRINTABLE = re.compile(r"^\[([0-9A-F]+)\]$")  # how the UART decoder writes a control character
_UART_RATES = (1200, 2400, 4800, 9600, 14400, 19200, 38400, 57600, 115200, 230400, 460800, 921600, 1000000, 2000000, 3000000)
_CAN_RATES = (10000, 20000, 50000, 100000, 125000, 250000, 500000, 800000, 1000000)
_CAN_FD_RATES = (1000000, 2000000, 4000000, 5000000, 8000000)
_ISO14443_RATES = (106000, 212000, 424000, 848000)
TYPICAL_RATES = {("uart", "baudrate"): _UART_RATES, ("can", "nominal_bitrate"): _CAN_RATES, ("can", "fast_bitrate"): _CAN_FD_RATES,
                 ("miller", "baudrate"): _ISO14443_RATES}
_SPIN_LIMIT = 2_000_000_000
_FLOAT_DECIMALS = 6


def option_param(option: dict, decoder_id: str = "") -> ParamSpec:
    default = option["default"]
    values = option.get("values")
    if values and default not in values:  # upstream am230x: the default is not among its values
        values = (default, *values)
    return ParamSpec(
        key=option["id"], label=option.get("desc", option["id"]), default=default,
        choices=tuple((value, str(value)) for value in values) if values else None,
        minimum=-_SPIN_LIMIT, maximum=_SPIN_LIMIT, decimals=_FLOAT_DECIMALS, rates=TYPICAL_RATES.get((decoder_id, option["id"]), ()))


def _is_data(name: str) -> bool:
    return bool(_DATA_NAME.search(name)) and not _BIT_ROW.search(name)


def _with_hex_prefix(text: str) -> str:
    return _HEX_TOKEN.sub(lambda token: f"0x{token.group(0).upper()}", text)


def _as_ascii(text: str) -> str:
    unprintable = _UART_UNPRINTABLE.match(text)
    return f"0x{unprintable.group(1)}" if unprintable else f'"{text}"'


def _value_style(info: DecoderInfo, options: dict) -> dict:
    """Annotation class name -> how its texts are shown: 0x before hex, quotes around ASCII."""
    if info.id == "uart":
        chosen = options.get("format", next(option["default"] for option in info.options if option["id"] == "format"))
        style = {"hex": _with_hex_prefix, "ascii": _as_ascii}.get(chosen)
        return dict.fromkeys(_UART_VALUES, style) if style else {}
    return dict.fromkeys(_HEX_CLASSES.get(info.id, ()), _with_hex_prefix)


def _row_table(info: DecoderInfo, prefix: str):
    """For each annotation class: (row key, row label, shown in the table). Classes outside
    annotation_rows get a row of their own, as PulseView does."""
    table = {}
    for row_id, row_label, classes in info.annotation_rows:
        for cls in classes:
            table[cls] = (f"{prefix}:{row_id}", f"{info.name}: {row_label}", not _BIT_ROW.search(row_id))
    for cls, annotation in enumerate(info.annotations):
        if cls not in table:
            table[cls] = (f"{prefix}:{annotation[0]}", f"{info.name}: {annotation[1]}", not _BIT_ROW.search(annotation[0]))
    return table


class SigrokDecoder(Decoder):
    def __init__(self, info: DecoderInfo, registry: dict[str, DecoderInfo]):
        self.info = info
        self.registry = registry
        self.name = info.name
        self.signals = tuple(SignalSpec(spec["id"], spec["name"], True) for spec in info.channels) + tuple(
            SignalSpec(spec["id"], spec["name"], False) for spec in info.optional_channels)
        self.params = tuple(option_param(option, info.id) for option in info.options)

    def decode(self, traces, /, *, srd_stack=(), **options) -> DecodeResult:
        if not traces:
            raise ValueError(tr("Przypisz kanały do sygnałów dekodera."))
        abort.checkpoint()
        first = next(iter(traces.values()))
        session = sigrokdecode.Session(first.length, round(1 / first.dt))
        result = DecodeResult()
        chain = [(self.info, options)] + [(self.registry[pd_id], values) for pd_id, values in srd_stack]
        tables, prefixes, styles, divided = {}, {}, {}, {}
        for position, (info, values) in enumerate(chain):
            count = sum(1 for other, _values in chain[:position] if other.id == info.id)
            prefix = f"{info.id}#{count + 1}" if count else info.id
            try:
                instance = session.add(info.cls, values, traces if position == 0 else None)
            except Exception as exc:  # a decoder's constructor may raise its own classes
                raise ValueError(tr("Dekoder {decoder}: {error}", decoder=info.id, error=exc)) from exc
            tables[id(instance)] = _row_table(info, prefix)
            prefixes[id(instance)] = prefix
            styles[id(instance)] = _value_style(info, values)
            divided[id(instance)] = any(_is_data(annotation[0]) for annotation in info.annotations)
            for _row, (key, label, _table) in sorted(tables[id(instance)].items()):
                result.rows.setdefault(key, label)
        t0, dt = first.t0, first.dt

        def on_annotation(instance, start, end, cls, texts):
            key, _label, in_table = tables[id(instance)][cls]
            name = type(instance).annotations[cls][0]
            text = texts[0] if texts else ""
            style = styles[id(instance)].get(name)
            shown = [style(form) for form in texts] if style else list(texts)
            result.annotations.append(Annotation(
                t0 + start * dt, t0 + end * dt, key, name, shown[0] if shown else "", error=text if _ERROR_NAME.search(name) else "",
                detail=not in_table, texts=tuple(shown), control=divided[id(instance)] and not _is_data(name)))
            byte = _CAN_DATA_BYTE.match(text) if type(instance).id == "can" and name == "data" else None
            if byte:
                result.annotations.append(Annotation(t0 + start * dt, t0 + end * dt, f"{prefixes[id(instance)]}:bin:data", "DATA",
                                                     byte.group(1).upper(), int(byte.group(1), 16), detail=True, hidden=True))

        def on_binary(instance, start, end, cls, data):
            # One value per byte for the comparison with expected data.
            key = f"{prefixes[id(instance)]}:bin:{type(instance).binary[cls][0]}"
            for byte in data:
                result.annotations.append(Annotation(t0 + start * dt, t0 + end * dt, key, "DATA", f"{byte:02X}",
                                                     byte, detail=True, hidden=True))

        session.on_annotation, session.on_binary = on_annotation, on_binary
        session.run()
        result.annotations.sort(key=lambda item: item.start)
        for pd_id, exc in session.exceptions:
            result.notes.append(tr("Dekoder {decoder} przerwał pracę: {error}: {detail}",
                                   decoder=pd_id, error=type(exc).__name__, detail=exc))
        for problem in sorted(set(session.problems)):
            result.notes.append(tr("Dekoder zgłosił nieprawidłowe dane: {problem}", problem=problem))
        return result

"""Trigger settings of the MHO900, described as data; the scope tab builds its forms from this.

Commands and ranges follow chapter 3.27 of the MHO900 Programming Guide. I2S, FlexRay and 1553B
triggers need the options MHO900-AUDIOA, MHO900-FlexA and MHO900-AEROA.
"""
from __future__ import annotations

import math
from dataclasses import dataclass

from .i18n import tr
from .scope import channel_name, digital_channel, is_digital

CHOICE, NUMBER, CHECK, HEX, BITS = "choice", "number", "check", "hex", "bits"

# The condition of one channel, as on a trace in PulseView.
NONE, LOW, HIGH, RISING, FALLING, ANY_EDGE = "", "L", "H", "R", "F", "E"
CONDITIONS = (("None", NONE, "–"), ("Low", LOW, "L"), ("High", HIGH, "H"),
              ("Rising edge", RISING, "↑"), ("Falling edge", FALLING, "↓"), ("Either edge", ANY_EDGE, "↕"))
EDGE_SLOPES = {RISING: "POSitive", FALLING: "NEGative", ANY_EDGE: "RFALl"}
PATTERN_CHANNELS = (1, 2, 3, 4)

SOURCES = tuple((f"CH{n}", f"CHANnel{n}") for n in range(1, 5)) + tuple((f"D{n}", f"D{n}") for n in range(16))
POLARITY = (("Positive", "POSitive"), ("Negative", "NEGative"))
SWEEPS = (("Auto", "AUTO"), ("Normal", "NORMal"), ("Single", "SINGle"))


@dataclass(frozen=True)
class Field:
    key: str  # the command after :TRIGger:<mode>:
    label: str
    kind: str
    choices: tuple = ()  # (label, SCPI value) pairs; labels of plain numbers and names stay untranslated
    minimum: float = 0.0
    maximum: float = 0.0
    decimals: int = 0
    suffix: str = ""
    when: tuple = ()  # shown only with these values of the WHEN field (empty: always)
    bits: bool = False  # the reply is a string of 0, 1 and X (don't care) instead of a number
    width: int = 0  # BITS: the field selects a bit, whose value then goes through :CODE
    width_from: str = ""  # BITS: the field of this mode that sets the number of bits, up to width


@dataclass(frozen=True)
class Mode:
    name: str  # SCPI value of :TRIGger:MODE
    label: str
    path: str  # the part of the command between :TRIGger: and the field
    fields: tuple


def _source(key, label, when=()):
    return Field(key, label, CHOICE, SOURCES, when=when)


def _level(key, label, when=()):
    return Field(key, label, NUMBER, minimum=-500.0, maximum=500.0, decimals=3, suffix=" V", when=when)


def _numbers(key, label, values, when=()):
    return Field(key, label, CHOICE, tuple((str(value), str(value)) for value in values), when=when)


EDGE = Mode("EDGE", "Slope", "EDGE", (
    _source("SOURce", "Source"),
    Field("SLOPe", "Slope", CHOICE, (("Rising", "POSitive"), ("Falling", "NEGative"), ("Both", "RFALl"))),
    _level("LEVel", "Level"),
))

_RS232_DATA = ("DATA",)
RS232 = Mode("RS232", "UART (RS232)", "RS232", (
    _source("SOURce", "Source"),
    _level("LEVel", "Level"),
    Field("BAUD", "Speed", NUMBER, minimum=1, maximum=20_000_000, suffix=" bps"),
    Field("POLarity", "Polarity", CHOICE, POLARITY),
    _numbers("WIDTh", "Data bits", (5, 6, 7, 8)),
    _numbers("STOP", "Stop bits", ("1", "1.5", "2")),
    Field("PARity", "Parity", CHOICE, (("None", "NONE"), ("Even", "EVEN"), ("Odd", "ODD"))),
    Field("WHEN", "Condition", CHOICE, (("Start bit", "STARt"), ("Frame error", "ERRor"),
                                      ("Parity error", "CERRor"), ("Data", "DATA"))),
    Field("DATA", "Data (hex)", HEX, maximum=0xFF, when=_RS232_DATA),
))

_I2C_ADDRESS = ("ADDRess", "ADATa")
_I2C_DATA = ("DATA", "ADATa")
I2C = Mode("IIC", "I2C", "IIC", (
    _source("SCL", "SCL source"),
    _level("CLEVel", "SCL level"),
    _source("SDA", "SDA source"),
    _level("DLEVel", "SDA level"),
    Field("WHEN", "Condition", CHOICE, (("Start", "STARt"), ("Repeated start", "RESTart"), ("Stop", "STOP"),
                                      ("Missing ACK", "NACKnowledge"), ("Address", "ADDRess"), ("Data", "DATA"),
                                      ("Address and data", "ADATa"))),
    _numbers("AWIDth", "Address bits", (7, 8, 10), when=_I2C_ADDRESS),
    Field("ADDRess", "Address (hex)", HEX, maximum=0x3FF, when=_I2C_ADDRESS),
    Field("DIRection", "Direction", CHOICE, (("Write", "WRITe"), ("Read", "READ"), ("Both", "RWRite")), when=_I2C_ADDRESS),
    Field("DBYTes", "Data bytes", NUMBER, minimum=1, maximum=5, when=_I2C_DATA),
    Field("DATA", "Data (hex)", HEX, maximum=2 ** 40 - 1, when=_I2C_DATA),
))

_SPI_CS = ("CS",)
SPI = Mode("SPI", "SPI", "SPI", (
    _source("SCL", "Clock source"),
    _level("CLEVel", "Clock level"),
    Field("SLOPe", "Sampling", CHOICE, (("Rising edge", "POSitive"), ("Falling edge", "NEGative"))),
    _source("SDA", "Data source"),
    _level("DLEVel", "Data level"),
    Field("WHEN", "Condition", CHOICE, (("CS active", "CS"), ("Clock idle", "TIMeout"))),
    _source("CS", "CS source", when=_SPI_CS),
    _level("SLEVel", "CS level", when=_SPI_CS),
    Field("MODE", "CS active state", CHOICE, (("Low", "LOW"), ("High", "HIGH")), when=_SPI_CS),
    Field("TIMeout", "Idle time", NUMBER, minimum=8e-9, maximum=10.0, decimals=9, suffix=" s", when=("TIMeout",)),
    Field("WIDTh", "Data bits", NUMBER, minimum=4, maximum=32),
    Field("DATA", "Data (hex)", HEX, maximum=2 ** 32 - 1),
))

_CAN_ID = ("IDRemote", "IDFRame", "IDData")
_CAN_DATA = ("DAT", "IDData")
# The guide lists DATaframe and ERFRame, but firmware 00.01.00 takes only the short forms (-222 otherwise).
CAN = Mode("CAN", "CAN", "CAN", (
    _source("SOURce", "Source"),
    Field("STYPe", "Signal type", CHOICE, (("CAN_H", "H"), ("CAN_L", "L"), ("RX/TX", "RXTX"), ("Differential", "DIFFerential"))),
    _level("LEVel", "Level"),
    Field("BAUD", "Speed", NUMBER, minimum=10_000, maximum=5_000_000, suffix=" bps"),
    Field("SPOint", "Sample point", NUMBER, minimum=10, maximum=90, suffix=" %"),
    Field("WHEN", "Condition", CHOICE, (("Start of frame", "SOF"), ("End of frame", "EOF"), ("Remote frame with ID", "IDRemote"),
                                      ("Overload frame", "OVERload"), ("Frame with ID", "IDFRame"),
                                      ("Frame with data", "DAT"), ("ID and data", "IDData"), ("Error frame", "ERFR"),
                                      ("ACK error", "ERANswer"), ("CRC error", "ERCHeck"), ("Format error", "ERFormat"),
                                      ("Any error", "ERRandom"), ("Bit stuffing error", "ERBit"))),
    Field("EXTended", "Extended ID (29 bits)", CHECK, when=("IDRemote", "IDFRame")),
    Field("DEFine", "The Data field holds", CHOICE, (("The data", "DATA"), ("ID", "ID")), when=("IDData",)),
    Field("DWIDth", "Data bytes", NUMBER, minimum=1, maximum=8, when=_CAN_DATA),
    Field("DATA", "Data or ID (hex)", HEX, maximum=2 ** 64 - 1, when=_CAN_ID + ("DAT",)),
))

_LIN_ID = ("ID", "IDData")
_LIN_DATA = ("DATA", "IDData")
LIN = Mode("LIN", "LIN", "LIN", (
    _source("SOURce", "Source"),
    _level("LEVel", "Level"),
    Field("STANdard", "Standard", CHOICE, (("LIN 1.x", "1X"), ("LIN 2.x", "2X"), ("Both", "BOTH"))),
    Field("BAUD", "Speed", NUMBER, minimum=1000, maximum=20_000_000, suffix=" bps"),
    Field("SAMPlepoint", "Sample point", NUMBER, minimum=10, maximum=90, suffix=" %"),
    Field("WHEN", "Condition", CHOICE, (("Sync field", "SYNCbreak"), ("ID", "ID"), ("Data", "DATA"), ("ID and data", "IDData"),
                                      ("Sleep frame", "SLEep"), ("Wakeup frame", "WAKeup"), ("Error", "ERRor"))),
    Field("ERRor", "Error type", CHOICE, (("Sync", "SYNC"), ("ID parity", "ID"), ("Checksum", "CHECk")), when=("ERRor",)),
    Field("ID", "ID (hex)", HEX, maximum=63, when=_LIN_ID),
    Field("DATA", "Data (hex)", HEX, maximum=2 ** 64 - 1, when=_LIN_DATA, bits=True),
))

_COMPARISONS = (("Equal", "EQUal"), ("Not equal", "NOTequal"), ("Greater", "GREaterthan"), ("Less", "LESSthan"),
                ("In range", "INRange"), ("Out of range", "OUTRange"))
_RANGES = ("INRange", "OUTRange")
_SINGLE_VALUES = ("EQUal", "NOTequal", "GREaterthan", "LESSthan")
I2S = Mode("IIS", "I2S", "IIS", (
    _source("SOURce:CLOCk", "Clock source"),
    _level("CLEVel", "Clock level"),
    Field("CLOCk:SLOPe", "Sampling", CHOICE, (("Rising edge", "POSitive"), ("Falling edge", "NEGative"))),
    _source("SOURce:WSELect", "WS source"),
    _level("SLEVel", "WS level"),
    _source("SOURce:DATA", "Data source"),
    _level("DLEVel", "Data level"),
    Field("ALIGnment", "Alignment", CHOICE, (("I2S", "IIS"), ("Left-justified", "LJ"), ("Right-justified", "RJ"))),
    Field("WIDTh", "Bits per word", NUMBER, minimum=4, maximum=32),
    Field("UWIDth", "Bits used", NUMBER, minimum=4, maximum=32),
    Field("AUDio", "Audio channel", CHOICE, (("Left", "LEFT"), ("Right", "RIGHt"), ("Any", "EITHer"))),
    Field("WHEN", "Condition", CHOICE, _COMPARISONS),
    Field("DATA", "Data (hex)", HEX, maximum=2 ** 32 - 1, when=_SINGLE_VALUES),
    # The guide relates the index range to UWIDth. As for 1553B, the indices above the bits stand for
    # hex digits and overwrite groups of bits.
    Field("DMIN", "Data from (bits)", BITS, when=_RANGES, width=32, width_from="UWIDth"),
    Field("DMAX", "Data to (bits)", BITS, when=_RANGES, width=32, width_from="UWIDth"),
))

_FLEX_FRAME = ("FRAMe",)
FLEXRAY = Mode("FLEXray", "FlexRay", "FLEXray", (
    _source("SOURce", "Source"),
    _level("LEVel", "Level"),
    Field("BAUD", "Speed", CHOICE, (("2.5 Mb/s", "2500000"), ("5 Mb/s", "5000000"), ("10 Mb/s", "10000000"))),
    Field("CH", "Bus channel", CHOICE, (("A", "A"), ("B", "B"))),
    Field("WHEN", "Condition", CHOICE, (("Frame", "FRAMe"), ("Symbol", "SYMBol"), ("Error", "ERRor"), ("Position", "TSS"))),
    Field("FRAMe", "Frame type", CHOICE, (("Any kind", "ANY"), ("Null", "NULL"), ("Sync frame", "SYNC"), ("Startup", "STAR")), when=_FLEX_FRAME),
    Field("IDCmp", "ID comparison", CHOICE, _COMPARISONS, when=_FLEX_FRAME),
    Field("MINid", "ID from", NUMBER, maximum=1023, when=_FLEX_FRAME),
    Field("MAXid", "ID to", NUMBER, maximum=1023, when=_FLEX_FRAME),
    Field("CYCComp", "Cycle count comparison", CHOICE, _COMPARISONS, when=_FLEX_FRAME),
    Field("MINCy", "Cycle count from", NUMBER, maximum=63, when=_FLEX_FRAME),
    Field("MAXCy", "Cycle count to", NUMBER, maximum=63, when=_FLEX_FRAME),
    Field("SYMBol", "Symbol", CHOICE, (("CAS", "CAS"), ("WUS", "WUS")), when=("SYMBol",)),
    Field("ERRor", "Error type", CHOICE, (("Header CRC", "HEAD"), ("Trailer CRC", "TRAiler"), ("Decoding", "DECode"), ("Any", "ANY")), when=("ERRor",)),
    Field("POS", "Position", CHOICE, (("TSS", "TSS"), ("FSS", "FSS"), ("FES", "FES"), ("DTS", "DTS")), when=("TSS",)),
))

M1553 = Mode("M1553", "MIL-STD-1553B", "M1553", (
    _source("SOURce", "Source"),
    Field("POLarity", "Polarity", CHOICE, POLARITY),
    Field("WINDow", "Level adjustment", CHOICE, (("Upper", "TA"), ("Lower", "TB"), ("Both", "TAB"))),
    _level("ALEVel", "Upper level"),
    _level("BLEVel", "Lower level"),
    Field("WHEN", "Condition", CHOICE, (("Sync", "SYNCbreak"), ("Data word", "DATA"), ("Command word", "CMD"), ("Status word", "STATus"), ("Error", "ERRor"))),
    Field("SYNC", "Sync type", CHOICE, (("Data", "DATA"), ("Command or status", "STATus"), ("Any kind", "ALL")), when=("SYNCbreak",)),
    Field("ERRor", "Error type", CHOICE, (("Sync", "SYNC"), ("Parity", "ERR")), when=("ERRor",)),
    Field("DATComp", "Data comparison", CHOICE, _COMPARISONS, when=("DATA",)),
    # The guide's index ranges run through the binary bits and then the hex digits, which overwrite
    # groups of bits, so these widths are the binary part only (measured on the scope).
    Field("DMIN", "Data from (bits)", BITS, when=("DATA",), width=16),
    Field("DMAX", "Data to (bits)", BITS, when=("DATA",), width=16),
    Field("DRTA", "Terminal address (bits)", BITS, when=("CMD", "STATus"), width=5),
    Field("DBIT", "Remaining bits", BITS, when=("STATus",), width=11),
))

MODES = (EDGE, RS232, I2C, SPI, CAN, LIN, I2S, FLEXRAY, M1553)
HOLDOFF_MODES = (EDGE.name,)  # the Programming Guide rules holdoff out for all the others on this list


def is_source(field: Field) -> bool:
    return field.choices is SOURCES


def is_level(field: Field) -> bool:
    return field.kind == NUMBER and field.suffix == " V"


def source_channel(value: str) -> int:
    """The channel number behind a value of a source field."""
    return digital_channel(int(value[1:])) if value.startswith("D") else int(value.removeprefix("CHANnel"))


def source_of_level(mode: Mode, level: Field) -> Field:
    """Each level belongs to the source field listed before it."""
    return next(field for field in reversed(mode.fields[:mode.fields.index(level)]) if is_source(field))


def symbol(condition: str) -> str:
    return "" if condition == NONE else next(mark for _label, code, mark in CONDITIONS if code == condition)


def allowed_conditions(channel: int) -> tuple:
    """:TRIGger:PATTern:PATTern takes four values, for CH1 to CH4: with more it answers -108 on firmware
    00.01.00, although its reply has 24. So D0 to D15 have only what the Edge trigger offers."""
    return (NONE, *EDGE_SLOPES) if is_digital(channel) else tuple(code for _label, code, _symbol in CONDITIONS)


def conflicts(conditions: dict, channel: int, condition: str) -> set:
    """The channels that cannot keep their condition next to this one. A pattern takes levels on CH1 to CH4
    and one rising or falling edge, while a digital channel or either edge needs the Edge trigger, all alone."""
    others = {other: code for other, code in conditions.items() if other != channel}
    if condition == NONE:
        return set()
    if is_digital(channel) or condition == ANY_EDGE:
        return set(others)
    return {other for other, code in others.items()
            if is_digital(other) or code == ANY_EDGE or (condition in EDGE_SLOPES and code in EDGE_SLOPES)}


def _source_name(channel: int) -> str:
    return channel_name(channel) if is_digital(channel) else f"CHANnel{channel}"


def condition_commands(conditions: dict, levels: dict) -> list[str]:
    """What sets the scope to the channels' conditions. levels: channel -> volts, where it is known."""
    if not conditions:
        return []
    if len(conditions) == 1 and next(iter(conditions.values())) in EDGE_SLOPES:
        channel, condition = next(iter(conditions.items()))
        commands = [":TRIGger:MODE EDGE", f":TRIGger:EDGE:SOURce {_source_name(channel)}",
                    f":TRIGger:EDGE:SLOPe {EDGE_SLOPES[condition]}"]
        if channel in levels:
            commands.append(f":TRIGger:EDGE:LEVel {levels[channel]:.6g}")
        return commands
    pattern = ",".join(conditions.get(channel, "X") for channel in PATTERN_CHANNELS)
    return [":TRIGger:MODE PATTern", f":TRIGger:PATTern:PATTern {pattern}",
            *(f":TRIGger:PATTern:LEVel {_source_name(channel)},{levels[channel]:.6g}" for channel in sorted(conditions) if channel in levels)]


def short_form(mnemonic: str) -> str:
    """The form the instrument answers with: the capitals and digits of a SCPI word."""
    return "".join(char for char in mnemonic if char.isupper() or char.isdigit())


def same_choice(reply: str, value: str) -> bool:
    """Replies are abbreviated (POS, CHAN1) or numbers in another notation (8.000000E0)."""
    reply = reply.strip()
    if reply.upper() in (value.upper(), short_form(value)):
        return True
    try:
        return math.isclose(float(reply), float(value))
    except ValueError:
        return False


def find_choice(reply: str, choices) -> int | None:
    for position, (_label, value) in enumerate(choices):
        if same_choice(reply, value):
            return position
    return None


def parse_integer(reply: str, bits: bool = False) -> int | None:
    """Whole numbers come back as plain digits or in scientific notation; None: don't care bits."""
    reply = reply.strip()
    if bits:
        return None if "X" in reply.upper() else int(reply, 2)
    try:
        return int(reply, 0)
    except ValueError:
        return int(round(float(reply)))


def parse_hex(text: str, maximum: int) -> int:
    text = text.strip() or "0"
    try:
        value = int(text, 16)
    except ValueError:
        raise ValueError(tr("{text} is not a hexadecimal number.", text=text)) from None
    if not 0 <= value <= maximum:
        raise ValueError(tr("{text} is outside the range 0…{maximum:X}.", text=text, maximum=maximum))
    return value


def bit_codes(text: str, width: int) -> list[int]:
    """0, 1 and X per bit, the most significant first (the instrument's bit 0); what is left out does not matter."""
    codes = {"0": 0, "1": 1, "X": 255}
    text = text.strip().upper()
    if len(text) > width or any(char not in codes for char in text):
        raise ValueError(tr("A bit field takes up to {width} characters 0, 1 or X.", width=width))
    return [codes[char] for char in text.ljust(width, "X")]


def command(mode: Mode, field: Field, value, width: int | None = None) -> str:
    """value: the SCPI text of a choice, a number, a bool, an int for hex fields, bit text for BITS.
    width: the number of bits of a BITS field with width_from."""
    head = f":TRIGger:{mode.path}:{field.key}"
    if field.kind == BITS:
        return "\n".join(f"{head} {position}\n:TRIGger:{mode.path}:CODE {code}"
                         for position, code in enumerate(bit_codes(value, width or field.width)))
    if field.kind == CHECK:
        return f"{head} {'ON' if value else 'OFF'}"
    if field.kind == HEX:
        return f"{head} {int(value)}"
    if field.kind == NUMBER:
        return f"{head} {value:.10g}"
    return f"{head} {value}"


def query(mode: Mode, field: Field) -> str | None:
    """None: a bit field cannot be read in one exchange, so it is only sent."""
    return None if field.kind == BITS else f":TRIGger:{mode.path}:{field.key}?"

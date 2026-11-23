"""Boundary-value cases for live_pico.py, beyond those of the pico_generator project and sigrok-test.

Same layout as that project's tests/cases.py: (name, generator command, sigrok-cli -P, -A filter, expected texts,
VCD timescale in ns). A list is ordered, a tuple is any order. The expected values follow from the protocol (checksums,
parity and dates are calculated here); only their wording is the decoder's. They are never a recording of its output.
Buses with open drain are driven push-pull, as the Pico's own pull-ups are too weak for them on the bench.
"""
from __future__ import annotations

import datetime

CASES: list[tuple] = []


def add(name, command, stack, shown, expected, timescale=10):
    CASES.append(("edge-" + name, command, stack, shown, expected, timescale))


def words(values, digits=2):
    return ",".join(f"{value:0{digits}X}" for value in values)


BYTES = [0x00, 0x01, 0x7F, 0x80, 0xFE, 0xFF]
SHOWN = [f"{value:02X}" for value in BYTES]


# ---- UART: every data width at its ends, every parity, stop bits, the baud rate at its ends and off by 4 %
for bits in (5, 6, 7, 8, 9):
    top, digits = (1 << bits) - 1, 3 if bits == 9 else 2
    values = [0, 1, 1 << bits - 1, top - 1, top]
    add(f"uart-{bits}bit", f"uart tx=0 bits={bits} data={words(values, digits)}", f"uart:tx=gp0:data_bits={bits}",
        "uart=tx-data", [f"{value:0{digits}X}" for value in values])
for parity in ("odd", "even", "zero", "one"):
    add(f"uart-parity-{parity}", f"uart tx=0 parity={parity} data={words(BYTES)}", f"uart:tx=gp0:parity={parity}",
        "uart=tx-data:tx-parity-ok:tx-parity-err", tuple(SHOWN + ["Parity bit"] * len(BYTES)))
add("uart-parity-wrong", f"uart tx=0 parity=odd data={words(BYTES)}", "uart:tx=gp0:parity=even",
    "uart=tx-parity-ok:tx-parity-err", ["Parity error"] * len(BYTES))
for stop in ("0.5", "1.5", "2"):
    add(f"uart-stop-{stop}", f"uart tx=0 stop={stop} data={words(BYTES)}", f"uart:tx=gp0:stop_bits={float(stop)}",
        "uart=tx-data:tx-warning", SHOWN)
add("uart-msb", f"uart tx=0 order=msb data={words(BYTES)}", "uart:tx=gp0:bit_order=msb-first", "uart=tx-data", SHOWN)
for baud in (300, 921600, 5000000):
    few = BYTES if baud > 300 else BYTES[:1] + BYTES[-1:]
    add(f"uart-{baud}bd", f"uart tx=0 baud={baud} data={words(few)}", f"uart:tx=gp0:baudrate={baud}", "uart=tx-data",
        [f"{value:02X}" for value in few], 1000 if baud == 300 else 5)
# The stop bit is sampled 9.5 bits after the start edge: 4 % off is 0.38 of a bit there, less than the half bit allowed.
# With idle between the words an error does not carry on to the next.
for name, sent in (("slow", 110592), ("fast", 119808)):
    add(f"uart-4percent-{name}", f"uart tx=0 baud={sent} bytegap=200us data={words(BYTES)}", "uart:tx=gp0",
        "uart=tx-data:tx-warning", SHOWN)
add("uart-one-word", "uart tx=0 data=00", "uart:tx=gp0", "uart=tx-data", ["00"])
add("uart-break", "uart tx=0 brk=2ms data=FF", "uart:tx=gp0", "uart=tx-break", ["Break condition"])

# ---- SPI: word widths, every mode, single edge bits, the clock at its ends
for bits in (1, 2, 8, 24, 32):
    top = (1 << bits) - 1
    values = sorted({0, 1, 1 << bits - 1, top})
    mirror = values[::-1]
    add(f"spi-{bits}bit", f"spi clk=0 mosi=1 miso=2 cs=3 bits={bits} data={words(values, 8)} rxdata={words(mirror, 8)}",
        f"spi:clk=gp0:mosi=gp1:miso=gp2:cs=gp3:wordsize={bits}", "spi=mosi-data:miso-data",
        tuple(f"{value:02X}" for value in values + mirror))
for mode in range(4):
    add(f"spi-mode{mode}", f"spi clk=0 mosi=1 miso=2 cs=3 mode={mode} data={words(BYTES)} rxdata={words(BYTES[::-1])}",
        f"spi:clk=gp0:mosi=gp1:miso=gp2:cs=gp3:cpol={mode >> 1}:cpha={mode & 1}", "spi=mosi-data:miso-data",
        tuple(SHOWN + SHOWN))
add("spi-cs-high-lsb", f"spi clk=0 mosi=1 cs=2 cspol=high order=lsb data={words(BYTES)}",
    "spi:clk=gp0:mosi=gp1:cs=gp2:cs_polarity=active-high:bitorder=lsb-first", "spi=mosi-data", SHOWN)
add("spi-word-per-frame", f"spi clk=0 mosi=1 cs=2 frame=1 data={words(BYTES)}", "spi:clk=gp0:mosi=gp1:cs=gp2",
    "spi=mosi-transfer", SHOWN)
add("spi-10k", "spi clk=0 mosi=1 cs=2 freq=10k data=00,FF,81", "spi:clk=gp0:mosi=gp1:cs=gp2", "spi=mosi-data",
    ["00", "FF", "81"], 100)
add("spi-20M", f"spi clk=0 mosi=1 cs=2 freq=20M data={words(BYTES)}", "spi:clk=gp0:mosi=gp1:cs=gp2", "spi=mosi-data",
    SHOWN, 5)

# ---- I2C: the first and last address, data at its ends, no acknowledge
for address in (0x00, 0x01, 0x7F):
    add(f"i2c-addr-{address:02X}", f"i2c scl=0 sda=1 drive=pp addr=0x{address:02X} wr={words(BYTES)}",
        "i2c:scl=gp0:sda=gp1", "i2c=start:repeat-start:stop:nack:address-read:address-write:data-read:data-write",
        ["Start", "Write", f"Address write: {address:02X}", *(f"Data write: {value}" for value in SHOWN), "Stop"], 100)
add("i2c-read", f"i2c scl=0 sda=1 drive=pp wr=FF rd={words(BYTES)}", "i2c:scl=gp0:sda=gp1",
    "i2c=data-read:data-write:address-read",
    ["Data write: FF", "Read", "Address read: 50", *(f"Data read: {value}" for value in SHOWN)], 100)
add("i2c-nack", "i2c scl=0 sda=1 drive=pp nack=1 wr=00", "i2c:scl=gp0:sda=gp1", "i2c=nack", ["NACK"], 100)
for freq in ("10k", "400k", "1M"):
    add(f"i2c-{freq}", f"i2c scl=0 sda=1 drive=pp freq={freq} wr=00,FF,55,AA", "i2c:scl=gp0:sda=gp1", "i2c=data-write",
        [f"Data write: {value}" for value in ("00", "FF", "55", "AA")], 50)

# ---- CAN: identifiers, lengths, stuffing at its worst (all recessive, all dominant), bit rates
CAN = "can:can_rx=gp0:nominal_bitrate={}:fast_bitrate=2000000"


def can_data(payload):
    return [f"Data byte {n}: 0x{value:02x}" for n, value in enumerate(payload)]


# The Bosch CAN 2.0 specification forbids an identifier whose 7 highest bits are all recessive: 7EF is the last without a warning.
RECESSIVE = "Identifier bits 10..4 must not be all recessive"
for ident in (0x7F0, 0x7FF):
    add(f"can-id-{ident:03X}", f"can can_rx=0 id={ident:X} payload=00", CAN.format(500000), "can=id:data:warning",
        (f"Identifier: {ident} (0x{ident:x})", RECESSIVE, *can_data([0x00])), 50)
add("can-ext-1FFFFFFF", "can can_rx=0 ext=1 id=1FFFFFFF payload=80", CAN.format(500000), "can=full-id:data:warning",
    ("Full Identifier: 536870911 (0x1fffffff)", RECESSIVE, *can_data([0x80])), 50)
for ident in (0x000, 0x001, 0x7EF):
    add(f"can-id-{ident:03X}", f"can can_rx=0 id={ident:X} payload=00,FF", CAN.format(500000), "can=id:dlc:data:warning",
        [f"Identifier: {ident} (0x{ident:x})", "Data length code: 2", *can_data([0x00, 0xFF])], 50)
for ident in (0x00000000, 0x1FBFFFFF):
    add(f"can-ext-{ident:08X}", f"can can_rx=0 ext=1 id={ident:X} payload=80", CAN.format(500000), "can=full-id:data:warning",
        [f"Full Identifier: {ident} (0x{ident:x})", *can_data([0x80])], 50)
for value, ident in ((0x00, "000"), (0xFF, "7EF")):
    add(f"can-8x{value:02X}", f"can can_rx=0 id={ident} payload={words([value] * 8)}", CAN.format(500000),
        "can=dlc:data:warning", ["Data length code: 8", *can_data([value] * 8)], 50)
add("can-no-data", "can can_rx=0 payload= dlc=0", CAN.format(500000), "can=dlc:data:warning", ["Data length code: 0"], 50)
add("can-rtr-dlc8", "can can_rx=0 rtr=1 dlc=8 payload=", CAN.format(500000), "can=dlc:data:rtr:eof:warning",
    ("Data length code: 8", "Remote transmission request: remote frame", "End of frame"), 50)
for dlc in (9, 15):  # ISO 11898-1: a classic frame with a DLC of 9 to 15 carries 8 bytes
    add(f"can-classic-dlc{dlc}", f"can can_rx=0 dlc={dlc} payload={words(range(1, 9))}", CAN.format(500000), "can=data:eof",
        [*can_data(range(1, 9)), "End of frame"], 50)
for rate in (10000, 1000000):
    add(f"can-{rate}", f"can can_rx=0 bitrate={rate} id=555 payload=00,FF", CAN.format(rate), "can=id:data:warning",
        ["Identifier: 1365 (0x555)", *can_data([0x00, 0xFF])], 1000 if rate == 10000 else 50)
for count in (0, 1, 16, 64):
    payload = [(7 * n + 1) & 0xFF for n in range(count)]
    add(f"can-fd-{count}", f"can can_rx=0 fd=1 esi=1 id=7EF payload={words(payload)}", CAN.format(500000), "can=id:data:warning",
        ["Identifier: 2031 (0x7ef)", *can_data(payload)], 50)

# Five equal bits at the end of the data (with no data: of the DLC): the first fixed stuff bit stands for the dynamic one.
for name, payload in (("none", []), ("00", [0x00]), ("8xFF", [0xFF] * 8), ("16x1F", [0x1F] * 16), ("64x00", [0x00] * 64)):
    add(f"can-fd-tail-{name}", f"can can_rx=0 fd=1 id=7EF payload={words(payload)}", CAN.format(500000),
        "can=data:ack-slot:eof:warning", [*can_data(payload), "ACK slot: ACK", "End of frame"], 50)
add("can-fd-no-ack", "can can_rx=0 fd=1 esi=1 ack=0 payload=01", CAN.format(500000), "can=data:ack-slot:eof:warning",
    ["Data byte 0: 0x01", "ACK slot: NACK", "End of frame"], 50)


# ---- LIN: the first and last identifier of each kind, 1 and 8 bytes, a checksum that carries
def lin(ident, data, version):
    bit = [ident >> n & 1 for n in range(6)]
    parity = bit[0] ^ bit[1] ^ bit[2] ^ bit[4] | (1 ^ bit[1] ^ bit[3] ^ bit[4] ^ bit[5]) << 1
    total = sum(data) + (ident | parity << 6 if version == 2 and ident < 0x3C else 0)
    while total > 0xFF:
        total = (total & 0xFF) + (total >> 8)
    return ["Break condition", "Sync", f"ID: {ident:02X} Parity: {parity} (ok)", *(f"Data: 0x{value:02X}" for value in data),
            f"Checksum: 0x{total ^ 0xFF:02X}"]


# LIN 2 wants the classic checksum for the diagnostic identifiers 3C and 3D.
for ident, data, version in ((0x00, [0x00], 2), (0x3B, [0xFF] * 8, 2), (0x3C, [0x00] * 8, 2), (0x3D, [0xFF], 2),
                             (0x00, [0xFF] * 8, 1), (0x3B, [0x80], 1)):
    add(f"lin-{ident:02X}-{len(data)}x{data[0]:02X}-v{version}", f"lin tx=0 id={ident:X} data={words(data)} "
        f"version={version}", f"uart:rx=gp0:baudrate=19200,lin:version={version}", "lin", lin(ident, data, version), 50)

# ---- Modbus: server and register at their ends, the longest read
MODBUS = "uart:rx=gp1:tx=gp0:baudrate=19200:parity=even,modbus"
for server, register in ((1, 0x0000), (247, 0xFFFE)):
    add(f"modbus-{server}-{register:04X}", f"modbus tx=0 rx=1 server={server} reg=0x{register:X} values=0000,FFFF", MODBUS,
        "modbus",
        [f"Slave ID: {server}", "Function 3: Read Holding Registers", f"Start at address 0x{register:X} / {register + 30001}",
         "Read 2 units of data", "CRC correct", f"Slave ID: {server}", "Function 3: Read Holding Registers", "Byte count: 4",
         "0x0000 / 0", "0xFFFF / 65535", "CRC correct"], 1000)
add("modbus-125", "modbus tx=0 rx=1 values=" + words(range(125), 4), MODBUS, "modbus=sc-length:sc-data:sc-crc",
    ["Byte count: 250", *(f"0x{value:04X} / {value}" for value in range(125)), "CRC correct"], 1000)

# ---- MIDI and DMX512: notes, velocity, channel and slots at their ends
MIDI = "uart:rx=gp0:baudrate=31250,midi"
for channel, note, name, velocity in ((0, 0, "C-1", 1), (15, 127, "G9", 127)):
    add(f"midi-{channel}-{note}", f"midi tx=0 channel={channel} notes={note} velocity={velocity}", MIDI, "midi=text-verbose",
        [f"Channel {channel + 1}: note on (note = {note} '{name}', velocity = {velocity})",
         f"Channel {channel + 1}: note off (note = {note} '{name}', velocity = 64)"], 1000)
# A note on with velocity 0 ends the note.
add("midi-velocity-0", "midi tx=0 data=90,3C,00", MIDI, "midi=text-verbose",
    ["Channel 1: note off (note = 60 'C4', velocity = 0)"], 1000)
DMX = "uart:rx=gp0:baudrate=250000,dmx512:show_zero=yes"
add("dmx512-1", "dmx512 tx=0 packets=1 data=255", DMX, "dmx512=startcode:chan_data", ["STARTCODE 0", "CHANNEL 1: 255"], 100)
add("dmx512-512", "dmx512 tx=0 packets=1 data=" + ",".join(str(n % 256) for n in range(512)), DMX, "dmx512=chan_data",
    [f"CHANNEL {n + 1}: {n % 256}" for n in range(512)], 100)

# ---- I2S, JTAG, SWD, MDIO: words at their ends
for bits, digits, samples in ((8, 2, (0x00, 0xFF, 0x80, 0x7F)), (16, 4, (0x0000, 0xFFFF, 0x8000, 0x7FFF)),
                              (32, 8, (0x00000000, 0xFFFFFFFF, 0x80000000, 0x7FFFFFFF))):
    add(f"i2s-{bits}", f"i2s sck=0 ws=1 sd=2 bits={bits} samples={words(samples, digits)}", "i2s:sck=gp0:ws=gp1:sd=gp2",
        None, [f"{'Right' if n & 1 else 'Left'} channel: {value:08x}" for n, value in enumerate(samples)])


def jtag(register, value, length):
    return f"{register}: {value:0{length}b} (0x{value:x}), {length} bits"


for length in (1, 32, 64):
    top = (1 << length) - 1
    add(f"jtag-dr{length}", f"jtag tdi=0 tdo=1 tck=2 tms=3 seq=reset,dr:{length}:{top:X}:0,dr:{length}:0:{top:X},idle:2",
        "jtag:tdi=gp0:tdo=gp1:tck=gp2:tms=gp3", "jtag=bitstring-tdi:bitstring-tdo",
        (jtag("DR TDI", top, length), jtag("DR TDO", 0, length), jtag("DR TDI", 0, length), jtag("DR TDO", top, length)))
add("swd-data", "swd swclk=0 swdio=1 seq=reset,idle:2,dpr:0:00000000,dpr:0:FFFFFFFF,dpw:0:00000001,dpw:0:80000000,idle:8",
    "swd:swclk=gp0:swdio=gp1", "swd=data", ["0x00000000", "0xffffffff", "0x00000001", "0x80000000"])
for value in (0x0000, 0xFFFF):
    add(f"mdio-{value:04X}", f"mdio mdc=0 mdio=1 seq=w:0:0:{value:04X},r:0:0:{value:04X}", "mdio:mdc=gp0:mdio=gp1",
        "mdio=decode:frame-error", [f"WRITE: {value:04X} PHYAD: 00 REGAD: 00", f"READ:  {value:04X} PHYAD: 00 REGAD: 00"], 50)

# ---- infrared: address and command at their ends
for address, command in ((0x00, 0x00), (0xFF, 0xFF)):
    add(f"ir_nec-{address:02X}", f"ir_nec ir=0 addr=0x{address:X} cmd=0x{command:X} repeats=0", "ir_nec:ir=gp0",
        "ir_nec=addr:addr-inv:cmd:cmd-inv",
        [f"Address: 0x{address:02X}", f"Address#: 0x{address ^ 255:02X}", f"Command: 0x{command:02X}",
         f"Command#: 0x{command ^ 255:02X}"], 1000)
for bits, address, ext, command in ((12, 0, 0, 0), (12, 31, 0, 127), (15, 255, 0, 127), (20, 31, 255, 127)):
    add(f"ir_sirc-{bits}-{command}", f"ir_sirc ir=0 bits={bits} addr={address} ext={ext} cmd={command} frames=1",
        "ir_sirc:ir=gp0", "ir_sirc=command:address:extended",
        [f"Command: 0x{command:02X}", f"Address: 0x{address:02X}"] + ([f"Extended: 0x{ext:02X}"] if bits == 20 else []), 1000)

# ---- sensors: the ends of the range, both sides of zero
temperatures = (-55, -0.5, 0, 0.5, 125)
add("lm75-range", "lm75 scl=0 sda=1 drive=pp seq=" + ",".join(f"temp:{value:g}" for value in temperatures),
    "i2c:scl=gp0:sda=gp1,lm75", "lm75=celsius", [f"Temperature: {value:.1f} °C" for value in temperatures], 100)
# Each write selects the register first; the read after it, with or without a repeated start, is of that register.
add("lm75-trip", "lm75 scl=0 sda=1 drive=pp seq=thyst:-55,tos:125,thyst:-0.5,ptr:2,temp:75,ptr:0,temp:-0.5",
    "i2c:scl=gp0:sda=gp1,lm75", "lm75=celsius",
    [f"{name} trip temperature: {value} °C" for name, value in (("T_HYST", "-55.0"), ("T_OS", "125.0"), ("T_HYST", "-0.5"), ("T_HYST", "75.0"))]
    + ["Temperature: -0.5 °C"], 100)
for humidity, temperature in ((0, -400), (1000, 800), (1, -1), (999, 0), (500, 1)):
    add(f"am230x-{humidity}-{temperature}", f"am230x sda=0 humidity={humidity} temp={temperature}", "am230x:sda=gp0",
        "am230x=humidity:temperature:checksum",
        [f"Humidity: {humidity / 10:.1f} %", f"Temperature: {temperature / 10:.1f} °C", "Checksum: OK"], 1000)

# ---- others
for colors in (("000000", "FFFFFF"), ("800000", "008000", "000080", "010000", "000100", "000001")):
    add(f"ws281x-{len(colors)}", "rgb_led_ws281x din=0 colors=" + ",".join(colors), "rgb_led_ws281x:din=gp0",
        "rgb_led_ws281x=rgb", ["#" + color.lower() for color in colors])
add("ps2-bytes", f"ps2 clk=0 data=1 drive=pp codes={words(BYTES)}", "ps2:clk=gp0:data=gp1", "ps2=word:parity-ok:parity-err",
    [text for value in BYTES for text in (f"Data: {value:02x}", "Parity OK")], 1000)


def wiegand26(facility, card):
    bits = f"{facility:08b}{card:016b}"
    return f"26 bits {bits[:12].count('1') % 2}{bits}{1 - bits[12:].count('1') % 2}"


for facility, card in ((0, 0), (0, 1), (255, 65535), (128, 32768)):
    add(f"wiegand-{facility}-{card}", f"wiegand d0=0 d1=1 facility={facility} card={card}", "wiegand:d0=gp0:d1=gp1",
        "wiegand=state", [wiegand26(facility, card)], 10000)
add("wiegand-1bit", "wiegand d0=0 d1=1 format=raw bits=1", "wiegand:d0=gp0:d1=gp1", "wiegand=state", ["1 bits 1"], 10000)
for value in (0, 2047):
    flag = int(value > 0)
    add(f"sbus-{value}",
        f"sbus_futaba tx=0 frames=1 ch={','.join([str(value)] * 16)} ch17={flag} ch18={flag} lost={flag} failsafe={flag}",
        "uart:rx=gp0:baudrate=100000:parity=even:invert_rx=yes,sbus_futaba", "sbus_futaba",
        ["0x0f"] + [str(value)] * 16 + [str(flag)] * 4 + ["0x00"], 1000)


def crc8(data):
    crc = 0
    for byte in data:
        for n in range(8):
            crc = crc >> 1 ^ (0x8C if (crc ^ byte >> n) & 1 else 0)
    return crc


for family, serial in ((0x01, 0), (0xFF, 0xFFFFFFFFFFFF)):
    rom = bytes([family]) + serial.to_bytes(6, "little")
    add(f"onewire-{family:02X}", f"onewire owr=0 drive=pp family=0x{family:X} serial=0x{serial:X} tx=00 rx=FF",
        "onewire_link:owr=gp0,onewire_network", "onewire_network",
        ["Reset/presence: true", "ROM command: 0x55 'Match ROM'",
         f"ROM: 0x{int.from_bytes(rom + bytes([crc8(rom)]), 'little'):016x}", "Data: 0x00", "Data: 0xff"], 100)
for moment in ((2000, 1, 1, 0, 0, 0), (2000, 2, 29, 23, 59, 59), (2024, 2, 29, 12, 0, 0), (2099, 12, 31, 23, 59, 59)):
    date = datetime.datetime(*moment)
    add(f"ds1307-{date:%Y%m%d}", "ds1307 scl=0 sda=1 drive=pp seq=set:" + ":".join(map(str, moment)),
        "i2c:scl=gp0:sda=gp1,ds1307", "ds1307=write_date_time",
        [f"Written date/time: {date:%A}, {date:%d.%m.%Y %H:%M:%S}"], 100)
for address in (0x00, 0x7F):
    add(f"eeprom24-{address:02X}", f"eeprom24xx scl=0 sda=1 drive=pp seq=w:{address:02X}:FF,r:{address:02X}:00",
        "i2c:scl=gp0:sda=gp1,eeprom24xx", "eeprom24xx=byte-write:random-read",
        [f"Byte write (addr={address:02X}, 1 byte): FF", f"Random access read (addr={address:02X}, 1 byte): 00"], 100)

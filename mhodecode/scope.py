"""SCPI client for the Rigol MHO900 series over a raw TCP socket (port 5555).

Commands follow the MHO900 Programming Guide. Arbitrary waveforms cannot be sent
over SCPI on this instrument: :SOURce<n>:LOAD:ARBitrary only reads a CSV file
from the instrument's own storage, so the CSV is first uploaded through the
built-in FTP server (anonymous login, /arb folder, seen as C:/arb on the scope).
"""
from __future__ import annotations

import ftplib
import io
import re
import socket
import threading
import time
from dataclasses import dataclass, field

import numpy as np

from . import abort
from .i18n import tr
from .signal import MIN_SWING_V, auto_thresholds

SCPI_PORT = 5555
FTP_PORT = 21
ARB_FTP_DIR = "arb"
ARB_SCOPE_DIR = "C:/arb"
# The socket wakes up this often while waiting, so an abort is seen quickly.
ABORT_POLL_S = 0.2
BLOCK_TIMEOUT_S = 60.0
EMPTY_BLOCK_RETRY_S = 0.05
EMPTY_BLOCK_LIMIT_S = 5.0
PROGRESS_STEP_BYTES = 1 << 20
RECEIVE_BUFFER_BYTES = 1 << 20
# After a connection is dropped in the middle of a waveform block the firmware still sends
# about 1 MB of it on the next connection (measured), so *IDN? is read until its reply shows up.
IDENTITY_LINE = re.compile(rb"[^,\n]+,[^,\n]+,[^,\n]+,[^,\n]+\n$")
LEFTOVER_LIMIT_S = 30.0
# The arbitrary load runs in the background: *OPC? returns at once and commands sent
# too early make AFG2 keep an old waveform or play AFG1's. The firmware offers no way
# to see it finish. Loads were reliable from 0.2 s and 0.5 s; these values add margin.
AFG_FUNCTION_SETTLE_S = 0.3
AFG_LOAD_SETTLE_S = 1.0
# Samples saturate about 270 counts below full scale in WORD format (measured: 65495 on an
# overdriven input). BYTE samples are exactly the top byte of WORD samples (measured on 10 M points).
CLIP_CODES = {"WORD": (500, 65000), "BYTE": (1, 253)}
SAMPLE_TYPES = {"WORD": "<u2", "BYTE": "u1"}
DEFAULT_FORMAT = "BYTE"
AUTOSCALE_DEPTH = 100_000
AUTOSCALE_START_V_PER_DIV = 5.0
AUTOSCALE_MIN_SWING_V = 0.1
AUTOSCALE_DIVISIONS = 6
# At a probe ratio of 1: 1 mV/div to 10 V/div with 1 MOhm input, from the Programming Guide. Each step times the ratio
# came back as sent for ratios from 0.1 to 100 (measured, firmware 00.01.00).
VERTICAL_STEPS = tuple(mantissa * 10.0 ** power for power in range(-3, 1) for mantissa in (1, 2, 5)) + (10.0,)
SI_PREFIXES = ((1.0, ""), (1e-3, "m"), (1e-6, "µ"), (1e-9, "n"), (1e-12, "p"))
ERROR_QUEUE_LIMIT = 50
# Digital channels share the numbering of analog ones, as a number from this one up.
DIGITAL_BASE = 100
DIGITAL_CHANNELS = 16
DIGITAL_PODS = (1, 2)  # POD1 is D0 to D7, POD2 is D8 to D15
POD_CHANNELS = 8
ANALOG_CHANNELS = 4
# Enabling the digital channels takes this long before they accept further commands (0.3 s was
# too short on firmware 00.01.00: POD1 still read as off).
LA_ENABLE_SETTLE_S = 0.8
MEMORY_FILE = "mhodecode_memory.bin"
MEMORY_SCOPE_DIR = "C:"
# :SAVE:STATus? can still show the previous "finished" right after the command.
SAVE_START_S = 0.3
SAVE_POLL_S = 0.2
SAVE_LIMIT_S = 900.0  # measured 14 s for 10 M points on two channels plus the digital ones
FTP_BLOCK_BYTES = 1 << 20
# Nothing connected, DC, 10 mV/div to 5 V/div: the swing that auto_thresholds measures reached 0.11
# div (BYTE and WORD), which in volts grows with the scale. Twice that is noise and not a signal.
NOISE_DIVISIONS = 0.2


def vertical_scales(probe: float) -> list[float]:
    """The 1-2-5 steps of the vertical scale at this probe ratio, in volts per division."""
    return [float(f"{step * probe:.6g}") for step in VERTICAL_STEPS]


def standard_scale(volts_per_div: float, probe: float) -> float:
    """The smallest step that shows at least this much per division, else the largest one."""
    scales = vertical_scales(probe)
    return next((scale for scale in scales if scale >= volts_per_div), scales[-1])


def format_si(value: float, unit: str) -> str:
    """1.2112e-3 and "s" give "1.2112 ms"."""
    if value == 0:
        return f"0 {unit}"
    factor, prefix = next((entry for entry in SI_PREFIXES if abs(value) >= entry[0]), SI_PREFIXES[-1])
    return f"{value / factor:.6g} {prefix}{unit}"


class ScopeError(RuntimeError):
    """Communication failure or an error reported by the instrument."""


def digital_channel(number: int) -> int:
    return DIGITAL_BASE + number


def is_digital(channel: int) -> bool:
    return channel >= DIGITAL_BASE


def channel_name(channel: int) -> str:
    return f"D{channel - DIGITAL_BASE}" if is_digital(channel) else f"CH{channel}"


@dataclass
class Preamble:
    format: int
    type: int
    points: int
    count: int
    xincrement: float
    xorigin: float
    xreference: float
    yincrement: float
    yorigin: float
    yreference: float

    @classmethod
    def parse(cls, text: str) -> "Preamble":
        parts = text.strip().split(",")
        if len(parts) != 10:
            raise ScopeError(tr("Unexpected WAV:PRE reply: {text!r}", text=text))
        return cls(
            int(float(parts[0])), int(float(parts[1])), int(float(parts[2])),
            int(float(parts[3])), float(parts[4]), float(parts[5]),
            float(parts[6]), float(parts[7]), float(parts[8]), float(parts[9]),
        )


@dataclass
class Waveform:
    channel: int
    volts: np.ndarray
    dt: float
    t0: float
    clipped: float = 0.0  # fraction of samples at the ends of the ADC range
    digital: bool = False  # volts holds the levels 0 and 1 of a digital channel
    volts_per_div: float = 0.0  # 0 when not known, as in a BIN file opened from disk

    @property
    def min_swing(self) -> float:
        """Below this the channel carries only the instrument's own noise."""
        return max(MIN_SWING_V, NOISE_DIVISIONS * self.volts_per_div)

    def has_signal(self) -> bool:
        if self.digital:
            return bool(self.volts.min() != self.volts.max())
        return auto_thresholds(self.volts, self.min_swing) is not None


PRETRIGGER_TOLERANCE = 0.005  # of the memory's length: half a percent of the choice's whole-percent step


@dataclass
class CapturePlan:
    """What an acquisition sets on the scope. None: that setting stays as it is on the scope."""
    channels: set
    force_dc: bool = True
    depth: str | None = None
    time_scale: float | None = None
    trigger: tuple = ()  # commands of the trigger, empty to leave it alone
    thresholds: dict = field(default_factory=dict)  # group of digital channels -> volts, for the groups to change
    pretrigger: float | None = None  # percent of the memory before the trigger


class Scope:
    def __init__(self, host: str, port: int = SCPI_PORT, timeout: float = 5.0):
        self.host = host
        self.port = port
        self.timeout = timeout
        self.trace = None  # optional callable receiving a text line per exchange
        self._sock: socket.socket | None = None
        self._lock = threading.RLock()
        self._acquiring = False

    def _note(self, text: str) -> None:
        if self.trace:
            self.trace(text)

    # ---- connection -----------------------------------------------------
    def connect(self) -> str:
        with self._lock:
            self.close()
            try:
                sock = socket.create_connection((self.host, self.port), self.timeout)
            except OSError as exc:
                raise ScopeError(tr("Cannot connect to {host}:{port}: {error}",
                                    host=self.host, port=self.port, error=exc)) from exc
            sock.settimeout(ABORT_POLL_S)
            sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
            self._sock = sock
            return self._read_identity()

    def _read_identity(self) -> str:
        """Ask for *IDN? and skip whatever an interrupted transfer left in the stream."""
        while True:
            self.write("*IDN?")
            tail = bytearray()
            received = 0
            started = time.monotonic()
            while not (tail.endswith(b"\n") and IDENTITY_LINE.search(tail)):
                if time.monotonic() - started > LEFTOVER_LIMIT_S:
                    raise ScopeError(tr("No reply to {command}: {error}", command="*IDN?", error="timeout"))
                chunk = self._receive_some(self.timeout)
                received += len(chunk)
                tail = (tail + chunk)[-1024:]
            line = bytes(tail).rsplit(b"\n", 2)[-2]
            if received == len(line) + 1:
                return line.decode("ascii", "replace").strip()
            self._note(tr("Discarded {count} bytes after an interrupted transfer", count=received - len(line) - 1))

    def recover(self) -> None:
        """Bring the link back to a clean state after an abort."""
        with self._lock:
            abort.clear()
            self.connect()
            if self._acquiring:
                self._acquiring = False
                self.stop()

    def close(self) -> None:
        with self._lock:
            if self._sock is not None:
                try:
                    self._sock.close()
                finally:
                    self._sock = None

    @property
    def connected(self) -> bool:
        return self._sock is not None

    # ---- raw SCPI -------------------------------------------------------
    def _socket(self) -> socket.socket:
        if self._sock is None:
            raise ScopeError(tr("Not connected to the oscilloscope"))
        return self._sock

    def write(self, command: str) -> None:
        with self._lock:
            self._note(f"> {command}")
            try:
                self._socket().sendall((command + "\n").encode("ascii"))
            except OSError as exc:
                raise ScopeError(tr("Send error ({command}): {error}", command=command, error=exc)) from exc

    def _receive_into(self, view: memoryview, limit: float) -> int:
        """Fill part of view; waits at most limit seconds for data and stops on abort."""
        sock = self._socket()
        waited_from = time.monotonic()
        while True:
            abort.checkpoint()
            try:
                got = sock.recv_into(view)
            except socket.timeout:
                if time.monotonic() - waited_from > limit:
                    raise ScopeError(tr("No data from the oscilloscope for {seconds:.0f} s", seconds=limit)) from None
                continue
            except OSError as exc:
                raise ScopeError(tr("Read error: {error}", error=exc)) from exc
            if got == 0:
                raise ScopeError(tr("The oscilloscope closed the connection"))
            return got

    def _receive_some(self, limit: float) -> bytes:
        buffer = bytearray(65536)
        got = self._receive_into(memoryview(buffer), limit)
        return bytes(buffer[:got])

    def _read_exact(self, count: int, limit: float, progress=None) -> bytearray:
        buffer = bytearray(count)
        view = memoryview(buffer)
        pos = 0
        reported = 0
        while pos < count:
            pos += self._receive_into(view[pos:pos + RECEIVE_BUFFER_BYTES], limit)
            if progress and pos - reported >= PROGRESS_STEP_BYTES:
                reported = pos
                progress(pos, count)
        return buffer

    def query(self, command: str) -> str:
        with self._lock:
            self.write(command)
            data = bytearray()
            while not data.endswith(b"\n"):
                data += self._receive_some(self.timeout)
            reply = data.decode("ascii", "replace").strip()
            self._note(f"< {reply}")
            return reply

    def query_float(self, command: str) -> float:
        return float(self.query(command))

    def query_block(self, command: str, timeout: float = BLOCK_TIMEOUT_S, progress=None) -> bytearray:
        """Query returning a TMC block (#N<len><data><terminator>)."""
        with self._lock:
            started = time.monotonic()
            self.write(command)
            head = self._read_exact(2, timeout)
            if head[:1] != b"#":
                raise ScopeError(tr("Unexpected reply to {command}: {head!r}", command=command, head=bytes(head)))
            length = int(self._read_exact(int(head[1:2]), timeout))
            data = self._read_exact(length, timeout, progress)
            self._read_exact(1, timeout)  # block terminator
            elapsed = time.monotonic() - started
            self._note(f"< {length:,} B, {elapsed:.3f} s, {length / max(elapsed, 1e-6) / 1e6:.2f} MB/s")
            return data

    def errors(self) -> list[str]:
        """Empty the instrument's error queue and return what it held."""
        found = []
        for _ in range(ERROR_QUEUE_LIMIT):
            reply = self.query(":SYSTem:ERRor?")
            if reply.startswith(("0,", "+0,")):
                break
            found.append(reply)
        return found

    # ---- acquisition ----------------------------------------------------
    def run(self) -> None:
        self.write(":RUN")

    def stop(self) -> None:
        self.write(":STOP")

    def channel_enabled(self, channel: int) -> bool:
        return self.query(f":CHANnel{channel}:DISPlay?") == "1"

    def set_channel(self, channel: int, enabled: bool = True) -> None:
        self.write(f":CHANnel{channel}:DISPlay {'ON' if enabled else 'OFF'}")

    def set_dc_coupling(self, channel: int) -> None:
        """AC coupling removes the logic levels and droops long pulses, so decoding needs DC."""
        self.write(f":CHANnel{channel}:COUPling DC")

    def set_probe(self, channel: int, ratio: float) -> None:
        self.write(f":CHANnel{channel}:PROBe {ratio:g}")

    def channel_state(self, channel: int) -> dict:
        return {
            "scale": self.query_float(f":CHANnel{channel}:SCALe?"),
            "offset": self.query_float(f":CHANnel{channel}:OFFSet?"),
            "probe": self.query_float(f":CHANnel{channel}:PROBe?"),
            "coupling": self.query(f":CHANnel{channel}:COUPling?"),
        }

    def set_vertical(self, channel: int, scale: float, offset: float) -> None:
        self.write(f":CHANnel{channel}:SCALe {scale:g}")
        # + 0.0 turns -0.0 into 0.0: the scope refuses "-0" with -120, and a flat channel measures exactly 0 V.
        self.write(f":CHANnel{channel}:OFFSet {offset + 0.0:g}")

    def set_memory_depth(self, depth: int | str) -> None:
        self.write(f":ACQuire:MDEPth {depth}")

    def set_time_scale(self, seconds_per_div: float) -> None:
        self.write(f":TIMebase:MAIN:SCALe {seconds_per_div:g}")

    def set_edge_trigger(self, channel: int, level: float, slope: str = "POSitive") -> None:
        self.write(":TRIGger:MODE EDGE")
        self.write(f":TRIGger:EDGE:SOURce CHANnel{channel}")
        self.write(f":TRIGger:EDGE:SLOPe {slope}")
        self.write(f":TRIGger:EDGE:LEVel {level:g}")

    def apply_plan(self, plan: CapturePlan) -> list[str]:
        """Set the scope up for a capture. Returns what did not come out as asked, for the log."""
        self.errors()  # what is left in the queue is not this plan's doing
        analog = [channel for channel in range(1, ANALOG_CHANNELS + 1)]
        for channel in analog:  # on first: the scope must not pass through no channel at all
            if channel in plan.channels:
                self.set_channel(channel, True)
                if plan.force_dc:
                    self.set_dc_coupling(channel)
        for channel in analog:
            if channel not in plan.channels:
                self.set_channel(channel, False)
        self._apply_digital({channel for channel in plan.channels if is_digital(channel)}, plan.thresholds)
        if plan.time_scale is not None:
            self.set_time_scale(plan.time_scale)
        if plan.depth is not None:  # after the channels, on which its limit depends
            self.set_memory_depth(plan.depth)
        self.query("*OPC?")
        notes = [tr("the scope reported {error}", error=error) for error in self.errors()]
        self._apply_trigger(plan.trigger)
        if plan.pretrigger is not None:
            notes += self._apply_pretrigger(plan.pretrigger)
        if plan.depth not in (None, "AUTO"):
            depth = self.query_float(":ACQuire:MDEPth?")
            if depth != float(plan.depth):
                notes.append(tr("the memory depth is {got:,.0f} points instead of {wanted:,.0f}.", got=depth, wanted=float(plan.depth)))
        return notes

    def _apply_pretrigger(self, percent: float) -> list[str]:
        """The scope has no command for this share. The trigger lies at offset - memory length / 2 from the record's
        start (measured), so the share follows from the offset. It clamps the offset without an error at -5 divisions."""
        self.write(":RUN")  # stopped, it still reports the sample rate of the last capture
        self.query("*OPC?")
        length = self.query_float(":ACQuire:MDEPth?") / self.query_float(":ACQuire:SRATe?")
        wanted = length * (0.5 - percent / 100)
        self.write(f":TIMebase:MAIN:OFFSet {wanted:.10g}")
        self.query("*OPC?")
        notes = [tr("the scope reported {error}", error=error) for error in self.errors()]
        got = self.query_float(":TIMebase:MAIN:OFFSet?")
        if abs(got - wanted) > length * PRETRIGGER_TOLERANCE:
            notes.append(tr("pretrigger is {got:.0f} % instead of {wanted:.0f} %: the scope limited the time base offset to {offset}.",
                            got=(0.5 - got / length) * 100, wanted=percent, offset=format_si(got, "s")))
        return notes

    def _apply_trigger(self, commands: tuple) -> None:
        """An acquisition then waits for this trigger without a limit, so a refused one must stop it here."""
        for command in commands:
            for line in command.split("\n"):  # a bit field takes one exchange per bit
                self.write(line)
        self.query("*OPC?")
        refused = self.errors()
        if refused:
            raise ScopeError(tr("The scope refused the trigger settings: {errors}", errors="; ".join(refused)))

    def _apply_digital(self, wanted: set, thresholds: dict) -> None:
        if not wanted:
            if self.query(":LA:ENABle?") == "1":
                self.write(":LA:ENABle OFF")
            return
        self.enable_digital()
        members = {pod: range((pod - 1) * POD_CHANNELS, pod * POD_CHANNELS) for pod in DIGITAL_PODS}
        used = [pod for pod in DIGITAL_PODS if any(digital_channel(number) in wanted for number in members[pod])]
        # Turning the last digital channel off also turns :LA:ENABle off (measured, firmware 00.01.00),
        # so what is wanted goes on first. A group's display sets all its channels and reads 1 only when
        # all of them are on.
        for pod in used:
            self.write(f":LA:POD{pod}:DISPlay ON")
            # The reply can name a threshold the group is not working at: POD2 read 1.65 and showed D8 to D15
            # stuck, until the same value was written again (measured, firmware 00.01.00; what brings this about
            # was not found, turning the group or the digital channels off and on does not).
            level = thresholds[pod] if pod in thresholds else self.query_float(f":LA:POD{pod}:THReshold?")
            self.write(f":LA:POD{pod}:THReshold {level:.6g}")
            for number in members[pod]:
                if digital_channel(number) not in wanted:
                    self.write(f":LA:DIGital:ENABle D{number},OFF")
        for pod in DIGITAL_PODS:
            if pod not in used:
                self.write(f":LA:POD{pod}:DISPlay OFF")

    def single(self, timeout: float | None = 10.0, force_after: float | None = 1.0) -> bool:
        """Run one acquisition and wait until it stops. timeout None waits until aborted.

        Returns True when it triggered on its own, False when it had to be forced.
        """
        # Right after :SINGle from the stopped state the status can still show the old
        # STOP for a long time (over 0.5 s after a timebase change), which looks the same
        # as a finished capture. Going through RUN first leaves STOP out of the sequence.
        self._acquiring = True
        self.write(":RUN")
        started = time.monotonic()
        while self.query(":TRIGger:STATus?") == "STOP":
            if timeout is not None and time.monotonic() - started > timeout:
                self._acquiring = False
                raise ScopeError(tr("The acquisition did not start within {seconds} s", seconds=timeout))
            abort.sleep(0.02)
        self.write(":SINGle")
        started = time.monotonic()
        forced = False
        while timeout is None or time.monotonic() - started < timeout:
            elapsed = time.monotonic() - started
            if self.query(":TRIGger:STATus?") == "STOP":
                self._acquiring = False
                return not forced
            if force_after is not None and not forced and elapsed > force_after:
                self.write(":TFORce")
                forced = True
            abort.sleep(0.02)
        self._acquiring = False
        self.stop()
        raise ScopeError(tr("The acquisition did not finish within {seconds} s", seconds=timeout))

    def fetch(self, channel: int, points: int | None = None, progress=None, sample_format: str = DEFAULT_FORMAT) -> Waveform:
        """Read the channel from internal memory (the scope must be stopped).

        BYTE in a single block is the fastest way out of this firmware: 10 M points took
        5.5 s, against 7.9 s in 1 M chunks, 11 s as WORD, 13 s through a file and FTP and
        about 150 s as ASCII. FTP runs at 11.8 MB/s on the same link, so the 1.8 MB/s
        limit is the instrument's SCPI path and a bigger socket buffer changes nothing.
        """
        with self._lock:
            self.write(f":WAVeform:SOURce CHANnel{channel}")
            # A channel that is off refuses this with -200 and the source stays where it was, so what
            # follows would be another channel's data under this channel's name (measured, firmware 00.01.00).
            if self.query(":WAVeform:SOURce?") != f"CHAN{channel}":
                raise ScopeError(tr("The scope gives no data for CH{channel}. Turn this channel on.", channel=channel))
            self.write(f":WAVeform:FORMat {sample_format}")
            # The read window survives a change of memory depth, and once it lies beyond
            # the memory the firmware rejects new limits (-200). Leaving RAW mode resets it
            # to the whole memory.
            self.write(":WAVeform:MODE NORMal")
            self.write(":WAVeform:MODE RAW")
            if points:
                self.write(f":WAVeform:STOP {points}")
            preamble = Preamble.parse(self.query(":WAVeform:PREamble?"))
            size = np.dtype(SAMPLE_TYPES[sample_format]).itemsize

            def report(done, total):
                if progress:
                    progress(done // size, total // size)

            # The trigger status can show STOP slightly before the memory is readable: the
            # block then comes back empty (seen in about 1 of 6 captures) and a later read
            # returns the data, so an empty block means "ask again".
            started = time.monotonic()
            raw = self.query_block(":WAVeform:DATA?", progress=report)
            while not raw:
                if time.monotonic() - started > EMPTY_BLOCK_LIMIT_S:
                    raise ScopeError(tr("No waveform data on CH{channel}", channel=channel))
                abort.sleep(EMPTY_BLOCK_RETRY_S)
                raw = self.query_block(":WAVeform:DATA?", progress=report)
            codes = np.frombuffer(raw, dtype=SAMPLE_TYPES[sample_format])
            low, high = CLIP_CODES[sample_format]
            clipped = float(np.mean((codes >= high) | (codes <= low)))
            volts = (codes.astype(np.float32) - preamble.yorigin - preamble.yreference) * preamble.yincrement
            return Waveform(channel, volts, preamble.xincrement, preamble.xorigin, clipped)

    def screenshot(self) -> bytes:
        """The scope's screen as PNG: 81 kB in 0.26 s, against 235 kB in 0.19 s as lossy JPG."""
        return bytes(self.query_block(":DISPlay:DATA? PNG", timeout=20.0))

    def autoscale(self, channels: list[int], trigger_channel: int | None = None, slope: str = "POSitive", auto_depth: bool = False) -> dict:
        """Fit each channel to the screen on the standard steps; returns channel -> (low, high) in volts.
        auto_depth: the memory depth is on Auto, which its reply cannot tell, so Auto is what is set back."""
        levels: dict[int, tuple[float, float]] = {}
        # The trigger level comes from the channel's levels, so it is measured even if unused.
        channels = sorted(set(channels) | ({trigger_channel} if trigger_channel else set()))
        for channel in channels:
            self.set_channel(channel, True)
            self.set_dc_coupling(channel)
        depth_before = self.query(":ACQuire:MDEPth?")
        self.set_memory_depth(AUTOSCALE_DEPTH)
        probes = {channel: self.query_float(f":CHANnel{channel}:PROBe?") for channel in channels}
        for channel in channels:
            self.write(f":CHANnel{channel}:VERNier OFF")
        for measured in (False, True):
            for channel in channels:
                if not measured:
                    self.set_vertical(channel, AUTOSCALE_START_V_PER_DIV * probes[channel], 0.0)
                else:
                    low, high = levels[channel]
                    swing = max(high - low, AUTOSCALE_MIN_SWING_V)
                    self.set_vertical(channel, standard_scale(swing / AUTOSCALE_DIVISIONS, probes[channel]), -(high + low) / 2)
            abort.sleep(0.3)
            self.single(force_after=0.5)
            for channel in channels:
                volts = self.fetch(channel, AUTOSCALE_DEPTH).volts
                levels[channel] = (float(np.percentile(volts, 0.1)), float(np.percentile(volts, 99.9)))
        if trigger_channel is not None:
            low, high = levels[trigger_channel]
            self.set_edge_trigger(trigger_channel, (low + high) / 2, slope)
        self.set_memory_depth("AUTO" if auto_depth else int(float(depth_before)))
        self.run()
        return levels

    # ---- digital channels and memory files -------------------------------
    def enable_digital(self) -> None:
        """Turn the digital channels on, waiting only when they were off."""
        if self.query(":LA:ENABle?") != "1":
            self.write(":LA:ENABle ON")
            abort.sleep(LA_ENABLE_SETTLE_S)

    def digital_thresholds(self) -> dict:
        return {pod: self.query_float(f":LA:POD{pod}:THReshold?") for pod in DIGITAL_PODS}

    def save_memory(self, progress=None) -> bytes:
        """The whole memory as a BIN file: the only way to the digital channels (see rigol_files).

        :WAVeform:SOURce refuses D0 to D15 with -200 on firmware 00.01.00, and the CSV
        file takes 35 s per million points, against 7 s for this one.
        """
        overlap = self.query(":SAVE:OVERlap?")
        self._ftp_remove(MEMORY_FILE)
        self.write(":SAVE:OVERlap ON")
        try:
            self.write(f":SAVE:MEMory:WAVeform {MEMORY_SCOPE_DIR}/{MEMORY_FILE}")
            abort.sleep(SAVE_START_S)
            started = time.monotonic()
            while self.query(":SAVE:STATus?") != "1" or not self._ftp_exists(MEMORY_FILE):
                if time.monotonic() - started > SAVE_LIMIT_S:
                    raise ScopeError(tr("The scope did not save its memory within {seconds:.0f} s", seconds=SAVE_LIMIT_S))
                abort.sleep(SAVE_POLL_S)
        finally:
            # An abort must not leave the user's own setting changed.
            self.write(f":SAVE:OVERlap {'ON' if overlap == '1' else 'OFF'}")
        data = self._ftp_read(MEMORY_FILE, progress)
        self._ftp_remove(MEMORY_FILE)
        return data

    def _ftp(self) -> ftplib.FTP:
        ftp = ftplib.FTP()
        ftp.connect(self.host, FTP_PORT, timeout=self.timeout)
        ftp.login()
        return ftp

    def _ftp_exists(self, name: str) -> bool:
        try:
            with self._ftp() as ftp:
                return name in ftp.nlst()
        except (OSError, ftplib.Error) as exc:
            raise ScopeError(tr("FTP error: {error}", error=exc)) from exc

    def _ftp_remove(self, name: str) -> None:
        try:
            with self._ftp() as ftp:
                if name in ftp.nlst():
                    ftp.delete(name)
        except (OSError, ftplib.Error) as exc:
            raise ScopeError(tr("FTP error: {error}", error=exc)) from exc

    def _ftp_read(self, name: str, progress=None) -> bytes:
        self._note(f"FTP RETR {name}")
        started = time.monotonic()
        data = bytearray()

        def store(block):
            abort.checkpoint()
            data.extend(block)
            if progress:
                progress(len(data))

        try:
            with self._ftp() as ftp:
                ftp.retrbinary(f"RETR {name}", store, FTP_BLOCK_BYTES)
        except (OSError, ftplib.Error) as exc:
            raise ScopeError(tr("FTP error: {error}", error=exc)) from exc
        self._note(f"< {len(data):,} B, {time.monotonic() - started:.3f} s")
        return bytes(data)

    # ---- AFG ------------------------------------------------------------
    def afg_enable(self, afg: int, enabled: bool) -> None:
        self.write(f":SOURce{afg}:OUTPut:STATe {'ON' if enabled else 'OFF'}")

    def _afg_levels(self, afg: int, low: float, high: float) -> None:
        self.write(f":SOURce{afg}:VOLTage:AMPLitude {high - low:.4f}")
        self.write(f":SOURce{afg}:VOLTage:OFFSet {(high + low) / 2:.4f}")

    def afg_play(self, afg: int, name: str, values: np.ndarray, frequency: float, low: float, high: float,
                 impedance: str = "OMEG") -> None:
        """Output one record cyclically. Values 0 and 1 map to the low and high voltage."""
        path = self.afg_upload(name, values)
        # A device may be connected: the levels go in while the output is still off, so it
        # never sees the amplitude left from earlier use.
        self.afg_enable(afg, False)
        self.write(f":SOURce{afg}:IMPedance {impedance}")
        self._afg_levels(afg, low, high)
        # A load sent while the output is off leaves the previous waveform playing
        # (seen on firmware 00.01.00, with no error reported), so enable it first.
        self.afg_enable(afg, True)
        self.write(f":SOURce{afg}:FUNCtion ARB")
        abort.sleep(AFG_FUNCTION_SETTLE_S)
        self.write(f":SOURce{afg}:LOAD:ARBitrary {path}")
        abort.sleep(AFG_LOAD_SETTLE_S)
        self.write(f":SOURce{afg}:FREQuency {frequency:.6f}")
        self._afg_levels(afg, low, high)
        self.query("*OPC?")

    def afg_align(self) -> None:
        self.write(":SOURce1:PHASe:SYNChronize")
        abort.sleep(AFG_FUNCTION_SETTLE_S)
        self.query("*OPC?")

    def afg_upload(self, name: str, values: np.ndarray) -> str:
        """Store values as a one-column CSV on the scope; return its path there."""
        text = "\r\n".join(f"{v:.6g}" for v in values) + "\r\n"
        self._note(f"FTP STOR {ARB_FTP_DIR}/{name}, {len(text):,} B")
        try:
            with ftplib.FTP() as ftp:
                ftp.connect(self.host, FTP_PORT, timeout=self.timeout)
                ftp.login()
                ftp.storbinary(f"STOR {ARB_FTP_DIR}/{name}", io.BytesIO(text.encode("ascii")))
        except (OSError, ftplib.Error) as exc:
            raise ScopeError(tr("Error sending {name} over FTP: {error}", name=name, error=exc)) from exc
        return f"{ARB_SCOPE_DIR}/{name}"

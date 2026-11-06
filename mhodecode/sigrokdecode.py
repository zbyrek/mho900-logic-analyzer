"""Python replacement for libsigrokdecode's C core, so its protocol decoders run unmodified.

The matching rules of wait() follow type_decoder.c and instance.c of libsigrokdecode. The C core
tests every sample in turn. Here the pins are held as segments of constant level, so a wait only
looks at the samples where its result can change, which keeps long captures usable in Python.
"""
from __future__ import annotations

from bisect import bisect_right
from dataclasses import dataclass, field
from operator import index

import numpy as np

from . import abort

OUTPUT_ANN = 0
OUTPUT_PYTHON = 1
OUTPUT_BINARY = 2
OUTPUT_LOGIC = 3
OUTPUT_META = 4
SRD_CONF_SAMPLERATE = 10000

UNUSED_PIN = 0xFF
ABORT_CHECK_MASK = 0xFFF

_HIGH, _LOW, _RISE, _FALL, _EITHER, _NONE = range(6)
_TERMS = {"h": _HIGH, "l": _LOW, "r": _RISE, "f": _FALL, "e": _EITHER, "n": _NONE}
_EDGE_TERMS = (_RISE, _FALL, _EITHER)


class Decoder:
    """Base class of the decoders. They define no constructor arguments and do not call
    super().__init__(), so the session fills these fields in after creating an instance."""
    samplenum = 0
    matched = None
    _session = None
    _outputs: list = []
    _next: list = []
    # Pin state of a root decoder.
    _length = 0
    _cur = 0
    _seg = 0
    _old: tuple = ()
    _starts: list = [0]
    _pins: list = [()]
    _channel_used: tuple = ()
    _had_conditions = False
    _ticks = 0

    def register(self, output_type, proto_id=None, meta=None):
        proto = type(self).id if proto_id is None else proto_id
        if output_type == OUTPUT_META and (meta is None or meta[0] not in (int, float)):
            raise TypeError("Unsupported type.")
        wanted = (output_type, proto, meta if output_type == OUTPUT_META else None)
        if wanted in self._outputs:
            return self._outputs.index(wanted)
        self._outputs.append(wanted)
        return len(self._outputs) - 1

    def put(self, startsample, endsample, output_id, data):
        startsample, endsample = index(startsample), index(endsample)
        if not 0 <= output_id < len(self._outputs):
            self._session.report(self, f"invalid output ID {output_id}")
            return
        output_type = self._outputs[output_id][0]
        if output_type == OUTPUT_ANN:
            self._session.annotation(self, startsample, endsample, data)
        elif output_type == OUTPUT_PYTHON:
            self._session.python(self, startsample, endsample, data)
        elif output_type == OUTPUT_BINARY:
            self._session.binary(self, startsample, endsample, data)

    def has_channel(self, channel):
        if not 0 <= channel < len(self._channel_used):
            raise IndexError("invalid channel index")
        return self._channel_used[channel]

    def wait(self, conds=None):
        self._ticks += 1
        if not self._ticks & ABORT_CHECK_MASK:
            abort.checkpoint()
        if type(conds) is dict and len(conds) == 1 and "skip" in conds:
            count = conds["skip"]
            if type(count) is not int:
                raise TypeError("skip needs an integer")
            if count < 0:
                return self._scan([(None, None)])
            return self._skip((self._cur + count,), 0)
        if conds is None or (isinstance(conds, (list, dict)) and not conds):
            count = 1 if self._cur or self._had_conditions else 0
            return self._skip((self._cur + count,), 0)
        if isinstance(conds, dict):
            conds = [conds]
        elif not isinstance(conds, list):
            raise TypeError("Condition list is neither a list nor a dict.")
        compiled = [self._compile(cond) for cond in conds]
        if not any(cond for cond in conds):
            # Only empty conditions: an automatic match that does not advance.
            self.samplenum = self._cur
            self.matched = None
            self._had_conditions = True
            return self._pins[self._seg]
        return self._scan(compiled)

    def _compile(self, cond):
        """A condition as (terms, skip target). terms is None for a condition that cannot match."""
        if not isinstance(cond, dict):
            raise TypeError("Condition is not a dict.")
        if not cond:
            return (None, None)
        terms = []
        target = None
        impossible = False
        for key, value in cond.items():
            if isinstance(key, int):
                kind = _TERMS.get(value[0]) if isinstance(value, str) and value else None
                if kind is None or not 0 <= key < len(self._channel_used):
                    impossible = True
                elif self._channel_used[key]:
                    terms.append((key, kind))
                elif kind != _LOW:
                    # An unused pin reads as low and has no history, see term_matches() in C.
                    impossible = True
            elif isinstance(key, str):
                if type(value) is not int:
                    raise TypeError("skip needs an integer")
                if value < 0:
                    impossible = True
                else:
                    target = self._cur + value
            else:
                raise TypeError("Term key is neither a string nor a number.")
        if target is not None and terms:
            # No decoder uses this: the C core counts the skip only while the other terms hold.
            raise ValueError("A condition mixing 'skip' with pin terms is not supported.")
        if impossible:
            return (None, None)
        return (tuple(terms), target)

    def _end_of_samples(self):
        self._cur = self.samplenum = self._length
        raise EOFError("samples exhausted")

    def _skip(self, targets, _unused):
        """A jump with no pin term to look at."""
        target = targets[0]
        if target >= self._length:
            self._end_of_samples()
        self._had_conditions = True
        if target != self._cur:
            self._seg = bisect_right(self._starts, target, self._seg) - 1
            self._cur = target
        new = self._pins[self._seg]
        self._old = new
        self.samplenum = target
        self.matched = (True,)
        return new

    def _scan(self, conds):
        self._had_conditions = True
        length = self._length
        position = self._cur
        if position >= length:
            self._end_of_samples()
        starts = self._starts
        pins = self._pins
        segments = len(starts)
        segment = self._seg
        new = pins[segment]
        old = self._old
        next_change = starts[segment + 1] if segment + 1 < segments else length
        skips = [target for terms, target in conds if terms is not None and target is not None]
        # Without an edge term a condition can also match on a sample after the change.
        quiet_matters = any(terms is not None and target is None
                            and not any(kind in _EDGE_TERMS for _channel, kind in terms)
                            for terms, target in conds)
        while True:
            self._ticks += 1
            if not self._ticks & ABORT_CHECK_MASK:
                abort.checkpoint()
            matched = []
            for terms, target in conds:
                if terms is None:
                    matched.append(False)
                elif target is not None:
                    matched.append(position >= target)
                else:
                    for channel, kind in terms:
                        pin = new[channel]
                        if kind == _HIGH:
                            hit = pin == 1
                        elif kind == _LOW:
                            hit = pin == 0
                        elif kind == _RISE:
                            hit = old[channel] == 0 and pin == 1
                        elif kind == _FALL:
                            hit = old[channel] == 1 and pin == 0
                        elif kind == _EITHER:
                            hit = old[channel] != pin
                        else:
                            hit = old[channel] == pin
                        if not hit:
                            matched.append(False)
                            break
                    else:
                        matched.append(True)
            if True in matched:
                self._cur = self.samplenum = position
                self._seg = segment
                self._old = new
                self.matched = tuple(matched)
                return new
            was_quiet = old is new
            old = new
            if not was_quiet and quiet_matters and position + 1 != next_change:
                if position + 1 >= length:
                    self._end_of_samples()
                position += 1
                continue
            target = next_change
            for skip in skips:
                if position < skip < target:
                    target = skip
            if target >= length:
                self._end_of_samples()
            if target == next_change:
                segment += 1
                new = pins[segment]
                next_change = starts[segment + 1] if segment + 1 < segments else length
            position = target


def _pin_segments(channels: list, count: int):
    """Segment starts and pin tuples of the channels (DigitalTrace or None for an unused one)."""
    used = [trace for trace in channels if trace is not None]
    if used:
        edges = np.unique(np.concatenate([np.asarray(trace.edges, dtype=np.int64) for trace in used]))
    else:
        edges = np.zeros(0, dtype=np.int64)
    edges = edges[(edges > 0) & (edges < count)]
    starts = np.concatenate(([0], edges))
    masks = np.zeros(len(starts), dtype=np.int64)
    for bit, trace in enumerate(channels):
        if trace is not None:
            masks |= trace.levels(starts).astype(np.int64) << bit
    table = {}
    for mask in np.unique(masks).tolist():
        table[mask] = tuple(UNUSED_PIN if trace is None else (mask >> bit) & 1 for bit, trace in enumerate(channels))
    return starts.tolist(), [table[mask] for mask in masks.tolist()]


@dataclass
class Session:
    """One stack of decoders over one capture. Callbacks: annotation(instance, ss, es, class, texts),
    binary(instance, ss, es, class, bytes), python(instance, ss, es, data)."""
    length: int
    samplerate: float | None = None
    on_annotation: object = None
    on_binary: object = None
    on_python: object = None
    instances: list = field(default_factory=list)
    exceptions: list = field(default_factory=list)  # (decoder id, exception)
    problems: list = field(default_factory=list)  # output the C core would log and drop

    def add(self, decoder_class, options: dict | None = None, channels: dict | None = None,
            initial_pins: dict | None = None) -> Decoder:
        """Stack a decoder on the last one. channels and initial_pins go by channel id."""
        instance = decoder_class()
        instance._session = self
        instance._outputs = []
        instance._next = []
        instance.options = resolve_options(decoder_class, options or {})
        if self.instances:
            self.instances[-1]._next = [instance]
        else:
            self._attach_channels(instance, channels or {}, initial_pins or {})
        self.instances.append(instance)
        return instance

    def _attach_channels(self, instance, channels, initial_pins):
        # From the class: several decoders use self.id or self.channels for their own data.
        cls = type(instance)
        specs = tuple(getattr(cls, "channels", ())) + tuple(getattr(cls, "optional_channels", ()))
        for spec in getattr(cls, "channels", ()):
            if spec["id"] not in channels:
                raise ValueError(f"Required channel '{spec['id']}' is not assigned.")
        traces = [channels.get(spec["id"]) for spec in specs]
        instance._channel_used = tuple(trace is not None for trace in traces)
        instance._length = self.length
        instance._starts, instance._pins = _pin_segments(traces, self.length)
        first = instance._pins[0]
        instance._old = tuple(initial_pins.get(spec["id"], first[position]) if first[position] != UNUSED_PIN
                              else UNUSED_PIN for position, spec in enumerate(specs))
        if instance._old == first:
            instance._old = first

    def run(self) -> None:
        """metadata and start on every decoder, decode on the root, then flush."""
        try:
            for instance in self.instances:
                if self.samplerate is not None and hasattr(instance, "metadata"):
                    instance.metadata(SRD_CONF_SAMPLERATE, self.samplerate)
            for instance in self.instances:
                instance.start()
                instance.samplenum = 0
                instance.matched = None
        except abort.OperationAborted:
            raise
        except Exception as exc:  # decoders raise their own classes; the C core logs them all
            self.exceptions.append((type(self.instances[0]).id, exc))
            return
        self._guarded(self.instances[0], self.instances[0].decode)
        for instance in self.instances:
            if hasattr(instance, "flush"):
                self._guarded(instance, instance.flush)

    def _guarded(self, instance, call, *args):
        try:
            call(*args)
        except EOFError:
            pass
        except abort.OperationAborted:
            raise
        except Exception as exc:  # decoders raise their own classes; the C core logs them all
            self.exceptions.append((type(instance).id, exc))

    def report(self, instance, text):
        self.problems.append(f"{type(instance).id}: {text}")

    def annotation(self, instance, start, end, data):
        if (not isinstance(data, list) or len(data) != 2 or not isinstance(data[0], int)
                or not isinstance(data[1], list) or not all(isinstance(text, str) for text in data[1])):
            self.report(instance, "invalid annotation")
            return
        if not 0 <= data[0] < len(getattr(type(instance), "annotations", ())):
            self.report(instance, f"unregistered annotation class {data[0]}")
            return
        if self.on_annotation:
            # The C core hands the texts on as C strings, which end at the first NUL.
            texts = [text.split(chr(0), 1)[0] for text in data[1]]
            self.on_annotation(instance, start, end, data[0], texts)

    def python(self, instance, start, end, data):
        for upper in instance._next:
            self._guarded(upper, upper.decode, start, end, data)
        if self.on_python:
            self.on_python(instance, start, end, data)

    def binary(self, instance, start, end, data):
        if (not isinstance(data, list) or len(data) != 2 or not isinstance(data[0], int)
                or not isinstance(data[1], (bytes, bytearray)) or not data[1]):
            self.report(instance, "invalid binary output")
            return
        if not 0 <= data[0] < len(getattr(type(instance), "binary", ())):
            self.report(instance, f"unregistered binary class {data[0]}")
            return
        if self.on_binary:
            self.on_binary(instance, start, end, data[0], bytes(data[1]))


def resolve_options(decoder_class, given: dict) -> dict:
    """Defaults filled in and types checked, as srd_inst_option_set() does."""
    options = {}
    for spec in getattr(decoder_class, "options", ()):
        value = given.get(spec["id"], spec.get("default"))
        if spec["id"] in given and type(value) is not type(spec.get("default")):
            raise ValueError(f"Option '{spec['id']}' should have the same type as the default value.")
        options[spec["id"]] = value
    return options

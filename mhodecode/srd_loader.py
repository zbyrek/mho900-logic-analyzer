"""Loads the unmodified libsigrokdecode decoders in mhodecode/srd_decoders."""
from __future__ import annotations

import importlib.util
import sys
from collections import deque
from dataclasses import dataclass
from pathlib import Path

from . import sigrokdecode

DECODERS_DIR = Path(__file__).resolve().parent / "srd_decoders"
LOGIC_INPUT = "logic"
_PREFIX = "mhodecode_srd_"


@dataclass(frozen=True)
class DecoderInfo:
    id: str
    name: str
    longname: str
    desc: str
    license: str
    inputs: tuple
    outputs: tuple
    tags: tuple
    channels: tuple
    optional_channels: tuple
    options: tuple
    annotations: tuple
    annotation_rows: tuple
    binary: tuple
    cls: type

    @property
    def needs_base(self) -> bool:
        return LOGIC_INPUT not in self.inputs


def _load_package(module_name: str, folder: Path):
    spec = importlib.util.spec_from_file_location(
        module_name, folder / "__init__.py", submodule_search_locations=[str(folder)])
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    try:
        spec.loader.exec_module(module)
    except BaseException:
        del sys.modules[module_name]
        raise
    return module


def _info(cls) -> DecoderInfo:
    return DecoderInfo(
        id=cls.id, name=cls.name, longname=cls.longname, desc=cls.desc, license=cls.license,
        inputs=tuple(cls.inputs), outputs=tuple(cls.outputs), tags=tuple(getattr(cls, "tags", ())),
        channels=tuple(getattr(cls, "channels", ())), optional_channels=tuple(getattr(cls, "optional_channels", ())),
        options=tuple(getattr(cls, "options", ())), annotations=tuple(getattr(cls, "annotations", ())),
        annotation_rows=tuple(getattr(cls, "annotation_rows", ())), binary=tuple(getattr(cls, "binary", ())), cls=cls)


def load_all() -> tuple[dict[str, DecoderInfo], dict[str, str]]:
    """All decoders by id, and the ones that could not be loaded with the reason."""
    sys.modules.setdefault("sigrokdecode", sigrokdecode)
    if "common" not in sys.modules:
        # The decoders import this helper package by its top-level name.
        _load_package("common", DECODERS_DIR / "common")
    loaded, failed = {}, {}
    for folder in sorted(DECODERS_DIR.iterdir()):
        if folder.name == "common" or not (folder / "pd.py").is_file():
            continue
        try:
            module = _load_package(_PREFIX + folder.name.replace("-", "_"), folder)
            info = _info(module.Decoder)
        except Exception as exc:  # a broken decoder must not take the others down
            failed[folder.name] = f"{type(exc).__name__}: {exc}"
            continue
        loaded[info.id] = info
    return loaded, failed


def base_chain(target: str, decoders: dict[str, DecoderInfo]) -> list[str]:
    """Shortest stack from the logic input up to target, as decoder ids, root first.
    Among equally short ones a decoder named after the protocol it provides goes first."""
    queue = deque([[target]])
    seen = {target}
    while queue:
        chain = queue.popleft()
        info = decoders[chain[0]]
        if not info.needs_base:
            return chain
        for wanted in info.inputs:
            providers = [item.id for item in decoders.values() if wanted in item.outputs]
            for provider in sorted(providers, key=lambda name: (name != wanted, name)):
                if provider not in seen:
                    seen.add(provider)
                    queue.append([provider, *chain])
    raise ValueError(f"No stack leads from the logic input to '{target}'.")


def stackable_on(lower: str, decoders: dict[str, DecoderInfo]) -> list[DecoderInfo]:
    """Decoders that take this decoder's output as their input."""
    outputs = set(decoders[lower].outputs)
    return sorted((item for item in decoders.values() if outputs & set(item.inputs)), key=lambda item: item.name.lower())

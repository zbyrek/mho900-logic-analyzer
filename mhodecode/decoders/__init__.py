from ..srd_loader import load_all
from .sigrok import SigrokDecoder

SIGROK_ALL, SIGROK_FAILED = load_all()
# Calls a native library (libirmp) that libsigrokdecode builds from C and this program does not ship.
_NEEDS_NATIVE_LIBRARY = {"ir_irmp"}
SIGROK = {pd_id: info for pd_id, info in SIGROK_ALL.items() if pd_id not in _NEEDS_NATIVE_LIBRARY}
SIGROK_ROOTS = {}
DECODERS = {}
for _info in sorted(SIGROK.values(), key=lambda item: item.name.lower()):
    if not _info.needs_base:
        SIGROK_ROOTS[_info.id] = SigrokDecoder(_info, SIGROK)
        DECODERS[SIGROK_ROOTS[_info.id].name] = SIGROK_ROOTS[_info.id]

"""User-interface translation. English source strings are the keys of the catalogue."""
from __future__ import annotations

from .i18n_pl import POLISH

LANGUAGES = (("en", "English"), ("pl", "Polski"))
_language = "en"


def set_language(code: str) -> None:
    global _language
    _language = code if code in dict(LANGUAGES) else "en"


def language() -> str:
    return _language


def tr(text: str, /, **values) -> str:
    """Translate text and fill its {placeholders}. A missing entry is a test failure."""
    translated = POLISH.get(text, text) if _language == "pl" else text
    return translated.format(**values) if values else translated

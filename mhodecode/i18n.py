"""User-interface translation. Polish source strings are the keys of the catalogue."""
from __future__ import annotations

from .i18n_en import ENGLISH

LANGUAGES = (("pl", "Polski"), ("en", "English"))
_language = "pl"


def set_language(code: str) -> None:
    global _language
    _language = code if code in dict(LANGUAGES) else "pl"


def language() -> str:
    return _language


def tr(text: str, /, **values) -> str:
    """Translate text and fill its {placeholders}. A missing entry is a test failure."""
    translated = ENGLISH.get(text, text) if _language == "en" else text
    return translated.format(**values) if values else translated

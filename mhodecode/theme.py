"""Light and dark colours of the window and of the waveform view."""
from __future__ import annotations

from dataclasses import dataclass

from PyQt5 import QtGui, QtWidgets


@dataclass(frozen=True)
class Theme:
    canvas: str
    canvas_text: str
    bar_text: str
    error_bar: str
    error_text: str
    warning_text: str
    ok_text: str
    accent: str
    accent_text: str
    cursor: str
    cursor_text: str  # the letter on a cursor's flag
    control_bar: str  # annotations of protocol fields, as against the data
    control_text: str
    channels: dict  # as on the scope's screen; the light theme darkens them, because pure yellow cannot be read on white
    digital: str  # the scope draws all digital channels in one colour
    rows: tuple  # colours of annotation rows, used in turn
    palette: dict | None  # QPalette roles; None keeps the style's own light palette
    disabled_text: str = ""


DARK_PALETTE = {
    "Window": "#2b2e33", "WindowText": "#e3e5e8", "Base": "#1e2024", "AlternateBase": "#26292e",
    "ToolTipBase": "#1e2024", "ToolTipText": "#e3e5e8", "Text": "#e3e5e8", "Button": "#363a40",
    "ButtonText": "#e3e5e8", "BrightText": "#ffffff", "Link": "#6cb2f0", "Highlight": "#2f6db3",
    "HighlightedText": "#ffffff", "Light": "#454950", "Midlight": "#3c4046", "Mid": "#2f3237",
    "Dark": "#1a1c1f", "Shadow": "#101113",
}

THEMES = {
    "light": Theme(
        canvas="#fbfbfc", canvas_text="#3a3f46", bar_text="#14181d",
        error_bar="#f19a9a", error_text="#c0281e", warning_text="#a86400", ok_text="#1e8449",
        accent="#2467b5", accent_text="#ffffff", cursor="#c2006a", cursor_text="#ffffff",
        control_bar="#d9dce1", control_text="#5c626a",
        channels={1: "#a3a300", 2: "#008f8f", 3: "#b800b8", 4: "#0066cc"},
        digital="#028a02",
        rows=("#a9cdf2", "#a8e0b6", "#f6d58f", "#d6bdf3", "#f4b8c9", "#a7e3e0"),
        palette=None),
    "dark": Theme(
        canvas="#15181d", canvas_text="#c8ccd2", bar_text="#ffffff",
        error_bar="#c23b3b", error_text="#ff7b72", warning_text="#f0b94a", ok_text="#5fd38a",
        accent="#2f6db3", accent_text="#ffffff", cursor="#ff5fae", cursor_text="#15181d",
        control_bar="#454a52", control_text="#a4a9b0",
        channels={1: "#ffff00", 2: "#00ffff", 3: "#ff00ff", 4: "#007fff"},
        digital="#04c304",
        rows=("#2f6db3", "#2c8a55", "#a8741a", "#7450b5", "#ad4468", "#22868a"),
        palette=DARK_PALETTE, disabled_text="#7a7e85"),
}
DEFAULT_THEME = "light"


# Fusion's outline of an empty check box almost disappears on a dark window.
CHECK_BOX_STYLE = (
    "QCheckBox::indicator {{ width: 13px; height: 13px; border: 1px solid #8a8f96; border-radius: 2px; }}"
    "QCheckBox::indicator:checked {{ background: {accent}; border-color: #cfd3d8; }}"
)


def apply_palette(theme: Theme) -> None:
    """Fusion draws the same in both themes and follows the palette completely."""
    app = QtWidgets.QApplication.instance()
    if app.style().objectName().lower() != "fusion":  # setting it again repolishes every widget
        app.setStyle("Fusion")
    if theme.palette is None:
        app.setPalette(app.style().standardPalette())
        return
    palette = QtGui.QPalette()
    for role, colour in theme.palette.items():
        palette.setColor(getattr(QtGui.QPalette, role), QtGui.QColor(colour))
    for role in (QtGui.QPalette.WindowText, QtGui.QPalette.Text, QtGui.QPalette.ButtonText):
        palette.setColor(QtGui.QPalette.Disabled, role, QtGui.QColor(theme.disabled_text))
    app.setPalette(palette)


def window_style(theme: Theme) -> str:
    return (
        f"QPushButton#primary:enabled {{ background: {theme.accent}; color: {theme.accent_text}; font-weight: bold; }}"
        f"QPushButton#abort:enabled {{ color: {theme.error_text}; font-weight: bold; }}"
        f"QPushButton#danger {{ color: {theme.error_text}; }}"
        + (CHECK_BOX_STYLE.format(accent=theme.accent) if theme.palette else "")
    )

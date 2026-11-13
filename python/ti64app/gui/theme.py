"""One colour scheme for the whole app: navy top bar, blue primary, white cards on a light grey page.
Contrast checked for text: #1f2937 on #f4f6f9 (14.6:1), white on #1e3a5f (11.6:1), white on #2563eb (5.2:1)."""
from __future__ import annotations

from nicegui import ui

NAVY = "#1e3a5f"
PRIMARY = "#2563eb"
PAGE = "#f4f6f9"
TEXT = "#1f2937"
MUTED = "#5b6676"          # 5.6:1 on white
BORDER = "#d6dde6"
OK = "#15803d"
WARN = "#b45309"
ERR = "#b91c1c"

CSS = f"""
:root {{ --ti-navy: {NAVY}; --ti-primary: {PRIMARY}; --ti-border: {BORDER}; --ti-muted: {MUTED}; }}
body {{ background: {PAGE}; color: {TEXT}; font-family: "Segoe UI", system-ui, sans-serif; }}
.nicegui-content {{ padding: 0; }}
.ti-topbar {{ background: {NAVY}; color: #fff; min-height: 52px; }}
.ti-tab {{ border-radius: 9999px; padding: 4px 16px; font-weight: 600; letter-spacing: .01em; }}
.ti-tab:hover {{ background: rgba(255,255,255,.12); }}

.ti-card {{ background: #fff; border: 1px solid {BORDER}; border-radius: 12px; }}
.ti-muted {{ color: {MUTED}; }}
.ti-section .q-expansion-item__container > .q-item {{ font-weight: 600; min-height: 44px; }}
.ti-section {{ border-bottom: 1px solid {BORDER}; }}
.ti-draft {{ font-size: 11px; color: {WARN}; border: 1px solid {WARN}; border-radius: 9999px; padding: 0 8px;
             white-space: nowrap; }}
.ti-stale {{ font-size: 11px; color: #9a3412; background: #fff1e6; border: 1px solid #f0b48a; border-radius: 9999px;
             padding: 0 8px; white-space: nowrap; }}
.ti-applied {{ font-size: 11px; color: #1e6b3a; border: 1px solid #9fd3b0; border-radius: 9999px; padding: 0 8px;
               white-space: nowrap; }}
.ti-subsection {{ border: 1px solid {BORDER}; border-radius: 8px; margin: 4px 0; background: #fbfcfe; }}
.ti-subsection .q-expansion-item__container > .q-item {{ min-height: 36px; padding: 2px 10px; }}
.ti-inherited input, .ti-inherited .q-field__native {{ color: {MUTED} !important; font-style: italic; }}
.ti-row {{ border-radius: 8px; cursor: pointer; padding: 3px 8px; user-select: none; }}
.ti-row:hover {{ background: #eef3fb; }}
.ti-row.selected {{ background: #dbe7fb; outline: 2px solid {PRIMARY}; }}
.ti-row.missing {{ color: #8a94a3; }}
.ti-row.missing .ti-name {{ text-decoration: line-through; }}
.ti-row.drop-target {{ background: #e0ecff; outline: 2px dashed {PRIMARY}; }}
.ti-group {{ font-size: 10.5px; font-weight: 500; color: #9aa3ad; text-transform: uppercase; letter-spacing: .06em;
             padding: 5px 8px 1px 78px; }}
.ti-folder {{ font-size: 11px; color: #9aa3ad; padding-left: 78px; }}
.ti-tag {{ font-size: 10.5px; line-height: 16px; padding: 0 6px; border-radius: 8px; background: #e8eefb;
          color: #2c4a86; white-space: nowrap; }}
input[type=number]::-webkit-inner-spin-button, input[type=number]::-webkit-outer-spin-button {{ opacity: 1; }}
.q-tooltip {{ font-size: 13px; max-width: 360px; line-height: 1.35; }}
.ti64-showing-original::after {{ content: "Original (unedited)"; position: absolute; top: 36px; left: 50%;
  transform: translateX(-50%); background: {NAVY}; color: #fff; padding: 2px 12px; border-radius: 9999px;
  font: 600 12px "Segoe UI", sans-serif; z-index: 1000; }}
*:focus-visible, .q-btn:focus-visible {{ outline: 3px solid #f59e0b !important; outline-offset: 2px; }}
.q-btn.q-focusable:focus-visible .q-focus-helper {{ opacity: .25 !important; }}
.ti-home h1 {{ font-size: 30px; font-weight: 700; color: {NAVY}; }}
"""


def apply_theme() -> None:
    """Call once at the top of every page."""
    ui.colors(primary=PRIMARY, secondary=NAVY, accent="#0f766e", positive=OK, negative=ERR, warning=WARN,
              info="#1d4ed8", dark=TEXT)
    ui.add_css(CSS)
    ui.query("body").classes("ti-body")


def setup_defaults() -> None:
    """Rounded, flat buttons and outlined dense inputs everywhere (set once before the pages are built)."""
    ui.button.default_props("rounded unelevated no-caps")
    for cls in (ui.number, ui.input, ui.select):
        cls.default_props("outlined dense")

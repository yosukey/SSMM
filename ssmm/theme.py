# theme.py
# Central design tokens and stylesheet assembly for the whole application.
#
# The app renders on top of qdarktheme (pyqtdarktheme). This module layers two
# things on top of that base:
#   1. A single accent (indigo) fed to qdarktheme as its "primary" color, so
#      buttons, tab underlines, selections and focus rings all share one hue.
#   2. An extra QSS block (build_app_qss) that styles the bespoke widgets —
#      step badges, section cards, the button hierarchy and the status bar —
#      entirely from the tokens below, so nothing is hard-coded at call sites.
#
# Keeping every color here means light/dark stay in sync and there is one place
# to adjust the palette. qdarktheme is imported lazily inside apply() so that
# importing this module for its tokens (e.g. from models.assemble_html) does not
# require the Qt widget stack.
from ssmm import app_settings

# Indigo accent, tuned per theme. Also handed to qdarktheme as "primary".
ACCENT = {"light": "#45509c", "dark": "#8b97e6"}

# Full token palette. Neutrals carry a faint warm bias in light and a cool bias
# in dark so they read as chosen rather than default grey.
_TOKENS = {
    "light": {
        "surface": "#ffffff",
        "surface_2": "#f2f0ea",
        "border": "#cbc6ba",
        "border_soft": "#ddd9cf",
        "text": "#23252b",
        "text_muted": "#565a61",
        "text_faint": "#686d76",
        "accent": "#45509c",
        "accent_strong": "#3a4488",
        "accent_tint": "#ecedf7",
        "accent_line": "#c7cbeb",
        "on_accent": "#ffffff",
        "ok": "#357259",
        "ok_tint": "#e7f1ec",
        "ok_line": "#b4d4c3",
        "warn": "#8a6114",
        "warn_tint": "#f6efe0",
        "warn_line": "#dcc491",
        "danger": "#a4423a",
        "danger_tint": "#f7e8e6",
        "danger_line": "#e2afa8",
    },
    "dark": {
        "surface": "#1e2128",
        "surface_2": "#23262e",
        "border": "#2e323b",
        "border_soft": "#282c34",
        "text": "#e7e8eb",
        "text_muted": "#9aa0a9",
        "text_faint": "#6b7078",
        "accent": "#8b97e6",
        "accent_strong": "#a2ade9",
        "accent_tint": "#242844",
        "accent_line": "#343a63",
        "on_accent": "#181b22",
        "ok": "#63b394",
        "ok_tint": "#1c2f28",
        "ok_line": "#2c4a3c",
        "warn": "#cf9d4e",
        "warn_tint": "#322a1a",
        "warn_line": "#4d3f22",
        "danger": "#d97b70",
        "danger_tint": "#33211f",
        "danger_line": "#4d302c",
    },
}


# Media surfaces (waveform / video canvases, transition-GIF previews) stay on a
# dark neutral in both themes, matching the inherently black video area — a
# light canvas would read oddly for moving image content.
MEDIA_BACKDROP = "#1b1d22"
MEDIA_BORDER = "#3a3f4a"
MEDIA_MUTED = "#8b9099"

# Semantic colors for the slide "material type" shown in legends and badges,
# expressed as token names so both themes stay consistent with the app.
_SLIDE_TYPE_TOKENS = {
    "movie": "ok",
    "video": "ok",
    "audio": "accent",
    "silent": "text_faint",
    "unassigned": "warn",
}


def _resolve(theme: str | None) -> str:
    # Concrete themes pass through unchanged.
    if theme in ("light", "dark"):
        return theme
    # None means "whatever the stored preference currently resolves to" — used
    # by tokens()/accent() callers that just want the live palette.
    if theme is None:
        return app_settings.get_effective_theme()
    # An explicit "system" (or anything unexpected) follows the OS appearance
    # directly. We must not route this through get_effective_theme(), which
    # reads the *stored* preference — during a theme switch that value has not
    # been persisted yet, so it would lag one step behind.
    try:
        import darkdetect
        return "dark" if darkdetect.isDark() else "light"
    except Exception:
        return app_settings.get_effective_theme()


def tokens(theme: str | None = None) -> dict:
    return _TOKENS[_resolve(theme)]


def accent(theme: str | None = None) -> str:
    return ACCENT[_resolve(theme)]


def custom_colors(theme: str | None = None) -> dict:
    # qdarktheme keys its accent as "primary".
    return {"primary": accent(theme)}


def slide_type_color(kind: str, theme: str | None = None) -> str:
    """Return the hex color for a slide material type (movie/audio/silent/…)."""
    t = tokens(theme)
    return t[_SLIDE_TYPE_TOKENS.get(kind, "text")]


def build_app_qss(theme: str | None = None) -> str:
    t = tokens(theme)
    return """
    /* --- Section cards (left workflow column) --- */
    QFrame#card {{
        background-color: {surface};
        border: 1px solid {border};
        border-radius: 10px;
    }}

    /* --- Numbered step badges --- */
    QLabel#stepBadge {{
        background-color: {accent_tint};
        color: {accent_strong};
        border: 1px solid {accent_line};
        border-radius: 7px;
        font-weight: 700;
    }}
    QLabel[class="stepLabel"] {{
        font-weight: 600;
    }}

    /* --- Button hierarchy --- */
    QPushButton#primaryButton {{
        background-color: {accent};
        color: {on_accent};
        border: 1px solid {accent};
        border-radius: 7px;
        padding: 7px 16px;
        font-weight: 600;
    }}
    QPushButton#primaryButton:hover {{
        background-color: {accent_strong};
        border-color: {accent_strong};
    }}
    QPushButton#primaryButton:pressed {{
        background-color: {accent_strong};
    }}
    QPushButton#primaryButton:disabled {{
        background-color: {border};
        color: {text_faint};
        border-color: {border};
    }}

    QPushButton#secondaryButton {{
        background-color: transparent;
        color: {accent};
        border: 1px solid {accent_line};
        border-radius: 7px;
        padding: 7px 14px;
        font-weight: 600;
    }}
    QPushButton#secondaryButton:hover {{
        background-color: {accent_tint};
    }}
    QPushButton#secondaryButton:disabled {{
        color: {text_faint};
        border-color: {border};
    }}

    QPushButton#ghostButton {{
        background-color: transparent;
        color: {text_muted};
        border: 1px solid {border};
        border-radius: 7px;
        padding: 7px 14px;
    }}
    QPushButton#ghostButton:hover {{
        color: {danger};
        border-color: {danger_line};
        background-color: {danger_tint};
    }}
    QPushButton#ghostButton:disabled {{
        color: {text_faint};
        border-color: {border};
    }}

    QPushButton#resetDefaultsButton {{
        background-color: transparent;
        color: {text_muted};
        border: 1px solid {border};
        border-radius: 6px;
        padding: 3px 10px;
    }}
    QPushButton#resetDefaultsButton:hover {{
        color: {text};
        border-color: {accent_line};
    }}

    /* --- Status bar (right pane) — severity set as a dynamic property --- */
    QLabel#statusBar {{
        border-radius: 8px;
        padding: 9px 13px;
        font-weight: 600;
        background-color: {surface_2};
        color: {text_muted};
        border: 1px solid {border};
    }}
    QLabel#statusBar[severity="working"] {{
        background-color: {accent_tint};
        border-color: {accent_line};
        color: {text};
    }}
    QLabel#statusBar[severity="success"] {{
        background-color: {ok_tint};
        border-color: {ok_line};
        color: {text};
    }}
    QLabel#statusBar[severity="error"] {{
        background-color: {danger_tint};
        border-color: {danger_line};
        color: {text};
    }}

    /* --- "Report may be outdated" warning banner --- */
    QLabel#validationWarningLabel {{
        background-color: {warn_tint};
        color: {text};
        border: 1px solid {warn_line};
        border-left: 3px solid {warn};
        border-radius: 8px;
        padding: 9px 12px;
        font-weight: 600;
    }}

    /* --- Clickable folder path labels --- */
    QLabel#projectFolderLabel, QLabel#outputFolderLabel {{
        color: {accent};
        text-decoration: underline;
        padding: 2px 0;
    }}

    /* --- Transition-effect GIF preview tile --- */
    QLabel#gifPreview {{
        border: 1px solid {border};
        border-radius: 6px;
        background-color: {surface_2};
    }}
    """.format(**t)


def apply(app, theme_code: str) -> str:
    """Apply the base qdarktheme stylesheet (with our accent) plus the bespoke
    app QSS to the given QApplication. Returns the resolved concrete theme."""
    import qdarktheme  # lazy: only needed when actually styling a live app

    theme_arg = app_settings.theme_to_stylesheet_arg(theme_code)
    effective = _resolve(theme_code)
    base = qdarktheme.load_stylesheet(theme_arg, custom_colors=custom_colors(effective))
    app.setStyleSheet(base + build_app_qss(effective))
    return effective

# app_settings.py
# Application-wide user preferences, persisted via QSettings in INI format.
# This is intentionally separate from settings_manager.py, which handles
# per-project settings stored as TOML inside each project folder.
from pathlib import Path

from PySide6.QtCore import QByteArray, QLocale, QSettings, QStandardPaths

LANGUAGE_KEY = "ui/language"
DEFAULT_LANGUAGE = "system"
VALID_LANGUAGES = ("system", "en", "ja")

THEME_KEY = "ui/theme"
DEFAULT_THEME = "system"
VALID_THEMES = ("system", "light", "dark")

RECENT_PROJECTS_KEY = "recent/projects"
MAX_RECENT = 5

WINDOW_GEOMETRY_KEY = "window/geometry"

LAST_DIR_KEY = "paths/last_dir"
VALID_DIR_KINDS = ("project", "output")


def _settings_path() -> str:
    # Use the same QStandardPaths family already relied on elsewhere in the app.
    base = QStandardPaths.writableLocation(QStandardPaths.StandardLocation.AppConfigLocation)
    # AppConfigLocation may be empty in rare/headless environments; fall back to home.
    if not base:
        base = str(Path.home() / ".config")
    return str(Path(base) / "SSMM" / "settings.ini")


def _settings() -> QSettings:
    return QSettings(_settings_path(), QSettings.Format.IniFormat)


def get_language() -> str:
    value = _settings().value(LANGUAGE_KEY, DEFAULT_LANGUAGE)
    if value not in VALID_LANGUAGES:
        return DEFAULT_LANGUAGE
    return value


def set_language(code: str) -> None:
    if code not in VALID_LANGUAGES:
        code = DEFAULT_LANGUAGE
    settings = _settings()
    settings.setValue(LANGUAGE_KEY, code)
    settings.sync()


def resolve_qlocale(code: str) -> QLocale:
    if code == "en":
        return QLocale("en")
    if code == "ja":
        return QLocale("ja")
    # "system" or any unexpected value falls back to the OS locale.
    return QLocale.system()


# --- Theme ------------------------------------------------------------------

def get_theme() -> str:
    value = _settings().value(THEME_KEY, DEFAULT_THEME)
    if value not in VALID_THEMES:
        return DEFAULT_THEME
    return value


def set_theme(code: str) -> None:
    if code not in VALID_THEMES:
        code = DEFAULT_THEME
    settings = _settings()
    settings.setValue(THEME_KEY, code)
    settings.sync()


def theme_to_stylesheet_arg(code: str) -> str:
    # qdarktheme uses "auto" to follow the OS appearance.
    return {"light": "light", "dark": "dark"}.get(code, "auto")


# --- Recent projects --------------------------------------------------------

def get_recent_projects() -> list[str]:
    value = _settings().value(RECENT_PROJECTS_KEY, [])
    # QSettings/INI may return a single string when only one value is stored,
    # or None when unset; normalize to a list of strings.
    if value is None:
        items = []
    elif isinstance(value, str):
        items = [value]
    else:
        items = list(value)
    # Keep only paths that still exist on disk, preserving order, capped.
    existing = [p for p in items if p and Path(p).exists()]
    return existing[:MAX_RECENT]


def add_recent_project(path: str) -> None:
    path = str(path)
    # Read raw stored values (not the existence-filtered view) so a temporarily
    # missing entry isn't silently dropped here; filtering happens on read.
    settings = _settings()
    raw = settings.value(RECENT_PROJECTS_KEY, [])
    if raw is None:
        items = []
    elif isinstance(raw, str):
        items = [raw]
    else:
        items = list(raw)
    # Move-to-front (most-recently-used), de-duplicated.
    items = [p for p in items if p != path]
    items.insert(0, path)
    settings.setValue(RECENT_PROJECTS_KEY, items[:MAX_RECENT])
    settings.sync()


def clear_recent_projects() -> None:
    settings = _settings()
    settings.remove(RECENT_PROJECTS_KEY)
    settings.sync()


# --- Window geometry --------------------------------------------------------

def get_window_geometry() -> QByteArray | None:
    value = _settings().value(WINDOW_GEOMETRY_KEY)
    if isinstance(value, QByteArray) and not value.isEmpty():
        return value
    return None


def set_window_geometry(geometry: QByteArray) -> None:
    settings = _settings()
    settings.setValue(WINDOW_GEOMETRY_KEY, geometry)
    settings.sync()


# --- Last-used directories --------------------------------------------------

def get_last_dir(kind: str, fallback: str = "") -> str:
    if kind not in VALID_DIR_KINDS:
        return fallback
    value = _settings().value(f"{LAST_DIR_KEY}/{kind}", "")
    if value and Path(value).is_dir():
        return value
    return fallback


def set_last_dir(kind: str, path: str) -> None:
    if kind not in VALID_DIR_KINDS:
        return
    settings = _settings()
    settings.setValue(f"{LAST_DIR_KEY}/{kind}", str(path))
    settings.sync()

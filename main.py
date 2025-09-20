# main.py
import sys
import argparse
from pathlib import Path

SHOULD_SHOW_SPLASH = not (getattr(sys, 'frozen', False) and sys.platform == 'darwin')
is_pyinstaller_bundle = getattr(sys, 'frozen', False)
if is_pyinstaller_bundle:
    try:
        import pyi_splash
        if not SHOULD_SHOW_SPLASH:
            pyi_splash.close()
        else:
            pyi_splash.update_text("Loading application, please wait...")

    except (ImportError, KeyError):
        pass

from PySide6.QtWidgets import QApplication, QSplashScreen
from PySide6.QtGui import QIcon, QPixmap
from PySide6.QtCore import Qt, QTimer
import qdarktheme
from main_window import MainWindow, EmittingStream
from utils import resolve_resource_path

MIN_PYTHON_VERSION = (3, 10)

def check_python_version():
    if sys.version_info < MIN_PYTHON_VERSION:
        required_version = ".".join(map(str, MIN_PYTHON_VERSION))
        current_version = f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}"
        
        sys.stderr.write(
            f"\n[ERROR] Incompatible Python Version.\n"
            f"This application requires Python {required_version} or newer.\n"
            f"You are running version {current_version}.\n\n"
            f"Please upgrade your Python interpreter to run this application.\n"
        )
        sys.exit(1)

class SplashScreenManager:
    def __init__(self, splash_screen, main_window):
        self.splash = splash_screen
        self.window = main_window
        self.app_is_ready = False
        self.timer_is_done = False

    def set_app_ready(self):
        self.app_is_ready = True
        self.close_if_ready()

    def set_timer_done(self):
        self.timer_is_done = True
        self.close_if_ready()

    def close_if_ready(self):
        if self.app_is_ready and self.timer_is_done:
            self.splash.finish(self.window)


if __name__ == "__main__":
    check_python_version()

    parser = argparse.ArgumentParser(description="Simple Slideshow Movie Maker")
    parser.add_argument(
        '-v', '--verbose',
        action='store_true',
        help='Enable verbose logging on startup'
    )
    parser.add_argument(
        'project_path',
        nargs='?',
        type=Path,
        default=None,
        help='Optional path to a project folder or .toml file to load on startup.'
    )
    args = parser.parse_args()

    app = QApplication(sys.argv)
    QApplication.instance().setStyleSheet(qdarktheme.load_stylesheet("auto"))

    splash = None
    manager = None

    if SHOULD_SHOW_SPLASH:
        if not is_pyinstaller_bundle:
            try:
                splash_pix = QPixmap(str(resolve_resource_path("assets/splash_screen.png")))
                if not splash_pix.isNull():
                    splash = QSplashScreen(splash_pix, Qt.WindowStaysOnTopHint)
                    splash.show()
                    splash.showMessage("Loading application, please wait...", Qt.AlignBottom | Qt.AlignCenter, Qt.white)
            except Exception as e:
                print(f"Could not create QSplashScreen: {e}")
        else:
            pass

    window = MainWindow(
        verbose_startup=args.verbose,
        project_path_on_startup=args.project_path
    )

    pyi_window_ready = False
    pyi_timer_done = False

    def close_pyi_splash_if_ready():
        if pyi_window_ready and pyi_timer_done:
            try:
                import pyi_splash
                pyi_splash.close()
            except (ImportError, KeyError, RuntimeError):
                pass
    
    def on_pyi_timer_done():
        global pyi_timer_done
        pyi_timer_done = True
        close_pyi_splash_if_ready()

    if splash:
        manager = SplashScreenManager(splash, window)
        QTimer.singleShot(4000, manager.set_timer_done)

    try:
        icon_path = resolve_resource_path("assets/app_icon.png")
        if icon_path.exists():
            app_icon = QIcon(str(icon_path))
            window.setWindowIcon(app_icon)
    except Exception as e:
        print(f"Could not load application icon: {e}")

    original_stdout = sys.stdout
    original_stderr = sys.stderr
    stdout_stream = EmittingStream()
    stdout_stream.text_written.connect(window.write_debug)
    stderr_stream = EmittingStream()
    stderr_stream.text_written.connect(window.write_debug)
    sys.stdout = stdout_stream
    sys.stderr = stderr_stream

    window.show()

    if SHOULD_SHOW_SPLASH:
        if is_pyinstaller_bundle:
            QTimer.singleShot(4000, on_pyi_timer_done)
            pyi_window_ready = True
            close_pyi_splash_if_ready()
        elif manager:
            manager.set_app_ready()

    exit_code = 0
    try:
        exit_code = app.exec()
    finally:
        sys.stdout = original_stdout
        sys.stderr = original_stderr

    sys.exit(exit_code)
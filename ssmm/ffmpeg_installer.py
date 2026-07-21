# ffmpeg_installer.py
import os
import platform
import subprocess
import threading
from pathlib import Path
from PySide6.QtCore import QThread, Signal
from ssmm import config

class FFmpegInstaller(QThread):
    log_message = Signal(str)
    # Named install_finished (not 'finished') so it does not shadow QThread.finished,
    # which callers rely on for thread lifetime management.
    install_finished = Signal(bool, str)

    def _get_windows_winget_path(self) -> Path | None:
        winget_path_str = os.path.expandvars(r'%LOCALAPPDATA%\Microsoft\WindowsApps\winget.exe')
        winget_path = Path(winget_path_str)
        if winget_path.exists():
            return winget_path
        return None

    def _get_tool_executable_path(self, name: str) -> str | None:
        try:
            subprocess.run([name, '--version'], check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                           creationflags=config.SUBPROCESS_CREATION_FLAGS, timeout=10)
            return name
        except (OSError, subprocess.CalledProcessError, subprocess.TimeoutExpired):
            if platform.system() == 'Windows' and name == 'winget':
                winget_path = self._get_windows_winget_path()
                if winget_path:
                    try:
                        subprocess.run([str(winget_path), '--version'], check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                       creationflags=config.SUBPROCESS_CREATION_FLAGS, timeout=10)
                        return str(winget_path)
                    except (OSError, subprocess.CalledProcessError, subprocess.TimeoutExpired):
                        pass

            if platform.system() == 'Darwin' and name == 'brew':
                # A frozen .app launched from Finder inherits the minimal launchd PATH
                # (/usr/bin:/bin:/usr/sbin:/sbin), which omits Homebrew's install dirs,
                # so `brew` on PATH fails even when it is installed. Probe the standard
                # Apple-silicon and Intel locations before giving up.
                for candidate in ('/opt/homebrew/bin/brew', '/usr/local/bin/brew'):
                    brew_path = Path(candidate)
                    if brew_path.exists():
                        try:
                            subprocess.run([str(brew_path), '--version'], check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                           creationflags=config.SUBPROCESS_CREATION_FLAGS, timeout=10)
                            return str(brew_path)
                        except (OSError, subprocess.CalledProcessError, subprocess.TimeoutExpired):
                            pass

            return None

    def run(self):
        system = platform.system()
        try:
            if system == 'Windows':
                self.log_message.emit(self.tr("Checking for winget package manager..."))
                winget_executable = self._get_tool_executable_path('winget')
                if not winget_executable:
                    raise RuntimeError(
                        self.tr("The 'winget' command was not found. "
                        "Please install or update 'App Installer' from the Microsoft Store and try again.\n\n"
                        "You can find it here:\n"
                        "https://apps.microsoft.com/store/detail/app-installer/9NBLGGH4NNS1")
                    )
                
                self.log_message.emit(self.tr("Installing FFmpeg using winget..."))
                # Pin the source and pre-accept the agreements; on a fresh account
                # winget otherwise prompts on stdout ("Do you agree...? [Y/N]") and,
                # with no usable stdin, blocks forever behind the modal progress dialog.
                command = [
                    winget_executable, 'install', '-e', '--id', 'Gyan.FFmpeg',
                    '--source', 'winget',
                    '--accept-source-agreements', '--accept-package-agreements',
                    '--disable-interactivity',
                ]
                self._run_command(command)

            elif system == 'Darwin':
                self.log_message.emit(self.tr("Checking for Homebrew package manager..."))
                brew_executable = self._get_tool_executable_path('brew')
                if not brew_executable:
                    raise RuntimeError(
                        self.tr("Homebrew is not installed. "
                        "Please install it by following the instructions at https://brew.sh, then try again.")
                    )
                
                self.log_message.emit(self.tr("Installing FFmpeg using Homebrew..."))
                self._run_command([brew_executable, 'install', 'ffmpeg'])

            elif system == 'Linux':
                self.log_message.emit(self.tr("Manual installation required for Linux."))
                raise NotImplementedError(
                    self.tr("On Debian/Ubuntu, please run this command in your terminal:\n\n"
                    "sudo apt update && sudo apt install ffmpeg\n\n"
                    "For other distributions, please use your system's package manager.")
                )
            else:
                raise OSError(self.tr("Unsupported OS: {0}").format(system))

            self.install_finished.emit(True, self.tr("Installation process finished."))

        except Exception as e:
            self.install_finished.emit(False, self.tr("An error occurred: {0}").format(e))

    def _run_command(self, command):
        process = subprocess.Popen(
            command,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding='utf-8',
            errors='replace',
            creationflags=config.SUBPROCESS_CREATION_FLAGS
        )

        # Watchdog: an installer that hangs (e.g. waiting on unexpected input) would
        # otherwise block the readline loop forever behind the modal dialog. Killing it
        # closes stdout, ending the loop; the timed_out flag then raises below.
        timed_out = threading.Event()

        def _kill_on_timeout():
            timed_out.set()
            try:
                process.kill()
            except OSError:
                pass

        watchdog = threading.Timer(config.FFMPEG_INSTALL_TIMEOUT_S, _kill_on_timeout)
        watchdog.start()
        try:
            if process.stdout:
                for line in iter(process.stdout.readline, ''):
                    if line:
                        self.log_message.emit(line.strip())
            process.wait()
        finally:
            watchdog.cancel()

        if timed_out.is_set():
            raise subprocess.TimeoutExpired(command, config.FFMPEG_INSTALL_TIMEOUT_S)
        if process.returncode != 0:
            raise subprocess.CalledProcessError(process.returncode, command, self.tr("Process exited with a non-zero status."))
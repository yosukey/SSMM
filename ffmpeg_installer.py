# ffmpeg_installer.py
import os
import platform
import subprocess
from pathlib import Path
from PySide6.QtCore import QThread, Signal
import config

class FFmpegInstaller(QThread):
    log_message = Signal(str)
    finished = Signal(bool, str)

    def _get_windows_winget_path(self) -> Path | None:
        winget_path_str = os.path.expandvars(r'%LOCALAPPDATA%\Microsoft\WindowsApps\winget.exe')
        winget_path = Path(winget_path_str)
        if winget_path.exists():
            return winget_path
        return None

    def _get_tool_executable_path(self, name: str) -> str | None:
        try:
            subprocess.run([name, '--version'], check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            return name
        except (OSError, subprocess.CalledProcessError):
            if platform.system() == 'Windows' and name == 'winget':
                winget_path = self._get_windows_winget_path()
                if winget_path:
                    try:
                        subprocess.run([str(winget_path), '--version'], check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                        return str(winget_path)
                    except (OSError, subprocess.CalledProcessError):
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
                command = [winget_executable, 'install', '-e', '--id', 'Gyan.FFmpeg']
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

            self.finished.emit(True, self.tr("Installation process finished."))

        except Exception as e:
            self.finished.emit(False, self.tr("An error occurred: {0}").format(e))

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

        if process.stdout:
            for line in iter(process.stdout.readline, ''):
                if line:
                    self.log_message.emit(line.strip())

        process.wait()
        if process.returncode != 0:
            raise subprocess.CalledProcessError(process.returncode, command, self.tr("Process exited with a non-zero status."))
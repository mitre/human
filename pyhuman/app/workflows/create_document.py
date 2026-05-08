import os
import platform
import shutil
import subprocess
import time
from pathlib import Path
from time import sleep

import pyautogui
from lorem.text import TextLorem

from ..utility.base_workflow import BaseWorkflow

WORKFLOW_NAME = 'CreateDocument'
WORKFLOW_DESCRIPTION = 'Open Writer/Notepad, type lorem ipsum, save to Documents folder'
DEFAULT_WAIT_TIME = 2


def load():
    return CreateDocument()


class CreateDocument(BaseWorkflow):

    def __init__(self, default_wait_time=DEFAULT_WAIT_TIME):
        super().__init__(name=WORKFLOW_NAME, description=WORKFLOW_DESCRIPTION)
        self.default_wait_time = default_wait_time

    def action(self, extra=None):
        documents_dir = self._documents_dir()
        documents_dir.mkdir(parents=True, exist_ok=True)
        filename = f'note_{int(time.time())}.txt'
        filepath = documents_dir / filename

        editor_proc = self._launch_editor(filepath)
        sleep(self.default_wait_time * 2)

        # Type a paragraph; same lorem pattern open_office_writer uses.
        pyautogui.typewrite(TextLorem().paragraph(), interval=0.02)
        sleep(self.default_wait_time)

        # Ctrl+S works in Writer and Notepad alike; Notepad's Save dialog
        # is bypassed because we passed the path on the command line.
        pyautogui.hotkey('ctrl', 's')
        sleep(self.default_wait_time)
        pyautogui.press('enter')
        sleep(self.default_wait_time)

        self._close_editor(editor_proc)
        print(f'Saved document to {filepath}')

    @staticmethod
    def _documents_dir() -> Path:
        home = Path.home()
        candidate = home / 'Documents'
        return candidate if candidate.exists() or platform.system() == 'Windows' else home

    def _launch_editor(self, filepath: Path):
        if platform.system() == 'Windows':
            return subprocess.Popen(['notepad.exe', str(filepath)])
        # Linux / macOS: prefer LibreOffice Writer, fall back to a plain
        # editor only if LO isn't on PATH.
        soffice = shutil.which('soffice') or shutil.which('libreoffice')
        if soffice:
            return subprocess.Popen([soffice, '--writer', str(filepath)])
        gedit = shutil.which('gedit') or shutil.which('xdg-open')
        if gedit:
            return subprocess.Popen([gedit, str(filepath)])
        # Last resort: just create the file so the workflow doesn't fail.
        filepath.touch()
        return None

    def _close_editor(self, proc):
        try:
            pyautogui.hotkey('ctrl', 'q' if platform.system() != 'Windows' else 'w')
        except Exception:
            pass
        sleep(self.default_wait_time)
        if proc and proc.poll() is None:
            proc.terminate()

import os
import pyautogui
from time import sleep

from ..utility.base_workflow import BaseWorkflow

WORKFLOW_NAME = 'MicrosoftPaint'
WORKFLOW_DESCRIPTION = 'Create a blank MS Paint file (Windows)'

DEFAULT_INPUT_WAIT_TIME = 2


def load():
    return msPaint()


class msPaint(BaseWorkflow):

    def __init__(self, input_wait_time=DEFAULT_INPUT_WAIT_TIME):
        super().__init__(name=WORKFLOW_NAME, description=WORKFLOW_DESCRIPTION)
        self.input_wait_time = input_wait_time

    def action(self, extra=None):
        self._ms_paint()


    """ PRIVATE """

    @staticmethod
    def _ms_paint(self):
        paint_path = "C:\Windows\System32\mspaint.exe"
        os.startfile(paint_path)
        pyautogui.getWindowsWithTitle("Paint")
        sleep(self.input_wait_time)
        pyautogui.hotkey('ctrl', 's')
        file_name = int(time.time())
        sleep(self.input_wait_time)
        pyautogui.typewrite(str(file_name))
        sleep(self.input_wait_time)
        pyautogui.press('enter')
        sleep(self.input_wait_time)
        pyautogui.hotkey('alt', 'f4')

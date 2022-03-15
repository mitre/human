import os
import time
import pyautogui

from ..utility.base_workflow import BaseWorkflow
# from ..utility.webdriver_helper import WebDriverHelper


WORKFLOW_NAME = 'MicrosoftPaint'
WORKFLOW_DESCRIPTION = 'Create a blank MS Paint file'

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
    def _ms_paint():
        paint_path = "C:\Windows\System32\mspaint.exe"
        os.startfile(paint_path)
        # time.sleep(1)
        # pyautogui.click()
        pyautogui.hotkey('ctrl', 's')
        file_name = int(time.time())
        pyautogui.typewrite(str(file_name))
        # time.sleep(1)
        pyautogui.press('enter')
        pyautogui.hotkey('alt', 'f4')




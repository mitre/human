from ..utility.base_workflow import BaseWorkflow
import lorem
from lorem.text import TextLorem
import os
import pyautogui
from time import sleep
import random


WORKFLOW_NAME = 'OpenOfficeCalc'
WORKFLOW_DESCRIPTION = 'Create spreadsheets with Apache OpenOffice Calc (Windows)'


sleeptime = 2
openoffice_path = "C:\Program Files (x86)\OpenOffice 4\program\soffice"

def load():
    return DocumentManipulation()

class DocumentManipulation(BaseWorkflow):

    def __init__(self):
        super().__init__(name=WORKFLOW_NAME, description=WORKFLOW_DESCRIPTION)

    def action(self, extra=None):
        self._create_spreadsheet()

    def insert_comment(self):
        print("Inserting a comment")
        pyautogui.hotkey('ctrl', 'alt', 'c')
        pyautogui.typewrite(TextLorem().sentence())
        pyautogui.press('esc')
        sleep(sleeptime)
        print("...Done")

    def find(self):
        pyautogui.hotkey('ctrl', 'f')
        sleep(sleeptime)
        pyautogui.typewrite(TextLorem()._word())
        sleep(sleeptime)
        pyautogui.press('enter')
        sleep(sleeptime)
        pyautogui.hotkey('alt','y')
        sleep(sleeptime)
        pyautogui.hotkey('alt','c')
        sleep(sleeptime)



    def new_spreadsheet(self):
        # Open new spreadsheet in OpenOffice
        os.startfile(openoffice_path)
        sleep(sleeptime)
        pyautogui.press('s') 
        sleep(sleeptime)


    def save_quit(self):
        pyautogui.hotkey('ctrl', 's')
        sleep(sleeptime)
        pyautogui.typewrite(TextLorem(wsep='-', srange=(1,3)).sentence()[:-1])
        sleep(sleeptime)
        pyautogui.press('enter') 
        pyautogui.hotkey('alt','y')
        sleep(sleeptime)
        pyautogui.hotkey('ctrl','q')

    def move_to_cell(self, cell_coordinate):
        pyautogui.press('f5')
        pyautogui.hotkey('ctrl','a')
        pyautogui.write(str(cell_coordinate[0]))
        pyautogui.press('tab')
        pyautogui.write(str(cell_coordinate[1]))
        pyautogui.press('enter')
        pyautogui.press('f5')

    def insert_table(self):
        row_length = random.randint(3,10)
        for i in range(0, row_length):
            pyautogui.write(TextLorem()._word())
            pyautogui.press('tab')
        for j in range(0, random.randint(3,10)):
            pyautogui.press('enter')
            for k in range(0, row_length):
                pyautogui.write(str(random.randint(0,10000)))
                pyautogui.press('tab')



    def _create_spreadsheet(self):
        self.new_spreadsheet()
        self.move_to_cell([random.choice('abcde'),random.randrange(6)])
        sleep(1)
        self.insert_table()
        sleep(1)
        self.move_to_cell([random.choice('abcdefghijkl'),random.randrange(15)])
        self.insert_comment()
        sleep(3)
        # find()/


        self.save_quit()





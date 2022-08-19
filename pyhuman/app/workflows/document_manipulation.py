from ..utility.base_workflow import BaseWorkflow
import lorem
from lorem.text import TextLorem
import os
import pyautogui
from time import sleep
import random


WORKFLOW_NAME = 'DocumentManipulation'
WORKFLOW_DESCRIPTION = 'Create documents with Apache OpenOffice (Windows)'


sleeptime = 2
openoffice_path = "C:\Program Files (x86)\OpenOffice 4\program\soffice"

def load():
    return DocumentManipulation()

class DocumentManipulation(BaseWorkflow):

    def __init__(self):
        super().__init__(name=WORKFLOW_NAME, description=WORKFLOW_DESCRIPTION)

    def action(self, extra=None):
        self._create_document()

    def _create_document(self):
        self.new_document()
        # Type random paragrahs and sentences
        for i in range(0, random.randint(2,10)):
            random.choice([pyautogui.typewrite(TextLorem().paragraph()), pyautogui.typewrite(TextLorem().sentence())])
            pyautogui.press('enter')
        sleep(sleeptime)
        # Randomly perform actions
        for i in range(0, random.randint(6,15)):
            random.choice([
                self.write_sentence,
                self.write_paragraph,
                self.copy_paste, 
                self.insert_comment,
                self.find,
                self.delete_text,
                self.format_text])()
            sleep(sleeptime)
        # Save and quit the document
        self.save_quit()


    def insert_comment(self):
        pyautogui.hotkey('ctrl', 'alt', 'c')
        pyautogui.typewrite(TextLorem().sentence())
        pyautogui.press('esc')
        sleep(sleeptime)

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

    def copy_paste(self):
        self.select_text()
        sleep(sleeptime)
        pyautogui.hotkey('ctrl', 'c')
        sleep(sleeptime)
        pyautogui.press('backspace')
        sleep(sleeptime)
        pyautogui.typewrite(TextLorem().paragraph())
        sleep(sleeptime)
        pyautogui.press('enter')
        pyautogui.press('enter')
        pyautogui.hotkey('ctrl', 'v')
        sleep(sleeptime)

    def select_text(self):
        selection_params = [
            ['ctrl'  , 'home'],
            ['shift' , 'left'],
            ['shift' , 'up']
        ]
        pyautogui.hotkey(*random.choice(selection_params)) 

    def format_text(self):
        self.select_text()
        sleep(sleeptime)
        formatting_params = [
        ['ctrl','1'], # Apply heading 1 style
        ['ctrl','2'], # Apply heading 2 style
        ['ctrl','3'], # Apply heading 3 style
        ['ctrl','d'], # Double underline
        ['ctrl','e'], # Center
        ['ctrl','5'] # Set 1.5 line spacing
        ]
        pyautogui.hotkey(*random.choice(formatting_params))
        sleep(sleeptime)

    def delete_text(self):
        print("Deleting")
        pyautogui.hotkey('ctrl', 'shift', 'delete') # Delete text to beginning of line
        pyautogui.hotkey('ctrl', 'backspace') # Delete text to beginning of word 
        print("..done")

    def save_pdf(self):
        # Export a pdf
        pyautogui.hotkey('alt','f')
        pyautogui.hotkey('alt','d')
        pyautogui.press('enter')
        pyautogui.hotkey('alt','x')
        pyautogui.typewrite(TextLorem(wsep='-', srange=(1,3)).sentence()[:-1])
        sleep(sleeptime)
        pyautogui.press('enter') 
        pyautogui.hotkey('alt','y')
        sleep(sleeptime)
        pyautogui.hotkey('ctrl','q')


    def new_document(self):
        # Open new document in OpenOffice
        os.startfile(openoffice_path)
        sleep(sleeptime)
        pyautogui.press('d') 
        sleep(sleeptime)
        # pyautogui.hotkey('ctrl','shift', 'j') # full screen mode


    def save_quit(self):
        pyautogui.hotkey('ctrl', 's')
        sleep(sleeptime)
        pyautogui.typewrite(TextLorem(wsep='-', srange=(1,3)).sentence()[:-1])
        sleep(sleeptime)
        pyautogui.press('enter') 
        pyautogui.hotkey('alt','y')
        sleep(sleeptime)
        pyautogui.hotkey('ctrl','q')

    def write_paragraph(self):
        pyautogui.typewrite(TextLorem().paragraph()), 
    def write_sentence(self):
        pyautogui.typewrite(TextLorem().sentence()), 








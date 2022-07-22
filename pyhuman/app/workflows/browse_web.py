import os
import random
from time import sleep

from ..utility.base_workflow import BaseWorkflow
from ..utility.webdriver_helper import WebDriverHelper
from selenium.webdriver.common.by import By

WORKFLOW_NAME = 'WebBrowser'
WORKFLOW_DESCRIPTION = 'Select a random website and browse'

DEFAULT_INPUT_WAIT_TIME = 2
MAX_NAVIGATION_CLICKS = 5
DEFAULT_WAIT_TIME = 2

def load():
    driver = WebDriverHelper()
    return WebBrowse(driver=driver)


class WebBrowse(BaseWorkflow):

    def __init__(self, driver, input_wait_time=DEFAULT_INPUT_WAIT_TIME):
        super().__init__(name=WORKFLOW_NAME, description=WORKFLOW_DESCRIPTION, driver=driver)

        self.input_wait_time = input_wait_time
        self.website_list = self._load_website_list()

    def action(self, extra=None):
        self._web_browse()

    """ PRIVATE """

    def _web_browse(self):
        random_website = self._get_random_website()
        print("Browsing to", random_website)
        try:
            self.driver.driver.get('https://' + random_website)
            sleep(self.input_wait_time)
            self._navigate_webpage()
        except Exception as e:
            print('Error loading random website %s: %s' % (random_website.rstrip(), e))

    def _get_random_website(self):
        return random.choice(self.website_list)

    @staticmethod
    def _load_website_list():
        wordlist = []
        with open(os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..',
                                               'data', 'websites.txt')), 'r') as f:
            for line in f:
                wordlist.append(line)
        return wordlist


    def _navigate_webpage(self):
        # Navigate webpage
        navigation_clicks = random.randrange(0, MAX_NAVIGATION_CLICKS)
        print(".... Navigating and highlighting web page", navigation_clicks, "times")
        for _ in range(0, navigation_clicks):
            clickables = self.driver.driver.find_elements(By.TAG_NAME, ("a"))
            if len(clickables) == 0:
                return
            clickable = random.choice(clickables)
            try:
                self._highlight(clickable)
                self.driver.driver.execute_script("arguments[0].target='_self';", clickable)
                clickable.click()
                print("........ successful navigation")
            except:
                print("........ X unsuccessful navigation")
                pass

    def _highlight(self, element):
        driver = element._parent
        def apply_style(s):
            driver.execute_script("arguments[0].setAttribute('style', arguments[1]);",
                                element, s)
        original_style = element.get_attribute('style')
        apply_style("border: 10px solid red;")
        sleep(DEFAULT_WAIT_TIME)
        apply_style(original_style)

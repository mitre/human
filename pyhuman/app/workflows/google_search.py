from time import sleep
import os
import random

from ..utility.base_workflow import BaseWorkflow
from ..utility.webdriver_helper import WebDriverHelper
from selenium.webdriver.common.keys import Keys

WORKFLOW_NAME = 'GoogleSearcher'
WORKFLOW_DESCRIPTION = 'Search for something on Google'

DEFAULT_INPUT_WAIT_TIME = 2
SEARCH_LIST = 'google_searches.txt'

def load():
    driver = WebDriverHelper()
    return GoogleSearch(driver=driver)


class GoogleSearch(BaseWorkflow):

    def __init__(self, driver, input_wait_time=DEFAULT_INPUT_WAIT_TIME):
        super().__init__(name=WORKFLOW_NAME, description=WORKFLOW_DESCRIPTION, driver=driver)

        self.input_wait_time = input_wait_time
        self.search_list = self._load_search_list()

    def action(self, extra=None):
        self._search_web()

    """ PRIVATE """

    def _search_web(self):
        random_search = self._get_random_search()
        try:
            self.driver.driver.get('https://www.google.com')
            assert 'Google' in self.driver.driver.title
            elem = self.driver.driver.find_element_by_name('q')
            elem.clear()
            sleep(self.input_wait_time)
            elem.send_keys(random_search)
        except Exception as e:
            print('Error performing google search %s: %s' % (random_search.rstrip(), e))

    def _get_random_search(self):
        return random.choice(self.search_list)

    @staticmethod
    def _load_search_list():
        with open(os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..',
                                               'data', SEARCH_LIST))) as f:
            wordlist = f.readlines()
        return wordlist


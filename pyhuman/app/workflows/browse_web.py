import os
import random
from time import sleep

from ..utility.base_workflow import BaseWorkflow
from ..utility.webdriver_helper import WebDriverHelper

def load():
    driver = WebDriverHelper()
    return WebBrowse(driver=driver)


class WebBrowse(BaseWorkflow):

    def __init__(self, driver):
        super().__init__(name='WebBrowser', description='Select a random website and browse', driver=driver)
        self.website_list = self._load_website_list()

    def action(self, extra=None):
        self._web_browse()

    """ PRIVATE """

    def _web_browse(self):
        random_website = self._get_random_website()
        try:
            with self.driver as d:
                d.get('https://' + random_website)
                sleep(2)
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

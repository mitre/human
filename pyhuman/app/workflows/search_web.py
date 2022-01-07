from selenium.webdriver.common.keys import Keys
from time import sleep

from ..utility.base_workflow import BaseWorkflow
from ..utility.webdriver_helper import WebDriverHelper


def load():
    driver = WebDriverHelper()
    return SearchWeb(driver=driver)


class SearchWeb(BaseWorkflow):

    def __init__(self, driver):
        super().__init__(name='WebSearcher', description='Search for something on google', driver=driver)

    def action(self, extra=None):
        self._search_web('MITRE Caldera')

    """ PRIVATE """

    def _search_web(self, search_string):
        with self.driver as d:
            d.get('https://www.google.com')
            assert 'Google' in d.title
            elem = d.find_element_by_name('q')
            elem.clear()
            sleep(2)
            elem.send_keys(search_string)
            sleep(2)
            elem.send_keys(Keys.RETURN)




from selenium.webdriver.common.keys import Keys
from time import sleep

from ..utility.base_workflow import BaseWorkflow
from ..utility.webdriver_helper import WebDriverHelper


DEFAULT_INPUT_WAIT_TIME = 2


def load():
    driver = WebDriverHelper()
    return GoogleSearch(driver=driver)


class GoogleSearch(BaseWorkflow):

    def __init__(self, driver, input_wait_time=DEFAULT_INPUT_WAIT_TIME):
        super().__init__(name='GoogleSearcher', description='Search for something on google', driver=driver)

        self.input_wait_time = input_wait_time

    def action(self, extra=None):
        self._search_web('MITRE Caldera')

    """ PRIVATE """

    def _search_web(self, search_string):
        self.driver.driver.get('https://www.google.com')
        assert 'Google' in self.driver.driver.title
        elem = self.driver.driver.find_element_by_name('q')
        elem.clear()
        sleep(self.input_wait_time)
        elem.send_keys(search_string)
        sleep(self.input_wait_time)
        elem.send_keys(Keys.RETURN)




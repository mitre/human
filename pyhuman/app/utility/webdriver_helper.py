from selenium import webdriver
from webdriver_manager.chrome import ChromeDriverManager

from .base_driver import BaseDriverHelper

DRIVER_NAME = 'ChromeWebDriver'

class WebDriverHelper(BaseDriverHelper):

    def __init__(self):
        super().__init__(name=DRIVER_NAME)
        self._driver_path = ChromeDriverManager().install()
        self._driver = webdriver.Chrome(self._driver_path)

    @property
    def driver(self):
        return self._driver

    def __enter__(self):
        return self._driver

    def __exit__(self, exc_type, exc_val, exc_tb):
        pass
    
    def cleanup(self):
        self._driver.quit()

    """ PRIVATE """

    def check_valid_driver_connection(self):
        try:
            driver = webdriver.Chrome(self._driver_path)
            driver.quit()
            return True
        except Exception as e:
            print('Could not load ChromeDriver: %s'.format(e))
            return False

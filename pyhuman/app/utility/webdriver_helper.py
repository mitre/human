from selenium import webdriver
from webdriver_manager.chrome import ChromeDriverManager


class WebDriverHelper:

    def __init__(self):
        self.driver = None
        self._driver_path = ChromeDriverManager().install()

    def __enter__(self):
        self.driver = webdriver.Chrome(self._driver_path)
        return self.driver

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.driver.quit()

    """ PRIVATE """

    def check_valid_driver_connection(self):
        try:
            driver = webdriver.Chrome(self._driver_path)
            driver.quit()
            return True
        except Exception as e:
            print('Could not load ChromeDriver: %s'.format(e))
            return False

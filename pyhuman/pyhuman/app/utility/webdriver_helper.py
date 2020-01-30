import webbrowser
from selenium import webdriver
from webdriver_manager.chrome import ChromeDriverManager
from webdriver_manager.firefox import GeckoDriverManager
from webdriver_manager.microsoft import IEDriverManager


class WebDriverHelper:

    def __init__(self):
        self.browser = ''
        self.driver = None
        self._determine_browser()

    def __enter__(self):
        if self.browser == 'chrome':
            self.driver = webdriver.Chrome(ChromeDriverManager().install())
        elif self.browser == 'firefox':
            self.driver = webdriver.Firefox(GeckoDriverManager().install())
        elif self.browser == 'windows-default':
            self.driver = webdriver.Ie(IEDriverManager().install())
        return self.driver

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.driver.quit()

    """ PRIVATE """

    def _determine_browser(self):
        try:
            webbrowser.get(using='chrome')
            self.browser = 'chrome'
        except webbrowser.Error:
            try:
                webbrowser.get(using='firefox')
                self.browser = 'firefox'
            except webbrowser.Error:
                try:
                    webbrowser.get(using='windows-default')
                    self.browser = 'windows-default'
                except webbrowser.Error:
                    print('No compatible browsers available on the system')

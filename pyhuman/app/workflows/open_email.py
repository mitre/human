import random
from time import sleep

from ..utility.base_workflow import BaseWorkflow
from ..utility.webdriver_helper import WebDriverHelper
from selenium.webdriver.common.by import By
from selenium.common.exceptions import WebDriverException, TimeoutException

WORKFLOW_NAME = 'OpenEmail'
WORKFLOW_DESCRIPTION = 'Open a webmail tab (gmail/outlook), idle on the page; no login'

WEBMAIL_URLS = [
    'https://mail.google.com',
    'https://outlook.live.com',
]
IDLE_SECONDS = 15
DEFAULT_TIMEOUT = 30


def load():
    driver = WebDriverHelper()
    return OpenEmail(driver=driver)


class OpenEmail(BaseWorkflow):

    def __init__(self, driver, idle_seconds=IDLE_SECONDS, default_timeout=DEFAULT_TIMEOUT):
        super().__init__(name=WORKFLOW_NAME, description=WORKFLOW_DESCRIPTION, driver=driver)
        self.idle_seconds = idle_seconds
        self.default_timeout = default_timeout

    def action(self, extra=None):
        url = random.choice(WEBMAIL_URLS)
        print('Opening webmail page:', url)
        try:
            self.driver.driver.set_page_load_timeout(self.default_timeout)
            self.driver.driver.get(url)
        except TimeoutException as e:
            print(f'Timeout loading {url}: {e}')
            return
        except WebDriverException as e:
            print(f'Error loading {url}: {e}')
            return

        # Idle on the landing page, scrolling and hovering benign chrome
        # elements (sign-in card etc.) without ever submitting credentials.
        end = self.idle_seconds
        for _ in range(end // 3):
            try:
                self.driver.driver.execute_script(
                    'window.scrollBy(0, arguments[0]);', random.randint(50, 300)
                )
                self.driver.driver.find_elements(By.TAG_NAME, 'a')
            except Exception:
                pass
            sleep(3)

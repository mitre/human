import os
import random
from time import sleep

from ..utility.base_workflow import BaseWorkflow
from ..utility.webdriver_helper import WebDriverHelper
from selenium.webdriver.common.by import By
from selenium.common.exceptions import WebDriverException, TimeoutException

WORKFLOW_NAME = 'ClickLinks'
WORKFLOW_DESCRIPTION = 'Visit a website and click 3 random links with realistic delays'

WEBSITE_LIST = 'websites.txt'
NUM_CLICKS = 3
MIN_DELAY = 5
MAX_DELAY = 15
DEFAULT_TIMEOUT = 30


def load():
    driver = WebDriverHelper()
    return ClickLinks(driver=driver)


class ClickLinks(BaseWorkflow):

    def __init__(self, driver, num_clicks=NUM_CLICKS, default_timeout=DEFAULT_TIMEOUT):
        super().__init__(name=WORKFLOW_NAME, description=WORKFLOW_DESCRIPTION, driver=driver)
        self.num_clicks = num_clicks
        self.default_timeout = default_timeout
        self.website_list = self._load_website_list()

    def action(self, extra=None):
        site = random.choice(self.website_list).strip()
        url = 'https://' + site
        print(f'Visiting {url} and clicking {self.num_clicks} links')
        try:
            self.driver.driver.set_page_load_timeout(self.default_timeout)
            self.driver.driver.get(url)
        except (TimeoutException, WebDriverException) as e:
            print(f'Error loading {url}: {e}')
            return

        sleep(random.randint(MIN_DELAY, MAX_DELAY))

        # Sample up-front so each click navigates from the original page
        # context; clicking sequentially after navigation tends to stale
        # the DOM references on many real sites.
        anchors = self.driver.driver.find_elements(By.TAG_NAME, 'a')
        candidates = [a for a in anchors if a.get_attribute('href')]
        if not candidates:
            print('No clickable links found')
            return

        targets = random.sample(candidates, k=min(self.num_clicks, len(candidates)))
        urls = [a.get_attribute('href') for a in targets]

        for i, target_url in enumerate(urls, start=1):
            try:
                self.driver.driver.get(target_url)
                print(f'... {i}. clicked {target_url}')
            except (TimeoutException, WebDriverException) as e:
                print(f'... {i}. failed {target_url}: {e}')
            sleep(random.randint(MIN_DELAY, MAX_DELAY))

    @staticmethod
    def _load_website_list():
        path = os.path.abspath(os.path.join(os.path.dirname(__file__),
                                            '..', '..', 'data', WEBSITE_LIST))
        with open(path, 'r') as f:
            return [line for line in f if line.strip()]

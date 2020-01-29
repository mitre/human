import os
import random
from importlib import import_module
from time import sleep

from app.utility.webdriver_helper import WebDriverHelper


TASK_CLUSTER_COUNT = 5
TASK_INTERVAL_SECONDS = 10
GROUPING_INTERVAL_SECONDS = 500


def emulation_loop(workflows):
    while True:
        for c in range(TASK_CLUSTER_COUNT):
            sleep(random.randrange(TASK_INTERVAL_SECONDS))
            workflows[random.randrange(len(workflows))].action()
        sleep(random.randrange(GROUPING_INTERVAL_SECONDS))


def import_workflows(webdriver_helper):
    extensions = []
    for root, dirs, files in os.walk(os.path.join('app', 'workflows')):
        files = [f for f in files if not f[0] == '.' and not f[0] == "_"]
        dirs[:] = [d for d in dirs if not d[0] == '.' and not d[0] == "_"]
        for file in files:
            extensions.append(load_module(root, file, webdriver_helper))
    return extensions


def load_module(root, file, webdriver_helper):
    module = os.path.join(root, file.split('.')[0]).replace(os.path.sep, '.')
    try:
        return getattr(import_module(module), 'load')(driver=webdriver_helper)
    except Exception as e:
        print('Error could not load workflow. {}'.format(e))


if __name__ == '__main__':
    random.seed()
    webdriver_helper = WebDriverHelper()
    if webdriver_helper.browser != '':
        workflows = import_workflows(webdriver_helper=webdriver_helper)
        emulation_loop(workflows=workflows)

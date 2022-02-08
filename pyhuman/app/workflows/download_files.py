import subprocess
import sys
from time import sleep
import ssl
import urllib.request
import json
import random
import requests

from ..utility.base_workflow import BaseWorkflow


WORKFLOW_NAME = 'DownloadFiles'
WORKFLOW_DESCRIPTION = 'Download files'


def load():
    return DownloadFiles()


class DownloadFiles(BaseWorkflow):

    def __init__(self):
        super().__init__(name=WORKFLOW_NAME, description=WORKFLOW_DESCRIPTION)

    def action(self, extra=None):
        self._download_xkcd()
        self._download_wikipedia()


    """ PRIVATE """
    # def _download_files(self):
    #     ssl._create_default_https_context = ssl._create_unverified_context
    #     file_url = 'https://imgs.xkcd.com/comics/file_transfer.png'
    #     urllib.request.urlretrieve(file_url, "file_downloaded.jpg")
    #     sleep(5)

    def _download_wikipedia(self):
        url = "https://en.wikipedia.org/wiki/Special:Random"
        r = requests.get(url, verify=False)
        wiki_name = "wiki" + str(random.randint(1, 100000)) + ".html"
        open(wiki_name, 'wb').write(r.content)

    def _download_xkcd(self):
        ssl._create_default_https_context = ssl._create_unverified_context
        xkcd_url = "https://xkcd.com/" + str(random.randint(1, 1000)) + "/info.0.json"
        request = urllib.request.urlopen(xkcd_url)
        pic_url = json.load(request)['img']
        pic_name = pic_url.split("https://imgs.xkcd.com/comics/", 1)[1]
        urllib.request.urlretrieve(pic_url, pic_name)


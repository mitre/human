import subprocess
import sys
from time import sleep
import ssl
import urllib.request

from ..utility.base_workflow import BaseWorkflow


WORKFLOW_NAME = 'DownloadFiles'
WORKFLOW_DESCRIPTION = 'Download files'


def load():
    return DownloadFiles()


class DownloadFiles(BaseWorkflow):

    def __init__(self):
        super().__init__(name=WORKFLOW_NAME, description=WORKFLOW_DESCRIPTION)

    def action(self, extra=None):
        self._download_files()


    """ PRIVATE """
    def _download_files(self):
        ssl._create_default_https_context = ssl._create_unverified_context
        file_url = 'https://imgs.xkcd.com/comics/file_transfer.png'
        urllib.request.urlretrieve(file_url, "file_downloaded.jpg")
        sleep(5)




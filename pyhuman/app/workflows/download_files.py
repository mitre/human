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

    def _download_files(self):
        ssl._create_default_https_context = ssl._create_unverified_context
        file_url = 'https://imgs.xkcd.com/comics/file_transfer.png'
        urllib.request.urlretrieve(file_url, "file_downloaded.jpg")
        sleep(5)

    """ PRIVATE """

    # def _spawn_shell_and_quit(self):
    #     p = subprocess.Popen(self._determine_os_shell_command(), shell=True)
    #     sleep(5)
    #     p.kill()
    #
    # @staticmethod
    # def _determine_os_shell_command():
    #     if sys.platform.startswith('win32') or sys.platform.startswith('cygwin'):
    #         return 'dir'
    #     return 'ls -la'

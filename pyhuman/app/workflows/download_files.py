import subprocess
import sys
import os
import ssl
import urllib.request
import json
import random
import requests
from bs4 import BeautifulSoup
from random import choice

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
        random_function_selector = [self._download_xkcd, self._download_wikipedia, self._download_nist]
        dir = os.path.join(os.path.expanduser("~"), "Downloads")
        random.choice(random_function_selector)(dir)

    def _download_wikipedia(self, dir):
        url = "https://en.wikipedia.org/wiki/Special:Random"
        r = requests.get(url, verify=False)
        wiki_name = "wiki" + str(random.randint(1, 100000)) + ".html"
        open(os.path.join(dir, wiki_name), 'wb').write(r.content)

    def _download_xkcd(self, dir):
        ssl._create_default_https_context = ssl._create_unverified_context
        xkcd_url = "https://xkcd.com/" + str(random.randint(1, 1000)) + "/info.0.json"
        request = urllib.request.urlopen(xkcd_url)
        pic_url = json.load(request)['img']
        pic_name = pic_url.split("https://imgs.xkcd.com/comics/", 1)[1]
        urllib.request.urlretrieve(pic_url, os.path.join(dir, pic_name))

    def _download_nist(self, dir):
        # Get random page of NIST search results
        nist_search_url = "https://www.nist.gov/publications/search?k=&t=&a=&ps=All&n=&d[min]=&d[max]=&page=" + str(random.randint(1, 2000))
        nist_search_text = requests.get(nist_search_url).text
        nist_search_soup = BeautifulSoup(nist_search_text, features="lxml")
        publications_links = (nist_search_soup.select('a[href^="/publications"]'))

        # Download random publication from the NIST search page
        random_publication = choice(publications_links[1:])
        publication_url = "https://www.nist.gov" + random_publication.get('href')
        publication_page_text = requests.get(publication_url).text
        publication_page_soup = BeautifulSoup(publication_page_text, features="lxml")
        publication_download_link = publication_page_soup.find('a', href=True, text='Local Download')
        if publication_download_link is not None:
            file_url = (publication_download_link.get('href'))
            file_name = publication_url.split("https://www.nist.gov/publications/", 1)[1] + ".pdf"
            urllib.request.urlretrieve(file_url,  os.path.join(dir, file_name))

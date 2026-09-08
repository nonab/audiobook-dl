from .source import Source
from audiobookdl import AudiobookFile, Chapter, logging, AudiobookMetadata, Cover, Audiobook, Result, Series, BookId
from audiobookdl.exceptions import UserNotAuthorized, MissingBookAccess, DataNotPresent
from Crypto.Cipher import AES
from Crypto.Util.Padding import pad
from typing import Any, List, Optional
from urllib3.util import parse_url
from urllib.parse import urlunparse
import requests
import re
import sys
import requests
import re
from bs4 import BeautifulSoup

class KubusSource(Source):
    match = [
        r"https?://(kubus).pl/storytel/.+",
        r"https?://(kubus).pl/audiobooki.+",
    ]
    names = [ "Kubus" ]
    _authentication_methods: list[str] = []
    def download(self, url: str) -> Result:
        # Matches series url
        if re.match(self.match[1], url):
            return self.download_series(url)
        else:
            return self.download_book_from_url(url)

    def download_from_id(self, book_id: str) -> Audiobook:
        return self.download(
            f"https://kubus.pl/storytel/{book_id}"
        )

    def download_series(self, url: str) -> Series[str]:
        series_id: str = url.split("/")[-2]
        logging.debug(f"{series_id=}")
        return Series(
            title = series_id,
            books = self.download_series_books(url, series_id)
        )

    def download_series_books(self, url, series_id: str) -> list:
        response = requests.get(url)
        html_content = response.text

        soup = BeautifulSoup(html_content, 'html.parser')
        audiobook_divs = soup.find_all('div', class_='audiobook')

        books = []
        for div in audiobook_divs:
            book_id = div.get('data-id')
            if book_id:
                ajax_url = f"https://kubus.pl/wp-admin/admin-ajax.php?&id={book_id}&action=kubus_storytel_info"
                ajax_response = requests.get(ajax_url)

                if ajax_response.status_code == 200:
                    ajax_soup = BeautifulSoup(ajax_response.text, 'html.parser')
                    link = ajax_soup.find('a', class_='btn-primary')
                    if link and 'href' in link.attrs:
                        href = link['href']

                        parts = href.strip('/').split('/')
                        formatted_title = parts[-1] if parts else None

                        books.append({
                            'id': formatted_title
                        })

        return [
            BookId(i["id"])
            for i in books
        ]

    def download_book_from_url(self, url: str) -> Audiobook:
        book_info = self.find_book_info(url)
        return Audiobook(
            session = self._session,
            files = self.get_files(book_info),
            metadata = self.get_metadata(book_info),
            cover = self.download_cover(book_info),
        )

    @staticmethod
    def find_book_info(url):
        # Get the HTML content of the page
        response = requests.get(url)
        html_content = response.text

        # Parse the HTML content
        soup = BeautifulSoup(html_content, 'html.parser')

        # Extract book ID from audio source
        audio_source = soup.select_one('audio source')
        if audio_source and 'src' in audio_source.attrs:
            src_url = audio_source['src']
            id_match = re.search(r'id=(\d+)', src_url)
            book_id = id_match.group(1) if id_match else None
            download_url = src_url
        else:
            book_id = None
            download_url = None

        # Extract title
        title_span = soup.select_one('span.breadcrumb_last strong')
        title = title_span.text if title_span else None

        # Extract author and narrator using a more direct approach
        author = "Nieznany"
        narrator = None

        # Find all spans with text content
        spans = soup.find_all('span')
        for span in spans:
            if span.text.strip().startswith('autor:'):
                name_span = span.find('span', class_='name')
                if name_span:
                    author = name_span.text.strip()
            elif span.text.strip().startswith('czyta:'):
                name_span = span.find('span', class_='name')
                if name_span:
                    narrator = name_span.text.strip()

        # Extract cover URL
        cover_div = soup.select_one('div.cover')
        cover_url = None
        if cover_div and 'style' in cover_div.attrs:
            style = cover_div['style']
            url_match = re.search(r'url\((.*?)\)', style)
            if url_match:
                cover_url = url_match.group(1)

        # Return all extracted data
        return {
            'id': book_id,
            'title': title,
            'author': author,
            'narrator': narrator,
            'cover_url': cover_url,
            'download_url': download_url
        }

    def get_files(self, book_info) -> List[AudiobookFile]:
        id = book_info["id"]
        audio_url = f"https://kubus.pl/wp-content/themes/kubus/play.php?id={id}"
        return [
            AudiobookFile(
                url=audio_url,
                headers=self._session.headers,
                ext="mp3"
            )
        ]


    @staticmethod
    def get_metadata(book_info) -> AudiobookMetadata:
        title = book_info["title"]
        metadata = AudiobookMetadata(title)
        try:
            metadata.add_author(book_info["author"])
            metadata.add_narrator(book_info["narrator"])
            return metadata
        except:
            return metadata

    def download_cover(self, book_info) -> Cover:
        cover_url = book_info["cover_url"]
        cover_data = self.get(cover_url)
        return Cover(cover_data, "jpg")

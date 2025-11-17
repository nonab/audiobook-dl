from .source import Source
from audiobookdl import Audiobook, AudiobookFile, AudiobookMetadata, Cover
from typing import List
import sys
import requests
import re
import json

API_BASE_URL = "https://api-audioteka.audioteka.com"

class AudiotekaSource(Source):
    names = [ "Audioteka" ]
    _authentication_methods = [ "cookies" ]
    match = [
        r"https://audioteka.com/pl/audiobook/.+"
    ]
    def extract_token_from_cookies(self) -> str:
        """
        Extracts api_token from cookies in local session.

        :returns: api_token string
        """
        return self._session.cookies.get("api_token")

    def download(self, url: str) -> Audiobook:
        token = self.extract_token_from_cookies()
        print("Trying to download "+url)

        self._session.headers.update({
            "User-Agent": "Audioteka/3.45.5 (2345) Android/9 (Phone;samsung SM-N975F)",
            "X-Distribution-platform": "google_play",
            "Authorization": f"Bearer {token}"
        })


        book_id = self.extract_id_from_url(url)
        book_info = self.download_book_info(book_id)

        return Audiobook(
            session = self._session,
            files = self.download_files(book_id),
            metadata = self.format_metadata(book_info),
            cover = self.download_cover(book_info)
        )


    def download_cover(self, book_info: dict) -> Cover:
        cover_url = book_info["image_url"]
        cover_data = self._session.get(cover_url).content
        return Cover(cover_data, "jpg")


    @staticmethod
    def format_metadata(book_info: dict) -> AudiobookMetadata:
        title = book_info["name"]
        metadata = AudiobookMetadata(title)
        try:
            metadata.add_author(book_info["book"]["_embedded"]["app:author"]["name"])
            metadata.add_narrator(book_info["book"]["_embedded"]["app:lector"]["name"])
            return metadata
        except:
            return metadata

    def download_files(self, book_id: str) -> List[AudiobookFile]:
        files = []
        response = self._session.get(f"{API_BASE_URL}/v2/audiobooks/{book_id}/tracks")

        try:
            chapters_data = response.json()
            add_prefix = False
            for index, chapter in enumerate(chapters_data["_embedded"]["app:track"], start=0):
                title = chapter["title"]
                if add_prefix == False and chapters_data["_embedded"]["app:track"][index]["title"] == title:
                    add_prefix = True
                if add_prefix == True:
                    title = f"{index} {title}"

                chapter_url = f"{API_BASE_URL}{chapter['_links']['app:file']['href']}"
                chapter_response = self._session.get(chapter_url).json()

                if chapter_response.get("message") is not None:
                    print(chapter_response.get("message"))
                    sys.exit()

                files.append(AudiobookFile(
                    title=title,
                    url=chapter_response["url"],
                    ext="mp3"
                ))
        except ValueError as e:
            print(f"API Error: {e}")
            return []

        return files


    def download_book_info(self, book_id: str) -> dict:
        return self._session.get(
            f"{API_BASE_URL}/v2/audiobooks/{book_id}",
        ).json()


    @staticmethod
    def extract_id_from_url(url: str) -> str:
        response = requests.get(url, headers={"User-Agent": "Mozilla/5.0"})
        if response.status_code != 200:
            raise ValueError(f"Failed to fetch URL: {url}, Status Code: {response.status_code}")

        html = response.text

        # ============================================================
        # 1) Try: __NEXT_DATA__
        # ============================================================
        next_data_match = re.search(
            r'<script id="__NEXT_DATA__" type="application/json">(.*?)</script>',
            html,
            re.DOTALL
        )

        if next_data_match:
            try:
                data = json.loads(next_data_match.group(1))
                return data["props"]["pageProps"]["audiobook"]["id"]
            except Exception:
                pass  # fallback below

        # ============================================================
        # 2) Fallback: extract ID from /v2/me/playback-progress/XXXXX\"}
        # ============================================================

        # Regex: capture everything between prefix and before the \"}
        m = re.search(
            r'/v2/me/playback-progress/([0-9a-fA-F-]{36})\\?"?}',
            html
        )
        if m:
            return m.group(1)

        raise ValueError("Unable to extract audiobook ID from page.")

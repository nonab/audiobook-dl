from .source import Source
from audiobookdl import AudiobookFile, logging, AudiobookMetadata, Cover, Audiobook, Result, Series, BookId
from audiobookdl.exceptions import NoFilesFound
import requests
import re
from bs4 import BeautifulSoup

class PismoSource(Source):
    names = ["Magazyn Pismo"]
    match = [
        r"https?://magazynpismo\.pl/audioseriale-[^/]+/[^/]+/[^/]+/?$", # Episode
        r"https?://magazynpismo\.pl/audioseriale-[^/]+/[^/]+/?$",       # Series
    ]
    _authentication_methods = ["login"]
    login_data = ["username", "password"]
    _access_token = None
    
    def __init__(self, options):
        super().__init__(options)
        self._session.headers.update({
            "User-Agent": "Ktor client",
            "Accept": "application/json"
        })

    def _login(self, url: str, username: str, password: str):
        login_url = "https://mobile.magazynpismo.pl/wp-json/mobile/v1/user/login"
        payload = {
            "log": username,
            "pwd": password,
            "mobile": "1"
        }
        logging.log(f"Logging in to Pismo Mobile API as {username}")
        response = self._session.post(login_url, data=payload)
        
        if response.status_code == 200:
            data = response.json()
            if data.get("login_status") == "logged-in":
                self._access_token = data.get("mobile_access_token")
                self._session.headers.update({
                    "Authorization": f"Bearer {self._access_token}"
                })
                logging.log(f"Login successful. Access token acquired.")
            else:
                 logging.error(f"Login failed: {data.get('login_status')}. Message: {data.get('message', 'No message')}")
        else:
            logging.error(f"Mobile login failed with status {response.status_code}. Content: {response.text[:200]}")

    def download(self, url: str) -> Result:
        if re.search(r'odcinek-', url):
            return self.download_book_from_url(url)
        elif re.match(self.match[1], url):
            return self.download_series(url)
        else:
            return self.download_book_from_url(url)

    def download_from_id(self, book_id: str) -> Audiobook:
        return self.download_book_from_url(book_id)

    def download_book_from_url(self, url: str) -> Audiobook:
        book_info = self.find_book_info(url)
        return Audiobook(
            session = self._session,
            files = self.get_files(book_info),
            metadata = self.get_metadata(book_info),
            cover = self.download_cover(book_info),
        )

    def download_series(self, url: str) -> Series[str]:
        series_slug = url.strip('/').split('/')[-1]
        return Series(
            title = series_slug.replace('-', ' ').title(),
            books = [BookId(link) for link in self.download_series_books(url)]
        )

    def download_series_books(self, url: str) -> list[str]:
        response = self._session.get(url)
        soup = BeautifulSoup(response.text, 'html.parser')
        links = []
        for a in soup.find_all('a', href=True):
            href = a['href']
            if 'odcinek-' in href and '/audioseriale-' in href:
                if href.startswith('/'):
                    href = f"https://magazynpismo.pl{href}"
                if href not in links:
                    links.append(href)
        return links

    def find_book_info(self, url: str):
        old_ua = self._session.headers.get("User-Agent")
        self._session.headers.update({"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/144.0.0.0 Safari/537.36"})
        
        response = self._session.get(url)
        soup = BeautifulSoup(response.text, 'html.parser')
        self._session.headers.update({"User-Agent": old_ua})

        player_action = soup.find(attrs={"data-id": True})
        if not player_action:
            player_action = soup.select_one('.player_action')
        
        article_id = player_action.get('data-id') if player_action else None

        src_url = None
        title = None
        author = None
        narrator = None
        cover_url = None

        if article_id:
            logging.log(f"Found article ID: {article_id}")
            api_url = f"https://mobile.magazynpismo.pl/wp-json/mobile/v1/articles/{article_id}"
            
            headers_debug = {k: v for k, v in self._session.headers.items() if k in ["Authorization", "User-Agent"]}
            logging.debug(f"Requesting API: {api_url} with headers: {headers_debug}")
            
            api_resp = self._session.get(api_url)
            if api_resp.status_code == 200:
                api_data = api_resp.json().get('data', {})
                audio_data = api_data.get('audio_data', {})
                
                src_url = audio_data.get('audio_src')
                title = audio_data.get('title') or api_data.get('title')
                cover_url = audio_data.get('cover_src') or api_data.get('image_src')
                
                if src_url:
                    logging.log(f"Successfully found audio URL via API: {src_url}")
                else:
                    logging.log("Warning: API response did not contain audio_src.")
                    logging.debug(f"API Data: {api_data}")
            else:
                logging.log(f"Warning: API request failed with status {api_resp.status_code}")
                logging.debug(f"API Error Content: {api_resp.text[:500]}")

        if not title:
            title_h1 = soup.find('h1')
            title = title_h1.text.strip() if title_h1 else "Unknown"

        for p in soup.find_all('p'):
            text = p.get_text().replace('\xa0', ' ').strip()
            if not author and (text.startswith('Autorka:') or text.startswith('Autor:')):
                author = text.split(':', 1)[1].strip()
            elif not narrator and (text.startswith('Lektorzy:') or text.startswith('Lektor:')):
                narrator = text.split(':', 1)[1].strip()

        if not author:
            meta_author = soup.find('meta', attrs={"name": "author"})
            if meta_author:
                author = meta_author.get('content')

        if not cover_url:
            og_image = soup.find('meta', property='og:image')
            if og_image:
                cover_url = og_image.get('content')

        return {
            'url': url,
            'title': title,
            'author': author,
            'narrator': narrator,
            'cover_url': cover_url,
            'download_url': src_url
        }

    def get_files(self, book_info) -> list[AudiobookFile]:
        url = book_info.get("download_url")
        if not url:
            raise NoFilesFound(url=book_info.get('url'))
        return [
            AudiobookFile(
                url=url, 
                ext="mp3",
            )
        ]

    def get_metadata(self, book_info) -> AudiobookMetadata:
        metadata = AudiobookMetadata(book_info['title'])
        if book_info.get('author'): metadata.add_author(book_info['author'])
        if book_info.get('narrator'): metadata.add_narrator(book_info['narrator'])
        return metadata

    def download_cover(self, book_info) -> Cover:
        if not book_info['cover_url']: return None
        try:
            return Cover(self._session.get(book_info['cover_url']).content, "jpg")
        except:
            return None

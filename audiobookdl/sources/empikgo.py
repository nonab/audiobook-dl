from .source import Source
from audiobookdl import (
    Audiobook,
    AudiobookFile,
    AudiobookMetadata,
    Chapter,
    Cover,
    logging,
)
from audiobookdl.exceptions import (
    BookHasNoAudiobook,
    BookNotFound,
    GenericAudiobookDLException,
    MissingBookAccess,
    NoFilesFound,
    UserNotAuthorized,
)
import base64
import hashlib
import json
import pycountry
import re
from typing import Any, Dict, List, Optional, Tuple

API_BASE_URL = "https://api.empik.com/api/ego"

APP_HEADERS = {
    "Accept": "application/empikgo.app.ver-6+json",
    "Accept-Language": "pl",
    "X-Device-Name": "Samsung SM-G780F",
    "X-Device-Id": "Samsung SM-G780F1788636215339",
    "X-Device-Type": "PHONE",
    "X-Distribution-platform": "google_play",
    "User-Agent": "Empik Go, Android; 33; 3.16.12.02.71; 3161202; release; phone; SM-G780F",
}


class EmpikGoSource(Source):
    names = ["EmpikGo", "Empik"]
    _authentication_methods = ["cookies", "login"]
    login_data = ["username", "password"]

    match = [
        r"https?://(?:www\.)?empik\.com/.+",
        r"https?://(?:www\.)?empikgo\.com/.+",
    ]

    def __init__(self, options: Any):
        super().__init__(options)
        self._options = options
        self._token: Optional[str] = None
        self._refresh_token: Optional[str] = None
        self.ebook: bool = bool(getattr(options, "ebook", None))
        self._session.headers.update(APP_HEADERS)

    @staticmethod
    def compute_body_hash(body: str) -> str:
        """
        Calculates the Empik Go X-Body-Hash header:
        1. Base64-encodes the JSON request body string
        2. Computes MD5 of the base64 string
        3. Takes the first 11 characters of MD5 #1 and computes MD5 #2
        """
        b64 = base64.b64encode(body.encode("utf-8")).decode("utf-8")
        m1 = hashlib.md5(b64.encode("utf-8")).hexdigest()
        return hashlib.md5(m1[:11].encode("utf-8")).hexdigest()

    def extract_token_from_cookies(self) -> Optional[str]:
        """Extracts access_token and refresh_token from cookie jar."""
        if not self._refresh_token:
            for rt_name in ["refresh_token", "refreshToken"]:
                rt = self._session.cookies.get(rt_name)
                if rt:
                    self._refresh_token = rt
                    break

        for cookie_name in ["access_token", "X-Auth-Token", "token", "refresh_token", "refreshToken"]:
            token = self._session.cookies.get(cookie_name)
            if token:
                return token
        return None

    @staticmethod
    def _is_refresh_token(token: str) -> bool:
        try:
            parts = token.split(".")
            if len(parts) >= 2:
                padded = parts[0] + "=" * ((4 - len(parts[0]) % 4) % 4)
                header = json.loads(base64.urlsafe_b64decode(padded.encode()).decode())
                return header.get("typ") == "REFRESH_TOKEN"
        except Exception:
            pass
        return False

    def _refresh_access_token(self, refresh_token: str) -> str:
        """Exchanges a refresh token for a fresh access token."""
        logging.debug("Refreshing Empik Go access token")
        endpoint = f"{API_BASE_URL}/auth/refresh"
        payload = {"refreshToken": refresh_token}
        body = json.dumps(payload, separators=(",", ":"))
        headers = {
            "Content-Type": "application/json; charset=UTF-8",
            "X-Body-Hash": self.compute_body_hash(body),
        }
        resp = self._session.post(endpoint, headers=headers, data=body.encode("utf-8"))
        if resp.status_code != 200:
            raise UserNotAuthorized("Refresh token is expired or invalid. Please provide a new token.")
        data = resp.json()
        new_access = data.get("accessToken")
        new_refresh = data.get("refreshToken")
        if new_access:
            self._token = new_access
            self._session.headers["X-Auth-Token"] = new_access
        if new_refresh:
            self._refresh_token = new_refresh
        return self._token

    def _login_with_camoufox(self, username: str, password: str) -> None:
        """Uses Camoufox to bypass Cloudflare Turnstile and obtain session tokens."""
        try:
            from camoufox.sync_api import Camoufox
        except ImportError:
            raise UserNotAuthorized(
                "Headless login requires camoufox. Install it with: pip install camoufox\n"
                "Or authenticate using browser cookies with: --cookie cookies.txt"
            )

        logging.log("Logging into Empik using automated browser...")
        import time

        with Camoufox(headless=True) as browser:
            page = browser.new_page()
            page.goto("https://www.empik.com/konto/logowanie?continue=%2F", wait_until="networkidle")

            turnstile_token = None
            for _ in range(20):
                val = page.evaluate("() => { const el = document.querySelector('input[name=\"cf-turnstile-response\"]'); return el ? el.value : null; }")
                if val and len(val) > 20:
                    turnstile_token = val
                    break
                time.sleep(1)

            if not turnstile_token:
                raise UserNotAuthorized("Could not solve Cloudflare challenge. Please provide cookies via --cookie cookies.txt.")

            res = page.evaluate("""
                async ({login, password, token}) => {
                    const r = await fetch('https://www.empik.com/gateway/api/auth/web-login', {
                        method: 'POST',
                        headers: {
                            'content-type': 'application/json',
                            'accept': 'application/json, text/plain, */*',
                            'cf-turnstile-response': token
                        },
                        body: JSON.stringify({ login, password })
                    });
                    return { status: r.status };
                }
            """, {"login": username, "password": password, "token": turnstile_token})

            if res.get("status") != 200:
                raise UserNotAuthorized("Invalid Empik credentials or login failed.")

            time.sleep(2)
            cookies = browser.contexts[0].cookies()
            for c in cookies:
                self._session.cookies.set(c["name"], c["value"], domain=c.get("domain", ""))
                if c["name"] == "access_token":
                    self._token = c["value"]
                    self._session.headers["X-Auth-Token"] = c["value"]
                elif c["name"] in ("refresh_token", "refreshToken"):
                    self._refresh_token = c["value"]

            if not self._token:
                raise UserNotAuthorized("Login succeeded but no access token was returned.")
            logging.log("Successfully authenticated with Empik Go")

    def _login(self, url: str, password: str, username: Optional[str] = None, **kwargs):
        """Allows login via username & password (using Camoufox) or passing a token directly."""
        username = username or getattr(self._options, "username", None)
        if username:
            self._login_with_camoufox(username.strip(), password)
            return

        if password:
            clean_token = password.strip()
            if self._is_refresh_token(clean_token):
                self._refresh_token = clean_token
                self._refresh_access_token(clean_token)
            else:
                self._token = clean_token
                self._session.headers["X-Auth-Token"] = self._token
            logging.debug("Using token from password argument")

    def _ensure_authenticated(self) -> str:
        """Ensures an access token is available and configured on the session."""
        username = getattr(self._options, "username", None)
        password = getattr(self._options, "password", None)
        if username and password and not self._token:
            self._login_with_camoufox(username.strip(), password)
            return self._token

        raw_token = (
            password
            or self._token
            or self.extract_token_from_cookies()
        )
        if not raw_token:
            raise UserNotAuthorized()

        if self._is_refresh_token(raw_token):
            self._refresh_token = raw_token
            return self._refresh_access_token(raw_token)

        self._token = raw_token
        self._session.headers["X-Auth-Token"] = raw_token
        return raw_token

    def _post(self, endpoint: str, data: Dict[str, Any], retry_on_401: bool = True) -> Dict[str, Any]:
        """Performs a POST request with X-Body-Hash and error handling."""
        url = f"{API_BASE_URL}{endpoint}"
        body = json.dumps(data, separators=(",", ":"))
        body_bytes = body.encode("utf-8")
        headers = {
            "Content-Type": "application/json; charset=UTF-8",
            "X-Body-Hash": self.compute_body_hash(body),
        }

        resp = self._session.post(url, headers=headers, data=body_bytes)
        if resp.status_code in (401, 403):
            if retry_on_401 and self._refresh_token:
                try:
                    self._refresh_access_token(self._refresh_token)
                    return self._post(endpoint, data, retry_on_401=False)
                except Exception:
                    pass
            raise UserNotAuthorized()
        if resp.status_code == 404:
            raise BookNotFound()
        if resp.status_code != 200:
            raise GenericAudiobookDLException(
                f"Empik Go API error ({resp.status_code}): {resp.text}"
            )

        return resp.json()

    @staticmethod
    def extract_id_from_url(url: str) -> str:
        """
        Extracts the Empik product ID from URLs, e.g.:
        https://www.empik.com/pucio-na-wakacjach-...,p1725961155,ebooki-i-mp3-p
        https://www.empik.com/app/audiobook/p1725961155
        p1725961155
        """
        match = re.search(r"(?:,|^|/)(p\d{5,})(?:,|$|/|\?)", url)
        if match:
            return match.group(1)
        match = re.search(r"(p\d+)", url)
        if match:
            return match.group(1)
        raise ValueError(f"Could not extract product ID from URL: {url}")

    def download_book_info(self, book_id: str) -> Dict[str, Any]:
        """Fetches product details from Empik Go API."""
        endpoint = f"/product/{book_id}?content=ALL"
        payload = {"supportedFileFormats": ["EPUB", "PDF"]}
        logging.debug(f"Fetching product info for {book_id}")
        data = self._post(endpoint, payload)

        for module in data.get("modules", []):
            if module.get("module") == "PRODUCT":
                return module

        raise BookNotFound()

    def format_metadata(self, product: Dict[str, Any], url: str) -> AudiobookMetadata:
        """Extracts and formats AudiobookMetadata from product details."""
        title = product.get("title", "")
        metadata = AudiobookMetadata(title=title)
        metadata.scrape_url = url

        for author in product.get("authors", []):
            metadata.add_author(author)

        lector = product.get("lector")
        if lector:
            if isinstance(lector, str):
                narrators = [n.strip() for n in lector.split(",") if n.strip()]
                metadata.add_narrators(narrators)
            elif isinstance(lector, list):
                metadata.add_narrators(lector)

        if product.get("publishingHouse"):
            metadata.publisher = product["publishingHouse"]

        if product.get("description"):
            metadata.description = product["description"]

        lang_code = product.get("language")
        if lang_code:
            try:
                metadata.language = pycountry.languages.get(alpha_2=lang_code.lower()) or pycountry.languages.get(name=lang_code)
            except Exception:
                pass

        return metadata

    def download_cover(self, product: Dict[str, Any]) -> Optional[Cover]:
        """Downloads cover image if available."""
        cover_url = product.get("cover")
        if not cover_url:
            return None

        try:
            resp = self._session.get(cover_url)
            if resp.status_code == 200:
                ext = "jpg"
                if "png" in cover_url.lower():
                    ext = "png"
                return Cover(resp.content, ext)
        except Exception as e:
            logging.debug(f"Failed to download cover: {e}")
        return None

    def download_files(
        self, book_id: str, line_id: Optional[str], title: str = ""
    ) -> Tuple[List[AudiobookFile], List[Chapter]]:
        """
        Retrieves chapters or ebook file for the book, purchasing/adding to library if lineId is not yet present.
        """
        # If book is not already added in user library, perform subscription purchase
        if not line_id:
            logging.debug(f"Book {book_id} has no lineId; acquiring via subscription purchase")
            purchase_payload = {"productsInCart": [{"productId": book_id}]}
            purchase_data = self._post("/subscription/purchase", purchase_payload)

            try:
                line_id = purchase_data["order"]["products"][0]["lineId"]
            except (KeyError, IndexError):
                raise MissingBookAccess()

        logging.debug(f"Requesting download information for lineId: {line_id}")
        dl_payload = {
            "lineId": line_id,
            "supportedFileFormats": ["EPUB", "PDF"],
        }
        dl_data = self._post("/user/library/download", dl_payload)

        # Check if the download is an EBOOK (single EPUB/PDF file)
        format_type = dl_data.get("format", "").upper()
        if format_type == "EBOOK" or (not dl_data.get("chapters") and dl_data.get("downloadLink")):
            dl_link = dl_data.get("downloadLink")
            ext = (dl_data.get("fileFormat") or "epub").lower()
            files = [
                AudiobookFile(
                    url=dl_link,
                    ext=ext,
                    title=title or book_id,
                    headers={"User-Agent": APP_HEADERS["User-Agent"]},
                )
            ]
            return files, []

        raw_chapters = dl_data.get("chapters", [])
        if not raw_chapters:
            raise NoFilesFound(f"No chapters found for {book_id}")

        files: List[AudiobookFile] = []
        chapters: List[Chapter] = []
        current_time_ms = 0

        for i, ch in enumerate(raw_chapters):
            ch_title = ch.get("chapterTitle") or f"Chapter {i+1:02d}"
            download_link = ch.get("downloadLink")
            if not download_link:
                continue

            files.append(
                AudiobookFile(
                    url=download_link,
                    ext="mp3",
                    title=ch_title,
                    headers={"User-Agent": APP_HEADERS["User-Agent"]},
                )
            )

            chapters.append(Chapter(start=current_time_ms, title=ch_title))
            duration_sec = int(ch.get("chapterLength", 0))
            current_time_ms += duration_sec * 1000

        return files, chapters

    def download(self, url: str) -> Audiobook:
        self._ensure_authenticated()

        book_id = self.extract_id_from_url(url)
        logging.log(f"Fetching Empik Go metadata for [blue]{book_id}[/]")

        product = self.download_book_info(book_id)

        formats = product.get("formats", [])
        has_audio = "AUDIOBOOK" in formats or product.get("isPodcast", False)
        has_ebook = "EBOOK" in formats or "EPUB" in formats or "PDF" in formats

        if self.ebook:
            if not has_ebook:
                raise GenericAudiobookDLException(f"Book {book_id} does not have an ebook format available.")
        elif not has_audio:
            if has_ebook:
                logging.log(f"[yellow]Notice: Book {book_id} is an ebook. Downloading ebook.[/]")
            else:
                raise BookHasNoAudiobook()

        metadata = self.format_metadata(product, url)
        cover = self.download_cover(product)

        line_id = product.get("lineId")
        files, chapters = self.download_files(book_id, line_id, title=metadata.title)

        return Audiobook(
            session=self._session,
            files=files,
            metadata=metadata,
            cover=cover,
            chapters=chapters,
        )

    def download_from_id(self, book_id: str) -> Audiobook:
        return self.download(f"https://www.empik.com/product,{book_id}")

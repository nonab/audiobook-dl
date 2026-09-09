from .source import Source
from audiobookdl import (
    Audiobook,
    AudiobookFile,
    AudiobookMetadata,
    Chapter,
    Cover,
    logging,
    Series,
    BookId,
    Result,
)
from audiobookdl.exceptions import (
    BookHasNoAudiobook,
    BookNotFound,
    GenericAudiobookDLException,
    UserNotAuthorized,
    NoFilesFound,
)
from audiobookdl.utils.audiobook import LegimiAESEncryption

import html
import io
import json
import os
import re
import struct
import zipfile
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import urlsplit, parse_qs
import requests

CORE_SYNC_URL = "https://app.legimi.pl/svc/sync/core.aspx"
CATALOGUE_SVC_URL = "https://app.legimi.pl/svc/catalogue/CatalogueService.svc/catalogue/lite2"
MOBILE_GP_URL = "https://mobile-gp.legimi.pl"

DEFAULT_DEVICE_CODE = "Android||Android 2.2+||Samsung:SM-S9210:2NZXl8uVMK/2yOqIUtgdw1cm55KdwNj4OD4A9IbUvfc=||PHONE"
DEFAULT_USER_AGENT = "Dalvik/2.1.0 (Linux; U; Android 12; SM-S9210 Build/673d380.0)"


class LegimiSource(Source):
    names = ["Legimi"]
    _authentication_methods = ["login"]
    login_data = ["username", "password"]

    match = [
        r"https?://(?:www\.|mobile-gp\.)?legimi\.pl/(?:audiobook|ebook|ksiazka)/.+?,b(?P<id>\d+)\.html.*",
        r"https?://(?:www\.|mobile-gp\.)?legimi\.pl/.+?,b(?P<id>\d+)\.html.*",
        r"https?://(?:www\.|mobile-gp\.)?legimi\.pl/.+?,b(?P<id>\d+).*",
        r"https?://(?:www\.|mobile-gp\.)?legimi\.pl/.+?,(?:c|ad|cl)(?P<id>\d+).*",
        r"^b?(?P<id>\d+)$",
    ]

    def __init__(self, options: Any):
        super().__init__(options)
        self._options = options
        self._session_id: Optional[str] = None
        dev_id = getattr(options, "device_id", None)
        self._device_id: Optional[int] = int(dev_id) if dev_id else None
        self.ebook: bool = bool(getattr(options, "ebook", None))
        self._session.headers.update({"User-Agent": DEFAULT_USER_AGENT})
        self._session_cache_path = self._get_session_cache_path()

    @property
    def authenticated(self) -> bool:
        if self._session_id:
            return True
        cached = self._load_cached_session()
        if cached:
            return True
        return False

    def _login(self, url: str, username: str, password: str) -> None:
        setattr(self._options, "username", username)
        setattr(self._options, "password", password)
        self.authenticate(force=True)

    def _get_session_cache_path(self) -> Path:
        db_dir = getattr(self._options, "database_dir", None)
        if db_dir:
            return Path(db_dir) / "legimi_session.json"
        return Path.home() / ".config" / "audiobook-dl" / "legimi_session.json"

    def _load_cached_session(self, user: Optional[str] = None) -> Optional[str]:
        try:
            if self._session_cache_path.exists():
                with open(self._session_cache_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    cached_user = data.get("username")
                    opt_user = user or getattr(self._options, "username", None)
                    if not opt_user or cached_user == opt_user:
                        self._session_id = data.get("session_id")
                        cached_dev = data.get("device_id")
                        if cached_dev:
                            self._device_id = int(cached_dev)
                        return self._session_id
        except Exception as e:
            logging.debug(f"Failed to load cached Legimi session: {e}")
        return None

    def _save_cached_session(self) -> None:
        try:
            self._session_cache_path.parent.mkdir(parents=True, exist_ok=True)
            with open(self._session_cache_path, "w", encoding="utf-8") as f:
                json.dump(
                    {
                        "session_id": self._session_id,
                        "device_id": self._device_id,
                        "username": getattr(self._options, "username", None),
                    },
                    f,
                    indent=2,
                )
        except Exception as e:
            logging.debug(f"Failed to save cached Legimi session: {e}")

    @staticmethod
    def _build_auth_req(username: str, password: str, device_id: int) -> bytes:
        """
        Builds the binary AUTH_REQ_NEW packet (Type 80) for core.aspx.
        """
        default_sync_state = (
            b"\x03\x00\x01\x00\x08\x00\x00\x00\xcc\xc3\n\x00\x00\x00\x00\x00"
            b"\x02\x00\x02\x00\x00\x00\x01\x00\x03\x00\xda\x00\x00\x00\x06\x00"
            b"\x02\x00\x04\x00\x00\x00\xb4\x00\x00\x00\x00\x00\x04\x00\x00\x00"
            b'"\x01\x00\x00\x03\x00\x04\x00\x00\x00\x02\x00\x00\x00\x01\x00'
            b"\x04\x00\x00\x00\x01\x00\x00\x00\x04\x00\xa3\x00\x00\x00"
            b'{"TimeStampUtc":"\\/Date(1788787063734)\\/","TotalWordsRead":1,"_dirty":true,"partialChapters":[],"fullyReadChapters":[{"Key":0,"Value":{"Index":0,"TotalWords":1}}]}'
            b"\x05\x00\x01\x00\x00\x00\x00"
        )
        fields = [
            (4, struct.pack("<i", 0)),
            (3, b"3.27.0"),
            (2, struct.pack("<q", device_id)),
            (5, struct.pack("<q", 0)),
            (1, password.encode("utf-8")),
            (0, username.encode("utf-8")),
            (11, b"\x00"),
            (6, struct.pack("<h", 1)),
            (7, default_sync_state),
        ]
        dict_body = struct.pack("<h", len(fields))
        for fid, val in fields:
            dict_body += struct.pack("<hI", fid, len(val)) + val

        proto_ver = 21
        pkt_type = 80  # Commands.AUTH_REQ_NEW
        header = struct.pack("<ihI", proto_ver, pkt_type, len(dict_body))
        return header + dict_body

    def _auto_acquire_device_id(self, username: str, password: str) -> Optional[int]:
        """
        Attempts to automatically register or retrieve a device ID from Legimi via ActivateRequest (Type 66).
        """
        try:
            u = username.encode("utf-8")
            p = password.encode("utf-8")
            d = DEFAULT_DEVICE_CODE.encode("utf-8")

            payload = struct.pack("<q", 0)
            payload += struct.pack("<h", len(u)) + u
            payload += struct.pack("<h", len(p)) + p
            payload += struct.pack("<h", len(d)) + d
            payload += struct.pack("<h", 0)

            header = struct.pack("<ihI", 21, 66, len(payload))
            headers = {
                "Content-Type": "application/octet-stream",
                "Accept": "application/octet-stream",
                "User-Agent": DEFAULT_USER_AGENT,
            }
            resp = self._session.post(CORE_SYNC_URL, data=header + payload, headers=headers)
            if len(resp.content) >= 10:
                proto, ptype, plen = struct.unpack("<ihI", resp.content[:10])
                if ptype == 16384:
                    map_data = resp.content[10:]
                    if len(map_data) >= 2:
                        count = struct.unpack("<H", map_data[:2])[0]
                        offset = 2
                        for _ in range(count):
                            if offset + 6 > len(map_data):
                                break
                            k, vlen = struct.unpack("<Hi", map_data[offset:offset + 6])
                            offset += 6
                            val = map_data[offset:offset + vlen]
                            offset += vlen
                            if k == 6:  # UnlimitedData.DEVICE_ID
                                dev_id = struct.unpack("<q", val)[0]
                                logging.debug(f"Auto-acquired Legimi device ID: {dev_id}")
                                return dev_id
        except Exception as e:
            logging.debug(f"Failed to auto-acquire Legimi device ID: {e}")
        return None

    def authenticate(self, force: bool = False) -> str:
        """Authenticates with Legimi sync service, obtaining a session token."""
        if not force and self._session_id:
            return self._session_id

        if not force:
            cached = self._load_cached_session()
            if cached:
                return cached

        self._session_id = None
        if self._session_cache_path.exists():
            try:
                self._session_cache_path.unlink()
            except Exception:
                pass

        username = getattr(self._options, "username", None)
        password = getattr(self._options, "password", None)
        if not username or not password:
            raise UserNotAuthorized(
                "Legimi requires --username and --password (and optional --device-id) to establish sync session."
            )

        if not self._device_id:
            self._device_id = self._auto_acquire_device_id(username, password)
            if not self._device_id:
                raise UserNotAuthorized(
                    "Could not determine Legimi device ID. If your device limit is reached, "
                    "please specify your registered device ID via --device-id <ID>."
                )

        logging.log(f"Authenticating with Legimi (device ID {self._device_id})...")
        pkt = self._build_auth_req(username, password, self._device_id)
        headers = {
            "Content-Type": "application/octet-stream",
            "Accept": "application/octet-stream",
            "User-Agent": DEFAULT_USER_AGENT,
        }
        resp = self._session.post(CORE_SYNC_URL, data=pkt, headers=headers)
        if resp.status_code != 200 or len(resp.content) < 12:
            raise UserNotAuthorized("Legimi authentication failed. Check credentials and device ID.")

        proto, ptype, plen = struct.unpack("<ihI", resp.content[:10])
        if ptype == 9:
            raise UserNotAuthorized("Legimi rejected credentials or device limit exceeded.")

        tokens = re.findall(r"[a-f0-9]{32}", resp.content.decode("latin1", errors="ignore"))
        if not tokens:
            raise UserNotAuthorized("No session token received from Legimi.")

        self._session_id = tokens[0]
        self._save_cached_session()
        logging.log("Successfully authenticated with Legimi.")
        return self._session_id

    @staticmethod
    def extract_id_from_url(url: str) -> str:
        """Extracts numerical book ID from Legimi URL or string."""
        match = re.search(r",b(?P<id>\d+)", url)
        if match:
            return match.group("id")
        match = re.search(r"\b(?P<id>\d{5,})\b", url)
        if match:
            return match.group("id")
        match = re.search(r"^b?(?P<id>\d+)$", url.strip())
        if match:
            return match.group("id")
        raise ValueError(f"Could not extract book ID from URL: {url}")

    def borrow_book(self, book_id: str) -> None:
        """Borrows book / adds book to user shelf via CatalogueService lite2 download endpoint."""
        username = getattr(self._options, "username", None)
        password = getattr(self._options, "password", None)
        if not username or not password:
            return

        logging.debug(f"Borrowing / activating book {book_id} on shelf...")
        url = f"{CATALOGUE_SVC_URL}/download/"
        body = (
            f"id={book_id}&dev={self._device_id}&login={username}&pass={password}&unlimited=True&points=-1"
        ).encode("utf-8")
        headers = {
            "Content-Type": "application/octet-stream",
            "Accept": "application/octet-stream",
            "User-Agent": DEFAULT_USER_AGENT,
        }
        resp = self._session.post(url, data=body, headers=headers)
        if resp.status_code != 200:
            logging.debug(f"Borrow request status: {resp.status_code}")

    def get_audio_toc(self, book_id: str) -> List[Dict[str, Any]]:
        """
        Calls /audio-toc/ on CatalogueService to obtain toc.zip containing toc.json.
        """
        username = getattr(self._options, "username", None)
        password = getattr(self._options, "password", None)
        url = f"{CATALOGUE_SVC_URL}/audio-toc/"
        body = (
            f"id={book_id}&dev={self._device_id}&login={username or ''}&pass={password or ''}"
        ).encode("utf-8")
        headers = {
            "Content-Type": "application/octet-stream",
            "Accept": "application/octet-stream",
            "User-Agent": DEFAULT_USER_AGENT,
        }
        resp = self._session.post(url, data=body, headers=headers)
        if resp.status_code != 200:
            raise GenericAudiobookDLException(f"Failed to fetch TOC for book {book_id}")

        content_str = resp.content.decode("latin1", errors="ignore")
        match = re.search(r"https?://[^\s\"\'\x00-\x1f]+\.zip[^\s\"\'\x00-\x1f]*", content_str)
        if not match:
            raise GenericAudiobookDLException("Could not find TOC zip URL in response.")

        toc_zip_url = match.group(0)
        logging.debug(f"Downloading TOC zip from {toc_zip_url}")
        zip_resp = self._session.get(toc_zip_url)
        if zip_resp.status_code != 200:
            raise GenericAudiobookDLException("Failed to download TOC zip.")

        with zipfile.ZipFile(io.BytesIO(zip_resp.content)) as z:
            with z.open("toc.json") as f:
                toc_data = json.load(f)
                return toc_data

    def get_audio_file_url(self, book_id: int, file_index: int) -> str:
        """
        Sends AUDIO_FILE_REQ (Type 16490) to core.aspx and returns signed Azure CDN URL.
        """
        for attempt in range(2):
            session_id = self.authenticate()
            fields = [
                (0, struct.pack("<q", book_id)),
                (1, struct.pack("<i", file_index)),
                (2, session_id.encode("ascii")),
            ]
            dict_body = struct.pack("<h", len(fields))
            for fid, val in fields:
                dict_body += struct.pack("<hI", fid, len(val)) + val

            proto_ver = 21
            pkt_type = 16490  # Commands.AUDIO_FILE_REQ
            pkt = struct.pack("<ihI", proto_ver, pkt_type, len(dict_body)) + dict_body

            headers = {
                "Content-Type": "application/octet-stream",
                "Accept": "application/octet-stream",
                "User-Agent": DEFAULT_USER_AGENT,
            }
            resp = self._session.post(CORE_SYNC_URL, data=pkt, headers=headers)
            if resp.status_code == 200 and len(resp.content) >= 10:
                proto, ptype, plen = struct.unpack("<ihI", resp.content[:10])
                if ptype == 257:  # ERR_AUTH_EXPIRED
                    self.authenticate(force=True)
                    continue

            if resp.status_code != 200 or len(resp.content) < 12:
                raise GenericAudiobookDLException(f"Failed to get audio URL for file index {file_index}")

            content_str = resp.content.decode("latin1", errors="ignore")
            match = re.search(r"https?://audio\.legimi\.com/[^\s\"\'\x00-\x1f]+", content_str)
            if not match:
                raise GenericAudiobookDLException(f"No audio download URL returned for file index {file_index}")

            return match.group(0)

        raise GenericAudiobookDLException(f"Failed to get audio URL for file index {file_index}")

    def get_drm_keys(self, book_id: int) -> Tuple[bytes, bytes]:
        """
        Fetches shelf documents from core.aspx (LIST_DOCS_REQ) to retrieve DrmKey and DrmIv.
        Queries formats [1, 2, 3, 6] to find the active publication keys.
        """
        headers = {
            "Content-Type": "application/octet-stream",
            "Accept": "application/octet-stream",
            "User-Agent": DEFAULT_USER_AGENT,
        }

        # Query shelf for publication DRM keys across versions (typically 1..10)
        # Filters:
        # 1. ID_EQUAL (Type 10, len 8) -> book_id
        # 2. VERSION_EQUAL (Type 11, len 8) -> version
        # 3. ACCEPTED_FORMATS (Type 14, len 2) -> 5 (EPUB | AUDIO)
        # 4. OPTIONS (Type 600, len 32) -> layout and cover options
        b_id = struct.pack("<q", book_id)
        suffix = bytes.fromhex(
            "020e0002000500045802200003000100040000009801000002000400000048030000030004000000f4010000"
        )
        for attempt in range(2):
            session_id = self.authenticate()
            expired = False

            for ver in range(1, 15):
                v_bytes = struct.pack("<q", ver)
                part = bytes.fromhex("020a000800") + b_id + bytes.fromhex("020b000800") + v_bytes + suffix
                payload = b"\x04" + session_id.encode("ascii") + part
                pkt = struct.pack("<ihI", 21, 26, len(payload)) + payload

                resp = self._session.post(CORE_SYNC_URL, data=pkt, headers=headers)
                raw = resp.content

                if len(raw) >= 10:
                    proto, ptype, plen = struct.unpack("<ihI", raw[:10])
                    if ptype == 257:  # ERR_AUTH_EXPIRED
                        self.authenticate(force=True)
                        expired = True
                        break

                k_match = re.search(b"\x11\x00\x10\x00\x00\x00(.{16})", raw, re.DOTALL)
                iv_match = re.search(b"\x12\x00\x10\x00\x00\x00(.{16})", raw, re.DOTALL)
                if k_match and iv_match:
                    return k_match.group(1), iv_match.group(1)

            if expired:
                continue

            # General shelf query fallback (2 filters: format + options)
            payload = b"\x02" + session_id.encode("ascii") + suffix
            pkt = struct.pack("<ihI", 21, 26, len(payload)) + payload
            resp = self._session.post(CORE_SYNC_URL, data=pkt, headers=headers)
            raw = resp.content

            k_match = re.search(b"\x11\x00\x10\x00\x00\x00(.{16})", raw, re.DOTALL)
            iv_match = re.search(b"\x12\x00\x10\x00\x00\x00(.{16})", raw, re.DOTALL)
            if k_match and iv_match:
                return k_match.group(1), iv_match.group(1)

        raise GenericAudiobookDLException(f"Could not retrieve DRM keys for book {book_id}")

    def get_ebook_file_url(self, book_id: int) -> str:
        """
        Requests signed streameddownload URL for an ebook using packet 200.
        """
        headers = {
            "Content-Type": "application/octet-stream",
            "Accept": "application/octet-stream",
            "User-Agent": DEFAULT_USER_AGENT,
        }
        rem = bytes.fromhex("00000000000000000000010000000000")
        for attempt in range(2):
            session_id = self.authenticate()
            expired = False
            for fmt in [1, 2, 3]:
                payload = struct.pack("<qq", book_id, fmt) + session_id.encode("ascii") + rem
                pkt = struct.pack("<ihI", 21, 200, len(payload)) + payload
                resp = self._session.post(CORE_SYNC_URL, data=pkt, headers=headers)
                if len(resp.content) >= 10:
                    proto, ptype, plen = struct.unpack("<ihI", resp.content[:10])
                    if ptype == 257:
                        self.authenticate(force=True)
                        expired = True
                        break
                pos = resp.content.find(b"https://")
                if pos != -1:
                    return resp.content[pos:].decode("latin1").split("\x00")[0]
            if expired:
                continue

        raise GenericAudiobookDLException(f"Could not resolve ebook download URL for book {book_id}")

    def fetch_metadata(self, book_id: str, url: str) -> Tuple[AudiobookMetadata, Optional[Cover], Optional[int]]:
        """Scrapes book metadata from mobile-gp Legimi web page."""
        page_url = f"{MOBILE_GP_URL}/audiobook-,b{book_id}.html"
        headers = {"User-Agent": "Mozilla/5.0 (Linux; Android 12)"}
        resp = self._session.get(page_url, headers=headers)

        html_text = resp.text
        if resp.status_code == 404 or "/ebook" in url or self.ebook:
            page_url = f"{MOBILE_GP_URL}/ebook-,b{book_id}.html"
            resp = self._session.get(page_url, headers=headers)
            html_text = resp.text

        title_match = re.search(r'<meta[^>]+property=["\']og:title["\'][^>]+content=["\']([^"\']*)["\']', html_text)
        desc_match = re.search(r'<meta[^>]+property=["\']og:description["\'][^>]+content=["\']([^"\']*)["\']', html_text)
        img_match = re.search(r'<meta[^>]+property=["\']og:image["\'][^>]+content=["\']([^"\']*)["\']', html_text)

        raw_title = title_match.group(1).strip() if title_match else f"Book {book_id}"
        title = html.unescape(raw_title)
        title_parts = [p.strip() for p in title.split(" - ") if p.strip()]
        book_title = title_parts[0] if title_parts else title
        authors = [title_parts[1]] if len(title_parts) > 1 else []

        metadata = AudiobookMetadata(title=book_title)
        metadata.scrape_url = url
        for a in authors:
            metadata.add_author(a)

        if desc_match:
            metadata.description = html.unescape(desc_match.group(1).strip())

        linked_audio_id = None
        clean_text = html_text.replace(r"\u0022", '"')
        pos = clean_text.find('"audiobook":{')
        if pos != -1:
            brace_count = 0
            end_pos = pos + len('"audiobook":')
            for i in range(end_pos, len(clean_text)):
                if clean_text[i] == '{':
                    brace_count += 1
                elif clean_text[i] == '}':
                    brace_count -= 1
                    if brace_count == 0:
                        end_pos = i + 1
                        break
            try:
                audio_data = json.loads(clean_text[pos + len('"audiobook":') : end_pos])
                aid = audio_data.get("id")
                if aid:
                    linked_audio_id = int(aid)
                lector = audio_data.get("lectorName")
                if lector:
                    metadata.add_narrator(lector)
            except Exception:
                pass

        cover: Optional[Cover] = None
        if img_match:
            orig_cover_url = img_match.group(1)
            hi_res_url = re.sub(r"/w\d+_u\d+\.jpg", "/w512_u90.jpg", orig_cover_url)
            for target_url in [hi_res_url, orig_cover_url]:
                try:
                    c_resp = self._session.get(target_url)
                    if c_resp.status_code == 200 and len(c_resp.content) > 1000:
                        cover = Cover(c_resp.content, "jpg")
                        break
                except Exception as e:
                    logging.debug(f"Failed to fetch cover from {target_url}: {e}")

        return metadata, cover, linked_audio_id

    @staticmethod
    def is_series_url(url: str) -> bool:
        """Determines if the given Legimi URL points to a category, series, author or collection."""
        return bool(re.search(r",(?P<type>c|ad|cl)\d+", url))

    def download_series(self, url: str) -> Series[str]:
        """Crawls all pages of a Legimi series, author or collection and returns a Series object."""
        headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}
        parsed = urlsplit(url)
        base_url = f"{parsed.scheme}://{parsed.netloc}{parsed.path}"
        base_params = {k: v[0] for k, v in parse_qs(parsed.query).items()}

        all_book_ids: List[str] = []
        series_title: Optional[str] = None
        page = 1

        while True:
            params = dict(base_params)
            params["page"] = page
            logging.debug(f"Fetching Legimi series page {page}: {base_url} with params {params}")
            resp = self._session.get(base_url, params=params, headers=headers)
            if resp.status_code != 200:
                break

            if not series_title:
                m_title = re.search(r'<h1[^>]*>\s*(?:<!--[^>]*-->\s*)*([^<\r\n]+)', resp.text)
                if m_title:
                    series_title = html.unescape(m_title.group(1).strip())

            page_ids = list(dict.fromkeys(re.findall(r',b(\d+)\.html', resp.text)))
            if not page_ids:
                break

            new_ids = [bid for bid in page_ids if bid not in all_book_ids]
            if not new_ids:
                break

            all_book_ids.extend(new_ids)
            page += 1

        if not series_title:
            series_title = f"Legimi ({parsed.path.strip('/').split('/')[-1]})"

        logging.log(f"Found [yellow]{len(all_book_ids)}[/] books in [blue]{series_title}[/]")
        return Series[str](
            title=series_title,
            books=[BookId(id=bid) for bid in all_book_ids],
        )

    def download(self, url: str) -> Result:
        if self.is_series_url(url):
            return self.download_series(url)

        self.authenticate()

        book_id_str = self.extract_id_from_url(url)
        book_id = int(book_id_str)
        logging.log(f"Fetching Legimi title [blue]{book_id}[/]")

        metadata, cover, linked_audio_id = self.fetch_metadata(book_id_str, url)

        # If user did NOT specify --ebook, check if this is a synchrobook pointing to an audiobook
        if not self.ebook and linked_audio_id and linked_audio_id != book_id:
            logging.log(f"Detected synchrobook with audio version [blue]{linked_audio_id}[/]")
            book_id = linked_audio_id
            book_id_str = str(linked_audio_id)

        # Borrow title to ensure it's registered on the shelf
        self.borrow_book(book_id_str)

        # Retrieve DRM keys for decryption
        logging.debug("Obtaining DRM keys for content...")
        drm_key, drm_iv = self.get_drm_keys(book_id)

        # Check if ebook download requested
        if self.ebook:
            ebook_url = self.get_ebook_file_url(book_id)
            file_obj = AudiobookFile(
                url=ebook_url,
                ext="epub",
                title=metadata.title,
                headers={"User-Agent": DEFAULT_USER_AGENT},
                encryption_method=LegimiAESEncryption(key=drm_key, iv=drm_iv, offset=10),
            )
            return Audiobook(
                session=self._session,
                files=[file_obj],
                metadata=metadata,
                cover=cover,
                chapters=[],
            )

        # Audiobook flow: download TOC and audio files
        try:
            toc_items = self.get_audio_toc(book_id_str)
        except Exception as e:
            raise BookHasNoAudiobook(f"Book {book_id} has no audiobook available on Legimi (use --ebook 1 for ebook).")

        if not toc_items:
            raise NoFilesFound(f"No audio chapters found for {book_id}")

        files: List[AudiobookFile] = []
        chapters: List[Chapter] = []
        current_time_ms = 0

        for i, item in enumerate(toc_items):
            ch_title = item.get("t") or f"Chapter {i+1:02d}"
            duration_str = item.get("d", "00:00:00")
            dur_parts = duration_str.split(":")
            sec = 0.0
            if len(dur_parts) == 3:
                sec = float(dur_parts[0]) * 3600 + float(dur_parts[1]) * 60 + float(dur_parts[2])

            cdn_url = self.get_audio_file_url(book_id, i)
            files.append(
                AudiobookFile(
                    url=cdn_url,
                    ext="mp3",
                    title=ch_title,
                    headers={"User-Agent": DEFAULT_USER_AGENT},
                    encryption_method=LegimiAESEncryption(key=drm_key, iv=drm_iv, offset=10),
                )
            )

            chapters.append(Chapter(start=int(current_time_ms), title=ch_title))
            current_time_ms += sec * 1000

        return Audiobook(
            session=self._session,
            files=files,
            metadata=metadata,
            cover=cover,
            chapters=chapters,
        )

    def download_from_id(self, book_id: str) -> Audiobook:
        kind = "ebook" if self.ebook else "audiobook"
        return self.download(f"https://www.legimi.pl/{kind}-,b{book_id}.html")

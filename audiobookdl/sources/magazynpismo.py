from .source import Source
from audiobookdl import (
    AudiobookFile,
    logging,
    AudiobookMetadata,
    Cover,
    Audiobook,
    Result,
    Series,
    BookId,
)
from audiobookdl.exceptions import NoFilesFound
import re
from bs4 import BeautifulSoup


class PismoSource(Source):
    names = ["Magazyn Pismo"]

    # Existing audioseriale URLs
    match = [
        r"https?://magazynpismo\.pl/audioseriale-[^/]+/[^/]+/[^/]+/?$",
        r"https?://magazynpismo\.pl/audioseriale-[^/]+/[^/]+/?$",
        r"https?://magazynpismo\.pl/sledztwo-pisma/?$",
        r"https?://magazynpismo\.pl/posluchaj/sezon-\d+(/lista)?/?$",
        r"https?://magazynpismo\.pl/sledztwo-pisma/sezon-\d+/[^/]+/?$",
    ]

    _authentication_methods = ["login"]
    login_data = ["username", "password"]
    _access_token = None

    # Śledztwo Pisma URL patterns
    SLEDZTWO_SERIES_RE = re.compile(
        r"https?://magazynpismo\.pl/sledztwo-pisma/?$"
    )

    SLEDZTWO_SEASON_RE = re.compile(
        r"https?://magazynpismo\.pl/posluchaj/sezon-(\d+)(/lista)?/?$"
    )

    SLEDZTWO_EPISODE_RE = re.compile(
        r"https?://magazynpismo\.pl/sledztwo-pisma/sezon-(\d+)/[^/]+/?$"
    )

    def __init__(self, options):
        super().__init__(options)
        self._session.headers.update({
            "User-Agent": "Ktor client",
            "Accept": "application/json",
        })

    # ------------------------------------------------------------
    # AUTH
    # ------------------------------------------------------------

    def _login(self, url: str, username: str, password: str):
        login_url = "https://mobile.magazynpismo.pl/wp-json/mobile/v1/user/login"
        payload = {
            "log": username,
            "pwd": password,
            "mobile": "1",
        }

        logging.log(f"Logging in to Pismo Mobile API as {username}")
        response = self._session.post(login_url, data=payload)

        if response.status_code != 200:
            logging.error(
                f"Mobile login failed ({response.status_code}): {response.text[:200]}"
            )
            return

        data = response.json()
        if data.get("login_status") != "logged-in":
            logging.error(
                f"Login failed: {data.get('login_status')} – "
                f"{data.get('message', 'no message')}"
            )
            return

        self._access_token = data.get("mobile_access_token")
        self._session.headers.update({
            "Authorization": f"Bearer {self._access_token}"
        })

        logging.log("Login successful. Access token acquired.")

    # ------------------------------------------------------------
    # DISPATCH
    # ------------------------------------------------------------

    def download(self, url: str) -> Result:
        # Śledztwo Pisma
        if self.SLEDZTWO_EPISODE_RE.match(url):
            return self.download_book_from_url(url)

        if self.SLEDZTWO_SEASON_RE.match(url):
            return self.download_sledztwo_season(url)

        if self.SLEDZTWO_SERIES_RE.match(url):
            return self.download_sledztwo_series(url)

        # audioseriale
        if re.search(r'/odcinek-', url):
            return self.download_book_from_url(url)

        if re.match(self.match[1], url):
            return self.download_series(url)

        raise ValueError(f"Unsupported Pismo URL: {url}")

    def download_from_id(self, book_id: str) -> Audiobook:
        return self.download_book_from_url(book_id)

    # ------------------------------------------------------------
    # ŚLEDZTWO PISMA
    # ------------------------------------------------------------

    def find_sledztwo_season_episodes(self, url: str) -> list[str]:
        """
        Return a list of episode URLs for a Śledztwo Pisma season page.
        Only includes articles that actually have an audio player
        (presence of .player_action > .article_button).
        """
        response = self._session.get(url)
        soup = BeautifulSoup(response.text, "html.parser")

        episodes: list[str] = []

        # Match each full article box
        for box in soup.select("div.article_box"):
            # 🔑 must have an audio player
            if not box.select_one("a.player_action span.article_button"):
                continue

            # Episode link (title link)
            link = box.select_one(
                'a[href*="/sledztwo-pisma/sezon-"]'
            )
            if not link:
                continue

            href = link.get("href")
            if not href:
                continue

            if href.startswith("/"):
                href = f"https://magazynpismo.pl{href}"

            episodes.append(href)

        # Deduplicate, preserve order
        episodes = list(dict.fromkeys(episodes))

        if not episodes:
            logging.log(
                f"Warning: no audio episodes found on season page {url}"
            )

        return episodes

    def download_sledztwo_season(self, url: str) -> Series[str]:
        m = self.SLEDZTWO_SEASON_RE.match(url)
        season_no = m.group(1) if m else "?"

        episodes = self.find_sledztwo_season_episodes(url)

        return Series(
            title=f"Śledztwo Pisma – Sezon {season_no}",
            books=[BookId(ep) for ep in episodes],
        )

    def download_sledztwo_series(self, url: str) -> Series[str]:
        response = self._session.get(url)
        soup = BeautifulSoup(response.text, "html.parser")

        season_urls = set()

        for a in soup.find_all("a", href=True):
            href = a["href"]
            if self.SLEDZTWO_SEASON_RE.search(href):
                if href.startswith("/"):
                    href = f"https://magazynpismo.pl{href}"
                season_urls.add(href)

        if not season_urls:
            logging.log("Warning: no seasons found on Śledztwo Pisma page")

        all_episodes = []
        for season_url in sorted(season_urls):
            all_episodes.extend(
                self.find_sledztwo_season_episodes(season_url)
            )

        return Series(
            title="Śledztwo Pisma",
            books=[BookId(ep) for ep in all_episodes],
        )

    # ------------------------------------------------------------
    # AUDIOSERIALE (existing)
    # ------------------------------------------------------------

    def download_series(self, url: str) -> Series[str]:
        series_slug = url.strip("/").split("/")[-1]
        return Series(
            title=series_slug.replace("-", " ").title(),
            books=[BookId(link) for link in self.download_series_books(url)],
        )

    def download_series_books(self, url: str) -> list[str]:
        print("d_series_books")
        response = self._session.get(url)
        soup = BeautifulSoup(response.text, "html.parser")

        links = []
        for a in soup.find_all("a", href=True):
            href = a["href"]
            if "odcinek-" in href and "/audioseriale-" in href:
                if href.startswith("/"):
                    href = f"https://magazynpismo.pl{href}"
                if href not in links:
                    links.append(href)

        return links

    def is_audio_episode(self, url: str) -> bool:
        info = self.find_book_info(url)
        return bool(info and info.get("download_url"))
    # ------------------------------------------------------------
    # AUDIOBOOK (shared)
    # ------------------------------------------------------------

    def download_book_from_url(self, url: str) -> Audiobook:
        book_info = self.find_book_info(url)
        return Audiobook(
            session=self._session,
            files=self.get_files(book_info),
            metadata=self.get_metadata(book_info),
            cover=self.download_cover(book_info),
        )

    def find_book_info(self, url: str):
        old_ua = self._session.headers.get("User-Agent")
        self._session.headers.update({
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/120.0 Safari/537.36"
            )
        })

        response = self._session.get(url)
        soup = BeautifulSoup(response.text, "html.parser")
        self._session.headers.update({"User-Agent": old_ua})

        player = soup.find(attrs={"data-id": True}) or soup.select_one(".player_action")
        article_id = player.get("data-id") if player else None

        src_url = title = author = narrator = cover_url = None

        if article_id:
            api_url = (
                "https://mobile.magazynpismo.pl/wp-json/mobile/v1/articles/"
                f"{article_id}"
            )
            api_resp = self._session.get(api_url)
            if api_resp.status_code == 200:
                api_data = api_resp.json().get("data", {})
                audio = api_data.get("audio_data", {})
                src_url = audio.get("audio_src")
                title = audio.get("title") or api_data.get("title")
                cover_url = audio.get("cover_src") or api_data.get("image_src")

        if not title:
            h1 = soup.find("h1")
            title = h1.text.strip() if h1 else "Unknown"

        for p in soup.find_all("p"):
            text = p.get_text().replace("\xa0", " ").strip()
            if not author and text.startswith(("Autor:", "Autorka:")):
                author = text.split(":", 1)[1].strip()
            elif not narrator and text.startswith(("Lektor:", "Lektorzy:")):
                narrator = text.split(":", 1)[1].strip()

        if not author:
            meta = soup.find("meta", attrs={"name": "author"})
            if meta:
                author = meta.get("content")

        if not cover_url:
            og = soup.find("meta", property="og:image")
            if og:
                cover_url = og.get("content")

        return {
            "url": url,
            "title": title,
            "author": author,
            "narrator": narrator,
            "cover_url": cover_url,
            "download_url": src_url,
        }

    def get_files(self, book_info) -> list[AudiobookFile]:
        url = book_info.get("download_url")
        if not url:
            print(book_info)
            raise NoFilesFound(url=book_info.get("url"))
        return [AudiobookFile(url=url, ext="mp3")]

    def get_metadata(self, book_info) -> AudiobookMetadata:
        metadata = AudiobookMetadata(book_info["title"])
        if book_info.get("author"):
            metadata.add_author(book_info["author"])
        if book_info.get("narrator"):
            metadata.add_narrator(book_info["narrator"])
        return metadata

    def download_cover(self, book_info) -> Cover | None:
        if not book_info.get("cover_url"):
            return None
        try:
            return Cover(
                self._session.get(book_info["cover_url"]).content,
                "jpg",
            )
        except Exception:
            return None

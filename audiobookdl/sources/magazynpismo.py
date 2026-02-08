from .source import Source
from audiobookdl import (
    AudiobookFile, logging, AudiobookMetadata,
    Cover, Audiobook, Result, Series, BookId
)
from audiobookdl.exceptions import NoFilesFound
import re
from bs4 import BeautifulSoup


class PismoSource(Source):
    names = ["Magazyn Pismo"]

    match = [
        # Śledztwo Pisma
        r"https?://magazynpismo\.pl/sledztwo-pisma/?$",
        r"https?://magazynpismo\.pl/sledztwo-pisma/sezon-\d+/?$",
        r"https?://magazynpismo\.pl/posluchaj/sezon-\d+(/lista)?/?$",

        # Podcast pages
        r"https?://magazynpismo\.pl/podcasty/[^/]+(/lista)?/?$",
        r"https?://magazynpismo\.pl/posluchaj/[^/]+(/lista)?/?$",

        # Archive pages
        r"https?://magazynpismo\.pl/archiwum/?$",
        r"https?://magazynpismo\.pl/Edycje/\d{2}-\d{4}/?$",

        # Single-article URLs (catch-all)
        r"https?://magazynpismo\.pl/(?!podcasty|posluchaj|sledztwo-pisma|Edycje|archiwum)[^/]+/?$",
        r"https?://magazynpismo\.pl/(?!podcasty|posluchaj|sledztwo-pisma|Edycje|archiwum)[^/]+/[^/]+/?$",
    ]

    _authentication_methods = ["login"]
    login_data = ["username", "password"]

    SLEDZTWO_SEASON_RE = re.compile(r"/sezon-(\d+)")

    def __init__(self, options):
        super().__init__(options)
        self._session.headers.update({
            "User-Agent": "Mozilla/5.0",
            "Accept": "application/json"
        })

    # ---------------------------------------------------------
    # LOGIN
    # ---------------------------------------------------------

    def _login(self, url: str, username: str, password: str):
        login_url = "https://mobile.magazynpismo.pl/wp-json/mobile/v1/user/login"
        payload = {"log": username, "pwd": password, "mobile": "1"}

        response = self._session.post(login_url, data=payload)
        if response.status_code != 200:
            logging.error("Login failed")
            return

        data = response.json()
        if data.get("login_status") == "logged-in":
            token = data.get("mobile_access_token")
            self._session.headers["Authorization"] = f"Bearer {token}"
            logging.log("Logged in to Pismo")
        else:
            logging.error("Login rejected")

    # ---------------------------------------------------------
    # ROUTING
    # ---------------------------------------------------------

    def download(self, url: str) -> Result:
        if "/archiwum" in url:
            return Series("Archiwum Pisma", [
                BookId(u) for u in self.find_archive_issues(url)
            ])

        if "/Edycje/" in url:
            return Series(
                title=url.rstrip("/").split("/")[-1],
                books=[BookId(u) for u in self.find_archive_issue_episodes(url)]
            )

        if url.rstrip("/").endswith("/sledztwo-pisma"):
            return Series(
                "Śledztwo Pisma",
                books=[BookId(u) for u in self.find_sledztwo_seasons(url)]
            )

        if "/posluchaj/sezon-" in url:
            season = re.search(r"sezon-(\d+)", url)
            season_no = season.group(1) if season else "?"
            return Series(
                title=f"Śledztwo Pisma – Sezon {season_no}",
                books=[BookId(u) for u in self.find_sledztwo_season_episodes(url)]
            )

        if "/sezon-" in url and "/sledztwo-pisma/" in url:
            return Series(
                title=f"Śledztwo Pisma – {url.rstrip('/').split('/')[-1]}",
                books=[BookId(u) for u in self.find_sledztwo_season_episodes(url)]
            )

        if "/podcasty/" in url or "/posluchaj/" in url:
            return Series(
                title=url.rstrip("/").split("/")[-1].replace("-", " ").title(),
                books=[BookId(u) for u in self.find_article_item_episodes(url)]
            )

        return self.download_book_from_url(url)

    # ---------------------------------------------------------
    # SERIES FINDERS
    # ---------------------------------------------------------

    def find_article_item_episodes(self, url: str) -> list[str]:
        soup = BeautifulSoup(self._session.get(url).text, "html.parser")
        episodes = []

        for article in soup.select("article.article-item"):
            link = article.select_one(".article-item__title a")
            if not link:
                continue
            href = link.get("href")
            if href.startswith("/"):
                href = f"https://magazynpismo.pl{href}"
            episodes.append(href)

        return list(dict.fromkeys(episodes))

    def find_sledztwo_seasons(self, url: str) -> list[str]:
        soup = BeautifulSoup(self._session.get(url).text, "html.parser")
        seasons = []

        for a in soup.select("a[href*='/sezon-']"):
            href = a.get("href")
            if href.startswith("/"):
                href = f"https://magazynpismo.pl{href}"
            if "/sledztwo-pisma/sezon-" in href:
                seasons.append(href)

        return list(dict.fromkeys(seasons))

    def find_sledztwo_season_episodes(self, url: str) -> list[str]:
        soup = BeautifulSoup(self._session.get(url).text, "html.parser")
        episodes = []

        for box in soup.select("div.article_box"):
            if not box.select_one("a.player_action span.article_button"):
                continue

            link = box.select_one('a[href*="/sledztwo-pisma/sezon-"]')
            if not link:
                continue

            href = link.get("href")
            if href.startswith("/"):
                href = f"https://magazynpismo.pl{href}"
            episodes.append(href)

        return list(dict.fromkeys(episodes))

    def find_archive_issues(self, url: str) -> list[str]:
        soup = BeautifulSoup(self._session.get(url).text, "html.parser")
        issues = []

        for item in soup.select("div.archive-item"):
            link = item.select_one("a.archive-item__cover")
            if not link:
                continue
            href = link.get("href")
            if href.startswith("/"):
                href = f"https://magazynpismo.pl{href}"
            issues.append(href)

        return list(dict.fromkeys(issues))

    def find_archive_issue_episodes(self, url: str) -> list[str]:
        soup = BeautifulSoup(self._session.get(url).text, "html.parser")
        episodes = []

        for item in soup.select("div.archive-item"):
            if not item.select_one("a.player_action"):
                continue

            link = item.select_one("a.archive-item__cover")
            if not link:
                continue

            href = link.get("href")
            if href.startswith("/"):
                href = f"https://magazynpismo.pl{href}"
            episodes.append(href)

        return list(dict.fromkeys(episodes))

    # ---------------------------------------------------------
    # SINGLE EPISODE
    # ---------------------------------------------------------

    def download_book_from_url(self, url: str) -> Audiobook | None:
        info = self.find_book_info(url)
        if not info:
            raise NoFilesFound(url=url)

        return Audiobook(
            session=self._session,
            files=self.get_files(info),
            metadata=self.get_metadata(info),
            cover=self.download_cover(info),
        )

    def find_book_info(self, url: str):
        soup = BeautifulSoup(self._session.get(url).text, "html.parser")
        player = soup.select_one("a.player_action[data-id]")
        if not player:
            return None

        article_id = player.get("data-id")
        api = f"https://mobile.magazynpismo.pl/wp-json/mobile/v1/articles/{article_id}"
        r = self._session.get(api)
        if r.status_code != 200:
            return None

        data = r.json().get("data", {})
        audio = data.get("audio_data", {})
        src = audio.get("audio_src")
        if not src:
            return None

        return {
            "url": url,
            "download_url": src,
            "title": audio.get("title") or data.get("title"),
            "cover_url": audio.get("cover_src") or data.get("image_src"),
        }

    def download_from_id(self, book_id: str) -> Audiobook:
        # In PismoSource, BookId is just a full URL
        return self.download_book_from_url(book_id)
    # ---------------------------------------------------------
    # AUDIOBOOKDL ADAPTERS
    # ---------------------------------------------------------

    def get_files(self, info):
        return [AudiobookFile(url=info["download_url"], ext="mp3")]

    def get_metadata(self, info):
        return AudiobookMetadata(info["title"])

    def download_cover(self, info):
        if not info.get("cover_url"):
            return None
        try:
            return Cover(self._session.get(info["cover_url"]).content, "jpg")
        except Exception:
            return None

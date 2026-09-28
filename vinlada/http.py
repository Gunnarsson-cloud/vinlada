"""Artig HTTP-hämtning: cache på disk, robots.txt och paus mellan anrop."""

from __future__ import annotations

import gzip
import hashlib
import json
import logging
import time
import urllib.error
import urllib.request
import urllib.robotparser
from pathlib import Path
from urllib.parse import urlsplit

log = logging.getLogger(__name__)

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/140.0.0.0 Safari/537.36"
)


class FetchError(Exception):
    def __init__(self, message: str, status: int | None = None) -> None:
        super().__init__(message)
        self.status = status


class Fetcher:
    def __init__(
        self,
        cache_dir: Path | None = Path(".cache"),
        ttl_hours: float = 6,
        delay: float = 1.0,
        timeout: float = 40,
        respect_robots: bool = True,
    ) -> None:
        self.cache_dir = cache_dir
        self.ttl = ttl_hours * 3600
        self.delay = delay
        self.timeout = timeout
        self.respect_robots = respect_robots
        self._last_request: dict[str, float] = {}
        self._robots: dict[str, urllib.robotparser.RobotFileParser | None] = {}
        if cache_dir:
            cache_dir.mkdir(parents=True, exist_ok=True)

    # -- cache --------------------------------------------------------------

    def _cache_path(self, url: str) -> Path | None:
        if not self.cache_dir:
            return None
        return self.cache_dir / (hashlib.sha1(url.encode()).hexdigest() + ".txt")

    def _from_cache(self, url: str) -> str | None:
        path = self._cache_path(url)
        if path and path.exists() and time.time() - path.stat().st_mtime < self.ttl:
            return path.read_text(encoding="utf-8")
        return None

    def _to_cache(self, url: str, body: str) -> None:
        path = self._cache_path(url)
        if path:
            path.write_text(body, encoding="utf-8")

    # -- robots.txt -----------------------------------------------------------

    def allowed(self, url: str) -> bool:
        if not self.respect_robots:
            return True
        parts = urlsplit(url)
        origin = f"{parts.scheme}://{parts.netloc}"
        if origin not in self._robots:
            parser = urllib.robotparser.RobotFileParser()
            try:
                text = self._raw_get(origin + "/robots.txt")
                parser.parse(text.splitlines())
            except FetchError:
                parser = None  # ingen robots.txt åtkomlig -> tillåt
            self._robots[origin] = parser
        parser = self._robots[origin]
        return parser is None or parser.can_fetch(USER_AGENT, url)

    # -- hämtning -------------------------------------------------------------

    def _throttle(self, host: str) -> None:
        wait = self._last_request.get(host, 0) + self.delay - time.time()
        if wait > 0:
            time.sleep(wait)
        self._last_request[host] = time.time()

    def _raw_get(self, url: str, accept: str = "*/*", headers: dict[str, str] | None = None) -> str:
        """GET med ett nytt försök vid timeout eller serverfel (5xx)."""
        for attempt in (1, 2):
            try:
                return self._request(url, accept, headers)
            except FetchError as exc:
                retry = exc.status is None or exc.status >= 500
                if attempt == 2 or not retry:
                    raise
                log.debug("försöker igen: %s", exc)
                time.sleep(3)
        raise AssertionError("unreachable")

    def _request(self, url: str, accept: str, headers: dict[str, str] | None) -> str:
        self._throttle(urlsplit(url).netloc)
        is_html = "html" in accept
        req = urllib.request.Request(url, headers={
            "User-Agent": USER_AGENT,
            "Accept": ("text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,"
                       "image/webp,*/*;q=0.8") if is_html else accept,
            "Accept-Language": "sv-SE,sv;q=0.9,en-US;q=0.6,en;q=0.5",
            "Accept-Encoding": "gzip",
            "Sec-Fetch-Dest": "document" if is_html else "empty",
            "Sec-Fetch-Mode": "navigate" if is_html else "cors",
            "Sec-Fetch-Site": "none" if is_html else "same-origin",
            "Upgrade-Insecure-Requests": "1",
            **(headers or {}),
        })
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                data = resp.read()
                if resp.headers.get("Content-Encoding") == "gzip":
                    data = gzip.decompress(data)
                charset = resp.headers.get_content_charset() or "utf-8"
                return data.decode(charset, errors="replace")
        except urllib.error.HTTPError as exc:
            raise FetchError(f"HTTP {exc.code} för {url}", status=exc.code) from exc
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            raise FetchError(f"{exc} för {url}") from exc

    def get(self, url: str, accept: str = "text/html,*/*", headers: dict[str, str] | None = None) -> str:
        cached = self._from_cache(url)
        if cached is not None:
            return cached
        if not self.allowed(url):
            raise FetchError(f"robots.txt tillåter inte {url}")
        log.debug("GET %s", url)
        body = self._raw_get(url, accept, headers)
        self._to_cache(url, body)
        return body

    def get_json(self, url: str, headers: dict[str, str] | None = None) -> object:
        body = self.get(url, accept="application/json", headers=headers)
        try:
            return json.loads(body)
        except ValueError as exc:
            raise FetchError(f"Inte JSON: {url}") from exc

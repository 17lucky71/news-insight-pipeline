"""뉴스 수집 모듈.

- 방법 1 (RSS)  : 언론사 RSS 피드에서 기사 목록(제목, 링크, 날짜, 요약)을 가져온다.
- 방법 2 (크롤링): RSS 로 얻은 기사 링크에 접속해 BeautifulSoup 으로 본문을 추출한다.

수집한 결과는 가공하지 않고 그대로 raw 저장소에 저장한다. (정제는 clean 단계 담당)
"""
import logging
import math
import time
from urllib.parse import urlparse
from urllib.robotparser import RobotFileParser

import feedparser
import requests
from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)


class FetchError(Exception):
    """수집 중 복구할 수 없는 오류."""


class HttpClient:
    """타임아웃, 재시도, 요청 간 지연, robots.txt 확인을 담당하는 HTTP 클라이언트."""

    def __init__(self, http_cfg: dict):
        self.timeout = http_cfg.get("timeout", 10)
        self.max_retries = http_cfg.get("max_retries", 2)
        self.delay = http_cfg.get("request_delay", 1.0)
        self.user_agent = http_cfg.get("user_agent", "news-insight-pipeline")
        self.session = requests.Session()
        self.session.headers["User-Agent"] = self.user_agent
        self._robots: dict[str, RobotFileParser] = {}
        self._last_request = 0.0

    def _wait(self) -> None:
        """직전 요청과 최소 delay 초 간격을 둔다 (과도한 요청 방지)."""
        elapsed = time.monotonic() - self._last_request
        if elapsed < self.delay:
            time.sleep(self.delay - elapsed)
        self._last_request = time.monotonic()

    def get(self, url: str) -> requests.Response:
        """GET 요청. 타임아웃/연결 오류/5xx 는 재시도, 4xx 는 바로 실패 처리."""
        last_error = "알 수 없는 오류"
        attempts = self.max_retries + 1
        for attempt in range(1, attempts + 1):
            self._wait()
            try:
                resp = self.session.get(url, timeout=self.timeout)
                resp.raise_for_status()
                return resp
            except requests.Timeout:
                last_error = f"타임아웃({self.timeout}초)"
            except requests.ConnectionError:
                last_error = "연결 실패"
            except requests.HTTPError as e:
                status = e.response.status_code if e.response is not None else 0
                last_error = f"HTTP {status}"
                if 400 <= status < 500:  # 클라이언트 오류는 재시도해도 결과가 같음
                    break
            logger.warning("요청 실패 (%s) [%d/%d]: %s", last_error, attempt, attempts, url)
        raise FetchError(f"{last_error}: {url}")

    def allowed(self, url: str) -> bool:
        """robots.txt 정책상 수집이 허용된 URL 인지 확인한다 (도메인별 1회 조회 후 캐시)."""
        parts = urlparse(url)
        base = f"{parts.scheme}://{parts.netloc}"
        if base not in self._robots:
            rp = RobotFileParser()
            try:
                resp = self.session.get(base + "/robots.txt", timeout=self.timeout)
                # robots.txt 가 없으면(404 등) 제한 없음으로 간주
                rp.parse(resp.text.splitlines() if resp.status_code == 200 else [])
            except requests.RequestException:
                logger.warning("robots.txt 를 확인하지 못했습니다: %s", base)
                rp.parse([])
            self._robots[base] = rp
        return self._robots[base].can_fetch(self.user_agent, url)


# ---------------- 방법 1: RSS ----------------
def fetch_rss(client: HttpClient, feed_url: str, category: str, limit: int) -> list[dict]:
    resp = client.get(feed_url)
    feed = feedparser.parse(resp.content)
    if feed.bozo and not feed.entries:
        raise FetchError(f"RSS 형식을 읽을 수 없습니다: {feed_url}")

    items = []
    for entry in feed.entries[:limit]:
        items.append({
            "title": entry.get("title"),
            "link": entry.get("link"),
            "description": entry.get("summary"),
            "published": entry.get("published") or entry.get("updated"),
            "author": entry.get("author"),
            "category": category,
            "feed_url": feed_url,
        })
    return items


# ---------------- 방법 2: 크롤링 ----------------
def _meta(soup: BeautifulSoup, name: str) -> str | None:
    tag = soup.find("meta", property=name) or soup.find("meta", attrs={"name": name})
    return tag.get("content") if tag else None


def _extract_body(soup: BeautifulSoup, selectors: list[str]) -> str:
    """설정된 CSS 선택자를 순서대로 시도해 본문 문단을 모은다."""
    for selector in selectors:
        paragraphs = [p.get_text(" ", strip=True) for p in soup.select(selector)]
        text = "\n".join(p for p in paragraphs if p)
        if len(text) >= 100:  # 너무 짧으면 본문이 아니라고 보고 다음 선택자 시도
            return text
    return ""


def crawl_article(client: HttpClient, url: str, selectors: list[str]) -> dict:
    if not client.allowed(url):
        raise FetchError(f"robots.txt 정책상 수집이 금지된 페이지입니다: {url}")

    resp = client.get(url)
    if not resp.encoding or resp.encoding.lower() == "iso-8859-1":
        resp.encoding = resp.apparent_encoding  # 한글 깨짐 방지
    soup = BeautifulSoup(resp.text, "html.parser")

    return {
        "url": url,
        "title": _meta(soup, "og:title") or (soup.title.get_text(strip=True) if soup.title else None),
        "body": _extract_body(soup, selectors),
        "published": _meta(soup, "article:published_time"),
        "description": _meta(soup, "og:description"),
    }


# ---------------- fetch 명령 ----------------
def run_fetch(args, config: dict, storage) -> None:
    sources = config.get("sources", {})
    name = args.source or config.get("default_source")
    if name not in sources:
        logger.error("등록되지 않은 소스입니다: %s (사용 가능: %s)", name, ", ".join(sources) or "없음")
        return
    source = sources[name]

    feeds = source.get("rss", {})
    if args.category:
        if args.category not in feeds:
            logger.error("카테고리 '%s' 가 없습니다 (사용 가능: %s)", args.category, ", ".join(feeds))
            return
        feeds = {args.category: feeds[args.category]}

    client = HttpClient(config.get("http", {}))
    logger.info("뉴스 수집 시작: source=%s, category=%s, limit=%d",
                name, args.category or "전체", args.limit)

    # 1) RSS 로 기사 목록 수집
    per_feed = math.ceil(args.limit / len(feeds))
    items = []
    for category, feed_url in feeds.items():
        try:
            got = fetch_rss(client, feed_url, category, per_feed)
            logger.info("RSS [%s] %d건 수신", category, len(got))
            items.extend(got)
        except FetchError as e:
            logger.error("RSS 수집 실패 [%s]: %s", category, e)
    items = items[:args.limit]

    rss_ok = crawl_ok = crawl_fail = crawl_skip = 0
    for i, item in enumerate(items, 1):
        link = item.get("link")
        if not link:
            logger.warning("링크가 없는 RSS 항목을 건너뜁니다: %s", item.get("title"))
            continue
        storage.insert_raw(name, "rss", link, item)
        rss_ok += 1

        # 2) 기사 페이지 크롤링으로 본문 수집
        if args.no_crawl:
            continue
        if storage.raw_exists(link, "crawl"):
            crawl_skip += 1
            continue
        try:
            data = crawl_article(client, link, source.get("article_selectors", ["article p"]))
            data["category"] = item["category"]
            storage.insert_raw(name, "crawl", link, data)
            if data["body"]:
                crawl_ok += 1
                logger.info("[%d/%d] 크롤링 완료 (%d자): %s", i, len(items), len(data["body"]),
                            (item.get("title") or "")[:30])
            else:
                crawl_fail += 1
                logger.warning("[%d/%d] 본문을 찾지 못했습니다 (선택자 확인 필요): %s", i, len(items), link)
        except FetchError as e:
            crawl_fail += 1
            logger.error("[%d/%d] 크롤링 실패: %s", i, len(items), e)

    logger.info("수집 완료: RSS %d건 / 크롤링 %d건 성공, %d건 실패, %d건 기존 수집분 스킵",
                rss_ok, crawl_ok, crawl_fail, crawl_skip)
    attempted = crawl_ok + crawl_fail
    if attempted >= 3 and crawl_fail / attempted >= 0.5:
        # 대부분 실패했다면 개별 기사 문제가 아니라 사이트 구조(HTML) 변경일 가능성이 높다
        logger.warning("크롤링 실패율이 %.0f%% 입니다. 사이트 구조가 바뀌었을 수 있으니 "
                       "config.json 의 article_selectors 를 확인하세요.", crawl_fail / attempted * 100)
    logger.info("raw 저장소에 저장 완료 (누적 raw %d건)", storage.count("raw_articles"))

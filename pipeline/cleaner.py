"""데이터 정제 모듈 (raw → clean).

정제 규칙
1. 필수 필드 검증 : url, title, content 가 없으면 저장하지 않는다.
2. 텍스트 정규화 : HTML 엔티티/태그 제거, 유니코드 NFC 정규화, 공백 정리,
                   사진 설명·기자 이메일·저작권 문구 등 본문 외 텍스트 제거.
3. 날짜 형식 통일 : RFC 822(RSS), ISO 8601(웹페이지) → 'YYYY-MM-DD HH:MM:SS' (한국 시간).
4. 결측값 처리   : 본문 없음 → RSS 요약으로 대체 / 발행일 없음 → 수집 시각으로 대체 /
                   카테고리 없음 → '미분류'.
5. 중복 처리     : URL 기준. 정책(skip/upsert)에 따라 건너뛰거나 덮어쓴다.
"""
import html
import logging
import re
import unicodedata
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)

KST = timezone(timedelta(hours=9))
DATE_FORMAT = "%Y-%m-%d %H:%M:%S"
MIN_CONTENT_LENGTH = 50

# URL 에서 제거할 추적용 파라미터 (같은 기사인데 URL 이 달라 중복 판단이 안 되는 문제 방지)
TRACKING_PARAMS = ("utm_", "input", "from", "ref")

# 본문에서 통째로 지울 줄의 패턴
DROP_LINE_PATTERNS = [
    re.compile(r"\d{4}\.\d{1,2}\.\d{1,2}\.?\s+[\w.\-]+@[\w.\-]+\s*$"),   # 사진 설명: "... 2026.10.3 abc@yna.co.kr"
    re.compile(r"^[\w.\-]+@[\w.\-]+\.\w+$"),                              # 기자 이메일만 있는 줄
    re.compile(r"저작권자|무단 전재|재배포 금지|제보는 카카오톡"),          # 저작권·제보 안내
    re.compile(r"\d{4}/\d{2}/\d{2} \d{2}:\d{2} 송고"),                    # 송고 시각
]
# 문단 앞에 붙는 "(서울=연합뉴스) 홍길동 기자 =" 형태의 머리말
BYLINE_PATTERN = re.compile(r"^\([^()]{1,20}=[^()]{1,10}\)\s*[^=\n]{0,40}?=\s*")


# ---------------- 정규화 함수 ----------------
def normalize_text(text: str | None) -> str:
    """HTML 엔티티 해제, 유니코드 정규화, 줄 단위 공백 정리."""
    if not text:
        return ""
    text = html.unescape(text)
    text = unicodedata.normalize("NFC", text).replace("\xa0", " ")
    lines = [re.sub(r"\s+", " ", line).strip() for line in text.splitlines()]
    return "\n".join(line for line in lines if line)


def strip_html(text: str | None) -> str:
    if not text:
        return ""
    return BeautifulSoup(text, "html.parser").get_text(" ")


def clean_body(body: str) -> str:
    """본문에서 사진 설명, 기자 머리말·이메일, 저작권 문구를 제거한다."""
    kept = []
    for line in normalize_text(body).splitlines():
        if any(p.search(line) for p in DROP_LINE_PATTERNS):
            continue
        line = BYLINE_PATTERN.sub("", line).strip()
        if line:
            kept.append(line)
    return "\n".join(kept)


def normalize_url(url: str | None) -> str:
    if not url:
        return ""
    parts = urlparse(url.strip())
    query = [(k, v) for k, v in parse_qsl(parts.query)
             if not k.lower().startswith(TRACKING_PARAMS)]
    return urlunparse(parts._replace(query=urlencode(query), fragment=""))


def parse_date(value: str | None) -> str | None:
    """여러 형식의 날짜 문자열을 'YYYY-MM-DD HH:MM:SS'(KST) 로 통일. 실패하면 None."""
    if not value:
        return None
    value = value.strip()
    dt = None
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))  # ISO 8601
    except ValueError:
        try:
            dt = parsedate_to_datetime(value)                        # RFC 822 (RSS)
        except (TypeError, ValueError):
            for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y.%m.%d %H:%M", "%Y-%m-%d"):
                try:
                    dt = datetime.strptime(value, fmt)
                    break
                except ValueError:
                    continue
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=KST)  # 시간대 정보가 없으면 한국 시간으로 간주
    return dt.astimezone(KST).strftime(DATE_FORMAT)


# ---------------- raw 병합 + 정제 ----------------
def build_article(url: str, rss: dict | None, crawl: dict | None) -> tuple[dict | None, str]:
    """같은 URL 의 RSS/크롤링 raw 를 합쳐 clean 기사 1건을 만든다.
    반환: (기사 dict 또는 None, 실패 사유)"""
    rss_p = rss["payload"] if rss else {}
    crawl_p = crawl["payload"] if crawl else {}

    title = normalize_text(rss_p.get("title") or crawl_p.get("title"))

    content = clean_body(crawl_p.get("body", ""))
    if len(content) < MIN_CONTENT_LENGTH:  # 결측: 본문 크롤링 실패 → RSS 요약으로 대체
        fallback = normalize_text(strip_html(rss_p.get("description") or crawl_p.get("description")))
        if fallback:
            logger.warning("본문 부족 → RSS 요약으로 대체: %s", url)
            content = fallback

    # 필수 필드 검증
    if not url:
        return None, "URL 없음"
    if not title:
        return None, "제목 없음"
    if not content:
        return None, "본문 없음"

    first = rss or crawl
    published = parse_date(crawl_p.get("published")) or parse_date(rss_p.get("published"))
    if not published:  # 결측: 발행일 없음 → 수집 시각으로 대체
        logger.warning("발행일 없음 → 수집 시각으로 대체: %s", url)
        published = first["fetched_at"]

    methods = [m for m, raw in (("rss", rss), ("crawl", crawl)) if raw]
    return {
        "raw_id": (crawl or rss)["id"],
        "url": url,
        "title": title,
        "content": content,
        "category": rss_p.get("category") or crawl_p.get("category") or "미분류",
        "source": first["source"],
        "method": "+".join(methods),
        "published_at": published,
        "collected_at": first["fetched_at"],
    }, ""


def run_clean(args, config: dict, storage) -> None:
    policy = args.policy or config.get("duplicate_policy", "skip")
    raws = storage.get_unprocessed_raw()
    if not raws:
        logger.info("정제할 새 raw 데이터가 없습니다. 먼저 fetch 를 실행하세요.")
        return

    # 정규화된 URL 별로 묶기 (입력 순서 유지)
    groups: dict[str, list[dict]] = {}
    for r in raws:
        groups.setdefault(normalize_url(r["url"]), []).append(r)
    logger.info("정제 시작: raw %d건 → 기사 %d건, 중복 정책=%s", len(raws), len(groups), policy)

    stats = {"inserted": 0, "updated": 0, "skipped": 0, "invalid": 0}
    for url, group in groups.items():
        found = {}
        for method in ("rss", "crawl"):
            same = [r for r in group if r["method"] == method]
            # 이번 묶음에 없으면 예전에 처리된 raw 에서 찾아 합친다
            found[method] = same[-1] if same else storage.get_latest_raw(group[0]["url"], method)
        article, reason = build_article(url, found["rss"], found["crawl"])

        if article is None:
            stats["invalid"] += 1
            logger.warning("검증 실패(%s), 저장하지 않음: %s", reason, url)
        else:
            result = storage.save_article(article, policy)
            stats[result] += 1
            if result == "skipped":
                logger.info("중복 스킵: %s", article["title"][:30])

        for r in group:  # 처리한 raw 표시 (성공/실패 무관하게 다시 처리하지 않음)
            storage.mark_raw_processed(r["id"])

    logger.info("정제 완료: 신규 %d건, 갱신 %d건, 중복 스킵 %d건, 검증 실패 %d건",
                stats["inserted"], stats["updated"], stats["skipped"], stats["invalid"])
    logger.info("clean 저장소 누적 기사 %d건", storage.count("articles"))

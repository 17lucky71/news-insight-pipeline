"""리포트 생성 모듈 (report 명령).

리포트 구성
1. 데이터 개요   : 기간, 기사 수, 카테고리 수
2. 품질 지표     : 정제 통과율, 본문 크롤링 성공률, AI 요약 완료율, 평균 길이·압축률
3. TOP N 집계    : 카테고리별 기사 수, 제목 키워드 빈도
4. AI 인사이트   : 가장 최근 analyze 결과
5. 차트          : 카테고리별 뉴스 수, 일자별 수집 추이 (+ 감성 분포)
콘솔에 출력하고 output/reports 에 MD 또는 TXT 파일로 저장한다.
"""
import logging
import os
import re
from collections import Counter
from datetime import datetime
from pathlib import Path

from pipeline.analyzer import SECTIONS
from pipeline.visualizer import make_charts

logger = logging.getLogger(__name__)

# 제목 키워드 집계에서 제외할 흔한 단어
STOPWORDS = {"있다", "없다", "하는", "위해", "대한", "관련", "이번", "오늘", "올해", "지난",
             "통해", "따라", "등", "및", "에서", "으로", "까지", "부터", "속보", "종합", "단독"}
WORD_PATTERN = re.compile(r"[가-힣A-Za-z0-9]{2,}")


def pct(part: int, total: int) -> str:
    return f"{part / total * 100:.1f}%" if total else "-"


def quality_metrics(articles: list[dict], storage) -> list[tuple[str, str, str]]:
    """(지표 이름, 값, 설명) 목록."""
    total = len(articles)
    raw_urls = storage.count_raw_urls()
    crawled = sum(1 for a in articles if "crawl" in a["method"])
    summarized = [a for a in articles if a["summary"]]
    avg_content = sum(len(a["content"]) for a in articles) / total if total else 0
    avg_summary = (sum(len(a["summary"]) for a in summarized) / len(summarized)) if summarized else 0
    ratio = (sum(len(a["summary"]) / len(a["content"]) for a in summarized) / len(summarized)
             if summarized else 0)

    return [
        ("정제 통과율", f"{pct(storage.count('articles'), raw_urls)} ({storage.count('articles')}/{raw_urls})",
         "수집한 고유 URL 중 필수 필드 검증을 통과해 clean 에 저장된 비율"),
        ("본문 크롤링 성공률", f"{pct(crawled, total)} ({crawled}/{total})",
         "RSS 요약이 아닌 크롤링한 본문을 확보한 기사 비율"),
        ("AI 요약 완료율", f"{pct(len(summarized), total)} ({len(summarized)}/{total})",
         "AI 요약이 생성된 기사 비율"),
        ("평균 본문 길이", f"{avg_content:,.0f}자", "정제된 본문의 평균 글자 수"),
        ("평균 요약 길이 / 압축률", f"{avg_summary:,.0f}자 / {ratio * 100:.1f}%",
         "요약문 평균 길이와 본문 대비 요약 길이 비율"),
    ]


def top_categories(articles: list[dict], n: int) -> list[tuple[str, int]]:
    return Counter(a["category"] for a in articles).most_common(n)


def top_keywords(articles: list[dict], n: int) -> list[tuple[str, int]]:
    words = Counter()
    for a in articles:
        # 한 기사 제목에 같은 단어가 여러 번 나와도 1번으로 센다
        words.update({w for w in WORD_PATTERN.findall(a["title"]) if w not in STOPWORDS})
    return words.most_common(n)


def build_report(articles, storage, analysis, charts, top_n, report_dir) -> list[str]:
    """마크다운 형식의 리포트 줄 목록을 만든다."""
    dates = sorted(a["published_at"][:10] for a in articles)
    out = [
        "# AI 뉴스 트렌드 종합 리포트",
        "",
        f"- 생성 시각: {datetime.now():%Y-%m-%d %H:%M:%S}",
        f"- 기사 발행 기간: {dates[0]} ~ {dates[-1]}",
        f"- 분석 기사 수: {len(articles)}건 / 카테고리 {len({a['category'] for a in articles})}개",
        "",
        "## 1. 품질 지표",
        "",
        "| 지표 | 값 | 설명 |",
        "|---|---|---|",
    ]
    out += [f"| {name} | {value} | {desc} |" for name, value, desc in quality_metrics(articles, storage)]

    out += ["", f"## 2. TOP {top_n} 집계", "", "### 카테고리별 기사 수", "",
            "| 순위 | 카테고리 | 기사 수 | 비율 |", "|---|---|---|---|"]
    out += [f"| {i} | {c} | {n}건 | {pct(n, len(articles))} |"
            for i, (c, n) in enumerate(top_categories(articles, top_n), 1)]
    out += ["", "### 제목 키워드 빈도", "", "| 순위 | 키워드 | 등장 기사 수 |", "|---|---|---|"]
    out += [f"| {i} | {w} | {n}건 |" for i, (w, n) in enumerate(top_keywords(articles, top_n), 1)]

    out += ["", "## 3. AI 인사이트", ""]
    if analysis:
        r = analysis["result"]
        out.append(f"> 분석 ID {analysis['id']} · {analysis['created_at']} · 기사 {analysis['article_count']}건 · "
                   f"카테고리 {analysis['category'] or '전체'} · 모델 {analysis['model']}")
        for key, title in SECTIONS:
            out += ["", f"### {title}", ""]
            items = r.get(key) or []
            out += [", ".join(items)] if key == "keywords" else [f"- {x}" for x in items]
    else:
        out.append("저장된 분석 결과가 없습니다. `python main.py analyze` 를 먼저 실행하세요.")

    out += ["", "## 4. 차트", ""]
    for title, path in charts.items():
        rel = Path(os.path.relpath(path, report_dir)).as_posix()  # 리포트 파일 기준 상대 경로
        out += [f"### {title}", "", f"![{title}]({rel})", ""]
    return out


def to_text(md_lines: list[str]) -> str:
    """마크다운을 콘솔/TXT 용 일반 텍스트로 간단히 변환."""
    text = []
    for line in md_lines:
        if line.startswith("|---"):
            continue
        if line.startswith("|"):
            line = "  " + "  ".join(c.strip() for c in line.strip("|").split("|"))
        line = re.sub(r"^#+ ", "", line)
        line = re.sub(r"!\[(.+?)\]\((.+?)\)", r"[\1] \2", line)
        text.append(line.replace("> ", "", 1) if line.startswith("> ") else line)
    return "\n".join(text)


def run_report(args, config: dict, storage) -> None:
    articles = storage.get_articles(category=args.category)
    if not articles:
        logger.warning("리포트를 만들 기사가 없습니다. fetch → clean 을 먼저 실행하세요.")
        return

    output = config.get("output", {})
    report_dir = Path(output.get("report_dir", "output/reports"))
    report_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")

    logger.info("리포트 생성 시작: 기사 %d건, TOP %d", len(articles), args.top)
    charts = make_charts(articles, output.get("chart_dir", "output/charts"), stamp)
    analysis = storage.get_analysis(args.analysis_id) if args.analysis_id else storage.get_latest_analysis()
    md_lines = build_report(articles, storage, analysis, charts, args.top, report_dir)

    text = to_text(md_lines)
    print()
    print(text)
    print()

    path = report_dir / f"report_{stamp}.{args.format}"
    path.write_text("\n".join(md_lines) if args.format == "md" else text, encoding="utf-8")
    logger.info("리포트 저장 완료: %s", path)

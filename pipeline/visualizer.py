"""matplotlib 시각화 모듈.

- 카테고리별 뉴스 수 (막대 그래프)
- 일자별 수집 추이 (선 그래프)
- 감성 분포 (보너스, 감성 분석 결과가 있을 때만)
모든 차트는 한글 폰트를 적용해 PNG 파일로 저장한다.
"""
import logging
from collections import Counter
from pathlib import Path

import matplotlib

matplotlib.use("Agg")  # 화면 없이 파일로만 저장 (CLI 환경)
import matplotlib.pyplot as plt  # noqa: E402
from matplotlib import font_manager  # noqa: E402
from matplotlib.ticker import MaxNLocator  # noqa: E402

logger = logging.getLogger(__name__)

# 운영체제별로 기본 설치된 한글 폰트 후보 (위에서부터 먼저 찾은 것을 사용)
KOREAN_FONTS = ["Malgun Gothic", "AppleGothic", "NanumGothic", "NanumBarunGothic",
                "Noto Sans CJK KR", "Noto Sans KR", "Noto Sans CJK JP", "UnDotum"]


def set_korean_font() -> str | None:
    installed = {f.name for f in font_manager.fontManager.ttflist}
    for name in KOREAN_FONTS:
        if name in installed:
            plt.rcParams["font.family"] = name
            plt.rcParams["axes.unicode_minus"] = False  # 마이너스 기호 깨짐 방지
            return name
    logger.warning("한글 폰트를 찾지 못했습니다. 차트의 한글이 깨질 수 있습니다 "
                   "(Windows: 맑은 고딕, Mac: AppleGothic, Linux: 나눔고딕 설치 권장).")
    return None


def _save(fig, path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    fig.tight_layout()
    fig.savefig(path, dpi=150)
    plt.close(fig)
    logger.info("차트 저장: %s", path)
    return path


def chart_category(articles: list[dict], path: Path) -> Path:
    counts = Counter(a["category"] for a in articles).most_common()
    labels = [c for c, _ in counts]
    values = [n for _, n in counts]

    fig, ax = plt.subplots(figsize=(8, 4.5))
    bars = ax.bar(labels, values, color="#4C72B0")
    ax.bar_label(bars, padding=2)
    ax.set_title("카테고리별 뉴스 수")
    ax.set_xlabel("카테고리")
    ax.set_ylabel("기사 수 (건)")
    ax.set_ylim(0, max(values) * 1.15)
    ax.yaxis.set_major_locator(MaxNLocator(integer=True))
    return _save(fig, path)


def chart_daily(articles: list[dict], path: Path) -> Path:
    counts = Counter(a["collected_at"][:10] for a in articles)
    days = sorted(counts)
    values = [counts[d] for d in days]

    fig, ax = plt.subplots(figsize=(8, 4.5))
    ax.plot(days, values, marker="o", color="#DD8452")
    for d, v in zip(days, values):
        ax.annotate(str(v), (d, v), textcoords="offset points", xytext=(0, 6), ha="center")
    ax.set_title("일자별 뉴스 수집 추이")
    ax.set_xlabel("수집일")
    ax.set_ylabel("수집 기사 수 (건)")
    ax.set_ylim(0, max(values) * 1.2)
    ax.yaxis.set_major_locator(MaxNLocator(integer=True))
    ax.grid(axis="y", alpha=0.3)
    fig.autofmt_xdate()
    return _save(fig, path)


def chart_sentiment(articles: list[dict], path: Path) -> Path | None:
    counts = Counter(a["sentiment"] for a in articles if a.get("sentiment"))
    if not counts:
        return None
    order = [s for s in ("긍정", "중립", "부정") if s in counts]
    colors = {"긍정": "#55A868", "중립": "#8C8C8C", "부정": "#C44E52"}

    fig, ax = plt.subplots(figsize=(6, 4.5))
    ax.pie([counts[s] for s in order], labels=order, autopct="%1.0f%%",
           colors=[colors[s] for s in order], startangle=90)
    ax.set_title(f"뉴스 감성 분포 (분석 {sum(counts.values())}건)")
    return _save(fig, path)


def make_charts(articles: list[dict], chart_dir: str, stamp: str) -> dict[str, Path]:
    """리포트에 들어갈 차트를 모두 만들고 {이름: 경로} 를 돌려준다."""
    set_korean_font()
    out = Path(chart_dir)
    charts = {
        "카테고리별 뉴스 수": chart_category(articles, out / f"category_{stamp}.png"),
        "일자별 수집 추이": chart_daily(articles, out / f"daily_{stamp}.png"),
    }
    sentiment = chart_sentiment(articles, out / f"sentiment_{stamp}.png")
    if sentiment:
        charts["감성 분포"] = sentiment
    return charts

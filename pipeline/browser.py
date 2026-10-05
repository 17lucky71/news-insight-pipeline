"""뉴스 조회 모듈 (보너스: list / show 명령).

- list : 조건 필터링(카테고리, 날짜, 키워드, 상태) + 페이지네이션
- show : 기사 1건 상세 조회 (본문, 요약, 감성 포함)
"""
import logging
import math
import textwrap

logger = logging.getLogger(__name__)


def _filters(args) -> dict:
    date_from, date_to = args.date_from, args.date_to
    if args.date:  # --date 하루만 지정
        date_from = date_to = args.date
    return dict(category=args.category, date_from=date_from, date_to=date_to,
                keyword=args.keyword, status=args.status)


def run_list(args, config: dict, storage) -> None:
    filters = _filters(args)
    total = storage.count_articles(**filters)
    if total == 0:
        logger.info("조건에 맞는 기사가 없습니다.")
        return

    pages = math.ceil(total / args.size)
    page = min(max(args.page, 1), pages)
    rows = storage.get_articles(**filters, limit=args.size, offset=(page - 1) * args.size)

    active = ", ".join(f"{k}={v}" for k, v in filters.items() if v) or "없음"
    print(f"\n[뉴스 목록] 총 {total}건 | {page}/{pages} 페이지 | 필터: {active}\n")
    print(f"{'ID':>4}  {'발행일시':<16}  {'카테고리':<4}  {'상태':<4}  {'감성':<2}  제목")
    print("-" * 90)
    for r in rows:
        status = "요약" if r["status"] == "summarized" else "정제"
        print(f"{r['id']:>4}  {r['published_at'][:16]:<16}  {r['category']:<4}  {status:<4}  "
              f"{r['sentiment'] or '-':<2}  {r['title'][:40]}")
    if page < pages:
        print(f"\n다음 페이지: python main.py list --page {page + 1} (같은 필터 옵션 유지)")


def run_show(args, config: dict, storage) -> None:
    a = storage.get_article(args.id)
    if a is None:
        logger.warning("ID=%d 기사가 없습니다. 'python main.py list' 로 ID 를 확인하세요.", args.id)
        return

    wrap = lambda text: "\n".join(textwrap.fill(p, 70) for p in text.splitlines())  # noqa: E731
    print(f"\n[{a['category']}] {a['title']}")
    print(f"ID {a['id']} | 발행 {a['published_at']} | 수집 {a['collected_at']} | 출처 {a['source']} ({a['method']})")
    print(f"URL  {a['url']}")
    print(f"상태 {a['status']} | 감성 {a['sentiment'] or '미분석'}")
    print("\n[AI 요약]")
    print(wrap(a["summary"]) if a["summary"] else "(아직 요약되지 않음: python main.py summarize --id "
          f"{a['id']})")
    print("\n[본문]")
    body = a["content"] if args.full or len(a["content"]) <= 600 else a["content"][:600] + " …(--full 로 전체 보기)"
    print(wrap(body))

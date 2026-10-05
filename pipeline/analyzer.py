"""AI 인사이트 분석 모듈 (analyze 명령).

조건(기간, 카테고리)에 맞는 뉴스를 모아 한 번에 AI 에 보내고,
주요 트렌드 / 핵심 키워드 / 공통점 / 차이점 / 시사점을 JSON 으로 받아 저장한다.
- 요약이 있는 기사는 요약문을, 없는 기사는 본문 앞부분을 사용해 토큰 사용량을 줄인다.
- 저장된 분석 결과는 analyze --show 로 다시 조회할 수 있고, report 에서도 사용한다.
"""
import logging
from datetime import datetime

from pipeline.ai_client import AIClient, AIError, parse_json
from pipeline.config import ConfigError

logger = logging.getLogger(__name__)

SECTIONS = [  # (JSON 키, 화면에 보일 제목)
    ("trends", "주요 트렌드"),
    ("keywords", "핵심 키워드"),
    ("commonalities", "공통점"),
    ("differences", "차이점"),
    ("implications", "시사점"),
]

SYSTEM_PROMPT = (
    "당신은 뉴스 트렌드를 분석하는 한국어 데이터 애널리스트입니다. "
    "제공된 기사 목록에 근거해서만 분석하고, 근거 없는 추측은 하지 않습니다."
)

ANALYSIS_PROMPT = """다음은 {condition} 조건으로 모은 뉴스 {count}건입니다.
기사들을 종합 분석해서 아래 JSON 형식으로만 답하세요. 설명 문장이나 코드블록 표시는 쓰지 마세요.

{{
  "trends": ["주요 트렌드 3~5개, 각각 한 문장"],
  "keywords": ["핵심 키워드 5~10개, 명사 위주"],
  "commonalities": ["기사들의 공통점 2~3개"],
  "differences": ["카테고리·관점별 차이점 2~3개"],
  "implications": ["시사점 2~3개, 각각 한두 문장"]
}}

[기사 목록]
{articles}"""


def _valid_date(value: str | None, name: str) -> str | None:
    if value is None:
        return None
    try:
        datetime.strptime(value, "%Y-%m-%d")
    except ValueError:
        raise ValueError(f"{name} 는 YYYY-MM-DD 형식이어야 합니다: {value}")
    return value


def build_article_block(articles: list[dict], max_chars: int) -> str:
    """기사들을 프롬프트용 텍스트로 만든다. 전체 길이가 max_chars 를 넘으면 거기서 멈춘다."""
    lines, total = [], 0
    for i, a in enumerate(articles, 1):
        text = a["summary"] or a["content"][:300]
        line = f"{i}. [{a['category']}] ({a['published_at'][:10]}) {a['title']}\n   {text}"
        if total + len(line) > max_chars:
            logger.warning("입력 길이 제한으로 %d건 중 %d건만 분석에 사용합니다.", len(articles), i - 1)
            break
        lines.append(line)
        total += len(line)
    return "\n".join(lines)


def format_result(analysis: dict) -> str:
    result = analysis["result"]
    cond = []
    if analysis.get("date_from") or analysis.get("date_to"):
        cond.append(f"기간 {analysis.get('date_from') or '처음'} ~ {analysis.get('date_to') or '현재'}")
    cond.append(f"카테고리 {analysis.get('category') or '전체'}")
    cond.append(f"기사 {analysis['article_count']}건")

    out = [f"=== AI 인사이트 분석 결과 (ID={analysis['id']}, {analysis['created_at']}) ===",
           "조건: " + ", ".join(cond)]
    for key, title in SECTIONS:
        items = result.get(key) or []
        out.append(f"\n[{title}]")
        if key == "keywords":
            out.append(", ".join(items) if items else "(없음)")
        else:
            out.extend(f"- {item}" for item in items) if items else out.append("(없음)")
    return "\n".join(out)


def show_analysis(storage, analysis_id: int) -> None:
    analysis = storage.get_analysis(analysis_id) if analysis_id else storage.get_latest_analysis()
    if analysis is None:
        logger.warning("저장된 분석 결과가 없습니다." if not analysis_id
                       else f"ID={analysis_id} 분석 결과가 없습니다.")
        return
    print(format_result(analysis))


def run_analyze(args, config: dict, storage) -> None:
    if args.list:
        rows = storage.list_analyses()
        if not rows:
            logger.info("저장된 분석 결과가 없습니다.")
        for r in rows:
            print(f"ID={r['id']:<3} {r['created_at']}  기간={r['date_from'] or '-'}~{r['date_to'] or '-'}"
                  f"  카테고리={r['category'] or '전체'}  기사={r['article_count']}건")
        return
    if args.show is not None:
        show_analysis(storage, args.show)
        return

    try:
        date_from = _valid_date(args.date_from, "--date-from")
        date_to = _valid_date(args.date_to, "--date-to")
    except ValueError as e:
        logger.error("%s", e)
        return

    articles = storage.get_articles(category=args.category, date_from=date_from, date_to=date_to)
    articles = articles[:args.max_articles]
    if len(articles) < 2:
        logger.warning("분석할 기사가 %d건뿐입니다. 조건을 넓히거나 fetch/clean 을 먼저 실행하세요.",
                       len(articles))
        return

    summarized = sum(1 for a in articles if a["summary"])
    logger.info("분석 대상: %d건 (요약 있음 %d건, 본문 사용 %d건)",
                len(articles), summarized, len(articles) - summarized)

    try:
        client = AIClient(config)
    except (ConfigError, AIError) as e:
        logger.error("AI 클라이언트를 만들 수 없습니다: %s", e)
        return

    condition = ", ".join(filter(None, [
        f"기간 {date_from or '처음'}~{date_to or '현재'}" if (date_from or date_to) else "",
        f"카테고리 '{args.category}'" if args.category else "전체 카테고리",
    ]))
    max_chars = config.get("ai", {}).get("max_analysis_chars", 30000)
    prompt = ANALYSIS_PROMPT.format(condition=condition, count=len(articles),
                                    articles=build_article_block(articles, max_chars))

    logger.info("AI 분석 요청 중... (모델: %s)", client.model)
    try:
        result = parse_json(client.generate(prompt, system=SYSTEM_PROMPT, json_mode=True, max_tokens=8192))
    except AIError as e:
        logger.error("AI 분석 실패: %s", e)
        return

    missing = [title for key, title in SECTIONS if not result.get(key)]
    if missing:
        logger.warning("AI 응답에 일부 항목이 비어 있습니다: %s", ", ".join(missing))

    analysis_id = storage.save_analysis(result, len(articles), client.model,
                                        date_from=date_from, date_to=date_to, category=args.category)
    logger.info("분석 완료 (분석 ID=%d 로 저장됨)", analysis_id)
    print()
    print(format_result(storage.get_analysis(analysis_id)))

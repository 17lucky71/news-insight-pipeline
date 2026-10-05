"""AI 뉴스 요약 모듈 (summarize 명령).

- 대상 선택: --all(전체) / --id(특정 ID) / --unsummarized(요약 안 된 것만)
- 이미 요약된 뉴스는 기본적으로 스킵 (--force 로 재요약)
- API 호출이 실패한 기사는 로그를 남기고 건너뛴 뒤 다음 기사를 계속 처리
"""
import logging

from pipeline.ai_client import AIClient, AIError, AIQuotaError
from pipeline.config import ConfigError

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = (
    "당신은 한국어 뉴스 편집자입니다. 기사 내용에 있는 사실만 사용하고 추측이나 의견을 덧붙이지 않습니다."
)

SUMMARY_PROMPT = """다음 뉴스 기사를 {sentences}문장 이내의 한국어로 요약하세요.
- 누가, 무엇을, 왜/어떻게 했는지 핵심 사실 위주로 작성
- 기사에 없는 내용은 쓰지 말 것
- 머리말 없이 요약문만 출력

[제목] {title}
[본문]
{content}"""


def select_targets(args, storage) -> list[dict]:
    if args.id:
        articles = storage.get_articles(ids=args.id)
        missing = set(args.id) - {a["id"] for a in articles}
        for mid in sorted(missing):
            logger.warning("ID=%d 기사가 없습니다.", mid)
    elif args.unsummarized:
        articles = storage.get_articles(status="cleaned")
    else:  # --all
        articles = storage.get_articles()

    articles.sort(key=lambda a: a["id"])  # 오래된 ID 부터 처리
    if not args.force:
        done = [a for a in articles if a["status"] == "summarized"]
        if done:
            logger.info("이미 요약된 %d건은 건너뜁니다 (다시 요약하려면 --force).", len(done))
        articles = [a for a in articles if a["status"] != "summarized"]
    if args.limit:
        articles = articles[:args.limit]
    return articles


def summarize_article(client: AIClient, article: dict, max_chars: int, sentences: int) -> str:
    content = article["content"][:max_chars]  # 너무 긴 본문은 잘라서 토큰 사용량 제한
    prompt = SUMMARY_PROMPT.format(sentences=sentences, title=article["title"], content=content)
    return client.generate(prompt, system=SYSTEM_PROMPT, max_tokens=1024)


def run_summarize(args, config: dict, storage) -> None:
    targets = select_targets(args, storage)
    if not targets:
        logger.info("요약할 뉴스가 없습니다.")
        return

    try:
        client = AIClient(config)
    except (ConfigError, AIError) as e:
        logger.error("AI 클라이언트를 만들 수 없습니다: %s", e)
        return

    max_chars = config.get("ai", {}).get("max_input_chars", 4000)
    logger.info("요약 대상: %d건 (모델: %s)", len(targets), client.model)

    ok = fail = 0
    for i, article in enumerate(targets, 1):
        try:
            summary = summarize_article(client, article, max_chars, args.sentences)
        except AIQuotaError as e:
            logger.error("%s", e)
            logger.warning("남은 %d건은 한도가 초기화된 뒤 'summarize --unsummarized' 로 이어서 요약하세요.",
                           len(targets) - i + 1)
            fail += len(targets) - i + 1
            break
        except AIError as e:
            fail += 1
            logger.error("[%d/%d] ID=%d 요약 실패, 건너뜀: %s", i, len(targets), article["id"], e)
            continue
        storage.update_summary(article["id"], summary)
        ok += 1
        logger.info("[%d/%d] ID=%d 요약 완료 (%d자 → %d자)",
                    i, len(targets), article["id"], len(article["content"]), len(summary))

    logger.info("요약 완료: %d건 성공, %d건 실패", ok, fail)

"""감성 분석 모듈 (보너스: sentiment 명령).

기사 여러 건을 한 번의 요청에 묶어(batch) 보내 긍정/부정/중립을 판정한다.
→ 무료 요금제의 요청 횟수 한도를 아끼기 위해 기사 1건당 1회가 아니라 N건당 1회 호출.
결과는 articles.sentiment 에 저장되고, report 의 감성 분포 차트에 사용된다.
"""
import logging

from pipeline.ai_client import AIClient, AIError, AIQuotaError, parse_json
from pipeline.config import ConfigError

logger = logging.getLogger(__name__)

LABELS = ("긍정", "부정", "중립")

SYSTEM_PROMPT = "당신은 한국어 뉴스의 논조를 판정하는 분석가입니다."

PROMPT = """다음 뉴스 각각의 전반적인 감성을 "긍정", "부정", "중립" 중 하나로 판정하세요.
- 긍정: 성과, 성장, 개선, 축하 등 좋은 소식
- 부정: 사건·사고, 갈등, 손실, 위기 등 나쁜 소식
- 중립: 단순 사실 전달, 일정 안내 등
아래 JSON 형식으로만 답하세요.
{{"results": [{{"id": 기사ID, "sentiment": "긍정|부정|중립"}}]}}

[뉴스]
{articles}"""


def classify_batch(client: AIClient, batch: list[dict]) -> dict[int, str]:
    lines = [f"ID={a['id']} | {a['title']} | {(a['summary'] or a['content'])[:200]}" for a in batch]
    data = parse_json(client.generate(PROMPT.format(articles="\n".join(lines)),
                                      system=SYSTEM_PROMPT, json_mode=True, max_tokens=4096))
    result = {}
    for item in data.get("results", []):
        try:
            aid, label = int(item["id"]), str(item["sentiment"]).strip()
        except (KeyError, TypeError, ValueError):
            continue
        if label in LABELS:
            result[aid] = label
    return result


def run_sentiment(args, config: dict, storage) -> None:
    if args.id:
        targets = storage.get_articles(ids=args.id)
    else:
        targets = storage.get_articles()
        if not args.all:
            targets = [a for a in targets if not a["sentiment"]]
    targets.sort(key=lambda a: a["id"])
    if args.limit:
        targets = targets[:args.limit]
    if not targets:
        logger.info("감성 분석할 기사가 없습니다.")
        return

    try:
        client = AIClient(config)
    except (ConfigError, AIError) as e:
        logger.error("AI 클라이언트를 만들 수 없습니다: %s", e)
        return

    batches = [targets[i:i + args.batch] for i in range(0, len(targets), args.batch)]
    logger.info("감성 분석 대상: %d건 (%d건씩 %d회 요청, 모델: %s)",
                len(targets), args.batch, len(batches), client.model)

    done = 0
    for n, batch in enumerate(batches, 1):
        try:
            labels = classify_batch(client, batch)
        except AIQuotaError as e:
            logger.error("%s - 남은 기사는 나중에 'sentiment' 로 이어서 분석하세요.", e)
            break
        except AIError as e:
            logger.error("[%d/%d] 묶음 분석 실패, 건너뜀: %s", n, len(batches), e)
            continue
        for aid, label in labels.items():
            storage.update_sentiment(aid, label)
        done += len(labels)
        missing = len(batch) - len(labels)
        logger.info("[%d/%d] %d건 판정 완료%s", n, len(batches), len(labels),
                    f" ({missing}건 응답 누락)" if missing else "")

    counts = storage.sentiment_counts()
    logger.info("감성 분석 완료: 이번 %d건 | 누적 분포 %s", done,
                ", ".join(f"{k} {v}건" for k, v in counts.items()) or "-")

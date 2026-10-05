"""AI 뉴스 트렌드 분석 CLI 진입점.

사용 예:
    python main.py fetch --limit 20
    python main.py clean
    python main.py summarize --unsummarized --limit 10
    python main.py analyze --date-from 2026-10-01 --date-to 2026-10-05 --category IT
    python main.py report --top 5
    python main.py export --format csv --status summarized
"""
import argparse
import logging
import sys

from pipeline.analyzer import run_analyze
from pipeline.cleaner import run_clean
from pipeline.collector import run_fetch
from pipeline.config import ConfigError, load_config
from pipeline.logger import setup_logging
from pipeline.storage import Storage
from pipeline.summarizer import run_summarize

logger = logging.getLogger("main")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="main.py", description="AI 뉴스 트렌드 및 종합 분석 리포트 CLI"
    )
    parser.add_argument("--config", default="config.json", help="설정 파일 경로")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("fetch", help="뉴스 수집 (RSS + 크롤링) → raw 저장")
    p.add_argument("--source", help="설정에 등록된 소스 이름 (기본: default_source)")
    p.add_argument("--category", help="수집할 카테고리")
    p.add_argument("--limit", type=int, default=20, help="수집 최대 건수")
    p.add_argument("--no-crawl", action="store_true", help="RSS 만 수집하고 본문 크롤링은 생략")

    p = sub.add_parser("clean", help="raw 데이터 정제 → clean 저장")
    p.add_argument("--policy", choices=["skip", "upsert"],
                   help="중복 처리 정책 (기본: config.json 의 duplicate_policy)")
    p.add_argument("--reprocess", action="store_true",
                   help="이미 처리한 raw 까지 전부 다시 정제 (정제 규칙 변경 후 사용)")

    p = sub.add_parser("summarize", help="AI 뉴스 요약")
    target = p.add_mutually_exclusive_group(required=True)
    target.add_argument("--all", action="store_true", help="전체 뉴스 (기존 요약은 스킵)")
    target.add_argument("--id", type=int, nargs="+", help="특정 뉴스 ID")
    target.add_argument("--unsummarized", action="store_true", help="요약 안 된 뉴스만")
    p.add_argument("--force", action="store_true", help="이미 요약된 뉴스도 다시 요약")
    p.add_argument("--limit", type=int, help="최대 처리 건수")
    p.add_argument("--sentences", type=int, default=3, help="요약 문장 수 (기본 3)")

    p = sub.add_parser("analyze", help="AI 인사이트 분석")
    p.add_argument("--date-from", help="시작일 YYYY-MM-DD")
    p.add_argument("--date-to", help="종료일 YYYY-MM-DD")
    p.add_argument("--category", help="카테고리")
    p.add_argument("--max-articles", type=int, default=50, help="분석에 사용할 최대 기사 수 (기본 50)")
    p.add_argument("--show", type=int, nargs="?", const=0, metavar="ID",
                   help="저장된 분석 결과 조회 (ID 생략 시 최신)")
    p.add_argument("--list", action="store_true", help="저장된 분석 목록 보기")

    p = sub.add_parser("report", help="시각화 + 리포트 생성")
    p.add_argument("--top", type=int, default=5, help="TOP N 개수")
    p.add_argument("--format", choices=["md", "txt"], default="md", help="저장 형식")

    p = sub.add_parser("export", help="데이터 내보내기")
    p.add_argument("--format", choices=["csv", "excel", "jsonl"], default="csv")
    p.add_argument("--status", choices=["cleaned", "summarized"], help="상태 필터")
    p.add_argument("--category", help="카테고리 필터")

    return parser


def not_implemented(args, config, storage):
    logger.warning("'%s' 명령은 아직 구현 전입니다.", args.command)


COMMANDS = {
    "fetch": run_fetch,
    "clean": run_clean,
    "summarize": run_summarize,
    "analyze": run_analyze,
    "report": not_implemented,
    "export": not_implemented,
}


def main() -> int:
    args = build_parser().parse_args()

    try:
        config = load_config(args.config)
    except ConfigError as e:
        print(f"[ERROR] {e}", file=sys.stderr)
        return 1

    log_cfg = config.get("logging", {})
    setup_logging(log_cfg.get("level", "INFO"), log_cfg.get("file", "logs/app.log"))

    storage = Storage(config["database"]["path"])
    try:
        COMMANDS[args.command](args, config, storage)
    except KeyboardInterrupt:
        logger.warning("사용자에 의해 중단되었습니다.")
        return 130
    except Exception:
        logger.exception("처리 중 예기치 못한 오류가 발생했습니다.")
        return 1
    finally:
        storage.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())

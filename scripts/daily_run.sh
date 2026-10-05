#!/bin/bash
# 매일 정기 실행용 스크립트 (Linux/macOS cron 에 등록)
# 수집 -> 정제 -> 요약 -> 감성 분석 -> 리포트 순서로 실행한다.
cd "$(dirname "$0")/.." || exit 1
source venv/bin/activate
python main.py fetch --limit 30
python main.py clean
python main.py summarize --unsummarized --limit 20
python main.py sentiment --limit 30
python main.py report --top 5

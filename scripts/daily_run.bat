@echo off
REM 매일 정기 실행용 배치 파일 (Windows 작업 스케줄러에 등록)
REM 수집 -> 정제 -> 요약 -> 감성 분석 -> 리포트 순서로 실행한다.
cd /d "%~dp0.."
call venv\Scripts\activate.bat
python main.py fetch --limit 30
python main.py clean
python main.py summarize --unsummarized --limit 20
python main.py sentiment --limit 30
python main.py report --top 5

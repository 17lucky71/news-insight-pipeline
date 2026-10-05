"""가장 최근 리포트와 차트를 docs/ 폴더로 복사한다 (README 에 실행 결과를 첨부하기 위함).

사용법: python scripts/update_docs.py   (먼저 python main.py report 를 실행해 둘 것)
"""
import re
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
REPORTS, CHARTS, DOCS = ROOT / "output/reports", ROOT / "output/charts", ROOT / "docs"


def latest(folder: Path, pattern: str) -> Path | None:
    files = sorted(folder.glob(pattern), key=lambda p: p.stat().st_mtime)
    return files[-1] if files else None


def main() -> int:
    report = latest(REPORTS, "report_*.md")
    if report is None:
        print("output/reports 에 MD 리포트가 없습니다. 먼저 'python main.py report' 를 실행하세요.")
        return 1
    DOCS.mkdir(exist_ok=True)

    text = report.read_text(encoding="utf-8")
    for name in ("category", "daily", "sentiment"):
        chart = latest(CHARTS, f"{name}_*.png")
        if chart:
            shutil.copy(chart, DOCS / f"{name}.png")
            print(f"복사: {chart.name} → docs/{name}.png")
        # 리포트 안의 차트 경로를 docs 기준으로 변경
        text = re.sub(rf"\]\([^)]*{name}_\d+_\d+\.png\)", f"]({name}.png)", text)

    (DOCS / "sample_report.md").write_text(text, encoding="utf-8")
    print(f"복사: {report.name} → docs/sample_report.md")
    return 0


if __name__ == "__main__":
    sys.exit(main())

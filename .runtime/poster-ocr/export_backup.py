"""백업 저장소(github.com/AhnJung-min/final_data_backup)용 내보내기 폴더를 만들고 비밀값을 검사한다.

넣는 것: Git이 관리하는 파일과 무시되지 않은 새 파일(작업 트리 그대로), 포스터 원본(data/img-v2, 변경 없는 원본 바이트),
OCR·추출·평가 결과(data/runtime/poster-ocr), 수집 실행의 JSON(data/runtime/festivals/*/ 최상위 파일), 실험 작업 기록(.runtime/poster-ocr 텍스트).
빼는 것: .env, 가상환경, 모델 가중치, 캐시, 로그, 업로드 파일, 변형·잘라낸 포스터 이미지(공공누리 제3유형 변경금지), 이 내보내기 폴더 자신.
  .venv/Scripts/python.exe .runtime/poster-ocr/export_backup.py <내보낼 폴더>
"""
from __future__ import annotations

import os
from pathlib import Path
import re
import shutil
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[2]
IMAGE = {".png", ".jpg", ".jpeg", ".webp", ".bmp", ".gif", ".tif", ".tiff"}
SKIP_PARTS = {"__pycache__", ".pytest_cache", "node_modules", "cache", "images", "inputs", "hybrid-crops", "hybrid-crops-reserve", "uploads"}
SKIP_SUFFIX = {".log", ".pid", ".pyc"}


def tracked():
    out = subprocess.run(["git", "ls-files", "-co", "--exclude-standard", "-z"], cwd=ROOT, capture_output=True, check=True).stdout
    return [p for p in out.decode("utf-8").split("\0") if p]


def walk(rel, images_ok=False, top_only=False):
    base = ROOT / rel
    if not base.exists():
        return []
    paths = base.glob("*") if top_only else base.rglob("*")
    out = []
    for path in paths:
        if not path.is_file():
            continue
        parts = set(path.relative_to(ROOT).parts)
        if parts & SKIP_PARTS or path.suffix.lower() in SKIP_SUFFIX:
            continue
        if path.suffix.lower() in IMAGE and not images_ok:
            continue
        out.append(path.relative_to(ROOT).as_posix())
    return out


def work_records():
    """.runtime/poster-ocr의 작업 기록: 스크립트·제안 diff·문서·요약 JSON만(후보 예측 수만 개와 이미지는 뺀다)."""
    base = ROOT / ".runtime/poster-ocr"
    skip = {"pred", "zoom", "pilot-crops", "inputs", "hybrid-crops", "hybrid-crops-reserve", "backup-export", "verify-summary", "__pycache__"}
    keep_suffix = {".py", ".md", ".diff", ".sh", ".txt", ".json", ".html"}
    out = []
    for dirpath, dirnames, filenames in os.walk(base):
        dirnames[:] = [d for d in dirnames if d not in skip]
        for name in filenames:
            path = Path(dirpath) / name
            if path.suffix.lower() not in keep_suffix or path.stat().st_size > 3 * 2**20:
                continue
            if path.relative_to(base).parts[0].startswith("proposals") and name.endswith("-summary.json"):
                continue  # 규칙 후보별 채점 요약(행 전체 포함, 개당 최대 2MB)은 다시 만들 수 있어 뺀다
            out.append(path.relative_to(ROOT).as_posix())
    return out


def main():
    out = Path(sys.argv[1]).resolve()
    files = set(tracked())
    files |= set(walk("data/img-v2", images_ok=True))
    files |= set(walk("data/runtime/poster-ocr"))
    for run in sorted((ROOT / "data/runtime/festivals").glob("*/")):
        files |= set(walk(run.relative_to(ROOT).as_posix(), top_only=True))
    files |= set(work_records())
    env_like = lambda f: Path(f).name.startswith(".env") and not Path(f).name.endswith((".example", ".sample", ".template"))
    files = {f for f in files if not f.startswith(".runtime/poster-ocr/backup-export") and not env_like(f)}
    # Git이 관리하는 파일은 이미지라도 그대로 둔다(팀 저장소에 이미 공개된 파일). 그 밖의 이미지는 포스터 원본만.
    git_files = set(tracked())
    files = {f for f in files if Path(f).suffix.lower() not in IMAGE or f in git_files or f.startswith("data/img-v2/")}
    # .env는 읽지 않는다. 키가 들어가는 형태(URL 쿼리, 설정 대입)만 찾는다.
    pattern = re.compile(r"(?i)(?:service_?key|api_?key|access_?token|secret)[\"']?\s*[=:]\s*[\"']?([A-Za-z0-9%+/=_\-]{20,})")
    leaks = []
    total = 0
    for rel in sorted(files):
        src = ROOT / rel
        if not src.is_file():
            continue
        data = src.read_bytes()
        text = data.decode("utf-8", errors="ignore")
        if pattern.search(text):
            leaks.append(rel)
            continue
        dst = out / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)
        total += len(data)
    # 백업 저장소 전용 문서: 루트 README(팀 브랜치의 옛 README 대신), 설치 안내, Claude Code 맥락, .gitignore, .gitattributes
    docs = ROOT / ".runtime/poster-ocr/backup-docs"
    for name, target in (("README.md", "README.md"), ("README_BACKUP.md", "README_BACKUP.md"), ("CLAUDE.md", "CLAUDE.md"),
                         ("gitignore.backup", ".gitignore")):
        if (docs / name).exists():
            shutil.copy2(docs / name, out / target)
    (out / ".gitattributes").write_text("# 파일 바이트를 그대로 보관한다(FREEZE와 SHA256SUMS 해시가 다른 PC에서도 맞도록 줄바꿈 변환 끔)\n* -text\n", encoding="utf-8", newline="\n")
    (out / ".env.example").unlink(missing_ok=True)  # 키 이름만 있는 예시도 넣지 않는다(설치 안내에 변수 이름을 적음)
    shutil.rmtree(out / ".runtime/poster-ocr/backup-docs", ignore_errors=True)
    big = sorted(((ROOT / f).stat().st_size, f) for f in files if (ROOT / f).is_file() and (ROOT / f).stat().st_size > 50 * 2**20)
    print(f"copied files: {len(files) - len(leaks)}, bytes: {total / 2**20:.1f} MiB")
    print(f"skipped for secret match: {len(leaks)}")
    for rel in leaks:
        print("  SECRET?", rel)
    for size, rel in big:
        print(f"  LARGE {size / 2**20:.1f} MiB {rel}")


if __name__ == "__main__":
    main()

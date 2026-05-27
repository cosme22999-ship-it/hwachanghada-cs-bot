"""
SQLite DB 자동 백업/복원 — HF Dataset 저장소 사용

- 시작 시: 백업 저장소에서 DB 받아와서 복원
- 주기적: 변경 감지되면 DB push (5분 간격)
- 환경변수 HF_TOKEN 필요 (없으면 백업 비활성)
"""

from __future__ import annotations

import os
import shutil
import threading
import time
from datetime import datetime
from pathlib import Path


BACKUP_REPO = os.environ.get("CSBOT_BACKUP_REPO", "mmmaadfdf/cs-bot-data")
BACKUP_FILE = "cs_bot.db"
HF_TOKEN = os.environ.get("HF_TOKEN", "")
BACKUP_INTERVAL_SEC = int(os.environ.get("CSBOT_BACKUP_INTERVAL", "300"))  # 5분

_last_mtime = 0.0
_last_backup_at = 0.0
_lock = threading.Lock()


def _api():
    from huggingface_hub import HfApi
    return HfApi(token=HF_TOKEN)


def restore_db(local_db: Path) -> bool:
    """백업 저장소에서 DB 받아오기. 처음 실행이면 백업이 없어 실패하는 게 정상."""
    if not HF_TOKEN:
        print("[backup] HF_TOKEN 없음 — 백업 비활성")
        return False
    if local_db.exists() and local_db.stat().st_size > 1024:
        print(f"[backup] 로컬 DB 이미 있음 ({local_db.stat().st_size} bytes) — 복원 스킵")
        return False
    try:
        from huggingface_hub import hf_hub_download
        path = hf_hub_download(
            repo_id=BACKUP_REPO,
            filename=BACKUP_FILE,
            repo_type="dataset",
            token=HF_TOKEN,
        )
        local_db.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy(path, local_db)
        size = local_db.stat().st_size
        print(f"[backup] DB 복원 완료: {size} bytes")
        return True
    except Exception as e:
        # 백업 파일이 아직 없으면 EntryNotFoundError 등 — 첫 실행이면 정상
        msg = str(e)
        if "EntryNotFoundError" in msg or "404" in msg or "Entry Not Found" in msg:
            print("[backup] 백업 파일 없음 (첫 실행) — 새로 시작")
        else:
            print(f"[backup] 복원 실패: {e}")
        return False


def backup_db(local_db: Path, force: bool = False) -> bool:
    """변경 있으면 백업 저장소에 push. force=True면 변경 무관 push."""
    global _last_mtime, _last_backup_at
    if not HF_TOKEN or not local_db.exists():
        return False
    try:
        mtime = local_db.stat().st_mtime
        if not force and mtime <= _last_mtime:
            return False  # 변경 없음
        with _lock:
            api = _api()
            api.upload_file(
                path_or_fileobj=str(local_db),
                path_in_repo=BACKUP_FILE,
                repo_id=BACKUP_REPO,
                repo_type="dataset",
                commit_message=f"Auto-backup {datetime.now().isoformat(timespec='seconds')}",
            )
            _last_mtime = mtime
            _last_backup_at = time.time()
        print(f"[backup] DB 백업 완료 ({local_db.stat().st_size} bytes)")
        return True
    except Exception as e:
        print(f"[backup] 백업 실패: {e}")
        return False


def start_periodic_backup(local_db: Path, interval: int = BACKUP_INTERVAL_SEC):
    """백그라운드 스레드로 주기적 백업"""
    if not HF_TOKEN:
        return

    def loop():
        while True:
            time.sleep(interval)
            try:
                backup_db(local_db)
            except Exception as e:
                print(f"[backup] 루프 에러: {e}")

    t = threading.Thread(target=loop, daemon=True, name="db-backup")
    t.start()
    print(f"[backup] 주기적 백업 시작 ({interval}초 간격)")

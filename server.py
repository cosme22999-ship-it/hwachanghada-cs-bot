"""
화창하다 CS봇 - FastAPI 서버 (클라우드 배포 호환)
"""

from __future__ import annotations

import os
import secrets
import sqlite3
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from threading import Lock

from fastapi import Depends, FastAPI, HTTPException, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from bot_core import CSBot


HERE = Path(__file__).parent.resolve()
STATIC_DIR = HERE / "static"
DATA_DIR = HERE / "data"

# ===== 경로 설정 =====
DEFAULT_VERIFIED_CLOUD = DATA_DIR / "verified_faq.json"
DEFAULT_VERIFIED_LOCAL = Path(r"C:\Users\깡대표\OneDrive\문서\CS봇_검증FAQ.json")

# 클라우드: data/verified_faq.json (git에 포함)
# 로컬: 기존 OneDrive 경로 폴백
if DEFAULT_VERIFIED_CLOUD.exists():
    DEFAULT_VERIFIED = DEFAULT_VERIFIED_CLOUD
else:
    DEFAULT_VERIFIED = DEFAULT_VERIFIED_LOCAL

VERIFIED_PATH = Path(os.environ.get("CSBOT_VERIFIED", DEFAULT_VERIFIED))

# 카톡 폴백은 클라우드 메모리 절약 위해 기본 OFF (환경변수로만 켬)
KAKAO_PATH_ENV = os.environ.get("CSBOT_KAKAO", "").strip()
KAKAO_PATH = Path(KAKAO_PATH_ENV) if KAKAO_PATH_ENV else None

# 로그 DB (클라우드 ephemeral, 로컬 vault 자동 미러)
DB_PATH = Path(os.environ.get("CSBOT_DB", str(HERE / "data" / "cs_bot.db")))

# 관리자 비밀번호 (환경변수)
ADMIN_PASSWORD = os.environ.get("CSBOT_ADMIN_PASSWORD", "")

# 저확신 임계값 (이 미만이면 로그됨)
LOG_LOW_CONFIDENCE_THRESHOLD = float(os.environ.get("CSBOT_LOG_THRESHOLD", "65.0"))

_log_lock = Lock()


# ===== 검증 =====
print(f"[server] 검증 FAQ: {VERIFIED_PATH}")
if not VERIFIED_PATH.exists():
    raise FileNotFoundError(
        f"검증 FAQ JSON 없음: {VERIFIED_PATH}\n"
        f"  → build_data.py를 먼저 실행하세요."
    )

print(f"[server] DB: {DB_PATH}")
DB_PATH.parent.mkdir(parents=True, exist_ok=True)


# ===== DB 초기화 =====
def init_db():
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS unmatched_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                created_at TEXT NOT NULL,
                question TEXT NOT NULL,
                status TEXT NOT NULL,
                confidence REAL,
                matched_id TEXT,
                matched_question TEXT,
                top_alt_id TEXT,
                top_alt_question TEXT,
                top_alt_confidence REAL
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS query_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                created_at TEXT NOT NULL,
                question TEXT NOT NULL,
                status TEXT NOT NULL,
                confidence REAL,
                matched_id TEXT
            )
        """)
        conn.execute("CREATE INDEX IF NOT EXISTS idx_unmatched_created ON unmatched_log(created_at DESC)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_query_created ON query_log(created_at DESC)")


init_db()


@contextmanager
def db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


# ===== 봇 초기화 =====
bot = CSBot(
    verified_path=VERIFIED_PATH,
    kakao_path=KAKAO_PATH if (KAKAO_PATH and KAKAO_PATH.exists()) else None,
)


# ===== FastAPI =====
app = FastAPI(title="화창하다 CS봇", version="2.0.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


class AskRequest(BaseModel):
    question: str
    verified_threshold: float = 0.50
    kakao_threshold: float = 0.65


def log_query(question: str, result: dict) -> None:
    """모든 질문을 query_log에 기록. 미매칭/저확신은 unmatched_log에도."""
    status = result.get("status", "")
    confidence = float(result.get("confidence", 0) or 0)
    matched_id = result.get("matched_id") or ""
    matched_q = result.get("matched_question") or ""
    now = datetime.now().isoformat(timespec="seconds")

    should_log_unmatched = status == "no_match" or (
        status == "found_verified" and confidence < LOG_LOW_CONFIDENCE_THRESHOLD
    )

    alts = result.get("alternatives") or []
    top_alt = alts[0] if alts else {}

    try:
        with _log_lock, db() as conn:
            conn.execute(
                "INSERT INTO query_log (created_at, question, status, confidence, matched_id) VALUES (?, ?, ?, ?, ?)",
                (now, question, status, confidence, matched_id),
            )
            if should_log_unmatched:
                conn.execute(
                    """INSERT INTO unmatched_log
                       (created_at, question, status, confidence,
                        matched_id, matched_question,
                        top_alt_id, top_alt_question, top_alt_confidence)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        now, question, status, confidence,
                        matched_id, matched_q,
                        top_alt.get("id", ""), top_alt.get("question", ""),
                        float(top_alt.get("confidence", 0) or 0),
                    ),
                )
    except Exception as e:
        print(f"[log] 기록 실패: {e}")


@app.post("/api/ask")
def ask(req: AskRequest):
    q = (req.question or "").strip()
    if not q:
        raise HTTPException(status_code=400, detail="question is empty")
    result = bot.answer(
        q,
        verified_threshold=req.verified_threshold,
        kakao_threshold=req.kakao_threshold,
    )
    log_query(q, result)
    return result


@app.get("/api/stats")
def stats():
    s = bot.stats()
    try:
        with db() as conn:
            s["total_queries"] = conn.execute("SELECT COUNT(*) FROM query_log").fetchone()[0]
            s["total_unmatched"] = conn.execute("SELECT COUNT(*) FROM unmatched_log").fetchone()[0]
    except Exception:
        s["total_queries"] = 0
        s["total_unmatched"] = 0
    return s


@app.get("/api/health")
def health():
    return {"ok": True}


# ===== 관리자 페이지 =====
security = HTTPBasic()


def require_admin(credentials: HTTPBasicCredentials = Depends(security)):
    if not ADMIN_PASSWORD:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="ADMIN_PASSWORD env var not configured",
        )
    ok_user = secrets.compare_digest(credentials.username, "admin")
    ok_pw = secrets.compare_digest(credentials.password, ADMIN_PASSWORD)
    if not (ok_user and ok_pw):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Unauthorized",
            headers={"WWW-Authenticate": "Basic"},
        )
    return credentials.username


@app.get("/admin", response_class=HTMLResponse)
def admin_page(_: str = Depends(require_admin)):
    with db() as conn:
        unmatched = conn.execute(
            "SELECT * FROM unmatched_log ORDER BY created_at DESC LIMIT 200"
        ).fetchall()
        total_q = conn.execute("SELECT COUNT(*) FROM query_log").fetchone()[0]
        total_u = conn.execute("SELECT COUNT(*) FROM unmatched_log").fetchone()[0]
        recent_status = conn.execute(
            "SELECT status, COUNT(*) c FROM query_log GROUP BY status ORDER BY c DESC"
        ).fetchall()
        top_unmatched = conn.execute(
            "SELECT question, COUNT(*) c FROM unmatched_log GROUP BY question ORDER BY c DESC LIMIT 20"
        ).fetchall()

    rows_html = []
    for r in unmatched:
        badge = "🔴" if r["status"] == "no_match" else "🟡"
        alt = ""
        if r["top_alt_question"]:
            alt = f"<div class='alt'>가장 가까운 FAQ: <b>[{r['top_alt_id']}]</b> {escape(r['top_alt_question'])} ({r['top_alt_confidence']:.1f}%)</div>"
        rows_html.append(f"""
        <tr>
          <td class='ts'>{r['created_at']}</td>
          <td>{badge} {r['status']} {f"({r['confidence']:.1f}%)" if r['confidence'] else ""}</td>
          <td>
            <div class='q'>{escape(r['question'])}</div>
            {alt}
          </td>
        </tr>""")

    status_rows = "".join(
        f"<li><b>{escape(r['status'])}</b>: {r['c']:,}회</li>"
        for r in recent_status
    )
    top_rows = "".join(
        f"<li>{escape(r['question'])} <span class='cnt'>({r['c']}회)</span></li>"
        for r in top_unmatched
    )

    return f"""<!DOCTYPE html>
<html lang="ko"><head><meta charset="UTF-8"><title>CS봇 관리자</title>
<style>
body {{ font-family: -apple-system, "Malgun Gothic", sans-serif; margin: 0; padding: 24px; background: #f6f7fb; color: #1f2937; }}
h1 {{ margin: 0 0 8px; font-size: 22px; }}
.sub {{ color: #6b7280; margin-bottom: 24px; font-size: 13px; }}
.cards {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(220px, 1fr)); gap: 12px; margin-bottom: 24px; }}
.card {{ background: white; border-radius: 12px; padding: 16px; box-shadow: 0 1px 3px rgba(0,0,0,0.04); }}
.card h2 {{ margin: 0 0 8px; font-size: 14px; color: #6b7280; font-weight: 600; }}
.card .big {{ font-size: 28px; font-weight: 700; color: #ff6b9d; }}
.card ul {{ margin: 0; padding-left: 18px; font-size: 13px; }}
.card li {{ margin: 4px 0; }}
.cnt {{ color: #6b7280; font-size: 12px; }}
table {{ width: 100%; background: white; border-collapse: collapse; border-radius: 12px; overflow: hidden; box-shadow: 0 1px 3px rgba(0,0,0,0.04); }}
th, td {{ padding: 10px 14px; text-align: left; border-bottom: 1px solid #f0f1f5; font-size: 13px; vertical-align: top; }}
th {{ background: #fafbff; font-weight: 600; color: #6b7280; }}
.ts {{ white-space: nowrap; color: #6b7280; font-size: 12px; font-family: monospace; }}
.q {{ font-weight: 500; }}
.alt {{ color: #6b7280; font-size: 12px; margin-top: 4px; }}
.actions {{ margin-bottom: 16px; }}
.btn {{ display: inline-block; padding: 6px 12px; background: white; border: 1px solid #e5e7eb; border-radius: 8px; color: #1f2937; text-decoration: none; font-size: 13px; margin-right: 6px; }}
.btn:hover {{ background: #f6f7fb; }}
</style></head>
<body>
<h1>🤖 화창하다 CS봇 관리자</h1>
<div class="sub">미매칭/저확신 질문 로그 — 정기 검토 후 FAQ 보강용</div>

<div class="cards">
  <div class="card"><h2>총 질문 수</h2><div class="big">{total_q:,}</div></div>
  <div class="card"><h2>미매칭/저확신</h2><div class="big">{total_u:,}</div></div>
  <div class="card"><h2>상태별 분포</h2><ul>{status_rows or '<li>데이터 없음</li>'}</ul></div>
  <div class="card"><h2>자주 묻는 미매칭 TOP 20</h2><ul>{top_rows or '<li>없음</li>'}</ul></div>
</div>

<div class="actions">
  <a class="btn" href="/admin/export.csv">📥 CSV 다운로드</a>
  <a class="btn" href="/">← 봇으로 돌아가기</a>
</div>

<table>
  <thead><tr><th>시각</th><th>상태</th><th>질문</th></tr></thead>
  <tbody>{''.join(rows_html) if rows_html else '<tr><td colspan="3" style="text-align:center; padding: 30px; color: #6b7280;">아직 미매칭 질문이 없습니다 🎉</td></tr>'}</tbody>
</table>
</body></html>
"""


@app.get("/admin/export.csv")
def admin_export(_: str = Depends(require_admin)):
    from fastapi.responses import Response
    import csv
    import io as iox

    buf = iox.StringIO()
    w = csv.writer(buf)
    w.writerow(["created_at", "status", "confidence", "question", "matched_id", "matched_question", "top_alt_id", "top_alt_question", "top_alt_confidence"])
    with db() as conn:
        for r in conn.execute("SELECT * FROM unmatched_log ORDER BY created_at DESC").fetchall():
            w.writerow([
                r["created_at"], r["status"], r["confidence"], r["question"],
                r["matched_id"], r["matched_question"],
                r["top_alt_id"], r["top_alt_question"], r["top_alt_confidence"],
            ])
    return Response(
        content="﻿" + buf.getvalue(),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": 'attachment; filename="cs_bot_unmatched.csv"'},
    )


# ===== HTML escape (관리자 페이지 안전성) =====
def escape(s: str) -> str:
    if not s:
        return ""
    return (str(s)
            .replace("&", "&amp;")
            .replace("<", "&lt;")
            .replace(">", "&gt;")
            .replace('"', "&quot;"))


# ===== 정적 파일 =====
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


@app.get("/")
def root():
    return FileResponse(STATIC_DIR / "index.html")


if __name__ == "__main__":
    import uvicorn
    host = os.environ.get("CSBOT_HOST", "0.0.0.0")
    port = int(os.environ.get("PORT", os.environ.get("CSBOT_PORT", "8765")))
    print(f"[server] http://{host}:{port}")
    uvicorn.run(app, host=host, port=port, log_level="info")

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
DEFAULT_VERIFIED_CLOUD_GZ = DATA_DIR / "verified_faq.json.gz"
DEFAULT_VERIFIED_LOCAL = Path(r"C:\Users\깡대표\OneDrive\문서\CS봇_검증FAQ.json")

# 클라우드: data/verified_faq.json[.gz] (git에 포함, .gz 우선)
# 로컬: OneDrive vault 경로 폴백
if DEFAULT_VERIFIED_CLOUD_GZ.exists():
    DEFAULT_VERIFIED = DEFAULT_VERIFIED_CLOUD_GZ
elif DEFAULT_VERIFIED_CLOUD.exists():
    DEFAULT_VERIFIED = DEFAULT_VERIFIED_CLOUD
else:
    DEFAULT_VERIFIED = DEFAULT_VERIFIED_LOCAL

VERIFIED_PATH = Path(os.environ.get("CSBOT_VERIFIED", DEFAULT_VERIFIED))

# 카톡 폴백은 클라우드 메모리 절약 위해 기본 OFF (환경변수로만 켬)
KAKAO_PATH_ENV = os.environ.get("CSBOT_KAKAO", "").strip()
KAKAO_PATH = Path(KAKAO_PATH_ENV) if KAKAO_PATH_ENV else None

# 로그 DB (클라우드 ephemeral, 로컬 vault 자동 미러)
DB_PATH = Path(os.environ.get("CSBOT_DB", str(HERE / "data" / "cs_bot.db")))

# 관리자 인증 (환경변수)
ADMIN_USERNAME = os.environ.get("CSBOT_ADMIN_USERNAME", "admin")
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

# 영구 백업/복원: HF_TOKEN 있으면 시작 시 DB 복원 시도
try:
    from backup import restore_db, start_periodic_backup, backup_db
    restore_db(DB_PATH)
except Exception as e:
    print(f"[server] backup module 로드 실패: {e}")
    restore_db = backup_db = start_periodic_backup = lambda *a, **k: False


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
        conn.execute("""
            CREATE TABLE IF NOT EXISTS custom_faqs (
                id TEXT PRIMARY KEY,
                question TEXT NOT NULL,
                answer TEXT NOT NULL,
                aliases TEXT NOT NULL DEFAULT '[]',
                category TEXT NOT NULL DEFAULT '관리자 추가',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS feedback (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                created_at TEXT NOT NULL,
                question TEXT NOT NULL,
                matched_id TEXT,
                answer TEXT,
                rating TEXT NOT NULL,
                comment TEXT,
                confidence REAL
            )
        """)
        conn.execute("CREATE INDEX IF NOT EXISTS idx_feedback_created ON feedback(created_at DESC)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_feedback_rating ON feedback(rating)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_unmatched_created ON unmatched_log(created_at DESC)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_query_created ON query_log(created_at DESC)")


init_db()


def _migrate_db():
    """기존 DB 컬럼 추가 (운영 중 안전하게)"""
    try:
        with sqlite3.connect(DB_PATH) as conn:
            cols = [r[1] for r in conn.execute("PRAGMA table_info(feedback)").fetchall()]
            if cols and "admin_note" not in cols:
                conn.execute("ALTER TABLE feedback ADD COLUMN admin_note TEXT")
                print("[db] feedback.admin_note 컬럼 추가 완료")
            if cols and "resolved" not in cols:
                conn.execute("ALTER TABLE feedback ADD COLUMN resolved INTEGER NOT NULL DEFAULT 0")
                print("[db] feedback.resolved 컬럼 추가 완료")

            ucols = [r[1] for r in conn.execute("PRAGMA table_info(unmatched_log)").fetchall()]
            if ucols and "admin_note" not in ucols:
                conn.execute("ALTER TABLE unmatched_log ADD COLUMN admin_note TEXT")
                print("[db] unmatched_log.admin_note 컬럼 추가 완료")
            if ucols and "resolved" not in ucols:
                conn.execute("ALTER TABLE unmatched_log ADD COLUMN resolved INTEGER NOT NULL DEFAULT 0")
                print("[db] unmatched_log.resolved 컬럼 추가 완료")
    except Exception as e:
        print(f"[db] 마이그레이션 실패: {e}")


_migrate_db()


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

# DB에 저장된 관리자 FAQ를 봇 메모리에 로드 (서버 재시작 시 복원)
def _load_custom_faqs_into_bot():
    try:
        with db() as conn:
            rows = conn.execute(
                "SELECT id, question, answer, aliases, category FROM custom_faqs ORDER BY created_at"
            ).fetchall()
        loaded = 0
        for r in rows:
            try:
                aliases = json.loads(r["aliases"]) if r["aliases"] else []
            except Exception:
                aliases = []
            bot.add_custom_faq(
                qid=r["id"],
                question=r["question"],
                answer=r["answer"],
                aliases=aliases,
                category=r["category"],
            )
            loaded += 1
        if loaded:
            print(f"[server] 관리자 FAQ {loaded}개 DB → 봇 메모리 로드 완료")
    except Exception as e:
        print(f"[server] custom_faqs 로드 실패: {e}")


import json
_load_custom_faqs_into_bot()


# ===== FastAPI =====
app = FastAPI(title="화창하다 CS봇", version="2.0.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


# 정적 파일 + HTML 응답의 캐시 무효화 (운영 중 즉시 반영)
@app.middleware("http")
async def add_no_cache_headers(request, call_next):
    response = await call_next(request)
    path = request.url.path
    if path == "/" or path.startswith("/static/") or path.startswith("/admin"):
        response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
        response.headers["Pragma"] = "no-cache"
        response.headers["Expires"] = "0"
    return response


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


class FeedbackRequest(BaseModel):
    question: str
    matched_id: str | None = None
    answer: str | None = None
    rating: str  # 'good' | 'bad'
    comment: str | None = None
    confidence: float | None = None


@app.post("/api/feedback")
def post_feedback(req: FeedbackRequest):
    rating = (req.rating or "").lower().strip()
    if rating not in ("good", "bad"):
        raise HTTPException(status_code=400, detail="rating must be 'good' or 'bad'")
    q = (req.question or "").strip()
    if not q:
        raise HTTPException(status_code=400, detail="question required")
    now = datetime.now().isoformat(timespec="seconds")
    try:
        with _log_lock, db() as conn:
            conn.execute(
                """INSERT INTO feedback (created_at, question, matched_id, answer, rating, comment, confidence)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (
                    now, q, req.matched_id, (req.answer or "")[:2000],
                    rating, (req.comment or "")[:1000],
                    req.confidence,
                ),
            )
        return {"ok": True}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


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
    ok_user = secrets.compare_digest(credentials.username.encode("utf-8"), ADMIN_USERNAME.encode("utf-8"))
    ok_pw = secrets.compare_digest(credentials.password.encode("utf-8"), ADMIN_PASSWORD.encode("utf-8"))
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
        total_q = conn.execute("SELECT COUNT(*) FROM query_log").fetchone()[0]
        total_u = conn.execute("SELECT COUNT(*) FROM unmatched_log").fetchone()[0]
        recent_status = conn.execute(
            "SELECT status, COUNT(*) c FROM query_log GROUP BY status ORDER BY c DESC"
        ).fetchall()
        top_unmatched = conn.execute(
            "SELECT question, COUNT(*) c FROM unmatched_log GROUP BY question ORDER BY c DESC LIMIT 20"
        ).fetchall()

        # 피드백 통계
        fb_good = conn.execute("SELECT COUNT(*) FROM feedback WHERE rating='good'").fetchone()[0]
        fb_bad = conn.execute("SELECT COUNT(*) FROM feedback WHERE rating='bad'").fetchone()[0]
        fb_bad_list = conn.execute(
            "SELECT * FROM feedback WHERE rating='bad' ORDER BY created_at DESC LIMIT 50"
        ).fetchall()
        fb_bad_by_faq = conn.execute(
            """SELECT matched_id, COUNT(*) c FROM feedback
               WHERE rating='bad' AND matched_id IS NOT NULL AND matched_id != ''
               GROUP BY matched_id ORDER BY c DESC LIMIT 15"""
        ).fetchall()


    status_rows = "".join(
        f"<li><b>{escape(r['status'])}</b>: {r['c']:,}회</li>"
        for r in recent_status
    )
    top_rows = "".join(
        f"<li>{escape(r['question'])} <span class='cnt'>({r['c']}회)</span></li>"
        for r in top_unmatched
    )
    fb_faq_rows = "".join(
        f"<li><b>{escape(r['matched_id'])}</b>: 👎 {r['c']}회</li>"
        for r in fb_bad_by_faq
    )

    return f"""<!DOCTYPE html>
<html lang="ko"><head><meta charset="UTF-8"><title>CS봇 관리자</title>
<style>
body {{ font-family: -apple-system, "Malgun Gothic", sans-serif; margin: 0; padding: 24px; background: #f6f7fb; color: #1f2937; }}
h1 {{ margin: 0 0 8px; font-size: 22px; }}
h2.section {{ margin: 32px 0 12px; font-size: 18px; }}
.sub {{ color: #6b7280; margin-bottom: 24px; font-size: 13px; }}
.cards {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(220px, 1fr)); gap: 12px; margin-bottom: 24px; }}
.card {{ background: white; border-radius: 12px; padding: 16px; box-shadow: 0 1px 3px rgba(0,0,0,0.04); }}
.card h2 {{ margin: 0 0 8px; font-size: 14px; color: #6b7280; font-weight: 600; }}
.card .big {{ font-size: 28px; font-weight: 700; color: #D60019; }}
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
.btn {{ display: inline-block; padding: 6px 12px; background: white; border: 1px solid #e5e7eb; border-radius: 8px; color: #1f2937; text-decoration: none; font-size: 13px; margin-right: 6px; cursor: pointer; font-family: inherit; }}
.btn:hover {{ background: #f6f7fb; }}
.btn.primary {{ background: #D60019; color: white; border-color: #D60019; }}
.btn.primary:hover {{ background: #B30015; }}
.btn.danger {{ color: #991b1b; border-color: #fecaca; }}
.btn.danger:hover {{ background: #fee2e2; }}

/* FAQ 편집 폼 */
.faq-form {{ background: white; border-radius: 12px; padding: 20px; box-shadow: 0 1px 3px rgba(0,0,0,0.04); margin-bottom: 16px; }}
.faq-form .row {{ margin-bottom: 12px; }}
.faq-form label {{ display: block; font-size: 12px; font-weight: 600; color: #6b7280; margin-bottom: 4px; }}
.faq-form input, .faq-form textarea {{ width: 100%; padding: 8px 10px; border: 1px solid #e5e7eb; border-radius: 8px; font-size: 13px; font-family: inherit; box-sizing: border-box; resize: vertical; }}
.faq-form input:focus, .faq-form textarea:focus {{ outline: none; border-color: #D60019; }}
.faq-form textarea {{ min-height: 80px; }}
.faq-form .hint {{ font-size: 11px; color: #9ca3af; margin-top: 3px; }}
.faq-form .form-actions {{ display: flex; gap: 8px; margin-top: 12px; }}
.faq-list-row {{ background: white; border-radius: 8px; padding: 12px 14px; margin-bottom: 8px; box-shadow: 0 1px 2px rgba(0,0,0,0.04); }}
.faq-list-row .head {{ display: flex; justify-content: space-between; gap: 12px; align-items: flex-start; margin-bottom: 6px; }}
.faq-list-row .id {{ font-family: monospace; color: #6b7280; font-size: 11px; padding: 2px 8px; background: #f6f7fb; border-radius: 4px; }}
.faq-list-row .question {{ font-weight: 600; }}
.faq-list-row .answer {{ font-size: 12px; color: #4b5563; margin: 6px 0; white-space: pre-wrap; word-break: break-word; }}
.faq-list-row .aliases {{ font-size: 11px; color: #6b7280; }}
.faq-list-row .alias-chip {{ display: inline-block; padding: 1px 6px; background: #eef0f5; border-radius: 999px; margin: 2px; }}
#faq-empty {{ text-align: center; padding: 24px; color: #6b7280; font-size: 13px; }}
.toast {{ position: fixed; bottom: 24px; right: 24px; padding: 12px 18px; background: #1f2937; color: white; border-radius: 8px; font-size: 13px; opacity: 0; transition: opacity 0.3s; pointer-events: none; z-index: 100; }}
.toast.show {{ opacity: 1; }}

/* 피드백 카드 */
.fb-row {{ background: white; border-radius: 10px; padding: 14px 16px; margin-bottom: 10px; box-shadow: 0 1px 3px rgba(0,0,0,0.04); border-left: 4px solid #D60019; }}
.fb-row.resolved {{ opacity: 0.55; border-left-color: #10b981; }}
.fb-row .meta {{ display: flex; gap: 8px; align-items: center; flex-wrap: wrap; margin-bottom: 6px; font-size: 12px; }}
.fb-row .ts {{ color: #6b7280; font-family: monospace; }}
.fb-row .id {{ font-family: monospace; color: #6b7280; padding: 1px 6px; background: #f6f7fb; border-radius: 4px; }}
.fb-row .conf {{ color: #6b7280; }}
.fb-row .q {{ font-weight: 600; margin-bottom: 4px; }}
.fb-row .student-comment {{ background: #FFF5F5; border-left: 3px solid #D60019; padding: 8px 10px; margin: 8px 0; border-radius: 4px; font-size: 13px; color: #4b5563; white-space: pre-wrap; word-break: break-word; }}
.fb-row .student-comment.empty {{ background: #f6f7fb; border-left-color: #9ca3af; color: #9ca3af; font-style: italic; }}
.fb-row .admin-note {{ margin-top: 10px; padding-top: 10px; border-top: 1px dashed #e5e7eb; }}
.fb-row .admin-note label {{ display: block; font-size: 11px; font-weight: 600; color: #6b7280; margin-bottom: 4px; }}
.fb-row .admin-note textarea {{ width: 100%; padding: 8px 10px; border: 1px solid #e5e7eb; border-radius: 6px; font-size: 12px; font-family: inherit; resize: vertical; box-sizing: border-box; min-height: 50px; }}
.fb-row .admin-note textarea:focus {{ outline: none; border-color: #D60019; }}
.fb-row .row-actions {{ margin-top: 8px; display: flex; gap: 6px; align-items: center; }}
.fb-row .saved {{ color: #10b981; font-size: 12px; }}
.fb-empty {{ text-align: center; padding: 30px; color: #6b7280; font-size: 13px; background: white; border-radius: 10px; }}
</style></head>
<body>
<h1>🤖 화창하다 CS봇 관리자</h1>
<div class="sub">미매칭/저확신 질문 로그 + FAQ 편집</div>

<div class="cards">
  <div class="card"><h2>총 질문 수</h2><div class="big">{total_q:,}</div></div>
  <div class="card"><h2>미매칭/저확신</h2><div class="big">{total_u:,}</div></div>
  <div class="card"><h2>👍 도움됐어요</h2><div class="big" style="color:#10b981">{fb_good:,}</div></div>
  <div class="card"><h2>👎 수정 필요</h2><div class="big" style="color:#D60019">{fb_bad:,}</div></div>
  <div class="card"><h2>상태별 분포</h2><ul>{status_rows or '<li>데이터 없음</li>'}</ul></div>
  <div class="card"><h2>자주 묻는 미매칭 TOP 20</h2><ul>{top_rows or '<li>없음</li>'}</ul></div>
  <div class="card"><h2>👎 많이 받은 FAQ TOP 15</h2><ul>{fb_faq_rows or '<li>없음</li>'}</ul></div>
</div>

<!-- ===== FAQ 편집 섹션 ===== -->
<h2 class="section">📝 FAQ 추가/편집 (즉시 반영)</h2>
<div class="sub">여기서 추가한 FAQ는 저장 즉시 봇이 검색에 사용합니다. 자주 묻는 미매칭 질문을 새 FAQ로 만들어 운영하세요.</div>

<div class="faq-form">
  <input type="hidden" id="faq-edit-id" value="">
  <div class="row">
    <label>질문 *</label>
    <input id="faq-question" placeholder="예: 카드결제도 가능한가요?">
  </div>
  <div class="row">
    <label>답변 * (마크다운 가능)</label>
    <textarea id="faq-answer" placeholder="**교육비용**은 카드결제가 가능합니다..."></textarea>
  </div>
  <div class="row">
    <label>별칭 (쉼표로 구분, 동의어/구어체)</label>
    <input id="faq-aliases" placeholder="예: 카드 결제, 신용카드, 결제 수단">
    <div class="hint">하나의 질문을 다양한 표현으로 잡으려면 별칭을 풍부하게 넣어주세요</div>
  </div>
  <div class="row">
    <label>카테고리</label>
    <input id="faq-category" placeholder="관리자 추가" value="관리자 추가">
  </div>
  <div class="form-actions">
    <button class="btn primary" id="faq-save-btn" type="button">+ 추가</button>
    <button class="btn" data-action="reset" type="button">초기화</button>
    <span style="flex:1"></span>
    <span id="faq-edit-info" style="font-size:12px; color:#6b7280; align-self:center;"></span>
  </div>
</div>

<div id="faq-list"></div>

<!-- ===== 📚 검증 FAQ 수정 ===== -->
<h2 class="section">📚 검증 FAQ 수정 (전체 {len(bot.verified["faqs"])}개)</h2>
<div class="sub">외부에서도 검증 FAQ 답변·별칭을 직접 수정 가능. <b>✏️ 수정</b> 버튼 누르면 위 폼에 자동 채워지고, 저장하면 즉시 봇에 반영됩니다.</div>

<div style="margin-bottom: 12px;">
  <input id="verified-search" type="search" placeholder="🔍 ID·질문·답변·별칭·카테고리로 검색"
         style="width:100%; padding:10px 12px; border:1px solid #e5e7eb; border-radius:8px; font-size:14px; box-sizing:border-box;">
</div>
<div id="verified-list"></div>

<!-- ===== 👎 피드백 목록 ===== -->
<h2 class="section">👎 답변 수정 필요 (사용자 피드백)</h2>
<div class="sub">학생 피드백을 보고 <b>관리자 메모</b>를 작성하거나, 답변을 보완한 뒤 <b>처리완료</b>로 표시하세요.</div>

<div id="feedback-list"></div>

<!-- ===== 미매칭 로그 ===== -->
<h2 class="section">🔍 미매칭/저확신 질문 로그</h2>
<div class="sub">학생이 묻고 봇이 답 못 했거나 저확신으로 답한 질문 — <b>📝 FAQ로 만들기</b> 버튼으로 폼에 자동 채워서 바로 추가 가능.</div>
<div class="actions">
  <a class="btn" href="/admin/export.csv">📥 CSV 다운로드</a>
  <a class="btn" href="/">← 봇으로 돌아가기</a>
</div>

<div id="unmatched-list"></div>

<div id="toast" class="toast"></div>

<script src="/static/admin.js"></script>
</body></html>
"""


# ===== 관리자 FAQ CRUD API =====
class FaqCreate(BaseModel):
    id: str | None = None
    question: str
    answer: str
    aliases: list[str] = []
    category: str = "관리자 추가"


def _next_custom_id() -> str:
    """다음 커스텀 FAQ ID 자동 생성 (CUSTOM-1, CUSTOM-2 ...)"""
    with db() as conn:
        row = conn.execute(
            "SELECT id FROM custom_faqs WHERE id LIKE 'CUSTOM-%' ORDER BY CAST(SUBSTR(id, 8) AS INTEGER) DESC LIMIT 1"
        ).fetchone()
        if not row:
            return "CUSTOM-1"
        try:
            n = int(row["id"].split("-")[1])
        except Exception:
            n = 0
        return f"CUSTOM-{n + 1}"


@app.get("/admin/api/faqs")
def admin_list_faqs(_: str = Depends(require_admin)):
    """관리자가 추가한 FAQ 목록 반환"""
    return {"faqs": bot.list_custom_faqs()}


@app.get("/admin/api/verified-faqs")
def admin_list_verified_faqs(_: str = Depends(require_admin)):
    """검증 FAQ 전체 목록 (수정용, 임베딩/토큰 등 메타데이터 제외)"""
    return {
        "faqs": [
            {
                "id": f["id"],
                "question": f["question"],
                "answer": f["answer"],
                "aliases": f.get("aliases", []),
                "category": f.get("category", ""),
                "custom": bool(f.get("custom")),
            }
            for f in bot.verified["faqs"]
        ]
    }


@app.post("/admin/api/faqs")
def admin_create_faq(faq: FaqCreate, _: str = Depends(require_admin)):
    """새 FAQ 추가 - 임베딩 생성하고 즉시 검색 가능"""
    q = (faq.question or "").strip()
    a = (faq.answer or "").strip()
    if not q or not a:
        raise HTTPException(status_code=400, detail="question/answer required")

    qid = (faq.id or "").strip() or _next_custom_id()

    # 검증 FAQ ID와 충돌 방지
    if any(f["id"] == qid and not f.get("custom") for f in bot.verified["faqs"]):
        raise HTTPException(status_code=400, detail=f"id {qid} conflicts with verified FAQ")

    aliases = [s.strip() for s in (faq.aliases or []) if s and s.strip()]
    now = datetime.now().isoformat(timespec="seconds")

    with _log_lock, db() as conn:
        conn.execute(
            """INSERT OR REPLACE INTO custom_faqs (id, question, answer, aliases, category, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?,
                       COALESCE((SELECT created_at FROM custom_faqs WHERE id=?), ?),
                       ?)""",
            (qid, q, a, json.dumps(aliases, ensure_ascii=False), faq.category, qid, now, now),
        )

    item = bot.add_custom_faq(qid=qid, question=q, answer=a, aliases=aliases, category=faq.category)
    return {
        "ok": True,
        "id": qid,
        "question": item["question"],
        "answer": item["answer"],
        "aliases": item["aliases"],
        "category": item["category"],
    }


@app.put("/admin/api/faqs/{faq_id}")
def admin_update_faq(faq_id: str, faq: FaqCreate, _: str = Depends(require_admin)):
    """기존 FAQ 수정 — 커스텀 FAQ 또는 검증 FAQ override"""
    q = (faq.question or "").strip()
    a = (faq.answer or "").strip()
    if not q or not a:
        raise HTTPException(status_code=400, detail="question/answer required")

    # 검증 FAQ인지 확인 (override 케이스)
    is_verified = any(f["id"] == faq_id for f in bot.verified["faqs"])
    with db() as conn:
        custom_row = conn.execute("SELECT id FROM custom_faqs WHERE id=?", (faq_id,)).fetchone()

    if not is_verified and not custom_row:
        raise HTTPException(status_code=404, detail="faq not found")

    aliases = [s.strip() for s in (faq.aliases or []) if s and s.strip()]
    now = datetime.now().isoformat(timespec="seconds")

    with _log_lock, db() as conn:
        # INSERT OR REPLACE — 검증 FAQ override든 커스텀 수정이든 같은 테이블 사용
        conn.execute(
            """INSERT OR REPLACE INTO custom_faqs (id, question, answer, aliases, category, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?,
                       COALESCE((SELECT created_at FROM custom_faqs WHERE id=?), ?),
                       ?)""",
            (faq_id, q, a, json.dumps(aliases, ensure_ascii=False), faq.category, faq_id, now, now),
        )

    bot.add_custom_faq(qid=faq_id, question=q, answer=a, aliases=aliases, category=faq.category)
    return {"ok": True, "id": faq_id, "updated_at": now, "override": is_verified}


# ===== 미매칭 로그 관리 =====
@app.get("/admin/api/unmatched")
def admin_list_unmatched(_: str = Depends(require_admin)):
    """미매칭/저확신 질문 목록 (최근 300건)"""
    with db() as conn:
        rows = conn.execute(
            """SELECT id, created_at, question, status, confidence,
                      matched_id, matched_question, top_alt_id, top_alt_question,
                      top_alt_confidence, admin_note, resolved
               FROM unmatched_log
               ORDER BY resolved ASC, created_at DESC
               LIMIT 300"""
        ).fetchall()
    return {
        "unmatched": [
            {
                "id": r["id"],
                "created_at": r["created_at"],
                "question": r["question"],
                "status": r["status"],
                "confidence": r["confidence"],
                "matched_id": r["matched_id"],
                "matched_question": r["matched_question"],
                "top_alt_id": r["top_alt_id"],
                "top_alt_question": r["top_alt_question"],
                "top_alt_confidence": r["top_alt_confidence"],
                "admin_note": r["admin_note"],
                "resolved": bool(r["resolved"]),
            }
            for r in rows
        ]
    }


class UnmatchedNoteUpdate(BaseModel):
    admin_note: str | None = None
    resolved: bool | None = None


@app.patch("/admin/api/unmatched/{log_id}")
def admin_update_unmatched(log_id: int, body: UnmatchedNoteUpdate, _: str = Depends(require_admin)):
    """미매칭 로그에 관리자 메모 / 해결 표시"""
    fields, values = [], []
    if body.admin_note is not None:
        fields.append("admin_note=?")
        values.append(body.admin_note.strip()[:2000])
    if body.resolved is not None:
        fields.append("resolved=?")
        values.append(1 if body.resolved else 0)
    if not fields:
        raise HTTPException(status_code=400, detail="no fields to update")
    values.append(log_id)
    with _log_lock, db() as conn:
        cur = conn.execute(
            f"UPDATE unmatched_log SET {', '.join(fields)} WHERE id=?",
            values,
        )
        if cur.rowcount == 0:
            raise HTTPException(status_code=404, detail="log not found")
    return {"ok": True}


# ===== 피드백 관리자 코멘트 =====
class FeedbackNoteUpdate(BaseModel):
    admin_note: str | None = None
    resolved: bool | None = None


@app.get("/admin/api/feedback")
def admin_list_feedback(_: str = Depends(require_admin)):
    """👎 피드백 목록 (최근 200건, 코멘트/해결 상태 포함)"""
    with db() as conn:
        rows = conn.execute(
            """SELECT id, created_at, question, matched_id, answer, rating,
                      comment, confidence, admin_note, resolved
               FROM feedback
               WHERE rating='bad'
               ORDER BY resolved ASC, created_at DESC
               LIMIT 200"""
        ).fetchall()
    return {
        "feedback": [
            {
                "id": r["id"],
                "created_at": r["created_at"],
                "question": r["question"],
                "matched_id": r["matched_id"],
                "answer": r["answer"],
                "comment": r["comment"],
                "confidence": r["confidence"],
                "admin_note": r["admin_note"],
                "resolved": bool(r["resolved"]),
            }
            for r in rows
        ]
    }


@app.patch("/admin/api/feedback/{feedback_id}")
def admin_update_feedback(feedback_id: int, body: FeedbackNoteUpdate, _: str = Depends(require_admin)):
    """피드백에 관리자 메모/해결 표시 저장"""
    fields, values = [], []
    if body.admin_note is not None:
        fields.append("admin_note=?")
        values.append(body.admin_note.strip()[:2000])
    if body.resolved is not None:
        fields.append("resolved=?")
        values.append(1 if body.resolved else 0)
    if not fields:
        raise HTTPException(status_code=400, detail="no fields to update")
    values.append(feedback_id)
    with _log_lock, db() as conn:
        cur = conn.execute(
            f"UPDATE feedback SET {', '.join(fields)} WHERE id=?",
            values,
        )
        if cur.rowcount == 0:
            raise HTTPException(status_code=404, detail="feedback not found")
    return {"ok": True}


@app.delete("/admin/api/faqs/{faq_id}")
def admin_delete_faq(faq_id: str, _: str = Depends(require_admin)):
    """커스텀 FAQ 삭제"""
    with _log_lock, db() as conn:
        cur = conn.execute("DELETE FROM custom_faqs WHERE id=?", (faq_id,))
        if cur.rowcount == 0:
            raise HTTPException(status_code=404, detail="faq not found")
    bot.remove_custom_faq(faq_id)
    return {"ok": True, "id": faq_id}


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
    # 주기적 백업 스레드 시작
    try:
        start_periodic_backup(DB_PATH)
    except Exception as e:
        print(f"[server] 백업 스레드 시작 실패: {e}")
    uvicorn.run(app, host=host, port=port, log_level="info")

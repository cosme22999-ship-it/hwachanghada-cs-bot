---
title: Hwachanghada CS Bot
emoji: ☀️
colorFrom: red
colorTo: pink
sdk: docker
pinned: false
app_port: 7860
---

# 화창하다 CS봇

옵시디언 vault의 검증 FAQ를 기반으로 24시간 운영되는 CS 챗봇.

## 두 가지 모드

| 모드 | 용도 | 데이터 |
|---|---|---|
| **로컬** | 본인 PC에서 테스트·관리 | OneDrive 경로 자동 인식 |
| **클라우드 (Render.com)** | 학생들이 24/7 접속 | `data/verified_faq.json` (git 포함) |

---

## 로컬 실행

```bash
# 더블클릭 한 번
run.bat
```

또는 수동:
```bash
python -m venv .venv && .venv\Scripts\activate
pip install -r requirements.txt
python server.py
```
→ http://127.0.0.1:8765

**관리자 페이지**: `set CSBOT_ADMIN_PASSWORD=비번` 후 실행 → http://127.0.0.1:8765/admin (id: `admin`)

---

## 클라우드 배포 (Render.com 무료, 24/7)

### 1단계 — GitHub에 푸시
```bash
# 새 GitHub 저장소 만든 후 (https://github.com/new)
git remote add origin https://github.com/<당신아이디>/hwachanghada-cs-bot.git
git push -u origin main
```

### 2단계 — Render.com 가입 & 배포
1. https://render.com 가입 (GitHub 계정 연동, 결제정보 불필요)
2. **Dashboard → New → Blueprint** 클릭
3. 방금 푸시한 저장소 선택 → `render.yaml` 자동 인식
4. **Apply** 클릭 → 약 5-7분 빌드 → 배포 완료

### 3단계 — 환경변수 확인
Render 대시보드에서 `CSBOT_ADMIN_PASSWORD`가 자동 생성된 걸 확인 → 메모

### 4단계 — 접속
- 봇: `https://hwachanghada-cs-bot.onrender.com`
- 관리자: `https://hwachanghada-cs-bot.onrender.com/admin` (id: `admin`, pw: 위에서 메모한 값)

> ⚠️ 무료 티어는 15분 미사용 시 sleep → 첫 요청은 30-60초 콜드 스타트. 학생용 안내에 "잠시만 기다려주세요"를 추가하거나 [UptimeRobot](https://uptimerobot.com)으로 5분마다 헬스체크하면 항상 깨어 있음.

---

## FAQ 업데이트 흐름

1. 옵시디언에서 `CS봇_핵심FAQ_정리완료.md` 편집 (질문 추가/수정)
2. `CS봇_FAQ_별칭.json`에 동의어 추가 (선택)
3. 터미널에서:
   ```bash
   cd cs_bot
   python build_data.py    # 임베딩 재생성 → data/verified_faq.json 업데이트
   git add data/verified_faq.json
   git commit -m "FAQ 업데이트: ..."
   git push
   ```
4. Render가 push 감지하고 자동 재배포 (3-5분)

---

## 미매칭 질문 관리

봇이 답 못한 질문 또는 65% 미만 저확신 매칭 → 자동으로 SQLite에 저장.

- `/admin` 페이지에서 미매칭 TOP 20, 최근 200건 확인
- `/admin/export.csv` 로 전체 다운로드
- 자주 묻는 미매칭을 마크다운에 추가 → 재빌드 → 푸시

---

## 구조

```
cs_bot/
├── server.py         FastAPI 서버 + 관리자 페이지 + SQLite 로깅
├── bot_core.py       검색 엔진 (임베딩 + BM25 하이브리드)
├── build_data.py     vault → data/verified_faq.json 빌드
├── data/
│   ├── verified_faq.json   56개 FAQ + 임베딩 (git 포함, 4.6MB)
│   └── cs_bot.db           SQLite 로그 (.gitignore)
├── static/           채팅 UI (외부 CDN 없음)
├── Dockerfile        Render 배포용
├── render.yaml       Render 배포 설정
├── requirements.txt
└── run.bat           로컬 원클릭 실행
```

## 모델

- **paraphrase-multilingual-MiniLM-L12-v2** (118MB, RAM ~300MB)
- 한국어 56개 FAQ 매칭 정확도 9/9 (평균 90%+ 확신도)
- Render 무료 티어(512MB) 호환

## API

| 엔드포인트 | 메서드 | 설명 |
|---|---|---|
| `/` | GET | 채팅 UI |
| `/api/ask` | POST | `{"question": "..."}` → 답변 |
| `/api/stats` | GET | 봇 통계 |
| `/api/health` | GET | 헬스체크 |
| `/admin` | GET | 관리자 페이지 (베이직 인증) |
| `/admin/export.csv` | GET | 미매칭 로그 CSV |

## 환경 변수

| 변수 | 기본 | 설명 |
|---|---|---|
| `PORT` | 8765 | 서버 포트 (Render 자동 지정) |
| `CSBOT_ADMIN_PASSWORD` | 없음 | 관리자 페이지 비밀번호 (필수) |
| `CSBOT_MODEL` | MiniLM | 임베딩 모델 |
| `CSBOT_LOG_THRESHOLD` | 65.0 | 저확신 로그 임계값 (%) |
| `CSBOT_VERIFIED` | 자동 | 검증 FAQ JSON 경로 |
| `CSBOT_KAKAO` | 없음 | 카톡 폴백 (선택, 메모리 많이 씀) |
| `CSBOT_DB` | data/cs_bot.db | SQLite 경로 |

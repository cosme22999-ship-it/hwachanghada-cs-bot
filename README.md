# 화창하다 CS봇

옵시디언 vault에 정리된 검증 FAQ 56개 + 카톡 학습 데이터를 기반으로 한 웹 CS봇.

## 빠른 시작

### 1. 한 번에 실행 (추천)
`run.bat` 더블클릭 → 자동으로 의존성 설치 + 서버 시작 + 브라우저 오픈

### 2. 수동 실행
```bash
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
python server.py
```

브라우저에서 http://127.0.0.1:8765 접속

## 구조

```
cs_bot/
├── server.py       # FastAPI 서버 (API + 정적 파일 서빙)
├── bot_core.py     # 검색 엔진 (검증FAQ 임베딩 + BM25 하이브리드)
├── static/
│   ├── index.html  # 채팅 UI
│   ├── style.css   # 자체 호스팅 스타일
│   └── chat.js     # 클라이언트 로직
├── requirements.txt
├── run.bat         # 원클릭 실행
└── README.md
```

## 데이터 의존성

- `C:\Users\깡대표\OneDrive\문서\CS봇_검증FAQ.json` (필수, 6.4MB)
- `C:\Users\깡대표\OneDrive\문서\CS봇_FAQ데이터베이스.json` (선택, 폴백용)

검증FAQ JSON은 옵시디언 vault의 `faq_builder.py`로 빌드되어 있음.

## 검색 로직 (우선순위)

1. **키워드 트리거** ("내 제품", "전성분", "단가" 등) → 제조지원톡 안내
2. **검증 FAQ 하이브리드 검색** (임베딩 70% + BM25 30%)
   - 임계값 0.50 이상이면 답변
3. **카톡 학습 FAQ 폴백** (임계값 0.65 이상이면 참고용으로 답변)
4. **매칭 실패** → 채널톡 안내

## API

### POST /api/ask
```json
{ "question": "MOQ가 얼마예요?" }
```

응답:
```json
{
  "status": "found_verified",
  "answer": "기본적으로 100ml 기준 300개...",
  "category": "제품 개발",
  "matched_id": "Q17",
  "matched_question": "MOQ(최소 수량)는 얼마인가요?",
  "confidence": 78.5,
  "alternatives": [...]
}
```

### GET /api/stats
검증 FAQ 통계

### GET /api/health
서버 헬스체크

## 환경 변수 (선택)

| 변수 | 기본값 |
|---|---|
| `CSBOT_HOST` | `127.0.0.1` |
| `CSBOT_PORT` | `8765` |
| `CSBOT_VERIFIED` | `C:\Users\깡대표\OneDrive\문서\CS봇_검증FAQ.json` |
| `CSBOT_KAKAO` | `C:\Users\깡대표\OneDrive\문서\CS봇_FAQ데이터베이스.json` |

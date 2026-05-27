"""
카톡 학습 데이터(3,072개 Q&A)에서 의미가 비슷한 질문들을 추출해
검증 FAQ의 별칭(aliases)에 자동 보강.

흐름:
1. 검증 FAQ 마크다운 + 별칭 JSON 로드
2. 카톡 학습 데이터 로드
3. 검증 FAQ 임베딩 (질문 + 기존 별칭)
4. 카톡 질문 임베딩
5. 각 카톡 질문 → 가장 가까운 검증 FAQ (코사인 유사도)
6. 유사도가 SIMILARITY_THRESHOLD 이상이면 그 FAQ의 별칭에 추가
7. 결과를 별칭 JSON에 저장 (백업본 보존)

사용:
    python enrich_aliases.py [--threshold 0.70] [--max-per-faq 40] [--dry-run]
"""

import argparse
import io
import json
import os
import re
import sys
from collections import defaultdict
from pathlib import Path

import numpy as np
from sentence_transformers import SentenceTransformer

if sys.platform == "win32":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")


HERE = Path(__file__).parent.resolve()
VAULT = Path(os.environ.get("CSBOT_VAULT", r"C:\Users\깡대표\OneDrive\문서\Obsidian Vault"))
KAKAO_DATA = Path(r"C:\Users\깡대표\OneDrive\문서\CS봇_FAQ데이터베이스.json")

INPUT_MD = VAULT / "CS봇_핵심FAQ_정리완료.md"
INPUT_ALIASES = VAULT / "CS봇_FAQ_별칭.json"
BACKUP_ALIASES = VAULT / "CS봇_FAQ_별칭.backup.json"

MODEL_NAME = os.environ.get(
    "CSBOT_MODEL",
    "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2",
)


def parse_markdown(md_path: Path):
    """검증 FAQ 마크다운에서 Q번호+질문+답변 추출"""
    text = md_path.read_text(encoding="utf-8")
    faqs = []
    lines = text.split("\n")
    cur_id, cur_q, cur_a, in_a = None, None, [], False
    for line in lines:
        qm = re.match(r"^###\s*Q(\d+)\.\s*(.+?)\s*$", line)
        if qm:
            if cur_id and cur_q:
                faqs.append({"id": cur_id, "question": cur_q, "answer": "\n".join(cur_a).strip()})
            cur_id = f"Q{int(qm.group(1))}"
            cur_q = qm.group(2).strip()
            cur_a = []
            in_a = False
            continue
        am = re.match(r"^\*\*A:\*\*\s*(.*)$", line)
        if am:
            in_a = True
            if am.group(1).strip():
                cur_a.append(am.group(1).strip())
            continue
        if in_a:
            if line.startswith("---") or line.startswith("##"):
                in_a = False
                continue
            cur_a.append(line)
    if cur_id and cur_q:
        faqs.append({"id": cur_id, "question": cur_q, "answer": "\n".join(cur_a).strip()})
    return faqs


def _is_question_like(q: str) -> bool:
    """한국어 질문 패턴인지 판단 (응답/감사/짧은 대화 단편 제외)"""
    t = q.strip()
    # 길이 필터
    if len(t) < 6 or len(t) > 120:
        return False
    # 줄바꿈 많은 긴 채팅은 제외
    if t.count("\n") >= 2:
        return False
    # 의문 패턴 (끝 + 본문 키워드)
    question_endings = ["?", "?", "요?", "까요", "나요", "가요", "니까", "는지", "ㄴ가",
                        "뭐예요", "뭔가요", "어떻", "얼마", "언제", "어디", "어느", "왜",
                        "되나", "있나", "할까", "가능한", "맞나", "되는"]
    has_question = any(end in t for end in question_endings)
    # 응답성 표현 (시작에 자주 나옴) 제외
    response_starts = ["네 ", "네,", "네!", "네~", "넵", "넹", "네에", "네네",
                       "확인했", "확인해", "확인하", "확인합", "알겠습", "알겠어",
                       "감사합", "감사해", "감사드", "ㄱㅅ", "고맙",
                       "안녕하세요", "안녕하십", "수고하", "수고하세",
                       "맞아요", "맞습니다", "맞네", "그렇네",
                       "좋아요", "좋습니", "좋네", "괜찮",
                       "죄송합", "죄송해", "죄송하",
                       "넵!", "오 ", "와 "]
    if any(t.startswith(s) for s in response_starts):
        return False
    return has_question


def load_kakao(path: Path):
    """카톡 학습 DB에서 (질문, 답변) 쌍 추출 (질문 필터 + 중복 제거)"""
    if not path.exists():
        print(f"[!] 카톡 데이터 없음: {path}")
        return []
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    pairs = []
    seen = set()
    for category, qa_list in data.items():
        for qa in qa_list:
            q = (qa.get("question") or "").strip()
            a = (qa.get("answer") or "").strip()
            if not _is_question_like(q):
                continue
            if len(a) < 10:  # 답변이 너무 짧으면 (그냥 "넵") 제외
                continue
            qkey = re.sub(r"\s+", "", q).lower()
            if qkey in seen:
                continue
            seen.add(qkey)
            pairs.append((q, a))
    return pairs


def normalize_str(s: str) -> str:
    return re.sub(r"\s+", " ", s.strip()).lower()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--q-threshold", type=float, default=0.75,
                    help="질문 유사도 임계값 (0~1)")
    ap.add_argument("--a-threshold", type=float, default=0.55,
                    help="답변 유사도 임계값 (의도 검증용)")
    ap.add_argument("--max-per-faq", type=int, default=30,
                    help="검증 FAQ 한 개당 별칭 최대 개수")
    ap.add_argument("--dry-run", action="store_true",
                    help="실제 저장 없이 통계만")
    args = ap.parse_args()

    print("=" * 70)
    print(f"카톡 학습 데이터에서 별칭 자동 추출")
    print(f"  · 질문 유사도 ≥ {args.q_threshold}")
    print(f"  · 답변 유사도 ≥ {args.a_threshold} (의도 일치 검증)")
    print("=" * 70)

    # 1) 검증 FAQ 로드
    faqs = parse_markdown(INPUT_MD)
    print(f"\n[1] 검증 FAQ 로드: {len(faqs)}개")

    # 2) 기존 별칭 로드
    with open(INPUT_ALIASES, "r", encoding="utf-8") as f:
        alias_doc = json.load(f)
    aliases_map = alias_doc.get("aliases", {})
    existing_total = sum(len(v) for v in aliases_map.values())
    print(f"[2] 기존 별칭: {existing_total}개")

    # 3) 카톡 데이터 로드 (질문 + 답변 쌍)
    kakao_pairs = load_kakao(KAKAO_DATA)
    print(f"[3] 카톡 (질문,답변) 쌍: {len(kakao_pairs)}개")

    # 4) 모델 로드
    print(f"\n[4] 모델 로드: {MODEL_NAME}")
    model = SentenceTransformer(MODEL_NAME)

    # 5) 검증 FAQ + 기존 별칭 임베딩 (variant들의 max 매칭)
    print(f"\n[5] 검증 FAQ 질문/별칭 임베딩...")
    faq_variants = []  # [(faq_idx, text), ...]
    for fi, item in enumerate(faqs):
        existing = aliases_map.get(item["id"], [])
        for v in [item["question"]] + existing:
            faq_variants.append((fi, v))
    variant_texts = [v[1] for v in faq_variants]
    variant_vecs = model.encode(variant_texts, show_progress_bar=False, convert_to_numpy=True, batch_size=64)
    variant_vecs /= (np.linalg.norm(variant_vecs, axis=1, keepdims=True) + 1e-10)

    # 검증 FAQ 답변 임베딩 (의도 검증용)
    print(f"[5b] 검증 FAQ 답변 임베딩...")
    faq_answer_texts = [(faqs[fi]["answer"][:500] or faqs[fi]["question"]) for fi in range(len(faqs))]
    faq_answer_vecs = model.encode(faq_answer_texts, show_progress_bar=False, convert_to_numpy=True, batch_size=32)
    faq_answer_vecs /= (np.linalg.norm(faq_answer_vecs, axis=1, keepdims=True) + 1e-10)

    # 6) 카톡 질문 + 답변 임베딩
    print(f"[6] 카톡 질문+답변 임베딩...")
    kakao_qs = [p[0] for p in kakao_pairs]
    kakao_as = [p[1][:500] for p in kakao_pairs]
    k_q_vecs = model.encode(kakao_qs, show_progress_bar=False, convert_to_numpy=True, batch_size=64)
    k_q_vecs /= (np.linalg.norm(k_q_vecs, axis=1, keepdims=True) + 1e-10)
    k_a_vecs = model.encode(kakao_as, show_progress_bar=False, convert_to_numpy=True, batch_size=32)
    k_a_vecs /= (np.linalg.norm(k_a_vecs, axis=1, keepdims=True) + 1e-10)

    # 7) 매칭 - 질문 유사도 + 답변 유사도 둘 다 통과해야 별칭으로 채택
    print(f"\n[7] 매칭 (질문 ≥ {args.q_threshold} AND 답변 ≥ {args.a_threshold})...")
    candidates = defaultdict(list)  # faq_id -> [(q_score, a_score, kakao_q), ...]

    # 질문 유사도: kakao × variant
    q_sims = k_q_vecs @ variant_vecs.T  # (N_kakao, N_variants)
    # 답변 유사도: kakao_ans × faq_ans
    a_sims = k_a_vecs @ faq_answer_vecs.T  # (N_kakao, N_faqs)

    n_faqs = len(faqs)
    for ki, kq in enumerate(kakao_qs):
        row = q_sims[ki]
        per_faq_max = np.full(n_faqs, -1.0)
        for vi, (fi, _) in enumerate(faq_variants):
            if row[vi] > per_faq_max[fi]:
                per_faq_max[fi] = row[vi]
        best_fi = int(np.argmax(per_faq_max))
        q_score = float(per_faq_max[best_fi])
        a_score = float(a_sims[ki][best_fi])
        # 두 조건 모두 통과
        if q_score >= args.q_threshold and a_score >= args.a_threshold:
            candidates[faqs[best_fi]["id"]].append((q_score, a_score, kq))

    # 8) 정렬 + 중복 제거 + 길이 제한
    print(f"\n[8] 별칭 정리 (FAQ당 최대 {args.max_per_faq}개)...")
    added_count = 0
    for qid, items in candidates.items():
        # 질문+답변 유사도 합산 기준 정렬
        items.sort(key=lambda x: -(x[0] + x[1] * 0.5))
        existing = aliases_map.get(qid, [])
        existing_normalized = {normalize_str(a) for a in existing}
        faq_q = next((f["question"] for f in faqs if f["id"] == qid), "")
        existing_normalized.add(normalize_str(faq_q))

        new_aliases = list(existing)
        for q_score, a_score, kq in items:
            if normalize_str(kq) in existing_normalized:
                continue
            if len(new_aliases) >= args.max_per_faq:
                break
            new_aliases.append(kq)
            existing_normalized.add(normalize_str(kq))
            added_count += 1
        aliases_map[qid] = new_aliases

    # 9) 통계
    print(f"\n{'=' * 70}")
    print("결과")
    print("=" * 70)
    total_after = sum(len(v) for v in aliases_map.values())
    print(f"별칭 총합: {existing_total} → {total_after} (+{added_count})")

    # TOP 10 보강된 FAQ
    top_added = sorted(candidates.items(), key=lambda kv: -len(kv[1]))[:10]
    print(f"\n가장 많이 보강된 FAQ TOP 10:")
    for qid, items in top_added:
        faq_q = next((f["question"] for f in faqs if f["id"] == qid), "")
        print(f"  · {qid}: +{len(items)}개 후보 / {faq_q[:50]}")
        for q_score, a_score, kq in items[:3]:
            print(f"      · Q{q_score*100:.0f}% A{a_score*100:.0f}% | {kq[:60]}")

    # 10) 저장
    if args.dry_run:
        print(f"\n[!] DRY-RUN: 실제 저장 안 함")
        return

    print(f"\n[10] 저장")
    # 백업
    if INPUT_ALIASES.exists():
        BACKUP_ALIASES.write_text(INPUT_ALIASES.read_text(encoding="utf-8"), encoding="utf-8")
        print(f"  백업: {BACKUP_ALIASES.name}")

    alias_doc["aliases"] = aliases_map
    alias_doc["_auto_enriched"] = True
    alias_doc["_enriched_at"] = __import__("datetime").datetime.now().isoformat(timespec="seconds")
    alias_doc["_enrichment_stats"] = {
        "q_threshold": args.q_threshold,
        "a_threshold": args.a_threshold,
        "max_per_faq": args.max_per_faq,
        "added": added_count,
        "before": existing_total,
        "after": total_after,
    }

    with open(INPUT_ALIASES, "w", encoding="utf-8") as f:
        json.dump(alias_doc, f, ensure_ascii=False, indent=2)
    print(f"  저장: {INPUT_ALIASES.name}")

    print(f"\n[+] 완료. 이제 `python build_data.py` 실행해서 임베딩 재생성하세요.")


if __name__ == "__main__":
    main()

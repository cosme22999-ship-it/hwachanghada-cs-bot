"""
화창하다 CS봇 - 검색 엔진 코어
검증FAQ(임베딩+별칭) + BM25 하이브리드 검색
"""

from __future__ import annotations

import gzip
import json
import os
import re
from pathlib import Path
from typing import Optional


def _open_json(path):
    """json 또는 json.gz 자동 인식"""
    p = Path(path)
    if str(p).endswith(".gz"):
        return gzip.open(p, "rt", encoding="utf-8")
    # .gz 우선 시도 (운영 환경)
    gz = p.with_suffix(p.suffix + ".gz") if not str(p).endswith(".gz") else None
    if gz and gz.exists() and not p.exists():
        return gzip.open(gz, "rt", encoding="utf-8")
    return open(p, "r", encoding="utf-8")

import numpy as np
from rank_bm25 import BM25Okapi
from sentence_transformers import SentenceTransformer


CHANNEL_TALK_MANUFACTURING = "https://pf.kakao.com/_WxhHVX/friend"
# 교육·강의·미션은 본인 집중코칭방으로 안내 (개별 톡방이라 단일 링크 없음)

TRIGGER_KEYWORDS_MANUFACTURING = [
    "내 제품", "제 제품", "우리 제품", "내제품", "제제품",
    # "전성분"은 일반 정책 질문(Q70)도 있어서 트리거에서 제외 — 개인 정보 요청은 "내/제/우리 제품" 키워드로 잡힘
    "언제 나와", "언제 출고", "언제 출시",
    "진행 상황", "진행상황", "어디까지 됐", "어디까지 진행",
    "내 견적", "내 단가", "내 유통기한", "내 로트",
]

# 회사명/관계 키워드 - 봇이 답변하지 않고 교육 채널톡으로 안내
# (레드메디코스는 강미정 대표 명함에 도메인이 포함되어 있어 트리거에서 제외)
# 정반합: 기수에 따라 내용이 다르므로 봇이 일률적으로 안내하지 않음
TRIGGER_KEYWORDS_COMPANY_INFO = [
    "콰브",
    "정반합",
]

FALLBACK_MSG_MANUFACTURING = (
    "이 질문은 제품마다 개별 확인이 필요한 사항이라 "
    "**화창하다 제조지원 채널톡**으로 문의 부탁드립니다 🙏\n\n"
    f"👉 {CHANNEL_TALK_MANUFACTURING}\n\n"
    "**기수·성함·제품명**을 함께 남겨주시면 빠르게 확인 도와드리겠습니다!"
)

FALLBACK_MSG_COMPANY_INFO = (
    "관련 안내는 담당 멘토님께 직접 확인 부탁드립니다 🙏\n\n"
    "멘토 연락처를 모르신다면 본인이 참여 중인 **집중코칭방**에 문의해주세요!\n\n"
    "**기수·성함·연락처**와 함께 남겨주시면 빠르게 안내드리겠습니다!"
)

FALLBACK_MSG_NO_MATCH = (
    "죄송합니다. 정확한 답변을 찾지 못했습니다.\n\n"
    "📚 **강의·미션·교육 관련 문의** → 본인이 참여 중인 **집중코칭방**에 문의해주세요.\n\n"
    "🧪 **성분·연구소·화장품 개발 관련 문의** → **화창하다 제조지원 채널톡**\n"
    f"👉 {CHANNEL_TALK_MANUFACTURING}\n\n"
    "**기수·성함·연락처**와 함께 문의 남겨주세요!"
)


def tokenize(text: str) -> list[str]:
    return re.findall(r"[가-힣]+|[a-z]+|\d+", (text or "").lower())


class CSBot:
    def __init__(
        self,
        verified_path: str | Path,
        kakao_path: Optional[str | Path] = None,
        model_name: Optional[str] = None,
        emb_weight: float = 0.78,
        bm25_weight: float = 0.22,
    ):
        self.emb_w = emb_weight
        self.bm25_w = bm25_weight

        with _open_json(verified_path) as f:
            self.verified = json.load(f)

        # 모델은 검증FAQ에 기록된 것 우선, 환경변수, 인자 순으로
        resolved_model = (
            model_name
            or os.environ.get("CSBOT_MODEL")
            or self.verified.get("model")
            or "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"
        )
        print(f"[bot] 모델 로드: {resolved_model}")
        self.model = SentenceTransformer(resolved_model)
        self.model_name = resolved_model

        self.variant_index = []
        variant_vecs = []
        for faq_idx, item in enumerate(self.verified["faqs"]):
            for v_text, v_emb in zip(item["variant_texts"], item["variant_embeddings"]):
                self.variant_index.append({"faq_idx": faq_idx, "text": v_text})
                variant_vecs.append(v_emb)
        self.variant_vecs = np.array(variant_vecs, dtype=np.float32) if variant_vecs else np.zeros((0, 0), dtype=np.float32)
        print(f"[bot] 검증 FAQ {len(self.verified['faqs'])}개 / 변형(질문+별칭) {len(self.variant_index)}개")

        self.bm25_corpus = [item["tokens"] for item in self.verified["faqs"]]
        self.bm25 = BM25Okapi(self.bm25_corpus) if self.bm25_corpus else None

        self.kakao_index: list[dict] = []
        self.kakao_vecs_norm: Optional[np.ndarray] = None
        if kakao_path and Path(kakao_path).exists():
            print(f"[bot] 카톡 폴백 FAQ 로드: {Path(kakao_path).name}")
            with open(kakao_path, "r", encoding="utf-8") as f:
                kakao_db = json.load(f)
            questions = []
            for category, qa_list in kakao_db.items():
                for qa in qa_list:
                    self.kakao_index.append({
                        "question": qa["question"],
                        "answer": qa["answer"],
                        "category": category,
                        "responder": qa.get("responder", ""),
                    })
                    questions.append(qa["question"])
            if questions:
                vecs = self.model.encode(questions, show_progress_bar=False, convert_to_numpy=True)
                norms = np.linalg.norm(vecs, axis=1, keepdims=True) + 1e-10
                self.kakao_vecs_norm = (vecs / norms).astype(np.float32)
                print(f"[bot] 카톡 폴백 {len(self.kakao_index)}개 임베딩 완료")

        print("[bot] 준비 완료")

    @staticmethod
    def _is_manufacturing_trigger(text: str) -> Optional[str]:
        t = text.replace(" ", "")
        for kw in TRIGGER_KEYWORDS_MANUFACTURING:
            if kw.replace(" ", "") in t:
                return kw
        return None

    @staticmethod
    def _is_company_info_trigger(text: str) -> Optional[str]:
        t = text.replace(" ", "")
        for kw in TRIGGER_KEYWORDS_COMPANY_INFO:
            if kw.replace(" ", "") in t:
                return kw
        return None

    def _embed(self, text: str) -> np.ndarray:
        v = self.model.encode(text, convert_to_numpy=True)
        return (v / (np.linalg.norm(v) + 1e-10)).astype(np.float32)

    @staticmethod
    def _normalize_minmax(arr: np.ndarray) -> np.ndarray:
        if arr.max() <= arr.min():
            return np.zeros_like(arr)
        return (arr - arr.min()) / (arr.max() - arr.min())

    def _search_verified(self, user_input: str, q_vec: np.ndarray, top_k: int = 3):
        n_faqs = len(self.verified["faqs"])
        variant_sims = self.variant_vecs @ q_vec
        emb_scores = np.zeros(n_faqs, dtype=np.float32)
        for i, vinfo in enumerate(self.variant_index):
            f_idx = vinfo["faq_idx"]
            if variant_sims[i] > emb_scores[f_idx]:
                emb_scores[f_idx] = variant_sims[i]

        tokens = tokenize(user_input)
        bm25_raw = self.bm25.get_scores(tokens) if tokens else np.zeros(n_faqs)
        bm25_norm = self._normalize_minmax(bm25_raw)

        combined = self.emb_w * emb_scores + self.bm25_w * bm25_norm
        idx_sorted = np.argsort(-combined)[:top_k]

        results = []
        for idx in idx_sorted:
            results.append({
                "faq": self.verified["faqs"][idx],
                "combined": float(combined[idx]),
                "emb": float(emb_scores[idx]),
                "bm25": float(bm25_norm[idx]),
            })
        return results

    def _search_kakao(self, q_vec: np.ndarray, top_k: int = 1):
        if self.kakao_vecs_norm is None or not self.kakao_index:
            return []
        sims = self.kakao_vecs_norm @ q_vec
        idx_sorted = np.argsort(-sims)[:top_k]
        return [(float(sims[i]), self.kakao_index[i]) for i in idx_sorted]

    def answer(
        self,
        user_input: str,
        verified_threshold: float = 0.45,
        kakao_threshold: float = 0.65,
    ) -> dict:
        user_input = (user_input or "").strip()
        if not user_input:
            return {"status": "empty", "answer": "질문을 입력해주세요."}

        trigger = self._is_manufacturing_trigger(user_input)
        if trigger:
            return {
                "status": "trigger_manufacturing",
                "answer": FALLBACK_MSG_MANUFACTURING,
                "category": "제조지원톡 안내",
                "trigger_keyword": trigger,
                "confidence": 100.0,
                "source": "keyword_trigger",
                "alternatives": [],
            }

        company_trigger = self._is_company_info_trigger(user_input)
        if company_trigger:
            return {
                "status": "trigger_company_info",
                "answer": FALLBACK_MSG_COMPANY_INFO,
                "category": "교육지원톡 안내",
                "trigger_keyword": company_trigger,
                "confidence": 100.0,
                "source": "keyword_trigger",
                "alternatives": [],
            }

        q_vec = self._embed(user_input)
        v_hits = self._search_verified(user_input, q_vec, top_k=3)

        if v_hits and v_hits[0]["combined"] >= verified_threshold:
            best = v_hits[0]
            faq = best["faq"]
            return {
                "status": "found_verified",
                "answer": faq["answer"],
                "category": faq["category"],
                "matched_id": faq["id"],
                "matched_question": faq["question"],
                "confidence": round(best["combined"] * 100, 1),
                "embedding_score": round(best["emb"] * 100, 1),
                "bm25_score": round(best["bm25"] * 100, 1),
                "source": "verified_faq",
                "alternatives": [
                    {
                        "id": h["faq"]["id"],
                        "question": h["faq"]["question"],
                        "confidence": round(h["combined"] * 100, 1),
                    }
                    for h in v_hits[1:]
                    if h["combined"] >= verified_threshold - 0.10
                ],
            }

        k_hits = self._search_kakao(q_vec, top_k=1)
        if k_hits and k_hits[0][0] >= kakao_threshold:
            score, item = k_hits[0]
            return {
                "status": "found_kakao_reference",
                "answer": item["answer"],
                "category": item["category"],
                "matched_question": item["question"],
                "confidence": round(score * 100, 1),
                "warning": "참고용 답변입니다. 정확한 정보는 채널톡으로 문의주세요.",
                "source": "kakao_reference",
                "alternatives": [],
            }

        return {
            "status": "no_match",
            "answer": FALLBACK_MSG_NO_MATCH,
            "category": "안내",
            "confidence": 0.0,
            "source": "fallback",
            "alternatives": [
                {
                    "id": h["faq"]["id"],
                    "question": h["faq"]["question"],
                    "confidence": round(h["combined"] * 100, 1),
                }
                for h in v_hits[:3]
            ] if v_hits else [],
        }

    def stats(self) -> dict:
        return {
            "verified_faq_count": len(self.verified["faqs"]),
            "variant_count": len(self.variant_index),
            "categories": self.verified.get("categories", {}),
            "kakao_fallback_count": len(self.kakao_index),
            "model": self.model_name,
            "custom_faq_count": sum(1 for f in self.verified["faqs"] if f.get("custom")),
        }

    # ===== 동적 FAQ 관리 (관리자용) =====
    def _embed_variants(self, variants: list[str]) -> list[list[float]]:
        """질문 + 별칭 임베딩 (정규화)"""
        vecs = self.model.encode(variants, show_progress_bar=False, convert_to_numpy=True)
        norms = np.linalg.norm(vecs, axis=1, keepdims=True) + 1e-10
        return (vecs / norms).astype(np.float32).tolist()

    def _build_tokens(self, variants: list[str], answer: str) -> list[str]:
        """BM25용 토큰"""
        return list(set(
            sum([tokenize(v) for v in variants], [])
            + tokenize(answer)[:50]
        ))

    def _rebuild_indexes(self) -> None:
        """검증 + 커스텀 합쳐서 variant_index/variant_vecs/bm25 재구성"""
        self.variant_index = []
        variant_vecs = []
        for faq_idx, item in enumerate(self.verified["faqs"]):
            for v_text, v_emb in zip(item["variant_texts"], item["variant_embeddings"]):
                self.variant_index.append({"faq_idx": faq_idx, "text": v_text})
                variant_vecs.append(v_emb)
        self.variant_vecs = np.array(variant_vecs, dtype=np.float32) if variant_vecs else np.zeros((0, 0), dtype=np.float32)
        self.bm25_corpus = [item["tokens"] for item in self.verified["faqs"]]
        self.bm25 = BM25Okapi(self.bm25_corpus) if self.bm25_corpus else None

    def add_custom_faq(self, qid: str, question: str, answer: str,
                        aliases: list[str] | None = None,
                        category: str = "관리자 추가") -> dict:
        """관리자 페이지에서 FAQ 추가. 임베딩 즉시 생성하고 인덱스에 합침."""
        aliases = aliases or []
        variants = [question] + aliases
        item = {
            "id": qid,
            "category": category,
            "question": question,
            "answer": answer,
            "aliases": aliases,
            "variant_texts": variants,
            "variant_embeddings": self._embed_variants(variants),
            "tokens": self._build_tokens(variants, answer),
            "custom": True,
        }
        # 기존 같은 id 있으면 교체
        existing_idx = next((i for i, f in enumerate(self.verified["faqs"]) if f["id"] == qid), None)
        if existing_idx is not None:
            self.verified["faqs"][existing_idx] = item
        else:
            self.verified["faqs"].append(item)
        self._rebuild_indexes()
        return item

    def remove_custom_faq(self, qid: str) -> bool:
        """관리자가 추가한 FAQ만 삭제 가능 (검증 FAQ는 보호)"""
        for i, f in enumerate(self.verified["faqs"]):
            if f["id"] == qid and f.get("custom"):
                del self.verified["faqs"][i]
                self._rebuild_indexes()
                return True
        return False

    def list_custom_faqs(self) -> list[dict]:
        """관리자 추가 FAQ만 반환 (임베딩/토큰 제외, UI용)"""
        return [
            {
                "id": f["id"],
                "question": f["question"],
                "answer": f["answer"],
                "aliases": f.get("aliases", []),
                "category": f.get("category", ""),
            }
            for f in self.verified["faqs"]
            if f.get("custom")
        ]

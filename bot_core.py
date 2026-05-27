"""
화창하다 CS봇 - 검색 엔진 코어
검증FAQ(임베딩+별칭) + BM25 하이브리드 검색
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Optional

import numpy as np
from rank_bm25 import BM25Okapi
from sentence_transformers import SentenceTransformer


CHANNEL_TALK_MANUFACTURING = "https://pf.kakao.com/_WxhHVX/friend"
CHANNEL_TALK_EDUCATION = "https://pf.kakao.com/_xcxoxkVX/friend"

TRIGGER_KEYWORDS_MANUFACTURING = [
    "내 제품", "제 제품", "우리 제품", "내제품", "제제품",
    "전성분", "전 성분",
    "언제 나와", "언제 출고", "언제 출시",
    "진행 상황", "진행상황", "어디까지 됐", "어디까지 진행",
    "내 견적", "내 단가", "내 유통기한", "내 로트",
]

FALLBACK_MSG_MANUFACTURING = (
    "이 질문은 제품마다 개별 확인이 필요한 사항이라 "
    "**화창하다 제조지원 채널톡**으로 문의 부탁드립니다 🙏\n\n"
    f"👉 {CHANNEL_TALK_MANUFACTURING}\n\n"
    "**기수·성함·제품명**을 함께 남겨주시면 빠르게 확인 도와드리겠습니다!"
)

FALLBACK_MSG_NO_MATCH = (
    "죄송합니다. 정확한 답변을 찾지 못했습니다.\n\n"
    "교육 관련 문의는 **화창하다 교육지원 채널톡**\n"
    f"👉 {CHANNEL_TALK_EDUCATION}\n\n"
    "화장품 개발/제조 관련 문의는 **화창하다 제조지원 채널톡**\n"
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
        emb_weight: float = 0.7,
        bm25_weight: float = 0.3,
    ):
        self.emb_w = emb_weight
        self.bm25_w = bm25_weight

        with open(verified_path, "r", encoding="utf-8") as f:
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
        self.variant_vecs = np.array(variant_vecs, dtype=np.float32)
        print(f"[bot] 검증 FAQ {len(self.verified['faqs'])}개 / 변형(질문+별칭) {len(self.variant_index)}개")

        self.bm25_corpus = [item["tokens"] for item in self.verified["faqs"]]
        self.bm25 = BM25Okapi(self.bm25_corpus)

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
        verified_threshold: float = 0.50,
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
        }

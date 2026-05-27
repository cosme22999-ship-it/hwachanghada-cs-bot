"""
검증 FAQ 마크다운 + 별칭 → 임베딩 JSON 빌드 (클라우드 배포용)

옵시디언 vault의 마크다운/별칭을 읽어서 cs_bot/data/verified_faq.json 생성.
작은 다국어 모델(paraphrase-multilingual-MiniLM-L12-v2, ~120MB)로 임베딩하여
Render.com 무료 티어(512MB RAM)에서도 동작 가능하게 함.

사용:
    python build_data.py
"""

import io
import json
import os
import re
import sys
from pathlib import Path

import numpy as np
from sentence_transformers import SentenceTransformer

if sys.platform == "win32":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")


HERE = Path(__file__).parent.resolve()
DATA_DIR = HERE / "data"
DATA_DIR.mkdir(exist_ok=True)

VAULT_DEFAULT = Path(r"C:\Users\깡대표\OneDrive\문서\Obsidian Vault")
VAULT = Path(os.environ.get("CSBOT_VAULT", VAULT_DEFAULT))

INPUT_MD = VAULT / "CS봇_핵심FAQ_정리완료.md"
INPUT_ALIASES = VAULT / "CS봇_FAQ_별칭.json"
OUTPUT_JSON = DATA_DIR / "verified_faq.json"

MODEL_NAME = os.environ.get(
    "CSBOT_MODEL",
    "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2",
)


def tokenize(text: str) -> list[str]:
    text = text.lower()
    return re.findall(r"[가-힣]+|[a-z]+|\d+", text)


def parse_markdown(md_path: Path):
    text = md_path.read_text(encoding="utf-8")
    lines = text.split("\n")

    faqs = []
    current_category = None
    current_q_num = None
    current_q_text = None
    current_a_lines = []
    in_answer = False

    def flush():
        nonlocal current_q_num, current_q_text, current_a_lines, in_answer
        if current_q_num is not None and current_q_text and current_a_lines:
            answer = "\n".join(current_a_lines).strip()
            if answer:
                faqs.append({
                    "id": f"Q{current_q_num}",
                    "category": current_category or "일반",
                    "question": current_q_text.strip(),
                    "answer": answer,
                })
        current_q_num = None
        current_q_text = None
        current_a_lines = []
        in_answer = False

    for line in lines:
        cat_match = re.match(r"^##\s*📂\s*카테고리\s*\d+\.\s*(.+?)\s*$", line)
        if cat_match:
            flush()
            current_category = cat_match.group(1).strip()
            continue

        q_match = re.match(r"^###\s*Q(\d+)\.\s*(.+?)\s*$", line)
        if q_match:
            flush()
            current_q_num = int(q_match.group(1))
            current_q_text = q_match.group(2).strip()
            continue

        a_match = re.match(r"^\*\*A:\*\*\s*(.*)$", line)
        if a_match:
            in_answer = True
            first = a_match.group(1).strip()
            if first:
                current_a_lines.append(first)
            continue

        if in_answer:
            if line.startswith("---"):
                in_answer = False
                continue
            current_a_lines.append(line)

    flush()
    return faqs


def main():
    print("=" * 60)
    print("CS봇 빌드 - 클라우드 배포용 임베딩 생성")
    print("=" * 60)

    if not INPUT_MD.exists():
        print(f"[!] 마크다운 없음: {INPUT_MD}")
        return

    print(f"[*] 마크다운: {INPUT_MD.name}")
    faqs = parse_markdown(INPUT_MD)
    print(f"    → {len(faqs)}개 Q&A 추출")

    aliases_map = {}
    if INPUT_ALIASES.exists():
        print(f"[*] 별칭: {INPUT_ALIASES.name}")
        with open(INPUT_ALIASES, "r", encoding="utf-8") as f:
            aliases_map = json.load(f).get("aliases", {})
        total = sum(len(v) for v in aliases_map.values())
        print(f"    → {len(aliases_map)}개 ID에 별칭 {total}개")

    cats = {}
    for item in faqs:
        cats[item["category"]] = cats.get(item["category"], 0) + 1
    print("\n카테고리 분포:")
    for cat, cnt in sorted(cats.items(), key=lambda x: -x[1]):
        print(f"  • {cat}: {cnt}개")

    print(f"\n[*] 모델 로드: {MODEL_NAME}")
    model = SentenceTransformer(MODEL_NAME)

    print("[*] 임베딩 생성...")
    for item in faqs:
        qid = item["id"]
        aliases = aliases_map.get(qid, [])
        variants = [item["question"]] + aliases

        vecs = model.encode(variants, show_progress_bar=False, convert_to_numpy=True)
        norms = np.linalg.norm(vecs, axis=1, keepdims=True) + 1e-10
        vecs_norm = (vecs / norms).astype(np.float32)

        item["aliases"] = aliases
        item["variant_texts"] = variants
        item["variant_embeddings"] = vecs_norm.tolist()
        item["tokens"] = list(set(
            sum([tokenize(v) for v in variants], [])
            + tokenize(item.get("answer", ""))[:50]
        ))

    output = {
        "version": "2.0-cloud",
        "source": INPUT_MD.name,
        "model": MODEL_NAME,
        "total": len(faqs),
        "categories": cats,
        "aliases_total": sum(len(v) for v in aliases_map.values()),
        "faqs": faqs,
    }

    print(f"\n[*] 저장: {OUTPUT_JSON}")
    with open(OUTPUT_JSON, "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2)

    size_mb = OUTPUT_JSON.stat().st_size / (1024 * 1024)
    print(f"    → {size_mb:.2f} MB")

    print("\n[+] 완료. 이제 `python server.py` 로 실행 가능")


if __name__ == "__main__":
    main()

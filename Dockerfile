FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    HF_HOME=/app/.cache/huggingface \
    TRANSFORMERS_CACHE=/app/.cache/huggingface

WORKDIR /app

# 시스템 의존성 (sentence-transformers는 torch 필요)
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

# Python 패키지 (CPU torch로 가볍게)
COPY requirements.txt .
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir torch --index-url https://download.pytorch.org/whl/cpu && \
    pip install --no-cache-dir -r requirements.txt

# 모델 미리 다운로드 (콜드 스타트 단축)
RUN python -c "from sentence_transformers import SentenceTransformer; SentenceTransformer('sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2')"

# 앱 파일 복사
COPY bot_core.py server.py ./
COPY static ./static
COPY data ./data

# HF Spaces는 7860, Render는 동적 PORT — 둘 다 PORT 환경변수로 처리
ENV PORT=7860
EXPOSE 7860

CMD ["python", "server.py"]

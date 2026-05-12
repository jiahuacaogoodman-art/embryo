FROM python:3.11-slim

WORKDIR /app

# 系统依赖（OCR + GUI 支持）
RUN apt-get update && apt-get install -y --no-install-recommends \
    tesseract-ocr \
    tesseract-ocr-chi-sim \
    xvfb \
    xdotool \
    scrot \
    && rm -rf /var/lib/apt/lists/*

# Python 依赖
COPY pyproject.toml .
RUN pip install --no-cache-dir -e ".[full]" && \
    pip install --no-cache-dir fastapi uvicorn python-telegram-bot

# 复制代码
COPY src/ src/
COPY skills/ skills/

# 数据目录
RUN mkdir -p /data/memory /data/skills /data/sessions /data/screenshots /data/scheduler

ENV EMBRYO_DATA_DIR=/data
ENV EMBRYO_LOG_LEVEL=INFO

EXPOSE 8642

# 默认启动 Web API
CMD ["python", "-m", "embryo.main", "serve"]

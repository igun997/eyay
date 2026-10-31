# syntax=docker/dockerfile:1
# Worker image: build with the repository root as context:  docker build -f docker/worker.Dockerfile -t eyeai-worker .

FROM python:3.12-slim AS model
ARG YOLOX_URL=https://github.com/Megvii-BaseDetection/YOLOX/releases/download/0.1.1rc0/yolox_s.onnx
ARG YOLOX_SHA256=c5c2d13e59ae883e6af3b45daea64af4833a4951c92d116ec270d9ddbe998063
COPY docker/fetch_model.py /fetch_model.py
# Optional `--secret id=ca,src=ca-bundle.crt` for build networks behind a TLS-inspecting proxy.
RUN --mount=type=secret,id=ca,target=/run/ca.crt,required=false \
    if [ -s /run/ca.crt ]; then export SSL_CERT_FILE=/run/ca.crt; fi; \
    python /fetch_model.py "$YOLOX_URL" "$YOLOX_SHA256" /yolox_s.onnx

FROM python:3.12-slim
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 PIP_NO_CACHE_DIR=1 \
    PYTHONPATH=/app/src:/app/worker \
    EYEAI_MODEL=/app/data/models/yolox_s.onnx PORT=8090
WORKDIR /app
COPY worker/requirements.txt /tmp/requirements.txt
RUN --mount=type=secret,id=ca,target=/run/ca.crt,required=false \
    if [ -s /run/ca.crt ]; then export PIP_CERT=/run/ca.crt; fi; \
    pip install -r /tmp/requirements.txt
COPY src/eyeai /app/src/eyeai
COPY worker/eyeai_worker /app/worker/eyeai_worker
COPY --from=model /yolox_s.onnx /app/data/models/yolox_s.onnx
RUN useradd --system --uid 10001 eyeai && chown -R eyeai /app
USER eyeai
EXPOSE 8090
HEALTHCHECK --interval=15s --timeout=3s --start-period=20s --retries=3 \
  CMD python -c "import os,urllib.request as u; u.urlopen('http://127.0.0.1:%s/health' % os.environ.get('PORT','8090'), timeout=2)"
CMD ["python", "-m", "eyeai_worker"]

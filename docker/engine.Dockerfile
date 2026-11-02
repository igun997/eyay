# syntax=docker/dockerfile:1
# Worker image: the Rust engine (engine/). Build with the repository root as context:
#   docker build -f docker/engine.Dockerfile -t eyeai-worker .
# Debian forky (testing) is used because it is the distribution that ships ffmpeg 9.x, which ffmpeg-next 9 requires.

FROM python:3.12-slim AS model
ARG YOLOX_URL=https://github.com/Megvii-BaseDetection/YOLOX/releases/download/0.1.1rc0/yolox_s.onnx
ARG YOLOX_SHA256=c5c2d13e59ae883e6af3b45daea64af4833a4951c92d116ec270d9ddbe998063
COPY docker/fetch_model.py /fetch_model.py
# Optional `--secret id=ca,src=ca-bundle.crt` for build networks behind a TLS-inspecting proxy.
RUN --mount=type=secret,id=ca,target=/run/ca.crt,required=false \
    if [ -s /run/ca.crt ]; then export SSL_CERT_FILE=/run/ca.crt; fi; \
    python /fetch_model.py "$YOLOX_URL" "$YOLOX_SHA256" /yolox_s.onnx

FROM debian:forky-slim AS build
RUN apt-get update && apt-get install -y --no-install-recommends \
      cargo rustc build-essential pkg-config clang libclang-dev ca-certificates libssl-dev \
      libavcodec-dev libavformat-dev libavutil-dev libswscale-dev libavdevice-dev libavfilter-dev libswresample-dev \
 && rm -rf /var/lib/apt/lists/*
WORKDIR /src
# dependencies first, so a source-only change does not rebuild them
COPY engine/Cargo.toml engine/Cargo.lock ./
RUN mkdir src && echo 'fn main() {}' > src/main.rs && cargo build --release --locked && rm -rf src target/release/eyeai-engine target/release/deps/eyeai_engine*
COPY engine/src ./src
RUN cargo build --release --locked \
 # the runtime image needs exactly the packages that own the shared libraries the binary links
 && ldd target/release/eyeai-engine | awk '/=> \//{print $3}' | xargs dpkg -S 2>/dev/null | cut -d: -f1 | sort -u > /runtime-packages.txt

# Tests: `docker build --target test -t eyeai-engine-test .` then run it. The Postgres integration tests run when DATABASE_URL
# points at a database with db/migrations applied (CI does); without it they are skipped.
FROM build AS test
RUN cargo test --release --locked --no-run
CMD ["cargo", "test", "--release", "--locked"]

FROM debian:forky-slim
ENV EYEAI_MODEL=/app/data/models/yolox_s.onnx PORT=8090
COPY --from=build /runtime-packages.txt /tmp/runtime-packages.txt
RUN apt-get update && apt-get install -y --no-install-recommends ca-certificates $(cat /tmp/runtime-packages.txt) \
 && rm -rf /var/lib/apt/lists/* /tmp/runtime-packages.txt
WORKDIR /app
COPY --from=build /src/target/release/eyeai-engine /usr/local/bin/eyeai-engine
COPY --from=model /yolox_s.onnx /app/data/models/yolox_s.onnx
RUN useradd --system --uid 10001 eyeai && chown -R eyeai /app
USER eyeai
EXPOSE 8090
HEALTHCHECK --interval=15s --timeout=3s --start-period=20s --retries=3 \
  CMD ["/usr/local/bin/eyeai-engine", "--healthcheck"]
CMD ["eyeai-engine"]

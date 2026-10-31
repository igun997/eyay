#!/usr/bin/env bash
# Download Apache-2.0 YOLOX ONNX models and public sample videos into data/ (not tracked by git).
set -euo pipefail
cd "$(dirname "$0")/.."
mkdir -p data/models data/samples

fetch() { # url dest sha256
  if [[ -f "$2" ]] && echo "$3  $2" | sha256sum -c --quiet 2>/dev/null; then echo "ok   $2"; return; fi
  curl -fL --retry 3 -o "$2" "$1"
  echo "$3  $2" | sha256sum -c --quiet
  echo "got  $2"
}

R=https://github.com/Megvii-BaseDetection/YOLOX/releases/download/0.1.1rc0
fetch "$R/yolox_s.onnx"    data/models/yolox_s.onnx    c5c2d13e59ae883e6af3b45daea64af4833a4951c92d116ec270d9ddbe998063
fetch "$R/yolox_tiny.onnx" data/models/yolox_tiny.onnx 427cc366d34e27ff7a03e2899b5e3671425c262ea2291f88bb942bc1cc70b0f7
fetch "$R/yolox_nano.onnx" data/models/yolox_nano.onnx c789161ed43c8269fcd4e67c67eeeb4e80c622da2eb296a20bc6007bd18a0b7d

V=https://raw.githubusercontent.com/intel-iot-devkit/sample-videos/master
fetch "$V/car-detection.mp4"               data/samples/car-detection.mp4               d31e0ebf194cc16e50eea784e7c3b0b1bb7976a4fb9b3fec26cb92a6704cca34
fetch "$V/person-bicycle-car-detection.mp4" data/samples/person-bicycle-car-detection.mp4 452b11b7e0efbd019f1d9570d0c790e90416ad4ad29eec6003872d08443140ef
fetch "$V/store-aisle-detection.mp4"       data/samples/store-aisle-detection.mp4       3526fee39cac70d6366471e4324a6d63b337c8f8ab99c015521bee0d60ed6e04

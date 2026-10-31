#!/bin/sh
# Dev only: loops sample videos as live streams. RTSP -> mediamtx, HLS -> served over HTTP on :8099.
# /samples is the repo's data/samples (fetch with scripts/fetch_assets.sh).
set -eu
RTSP=${RTSP_BASE:-rtsp://mediamtx:8554}
mkdir -p /hls
python3 -m http.server 8099 --directory /hls >/dev/null 2>&1 &

push() { # name file
  while true; do
    ffmpeg -hide_banner -loglevel error -re -stream_loop -1 -i "/samples/$2" -an -c:v libx264 -preset veryfast -tune zerolatency -g 25 -pix_fmt yuv420p \
      -f rtsp -rtsp_transport tcp "$RTSP/$1" || true
    sleep 2
  done
}
hls() { # name file
  mkdir -p "/hls/$1"
  while true; do
    ffmpeg -hide_banner -loglevel error -re -stream_loop -1 -i "/samples/$2" -an -c:v libx264 -preset veryfast -g 25 -pix_fmt yuv420p \
      -f hls -hls_time 2 -hls_list_size 6 -hls_flags delete_segments+omit_endlist "/hls/$1/live.m3u8" || true
    sleep 2
  done
}

push dock2 person-bicycle-car-detection.mp4 &
push gate  car-detection.mp4 &
push aisle store-aisle-detection.mp4 &
hls  yard  person-bicycle-car-detection.mp4 &
wait

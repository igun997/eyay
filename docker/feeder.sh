#!/bin/sh
# Dev only: loops sample videos as live RTSP streams into mediamtx.
# mediamtx also serves each path as HLS:  http://mediamtx:8888/<path>/index.m3u8  (use these as m3u8 sources).
# /samples is the repo's data/samples (fetch with scripts/fetch_assets.sh).
RTSP=${RTSP_BASE:-rtsp://mediamtx:8554}

push() { # name file
  while true; do
    ffmpeg -hide_banner -loglevel error -re -stream_loop -1 -i "/samples/$2" -an -c:v libx264 -preset veryfast -tune zerolatency -g 25 -pix_fmt yuv420p \
      -f rtsp -rtsp_transport tcp "$RTSP/$1" || true
    sleep 2
  done
}

push dock2 person-bicycle-car-detection.mp4 &
push gate  car-detection.mp4 &
push aisle store-aisle-detection.mp4 &
wait

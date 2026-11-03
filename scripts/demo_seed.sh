#!/usr/bin/env bash
# Register the demo cameras (idempotent). Run after `docker compose --profile dev up -d --build` with
#   FEED_STREAMS="dock2 aisle gate"   (add "burglary assault" if scripts/fetch_ucf_clips.sh was run)
# The feeder loops the sample videos from data/samples (scripts/fetch_assets.sh) as RTSP streams; this script adds them as cameras.
# The gate camera gets an example speed setup (horizontal lines, 15 m apart): the distance is a placeholder, not a field measurement.
set -euo pipefail
cd "$(dirname "$0")/.."
psql() { docker compose exec -T db psql -U eyeai -d eyeai -v ON_ERROR_STOP=1 -q "$@"; }

psql <<'SQL'
INSERT INTO sources (code, name, url, kind, labels, min_confidence, config) VALUES
  ('DEMO-01', 'Dermaga (demo)', 'rtsp://mediamtx:8554/dock2', 'rtsp', '{person,car,truck,bus,motorcycle,bicycle}', 0.40, '{}'),
  ('DEMO-02', 'Lorong toko (demo)', 'rtsp://mediamtx:8554/aisle', 'rtsp', '{person}', 0.40, '{}'),
  ('DEMO-03', 'Gerbang (demo)', 'rtsp://mediamtx:8554/gate', 'rtsp', '{car,truck,bus}', 0.40,
   '{"calibration":{"method":"line","line_axis":"y","line1":0.3,"line2":0.7,"distance_m":15,"speed_limit_kmh":30},"tracker":{"max_age":15}}')
ON CONFLICT (code) DO NOTHING;
SQL

if [ -f data/samples/eval-ucf/Burglary078_x264.mp4 ]; then
psql <<'SQL'
INSERT INTO sources (code, name, url, kind, labels, min_confidence) VALUES
  ('UCF-BURG', 'Uji: pembobolan (UCF-Crime)', 'rtsp://mediamtx:8554/burglary', 'rtsp', '{person,car,truck,motorcycle,bicycle}', 0.35),
  ('UCF-ASLT', 'Uji: penyerangan (UCF-Crime)', 'rtsp://mediamtx:8554/assault', 'rtsp', '{person,car,truck,motorcycle,bicycle}', 0.35)
ON CONFLICT (code) DO NOTHING;
SQL
fi
echo "demo cameras registered; they go online within a few seconds (Pengaturan kamera)"

# Concurrent streams on `car-detection.mp4` (target 5.0 fps/stream, CPU)

| model | streams | total fps | min per-stream fps | sustained | CPU % | RSS MB |
|---|---|---|---|---|---|---|
| yolox_nano | 1 | 40.9 | 40.91 | yes | 397 | 171 |
| yolox_nano | 2 | 50.9 | 25.48 | yes | 658 | 221 |
| yolox_nano | 4 | 39.8 | 9.96 | yes | 679 | 312 |
| yolox_tiny | 1 | 23.5 | 23.52 | yes | 406 | 311 |
| yolox_tiny | 2 | 25.5 | 12.73 | yes | 713 | 364 |
| yolox_tiny | 4 | 17.1 | 4.28 | no | 680 | 480 |
| yolox_s | 1 | 5.8 | 5.85 | yes | 402 | 502 |
| yolox_s | 2 | 6.0 | 2.99 | no | 689 | 593 |
| yolox_s | 4 | 4.5 | 1.11 | no | 698 | 835 |

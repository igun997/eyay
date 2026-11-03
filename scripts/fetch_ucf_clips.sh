#!/usr/bin/env bash
# Optional: two short UCF-Crime clips (a burglary and an assault, real surveillance footage) from a public Hugging Face mirror, for
# showing what the system does and does not recognise. Research data with its own terms of use (non-commercial), so it is NOT in git; read
# https://www.crcv.ucf.edu/projects/real-world/ before using it beyond a private evaluation. The clips show real violence.
set -euo pipefail
cd "$(dirname "$0")/.."
mkdir -p data/samples/eval-ucf
B=https://huggingface.co/datasets/shahadalll/UCF-crime-binary/resolve/main

fetch() { # url dest sha256
  if [[ -f "$2" ]] && echo "$3  $2" | sha256sum -c --quiet 2>/dev/null; then echo "ok   $2"; return; fi
  curl -fL --retry 3 -o "$2" "$1"
  echo "$3  $2" | sha256sum -c --quiet
  echo "got  $2"
}

fetch "$B/data/val/abnormal/Burglary078_x264.mp4"  data/samples/eval-ucf/Burglary078_x264.mp4  779e7b889363141a7385377dc3c155c3a97f1336546ba14eddf9c9a991c4ff6b
fetch "$B/data/train/abnormal/Assault018_x264.mp4" data/samples/eval-ucf/Assault018_x264.mp4  356a959dd9be5eca3b737e21209a73cc0a202e10fa02f22e0a7e142cf9e7013a

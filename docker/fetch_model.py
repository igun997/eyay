"""Download a file and verify its sha256 (used by docker/worker.Dockerfile)."""
import hashlib
import sys
import urllib.request

url, sha, dest = sys.argv[1:4]
data = urllib.request.urlopen(url, timeout=180).read()
got = hashlib.sha256(data).hexdigest()
if got != sha:
    sys.exit(f"sha256 mismatch for {url}: {got}")
open(dest, "wb").write(data)
print(f"ok {dest} {len(data)} bytes")

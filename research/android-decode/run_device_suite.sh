#!/usr/bin/env bash
# Device evidence run for #258: 5 timing repeats per case, then one parity run per case.
# Usage (on the phone, from research/android-decode): ./run_device_suite.sh <repo-root> <out-dir>
# Timing runs never write --dump-gray; parity runs do and are not timed.
set -euo pipefail

REPO=${1:?repo root holding validation/}
OUT=${2:?output directory}
BIN=target/release/openbar-android-decode
SYNTH="$REPO/validation/fixtures/public/synthetic-clean-side-12.mp4"
SQUAT="$REPO/validation/private/media/VID_20261002_075848~2.mp4"   # self-back-squat-side-002
REPEATS=${REPEATS:-5}
mkdir -p "$OUT"

# case-id | video | extra args (window), same windows as phone-runtime-suite-v1.json
run_case() {
  local id=$1 video=$2; shift 2
  for i in $(seq 1 "$REPEATS"); do
    "$BIN" --input "$video" --assume-range limited "$@" --report "$OUT/$id-$i.json"
  done
  python compare.py summarize "$OUT/$id"-[0-9]*.json > "$OUT/$id-summary.json"
  "$BIN" --input "$video" --assume-range limited "$@" \
    --report "$OUT/$id-parity-report.json" --dump-gray "$OUT/$id.gray"
  python compare.py parity --input "$video" --report "$OUT/$id-parity-report.json" \
    --dump "$OUT/$id.gray" --output "$OUT/$id-parity.json" > /dev/null || true
  rm -f "$OUT/$id.gray"
}

{
  echo "model=$(getprop ro.product.model) device=$(getprop ro.product.device) android=$(getprop ro.build.version.release) sdk=$(getprop ro.build.version.sdk)"
  rustc --version
  ffmpeg -version | head -1
  python --version
  sha256sum "$BIN"
} > "$OUT/environment.txt"

run_case synthetic-smoke "$SYNTH"
run_case back-squat-lift "$SQUAT" --start-s 4.8 --end-s 10.8
run_case back-squat-full "$SQUAT"
echo "done: $OUT"

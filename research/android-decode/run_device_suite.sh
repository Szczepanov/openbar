#!/usr/bin/env bash
# Device evidence run for #258: per variant and case, 5 timing repeats, then one parity run.
# Usage (on the phone, from research/android-decode): ./run_device_suite.sh <repo-root> <out-dir>
# Timing runs never write --dump-gray; parity runs do and are not timed.
# VARIANTS selects variants (default: all, in the order below); results go to <out-dir>/<variant>/.
set -euo pipefail

REPO=${1:?repo root holding validation/}
OUT=${2:?output directory}
BIN=target/release/openbar-android-decode
SYNTH="$REPO/validation/fixtures/public/synthetic-clean-side-12.mp4"
SQUAT="$REPO/validation/private/media/VID_20261002_075848~2.mp4"   # self-back-squat-side-002
REPEATS=${REPEATS:-5}

# variant id -> decoder arguments. `packed` is the #258 baseline configuration.
declare -A VARIANT_ARGS=(
  [packed]=""
  [view]="--frame-mode view"
  [view-scan]="--frame-mode view-scan"
  [hints-none]="--frame-mode view --priority none --operating-rate none"
  [realtime]="--frame-mode view --priority 0 --operating-rate none"
  [rate-240]="--frame-mode view --operating-rate 240"
  [image-reader]="--frame-mode view --output-path image-reader"
)
VARIANTS=${VARIANTS:-packed view view-scan hints-none realtime rate-240 image-reader}
for variant in $VARIANTS; do
  [[ -v "VARIANT_ARGS[$variant]" ]] || { echo "unknown variant: $variant" >&2; exit 2; }
done
mkdir -p "$OUT"

# variant | case-id | video | extra args (window), same windows as phone-runtime-suite-v1.json
run_case() {
  local variant=$1 id=$2 video=$3; shift 3
  local dir="$OUT/$variant" reports=() args
  read -r -a args <<< "${VARIANT_ARGS[$variant]}"
  mkdir -p "$dir"
  for i in $(seq 1 "$REPEATS"); do
    "$BIN" --input "$video" --assume-range limited "${args[@]}" "$@" --report "$dir/$id-$i.json"
    reports+=("$dir/$id-$i.json")
  done
  python compare.py summarize "${reports[@]}" > "$dir/$id-summary.json"
  "$BIN" --input "$video" --assume-range limited "${args[@]}" "$@" \
    --report "$dir/$id-parity-report.json" --dump-gray "$dir/$id.gray"
  rm -f "$dir/$id-parity.json"
  # A measured mismatch writes a result; a tool failure must stop the suite.
  python compare.py parity --input "$video" --report "$dir/$id-parity-report.json" \
    --dump "$dir/$id.gray" --output "$dir/$id-parity.json" > /dev/null || {
      local status=$?
      if [ "$status" -ne 1 ] || [ ! -s "$dir/$id-parity.json" ]; then
        return "$status"
      fi
    }
  rm -f "$dir/$id.gray"
}

{
  echo "model=$(getprop ro.product.model) device=$(getprop ro.product.device) android=$(getprop ro.build.version.release) sdk=$(getprop ro.build.version.sdk)"
  rustc --version
  ffmpeg -version | head -1
  python --version
  sha256sum "$BIN"
  echo "variants=$VARIANTS repeats=$REPEATS"
} > "$OUT/environment.txt"

for variant in $VARIANTS; do
  run_case "$variant" synthetic-smoke "$SYNTH"
  run_case "$variant" back-squat-lift "$SQUAT" --start-s 4.8 --end-s 10.8
  run_case "$variant" back-squat-full "$SQUAT"
done
echo "done: $OUT"

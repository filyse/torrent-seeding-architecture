#!/bin/sh
# Ramp synthetic torrents across all engines.
# Usage: bash ramp.sh PER_ENGINE ID_START
PER="${1:-50}"
START="${2:-0}"
for n in 1 2 3 4 5 6; do
  off=$((n * 1000000 + START))
  printf 'b%s off=%s -> ' "$n" "$off"
  docker exec "containerd-engine-b${n}-1" python3 /tmp/synth_gen.py "$PER" "$off"
done

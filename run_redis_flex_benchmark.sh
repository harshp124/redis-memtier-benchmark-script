#!/usr/bin/env bash
set -euo pipefail

###############################################################################
# Redis Flex Benchmark Runner
#
# Edit only the configuration section below, save the file, then run:
#   bash run_redis_flex_benchmark.sh
#
# Optional:
#   DRY_RUN=true bash run_redis_flex_benchmark.sh
###############################################################################

#######################################
# User Configuration
#######################################

# Redis connection
# Hostname or IP address of the Redis endpoint to benchmark.
REDIS_HOST="127.0.0.1"
REDIS_PORT="6379"
REDIS_PASSWORD=""
REDIS_PROTOCOL="redis"

# Dataset shape
# Prefix applied to every generated key. Later benchmark phases reuse this same namespace.
KEY_PREFIX="flex:"
# Lowest numeric key id used during the full data load.
FULL_KEY_MIN=1
# Highest numeric key id used during the full data load.
FULL_KEY_MAX=75000000
# Lowest numeric key id used during warmup and benchmark phases.
SUBSET_KEY_MIN=1
# Highest numeric key id used during warmup and benchmark phases.
SUBSET_KEY_MAX=12500000
# Value payload size in bytes written by memtier for generated objects.
DATA_SIZE_BYTES=4096

# Load phase
# Number of memtier worker threads used only during the initial write-only load.
LOAD_THREADS=8
# Number of connections opened per load-phase thread.
LOAD_CLIENTS=50
# Pipeline depth used during the load phase to ingest data faster.
LOAD_PIPELINE=32

# Warmup phase
# If true, touch the focused subset before measured benchmarks begin.
ENABLE_WARMUP=true
# Number of memtier worker threads used during warmup.
WARMUP_THREADS=4
# Number of connections opened per warmup thread.
WARMUP_CLIENTS=20
# Pipeline depth used during warmup.
WARMUP_PIPELINE=8

# Benchmark phases
# Number of memtier worker threads used in saturation and rate-limited benchmark runs.
BENCHMARK_THREADS=4
# Number of connections opened per benchmark thread.
BENCHMARK_CLIENTS=50
# Pipeline depth used during measured benchmark runs. Keep at 1 for latency-focused testing.
BENCHMARK_PIPELINE=1
# Duration in seconds for each measured benchmark run.
BENCHMARK_TIME_SECONDS=300
# Latency percentile columns to request in memtier's summary table.
PERCENTILES="50,80,90,95,99,99.9"

# Workload ratios (write:read)
# Write:read ratio for the read-heavy workload.
READ_HEAVY_RATIO="1:19"
# Write:read ratio for the mixed workload.
MIXED_RATIO="1:4"
# Write:read ratio for the write-heavy workload.
WRITE_HEAVY_RATIO="4:1"

# Controlled-load ladder.
# Approx offered load = BENCHMARK_THREADS * BENCHMARK_CLIENTS * rate_limit
# Per-connection rate-limit values used to build the controlled-load benchmark ladder.
RATE_LIMITS=(50 100 150 200)

# File layout
# Name of the output directory created under the current working directory for this benchmark campaign.
OUTPUT_DIR_NAME="flex_benchmark_$(date +%Y%m%d_%H%M%S)"

#######################################
# Internal helpers
#######################################

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
RUN_DIR="${PWD}/${OUTPUT_DIR_NAME}"
DRY_RUN="${DRY_RUN:-false}"

auth_args=()
if [[ -n "${REDIS_PASSWORD}" ]]; then
  auth_args+=(--authenticate="${REDIS_PASSWORD}")
fi

log() {
  printf '[%s] %s\n' "$(date +%H:%M:%S)" "$*"
}

run_cmd() {
  local description="$1"
  shift

  log "${description}"
  if [[ "${DRY_RUN}" == "true" ]]; then
    printf 'DRY RUN:'
    printf ' %q' "$@"
    printf '\n'
    return 0
  fi

  "$@"
}

require_binary() {
  local name="$1"
  if ! command -v "${name}" >/dev/null 2>&1; then
    echo "Required binary not found: ${name}" >&2
    exit 1
  fi
}

rate_label() {
  local rate="$1"
  local offered=$(( BENCHMARK_THREADS * BENCHMARK_CLIENTS * rate ))
  if (( offered % 1000 == 0 )); then
    printf '%sk' "$(( offered / 1000 ))"
  else
    printf '%s' "${offered}"
  fi
}

run_memtier_to_file() {
  local description="$1"
  local raw_file="$2"
  local hdr_prefix="$3"
  shift 3

  local -a cmd=(
    memtier_benchmark
    -s "${REDIS_HOST}"
    -p "${REDIS_PORT}"
    --protocol="${REDIS_PROTOCOL}"
    "$@"
  )

  if (( ${#auth_args[@]} > 0 )); then
    cmd+=( "${auth_args[@]}" )
  fi

  if [[ "${DRY_RUN}" == "true" ]]; then
    run_cmd "${description}" "${cmd[@]}"
    return 0
  fi

  run_cmd "${description}" bash -lc "$(printf '%q ' "${cmd[@]}") | tee $(printf '%q' "${raw_file}")"

  if [[ -n "${hdr_prefix}" ]]; then
    local hdr_found
    hdr_found="$(find "${RUN_DIR}" -maxdepth 1 -type f -name "$(basename "${hdr_prefix}")*" | wc -l | tr -d ' ')"
    log "Generated HDR-related files: ${hdr_found}"
  fi
}

#######################################
# Validation
#######################################

require_binary memtier_benchmark
require_binary python3

mkdir -p "${RUN_DIR}"

cat > "${RUN_DIR}/run_config.txt" <<EOF
REDIS_HOST=${REDIS_HOST}
REDIS_PORT=${REDIS_PORT}
REDIS_PROTOCOL=${REDIS_PROTOCOL}
KEY_PREFIX=${KEY_PREFIX}
FULL_KEY_MIN=${FULL_KEY_MIN}
FULL_KEY_MAX=${FULL_KEY_MAX}
SUBSET_KEY_MIN=${SUBSET_KEY_MIN}
SUBSET_KEY_MAX=${SUBSET_KEY_MAX}
DATA_SIZE_BYTES=${DATA_SIZE_BYTES}
LOAD_THREADS=${LOAD_THREADS}
LOAD_CLIENTS=${LOAD_CLIENTS}
LOAD_PIPELINE=${LOAD_PIPELINE}
ENABLE_WARMUP=${ENABLE_WARMUP}
WARMUP_THREADS=${WARMUP_THREADS}
WARMUP_CLIENTS=${WARMUP_CLIENTS}
WARMUP_PIPELINE=${WARMUP_PIPELINE}
BENCHMARK_THREADS=${BENCHMARK_THREADS}
BENCHMARK_CLIENTS=${BENCHMARK_CLIENTS}
BENCHMARK_PIPELINE=${BENCHMARK_PIPELINE}
BENCHMARK_TIME_SECONDS=${BENCHMARK_TIME_SECONDS}
PERCENTILES=${PERCENTILES}
READ_HEAVY_RATIO=${READ_HEAVY_RATIO}
MIXED_RATIO=${MIXED_RATIO}
WRITE_HEAVY_RATIO=${WRITE_HEAVY_RATIO}
RATE_LIMITS=${RATE_LIMITS[*]}
OUTPUT_DIR_NAME=${OUTPUT_DIR_NAME}
DRY_RUN=${DRY_RUN}
EOF

log "Output directory: ${RUN_DIR}"
log "Configuration snapshot: ${RUN_DIR}/run_config.txt"

#######################################
# Phase 1: Full load
#######################################

run_memtier_to_file \
  "Phase 1: load full dataset" \
  "${RUN_DIR}/01_load_full.raw.txt" \
  "" \
  -t "${LOAD_THREADS}" \
  -c "${LOAD_CLIENTS}" \
  --pipeline="${LOAD_PIPELINE}" \
  --ratio=1:0 \
  --key-prefix="${KEY_PREFIX}" \
  --key-minimum="${FULL_KEY_MIN}" \
  --key-maximum="${FULL_KEY_MAX}" \
  --key-pattern=P:P \
  --data-size="${DATA_SIZE_BYTES}" \
  --distinct-client-seed \
  --random-data \
  --randomize \
  --requests=allkeys \
  --hide-histogram

#######################################
# Phase 2: Warmup
#######################################

if [[ "${ENABLE_WARMUP}" == "true" ]]; then
  run_memtier_to_file \
    "Phase 2: warmup focused subset" \
    "${RUN_DIR}/02_warmup_subset.raw.txt" \
    "" \
    -t "${WARMUP_THREADS}" \
    -c "${WARMUP_CLIENTS}" \
    --pipeline="${WARMUP_PIPELINE}" \
    --ratio=0:1 \
    --key-prefix="${KEY_PREFIX}" \
    --key-minimum="${SUBSET_KEY_MIN}" \
    --key-maximum="${SUBSET_KEY_MAX}" \
    --key-pattern=R:R \
    --data-size="${DATA_SIZE_BYTES}" \
    --requests=allkeys \
    --hide-histogram
else
  log "Phase 2: warmup skipped"
fi

#######################################
# Phase 3: Saturation benchmarks
#######################################

run_memtier_to_file \
  "Phase 3.1: read-heavy saturation" \
  "${RUN_DIR}/03_readheavy_saturation.raw.txt" \
  "${RUN_DIR}/03_readheavy_saturation" \
  -t "${BENCHMARK_THREADS}" \
  -c "${BENCHMARK_CLIENTS}" \
  --pipeline="${BENCHMARK_PIPELINE}" \
  --test-time="${BENCHMARK_TIME_SECONDS}" \
  --ratio="${READ_HEAVY_RATIO}" \
  --key-prefix="${KEY_PREFIX}" \
  --key-minimum="${SUBSET_KEY_MIN}" \
  --key-maximum="${SUBSET_KEY_MAX}" \
  --key-pattern=R:R \
  --data-size="${DATA_SIZE_BYTES}" \
  --print-percentiles="${PERCENTILES}" \
  --hdr-file-prefix="${RUN_DIR}/03_readheavy_saturation"

run_memtier_to_file \
  "Phase 3.2: mixed saturation" \
  "${RUN_DIR}/03_mixed_saturation.raw.txt" \
  "${RUN_DIR}/03_mixed_saturation" \
  -t "${BENCHMARK_THREADS}" \
  -c "${BENCHMARK_CLIENTS}" \
  --pipeline="${BENCHMARK_PIPELINE}" \
  --test-time="${BENCHMARK_TIME_SECONDS}" \
  --ratio="${MIXED_RATIO}" \
  --key-prefix="${KEY_PREFIX}" \
  --key-minimum="${SUBSET_KEY_MIN}" \
  --key-maximum="${SUBSET_KEY_MAX}" \
  --key-pattern=R:R \
  --data-size="${DATA_SIZE_BYTES}" \
  --print-percentiles="${PERCENTILES}" \
  --hdr-file-prefix="${RUN_DIR}/03_mixed_saturation"

run_memtier_to_file \
  "Phase 3.3: write-heavy saturation" \
  "${RUN_DIR}/03_writeheavy_saturation.raw.txt" \
  "${RUN_DIR}/03_writeheavy_saturation" \
  -t "${BENCHMARK_THREADS}" \
  -c "${BENCHMARK_CLIENTS}" \
  --pipeline="${BENCHMARK_PIPELINE}" \
  --test-time="${BENCHMARK_TIME_SECONDS}" \
  --ratio="${WRITE_HEAVY_RATIO}" \
  --key-prefix="${KEY_PREFIX}" \
  --key-minimum="${SUBSET_KEY_MIN}" \
  --key-maximum="${SUBSET_KEY_MAX}" \
  --key-pattern=R:R \
  --data-size="${DATA_SIZE_BYTES}" \
  --print-percentiles="${PERCENTILES}" \
  --hdr-file-prefix="${RUN_DIR}/03_writeheavy_saturation"

#######################################
# Phase 4: Controlled-load benchmarks
#######################################

for rate in "${RATE_LIMITS[@]}"; do
  label="$(rate_label "${rate}")"

  run_memtier_to_file \
    "Phase 4.1: read-heavy controlled load ${label}" \
    "${RUN_DIR}/04_readheavy_ratelimit_${label}.raw.txt" \
    "${RUN_DIR}/04_readheavy_ratelimit_${label}" \
    -t "${BENCHMARK_THREADS}" \
    -c "${BENCHMARK_CLIENTS}" \
    --pipeline="${BENCHMARK_PIPELINE}" \
    --test-time="${BENCHMARK_TIME_SECONDS}" \
    --rate-limiting="${rate}" \
    --ratio="${READ_HEAVY_RATIO}" \
    --key-prefix="${KEY_PREFIX}" \
    --key-minimum="${SUBSET_KEY_MIN}" \
    --key-maximum="${SUBSET_KEY_MAX}" \
    --key-pattern=R:R \
    --data-size="${DATA_SIZE_BYTES}" \
    --print-percentiles="${PERCENTILES}" \
    --hdr-file-prefix="${RUN_DIR}/04_readheavy_ratelimit_${label}"

  run_memtier_to_file \
    "Phase 4.2: mixed controlled load ${label}" \
    "${RUN_DIR}/04_mixed_ratelimit_${label}.raw.txt" \
    "${RUN_DIR}/04_mixed_ratelimit_${label}" \
    -t "${BENCHMARK_THREADS}" \
    -c "${BENCHMARK_CLIENTS}" \
    --pipeline="${BENCHMARK_PIPELINE}" \
    --test-time="${BENCHMARK_TIME_SECONDS}" \
    --rate-limiting="${rate}" \
    --ratio="${MIXED_RATIO}" \
    --key-prefix="${KEY_PREFIX}" \
    --key-minimum="${SUBSET_KEY_MIN}" \
    --key-maximum="${SUBSET_KEY_MAX}" \
    --key-pattern=R:R \
    --data-size="${DATA_SIZE_BYTES}" \
    --print-percentiles="${PERCENTILES}" \
    --hdr-file-prefix="${RUN_DIR}/04_mixed_ratelimit_${label}"

  run_memtier_to_file \
    "Phase 4.3: write-heavy controlled load ${label}" \
    "${RUN_DIR}/04_writeheavy_ratelimit_${label}.raw.txt" \
    "${RUN_DIR}/04_writeheavy_ratelimit_${label}" \
    -t "${BENCHMARK_THREADS}" \
    -c "${BENCHMARK_CLIENTS}" \
    --pipeline="${BENCHMARK_PIPELINE}" \
    --test-time="${BENCHMARK_TIME_SECONDS}" \
    --rate-limiting="${rate}" \
    --ratio="${WRITE_HEAVY_RATIO}" \
    --key-prefix="${KEY_PREFIX}" \
    --key-minimum="${SUBSET_KEY_MIN}" \
    --key-maximum="${SUBSET_KEY_MAX}" \
    --key-pattern=R:R \
    --data-size="${DATA_SIZE_BYTES}" \
    --print-percentiles="${PERCENTILES}" \
    --hdr-file-prefix="${RUN_DIR}/04_writeheavy_ratelimit_${label}"
done

#######################################
# Summary
#######################################

if [[ "${DRY_RUN}" == "true" ]]; then
  log "Dry run completed. No benchmark commands were executed."
else
  if [[ -f "${SCRIPT_DIR}/generate_campaign_report.py" ]]; then
    run_cmd \
      "Generating campaign HTML report" \
      python3 "${SCRIPT_DIR}/generate_campaign_report.py" --run-dir "${RUN_DIR}"
    log "Campaign report: ${RUN_DIR}/index.html"
  fi
  log "Benchmark run completed."
fi

log "All output files are under: ${RUN_DIR}"
log "Share this directory with the analysis team."

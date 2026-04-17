# Redis Flex Benchmark Bundle

This folder is self-contained and includes:

- `run_redis_flex_benchmark.sh`
- `generate_campaign_report.py`
- `memtier-workbench.html`

Use this bundle to:

1. Load synthetic data into Redis Flex with `memtier_benchmark`
2. Run warmup, saturation, and controlled-load tests
3. Automatically generate a tabbed HTML campaign report
4. Open the standalone HTML workbench for command building and output inspection

## Minimum Requirements

The laptop or jump host that runs this bundle needs:

- `bash`
- `python3`
- `memtier_benchmark`
- network access to the Redis Flex endpoint
- Redis host, port, and password if authentication is enabled

## Install memtier_benchmark

Official installation references:

- Redis memtier_benchmark GitHub: [https://github.com/RedisLabs/memtier_benchmark](https://github.com/RedisLabs/memtier_benchmark)
- Redis benchmarking docs: [https://redis.io/docs/latest/operate/rs/clusters/optimize/memtier-benchmark/](https://redis.io/docs/latest/operate/rs/clusters/optimize/memtier-benchmark/)

### macOS

```bash
brew install memtier_benchmark
```

### Debian / Ubuntu

```bash
sudo apt install lsb-release curl gpg
curl -fsSL https://packages.redis.io/gpg | sudo gpg --dearmor -o /usr/share/keyrings/redis-archive-keyring.gpg
echo "deb [signed-by=/usr/share/keyrings/redis-archive-keyring.gpg] https://packages.redis.io/deb $(lsb_release -cs) main" | sudo tee /etc/apt/sources.list.d/redis.list
sudo apt-get update
sudo apt-get install memtier-benchmark
```

## Verify Prerequisites

Run these commands first:

```bash
bash --version
python3 --version
memtier_benchmark --version
```

## Step-by-Step Usage

### 1. Copy this folder to the machine that will run the benchmark

The benchmark client should run from a machine that can reach the Redis Flex endpoint.

### 2. Open the runner script and edit the configuration section

Edit:

- Redis host
- Redis port
- Redis password
- whether to run the initial full data load
- key ranges
- data size
- load, warmup, and benchmark concurrency
- benchmark duration
- workload ratios
- rate limits

File:

- `run_redis_flex_benchmark.sh`

### 3. Run the benchmark script with Bash

Do not run it with Python.

```bash
bash run_redis_flex_benchmark.sh
```

Or:

```bash
chmod +x run_redis_flex_benchmark.sh
./run_redis_flex_benchmark.sh
```

### 4. Wait for the script to finish

The phases stop automatically:

- load phase ends when memtier has covered the configured key range, if `ENABLE_INITIAL_LOAD=true`
- warmup phase ends after the configured warmup duration
- measured benchmarks end after the configured `BENCHMARK_TIME_SECONDS`

You do not need to manually stop the script unless you want to abort early.

If you are rerunning benchmarks against data that is already loaded, set:

```bash
ENABLE_INITIAL_LOAD=false
```

at the top of `run_redis_flex_benchmark.sh` to skip the initial load phase.

Important:

- Only set `ENABLE_INITIAL_LOAD=false` after a known successful full load.
- If a previous load was interrupted, failed, or you are unsure whether it completed, set `ENABLE_INITIAL_LOAD=true` and run the load phase again.
- If you skip the load after a partial load, later benchmark results may be misleading because only part of the expected keyspace may exist.

Warmup behavior:

- Warmup is read-only
- Warmup uses random key access within the configured benchmark subset
- Warmup runs for the configured `WARMUP_TIME_SECONDS` instead of trying to sweep the entire subset

### 5. Collect the generated output folder

The script creates a timestamped output folder in the directory where you run it.

Example:

```text
flex_benchmark_20260415_101530
```

That folder will contain:

- all `*.raw.txt` output files
- all HDR histogram files
- `run_config.txt`
- `campaign_summary.json`
- `index.html`

### 6. Open the generated HTML report

Open:

- `index.html`

inside the generated output folder.

This report includes:

- a summary tab
- one tab per test or sub-test
- KPI cards
- percentile charts
- request latency distribution charts
- parsed output tables
- links to related files

### 7. Optional: use the HTML workbench

Open:

- `memtier-workbench.html`

You can use it to:

- build `memtier_benchmark` commands manually
- inspect a specific raw output file
- inspect HDR histogram text files

## What To Share Back

Share the generated output folder from the run.

That folder contains everything needed for later analysis:

- raw benchmark output
- histogram files
- run configuration snapshot
- generated HTML report

## Notes

- `BENCHMARK_PIPELINE=1` is recommended for latency-focused testing.
- Saturation tests run without `--rate-limiting`.
- Controlled-load tests use the configured `RATE_LIMITS` ladder.
- The report generator is called automatically by the script at the end of a successful run.

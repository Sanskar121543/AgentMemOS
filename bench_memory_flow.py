import asyncio
import aiohttp
import time
import csv
import argparse
import statistics
from pathlib import Path

parser = argparse.ArgumentParser()
parser.add_argument("--writes", type=int, default=1000)
parser.add_argument("--concurrency", type=int, default=50)
parser.add_argument("--output", type=str, default="results/bench.csv")
args = parser.parse_args()

BASE = "http://localhost:8000"

latencies = []
success = 0
fail = 0


async def one_request(session, i):
    global success, fail

    payload = {
        "user_id": "bench",
        "content": f"memory benchmark {i}"
    }

    start = time.perf_counter()

    try:
        async with session.post(
            f"{BASE}/memories",
            json=payload,
            timeout=15
        ) as resp:
            await resp.text()
            end = time.perf_counter()

            latencies.append((end - start) * 1000)

            if 200 <= resp.status < 300:
                success += 1
            else:
                fail += 1

    except:
        end = time.perf_counter()
        latencies.append((end - start) * 1000)
        fail += 1


async def run():
    connector = aiohttp.TCPConnector(limit=0)

    async with aiohttp.ClientSession(connector=connector) as session:
        sem = asyncio.Semaphore(args.concurrency)

        async def wrapped(i):
            async with sem:
                await one_request(session, i)

        tasks = [wrapped(i) for i in range(args.writes)]

        start_total = time.perf_counter()
        await asyncio.gather(*tasks)
        end_total = time.perf_counter()

        return end_total - start_total


total_time = asyncio.run(run())

latencies.sort()

mean = round(statistics.mean(latencies), 2)
p50 = round(statistics.median(latencies), 2)
p95 = round(latencies[int(len(latencies) * 0.95)], 2)
p99 = round(latencies[int(len(latencies) * 0.99)], 2)

throughput = round(success / total_time, 2)

Path("results").mkdir(exist_ok=True)

with open(args.output, "w", newline="") as f:
    writer = csv.writer(f)
    writer.writerow([
        "writes",
        "concurrency",
        "success",
        "fail",
        "mean_ms",
        "p50_ms",
        "p95_ms",
        "p99_ms",
        "throughput_rps"
    ])
    writer.writerow([
        args.writes,
        args.concurrency,
        success,
        fail,
        mean,
        p50,
        p95,
        p99,
        throughput
    ])

print("=== Benchmark Complete ===")
print("Writes:", args.writes)
print("Concurrency:", args.concurrency)
print("Success:", success)
print("Fail:", fail)
print("Mean:", mean, "ms")
print("P95:", p95, "ms")
print("P99:", p99, "ms")
print("Throughput:", throughput, "req/sec")
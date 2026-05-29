import argparse
import asyncio
import csv
import statistics
import time
from pathlib import Path
import aiohttp

parser = argparse.ArgumentParser()
parser.add_argument("--writes", type=int, default=1000)
parser.add_argument("--concurrency", type=int, default=50)
parser.add_argument("--output", type=str, default="results/bench.csv")
args = parser.parse_args()

BASE = "http://localhost:8000"

latencies = []
success = 0
fail = 0

sem = asyncio.Semaphore(args.concurrency)

async def write_memory(session, i):
    global success, fail
    payload = {
        "user_id": "bench",
        "content": f"benchmark memory {i}"
    }

    async with sem:
        start = time.perf_counter()
        try:
            async with session.post(f"{BASE}/memories", json=payload, timeout=10) as r:
                await r.text()
                end = time.perf_counter()
                latencies.append((end - start) * 1000)
                if r.status < 300:
                    success += 1
                else:
                    fail += 1
        except:
            fail += 1

def pct(arr, p):
    arr = sorted(arr)
    idx = int(len(arr) * p / 100)
    idx = min(idx, len(arr)-1)
    return arr[idx]

async def main():
    start_total = time.perf_counter()

    async with aiohttp.ClientSession() as session:
        tasks = [write_memory(session, i) for i in range(args.writes)]
        await asyncio.gather(*tasks)

    end_total = time.perf_counter()

    total_sec = end_total - start_total
    throughput = success / total_sec if total_sec else 0

    mean = statistics.mean(latencies)
    p50 = pct(latencies, 50)
    p95 = pct(latencies, 95)
    p99 = pct(latencies, 99)

    Path("results").mkdir(exist_ok=True)

    with open(args.output, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow([
            "writes","concurrency","success","fail",
            "mean_ms","p50_ms","p95_ms","p99_ms","throughput_req_sec"
        ])
        w.writerow([
            args.writes,args.concurrency,success,fail,
            round(mean,2),round(p50,2),round(p95,2),round(p99,2),
            round(throughput,2)
        ])

    print("=== Benchmark Complete ===")
    print("Writes:", args.writes)
    print("Concurrency:", args.concurrency)
    print("Success:", success)
    print("Fail:", fail)
    print("Mean:", round(mean,2), "ms")
    print("P95:", round(p95,2), "ms")
    print("P99:", round(p99,2), "ms")
    print("Throughput:", round(throughput,2), "req/sec")

asyncio.run(main())
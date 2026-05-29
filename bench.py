# scripts/bench_grpc.py
# Benchmark AgentMemOS real memory path over gRPC (port 50051)

import asyncio
import time
import statistics
import csv
import grpc
import argparse

# CHANGE THESE after checking generated files
import memory_pb2
import memory_pb2_grpc


TARGET = "localhost:50051"


async def one_call(stub, i):
    text = f"benchmark memory {i}"

    req = memory_pb2.WriteMemoryRequest(
        agent_id="bench-agent",
        content=text
    )

    start = time.perf_counter()

    try:
        await stub.WriteMemory(req)
        ok = True
    except Exception:
        ok = False

    end = time.perf_counter()

    return (end - start) * 1000, ok


async def worker(stub, start_i, count):
    out = []
    for i in range(start_i, start_i + count):
        out.append(await one_call(stub, i))
    return out


async def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--writes", type=int, default=1000)
    parser.add_argument("--concurrency", type=int, default=50)
    parser.add_argument("--output", type=str, default="results/grpc.csv")
    args = parser.parse_args()

    writes = args.writes
    concurrency = args.concurrency

    async with grpc.aio.insecure_channel(TARGET) as channel:
        stub = memory_pb2_grpc.MemoryServiceStub(channel)

        batch = writes // concurrency
        extra = writes % concurrency

        tasks = []
        cur = 0

        start_total = time.perf_counter()

        for x in range(concurrency):
            count = batch + (1 if x < extra else 0)
            tasks.append(worker(stub, cur, count))
            cur += count

        results = await asyncio.gather(*tasks)

        end_total = time.perf_counter()

    latencies = []
    success = 0
    fail = 0

    for block in results:
        for ms, ok in block:
            latencies.append(ms)
            if ok:
                success += 1
            else:
                fail += 1

    latencies.sort()

    mean = round(statistics.mean(latencies), 2)
    p95 = round(latencies[int(len(latencies) * 0.95)], 2)
    p99 = round(latencies[int(len(latencies) * 0.99)], 2)

    total_sec = end_total - start_total
    throughput = round(success / total_sec, 2)

    with open(args.output, "w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(
            ["writes", "concurrency", "success", "fail", "mean_ms", "p95_ms", "p99_ms", "throughput_rps"]
        )
        writer.writerow(
            [writes, concurrency, success, fail, mean, p95, p99, throughput]
        )

    print("=== gRPC Benchmark Complete ===")
    print("Writes:", writes)
    print("Concurrency:", concurrency)
    print("Success:", success)
    print("Fail:", fail)
    print("Mean:", mean, "ms")
    print("P95:", p95, "ms")
    print("P99:", p99, "ms")
    print("Throughput:", throughput, "req/sec")


if __name__ == "__main__":
    asyncio.run(main())
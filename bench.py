import time
import asyncio
import aiohttp
import statistics

URL = "http://localhost:8000/metrics"
TOTAL = 500
CONCURRENCY = 10

times = []
success = 0
fail = 0

sem = asyncio.Semaphore(CONCURRENCY)

async def hit(session):
    global success, fail
    async with sem:
        t1 = time.perf_counter()
        try:
            async with session.get(URL) as r:
                await r.read()
                dt = (time.perf_counter() - t1) * 1000
                times.append(dt)
                if r.status == 200:
                    success += 1
                else:
                    fail += 1
        except:
            fail += 1

async def main():
    connector = aiohttp.TCPConnector(limit=CONCURRENCY)
    timeout = aiohttp.ClientTimeout(total=10)

    start = time.perf_counter()

    async with aiohttp.ClientSession(
        connector=connector,
        timeout=timeout
    ) as session:
        await asyncio.gather(*[hit(session) for _ in range(TOTAL)])

    total_time = time.perf_counter() - start

    times.sort()

    print("Completed:", success)
    print("Failed:", fail)
    print("Total seconds:", round(total_time, 2))
    print("Req/sec:", round(success / total_time, 2))

    if times:
        print("Average ms:", round(statistics.mean(times), 2))
        print("Median ms:", round(statistics.median(times), 2))
        print("P95 ms:", round(times[int(len(times)*0.95)], 2))
        print("P99 ms:", round(times[int(len(times)*0.99)], 2))
        print("Max ms:", round(max(times), 2))

asyncio.run(main())
"""
Benchmark: HeapQueue vs SkipListQueue

Tests the two priority queue implementations on:
  - Push throughput (single-threaded, various sizes)
  - Pop throughput (separate from push)
  - Push+Pop interleaved (realistic workload)
  - Mixed priority distribution
  - Ordering correctness
  - Peek performance

Run:
    cd backend && python -m tests.benchmark_scheduler
"""

import time
import gc
import sys
import statistics
from datetime import datetime, timedelta
from typing import Callable

sys.path.insert(0, ".")

from backend.models import Job
from backend.scheduler.heap_queue import HeapQueue
from backend.scheduler.skip_list_queue import SkipListQueue
from backend.scheduler.broker import JobBroker

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

NOW = datetime(2026, 6, 12, 10, 0, 0)

WARMUP = 3
REPEATS = 7


def _make_job(job_id: int, priority: int = 2,
              scheduled_at: datetime | None = None,
              created_at: datetime | None = None) -> Job:
    return Job(
        id=job_id,
        type="benchmark",
        priority=priority,
        payload="{}",
        scheduled_at=scheduled_at,
        created_at=created_at or NOW,
    )


def _make_jobs(count: int, seed: int = 0) -> list[Job]:
    """Generate `count` jobs with varying priorities and times."""
    rng = _RNG(seed)
    jobs = []
    for i in range(count):
        p = rng.choices([1, 2, 3], weights=[2, 6, 2])[0]
        if rng.random() < 0.3:
            sched = NOW + timedelta(seconds=rng.randint(60, 3600))
        else:
            sched = None
        created = NOW - timedelta(seconds=rng.randint(0, 86400))
        jobs.append(_make_job(i, priority=p, scheduled_at=sched, created_at=created))
    return jobs


class _RNG:
    """Minimal deterministic PRNG so we don't depend on `random` (seeded)."""
    def __init__(self, seed: int):
        self._state = seed
    def random(self) -> float:
        self._state = (self._state * 1103515245 + 12345) & 0x7FFFFFFF
        return self._state / 0x7FFFFFFF
    def randint(self, a: int, b: int) -> int:
        return a + int(self.random() * (b - a + 1))
    def choices(self, population: list, weights: list) -> list:
        total = sum(weights)
        r = self.random() * total
        cumulative = 0.0
        for i, w in enumerate(weights):
            cumulative += w
            if r < cumulative:
                return [population[i]]
        return [population[-1]]


def _time_op(label: str, fn: Callable[[], None]) -> dict:
    """Time `fn()` and return statistics in milliseconds."""
    for _ in range(WARMUP):
        fn()
    gc.collect()
    timings = []
    for _ in range(REPEATS):
        gc.collect()
        t0 = time.perf_counter()
        fn()
        t1 = time.perf_counter()
        timings.append((t1 - t0) * 1000)
    return {
        "label": label,
        "mean_ms": statistics.mean(timings),
        "median_ms": statistics.median(timings),
        "min_ms": min(timings),
        "max_ms": max(timings),
        "stdev_ms": statistics.stdev(timings) if len(timings) > 1 else 0,
    }


def _fmt(r: dict) -> str:
    return (f"  {r['label']:<60s}"
            f"  mean={r['mean_ms']:>9.3f} ms"
            f"  median={r['median_ms']:>9.3f} ms"
            f"  ±{r['stdev_ms']:>6.3f}")


# ---------------------------------------------------------------------------
# Benchmark suites
# ---------------------------------------------------------------------------

def bench_push(queue_factory: Callable[[], object], count: int, label: str) -> dict:
    jobs = _make_jobs(count)
    def run():
        q = queue_factory()
        for j in jobs:
            q.push(j)
    return _time_op(f"{label} push {count:>6} items", run)


def bench_pop(queue_factory: Callable[[], object], count: int, label: str) -> dict:
    """Pop throughput — queue is pre-populated *outside* the timed region."""
    jobs = _make_jobs(count)
    def run():
        q = queue_factory()
        # pre-populate (not timed)
        for j in jobs:
            q.push(j)
        # time only the pops
        t0 = time.perf_counter()
        for _ in range(count):
            q.pop()
        t1 = time.perf_counter()
        # manual capture because _time_op's fn() returns None; we return the delta
        return t1 - t0
    # Warmup & repeats handled manually since we need the push/pop separation
    # (we do full warmup/repeat including push, but only time pop)
    for _ in range(WARMUP):
        run()
    gc.collect()
    timings = []
    for _ in range(REPEATS):
        gc.collect()
        dt = run()
        timings.append(dt * 1000)
    return {
        "label": f"{label} pop  {count:>6} items (push excluded from timing)",
        "mean_ms": statistics.mean(timings),
        "median_ms": statistics.median(timings),
        "min_ms": min(timings),
        "max_ms": max(timings),
        "stdev_ms": statistics.stdev(timings) if len(timings) > 1 else 0,
    }


def bench_push_pop_interleaved(queue_factory: Callable[[], object], count: int, label: str) -> dict:
    """Push and pop in a 1:1 ratio – simulates realistic broker usage."""
    jobs = _make_jobs(count * 2)
    def run():
        q = queue_factory()
        for i in range(count):
            q.push(jobs[2 * i])
            q.push(jobs[2 * i + 1])
            q.pop()
    return _time_op(f"{label} interleaved push/pop {count:>4} pairs", run)


def bench_ordering(queue_factory: type, count: int, label: str) -> dict:
    """Push many jobs and verify correct ordering on pop."""
    jobs = _make_jobs(count)
    q = queue_factory()
    for j in jobs:
        q.push(j)

    popped = []
    while True:
        j = q.pop()
        if j is None:
            break
        popped.append(j)

    errors = 0
    for a, b in zip(popped, popped[1:]):
        a_key = (a.priority, a.scheduled_at or datetime.min, a.created_at, a.id)
        b_key = (b.priority, b.scheduled_at or datetime.min, b.created_at, b.id)
        if a_key > b_key:
            errors += 1

    return {
        "label": f"{label} ordering check ({count} items)",
        "result": "PASS" if errors == 0 else f"FAIL ({errors} inversion(s))",
    }


def bench_peek(queue_factory: Callable[[], object], count: int, label: str) -> dict:
    jobs = _make_jobs(count)
    def run():
        q = queue_factory()
        for j in jobs:
            q.push(j)
        for _ in range(1000):
            _ = q.peek()
    return _time_op(f"{label} peek 1000x ({count} items)", run)


def bench_mixed_workload(queue_factory: Callable[[], object], count: int, label: str) -> dict:
    """
    Simulate the broker's usage pattern:
      - Push many jobs
      - Pop some, then push more, pop some, etc.
    """
    jobs = _make_jobs(count * 2)
    def run():
        q = queue_factory()
        for j in jobs[:count]:
            q.push(j)
        idx = count
        for _ in range(count // 10):
            for _ in range(5):
                if idx < len(jobs):
                    q.push(jobs[idx])
                    idx += 1
            for _ in range(5):
                q.pop()
    return _time_op(f"{label} mixed workload ({count} initial + {count//10} batches)", run)


# ---------------------------------------------------------------------------
# Scale tests
# ---------------------------------------------------------------------------

SCALES = [100, 1_000, 10_000]
LARGE_SCALES = [100_000]


def run_all():
    print("=" * 80)
    print("  HeapQueue  vs  SkipListQueue  –  Benchmark")
    print("=" * 80)
    print()

    results = []

    # --- Push throughput ---
    print("── Push throughput ──────────────────────────────────────────────")
    for n in SCALES:
        for label, factory in [("HeapQueue", HeapQueue), ("SkipListQueue", SkipListQueue)]:
            r = bench_push(lambda f=factory: f(), n, label)
            results.append(r)
            print(_fmt(r))
    for n in LARGE_SCALES:
        for label, factory in [("HeapQueue", HeapQueue), ("SkipListQueue", SkipListQueue)]:
            r = bench_push(lambda f=factory: f(), n, label)
            results.append(r)
            print(_fmt(r))
    print()

    # --- Pop throughput ---
    print("── Pop throughput ───────────────────────────────────────────────")
    for n in SCALES:
        for label, factory in [("HeapQueue", HeapQueue), ("SkipListQueue", SkipListQueue)]:
            r = bench_pop(lambda f=factory: f(), n, label)
            results.append(r)
            print(_fmt(r))
    for n in LARGE_SCALES:
        for label, factory in [("HeapQueue", HeapQueue), ("SkipListQueue", SkipListQueue)]:
            r = bench_pop(lambda f=factory: f(), n, label)
            results.append(r)
            print(_fmt(r))
    print()

    # --- Interleaved push/pop ---
    print("── Interleaved push/pop ─────────────────────────────────────────")
    for n in [100, 1_000, 10_000]:
        for label, factory in [("HeapQueue", HeapQueue), ("SkipListQueue", SkipListQueue)]:
            r = bench_push_pop_interleaved(lambda f=factory: f(), n, label)
            results.append(r)
            print(_fmt(r))
    print()

    # --- Peek ---
    print("── Peek ─────────────────────────────────────────────────────────")
    for n in [100, 1_000]:
        for label, factory in [("HeapQueue", HeapQueue), ("SkipListQueue", SkipListQueue)]:
            r = bench_peek(lambda f=factory: f(), n, label)
            results.append(r)
            print(_fmt(r))
    print()

    # --- Mixed workload ---
    print("── Mixed workload (broker-like) ─────────────────────────────────")
    for n in [100, 1_000]:
        for label, factory in [("HeapQueue", HeapQueue), ("SkipListQueue", SkipListQueue)]:
            r = bench_mixed_workload(lambda f=factory: f(), n, label)
            results.append(r)
            print(_fmt(r))
    print()

    # --- Ordering correctness ---
    print("── Ordering Correctness ─────────────────────────────────────────")
    for n in [100, 1_000, 10_000]:
        for label, factory in [("HeapQueue", HeapQueue), ("SkipListQueue", SkipListQueue)]:
            r = bench_ordering(factory, n, label)
            results.append(r)
            print(f"  {r['label']:<65s}  {r['result']}")
    print()

    # --- Summary: speedup factor ---
    print("── Summary: Speedup (SkipList / Heap, >1 = SkipList slower) ─────")
    for n in SCALES + LARGE_SCALES:
        h_push = [r for r in results if r["label"].startswith("HeapQueue") and f"push {n:>6}" in r["label"]]
        s_push = [r for r in results if r["label"].startswith("SkipList") and f"push {n:>6}" in r["label"]]
        if h_push and s_push:
            ratio = s_push[0]["mean_ms"] / h_push[0]["mean_ms"]
            print(f"  Push {n:>6}:  SkipList is {ratio:.2f}x of Heap")

        h_pop = [r for r in results if r["label"].startswith("HeapQueue") and f"pop  {n:>6}" in r["label"]]
        s_pop = [r for r in results if r["label"].startswith("SkipList") and f"pop  {n:>6}" in r["label"]]
        if h_pop and s_pop:
            ratio = s_pop[0]["mean_ms"] / h_pop[0]["mean_ms"]
            print(f"  Pop  {n:>6}:  SkipList is {ratio:.2f}x of Heap")
    print()

    # --- Big table ---
    # Only timing results (skip ordering results which have no 'mean_ms')
    timing_results = [r for r in results if "mean_ms" in r]
    print("── Result table ─────────────────────────────────────────────────")
    header = f"{'Test':<62s} {'Mean (ms)':>10s}  {'Median (ms)':>10s}  {'±Stdev':>7s}"
    print(header)
    print("-" * len(header))
    for r in timing_results:
        print(f"{r['label']:<62s} {r['mean_ms']:>10.3f}  {r['median_ms']:>10.3f}  {r['stdev_ms']:>7.3f}")
    print()

    print("=" * 80)
    print("  Done.")
    print("=" * 80)


if __name__ == "__main__":
    run_all()

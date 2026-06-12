# Benchmark of HeapQueue and SkipList

**Push performance:**
- HeapQueue is consistently faster for pushes
- At 100k items: Heap 370ms vs SkipList 1741ms (SkipList is 4.7x slower)
- The gap grows with queue size

**Pop performance:**
- SkipListQueue beats HeapQueue for pops at scale
- At 100k items: Heap 621ms vs SkipList 393ms (SkipList is 0.63x, i.e., 37% faster)
- At smaller sizes, they're comparable

**Interleaved (push+pop mixed):**
- HeapQueue is faster across the board (about 2x)
- This makes sense because in an interleaved workload, the number of pushes (which HeapQueue is better at) equals the number of pops

**Peek:**
- SkipListQueue peek involves lock acquisition and going through the header's forward[0], while HeapQueue peek is just self._heap[0] with lock. Both are O(1) but SkipList has slightly more overhead.

**Ordering:**
- Both produce correct ordering (PASS for all sizes)

**Tradeoffs analysis:**

1. **Push complexity**: HeapQueue push is O(log n) with a simple `heapq.heappush`. Skip list push involves generating a random level, building an update array, and potentially re-linking multiple levels. This makes SkipList push slower, especially at scale (4.7x at 100k).

2. **Pop complexity**: Both are O(log n) on average, but:
   - HeapQueue pop requires `heapq.heappop` which involves sifting down the last element — this is O(log n) with array operations
   - SkipList pop just removes the head node and updates pointers — this is O(1) on average (just updating the header's forward pointers)
   - This explains why SkipList pop is faster at scale

3. **Memory**: 
   - HeapQueue: O(n) for the array, very compact
   - SkipListQueue: O(n log n) average due to multiple forward pointers per node. Each node has a forward array.

4. **Thread safety**: Both use threading.Lock, similar contention

5. **Remove operation**: 
   - HeapQueue remove is O(n) — rebuild the whole heap
   - SkipListQueue remove is theoretically O(log n) but the current implementation has a bug (it doesn't actually remove)

6. **Cache locality**: HeapQueue's array-based storage has excellent cache locality. Skip list's linked structure has poor cache locality (nodes scattered in memory).

7. **Predictability**: Heap operations are deterministic. Skip list operations have probabilistic behavior due to random levels.


---

## Benchmark Results

### Push Throughput (lower is better)

| Size | HeapQueue | SkipListQueue | Ratio (SL/Heap) |
|------|-----------|---------------|-----------------|
| 100  | 0.68 ms   | 1.13 ms       | **1.66x** |
| 1K   | 4.66 ms   | 12.27 ms      | **2.63x** |
| 10K  | 38.35 ms  | 137.98 ms     | **3.60x** |
| 100K | 370.47 ms | 1740.92 ms    | **4.70x** |

**Heap wins push** — the gap grows with scale because skip list pushes involve random-level generation & multi-level pointer traversal, while `heapq.heappush` is a tight O(log n) array operation with great cache locality.

### Pop Throughput (push excluded from timing)

| Size | HeapQueue | SkipListQueue | Ratio (SL/Heap) |
|------|-----------|---------------|-----------------|
| 100  | 0.29 ms   | 0.54 ms       | 1.85x |
| 1K   | 3.61 ms   | 3.38 ms       | **0.94x** |
| 10K  | 41.20 ms  | 36.46 ms      | **0.89x** |
| 100K | 621.00 ms | 393.29 ms     | **0.63x** |

**SkipList wins pop** — at 100K items it's **37% faster**. Popping from a skip list is nearly O(1) (just update the head pointer), while heap pop requires sifting down the last element O(log n).

### Interleaved Push/Pop (realistic broker pattern)

| Pairs | HeapQueue | SkipListQueue | Ratio |
|-------|-----------|---------------|-------|
| 100   | 1.30 ms   | 3.06 ms       | 2.35x |
| 1K    | 13.85 ms  | 22.19 ms      | 1.60x |
| 10K   | 124.58 ms | 237.87 ms     | 1.91x |

**Heap wins interleaved** — since pushes and pops are balanced, Heap's faster pushes dominate the total time.

### Mixed Workload & Peek

| Test | HeapQueue | SkipListQueue |
|------|-----------|---------------|
| Mixed 100+10 | 0.98 ms | 2.07 ms |
| Mixed 1K+100 | 9.06 ms | 18.12 ms |
| Peek 1000x (100 items) | 1.31 ms | 1.96 ms |
| Peek 1000x (1K items) | 5.25 ms | 11.83 ms |

**Heap wins** consistently for peek and mixed workloads.

### Ordering Correctness

Both queues **PASS** ordering verification at all scales (100, 1K, 10K items) — identical priority ordering.

---

## Tradeoffs Analysis

| Dimension | HeapQueue | SkipListQueue |
|-----------|-----------|---------------|
| **Push** | 🏆 **Faster** (4.7x at 100K) | Slower — random level generation, pointer updates |
| **Pop** | Slower — O(log n) sift-down | 🏆 **Faster** (37% faster at 100K) |
| **Remove** | O(n) — rebuilds entire heap | O(log n) avg *theoretically* (⚠️ current impl is broken — has `pass`) |
| **Memory** | 🏆 Compact — one Python list | Larger — each node stores a forward-pointer array |
| **Cache locality** | 🏆 Excellent — dense array | Poor — nodes scattered in heap memory |
| **Determinism** | 🏆 Deterministic O(log n) | Probabilistic — random levels |
| **Concurrency** | Lock per operation (same) | Lock per operation (same) |
| **Implementation** | ~40 lines, Python stdlib | ~95 lines, custom data structure |

### When to use each

- **Use HeapQueue if**: Your workload is write-heavy (more pushes than pops), you care about memory, or you need predictable deterministic performance. It's the simpler, more battle-tested choice.
- **Use SkipListQueue if**: Your workload is read-heavy (many pops, few pushes), or you need efficient `remove()` (once the bug is fixed). The pop advantage compounds at scale.

---

The benchmark script is at **`backend/tests/benchmark_scheduler.py`** — you can re-run it anytime with `python3 -m backend.tests.benchmark_scheduler`.
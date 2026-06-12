# Dilamme Job Scheduler — Interview Preparation Guide

> Use this document to walk through every requirement, explain your implementation decisions,
> and demonstrate deep understanding of the algorithms, tradeoffs, and system design.

---

## Table of Contents

1. [System Overview](#1-system-overview)
2. [The Requirements — Mapped to Code](#2-the-requirements--mapped-to-code)
3. [Algorithms Deep Dive](#3-algorithms-deep-dive)
4. [Design Decisions & Tradeoffs](#4-design-decisions--tradeoffs)
5. [Scenarios & Edge Cases](#5-scenarios--edge-cases)
6. [Potential Interview Questions](#6-potential-interview-questions)
7. [Architecture Diagram (Mental Model)](#7-architecture-diagram-mental-model)

---

## 1. System Overview

**One-liner:** A background job scheduler with priority queuing, DAG dependency resolution, automated retries with exponential backoff, a dead-letter queue, starvation prevention, and a React dashboard — all running on a single server with Nginx reverse proxy.

### The Three Processes

```
┌─────────────────────────────────────────────────────────────────────┐
│                          Nginx (Reverse Proxy)                      │
│   ┌───────────────┐  ┌─────────────────┐  ┌────────────────────┐   │
│   │  Static Files  │  │  /docs, /redoc  │  │  /api/* → backend  │   │
│   │  (React SPA)   │  │  (Swagger UI)   │  │  /internal/*       │   │
│   └───────────────┘  └─────────────────┘  └────────────────────┘   │
└──────────────────────────────┬──────────────────────────────────────┘
                               │
┌──────────────────────────────▼──────────────────────────────────────┐
│                   FastAPI Backend (uvicorn :8000)                    │
│                                                                     │
│   ┌──────────────┐  ┌──────────────────┐  ┌────────────────────┐   │
│   │  Public API   │  │   Internal API   │  │    Job Broker      │   │
│   │  POST /jobs   │  │  POST /internal │  │  (background sync  │   │
│   │  GET /jobs    │  │  /jobs/pop      │  │   thread daemon)   │   │
│   │  POST /cancel │  │  PATCH /jobs/   │  │                    │   │
│   │  POST /retry   │  │  {id}/status   │  │  Heap/SkipList     │   │
│   └──────────────┘  └──────────────────┘  └────────────────────┘   │
└──────────────────────────────┬──────────────────────────────────────┘
                               │
          ┌────────────────────┼────────────────────┐
          ▼                    ▼                    ▼
   ┌──────────────┐   ┌──────────────┐   ┌──────────────────┐
   │   SQLite DB   │   │  Job Broker  │   │  Background      │
   │   (jobs,      │   │  Queue       │   │  Worker Process  │
   │    job_logs,  │   │  (in-memory) │   │  (HTTP polling)  │
   │    job_deps)  │   └──────────────┘   └──────────────────┘
   └──────────────┘
```

**Key point for interview:** The **broker** and the **worker** are separate:
- The **broker** (inside the API process) syncs the database to the in-memory priority queue every 2 seconds.
- The **worker** (separate process) HTTP-polls the API's `/internal/jobs/pop` endpoint to get the next job.

This architecture means the worker never directly touches the database — it communicates through the API, which prevents split-brain scenarios.

---

## 2. The Requirements — Mapped to Code

### 2.1 Job Model

**Requirement:** Each job has a type, payload, priority (1=High, 2=Medium, 3=Low), scheduled time, and optional recurring interval.

**Implementation:** `backend/models.py` — `Job` class (SQLAlchemy model)

```python
class Job(Base):
    __tablename__ = "jobs"
    id = Column(Integer, primary_key=True)
    type = Column(String(50), nullable=False)         # e.g., "send_email"
    priority = Column(Integer, default=2)              # 1=High, 2=Medium, 3=Low
    status = Column(String(20), default="pending")     # lifecycle state
    payload = Column(Text, nullable=True)               # JSON string
    retry_count = Column(Integer, default=0)
    scheduled_at = Column(DateTime, nullable=True)      # future scheduling
    interval = Column(String(50), nullable=True)        # "every_1_minute", etc.
    created_at = Column(DateTime, default=datetime.utcnow)
    started_at = Column(DateTime, nullable=True)        # for timeout detection
    finished_at = Column(DateTime, nullable=True)
```

**What to say in interview:** "I used SQLAlchemy ORM with a `jobs` table. The status field drives the entire state machine. I added `started_at` specifically to detect stuck processing jobs — if a worker crashes, the broker recovers the job after 10 minutes."

---

### 2.2 Job Status Lifecycle

**Requirement:** `pending → processing → completed / failed / cancelled`

**Implementation:** Status transitions happen in:
- `backend/api.py:pop_job()` — `pending → processing` (when worker pops a job)
- `backend/api.py:update_job_status()` — `processing → completed` or `processing → failed`
- `backend/api.py:cancel_job()` — `pending/processing → cancelled`
- `backend/scheduler/broker.py:_sync_jobs()` — `processing → pending` (recovery of stuck jobs)

**Cancellation decision (document this in the interview!):** If a job is *already processing* when cancelled, the API checks cancellation status in `update_job_status()`. If the job was cancelled mid-processing, the worker's status update is ignored — the job stays cancelled.

```python
# backend/api.py, line ~120
if db_job.status == "cancelled":
    return {"status": "cancelled"}  # Respect cancellation, ignore worker's update
```

**What to say:** "I made a explicit design choice: cancellation is final. Even if the worker completes processing, a cancelled job stays cancelled. The worker's result is discarded. This avoids race conditions between the user hitting cancel and the worker finishing."

---

### 2.3 Worker

**Requirement:** Workers run independently from the main app. They poll for jobs, process them, and update statuses. The main application does not wait.

**Implementation:** `backend/worker.py` — standalone process that loops:

```python
# Pseudocode of the worker loop
while True:
    resp = requests.post(f"{API_URL}/internal/jobs/pop")
    if resp.status_code == 200 and job:
        try:
            mock_email_handler(job)       # Execute the handler
            requests.patch(f"{API_URL}/internal/jobs/{id}/status",
                          json={"status": "completed", "message": "..."})
        except Exception as e:
            # Handle retry logic (see section 2.5)
    else:
        time.sleep(1)  # No job available, wait
```

**Key design decisions:**
1. **HTTP polling** (not a message queue like Redis/RabbitMQ) — keeps infrastructure minimal. No additional services to manage.
2. **Stateless workers** — any worker can pick up any job. The API is the single source of truth.
3. **Duplicate protection** is handled by the API's `pop_job()` endpoint, which checks `status == "pending"` before assigning.

---

### 2.4 Job Handler (Email Simulation)

**Requirement:** Implement one working handler. Mock the external service but execute real logic.

**Implementation:** `backend/worker.py:mock_email_handler()`

```python
def mock_email_handler(job):
    payload = json.loads(job.get('payload', '{}'))
    time.sleep(random.uniform(0.5, 1.5))  # Simulate network/processing time
    
    # Real logic: validate payload, simulate failure conditions
    if payload.get("force_fail") or (payload.get("random_fail") and random.random() < 0.5):
        raise Exception("Mock email delivery failed")
```

**What to say:** "I didn't just return 200. The handler actually parses the payload, simulates processing time, and has configurable failure modes — `force_fail` for testing and `random_fail` with 50% probability. This made testing retries and the DLQ much easier."

---

### 2.5 Retries with Exponential Backoff & Jitter

**Requirement:** Failed jobs retry automatically up to 3 times. Backoff: ~1s, ~5s, ~25s.

**Implementation:** `backend/worker.py:calculate_backoff()`

```python
def calculate_backoff(retry_count):
    base_delay = 5 ** retry_count if retry_count > 0 else 1
    jitter = base_delay * 0.2  # ±20% random jitter
    return base_delay + random.uniform(-jitter, jitter)
```

| Attempt | Base Formula | Base Delay | With Jitter (±20%) |
|---------|-------------|-----------|-------------------|
| 0       | 1           | ~1s       | 0.8s – 1.2s       |
| 1       | 5^1 = 5     | ~5s       | 4.0s – 6.0s       |
| 2       | 5^2 = 25    | ~25s      | 20s – 30s          |

**Retry flow in worker:**
1. Worker attempts to process → exception thrown
2. Worker checks `retry_count` (how many times it's already failed)
3. If `retry_count < 3`: calculates backoff, sets status to `pending` with a future `scheduled_at`
4. If `retry_count >= 3`: sets status to `failed` (moves to DLQ)

**What to say:** "The jitter prevents the thundering herd problem — if 100 jobs fail simultaneously, they don't all retry at the same time. The exponential backoff gives the system time to recover before retrying."

---

### 2.6 Dead-Letter Queue (DLQ)

**Requirement:** Jobs that exhaust retries land here. Engineers can view, investigate, and manually retry. Alerts fire when threshold is crossed.

**Implementation:**
- **Storage:** DLQ is implicit — jobs with `status = "failed"` and `retry_count >= 3` are considered DLQ entries. They remain in the `jobs` table with their error logs.
- **Viewing:** Frontend's DLQ tab (`activeTab === 'dlq'`) filters `jobs.filter(j => j.status === 'failed' && j.retry_count >= 3)`
- **Manual Retry:** `POST /jobs/{id}/retry` resets `status = "pending"` and `retry_count = 0`
- **Threshold Alerting:** In `backend/api.py:update_job_status()`:

```python
if body.status == "failed" and db_job.retry_count >= 3:
    failed_count = db.query(Job).filter(Job.status == "failed", Job.retry_count >= 3).count()
    if failed_count >= 10:  # Threshold
        logger.warning("ALERT: DLQ threshold reached! Over 10 failed jobs.")
```

**Documented threshold:** **10 failed jobs** triggers the alert.

**What to say:** "I chose to use the database as the DLQ rather than a separate table. This simplifies the architecture — the DLQ is just a query filter. The `retry_count >= 3` condition is the DLQ membership criterion. I log a system alert at threshold 10."

---

### 2.7 Scheduled Jobs

**Requirement:** Jobs with a future `scheduled_at` don't run until that time.

**Implementation:** In `backend/scheduler/broker.py:_sync_jobs()`:

```python
if job.scheduled_at and job.scheduled_at > now:
    continue  # Not due yet — skip
```

The broker syncs every 2 seconds. When a job's `scheduled_at` passes, it gets picked up in the next sync cycle and pushed to the priority queue.

---

### 2.8 Recurring Jobs

**Requirement:** When a recurring job completes, the next run schedules itself automatically.

**Implementation:** In `backend/api.py:update_job_status()`:

```python
if body.status == "completed" and db_job.interval:
    delta = timedelta(minutes=1)
    if db_job.interval == "every_5_minutes":
        delta = timedelta(minutes=5)
    elif db_job.interval == "every_1_hour":
        delta = timedelta(hours=1)
    
    next_job = Job(
        type=db_job.type, priority=db_job.priority, payload=db_job.payload,
        scheduled_at=datetime.utcnow() + delta, interval=db_job.interval
    )
    db.add(next_job)
```

**What to say:** "Recurring jobs create a new job instance on completion, not a loop on one job. This means each occurrence has its own ID, its own logs, and its own lifecycle. You can cancel a single occurrence without affecting future ones."

---

### 2.9 Cancellation

**Requirement:** Cancelled jobs don't get processed. If a job is already processing when cancelled, handle it and document.

**Implementation:** Two layers of protection:

1. **Broker layer** — `pop_job()` checks `status == "pending"` before returning the job
2. **Worker status update** — `update_job_status()` checks if job was cancelled and ignores the update:

```python
if db_job.status == "cancelled":
    return {"status": "cancelled"}  # Cancellation wins
```

**Documented decision:** **Cancellation is final.** Even if a worker finishes processing, the cancellation takes precedence.

---

### 2.10 Live Updates (Polling)

**Requirement:** UI reflects status changes without page refresh.

**Implementation:** `frontend/src/App.tsx`:

```typescript
useEffect(() => {
    fetchJobs();
    const interval = setInterval(fetchJobs, 3000);
    return () => clearInterval(interval);
}, []);
```

**Why polling over SSE/WebSockets:** "Polling is simpler to implement and debug. For a single-server setup with moderate traffic, it's sufficient. The 3-second interval provides near-real-time updates without overwhelming the server."

---

### 2.11 Duplicate Protection

**Requirement:** One job cannot be picked up by two workers.

**Implementation:** The API's `pop_job()` endpoint does an atomic check-and-update:

```python
db_job = db.query(Job).filter(Job.id == job.id).first()
if not db_job or db_job.status != "pending":
    return {"job": None}  # Already taken or cancelled

db_job.status = "processing"  # Atomically claim the job
db_job.started_at = datetime.utcnow()
db.commit()
```

**What to say:** "The database is the lock. When a worker pops a job, the status goes to `processing` in the same transaction. Even if two workers poll simultaneously, SQLAlchemy's session isolation ensures only one gets the `pending` job."

---

### 2.12 Starvation Prevention

**Requirement:** Low-priority jobs cannot wait forever. The longer a job sits, the higher its effective priority.

**Implementation:** `backend/scheduler/broker.py:_sync_jobs()`

```python
starvation_threshold = now - timedelta(seconds=self.STARVATION_THRESHOLD_SEC)  # 300s = 5 min
starving_jobs = db.query(Job).filter(
    Job.status == "pending",
    Job.created_at < starvation_threshold,
    Job.priority > 1
).all()

for job in starving_jobs:
    job.priority -= 1  # 3→2 (Low→Medium), 2→1 (Medium→High)
    job.created_at = now  # Reset the starvation clock
```

**Aging mechanism:**
- Threshold: **5 minutes** (300 seconds)
- Mechanism: Decrement priority by 1 each time the threshold is crossed
- Reset: After bumping, `created_at` is updated so the job has another 5 minutes before the next bump
- Only applies if `priority > 1` (High priority jobs don't need bumping)

**What to say:** "This prevents the classic priority inversion problem. Without this, a flood of high-priority jobs could starve low-priority ones indefinitely. The priority aging ensures every job eventually reaches High priority and gets processed."

---

### 2.13 Logging

**Requirement:** Log every significant event in structured format.

**Implementation:** Uses two logging systems:

1. **Structured application logs** (Python `logging` module with JSON-like format):
```python
logging.basicConfig(
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    ...
)
logger.info("Job %s processed successfully", job['id'])
```

2. **Database audit logs** (`job_logs` table):
```python
class JobLog(Base):
    __tablename__ = "job_logs"
    id = Column(Integer, primary_key=True)
    job_id = Column(Integer, ForeignKey("jobs.id"))
    event_type = Column(String(50))  # created, started, completed, failed, cancelled,
                                     # priority_bump, recovered, manual_retry
    message = Column(Text)
    timestamp = Column(DateTime, default=datetime.utcnow)
```

**Event types tracked:**
| Event | Location | Trigger |
|-------|----------|---------|
| `created` | `api.py:create_job()` | Job creation |
| `started` | `api.py:pop_job()` | Worker picks up job |
| `completed` | `api.py:update_job_status()` | Successful processing |
| `failed` | `api.py:update_job_status()` | Processing failure |
| `cancelled` | `api.py:cancel_job()` | User cancellation |
| `priority_bump` | `broker.py:_sync_jobs()` | Starvation prevention |
| `recovered` | `broker.py:_sync_jobs()` | Stuck job recovery |
| `manual_retry` | `api.py:retry_job()` | Manual retry from DLQ |

---

### 2.14 Heap-Based Priority Queue

**Requirement:** The scheduler uses a heap internally. Jobs ordered by: 1. Priority, 2. Scheduled time, 3. Creation time.

**Implementation:** `backend/scheduler/heap_queue.py`

```python
class JobItem:
    def __lt__(self, other):
        # 1. Priority (lower number = higher priority)
        if self.priority != other.priority:
            return self.priority < other.priority
        # 2. Scheduled time (earlier = higher priority)
        if self.scheduled_at != other.scheduled_at:
            return self.scheduled_at < other.scheduled_at
        # 3. Creation time (earlier = higher priority)
        if self.created_at != other.created_at:
            return self.created_at < other.created_at
        return self.id < other.id  # Tiebreaker
```

**How the heap works:**
- Python's `heapq` implements a **binary min-heap** as a list
- Parent at index `i`, children at `2i+1` and `2i+2`
- `heapq.heappush`: Append to end, then "sift up" (swap with parent until heap property restored) — **O(log n)**
- `heapq.heappop`: Pop root, move last element to root, "sift down" — **O(log n)**
- The `__lt__` method defines the ordering for the heap

---

### 2.15 DAG Workflow (Dependencies)

**Requirement:** Jobs can depend on other jobs. A job doesn't run until all dependencies complete.

**Implementation:**
- **Database:** `job_dependencies` table maps `job_id → depends_on_job_id`
- **API:** `CreateJobRequest.depends_on` accepts a list of job IDs
- **Resolution:** In `broker.py:_sync_jobs()`, before pushing to queue:

```python
deps = db.query(JobDependency).filter(JobDependency.job_id == job.id).all()
all_met = True
for dep in deps:
    parent_job = db.query(Job).filter(Job.id == dep.depends_on_job_id).first()
    if not parent_job or parent_job.status != "completed":
        all_met = False
        break

if all_met:
    self.queue.push(job)  # Ready to process!
```

**DAG example:**
```
Generate Report (job 1)
      ↓
Upload File (job 2, depends_on: [1])
      ↓
Send Email (job 3, depends_on: [2])
```

The broker checks dependencies every 2 seconds. Job 2 only enters the priority queue when job 1 is `completed`.

---

### 2.16 Alternative Scheduling Algorithm (Skip List)

**Requirement:** Implement a second algorithm and benchmark it.

**Implementation:** `backend/scheduler/skip_list_queue.py`

A **Skip List** is a probabilistic data structure with multiple levels of linked lists. The bottom level contains all elements. Each higher level is an "express lane" that skips over elements.

```
Level 3:  [1] --------------------------------> [5] -----> [9]
Level 2:  [1] ---------> [3] ---------> [5] -> [7] -----> [9]
Level 1:  [1] -> [2] -> [3] -> [4] -> [5] -> [7] -> [8] -> [9]
```

- **Insert (push):** Find position at each level (O(log n) avg), generate random level, link pointers
- **Delete (pop):** Remove head node by updating header pointers at each level (O(1) avg for head removal)
- **Random level generation:** Uses geometric distribution with `p=0.5`

**Benchmark results (see `benchmark.md` for full details):**

| Operation | HeapQueue | SkipListQueue | Winner |
|-----------|-----------|---------------|--------|
| Push (100K) | 370 ms | 1741 ms (4.7x slower) | **Heap** |
| Pop (100K) | 621 ms | 393 ms (37% faster) | **SkipList** |
| Interleaved (10K pairs) | 125 ms | 238 ms | **Heap** |

**Tradeoffs to discuss:**
- **Heap** is simpler (~40 lines), uses stdlib, has excellent cache locality, and is faster for write-heavy workloads
- **SkipList** has faster pops (head removal is O(1) avg), faster theoretical `remove()` operation, but uses more memory and has probabilistic performance

**Why SkipList is slower on push:** Generating random levels, maintaining update arrays, and pointer manipulation at multiple levels adds overhead. The heap's `heappush` is just array append + sift-up — tight loop with great cache locality.

**Why SkipList is faster on pop:** Heap pop requires sifting the last element down through log(n) levels. SkipList pop just updates the header's forward pointers — no element needs to move.

---

## 3. Algorithms Deep Dive

### 3.1 Binary Heap

```
Representation:    [1, 2, 3, 5, 4, 6, 7]
                         1
                       /   \
                      2     3
                     / \   / \
                    5   4 6   7

Parent(i) = (i-1) / 2
Left(i)   = 2i + 1
Right(i)  = 2i + 2
```

**Push (heappush):**
1. Append new element to end of array (maintains complete tree property)
2. Sift up: Compare with parent, swap if out of order, repeat
3. Worst case: O(log n) swaps if element is the new minimum

**Pop (heappop):**
1. Save root (minimum element)
2. Move last element to root
3. Sift down: Compare with children, swap with smaller child, repeat
4. Worst case: O(log n) swaps

**Remove (heapremove in our implementation):**
1. Our implementation: mark as removed, then rebuild the heap — O(n)
2. Alternative: Lazy deletion (mark as removed, skip during pop) — O(1) remove but O(n log n) space
3. True removal: Replace with last element, sift up/down — O(log n) but needs position tracking

### 3.2 Skip List

```
header → [1]─────────────────────────────────────────────→ [9]→ None    ← Level 4
header → [1]──────────────────→ [5]───────────────────────→ [9]→ None    ← Level 3
header → [1]─────────→ [3]────→ [5]──────────→ [7]────────→ [9]→ None    ← Level 2
header → [1]→ [2]───→ [3]→ [4]→ [5]→ [6]───→ [7]→ [8]──→ [9]→ None    ← Level 1
```

**Insert (push) — O(log n) average:**
1. Start at highest level of header
2. At each level, move forward while next node < new node
3. Record the node at each level (update array)
4. Generate random level for new node
5. If new level > current max level, update header for new levels
6. Insert node at each level by updating pointers

**Delete (pop from head) — O(1) average:**
1. Save `header.forward[0]` (the first element)
2. For each level where header points to this element, update to `element.forward[i]`
3. Decrease max level if top level is now empty

**Random level generation:**
- `level = 0`, while `random() < 0.5`: `level += 1`
- This creates a geometric distribution where ~50% of nodes are level 0, ~25% are level 1, ~12.5% are level 2, etc.
- With `max_level=16`, we cap at 16 levels (supports up to 2^16 = 65536 elements optimally)

### 3.3 Dependency Resolution (DAG)

**How it works:**
1. Broker scans all `pending` jobs every 2 seconds
2. For each pending job, check its `depends_on` list from `job_dependencies` table
3. For each dependency, query the parent job's status
4. Only push to queue if ALL dependencies are `completed`
5. If a dependency failed, the dependent job stays pending forever (could add deadlock detection — worth mentioning)

**Limitation:** There's no cycle detection or deadlock resolution. A job that depends on a failed job will never execute. The UI shows its dependencies so an operator can manually fix the situation.

**Potential improvement to mention:** "I could add a deadlock detector that checks if any dependency has failed and marks the dependent as failed too."

---

## 4. Design Decisions & Tradeoffs

| Decision | Choice | Alternatives Considered | Why This Won |
|----------|--------|------------------------|--------------|
| **Worker model** | HTTP polling from API | Message queue (Redis/RabbitMQ) | Simpler infrastructure — no additional services |
| **Inter-process comms** | REST API | Shared DB polling, RPC | Standard, debuggable, works through reverse proxy |
| **Priority queue** | In-memory heap/skip list | DB-ordered queries | Performance — in-memory is much faster than DB for frequent push/pop |
| **Database** | SQLite (dev) | PostgreSQL | Zero setup, file-based, easy dev. PG switchable via config |
| **DLQ** | Same table, status filter | Separate table | Simpler queries, one source of truth. Performance hit at scale |
| **Live updates** | Polling (3s) | SSE, WebSockets | Simpler, good enough for single-server setup |
| **Broker sync interval** | 2 seconds | Continuous, 1s, 5s | Balance between responsiveness and DB load |
| **Starvation threshold** | 5 minutes | 1 min, 10 min, 30 min | Reasonable tradeoff — enough time for high-priority jobs without delaying low-priority too much |
| **Processing timeout** | 10 minutes | 5 min, 30 min | Allows for long jobs but catches crashed workers quickly enough |

---

## 5. Scenarios & Edge Cases

### Scenario 1: Worker crashes mid-processing

1. Job status is `processing`, but the worker never sends the completion/failure update
2. Broker's sync loop detects `processing` jobs with `started_at` older than 10 minutes
3. These jobs are recovered back to `pending` with `started_at = None`
4. A `recovered` log entry is created
5. Next sync cycle picks up the job and pushes it back to the queue

### Scenario 2: Stuck job recovery race condition

**What if a worker comes back and tries to update a recovered job?**
- The `update_job_status` endpoint checks the current status
- If the job was recovered to `pending`, the old worker's update will change it to `completed` or `failed`
- This is acceptable — the job was completed, just the worker was slow to report back

### Scenario 3: Dependency that never resolves

**What if a dependency job fails?**
- The dependent job stays `pending` forever because `status == "completed"` is never true
- The UI shows the dependency chain (`#1, #2`) so the operator can see why
- The operator can manually retry the failed dependency via the DLQ view
- Or cancel the dependent job

### Scenario 4: High-frequency recurring jobs

**What if a recurring job runs every minute and takes 45 seconds?**
- Each completion creates a new job instance with `scheduled_at = now + interval`
- The next instance is scheduled for 1 minute after completion, not 1 minute after start
- This naturally prevents overlapping executions

### Scenario 5: Cancellation during processing

1. User clicks "Cancel" → status changes to `cancelled`
2. Worker is still processing (hasn't called `update_job_status` yet)
3. Worker finishes and sends `status: "completed"` update
4. API checks `db_job.status == "cancelled"` → returns without changing anything
5. Job remains `cancelled`

---

## 6. Potential Interview Questions

### Q: How does your scheduler prevent two workers from processing the same job?

**Answer:** The `pop_job()` endpoint acts as a transactional claim. When a worker requests a job, the API:
1. Checks the current status in the database (must be `pending`)
2. Atomically sets it to `processing` with `started_at = now`
3. Commits the transaction
Because SQLAlchemy serializes writes, two concurrent requests cannot both claim the same job. The second worker will see `status != "pending"` and get `null`.

### Q: What's the difference between a Heap Queue and a Skip List for this use case?

**Answer:** Both provide O(log n) average push/pop, but with different characteristics:
- **Heap** is array-based with excellent cache locality, deterministic performance, and very simple code. It's faster for push-heavy workloads.
- **Skip List** is pointer-based with probabilistic performance, uses more memory (forward pointer arrays), but has faster pop (O(1) for head removal) and faster remove (O(log n) vs O(n)).
In my benchmarks, Heap was ~4x faster for pushes but SkipList was ~1.6x faster for pops at 100K scale.

### Q: How do you handle recurring jobs without overlap?

**Answer:** Each recurrence creates a new job row in the database with its own ID, scheduled 1 interval after the previous completion. The next instance doesn't exist until the current one completes, so there can never be overlap.

### Q: What happens if the worker crashes?

**Answer:** The broker runs a recovery mechanism every 2 seconds. It checks for jobs that have been in `processing` state for more than 10 minutes (indicating a crashed worker). These are reset to `pending` with `started_at = NULL` and a log entry is created. The next broker sync cycle will push them back to the priority queue.

### Q: How do you prevent low-priority jobs from starving?

**Answer:** Every 2 seconds, the broker checks for pending jobs that have been waiting more than 5 minutes. Their priority is bumped by one level (3→2, 2→1), and their `created_at` timestamp is reset so the bump doesn't happen again immediately. Over time, any job will eventually reach priority 1 (High) and be processed.

### Q: Why didn't you use a message queue like Redis or RabbitMQ?

**Answer:** The requirements specified this can use any database and Redis is allowed, but I chose a simpler architecture: HTTP polling with a REST API. This eliminates the operational complexity of managing a message broker. For a single-server deployment, this is more than sufficient. The in-memory heap/skip list handles the priority queuing efficiently, and the database is the source of truth for persistence.

### Q: How would you scale this system?

**Answer:** Several levers:
1. **Multiple workers** — Simply run more worker processes. The API's duplicate protection ensures each job goes to only one worker.
2. **PostgreSQL** — Replace SQLite with PostgreSQL for better concurrent access.
3. **Load balancer** — Put multiple API instances behind the load balancer. The in-memory queue would need to be replaced with a shared Redis queue.
4. **Horizontal partitioning** — Split by job type or tenant.

### Q: Explain the backoff formula. Why 5^retry?

**Answer:** I chose exponential backoff with base 5 to get a good spread: ~1s, ~5s, ~25s. The jitter (±20%) prevents the thundering herd problem. The formula `5^retry` was chosen because:
- Attempt 0: 5^0 = 1, but we use 1s as minimum (return 1 for retry_count=0)
- Attempt 1: 5^1 = 5s
- Attempt 2: 5^2 = 25s
This gives fast retry for transient failures but backs off quickly for persistent ones.

---

## 7. Architecture Diagram (Mental Model)

Draw this on the whiteboard:

```
┌────────────┐     ┌────────────────┐     ┌──────────────┐
│  Browser   │────▶│   Nginx :443   │────▶│  API :8000   │
│  (React)   │     │  Reverse Proxy │     │  (FastAPI)   │
└────────────┘     └────────────────┘     └──────┬───────┘
                                                  │
                     ┌────────────────────────────┼──────────────┐
                     │                            │              │
               ┌─────▼─────┐             ┌───────▼───────┐     │
               │  SQLite   │             │  Job Broker   │     │
               │  Database │             │  (Background  │     │
               │           │             │   Thread)     │     │
               │ jobs      │             │               │     │
               │ job_logs  │             │  In-Memory    │     │
               │ job_deps  │             │  Queue        │     │
               └───────────┘             │  (Heap/Skip)  │     │
                                         └───────────────┘     │
                                                               │
                                                  ┌────────────┴──┐
                                                  │   Worker(s)   │
                                                  │  (Separate    │
                                                  │   Process)    │
                                                  │  HTTP Polls   │
                                                  └───────────────┘
```

**Data Flow (job lifecycle):**
1. **Create:** Browser → POST /jobs → DB insert → log `created`
2. **Sync:** Broker loop → query pending jobs → check deps/schedule → push to queue
3. **Claim:** Worker → POST /internal/jobs/pop → DB: `pending → processing` → log `started`
4. **Execute:** Worker runs `mock_email_handler()`
5. **Complete:** Worker → PATCH status → DB: `processing → completed` → log `completed`
6. **Recur:** If job has interval → create new job with `scheduled_at = now + interval`

---

## Quick Reference: Key Files & What They Do

| File | Purpose | Key Classes/Functions |
|------|---------|----------------------|
| `backend/models.py` | Database models | `Job`, `JobDependency`, `JobLog` |
| `backend/api.py` | REST endpoints | `create_job`, `get_jobs`, `cancel_job`, `retry_job`, `pop_job`, `update_job_status` |
| `backend/worker.py` | Background worker | `main()`, `mock_email_handler()`, `calculate_backoff()` |
| `backend/scheduler/broker.py` | Queue sync & management | `JobBroker`, `_sync_jobs()`, `get_next_job()` |
| `backend/scheduler/heap_queue.py` | Binary heap priority queue | `HeapQueue`, `JobItem` |
| `backend/scheduler/skip_list_queue.py` | Skip list priority queue | `SkipListQueue`, `SkipListNode` |
| `frontend/src/App.tsx` | React SPA | Dashboard, Create Job, DLQ views |
| `deploy/nginx.conf` | Nginx configuration | Reverse proxy routing |

---

## Before the Interview: Quick Review Checklist

- [ ] Walk through the job lifecycle from creation to completion/failure
- [ ] Explain how the heap works (binary tree array representation, sift up/down)
- [ ] Compare heap vs skip list tradeoffs
- [ ] Explain how DAG dependencies are resolved
- [ ] Explain the retry flow with backoff
- [ ] Explain starvation prevention mechanism
- [ ] Draw the architecture diagram from memory
- [ ] Know the DLQ threshold (10 failed jobs)
- [ ] Know the starvation threshold (5 minutes)
- [ ] Know the processing timeout (10 minutes)
- [ ] Explain the cancellation decision (cancellation is final)

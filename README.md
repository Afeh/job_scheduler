# Dilamme Job Scheduler

A premium background job scheduler and workflow engine built with **FastAPI** (Python backend), **React & TypeScript** (Vite frontend), and **SQLAlchemy** (Database Layer).

The scheduler processes jobs asynchronously, handles failures gracefully with automated retries and backoff, implements a dead-letter queue (DLQ) with automated threshold alerts, supports dependency-driven DAG workflows, and employs starvation prevention.

---

## Architectural Overview

```
       +---------------------------------------------+
       |             Vite React Frontend             |
       +---------------------------------------------+
                              |
                              | REST APIs & Polling
                              v
       +---------------------------------------------+
       |            FastAPI Backend App              |
       |                                             |
       |  +----------------+    +-----------------+  |
       |  |  REST Endpoints|    |   Job Broker    |  |
       |  +----------------+    +-----------------+  |
       +---------------------------------------------+
               |                       |
      Reads /  |                       | Controls Queue
      Writes   v                       v
       +---------------------------------------------+
       |              SQLite / SQLite DB             |
       +---------------------------------------------+
               ^
               | Pulls / Updates
               |
       +---------------------------------------------+
       |             Background Worker               |
       +---------------------------------------------+
```

1. **FastAPI Backend Web Server**: Exposes REST endpoints to manage and create jobs.
2. **Job Broker**: Runs a background synchronization thread daemon. It checks for starving jobs, cleans up stuck processing states, resolves dependencies, and pushes jobs ready to be processed into the priority queue.
3. **Background Worker**: Runs as a separate process. It pops ready jobs from the internal broker, mocks the external handler, schedules retries if exceptions occur, and updates the job status via REST APIs.
4. **Vite React Frontend**: Displays dashboard counts, a jobs table, job creation forms, and the DLQ view. Polling is utilized to update state dynamically.

---

## Core Algorithms & Mechanisms

### 1. Priority Queuing (Heap vs. Skip List)
The Broker supports two alternative priority structures for queue management:
- **Heap Queue (Default)**: Uses Python's native binary min-heap (`heapq`). Ordering is determined by:
  1. Priority level (1: High, 2: Medium, 3: Low).
  2. Scheduled execution time.
  3. Job creation timestamp.
  *Heap push/pop operations run in $O(\log N)$ time.*
- **Skip List Queue (Alternative)**: A probabilistic linked list structure built with multiple forward-pointer levels.
  *Offers $O(\log N)$ average complexity for search, insertion, and deletion without needing to re-heapify the whole list.*

### 2. Dependency Resolution (DAGs)
Jobs can form Directed Acyclic Graphs (DAGs) by defining a dependency array. A job remains in the `pending` state and will not be pushed to the active heap queue until all parent jobs in its `depends_on` list are successfully `completed`.

### 3. Starvation Prevention
To prevent low-priority jobs from sitting in the queue indefinitely, the Broker monitors pending tasks. If a job remains `pending` for longer than **5 minutes** (300 seconds), its effective priority is bumped up by one level (e.g. from Low to Medium, or Medium to High) to ensure eventual execution.

### 4. Worker Fail-Safe & DLQ
- **Retry Backoff with Jitter**: If a worker fails to process a job, it schedules a retry with exponential backoff and randomized jitter:
  - Attempt 1: ~1s delay
  - Attempt 2: ~5s delay
  - Attempt 3: ~25s delay
- **Dead-Letter Queue (DLQ)**: Once a job fails 3 times, its status is finalized as `failed`. It remains in the database for manual inspection and can be manually retried from the frontend.
- **DLQ Threshold Alerting**: If the number of failed jobs in the DLQ reaches **10 or more**, the system logs an automatic system alert (`ALERT: DLQ threshold reached!`).

---

## Database Schema

Three main tables are utilized:

### 1. `jobs`
Represents the core job entity.
- `id` (Integer, Primary Key)
- `type` (String, e.g. `send_email`)
- `priority` (Integer, Default 2)
- `status` (String, e.g. `pending`, `processing`, `completed`, `failed`, `cancelled`)
- `payload` (Text JSON string)
- `retry_count` (Integer, Default 0)
- `scheduled_at` (DateTime, Optional)
- `interval` (String, Optional)
- `created_at` (DateTime, Default UtcNow)
- `started_at` / `finished_at` (DateTime, Optional)

### 2. `job_dependencies`
Maps parent-child relationships for DAG workflows.
- `id` (Integer, Primary Key)
- `job_id` (Integer, ForeignKey to `jobs.id`)
- `depends_on_job_id` (Integer, ForeignKey to `jobs.id`)

### 3. `job_logs`
Stores structural audit logs for each state transition.
- `id` (Integer, Primary Key)
- `job_id` (Integer, ForeignKey to `jobs.id`)
- `event_type` (String, e.g. `created`, `started`, `priority_bump`, `completed`, `failed`)
- `message` (Text)
- `timestamp` (DateTime)

---

## API Documentation

All request bodies are strictly validated using Pydantic schemas.

### 1. Create a Job
- **Endpoint**: `POST /jobs`
- **Request Body** (`CreateJobRequest`):
```json
{
  "type": "send_email",
  "priority": 2,
  "payload": "{\"to\": \"user@example.com\", \"subject\": \"Hello World\"}",
  "scheduled_at": "2026-06-11T16:00:00Z",
  "interval": "every_5_minutes",
  "depends_on": []
}
```
- **Response**:
```json
{
  "id": 12
}
```

### 2. Get All Jobs
- **Endpoint**: `GET /jobs`
- **Response**:
```json
[
  {
    "id": 12,
    "type": "send_email",
    "priority": 2,
    "status": "pending",
    "retry_count": 0,
    "scheduled_at": "2026-06-11T16:00:00Z",
    "interval": "every_5_minutes",
    "created_at": "2026-06-11T15:30:00Z"
  }
]
```

### 3. Cancel a Job
- **Endpoint**: `POST /jobs/{job_id}/cancel`
- **Response**:
```json
{
  "status": "cancelled"
}
```

### 4. Manually Retry a DLQ Job
- **Endpoint**: `POST /jobs/{job_id}/retry`
- **Response**:
```json
{
  "status": "pending"
}
```

### 5. Pop Next Job (Internal Worker API)
- **Endpoint**: `POST /internal/jobs/pop`
- **Response**:
```json
{
  "job": {
    "id": 12,
    "type": "send_email",
    "payload": "{\"to\": \"user@example.com\"}",
    "retry_count": 0
  }
}
```

### 6. Update Job Status (Internal Worker API)
- **Endpoint**: `PATCH /internal/jobs/{job_id}/status`
- **Request Body** (`UpdateJobStatusRequest`):
```json
{
  "status": "completed",
  "message": "Email sent successfully",
  "scheduled_at": null,
  "increment_retry": false
}
```
- **Response**:
```json
{
  "status": "ok"
}
```

---

## Local Development

### 1. Running the Backend Server
Prerequisites: Python 3.10+, pip, FastAPI.
Navigate to the root directory and start the server:
```bash
cd backend
pip install -r requirements.txt
uvicorn backend.api:app --reload
```
The server will run on `http://127.0.0.1:8000`.

### 2. Running the Worker Process
Start the worker process in a separate terminal:
```bash
python -m backend.worker
```
The worker will log status updates as it pulls, processes, and completes scheduled or pending tasks.

### 3. Running the Frontend Dashboard
Navigate to the frontend folder, install dependencies, and run Vite:
```bash
cd frontend
npm install
npm run dev
```
Open `http://localhost:5173` in your browser.

---


import heapq
import threading
from datetime import datetime

class JobItem:
    def __init__(self, job):
        self.job = job
        self.id = job.id
        self.priority = job.priority
        self.scheduled_at = job.scheduled_at or datetime.min
        self.created_at = job.created_at or datetime.min
    
    def __lt__(self, other):
        # 1. Priority (lower number is higher priority)
        if self.priority != other.priority:
            return self.priority < other.priority
        # 2. Scheduled time (earlier is better)
        if self.scheduled_at != other.scheduled_at:
            return self.scheduled_at < other.scheduled_at
        # 3. Creation time (earlier is better)
        if self.created_at != other.created_at:
            return self.created_at < other.created_at
        
        return self.id < other.id

class HeapQueue:
    def __init__(self):
        self._heap = []
        self._job_ids = set()
        self._lock = threading.Lock()

    def push(self, job):
        with self._lock:
            if job.id not in self._job_ids:
                item = JobItem(job)
                heapq.heappush(self._heap, item)
                self._job_ids.add(job.id)

    def pop(self):
        with self._lock:
            if not self._heap:
                return None
            item = heapq.heappop(self._heap)
            self._job_ids.remove(item.id)
            return item.job

    def remove(self, job_id):
        # Removing from a heap is O(N), so we just rebuild it
        with self._lock:
            if job_id in self._job_ids:
                self._heap = [item for item in self._heap if item.id != job_id]
                heapq.heapify(self._heap)
                self._job_ids.remove(job_id)
                return True
            return False

    def peek(self):
        with self._lock:
            if self._heap:
                return self._heap[0].job
            return None

    def __len__(self):
        with self._lock:
            return len(self._heap)

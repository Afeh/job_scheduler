import time
import threading
import logging
from datetime import datetime, timedelta
from sqlalchemy.orm import Session
from ..database import SessionLocal
from ..models import Job, JobDependency, JobLog
from .heap_queue import HeapQueue
from .skip_list_queue import SkipListQueue

logger = logging.getLogger("broker")

class JobBroker:
    def __init__(self, use_skip_list=False):
        self.queue = SkipListQueue() if use_skip_list else HeapQueue()
        self._running = False
        self._thread = None
        
        # Starvation threshold: if pending > 5 mins, increase priority
        self.STARVATION_THRESHOLD_SEC = 300 
        
        # Timeout for stuck jobs (worker crashed)
        self.PROCESSING_TIMEOUT_SEC = 600
        
    def start(self):
        self._running = True
        self._thread = threading.Thread(target=self._broker_loop, daemon=True)
        self._thread.start()

    def stop(self):
        self._running = False
        if self._thread:
            self._thread.join()

    def _broker_loop(self):
        while self._running:
            try:
                self._sync_jobs()
            except Exception as e:
                logger.error("Broker error: %s", e, exc_info=True)
            time.sleep(2) # Poll every 2 seconds

    def _sync_jobs(self):
        db = SessionLocal()
        try:
            now = datetime.utcnow()
            
            # 1. Recover stuck processing jobs
            stuck_threshold = now - timedelta(seconds=self.PROCESSING_TIMEOUT_SEC)
            stuck_jobs = db.query(Job).filter(
                Job.status == "processing",
                Job.started_at < stuck_threshold
            ).all()
            
            for job in stuck_jobs:
                job.status = "pending"
                job.started_at = None
                log = JobLog(job_id=job.id, event_type="recovered", message="Recovered from stuck processing state")
                db.add(log)
            
            if stuck_jobs:
                db.commit()

            # 2. Starvation prevention: increase priority of old pending jobs
            starvation_threshold = now - timedelta(seconds=self.STARVATION_THRESHOLD_SEC)
            starving_jobs = db.query(Job).filter(
                Job.status == "pending",
                Job.created_at < starvation_threshold,
                Job.priority > 1
            ).all()
            
            for job in starving_jobs:
                job.priority -= 1 # 3->2, 2->1
                job.created_at = now # Reset clock for next starvation check
                log = JobLog(job_id=job.id, event_type="priority_bump", message=f"Priority bumped to {job.priority} due to starvation")
                db.add(log)
                
            if starving_jobs:
                db.commit()

            # 3. Find pending jobs ready to execute
            # Ready means:
            # - Scheduled time is past or None
            # - All dependencies are completed
            
            pending_jobs = db.query(Job).filter(Job.status == "pending").all()
            for job in pending_jobs:
                if job.scheduled_at and job.scheduled_at > now:
                    continue # Not due yet
                    
                # Check dependencies
                deps = db.query(JobDependency).filter(JobDependency.job_id == job.id).all()
                all_met = True
                for dep in deps:
                    parent_job = db.query(Job).filter(Job.id == dep.depends_on_job_id).first()
                    if not parent_job or parent_job.status != "completed":
                        all_met = False
                        break
                        
                if all_met:
                    self.queue.push(job)
                    
        finally:
            db.close()

    def get_next_job(self):
        # Called by worker to get the next job
        job = self.queue.pop()
        return job

broker = JobBroker()

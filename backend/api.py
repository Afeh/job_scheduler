from fastapi import FastAPI, Depends, HTTPException, BackgroundTasks
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy.orm import Session
from sqlalchemy import desc
from typing import List, Optional
from datetime import datetime, timedelta
import os
import logging

from dotenv import load_dotenv

load_dotenv()

# Configure logging
log_level = os.getenv("LOG_LEVEL", "INFO").upper()
logging.basicConfig(
    level=getattr(logging, log_level, logging.INFO),
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s"
)
logger = logging.getLogger("api")

from .database import engine, Base, get_db
from .models import Job, JobDependency, JobLog
from .schemas import CreateJobRequest, UpdateJobStatusRequest
from .scheduler.broker import broker

# Create tables
Base.metadata.create_all(bind=engine)

app = FastAPI(title="Dilamme Job Scheduler")

# Read allowed CORS origins from environment (comma-separated, no spaces)
# Default: development-friendly origins
cors_origins_env = os.getenv("CORS_ORIGINS", "http://localhost:5173,http://127.0.0.1:5173")
cors_origins = [origin.strip() for origin in cors_origins_env.split(",") if origin.strip()]

app.add_middleware(
    CORSMiddleware,
    allow_origins=cors_origins,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PATCH"],
    allow_headers=["Content-Type"],
)

@app.on_event("startup")
def startup_event():
    broker.start()

@app.on_event("shutdown")
def shutdown_event():
    broker.stop()



# --- Public API ---

@app.post("/jobs")
async def create_job(
    body: CreateJobRequest,
    db: Session = Depends(get_db)
):
    job = Job(
        type=body.type,
        priority=body.priority,
        payload=body.payload,
        scheduled_at=body.scheduled_at,
        interval=body.interval
    )
    db.add(job)
    db.commit()
    db.refresh(job)
    
    log = JobLog(job_id=job.id, event_type="created", message="Job created")
    db.add(log)
    
    for dep_id in body.depends_on:
        dep = JobDependency(job_id=job.id, depends_on_job_id=dep_id)
        db.add(dep)
        
    db.commit()
    
    return {"id": job.id}

@app.get("/jobs")
def get_jobs(db: Session = Depends(get_db)):
    jobs = db.query(Job).order_by(desc(Job.created_at)).all()
    result = []
    for j in jobs:
        deps = db.query(JobDependency.depends_on_job_id).filter(
            JobDependency.job_id == j.id
        ).all()
        result.append({
            "id": j.id, "type": j.type, "priority": j.priority, 
            "status": j.status, "retry_count": j.retry_count,
            "scheduled_at": j.scheduled_at, "interval": j.interval,
            "created_at": j.created_at,
            "depends_on": [d[0] for d in deps]
        })
    return result

@app.post("/jobs/{job_id}/cancel")
async def cancel_job(job_id: int, db: Session = Depends(get_db)):
    job = db.query(Job).filter(Job.id == job_id).first()
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
        
    if job.status in ["completed", "failed"]:
        raise HTTPException(status_code=400, detail=f"Cannot cancel {job.status} job")
        
    job.status = "cancelled"
    log = JobLog(job_id=job.id, event_type="cancelled", message="Job cancelled by user")
    db.add(log)
    db.commit()
    
    return {"status": "cancelled"}

@app.post("/jobs/{job_id}/retry")
async def retry_job(job_id: int, db: Session = Depends(get_db)):
    job = db.query(Job).filter(Job.id == job_id).first()
    if not job or job.status != "failed":
        raise HTTPException(status_code=400, detail="Only failed jobs can be manually retried")
        
    job.status = "pending"
    # Do not reset retry count on manual retry as per typical DLQ manual retry, or maybe reset to 0? 
    # The requirement says "If it fails again, it goes back to the DLQ". Let's reset retry_count.
    job.retry_count = 0 
    
    log = JobLog(job_id=job.id, event_type="manual_retry", message="Manual retry triggered from DLQ")
    db.add(log)
    db.commit()
    
    return {"status": "pending"}

# --- Internal API for Workers ---

@app.post("/internal/jobs/pop")
async def pop_job(db: Session = Depends(get_db)):
    # Broker holds the heap queue
    job = broker.get_next_job()
    if not job:
        return {"job": None}
        
    # Re-fetch from DB to ensure it hasn't been cancelled
    db_job = db.query(Job).filter(Job.id == job.id).first()
    if not db_job or db_job.status != "pending":
        return {"job": None}
        
    db_job.status = "processing"
    db_job.started_at = datetime.utcnow()
    log = JobLog(job_id=db_job.id, event_type="started", message="Job started processing")
    db.add(log)
    db.commit()
    
    
    return {
        "job": {
            "id": db_job.id,
            "type": db_job.type,
            "payload": db_job.payload,
            "retry_count": db_job.retry_count
        }
    }

@app.patch("/internal/jobs/{job_id}/status")
async def update_job_status(job_id: int, body: UpdateJobStatusRequest, db: Session = Depends(get_db)):
    db_job = db.query(Job).filter(Job.id == job_id).first()
    if not db_job:
        raise HTTPException(status_code=404)
        
    if db_job.status == "cancelled":
        # If cancelled while processing, respect the cancellation
        return {"status": "cancelled"}
        
    db_job.status = body.status
    if body.scheduled_at:
        db_job.scheduled_at = body.scheduled_at
        
    if body.increment_retry:
        db_job.retry_count += 1

    if body.status in ["completed", "failed"]:
        db_job.finished_at = datetime.utcnow()
        
    if body.status == "failed":
        db_job.retry_count += 1
        event_type = "failed"
    elif body.status == "completed":
        event_type = "completed"
    else:
        event_type = "status_update"
        
    log = JobLog(job_id=db_job.id, event_type=event_type, message=body.message)
    db.add(log)
    db.commit()
    
    # Check DLQ threshold
    if body.status == "failed" and db_job.retry_count >= 3:
        # Move to DLQ (status is 'failed' and retry_count >= 3 is effectively DLQ)
        failed_count = db.query(Job).filter(Job.status == "failed", Job.retry_count >= 3).count()
        if failed_count >= 10:
            logger.warning("ALERT: DLQ threshold reached! Over 10 failed jobs.")
            
    # Handle Recurring
    if body.status == "completed" and db_job.interval:
        # Schedule next run
        # Naive interval parsing for demo
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
        db.commit()
    
    return {"status": "ok"}

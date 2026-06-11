from sqlalchemy import Column, Integer, String, DateTime, ForeignKey, Text
from sqlalchemy.orm import relationship
import datetime
from .database import Base

class Job(Base):
    __tablename__ = "jobs"

    id = Column(Integer, primary_key=True, index=True)
    type = Column(String(50), nullable=False)
    priority = Column(Integer, default=2) # 1: High, 2: Medium, 3: Low
    status = Column(String(20), default="pending") # pending, processing, completed, failed, cancelled
    payload = Column(Text, nullable=True) # JSON string
    retry_count = Column(Integer, default=0)
    scheduled_at = Column(DateTime, nullable=True)
    interval = Column(String(50), nullable=True) # e.g., 'every_1_minute'
    created_at = Column(DateTime, default=datetime.datetime.utcnow)
    started_at = Column(DateTime, nullable=True)
    finished_at = Column(DateTime, nullable=True)

    # Relationships
    logs = relationship("JobLog", back_populates="job", cascade="all, delete")

class JobDependency(Base):
    __tablename__ = "job_dependencies"

    id = Column(Integer, primary_key=True, index=True)
    job_id = Column(Integer, ForeignKey("jobs.id"), nullable=False)
    depends_on_job_id = Column(Integer, ForeignKey("jobs.id"), nullable=False)

class JobLog(Base):
    __tablename__ = "job_logs"

    id = Column(Integer, primary_key=True, index=True)
    job_id = Column(Integer, ForeignKey("jobs.id"), nullable=False)
    event_type = Column(String(50), nullable=False) # e.g., 'created', 'started', 'retry_attempted', 'failed', 'cancelled', 'completed'
    message = Column(Text, nullable=True)
    timestamp = Column(DateTime, default=datetime.datetime.utcnow)

    job = relationship("Job", back_populates="logs")

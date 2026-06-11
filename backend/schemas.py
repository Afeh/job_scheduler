from pydantic import BaseModel, Field
from typing import List, Optional
from datetime import datetime


class CreateJobRequest(BaseModel):
    type: str = Field(..., min_length=1, max_length=50, description="The job type identifier")
    priority: int = Field(default=2, ge=1, le=3, description="Job priority: 1 (High), 2 (Medium), 3 (Low)")
    payload: str = Field(default="{}", description="JSON string payload for the job")
    scheduled_at: Optional[datetime] = Field(default=None, description="When to schedule the job")
    interval: Optional[str] = Field(default=None, max_length=50, description="Recurrence interval, e.g. 'every_5_minutes'")
    depends_on: List[int] = Field(default_factory=list, description="List of job IDs this job depends on")


class UpdateJobStatusRequest(BaseModel):
    status: str = Field(..., min_length=1, max_length=20, description="New job status")
    message: str = Field(default="", description="Status update message")
    scheduled_at: Optional[datetime] = Field(default=None, description="New scheduled time for the job")
    increment_retry: bool = Field(default=False, description="Whether to increment the retry count")

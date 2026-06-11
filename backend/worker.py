import os
import time
import requests
import json
import random
import logging
from datetime import datetime, timedelta

from dotenv import load_dotenv

load_dotenv()

# Configure logging
log_level = os.getenv("LOG_LEVEL", "INFO").upper()
logging.basicConfig(
    level=getattr(logging, log_level, logging.INFO),
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s"
)
logger = logging.getLogger("worker")

# Read API base URL from environment (default: local development)
API_URL = os.getenv("API_URL", "http://127.0.0.1:8000")

def calculate_backoff(retry_count):
    # Attempt 1 -> ~1s
    # Attempt 2 -> ~5s
    # Attempt 3 -> ~25s
    # Formula: 5^(retry_count)
    base_delay = 5 ** retry_count if retry_count > 0 else 1
    # Add jitter +/- 20%
    jitter = base_delay * 0.2
    return base_delay + random.uniform(-jitter, jitter)

def mock_email_handler(job):
    logger.info("Processing job %s of type %s", job['id'], job['type'])
    try:
        payload = json.loads(job.get('payload', '{}'))
    except:
        payload = {}
        
    logger.info("Payload: %s", payload)
    
    # Simulate work
    time.sleep(random.uniform(0.5, 1.5))
    
    # Simulate failure conditions
    if payload.get("force_fail", False) or (payload.get("random_fail", False) and random.random() < 0.5):
        raise Exception("Mock email delivery failed")
        
    logger.info("Job %s processed successfully", job['id'])

def main():
    logger.info("Worker started...")
    while True:
        try:
            resp = requests.post(f"{API_URL}/internal/jobs/pop")
            if resp.status_code == 200:
                data = resp.json()
                job = data.get("job")
                
                if job:
                    try:
                        mock_email_handler(job)
                        # Success
                        requests.patch(
                            f"{API_URL}/internal/jobs/{job['id']}/status",
                            json={"status": "completed", "message": "Email sent successfully"}
                        )
                    except Exception as e:
                        logger.error("Job %s failed: %s", job['id'], e, exc_info=True)
                        retry_count = job.get("retry_count", 0)
                        if retry_count < 2: # Currently attempting 0, 1, 2. If it fails on 2, it's the 3rd failure. Wait, the API increments retry count on 'failed' status!
                            # Wait, if we set status="failed", the API increments retry_count to 1.
                            # The API checks if retry_count >= 3 AFTER incrementing.
                            # So we just send 'failed'. But wait, if it fails, it goes to DLQ. If we want it to retry automatically, it should go to 'pending' with a new scheduled_at!
                            
                            # Let's handle retry transition directly from the worker or API?
                            # Worker knows it failed. It sends 'failed'.
                            # Wait, the API right now says:
                            # if status == "failed": db_job.retry_count += 1
                            # if retry_count >= 3: print alert
                            # BUT it leaves status="failed". The broker ignores "failed" jobs.
                            # So if we want it to retry, we must set status="pending" and schedule it!
                            
                            new_retry = retry_count + 1
                            if new_retry >= 3:
                                # Final failure
                                requests.patch(
                                    f"{API_URL}/internal/jobs/{job['id']}/status",
                                    json={"status": "failed", "message": str(e)}
                                )
                            else:
                                # Auto retry
                                backoff = calculate_backoff(retry_count)
                                next_run = datetime.utcnow() + timedelta(seconds=backoff)
                                
                                # Send pending with scheduled_at
                                requests.patch(
                                    f"{API_URL}/internal/jobs/{job['id']}/status",
                                    json={
                                        "status": "pending", 
                                        "message": f"Failed attempt {new_retry}. Retrying in {backoff:.1f}s",
                                        "scheduled_at": next_run.isoformat(),
                                        "increment_retry": True
                                    }
                                )
                                # Wait, setting it to pending doesn't increment the retry count in API!
                                # I need to update the API to accept retry_count or I can increment it here and send it.
                                # Actually, it's easier to modify API to increment retry_count when we pass a query param, or just do it in worker.
                                # Let's just fix the API to increment retry count when status goes from processing -> pending during a retry.
                        
                else:
                    time.sleep(1)
            else:
                time.sleep(1)
        except requests.exceptions.ConnectionError:
            logger.warning("API not available, waiting...")
            time.sleep(2)
        except Exception as e:
            logger.error("Unexpected error: %s", e, exc_info=True)
            time.sleep(1)

if __name__ == "__main__":
    main()

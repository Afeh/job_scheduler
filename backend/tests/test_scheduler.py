import pytest
from datetime import datetime, timedelta
from backend.models import Job
from backend.scheduler.heap_queue import HeapQueue
from backend.scheduler.skip_list_queue import SkipListQueue

def test_heap_queue_ordering():
    hq = HeapQueue()
    
    # Ordered by: Priority, Scheduled Time, Creation Time
    now = datetime.utcnow()
    
    j1 = Job(id=1, priority=3, created_at=now) # Low priority
    j2 = Job(id=2, priority=1, created_at=now) # High priority
    j3 = Job(id=3, priority=2, created_at=now - timedelta(minutes=10)) # Medium priority, older
    j4 = Job(id=4, priority=2, created_at=now) # Medium priority, newer
    
    hq.push(j1)
    hq.push(j2)
    hq.push(j3)
    hq.push(j4)
    
    assert hq.pop().id == 2 # Priority 1
    assert hq.pop().id == 3 # Priority 2, older
    assert hq.pop().id == 4 # Priority 2, newer
    assert hq.pop().id == 1 # Priority 3
    assert hq.pop() is None

def test_skip_list_queue_ordering():
    sq = SkipListQueue()
    
    now = datetime.utcnow()
    
    j1 = Job(id=1, priority=3, created_at=now)
    j2 = Job(id=2, priority=1, created_at=now)
    j3 = Job(id=3, priority=2, created_at=now - timedelta(minutes=10))
    j4 = Job(id=4, priority=2, created_at=now)
    
    sq.push(j1)
    sq.push(j2)
    sq.push(j3)
    sq.push(j4)
    
    assert sq.pop().id == 2
    assert sq.pop().id == 3
    assert sq.pop().id == 4
    assert sq.pop().id == 1
    assert sq.pop() is None

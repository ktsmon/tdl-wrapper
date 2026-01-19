#!/usr/bin/env python3
"""Quick script to verify scheduler is working correctly."""

from src.database import Database
from src.config import Config
import requests
import datetime

# Check database schedules
db = Database()
session = db.get_session()
from src.models import Schedule

schedules = session.query(Schedule).filter_by(is_enabled=True).all()
print(f"Enabled schedules in database: {len(schedules)}")
for s in schedules[:3]:
    print(f"  - Chat {s.chat_id} ({s.job_type}): next_run = {s.next_run_time}")

session.close()

# Check API
try:
    response = requests.get('http://127.0.0.1:5000/api/scheduler/next_run')
    data = response.json()
    print(f"\nAPI Response:")
    print(f"  Cron schedule: {data['cron_schedule']}")
    print(f"  Next run time: {data['next_run_time']}")

    # Calculate countdown
    next_run = datetime.datetime.fromisoformat(data['next_run_time'])
    now = datetime.datetime.now(next_run.tzinfo)
    diff = (next_run - now).total_seconds()
    minutes = int(diff // 60)
    seconds = int(diff % 60)
    print(f"  Time until next run: {minutes}m {seconds}s")
except Exception as e:
    print(f"Error checking API: {e}")

# Check config
config = Config()
print(f"\nConfig cron_schedule: {config.get('scheduler.cron_schedule')}")

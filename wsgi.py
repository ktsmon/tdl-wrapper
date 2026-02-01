"""WSGI entry point for Gunicorn."""

import os
import atexit

from src.config import Config
from src.database import Database
from src.core import TDLWrapper
from src.notifications import DiscordNotifier
from src.scheduler import TDLScheduler
from src.web.app import create_app

# Load configuration
config = Config()
cfg = config.config

# Initialize database
db = Database(cfg['database']['path'])

# Initialize wrapper
wrapper = TDLWrapper(cfg, db)

# Initialize notifier (if enabled)
if cfg['discord']['enabled'] and cfg['discord']['webhook_url']:
    notifier = DiscordNotifier(
        cfg['discord']['webhook_url'],
        cfg['discord']
    )
else:
    notifier = None

# Initialize scheduler
scheduler = TDLScheduler(wrapper, db, cfg['scheduler'], notifier)
scheduler.start()
print("[WSGI] Background scheduler started")

# Create the Flask app
app = create_app(config, db, wrapper, scheduler)

# Cleanup on shutdown
def cleanup():
    print("[WSGI] Shutting down scheduler...")
    scheduler.stop()

atexit.register(cleanup)

# Gunicorn expects an 'app' or 'application' variable
application = app

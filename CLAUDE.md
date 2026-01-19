# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Overview

TDL Wrapper is a Python-based state management layer for the `tdl` CLI (Telegram Downloader). It provides incremental sync, automated scheduling, Discord notifications, and a web dashboard for downloading and managing Telegram content across multiple chats.

## Key Commands

### Development and Testing
```bash
# Run CLI commands
python -m src.cli <command>

# Common operations
python -m src.cli list                    # List tracked chats
python -m src.cli sync <chat_id>          # Full sync (export + download)
python -m src.cli sync --all              # Sync all active chats
python -m src.cli export <chat_id>        # Export messages only
python -m src.cli download <chat_id>      # Download from latest export
python -m src.cli status <chat_id>        # Show detailed chat status

# Sync chats from Telegram
python -m src.cli sync-chats              # Import all chats
python -m src.cli sync-chats --filter "Type contains 'channel'"

# Add chat manually
python -m src.cli add <chat_id> --name "Chat Name"

# Scheduler daemon
python -m src.cli daemon --foreground     # Run in foreground (for testing)
python -m src.cli daemon                  # Background mode

# Web dashboard
python -m src.cli web                     # Start web UI at http://127.0.0.1:5000

# Test Discord notifications
python -m src.cli test-discord

# Reprocess exports (fix counts/trigger downloads)
python -m src.cli reprocess --download
```

### Installation
```bash
pip install -r requirements.txt           # Install dependencies
pip install -e .                          # Install as editable package (optional)
```

## Architecture

### Core Components

**src/core.py (TDLWrapper)**
- Main wrapper around `tdl` CLI commands
- Handles export and download operations via subprocess
- Manages incremental sync logic using timestamps
- Special handling for Windows: tdl process doesn't exit cleanly, so downloads are monitored via log file and killed after 10s of inactivity
- Export always starts from timestamp 0 (1970) - incrementality comes from `tdl dl --skip-same` during downloads

**src/database.py (Database + SQLAlchemy Models)**
- SQLite database with SQLAlchemy ORM
- Core tables:
  - `chats`: Tracked Telegram chats with `sync_enabled`/`download_enabled` flags
  - `exports`: Export operations with time ranges and message/media counts
  - `downloads`: Download operations with file counts and sizes
  - `schedules`: Per-chat scheduling configuration with APScheduler job IDs
  - `job_logs`: Detailed execution logs for each scheduled run
- Includes migration system (`migrate_to_per_chat_scheduler`) that runs on startup

**src/scheduler.py (TDLScheduler)**
- APScheduler-based background scheduler
- Uses global cron schedule (default: `0 */6 * * *` - every 6 hours)
- Creates per-chat Schedule records but runs jobs as single batch
- Batch execution processes each chat completely (sync then download) before moving to next
- Thread-safe job execution prevents overlapping runs
- Auto-syncs chats from Telegram on first startup if database is empty

**src/cli.py**
- Click-based command-line interface
- Entry point for all operations
- Initializes Config, Database, TDLWrapper, DiscordNotifier, TDLScheduler

**src/config.py (Config)**
- YAML configuration with environment variable overrides
- Searches for config.yaml in current dir, ~/.tdl-wrapper, ~/.config/tdl-wrapper
- Key env vars: `TDL_DISCORD_WEBHOOK`, `TDL_PATH`, `TDL_DB_PATH`

**src/notifications.py (DiscordNotifier)**
- Discord webhook notifications for batch events, new files, errors
- Can be enabled/disabled per notification type in config

**src/web/app.py**
- Flask web dashboard for monitoring and manual triggers
- Manages scheduler lifecycle when running with web interface

### Important Architectural Details

**Incremental Sync Strategy**
- Exports always use full time range (0 to current timestamp)
- Incrementality handled by `tdl dl --skip-same --continue` flags during download
- Database tracks export/download history for each chat
- State management ensures no duplicate downloads

**Windows-Specific Handling**
- `tdl dl` command doesn't exit on Windows after completion
- TDLWrapper monitors log file size in `logs/download_{id}.log`
- Process killed after 10s of log inactivity (treated as success)
- Uses `subprocess.CREATE_NO_WINDOW` flag for background execution

**Scheduler Architecture**
- Migrated from batch-based to per-chat scheduling system
- Schedule table has `chat_id`, `job_type` (sync/download), `interval_seconds`, `apscheduler_job_id`
- Jobs controlled by `sync_enabled`/`download_enabled` flags on Chat model
- Single global cron job runs batch of all enabled schedules
- Prevents race conditions by running sync before download for each chat

**Session Management**
- Database uses SQLAlchemy sessions throughout
- Pattern: `session = db.get_session()` / `try:` / `finally: session.close()`
- Core operations sometimes re-fetch objects in new sessions to avoid detached instance errors

**File Organization**
- Exports: `./exports/{chat_id}/export_{timestamp}.json`
- Downloads: `./downloads/{chat_id}/` (if `organize_by_chat: true`)
- Logs: `./logs/download_{id}.log`
- Database: `./tdl_wrapper.db` (SQLite)

## Development Patterns

**Adding New CLI Commands**
1. Add command function in `src/cli.py` with `@cli.command()` decorator
2. Use Click options/arguments for parameters
3. Access shared objects via `ctx.obj` (config, db, wrapper, notifier)
4. Follow existing patterns for error handling and console output

**Database Changes**
1. Update model classes in `src/database.py`
2. Add migration logic to `migrate_to_per_chat_scheduler()` or create new migration function
3. Use `ALTER TABLE` with try/except for SQLite compatibility
4. Test with fresh database and existing database

**Scheduler Jobs**
1. Jobs defined in `TDLScheduler` class
2. Use `run_sync_job()` or `run_download_job()` for per-chat operations
3. Thread-safe execution with `_lock` and `_running_jobs` set
4. Always create JobLog for tracking execution history
5. Update Schedule record with `last_run_time` after completion

**Subprocess Execution**
- Use `TDLWrapper._run_command()` for all tdl CLI calls
- Handle timeouts and large outputs properly
- For downloads on Windows, use log file monitoring approach in `download_from_export()`
- Always flush stdout when logging from threads

## Configuration

Default configuration in `src/config.py:DEFAULT_CONFIG`. User config merges with defaults.

Key settings:
- `scheduler.cron_schedule`: Global cron expression for batch runs (default: `'0 */6 * * *'`)
- `scheduler.enabled`: Enable/disable scheduler
- `downloads.organize_by_chat`: Create per-chat subdirectories
- `exports.include_content`: Include message text in exports
- `discord.*`: Notification settings

## External Dependencies

**Required**
- `tdl` CLI must be installed and in PATH (or specified via `tdl_path` config)
- User must be logged in via `tdl login` before first use

**Python Packages**
- Click: CLI framework
- Flask: Web dashboard
- SQLAlchemy: Database ORM
- APScheduler: Background scheduling
- Rich: Terminal formatting
- PyYAML: Configuration
- humanize: Human-readable sizes

## Testing Workflows

**Manual Testing**
```bash
# 1. Fresh setup
cp config.example.yaml config.yaml
# Edit config.yaml with Discord webhook

# 2. Import chats
python -m src.cli sync-chats

# 3. Test single sync
python -m src.cli sync <chat_id>

# 4. Test scheduler (foreground)
python -m src.cli daemon --foreground

# 5. Test web dashboard
python -m src.cli web
```

**Verification**
- Check `tdl_wrapper.db` with SQLite browser
- Monitor `tdl_wrapper.log` for errors
- Check Discord notifications
- Verify files in `./downloads/` and `./exports/`

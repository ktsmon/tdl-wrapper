# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Overview

TDL Wrapper is a Python-based state management layer for the `tdl` CLI (Telegram Downloader). It provides incremental sync, automated scheduling, Discord notifications, and a web dashboard for downloading and managing Telegram content across multiple chats.

## Key Commands

### Docker Commands (Recommended for Production)

```bash
# Setup and build
make setup                                # Initial setup
make login                                # TDL authentication

# Container management
make up                                   # Start web dashboard
make daemon                               # Start scheduler daemon
make down                                 # Stop containers
make logs                                 # View logs

# CLI operations via Docker
make cli CMD='list'                       # List chats
make cli CMD='sync CHAT_ID'              # Sync chat
make cli CMD='sync --all'                # Sync all
make sync-chats                           # Import from Telegram

# Direct docker-compose
docker-compose exec tdl-wrapper python -m src.cli <command>
```

### Development and Testing (Local)
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

# Rename files to use message IDs
python -m src.cli rename <chat_id>        # Rename for specific chat
python -m src.cli rename --all            # Rename for all chats
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
- **Timestamp-based incremental exports**: Each export starts from the `end_timestamp` of the last successful export, ensuring only NEW messages are exported (no duplicates)
- **Pre-download auto-rename**: Before downloading, automatically renames any unrenamed files from previous downloads using all historical exports (ensures proper duplicate detection even after upgrades)
- **Smart download filtering**: After pre-rename, scans destination directory for existing message IDs, filters export JSON to exclude already-downloaded files, then creates temp filtered JSON for tdl to process
- **No --skip-same flag**: Since files are renamed to message IDs, tdl can't match them against export JSON. Instead, we pre-filter the JSON to only include undownloaded messages
- **Post-download auto-rename**: Files are automatically renamed to message IDs (e.g., 12345.jpg) after every download for guaranteed unique, chronological naming. Enabled by default via `rename_by_timestamp: true` config

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
- **Download-based incrementality**: Each export starts from `Chat.last_successful_download_timestamp + 1`
- **Failure recovery**: If export succeeds but download fails, next export will re-export those messages
- Export timestamps are only updated AFTER successful download completion
- First export for a chat starts from timestamp 0 (1970)
- Exports only contain NEW messages since last successful download, guaranteeing no duplicates
- **Smart pre-download process**: Before downloading, the wrapper:
  1. **Pre-rename safety check**: Renames any unrenamed files from ALL previous exports (handles upgrades from old versions)
  2. Scans destination directory to get all existing message IDs from renamed files (e.g., "12345.jpg" -> ID 12345)
  3. Parses export JSON to identify which message IDs are already downloaded
  4. Creates a filtered temp JSON containing ONLY undownloaded messages
  5. Runs `tdl dl` on the filtered JSON (using `--continue` flag for resume support)
  6. Renames newly downloaded files to message IDs
- **No --skip-same flag**: Can't use it because renamed files don't match original filenames in export JSON
- Message ID-based file naming provides guaranteed unique, chronological ordering
- Database tracks download completion timestamps for precise incrementality and automatic retry

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
- File naming: `{message_id}.{ext}` (if `rename_by_timestamp: true`) - IDs are chronological and unique
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
- `downloads.rename_by_timestamp`: Rename files to Unix timestamps with duplicate handling (default: `true`)
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

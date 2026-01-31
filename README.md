# TDL Wrapper

Advanced state management wrapper for [tdl (Telegram Downloader)](https://github.com/iyear/tdl) with automated scheduling, Discord notifications, and web dashboard monitoring.

## Features

- **True Incremental Sync**: Each export starts from the last message timestamp, ensuring only NEW messages are downloaded (no duplicates, no re-downloads)
- **Message ID-Based Naming**: Automatically rename downloaded files to their message IDs for perfect chronological organization
- **Per-Chat Scheduling**: Enable/disable automated sync and download for each chat individually
- **Cron-Based Scheduler**: Configurable global cron schedule (default: every 6 hours)
- **Discord Notifications**: Real-time notifications for operations, new files, and errors
- **Web Dashboard**: Modern web interface to monitor chats, configure schedules, and trigger operations
- **SQLite Database**: Full history tracking of all exports, downloads, and scheduled jobs
- **Job Logs**: Detailed execution history with incremental statistics for each run
- **Auto-Import**: Automatically syncs chat list from Telegram on first startup

## Why TDL Wrapper?

While `tdl` is excellent for downloading from Telegram, managing continuous downloads across multiple chats requires:
- **True incrementality**: Only download NEW messages since last sync (not filename-based)
- **Timestamp tracking**: Database tracks exact message ranges to prevent duplicates
- **Scheduling**: Automated regular updates
- **Monitoring**: Progress tracking and execution history
- **Multi-chat management**: Efficient handling of multiple channels/groups

TDL Wrapper solves these problems by adding a comprehensive state management layer with timestamp-based incremental exports on top of `tdl`.

## Prerequisites

### Docker Deployment (Recommended)
- Docker and Docker Compose installed on your server
- See [DOCKER.md](DOCKER.md) for complete Docker setup guide

### Manual Installation
1. **tdl CLI** installed and configured
   - Install: https://docs.iyear.me/tdl/getting-started/installation/
   - Login: `tdl login` (complete authentication)

2. **Python 3.8+**

## Installation

### Option 1: Docker (Recommended for Production)

**Quick setup on Debian/Ubuntu:**
```bash
# Clone the repository
git clone <repository-url>
cd tdl-wrapper

# Copy example config
cp config.example.yaml config.yaml
# Edit config.yaml with your settings

# Build and start containers
docker-compose build
docker-compose up -d

# Authenticate with Telegram
docker-compose exec tdl-wrapper tdl login
```

**Manual Docker setup:**
```bash
# Clone and navigate
git clone <repository-url>
cd tdl-wrapper

# Create config and data directories
cp config.docker.yaml config.yaml
mkdir -p data/{downloads,exports,logs,db,tdl}

# Build and authenticate
docker-compose build
docker-compose run --rm tdl-wrapper tdl login

# Start the application
docker-compose up -d
```

See [DOCKER.md](DOCKER.md) for detailed Docker documentation.

### Option 2: Manual Installation

```bash
# Clone the repository
git clone <repository-url>
cd tdl-wrapper

# Install dependencies
pip install -r requirements.txt

# Optional: Install as package
pip install -e .
```

## Quick Start

### 1. Configure

Create `config.yaml` from the example:

```bash
cp config.example.yaml config.yaml
```

Edit `config.yaml` to add your Discord webhook (optional):

```yaml
discord:
  enabled: true
  webhook_url: "https://discord.com/api/webhooks/YOUR_WEBHOOK_URL"
```

Or use environment variables:
```bash
export TDL_DISCORD_WEBHOOK="https://discord.com/api/webhooks/YOUR_WEBHOOK_URL"
```

### 2. Import Chats from Telegram

The wrapper automatically imports chats on first startup, or you can manually trigger:

```bash
# Import all your chats from Telegram
python -m src.cli sync-chats

# Or filter specific types
python -m src.cli sync-chats --filter "Type contains 'channel'"
```

### 3. List Tracked Chats

```bash
python -m src.cli list
```

### 4. Sync a Chat (Export + Download)

```bash
# Sync specific chat
python -m src.cli sync <chat_id>

# Sync all active chats
python -m src.cli sync --all
```

### 5. Start Web Dashboard

```bash
python -m src.cli web
```

Open http://127.0.0.1:5000 in your browser to:
- View all chats and their sync/download status
- Enable/disable scheduled sync and download per chat
- Manually trigger sync or download operations
- Configure global cron schedule
- View job execution history and logs
- Monitor running operations in real-time

The web dashboard automatically starts the background scheduler.

### 6. Start Automated Scheduler (Alternative)

If you prefer running without the web interface:

```bash
# Run in foreground (recommended for testing)
python -m src.cli daemon --foreground

# Or as background service (use systemd/supervisor for production)
python -m src.cli daemon
```

## Usage Guide

### Basic Commands

#### Import and Manage Chats

```bash
# Import all chats from Telegram
python -m src.cli sync-chats

# Filter channels only
python -m src.cli sync-chats --filter "Type contains 'channel'"

# Filter by name pattern
python -m src.cli sync-chats --filter "VisibleName contains 'News'"

# List all tracked chats
python -m src.cli list

# Add a chat manually
python -m src.cli add -1001234567890 --name "My Channel"

# Show detailed status for a chat
python -m src.cli status -1001234567890
```

#### Export and Download Operations

```bash
# Export messages only
python -m src.cli export <chat_id>

# Download files from latest export
python -m src.cli download <chat_id>

# Full sync (export + download)
python -m src.cli sync <chat_id>

# Sync all active chats
python -m src.cli sync --all
```

#### Reprocess Exports

Fix message/media counts and trigger missing downloads:

```bash
# Reparse export files to fix counts
python -m src.cli reprocess

# Also trigger downloads for exports with missing media
python -m src.cli reprocess --download
```

#### Rename Downloaded Files

Rename already downloaded files to use message IDs (chronological ordering):

```bash
# Rename files for a specific chat
python -m src.cli rename <chat_id>

# Rename files for all active chats
python -m src.cli rename --all
```

This is useful if you downloaded files before enabling `rename_by_timestamp: true` in the config, or if you want to re-apply naming after fixing issues.

### How Incremental Sync Works

TDL Wrapper uses **timestamp-based incremental exports** combined with `tdl`'s `--skip-same` flag:

1. **First Sync**: Exports all messages from the beginning of time
   ```
   Export: 0 → current_timestamp
   Download: all media files (with --skip-same --continue)
   Tracks: last_successful_download_timestamp = max(downloaded file timestamps)
   ```

2. **Second Sync**: Exports ONLY new messages since last successful download
   ```
   Export: last_successful_download_timestamp+1 → current_timestamp
   Download: only new files (with --skip-same --continue for safety)
   Tracks: updates last_successful_download_timestamp
   ```

**Key Features:**
- **True incrementality**: Only new messages are exported and downloaded
- **Failure recovery**: If download fails, next sync re-exports those messages
- **No duplicates**: `--skip-same` prevents re-downloading files that already exist
- **Resume capability**: `--continue` allows resuming interrupted downloads

### Automated Scheduling

The scheduler uses a global cron expression to run batch operations for enabled chats.

**Default Schedule**: `0 */6 * * *` (every 6 hours at the top of the hour)

#### Using the Web Dashboard

1. Start the web dashboard: `python -m src.cli web`
2. Navigate to Settings to change the cron schedule
3. Enable sync/download for specific chats using the toggle switches
4. The scheduler runs automatically in the background

#### Using the Daemon

```bash
# Start scheduler in foreground (recommended for testing)
python -m src.cli daemon --foreground

# Check logs
tail -f tdl_wrapper.log
```

#### Production Deployment with systemd (Linux)

Create `/etc/systemd/system/tdl-wrapper.service`:

```ini
[Unit]
Description=TDL Wrapper Daemon
After=network.target

[Service]
Type=simple
User=your-user
WorkingDirectory=/path/to/tdl-wrapper
ExecStart=/usr/bin/python3 -m src.cli daemon --foreground
Restart=always

[Install]
WantedBy=multi-user.target
```

Enable and start:
```bash
sudo systemctl enable tdl-wrapper
sudo systemctl start tdl-wrapper
sudo systemctl status tdl-wrapper
```

### Discord Notifications

The wrapper sends Discord notifications for:
- **Chat Progress**: When export/download starts, completes, or fails
- **New Files**: Alert when new files are downloaded with count and size
- **Errors**: When operations fail with error details

Configure in `config.yaml`:

```yaml
discord:
  enabled: true
  webhook_url: "https://discord.com/api/webhooks/..."
  notify_on_start: true
  notify_on_complete: true
  notify_on_error: true
```

Test your webhook:
```bash
python -m src.cli test-discord
```

### Web Dashboard Features

Start the dashboard:
```bash
python -m src.cli web
```

#### Features:

**Dashboard Overview**
- Real-time statistics: total chats, exports, downloads, files, size
- Activity monitor showing running and recent operations

**Chat Management**
- View all tracked chats with detailed status
- Enable/disable scheduled sync per chat
- Enable/disable scheduled download per chat
- Toggle chat active/inactive status
- Manually trigger sync or download operations

**Scheduler Configuration**
- View current cron schedule
- Update cron schedule without restarting
- View next scheduled run time with countdown

**Job History**
- View execution logs for all operations
- Per-chat job history with timestamps and statistics
- Filter by sync or download operations
- See incremental stats (messages added, files downloaded)

**Export/Download History**
- View complete export history per chat
- View complete download history per chat
- File counts, sizes, and durations

## Configuration Reference

### Full `config.yaml` Example

```yaml
# Path to tdl executable
tdl_path: "tdl"

# Database configuration
database:
  path: "tdl_wrapper.db"

# Download settings
downloads:
  base_directory: "./downloads"
  organize_by_chat: true  # Create subdirectory for each chat
  rename_by_timestamp: true  # Rename files to message ID (e.g., 12345.jpg, 67890.mp4) for chronological ordering

# Export settings
exports:
  base_directory: "./exports"
  include_content: true  # Include message text
  include_all: false     # Include non-media messages

# Scheduling
scheduler:
  enabled: true
  cron_schedule: "0 */6 * * *"  # Every 6 hours (cron format)
  timezone: "UTC"

# Discord notifications
discord:
  enabled: true
  webhook_url: "https://discord.com/api/webhooks/YOUR_WEBHOOK_URL"
  notify_on_start: true
  notify_on_complete: true
  notify_on_error: true
  notify_batch_summary: true

# Web dashboard
web:
  enabled: true
  host: "127.0.0.1"
  port: 5000
  debug: false

# Logging
logging:
  level: "INFO"  # DEBUG, INFO, WARNING, ERROR, CRITICAL
  file: "tdl_wrapper.log"
  max_bytes: 10485760  # 10MB
  backup_count: 5
```

### Environment Variables

Override config with environment variables:

```bash
export TDL_PATH="/usr/local/bin/tdl"
export TDL_DISCORD_WEBHOOK="https://discord.com/api/webhooks/..."
export TDL_DB_PATH="/var/lib/tdl-wrapper/db.sqlite"
```

### Cron Schedule Format

The `cron_schedule` uses standard cron syntax:

```
* * * * *
│ │ │ │ │
│ │ │ │ └─── Day of week (0-7, Sunday=0 or 7)
│ │ │ └───── Month (1-12)
│ │ └─────── Day of month (1-31)
│ └───────── Hour (0-23)
└─────────── Minute (0-59)
```

Examples:
- `0 */6 * * *` - Every 6 hours at the top of the hour
- `0 */1 * * *` - Every hour
- `0 0,12 * * *` - At midnight and noon
- `0 9 * * *` - Every day at 9 AM
- `0 9 * * 1-5` - Every weekday at 9 AM

## Project Structure

```
tdl-wrapper/
├── src/
│   ├── __init__.py
│   ├── cli.py              # Command-line interface (Click)
│   ├── core.py             # Core TDL wrapper logic
│   ├── database.py         # SQLite models (SQLAlchemy)
│   ├── config.py           # Configuration management
│   ├── scheduler.py        # APScheduler integration
│   ├── notifications.py    # Discord webhook notifications
│   ├── logging_config.py   # Logging setup
│   └── web/
│       ├── __init__.py
│       ├── app.py          # Flask web application
│       └── templates/
│           └── dashboard.html
├── data/                   # Persistent data (Docker volume)
│   ├── db/                 # SQLite database
│   ├── downloads/          # Downloaded files
│   ├── exports/            # Export JSON files
│   ├── logs/               # Operation logs
│   └── tdl/                # TDL session data
├── config.yaml             # Configuration file (created from example)
├── config.example.yaml     # Example configuration
├── docker-compose.yml      # Docker Compose configuration
├── Dockerfile              # Docker image definition
└── requirements.txt        # Python dependencies
```

## Database Schema

The wrapper uses SQLite with the following tables:

- **chats**: Tracked Telegram chats with `sync_enabled`/`download_enabled` flags
- **exports**: Export operation history with time ranges, message/media counts
- **downloads**: Download operation history with file counts and sizes
- **schedules**: Per-chat scheduling configuration with APScheduler job IDs
- **job_logs**: Detailed execution logs for each scheduled run with incremental stats

All operations are tracked with full history for auditing and recovery.

## Advanced Usage

### Custom Cron Schedule

Edit `config.yaml` or use the web dashboard:
```yaml
scheduler:
  cron_schedule: "0 */3 * * *"  # Every 3 hours
```

Or update via web interface at http://127.0.0.1:5000

### Filtering Chats During Import

When syncing from Telegram, use expressions:

```bash
# Only channels
python -m src.cli sync-chats --filter "Type contains 'channel'"

# Only groups with topics
python -m src.cli sync-chats --filter "len(Topics) > 0"

# Specific name pattern
python -m src.cli sync-chats --filter "VisibleName contains 'Tech'"
```

### Manual tdl Operations

For manual control, use `tdl` directly:

```bash
# Export specific time range
tdl chat export -c <chat_id> -i 1665700000,1665761624 -o export.json

# Download from export
tdl dl -f export.json -d ./downloads
```

Then import to database:
```bash
python -m src.cli add <chat_id> --name "Manual Chat"
```

## Troubleshooting

### "tdl command not found"

Make sure `tdl` is installed and in PATH:
```bash
which tdl
# or specify full path in config.yaml
tdl_path: "/usr/local/bin/tdl"
```

### "Not logged in to Telegram"

Run tdl login first:
```bash
tdl login
```

### Discord notifications not working

Test the webhook:
```bash
python -m src.cli test-discord
```

Check webhook URL is correct in config.

### Database locked errors

Make sure only one process is running at a time. If using daemon + CLI simultaneously, you may encounter locks.

### Downloads hang on Windows

The wrapper includes special handling for Windows where `tdl dl` doesn't exit properly. It monitors log files and kills the process after 10 seconds of inactivity. Check `logs/download_*.log` files if issues occur.

## API Documentation

The web dashboard exposes a REST API for integration:

### Statistics
- `GET /api/stats` - Get overall statistics

### Chats
- `GET /api/chats` - Get all chats with status
- `POST /api/chat/<id>/toggle` - Toggle chat active status
- `POST /api/chat/<id>/toggle_sync` - Enable/disable scheduled sync
- `POST /api/chat/<id>/toggle_download` - Enable/disable scheduled download
- `POST /api/chat/<id>/trigger_sync` - Manually trigger sync
- `POST /api/chat/<id>/trigger_download` - Manually trigger download

### History
- `GET /api/chat/<id>/exports` - Get export history
- `GET /api/chat/<id>/downloads` - Get download history
- `GET /api/chat/<id>/job_logs` - Get job execution logs
- `GET /api/job_logs/recent` - Get recent job logs across all chats

### Scheduler
- `GET /api/scheduler/config` - Get scheduler configuration
- `POST /api/scheduler/config` - Update scheduler configuration
- `GET /api/scheduler/next_run` - Get next scheduled run time

### Activity
- `GET /api/activity` - Get running and recent operations

## Contributing

Contributions welcome! Please:
1. Fork the repository
2. Create a feature branch
3. Make your changes
4. Submit a pull request

## License

MIT License - see LICENSE file for details

## Acknowledgments

- [tdl](https://github.com/iyear/tdl) - The excellent Telegram downloader this wraps
- Built with: Click, Flask, SQLAlchemy, APScheduler, Rich, Discord Webhooks

## Support

For issues and questions, please open an issue on GitHub.

---

**Made for efficient Telegram content management**

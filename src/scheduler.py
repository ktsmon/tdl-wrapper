"""Scheduling system for automated sync operations."""

import datetime
import time
import threading
import re
from typing import Dict, Any, Optional
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.interval import IntervalTrigger
from apscheduler.triggers.cron import CronTrigger
from rich.console import Console

from .database import Database, Download, Export, Chat, Schedule, JobLog
from .core import TDLWrapper
from .notifications import DiscordNotifier

console = Console()


class TDLScheduler:
    """Manages scheduled sync operations."""

    def __init__(
        self,
        wrapper: TDLWrapper,
        db: Database,
        config: Dict[str, Any],
        notifier: DiscordNotifier = None
    ):
        """
        Initialize scheduler.

        Args:
            wrapper: TDL wrapper instance
            db: Database instance
            config: Scheduler configuration
            notifier: Discord notifier instance (optional)
        """
        self.wrapper = wrapper
        self.db = db
        self.config = config
        self.notifier = notifier

        self.scheduler = BackgroundScheduler(
            timezone=config.get('timezone', 'UTC')
        )

        # Thread safety for job execution
        self._running_jobs = set()  # Track running jobs to prevent overlaps
        self._lock = threading.Lock()

    def start(self):
        """Start the scheduler and create per-chat jobs."""
        if not self.config.get('enabled', True):
            console.print("[yellow]Scheduler is disabled in configuration[/yellow]")
            return

        # Auto-sync chats on startup
        self._ensure_chats_synced()

        # Initialize schedules and create jobs
        self._initialize_schedules()
        self._create_all_jobs()

        self.scheduler.start()
        console.print("[green]OK Scheduler started[/green]")
        console.print(f"[dim]Default interval: {self.config.get('default_interval', '1h')}[/dim]")

        # Print job summary
        jobs = self.scheduler.get_jobs()
        console.print(f"[dim]Active jobs: {len(jobs)}[/dim]")

    def stop(self):
        """Stop the scheduler."""
        self.scheduler.shutdown(wait=True)
        console.print("[yellow]Scheduler stopped[/yellow]")

    def reload_jobs(self):
        """Reload all jobs with current configuration without stopping the scheduler."""
        console.print("[yellow]Reloading scheduler jobs...[/yellow]")

        # Remove all existing jobs
        self.scheduler.remove_all_jobs()

        # Recreate jobs with new configuration
        self._create_all_jobs()

        console.print("[green]OK Jobs reloaded with new schedule[/green]")

    def validate_cron_schedule(self, cron_expr: str) -> tuple:
        """
        Validate a cron expression.

        Args:
            cron_expr: Cron expression to validate (e.g., '0 */6 * * *')

        Returns:
            tuple: (is_valid: bool, error_message: str)
        """
        try:
            trigger = CronTrigger.from_crontab(cron_expr)
            # Test if we can get next run time
            next_run = trigger.get_next_fire_time(None, datetime.datetime.now(trigger.timezone))
            if next_run is None:
                return False, "Cron expression doesn't generate any run times"
            return True, ""
        except ValueError as e:
            return False, str(e)
        except Exception as e:
            return False, f"Unknown error: {str(e)}"

    def _parse_interval(self, interval_str: str) -> Dict[str, int]:
        """
        Parse interval string like '1h', '30m', '2d' into kwargs for IntervalTrigger.

        Args:
            interval_str: Interval string (e.g., '1h', '30m', '2d')

        Returns:
            Dictionary with interval kwargs
        """
        interval_str = interval_str.strip().lower()

        # Extract number and unit
        match = re.match(r'(\d+)([smhd])', interval_str)
        if not match:
            console.print(f"[yellow]Invalid interval: {interval_str}, using 1h[/yellow]")
            return {'hours': 1}

        value = int(match.group(1))
        unit = match.group(2)

        unit_map = {
            's': 'seconds',
            'm': 'minutes',
            'h': 'hours',
            'd': 'days'
        }

        return {unit_map[unit]: value}

    def _parse_interval_to_seconds(self, interval_str: str) -> int:
        """
        Parse interval string like '1h', '30m', '2d' into seconds.

        Args:
            interval_str: Interval string (e.g., '1h', '30m', '2d')

        Returns:
            Interval in seconds
        """
        interval_str = interval_str.strip().lower()

        match = re.match(r'(\d+)([smhd])', interval_str)
        if not match:
            console.print(f"[yellow]Invalid interval: {interval_str}, using 1h[/yellow]")
            return 3600

        value = int(match.group(1))
        unit = match.group(2)

        unit_seconds = {
            's': 1,
            'm': 60,
            'h': 3600,
            'd': 86400
        }

        return value * unit_seconds[unit]

    def _ensure_chats_synced(self):
        """Ensure chats are synced from Telegram on startup."""
        console.print("[yellow]Checking for chats in database...[/yellow]")
        session = self.db.get_session()
        try:
            chat_count = session.query(Chat).count()
            if chat_count == 0:
                console.print("[yellow]No chats found. Auto-importing from Telegram...[/yellow]")
                self.wrapper.sync_chats_to_db()
                chat_count = session.query(Chat).count()
                console.print(f"[green]OK Successfully imported {chat_count} chats[/green]")
            else:
                console.print(f"[green]OK Found {chat_count} chats in database[/green]")
        finally:
            session.close()

    def _initialize_schedules(self):
        """Create Schedule records for chats that don't have them."""
        session = self.db.get_session()
        try:
            # Get all active chats
            chats = session.query(Chat).filter_by(is_active=True).all()

            default_interval = self.config.get('default_interval', '1h')
            interval_seconds = self._parse_interval_to_seconds(default_interval)

            schedules_created = 0

            for chat in chats:
                # Create sync schedule if not exists
                sync_schedule = session.query(Schedule).filter_by(
                    chat_id=chat.id,
                    job_type='sync'
                ).first()

                if not sync_schedule:
                    sync_schedule = Schedule(
                        chat_id=chat.id,
                        job_type='sync',
                        schedule_type='sync',  # Legacy field for backward compatibility
                        interval=default_interval,  # Legacy field for backward compatibility
                        interval_seconds=interval_seconds,
                        is_enabled=chat.sync_enabled,
                        apscheduler_job_id=f"sync_chat_{chat.id}"
                    )
                    session.add(sync_schedule)
                    schedules_created += 1

                # Create download schedule if not exists
                download_schedule = session.query(Schedule).filter_by(
                    chat_id=chat.id,
                    job_type='download'
                ).first()

                if not download_schedule:
                    download_schedule = Schedule(
                        chat_id=chat.id,
                        job_type='download',
                        schedule_type='download',  # Legacy field for backward compatibility
                        interval=default_interval,  # Legacy field for backward compatibility
                        interval_seconds=interval_seconds,
                        is_enabled=chat.download_enabled,
                        apscheduler_job_id=f"download_chat_{chat.id}"
                    )
                    session.add(download_schedule)
                    schedules_created += 1

            session.commit()

            if schedules_created > 0:
                console.print(f"[green]OK Created {schedules_created} schedule records[/green]")
            else:
                console.print("[dim]All schedules already exist[/dim]")

        finally:
            session.close()

    def _create_all_jobs(self):
        """Create global cron-based batch jobs for all enabled schedules."""
        session = self.db.get_session()
        try:
            cron_schedule = self.config.get('cron_schedule', '0 */6 * * *')

            # Validate and create trigger
            try:
                trigger = CronTrigger.from_crontab(cron_schedule)
            except ValueError as e:
                console.print(f"[red]Invalid cron schedule '{cron_schedule}': {e}[/red]")
                console.print("[yellow]Please fix the cron schedule in config.yaml[/yellow]")
                return

            # Calculate next run time
            next_run = trigger.get_next_fire_time(None, datetime.datetime.now(trigger.timezone))

            # Get all enabled schedules and group by job type
            enabled_schedules = session.query(Schedule).filter_by(is_enabled=True).all()

            sync_chats = []
            download_chats = []

            for schedule in enabled_schedules:
                # Update next_run_time in database
                schedule.next_run_time = next_run

                if schedule.job_type == 'sync':
                    sync_chats.append(schedule.chat_id)
                elif schedule.job_type == 'download':
                    download_chats.append(schedule.chat_id)

            session.commit()

            # Create a single batch job that runs sync then download
            # This prevents race conditions by ensuring sync completes before download
            if sync_chats or download_chats:
                self.scheduler.add_job(
                    lambda: self._run_batch_sync_and_download(sync_chats, download_chats),
                    trigger=trigger,
                    id='global_batch_job',
                    name='Global Batch Job',
                    replace_existing=True
                )
                console.print(f"[green]OK Created global batch job for {len(sync_chats)} sync + {len(download_chats)} download[/green]")

            if next_run:
                console.print(f"[dim]Next sync at: {next_run.strftime('%Y-%m-%d %H:%M:%S %Z')}[/dim]")

        finally:
            session.close()

    def _run_batch_sync_and_download(self, sync_chat_ids: list, download_chat_ids: list):
        """
        Run sync and download jobs for chats, processing each chat completely before moving to next.
        This prevents race conditions by ensuring sync completes before download for each chat.

        Args:
            sync_chat_ids: List of chat IDs to sync
            download_chat_ids: List of chat IDs to download
        """
        # Get unique chat IDs that need processing
        all_chat_ids = list(set(sync_chat_ids + download_chat_ids))

        console.print(f"\n[bold cyan]BATCH: Starting batch job for {len(all_chat_ids)} chats[/bold cyan]")

        for chat_id in all_chat_ids:
            # Sync first if this chat needs syncing
            if chat_id in sync_chat_ids:
                console.print(f"\n[bold cyan]Syncing chat {chat_id}...[/bold cyan]")
                self.run_sync_job(chat_id, trigger='scheduled')

            # Then download if this chat needs downloading
            if chat_id in download_chat_ids:
                console.print(f"[bold green]Downloading chat {chat_id}...[/bold green]")
                self.run_download_job(chat_id, trigger='scheduled')

        # Update next_run_time for all processed schedules
        if sync_chat_ids:
            self._update_next_run_time_for_all('sync')
        if download_chat_ids:
            self._update_next_run_time_for_all('download')

        console.print(f"[bold green]OK Batch job completed[/bold green]\n")

    def _run_batch_sync(self, chat_ids: list):
        """
        Run sync jobs for multiple chats in sequence.

        Args:
            chat_ids: List of chat IDs to sync
        """
        console.print(f"\n[bold cyan]SYNC: Starting batch sync for {len(chat_ids)} chats[/bold cyan]")

        for chat_id in chat_ids:
            self.run_sync_job(chat_id, trigger='scheduled')

        # Update next_run_time for all syncs
        self._update_next_run_time_for_all('sync')

        console.print(f"[bold cyan]OK Batch sync completed[/bold cyan]\n")

    def _run_batch_download(self, chat_ids: list):
        """
        Run download jobs for multiple chats in sequence.

        Args:
            chat_ids: List of chat IDs to download
        """
        console.print(f"\n[bold green]DOWNLOAD: Starting batch download for {len(chat_ids)} chats[/bold green]")

        for chat_id in chat_ids:
            self.run_download_job(chat_id, trigger='scheduled')

        # Update next_run_time for all downloads
        self._update_next_run_time_for_all('download')

        console.print(f"[bold green]OK Batch download completed[/bold green]\n")

    def _update_next_run_time_for_all(self, job_type: str):
        """
        Update next_run_time for all schedules of a given type.

        Args:
            job_type: 'sync' or 'download'
        """
        cron_schedule = self.config.get('cron_schedule', '0 */6 * * *')

        try:
            trigger = CronTrigger.from_crontab(cron_schedule)
            next_run = trigger.get_next_fire_time(None, datetime.datetime.now(trigger.timezone))

            session = self.db.get_session()
            try:
                schedules = session.query(Schedule).filter_by(job_type=job_type, is_enabled=True).all()
                for schedule in schedules:
                    schedule.next_run_time = next_run
                session.commit()

                console.print(f"[dim]Next {job_type} at: {next_run.strftime('%Y-%m-%d %H:%M:%S %Z')}[/dim]")
            finally:
                session.close()
        except Exception as e:
            console.print(f"[yellow]Warning: Could not update next_run_time: {e}[/yellow]")

    def _create_job_for_schedule(self, schedule: Schedule):
        """
        Create an APScheduler job for a schedule.

        Args:
            schedule: Schedule instance
        """
        trigger = IntervalTrigger(seconds=schedule.interval_seconds)

        # Determine which function to call
        if schedule.job_type == 'sync':
            func = lambda: self.run_sync_job(schedule.chat_id, trigger='scheduled')
            name = f"Sync Chat {schedule.chat_id}"
        elif schedule.job_type == 'download':
            func = lambda: self.run_download_job(schedule.chat_id, trigger='scheduled')
            name = f"Download Chat {schedule.chat_id}"
        else:
            console.print(f"[yellow]Unknown job type: {schedule.job_type}[/yellow]")
            return

        self.scheduler.add_job(
            func,
            trigger=trigger,
            id=schedule.apscheduler_job_id,
            name=name,
            replace_existing=True
        )

    def run_sync_job(self, chat_id: int, trigger: str = 'scheduled'):
        """
        Execute sync (export) job for a chat.

        Args:
            chat_id: Chat ID to sync
            trigger: 'scheduled' or 'manual'
        """
        job_key = f"sync_{chat_id}"

        # Prevent overlapping jobs
        with self._lock:
            if job_key in self._running_jobs:
                console.print(f"[yellow]Sync job for chat {chat_id} is already running[/yellow]")
                return
            self._running_jobs.add(job_key)

        session = self.db.get_session()
        job_log = None
        start_time = time.time()

        try:
            # Get chat
            chat = session.query(Chat).filter_by(id=chat_id).first()
            if not chat:
                console.print(f"[red]Chat {chat_id} not found[/red]")
                return

            # Check if sync is enabled
            if not chat.sync_enabled and trigger == 'scheduled':
                console.print(f"[dim]Sync disabled for {chat.chat_name}[/dim]")
                return

            console.print(f"\n[bold cyan]Syncing: {chat.chat_name}[/bold cyan]")

            # Create job log
            job_log = self.db.create_job_log(chat_id, 'sync', trigger)

            # Notify start
            if self.notifier and trigger == 'scheduled':
                self.notifier.notify_chat_progress(
                    chat.chat_name,
                    chat.chat_id,
                    'export',
                    'started'
                )

            # Export messages
            export = self.wrapper.export_messages(chat)

            if export:
                # Calculate incremental stats
                messages_added = export.message_count
                media_items_found = export.media_count

                # Update job log
                duration = int(time.time() - start_time)
                self.db.update_job_log(
                    job_log.id,
                    status='completed',
                    completed_at=datetime.datetime.utcnow(),
                    duration_seconds=duration,
                    messages_added=messages_added,
                    media_items_found=media_items_found,
                    export_id=export.id
                )

                # Update schedule
                schedule = self.db.get_schedule(chat_id, 'sync')
                if schedule:
                    self.db.update_schedule(
                        schedule.id,
                        last_run_time=datetime.datetime.utcnow()
                    )

                # Update chat
                chat.last_checked = datetime.datetime.utcnow()
                session.commit()

                console.print(
                    f"[green]OK Synced {chat.chat_name}: "
                    f"{messages_added} messages, {media_items_found} media ({duration}s)[/green]"
                )

                # Notify completion
                if self.notifier and trigger == 'scheduled':
                    self.notifier.notify_chat_progress(
                        chat.chat_name,
                        chat.chat_id,
                        'export',
                        'completed',
                        {
                            'message_count': messages_added,
                            'media_count': media_items_found,
                            'duration_seconds': duration
                        }
                    )

            else:
                # Sync failed
                duration = int(time.time() - start_time)
                self.db.update_job_log(
                    job_log.id,
                    status='failed',
                    completed_at=datetime.datetime.utcnow(),
                    duration_seconds=duration,
                    error_message='Export returned None'
                )

                console.print(f"[red]FAILED Failed to sync {chat.chat_name}[/red]")

                if self.notifier:
                    self.notifier.notify_chat_progress(
                        chat.chat_name,
                        chat.chat_id,
                        'export',
                        'failed'
                    )

        except Exception as e:
            duration = int(time.time() - start_time)
            console.print(f"[red]Error syncing chat {chat_id}: {e}[/red]")

            if job_log:
                self.db.update_job_log(
                    job_log.id,
                    status='failed',
                    completed_at=datetime.datetime.utcnow(),
                    duration_seconds=duration,
                    error_message=str(e)
                )

            if self.notifier:
                self.notifier.notify_error(
                    f"Error syncing chat",
                    {'chat_id': chat_id, 'error': str(e)}
                )

        finally:
            session.close()
            with self._lock:
                self._running_jobs.discard(job_key)

    def run_download_job(self, chat_id: int, trigger: str = 'scheduled'):
        """
        Execute download job for a chat (depends on successful sync).

        Args:
            chat_id: Chat ID to download media for
            trigger: 'scheduled' or 'manual'
        """
        job_key = f"download_{chat_id}"

        # Prevent overlapping jobs
        with self._lock:
            if job_key in self._running_jobs:
                console.print(f"[yellow]Download job for chat {chat_id} is already running[/yellow]")
                return
            self._running_jobs.add(job_key)

        session = self.db.get_session()
        job_log = None
        start_time = time.time()

        try:
            # Get chat
            chat = session.query(Chat).filter_by(id=chat_id).first()
            if not chat:
                console.print(f"[red]Chat {chat_id} not found[/red]")
                return

            # Check if download is enabled
            if not chat.download_enabled and trigger == 'scheduled':
                console.print(f"[dim]Download disabled for {chat.chat_name}[/dim]")
                return

            # Get last completed export for this chat
            last_export = session.query(Export)\
                .filter_by(chat_id=chat_id)\
                .order_by(Export.id.desc())\
                .first()

            if not last_export:
                console.print(f"[dim]No export found for {chat.chat_name}[/dim]")
                return

            if last_export.media_count == 0:
                console.print(f"[dim]No media to download for {chat.chat_name}[/dim]")
                return

            # Check if already downloaded
            existing_download = session.query(Download)\
                .filter_by(export_id=last_export.id)\
                .first()

            if existing_download:
                console.print(f"[dim]Already downloaded for {chat.chat_name}[/dim]")
                return

            console.print(f"\n[bold green]Downloading: {chat.chat_name}[/bold green]")

            # Create job log
            job_log = self.db.create_job_log(chat_id, 'download', trigger)

            # Notify start
            if self.notifier and trigger == 'scheduled':
                self.notifier.notify_chat_progress(
                    chat.chat_name,
                    chat.chat_id,
                    'download',
                    'started'
                )

            # Download files
            success = self.wrapper.download_from_export(last_export)

            if success:
                # Get download record
                download = session.query(Download)\
                    .filter_by(export_id=last_export.id)\
                    .order_by(Download.id.desc())\
                    .first()

                if download:
                    # Incremental stats
                    files_downloaded = download.files_count
                    bytes_downloaded = download.total_size_bytes

                    # Update job log
                    duration = int(time.time() - start_time)
                    self.db.update_job_log(
                        job_log.id,
                        status='completed',
                        completed_at=datetime.datetime.utcnow(),
                        duration_seconds=duration,
                        files_downloaded=files_downloaded,
                        bytes_downloaded=bytes_downloaded,
                        download_id=download.id,
                        export_id=last_export.id
                    )

                    # Update schedule
                    schedule = self.db.get_schedule(chat_id, 'download')
                    if schedule:
                        self.db.update_schedule(
                            schedule.id,
                            last_run_time=datetime.datetime.utcnow()
                        )

                    console.print(
                        f"[green]OK Downloaded {chat.chat_name}: "
                        f"{files_downloaded} files, {bytes_downloaded:,} bytes ({duration}s)[/green]"
                    )

                    # Notify completion
                    if self.notifier and trigger == 'scheduled':
                        self.notifier.notify_chat_progress(
                            chat.chat_name,
                            chat.chat_id,
                            'download',
                            'completed',
                            {
                                'files_count': files_downloaded,
                                'total_size_bytes': bytes_downloaded,
                                'duration_seconds': duration
                            }
                        )

                        # Notify about new files
                        if files_downloaded > 0:
                            self.notifier.notify_new_files(
                                chat.chat_name,
                                chat.chat_id,
                                files_downloaded,
                                bytes_downloaded
                            )

            else:
                # Download failed
                duration = int(time.time() - start_time)
                self.db.update_job_log(
                    job_log.id,
                    status='failed',
                    completed_at=datetime.datetime.utcnow(),
                    duration_seconds=duration,
                    error_message='Download returned False'
                )

                console.print(f"[red]FAILED Failed to download {chat.chat_name}[/red]")

                if self.notifier:
                    self.notifier.notify_chat_progress(
                        chat.chat_name,
                        chat.chat_id,
                        'download',
                        'failed'
                    )

        except Exception as e:
            duration = int(time.time() - start_time)
            console.print(f"[red]Error downloading for chat {chat_id}: {e}[/red]")

            if job_log:
                self.db.update_job_log(
                    job_log.id,
                    status='failed',
                    completed_at=datetime.datetime.utcnow(),
                    duration_seconds=duration,
                    error_message=str(e)
                )

            if self.notifier:
                self.notifier.notify_error(
                    f"Error downloading for chat",
                    {'chat_id': chat_id, 'error': str(e)}
                )

        finally:
            session.close()
            with self._lock:
                self._running_jobs.discard(job_key)

    def enable_job(self, chat_id: int, job_type: str):
        """
        Enable a job for a chat.

        Args:
            chat_id: Chat ID
            job_type: 'sync' or 'download'
        """
        session = self.db.get_session()
        try:
            # Update schedule
            schedule = session.query(Schedule).filter_by(
                chat_id=chat_id,
                job_type=job_type
            ).first()

            if not schedule:
                console.print(f"[yellow]No schedule found for chat {chat_id}, {job_type}[/yellow]")
                return False

            schedule.is_enabled = True
            session.commit()

            # Create APScheduler job
            self._create_job_for_schedule(schedule)

            console.print(f"[green]OK Enabled {job_type} job for chat {chat_id}[/green]")
            return True

        finally:
            session.close()

    def disable_job(self, chat_id: int, job_type: str):
        """
        Disable a job for a chat.

        Args:
            chat_id: Chat ID
            job_type: 'sync' or 'download'
        """
        session = self.db.get_session()
        try:
            # Update schedule
            schedule = session.query(Schedule).filter_by(
                chat_id=chat_id,
                job_type=job_type
            ).first()

            if not schedule:
                console.print(f"[yellow]No schedule found for chat {chat_id}, {job_type}[/yellow]")
                return False

            schedule.is_enabled = False
            session.commit()

            # Remove APScheduler job
            try:
                self.scheduler.remove_job(schedule.apscheduler_job_id)
                console.print(f"[green]OK Disabled {job_type} job for chat {chat_id}[/green]")
            except Exception as e:
                console.print(f"[yellow]Job may not have been scheduled: {e}[/yellow]")

            return True

        finally:
            session.close()

    def trigger_job_manually(self, chat_id: int, job_type: str):
        """
        Manually trigger a job (runs immediately in background thread).

        Args:
            chat_id: Chat ID
            job_type: 'sync' or 'download'
        """
        if job_type == 'sync':
            func = lambda: self.run_sync_job(chat_id, trigger='manual')
            job_name = f"Manual sync for chat {chat_id}"
        elif job_type == 'download':
            func = lambda: self.run_download_job(chat_id, trigger='manual')
            job_name = f"Manual download for chat {chat_id}"
        else:
            console.print(f"[red]Invalid job type: {job_type}[/red]")
            return False

        # Start in background thread
        thread = threading.Thread(target=func, daemon=True, name=job_name)
        thread.start()

        console.print(f"[green]OK Triggered {job_type} job for chat {chat_id}[/green]")
        return True

    def add_custom_schedule(
        self,
        job_id: str,
        func: callable,
        interval: str = None,
        cron: str = None,
        **kwargs
    ):
        """
        Add a custom scheduled job.

        Args:
            job_id: Unique job identifier
            func: Function to execute
            interval: Interval string (e.g., '1h', '30m')
            cron: Cron expression (e.g., '0 */6 * * *')
            **kwargs: Additional job arguments
        """
        if interval:
            trigger = IntervalTrigger(**self._parse_interval(interval))
        elif cron:
            trigger = CronTrigger.from_crontab(cron)
        else:
            raise ValueError("Either interval or cron must be specified")

        self.scheduler.add_job(
            func,
            trigger=trigger,
            id=job_id,
            replace_existing=True,
            **kwargs
        )

        console.print(f"[green]OK Added scheduled job: {job_id}[/green]")

    def remove_schedule(self, job_id: str):
        """Remove a scheduled job."""
        try:
            self.scheduler.remove_job(job_id)
            console.print(f"[green]OK Removed scheduled job: {job_id}[/green]")
        except Exception as e:
            console.print(f"[red]Error removing job: {e}[/red]")

    def list_jobs(self):
        """List all scheduled jobs."""
        jobs = self.scheduler.get_jobs()

        if not jobs:
            console.print("[yellow]No scheduled jobs[/yellow]")
            return

        console.print("\n[bold]Scheduled Jobs:[/bold]")
        for job in jobs:
            console.print(f"  - {job.id}: {job.name}")
            console.print(f"    Next run: {job.next_run_time}")

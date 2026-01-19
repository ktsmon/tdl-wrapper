"""Core TDL wrapper functionality."""

import json
import subprocess
import time
import datetime
from pathlib import Path
from typing import List, Optional, Dict, Any
from rich.console import Console
from rich.progress import Progress, SpinnerColumn, BarColumn, TextColumn, TimeRemainingColumn

from .database import Database, Chat, Export, Download
import sys

console = Console(force_terminal=True, force_interactive=False)
# Force stdout flush for better logging in threads
sys.stdout.reconfigure(line_buffering=True) if hasattr(sys.stdout, 'reconfigure') else None


class TDLWrapper:
    """Wrapper for TDL CLI commands with state management."""

    def __init__(self, config: Dict[str, Any], db: Database):
        """
        Initialize TDL wrapper.

        Args:
            config: Configuration dictionary
            db: Database instance
        """
        self.config = config
        self.db = db
        self.tdl_path = config.get('tdl_path', 'tdl')

    def _run_command(self, args: List[str], capture_output: bool = True, timeout: int = None) -> subprocess.CompletedProcess:
        """
        Run a tdl command.

        Args:
            args: Command arguments (without 'tdl' prefix)
            capture_output: Whether to capture stdout/stderr
            timeout: Optional timeout in seconds

        Returns:
            CompletedProcess instance
        """
        cmd = [self.tdl_path] + args
        console.print(f"[dim]Running: {' '.join(cmd)}[/dim]")

        try:
            if capture_output:
                # Use Popen with communicate() to properly handle large outputs
                process = subprocess.Popen(
                    cmd,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    stdin=subprocess.DEVNULL,
                    text=True,
                    encoding='utf-8',
                    errors='replace'
                )

                try:
                    stdout, stderr = process.communicate(timeout=timeout)
                    return subprocess.CompletedProcess(
                        cmd,
                        returncode=process.returncode,
                        stdout=stdout,
                        stderr=stderr
                    )
                except subprocess.TimeoutExpired:
                    process.kill()
                    stdout, stderr = process.communicate()
                    print(f"Command timed out after {timeout} seconds", flush=True)
                    return subprocess.CompletedProcess(
                        cmd,
                        returncode=-1,
                        stdout=stdout,
                        stderr=f"Command timed out after {timeout} seconds\n{stderr}"
                    )
            else:
                # Direct run without capture
                return subprocess.run(
                    cmd,
                    capture_output=False,
                    stdin=subprocess.DEVNULL,
                    check=False,
                    timeout=timeout
                )
        except Exception as e:
            print(f"Command execution error: {e}", flush=True)
            return subprocess.CompletedProcess(
                cmd,
                returncode=-1,
                stdout="",
                stderr=str(e)
            )

    def list_chats(self, format: str = 'json', filter_expr: Optional[str] = None) -> List[Dict[str, Any]]:
        """
        List all available chats.

        Args:
            format: Output format ('json' or 'text')
            filter_expr: Optional filter expression

        Returns:
            List of chat dictionaries (if format='json')
        """
        args = ['chat', 'ls', '-o', format]
        if filter_expr:
            args.extend(['-f', filter_expr])

        result = self._run_command(args)

        if result.returncode != 0:
            console.print(f"[red]Error listing chats: {result.stderr}[/red]")
            return []

        if format == 'json':
            try:
                return json.loads(result.stdout)
            except json.JSONDecodeError:
                console.print("[red]Error parsing JSON output[/red]")
                return []
        else:
            console.print(result.stdout)
            return []

    def sync_chats_to_db(self, filter_expr: Optional[str] = None):
        """
        Sync chats from Telegram to database.

        Args:
            filter_expr: Optional filter expression for chats
        """
        console.print("[yellow]Syncing chats from Telegram...[/yellow]")
        chats = self.list_chats(format='json', filter_expr=filter_expr)

        for chat_data in chats:
            chat_id = str(chat_data.get('id', ''))
            chat_name = chat_data.get('visible_name', 'Unknown')
            chat_type = chat_data.get('type', '')
            username = chat_data.get('username', '')

            self.db.add_chat(
                chat_id=chat_id,
                chat_name=chat_name,
                chat_type=chat_type,
                username=username
            )
            console.print(f"[green]OK[/green] Added/Updated: {chat_name} ({chat_id})")

    def export_messages(
        self,
        chat: Chat,
        start_timestamp: Optional[int] = None,
        end_timestamp: Optional[int] = None,
        output_file: Optional[str] = None
    ) -> Optional[Export]:
        """
        Export messages from a chat.

        Args:
            chat: Chat instance from database
            start_timestamp: Start time (Unix timestamp), defaults to 0 (1970)
            end_timestamp: End time (Unix timestamp), defaults to now
            output_file: Custom output file path

        Returns:
            Export instance or None on failure
        """
        # Determine time range - always do full export from 1970
        # The downloader with --skip-same --continue handles incremental downloads
        if start_timestamp is None:
            start_timestamp = 0

        if end_timestamp is None:
            end_timestamp = int(time.time())

        # Determine output file
        if output_file is None:
            export_dir = Path(self.config['exports']['base_directory']) / chat.chat_id
            export_dir.mkdir(parents=True, exist_ok=True)
            timestamp_str = datetime.datetime.now().strftime('%Y%m%d_%H%M%S')
            output_file = str(export_dir / f"export_{timestamp_str}.json")

        # Create export record
        export = self.db.create_export(
            chat_id=chat.id,
            start_timestamp=start_timestamp,
            end_timestamp=end_timestamp,
            output_file=output_file
        )

        console.print(f"\n[cyan]Exporting messages from: {chat.chat_name}[/cyan]")
        console.print(f"[dim]Time range: {start_timestamp} to {end_timestamp}[/dim]")
        console.print(f"[dim]Output: {output_file}[/dim]")

        # Build command
        args = ['chat', 'export', '-c', chat.chat_id, '-o', output_file]

        # Add time range
        args.extend(['-i', f"{start_timestamp},{end_timestamp}"])

        # Add optional flags from config
        if self.config['exports'].get('include_content', True):
            args.append('--with-content')
        if self.config['exports'].get('include_all', False):
            args.append('--all')

        # Update status to running
        self.db.update_export_status(export.id, 'running')

        # Run export
        start_time = time.time()
        result = self._run_command(args, capture_output=False)
        duration = int(time.time() - start_time)

        if result.returncode == 0:
            # Parse exported file to get stats
            message_count, media_count = self._parse_export_file(output_file)

            self.db.update_export_status(
                export.id,
                'completed',
                message_count=message_count,
                media_count=media_count,
                duration_seconds=duration
            )

            # Update the export object in memory with the correct counts
            export.message_count = message_count
            export.media_count = media_count
            export.status = 'completed'
            export.duration_seconds = duration

            console.print(f"[green]OK Export completed: {message_count} messages, {media_count} media files[/green]")
            return export
        else:
            self.db.update_export_status(
                export.id,
                'failed',
                error_message=result.stderr,
                duration_seconds=duration
            )
            console.print(f"[red]ERROR Export failed: {result.stderr}[/red]")
            return None

    def _parse_export_file(self, file_path: str) -> tuple[int, int]:
        """
        Parse export file to count messages and media.

        Args:
            file_path: Path to export JSON file

        Returns:
            Tuple of (message_count, media_count)
        """
        try:
            with open(file_path, 'r', encoding='utf-8') as f:
                data = json.load(f)

            # Handle different JSON structures from tdl
            if isinstance(data, dict):
                messages = data.get('messages', [])
            elif isinstance(data, list):
                messages = data
            else:
                messages = []

            message_count = len(messages)
            # tdl uses 'file' field for media attachments
            media_count = sum(1 for msg in messages if msg.get('file'))

            return message_count, media_count
        except Exception as e:
            console.print(f"[yellow]Warning: Could not parse export file: {e}[/yellow]")
            return 0, 0

    def download_from_export(
        self,
        export: Export,
        destination: Optional[str] = None
    ) -> bool:
        """
        Download media files from an export.

        Args:
            export: Export instance
            destination: Custom destination directory

        Returns:
            True if successful, False otherwise
        """
        download_id = None
        try:
            print("Starting download_from_export...", flush=True)

            # Re-fetch export in this thread's session to avoid detached instance errors
            session = self.db.get_session()
            try:
                export_id = export.id
                print(f"Fetching export {export_id} in new session...", flush=True)
                export = session.query(Export).filter_by(id=export_id).first()
                if not export:
                    print(f"Export not found: {export_id}", flush=True)
                    return False

                # Determine destination
                if destination is None:
                    chat = export.chat
                    if self.config['downloads'].get('organize_by_chat', True):
                        destination = str(Path(self.config['downloads']['base_directory']) / chat.chat_id)
                    else:
                        destination = self.config['downloads']['base_directory']

                # Store export file path before closing session
                export_file = export.output_file
                export_id_final = export.id
                print(f"Export file: {export_file}", flush=True)
            finally:
                session.close()

            print(f"Creating destination directory: {destination}", flush=True)
            Path(destination).mkdir(parents=True, exist_ok=True)
            print("Creating download record...", flush=True)

            # Create download record
            download = self.db.create_download(export_id_final, destination)
            download_id = download.id
            print(f"Created download record {download_id}", flush=True)
            print(f"Downloading files from export: {export_file}", flush=True)
            print(f"Destination: {destination}", flush=True)

            # Check if files already exist
            existing_files, existing_size = self._count_downloaded_files(destination)
            print(f"Destination already has {existing_files} files ({existing_size} bytes)", flush=True)

            # Build command - use --skip-same to avoid re-downloading
            # Note: tdl hangs at the end, but our log monitoring will kill it after 10s of inactivity
            args = ['dl', '-f', export_file, '-d', destination, '--skip-same', '--continue']

            # Setup log file for output
            log_dir = Path("logs")
            log_dir.mkdir(exist_ok=True)
            log_file = log_dir / f"download_{download_id}.log"

            # Update status to running
            print(f"Updating download {download_id} to running...", flush=True)
            self.db.update_download_status(download_id, 'running')
            print(f"Download {download_id} marked as running", flush=True)

            print(f"Executing tdl command: {' '.join([self.tdl_path] + args)}", flush=True)
            print(f"Output will be written to: {log_file}", flush=True)
            start_time = time.time()

            # Run subprocess through cmd.exe to properly detach from console
            import os

            # Build command line - run through cmd /c to avoid console attachment issues
            cmd_line = f'cmd /c "{self.tdl_path}" {" ".join(args)} > "{log_file}" 2>&1'

            print(f"Running command: {cmd_line}", flush=True)

            process = subprocess.Popen(
                cmd_line,
                shell=True,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == 'win32' else 0
            )

            # Monitor the log file to detect when tdl is done (it never exits on its own)
            print(f"Process started with PID {process.pid}", flush=True)
            print(f"Monitoring log file for completion...", flush=True)

            last_log_size = 0
            idle_seconds = 0
            max_idle = 10  # Kill after 10 seconds of no log activity
            max_total = 300  # 5 minute absolute timeout

            for total_seconds in range(max_total):
                returncode = process.poll()
                if returncode is not None:
                    print(f"Process exited naturally with return code {returncode}", flush=True)
                    break

                # Check log file size
                try:
                    current_size = log_file.stat().st_size if log_file.exists() else 0
                    if current_size > last_log_size:
                        # Log is growing, reset idle counter
                        idle_seconds = 0
                        last_log_size = current_size
                        if total_seconds % 5 == 0:
                            print(f"Download active... ({current_size} bytes written)", flush=True)
                    else:
                        # No change in log
                        idle_seconds += 1
                        if idle_seconds >= max_idle:
                            print(f"No log activity for {idle_seconds}s, download appears complete", flush=True)
                            print(f"Killing tdl process (it doesn't exit on its own)...", flush=True)
                            process.kill()
                            process.wait()
                            returncode = 0  # Treat as success
                            break
                except Exception as e:
                    print(f"Error checking log: {e}", flush=True)

                time.sleep(1)
            else:
                print(f"Download timed out after {max_total}s, killing process...", flush=True)
                process.kill()
                process.wait()
                returncode = -1

            duration = int(time.time() - start_time)

            print(f"Command execution took {duration} seconds", flush=True)
            print(f"Download command completed with exit code: {returncode}", flush=True)

            # Create a minimal result object
            class Result:
                pass
            result = Result()
            result.returncode = returncode

            # tdl returns 0 on success (even if files are skipped)
            if result.returncode == 0:
                # Count downloaded files and calculate size
                print(f"Counting files in {destination}...", flush=True)
                files_count, total_size = self._count_downloaded_files(destination)
                print(f"Found {files_count} files, {total_size} bytes", flush=True)

                print(f"Updating download {download_id} to completed...", flush=True)
                self.db.update_download_status(
                    download_id,
                    'completed',
                    files_count=files_count,
                    total_size_bytes=total_size,
                    duration_seconds=duration
                )
                print(f"Download {download_id} marked as completed!", flush=True)
                print(f"OK Download completed: {files_count} files ({total_size} bytes)", flush=True)
                return True
            else:
                error_msg = f"Exit code: {result.returncode}"
                print(f"Updating download {download_id} to failed...", flush=True)
                self.db.update_download_status(
                    download_id,
                    'failed',
                    error_message=error_msg,
                    duration_seconds=duration
                )
                print(f"Download {download_id} marked as failed", flush=True)
                print(f"ERROR Download failed with exit code {result.returncode}", flush=True)
                return False

        except Exception as e:
            import traceback
            error_details = traceback.format_exc()
            print(f"ERROR EXCEPTION IN DOWNLOAD: {e}", flush=True)
            print(f"Traceback: {error_details}", flush=True)
            # Try to update status if download record exists
            try:
                if download_id is not None:
                    print(f"Updating download {download_id} to failed...", flush=True)
                    self.db.update_download_status(
                        download_id,
                        'failed',
                        error_message=str(e)
                    )
                    print(f"Updated download {download_id} status to failed", flush=True)
            except Exception as update_error:
                print(f"Failed to update download status: {update_error}", flush=True)
            return False

    def _count_downloaded_files(self, directory: str) -> tuple[int, int]:
        """
        Count files and total size in a directory.

        Args:
            directory: Directory path

        Returns:
            Tuple of (file_count, total_size_bytes)
        """
        try:
            path = Path(directory)
            files = list(path.rglob('*'))
            files = [f for f in files if f.is_file()]
            total_size = sum(f.stat().st_size for f in files)
            return len(files), total_size
        except Exception as e:
            console.print(f"[yellow]Warning: Could not count files: {e}[/yellow]")
            return 0, 0

    def sync_chat(self, chat: Chat) -> bool:
        """
        Perform full sync (export + download) for a chat.

        Args:
            chat: Chat instance

        Returns:
            True if successful, False otherwise
        """
        console.print(f"\n[bold cyan]Syncing chat: {chat.chat_name}[/bold cyan]")

        # Export messages
        export = self.export_messages(chat)
        if not export:
            return False

        # Download files
        if export.media_count > 0:
            success = self.download_from_export(export)
            return success
        else:
            console.print("[yellow]No media files to download[/yellow]")
            return True

    def sync_all_chats(self):
        """Sync all active chats in the database."""
        chats = self.db.get_all_chats(active_only=True)

        if not chats:
            console.print("[yellow]No chats to sync. Add chats first using 'tdl-wrapper add'[/yellow]")
            return

        console.print(f"\n[bold]Starting sync for {len(chats)} chats...[/bold]\n")

        success_count = 0
        for i, chat in enumerate(chats, 1):
            console.print(f"\n[bold]--- Chat {i}/{len(chats)} ---[/bold]")
            if self.sync_chat(chat):
                success_count += 1

        console.print(f"\n[bold green]Sync completed: {success_count}/{len(chats)} successful[/bold green]")

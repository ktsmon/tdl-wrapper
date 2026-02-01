"""Core TDL wrapper functionality."""

import json
import subprocess
import time
import datetime
import os
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
        output_file: Optional[str] = None,
        incremental: bool = True
    ) -> Optional[Export]:
        """
        Export messages from a chat.

        Args:
            chat: Chat instance from database
            start_timestamp: Start time (Unix timestamp), defaults to last export or 0
            end_timestamp: End time (Unix timestamp), defaults to now
            output_file: Custom output file path
            incremental: If True, export only new messages since last successful export

        Returns:
            Export instance or None on failure
        """
        # Determine time range
        if end_timestamp is None:
            end_timestamp = int(time.time())

        if start_timestamp is None:
            if incremental:
                # Use last_successful_download_timestamp for true incrementality
                # This handles cases where export succeeded but download failed
                if chat.last_successful_download_timestamp:
                    # Start from where we last successfully downloaded (add 1 to avoid duplicating last message)
                    start_timestamp = chat.last_successful_download_timestamp + 1
                    console.print(f"[dim]Incremental export from last download timestamp {start_timestamp}[/dim]")
                else:
                    # No successful downloads yet, start from the beginning
                    start_timestamp = 0
                    console.print(f"[dim]First download, starting from beginning[/dim]")
            else:
                # Full export from the beginning
                start_timestamp = 0

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
                        # Use folder_name if set, otherwise fall back to chat_id
                        folder = chat.folder_name if chat.folder_name else chat.chat_id
                        destination = str(Path(self.config['downloads']['base_directory']) / folder)
                    else:
                        destination = self.config['downloads']['base_directory']

                # Store export metadata before closing session
                export_file = export.output_file
                export_id_final = export.id
                export_end_timestamp = export.end_timestamp
                chat_db_id = export.chat_id  # This is the database ID, not chat_id string
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

            # IMPORTANT: Rename any existing unrenamed files BEFORE filtering
            # This ensures the filter can properly detect what's already downloaded
            # (necessary when upgrading from old versions or if previous renames failed)
            if self.config['downloads'].get('rename_by_timestamp', True):
                print("Pre-download: Checking for unrenamed files from previous downloads...", flush=True)
                # Get all previous exports for this chat to catch any unrenamed files
                session = self.db.get_session()
                try:
                    all_exports = session.query(Export)\
                        .filter_by(chat_id=chat_db_id, status='completed')\
                        .order_by(Export.export_timestamp.asc())\
                        .all()

                    if all_exports:
                        print(f"Found {len(all_exports)} previous exports, checking for unrenamed files...", flush=True)
                        total_renamed = 0
                        for prev_export in all_exports:
                            if Path(prev_export.output_file).exists():
                                renamed = self._rename_files_by_timestamp(prev_export.output_file, destination)
                                total_renamed += renamed

                        if total_renamed > 0:
                            print(f"Pre-download: Renamed {total_renamed} previously unrenamed files", flush=True)
                        else:
                            print("Pre-download: All existing files already renamed", flush=True)
                finally:
                    session.close()

            # Filter export JSON to only include undownloaded files
            # This is necessary because we rename files to message IDs, so --skip-same won't work
            filtered_export_file = self._filter_export_for_download(export_file, destination)
            if not filtered_export_file:
                print("No new files to download (all files already exist)", flush=True)
                # Mark download as completed with 0 new files
                self.db.update_download_status(
                    download_id,
                    'completed',
                    files_count=0,
                    total_size_bytes=0,
                    duration_seconds=0
                )
                return True

            # Build command - use filtered export file and --continue for resume support
            # Note: --skip-same removed because we pre-filter the JSON
            # Note: tdl hangs at the end, but our log monitoring will kill it after 10s of inactivity
            args = ['dl', '-f', filtered_export_file, '-d', destination, '--continue']

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

            # Run subprocess through cmd.exe on Windows to properly detach from console
            import os

            # Build command line - platform-specific
            if sys.platform == 'win32':
                # Windows: use cmd /c to avoid console attachment issues
                cmd_line = f'cmd /c "{self.tdl_path}" {" ".join(args)} > "{log_file}" 2>&1'
            else:
                # Linux/Docker: use bash -c
                cmd_line = f'bash -c "{self.tdl_path} {" ".join(args)} > \\"{log_file}\\" 2>&1"'

            print(f"Running command: {cmd_line}", flush=True)

            # Use CREATE_NEW_PROCESS_GROUP so we can kill the entire tree later
            creationflags = 0
            preexec_fn = None

            if sys.platform == 'win32':
                creationflags = subprocess.CREATE_NO_WINDOW | subprocess.CREATE_NEW_PROCESS_GROUP
            else:
                # On Linux, create a new process group so we can kill the entire tree
                preexec_fn = os.setsid

            process = subprocess.Popen(
                cmd_line,
                shell=True,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                creationflags=creationflags,
                preexec_fn=preexec_fn
            )

            # Monitor the log file to detect when tdl is done (it never exits on its own)
            print(f"Process started with PID {process.pid}", flush=True)
            print(f"Monitoring log file for completion...", flush=True)

            last_log_size = 0
            idle_seconds = 0

            # Get timeout values from config (with fallback to defaults)
            max_idle = self.config['downloads'].get('timeout_idle_seconds', 10)
            max_total = self.config['downloads'].get('timeout_total_seconds', 300)

            # Validate timeout values (defensive programming)
            if not isinstance(max_idle, (int, float)) or max_idle < 1:
                print(f"Warning: Invalid timeout_idle_seconds ({max_idle}), using default 10s", flush=True)
                max_idle = 10
            if not isinstance(max_total, (int, float)) or max_total < 1:
                print(f"Warning: Invalid timeout_total_seconds ({max_total}), using default 300s", flush=True)
                max_total = 300

            # Log active timeout settings for transparency
            print(f"Download timeouts: idle={max_idle}s, total={max_total}s", flush=True)

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
                            print(f"Killing tdl process tree (it doesn't exit on its own)...", flush=True)
                            self._kill_process_tree(process)
                            returncode = 0  # Treat as success
                            break
                except Exception as e:
                    print(f"Error checking log: {e}", flush=True)

                time.sleep(1)
            else:
                print(f"Download timed out after {max_total}s, killing process tree...", flush=True)
                self._kill_process_tree(process)

                # Verify if download actually completed despite timeout
                print("Verifying if download completed...", flush=True)
                if self._verify_download_complete(filtered_export_file, destination):
                    print("Download verified complete! Final file exists.", flush=True)
                    returncode = 0  # Treat as success
                else:
                    print("Download incomplete - final file not found", flush=True)
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
                # Rename files by timestamp if enabled
                if self.config['downloads'].get('rename_by_timestamp', True):
                    print("Renaming files by timestamp...", flush=True)
                    # Use filtered_export_file since that's what was actually downloaded
                    renamed = self._rename_files_by_timestamp(filtered_export_file, destination)
                    print(f"Renamed {renamed} files", flush=True)

                # Count downloaded files and calculate NEW files (difference from before)
                print(f"Counting files in {destination}...", flush=True)
                total_files, total_size = self._count_downloaded_files(destination)
                new_files = total_files - existing_files
                new_size = total_size - existing_size
                print(f"Found {total_files} total files, {new_files} new files ({new_size} bytes)", flush=True)

                print(f"Updating download {download_id} to completed...", flush=True)
                self.db.update_download_status(
                    download_id,
                    'completed',
                    files_count=new_files,
                    total_size_bytes=new_size,
                    duration_seconds=duration
                )
                print(f"Download {download_id} marked as completed!", flush=True)

                # Update chat's last_successful_download_timestamp
                # Find the ACTUAL max timestamp from successfully downloaded files
                print(f"Determining last successfully downloaded message timestamp...", flush=True)
                max_downloaded_timestamp = self._get_max_downloaded_timestamp(export_file, destination)

                if max_downloaded_timestamp:
                    print(f"Updating chat last_successful_download_timestamp to {max_downloaded_timestamp}...", flush=True)
                    session = self.db.get_session()
                    try:
                        from .database import Chat
                        chat = session.query(Chat).filter_by(id=chat_db_id).first()
                        if chat:
                            chat.last_successful_download_timestamp = max_downloaded_timestamp
                            session.commit()
                            print(f"Updated chat download timestamp to {max_downloaded_timestamp}", flush=True)
                    finally:
                        session.close()
                else:
                    print(f"WARNING: Could not determine max downloaded timestamp, not updating chat", flush=True)

                print(f"OK Download completed: {files_count} files ({total_size} bytes)", flush=True)
                return True
            else:
                # Even on failure, rename any files that were successfully downloaded
                if self.config['downloads'].get('rename_by_timestamp', True):
                    print("Renaming successfully downloaded files before marking failed...", flush=True)
                    renamed = self._rename_files_by_timestamp(filtered_export_file, destination)
                    print(f"Renamed {renamed} files", flush=True)

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

    def _get_max_downloaded_timestamp(self, export_file: str, destination: str) -> Optional[int]:
        """
        Get the maximum message timestamp from successfully downloaded files.

        This is used to track incremental progress - we can only consider a message
        "successfully downloaded" if its file actually exists on disk.

        Args:
            export_file: Path to export JSON file
            destination: Directory containing downloaded files

        Returns:
            Maximum timestamp from downloaded messages, or None if no files found
        """
        try:
            print(f"Scanning downloaded files to find max timestamp...", flush=True)

            # Parse export file to get message metadata
            with open(export_file, 'r', encoding='utf-8') as f:
                data = json.load(f)

            # Handle different JSON structures from tdl
            if isinstance(data, dict):
                messages = data.get('messages', [])
            elif isinstance(data, list):
                messages = data
            else:
                print("No messages found in export", flush=True)
                return None

            # Get list of downloaded files
            dest_path = Path(destination)
            downloaded_files = {}  # Map: filename -> full_path
            for file_path in dest_path.rglob('*'):
                if file_path.is_file():
                    downloaded_files[file_path.name] = file_path

            print(f"Found {len(downloaded_files)} downloaded files", flush=True)

            if not downloaded_files:
                print("No downloaded files found", flush=True)
                return None

            # Find max timestamp from messages that were actually downloaded
            max_timestamp = None
            matched_count = 0

            for msg in messages:
                # Get file info
                file_field = msg.get('file')
                if not file_field:
                    continue

                # Extract filename - handle both string and dict formats
                if isinstance(file_field, str):
                    original_name = file_field
                elif isinstance(file_field, dict):
                    original_name = file_field.get('name')
                else:
                    continue

                if not original_name:
                    continue

                # Get message ID for matching
                message_id = msg.get('id') or msg.get('ID')
                if not message_id:
                    continue

                is_downloaded = False

                # Check if file exists in three ways:
                # 1. By message ID (renamed files): {message_id}.ext
                # 2. By TDL prefix + original name: ends with original_name
                # 3. By exact original name

                # First, try to match by message ID (for renamed files)
                for actual_filename in downloaded_files.keys():
                    # Check if filename is the message ID (e.g., "497.mp4" for message_id 497)
                    filename_without_ext = actual_filename.rsplit('.', 1)[0]
                    # Handle collision suffixes like "497_1.mp4"
                    if '_' in filename_without_ext:
                        filename_without_ext = filename_without_ext.split('_')[0]

                    if filename_without_ext == str(message_id):
                        is_downloaded = True
                        break

                # If not found by message ID, try to match by original filename
                if not is_downloaded:
                    for actual_filename in downloaded_files.keys():
                        if actual_filename.endswith(original_name):
                            is_downloaded = True
                            break

                # If still not found, check exact match
                if not is_downloaded and original_name in downloaded_files:
                    is_downloaded = True

                if is_downloaded:
                    # Get timestamp (try multiple field names)
                    timestamp = msg.get('date') or msg.get('Date') or msg.get('timestamp')
                    if timestamp:
                        if max_timestamp is None or timestamp > max_timestamp:
                            max_timestamp = timestamp
                        matched_count += 1

            print(f"Matched {matched_count} downloaded files to messages", flush=True)
            print(f"Max downloaded message timestamp: {max_timestamp}", flush=True)

            return max_timestamp

        except Exception as e:
            print(f"Error determining max downloaded timestamp: {e}", flush=True)
            import traceback
            print(traceback.format_exc(), flush=True)
            return None

    def _verify_download_complete(self, export_file: str, destination: str) -> bool:
        """
        Verify if download is complete by checking if the last file from export exists.

        This is used after a timeout to determine if the download actually completed
        despite the process not exiting cleanly.

        Args:
            export_file: Path to export JSON file
            destination: Directory containing downloaded files

        Returns:
            True if the last file from export exists in destination
        """
        try:
            print(f"Verifying download completion from: {export_file}", flush=True)

            # Parse export file
            with open(export_file, 'r', encoding='utf-8') as f:
                data = json.load(f)

            # Handle different JSON structures from tdl
            if isinstance(data, dict):
                messages = data.get('messages', [])
            elif isinstance(data, list):
                messages = data
            else:
                print("Unknown export format, cannot verify", flush=True)
                return False

            # Find the last message with a file attachment
            last_file_msg = None
            for msg in reversed(messages):
                file_field = msg.get('file')
                if file_field:
                    last_file_msg = msg
                    break

            if not last_file_msg:
                print("No files in export, treating as complete", flush=True)
                return True

            # Get message ID and filename
            message_id = last_file_msg.get('id') or last_file_msg.get('ID')
            file_field = last_file_msg.get('file')

            if isinstance(file_field, str):
                original_name = file_field
            elif isinstance(file_field, dict):
                original_name = file_field.get('name')
            else:
                original_name = None

            print(f"Last file in export: message_id={message_id}, filename={original_name}", flush=True)

            # Get list of downloaded files
            dest_path = Path(destination)
            if not dest_path.exists():
                print("Destination directory doesn't exist", flush=True)
                return False

            downloaded_files = {}
            for file_path in dest_path.rglob('*'):
                if file_path.is_file():
                    downloaded_files[file_path.name] = file_path

            print(f"Found {len(downloaded_files)} files in destination", flush=True)

            # Check if last file exists by message ID (renamed format)
            if message_id:
                for actual_filename in downloaded_files.keys():
                    filename_without_ext = actual_filename.rsplit('.', 1)[0]
                    # Handle collision suffixes like "497_1.mp4"
                    if '_' in filename_without_ext:
                        filename_without_ext = filename_without_ext.split('_')[0]

                    if filename_without_ext == str(message_id):
                        print(f"Found last file by message ID: {actual_filename}", flush=True)
                        return True

            # Check by original filename (TDL prefix + original name)
            if original_name:
                for actual_filename in downloaded_files.keys():
                    if actual_filename.endswith(original_name):
                        print(f"Found last file by original name: {actual_filename}", flush=True)
                        return True

                # Check exact match
                if original_name in downloaded_files:
                    print(f"Found last file by exact name: {original_name}", flush=True)
                    return True

            print("Last file not found in destination", flush=True)
            return False

        except Exception as e:
            print(f"Error verifying download completion: {e}", flush=True)
            import traceback
            print(traceback.format_exc(), flush=True)
            return False

    def _filter_export_for_download(self, export_file: str, destination: str) -> Optional[str]:
        """
        Filter export JSON to only include messages with files that haven't been downloaded yet.

        This is necessary because we rename files to message IDs, so tdl's --skip-same flag
        won't work (it can't match renamed files against the export JSON).

        Args:
            export_file: Path to original export JSON file
            destination: Directory containing already-downloaded files

        Returns:
            Path to filtered export JSON file, or None if all files already downloaded
        """
        try:
            print(f"Filtering export to exclude already-downloaded files...", flush=True)

            # Parse export file
            with open(export_file, 'r', encoding='utf-8') as f:
                data = json.load(f)

            # Handle different JSON structures from tdl
            if isinstance(data, dict):
                messages = data.get('messages', [])
                has_wrapper = True
            elif isinstance(data, list):
                messages = data
                has_wrapper = False
            else:
                print("Unknown export format", flush=True)
                return export_file  # Return original if we can't parse

            print(f"Export contains {len(messages)} messages", flush=True)

            # Get list of already-downloaded message IDs from destination directory
            dest_path = Path(destination)
            downloaded_message_ids = set()

            if dest_path.exists():
                for file_path in dest_path.rglob('*'):
                    if file_path.is_file():
                        # Extract message ID from filename (e.g., "12345.jpg" -> 12345)
                        filename = file_path.name
                        filename_without_ext = filename.rsplit('.', 1)[0]

                        # Handle collision suffixes like "12345_1.jpg"
                        if '_' in filename_without_ext:
                            filename_without_ext = filename_without_ext.split('_')[0]

                        # Try to parse as integer message ID
                        try:
                            msg_id = int(filename_without_ext)
                            downloaded_message_ids.add(msg_id)
                        except ValueError:
                            # Not a message ID-based filename, skip
                            pass

            print(f"Found {len(downloaded_message_ids)} already-downloaded message IDs", flush=True)

            # Filter messages to only include those with files NOT already downloaded
            filtered_messages = []
            skipped_count = 0

            for msg in messages:
                # Get message ID
                message_id = msg.get('id') or msg.get('ID')

                # Check if message has a file
                file_field = msg.get('file')
                has_file = bool(file_field)

                if has_file and message_id and message_id in downloaded_message_ids:
                    # File already downloaded, skip this message
                    skipped_count += 1
                    continue

                # Include this message in filtered export
                filtered_messages.append(msg)

            print(f"Filtered: {len(filtered_messages)} messages to download ({skipped_count} already downloaded)", flush=True)

            # If all files already downloaded, return None
            if skipped_count > 0 and len(filtered_messages) == 0:
                print("All files already downloaded!", flush=True)
                return None

            # If nothing was filtered out, use original file
            if skipped_count == 0:
                print("No files filtered, using original export", flush=True)
                return export_file

            # Create filtered export file
            export_path = Path(export_file)
            filtered_file = export_path.parent / f"filtered_{export_path.name}"

            # Write filtered export
            filtered_data = data.copy() if has_wrapper else filtered_messages
            if has_wrapper:
                filtered_data['messages'] = filtered_messages

            with open(filtered_file, 'w', encoding='utf-8') as f:
                json.dump(filtered_data, f, ensure_ascii=False, indent=2)

            print(f"Created filtered export: {filtered_file}", flush=True)
            return str(filtered_file)

        except Exception as e:
            print(f"Error filtering export: {e}", flush=True)
            import traceback
            print(traceback.format_exc(), flush=True)
            # On error, return original file to avoid breaking downloads
            return export_file

    def _rename_files_by_timestamp(self, export_file: str, destination: str) -> int:
        """
        Rename downloaded files to use message IDs.

        Args:
            export_file: Path to export JSON file
            destination: Directory containing downloaded files

        Returns:
            Number of files renamed
        """
        try:
            print(f"Renaming files by message ID from export: {export_file}", flush=True)

            # Parse export file to get message metadata
            with open(export_file, 'r', encoding='utf-8') as f:
                data = json.load(f)

            # Handle different JSON structures from tdl
            if isinstance(data, dict):
                messages = data.get('messages', [])
            elif isinstance(data, list):
                messages = data
            else:
                print("No messages found in export", flush=True)
                return 0

            print(f"Export contains {len(messages)} total messages", flush=True)

            # Debug: Check first message structure to understand field names
            if messages and len(messages) > 0:
                first_msg = messages[0]
                print(f"Sample message keys: {list(first_msg.keys())}", flush=True)
                if first_msg.get('file'):
                    file_field = first_msg['file']
                    print(f"Sample file field type: {type(file_field).__name__}", flush=True)
                    if isinstance(file_field, dict):
                        print(f"Sample file keys: {list(file_field.keys())}", flush=True)
                    else:
                        print(f"Sample file value: {file_field}", flush=True)

            # Build mapping: filename -> message_id
            file_to_id = {}
            for msg in messages:
                # Get file info from message
                file_field = msg.get('file')
                if not file_field:
                    continue

                # Extract filename - handle both string and dict formats
                if isinstance(file_field, str):
                    # File field is just the filename string
                    original_name = file_field
                elif isinstance(file_field, dict):
                    # File field is a dict with metadata
                    original_name = file_field.get('name')
                else:
                    print(f"Warning: Unknown file field type: {type(file_field)}", flush=True)
                    continue

                if not original_name:
                    continue

                # Get message ID (try both lowercase and uppercase)
                message_id = msg.get('id') or msg.get('ID')
                if not message_id:
                    print(f"Warning: No message ID found for file {original_name}, skipping", flush=True)
                    continue

                # Store mapping
                file_to_id[original_name] = message_id

            print(f"Found {len(file_to_id)} files with message IDs in export", flush=True)

            if len(file_to_id) == 0:
                print("WARNING: No files with message IDs found in export. Check export format.", flush=True)
                return 0

            # Scan destination directory for files
            dest_path = Path(destination)
            downloaded_files = [f for f in dest_path.rglob('*') if f.is_file()]

            print(f"Found {len(downloaded_files)} files in destination", flush=True)

            # Track renamed files
            renamed_count = 0
            skipped_count = 0

            for file_path in downloaded_files:
                actual_filename = file_path.name

                # TDL adds prefix: {chat_id}_{message_id}_{original_name}
                # Try to match by checking if original_name is a suffix of actual_filename
                matched_id = None
                matched_original = None

                for original_name, msg_id in file_to_id.items():
                    # Check if the actual filename ends with the original name
                    if actual_filename.endswith(original_name):
                        matched_id = msg_id
                        matched_original = original_name
                        break

                # If no match found, check exact match
                if not matched_id and actual_filename in file_to_id:
                    matched_id = file_to_id[actual_filename]
                    matched_original = actual_filename

                if not matched_id:
                    print(f"No message ID mapping for: {actual_filename} (keeping original name)", flush=True)
                    skipped_count += 1
                    continue

                message_id = matched_id

                # Get file extension
                file_ext = file_path.suffix

                # Create new filename: {message_id}{ext}
                new_name = f"{message_id}{file_ext}"
                new_path = file_path.parent / new_name

                # Handle collision (shouldn't happen since IDs are unique, but just in case)
                collision_counter = 1
                while new_path.exists() and new_path != file_path:
                    new_name = f"{message_id}_{collision_counter}{file_ext}"
                    new_path = file_path.parent / new_name
                    collision_counter += 1

                # Rename file
                try:
                    if new_path != file_path:
                        file_path.rename(new_path)
                        print(f"Renamed: {original_name} -> {new_name}", flush=True)
                        renamed_count += 1
                except Exception as e:
                    print(f"Error renaming {original_name}: {e}", flush=True)

            print(f"Renaming complete: {renamed_count} renamed, {skipped_count} skipped (no message ID)", flush=True)
            return renamed_count

        except Exception as e:
            print(f"Error in timestamp renaming: {e}", flush=True)
            import traceback
            print(traceback.format_exc(), flush=True)
            return 0

    def _wait_for_database_unlock(self, max_wait: int = 10) -> bool:
        """
        Wait for TDL database to be unlocked by checking if we can access it.

        Args:
            max_wait: Maximum time to wait in seconds

        Returns:
            True if database is unlocked, False if timeout
        """
        # TDL always uses ~/.tdl (which is /root/.tdl in Docker)
        # Don't use TDL_DATA_DIR env var as it points to the mount point, not the actual location
        tdl_data_dir = os.path.expanduser('~/.tdl')
        # TDL uses /data/default as the database file (not .db extension)
        db_file = Path(tdl_data_dir) / 'data' / 'default'

        if not db_file.exists():
            # Database doesn't exist yet, so it's not locked
            print(f"Database file not found: {db_file}, assuming unlocked", flush=True)
            return True

        print(f"Checking if TDL database is unlocked: {db_file}", flush=True)

        start_time = time.time()
        while time.time() - start_time < max_wait:
            try:
                # Try to open the database file in exclusive mode
                # If it's locked, this will fail
                with open(db_file, 'r+b') as f:
                    # Try to acquire an exclusive lock (platform-specific)
                    if sys.platform == 'win32':
                        import msvcrt
                        try:
                            msvcrt.locking(f.fileno(), msvcrt.LK_NBLCK, 1)
                            msvcrt.locking(f.fileno(), msvcrt.LK_UNLCK, 1)
                            print("Database is unlocked and ready!", flush=True)
                            return True
                        except (IOError, OSError):
                            # File is locked
                            pass
                    else:
                        import fcntl
                        try:
                            fcntl.flock(f.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                            fcntl.flock(f.fileno(), fcntl.LOCK_UN)
                            print("Database is unlocked and ready!", flush=True)
                            return True
                        except (IOError, OSError) as lock_error:
                            # File is locked
                            print(f"Database still locked (attempt {int(time.time() - start_time)}s)...", flush=True)
            except Exception as e:
                # Any error means we can try again
                print(f"Error checking database lock: {e}", flush=True)

            time.sleep(0.5)

        print(f"Warning: Database still appears locked after {max_wait}s", flush=True)
        return False

    def _kill_process_tree(self, process: subprocess.Popen):
        """
        Kill a process and all its children on Windows.

        Args:
            process: The Popen process to kill
        """
        if sys.platform == 'win32':
            # Use taskkill with /T flag to kill entire process tree
            # /F forces termination, /T terminates child processes
            try:
                subprocess.run(
                    ['taskkill', '/F', '/T', '/PID', str(process.pid)],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    timeout=5
                )
                print(f"Killed process tree with PID {process.pid}", flush=True)
            except Exception as e:
                print(f"Error killing process tree: {e}", flush=True)
                # Fallback to regular kill
                try:
                    process.kill()
                except:
                    pass

            # Wait for process to finish
            try:
                process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                print("Process didn't terminate within timeout", flush=True)
        else:
            # On Unix/Linux, kill entire process group to ensure child processes are terminated
            try:
                # Get the process group ID and kill entire group
                import signal
                pgid = os.getpgid(process.pid)
                print(f"Killing process group {pgid} (includes PID {process.pid})", flush=True)
                os.killpg(pgid, signal.SIGTERM)  # Try graceful termination first
                time.sleep(0.5)

                # Check if still alive, then force kill
                try:
                    os.killpg(pgid, signal.SIGKILL)
                    print(f"Force killed process group {pgid}", flush=True)
                except ProcessLookupError:
                    # Already dead
                    print(f"Process group {pgid} already terminated", flush=True)
                    pass
            except Exception as e:
                print(f"Error killing process group: {e}, falling back to process.kill()", flush=True)
                process.kill()

            # Wait for process to finish
            try:
                process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                print("Process didn't terminate within timeout", flush=True)

        # Wait for database lock to be released
        print("Waiting for database lock to be released...", flush=True)
        self._wait_for_database_unlock(max_wait=10)

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

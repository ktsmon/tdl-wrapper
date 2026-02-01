"""Discord notification system for TDL wrapper."""

import datetime
from typing import Dict, Any, List, Optional
from discord_webhook import DiscordWebhook, DiscordEmbed
import humanize


class DiscordNotifier:
    """Handles Discord webhook notifications."""

    def __init__(self, webhook_url: str, config: Dict[str, Any]):
        """
        Initialize Discord notifier.

        Args:
            webhook_url: Discord webhook URL
            config: Discord configuration dictionary
        """
        self.webhook_url = webhook_url
        self.config = config
        self.enabled = bool(webhook_url) and config.get('enabled', False)

    def _send_webhook(self, embeds: List[DiscordEmbed] = None, content: str = None):
        """
        Send a webhook message.

        Args:
            embeds: List of Discord embeds
            content: Plain text content
        """
        if not self.enabled:
            return

        try:
            webhook = DiscordWebhook(url=self.webhook_url, content=content)

            if embeds:
                for embed in embeds:
                    webhook.add_embed(embed)

            response = webhook.execute()
            return response
        except Exception as e:
            print(f"Error sending Discord notification: {e}")

    def notify_chat_progress(
        self,
        chat_name: str,
        chat_id: str,
        operation: str,
        status: str,
        details: Dict[str, Any] = None
    ):
        """
        Notify progress for a specific chat.

        Args:
            chat_name: Name of the chat
            chat_id: Chat ID
            operation: Operation type (export, download, sync)
            status: Status (started, completed, failed)
            details: Additional details dictionary
        """
        if status == 'started':
            emoji = "[START]"
            color = 0x3498db
            title = f"{emoji} {operation.title()} Started"
        elif status == 'completed':
            emoji = "[OK]"
            color = 0x2ecc71
            title = f"{emoji} {operation.title()} Completed"
        elif status == 'failed':
            emoji = "[FAILED]"
            color = 0xe74c3c
            title = f"{emoji} {operation.title()} Failed"
        else:
            emoji = "[INFO]"
            color = 0x95a5a6
            title = f"{emoji} {operation.title()}"

        embed = DiscordEmbed(
            title=title,
            description=f"**{chat_name}**\n`{chat_id}`",
            color=color
        )

        if details:
            if 'message_count' in details:
                embed.add_embed_field(
                    name="Messages",
                    value=str(details['message_count']),
                    inline=True
                )
            if 'media_count' in details:
                embed.add_embed_field(
                    name="Media Files",
                    value=str(details['media_count']),
                    inline=True
                )
            if 'files_count' in details:
                embed.add_embed_field(
                    name="Files Downloaded",
                    value=str(details['files_count']),
                    inline=True
                )
            if 'total_size_bytes' in details:
                embed.add_embed_field(
                    name="Size",
                    value=humanize.naturalsize(details['total_size_bytes']),
                    inline=True
                )
            if 'duration_seconds' in details:
                embed.add_embed_field(
                    name="Duration",
                    value=humanize.naturaldelta(datetime.timedelta(seconds=details['duration_seconds'])),
                    inline=True
                )
            if 'error_message' in details:
                embed.add_embed_field(
                    name="Error",
                    value=f"```{details['error_message'][:1000]}```",
                    inline=False
                )

        embed.set_timestamp()

        self._send_webhook(embeds=[embed])

    def notify_error(self, error_message: str, context: Dict[str, Any] = None):
        """
        Notify about an error.

        Args:
            error_message: Error message
            context: Additional context dictionary
        """
        if not self.config.get('notify_on_error', True):
            return

        embed = DiscordEmbed(
            title="[ERROR] Error Occurred",
            description=error_message,
            color=0xe74c3c
        )

        if context:
            for key, value in context.items():
                embed.add_embed_field(
                    name=key.replace('_', ' ').title(),
                    value=str(value)[:1024],
                    inline=True
                )

        embed.set_timestamp()

        self._send_webhook(embeds=[embed])

    def notify_new_files(
        self,
        chat_name: str,
        chat_id: str,
        new_files_count: int,
        total_size_bytes: int,
        file_list: List[str] = None
    ):
        """
        Notify about new files detected in a chat.

        Args:
            chat_name: Name of the chat
            chat_id: Chat ID
            new_files_count: Number of new files
            total_size_bytes: Total size of new files
            file_list: Optional list of file names
        """
        embed = DiscordEmbed(
            title="[DOWNLOAD] New Files Detected",
            description=f"**{chat_name}**\n`{chat_id}`",
            color=0x9b59b6
        )

        embed.add_embed_field(
            name="New Files",
            value=f"{new_files_count:,}",
            inline=True
        )
        embed.add_embed_field(
            name="Total Size",
            value=humanize.naturalsize(total_size_bytes),
            inline=True
        )

        if file_list:
            # Show first 10 files
            files_preview = '\n'.join(f"- {f}" for f in file_list[:10])
            if len(file_list) > 10:
                files_preview += f"\n... and {len(file_list) - 10} more"

            embed.add_embed_field(
                name="Files",
                value=f"```{files_preview[:1000]}```",
                inline=False
            )

        embed.set_timestamp()

        self._send_webhook(embeds=[embed])

    def notify_batch_complete(
        self,
        results: List[Dict[str, Any]],
        total_duration_seconds: int
    ):
        """
        Send a single summary notification for a batch job.

        Args:
            results: List of dicts with keys:
                - chat_name: str
                - chat_id: str
                - export_status: 'success' | 'failed' | 'skipped'
                - export_messages: int (or 0)
                - download_status: 'success' | 'failed' | 'skipped' | None
                - files_downloaded: int (actual new files)
                - size_bytes: int
                - error: str (if failed)
            total_duration_seconds: Total batch duration
        """
        if not self.config.get('notify_batch_summary', True):
            return

        # Calculate summary stats
        total_chats = len(results)
        total_files = sum(r.get('files_downloaded', 0) for r in results)
        total_bytes = sum(r.get('size_bytes', 0) for r in results)

        # Determine overall status
        failed_count = sum(1 for r in results if r.get('export_status') == 'failed' or r.get('download_status') == 'failed')

        if failed_count == total_chats:
            title = "[FAILED] Batch Failed"
            color = 0xe74c3c  # Red
        elif failed_count > 0:
            title = "[WARN] Batch Complete (with errors)"
            color = 0xf39c12  # Orange
        else:
            title = "[OK] Batch Complete"
            color = 0x2ecc71  # Green

        # Build summary description
        duration_str = humanize.naturaldelta(datetime.timedelta(seconds=total_duration_seconds))
        size_str = humanize.naturalsize(total_bytes) if total_bytes > 0 else "0 B"

        description = f"**{total_chats} chats** synced in {duration_str}\n"
        if total_files > 0:
            description += f"**{total_files:,} new files** ({size_str})"
        else:
            description += "No new files"

        embed = DiscordEmbed(
            title=title,
            description=description,
            color=color
        )

        # Build per-chat results
        result_lines = []
        for r in results:
            chat_name = r.get('chat_name', 'Unknown')
            export_status = r.get('export_status', 'skipped')
            download_status = r.get('download_status')
            files = r.get('files_downloaded', 0)
            size = r.get('size_bytes', 0)
            error = r.get('error')

            if export_status == 'failed':
                line = f":x: **{chat_name}** - export failed"
                if error:
                    line += f": {error[:50]}"
            elif download_status == 'failed':
                line = f":x: **{chat_name}** - download failed"
                if error:
                    line += f": {error[:50]}"
            elif files > 0:
                size_str = humanize.naturalsize(size)
                line = f":white_check_mark: **{chat_name}** - {files:,} files ({size_str})"
            else:
                line = f":white_check_mark: **{chat_name}** - no new media"

            result_lines.append(line)

        # Discord embed field limit is 1024 chars, so truncate if needed
        results_text = '\n'.join(result_lines)
        if len(results_text) > 1000:
            # Truncate and indicate more results
            truncated_lines = []
            current_len = 0
            for line in result_lines:
                if current_len + len(line) + 1 > 950:
                    truncated_lines.append(f"... and {len(result_lines) - len(truncated_lines)} more")
                    break
                truncated_lines.append(line)
                current_len += len(line) + 1
            results_text = '\n'.join(truncated_lines)

        embed.add_embed_field(
            name="Results",
            value=results_text,
            inline=False
        )

        embed.set_timestamp()

        self._send_webhook(embeds=[embed])

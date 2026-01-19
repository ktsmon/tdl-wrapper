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

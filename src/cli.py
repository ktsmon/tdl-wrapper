"""Command-line interface for TDL wrapper."""

import click
import time
import sys
from rich.console import Console
from rich.table import Table
from rich import box
import humanize
import datetime

from .config import Config
from .database import Database, Chat, Export, Download
from .core import TDLWrapper
from .notifications import DiscordNotifier
from .scheduler import TDLScheduler

console = Console()


@click.group()
@click.option('--config', '-c', type=click.Path(), help='Path to config file')
@click.pass_context
def cli(ctx, config):
    """TDL Wrapper - Advanced download manager for Telegram with incremental sync."""
    ctx.ensure_object(dict)

    # Load configuration
    ctx.obj['config'] = Config(config)
    cfg = ctx.obj['config'].config

    # Initialize database
    ctx.obj['db'] = Database(cfg['database']['path'])

    # Initialize wrapper
    ctx.obj['wrapper'] = TDLWrapper(cfg, ctx.obj['db'])

    # Initialize notifier (if enabled)
    if cfg['discord']['enabled'] and cfg['discord']['webhook_url']:
        ctx.obj['notifier'] = DiscordNotifier(
            cfg['discord']['webhook_url'],
            cfg['discord']
        )
    else:
        ctx.obj['notifier'] = None


@cli.command()
@click.option('--filter', '-f', help='Filter expression for chats')
@click.pass_context
def sync_chats(ctx, filter):
    """Sync chats from Telegram to database."""
    wrapper = ctx.obj['wrapper']
    wrapper.sync_chats_to_db(filter_expr=filter)


@cli.command()
@click.argument('chat_id')
@click.option('--name', help='Chat name')
@click.option('--type', help='Chat type (channel, group, user)')
@click.option('--username', help='Chat username')
@click.pass_context
def add(ctx, chat_id, name, type, username):
    """Add a chat to track."""
    db = ctx.obj['db']

    if not name:
        name = chat_id

    chat = db.add_chat(chat_id, name, type, username)
    console.print(f"[green]OK Added chat: {chat.chat_name} ({chat.chat_id})[/green]")


@cli.command()
@click.pass_context
def list(ctx):
    """List all tracked chats."""
    db = ctx.obj['db']
    chats = db.get_all_chats(active_only=False)

    if not chats:
        console.print("[yellow]No chats found. Use 'tdl-wrapper sync-chats' or 'tdl-wrapper add' to add chats.[/yellow]")
        return

    table = Table(title="Tracked Chats", box=box.ROUNDED)
    table.add_column("ID", style="cyan")
    table.add_column("Name", style="green")
    table.add_column("Type", style="yellow")
    table.add_column("Last Checked", style="magenta")
    table.add_column("Active", style="blue")

    for chat in chats:
        last_checked = chat.last_checked.strftime('%Y-%m-%d %H:%M') if chat.last_checked else 'Never'
        active = "OK" if chat.is_active else "X"

        table.add_row(
            chat.chat_id,
            chat.chat_name,
            chat.chat_type or 'Unknown',
            last_checked,
            active
        )

    console.print(table)


@cli.command()
@click.argument('chat_id')
@click.pass_context
def export(ctx, chat_id):
    """Export messages from a specific chat (incremental)."""
    db = ctx.obj['db']
    wrapper = ctx.obj['wrapper']

    chat = db.get_chat(chat_id)
    if not chat:
        console.print(f"[red]Chat not found: {chat_id}[/red]")
        console.print("Use 'tdl-wrapper list' to see tracked chats or 'tdl-wrapper add' to add a new one.")
        return

    wrapper.export_messages(chat)


@cli.command()
@click.argument('chat_id')
@click.option('--export-id', type=int, help='Specific export ID to download from')
@click.pass_context
def download(ctx, chat_id, export_id):
    """Download files from a chat's export."""
    db = ctx.obj['db']
    wrapper = ctx.obj['wrapper']

    chat = db.get_chat(chat_id)
    if not chat:
        console.print(f"[red]Chat not found: {chat_id}[/red]")
        return

    if export_id:
        session = db.get_session()
        try:
            export = session.query(Export).filter_by(id=export_id).first()
            if not export:
                console.print(f"[red]Export not found: {export_id}[/red]")
                return
        finally:
            session.close()
    else:
        export = db.get_last_export(chat.id)
        if not export:
            console.print(f"[yellow]No exports found for chat: {chat.chat_name}[/yellow]")
            console.print("Run 'tdl-wrapper export <chat_id>' first.")
            return

    wrapper.download_from_export(export)


@cli.command()
@click.argument('chat_id', required=False)
@click.option('--all', 'sync_all', is_flag=True, help='Sync all tracked chats')
@click.pass_context
def sync(ctx, chat_id, sync_all):
    """Sync (export + download) for a chat or all chats."""
    db = ctx.obj['db']
    wrapper = ctx.obj['wrapper']

    if sync_all:
        wrapper.sync_all_chats()
    elif chat_id:
        chat = db.get_chat(chat_id)
        if not chat:
            console.print(f"[red]Chat not found: {chat_id}[/red]")
            return

        wrapper.sync_chat(chat)
    else:
        console.print("[yellow]Specify --all or provide a chat_id[/yellow]")


@cli.command()
@click.argument('chat_id')
@click.pass_context
def status(ctx, chat_id):
    """Show detailed status for a chat."""
    db = ctx.obj['db']

    chat = db.get_chat(chat_id)
    if not chat:
        console.print(f"[red]Chat not found: {chat_id}[/red]")
        return

    # Chat info
    console.print(f"\n[bold cyan]{chat.chat_name}[/bold cyan]")
    console.print(f"Chat ID: {chat.chat_id}")
    console.print(f"Type: {chat.chat_type or 'Unknown'}")
    console.print(f"Added: {chat.added_at.strftime('%Y-%m-%d %H:%M:%S')}")
    console.print(f"Last Checked: {chat.last_checked.strftime('%Y-%m-%d %H:%M:%S') if chat.last_checked else 'Never'}")
    console.print(f"Active: {'Yes' if chat.is_active else 'No'}")

    # Export history
    session = db.get_session()
    try:
        exports = session.query(Export).filter_by(chat_id=chat.id)\
            .order_by(Export.export_timestamp.desc()).limit(10).all()

        if exports:
            console.print(f"\n[bold]Recent Exports (last 10):[/bold]")
            table = Table(box=box.SIMPLE)
            table.add_column("ID", style="cyan")
            table.add_column("Date", style="green")
            table.add_column("Messages", style="yellow")
            table.add_column("Media", style="magenta")
            table.add_column("Status", style="blue")

            for exp in exports:
                table.add_row(
                    str(exp.id),
                    exp.export_timestamp.strftime('%Y-%m-%d %H:%M'),
                    str(exp.message_count),
                    str(exp.media_count),
                    exp.status
                )

            console.print(table)

        # Download history
        downloads = session.query(Download)\
            .join(Export)\
            .filter(Export.chat_id == chat.id)\
            .order_by(Download.download_timestamp.desc())\
            .limit(10).all()

        if downloads:
            console.print(f"\n[bold]Recent Downloads (last 10):[/bold]")
            table = Table(box=box.SIMPLE)
            table.add_column("ID", style="cyan")
            table.add_column("Date", style="green")
            table.add_column("Files", style="yellow")
            table.add_column("Size", style="magenta")
            table.add_column("Status", style="blue")

            for dl in downloads:
                table.add_row(
                    str(dl.id),
                    dl.download_timestamp.strftime('%Y-%m-%d %H:%M'),
                    str(dl.files_count),
                    humanize.naturalsize(dl.total_size_bytes),
                    dl.status
                )

            console.print(table)
    finally:
        session.close()


@cli.command()
@click.option('--foreground', '-f', is_flag=True, help='Run in foreground')
@click.pass_context
def daemon(ctx, foreground):
    """Start the scheduler daemon for automatic syncing."""
    cfg = ctx.obj['config'].config
    db = ctx.obj['db']
    wrapper = ctx.obj['wrapper']
    notifier = ctx.obj['notifier']

    scheduler = TDLScheduler(wrapper, db, cfg['scheduler'], notifier)
    scheduler.start()

    if foreground:
        console.print("[green]Scheduler running in foreground. Press Ctrl+C to stop.[/green]")
        try:
            while True:
                time.sleep(1)
        except KeyboardInterrupt:
            console.print("\n[yellow]Stopping scheduler...[/yellow]")
            scheduler.stop()
    else:
        console.print("[green]Scheduler started in background[/green]")
        console.print("[dim]Note: This will exit immediately. Use --foreground to keep running.[/dim]")


@cli.command()
@click.option('--no-scheduler', is_flag=True, help='Disable the background scheduler')
@click.pass_context
def web(ctx, no_scheduler):
    """Start the web dashboard."""
    cfg = ctx.obj['config'].config

    if not cfg['web']['enabled']:
        console.print("[yellow]Web dashboard is disabled in configuration[/yellow]")
        return

    from .web.app import create_app

    scheduler = None
    if not no_scheduler:
        # Create and start the scheduler (enabled by default)
        scheduler = TDLScheduler(
            ctx.obj['wrapper'],
            ctx.obj['db'],
            cfg['scheduler'],
            ctx.obj['notifier']
        )
        scheduler.start()
        console.print("[green]OK Background scheduler started[/green]")
        console.print("[dim]Scheduler is running in the background. Jobs will execute on schedule.[/dim]")
    else:
        console.print("[yellow]Scheduler disabled (--no-scheduler flag)[/yellow]")

    app = create_app(ctx.obj['config'], ctx.obj['db'], ctx.obj['wrapper'], scheduler)

    console.print(f"[green]Starting web dashboard at http://{cfg['web']['host']}:{cfg['web']['port']}[/green]")

    try:
        app.run(
            host=cfg['web']['host'],
            port=cfg['web']['port'],
            debug=cfg['web']['debug']
        )
    finally:
        if scheduler:
            console.print("[yellow]Stopping scheduler...[/yellow]")
            scheduler.stop()


@cli.command()
@click.pass_context
def test_discord(ctx):
    """Test Discord webhook notification."""
    notifier = ctx.obj['notifier']

    if not notifier or not notifier.enabled:
        console.print("[red]Discord notifications not enabled[/red]")
        console.print("Set TDL_DISCORD_WEBHOOK environment variable or configure in config.yaml")
        return

    console.print("[yellow]Sending test notification...[/yellow]")

    notifier.notify_chat_progress(
        chat_name="Test Chat",
        chat_id="12345",
        operation="sync",
        status="completed",
        details={
            'message_count': 42,
            'media_count': 10,
            'duration_seconds': 15
        }
    )

    console.print("[green]OK Test notification sent![/green]")


@cli.command()
@click.option('--download', '-d', is_flag=True, help='Also download media from reprocessed exports')
@click.pass_context
def reprocess(ctx, download):
    """Reprocess existing exports to fix counts and trigger missing downloads."""
    db = ctx.obj['db']
    wrapper = ctx.obj['wrapper']

    console.print("[yellow]Reprocessing existing exports...[/yellow]\n")

    session = db.get_session()
    try:
        # Get all completed exports
        exports = session.query(Export).filter_by(status='completed').all()

        if not exports:
            console.print("[yellow]No completed exports found.[/yellow]")
            return

        reprocessed = 0
        downloads_triggered = 0

        for export in exports:
            # Reparse the export file to get accurate counts
            message_count, media_count = wrapper._parse_export_file(export.output_file)

            # Check if counts changed
            if export.message_count != message_count or export.media_count != media_count:
                console.print(f"[cyan]Updating export {export.id} ({export.chat.chat_name}):[/cyan]")
                console.print(f"  Old: {export.message_count} messages, {export.media_count} media")
                console.print(f"  New: {message_count} messages, {media_count} media")

                # Update the export record
                export.message_count = message_count
                export.media_count = media_count
                session.commit()
                reprocessed += 1

            # Check if this export has media but no completed download
            if media_count > 0 and download:
                has_download = session.query(Download)\
                    .filter_by(export_id=export.id, status='completed')\
                    .first()

                if not has_download:
                    console.print(f"[green]Triggering download for export {export.id}...[/green]")
                    # Refresh the export object to get updated counts
                    session.refresh(export)
                    wrapper.download_from_export(export)
                    downloads_triggered += 1

        console.print(f"\n[bold green]OK Reprocessed {reprocessed} exports[/bold green]")
        if download:
            console.print(f"[bold green]OK Triggered {downloads_triggered} downloads[/bold green]")
        else:
            console.print(f"[dim]Use --download flag to also trigger downloads for exports with missing files[/dim]")

    finally:
        session.close()


def main():
    """Main entry point."""
    cli(obj={})


if __name__ == '__main__':
    main()

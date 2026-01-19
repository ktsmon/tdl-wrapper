"""Web dashboard for TDL wrapper."""

from flask import Flask, render_template, jsonify, request
import humanize
import datetime
from sqlalchemy import func
from ..database import Chat, Export, Download, Schedule, JobLog


def create_app(config, db, wrapper, scheduler=None):
    """Create and configure Flask app."""
    app = Flask(__name__)
    app.config['SECRET_KEY'] = 'dev-secret-key-change-in-production'

    # Auto-sync chats on startup
    print("[Web] Checking for chats in database...")
    session = db.get_session()
    try:
        chat_count = session.query(Chat).count()
        if chat_count == 0:
            print("[Web] No chats found. Auto-importing from Telegram...")
            wrapper.sync_chats_to_db()
            chat_count = session.query(Chat).count()
            print(f"[Web] Successfully imported {chat_count} chats")
        else:
            print(f"[Web] Found {chat_count} chats in database")
    finally:
        session.close()

    @app.route('/')
    def index():
        """Dashboard home page."""
        return render_template('dashboard.html')

    @app.route('/api/stats')
    def get_stats():
        """Get overall statistics."""
        session = db.get_session()
        try:
            # Get counts
            total_chats = session.query(Chat).count()
            active_chats = session.query(Chat).filter_by(is_active=True).count()
            total_exports = session.query(Export).count()
            completed_exports = session.query(Export).filter_by(status='completed').count()
            total_downloads = session.query(Download).count()
            completed_downloads = session.query(Download).filter_by(status='completed').count()

            # Get total files and size
            result = session.query(
                func.sum(Download.files_count),
                func.sum(Download.total_size_bytes)
            ).filter_by(status='completed').first()

            total_files = result[0] or 0
            total_size = result[1] or 0

            return jsonify({
                'total_chats': total_chats,
                'active_chats': active_chats,
                'total_exports': total_exports,
                'completed_exports': completed_exports,
                'total_downloads': total_downloads,
                'completed_downloads': completed_downloads,
                'total_files': total_files,
                'total_size': total_size,
                'total_size_human': humanize.naturalsize(total_size)
            })
        finally:
            session.close()

    @app.route('/api/chats')
    def get_chats():
        """Get all chats with their latest status, schedules, and job logs."""
        session = db.get_session()
        try:
            chats = session.query(Chat).all()

            chat_list = []
            for chat in chats:
                # Get last export
                last_export = session.query(Export)\
                    .filter_by(chat_id=chat.id)\
                    .order_by(Export.export_timestamp.desc())\
                    .first()

                # Get last download
                last_download = session.query(Download)\
                    .join(Export)\
                    .filter(Export.chat_id == chat.id)\
                    .order_by(Download.download_timestamp.desc())\
                    .first()

                # Get sync schedule
                sync_schedule = session.query(Schedule)\
                    .filter_by(chat_id=chat.id, job_type='sync')\
                    .first()

                # Get download schedule
                download_schedule = session.query(Schedule)\
                    .filter_by(chat_id=chat.id, job_type='download')\
                    .first()

                # Get last sync job log
                last_sync_log = session.query(JobLog)\
                    .filter_by(chat_id=chat.id, job_type='sync')\
                    .order_by(JobLog.job_timestamp.desc())\
                    .first()

                # Get last download job log
                last_download_log = session.query(JobLog)\
                    .filter_by(chat_id=chat.id, job_type='download')\
                    .order_by(JobLog.job_timestamp.desc())\
                    .first()

                chat_list.append({
                    'id': chat.id,
                    'chat_id': chat.chat_id,
                    'chat_name': chat.chat_name,
                    'chat_type': chat.chat_type,
                    'is_active': chat.is_active,
                    'sync_enabled': chat.sync_enabled,
                    'download_enabled': chat.download_enabled,
                    'added_at': chat.added_at.isoformat() if chat.added_at else None,
                    'last_checked': chat.last_checked.isoformat() if chat.last_checked else None,
                    'last_export': {
                        'id': last_export.id,
                        'timestamp': last_export.export_timestamp.isoformat(),
                        'message_count': last_export.message_count,
                        'media_count': last_export.media_count,
                        'status': last_export.status
                    } if last_export else None,
                    'last_download': {
                        'id': last_download.id,
                        'timestamp': last_download.download_timestamp.isoformat(),
                        'files_count': last_download.files_count,
                        'total_size_bytes': last_download.total_size_bytes,
                        'status': last_download.status
                    } if last_download else None,
                    'sync_schedule': {
                        'is_enabled': sync_schedule.is_enabled,
                        'last_run_time': sync_schedule.last_run_time.isoformat() if sync_schedule.last_run_time else None,
                        'interval_seconds': sync_schedule.interval_seconds
                    } if sync_schedule else None,
                    'download_schedule': {
                        'is_enabled': download_schedule.is_enabled,
                        'last_run_time': download_schedule.last_run_time.isoformat() if download_schedule.last_run_time else None,
                        'interval_seconds': download_schedule.interval_seconds
                    } if download_schedule else None,
                    'last_sync_log': {
                        'status': last_sync_log.status,
                        'messages_added': last_sync_log.messages_added,
                        'media_items_found': last_sync_log.media_items_found,
                        'duration_seconds': last_sync_log.duration_seconds,
                        'timestamp': last_sync_log.job_timestamp.isoformat()
                    } if last_sync_log else None,
                    'last_download_log': {
                        'status': last_download_log.status,
                        'files_downloaded': last_download_log.files_downloaded,
                        'bytes_downloaded': last_download_log.bytes_downloaded,
                        'duration_seconds': last_download_log.duration_seconds,
                        'timestamp': last_download_log.job_timestamp.isoformat()
                    } if last_download_log else None
                })

            return jsonify(chat_list)
        finally:
            session.close()

    @app.route('/api/chat/<int:chat_id>/exports')
    def get_chat_exports(chat_id):
        """Get export history for a specific chat."""
        session = db.get_session()
        try:
            exports = session.query(Export)\
                .filter_by(chat_id=chat_id)\
                .order_by(Export.export_timestamp.desc())\
                .limit(50)\
                .all()

            export_list = []
            for export in exports:
                export_list.append({
                    'id': export.id,
                    'export_timestamp': export.export_timestamp.isoformat(),
                    'start_timestamp': export.start_timestamp,
                    'end_timestamp': export.end_timestamp,
                    'message_count': export.message_count,
                    'media_count': export.media_count,
                    'status': export.status,
                    'duration_seconds': export.duration_seconds,
                    'output_file': export.output_file
                })

            return jsonify(export_list)
        finally:
            session.close()

    @app.route('/api/chat/<int:chat_id>/downloads')
    def get_chat_downloads(chat_id):
        """Get download history for a specific chat."""
        session = db.get_session()
        try:
            downloads = session.query(Download)\
                .join(Export)\
                .filter(Export.chat_id == chat_id)\
                .order_by(Download.download_timestamp.desc())\
                .limit(50)\
                .all()

            download_list = []
            for download in downloads:
                download_list.append({
                    'id': download.id,
                    'export_id': download.export_id,
                    'download_timestamp': download.download_timestamp.isoformat(),
                    'files_count': download.files_count,
                    'total_size_bytes': download.total_size_bytes,
                    'status': download.status,
                    'duration_seconds': download.duration_seconds,
                    'destination': download.destination
                })

            return jsonify(download_list)
        finally:
            session.close()

    @app.route('/api/chat/<int:chat_id>/toggle', methods=['POST'])
    def toggle_chat(chat_id):
        """Toggle chat active status."""
        session = db.get_session()
        try:
            chat = session.query(Chat).filter_by(id=chat_id).first()
            if not chat:
                return jsonify({'error': 'Chat not found'}), 404

            chat.is_active = not chat.is_active
            session.commit()

            return jsonify({
                'success': True,
                'is_active': chat.is_active
            })
        finally:
            session.close()

    @app.route('/api/sync/<int:chat_id>', methods=['POST'])
    def sync_chat(chat_id):
        """Manually trigger sync for a chat."""
        session = db.get_session()
        try:
            chat = session.query(Chat).filter_by(id=chat_id).first()
            if not chat:
                return jsonify({'error': 'Chat not found'}), 404

            # Run sync in background (simplified - in production use task queue)
            import threading
            thread = threading.Thread(target=wrapper.sync_chat, args=(chat,))
            thread.start()

            return jsonify({
                'success': True,
                'message': 'Sync started in background'
            })
        finally:
            session.close()

    @app.route('/api/download/<int:chat_id>', methods=['POST'])
    def download_chat(chat_id):
        """Manually trigger download from last export for a chat."""
        session = db.get_session()
        try:
            chat = session.query(Chat).filter_by(id=chat_id).first()
            if not chat:
                return jsonify({'error': 'Chat not found'}), 404

            # Get last export
            last_export = session.query(Export)\
                .filter_by(chat_id=chat_id, status='completed')\
                .order_by(Export.export_timestamp.desc())\
                .first()

            if not last_export:
                return jsonify({'error': 'No completed export found'}), 404

            if last_export.media_count == 0:
                return jsonify({'error': 'No media files in last export'}), 400

            # Run download in background
            import threading
            thread = threading.Thread(target=wrapper.download_from_export, args=(last_export,))
            thread.start()

            return jsonify({
                'success': True,
                'message': 'Download started in background'
            })
        finally:
            session.close()

    @app.route('/api/activity')
    def get_activity():
        """Get current activity (running sync and download jobs from JobLog)."""
        session = db.get_session()
        try:
            # Get running syncs from JobLog
            running_syncs = session.query(JobLog)\
                .filter_by(status='running', job_type='sync')\
                .all()

            # Get running downloads from JobLog
            running_downloads = session.query(JobLog)\
                .filter_by(status='running', job_type='download')\
                .all()

            # Get recent completed operations (last 10 minutes) from JobLog
            ten_min_ago = datetime.datetime.utcnow() - datetime.timedelta(minutes=10)

            recent_syncs = session.query(JobLog)\
                .filter(JobLog.status == 'completed')\
                .filter(JobLog.job_type == 'sync')\
                .filter(JobLog.job_timestamp >= ten_min_ago)\
                .all()

            recent_downloads = session.query(JobLog)\
                .filter(JobLog.status == 'completed')\
                .filter(JobLog.job_type == 'download')\
                .filter(JobLog.job_timestamp >= ten_min_ago)\
                .all()

            return jsonify({
                'running': {
                    'syncs': [{
                        'id': j.id,
                        'chat_id': j.chat_id,
                        'chat_name': j.chat.chat_name,
                        'started': j.started_at.isoformat()
                    } for j in running_syncs],
                    'downloads': [{
                        'id': j.id,
                        'chat_id': j.chat_id,
                        'chat_name': j.chat.chat_name,
                        'started': j.started_at.isoformat()
                    } for j in running_downloads]
                },
                'recent': {
                    'syncs': [{
                        'id': j.id,
                        'chat_id': j.chat_id,
                        'chat_name': j.chat.chat_name,
                        'completed': j.completed_at.isoformat() if j.completed_at else None,
                        'messages_added': j.messages_added,
                        'media_items_found': j.media_items_found
                    } for j in recent_syncs],
                    'downloads': [{
                        'id': j.id,
                        'chat_id': j.chat_id,
                        'chat_name': j.chat.chat_name,
                        'completed': j.completed_at.isoformat() if j.completed_at else None,
                        'files_downloaded': j.files_downloaded,
                        'bytes_downloaded': j.bytes_downloaded
                    } for j in recent_downloads]
                },
                'has_activity': len(running_syncs) > 0 or len(running_downloads) > 0
            })
        finally:
            session.close()

    @app.route('/api/chat/<int:chat_id>/toggle_sync', methods=['POST'])
    def toggle_sync(chat_id):
        """Toggle sync enabled/disabled for a chat."""
        if not scheduler:
            return jsonify({'error': 'Scheduler not available'}), 503

        session = db.get_session()
        try:
            chat = session.query(Chat).filter_by(id=chat_id).first()
            if not chat:
                return jsonify({'error': 'Chat not found'}), 404

            # Toggle sync_enabled
            chat.sync_enabled = not chat.sync_enabled
            session.commit()

            # Enable or disable the job
            if chat.sync_enabled:
                scheduler.enable_job(chat_id, 'sync')
            else:
                scheduler.disable_job(chat_id, 'sync')

            return jsonify({
                'success': True,
                'sync_enabled': chat.sync_enabled
            })
        finally:
            session.close()

    @app.route('/api/chat/<int:chat_id>/toggle_download', methods=['POST'])
    def toggle_download(chat_id):
        """Toggle download enabled/disabled for a chat."""
        if not scheduler:
            return jsonify({'error': 'Scheduler not available'}), 503

        session = db.get_session()
        try:
            chat = session.query(Chat).filter_by(id=chat_id).first()
            if not chat:
                return jsonify({'error': 'Chat not found'}), 404

            # Toggle download_enabled
            chat.download_enabled = not chat.download_enabled
            session.commit()

            # Enable or disable the job
            if chat.download_enabled:
                scheduler.enable_job(chat_id, 'download')
            else:
                scheduler.disable_job(chat_id, 'download')

            return jsonify({
                'success': True,
                'download_enabled': chat.download_enabled
            })
        finally:
            session.close()

    @app.route('/api/chat/<int:chat_id>/trigger_sync', methods=['POST'])
    def trigger_sync(chat_id):
        """Manually trigger sync for a chat."""
        if not scheduler:
            return jsonify({'error': 'Scheduler not available'}), 503

        session = db.get_session()
        try:
            chat = session.query(Chat).filter_by(id=chat_id).first()
            if not chat:
                return jsonify({'error': 'Chat not found'}), 404

            # Trigger the job manually
            scheduler.trigger_job_manually(chat_id, 'sync')

            return jsonify({
                'success': True,
                'message': 'Sync triggered'
            })
        finally:
            session.close()

    @app.route('/api/chat/<int:chat_id>/trigger_download', methods=['POST'])
    def trigger_download(chat_id):
        """Manually trigger download for a chat."""
        if not scheduler:
            return jsonify({'error': 'Scheduler not available'}), 503

        session = db.get_session()
        try:
            chat = session.query(Chat).filter_by(id=chat_id).first()
            if not chat:
                return jsonify({'error': 'Chat not found'}), 404

            # Trigger the job manually
            scheduler.trigger_job_manually(chat_id, 'download')

            return jsonify({
                'success': True,
                'message': 'Download triggered'
            })
        finally:
            session.close()

    @app.route('/api/chat/<int:chat_id>/job_logs')
    def get_job_logs_for_chat(chat_id):
        """Get job logs for a specific chat."""
        session = db.get_session()
        try:
            job_logs = session.query(JobLog)\
                .filter_by(chat_id=chat_id)\
                .order_by(JobLog.job_timestamp.desc())\
                .limit(50)\
                .all()

            log_list = []
            for log in job_logs:
                log_list.append({
                    'id': log.id,
                    'job_type': log.job_type,
                    'job_timestamp': log.job_timestamp.isoformat(),
                    'status': log.status,
                    'started_at': log.started_at.isoformat() if log.started_at else None,
                    'completed_at': log.completed_at.isoformat() if log.completed_at else None,
                    'duration_seconds': log.duration_seconds,
                    'messages_added': log.messages_added,
                    'media_items_found': log.media_items_found,
                    'files_downloaded': log.files_downloaded,
                    'bytes_downloaded': log.bytes_downloaded,
                    'files_skipped': log.files_skipped,
                    'error_message': log.error_message,
                    'trigger': log.trigger
                })

            return jsonify(log_list)
        finally:
            session.close()

    @app.route('/api/job_logs/recent')
    def get_recent_job_logs():
        """Get recent job logs across all chats."""
        session = db.get_session()
        try:
            job_logs = session.query(JobLog)\
                .order_by(JobLog.job_timestamp.desc())\
                .limit(100)\
                .all()

            log_list = []
            for log in job_logs:
                log_list.append({
                    'id': log.id,
                    'chat_id': log.chat_id,
                    'chat_name': log.chat.chat_name,
                    'job_type': log.job_type,
                    'job_timestamp': log.job_timestamp.isoformat(),
                    'status': log.status,
                    'started_at': log.started_at.isoformat() if log.started_at else None,
                    'completed_at': log.completed_at.isoformat() if log.completed_at else None,
                    'duration_seconds': log.duration_seconds,
                    'messages_added': log.messages_added,
                    'media_items_found': log.media_items_found,
                    'files_downloaded': log.files_downloaded,
                    'bytes_downloaded': log.bytes_downloaded,
                    'files_skipped': log.files_skipped,
                    'error_message': log.error_message,
                    'trigger': log.trigger
                })

            return jsonify(log_list)
        finally:
            session.close()

    @app.route('/api/scheduler/config')
    def get_scheduler_config():
        """Get current scheduler configuration."""
        return jsonify({
            'enabled': config.get('scheduler.enabled', True),
            'cron_schedule': config.get('scheduler.cron_schedule', '0 */6 * * *'),
            'timezone': config.get('scheduler.timezone', 'UTC')
        })

    @app.route('/api/scheduler/config', methods=['POST'])
    def update_scheduler_config():
        """Update scheduler configuration."""
        if not scheduler:
            return jsonify({'error': 'Scheduler not available'}), 503

        data = request.json

        # Validate cron schedule if provided
        if 'cron_schedule' in data:
            is_valid, error = scheduler.validate_cron_schedule(data['cron_schedule'])
            if not is_valid:
                return jsonify({'error': f'Invalid cron expression: {error}'}), 400

        # Update config
        if 'cron_schedule' in data:
            config.set('scheduler.cron_schedule', data['cron_schedule'])

        # Save config to file
        try:
            config.save()
        except Exception as e:
            return jsonify({'error': f'Failed to save config: {str(e)}'}), 500

        # Update scheduler's config dict with new values
        if 'cron_schedule' in data:
            scheduler.config['cron_schedule'] = data['cron_schedule']

        # Reload jobs with new configuration (without stopping scheduler)
        try:
            print("[API] Reloading scheduler jobs with new configuration...")
            scheduler.reload_jobs()
        except Exception as e:
            return jsonify({'error': f'Failed to reload scheduler: {str(e)}'}), 500

        return jsonify({
            'success': True,
            'message': 'Scheduler configuration updated and restarted'
        })

    @app.route('/api/scheduler/next_run')
    def get_next_run():
        """Get next scheduled run time for countdown timer."""
        if not scheduler:
            return jsonify({'error': 'Scheduler not available'}), 503

        # Priority: Use APScheduler jobs (always have correct timezone)
        jobs = scheduler.scheduler.get_jobs()
        if jobs:
            # Get the earliest next_run_time from active jobs
            next_job = min(jobs, key=lambda j: j.next_run_time if j.next_run_time else datetime.datetime.max.replace(tzinfo=datetime.timezone.utc))
            if next_job.next_run_time:
                return jsonify({
                    'next_run_time': next_job.next_run_time.isoformat(),
                    'cron_schedule': config.get('scheduler.cron_schedule', '0 */6 * * *')
                })

        # Fallback: Try database (may have naive datetimes)
        session = db.get_session()
        try:
            schedule = session.query(Schedule)\
                .filter_by(is_enabled=True)\
                .filter(Schedule.next_run_time.isnot(None))\
                .first()

            if schedule and schedule.next_run_time:
                return jsonify({
                    'next_run_time': schedule.next_run_time.isoformat(),
                    'cron_schedule': config.get('scheduler.cron_schedule', '0 */6 * * *')
                })

            return jsonify({
                'next_run_time': None,
                'cron_schedule': config.get('scheduler.cron_schedule', '0 */6 * * *'),
                'message': 'No scheduled jobs found'
            })

        finally:
            session.close()

    return app

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

        # Clean up stale "running" jobs from previous crashed/terminated sessions
        stale_jobs = session.query(JobLog).filter_by(status='running').all()
        if stale_jobs:
            print(f"[Web] Cleaning up {len(stale_jobs)} stale 'running' job logs from previous session...")
            for job in stale_jobs:
                job.status = 'failed'
                job.error_message = 'Job interrupted by container restart or crash'
                if job.started_at and not job.completed_at:
                    job.completed_at = datetime.datetime.utcnow()
                    job.duration_seconds = (job.completed_at - job.started_at).total_seconds()
            session.commit()
            print(f"[Web] Marked {len(stale_jobs)} stale jobs as failed")
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
                    'folder_name': chat.folder_name,
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
                # Detect stale downloads (running for more than 1 hour)
                status = download.status
                if status == 'running':
                    download_age = datetime.datetime.utcnow() - download.download_timestamp
                    if download_age > datetime.timedelta(hours=1):
                        status = 'failed'

                download_list.append({
                    'id': download.id,
                    'export_id': download.export_id,
                    'download_timestamp': download.download_timestamp.isoformat(),
                    'files_count': download.files_count,
                    'total_size_bytes': download.total_size_bytes,
                    'status': status,
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

    @app.route('/api/chat/<int:chat_id>/update_folder', methods=['POST'])
    def update_folder_name(chat_id):
        """Update folder name for a chat."""
        session = db.get_session()
        try:
            chat = session.query(Chat).filter_by(id=chat_id).first()
            if not chat:
                return jsonify({'error': 'Chat not found'}), 404

            data = request.json
            folder_name = data.get('folder_name', '').strip()

            # Update folder name (empty string means use chat_id as default)
            chat.folder_name = folder_name if folder_name else None
            session.commit()

            return jsonify({
                'success': True,
                'folder_name': chat.folder_name,
                'message': f'Folder name updated to: {folder_name or chat.chat_id}'
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
        # Get actual timezone from scheduler (uses system TZ if not configured)
        tz = str(scheduler.scheduler.timezone) if scheduler else config.get('scheduler.timezone', 'System')
        return jsonify({
            'enabled': config.get('scheduler.enabled', True),
            'cron_schedule': config.get('scheduler.cron_schedule', '0 */6 * * *'),
            'timezone': tz
        })

    @app.route('/api/downloads/timeout_config')
    def get_download_timeout_config():
        """Get current download timeout configuration."""
        return jsonify({
            'timeout_idle_seconds': config.get('downloads.timeout_idle_seconds', 10),
            'timeout_total_seconds': config.get('downloads.timeout_total_seconds', 300)
        })

    @app.route('/api/downloads/timeout_config', methods=['POST'])
    def update_download_timeout_config():
        """Update download timeout configuration."""
        data = request.json

        # Validate timeout_idle_seconds
        if 'timeout_idle_seconds' in data:
            timeout_idle = data['timeout_idle_seconds']
            if not isinstance(timeout_idle, (int, float)):
                return jsonify({'error': 'timeout_idle_seconds must be a number'}), 400
            if timeout_idle < 5 or timeout_idle > 300:
                return jsonify({'error': 'timeout_idle_seconds must be between 5 and 300'}), 400

        # Validate timeout_total_seconds
        if 'timeout_total_seconds' in data:
            timeout_total = data['timeout_total_seconds']
            if not isinstance(timeout_total, (int, float)):
                return jsonify({'error': 'timeout_total_seconds must be a number'}), 400
            if timeout_total < 60 or timeout_total > 3600:
                return jsonify({'error': 'timeout_total_seconds must be between 60 and 3600'}), 400

        # Cross-validation: total must be >= idle
        idle = data.get('timeout_idle_seconds', config.get('downloads.timeout_idle_seconds', 10))
        total = data.get('timeout_total_seconds', config.get('downloads.timeout_total_seconds', 300))
        if total < idle:
            return jsonify({'error': 'timeout_total_seconds must be >= timeout_idle_seconds'}), 400

        # Update config
        if 'timeout_idle_seconds' in data:
            config.set('downloads.timeout_idle_seconds', int(data['timeout_idle_seconds']))
        if 'timeout_total_seconds' in data:
            config.set('downloads.timeout_total_seconds', int(data['timeout_total_seconds']))

        # Save config to file
        try:
            config.save()
        except Exception as e:
            return jsonify({'error': f'Failed to save config: {str(e)}'}), 500

        return jsonify({
            'success': True,
            'message': 'Download timeout settings saved successfully',
            'timeout_idle_seconds': config.get('downloads.timeout_idle_seconds', 10),
            'timeout_total_seconds': config.get('downloads.timeout_total_seconds', 300)
        })

    @app.route('/api/scheduler/toggle', methods=['POST'])
    def toggle_scheduler():
        """Enable or disable the global scheduler."""
        if not scheduler:
            return jsonify({'error': 'Scheduler not available'}), 503

        data = request.json
        enabled = data.get('enabled', True)

        # Update config
        config.set('scheduler.enabled', enabled)

        # Save config to file
        try:
            config.save()
        except Exception as e:
            return jsonify({'error': f'Failed to save config: {str(e)}'}), 500

        # Update scheduler's config dict
        scheduler.config['enabled'] = enabled

        # Start or stop scheduler based on enabled state
        try:
            if enabled:
                # Reload jobs if enabling
                print("[API] Enabling scheduler...")
                scheduler.reload_jobs()
            else:
                # Remove all jobs if disabling
                print("[API] Disabling scheduler...")
                scheduler.scheduler.remove_all_jobs()
        except Exception as e:
            return jsonify({'error': f'Failed to update scheduler: {str(e)}'}), 500

        return jsonify({
            'success': True,
            'enabled': enabled,
            'message': f'Scheduler {"enabled" if enabled else "disabled"} successfully'
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

    @app.route('/api/scheduler/debug')
    def get_scheduler_debug():
        """Debug endpoint to check scheduler status."""
        if not scheduler:
            return jsonify({'error': 'Scheduler not available'}), 503

        jobs = scheduler.scheduler.get_jobs()
        job_info = []
        for job in jobs:
            job_info.append({
                'id': job.id,
                'name': job.name,
                'next_run_time': job.next_run_time.isoformat() if job.next_run_time else None,
                'trigger': str(job.trigger)
            })

        return jsonify({
            'scheduler_running': scheduler.scheduler.running,
            'job_count': len(jobs),
            'jobs': job_info,
            'config': {
                'enabled': scheduler.config.get('enabled', True),
                'cron_schedule': scheduler.config.get('cron_schedule', '0 */6 * * *'),
                'timezone': str(scheduler.scheduler.timezone)  # Actual scheduler timezone
            }
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
            # Filter out jobs without next_run_time first to avoid AttributeError
            jobs_with_next_run = [j for j in jobs if hasattr(j, 'next_run_time') and j.next_run_time]
            if jobs_with_next_run:
                next_job = min(jobs_with_next_run, key=lambda j: j.next_run_time)
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

    @app.route('/api/chat/<int:chat_id>/rename_files', methods=['POST'])
    def rename_chat_files(chat_id):
        """Rename already downloaded files for a chat using message IDs."""
        session = db.get_session()
        try:
            chat = session.query(Chat).filter_by(id=chat_id).first()
            if not chat:
                return jsonify({'success': False, 'message': 'Chat not found'}), 404

            # Get the most recent export for this chat
            last_export = session.query(Export)\
                .filter_by(chat_id=chat_id, status='completed')\
                .order_by(Export.export_timestamp.desc())\
                .first()

            if not last_export:
                return jsonify({
                    'success': False,
                    'message': 'No completed exports found for this chat'
                }), 400

            # Determine download destination
            from pathlib import Path
            if config['downloads'].get('organize_by_chat', True):
                folder = chat.folder_name if chat.folder_name else chat.chat_id
                destination = str(Path(config['downloads']['base_directory']) / folder)
            else:
                destination = config['downloads']['base_directory']

            # Check if destination exists
            dest_path = Path(destination)
            if not dest_path.exists():
                return jsonify({
                    'success': False,
                    'message': f'Download directory not found: {destination}'
                }), 400

            # Count files before rename
            files_before = len([f for f in dest_path.rglob('*') if f.is_file()])

            # Call the rename function
            renamed_count = wrapper._rename_files_by_timestamp(last_export.output_file, destination)

            return jsonify({
                'success': True,
                'message': f'Renamed {renamed_count} of {files_before} files',
                'renamed_count': renamed_count,
                'total_files': files_before
            })

        except Exception as e:
            import traceback
            print(f"Error renaming files: {e}", flush=True)
            print(traceback.format_exc(), flush=True)
            return jsonify({
                'success': False,
                'message': f'Error: {str(e)}'
            }), 500
        finally:
            session.close()

    return app

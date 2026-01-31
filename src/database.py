"""Database management for TDL wrapper using SQLAlchemy."""

import datetime
from pathlib import Path
from typing import Optional, List
from sqlalchemy import (
    create_engine, Column, Integer, String, DateTime,
    Boolean, ForeignKey, Text, BigInteger, UniqueConstraint
)
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker, relationship, Session

Base = declarative_base()


class Chat(Base):
    """Represents a tracked Telegram chat."""
    __tablename__ = 'chats'

    id = Column(Integer, primary_key=True)
    chat_id = Column(String, unique=True, nullable=False, index=True)
    chat_name = Column(String, nullable=False)
    chat_type = Column(String)  # channel, group, user, etc.
    username = Column(String)  # @username if available
    folder_name = Column(String)  # Custom folder name for downloads (defaults to chat_id if not set)
    added_at = Column(DateTime, default=datetime.datetime.utcnow)
    last_checked = Column(DateTime)
    is_active = Column(Boolean, default=True)

    # Download tracking - separate from export tracking for failure recovery
    last_successful_download_timestamp = Column(BigInteger)  # Unix timestamp of last successfully downloaded message

    # Job configuration
    sync_enabled = Column(Boolean, default=False)
    download_enabled = Column(Boolean, default=False)

    # Relationships
    exports = relationship("Export", back_populates="chat", cascade="all, delete-orphan")
    schedules = relationship("Schedule", back_populates="chat", cascade="all, delete-orphan")

    def __repr__(self):
        return f"<Chat(chat_id='{self.chat_id}', name='{self.chat_name}')>"


class Export(Base):
    """Represents an export operation."""
    __tablename__ = 'exports'

    id = Column(Integer, primary_key=True)
    chat_id = Column(Integer, ForeignKey('chats.id'), nullable=False, index=True)

    # Timestamps (Unix timestamps)
    start_timestamp = Column(BigInteger)  # Start of export range
    end_timestamp = Column(BigInteger)    # End of export range
    export_timestamp = Column(DateTime, default=datetime.datetime.utcnow)  # When export was performed

    # Export details
    output_file = Column(String, nullable=False)
    message_count = Column(Integer, default=0)
    media_count = Column(Integer, default=0)

    # Status tracking
    status = Column(String, default='pending')  # pending, running, completed, failed
    error_message = Column(Text)
    duration_seconds = Column(Integer)

    # Relationships
    chat = relationship("Chat", back_populates="exports")
    downloads = relationship("Download", back_populates="export", cascade="all, delete-orphan")

    def __repr__(self):
        return f"<Export(id={self.id}, chat_id={self.chat_id}, status='{self.status}')>"


class Download(Base):
    """Represents a download operation."""
    __tablename__ = 'downloads'

    id = Column(Integer, primary_key=True)
    export_id = Column(Integer, ForeignKey('exports.id'), nullable=False, index=True)

    download_timestamp = Column(DateTime, default=datetime.datetime.utcnow)
    destination = Column(String, nullable=False)

    # Download stats
    files_count = Column(Integer, default=0)
    total_size_bytes = Column(BigInteger, default=0)

    # Status tracking
    status = Column(String, default='pending')  # pending, running, completed, failed
    error_message = Column(Text)
    duration_seconds = Column(Integer)

    # Relationships
    export = relationship("Export", back_populates="downloads")

    def __repr__(self):
        return f"<Download(id={self.id}, export_id={self.export_id}, status='{self.status}')>"


class Schedule(Base):
    """Represents a scheduled task for a chat."""
    __tablename__ = 'schedules'

    id = Column(Integer, primary_key=True)
    chat_id = Column(Integer, ForeignKey('chats.id'), nullable=False, index=True)

    # Job type: 'sync' or 'download'
    job_type = Column(String, nullable=False)

    # Schedule configuration
    interval_seconds = Column(Integer, nullable=False, default=3600)  # Default 1 hour

    # Legacy fields (kept for backward compatibility, to be removed after migration)
    schedule_type = Column(String)  # export, download, sync
    interval = Column(String)  # e.g., "1h", "6h", "1d"

    # Status
    is_enabled = Column(Boolean, default=True)
    last_run_time = Column(DateTime)
    next_run_time = Column(DateTime)

    # Legacy fields (kept for backward compatibility, to be removed after migration)
    enabled = Column(Boolean, default=True)
    last_run = Column(DateTime)
    next_run = Column(DateTime)

    # APScheduler job ID
    apscheduler_job_id = Column(String, unique=True)  # e.g., "sync_chat_123"

    # Relationships
    chat = relationship("Chat", back_populates="schedules")

    # Unique constraint: one schedule per chat per job type
    __table_args__ = (
        UniqueConstraint('chat_id', 'job_type', name='uix_chat_job'),
    )

    def __repr__(self):
        return f"<Schedule(id={self.id}, chat_id={self.chat_id}, type='{self.job_type}')>"


class JobLog(Base):
    """Logs individual job executions with incremental stats."""
    __tablename__ = 'job_logs'

    id = Column(Integer, primary_key=True)
    chat_id = Column(Integer, ForeignKey('chats.id'), nullable=False, index=True)

    # Job identification
    job_type = Column(String, nullable=False)  # 'sync' or 'download'
    job_timestamp = Column(DateTime, default=datetime.datetime.utcnow, index=True)

    # Execution tracking
    status = Column(String, default='running')  # running, completed, failed
    started_at = Column(DateTime, default=datetime.datetime.utcnow)
    completed_at = Column(DateTime)
    duration_seconds = Column(Integer)

    # Incremental stats for sync jobs
    messages_added = Column(Integer, default=0)  # New messages this run
    media_items_found = Column(Integer, default=0)  # New media this run

    # Incremental stats for download jobs
    files_downloaded = Column(Integer, default=0)  # New files this run
    bytes_downloaded = Column(BigInteger, default=0)  # New bytes this run
    files_skipped = Column(Integer, default=0)  # Already existed

    # Related export/download for reference
    export_id = Column(Integer, ForeignKey('exports.id'))
    download_id = Column(Integer, ForeignKey('downloads.id'))

    # Error tracking
    error_message = Column(Text)

    # Trigger type
    trigger = Column(String, default='scheduled')  # scheduled, manual

    # Relationships
    chat = relationship("Chat")
    export = relationship("Export")
    download = relationship("Download")

    def __repr__(self):
        return f"<JobLog(id={self.id}, chat_id={self.chat_id}, type='{self.job_type}', status='{self.status}')>"


def migrate_to_per_chat_scheduler(engine):
    """Migrate from batch scheduler to per-chat scheduler."""
    from sqlalchemy import inspect, text

    inspector = inspect(engine)

    with engine.begin() as conn:
        # Check if migration needed for schedules table
        schedules_columns = [col['name'] for col in inspector.get_columns('schedules')]

        if 'apscheduler_job_id' not in schedules_columns:
            print("Migrating database schema...")

            # Add new columns to schedules
            try:
                conn.execute(text("ALTER TABLE schedules ADD COLUMN apscheduler_job_id VARCHAR"))
                print("  OK Added apscheduler_job_id to schedules")
            except Exception as e:
                print(f"  Note: apscheduler_job_id column may already exist: {e}")

            try:
                conn.execute(text("ALTER TABLE schedules ADD COLUMN interval_seconds INTEGER DEFAULT 3600"))
                print("  OK Added interval_seconds to schedules")
            except Exception as e:
                print(f"  Note: interval_seconds column may already exist: {e}")

            try:
                conn.execute(text("ALTER TABLE schedules ADD COLUMN job_type VARCHAR"))
                print("  OK Added job_type to schedules")
            except Exception as e:
                print(f"  Note: job_type column may already exist: {e}")

            try:
                conn.execute(text("ALTER TABLE schedules ADD COLUMN is_enabled BOOLEAN DEFAULT 1"))
                print("  OK Added is_enabled to schedules")
            except Exception as e:
                print(f"  Note: is_enabled column may already exist: {e}")

            try:
                conn.execute(text("ALTER TABLE schedules ADD COLUMN last_run_time DATETIME"))
                print("  OK Added last_run_time to schedules")
            except Exception as e:
                print(f"  Note: last_run_time column may already exist: {e}")

            try:
                conn.execute(text("ALTER TABLE schedules ADD COLUMN next_run_time DATETIME"))
                print("  OK Added next_run_time to schedules")
            except Exception as e:
                print(f"  Note: next_run_time column may already exist: {e}")

            # Migrate existing schedule data
            try:
                # Copy schedule_type to job_type for existing records
                conn.execute(text("UPDATE schedules SET job_type = schedule_type WHERE job_type IS NULL"))
                # Copy enabled to is_enabled
                conn.execute(text("UPDATE schedules SET is_enabled = enabled WHERE is_enabled IS NULL"))
                # Copy last_run to last_run_time
                conn.execute(text("UPDATE schedules SET last_run_time = last_run WHERE last_run_time IS NULL"))
                # Copy next_run to next_run_time
                conn.execute(text("UPDATE schedules SET next_run_time = next_run WHERE next_run_time IS NULL"))
                print("  OK Migrated existing schedule data")
            except Exception as e:
                print(f"  Note: Error migrating schedule data: {e}")

        # Add columns to chats table (check independently from schedules migration)
        chats_columns = [col['name'] for col in inspector.get_columns('chats')]

        if 'sync_enabled' not in chats_columns:
            try:
                conn.execute(text("ALTER TABLE chats ADD COLUMN sync_enabled BOOLEAN DEFAULT 0"))
                print("  OK Added sync_enabled to chats")
            except Exception as e:
                print(f"  Note: sync_enabled column may already exist: {e}")

        if 'download_enabled' not in chats_columns:
            try:
                conn.execute(text("ALTER TABLE chats ADD COLUMN download_enabled BOOLEAN DEFAULT 0"))
                print("  OK Added download_enabled to chats")
            except Exception as e:
                print(f"  Note: download_enabled column may already exist: {e}")

        if 'folder_name' not in chats_columns:
            try:
                conn.execute(text("ALTER TABLE chats ADD COLUMN folder_name VARCHAR"))
                print("  OK Added folder_name to chats")
            except Exception as e:
                print(f"  Note: folder_name column may already exist: {e}")

        if 'last_successful_download_timestamp' not in chats_columns:
            try:
                conn.execute(text("ALTER TABLE chats ADD COLUMN last_successful_download_timestamp BIGINT"))
                print("  OK Added last_successful_download_timestamp to chats")
            except Exception as e:
                print(f"  Note: last_successful_download_timestamp column may already exist: {e}")

        # Only update existing chats if this is part of the main migration
        if 'apscheduler_job_id' not in schedules_columns:
            # Update existing chats to have sync and download disabled by default
            try:
                result = conn.execute(text("UPDATE chats SET sync_enabled = 0, download_enabled = 0 WHERE sync_enabled = 1 OR download_enabled = 1"))
                if result.rowcount > 0:
                    print(f"  OK Disabled sync and download for {result.rowcount} existing chats")
            except Exception as e:
                print(f"  Note: Error updating existing chats: {e}")

            print("OK Schema migration completed")

        # Create job_logs table if it doesn't exist
        if 'job_logs' not in inspector.get_table_names():
            Base.metadata.tables['job_logs'].create(engine)
            print("OK Created job_logs table")


class Database:
    """Database manager for TDL wrapper."""

    def __init__(self, db_path: str = "tdl_wrapper.db"):
        """Initialize database connection."""
        self.db_path = Path(db_path)
        self.engine = create_engine(f'sqlite:///{self.db_path}')
        Base.metadata.create_all(self.engine)

        # Run migration
        migrate_to_per_chat_scheduler(self.engine)

        self.Session = sessionmaker(bind=self.engine)

    def get_session(self) -> Session:
        """Get a new database session."""
        return self.Session()

    def add_chat(self, chat_id: str, chat_name: str, chat_type: str = None,
                 username: str = None, folder_name: str = None) -> Chat:
        """Add or update a chat in the database."""
        session = self.get_session()
        try:
            chat = session.query(Chat).filter_by(chat_id=chat_id).first()
            if chat:
                chat.chat_name = chat_name
                if chat_type:
                    chat.chat_type = chat_type
                if username:
                    chat.username = username
                if folder_name is not None:
                    chat.folder_name = folder_name
            else:
                chat = Chat(
                    chat_id=chat_id,
                    chat_name=chat_name,
                    chat_type=chat_type,
                    username=username,
                    folder_name=folder_name
                )
                session.add(chat)
            session.commit()
            session.refresh(chat)
            return chat
        finally:
            session.close()

    def get_chat(self, chat_id: str) -> Optional[Chat]:
        """Get a chat by ID."""
        session = self.get_session()
        try:
            return session.query(Chat).filter_by(chat_id=chat_id).first()
        finally:
            session.close()

    def update_chat_folder(self, chat_id: str, folder_name: str = None) -> bool:
        """Update folder name for a chat."""
        session = self.get_session()
        try:
            chat = session.query(Chat).filter_by(chat_id=chat_id).first()
            if not chat:
                return False
            chat.folder_name = folder_name
            session.commit()
            return True
        finally:
            session.close()

    def get_all_chats(self, active_only: bool = True) -> List[Chat]:
        """Get all tracked chats."""
        session = self.get_session()
        try:
            query = session.query(Chat)
            if active_only:
                query = query.filter_by(is_active=True)
            return query.all()
        finally:
            session.close()

    def get_last_export(self, chat_id: int) -> Optional[Export]:
        """Get the last successful export for a chat."""
        session = self.get_session()
        try:
            return session.query(Export)\
                .filter_by(chat_id=chat_id, status='completed')\
                .order_by(Export.end_timestamp.desc())\
                .first()
        finally:
            session.close()

    def create_export(self, chat_id: int, start_timestamp: int,
                     end_timestamp: int, output_file: str) -> Export:
        """Create a new export record."""
        session = self.get_session()
        try:
            export = Export(
                chat_id=chat_id,
                start_timestamp=start_timestamp,
                end_timestamp=end_timestamp,
                output_file=output_file,
                status='pending'
            )
            session.add(export)
            session.commit()
            session.refresh(export)
            return export
        finally:
            session.close()

    def update_export_status(self, export_id: int, status: str,
                            message_count: int = None, media_count: int = None,
                            error_message: str = None, duration_seconds: int = None):
        """Update export status."""
        session = self.get_session()
        try:
            export = session.query(Export).filter_by(id=export_id).first()
            if export:
                export.status = status
                if message_count is not None:
                    export.message_count = message_count
                if media_count is not None:
                    export.media_count = media_count
                if error_message:
                    export.error_message = error_message
                if duration_seconds is not None:
                    export.duration_seconds = duration_seconds
                session.commit()
        finally:
            session.close()

    def create_download(self, export_id: int, destination: str) -> Download:
        """Create a new download record."""
        session = self.get_session()
        try:
            download = Download(
                export_id=export_id,
                destination=destination,
                status='pending'
            )
            session.add(download)
            session.commit()
            session.refresh(download)
            return download
        finally:
            session.close()

    def update_download_status(self, download_id: int, status: str,
                              files_count: int = None, total_size_bytes: int = None,
                              error_message: str = None, duration_seconds: int = None):
        """Update download status."""
        session = self.get_session()
        try:
            download = session.query(Download).filter_by(id=download_id).first()
            if download:
                download.status = status
                if files_count is not None:
                    download.files_count = files_count
                if total_size_bytes is not None:
                    download.total_size_bytes = total_size_bytes
                if error_message:
                    download.error_message = error_message
                if duration_seconds is not None:
                    download.duration_seconds = duration_seconds
                session.commit()
        finally:
            session.close()

    def create_job_log(self, chat_id: int, job_type: str, trigger: str = 'scheduled') -> JobLog:
        """Create a new job log entry."""
        session = self.get_session()
        try:
            job_log = JobLog(
                chat_id=chat_id,
                job_type=job_type,
                trigger=trigger,
                status='running'
            )
            session.add(job_log)
            session.commit()
            session.refresh(job_log)
            return job_log
        finally:
            session.close()

    def update_job_log(self, job_log_id: int, status: str, **kwargs):
        """Update job log status and stats."""
        session = self.get_session()
        try:
            job_log = session.query(JobLog).filter_by(id=job_log_id).first()
            if job_log:
                job_log.status = status
                job_log.completed_at = datetime.datetime.utcnow()

                if job_log.started_at:
                    duration = (job_log.completed_at - job_log.started_at).total_seconds()
                    job_log.duration_seconds = int(duration)

                for key, value in kwargs.items():
                    if hasattr(job_log, key):
                        setattr(job_log, key, value)

                session.commit()
        finally:
            session.close()

    def get_schedule(self, chat_id: int, job_type: str) -> Optional[Schedule]:
        """Get schedule for chat and job type."""
        session = self.get_session()
        try:
            return session.query(Schedule).filter_by(
                chat_id=chat_id, job_type=job_type
            ).first()
        finally:
            session.close()

    def update_schedule(self, schedule_id: int, **kwargs):
        """Update schedule fields."""
        session = self.get_session()
        try:
            schedule = session.query(Schedule).filter_by(id=schedule_id).first()
            if schedule:
                for key, value in kwargs.items():
                    if hasattr(schedule, key):
                        setattr(schedule, key, value)
                session.commit()
        finally:
            session.close()

    def get_job_logs(self, chat_id: int = None, limit: int = 100) -> List[JobLog]:
        """Get job logs, optionally filtered by chat."""
        session = self.get_session()
        try:
            query = session.query(JobLog)
            if chat_id:
                query = query.filter_by(chat_id=chat_id)
            return query.order_by(JobLog.job_timestamp.desc()).limit(limit).all()
        finally:
            session.close()

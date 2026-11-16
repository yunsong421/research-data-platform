"""SQLite-consistent backups and a shared server/offline-restore file lock."""
from contextlib import closing, contextmanager
from datetime import datetime
from pathlib import Path
import sqlite3
import threading
import time
import uuid
import os

from config import DATA_DIR

LOCK = threading.Lock()


@contextmanager
def runtime_lock():
    path = DATA_DIR / '.runtime-lock'
    with path.open('a+b') as stream:
        if path.stat().st_size == 0:
            stream.write(b'0')
            stream.flush()
        stream.seek(0)
        try:
            if os.name == 'nt':
                import msvcrt
                msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as error:
            raise RuntimeError('网站仍在运行，或另一个恢复程序正在使用数据库。请先停止服务。') from error
        try:
            yield
        finally:
            stream.seek(0)
            if os.name == 'nt':
                msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(stream, fcntl.LOCK_UN)


def backup_directory():
    path = DATA_DIR / 'backups'
    path.mkdir(exist_ok=True)
    return path


def list_backups():
    return [dict(name=path.name, bytes=path.stat().st_size,
                 created_at=datetime.fromtimestamp(path.stat().st_mtime).isoformat(timespec='seconds'))
            for path in sorted(backup_directory().glob('backup-*.db'), reverse=True)]


def create_backup(reason='manual'):
    if reason not in {'manual', 'daily', 'before-restore'}:
        raise ValueError('Invalid backup reason')
    with LOCK:
        source_path = DATA_DIR / 'data_website.db'
        if not source_path.is_file():
            raise RuntimeError('数据库不存在')
        destination = backup_directory() / f'backup-{datetime.now():%Y%m%d-%H%M%S}-{reason}-{uuid.uuid4().hex[:6]}.db'
        temporary = destination.with_suffix('.partial')
        started = time.monotonic()
        def progress(status, remaining, total):
            if time.monotonic() - started > 120:
                raise RuntimeError('数据库持续繁忙，备份超时，请稍后重试')
        try:
            with closing(sqlite3.connect(source_path)) as source, closing(sqlite3.connect(temporary)) as target:
                source.backup(target, pages=256, progress=progress, sleep=.05)
                if target.execute('PRAGMA quick_check').fetchone()[0] != 'ok':
                    raise RuntimeError('备份完整性检查失败')
            temporary.replace(destination)
        except Exception:
            temporary.unlink(missing_ok=True)
            raise
        if reason == 'daily':
            # Only rotate automatic copies; manual and recovery copies are retained.
            for old in sorted(backup_directory().glob('backup-*-daily-*.db'), reverse=True)[7:]:
                old.unlink()
        return destination


def daily_backup():
    today = datetime.now().strftime('%Y%m%d')
    if not list(backup_directory().glob(f'backup-{today}-*-daily-*.db')):
        return create_backup('daily')
    return None


def start_daily_backups():
    try:
        daily_backup()
    except Exception as error:
        print(f'Automatic backup failed: {error}', flush=True)
    stop = threading.Event()
    def worker():
        while not stop.wait(3600):
            try:
                daily_backup()
            except Exception as error:
                print(f'Automatic backup failed: {error}', flush=True)
    threading.Thread(target=worker, daemon=True).start()
    return stop

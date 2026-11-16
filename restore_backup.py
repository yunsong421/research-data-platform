"""Offline restore. Stop the website before using this command."""
import argparse
from contextlib import closing
from pathlib import Path
import sqlite3

from config import DATA_DIR
from backup_store import backup_directory, create_backup, list_backups, runtime_lock


def restore(name):
    if Path(name).name != name or not name.startswith('backup-') or not name.endswith('.db'):
        raise ValueError('请选择 backups 目录内的备份文件名。')
    source_path = backup_directory() / name
    if not source_path.is_file():
        raise ValueError('备份不存在。')
    with runtime_lock():
        with closing(sqlite3.connect(source_path.as_uri() + '?mode=ro', uri=True)) as source:
            if source.execute('PRAGMA quick_check').fetchone()[0] != 'ok':
                raise ValueError('备份完整性检查失败。')
            tables = {row[0] for row in source.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            if not {'users', 'documents', 'questionnaires', 'interview_recordings'} <= tables:
                raise ValueError('备份不是本网站的完整数据库。')
            # Preserve current state first. A failed restore can be retried from this copy.
            preserved = create_backup('before-restore') if (DATA_DIR / 'data_website.db').exists() else None
            with closing(sqlite3.connect(DATA_DIR / 'data_website.db')) as target:
                source.backup(target)
                target.execute('PRAGMA wal_checkpoint(TRUNCATE)')
        return preserved


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('name', nargs='?', help='Backup filename from data/backups')
    parser.add_argument('--list', action='store_true', help='List available backups')
    args = parser.parse_args()
    if args.list:
        for item in list_backups():
            print(item['name'])
        return
    if not args.name:
        parser.error('Specify a backup filename, or use --list')
    print('This replaces the complete database, including accounts, files and recycle bin.')
    print('Stop the website first. Type RESTORE to continue:')
    if input().strip() != 'RESTORE':
        raise SystemExit('Cancelled.')
    previous = restore(args.name)
    print('Restore completed. Restart the website and use the account from this backup.')
    if previous:
        print('Previous state preserved as:', previous.name)


if __name__ == '__main__':
    try:
        main()
    except (ValueError, RuntimeError, OSError, sqlite3.Error) as error:
        raise SystemExit(str(error))

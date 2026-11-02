"""Reversibly archive attachment metadata whose parent issue no longer exists.

Defaults to inspection. --apply creates a complete SQLite backup first and moves
only the displayed orphan metadata into an archive table. No attachment files move.
"""
import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import sqlite3


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('database', type=Path)
    parser.add_argument('--apply', action='store_true')
    args = parser.parse_args()
    database = args.database.resolve(strict=True)
    with sqlite3.connect(database) as connection:
        connection.row_factory = sqlite3.Row
        query = 'SELECT * FROM attachments WHERE NOT EXISTS (SELECT 1 FROM issues WHERE issues.id=attachments.issue_id)'
        rows = list(connection.execute(query))
        print('Orphan attachment records:', len(rows))
        if not args.apply or not rows:
            return
        stamp = datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%S')
        backup = database.with_name(database.name + '.before-attachment-repair-' + stamp + '.bak')
        with sqlite3.connect(backup) as target:
            connection.backup(target)
        connection.execute('BEGIN IMMEDIATE')
        connection.execute('CREATE TABLE IF NOT EXISTS orphaned_attachment_archive '
                           '(id TEXT PRIMARY KEY, archived_at TEXT NOT NULL, reason TEXT NOT NULL, payload TEXT NOT NULL)')
        for row in connection.execute(query).fetchall():
            connection.execute('INSERT INTO orphaned_attachment_archive VALUES (?, ?, ?, ?)',
                               (row['id'], stamp, 'Parent issue missing', json.dumps(dict(row))))
            connection.execute('DELETE FROM attachments WHERE id=?', (row['id'],))
        assert not list(connection.execute(query))
        connection.commit()
        print('Backup:', backup)
        print('Metadata preserved in orphaned_attachment_archive; attachment files unchanged.')
        print('Remaining foreign-key violations:', len(list(connection.execute('PRAGMA foreign_key_check'))))


if __name__ == '__main__':
    main()

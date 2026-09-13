"""Automatically keep explicitly registered local knowledge folders in sync."""

from __future__ import annotations

import json
import queue
import threading
import time
from datetime import datetime, timezone

from sqlalchemy import select

from hagent.db import get_session
from hagent.models import KnowledgeBase, KnowledgeFolder
from hagent.rag import index_local_folder, local_folder_snapshot

POLL_SECONDS = 3
RECOVERY_POLL_SECONDS = 30
DEBOUNCE_SECONDS = 0.8
_stop = threading.Event()
_queue: queue.Queue[tuple[str, bool]] = queue.Queue()
_observer = None
_worker: threading.Thread | None = None
_poller: threading.Thread | None = None
_refresh_lock = threading.Lock()


def sync_local_folder_once(folder_id: str, *, force: bool = False) -> dict[str, int] | None:
    """Reindex a watched folder if its filesystem snapshot changed."""
    with get_session(scoped=False) as session:
        folder = session.get(KnowledgeFolder, folder_id)
        if folder is None:
            return None
        base = session.get(KnowledgeBase, folder.knowledge_base_id)
        if base is None:
            return None
        try:
            snapshot = local_folder_snapshot(folder.path)
        except (OSError, ValueError) as exc:
            message = str(exc)
            if folder.error != message:
                folder.error = message
                session.commit()
            return None
        try:
            old_snapshot = json.loads(folder.snapshot_json or "{}")
        except ValueError:
            old_snapshot = {}
        if snapshot == old_snapshot and not force:
            if folder.error:
                folder.error = ""
                session.commit()
            return None
        try:
            counts = index_local_folder(session, base, folder.path)
        except (OSError, ValueError) as exc:
            folder.error = str(exc)[:1000]
            session.commit()
            return None
        if counts["failed"]:
            folder.error = f"{counts['failed']} file(s) could not be indexed; watching will retry on the next change."
        else:
            folder.snapshot_json = json.dumps(snapshot, separators=(",", ":"))
            folder.last_scanned_at = datetime.now(timezone.utc)
            folder.error = ""
        session.commit()
        return counts


def _registered_folders() -> list[tuple[str, str]]:
    with get_session(scoped=False) as session:
        rows = session.execute(select(KnowledgeFolder.id, KnowledgeFolder.path)).all()
        return [(row.id, row.path) for row in rows]


def _watch_loop() -> None:
    pending: dict[str, tuple[float, bool]] = {}
    refreshed_at = 0.0
    while not _stop.wait(0.2):
        now = time.monotonic()
        if _observer is not None and now - refreshed_at > 10:
            refresh_local_folder_watches()
            refreshed_at = now
        try:
            while True:
                folder_id, force = _queue.get_nowait()
                pending[folder_id] = (now, force or pending.get(folder_id, (0, False))[1])
        except queue.Empty:
            pass
        due = [folder_id for folder_id, (event_at, _) in pending.items() if now - event_at >= DEBOUNCE_SECONDS]
        for folder_id in due:
            _, force = pending.pop(folder_id, (now, False))
            try:
                sync_local_folder_once(folder_id, force=force)
            except Exception:
                # Keep the watcher alive; the next filesystem event will retry.
                continue


def _poll_loop(interval: int) -> None:
    """Fallback for environments where the optional native watcher is unavailable."""
    while not _stop.wait(interval):
        for folder_id, _ in _registered_folders():
            try:
                sync_local_folder_once(folder_id)
            except Exception:
                continue


def refresh_local_folder_watches() -> None:
    """Refresh native watch registrations after folders are added or removed."""
    if _observer is None or not _observer.is_alive():
        return
    try:
        from watchdog.events import FileSystemEventHandler
        from hagent.rag import IGNORED_FOLDER_NAMES, SUPPORTED_DOCUMENT_EXTENSIONS

        class Handler(FileSystemEventHandler):
            def __init__(self, folder_id: str):
                self.folder_id = folder_id

            def on_any_event(self, event):
                if event.event_type not in {"created", "modified", "deleted", "moved"}:
                    return
                from pathlib import Path
                source_path = Path(event.src_path)
                if any(part.startswith(".") or part.lower() in IGNORED_FOLDER_NAMES for part in source_path.parts):
                    return
                if event.is_directory:
                    _queue.put((self.folder_id, True))
                    return
                if source_path.suffix.lower() in SUPPORTED_DOCUMENT_EXTENSIONS:
                    _queue.put((self.folder_id, True))
                destination = getattr(event, "dest_path", "")
                if destination and Path(destination).suffix.lower() in SUPPORTED_DOCUMENT_EXTENSIONS:
                    _queue.put((self.folder_id, True))

        with _refresh_lock:
            _observer.unschedule_all()
            for folder_id, path in _registered_folders():
                try:
                    _observer.schedule(Handler(folder_id), path, recursive=True)
                except (OSError, FileNotFoundError):
                    continue
    except Exception:
        return


def start_local_folder_watcher() -> None:
    global _observer, _worker, _poller
    if _worker is not None and _worker.is_alive():
        return
    _stop.clear()
    _observer = None
    try:
        from watchdog.observers import Observer
        _observer = Observer()
        _observer.start()
        refresh_local_folder_watches()
    except Exception:
        if _observer is not None:
            try:
                _observer.stop()
                _observer.join(timeout=2)
            except Exception:
                pass
        _observer = None
    _worker = threading.Thread(target=_watch_loop, name="hagent-knowledge-watch-events", daemon=True)
    _worker.start()
    interval = RECOVERY_POLL_SECONDS if _observer is not None else POLL_SECONDS
    _poller = threading.Thread(target=_poll_loop, args=(interval,), name="hagent-knowledge-folder-poll", daemon=True)
    _poller.start()


def stop_local_folder_watcher() -> None:
    global _observer
    _stop.set()
    if _observer is not None:
        _observer.stop()
        _observer.join(timeout=3)
        _observer = None
    for thread in (_worker, _poller):
        if thread is not None and thread.is_alive():
            thread.join(timeout=3)

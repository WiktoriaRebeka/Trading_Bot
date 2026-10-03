# orderflow_engine/msi_state_persister.py
# Nieblokujący zapis migawek MSI do Firestore (kolekcja msi_state).
# Live 1M: kolejka + jeden batch na okno debounce (nie 66 osobnych set()).
# Bootstrap/resume: write_snapshots_now — natychmiastowy batch.

from __future__ import annotations

import logging
import math
import os
import queue
import threading
import time
from typing import Any, Dict, Iterable, List, Optional, Tuple

from shared_lib.firebase_client import get_db

logger = logging.getLogger(__name__)

MSI_STATE_FS_COLLECTION = "msi_state"
MSI_STATE_FS_QUEUE_MAX = int(os.environ.get("MSI_STATE_FS_QUEUE_MAX", "2000"))
MSI_STATE_BATCH_DEBOUNCE_SEC = float(os.environ.get("MSI_STATE_BATCH_DEBOUNCE_SEC", "1.5"))
_FS_BATCH_LIMIT = 500

_write_queue: Optional[queue.Queue] = None
_writer_thread: Optional[threading.Thread] = None
_writer_lock = threading.Lock()


def _sanitize_for_firestore(obj: Any) -> Any:
    if isinstance(obj, dict):
        return {str(k): _sanitize_for_firestore(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_sanitize_for_firestore(x) for x in obj]
    if isinstance(obj, float):
        if math.isnan(obj) or math.isinf(obj):
            return None
        return obj
    return obj


def _commit_batch(pairs: List[Tuple[str, Dict[str, Any]]]) -> None:
    if not pairs:
        return
    from google.cloud import firestore as gcf

    db = get_db()
    batch = db.batch()
    pending_ops = 0
    n_ok = 0
    for sym, snapshot in pairs:
        payload = _sanitize_for_firestore(snapshot)
        payload["updated_at"] = gcf.SERVER_TIMESTAMP
        ref = db.collection(MSI_STATE_FS_COLLECTION).document(sym)
        batch.set(ref, payload, merge=False)
        pending_ops += 1
        n_ok += 1
        if pending_ops >= _FS_BATCH_LIMIT:
            batch.commit()
            batch = db.batch()
            pending_ops = 0
    if pending_ops:
        batch.commit()
    logger.debug("[MSI-STATE] batch write n=%s", n_ok)


def _msi_state_writer_main() -> None:
    assert _write_queue is not None
    q = _write_queue
    debounce = MSI_STATE_BATCH_DEBOUNCE_SEC
    while True:
        item = q.get()
        if item is None:
            q.task_done()
            return
        pending: Dict[str, Dict[str, Any]] = {item[0]: item[1]}
        q.task_done()
        deadline = time.monotonic() + debounce
        sentinel = False
        while True:
            timeout = deadline - time.monotonic()
            if timeout <= 0:
                break
            try:
                nxt = q.get(timeout=timeout)
            except queue.Empty:
                break
            if nxt is None:
                q.task_done()
                sentinel = True
                break
            pending[nxt[0]] = nxt[1]
            q.task_done()
        try:
            _commit_batch(list(pending.items()))
        except Exception as e:
            logger.warning(
                "Firestore MSI state batch failed n=%s: %s: %s",
                len(pending),
                type(e).__name__,
                e,
            )
        if sentinel:
            return


class MsiStatePersister:
    """Kolejka + wątek writer — hot path tylko enqueue_snapshot (put_nowait)."""

    @classmethod
    def start(cls) -> None:
        global _write_queue, _writer_thread
        with _writer_lock:
            if _writer_thread is not None and _writer_thread.is_alive():
                return
            _write_queue = queue.Queue(maxsize=MSI_STATE_FS_QUEUE_MAX)
            _writer_thread = threading.Thread(
                target=_msi_state_writer_main,
                name="msi-state-fs-writer",
                daemon=True,
            )
            _writer_thread.start()
            logger.info(
                "Uruchomiono wątek zapisu MSI state do Firestore "
                "(kolekcja=%s, max_queue=%s, debounce=%.2fs batch).",
                MSI_STATE_FS_COLLECTION,
                MSI_STATE_FS_QUEUE_MAX,
                MSI_STATE_BATCH_DEBOUNCE_SEC,
            )

    @classmethod
    def stop(cls) -> None:
        """Graceful shutdown: sentinel kończy writer po flushu kolejki."""
        global _write_queue, _writer_thread
        with _writer_lock:
            if _write_queue is None:
                return
            try:
                _write_queue.put_nowait(None)
            except queue.Full:
                logger.warning("MSI state writer shutdown: kolejka pełna, pomijam flush")
            if _writer_thread is not None and _writer_thread.is_alive():
                _writer_thread.join(timeout=8.0)
            _write_queue = None
            _writer_thread = None

    @classmethod
    def enqueue_snapshot(cls, symbol: str, snapshot_dict: Dict[str, Any]) -> None:
        if _write_queue is None:
            return
        sym = str(symbol).upper()
        try:
            _write_queue.put_nowait((sym, snapshot_dict))
        except queue.Full:
            logger.warning(
                "Kolejka zapisu MSI state pełna (max=%s), pomijam persist symbol=%s",
                MSI_STATE_FS_QUEUE_MAX,
                sym,
            )

    @classmethod
    def write_snapshots_now(
        cls, items: Iterable[Tuple[str, Dict[str, Any]]]
    ) -> None:
        """Natychmiastowy batch (bootstrap / resume) — poza kolejką debounce."""
        pairs = [(str(sym).upper(), snap) for sym, snap in items]
        if not pairs:
            return
        try:
            _commit_batch(pairs)
            logger.info(
                "[MSI-STATE] natychmiastowy zapis n=%s symbols=%s",
                len(pairs),
                ",".join(s for s, _ in pairs[:8]) + ("..." if len(pairs) > 8 else ""),
            )
        except Exception as e:
            logger.warning(
                "[MSI-STATE] natychmiastowy zapis failed n=%s: %s: %s",
                len(pairs),
                type(e).__name__,
                e,
            )

# orderflow_engine/msi_state_persister.py
# Etap 2A: nieblokujący zapis migawek MSI do Firestore (kolekcja msi_state).

from __future__ import annotations

import logging
import math
import os
import queue
import threading
from typing import Any, Dict, Optional

from shared_lib.firebase_client import get_db

logger = logging.getLogger(__name__)

MSI_STATE_FS_COLLECTION = "msi_state"
MSI_STATE_FS_QUEUE_MAX = int(os.environ.get("MSI_STATE_FS_QUEUE_MAX", "2000"))

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


def _msi_state_writer_main() -> None:
    from google.cloud import firestore as gcf

    while True:
        item = _write_queue.get()
        sym_log = item[0] if (item is not None and isinstance(item, tuple) and len(item) >= 1) else "?"
        try:
            if item is None:
                return
            sym, snapshot = item
            payload = _sanitize_for_firestore(snapshot)
            payload["updated_at"] = gcf.SERVER_TIMESTAMP
            doc_ref = get_db().collection(MSI_STATE_FS_COLLECTION).document(sym)
            doc_ref.set(payload, merge=False)
        except Exception as e:
            logger.warning("Firestore MSI state persist failed symbol=%s: %s", sym_log, e)
        finally:
            _write_queue.task_done()


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
                "Uruchomiono wątek zapisu MSI state do Firestore (kolekcja=%s, max_queue=%s).",
                MSI_STATE_FS_COLLECTION,
                MSI_STATE_FS_QUEUE_MAX,
            )

    @classmethod
    def stop(cls) -> None:
        """Graceful shutdown: sentinel kończy writer."""
        global _write_queue, _writer_thread
        with _writer_lock:
            if _write_queue is None:
                return
            try:
                _write_queue.put_nowait(None)
            except queue.Full:
                logger.warning("MSI state writer shutdown: kolejka pełna, pomijam flush")
            if _writer_thread is not None and _writer_thread.is_alive():
                _writer_thread.join(timeout=5.0)
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

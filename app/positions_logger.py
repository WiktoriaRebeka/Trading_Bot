# app/positions_logger.py

import json
import os
from datetime import datetime
from typing import Optional, Literal

LOG_FILE = "positions_log.jsonl"

PositionStatus = Literal["planned", "opened", "closed", "cancelled"]


def get_timestamp() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def log_new_position(symbol: str, direction: str, entry: float, stoploss: float, target: float):
    position = {
        "symbol": symbol,
        "direction": direction,
        "entry": entry,
        "stoploss": stoploss,
        "target": target,
        "status": "planned",
        "planned_at": get_timestamp(),
        "opened_at": None,
        "closed_at": None,
        "cancelled_at": None,
        "result": None
    }
    with open(LOG_FILE, "a", encoding="utf-8") as f:
        f.write(json.dumps(position) + "\n")


def update_position_status(symbol: str, entry: float, status: PositionStatus, result: Optional[str] = None):
    if not os.path.exists(LOG_FILE):
        return

    updated_lines = []
    with open(LOG_FILE, "r", encoding="utf-8") as f:
        for line in f:
            pos = json.loads(line)
            if pos["symbol"] == symbol and pos["entry"] == entry and pos["status"] in ["planned", "opened"]:
                pos["status"] = status
                timestamp_field = status + "_at"
                pos[timestamp_field] = get_timestamp()
                if result:
                    pos["result"] = result
            updated_lines.append(json.dumps(pos))

    with open(LOG_FILE, "w", encoding="utf-8") as f:
        f.write("\n".join(updated_lines) + "\n")

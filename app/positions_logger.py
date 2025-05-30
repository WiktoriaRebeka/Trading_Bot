# trading_bot/app/positions_logger.py
import json
import os
from datetime import datetime
from typing import Optional, Literal
from app.constants import POSITIONS_LOG_FILE # Użyj stałej

PositionStatus = Literal["planned", "opened", "closed", "cancelled"]

def get_timestamp() -> str:
    # Użyj UTC dla spójności
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S %Z")

def log_new_position(symbol: str, direction: str, entry: float, stoploss: float, target: float, position_id: str):
    position = {
        "position_id": position_id, # Dodaj ID dla łatwiejszego śledzenia
        "symbol": symbol,
        "direction": direction,
        "entry_price": entry, # Zmiana nazwy dla spójności
        "stop_loss": stoploss, # Zmiana nazwy dla spójności
        "take_profit": target, # Zmiana nazwy dla spójności
        "status": "planned",
        "planned_at": get_timestamp(),
        "opened_at": None,
        "closed_at": None,
        "cancelled_at": None,
        "result_reason": None # Zmiana nazwy
    }
    try:
        with open(POSITIONS_LOG_FILE, "a", encoding="utf-8") as f:
            f.write(json.dumps(position) + "\n")
    except Exception as e:
        print(f"[LOGGER_ERROR] Błąd zapisu nowej pozycji: {e}")

def update_position_status(position_id: str, status: PositionStatus, result_reason: Optional[str] = None):
    if not os.path.exists(POSITIONS_LOG_FILE):
        print(f"[LOGGER_WARN] Plik logu {POSITIONS_LOG_FILE} nie istnieje. Nie można zaktualizować statusu.")
        return

    updated_lines = []
    found_and_updated = False
    try:
        with open(POSITIONS_LOG_FILE, "r", encoding="utf-8") as f:
            for line in f:
                try:
                    pos = json.loads(line)
                    if pos.get("position_id") == position_id and pos.get("status") in ["planned", "opened"]:
                        pos["status"] = status
                        timestamp_field = status + "_at" # np. "opened_at", "closed_at"
                        pos[timestamp_field] = get_timestamp()
                        if result_reason:
                            pos["result_reason"] = result_reason
                        updated_lines.append(json.dumps(pos))
                        found_and_updated = True
                    else:
                        updated_lines.append(line.strip()) # Zapisz oryginalną linię bez zmian
                except json.JSONDecodeError:
                    updated_lines.append(line.strip()) # Zachowaj uszkodzoną linię
        
        if found_and_updated:
            with open(POSITIONS_LOG_FILE, "w", encoding="utf-8") as f:
                for updated_line in updated_lines:
                    f.write(updated_line + "\n")
            print(f"[LOGGER] Zaktualizowano status pozycji {position_id} na {status}.")
        else:
            print(f"[LOGGER_WARN] Nie znaleziono pozycji {position_id} do aktualizacji lub miała już status finalny.")

    except Exception as e:
        print(f"[LOGGER_ERROR] Błąd aktualizacji statusu pozycji: {e}")
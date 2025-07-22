# Lokalizacja: shared_lib/leverage_calculator.py

import logging
from typing import Dict

from shared_lib.models import AlertData

logger = logging.getLogger(__name__)

# Stała prowizji zdefiniowana lokalnie w tym module.
# Prowizja 0.02% za wejście i 0.02% za wyjście.
TRANSACTION_FEE_PERCENT = 0.02

def calculate_sl_distance_points(entry_price: float, sl_price: float) -> float:
    """
    Oblicza bezwzględną odległość w punktach między ceną wejścia a stop lossem.
    """
    if entry_price == sl_price:
        logger.warning(
            f"Cena wejścia ({entry_price}) i stop loss ({sl_price}) są identyczne. "
            "Odległość SL wynosi 0."
        )
        return 0.0

    distance = abs(entry_price - sl_price)
    return round(distance, 8)

def calculate_sl_distance_percentage(entry_price: float, sl_price: float) -> float:
    """
    Oblicza odległość stop lossa jako procent ceny wejścia.
    """
    if entry_price == 0:
        logger.error(
            "Cena wejścia wynosi 0. Nie można obliczyć procentowej odległości SL."
        )
        return 0.0

    sl_distance_points = calculate_sl_distance_points(entry_price, sl_price)
    
    if sl_distance_points == 0.0:
        return 0.0

    percentage = (sl_distance_points / entry_price) * 100
    return round(percentage, 4)

# --- NOWA FUNKCJA ---
def calculate_real_sl_distance_percentage(sl_distance_percentage: float) -> float:
    """
    Oblicza realną procentową odległość SL, uwzględniając prowizje transakcyjne.
    Realna strata = (ruch ceny %) + (prowizja za wejście) + (prowizja za wyjście).
    """
    # Prowizję płacimy dwukrotnie (za otwarcie i zamknięcie).
    total_fee = TRANSACTION_FEE_PERCENT * 2
    real_distance = sl_distance_percentage + total_fee
    
    return round(real_distance, 4)

# --- ZAKTUALIZOWANA FUNKCJA POMOCNICZA ---
def get_sl_distances_for_alert(alert: AlertData) -> Dict[str, float]:
    """
    Funkcja pomocnicza, która pobiera dane z alertu i zwraca słownik z odległościami.
    """
    entry = alert.entry
    sl = alert.sl
    
    distance_points = calculate_sl_distance_points(entry, sl)
    distance_percentage = calculate_sl_distance_percentage(entry, sl)
    
    # Obliczamy realną odległość na podstawie już obliczonej procentowej
    real_distance_percentage = calculate_real_sl_distance_percentage(distance_percentage)
    
    return {
        "distance_points": distance_points,
        "distance_percentage": distance_percentage,
        "distance_percentage_real": real_distance_percentage # NOWA ZWRACANA WARTOŚĆ
    }
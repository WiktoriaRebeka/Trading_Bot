# Lokalizacja: shared_lib/leverage_calculator.py

import logging
from typing import Dict

from shared_lib.models import AlertData

logger = logging.getLogger(__name__)

def calculate_sl_distance_points(entry_price: float, sl_price: float) -> float:
    """
    Oblicza bezwzględną odległość w punktach między ceną wejścia a stop lossem.

    Jest to podstawowa, uniwersalna funkcja do mierzenia ryzyka w jednostkach ceny.

    Args:
        entry_price (float): Cena wejścia w pozycję.
        sl_price (float): Cena ustawienia stop loss.

    Returns:
        float: Bezwzględna różnica między ceną wejścia a stop lossem.
               Zwraca 0.0, jeśli ceny są identyczne, i loguje ostrzeżenie.
    """
    if entry_price == sl_price:
        logger.warning(
            f"Cena wejścia ({entry_price}) i stop loss ({sl_price}) są identyczne. "
            "Odległość SL wynosi 0, co może prowadzić do dzielenia przez zero w dalszych kalkulacjach."
        )
        return 0.0

    distance = abs(entry_price - sl_price)
    return round(distance, 8) # Zaokrąglenie do 8 miejsc po przecinku dla precyzji

def calculate_sl_distance_percentage(entry_price: float, sl_price: float) -> float:
    """
    Oblicza odległość stop lossa jako procent ceny wejścia.

    Ta metryka jest kluczowa do standaryzacji ryzyka niezależnie od ceny aktywa.

    Args:
        entry_price (float): Cena wejścia w pozycję.
        sl_price (float): Cena ustawienia stop loss.

    Returns:
        float: Odległość SL jako procent (np. 1.5 dla 1.5%).
               Zwraca 0.0 w przypadku błędu lub zerowej ceny wejścia.
    """
    if entry_price == 0:
        logger.error(
            "Cena wejścia wynosi 0. Nie można obliczyć procentowej odległości SL."
        )
        return 0.0

    sl_distance_points = calculate_sl_distance_points(entry_price, sl_price)
    
    # Jeśli odległość to 0, procent też jest 0
    if sl_distance_points == 0.0:
        return 0.0

    percentage = (sl_distance_points / entry_price) * 100
    return round(percentage, 4) # Zaokrąglenie do 4 miejsc po przecinku

def get_sl_distances_for_alert(alert: AlertData) -> Dict[str, float]:
    """
    Funkcja pomocnicza, która pobiera dane z alertu i zwraca słownik z odległościami.

    Args:
        alert (AlertData): Obiekt alertu Pydantic.

    Returns:
        Dict[str, float]: Słownik zawierający odległość w punktach i procentach.
    """
    entry = alert.entry
    sl = alert.sl
    
    distance_points = calculate_sl_distance_points(entry, sl)
    distance_percentage = calculate_sl_distance_percentage(entry, sl)
    
    return {
        "distance_points": distance_points,
        "distance_percentage": distance_percentage
    }
# orderflow_engine/confidence_scorer.py

import logging

logger = logging.getLogger(__name__)

class ConfidenceScorer:
    """
    Oblicza Confidence Score (0-100) dla setupu tradingowego.
    
    SCORING SYSTEM:
    - Liquidations: 0-30 pts
    - Delta Divergence: 0-25 pts
    - DOM/OBI: 0-20 pts
    - Structure Quality: 0-15 pts
    - Funding Rate (contrarian): 0-10 pts
    """
    
    def calculate(self, setup_data: dict) -> float:
        """
        Oblicza total confidence score.
        
        Args:
            setup_data: Dict z kluczami:
                - liquidation_volume_usd
                - delta_divergence (bool)
                - delta_strength
                - obi (Order Book Imbalance)
                - dom_wall_detected (bool)
                - structure_strength (1-5)
                - funding_rate
                - direction ('LONG' or 'SHORT')
        
        Returns:
            Score 0-100
        """
        score = 0.0
        
        # ========================================
        # 1. LIQUIDATION SCORE (0-30 pts)
        # ========================================
        liq_vol = setup_data.get('liquidation_volume_usd', 0)
        
        if liq_vol > 500000:
            score += 30
        elif liq_vol > 200000:
            score += 25
        elif liq_vol > 100000:
            score += 20
        elif liq_vol > 50000:
            score += 10
        else:
            score += 0
        
        # ========================================
        # 2. DELTA DIVERGENCE SCORE (0-25 pts)
        # ========================================
        if setup_data.get('delta_divergence'):
            delta_strength = setup_data.get('delta_strength', 0)
            
            # Normalizuj siłę (przykładowo: 0-5000 BTC volume)
            # Im większa różnica w Delta, tym silniejsza dywergencja
            normalized = min(delta_strength / 5000, 1.0)
            score += normalized * 25
        
        # ========================================
        # 3. DOM/OBI SCORE (0-20 pts)
        # ========================================
        obi = abs(setup_data.get('obi', 0))
        
        if obi > 0.6:
            score += 20
        elif obi > 0.4:
            score += 15
        elif obi > 0.2:
            score += 10
        else:
            score += 5
        
        # Bonus za ścianę w DOM
        if setup_data.get('dom_wall_detected'):
            score += 5
        
        # ========================================
        # 4. STRUCTURE QUALITY SCORE (0-15 pts)
        # ========================================
        # Czy to EQL/EQH (multi-touch) vs single swing?
        structure_strength = setup_data.get('structure_strength', 1)
        
        if structure_strength >= 3:  # 3+ touches (Equal Lows/Highs)
            score += 15
        elif structure_strength == 2:
            score += 10
        else:
            score += 5
        
        # ========================================
        # 5. FUNDING RATE BONUS (0-10 pts)
        # ========================================
        # Contrarian approach: Jeśli wszyscy są na jednej stronie,
        # trade w przeciwną stronę ma wyższy potencjał
        
        funding = setup_data.get('funding_rate', 0)
        direction = setup_data.get('direction', '')
        
        # HIGH funding (>0.05%) = Wszyscy long -> Short setup lepszy
        if direction == 'SHORT' and funding > 0.05:
            score += 10
        # LOW funding (<-0.05%) = Wszyscy short -> Long setup lepszy
        elif direction == 'LONG' and funding < -0.05:
            score += 10
        # Moderate funding - mały bonus
        elif abs(funding) < 0.01:
            score += 5
        
        # ========================================
        # FINAL SCORE (CAP AT 100)
        # ========================================
        final_score = min(score, 100)
        
        logger.debug(f"Confidence breakdown: Liq={liq_vol/1000:.0f}k, "
                    f"Delta={setup_data.get('delta_strength', 0):.0f}, "
                    f"OBI={obi:.2f}, "
                    f"Structure={structure_strength}, "
                    f"Final={final_score:.1f}/100")
        
        return final_score
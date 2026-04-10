from math import hypot
from typing import Dict, List, Optional, Tuple

from app.services.places_google import search_nearby


def _distance_m(a: Tuple[float,float], b: Tuple[float,float]) -> float:
    return hypot((a[0]-b[0])*111_000, (a[1]-b[1])*96_000)

def _availability_penalty(free_ports: int, total_ports: int) -> float:
    if total_ports <= 0: return 1.2
    ratio = max(0.0, min(1.0, 1.0 - (free_ports / total_ports)))
    return 1.0 + 0.2*ratio

def _pref_bonus_for_hub(lat: float, lng: float, user_prefs: Optional[Dict]) -> Tuple[float, List[str]]:
    reasons = []
    if not user_prefs: return 1.0, reasons
    nearby = search_nearby(lat, lng, 450)[:8]
    hit = False
    for place in nearby:
        hits = place.get("pref_hits") or {}
        if any(user_prefs.get(k) and hits.get(k) for k in user_prefs.keys()):
            hit = True
            reasons.append(place.get("nerava_badge","Nearby"))
            break
    return (0.95 if hit else 1.0), reasons

def recommend_hub(lat: float, lng: float, user_prefs: Optional[Dict], hubs: List[Dict]) -> Tuple[Dict, Dict]:
    origin = (lat, lng)
    best, best_score, best_meta = None, 1e12, {}
    for h in hubs:
        d = _distance_m(origin, (h["lat"], h["lng"]))
        avail = _availability_penalty(h.get("free_ports",0), h.get("total_ports",0))
        pref_mult, pref_reasons = _pref_bonus_for_hub(h["lat"], h["lng"], user_prefs)
        score = d * avail * pref_mult
        if score < best_score:
            best, best_score = h, score
            reason_tags = []
            reason_tags.append("closest" if d < 300 else "nearby")
            reason_tags.append(f'{h.get("free_ports",0)}-ports-free')
            for r in pref_reasons:
                reason_tags.append(r.lower().replace(" ","-"))
            best_meta = {"score": round(score,2), "distance_m": int(d), "reason_tags": reason_tags[:3]}
    return best, best_meta

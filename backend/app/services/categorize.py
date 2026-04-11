import json
import os
from functools import lru_cache
from typing import Dict, List, Set, Tuple

PREF_FLAG_MAP = {
    "coffee_drinks": "pref_coffee",
    "dining_sitdown": "pref_food",
    "quick_bite": "pref_food",
    "shopping_retail": "pref_shopping",
    "wellness_fitness": "pref_exercise",
    "parks_outdoors": "pref_exercise",
    "pet_friendly": "pref_dog",
    "kid_family": "pref_kid"
}

@lru_cache(maxsize=1)
def _load_map() -> Dict[str, Set[str]]:
    here = os.path.dirname(__file__)
    with open(os.path.join(here, "categories.json")) as f:
        data = json.load(f)
    return {cat: set(types) for cat, types in data.items()}

def categorize_google_types(google_types: List[str]) -> Tuple[List[str], Dict[str, bool]]:
    if not google_types:
        return [], {}
    mapping = _load_map()
    nerava_cats: Set[str] = set()
    pref_hits: Dict[str, bool] = {}
    norm = {t.strip().lower() for t in google_types if t}
    for cat, type_set in mapping.items():
        if norm & type_set:
            nerava_cats.add(cat)
            pref = PREF_FLAG_MAP.get(cat)
            if pref:
                pref_hits[pref] = True
    return sorted(nerava_cats), pref_hits

def summarize_for_badge(cats: List[str]) -> str:
    priority = [
        ("coffee_drinks", "Coffee"),
        ("quick_bite", "Quick bite"),
        ("dining_sitdown", "Dining"),
        ("shopping_retail", "Shopping"),
        ("groceries", "Groceries"),
        ("wellness_fitness", "Fitness"),
        ("auto_services", "Car care"),
        ("kid_family", "Family"),
        ("pet_friendly", "Pet-friendly"),
        ("parks_outdoors", "Outdoors"),
        ("lodging", "Lodging")
    ]
    for key, label in priority:
        if key in cats:
            return label
    return "Nearby"

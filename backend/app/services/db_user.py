from typing import Dict, Optional

_USERS: Dict[str, Dict] = {}

DEFAULT_PREFS = {
    "pref_coffee": False,
    "pref_food": False,
    "pref_dog": False,
    "pref_kid": False,
    "pref_shopping": False,
    "pref_exercise": False
}

def get_user_by_id(user_id: str) -> Optional[Dict]:
    return _USERS.get(user_id)

def upsert_user_prefs(user_id: str, prefs: Dict) -> bool:
    user = _USERS.setdefault(user_id, {"id": user_id})
    for k, v in DEFAULT_PREFS.items():
        user[k] = bool(prefs.get(k, v))
    return True

def get_prefs_dict(user_id: str) -> Dict:
    u = get_user_by_id(user_id)
    if not u:
        return DEFAULT_PREFS.copy()
    return {k: bool(u.get(k, False)) for k in DEFAULT_PREFS.keys()}

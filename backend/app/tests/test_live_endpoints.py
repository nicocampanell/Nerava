import json
import urllib.request

BASE = "http://127.0.0.1:8000"

def get(path):
    with urllib.request.urlopen(f"{BASE}{path}") as r:
        return json.loads(r.read().decode("utf-8"))

def test_chargers_domain():
    data = get("/v1/chargers/nearby?lat=30.4021&lng=-97.7265&radius_km=2&max_results=40")
    print("chargers:", len(data))
    assert isinstance(data, list)

def test_hubs_domain():
    data = get("/v1/hubs/nearby?lat=30.4021&lng=-97.7265&radius_km=2")
    print("hubs:", len(data))
    assert isinstance(data, list)

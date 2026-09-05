"""VWorld 지오코딩 래퍼 — 주소↔좌표 양방향.

    from geocode import to_coord, to_address
    x, y = to_coord("서울특별시 송파구 올림픽로 300", crs="epsg:5181")   # 주소 → 좌표
    road, parcel = to_address(x, y, crs="epsg:5181")                    # 좌표 → 주소

키: .env 의 VWORLD_API_KEY (scripts/_env.py 로 로드). 일 40,000건, 실시간만.
기본 CRS 는 epsg:5181 (상권 shp 와 동일) — 필요 시 epsg:4326(WGS84) 등으로 바꿀 것.

CLI:
  .venv/bin/python3 scripts/geocode.py coord "서울특별시 송파구 올림픽로 300"
  .venv/bin/python3 scripts/geocode.py addr 209370 445250
"""
from __future__ import annotations
import json, sys, time, urllib.parse, urllib.request

from _env import require

ENDPOINT = "https://api.vworld.kr/req/address"
_DEFAULT_CRS = "epsg:5181"


def _call(params: dict) -> dict:
    params = {**params, "service": "address", "version": "2.0",
              "format": "json", "key": require("VWORLD_API_KEY")}
    url = ENDPOINT + "?" + urllib.parse.urlencode(params)
    for attempt in range(3):
        try:
            with urllib.request.urlopen(url, timeout=20) as r:
                return json.loads(r.read().decode("utf-8"))
        except Exception as e:
            if attempt == 2:
                raise
            time.sleep(1.5 * (attempt + 1))
    return {}


def to_coord(address: str, crs: str = _DEFAULT_CRS,
             addr_type: str = "road") -> tuple[float, float] | None:
    """주소 → (x, y). 실패 시 None. addr_type: 'road'(도로명) 또는 'parcel'(지번)."""
    res = _call({"request": "getCoord", "crs": crs, "type": addr_type,
                 "address": address, "refine": "true", "simple": "false"})
    r = res.get("response", {})
    if r.get("status") != "OK":
        return None
    pt = r["result"]["point"]
    return float(pt["x"]), float(pt["y"])


def to_address(x: float, y: float, crs: str = _DEFAULT_CRS,
               addr_type: str = "both") -> tuple[str | None, str | None]:
    """좌표 → (도로명주소, 지번주소). 없으면 각각 None."""
    res = _call({"request": "getAddress", "crs": crs,
                 "point": f"{x},{y}", "type": addr_type})
    r = res.get("response", {})
    if r.get("status") != "OK":
        return None, None
    road = parcel = None
    for item in r.get("result", []):
        if item.get("type") == "road":
            road = item.get("text")
        elif item.get("type") == "parcel":
            parcel = item.get("text")
    return road, parcel


if __name__ == "__main__":
    if len(sys.argv) >= 3 and sys.argv[1] == "coord":
        print(to_coord(" ".join(sys.argv[2:])))
    elif len(sys.argv) == 4 and sys.argv[1] == "addr":
        print(to_address(float(sys.argv[2]), float(sys.argv[3])))
    else:
        print(__doc__)
        sys.exit(1)

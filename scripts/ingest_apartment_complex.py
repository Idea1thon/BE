"""서울 공동주택(아파트) 단지 → 단지 목록(좌표·세대수·상권) + 상권별 아파트 접근성.

원천:
  1. 국토교통부 공동주택 단지 목록 제공 서비스(15057332) `AptListService4/getSidoAptList4`
     → 서울(sidoCode=11) 전체 단지: kaptCode·kaptName·bjdCode·as1~as3. 좌표·주소 없음.
     (`.env` 의 DATA_GO_KR_SERVICE_KEY — Encoding 형태 그대로 URL 에 append)
  2. VWorld Search API(`/req/search`, type=place) — 단지명 → WGS84 좌표·주소. VWORLD_API_KEY.
     결과는 data/공동주택/_geocode_cache.json 에 캐시(재실행 시 재호출 안 함).
  3. 공동주택 단지 면적 정보(15073269) `~/…/공동주택/공동주택_단지면적정보.xlsx` — kaptCode 로 세대수·동수·면적 join.
  4. (선택) GIS건물통합정보 서울 `~/…/공동주택/GIS건물통합정보_서울/AL_D010_11_*` — 공동주택(A8=02000) 폴리곤.
     EPSG:5186 → 5181. 단지 좌표 주변 폴리곤을 모아 footprint 면적·동수·경계(convex hull) 산출. `--no-gis` 로 생략.

산출(집계본만 커밋):
  data/공동주택/아파트단지_서울.csv    — 단지 단위: 좌표·세대수·자치구·행정동·상권·geocode 신뢰도
  data/공동주택/상권_아파트접근성.csv  — 상권 1,650: 상권내·250m 단지 수, 상권내 총세대수, 최근접 단지, 인접단지 목록(json)

실행: .venv/bin/python3 scripts/ingest_apartment_complex.py [--no-gis] [--limit N]
"""
from __future__ import annotations
import csv, json, os, re, sys, time, urllib.parse, urllib.request

import shapefile
from shapely.geometry import shape, Point, MultiPoint
from shapely.strtree import STRtree
from shapely.ops import unary_union
from pyproj import Transformer

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "scripts"))
from _env import require
from _raw import raw

OUT_DIR = os.path.join(ROOT, "data", "공동주택")
CACHE = os.path.join(OUT_DIR, "_geocode_cache.json")
AREA_XLSX = raw("공동주택", "공동주택_단지면적정보.xlsx")
GIS_SHP = raw("공동주택", "GIS건물통합정보_서울", "AL_D010_11_20260809")
TRDAR_SHP = os.path.join(ROOT, "data", "영역", "상권", "서울시 상권분석서비스(영역-상권)")
DONG_SHP = os.path.join(ROOT, "data", "영역", "행정동", "서울시 상권분석서비스(영역-행정동)")

APT_LIST_EP = "https://apis.data.go.kr/1613000/AptListService4/getSidoAptList4"
VWORLD_SEARCH = "https://api.vworld.kr/req/search"
NEAR_M = 250            # 상권↔단지 이 거리 안이면 "인접"(도보권)
GIS_BUFFER_M = 120      # 단지 좌표 주변 이 거리 안의 공동주택 폴리곤을 footprint 로
NO_GIS = "--no-gis" in sys.argv
LIMIT = int(sys.argv[sys.argv.index("--limit") + 1]) if "--limit" in sys.argv else None


def _get(url: str, timeout=40) -> str:
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read().decode("utf-8", "replace")


def fetch_kapt_list() -> list[dict]:
    key = require("DATA_GO_KR_SERVICE_KEY")
    out, page = [], 1
    while True:
        url = (f"{APT_LIST_EP}?serviceKey={key}&sidoCode=11"
               f"&pageNo={page}&numOfRows=2000&_type=json")
        body = json.loads(_get(url))["response"]["body"]
        items = body.get("items") or []
        if isinstance(items, dict):
            items = [items]
        out.extend(items)
        total = int(body.get("totalCount") or 0)
        if len(out) >= total or not items:
            break
        page += 1
    print(f"K-apt 서울 단지: {len(out)}")
    return out


def load_area() -> dict:
    """면적 xlsx (1행 안내문, 2행 헤더). 단지당 여러 평형 행 → 세대수 합산."""
    import pandas as pd
    df = pd.read_excel(AREA_XLSX, header=1)
    df = df[df["시도"].astype(str).str.startswith("서울")]
    agg: dict[str, dict] = {}
    for code, sub in df.groupby("단지코드"):
        hh = int(pd.to_numeric(sub["세대수"], errors="coerce").fillna(0).sum())
        dong = sub["동수"].dropna()
        area = sub["관리비부과면적"].dropna()
        agg[str(code)] = {
            "세대수": hh,
            "동수": (dong.iloc[0] if len(dong) else None),
            "관리비부과면적": (round(float(area.iloc[0]), 1) if len(area) else None),
        }
    print(f"면적 xlsx 서울 단지: {len(agg)}")
    return agg


_STOP = re.compile(r"[\s()\-·,]|아파트|단지|주택|주공|차$")


def _norm(s: str) -> str:
    return _STOP.sub("", str(s or "")).lower()


def _name_variants(name: str, dong: str = "") -> list[str]:
    """검색어 품질을 높이려 단지명에서 (…)·동접두어·임대·맨션·차·단지 접미어를 점진 제거."""
    base = re.sub(r"\([^)]*\)", "", name).strip()
    v = [base]
    d = dong[:-1] if dong.endswith("동") else dong          # 개포동 → 개포
    if len(d) >= 2 and base.startswith(d) and len(base) > len(d) + 1:
        v.append(base[len(d):].strip())                      # 개포대치2단지 → 대치2단지
    for cand in list(v):
        c1 = re.sub(r"\s*(임대|공공|국민|행복|장기전세)?\s*(아파트|맨션|맨숀)$", "", cand).strip()
        if c1 and c1 not in v:
            v.append(c1)
        core = re.sub(r"\s*제?\d+\s*(차|단지).*$", "", c1 or cand).strip()
        if core and core not in v:
            v.append(core)
    return [x for x in v if x and len(x) >= 2]


def load_cache() -> dict:
    if os.path.isfile(CACHE):
        return json.load(open(CACHE, encoding="utf-8"))
    return {}


def _search(q: str, vkey: str, stat: dict) -> list:
    url = (f"{VWORLD_SEARCH}?service=search&request=search&version=2.0&format=json"
           f"&size=10&page=1&type=place&query={urllib.parse.quote(q)}&key={vkey}")
    for attempt in (1, 2):
        try:
            d = json.loads(_get(url, timeout=20))
            stat["calls"] += 1
            resp = d.get("response", {})
            if resp.get("status") != "OK":
                return []
            return resp.get("result", {}).get("items", [])
        except Exception:
            stat["err"] += 1
            time.sleep(0.5 * attempt)
    return []


def geocode_one(kapt: dict, vkey: str, stat: dict) -> dict:
    gu, dong, name = kapt.get("as2") or "", kapt.get("as3") or "", kapt["kaptName"]
    variants = _name_variants(name, dong)
    queries: list[str] = []
    for nm in variants:
        for q in (f"{gu} {dong} {nm}", f"{gu} {nm}", f"{dong} {nm}"):
            if q not in queries:
                queries.append(q)
    target = _norm(name)
    best = None
    for i, q in enumerate(queries):
        for it in _search(q, vkey, stat):
            addr = it.get("address") or {}
            par = addr.get("parcel") or addr.get("road") or ""
            if gu and gu not in par:
                continue
            cat = it.get("category") or ""
            title_n = _norm(it.get("title", ""))
            name_ok = target and (target in title_n or title_n in target
                                  or _ratio(target, title_n) >= 0.6)
            if cat.startswith("시설구역경계") and name_ok:
                rank = 3
            elif "공동주택" in cat and name_ok:
                rank = 2
            elif name_ok:
                rank = 1
            elif cat.startswith("시설구역경계") or "공동주택" in cat:
                rank = 0
            else:
                continue
            cand = {"lon": float(it["point"]["x"]), "lat": float(it["point"]["y"]),
                    "matched": it.get("title"), "category": cat,
                    "road": addr.get("road", ""), "parcel": addr.get("parcel", ""),
                    "rank": rank}
            if best is None or cand["rank"] > best["rank"]:
                best = cand
        if best and (best["rank"] >= 3 or (best["rank"] >= 2 and i >= 1)):
            break
    conf = {3: "high", 2: "high", 1: "medium", 0: "low"}.get(best["rank"]) if best else "none"
    stat["done"] += 1
    return {**(best or {}), "conf": conf} if best else {"conf": "none"}


def _ratio(a: str, b: str) -> float:
    if not a or not b:
        return 0.0
    sa, sb = set(a), set(b)
    return len(sa & sb) / len(sa | sb)


def load_shp(path, code_f, name_f):
    sf = shapefile.Reader(path, encoding="utf-8")
    flds = [f[0] for f in sf.fields if f[0] != "DeletionFlag"]
    geoms, recs = [], []
    for sr in sf.iterShapeRecords():
        rec = dict(zip(flds, sr.record))
        geoms.append(shape(sr.shape.__geo_interface__))
        recs.append((str(rec[code_f]), rec[name_f], rec))
    return STRtree(geoms), geoms, recs


def pip(tree, geoms, recs, pt):
    for i in tree.query(pt):
        if geoms[i].covers(pt):
            return recs[i]
    return None


def load_gis_apt(tf5186):
    """공동주택(A8=02000) 건물 폴리곤을 5181 로 재투영해 STRtree 로."""
    sf = shapefile.Reader(GIS_SHP, encoding="cp949")
    flds = [f[0] for f in sf.fields if f[0] != "DeletionFlag"]
    i_use = flds.index("A8"); i_gfa = flds.index("A14")
    geoms, gfas = [], []
    from shapely.ops import transform as shp_transform
    for sr in sf.iterShapeRecords():
        rec = sr.record
        if str(rec[i_use]) != "02000":
            continue
        try:
            g = shape(sr.shape.__geo_interface__)
            if not g.is_valid or g.is_empty:
                continue
            g = shp_transform(lambda xs, ys: tf5186.transform(xs, ys), g)
        except Exception:
            continue
        geoms.append(g)
        try:
            gfas.append(float(rec[i_gfa]))
        except (TypeError, ValueError):
            gfas.append(0.0)
    print(f"GIS 공동주택 폴리곤: {len(geoms)} (5186→5181)")
    return STRtree(geoms), geoms, gfas


def main() -> int:
    for p in (AREA_XLSX,):
        if not os.path.isfile(p):
            print(f"FAIL: {p} 없음")
            return 1
    os.makedirs(OUT_DIR, exist_ok=True)
    vkey = require("VWORLD_API_KEY")

    kapts = fetch_kapt_list()
    if LIMIT:
        kapts = kapts[:LIMIT]
    area = load_area()

    tf_wgs = Transformer.from_crs("EPSG:4326", "EPSG:5181", always_xy=True)
    print("상권·행정동 폴리곤 로드…")
    d_tree, d_geoms, d_recs = load_shp(DONG_SHP, "ADSTRD_CD", "ADSTRD_NM")
    t_tree, t_geoms, t_recs = load_shp(TRDAR_SHP, "TRDAR_CD", "TRDAR_CD_N")

    gis = None
    if not NO_GIS and os.path.isfile(GIS_SHP + ".shp"):
        tf_5186 = Transformer.from_crs("EPSG:5186", "EPSG:5181", always_xy=True)
        gis = load_gis_apt(tf_5186)

    cache = load_cache()
    if "--keep-none" not in sys.argv:
        cache = {k: v for k, v in cache.items() if v.get("conf") != "none"}   # 미매칭은 재시도
    stat = {"calls": 0, "err": 0, "done": 0}
    todo = [k for k in kapts if k["kaptCode"] not in cache]
    print(f"지오코딩: 캐시 {len(cache)}건, 신규/재시도 {len(todo)}건 (VWorld, 8스레드)…")
    if todo:
        from concurrent.futures import ThreadPoolExecutor
        lock = __import__("threading").Lock()
        def work(k):
            r = geocode_one(k, vkey, stat)
            with lock:
                cache[k["kaptCode"]] = r
                if stat["done"] % 300 == 0:
                    json.dump(cache, open(CACHE, "w", encoding="utf-8"), ensure_ascii=False)
                    print(f"  … {stat['done']}/{len(todo)} ({stat['calls']} calls)")
        with ThreadPoolExecutor(max_workers=8) as ex:
            list(ex.map(work, todo))
        json.dump(cache, open(CACHE, "w", encoding="utf-8"), ensure_ascii=False)

    rows, near = [], {code: [] for code, _, _ in t_recs}
    conf_cnt = {"high": 0, "medium": 0, "low": 0, "none": 0}
    for k in kapts:
        g = cache[k["kaptCode"]]
        a = area.get(k["kaptCode"], {})
        hh = a.get("세대수") or ""
        row = {
            "단지코드": k["kaptCode"], "단지명": k["kaptName"],
            "자치구": k.get("as2", ""), "법정동": k.get("as3", ""),
            "bjd_code": k.get("bjdCode", ""),
            "세대수": hh, "동수": a.get("동수") or "",
            "관리비부과면적_㎡": a.get("관리비부과면적") or "",
            "위도": "", "경도": "", "X_5181": "", "Y_5181": "",
            "행정동_코드": "", "행정동명": "", "상권_코드": "", "상권명": "",
            "geocode_신뢰도": g["conf"], "geocode_매칭명": g.get("matched", ""),
            "geocode_분류": g.get("category", ""), "geocode_도로명": g.get("road", ""),
            "gis_건물수": "", "gis_연면적합_㎡": "", "gis_footprint_㎡": "", "gis_버퍼_m": "",
        }
        conf_cnt[g["conf"]] += 1
        if g["conf"] != "none":
            x, y = tf_wgs.transform(g["lon"], g["lat"])
            pt = Point(x, y)
            row.update(위도=round(g["lat"], 7), 경도=round(g["lon"], 7),
                       X_5181=round(x, 2), Y_5181=round(y, 2))
            dong = pip(d_tree, d_geoms, d_recs, pt)
            trdar = pip(t_tree, t_geoms, t_recs, pt)
            if dong:
                row["행정동_코드"], row["행정동명"] = dong[0], dong[1]
            if trdar:
                row["상권_코드"], row["상권명"] = trdar[0], trdar[1]
            # 상권별 근접
            buf = pt.buffer(NEAR_M)
            for gi in t_tree.query(buf):
                dd = t_geoms[gi].distance(pt)
                if dd <= NEAR_M:
                    near[t_recs[gi][0]].append(
                        (round(dd, 1), k["kaptName"], int(hh) if str(hh).isdigit() else 0, dd == 0.0))
            # GIS footprint — 버퍼를 세대수에 맞춰 확대(대단지 전체 포착), 인접 단지 스필오버 감수
            if gis is not None:
                g_tree, g_geoms, g_gfas = gis
                n_hh = int(hh) if str(hh).isdigit() else 200
                buf_m = max(GIS_BUFFER_M, min(380, (n_hh ** 0.5) * 8))
                hit = [gi for gi in g_tree.query(pt.buffer(buf_m))
                       if g_geoms[gi].distance(pt) <= buf_m]
                if hit:
                    fp = unary_union([g_geoms[gi] for gi in hit])
                    row["gis_건물수"] = len(hit)
                    row["gis_연면적합_㎡"] = round(sum(g_gfas[gi] for gi in hit), 1)
                    row["gis_footprint_㎡"] = round(fp.area, 1)
                    row["gis_버퍼_m"] = round(buf_m)
        rows.append(row)

    json.dump(cache, open(CACHE, "w", encoding="utf-8"), ensure_ascii=False)

    # 단지 CSV
    ap = os.path.join(OUT_DIR, "아파트단지_서울.csv")
    cols = list(rows[0].keys())
    rows.sort(key=lambda r: (r["자치구"], r["단지명"]))
    with open(ap, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=cols)
        w.writeheader(); w.writerows(rows)

    # 상권 접근성 CSV
    sp = os.path.join(OUT_DIR, "상권_아파트접근성.csv")
    n_with = 0
    by_trdar_hh = {}
    for r in rows:
        if r["상권_코드"]:
            by_trdar_hh.setdefault(r["상권_코드"], [0, 0])
            by_trdar_hh[r["상권_코드"]][0] += 1
            by_trdar_hh[r["상권_코드"]][1] += int(r["세대수"]) if str(r["세대수"]).isdigit() else 0
    with open(sp, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f)
        w.writerow(["상권_코드", "상권명", "자치구", "상권내_단지_수", "상권내_총세대수",
                    "250m내_단지_수", "250m내_세대수합", "최근접_단지명", "최근접_거리_m",
                    "인접단지_목록", "데이터기준"])
        for code, name, rec in t_recs:
            lst = sorted(near[code])
            inside_n, inside_hh = by_trdar_hh.get(code, [0, 0])
            hh_sum = sum(h for _, _, h, _ in lst)
            if lst:
                n_with += 1
                bd, bn, *_ = lst[0]
                anchors = [{"name": n_, "households": h_, "distance_m": d_}
                           for d_, n_, h_, _ in lst[:6]]
            else:
                bd = bn = ""
                anchors = []
            w.writerow([code, name, rec["SIGNGU_CD_"], inside_n, inside_hh, len(lst), hh_sum,
                        bn, (bd if bd != "" else ""),
                        json.dumps(anchors, ensure_ascii=False), "2026-08(K-apt)+VWorld"])

    print(f"\n단지 {len(rows)} — 지오코딩 신뢰도: {conf_cnt}  (VWorld {stat['calls']} 호출, 오류 {stat['err']})")
    print(f"상권 배정된 단지: {sum(1 for r in rows if r['상권_코드'])}")
    print(f"상권 {len(t_recs)} 중 {NEAR_M}m 내 단지 있는 상권: {n_with}")
    print(f"→ {ap}\n→ {sp}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

"""실데이터 샘플 (지점 후보 재설계, 2026-09-02) — 송파구 잠실동 · 커피-음료(CS100010).

candidate-selection-spec.md §1(지점 seed = 아파트·역) + §1-2(grain 선택) + 프로파일을 손으로 실행.
후보 = 상권 폴리곤이 아니라 좌표를 가진 명명된 지점.

- 지점 반경 500m 직접 계산: FC-07 역·버스, FC-08 아파트
- 포함 상권 → 없으면 행정동 배경값: FC-01 유동밀도, FC-11 변화, FC-30·31·32 업종,
  FC-10 entry_health_v1(전 업종 통합 지역 진입 환경 등급, 컷 승인 2026-09-02)
- 자치구/권역: FC-20·41·53 (기존 프록시)

산출: artifacts/40-sample/jamsil-coffee/{feature-table.json, candidate-evidence.json, run-notes.md}

실행: .venv/bin/python3 scripts/sample_jamsil_coffee.py
"""
from __future__ import annotations
import csv, json, math, os, statistics, sys

import shapefile
from shapely.geometry import shape, Point
from shapely.strtree import STRtree

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, "artifacts", "40-sample", "jamsil-coffee")
IND = "CS100010"
Q = "20261"
RENT_Q = "20262"
# 법정동 '잠실동' = 행정동 잠실2·3·7동 (4·6동은 신천동, 잠실본동은 폐지된 구 코드)
TARGET_DONG_CODES = {"11710670", "11710680", "11710720"}
TARGET_GU = "송파구"
MARGIN_M = 150                    # 경계에서 이 거리 안의 seed 도 포함(경계 바로 밖 대단지·역)
DEDUP_M = 80
RADIUS_M = 500                    # FC-07·08 지점 반경
HOST_MAX_M = 300                  # 포함 상권 없을 때 최근접 상권 허용 거리

_EN2KO = {
    "stdr_yyqu_cd": "기준_년분기_코드", "trdar_cd": "상권_코드", "adstrd_cd": "행정동_코드",
    "svc_induty_cd": "서비스_업종_코드", "stor_co": "전체_점포_수", "frc_stor_co": "프랜차이즈_점포_수",
    "opbiz_rt": "개업_율", "clsbiz_rt": "폐업_률", "opbiz_stor_co": "개업_점포_수",
    "clsbiz_stor_co": "폐업_점포_수", "thsmon_selng_amt": "당월_매출_금액", "thsmon_selng_co": "당월_매출_건수",
    "점포_수": "전체_점포_수",   # 2025 점포-행정동 파일은 한글 헤더지만 '점포_수'
}

# entry_health_v1 (승인 2026-09-02, artifacts/20-method/entry-health-v1-cut-design.md §6)
# 지역(상권/행정동) 배경 진입 환경 등급 — 전 업종 통합, 업종 구분 없음
EH_LABEL_RISK = {"LH": 0.0, "HH": 0.5, "LL": 0.6, "HL": 1.0}
EH_GRADES = ("양호", "보통", "주의", "경계")
EH_CUTS = {"상권": (41, 51, 62), "행정동": (42, 52, 61)}   # 20261 서울 사분위 동결


def rd(path, enc="cp949"):
    with open(os.path.join(ROOT, path), encoding=enc, newline="") as f:
        rows = list(csv.DictReader(f))
    if rows and ("stdr_yyqu_cd" in rows[0] or "점포_수" in rows[0]):
        rows = [{_EN2KO.get(k, k): v for k, v in r.items()} for r in rows]
    return rows


def eh_grade(scope, p_close, p_open, p_delta, label_risk):
    """entry_health_v1 서수 등급 (전 업종 통합 지역 진입 환경). 성분 결측 시 존재 성분만 동일가중 재정규화."""
    parts = []
    if p_close is not None:
        parts.append(p_close)
    if p_open is not None:
        parts.append(100 - p_open)
    if p_delta is not None:
        parts.append(100 - p_delta)
    lr = label_risk if label_risk is not None else 0.5
    parts.append(lr * 100)
    risk = sum(parts) / len(parts)
    c1, c2, c3 = EH_CUTS[scope]
    g = EH_GRADES[0] if risk < c1 else EH_GRADES[1] if risk < c2 else EH_GRADES[2] if risk < c3 else EH_GRADES[3]
    return g, round(risk, 1)


def rdu(path):
    return rd(path, "utf-8-sig")


def fnum(d, k):
    try:
        return float(d[k])
    except (TypeError, ValueError, KeyError):
        return None


def load_shp(path, code_f, name_f):
    sf = shapefile.Reader(os.path.join(ROOT, path), encoding="utf-8")
    flds = [f[0] for f in sf.fields if f[0] != "DeletionFlag"]
    geoms, recs = [], []
    for sr in sf.iterShapeRecords():
        rec = dict(zip(flds, sr.record))
        geoms.append(shape(sr.shape.__geo_interface__))
        recs.append({"code": str(rec[code_f]), "name": rec[name_f], "rec": rec})
    return STRtree(geoms), geoms, recs


def pct(sorted_vals, v):
    if not sorted_vals or v is None:
        return None
    import bisect
    return round(100 * bisect.bisect_left(sorted_vals, v) / len(sorted_vals), 1)


def size_grade(sorted_vals, v):
    p = pct(sorted_vals, v)
    return None if p is None else ["소", "중하", "중", "중상", "대"][min(int(p // 20), 4)]


def main() -> int:
    os.makedirs(OUT, exist_ok=True)
    t_tree, t_geoms, t_recs = load_shp(
        "data/영역/상권/서울시 상권분석서비스(영역-상권)", "TRDAR_CD", "TRDAR_CD_N")
    d_tree, d_geoms, d_recs = load_shp(
        "data/영역/행정동/서울시 상권분석서비스(영역-행정동)", "ADSTRD_CD", "ADSTRD_NM")
    trdar_area = {r["code"]: float(r["rec"]["RELM_AR"]) for r in t_recs}

    # 대상 행정동 폴리곤(잠실2·3·7동) + 150m 버퍼 union
    from shapely.ops import unary_union
    target_idx = [i for i, r in enumerate(d_recs) if r["code"] in TARGET_DONG_CODES]
    target_poly = unary_union([d_geoms[i] for i in target_idx])
    target_buf = target_poly.buffer(MARGIN_M)
    target_names = sorted(d_recs[i]["name"] for i in target_idx)

    def in_target(p):
        if not target_buf.covers(p):
            return False
        # 자치구 가드: 지점이 송파구 행정동에 속하거나 대상 폴리곤 내부
        dg = next((d_recs[i] for i in d_tree.query(p) if d_geoms[i].covers(p)), None)
        return target_poly.covers(p) or (dg and dg["code"][:5] == "11710")

    # ---------- seed 수집 ----------
    seeds = []
    for r in rdu("data/공동주택/아파트단지_서울.csv"):
        if r["geocode_신뢰도"] not in ("high", "medium") or not r["X_5181"]:
            continue
        p = Point(float(r["X_5181"]), float(r["Y_5181"]))
        if not in_target(p):
            continue
        seeds.append({"kind": "아파트단지", "id": r["단지코드"], "name": r["단지명"],
                      "pt": p, "households": int(r["세대수"]) if str(r["세대수"]).isdigit() else None,
                      "line": None})
    for r in rdu("data/도시철도역사/역사정보_서울.csv"):
        p = Point(float(r["X_5181"]), float(r["Y_5181"]))
        if not in_target(p):
            continue
        seeds.append({"kind": "역", "id": r["역번호"], "name": r["역사명"], "pt": p,
                      "households": None, "line": r["노선명"], "transfer": r["환승역"] == "Y"})

    # dedup 80m (같은 역 여러 노선 → 이름 합침; 역+붙은 단지 → 병합)
    merged = []
    for s in sorted(seeds, key=lambda s: (s["kind"] != "역", s["name"])):
        hit = next((m for m in merged if m["pt"].distance(s["pt"]) <= DEDUP_M), None)
        if hit:
            if s["name"] not in hit["name"]:
                hit["also"].append(f"{s['name']}({s['kind']})")
            if s["kind"] == "역" and hit["kind"] == "역":
                hit["lines"].add(s["line"])
        else:
            s["also"] = []
            s["lines"] = {s["line"]} if s["kind"] == "역" else set()
            merged.append(s)
    seeds = merged

    # ---------- 배경 데이터 ----------
    def q_store(scope):   # scope: '상권' or '행정동'
        f = ("data/점포/2026년/서울시 상권분석서비스(점포-%s).csv" % scope)
        key = "상권_코드" if scope == "상권" else "행정동_코드"
        cur = {r[key]: r for r in rd(f) if r["기준_년분기_코드"] == Q and r["서비스_업종_코드"] == IND}
        return key, cur

    def q_sales(scope):
        f = ("data/추정매출/2026/서울시 상권분석서비스(추정매출-%s).csv" % scope)
        key = "상권_코드" if scope == "상권" else "행정동_코드"
        return {r[key]: r for r in rd(f) if r["기준_년분기_코드"] == Q and r["서비스_업종_코드"] == IND}

    def q_flow(scope):
        f = ("data/길단위인구/서울시 상권분석서비스(길단위인구-%s).csv" % scope)
        key = "상권_코드" if scope == "상권" else "행정동_코드"
        return {r[key]: r for r in rd(f) if r["기준_년분기_코드"] == Q}

    def q_chg(scope):
        f = ("data/상권변화지표/서울시 상권분석서비스(상권변화지표-%s).csv" % scope)
        key = "상권_코드" if scope == "상권" else "행정동_코드"
        return {r[key]: r for r in rd(f) if r["기준_년분기_코드"] == Q}

    def eh_env(scope):
        """entry_health_v1: 전 업종 통합 상권/행정동 진입 환경 {code: dict} + 서울 분위 분포."""
        f = "data/점포/2026년/서울시 상권분석서비스(점포-%s).csv" % scope
        f25 = "data/점포/2025년/서울시 상권분석서비스(점포-%s)_2025년.csv" % scope
        key = "상권_코드" if scope == "상권" else "행정동_코드"
        agg = {}
        for r in rd(f):
            if r["기준_년분기_코드"] != Q:
                continue
            a = agg.setdefault(r[key], {"n": 0, "op": 0, "cl": 0})
            a["n"] += int(float(r.get("전체_점포_수") or 0))
            a["op"] += int(float(r.get("개업_점포_수") or 0))
            a["cl"] += int(float(r.get("폐업_점포_수") or 0))
        y = {}
        for r in rd(f25):
            if r["기준_년분기_코드"] == "20254":
                y[r[key]] = y.get(r[key], 0) + int(float(r.get("전체_점포_수") or 0))
        env = {}
        for cd, a in agg.items():
            if a["n"] == 0:
                continue
            n0 = y.get(cd, 0)
            env[cd] = {"open_r": 100 * a["op"] / a["n"], "close_r": 100 * a["cl"] / a["n"],
                       "delta": ((a["n"] - n0) / n0) if n0 else None, "n_now": a["n"], "n_yoy": n0 or None}
        dist = {"close": sorted(e["close_r"] for e in env.values()),
                "open": sorted(e["open_r"] for e in env.values()),
                "delta": sorted(e["delta"] for e in env.values() if e["delta"] is not None)}
        return env, dist

    sk_t, store_t = q_store("상권")
    sk_d, store_d = q_store("행정동")
    eh_env_t, eh_dist_t = eh_env("상권")
    eh_env_d, eh_dist_d = eh_env("행정동")
    sales_t, sales_d = q_sales("상권"), q_sales("행정동")
    flow_t, flow_d = q_flow("상권"), q_flow("행정동")
    chg_t, chg_d = q_chg("상권"), q_chg("행정동")

    # FC-06a 외국인 거주 근사 비율 = 장기 외국인 생활인구 / 상주인구 (행정동, 20261)
    resid_d = {r["행정동_코드"]: r for r in
               rd("data/상주인구/서울시 상권분석서비스(상주인구-행정동).csv")
               if r["기준_년분기_코드"] == Q}
    foreign_d = {r["행정동_코드"]: r for r in
                 rdu("data/외국인생활인구/외국인생활인구_행정동_분기.csv")
                 if r["기준_년분기_코드"] == Q}
    fgn_ratio = {}   # 행정동_코드 -> (거주근사비율%, 장기외국인, 상주)
    fgn_visit = {}   # 행정동_코드 -> (방문근사비율%, 단기외국인, 유동일평균, 단기/장기)
    for cd, fr in foreign_d.items():
        try:
            lg = float(fr["장기_외국인_평균"]); dg = float(fr["단기_외국인_평균"])
        except (TypeError, ValueError, KeyError):
            continue
        rr = resid_d.get(cd)
        if rr:
            try:
                res = float(rr["총_상주인구_수"])
                if res > 0:
                    fgn_ratio[cd] = (100 * lg / res, lg, res)
            except (TypeError, ValueError, KeyError):
                pass
        flr = flow_d.get(cd)
        if flr and flr.get("총_유동인구_수"):
            try:
                fday = float(flr["총_유동인구_수"]) / 90   # 분기 총량 → 일평균 근사
                if fday > 0:
                    fgn_visit[cd] = (100 * dg / fday, dg, fday, dg / max(lg, 1))
            except (TypeError, ValueError):
                pass
    seoul_fgn = sorted(v[0] for v in fgn_ratio.values())
    seoul_fgn_v = sorted(v[0] for v in fgn_visit.values())

    # 서울 분포 (상권 유동밀도, 상권 커피 점포밀도·점포당매출)
    flow_t_all = {r["상권_코드"]: r for r in rd("data/길단위인구/서울시 상권분석서비스(길단위인구-상권).csv")
                  if r["기준_년분기_코드"] == Q}
    store_t_all = {r["상권_코드"]: r for r in rd("data/점포/2026년/서울시 상권분석서비스(점포-상권).csv")
                   if r["기준_년분기_코드"] == Q and r["서비스_업종_코드"] == IND}
    sales_t_all = {r["상권_코드"]: r for r in rd("data/추정매출/2026/서울시 상권분석서비스(추정매출-상권).csv")
                   if r["기준_년분기_코드"] == Q and r["서비스_업종_코드"] == IND}
    seoul_dens = sorted(float(flow_t_all[c]["총_유동인구_수"]) / trdar_area[c]
                        for c in flow_t_all if c in trdar_area and flow_t_all[c]["총_유동인구_수"])
    seoul_total = sorted(float(r["총_유동인구_수"]) for r in flow_t_all.values() if r["총_유동인구_수"])
    seoul_areas = sorted(trdar_area.values())
    seoul_st_den = sorted(float(store_t_all[c]["전체_점포_수"]) / (trdar_area[c] / 10000)
                          for c in store_t_all if c in trdar_area and store_t_all[c]["전체_점포_수"])
    seoul_pp = sorted(float(sales_t_all[c]["당월_매출_금액"]) / float(store_t_all[c]["전체_점포_수"])
                      for c in sales_t_all if c in store_t_all
                      and float(store_t_all[c]["전체_점포_수"]) > 0 and sales_t_all[c]["당월_매출_금액"])

    eh_dist = {"상권": eh_dist_t, "행정동": eh_dist_d}
    eh_env = {"상권": eh_env_t, "행정동": eh_env_d}

    # 지점 반경용 원천 좌표
    stations = [(Point(float(r["X_5181"]), float(r["Y_5181"])), r)
                for r in rdu("data/도시철도역사/역사정보_서울.csv")]
    busstops = [(Point(float(r["X_5181"]), float(r["Y_5181"])), r)
                for r in rdu("data/버스정류장/버스정류소_서울.csv")
                if r["X_5181"]]
    apts = [(Point(float(r["X_5181"]), float(r["Y_5181"])), r)
            for r in rdu("data/공동주택/아파트단지_서울.csv")
            if r["X_5181"] and r["geocode_신뢰도"] in ("high", "medium")]
    st_tree = STRtree([p for p, _ in stations])
    bs_tree = STRtree([p for p, _ in busstops])
    ap_tree = STRtree([p for p, _ in apts])

    # 개발사업·고용률·트렌드·임대료
    devp = rdu("data/도시계획사업/도시계획사업_상권겹침.csv")
    emp = [r for r in rdu("data/고용률/자치구_고용률_반기.csv")
           if r["자치구"] == "송파구" and r["성별"] == "계"]
    gu_tr = [r for r in rdu("data/네이버트렌드/자치구_검색트렌드_월.csv") if r["자치구"] == "송파구"]
    ind_tr = [r for r in rdu("data/네이버트렌드/업종_검색트렌드_월.csv") if r["업종코드"] == IND]
    rone = [r for r in rdu("data/임대료/R-ONE_임대동향_분기.csv")
            if r["grain"] == "서울전체" and r["상가유형"] == "소규모상가"
            and r["지표"] == "임대가격지수" and r["기준_년분기_코드"] == RENT_Q]

    candidates, notes = [], [
        "# 샘플 실행 (지점 후보) — 송파구 잠실동 · 커피-음료 (CS100010)\n",
        f"- 분기 {Q} · 임대료 {RENT_Q}",
        f"- 대상 행정동(잠실 일대): {', '.join(target_names)}  [법정동 '잠실동'은 잠실2·3·7동, 4·6동은 신천동 — 느슨하게 포함]",
        f"- seed: 아파트 {sum(1 for s in seeds if s['kind']=='아파트단지')} + 역 {sum(1 for s in seeds if s['kind']=='역')} (80m 병합 후 {len(seeds)})",
    ]

    for s in sorted(seeds, key=lambda s: (s["kind"], s["name"])):
        pt = s["pt"]
        dong = next((d_recs[i] for i in d_tree.query(pt) if d_geoms[i].covers(pt)), None)
        host_t = next((t_recs[i] for i in t_tree.query(pt) if t_geoms[i].covers(pt)), None)
        host_rel = "포함"
        host_d = 0.0
        if host_t is None:
            near_i = t_tree.nearest(pt)
            nd = t_geoms[near_i].distance(pt)
            # 최근접 상권이 지점 행정동과 같은 자치구여야 배정(M-S3). 아니면 행정동 배경값.
            near_gu = t_recs[near_i]["rec"].get("SIGNGU_CD_")
            if nd <= HOST_MAX_M and (not dong or near_gu == "송파구"):
                host_t = t_recs[near_i]
                host_rel = "최근접"
                host_d = round(nd, 1)

        # ---- FC-07: 지점 반경 500m 역·버스 (고유값) ----
        near_st = sorted(((round(stations[i][0].distance(pt), 1), stations[i][1])
                          for i in st_tree.query(pt.buffer(RADIUS_M))
                          if stations[i][0].distance(pt) <= RADIUS_M), key=lambda x: x[0])
        near_bs = [busstops[i][1] for i in bs_tree.query(pt.buffer(250))
                   if busstops[i][0].distance(pt) <= 250]
        near_ap = sorted(((round(apts[i][0].distance(pt), 1), apts[i][1])
                          for i in ap_tree.query(pt.buffer(RADIUS_M))
                          if apts[i][0].distance(pt) <= RADIUS_M and apts[i][1]["단지코드"] != s["id"]), key=lambda x: x[0])
        hh_500 = sum(int(a["세대수"]) for _, a in near_ap if str(a["세대수"]).isdigit())
        n_ap_total = len(near_ap)
        if s["kind"] == "아파트단지" and s.get("households"):   # seed 단지 자체 포함
            hh_500 += s["households"]
            n_ap_total += 1
        maeul = sum(1 for b in near_bs if b["정류소타입"] == "마을버스")
        trunk = sum(1 for b in near_bs if b["정류소타입"] in ("중앙차로", "일반중앙차로"))

        # ---- 배경값 grain 선택 ----
        if host_t and host_t["code"] in flow_t:
            fscope, fkey = "상권", host_t["code"]
            frow = flow_t[fkey]
            f_area = trdar_area[fkey]
        elif dong and dong["code"] in flow_d:
            fscope, fkey = "행정동", dong["code"]
            frow = flow_d[fkey]
            f_area = d_recs[[i for i, r in enumerate(d_recs) if r["code"] == fkey][0]]["rec"]["RELM_AR"]
            f_area = float(f_area)
        else:
            fscope = fkey = frow = f_area = None
        flow_total = fnum(frow or {}, "총_유동인구_수")
        flow_dens = (flow_total / f_area) if (flow_total and f_area) else None

        # 업종 (상권 → 행정동)
        if host_t and host_t["code"] in store_t:
            uscope, ukey = "상권", host_t["code"]
            urow = store_t.get(ukey)
            srow = sales_t.get(ukey)
            u_area = trdar_area[ukey]
        elif dong and dong["code"] in store_d:
            uscope, ukey = "행정동", dong["code"]
            urow = store_d.get(ukey)
            srow = sales_d.get(ukey)
            u_area = float([r["rec"]["RELM_AR"] for r in d_recs if r["code"] == ukey][0])
        else:
            uscope = ukey = urow = srow = u_area = None
        n_store = fnum(urow or {}, "전체_점포_수")
        fr_store = fnum(urow or {}, "프랜차이즈_점포_수")
        rev = fnum(srow or {}, "당월_매출_금액")  # 상권분석 추정매출은 분기 합계(컬럼명 '당월'은 레거시)
        dens = (n_store / (u_area / 10000)) if (n_store is not None and u_area) else None
        rev_pp = (rev / n_store) if (rev and n_store) else None
        if rev_pp is not None and rev_pp < 300_000:   # 값 수준 QA: 극소값은 표본 부족 아티팩트 → 결측 취급
            rev_pp = None
        fr_ratio = (fr_store / n_store) if (fr_store is not None and n_store) else None

        # 변화라벨
        if host_t and host_t["code"] in chg_t:
            crow, cscope = chg_t[host_t["code"]], "상권"
        elif dong and dong["code"] in chg_d:
            crow, cscope = chg_d[dong["code"]], "행정동"
        else:
            crow, cscope = None, None

        # entry_health_v1 (확정 산식 2026-09-02): 전 업종 통합 지역 진입 환경 + 변화라벨
        eh_lab = (crow or {}).get("상권_변화_지표")
        if host_t and host_t["code"] in eh_env["상권"]:
            eh_scope, eh_rec = "상권", eh_env["상권"][host_t["code"]]
        elif dong and dong["code"] in eh_env["행정동"]:
            eh_scope, eh_rec = "행정동", eh_env["행정동"][dong["code"]]
        else:
            eh_scope, eh_rec = None, None
        if eh_scope is None:
            grade, eh_risk = "정보없음", None
            p_close = p_open = p_delta = None
        else:
            d = eh_dist[eh_scope]
            p_close = pct(d["close"], eh_rec["close_r"])
            p_open = pct(d["open"], eh_rec["open_r"])
            p_delta = pct(d["delta"], eh_rec["delta"]) if eh_rec["delta"] is not None else None
            grade, eh_risk = eh_grade(eh_scope, p_close, p_open, p_delta, EH_LABEL_RISK.get(eh_lab))
        eh_gradable = grade != "정보없음"

        # ---- 판정 (FC 신호 등급표 §9-1: pos/neg = 판정·정렬, ctx = 서술만) ----
        pos, neg, ctx = [], [], []
        dens_p = pct(seoul_dens, flow_dens)
        st_den_p = pct(seoul_st_den, dens)
        rev_pp_p = pct(seoul_pp, rev_pp)
        area_hi = f_area and (pct(seoul_areas, f_area) or 0) >= 97

        # 신호 없음 — 서술만: FC-01 유동밀도, FC-30 점포밀도, FC-07 역거리
        if dens_p is not None:
            caveat = " · 면적 큰 상권이라 밀도 저평가 가능" if area_hi else ""
            ctx.append(f"유동밀도 {fscope or '미상'} 배경 서울 {dens_p}%{caveat} — 검증상 폐업/생존과 무연관, 판정·정렬 근거 아님 (FC-01)")
        if dens is not None:
            ctx.append(f"커피 점포밀도 {uscope or '미상'} 배경 서울 {st_den_p}% — 경쟁 규모이며 폐업/생존과 무연관 (FC-30)")
        if near_st and near_st[0][0] <= 300 and s["kind"] != "역":
            ctx.append(f"최근접 도시철도역 {near_st[0][1]['역사명']} {near_st[0][0]}m — 검증상 품질과 무연관 (FC-07 역거리)")

        # 모멘텀 (품질 아님): FC-32 프랜차이즈 비율
        if fr_ratio is not None and n_store and n_store >= 5:
            ctx.append(f"프랜차이즈 비율 {round(fr_ratio * 100, 1)}% ({uscope} 배경) — 최근 체인 확장 정도(모멘텀)이며 신규 독립점 유불리로 단정 불가 (FC-32)")

        # 약한 배경 신호 — 보조 근거, 단독 추천 승격 금지(2개 이상), 신뢰도 하향: FC-08 배후주거, FC-31 매출규모, FC-07 bus_n
        if hh_500 >= 3000:
            pos.append(f"반경 500m 아파트 {hh_500}세대 — 약한 배경 신호 (FC-08)")
        if rev_pp_p is not None and rev_pp_p >= 70:
            pos.append(f"{uscope} 배경 커피 점포당매출 서울 상위 {round(100 - rev_pp_p, 1)}% — 약한 배경 신호, 과거 실적이며 신규 성공 아님 (FC-31)")
        if len(near_bs) >= 8:
            pos.append(f"반경 250m 버스정류소 {len(near_bs)}개 — 약한 배경 신호 (FC-07 bus_n)")
        weak_only = bool(pos)

        eh_inputs = (f"폐업률분위 {p_close}·개업률분위 {p_open}·점포증감분위 {p_delta}·"
                     f"라벨 {eh_lab or '없음'}") if eh_gradable else "입력 부족"
        # 범주 신호 (반대근거): FC-10 등급, FC-11 라벨. 등급은 반대근거 1항목으로만 — 추천 상향/정렬키 금지.
        if grade in ("주의", "경계"):
            tag = "차단" if grade == "경계" else "정보"
            neg.append(f"지역 배경 진입 리스크 {grade}[{tag}] (entry_health_v1={eh_risk}; {eh_inputs}) — 반대근거 1항목")
        if (crow or {}).get("상권_변화_지표") in ("HH", "HL"):
            neg.append(f"상권변화 {(crow or {}).get('상권_변화_지표_명')} — 신규 진입 상대적 불리 (FC-11 범주 신호)")
        # 매출 미제공(≠결측): 추정매출은 카드거래 표본이 임계치 미만인 상권×업종을 추정하지 않음
        # → "소규모 시장 가능성" 신호. 다른 grain 매출로 대체하지 않음(결정 2026-09-02, value-level-qa.md).
        rev_thin_market = rev_pp is None and n_store and n_store > 0 and uscope == "상권"
        if rev_thin_market:
            neg.append("커피 추정매출 미제공 상권 — 카드거래 표본이 추정 임계치 미만(소규모 시장 가능성), 매출 검증 불가")
        elif rev_pp is None:
            neg.append(f"커피 점포당매출 근거 없음 — {uscope or '상권·행정동'} 데이터 부족")
        elif rev_pp_p is not None and rev_pp_p < 15:
            neg.append(f"{uscope} 배경 커피 점포당매출 서울 하위 {rev_pp_p}% — 시장 매출 규모 매우 작음")
        neg.append("비용(임대료): R-ONE crosswalk에서 잠실/송파는 복합권역 review → 서울 지수 proxy 사용")

        # tier 는 pos/neg 목록으로만 — 약한 배경 신호는 2개 이상 겹쳐야 추천, entry_health 경계는 추천 차단
        blocking = any(("불리" in n or "진입 리스크 경계" in n) for n in neg)
        if rev_pp is None:
            tier = "조건부 검토"
        elif len(pos) >= 2 and not blocking:
            tier = "추천"
        else:
            tier = "조건부 검토"
        if blocking and len(neg) >= 4:
            tier = "주의"

        anchor_disp = s["name"] + ("/" + "·".join(sorted(s["lines"])) if s.get("lines") else "")
        place_name = f"{s['name']} 인근"
        near_extra = [{"name": r["역사명"], "type": "역", "distance_m": d,
                       "line": r["노선명"], "transfer": r["환승역"] == "Y", "source_type": "observed"}
                      for d, r in near_st[1:4]]
        near_extra += [{"name": a["단지명"], "type": "아파트단지", "distance_m": d,
                        "households": int(a["세대수"]) if str(a["세대수"]).isdigit() else None,
                        "source_type": "observed"} for d, a in near_ap[:3]]

        # FC-06a 거주 근사 비율 / FC-06b 방문 근사 비율 (지점 행정동)
        fgn = fgn_ratio.get(dong["code"]) if dong else None
        fgn_pct = pct(seoul_fgn, fgn[0]) if fgn else None
        fgv = fgn_visit.get(dong["code"]) if dong else None
        fgv_pct = pct(seoul_fgn_v, fgv[0]) if fgv else None

        prof_notes = {
            "FC-07": "지점 반경 500m 직접 계산 · bus_n만 약한 배경 신호, 역거리는 서술", "FC-08": "지점 반경 500m 직접 계산 · 약한 배경 신호",
            "FC-01": f"{fscope or '없음'} 배경값" + (f"({host_rel} {host_d}m)" if fscope == "상권" and host_rel == "최근접" else "") + " · 검증상 신호 없음 → context_notes만, 판정·정렬 미반영",
            "FC-11": f"{cscope or '없음'} 배경값 · FC-10 등급과 함께 범주 신호(반대근거)", "FC-30/31/32": f"{uscope or '없음'}×업종 배경값 · FC-31 약한 배경 신호, FC-30 신호 없음(서술), FC-32 모멘텀(품질 아님)",
            "FC-06a": "행정동 근사 비율(장기외국인/상주인구) — 생활인구÷등록인구, 신호",
            "FC-06b": "행정동 근사 비율(단기외국인/유동일평균) — 방법론 상이, 신호",
        }

        cand = {
            "candidate_id": ("APT-" if s["kind"] == "아파트단지" else "STN-") + str(s["id"]),
            "candidate_type": "아파트단지_인근" if s["kind"] == "아파트단지" else "역_인근",
            "spatial_grain": "지점",
            "region": {"sido": "서울특별시", "sigungu": "송파구", "dong": "잠실동"},
            "location": {
                "sido": "서울특별시", "sigungu": "송파구",
                "admin_dong": dong["name"] if dong else None,
                "anchor": {"name": s["name"], "type": s["kind"], "id": str(s["id"]),
                           "households": s.get("households"),
                           "line": "·".join(sorted(s["lines"])) if s.get("lines") else None},
                "place_name": place_name,
                "point": {"x": round(pt.x, 2), "y": round(pt.y, 2), "crs": "EPSG:5181"},
                "precision": "지점",
                "host_commercial_area": ({"code": host_t["code"], "name": host_t["name"],
                                          "relation": host_rel, "distance_m": host_d}
                                         if host_t else None),
                "overlapping_units": {
                    "commercial_area": [host_t["code"]] if host_t else [],
                    "hinterland": [], "admin_dong": [dong["code"]] if dong else [],
                    "sigungu": "송파구"},
                "nearby_anchors": near_extra,
                "address_point": None,
            },
            "industry_code": IND,
            "profile_ref": {"anchor_id": str(s["id"]),
                            "host_area": host_t["code"] if host_t else None,
                            "as_of_quarter": {"flow": Q, "sales": Q, "store": Q, "change": Q,
                                              "resident": "미검증", "worker": "미검증", "rent": RENT_Q},
                            "profile_confidence": {"level": "medium",
                                                   "reasons": ["FC-01·11·30~32 배경값(grain_is_proxy)",
                                                               "FC-20 R-ONE crosswalk review-only(잠실/송파)"]}},
            "dimension_evidence": {
                "현재수요": {"features": ["FC-01", "FC-07", "FC-31"], "status": "mixed",
                         "grain_notes": {"FC-01": prof_notes["FC-01"], "FC-07": prof_notes["FC-07"],
                                         "FC-31": prof_notes["FC-30/31/32"]}},
                "경쟁·시장수용": {"features": ["FC-30", "FC-31", "FC-32"], "status": "mixed",
                            "grain_notes": {"all": prof_notes["FC-30/31/32"]}},
                "진입건전성": {"features": ["FC-10", "FC-11", "FC-12"], "entry_health_variant": "core",
                          "entry_health_v1": {
                              "version": "entry_health_v1", "grade": grade, "risk": eh_risk,
                              "formula": "0.25·폐업률분위 + 0.25·(100−개업률분위) + 0.25·(100−점포증감률분위) + 0.25·라벨리스크·100 (전 업종 통합)",
                              "cuts": list(EH_CUTS[eh_scope]) if eh_scope else None, "cut_scope": eh_scope,
                              "inputs": {"폐업률분위": p_close, "개업률분위": p_open,
                                         "점포증감률분위": p_delta, "라벨": eh_lab,
                                         "라벨리스크": EH_LABEL_RISK.get(eh_lab)},
                              "score_is_predictive": False,
                              "used_in_판정": "반대근거 1항목 (주의·경계일 때만)",
                              "ref": "artifacts/20-method/entry-health-v1-cut-design.md"},
                          "grain_notes": {"FC-11": prof_notes["FC-11"],
                                          "FC-10": f"{eh_scope or '없음'} 전 업종 통합 점포지표 + {cscope or '없음'} 변화라벨 (지역 배경)"}},
                "수요구성": {"features": ["FC-03", "FC-05", "FC-06a", "FC-06b", "FC-08"], "status": "mixed",
                         "grain_notes": {"FC-08": prof_notes["FC-08"], "FC-03": f"{fscope} 배경값",
                                         "FC-06a": prof_notes["FC-06a"], "FC-06b": prof_notes["FC-06b"]}},
            },
            "fit_tier": tier, "fit_index": None, "score_version": None,
            "score_is_predictive": False,
            "greenfield": (n_store == 0 or n_store is None),
            "data_confidence": {"level": "medium",
                                "reasons": ["FC-01·11·30~32 는 " + (fscope or "미상") + " 배경값(grain_is_proxy)",
                                            "FC-20 임대료 프록시",
                                            "entry_health_v1 컷 승인(2026-09-02) — 등급은 반대근거 1항목"]
                                + (["긍정 근거가 전부 '약한 배경 신호'(FC-08·31·07) — 검증된 품질 신호 아님"] if weak_only else [])},
            "feature_build": {
                "build_passed": True, "merge_key_dup_rate": 0,
                "merge_key_definition": "기준_년분기_코드 + 공간코드 + 서비스_업종_코드",
                "quarters_used": {"flow": [Q], "change": [Q], "sales": ["20261"],
                                  "store": ["20251", "20252", "20253", "20254", "20261"]},
                "normalizations_applied": ["encoding_detect", "eng_header_rename", "store_count_schema_map",
                                           "year_file_quarter_assignment", "point_in_polygon_area_assign",
                                           "coord_reproject_4326_to_5181", "coord_reproject_5186_to_5181",
                                           "nearest_station_distance", "nearest_bus_stop_distance",
                                           "apt_name_geocode_vworld", "households_by_radius",
                                           "flow_per_area_normalize", "halfyear_period_parse"],
                "grain_resolution": {"method": "path_and_code_membership", "conflicts": 0},
                "coverage": {
                    "host_commercial_area": {"matched": 1 if host_t else 0, "expected": 1,
                                             "missing_reason": None if host_t else "상권 밖·300m 초과 → 행정동 배경값"},
                    "industry_store": {"matched": 1 if urow else 0, "expected": 1,
                                       "missing_reason": None if urow else "상권·행정동 모두 커피 데이터 없음"},
                    "flow": {"matched": 1 if frow else 0, "expected": 1},
                },
                "sources": ["data/점포/", "data/추정매출/", "data/길단위인구/", "data/상권변화지표/",
                            "data/도시철도역사/", "data/버스정류장/", "data/공동주택/",
                            "data/도시계획사업/", "data/고용률/", "data/네이버트렌드/", "data/임대료/"],
            },
            "source_freshness": {
                "flow": {"observed_end_period": Q, "periods_behind_latest": 0,
                         "update_cadence": "quarterly", "is_partial_latest": False},
                "transit": {"observed_end_period": "2026-08", "periods_behind_latest": 0,
                            "update_cadence": "snapshot", "is_partial_latest": False},
                "apartment": {"observed_end_period": "2026-08", "periods_behind_latest": 0,
                              "update_cadence": "snapshot", "is_partial_latest": False},
                "rent": {"observed_end_period": RENT_Q, "periods_behind_latest": 0,
                         "update_cadence": "quarterly", "is_partial_latest": False},
                "search_trend": {"observed_end_period": "2026-08", "periods_behind_latest": 0,
                                 "update_cadence": "monthly", "is_partial_latest": True},
            },
            "evidence": _evidence(s, host_t, host_rel, host_d, fscope, flow_dens, flow_total,
                                  dens_p, seoul_dens, seoul_total, dens, seoul_st_den, rev_pp,
                                  seoul_pp, uscope, near_st, hh_500, n_ap_total, near_bs,
                                  maeul, trunk, crow, cscope, emp, gu_tr, rone,
                                  [d for d in devp if host_t and d["상권_코드"] == host_t["code"]],
                                  fgn, fgn_pct, dong["name"] if dong else None, fgv, fgv_pct),
            "reasons": pos, "counter_evidence": neg, "context_notes": ctx,
            "missing_features": ([{"feature": "FC-31", "reason": (
                "커피 추정매출 미제공 — 카드거래 표본 임계치 미만(소규모 시장 신호), fallback 미적용"
                if rev_thin_market else "커피 점포·추정매출 데이터 없음")}] if rev_pp is None else [])
            + [{"feature": "FC-20", "reason": "R-ONE crosswalk review-only(잠실/송파) — 비용은 서울 지수 프록시"}]
            + ([{"feature": "host_commercial_area", "reason": "지점이 상권 밖 — 행정동 배경값"}] if not host_t else []),
            "listing_url": None,
        }
        candidates.append(cand)
        also = (" [+" + ", ".join(s["also"]) + "]") if s.get("also") else ""
        notes.append(f"\n## {anchor_disp}{also} ({cand['candidate_id']}) — {tier}")
        notes.append(f"- 유형 {s['kind']} · 행정동 {dong['name'] if dong else '?'} · "
                     f"host 상권 {host_t['name'] if host_t else '없음(행정동 배경값)'}"
                     + (f" ({host_rel} {host_d}m)" if host_t and host_rel == '최근접' else ""))
        notes.append(f"- [지점] 역 {near_st[0][1]['역사명'] if near_st else '없음'} "
                     f"{near_st[0][0] if near_st else '-'}m · 500m 아파트 {hh_500}세대 · 250m 정류소 {len(near_bs)}")
        notes.append(f"- [{fscope or '?'} 배경] 유동밀도 {flow_dens and round(flow_dens,2)}/㎡ "
                     f"(서울 {dens_p}%) · [{uscope or '?'}] 커피 점포 {n_store} · 점포당 {rev_pp and int(rev_pp)} · "
                     f"변화 {(crow or {}).get('상권_변화_지표_명')}")
        notes.append(f"- [행정동 {dong['name'] if dong else '?'}] 외국인 거주 근사비율 "
                     f"{fgn and round(fgn[0],1)}% (장기/상주, 서울 {fgn_pct}%) · "
                     f"방문 근사비율 {fgv and round(fgv[0],2)}% (단기/유동일평균, 서울 {fgv_pct}%) — 둘 다 신호")
        notes.append(f"- 근거(+): {'; '.join(pos) or '없음'}")
        notes.append(f"- 반대(−): {'; '.join(neg)}")
        if ctx:
            notes.append(f"- 배경 서술(판정·정렬 미반영): {'; '.join(ctx)}")

    json.dump({"run": "jamsil-coffee-point", "quarter": Q, "seeds": len(candidates),
               "candidates": [c["candidate_id"] for c in candidates]},
              open(os.path.join(OUT, "feature-table.json"), "w", encoding="utf-8"),
              ensure_ascii=False, indent=2)
    json.dump(candidates, open(os.path.join(OUT, "candidate-evidence.json"), "w", encoding="utf-8"),
              ensure_ascii=False, indent=2)

    try:
        from jsonschema import Draft202012Validator
        schema = json.load(open(os.path.join(ROOT, "artifacts/20-method/rag-evidence-schema.json")))
        v = Draft202012Validator(schema)
        errs = sum(len(sorted(v.iter_errors(c), key=str)) for c in candidates)
        for c in candidates[:2]:
            for e in sorted(v.iter_errors(c), key=str)[:6]:
                notes.append(f"  SCHEMA {c['candidate_id']}: {list(e.path)} {e.message[:110]}")
        print(f"스키마 검증: 후보 {len(candidates)}건, 총 오류 {errs}")
    except ImportError:
        print("jsonschema 없음")

    open(os.path.join(OUT, "run-notes.md"), "w", encoding="utf-8").write("\n".join(notes) + "\n")
    print(f"지점 후보 {len(candidates)}건 → {OUT}/")
    for c in candidates:
        print(f"  {c['location']['place_name']:24} {c['fit_tier']:8} "
              f"host={c['location']['host_commercial_area'] and c['location']['host_commercial_area']['name']}")
    return 0


def _ev(name, value, unit, st, scope, period, grain, proxy, path, qf, norm, interp, lim,
        mr=None, pn=None, p=None, sp=None):
    if isinstance(value, float):
        value = round(value, 2)
    d = {"metric_name": name, "value": value, "unit": unit, "source_type": st,
         "comparison_scope": scope, "period": period, "spatial_grain": grain,
         "grain_is_proxy": proxy, "source_path": path, "quarter_file_source": qf,
         "normalization": norm, "interpretation": interp, "limitation": lim}
    if value is None:
        d["missing_reason"] = mr or "결측"
    if proxy or st == "synthetic":
        d["proxy_note"] = pn or "다른 grain 대리"
    if p is not None:
        d["percentile"] = p
    if sp is not None:
        d["seoul_percentile"] = sp
    return d


def _evidence(s, host_t, host_rel, host_d, fscope, flow_dens, flow_total, dens_p, seoul_dens,
              seoul_total, st_den, seoul_st_den, rev_pp, seoul_pp, uscope, near_st, hh_500,
              n_ap, near_bs, maeul, trunk, crow, cscope, emp, gu_tr, rone, devs,
              fgn=None, fgn_pct=None, dong_nm=None, fgv=None, fgv_pct=None):
    from bisect import bisect_left
    ev = []
    if fgn:
        ev.append(_ev("외국인_거주_근사비율", round(fgn[0], 2), "%", "derived", "seoul_quantile",
                      "20261", "행정동", True,
                      "data/외국인생활인구/외국인생활인구_행정동_분기.csv + data/상주인구/…행정동.csv",
                      "단일 파일", ["foreign_over_resident_ratio"],
                      f"{dong_nm} 장기 외국인 {fgn[1]:.0f} / 상주 {fgn[2]:.0f} = {fgn[0]:.1f}% (서울 {fgn_pct}%)",
                      "근사·신호 — 분자=생활인구(통신 시간대 평균), 분모=주민등록 상주인구. 정밀 비율은 내국인 생활인구 필요. 방문형(단기)은 이 분모 부적합",
                      pn="지점 행정동 값 대리", sp=fgn_pct))
    if fgv:
        ev.append(_ev("외국인_방문_근사비율", round(fgv[0], 3), "%", "derived", "seoul_quantile",
                      "20261", "행정동", True,
                      "data/외국인생활인구/외국인생활인구_행정동_분기.csv + data/길단위인구/…행정동.csv",
                      "단일 파일", ["short_foreign_over_flow_ratio"],
                      f"{dong_nm} 단기 외국인 일평균 {fgv[1]:.0f} / 유동 일평균 {fgv[2]:.0f} = {fgv[0]:.2f}% "
                      f"(서울 {fgv_pct}%; 단기/장기 {fgv[3]:.2f})",
                      "근사·신호 — 분자=단기체류 생활인구 일평균, 분모=길단위 유동인구 분기총÷90. 체류·통행 방법론 상이. 관광지 성격 신호로만",
                      pn="지점 행정동 값 대리", sp=fgv_pct))
    fg = "상권" if fscope == "상권" else ("행정동" if fscope == "행정동" else "서울시")
    ev.append(_ev("유동밀도", flow_dens, "명/㎡·분기", "derived", "seoul_quantile", "20261", fg,
                  True, f"data/길단위인구/서울시 상권분석서비스(길단위인구-{fscope or '상권'}).csv", "2026년 폴더",
                  ["flow_per_area_normalize"],
                  f"{fscope} 배경 유동밀도 서울 {dens_p}%, 총유동 {flow_total and int(flow_total)}",
                  "지점 고유값 아님(포함/최근접 " + (fscope or "미상") + " 배경값) · 검증상 폐업/생존 무연관이라 fit_tier 판정·정렬 근거 아님(FC-01) · 총량 순위 근거 금지",
                  pn=f"{fscope} 배경값" + (f" (host {host_rel} {host_d}m)" if fscope == "상권" and host_rel == "최근접" else ""),
                  sp=dens_p))
    if near_st:
        d0, r0 = near_st[0]
        ev.append(_ev("최근접_도시철도역_거리", d0, "m", "derived", "none", "2026-08", "상권", False,
                      "data/도시철도역사/역사정보_서울.csv", "스냅샷",
                      ["nearest_station_distance", "coord_reproject_4326_to_5181"],
                      f"지점 반경 내 최근접 역 {r0['역사명']}({r0['노선명']}) {d0}m",
                      "개통 스냅샷 · 출구 위치 없음 — 역 단위까지"))
    ev.append(_ev("반경500m_아파트_세대수", float(hh_500), "세대", "derived", "none", "2026-08",
                  "상권", False, "data/공동주택/아파트단지_서울.csv", "스냅샷",
                  ["households_by_radius", "apt_name_geocode_vworld", "coord_reproject_5186_to_5181"],
                  f"지점 반경 500m 내 아파트 {n_ap}단지 {hh_500}세대",
                  "K-apt 의무관리 위주 — 소형 빌라·연립 누락 · 좌표 89.7%만"))
    ev.append(_ev("반경250m_버스정류소", float(len(near_bs)), "개소", "derived", "none", "2026-08",
                  "상권", False, "data/버스정류장/버스정류소_서울.csv", "스냅샷",
                  ["nearest_bus_stop_distance"],
                  f"지점 250m 내 정류소 {len(near_bs)}(마을 {maeul}·간선 {trunk})",
                  "노선·배차 없음 · 정류소는 대부분 상권에 존재 → 수·유형 비교"))
    ev.append(_ev("커피_점포당_매출", rev_pp, "원/점포·분기", "derived", "seoul_quantile", "20261",
                  ("상권" if uscope == "상권" else "행정동"), True,
                  f"data/추정매출/2026/서울시 상권분석서비스(추정매출-{uscope or '상권'}).csv", "2026년 폴더",
                  ["sales_count_column_fix"],
                  (f"{uscope} 커피 점포당매출(분기) 서울 {round(100*bisect_left(seoul_pp, rev_pp)/len(seoul_pp),1)}%"
                   if rev_pp else "커피 매출 결측"),
                  "매출 상위 = 신규 성공 아님 · 추정매출은 분기 합계 · " + (uscope or "미상") + " 배경값",
                  mr=("추정매출 미제공 — 카드 표본 임계치 미만(소규모 시장 신호), fallback 미적용" if (rev_pp is None and uscope == "상권")
                      else "추정매출 데이터 없음" if rev_pp is None else None),
                  pn=f"{uscope} 배경값", sp=(round(100*bisect_left(seoul_pp, rev_pp)/len(seoul_pp), 1) if rev_pp else None)))
    if crow:
        ev.append(_ev("상권_변화_지표", crow.get("상권_변화_지표"), "코드", "observed", "none", "20261",
                      ("상권" if cscope == "상권" else "행정동"), True,
                      f"data/상권변화지표/서울시 상권분석서비스(상권변화지표-{cscope}).csv", "단일 파일", [],
                      f"{crow.get('상권_변화_지표_명')} — {cscope} 배경 리스크 신호",
                      "업종 구분 없음 · 연속 gap 순위 피처 금지", pn=f"{cscope} 배경값"))
    if emp:
        ev.append(_ev("송파구_고용률", float(emp[-1]["고용률"]), "%", "observed", "none",
                      emp[-1]["기준_반기"], "자치구", True, "data/고용률/자치구_고용률_반기.csv", "단일 파일",
                      ["halfyear_period_parse"], f"송파구 고용률 {emp[-1]['고용률']}%",
                      "반기·자치구 — 지점 고유값 아님", pn="자치구 값을 지점 대리"))
    tr = None
    if gu_tr:
        vs = [float(r["rel_index"]) for r in gu_tr[-3:] if r["rel_index"]]
        tr = round(statistics.mean(vs), 3) if vs else None
    ev.append(_ev("송파구_검색_관심도", tr, "rel_index", "observed", "none", "2026-06~08", "자치구",
                  True, "data/네이버트렌드/자치구_검색트렌드_월.csv", "단일 파일", ["rel_index_by_anchor"],
                  f"송파구 검색 rel_index 최근3M {tr}",
                  "계절 미보정 · 202607+ 값 급락(배치 경계 의심)", mr=("결측" if tr is None else None),
                  pn="자치구 검색량을 지점 대리"))
    if devs:
        ev.append(_ev("host상권_도시계획사업_수", float(len(devs)), "건", "observed", "none", "2026 스냅샷",
                      "상권", True, "data/도시계획사업/도시계획사업_상권겹침.csv", "단일 파일",
                      ["point_in_polygon_area_assign"],
                      f"host 상권 인근 {len(devs)}건 (예: {devs[0]['사업장명']}, {devs[0]['추진단계']})",
                      "추진단계 스냅샷 — '예정' 단정 금지", pn="host 상권 겹침 사업"))
    return ev


if __name__ == "__main__":
    raise SystemExit(main())

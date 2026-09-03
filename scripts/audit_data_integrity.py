"""data/ 원천 CSV 정합성 검사.

입지 추천 하네스 T02(데이터 감사) 부분 재실행용. 파일명이 아니라 실제 헤더/값으로
인코딩·컬럼 스키마·grain·기간·키 중복·null·공간 커버리지·계단식 여부를 점검한다.

실행:  .venv/bin/python3 scripts/audit_data_integrity.py [--json out.json]
산출:  표준출력 리포트 (+ 선택적 JSON). 데이터는 절대 수정하지 않는다.
"""
from __future__ import annotations
import csv, glob, json, os, sys, collections

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(ROOT, "data")

# 서울 열린데이터광장 영문 원본 컬럼명 -> 프로젝트 한글 표준명
ENG2KOR = {
    "stdr_yyqu_cd": "기준_년분기_코드", "trdar_se_cd": "상권_구분_코드",
    "trdar_se_cd_nm": "상권_구분_코드_명", "trdar_cd": "상권_코드", "trdar_cd_nm": "상권_코드_명",
    "relm_cd": "상권배후지_코드", "relm_cd_nm": "상권배후지_코드_명",
    "adstrd_cd": "행정동_코드", "adstrd_cd_nm": "행정동_코드_명",
    "svc_induty_cd": "서비스_업종_코드", "svc_induty_cd_nm": "서비스_업종_코드_명",
    "stor_co": "점포_수", "similr_induty_stor_co": "유사_업종_점포_수",
    "opbiz_rt": "개업_율", "opbiz_stor_co": "개업_점포_수",
    "clsbiz_rt": "폐업_률", "clsbiz_stor_co": "폐업_점포_수", "frc_stor_co": "프랜차이즈_점포_수",
    "thsmon_selng_amt": "당월_매출_금액", "thsmon_selng_co": "당월_매출_건수",
}
TARGET_INDS = {f"CS10000{i}" for i in range(1, 10)} | {"CS100010"}


def norm(c: str) -> str:
    c = c.strip().strip('"')
    return ENG2KOR.get(c, c)


def detect_enc(path: str) -> str:
    raw = open(path, "rb").read()
    if raw[:3] == b"\xef\xbb\xbf":
        return "utf-8-sig"
    for enc in ("cp949", "utf-8"):
        try:
            raw.decode(enc)
            return enc
        except UnicodeDecodeError:
            continue
    return "latin-1"


def open_csv(path: str):
    enc = detect_enc(path)
    f = open(path, encoding=enc, newline="")
    rdr = csv.reader(f)
    raw_header = next(rdr)
    header = [norm(c) for c in raw_header]
    return f, rdr, header, {c: i for i, c in enumerate(header)}, enc, raw_header


def grain_of(idx: dict) -> tuple[str, str | None]:
    if "상권배후지_코드" in idx:
        return "배후지", "상권배후지_코드"
    if "상권_코드" in idx:
        return "상권", "상권_코드"
    if "행정동_코드" in idx:
        return "행정동", "행정동_코드"
    return "기타", None


def walk(sub: str) -> list[str]:
    out = []
    for root, _, files in os.walk(os.path.join(DATA, sub)):
        out += [os.path.join(root, fn) for fn in files if fn.lower().endswith(".csv")]
    return sorted(out)


def rel(p: str) -> str:
    return os.path.relpath(p, DATA)


def audit_file(path: str, want_industry=True) -> dict:
    f, rdr, header, idx, enc, raw = open_csv(path)
    grain, code_col = grain_of(idx)
    qi = idx.get("기준_년분기_코드")
    ci = idx.get(code_col) if code_col else None
    ii = idx.get("서비스_업종_코드")
    quarters = collections.Counter()
    keyc = collections.Counter()
    codes = set()
    inds = set()
    nkey_null = 0
    n = 0
    for r in rdr:
        n += 1
        q = r[qi].strip().strip('"') if qi is not None and qi < len(r) else ""
        code = r[ci].strip().strip('"') if ci is not None and ci < len(r) else ""
        ind = r[ii].strip().strip('"') if ii is not None and ii < len(r) else ""
        if q:
            quarters[q] += 1
        if code:
            codes.add(code)
        if ind:
            inds.add(ind)
        if (qi is not None and not q) or (ci is not None and not code):
            nkey_null += 1
        keyc[(q, code, ind)] += 1
    f.close()
    dup = sum(1 for v in keyc.values() if v > 1)
    header_lang = "eng" if any(c in raw for c in ("stdr_yyqu_cd", "trdar_cd", "thsmon_selng_amt")) else "kor"
    return dict(
        file=rel(path), enc=enc, header_lang=header_lang, cols=len(header),
        rows=n, grain=grain, quarters=sorted(quarters),
        n_codes=len(codes), codes=codes,
        n_target_inds=len(inds & TARGET_INDS) if want_industry else None,
        n_other_inds=len(inds - TARGET_INDS) if want_industry else None,
        key_dup_groups=dup, key_null_rows=nkey_null,
        colnames=header,
    )


def main() -> int:
    report = {}
    print("=" * 78)
    print("data/ 정합성 검사")
    print("=" * 78)

    # 1. 파일별 기본 감사
    single = {
        "영역": ["영역"], "길단위인구": ["길단위인구"], "상주인구": ["상주인구"],
        "직장인구": ["직장인구"], "상권변화지표": ["상권변화지표"],
        "점포": ["점포"], "추정매출": ["추정매출"],
    }
    schema_fp = collections.defaultdict(list)
    file_reports = []
    for ds, subs in single.items():
        for sub in subs:
            for p in walk(sub):
                fr = audit_file(p, want_industry=(ds in ("점포", "추정매출")))
                file_reports.append(fr)
                schema_fp[(ds, tuple(fr["colnames"]))].append(fr["file"])
                enc_flag = "" if fr["enc"] == "cp949" else f"  <ENC:{fr['enc']}>"
                lang_flag = "  <HEADER:ENG>" if fr["header_lang"] == "eng" else ""
                q = fr["quarters"]
                qspan = f"{q[0]}~{q[-1]}({len(q)}q)" if q else "-"
                ind = ""
                if fr["n_target_inds"] is not None:
                    ind = f"  업종 타겟{fr['n_target_inds']}/10+기타{fr['n_other_inds']}"
                dupf = f"  <DUP:{fr['key_dup_groups']}>" if fr["key_dup_groups"] else ""
                print(f"[{fr['grain']:3s}] {fr['rows']:>8,}행 {fr['cols']:>2}열  {qspan:16s}{ind}{enc_flag}{lang_flag}{dupf}")
                print(f"      {fr['file']}")
    report["files"] = [{k: v for k, v in fr.items() if k != "codes"} for fr in file_reports]

    # 2. 컬럼 스키마 그룹 (점포/추정매출)
    print("\n" + "=" * 78)
    print("컬럼 스키마 그룹")
    print("=" * 78)
    schemas = []
    for (ds, cols), files in sorted(schema_fp.items()):
        if ds not in ("점포", "추정매출"):
            continue
        print(f"\n{ds}  ({len(files)}개 파일, {len(cols)}열)")
        print(f"  {list(cols)}")
        for x in files:
            print(f"    - {x}")
        schemas.append(dict(dataset=ds, n_files=len(files), n_cols=len(cols),
                            columns=list(cols), files=files))
    report["schemas"] = schemas

    # 3. 연도 폴더 병합 시 분기 중복
    print("\n" + "=" * 78)
    print("연도 파일 병합 시 (분기 x 공간 x 업종) 키 중복")
    print("=" * 78)
    merged = []
    for ds in ("점포", "추정매출"):
        bg = collections.defaultdict(collections.Counter)
        bgq = collections.defaultdict(collections.Counter)
        for p in walk(ds):
            f, rdr, header, idx, enc, raw = open_csv(p)
            grain, cc = grain_of(idx)
            qi, ci, ii = idx.get("기준_년분기_코드"), idx.get(cc), idx.get("서비스_업종_코드")
            for r in rdr:
                if None in (qi, ci, ii) or max(qi, ci, ii) >= len(r):
                    continue
                k = (r[qi].strip(), r[ci].strip(), r[ii].strip())
                bg[grain][k] += 1
                bgq[grain][r[qi].strip()] += 1
            f.close()
        for grain, ctr in bg.items():
            dgroups = sum(1 for v in ctr.values() if v > 1)
            drows = sum(v for v in ctr.values() if v > 1)
            qd = dict(sorted(bgq[grain].items()))
            status = "OK" if dgroups == 0 else f"중복 {dgroups:,}키 / {drows:,}행"
            print(f"  {ds}-{grain:4s}: 고유키 {len(ctr):>8,}  {status}")
            print(f"    분기행수 {qd}")
            merged.append(dict(dataset=ds, grain=grain, unique_keys=len(ctr),
                               dup_groups=dgroups, dup_rows=drows, quarter_rows=qd))
    report["merged_dup"] = merged

    # 4. 계단식 여부 (상권 단위 총량 컬럼의 상권별 분기 distinct)
    print("\n" + "=" * 78)
    print("계단식 갱신 여부 (상권별 21분기 값의 distinct 개수 분포)")
    print("=" * 78)
    step = []
    STEP_FILES = {
        "상주인구/서울시 상권분석서비스(상주인구-상권).csv": ("총_상주인구_수", "tot_repop_co"),
        "직장인구/서울시 상권분석서비스(직장인구-상권).csv": ("총_직장_인구_수", "tot_wrc_popltn_co"),
        "길단위인구/서울시 상권분석서비스(길단위인구-상권).csv": ("총_유동인구_수", "tot_flpop_co"),
    }
    for relp, valnames in STEP_FILES.items():
        p = os.path.join(DATA, relp)
        f, rdr, header, idx, enc, raw = open_csv(p)
        vc = next((idx[v] for v in valnames if v in idx), None)
        scode = idx.get("상권_코드")
        by = collections.defaultdict(set)
        for r in rdr:
            if vc is None or scode is None or max(vc, scode) >= len(r):
                continue
            by[r[scode]].add(r[vc])
        f.close()
        dist = collections.Counter(len(s) for s in by.values())
        one = sum(v for k, v in dist.items() if k == 1)
        few = sum(v for k, v in dist.items() if 2 <= k <= 6)
        many = sum(v for k, v in dist.items() if k > 6)
        verdict = "계단식(step)" if many == 0 else "분기 시계열"
        print(f"  {relp}")
        print(f"    distinct 1={one}  2-6={few}  7+={many}  -> {verdict}")
        step.append(dict(file=relp, dist_1=one, dist_2_6=few, dist_7plus=many, verdict=verdict))
    report["stepwise"] = step

    # 5. 공간 코드 커버리지 (상권 단위, 영역 기준)
    print("\n" + "=" * 78)
    print("공간 코드 커버리지 (영역-상권 1,650 기준)")
    print("=" * 78)
    def code_set(relp, col):
        p = os.path.join(DATA, relp)
        f, rdr, header, idx, enc, raw = open_csv(p)
        i = idx.get(col)
        s = set()
        if i is not None:
            for r in rdr:
                if i < len(r) and r[i].strip():
                    s.add(r[i].strip())
        f.close()
        return s
    area = code_set("영역/상권/서울시 상권분석서비스(영역-상권).csv", "상권_코드")
    others = {
        "점포2026": ("점포/2026년/서울시 상권분석서비스(점포-상권).csv", "상권_코드"),
        "추정매출2026": ("추정매출/2026/서울시 상권분석서비스(추정매출-상권).csv", "상권_코드"),
        "길단위인구": ("길단위인구/서울시 상권분석서비스(길단위인구-상권).csv", "상권_코드"),
        "상주인구": ("상주인구/서울시 상권분석서비스(상주인구-상권).csv", "상권_코드"),
        "직장인구": ("직장인구/서울시 상권분석서비스(직장인구-상권).csv", "상권_코드"),
        "상권변화지표": ("상권변화지표/서울시 상권분석서비스(상권변화지표-상권).csv", "상권_코드"),
    }
    cov = [dict(name="영역-상권", n=len(area), matched=len(area), only_self=0, only_area=0)]
    print(f"  영역-상권: {len(area):,}")
    for name, (relp, col) in others.items():
        s = code_set(relp, col)
        print(f"  {name:14s}: {len(s):>5,}  영역∩ {len(s & area):>5,}  {name}에만 {len(s - area):>3}  영역에만 {len(area - s):>3}")
        cov.append(dict(name=name, n=len(s), matched=len(s & area),
                        only_self=len(s - area), only_area=len(area - s)))
    report["coverage"] = cov

    if len(sys.argv) >= 3 and sys.argv[1] == "--json":
        with open(sys.argv[2], "w", encoding="utf-8") as fh:
            json.dump(report, fh, ensure_ascii=False, indent=2, default=list)
        print(f"\nJSON: {sys.argv[2]}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

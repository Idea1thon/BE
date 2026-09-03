"""빅카인즈 뉴스 export를 추천 파이프라인용 메타데이터 JSONL로 정규화한다.

입력 XLSX는 빅카인즈에서 내려받은 검색 결과 export를 가정한다. 기사 본문은
저장하지 않고, 제목·발행일·언론사·URL·빅카인즈 분류/키워드와 파생된 지역·주제
태그만 저장한다. 추천에서 뉴스는 공식 도시계획사업의 대체물이 아니라, 지역별
시설·개발 보도량을 보여 주는 미래신호 보조 근거로만 사용한다.

예시:
  .venv/bin/python3 scripts/ingest_bigkinds_news.py \
    --input "$HOME/Downloads/서울시 랜드마크 시설 계획20240101-20260901.xlsx" \
    --query-label "서울시 랜드마크 시설 계획" \
    --start-date 2024-01-01 --end-date 2026-09-01

출력:
  data/뉴스/bigkinds_news_*.jsonl
  data/뉴스/bigkinds_news_*_manifest.json

검색어/기간을 export 내부에서 검증할 수 없는 경우가 있으므로 CLI 인자로 받은
값은 ``metadata_provenance=filename_or_cli_inferred``로 기록한다. 분석제외 표시,
뉴스 식별자·URL·일자/제목 기준 중복을 제거한 뒤 지역·주제 매칭을 계산한다.
"""
from __future__ import annotations

import argparse
import csv
import datetime as dt
import hashlib
import json
import re
import unicodedata
import zipfile
from collections import Counter
from pathlib import Path
from typing import Any
from xml.etree import ElementTree as ET


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUT_DIR = ROOT / "data" / "뉴스"
DEFAULT_QUERY_LABEL = "서울시 랜드마크 시설 계획"
DEFAULT_START_DATE = "2024-01-01"
DEFAULT_END_DATE = "2026-09-01"

TOPIC_RULES = (
    ("랜드마크", ("랜드마크",)),
    ("재개발", ("재개발",)),
    ("재건축", ("재건축",)),
    ("도시개발", ("도시개발",)),
    ("복합개발", ("복합개발",)),
    ("정비사업", ("정비사업", "정비구역", "신통기획")),
    ("MICE", ("MICE", "마이스")),
    ("시설·계획", ("시설", "계획")),
)


def clean(value: Any) -> str:
    text = unicodedata.normalize("NFKC", str(value or ""))
    return re.sub(r"\s+", " ", text).strip()


def _local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _column_number(cell_ref: str) -> int:
    letters = re.match(r"[A-Z]+", cell_ref.upper())
    if not letters:
        return 0
    number = 0
    for char in letters.group(0):
        number = number * 26 + ord(char) - ord("A") + 1
    return number - 1


def _shared_strings(zf: zipfile.ZipFile) -> list[str]:
    try:
        root = ET.fromstring(zf.read("xl/sharedStrings.xml"))
    except KeyError:
        return []
    strings: list[str] = []
    for si in root.iter():
        if _local_name(si.tag) != "si":
            continue
        strings.append(clean("".join(node.text or "" for node in si.iter() if _local_name(node.tag) == "t")))
    return strings


def _cell_value(cell: ET.Element, shared: list[str]) -> str:
    cell_type = cell.attrib.get("t", "")
    if cell_type == "inlineStr":
        return clean("".join(node.text or "" for node in cell.iter() if _local_name(node.tag) == "t"))
    value_node = next((node for node in cell if _local_name(node.tag) == "v"), None)
    raw = value_node.text if value_node is not None else ""
    if cell_type == "s":
        try:
            return shared[int(raw or 0)]
        except (IndexError, TypeError, ValueError):
            return ""
    return clean(raw)


def read_xlsx(path: Path) -> list[dict[str, str]]:
    """단일 sheet BigKinds XLSX를 외부 패키지 없이 읽는다."""
    with zipfile.ZipFile(path) as zf:
        sheet_names = sorted(
            name for name in zf.namelist()
            if name.startswith("xl/worksheets/sheet") and name.endswith(".xml")
        )
        if not sheet_names:
            raise ValueError(f"워크시트를 찾을 수 없음: {path}")
        shared = _shared_strings(zf)
        root = ET.fromstring(zf.read(sheet_names[0]))
        raw_rows: list[dict[int, str]] = []
        for row in root.iter():
            if _local_name(row.tag) != "row":
                continue
            cells: dict[int, str] = {}
            for cell in row:
                if _local_name(cell.tag) != "c":
                    continue
                cells[_column_number(cell.attrib.get("r", ""))] = _cell_value(cell, shared)
            raw_rows.append(cells)
    if not raw_rows:
        return []
    max_col = max(max(row.keys(), default=0) for row in raw_rows)
    headers = [clean(raw_rows[0].get(index, "")) for index in range(max_col + 1)]
    if "뉴스 식별자" not in headers or "제목" not in headers:
        raise ValueError(f"BigKinds header를 확인할 수 없음: {headers[:10]}")
    records: list[dict[str, str]] = []
    for cells in raw_rows[1:]:
        records.append({headers[index]: clean(cells.get(index, "")) for index in range(max_col + 1) if headers[index]})
    return records


def normalize_date(value: str) -> str | None:
    value = clean(value)
    if re.fullmatch(r"\d{8}", value):
        return f"{value[:4]}-{value[4:6]}-{value[6:]}"
    if re.fullmatch(r"\d{4}[-/.]\d{1,2}[-/.]\d{1,2}", value):
        parts = re.split(r"[-/.]", value)
        return f"{int(parts[0]):04d}-{int(parts[1]):02d}-{int(parts[2]):02d}"
    if re.fullmatch(r"\d+(?:\.\d+)?", value):
        try:
            serial = float(value)
            return (dt.date(1899, 12, 30) + dt.timedelta(days=int(serial))).isoformat()
        except (OverflowError, ValueError):
            return None
    return None


def _read_csv_column(path: Path, columns: tuple[str, ...]) -> set[str]:
    if not path.is_file():
        return set()
    for encoding in ("utf-8-sig", "cp949", "utf-8"):
        try:
            with path.open(encoding=encoding, newline="") as handle:
                rows = csv.DictReader(handle)
                values: set[str] = set()
                for row in rows:
                    for column in columns:
                        value = clean(row.get(column, ""))
                        if value:
                            values.add(value)
                return values
        except UnicodeDecodeError:
            continue
    return set()


def load_region_names() -> tuple[set[str], set[str]]:
    sigungu = _read_csv_column(ROOT / "data/네이버트렌드/자치구_검색트렌드_월.csv", ("자치구", "자치구명"))
    dongs = _read_csv_column(ROOT / "data/네이버트렌드/행정동_검색트렌드_월.csv", ("행정동명", "행정동"))
    return sigungu, dongs


def _dong_aliases(name: str) -> set[str]:
    name = clean(name)
    aliases = {name} if name else set()
    # 행정동 공식명(잠실2동)과 통용명(잠실동)을 함께 인식한다.
    if re.search(r"\d+동$", name):
        aliases.add(re.sub(r"\d+동$", "동", name))
    if name.endswith("본동"):
        aliases.add(name[:-2] + "동")
    return aliases


def _find_terms(text: str, names: set[str]) -> list[str]:
    return sorted({name for name in names if name and name in text}, key=lambda value: (-len(value), value))


_LOCATION_TOKEN_SPLIT = re.compile(r"[\s,;/·|~()\[\]]+")


def _match_tokens(text: str, names: set[str]) -> list[str]:
    """콤마·공백으로 구분된 지명 토큰과 정확히 일치하는 이름만 반환한다.

    빅카인즈 '위치' 필드는 부분문자열로 매칭하면 '능동적'→능동, '홍길동'→길동
    같은 오탐이 잦다. 토큰 경계를 맞춰 정확일치로 제한한다.
    """
    tokens = {token for token in _LOCATION_TOKEN_SPLIT.split(text) if token}
    return sorted({name for name in names if name and name in tokens}, key=lambda value: (-len(value), value))


def _topic_tags(text: str) -> list[str]:
    tags: list[str] = []
    for tag, terms in TOPIC_RULES:
        if tag == "시설·계획":
            if all(term in text for term in terms):
                tags.append(tag)
        elif any(term in text for term in terms):
            tags.append(tag)
    return tags


def _exclusion_reason(value: str) -> str | None:
    value = clean(value)
    if not value:
        return None
    reasons = []
    if "중복" in value:
        reasons.append("중복")
    if "예외" in value:
        reasons.append("예외")
    return ",".join(reasons) or value


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def normalize_records(raw_records: list[dict[str, str]], query_label: str,
                      start_date: str, end_date: str) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    sigungu_names, dong_names = load_region_names()
    eligible: list[dict[str, Any]] = []
    excluded = Counter()
    invalid = Counter()
    for raw in raw_records:
        reason = _exclusion_reason(raw.get("분석제외 여부", ""))
        if reason:
            excluded[reason] += 1
            continue
        news_id = clean(raw.get("뉴스 식별자", ""))
        title = clean(raw.get("제목", ""))
        date = normalize_date(raw.get("일자", ""))
        if not news_id:
            invalid["missing_news_id"] += 1
            continue
        if not title:
            invalid["missing_title"] += 1
            continue
        if not date:
            invalid["invalid_date"] += 1
            continue

        # 본문은 태그 계산에만 사용하고 출력에는 보존하지 않는다.
        body = clean(raw.get("본문", ""))
        # 주제·서울 scope 판정은 넓은 메타데이터로 하되, 지역(자치구·행정동) 매칭은
        # 좁힌다: 제목 부분문자열 + 위치 필드 토큰 정확일치. 키워드·특성추출·통합
        # 분류·기관은 짧은 행정동명 오탐(능동/길동/번동 등)이 많아 지역 매칭에서 뺀다.
        scope_text = " ".join(
            clean(raw.get(key, ""))
            for key in ("제목", "통합 분류1", "통합 분류2", "통합 분류3", "위치", "기관", "키워드", "특성추출(가중치순 상위 50개)")
        )
        topic_text = f"{scope_text} {body}"
        title_text = clean(raw.get("제목", ""))
        location_text = clean(raw.get("위치", ""))
        dong_alias_names = set().union(*(_dong_aliases(name) for name in dong_names)) if dong_names else set()
        sigungu_tags = sorted(
            set(_find_terms(title_text, sigungu_names)) | set(_match_tokens(location_text, sigungu_names)),
            key=lambda value: (-len(value), value),
        )
        dong_tags = sorted(
            set(_find_terms(title_text, dong_alias_names)) | set(_match_tokens(location_text, dong_alias_names)),
            key=lambda value: (-len(value), value),
        )
        topic_tags = _topic_tags(topic_text)
        eligible.append({
            "source": "bigkinds",
            "source_type": "observed_news_metadata",
            "news_id": news_id,
            "published_date": date,
            "publisher": clean(raw.get("언론사", "")),
            "contributor": clean(raw.get("기고자", "")),
            "title": title,
            "categories": [clean(value) for value in (raw.get("통합 분류1", ""), raw.get("통합 분류2", ""), raw.get("통합 분류3", "")) if clean(value)],
            "location_raw": clean(raw.get("위치", "")),
            "institution_raw": clean(raw.get("기관", "")),
            "keywords": clean(raw.get("키워드", "")),
            "feature_terms": clean(raw.get("특성추출(가중치순 상위 50개)", "")),
            "url": clean(raw.get("URL", "")),
            "seoul_scope": "서울" in scope_text,
            "sigungu_tags": sigungu_tags,
            "dong_tags": dong_tags,
            "topic_tags": topic_tags,
            "topic_match": bool(topic_tags),
            "query_label": query_label,
            "search_period": {"start": start_date, "end": end_date},
        })

    # BigKinds 식별자는 보통 고유하지만, URL/일자·제목·언론사 중복도 제거해
    # syndication으로 기사량이 부풀지 않게 한다.
    seen_ids: set[str] = set()
    seen_urls: set[str] = set()
    seen_fallback: set[tuple[str, str, str]] = set()
    deduped: list[dict[str, Any]] = []
    dedupe_counts = Counter()
    for record in sorted(eligible, key=lambda item: (item["published_date"], item["news_id"])):
        news_id = record["news_id"]
        url = record["url"]
        fallback = (record["published_date"], record["title"], record["publisher"])
        if news_id in seen_ids:
            dedupe_counts["news_id"] += 1
            continue
        if url and url in seen_urls:
            dedupe_counts["url"] += 1
            continue
        if not url and fallback in seen_fallback:
            dedupe_counts["date_title_publisher"] += 1
            continue
        seen_ids.add(news_id)
        if url:
            seen_urls.add(url)
        seen_fallback.add(fallback)
        deduped.append(record)

    manifest = {
        "source": "bigkinds",
        "source_type": "observed_news_metadata",
        "query_label": query_label,
        "query_metadata_verified": False,
        "query_metadata_provenance": "filename_or_cli_inferred",
        "search_period": {"start": start_date, "end": end_date},
        "body_retained": False,
        "region_matching": "지역 태그 = 제목 부분문자열 + 위치 필드 토큰 정확일치. 키워드·특성추출·분류·기관은 행정동명 오탐이 많아 지역 매칭에서 제외(주제·서울 scope 판정에만 사용). 위치는 빅카인즈 다중 지명 추출이라 기사 주제와 무관한 곁다리 행정동이 포함될 수 있음(제목 언급이 더 강한 신호)",
        "topic_matching": [tag for tag, _ in TOPIC_RULES],
        "exclusion_policy": "분석제외 여부가 중복/예외인 행 제외",
        "dedupe_policy": "뉴스 식별자 → URL → (일자, 제목, 언론사)",
        "raw_row_count": len(raw_records),
        "eligible_before_dedupe": len(eligible),
        "normalized_row_count": len(deduped),
        "excluded_counts": dict(excluded),
        "invalid_counts": dict(invalid),
        "dedupe_counts": dict(dedupe_counts),
        "topic_match_count": sum(record["topic_match"] for record in deduped),
        "seoul_scope_count": sum(record["seoul_scope"] for record in deduped),
        "sigungu_tagged_count": sum(bool(record["sigungu_tags"]) for record in deduped),
        "dong_tagged_count": sum(bool(record["dong_tags"]) for record in deduped),
        "topic_tag_counts": dict(Counter(tag for record in deduped for tag in record["topic_tags"])),
        "date_range_observed": {
            "min": min((record["published_date"] for record in deduped), default=None),
            "max": max((record["published_date"] for record in deduped), default=None),
        },
    }
    return deduped, manifest


def main() -> int:
    parser = argparse.ArgumentParser(description="빅카인즈 XLSX 뉴스 export 정규화")
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--out-dir", type=Path, default=DEFAULT_OUT_DIR)
    parser.add_argument("--query-label", default=DEFAULT_QUERY_LABEL)
    parser.add_argument("--start-date", default=DEFAULT_START_DATE)
    parser.add_argument("--end-date", default=DEFAULT_END_DATE)
    args = parser.parse_args()
    input_path = args.input.expanduser().resolve()
    if not input_path.is_file():
        raise SystemExit(f"입력 파일이 없음: {input_path}")
    try:
        dt.date.fromisoformat(args.start_date)
        dt.date.fromisoformat(args.end_date)
    except ValueError as exc:
        raise SystemExit(f"기간은 YYYY-MM-DD여야 함: {exc}")
    raw = read_xlsx(input_path)
    records, manifest = normalize_records(raw, args.query_label, args.start_date, args.end_date)
    token = re.sub(r"[^0-9A-Za-z가-힣]+", "_", args.query_label).strip("_") or "news"
    token = f"{token}_{args.start_date.replace('-', '')}_{args.end_date.replace('-', '')}"
    args.out_dir.mkdir(parents=True, exist_ok=True)
    jsonl_path = args.out_dir / f"bigkinds_news_{token}.jsonl"
    manifest_path = args.out_dir / f"bigkinds_news_{token}_manifest.json"
    with jsonl_path.open("w", encoding="utf-8", newline="\n") as handle:
        for record in records:
            handle.write(json.dumps(record, ensure_ascii=False, separators=(",", ":")) + "\n")
    manifest.update({
        "input_file_name": input_path.name,
        "input_path_at_ingest": str(input_path),
        "input_sha256": _sha256(input_path),
        "ingested_at_utc": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
        "output_jsonl": str(jsonl_path.relative_to(ROOT)),
        "manifest_path": str(manifest_path.relative_to(ROOT)),
    })
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"jsonl": str(jsonl_path), "manifest": str(manifest_path), **{k: manifest[k] for k in ("raw_row_count", "normalized_row_count", "topic_match_count", "excluded_counts", "dedupe_counts")}}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

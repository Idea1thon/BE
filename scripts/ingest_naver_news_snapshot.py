"""네이버 뉴스 검색 API를 현재 시점에 수동 1회 호출해 snapshot으로 저장한다.

자동화·예약·백그라운드 갱신을 하지 않는다. 이 스크립트를 사용자가 직접
실행한 시점의 검색 결과만 ``data/뉴스/naver_news_snapshot.jsonl``에 저장한다.
추천 파이프라인은 이 파일을 읽을 뿐 네이버 API를 호출하지 않는다.

기본 검색어는 현재 운영 범위로 고정한 한 가지다.

  서울시 재개발

예시:
  .venv/bin/python3 scripts/ingest_naver_news_snapshot.py
  .venv/bin/python3 scripts/ingest_naver_news_snapshot.py \
    --query "서울시 재개발" --display 100

Naver API HUB의 뉴스 검색은 쿼리당 최대 100건을 현재 sort=date 순으로
가져온다. 검색 결과 총량은 검색엔진의 전체 일치 건수이며, snapshot은 반환된
현재 상위 결과에 한정된다. 제목·발행일·링크·검색어와 지역/주제 파생 태그만
저장하고 description 원문은 보존하지 않는다.
"""
from __future__ import annotations

import argparse
import datetime as dt
import html
import json
import os
import re
import urllib.error
import urllib.parse
import urllib.request
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Any

from _budget import Budget
from _env import load_env, require
from ingest_bigkinds_news import _dong_aliases, _find_terms, _topic_tags, load_region_names


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUT_DIR = ROOT / "data" / "뉴스"
DEFAULT_QUERIES = ("서울시 재개발",)
ENDPOINT = "https://naverapihub.apigw.ntruss.com/search/v1/news"

load_env()
_BUDGET = Budget(
    "naver_news",
    monthly_limit=int(os.environ.get("NAVER_NEWS_MONTHLY_LIMIT", "24000")),
    daily_limit=int(os.environ.get("NAVER_NEWS_DAILY_LIMIT", "24000")),
    per_run_limit=int(os.environ.get("NAVER_NEWS_PER_RUN_LIMIT", "50")),
)


def strip_html(value: Any) -> str:
    text = html.unescape(str(value or ""))
    return re.sub(r"<[^>]+>", "", text).strip()


def published_date(value: str) -> str | None:
    try:
        return parsedate_to_datetime(value).date().isoformat()
    except (TypeError, ValueError, OverflowError):
        return None


def fetch(query: str, display: int, sort: str) -> dict[str, Any]:
    params = urllib.parse.urlencode({"query": query, "display": display, "start": 1, "sort": sort, "format": "json"})
    endpoint = f"{os.environ.get('NAVER_NEWS_ENDPOINT', ENDPOINT)}?{params}"
    headers = {
        "X-NCP-APIGW-API-KEY-ID": require("NAVER_CLIENT_ID"),
        "X-NCP-APIGW-API-KEY": require("NAVER_CLIENT_SECRET"),
        "Accept": "application/json",
    }
    _BUDGET.check()
    request = urllib.request.Request(endpoint, headers=headers, method="GET")
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", "replace")[:300]
        raise SystemExit(f"네이버 뉴스 API 호출 실패 {exc.code}: {detail}") from exc
    _BUDGET.commit()
    return payload


def normalize_results(results: list[tuple[str, dict[str, Any]]], retrieved_at: str,
                      sort: str = "date", query_metadata_verified: bool = True) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    sigungu_names, dong_names = load_region_names()
    # 빅카인즈 ingest와 동일한 alias 규칙(숫자동→동, 본동→동)을 쓴다.
    dong_alias_names = set().union(*(_dong_aliases(name) for name in dong_names)) if dong_names else set()
    records_by_url: dict[str, dict[str, Any]] = {}
    query_totals: dict[str, int] = {}
    raw_count = 0
    for query, payload in results:
        query_totals[query] = int(payload.get("total") or 0)
        for rank, item in enumerate(payload.get("items") or [], start=1):
            raw_count += 1
            title = strip_html(item.get("title"))
            description = strip_html(item.get("description"))
            url = str(item.get("originallink") or item.get("link") or "").strip()
            date = published_date(item.get("pubDate", ""))
            if not title or not url or not date:
                continue
            # description은 주제·서울 scope 판정에만 쓰고 저장하지 않는다.
            # 지역(자치구·행정동)은 제목만 매칭한다 — description 스니펫은 곁다리 지명 오탐이 많다.
            metadata_text = f"{title} {description}"
            topic_tags = _topic_tags(metadata_text)
            record = records_by_url.get(url)
            if record is None:
                record = {
                    "source": "naver_news_api",
                    "source_type": "observed_news_metadata",
                    "news_id": url,
                    "published_date": date,
                    "publisher": "",
                    "title": title,
                    "url": url,
                    "query_labels": [query],
                    "result_ranks": {query: rank},
                    "seoul_scope": "서울" in metadata_text,
                    "sigungu_tags": _find_terms(title, sigungu_names),
                    "dong_tags": _find_terms(title, dong_alias_names),
                    "topic_tags": topic_tags,
                    "topic_match": bool(topic_tags),
                    "retrieved_at_utc": retrieved_at,
                    "query_metadata_verified": query_metadata_verified,
                }
                records_by_url[url] = record
            else:
                if query not in record["query_labels"]:
                    record["query_labels"].append(query)
                record["result_ranks"][query] = rank
                record["topic_tags"] = sorted(set(record["topic_tags"]) | set(topic_tags))
                record["topic_match"] = bool(record["topic_tags"])

    records = sorted(records_by_url.values(), key=lambda item: (item["published_date"], item["news_id"]), reverse=True)
    for record in records:
        record["query_label"] = " | ".join(record.pop("query_labels"))
        record["result_rank"] = min(record.pop("result_ranks").values())
        record["search_period"] = {"type": "snapshot", "retrieved_at_utc": retrieved_at}
    manifest = {
        "source": "naver_news_api",
        "source_type": "observed_news_metadata",
        "endpoint": os.environ.get("NAVER_NEWS_ENDPOINT", ENDPOINT),
        "query_labels": [query for query, _ in results],
        "query_metadata_verified": query_metadata_verified,
        "query_metadata_provenance": "cli_or_default_queries",
        "sort": sort,
        "display_per_query": max((len(payload.get("items") or []) for _, payload in results), default=0),
        "query_totals": query_totals,
        "raw_result_count": raw_count,
        "normalized_row_count": len(records),
        "deduped_count": raw_count - len(records),
        "dedupe_policy": "originallink_or_link exact URL dedup across queries",
        "body_retained": False,
        "region_matching": "지역 태그(자치구·행정동)는 제목만 부분문자열 매칭. description은 주제·서울 scope 판정에만 사용하며 저장하지 않음",
        "topic_matching": ["랜드마크", "재개발", "재건축", "도시개발", "복합개발", "정비사업", "MICE", "시설·계획"],
        "retrieved_at_utc": retrieved_at,
        "automation": False,
        "result_limit_note": "네이버 API가 반환한 현재 상위 결과 snapshot이며 전체 검색 일치 건수의 전수 목록이 아님",
    }
    return records, manifest


def main() -> int:
    parser = argparse.ArgumentParser(description="네이버 뉴스 현재 시점 수동 snapshot 수집")
    parser.add_argument("--query", action="append", dest="queries", help="검색어(반복 가능; 생략 시 프로젝트 기본값 '서울시 재개발')")
    parser.add_argument("--display", type=int, default=100)
    parser.add_argument("--sort", choices=("date", "sim"), default="date")
    parser.add_argument("--out-dir", type=Path, default=DEFAULT_OUT_DIR)
    args = parser.parse_args()
    if not 1 <= args.display <= 100:
        raise SystemExit("--display는 1~100이어야 합니다.")
    queries = args.queries or list(DEFAULT_QUERIES)
    retrieved_at = dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")
    fetched = [(query, fetch(query, args.display, args.sort)) for query in queries]
    records, manifest = normalize_results(fetched, retrieved_at, sort=args.sort)
    args.out_dir.mkdir(parents=True, exist_ok=True)
    jsonl_path = args.out_dir / "naver_news_snapshot.jsonl"
    manifest_path = args.out_dir / "naver_news_snapshot_manifest.json"
    with jsonl_path.open("w", encoding="utf-8", newline="\n") as handle:
        for record in records:
            handle.write(json.dumps(record, ensure_ascii=False, separators=(",", ":")) + "\n")
    manifest.update({
        "output_jsonl": str(jsonl_path.relative_to(ROOT)),
        "manifest_path": str(manifest_path.relative_to(ROOT)),
    })
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"jsonl": str(jsonl_path), "manifest": str(manifest_path), "retrieved_at_utc": retrieved_at, "query_totals": manifest["query_totals"], "raw_result_count": manifest["raw_result_count"], "normalized_row_count": manifest["normalized_row_count"], "automation": False}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

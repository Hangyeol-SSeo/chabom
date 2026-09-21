"""실행 진입점 (스펙 7장). 결과를 랭킹 리포트(콘솔 테이블/JSON/CSV)로 출력한다.

**2026-09-17부터 crawl 명령은 Playwright(실브라우저)로 동작한다** — 최초 1회
`playwright install chromium`을 실행해 둬야 한다(README 참고). 웹앱에서 실시간 검색을 쓰려면
이 CLI 대신 `python server.py`(FastAPI 로컬 검색 서버)를 쓰는 게 더 편하다 — 이 CLI는 필터
파라미터를 매번 손으로 넘겨야 하는 배치/디버깅용이다.

사용 예:
  # 수동 입력 JSON으로 스코어링만 실행 (네트워크 접근 없음)
  python -m cli.main score --input tests/sample_listings.json

  # 보배드림 실크롤링 + 저장 + 스코어링 — 이제 가격/연식/주행거리/연료/변속기 필터가
  # 목록 조회 URL에 그대로 실려 서버단에서 걸러진 결과만 받아온다(crawler/adapters 모듈 docstring 참고)
  python -m cli.main crawl --source bobaedream --gubun K --min-price 15000000 --max-price 30000000 \
      --min-year 2020 --pages 2 --db data/listings.db

  # 케이카/KB차차차도 목록 수준 크롤링 가능(2026-09-17 재조사, crawler/compliance.py 참고).
  # 케이카는 서버단 필터를 지원하지 않아(어댑터 docstring 참고) 로컬에서 필터링한다.
  python -m cli.main crawl --source kcar --min-price 10000000 --max-price 20000000 --pages 3
  python -m cli.main crawl --source kbchachacha --min-year 2019 --pages 3

  # 엔카는 여전히 robots.txt+CSR 구조로 막혀 있어 즉시 오류로 안내한다.
  python -m cli.main crawl --source encar
"""
from __future__ import annotations

import argparse
import csv
import json
import sys
from pathlib import Path

from crawler.adapters.blocked_adapter import EncarAdapter
from crawler.adapters.bobaedream_adapter import BobaedreamAdapter
from crawler.adapters.kbchachacha_adapter import KbchachachaAdapter
from crawler.adapters.kcar_adapter import KcarAdapter
from crawler.base_adapter import SearchParams
from crawler.compliance import ComplianceBlockedError
from normalizer.schema import Listing
from scoring.export import evaluated_to_rows
from scoring.scorer import EvaluatedListing, evaluate_all, load_weights, rank_listings
from storage.db import get_connection, insert_snapshot

ADAPTERS = {
    "bobaedream": BobaedreamAdapter,
    "encar": EncarAdapter,
    "kcar": KcarAdapter,
    "kbchachacha": KbchachachaAdapter,
}


def _print_report(evaluated: list[EvaluatedListing], excluded: list[EvaluatedListing]) -> None:
    print(f"\n=== 랭킹 결과 ({len(evaluated)}건, 제외 {len(excluded)}건) ===\n")
    for rank, e in enumerate(evaluated, start=1):
        v = e.listing.vehicle
        value_text = f"{e.value.score:.1f}" if e.value.score is not None else "N/A"
        print(f"[{rank}] {v.make} {v.model} ({v.model_year}) — {(v.price_krw or 0):,}원")
        print(f"    리스크 {e.risk.score}/100 · 가성비 {value_text}/100 · {e.listing.url}")
        print(f"    근거: {e.explanation}")
        if e.data_gaps:
            for gap in e.data_gaps:
                print(f"    ⚠ {gap}")
        if e.listing.verification_links:
            print("    직접 확인:")
            for link in e.listing.verification_links:
                note = f" — {link.note}" if link.note else ""
                print(f"      · {link.label}: {link.url}{note}")
        print()

    if excluded:
        print(f"=== 제외된 매물 ({len(excluded)}건) ===\n")
        for e in excluded:
            v = e.listing.vehicle
            print(f"- {v.make} {v.model} ({v.model_year}): {e.explanation}")
        print()


def _write_json(
    evaluated: list[EvaluatedListing],
    path: Path,
    batch_meta: dict | None = None,
    append: bool = False,
) -> None:
    rows = evaluated_to_rows(evaluated, batch_meta=batch_meta)

    if append and path.exists():
        existing = json.loads(path.read_text(encoding="utf-8"))
        by_key = {(r["listing"]["source"], r["listing"]["listing_id"]): r for r in existing}
        for row in rows:
            key = (row["listing"]["source"], row["listing"]["listing_id"])
            by_key[key] = row  # 같은 매물 재수집 시 최신 값으로 덮어씀
        rows = list(by_key.values())

    path.write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")


def _write_csv(evaluated: list[EvaluatedListing], path: Path) -> None:
    with open(path, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.writer(f)
        writer.writerow(["listing_id", "make", "model", "model_year", "price_krw", "risk_score", "value_score", "excluded", "explanation"])
        for e in evaluated:
            v = e.listing.vehicle
            writer.writerow([
                e.listing.listing_id, v.make, v.model, v.model_year, v.price_krw,
                e.risk.score, e.value.score, e.excluded, e.explanation,
            ])


def _apply_user_filters(listings: list[Listing], args: argparse.Namespace) -> list[Listing]:
    result = []
    for l in listings:
        v = l.vehicle
        if args.min_price and (v.price_krw is None or v.price_krw < args.min_price):
            continue
        if args.max_price and (v.price_krw is None or v.price_krw > args.max_price):
            continue
        if args.body_type and v.body_type and v.body_type not in args.body_type:
            continue
        if args.transmission and v.transmission and v.transmission != args.transmission:
            continue
        if args.fuel_type and v.fuel_type and v.fuel_type != args.fuel_type:
            continue
        result.append(l)
    return result


def _run_score(args: argparse.Namespace) -> None:
    raw = json.loads(Path(args.input).read_text(encoding="utf-8"))
    listings = [Listing.from_dict(item) for item in raw]
    listings = _apply_user_filters(listings, args)

    weights = load_weights(Path(args.weights) if args.weights else None)
    evaluated = evaluate_all(
        listings, weights, advanced_mode=args.advanced_mode, region_risk_enabled=not args.no_region_risk
    )
    excluded = [e for e in evaluated if e.excluded]
    ranked = rank_listings(evaluated, weights, mode=args.sort)

    _print_report(ranked, excluded)

    if args.json_out:
        _write_json(ranked, Path(args.json_out), append=args.append)
        print(f"JSON 저장: {args.json_out}")
    if args.csv_out:
        _write_csv(ranked, Path(args.csv_out))
        print(f"CSV 저장: {args.csv_out}")


def _run_crawl(args: argparse.Namespace) -> None:
    adapter_cls = ADAPTERS.get(args.source)
    if adapter_cls is None:
        print(f"알 수 없는 소스: {args.source} (지원: {', '.join(ADAPTERS)})", file=sys.stderr)
        sys.exit(1)

    adapter = adapter_cls()
    params = SearchParams(
        min_price_krw=args.min_price,
        max_price_krw=args.max_price,
        transmission=args.transmission,
        fuel_type=args.fuel_type,
        year_min=args.min_year,
        year_max=args.max_year,
        mileage_min_km=args.min_mileage,
        mileage_max_km=args.max_mileage,
        max_pages=args.pages,
        extra={"gubun": args.gubun} if args.gubun else {},
    )

    try:
        listings = list(adapter.fetch_normalized_listings(params))
    except ComplianceBlockedError as exc:
        print(f"\n[크롤링 차단] {exc}\n", file=sys.stderr)
        sys.exit(2)
    finally:
        fetcher = getattr(adapter, "fetcher", None)
        if fetcher is not None:
            fetcher.close()

    print(f"{len(listings)}건 수집 완료 ({args.source})")

    weights = load_weights(Path(args.weights) if args.weights else None)
    evaluated = evaluate_all(listings, weights, advanced_mode=args.advanced_mode)
    excluded = [e for e in evaluated if e.excluded]
    ranked = rank_listings(evaluated, weights, mode=args.sort)
    _print_report(ranked, excluded)

    if args.db:
        conn = get_connection(args.db)
        for e in evaluated:
            insert_snapshot(conn, e.listing, risk_score=e.risk.score, value_score=e.value.score)
        conn.close()
        print(f"DB 저장: {args.db}")

    if args.json_out:
        batch_meta = {"gubun": args.gubun} if args.gubun else None
        _write_json(ranked, Path(args.json_out), batch_meta=batch_meta, append=args.append)
        print(f"JSON 저장: {args.json_out}")
    if args.csv_out:
        _write_csv(ranked, Path(args.csv_out))
        print(f"CSV 저장: {args.csv_out}")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="차봄 — 중고차 매물 검증 및 선별")
    sub = parser.add_subparsers(dest="command", required=True)

    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--min-price", type=int, default=None, help="최소 가격(원)")
    common.add_argument("--max-price", type=int, default=None, help="최대 가격(원)")
    common.add_argument("--transmission", choices=["manual", "automatic"], default=None)
    common.add_argument("--fuel-type", default=None, help="gasoline|diesel|lpg|hybrid|bifuel|ev")
    common.add_argument("--min-year", type=int, default=None, help="연식 최소(예: 2020) — crawl에서만 서버단 필터로 적용됨")
    common.add_argument("--max-year", type=int, default=None, help="연식 최대")
    common.add_argument("--min-mileage", type=int, default=None, help="주행거리 최소(km)")
    common.add_argument("--max-mileage", type=int, default=None, help="주행거리 최대(km)")
    common.add_argument("--advanced-mode", action="store_true", help="하드필터를 경고만 하고 배제하지 않음")
    common.add_argument("--sort", choices=["risk", "value"], default="risk", help="정렬 기준(리스크 우선/가성비 우선)")
    common.add_argument("--weights", default=None, help="가중치 yaml 경로(기본: scoring/weights.yaml)")
    common.add_argument("--json-out", default=None)
    common.add_argument("--csv-out", default=None)
    common.add_argument(
        "--append", action="store_true",
        help="--json-out 파일이 있으면 이어붙임(listing_id+source 기준 중복 제거) — 여러 소스/구분을 하나로 합칠 때",
    )

    score_parser = sub.add_parser("score", parents=[common], help="수동 입력 JSON으로 스코어링만 실행")
    score_parser.add_argument("--input", required=True, help="정규화 스키마(2장) 형식의 매물 JSON 배열 파일")
    score_parser.add_argument("--body-type", action="append", default=None)
    score_parser.add_argument("--no-region-risk", action="store_true", help="지역 리스크 필터 끄기")
    score_parser.set_defaults(func=_run_score)

    crawl_parser = sub.add_parser("crawl", parents=[common], help="실제 크롤링 후 스코어링")
    crawl_parser.add_argument("--source", required=True, choices=list(ADAPTERS.keys()))
    crawl_parser.add_argument("--gubun", default=None, help="보배드림 전용: K=국산차, I=수입차")
    crawl_parser.add_argument("--pages", type=int, default=2)
    crawl_parser.add_argument("--db", default=None, help="sqlite 저장 경로(재크롤링 시 가격 변동 추적)")
    crawl_parser.set_defaults(func=_run_crawl)

    return parser


def main(argv: list[str] | None = None) -> None:
    parser = build_parser()
    args = parser.parse_args(argv)
    args.func(args)


if __name__ == "__main__":
    main()

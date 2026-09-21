"""케이카(kcar.com) 어댑터 — 목록 수준만, 상세페이지는 절대 크롤링하지 않는다.

robots.txt 확인 결과(crawler/compliance.py): `www.kcar.com/robots.txt`는 매물 상세 경로
(`/car/info/`, `/bc/detail/carInfoDtl?`)를 명시적으로 금지한다 — 이 어댑터는 그 경로를 절대
요청하지 않는다.

실제 매물 데이터는 www.kcar.com이 아니라 별도 호스트 `api.kcar.com`의 내부 API
(`/bc/stockCar/list`)에서 온다. 이 호스트엔 robots.txt 파일 자체가 없다(404 확인됨). 이 API는
페이지네이션 파라미터와 무관하게 `data.allStockCarList`에 전체 재고(약 470여 대)를 한 번에
반환한다는 것을 실측으로 확인했다 — 그래서 이 어댑터는 목록 페이지를 여러 번 순회하지 않고
**단 한 번의 요청**으로 전체 목록을 가져온 뒤 `SearchParams.max_pages`를 "가상 페이지 크기"로
슬라이싱해 다른 어댑터와 CLI 옵션 의미를 맞춘다.

**중요한 법적 유의사항**: api.kcar.com은 케이카 프론트엔드가 자체적으로 쓰는 비공개 내부 API를
개발자도구 네트워크 탭으로 역추적해 찾은 것이며, 공식 문서화된 공개 API가 아니다. robots.txt가
없다는 사실은 "명시적으로 금지되지 않았다"는 것이지 "명시적으로 허용됐다"는 뜻은 아니다 — 이
해석의 모호함을 사용자에게 미리 알렸고, 사용자가 이를 인지한 상태로 진행을 승인했다.

**데이터 범위**: 이 API는 목록 카드 수준 필드(모델명·가격·연식·주행거리·연료·사진·매장명)만
제공한다. `insurance_history`/`performance_record`는 채우지 않으며, `verification_links`에
(추정) 상세페이지 링크와 카히스토리 링크만 안내한다 — 상세페이지 URL 패턴은 실측으로 클릭까지
확인한 게 아니라 robots.txt의 disallow 경로(`/bc/detail/carInfoDtl?carCd=`)와 API의 `carCd` 필드
명을 근거로 추정한 것이므로, verification_link의 note에 그 사실을 명시한다.

**2026-09-17 확인**: 이 API는 필터 파라미터를 줘도 응답이 바뀌지 않는다 — 케이카 웹사이트 자체가
목록을 한 번에 다 받아온 뒤 브라우저에서 자체적으로(클라이언트 사이드) 필터링하는 구조임을 실측으로
확인했다(가격/연식 등을 실제 사이트에서 걸어도 `api.kcar.com` 요청이 새로 발생하지 않음). 그래서 이
어댑터도 동일하게 한 번만 전량을 받아온 뒤 `_passes_filters`로 즉시 필터링한다 — 다른 두 어댑터처럼
서버단 필터 파라미터를 만들 수 없다(그럴 필요도 없다, 어차피 한 번의 요청으로 끝나므로). 전송 계층은
Playwright 브라우저 컨텍스트의 요청 API로 바꿨다(사용자 요청, crawler/browser_fetch.py 참고).
"""
from __future__ import annotations

import logging
import re
from typing import Iterator, Optional

from crawler.base_adapter import BaseAdapter, SearchParams
from crawler.browser_fetch import BrowserFetcher
from crawler.rate_limiter import RateLimiter
from normalizer.schema import Dealer, Listing, Photos, Vehicle, VerificationLink

logger = logging.getLogger(__name__)

API_URL = "https://api.kcar.com/bc/stockCar/list"
# robots.txt가 명시적으로 금지한 상세페이지 경로 패턴(www.kcar.com/robots.txt: "Disallow: /bc/detail/carInfoDtl?")
# 을 근거로 추정한 URL이며, 이 어댑터는 이 URL을 절대 요청하지 않는다 — 링크로만 안내한다.
INFERRED_DETAIL_URL_TEMPLATE = "https://www.kcar.com/bc/detail/carInfoDtl?carCd={car_cd}"
CARHISTORY_URL = "https://www.carhistory.or.kr/main.car"

_FUEL_MAP = {
    "가솔린": "gasoline",
    "휘발유": "gasoline",
    "디젤": "diesel",
    "경유": "diesel",
    "LPG": "lpg",
    "하이브리드": "hybrid",
    "전기": "ev",
    "수소": "ev",
}

PSEUDO_PAGE_SIZE = 20  # SearchParams.max_pages를 다른 어댑터와 같은 의미로 쓰기 위한 슬라이싱 단위


class KcarAdapter(BaseAdapter):
    source = "kcar"

    def __init__(
        self,
        fetcher: Optional[BrowserFetcher] = None,
        rate_limiter: Optional[RateLimiter] = None,
        request_timeout_sec: float = 20.0,
    ):
        self.fetcher = fetcher or BrowserFetcher()
        self.rate_limiter = rate_limiter or RateLimiter(min_delay_sec=1.0, max_delay_sec=3.0)
        self.request_timeout_sec = request_timeout_sec
        self._item_cache: dict[str, dict] = {}

    def _fetch_all_items(self) -> list[dict]:
        self.rate_limiter.wait()
        data = self.fetcher.get_json(
            API_URL,
            params={"currentPage": 1, "pageSize": 1, "creatYn": "Y", "sIndex": 1, "eIndex": 10},
            timeout_sec=self.request_timeout_sec,
        )
        if data is None:
            logger.warning("케이카 API 요청 실패: %s", API_URL)
            return []
        try:
            return data["data"]["allStockCarList"] or []
        except (KeyError, TypeError) as exc:
            logger.warning("케이카 API 응답 구조가 예상과 다릅니다: %s", exc)
            return []

    def fetch_listing_urls(self, params: SearchParams) -> Iterator[str]:
        # 전량을 받아온 뒤(위 docstring 참고) 필터부터 적용하고 나서 개수를 제한해야 한다 —
        # 반대로 하면(먼저 max_pages만큼 자르고 필터링) 목록 앞쪽에 조건에 맞는 매물이 없는
        # 경우 실제로는 매물이 있어도 0건으로 보일 수 있다.
        items = self._fetch_all_items()
        limit = params.max_pages * PSEUDO_PAGE_SIZE
        matched = 0
        for item in items:
            car_cd = item.get("carCd")
            if not car_cd:
                continue
            detail_url = INFERRED_DETAIL_URL_TEMPLATE.format(car_cd=car_cd)
            listing = self._normalize(item, detail_url)
            if not self._passes_filters(listing, params):
                continue
            self._item_cache[car_cd] = item
            matched += 1
            if matched > limit:
                break
            yield detail_url

    def parse_detail(self, url: str) -> Listing:
        m = re.search(r"carCd=([\w]+)", url)
        car_cd = m.group(1) if m else url
        item = self._item_cache.get(car_cd)
        if item is None:
            raise RuntimeError(f"목록 조회 시 캐시되지 않은 매물입니다: {car_cd}")
        return self._normalize(item, url)

    def fetch_normalized_listings(self, params: SearchParams):
        for url in self.fetch_listing_urls(params):
            listing = self.parse_detail(url)
            if self._passes_filters(listing, params):
                yield listing

    def _passes_filters(self, listing: Listing, params: SearchParams) -> bool:
        v = listing.vehicle
        price = v.price_krw
        if params.min_price_krw is not None and (price is None or price < params.min_price_krw):
            return False
        if params.max_price_krw is not None and (price is None or price > params.max_price_krw):
            return False
        if params.fuel_type and v.fuel_type and v.fuel_type != params.fuel_type:
            return False
        if params.year_min is not None and (v.model_year is None or v.model_year < params.year_min):
            return False
        if params.year_max is not None and (v.model_year is None or v.model_year > params.year_max):
            return False
        if params.mileage_min_km is not None and (v.mileage_km is None or v.mileage_km < params.mileage_min_km):
            return False
        if params.mileage_max_km is not None and (v.mileage_km is None or v.mileage_km > params.mileage_max_km):
            return False
        return True

    def _normalize(self, item: dict, detail_url: str) -> Listing:
        car_cd = item.get("carCd", "")

        price_text = item.get("dcPrc") or item.get("prc")
        price_krw = int(price_text) * 10_000 if price_text and str(price_text).isdigit() else None

        model_year = None
        year_text = item.get("prdcnYr")
        if year_text and str(year_text).isdigit():
            model_year = int(year_text)

        first_registration_date = None
        mfg_dt = item.get("mfgDt")  # "202212" (YYYYMM)
        if mfg_dt and len(str(mfg_dt)) == 6:
            first_registration_date = f"{mfg_dt[:4]}-{mfg_dt[4:6]}-01"

        mileage_km = None
        milg = item.get("milg")
        if milg and str(milg).isdigit():
            mileage_km = int(milg)

        fuel_raw = item.get("fuelNm", "") or ""
        fuel_type = _FUEL_MAP.get(fuel_raw, fuel_raw)

        trim_parts = [p for p in (item.get("grdNm"), item.get("grdDtlNm")) if p]
        trim = " ".join(trim_parts)

        photos_urls = [item["lsizeImgPath"]] if item.get("lsizeImgPath") else []

        vehicle = Vehicle(
            make=item.get("mnuftrNm", "") or "",
            model=item.get("modelNm", "") or "",
            trim=trim,
            model_year=model_year,
            first_registration_date=first_registration_date,
            mileage_km=mileage_km,
            transmission="",  # API의 trnsmsnCd가 항상 동일한 값만 관측되어 신뢰할 수 없음 — 비워둠
            fuel_type=fuel_type,
            body_type=item.get("carctgrNm", "") or "",  # 경차/소형차/준중형차/중형차/대형차/SUV/RV/화물차/스포츠카
            price_krw=price_krw,
            new_car_price_krw=None,
            region="",  # cntrNm은 매장명이라 지역명으로 신뢰할 수 없음 — dealer 쪽에만 부기
        )

        dealer = Dealer(
            dealer_id=item.get("cntrNm", "") or "",  # 케이카는 직영점 운영이라 매장명을 식별자로 사용
            join_year=None,
            total_sales_count=None,
            active_listings_count=None,
            region=item.get("cntrNm", "") or "",
        )

        verification_links = [
            VerificationLink(
                label="매물 상세페이지(케이카, 추정 링크)",
                url=detail_url,
                note=(
                    "이 URL은 robots.txt의 차단 경로 패턴과 API의 carCd 필드명을 근거로 추정한 것이며 "
                    "크롤러가 직접 클릭해 확인하지 않았습니다 — 정확하지 않을 수 있습니다. 접속이 안 되면 "
                    "케이카 홈페이지(kcar.com)에서 차량코드로 직접 검색하세요."
                ),
            ),
            VerificationLink(
                label="카히스토리 공식 조회(보험개발원)",
                url=CARHISTORY_URL,
                note="차량번호를 직접 입력해 조회(유료, 본인/차량 인증 필요) — 목록 API에 차량번호가 없어 자동 기재 불가",
            ),
        ]

        return Listing(
            listing_id=car_cd,
            source=self.source,
            url=detail_url,
            vehicle=vehicle,
            dealer=dealer,
            photos=Photos(urls=photos_urls, vision_analysis=None),
            verification_links=verification_links,
        )

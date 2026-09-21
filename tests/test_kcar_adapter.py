"""오프라인 파서 회귀 테스트. 모든 매물·판매자 값은 합성 데이터입니다.

사이트 구조에 필요한 필드명만 재현하며 실제 조회 데이터는 포함하지 않습니다.
"""
from __future__ import annotations

from crawler.adapters.kcar_adapter import CARHISTORY_URL, KcarAdapter

SAMPLE_ITEM = {
    "carCd": "EC00000001",
    "modelNm": "더 뉴 기아 레이",
    "grdNm": "시그니처",
    "grdDtlNm": None,
    "mnuftrNm": "기아",
    "prc": "1600",
    "dcPrc": "1500",
    "mfgDt": "202212",
    "prdcnYr": "2023",
    "milg": "40000",
    "fuelNm": "가솔린",
    "carctgrNm": "경차",
    "cntrNm": "테스트직영점",
    "lsizeImgPath": "https://images.example.com/3dcarpicture/2026/08/142/00000001_1/main/main780.jpg",
}


def test_normalize_maps_core_fields():
    adapter = KcarAdapter.__new__(KcarAdapter)
    listing = adapter._normalize(SAMPLE_ITEM, "https://kcar.example.com/bc/detail/carInfoDtl?carCd=EC00000001")

    assert listing.listing_id == "EC00000001"
    assert listing.vehicle.make == "기아"
    assert listing.vehicle.model == "더 뉴 기아 레이"
    assert listing.vehicle.trim == "시그니처"
    assert listing.vehicle.body_type == "경차"
    assert listing.vehicle.price_krw == 15_000_000  # dcPrc(할인가) 우선 사용
    assert listing.vehicle.model_year == 2023
    assert listing.vehicle.first_registration_date == "2022-12-01"
    assert listing.vehicle.mileage_km == 40_000
    assert listing.vehicle.fuel_type == "gasoline"
    assert listing.dealer.dealer_id == "테스트직영점"
    assert listing.photos.urls == [SAMPLE_ITEM["lsizeImgPath"]]


def test_normalize_falls_back_to_list_price_when_no_discount():
    item = dict(SAMPLE_ITEM, dcPrc=None)
    adapter = KcarAdapter.__new__(KcarAdapter)
    listing = adapter._normalize(item, "https://kcar.example.com/bc/detail/carInfoDtl?carCd=EC00000001")
    assert listing.vehicle.price_krw == 16_000_000


def test_normalize_leaves_insurance_and_performance_empty():
    adapter = KcarAdapter.__new__(KcarAdapter)
    listing = adapter._normalize(SAMPLE_ITEM, "https://kcar.example.com/bc/detail/carInfoDtl?carCd=EC00000001")
    assert listing.insurance_history.history_disclosed is None
    assert listing.performance_record.frame_damage == []


def test_normalize_verification_links_include_carhistory_without_plate():
    adapter = KcarAdapter.__new__(KcarAdapter)
    listing = adapter._normalize(SAMPLE_ITEM, "https://kcar.example.com/bc/detail/carInfoDtl?carCd=EC00000001")
    labels = [l.label for l in listing.verification_links]
    assert any("추정 링크" in l for l in labels)
    carhistory = next(l for l in listing.verification_links if l.label == "카히스토리 공식 조회(보험개발원)")
    assert carhistory.url == CARHISTORY_URL


def test_passes_filters_respects_price_range():
    adapter = KcarAdapter.__new__(KcarAdapter)
    listing = adapter._normalize(SAMPLE_ITEM, "https://kcar.example.com/bc/detail/carInfoDtl?carCd=EC00000001")

    from crawler.base_adapter import SearchParams

    assert adapter._passes_filters(listing, SearchParams(min_price_krw=10_000_000, max_price_krw=20_000_000))
    assert not adapter._passes_filters(listing, SearchParams(min_price_krw=20_000_000))

"""오프라인 파서 회귀 테스트. 모든 매물·판매자 값은 합성 데이터입니다.

사이트 구조에 필요한 필드명만 재현하며 실제 조회 데이터는 포함하지 않습니다.
"""
from __future__ import annotations

from bs4 import BeautifulSoup

from crawler.adapters.encar_detail_adapter import CARHISTORY_URL, EncarDetailAdapter

STATE = {
    "category": {
        "manufacturerName": "기아",
        "modelName": "올 뉴 모닝",
        "gradeName": "밴",
        "gradeDetailName": "기본형",
        "formYear": "2013",
        "yearMonth": "201306",
        "originPrice": 900,
    },
    "advertisement": {
        "price": 300,
        "oneLineText": "합성 테스트용 판매 설명입니다.",
    },
    "contact": {
        "userId": "test-dealer-encar",
        "no": "050-0000-0000",
        "address": "테스트시 가상구 예시로 0",
    },
    "spec": {
        "mileage": 42000,
        "transmissionName": "수동",
        "fuelName": "가솔린",
        "bodyName": "경차",
    },
    "photos": [
        {"path": "/carpicture01/pic-test/00000000_017.jpg"},
        {"path": "/carpicture01/pic-test/00000000_004.jpg"},
    ],
    "vehicleNo": "00가0000",
}

HISTORY_HTML = """
<html><body>
  <button data-enlog-dt-eventname="판매자정보"><span>(주) 테스트상사</span><strong>테스트판매자</strong></button>
  <div data-impression="차량이력">
    <p>차량이력</p>
    <ul>
      <li><p>내차 피해</p><em>총 1,000,001원 (3회)</em></li>
      <li><p>타차 가해</p><em>없음</em></li>
      <li><p>특이 사항</p>없음</li>
    </ul>
  </div>
</body></html>
"""


def _soup() -> BeautifulSoup:
    return BeautifulSoup(HISTORY_HTML, "html.parser")


def test_build_vehicle_fields():
    adapter = EncarDetailAdapter.__new__(EncarDetailAdapter)
    vehicle = adapter._build_vehicle(STATE)
    assert vehicle.make == "기아"
    assert vehicle.model == "올 뉴 모닝"
    assert vehicle.trim == "밴 기본형"
    assert vehicle.model_year == 2013
    assert vehicle.first_registration_date == "2013-06-01"
    assert vehicle.mileage_km == 42000
    assert vehicle.transmission == "manual"
    assert vehicle.fuel_type == "gasoline"
    assert vehicle.price_krw == 3_000_000
    assert vehicle.new_car_price_krw == 9_000_000


def test_build_dealer_fields():
    adapter = EncarDetailAdapter.__new__(EncarDetailAdapter)
    dealer = adapter._build_dealer(STATE, _soup())
    assert dealer.dealer_id == "test-dealer-encar"
    assert dealer.phone == "05000000000"
    assert dealer.region == "테스트시 가상구 예시로 0"
    assert "테스트상사" in dealer.display_name


def test_parse_insurance_history_splits_own_damage_claims():
    adapter = EncarDetailAdapter.__new__(EncarDetailAdapter)
    ih = adapter._parse_insurance_history(_soup())
    assert len(ih.own_damage_claims) == 3
    assert sum(c.amount_krw for c in ih.own_damage_claims) == 1_000_001
    assert ih.other_party_damage_claims == []
    assert ih.history_disclosed is True


def test_special_note_none_does_not_confirm_clean():
    """"특이 사항 없음"은 무엇을 포괄하는지 확정할 수 없어 flood/total_loss/theft를
    자동으로 False로 채우면 안 된다 — 계속 미확인(None)이어야 한다(모듈 docstring 참고)."""
    adapter = EncarDetailAdapter.__new__(EncarDetailAdapter)
    ih = adapter._parse_insurance_history(_soup())
    assert ih.flood_damage is None
    assert ih.total_loss is None
    assert ih.theft is None


def test_special_note_positive_keyword_sets_true():
    html = HISTORY_HTML.replace("<p>특이 사항</p>없음", "<p>특이 사항</p>침수 이력 있음")
    adapter = EncarDetailAdapter.__new__(EncarDetailAdapter)
    ih = adapter._parse_insurance_history(BeautifulSoup(html, "html.parser"))
    assert ih.flood_damage is True
    assert ih.total_loss is None
    assert ih.theft is None


def test_build_photos_prefixes_cdn_host():
    adapter = EncarDetailAdapter.__new__(EncarDetailAdapter)
    photos = adapter._build_photos(STATE)
    assert photos.urls == [
        "https://images.example.com/carpicture/carpicture01/pic-test/00000000_017.jpg",
        "https://images.example.com/carpicture/carpicture01/pic-test/00000000_004.jpg",
    ]


def test_build_verification_links_includes_plate():
    adapter = EncarDetailAdapter.__new__(EncarDetailAdapter)
    links = adapter._build_verification_links(STATE, "https://encar.example.com/cars/detail/00000000")
    labels = [l.label for l in links]
    assert "매물 상세페이지(엔카)" in labels
    carhistory = next(l for l in links if l.url == CARHISTORY_URL)
    assert "00가0000" in carhistory.note


def test_parse_detail_url_requires_cars_detail_pattern():
    adapter = EncarDetailAdapter.__new__(EncarDetailAdapter)
    try:
        adapter.parse_detail("https://encar.example.com/cars/report/00000000")
        assert False, "should have raised for wrong URL pattern"
    except RuntimeError as exc:
        assert "cars/detail" in str(exc)

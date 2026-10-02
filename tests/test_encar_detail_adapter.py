"""오프라인 파서 회귀 테스트. 모든 매물·판매자 값은 합성 데이터입니다.

사이트 구조에 필요한 필드명만 재현하며 실제 조회 데이터는 포함하지 않습니다.
"""
from __future__ import annotations

from bs4 import BeautifulSoup

from crawler.adapters.encar_detail_adapter import CARHISTORY_URL, EncarDetailAdapter
from normalizer.schema import HistoryEvent

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


def test_parse_insurance_summary_keeps_aggregate_without_inventing_claims():
    adapter = EncarDetailAdapter.__new__(EncarDetailAdapter)
    ih = adapter._parse_insurance_history(_soup())
    assert ih.own_damage_claims == []
    assert ih.own_damage_count == 3
    assert ih.own_damage_total_krw == 1_000_001
    assert ih.other_party_damage_claims == []
    assert ih.other_party_damage_count == 0
    assert ih.history_disclosed is None
    assert ih.owner_change_count is None


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


def test_parse_history_events_keeps_number_and_usage_changes():
    html = """
    <div class="OrderedByTimeHistory_timeline__a"><span class="OrderedByTimeHistory_date__b">21년 03월</span>
      <button data-enlog-dt-eventname="소유자변경"><span class="OrderedByTimeHistory_detail_txt__c">당사자 거래이전</span></button></div>
    <div class="OrderedByTimeHistory_timeline__a"><span class="OrderedByTimeHistory_date__b">22년 07월</span>
      <button data-enlog-dt-eventname="차량번호변경"><span class="OrderedByTimeHistory_detail_txt__c">번호 변경</span></button>
      <button data-enlog-dt-eventname="용도변경"><span class="OrderedByTimeHistory_detail_txt__c">대여용으로 변경</span></button></div>
    """
    adapter = EncarDetailAdapter.__new__(EncarDetailAdapter)
    events = adapter._parse_history_events(BeautifulSoup(html, "html.parser"))
    assert [(e.category, e.date) for e in events] == [
        ("소유자변경", "2021-03"), ("차량번호변경", "2022-07"), ("용도변경", "2022-07")
    ]
    assert adapter._normalize_history_date("2021년 03월 14일") == "2021-03-14"


def test_change_counts_use_exact_event_type_and_date():
    adapter = EncarDetailAdapter.__new__(EncarDetailAdapter)
    history = adapter._parse_insurance_history(_soup())
    adapter._apply_history_events(history, [
        HistoryEvent(category="소유자변경", date="2021-03-14", summary="당사자 거래이전"),
        HistoryEvent(category="변경등록", date="2022-07", details={"변경구분": "번호변경"}),
        HistoryEvent(category="변경등록", date="2023-01", details={"변경구분": "주소변경"}),
        HistoryEvent(category="용도변경", date="2024-02-03"),
    ])
    assert history.owner_change_count == 1
    assert history.owner_change_log[0].date == "2021-03-14"
    assert history.number_change_count == 1
    assert history.usage_change_count == 1


def test_parse_history_warnings_distinguishes_gap_from_no_gap():
    html = """<ul class="OrderedByItem_caution_list__a">
      <li><p class="OrderedByItem_txt__a">전손, 침수, 도난</p><p class="OrderedByItem_count__a">없음</p></li>
      <li><p class="OrderedByItem_txt__a">렌터카 등 대여용</p><p class="OrderedByItem_count__a">1건</p></li>
      <li><p class="OrderedByItem_txt__a">자차 보험 미가입 기간</p><p class="OrderedByItem_count__a">1건</p></li>
    </ul>"""
    adapter = EncarDetailAdapter.__new__(EncarDetailAdapter)
    ih = adapter._parse_insurance_history(_soup())
    adapter._parse_history_warnings(BeautifulSoup(html, "html.parser"), ih)
    assert ih.coverage_verified is True
    assert len(ih.info_unavailable_periods) == 1
    assert ih.usage_history.rental_used is True
    assert ih.flood_damage is False and ih.total_loss is False and ih.theft is False


def test_history_drawer_allows_values_without_em_tag():
    html = """<ul class="DetailContentsLayer_info_list__a">
      <li><span class="DetailContentsLayer_title__a">변경일자</span><em class="DetailContentsLayer_info_val__a">2021년 03월 14일</em></li>
      <li><span class="DetailContentsLayer_title__a">검사결과</span><span>적합</span></li>
    </ul>"""
    adapter = EncarDetailAdapter.__new__(EncarDetailAdapter)
    assert adapter._parse_history_drawer_details(BeautifulSoup(html, "html.parser")) == {
        "변경일자": "2021년 03월 14일", "검사결과": "적합",
    }


def test_parse_performance_record_separates_panel_frame_and_selected_results():
    html = """<ul>
      <li><strong class="tit_canv">외판</strong><ul class="list_state uiListLank1">
        <li><strong class="tit_part">프론트 휀더(우)</strong><div class="txt_state"><span>교환</span></div></li>
      </ul></li>
      <li><strong class="tit_canv">주요골격</strong>
        <ul class="list_state uiListLankA"><li class="uiLankNone">없음</li></ul>
        <ul class="list_state uiListLankB"><li><strong class="tit_part">사이드 멤버</strong><div class="txt_state">판금</div></li></ul>
        <ul class="list_state uiListLankC"><li class="uiLankNone">없음</li></ul>
      </li>
    </ul><table><tr><th>실린더 커버</th><td><span class="txt_state">없음</span><span class="txt_state on">미세누유</span></td></tr></table>"""
    adapter = EncarDetailAdapter.__new__(EncarDetailAdapter)
    record = adapter._parse_performance_record(BeautifulSoup(html, "html.parser"))
    assert record.record_available is True
    assert record.panel_exchange == ["프론트 휀더(우) · 교환"]
    assert record.frame_damage == ["B랭크 사이드 멤버 · 판금"]
    assert record.third_party_inspection.frame_ok is False
    assert record.leak_records == ["실린더 커버: 미세누유"]

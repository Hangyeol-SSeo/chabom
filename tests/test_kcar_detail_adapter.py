"""오프라인 파서 회귀 테스트. 모든 매물·판매자 값은 합성 데이터입니다.

사이트 구조에 필요한 필드명만 재현하며 실제 조회 데이터는 포함하지 않습니다.
"""
from __future__ import annotations

from bs4 import BeautifulSoup

from crawler.adapters.kcar_detail_adapter import CARHISTORY_URL, KcarDetailAdapter
from normalizer.schema import InsuranceHistory

DETAIL_HTML = """
<html><head><title>차량상세 직영 중고차</title></head>
<body>
  <div class="carNameWrap">
    <h2 class="carName">제네시스 G80 3.3 GDI AWD 프레스티지</h2>
    <div class="left"><span class="carNum">00나0000</span>
      <ul class="dotLists">
        <li>단순수리</li><li>18년 1월식</li><li>40,000km</li><li>가솔린</li><li>쥐색</li><li>오토</li><li>2,000만원</li>
      </ul>
    </div>
  </div>

  <div id="ext" class="repair_ext"><div id="index"><ul class="labels"><li>판금 2건</li><li>교환 1건</li></ul></div></div>
  <div id="frame" class="repair_frame"><div id="index"><ul class="labels"><li>판금 1건</li><li>교환 0건</li></ul></div></div>

  <ul>
    <li class="item-row"><i class="icon-accident"></i><p class="title">사고진단</p><p class="value"><b>단순수리</b></p></li>
  </ul>

  <p class="detail-tit"><b>K Car가 직접 꼼꼼히 확인한 진단 결과</b></p>
  <div class="messageContent">일반 총평 텍스트 — 이건 무시해야 한다.</div>

  <p class="detail-tit"><b>K Car가 찾은 차량 과거이력</b></p>
  <div class="messageContent">합성 이력: 주요골격과 무관한 외판 수리 예시입니다.</div>

  <p class="detail-tit"><b>특별한 보증혜택 K Car Warranty</b></p>
  <div class="messageContent">보증 마케팅 문구 — 이것도 무시해야 한다.</div>

  <div class="userName">테스트평가사 차량평가사</div>
  <div class="callGuide">0504-0000-0000</div>

  <img src="https://images.example.com/3dcarpicture/2026/08/173/00000000_1/main/main780.jpg">
  <img src="https://images.example.com/3dcarpicture/2026/08/173/00000000_1/extra/extra_0_lq.jpg">
</body></html>
"""


def _soup() -> BeautifulSoup:
    return BeautifulSoup(DETAIL_HTML, "html.parser")


def test_parse_vehicle_fields():
    adapter = KcarDetailAdapter.__new__(KcarDetailAdapter)
    vehicle = adapter._parse_vehicle(_soup())
    assert vehicle.make == "제네시스"
    assert vehicle.model == "G80 3.3 GDI AWD 프레스티지"
    assert vehicle.model_year == 2018
    assert vehicle.first_registration_date == "2018-01-01"
    assert vehicle.mileage_km == 40_000
    assert vehicle.fuel_type == "gasoline"
    assert vehicle.transmission == "automatic"
    assert vehicle.price_krw == 20_000_000


def test_parse_performance_record_separates_panel_and_frame():
    adapter = KcarDetailAdapter.__new__(KcarDetailAdapter)
    pr = adapter._parse_performance_record(_soup())
    assert pr.third_party_inspection.provider == "kcar_diagnosis"
    # 프레임 판금 1건이라도 있으면 frame_ok=False여야 한다(외판과는 별개 신호).
    assert pr.third_party_inspection.frame_ok is False
    assert pr.frame_damage == ["프레임 판금 1건/교환 0건(케이카 진단)"]
    assert pr.panel_exchange == ["외판 교환 1건(케이카 진단)"]
    assert pr.panel_exchange_count == 1
    assert pr.panel_repairs == ["외판 판금 2건(케이카 진단)"]


def test_parse_performance_record_frame_ok_when_zero():
    html = DETAIL_HTML.replace(
        '<div id="frame" class="repair_frame"><div id="index"><ul class="labels"><li>판금 1건</li><li>교환 0건</li></ul></div></div>',
        '<div id="frame" class="repair_frame"><div id="index"><ul class="labels"><li>판금 0건</li><li>교환 0건</li></ul></div></div>',
    )
    adapter = KcarDetailAdapter.__new__(KcarDetailAdapter)
    pr = adapter._parse_performance_record(BeautifulSoup(html, "html.parser"))
    assert pr.third_party_inspection.frame_ok is True
    assert pr.frame_damage == []


def test_absent_frame_diagnosis_stays_unknown():
    html = DETAIL_HTML.replace('<div id="frame" class="repair_frame"><div id="index"><ul class="labels"><li>판금 1건</li><li>교환 0건</li></ul></div></div>', '')
    record = KcarDetailAdapter.__new__(KcarDetailAdapter)._parse_performance_record(BeautifulSoup(html, "html.parser"))
    assert record.third_party_inspection.frame_ok is None


def test_parse_insurance_and_timeline_dialogs_with_dates_and_gap():
    adapter = KcarDetailAdapter.__new__(KcarDetailAdapter)
    history = InsuranceHistory(owner_change_count=None)
    insurance_html = '''<div class="el-dialog__body"><h2>보험사고이력 상세 정보</h2>
      <div class="hisBox"><ul>
        <li><p>소유자 변경</p><strong>2회</strong></li><li><p>차량번호 변경</p><strong>1회</strong></li>
        <li><p>전손 보험사고</p><strong>없음</strong></li><li><p>도난 보험사고</p><strong>없음</strong></li>
        <li><p>침수 보험사고</p><strong>없음</strong></li>
        <li><p>내차 피해</p><strong>1회(1,000,000원)</strong></li>
        <li><p>상대차 피해</p><strong>없음</strong></li>
      </ul></div><div class="boxDesc insuBox"><div class="insuTxt"><strong>2020년 01월 ~ 2020년 03월</strong></div></div>
      <table class="hisTb"><tbody>
        <tr><td>2022.07.01</td><td>소유자 변경</td><td>00가0000</td><td>자가용</td></tr>
        <tr><td>2021.03.14</td><td>소유자 변경</td><td>-</td><td>영업용</td></tr>
      </tbody></table></div>'''
    adapter._parse_insurance_dialog(BeautifulSoup(insurance_html, "html.parser"), history)
    assert history.history_detail_status == "available"
    assert history.coverage_verified is True
    assert history.owner_change_count == 2
    assert history.number_change_count == 1
    assert history.usage_change_count == 1
    assert history.flood_damage is False
    assert history.own_damage_count == 1
    assert history.own_damage_total_krw == 1_000_000
    assert history.info_unavailable_periods[0].start == "2020-01-01"
    assert history.info_unavailable_periods[0].end == "2020-03-31"
    timeline_html = '''<ul>
      <li class="cell toggle"><div class="cell-top"><span class="label">소유자 변경</span><span class="value">2021.03.14</span></div><div class="cell-content"><ul class="dot-list"><li><p>변경 사유: 합성 거래</p></li></ul></div></li>
      <li class="cell toggle"><div class="cell-top"><span class="label">용도 변경</span><span class="value">2022.07.01</span></div><div class="cell-content"><ul class="dot-list"><li><p>영업용에서 자가용으로 변경</p></li></ul></div></li>
    </ul>'''
    adapter._parse_history_dialog(BeautifulSoup(timeline_html, "html.parser"), history)
    assert len(history.owner_change_log) == 2
    assert any(event.category == "용도 변경" and event.date == "2022-07-01" for event in history.history_events)


def test_insurance_dialog_without_gap_evidence_does_not_mark_coverage_verified():
    html = '''<div class="el-dialog__body"><h2>보험사고이력 상세 정보</h2>
      <div class="hisBox"><ul><li><p>소유자 변경</p><strong>없음</strong></li>
      <li><p>차량번호 변경</p><strong>없음</strong></li></ul></div></div>'''
    history = InsuranceHistory(owner_change_count=None)
    KcarDetailAdapter.__new__(KcarDetailAdapter)._parse_insurance_dialog(BeautifulSoup(html, "html.parser"), history)
    assert history.history_detail_status == "available"
    assert history.coverage_verified is False
    assert history.history_warnings["자차 보험 미가입 기간"] == "미확인"


def test_find_message_content_picks_history_section_not_general_summary():
    adapter = KcarDetailAdapter.__new__(KcarDetailAdapter)
    text = adapter._find_message_content(_soup(), "과거이력")
    assert "주요골격과 무관" in text
    assert "일반 총평" not in text
    assert "보증 마케팅" not in text


def test_parse_insurance_history_disclosed_when_history_section_present():
    adapter = KcarDetailAdapter.__new__(KcarDetailAdapter)
    ih = adapter._parse_insurance_history(_soup())
    assert ih.history_disclosed is True
    # 케이카는 자유서술문이라 침수/전손/도난을 안전하게 파싱할 수 없어 항상 미확인이어야 한다.
    assert ih.flood_damage is None
    assert ih.total_loss is None
    assert ih.theft is None


def test_parse_dealer_fields():
    adapter = KcarDetailAdapter.__new__(KcarDetailAdapter)
    dealer = adapter._parse_dealer(_soup())
    assert dealer.display_name == "테스트평가사 차량평가사"
    assert dealer.phone == "050400000000"
    assert dealer.dealer_id == "050400000000"


def test_parse_photos_excludes_extra_images():
    adapter = KcarDetailAdapter.__new__(KcarDetailAdapter)
    photos = adapter._parse_photos(_soup())
    assert photos.urls == ["https://images.example.com/3dcarpicture/2026/08/173/00000000_1/main/main780.jpg"]


def test_parse_listing_text_includes_accident_badge():
    adapter = KcarDetailAdapter.__new__(KcarDetailAdapter)
    lt = adapter._parse_listing_text(_soup())
    assert lt.claims_parsed == ["단순수리"]
    assert "주요골격과 무관" in lt.description_raw


def test_build_verification_links_includes_plate_and_carhistory():
    adapter = KcarDetailAdapter.__new__(KcarDetailAdapter)
    links = adapter._build_verification_links(_soup(), "https://kcar.example.com/bc/detail/carInfoDtl?i_sCarCd=EC00000000")
    labels = [l.label for l in links]
    assert "매물 상세페이지(케이카)" in labels
    assert "성능·상태 점검기록부 원본 보기" in labels
    carhistory = next(l for l in links if l.url == CARHISTORY_URL)
    assert "00나0000" in carhistory.note


def test_parse_detail_url_requires_i_scarcd_param():
    adapter = KcarDetailAdapter.__new__(KcarDetailAdapter)
    try:
        adapter.parse_detail("https://kcar.example.com/bc/detail/carInfoDtl?carCd=EC00000000")
        assert False, "should have raised for wrong param name"
    except RuntimeError as exc:
        assert "i_sCarCd" in str(exc)

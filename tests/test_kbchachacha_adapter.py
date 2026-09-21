"""오프라인 파서 회귀 테스트. 모든 매물·판매자 값은 합성 데이터입니다.

사이트 구조에 필요한 필드명만 재현하며 실제 조회 데이터는 포함하지 않습니다.
"""
from __future__ import annotations

from bs4 import BeautifulSoup

from crawler.adapters.kbchachacha_adapter import CARHISTORY_URL, KbchachachaAdapter
from normalizer.schema import Vehicle

DETAIL_HTML = """
<html><head>
<title>BMW 3시리즈 (G20) · 가솔린 · 경기 | 매물번호(00000000) | KB차차차</title>
<meta property="og:image" content="https://images.example.com/IMG/carimg/l/img06/img-test/1.jpeg" />
</head>
<body>
  <table>
    <tr><th>차량정보</th><td>000나0000</td><th>연식</th><td>23년02월(23년형)</td></tr>
    <tr><th>주행거리</th><td>40,000km</td><th>연료</th><td>가솔린</td></tr>
    <tr><th>변속기</th><td>오토</td><th>차종</th><td>준중형</td></tr>
    <tr><th>압류</th><td>없음</td><th>저당</th><td>있음</td></tr>
    <tr><th>세금미납</th><td>없음</td><th>제시번호</th><td>00000000000</td></tr>
  </table>
  <dl>
    <dt>판매가격</dt>
    <dd><strong class="c-title-28">3,000만원</strong></dd>
  </dl>
  <a class="detail-txt-link02">
    <span class="fs-16">보험사고정보</span>
    <span class="link-arrow" id="btnCarHistoryView2">사고있음</span>
  </a>
  <dl>
    <dt>전손이력</dt><dd><strong>없음</strong></dd>
    <dt class="mg-l">침수이력</dt><dd><strong>있음</strong></dd>
    <dt>용도이력</dt><dd><strong>없음</strong></dd>
    <dt class="mg-l">소유자변경</dt><dd><strong>있음</strong></dd>
  </dl>
  <a class="vs-pill" data-link-url="https://inspection.example.com/carCheck/carmodooPrint.do?print=0&checkNum=0000000000">
    <span class="vs-pill__label">성능점검</span>
  </a>
</body></html>
"""


def _soup() -> BeautifulSoup:
    return BeautifulSoup(DETAIL_HTML, "html.parser")


def test_looks_like_bot_block_detects_challenge_page():
    adapter = KbchachachaAdapter.__new__(KbchachachaAdapter)
    assert adapter._looks_like_bot_block(Vehicle(make="로봇여부", model="확인")) is True
    assert adapter._looks_like_bot_block(Vehicle()) is True  # 모든 핵심 필드가 비어있는 경우도 가드


def test_looks_like_bot_block_false_for_real_listing():
    adapter = KbchachachaAdapter.__new__(KbchachachaAdapter)
    real = Vehicle(make="BMW", price_krw=30_000_000, model_year=2023, mileage_km=40_000)
    assert adapter._looks_like_bot_block(real) is False


def test_parse_vehicle_fields():
    adapter = KbchachachaAdapter.__new__(KbchachachaAdapter)
    vehicle = adapter._parse_vehicle(_soup())
    assert vehicle.make == "BMW"
    assert vehicle.model_year == 2023
    assert vehicle.mileage_km == 40_000
    assert vehicle.transmission == "automatic"
    assert vehicle.fuel_type == "gasoline"
    assert vehicle.body_type == "준중형"
    assert vehicle.price_krw == 30_000_000
    assert vehicle.region == "경기"


def test_parse_insurance_summary_owner_change_and_disclosure():
    adapter = KbchachachaAdapter.__new__(KbchachachaAdapter)
    ih = adapter._parse_insurance_summary(_soup())
    assert ih.owner_change_count == 1  # "있음" -> 하한값 1로 근사
    assert ih.history_disclosed is True


def test_parse_listing_text_flags_accident_flood_and_lien():
    adapter = KbchachachaAdapter.__new__(KbchachachaAdapter)
    lt = adapter._parse_listing_text(_soup())
    assert "사고있음" in lt.claims_parsed
    assert "침수이력있음" in lt.claims_parsed
    assert "저당있음" in lt.claims_parsed
    assert "전손이력있음" not in lt.claims_parsed  # 없음이므로 태그 없어야 함


def test_build_verification_links_includes_carmodoo_and_carhistory_with_plate():
    adapter = KbchachachaAdapter.__new__(KbchachachaAdapter)
    links = adapter._build_verification_links(
        _soup(), "https://kb.example.com/public/car/detail.kbc?carSeq=00000000"
    )
    labels = [l.label for l in links]
    assert "매물 상세페이지(KB차차차)" in labels
    assert "성능점검기록부 원본 보기(카모두)" in labels
    assert "카히스토리 공식 조회(보험개발원)" in labels

    carmodoo = next(l for l in links if "카모두" in l.label)
    assert carmodoo.url.startswith("https://inspection.example.com/")

    carhistory = next(l for l in links if l.label == "카히스토리 공식 조회(보험개발원)")
    assert carhistory.url == CARHISTORY_URL
    assert "000나0000" in carhistory.note

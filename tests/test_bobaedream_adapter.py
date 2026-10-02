"""오프라인 파서 회귀 테스트. 모든 매물·판매자 값은 합성 데이터입니다.

사이트 구조에 필요한 필드명만 재현하며 실제 조회 데이터는 포함하지 않습니다.
"""
from __future__ import annotations

from bs4 import BeautifulSoup

from crawler.adapters.bobaedream_adapter import CARHISTORY_URL, BobaedreamAdapter

DETAIL_HTML = """
<html><head><title>2007 아우디 TT  로드스터 2.0 TFSI 중고차 | 보배드림 수입중고차</title></head>
<body>
  <div class="group-info">
    <div class="info-price box mode-basic">
      <span class="price">900</span> 만원
    </div>
  </div>
  <table>
    <tr><th>연식</th><td>2007.06</td><th>배기량</th><td>1,984 cc</td></tr>
    <tr><th>주행거리</th><td>100,000 km</td><th>색상</th><td>검정색</td></tr>
    <tr><th>변속기</th><td>자동</td><th></th><td></td></tr>
    <tr><th>연료</th><td>가솔린</td><th>확인사항</th><td></td></tr>
  </table>
  <dl class="clearfix"><b>차량번호 000가0000</b> 등록번호 0000000000</dl>
  <dl class="clearfix"><dt>휴대폰</dt><dd>010-0000-0000</dd><dt>주소</dt><dd>테스트시 가상구 예시로 0</dd></dl>
  <dl class="clearfix"><dd>판매중 <b><a href="/mycar/mycar_holding_list.php?sellerID=abc123">9</a></b></dd>
      <dd>판매완료 <b><a href="/mycar/mycar_holding_list.php?sellerID=abc123">0</a></b></dd></dl>
  <p>보험처리 5 회</p>
  <p>차량번호/소유자변경 3회 / 15회 자동차보험 특수사고 전손: 0 / 침수전손 : 0 / 침수분손 : 0 / 도난: 0</p>
  <p>보험사고(내차피헤) 3회 (3,000,001원) 보험사고(타차가해) 2회 (2,000,001원)</p>
  <a href="javascript:;" onclick="fNewWin('/mycar/popup/mycarChart_B.php?car_number=000%EA%B0%800000&amp;tbl=mycar&amp;cno=0000000', '1251', '700');">보험이력</a>
  <meta property="og:image" content="https://images.example.com/renew2017/assets/images/bobae.png" />
  <ul class="gallery">
    <li><img src="//images.example.com/pds/CyberCar/5/000000/img_000000_1.jpg"></li>
    <li><img src="//images.example.com/pds/CyberCar/5/000000/img_000000_2.jpg"></li>
  </ul>
</body></html>
"""


def _soup() -> BeautifulSoup:
    return BeautifulSoup(DETAIL_HTML, "html.parser")


def test_parse_vehicle_fields():
    adapter = BobaedreamAdapter.__new__(BobaedreamAdapter)
    vehicle = adapter._parse_vehicle(_soup())
    assert vehicle.make == "아우디"
    assert vehicle.model_year == 2007
    assert vehicle.mileage_km == 100_000
    assert vehicle.transmission == "automatic"
    assert vehicle.fuel_type == "gasoline"
    assert vehicle.price_krw == 9_000_000
    assert vehicle.region == "테스트시 가상구"
    assert vehicle.listing_type == "sale"


def test_parse_vehicle_detects_rental_transfer_listing():
    html = DETAIL_HTML.replace(
        "</table>",
        "</table><table><tr><th>월렌트료</th><td>44 만원</td><th>렌트기간</th>"
        "<td>26/05 ~ 31/05</td></tr></table>",
        1,
    )
    adapter = BobaedreamAdapter.__new__(BobaedreamAdapter)
    vehicle = adapter._parse_vehicle(BeautifulSoup(html, "html.parser"))
    assert vehicle.listing_type == "rental_transfer"


def test_parse_photos_uses_gallery_not_site_logo():
    adapter = BobaedreamAdapter.__new__(BobaedreamAdapter)
    photos = adapter._parse_photos(_soup())
    assert photos.urls == [
        "https://images.example.com/pds/CyberCar/5/000000/img_000000_1.jpg",
        "https://images.example.com/pds/CyberCar/5/000000/img_000000_2.jpg",
    ]
    assert all("bobae.png" not in u for u in photos.urls)


def test_parse_insurance_summary_splits_own_and_other_party_claims():
    adapter = BobaedreamAdapter.__new__(BobaedreamAdapter)
    ih = adapter._parse_insurance_summary(_soup())
    assert ih.owner_change_count == 15
    assert len(ih.own_damage_claims) == 3
    assert sum(c.amount_krw for c in ih.own_damage_claims) == 3_000_001
    assert len(ih.other_party_damage_claims) == 2
    assert sum(c.amount_krw for c in ih.other_party_damage_claims) == 2_000_001
    assert ih.history_disclosed is True


def test_parse_insurance_summary_special_accident_counts_all_zero():
    # 실제 페이지 표기: "자동차보험 특수사고 전손: 0 / 침수전손 : 0 / 침수분손 : 0 / 도난: 0"
    # 예전엔 이 줄의 "도난"이라는 글자만 보고 단순 부분 문자열 매칭으로 도난 이력이 "있다"고
    # 오판했다(숫자가 0이어도) — 이제는 숫자까지 정확히 파싱해 전부 False(없음)로 나와야 한다.
    adapter = BobaedreamAdapter.__new__(BobaedreamAdapter)
    ih = adapter._parse_insurance_summary(_soup())
    assert ih.total_loss is False
    assert ih.flood_damage is False
    assert ih.theft is False


def test_parse_insurance_summary_special_accident_counts_nonzero():
    html = DETAIL_HTML.replace(
        "전손: 0 / 침수전손 : 0 / 침수분손 : 0 / 도난: 0",
        "전손: 1 / 침수전손 : 0 / 침수분손 : 2 / 도난: 1",
    )
    adapter = BobaedreamAdapter.__new__(BobaedreamAdapter)
    ih = adapter._parse_insurance_summary(BeautifulSoup(html, "html.parser"))
    assert ih.total_loss is True
    assert ih.flood_damage is True  # 침수분손 2건 > 0
    assert ih.theft is True


def test_parse_insurance_summary_special_accident_counts_missing_is_unknown():
    html = DETAIL_HTML.replace("전손: 0 / 침수전손 : 0 / 침수분손 : 0 / 도난: 0", "")
    adapter = BobaedreamAdapter.__new__(BobaedreamAdapter)
    ih = adapter._parse_insurance_summary(BeautifulSoup(html, "html.parser"))
    assert ih.total_loss is None
    assert ih.flood_damage is None
    assert ih.theft is None


def test_parse_dealer_sales_counts():
    adapter = BobaedreamAdapter.__new__(BobaedreamAdapter)
    dealer = adapter._parse_dealer(_soup())
    assert dealer.active_listings_count == 9
    assert dealer.total_sales_count == 0
    assert dealer.region == "테스트시 가상구"
    assert dealer.phone == "01000000000"


def test_build_verification_links_includes_popup_and_carhistory_with_plate():
    adapter = BobaedreamAdapter.__new__(BobaedreamAdapter)
    links = adapter._build_verification_links(_soup(), "https://bobaedream.example.com/mycar/mycar_view.php?no=0000000&gubun=I")
    labels = [l.label for l in links]

    assert "매물 상세페이지(보배드림)" in labels
    assert "보험이력 상세 조회(보배드림)" in labels
    assert "카히스토리 공식 조회(보험개발원)" in labels
    assert "성능점검기록부 확인 안내" in labels

    carhistory_link = next(l for l in links if l.label == "카히스토리 공식 조회(보험개발원)")
    assert carhistory_link.url == CARHISTORY_URL
    assert "000가0000" in carhistory_link.note

    popup_link = next(l for l in links if l.label == "보험이력 상세 조회(보배드림)")
    assert popup_link.url.startswith("https://bobaedream.example.com/mycar/popup/mycarChart_B.php")


def test_build_verification_links_without_popup_link_falls_back_gracefully():
    soup = BeautifulSoup("<html><body>내용 없음</body></html>", "html.parser")
    adapter = BobaedreamAdapter.__new__(BobaedreamAdapter)
    links = adapter._build_verification_links(soup, "https://bobaedream.example.com/mycar/mycar_view.php?no=1&gubun=K")
    labels = [l.label for l in links]
    assert "보험이력 상세 조회(보배드림)" not in labels
    assert "카히스토리 공식 조회(보험개발원)" in labels


# -- 성능점검기록부 ("성능점검" 섹션의 상세보기 팝업) -----------------------------------

INFO_CHECK_HTML = """
<div class="info-check">
  <div class="top-state"><dl><dd>판금 <b>1</b> 회</dd><dd>교환 <b>2</b> 회</dd><dd>부식 <b>0</b> 회</dd></dl></div>
  <button type="button" class="btn-view-more"
    onclick="fNewWin('/mycar/popup/mycarChart_5.php?zone=M&amp;cno=0000000&amp;tbl=mycar', '870', '700')">상세보기</button>
</div>
"""


def _state_rows(rows: list[tuple[str, str]]) -> str:
    """rows: (부위 제목, 표시된 열). 열은 change/weld/corr 중 하나이거나 빈 문자열(이상 없음)."""
    labels = {"change": "교환", "weld": "판금/용접", "corr": "부식"}
    html = ""
    for part, marked in rows:
        cells = "".join(
            f'<td><span class="i-mark {name}">{labels[name]}</span></td>' if name == marked else "<td></td>"
            for name in ("change", "weld", "corr")
        )
        html += f"<tr><th>{part}</th>{cells}</tr>"
    return html


def _performance_html(panel_rows, frame_rows, leak_value="없음") -> str:
    head = "<thead><tr><th>구분</th><th>교환(교체)</th><th>판금/용접</th><th>부식</th></tr></thead>"
    return f"""
    <div class="popup-content p-performance-check">
      <div class="popup-section"><div class="tbl-01 mode-bg"><table><tbody>
        <tr><th>사고유무<br>(단순수리제외)</th><td>무</td><th>침수유무</th><td>무</td></tr>
        <tr><th rowspan="2">자가진단사항</th><td>원동기 양호</td><th rowspan="2">배출가스</th><td rowspan="2">매연 : 0%</td></tr>
        <tr><td>변속기 양호</td></tr>
      </tbody></table></div></div>
      <div class="popup-section check-list"><div class="tbl-01 mode-bg"><table>
        <thead><tr><th>주요장치</th><th>항목</th><th>해당부품</th><th>상태</th></tr></thead>
        <tbody>
          <tr><th rowspan="4">원동기</th><th rowspan="2">오일누유</th><th>실린더헤드</th><td>{leak_value}</td></tr>
          <tr><th>실린더블럭</th><td>없음</td></tr>
          <tr><th rowspan="2">냉각수 누수</th><th>워터펌프</th><td>없음</td></tr>
          <tr><th>냉각수량 및 오염</th><td>적정</td></tr>
          <tr><th>제동</th><th colspan="2">브레이크 오일 누유</th><td>양호</td></tr>
        </tbody></table></div></div>
      <div class="popup-section check-list"><div class="wrap-history-list">
        <div class="tbl-02 mode-bg" id="ex_history"><table>{head}<tbody>{_state_rows(panel_rows)}</tbody></table></div>
        <div class="tbl-02 mode-bg" id="in_history"><table>{head}<tbody>{_state_rows(frame_rows)}</tbody></table></div>
      </div></div>
    </div>
    """


def _parse_performance(html: str):
    adapter = BobaedreamAdapter.__new__(BobaedreamAdapter)
    return adapter._parse_performance_record(BeautifulSoup(html, "html.parser"))


def test_performance_popup_url_is_read_from_detail_page():
    adapter = BobaedreamAdapter.__new__(BobaedreamAdapter)
    assert adapter._find_performance_popup_url(_soup()) is None
    soup = BeautifulSoup(INFO_CHECK_HTML, "html.parser")
    url = adapter._find_performance_popup_url(soup)
    assert url == "https://bobaedream.example.com/mycar/popup/mycarChart_5.php?zone=M&cno=0000000&tbl=mycar"
    labels = {l.label: l.url for l in adapter._build_verification_links(soup, "https://bobaedream.example.com/x")}
    assert labels["성능점검기록부 원본(보배드림)"] == url
    assert "성능점검기록부 확인 안내" not in labels


def test_performance_record_clean_car_confirms_frame_ok():
    record = _parse_performance(_performance_html(
        [("1. 후드", ""), ("2. 프론트 휀더(좌)", "")], [("1. 프론트 패널", ""), ("22. 리어 패널", "")],
    ))
    assert record.record_available is True
    assert record.panel_exchange == record.panel_repairs == record.frame_damage == record.leak_records == []
    assert record.third_party_inspection.frame_ok is True
    assert record.inspection_results["원동기 · 오일누유 · 실린더블럭"] == "없음"
    assert record.inspection_results["원동기 · 냉각수 누수 · 냉각수량 및 오염"] == "적정"
    assert record.inspection_results["제동 · 브레이크 오일 누유"] == "양호"
    assert record.inspection_results["사고유무 (단순수리제외)"] == "무"
    assert "자가진단사항 · 배출가스" not in record.inspection_results


def test_performance_record_separates_panel_work_from_frame_damage():
    record = _parse_performance(_performance_html(
        [("3. 프론트 휀더(우)", "change"), ("7. 리어 도어(우)", "weld"), ("13.사이드실 패널(좌)", "corr")],
        [("1. 프론트 패널", ""), ("22. 리어 패널", "change")],
        leak_value="미세누유",
    ))
    assert record.panel_exchange == ["프론트 휀더(우) · 교환"]
    assert record.panel_repairs == ["리어 도어(우) · 판금/용접", "사이드실 패널(좌) · 부식"]
    assert record.frame_damage == ["리어 패널 · 교환"]
    assert record.third_party_inspection.frame_ok is False
    assert record.leak_records == ["원동기 · 오일누유 · 실린더헤드: 미세누유"]


def test_performance_record_registered_as_image_stays_unknown():
    record = _parse_performance(
        '<div class="popup-content p-certificate car"><div class="scroll-area">'
        '<img src="//images.example.com/confirm/record_1.jpg"></div></div>'
    )
    assert record.record_available is False
    assert record.third_party_inspection.frame_ok is None  # 판독 불가 — 미확인으로 남겨 보류시킨다
    assert record.record_images == ["https://images.example.com/confirm/record_1.jpg"]


def test_parse_detail_reads_performance_popup_once():
    class Fetcher:
        def __init__(self):
            self.urls = []

        def get_html(self, url, timeout_sec=20.0):
            self.urls.append(url)
            if "mycarChart_5" in url:
                return _performance_html([("3. 프론트 휀더(우)", "change")], [("1. 프론트 패널", "")])
            return DETAIL_HTML.replace("</body>", INFO_CHECK_HTML + "</body>")

    class NoWait:
        def wait(self):
            pass

    fetcher = Fetcher()
    adapter = BobaedreamAdapter(fetcher=fetcher, rate_limiter=NoWait())
    listing = adapter.parse_detail("https://bobaedream.example.com/mycar/mycar_view.php?no=0000000&gubun=K")
    assert [u for u in fetcher.urls if "mycarChart_5" in u] == [
        "https://bobaedream.example.com/mycar/popup/mycarChart_5.php?zone=M&cno=0000000&tbl=mycar"
    ]
    assert listing.performance_record.record_url == fetcher.urls[-1]
    assert listing.performance_record.panel_exchange == ["프론트 휀더(우) · 교환"]
    assert listing.performance_record.third_party_inspection.frame_ok is True
    restored = type(listing).from_dict(listing.to_dict())
    assert restored.performance_record.panel_exchange == ["프론트 휀더(우) · 교환"]

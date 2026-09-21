"""테스트에 실제 연락처·차량번호·매물 URL이 다시 들어오는 것을 방지한다."""
from pathlib import Path
import re
from urllib.parse import unquote, urlsplit


TESTS = Path(__file__).parent


def test_fixtures_use_synthetic_contact_and_plate_values():
    allowed_phones = {'01000000000', '01000000001', '05000000000', '050400000000'}
    allowed_plates = {'00가0000', '00나0000', '000가0000', '000나0000'}
    for path in TESTS.iterdir():
        if path.suffix not in {'.py', '.json'}:
            continue
        text = unquote(path.read_text())
        phones = re.findall(r'(?<!\d)(?:010|0504|050)[ -]?\d{4}[ -]?\d{4}(?!\d)', text)
        assert all(re.sub(r'\D', '', phone) in allowed_phones for phone in phones), path.name
        plates = re.findall(r'(?<!\d)\d{2,3}[가-힣]\d{4}(?!\d)', text)
        assert set(plates) <= allowed_plates, path.name


def test_fixture_urls_do_not_identify_real_listings():
    for path in TESTS.iterdir():
        if path.suffix not in {'.py', '.json'}:
            continue
        for url in re.findall(r'https?://[^\s\x22\x27<>]+', path.read_text()):
            parsed = urlsplit(url)
            host = parsed.hostname or ''
            if host == 'www.carhistory.or.kr' and parsed.path == '/main.car' and not parsed.query:
                continue  # 개인 식별정보가 없는 공식 조회 시작 페이지
            assert host in {'example.com', 'localhost', '127.0.0.1'} or host.endswith('.example.com'), path.name

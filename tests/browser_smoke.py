"""브라우저 통합 확인 (Firebase 에뮬레이터 + 로컬 개발 서버):

    npm run test:e2e

Auth·Firestore 에뮬레이터 안에서 CHABOM_DEV=1 서버를 8765 포트로 띄우고, 실제 차량 사이트는
조회하지 않는다(링크 조회는 가짜 결과로 바꾼다). Firebase JS SDK는 node_modules/firebase에서 내준다.
"""
import os
import threading
import time
from pathlib import Path

PROJECT = os.environ.setdefault('GOOGLE_CLOUD_PROJECT', 'demo-chabom')
os.environ.setdefault('FIREBASE_AUTH_EMULATOR_HOST', '127.0.0.1:9099')
os.environ.setdefault('FIRESTORE_EMULATOR_HOST', '127.0.0.1:8080')
os.environ['CHABOM_DEV'] = '1'

import uvicorn  # noqa: E402
from firebase_admin import auth as admin_auth, firestore  # noqa: E402
from playwright.sync_api import expect, sync_playwright  # noqa: E402

import server  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
SDK_DIR = ROOT / 'node_modules' / 'firebase'
EMAIL = 'tester@example.com'
SAMPLE = {'source': 'encar', 'listing_id': '123', 'url': 'https://example.com/car',
          'vehicle': {'make': '기아', 'model': '더 뉴 모닝', 'model_year': 2021, 'mileage_km': 32000, 'price_krw': 8900000, 'region': '서울'},
          'dealer': {'dealer_id': 'dealer-1', 'display_name': '테스트판매자A', 'phone': '01000000000', 'region': '서울 강남'},
          'insurance_history': {'own_damage_claims': [{'amount_krw': 5000000}]}}
LOOKUP_SAMPLE = {'source': 'bobaedream', 'listing_id': '789', 'url': 'https://bobaedream.example.com/car/789',
                 'vehicle': {'make': '현대', 'model': '아반떼', 'model_year': 2022, 'price_krw': 18000000},
                 'dealer': {'dealer_id': 'dealer-3', 'display_name': '테스트판매자C'}}

server._SOURCE_HOSTS = {'bobaedream.example.com': ('bobaedream', True)}
server._lookup = lambda url, source: {'ok': True, 'listing': {**LOOKUP_SAMPLE, 'url': url}}

db = server.gateway.firestore_client()


def seed(uid: str) -> None:
    import hashlib
    from datetime import datetime, timedelta, timezone

    base = datetime(2026, 10, 1, tzinfo=timezone.utc)
    items = [
        ('https://example.com/car', 'encar', SAMPLE, 'success', ''),
        ('https://example.com/car2', 'kcar', {'source': 'kcar', 'listing_id': '456', 'vehicle': {'make': '현대', 'model': '캐스퍼', 'model_year': 2023, 'price_krw': 15000000},
                                               'dealer': {'dealer_id': 'dealer-2', 'display_name': '테스트판매자B'}}, 'success', ''),
        ('https://example.com/failed', '', None, 'failed', '조회 실패'),
    ]
    for offset, (url, source, listing, status, reason) in enumerate(items):
        db.document(f'users/{uid}/history/{hashlib.sha256(url.encode()).hexdigest()}').set({
            'url': url, 'source': source, 'listing': listing, 'status': status, 'reason': reason,
            'last_viewed_at': (base - timedelta(minutes=offset)).isoformat(), 'favorite': False, 'origin': 'lookup'})


def reset_emulators() -> None:
    """이전 실행이 남긴 계정·문서를 지운다(에뮬레이터를 켜 둔 채 반복 실행할 때)."""
    from urllib.parse import urlunsplit
    from urllib.request import Request, urlopen

    for host, path in ((os.environ['FIRESTORE_EMULATOR_HOST'], f'/emulator/v1/projects/{PROJECT}/databases/(default)/documents'),
                       (os.environ['FIREBASE_AUTH_EMULATOR_HOST'], f'/emulator/v1/projects/{PROJECT}/accounts')):
        urlopen(Request(urlunsplit(('http', host, path, '', '')), method='DELETE')).close()


def serve_sdk(route):
    name = route.request.url.rsplit('/', 1)[-1]
    route.fulfill(path=str(SDK_DIR / name), content_type='text/javascript')


def sign_in(page):
    """에뮬레이터용 가짜 Google 자격 증명으로 로그인한다.

    팝업 로그인은 apis.google.com 스크립트를 받아야 해서 네트워크가 막힌 곳에서는 돌지 않는다.
    로그인 이후의 흐름(onAuthStateChanged → 허용 목록 확인 → 화면 전환)은 실제와 같다.
    """
    expect(page.get_by_role('button', name='Google로 계속하기')).to_be_visible()
    page.evaluate("""async email => {
        // 앱이 이미 불러온 SDK 모듈을 그대로 써야 같은 Firebase 앱 인스턴스에 로그인된다.
        const url = performance.getEntriesByType('resource').map(e => e.name).find(n => n.endsWith('/firebase-auth.js'));
        const sdk = await import(url);
        const token = JSON.stringify({sub: 'e2e-' + email, email, email_verified: true});
        await sdk.signInWithCredential(sdk.getAuth(), sdk.GoogleAuthProvider.credential(token));
    }""", EMAIL)


def main() -> None:
    for name in ('firebase-app.js', 'firebase-auth.js', 'firebase-firestore.js'):
        assert (SDK_DIR / name).exists(), f'{SDK_DIR / name} 없음 — npm install을 먼저 실행하세요.'
    reset_emulators()
    service = uvicorn.Server(uvicorn.Config(server.app, host='127.0.0.1', port=8765, log_level='error'))
    thread = threading.Thread(target=service.run, daemon=True)
    thread.start()
    try:
        for _ in range(100):
            if service.started:
                break
            time.sleep(.05)
        with sync_playwright() as p:
            # 미리 설치된 Chromium을 쓰려면 CHROMIUM_PATH를 지정한다(Playwright 버전과 브라우저 빌드가 다를 때).
            browser = p.chromium.launch(executable_path=os.environ.get('CHROMIUM_PATH') or None)
            context = browser.new_context(viewport={'width': 1440, 'height': 1050})
            context.route('**/firebasejs/12.19.0/*', serve_sdk)
            page = context.new_page()
            errors = []
            page.on('pageerror', lambda error: errors.append(str(error)))
            page.goto('http://127.0.0.1:8765')

            # 허용 목록에 없으면 승인 대기 화면에 머문다.
            expect(page.get_by_role('button', name='카카오로 계속하기')).to_be_visible()
            page.screenshot(path='/tmp/chabom-login-desktop.png')
            sign_in(page)
            expect(page.get_by_role('heading', name='사용 승인 대기')).to_be_visible()
            expect(page.locator('.auth-email')).to_have_text(EMAIL)
            page.set_viewport_size({'width': 390, 'height': 844})
            page.screenshot(path='/tmp/chabom-pending-mobile.png')
            page.set_viewport_size({'width': 1440, 'height': 1050})
            db.document(f'allowlist/{EMAIL}').set({'note': 'e2e'})
            uid = admin_auth.get_user_by_email(EMAIL).uid
            seed(uid)
            page.get_by_role('button', name='승인 여부 다시 확인').click()
            expect(page.locator('.car-row')).to_have_count(3)
            expect(page.locator('#accountEmail')).to_have_text(EMAIL)

            page.get_by_role('button', name='기아 더 뉴 모닝 찜하기', exact=True).click()
            page.get_by_role('button', name='찜한 매물1', exact=True).click()
            expect(page.locator('.car-row')).to_have_count(1)
            page.reload()
            expect(page.locator('.car-row')).to_have_count(3)
            expect(page.get_by_role('button', name='기아 더 뉴 모닝 찜 해제', exact=True)).to_be_visible()
            page.get_by_label('차량 검색').fill('캐스퍼')
            expect(page.locator('.car-row')).to_have_count(1)
            page.get_by_label('차량 검색').fill('')
            page.get_by_label('차량 정렬').select_option('price-high')
            expect(page.locator('.car-title').first).to_have_text('현대 캐스퍼')
            page.screenshot(path='/tmp/chabom-garage-desktop.png', full_page=True)

            page.get_by_role('button', name='기아 더 뉴 모닝', exact=True).click()
            page.get_by_role('button', name='딜러 찜하기', exact=True).click()
            expect(page.get_by_role('button', name='찜 해제', exact=True)).to_be_visible()
            page.get_by_role('link', name='딜러 관리', exact=False).click()
            expect(page.locator('.dealer-card')).to_have_count(1)
            page.get_by_role('button', name='제외 등록', exact=True).click()
            page.get_by_label('제외 사유').fill('성능기록부와 실제 상태가 다름')
            page.locator('#dialogSubmit').click()
            expect(page.locator('dialog')).not_to_be_visible()
            expect(page.locator('.dealer-card')).to_have_count(0)
            page.get_by_role('button', name='블랙리스트1', exact=True).click()
            expect(page.locator('.dealer-card')).to_have_count(1)
            expect(page.get_by_text('성능기록부와 실제 상태가 다름', exact=False)).to_be_visible()
            page.screenshot(path='/tmp/chabom-dealers-desktop.png', full_page=True)
            page.reload()
            page.get_by_role('button', name='블랙리스트1', exact=True).click()
            expect(page.locator('.dealer-card')).to_have_count(1)
            page.get_by_role('button', name='보관한 차량 1대 보기').click()
            expect(page.locator('.car-row')).to_have_count(1)

            # 블랙리스트 딜러는 판정에서 결격으로 걸리고, 저장된 보험 이력은 그대로 남는다.
            page.get_by_role('button', name='기아 더 뉴 모닝', exact=True).click()
            page.get_by_role('button', name='저장하고 검증하기').click()
            expect(page.get_by_role('heading', name='구매 보류 · 추가 확인 필요')).to_be_visible()
            expect(page.locator('.result-item').filter(has_text='딜러 블랙리스트 대조')).to_contain_text('결격')
            import hashlib
            saved = db.document(f'users/{uid}/history/{hashlib.sha256(SAMPLE["url"].encode()).hexdigest()}').get().to_dict()
            assert saved['listing']['insurance_history']['own_damage_claims'][0]['amount_krw'] == 5000000
            assert saved['favorite'] is True
            page.get_by_role('button', name='블랙리스트 해제', exact=True).click()
            expect(page.get_by_role('button', name='딜러 찜하기', exact=True)).to_be_visible()

            page.set_viewport_size({'width': 390, 'height': 844})
            assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
            page.evaluate('window.scrollTo({top:0,behavior:"instant"})')
            page.screenshot(path='/tmp/chabom-detail-mobile.png', full_page=True)

            # 링크 조회 → 보관함 저장 → 상세 화면
            page.get_by_role('link', name='매물 확인', exact=True).click()
            page.get_by_label('매물 링크', exact=True).fill('https://bobaedream.example.com/car/789')
            page.get_by_role('button', name='차량 불러오기', exact=False).click()
            expect(page.get_by_role('heading', name='현대 아반떼')).to_be_visible()
            page.get_by_role('link', name='매물 확인', exact=True).click()
            page.get_by_role('button', name='직접 입력', exact=False).click()
            page.get_by_label('모델', exact=True).fill('테스트 수동 차량')
            page.get_by_role('button', name='저장하고 검증하기').click()
            expect(page.get_by_role('heading', name='구매 보류 · 추가 확인 필요')).to_be_visible()
            page.get_by_role('link', name='딜러 관리', exact=False).click()
            page.get_by_role('button', name='딜러 추가', exact=False).click()
            page.get_by_label('사이트별 딜러 ID', exact=True).fill('new-dealer')
            page.get_by_label('딜러 이름', exact=True).fill('새 딜러')
            page.locator('#dialogSubmit').click()
            expect(page.locator('dialog')).not_to_be_visible()
            page.get_by_role('button', name='찜 딜러1', exact=True).click()
            expect(page.locator('.dealer-card')).to_have_count(1)
            expect(page.get_by_text('새 딜러', exact=True)).to_be_visible()
            assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
            page.get_by_role('link', name='차량 보관함', exact=False).last.click()
            page.get_by_role('button', name='필터 해제 ×').click()
            expect(page.locator('.car-row')).to_have_count(4)
            page.evaluate('window.scrollTo({top:0,behavior:"instant"})')
            page.screenshot(path='/tmp/chabom-garage-mobile.png', full_page=True)
            page.set_viewport_size({'width': 320, 'height': 740})
            assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')

            # 서버 오류와 조회 실패는 화면에 그대로 안내한다.
            page.route('**/api/lookup', lambda route: route.fulfill(status=429, content_type='application/json',
                                                                    body='{"detail":"오늘 링크 조회 한도를 모두 썼습니다."}'))
            page.get_by_role('link', name='매물 확인', exact=True).click()
            page.get_by_label('매물 링크', exact=True).fill('https://example.com/unavailable')
            page.get_by_role('button', name='차량 불러오기', exact=False).click()
            expect(page.locator('#lookupMessage')).to_contain_text('한도')
            expect(page.get_by_role('button', name='차량 불러오기', exact=False)).to_be_enabled()
            page.unroute('**/api/lookup')

            # 다른 사용자는 이 사용자의 보관함을 볼 수 없다(로그아웃 후 다른 계정).
            page.get_by_role('button', name='로그아웃').first.click()
            expect(page.get_by_role('button', name='Google로 계속하기')).to_be_visible()
            assert not errors, errors
            browser.close()
            print('PASS: allowlist gate, login, vehicle favorites/search/sort, dealer favorites/add/blacklist/reload/unblock, '
                  'blacklist gates verification, saved data preservation, link lookup, manual entry, lookup limit message, '
                  'logout, desktop and mobile, no JS errors')
    finally:
        service.should_exit = True
        thread.join(timeout=5)


if __name__ == '__main__':
    main()

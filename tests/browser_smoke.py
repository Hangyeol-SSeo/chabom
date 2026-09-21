"""브라우저 통합 확인: PYTHONPATH=. .venv/bin/python tests/browser_smoke.py
임시 DB와 8765 포트를 사용하며 실제 차량 사이트를 조회하지 않습니다.
"""
import tempfile, threading, time
from pathlib import Path
from contextlib import closing
import uvicorn
import server
from storage import history, dealers
from playwright.sync_api import sync_playwright, expect

with tempfile.TemporaryDirectory() as directory:
    server.HISTORY_DB_PATH=Path(directory)/'history.db'
    server.DEALERS_DB_PATH=Path(directory)/'dealers.db'
    sample={'source':'encar','listing_id':'123','url':'https://example.com/car','vehicle':{'make':'기아','model':'더 뉴 모닝','model_year':2021,'mileage_km':32000,'price_krw':8900000,'region':'서울'},'dealer':{'dealer_id':'dealer-1','display_name':'테스트판매자A','phone':'01000000000','region':'서울 강남'},'insurance_history':{'own_damage_claims':[{'amount_krw':5000000}]}}
    with closing(history.get_connection(server.HISTORY_DB_PATH)) as conn:
        history.record(conn,sample['url'],source='encar',listing=sample)
        history.record(conn,'https://example.com/car2',source='kcar',listing={'source':'kcar','listing_id':'456','vehicle':{'make':'현대','model':'캐스퍼','model_year':2023,'price_krw':15000000},'dealer':{'dealer_id':'dealer-2','display_name':'테스트판매자B'}})
        history.record(conn,'https://example.com/failed',status='failed',reason='조회 실패')
    service=uvicorn.Server(uvicorn.Config(server.app,host='127.0.0.1',port=8765,log_level='error'))
    thread=threading.Thread(target=service.run,daemon=True);thread.start()
    try:
        for _ in range(100):
            if service.started:break
            time.sleep(.05)
        with sync_playwright() as p:
            browser=p.chromium.launch()
            page=browser.new_page(viewport={'width':1440,'height':1050})
            errors=[];page.on('pageerror',lambda error:errors.append(str(error)))
            page.goto('http://127.0.0.1:8765')
            expect(page.locator('.car-row')).to_have_count(3)
            page.get_by_role('button',name='기아 더 뉴 모닝 찜하기',exact=True).click()
            page.get_by_role('button',name='찜한 매물1',exact=True).click()
            expect(page.locator('.car-row')).to_have_count(1)
            page.reload();expect(page.locator('.car-row')).to_have_count(3)
            expect(page.get_by_role('button',name='기아 더 뉴 모닝 찜 해제',exact=True)).to_be_visible()
            page.get_by_label('차량 검색').fill('캐스퍼');expect(page.locator('.car-row')).to_have_count(1)
            page.get_by_label('차량 검색').fill('');page.get_by_label('차량 정렬').select_option('price-high')
            expect(page.locator('.car-title').first).to_have_text('현대 캐스퍼')
            page.screenshot(path='/tmp/chabom-garage-desktop.png',full_page=True)
            page.get_by_role('button',name='기아 더 뉴 모닝',exact=True).click()
            page.get_by_role('button',name='딜러 찜하기',exact=True).click()
            expect(page.get_by_role('button',name='찜 해제',exact=True)).to_be_visible()
            page.get_by_role('link',name='딜러 관리',exact=False).click()
            expect(page.locator('.dealer-card')).to_have_count(1)
            page.get_by_role('button',name='제외 등록',exact=True).click()
            page.get_by_label('제외 사유').fill('성능기록부와 실제 상태가 다름')
            page.locator('#dialogSubmit').click()
            expect(page.locator('dialog')).not_to_be_visible()
            expect(page.locator('.dealer-card')).to_have_count(0)
            page.get_by_role('button',name='블랙리스트1',exact=True).click()
            expect(page.locator('.dealer-card')).to_have_count(1)
            expect(page.get_by_text('성능기록부와 실제 상태가 다름',exact=False)).to_be_visible()
            page.screenshot(path='/tmp/chabom-dealers-desktop.png',full_page=True)
            page.reload()
            page.get_by_role('button',name='블랙리스트1',exact=True).click()
            expect(page.locator('.dealer-card')).to_have_count(1)
            page.get_by_role('button',name='보관한 차량 1대 보기').click()
            expect(page.locator('.car-row')).to_have_count(1)
            page.get_by_role('button',name='기아 더 뉴 모닝',exact=True).click()
            page.get_by_role('button',name='저장하고 검증하기').click()
            expect(page.get_by_role('heading',name='구매 보류 · 추가 확인 필요')).to_be_visible()
            expect(page.locator('.result-item').filter(has_text='딜러 블랙리스트 대조')).to_contain_text('결격')
            with closing(history.get_connection(server.HISTORY_DB_PATH)) as conn:
                saved=next(i for i in history.list_history(conn) if i['url']==sample['url'])
                assert saved['listing']['insurance_history']['own_damage_claims'][0]['amount_krw']==5000000
            page.get_by_role('button',name='블랙리스트 해제',exact=True).click()
            expect(page.get_by_role('button',name='딜러 찜하기',exact=True)).to_be_visible()
            page.set_viewport_size({'width':390,'height':844})
            assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
            page.evaluate('window.scrollTo({top:0,behavior:"instant"})')
            page.screenshot(path='/tmp/chabom-detail-mobile.png',full_page=True)
            page.get_by_role('link',name='매물 확인',exact=True).click()
            expect(page.get_by_label('매물 링크',exact=True)).to_be_visible()
            page.get_by_role('button',name='직접 입력',exact=False).click()
            page.get_by_label('모델',exact=True).fill('테스트 수동 차량')
            page.get_by_role('button',name='저장하고 검증하기').click()
            expect(page.get_by_role('heading',name='구매 보류 · 추가 확인 필요')).to_be_visible()
            page.get_by_role('link',name='딜러 관리',exact=False).click()
            page.get_by_role('button',name='딜러 추가',exact=False).click()
            page.get_by_label('사이트별 딜러 ID',exact=True).fill('new-dealer')
            page.get_by_label('딜러 이름',exact=True).fill('새 딜러')
            page.locator('#dialogSubmit').click()
            expect(page.locator('dialog')).not_to_be_visible()
            page.get_by_role('button',name='찜 딜러1',exact=True).click()
            expect(page.locator('.dealer-card')).to_have_count(1)
            expect(page.get_by_text('새 딜러',exact=True)).to_be_visible()
            assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
            page.get_by_role('link',name='차량 보관함',exact=False).last.click()
            expect(page.get_by_role('heading',name='차량 보관함',exact=True)).to_be_visible()
            page.evaluate('window.scrollTo({top:0,behavior:"instant"})')
            page.screenshot(path='/tmp/chabom-garage-mobile.png',full_page=True)
            assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
            page.set_viewport_size({'width':320,'height':740})
            assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
            page.route('**/api/history/*/favorite',lambda route:route.fulfill(status=500,content_type='application/json',body='{"detail":"저장 실패 테스트"}'))
            page.get_by_role('button',name='기아 더 뉴 모닝 찜 해제',exact=True).click()
            expect(page.locator('#toast')).to_have_text('저장 실패 테스트')
            expect(page.get_by_role('button',name='기아 더 뉴 모닝 찜 해제',exact=True)).to_be_enabled()
            page.unroute('**/api/history/*/favorite')
            page.get_by_role('link',name='매물 확인',exact=True).click()
            page.route('**/api/lookup',lambda route:route.fulfill(status=200,content_type='application/json',body='{"ok":false,"reason":"일시적인 조회 실패"}'))
            page.get_by_label('매물 링크',exact=True).fill('https://example.com/unavailable')
            page.get_by_role('button',name='차량 불러오기',exact=False).click()
            expect(page.locator('#lookupMessage')).to_contain_text('일시적인 조회 실패')
            expect(page.get_by_role('button',name='차량 불러오기',exact=False)).to_be_enabled()
            page.unroute('**/api/lookup')
            assert not errors,errors
            browser.close()
            print('PASS: vehicle favorites/search/sort, dealer favorites/add/blacklist/reload/unblock, blacklist gates verification, saved data preservation, same-tab navigation, manual entry, desktop and mobile, no JS errors')
    finally:
        service.should_exit=True;thread.join(timeout=5)

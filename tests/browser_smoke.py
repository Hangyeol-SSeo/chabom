"""브라우저 통합 확인: PYTHONPATH=. .venv/bin/python tests/browser_smoke.py
임시 DB와 8765 포트를 사용하며 실제 차량 사이트를 조회하지 않습니다.
"""
import logging, tempfile, threading, time
from pathlib import Path
from contextlib import closing
import uvicorn
import server
from storage import history, dealers
from playwright.sync_api import sync_playwright, expect
from crawler.browser_fetch import BrowserFetcher
from normalizer.schema import Listing

class LocalPageAdapter:
    """실제 브라우저 세션을 열되 사이트 대신 data: 페이지만 읽는 조회 어댑터."""
    def __init__(self):
        self.fetcher=BrowserFetcher()
    def parse_detail(self,url):
        if self.fetcher.get_html('data:text/html,<title>ok</title>') is None:
            raise RuntimeError('브라우저 세션을 열지 못했습니다')
        return Listing(listing_id='session-check',source='manual',url=url)

class ServerErrors(logging.Handler):
    def __init__(self):
        super().__init__(logging.ERROR);self.messages=[]
    def emit(self,record):
        self.messages.append(record.getMessage())

with tempfile.TemporaryDirectory() as directory:
    server.HISTORY_DB_PATH=Path(directory)/'history.db'
    server.DEALERS_DB_PATH=Path(directory)/'dealers.db'
    sample={'source':'encar','listing_id':'123','url':'https://example.com/car','vehicle':{'make':'기아','model':'더 뉴 모닝','model_year':2021,'mileage_km':32000,'price_krw':8900000,'region':'서울'},'dealer':{'dealer_id':'dealer-1','display_name':'테스트판매자A','phone':'01000000000','region':'서울 강남'},'insurance_history':{'own_damage_claims':[{'amount_krw':5000000}],'owner_change_count':1,'number_change_count':1,'usage_change_count':1,'history_detail_status':'available','coverage_verified':True,'history_warnings':{'자차 보험 미가입 기간':'없음'},'history_events':[{'category':'소유자변경','date':'2021-03-14','summary':'당사자 거래이전','details':{'변경일자':'2021년 03월 14일'}},{'category':'차량번호변경','date':'2022-07-01','summary':'번호 변경','details':{}}]},'performance_record':{'record_available':True,'record_url':'https://example.com/record/123','panel_exchange':['프론트 휀더(우) · 교환'],'third_party_inspection':{'frame_ok':True}}}
    with closing(history.get_connection(server.HISTORY_DB_PATH)) as conn:
        history.record(conn,sample['url'],source='encar',listing=sample)
        history.record(conn,'https://example.com/car2',source='kcar',listing={'source':'kcar','listing_id':'456','url':'https://example.com/car2','vehicle':{'make':'현대','model':'캐스퍼','model_year':2023,'price_krw':15000000},'dealer':{'dealer_id':'dealer-2','display_name':'테스트판매자B'},'insurance_history':{'history_detail_status':'available','coverage_verified':True,'owner_change_count':2,'number_change_count':0,'history_events':[{'category':'소유자 변경','date':'2022-05-06','summary':'합성 거래','details':{}}]},'performance_record':{'record_available':True,'record_images':['https://images.example.com/test-record.jpg'],'panel_exchange_count':2,'panel_exchange':['외판 교환 2건(케이카 진단)'],'third_party_inspection':{'frame_ok':True}}})
        history.record(conn,'https://example.com/failed',status='failed',reason='조회 실패')
    server_errors=ServerErrors();logging.getLogger('uvicorn.error').addHandler(server_errors)
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
            auth={'status':'none'}
            def mock_encar_session(route):
                suffix=route.request.url.rsplit('/',1)[-1]
                if suffix=='start':auth['status']='waiting'
                elif suffix=='cancel':auth['status']='saved'
                route.fulfill(status=200,content_type='application/json',body='{"status":"'+auth['status']+'"}')
            page.route('**/api/encar/session',mock_encar_session)
            page.route('**/api/encar/session/**',mock_encar_session)
            kcar_auth={'status':'none'}
            def mock_kcar_session(route):
                suffix=route.request.url.rsplit('/',1)[-1]
                if suffix=='start':kcar_auth['status']='waiting'
                elif suffix=='cancel':kcar_auth['status']='saved'
                route.fulfill(status=200,content_type='application/json',body='{"status":"'+kcar_auth['status']+'"}')
            page.route('**/api/kcar/session',mock_kcar_session)
            page.route('**/api/kcar/session/**',mock_kcar_session)
            page.goto('http://127.0.0.1:8765')
            expect(page.locator('.car-row')).to_have_count(3)
            # 목록에서 누르지 않아도 판정이 보인다. 외판 교환은 결격이 아니라 주의다.
            flagged=page.locator('.car-row').filter(has_text='기아 더 뉴 모닝')
            expect(flagged.locator('.car-flag.caution')).to_contain_text('프론트 휀더(우) · 교환')
            expect(flagged.locator('.car-flag.fail')).to_have_count(0)
            expect(page.get_by_role('heading',name='엔카 사이트 연결')).to_be_visible()
            page.get_by_role('button',name='엔카 사이트 열기').click()
            expect(page.get_by_text('엔카 창 열림')).to_be_visible()
            page.get_by_role('button',name='엔카 창 닫기').click()
            expect(page.get_by_text('이전 세션 있음')).to_be_visible()
            expect(page.get_by_role('heading',name='케이카 사이트 연결')).to_be_visible()
            page.get_by_role('button',name='케이카 사이트 열기').click()
            expect(page.get_by_text('케이카 창 열림')).to_be_visible()
            page.get_by_role('button',name='케이카 창 닫기').click()
            page.get_by_role('link',name='매물 확인',exact=True).click()
            expect(page.get_by_role('heading',name='엔카 사이트 연결')).to_be_visible()
            expect(page.get_by_role('heading',name='케이카 사이트 연결')).to_be_visible()
            page.get_by_role('link',name='차량 보관함',exact=False).last.click()
            page.get_by_role('button',name='기아 더 뉴 모닝 찜하기',exact=True).click()
            page.get_by_role('button',name='찜한 매물1',exact=True).click()
            expect(page.locator('.car-row')).to_have_count(1)
            page.reload();expect(page.locator('.car-row')).to_have_count(3)
            expect(page.get_by_role('button',name='기아 더 뉴 모닝 찜 해제',exact=True)).to_be_visible()
            page.get_by_label('차량 검색').fill('캐스퍼');expect(page.locator('.car-row')).to_have_count(1)
            page.get_by_label('차량 검색').fill('');page.get_by_label('차량 정렬').select_option('price-high')
            expect(page.locator('.car-title').first).to_have_text('현대 캐스퍼')
            page.get_by_role('button',name='현대 캐스퍼',exact=True).click()
            expect(page.get_by_role('heading',name='보험·차량 상세 이력')).to_be_visible()
            expect(page.get_by_text('2022-05-06')).to_be_visible()
            expect(page.get_by_text('원본 1쪽 열기')).to_be_visible()
            expect(page.locator('.evidence-metrics').filter(has_text='외판 교환')).to_contain_text('2건')
            page.get_by_role('link',name='차량 보관함',exact=False).last.click()
            page.screenshot(path='/tmp/chabom-garage-desktop.png',full_page=True)
            page.get_by_role('button',name='기아 더 뉴 모닝',exact=True).click()
            expect(page.get_by_role('heading',name='보험·차량 상세 이력')).to_be_visible()
            expect(page.get_by_text('2021-03-14')).to_be_visible()
            expect(page.locator('.evidence-list').get_by_text('프론트 휀더(우) · 교환')).to_be_visible()
            # 검증 버튼을 누르기 전에도 저장된 정보 기준 판정이 펼쳐져 있다.
            expect(page.locator('#verificationResult .result-item').filter(has_text='외판(패널) 교환·판금')).to_contain_text('주의')
            expect(page.locator('#verificationResult .result-item').filter(has_text='외판(패널) 교환·판금').locator('p')).to_be_visible()
            expect(page.locator('#vehicleForm')).to_be_visible()
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
            expect(page.locator('.car-flag.fail')).to_contain_text('딜러 블랙리스트 대조')
            page.get_by_role('button',name='기아 더 뉴 모닝',exact=True).click()
            expect(page.get_by_role('heading',name='결격 사유 1건')).to_be_visible()
            expect(page.locator('.result-item').filter(has_text='딜러 블랙리스트 대조').locator('p')).to_contain_text('성능기록부와 실제 상태가 다름')
            page.get_by_role('button',name='저장하고 검증하기').click()
            expect(page.get_by_role('heading',name='결격 사유 1건')).to_be_visible()
            expect(page.get_by_role('button',name='입력 내용 수정')).to_be_visible()
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
            # 브라우저 세션은 조회마다 열고 닫는다: 사이트를 바꿔 이어서 조회해도, 동시에 조회해도 실패하지 않는다.
            server._SOURCE_HOSTS={'first.example.com':('bobaedream',True),'second.example.com':('encar',True)}
            server._ADAPTERS['bobaedream']=server._ADAPTERS['encar']=LocalPageAdapter
            lookup_all="urls=>Promise.all(urls.map(url=>fetch('/api/lookup',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({url})}).then(r=>r.json())))"
            for url in ('https://first.example.com/car','https://second.example.com/car','https://first.example.com/car'):
                result=page.evaluate(lookup_all,[url])[0];assert result['ok'],result
            results=page.evaluate(lookup_all,['https://first.example.com/car?no='+str(i) for i in range(4)])
            assert all(result['ok'] for result in results),results
            assert not errors,errors
            browser.close()
    finally:
        service.should_exit=True;thread.join(timeout=5)
    assert not thread.is_alive() and not server_errors.messages,server_errors.messages
    print('PASS: vehicle favorites/search/sort, dealer favorites/add/blacklist/reload/unblock, blacklist gates verification, saved data preservation, same-tab navigation, manual entry, desktop and mobile, no JS errors, per-lookup browser sessions, clean shutdown')

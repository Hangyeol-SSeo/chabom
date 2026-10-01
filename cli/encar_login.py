"""Encar 상세 이력 조회용 브라우저 세션을 사용자가 직접 연결한다.

실행: python -m cli.encar_login
로그인 정보는 화면에 입력하지 않으며, 브라우저 세션 상태만 로컬 data/에 보관한다.
"""
from __future__ import annotations

import json
import os
import re

from playwright.sync_api import sync_playwright

from crawler.adapters.encar_detail_adapter import AUTH_STATE_PATH
from crawler.browser_fetch import _DEFAULT_USER_AGENT


def main() -> int:
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=False)
        context = browser.new_context(locale="ko-KR", user_agent=_DEFAULT_USER_AGENT)
        page = context.new_page()
        try:
            page.goto("https://fem.encar.com/login", wait_until="domcontentloaded")
            print("열린 Encar 창에서 직접 로그인하신 뒤 이 터미널에서 Enter를 누르세요.")
            input()
            page.goto("https://fem.encar.com/", wait_until="domcontentloaded")
            try:
                page.get_by_role("button", name=re.compile("로그아웃")).wait_for(timeout=10000)
            except Exception:
                print("로그인 완료를 확인하지 못해 세션을 저장하지 않았습니다.")
                return 1
            AUTH_STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
            descriptor = os.open(AUTH_STATE_PATH, os.O_CREAT | os.O_WRONLY | os.O_TRUNC, 0o600)
            with os.fdopen(descriptor, "w", encoding="utf-8") as output:
                json.dump(context.storage_state(), output, ensure_ascii=False)
            os.chmod(AUTH_STATE_PATH, 0o600)
            print("Encar 연결을 저장했습니다. 차봄에서 해당 매물을 다시 불러오세요.")
            return 0
        finally:
            browser.close()


if __name__ == "__main__":
    raise SystemExit(main())

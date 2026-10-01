"""Firebase ID 토큰 검증, 허용 이메일 목록, 사용자별 일일 조회 한도.

웹 버전은 허용 목록(Firestore `allowlist/{email}`)에 등록된 Google·카카오 계정만 받는다.
같은 규칙이 firestore.rules의 `allowed()`에도 있다 — 브라우저가 Firestore에 직접 접근할 때는
보안 규칙이, 이 API를 부를 때는 이 모듈이 막는다. 둘 중 하나를 바꾸면 다른 쪽도 함께 바꿔야 한다.

테스트는 `server.gateway`를 가짜 객체로 바꿔 Firebase 없이 돈다(tests/test_server_api.py).
"""
from __future__ import annotations

import logging
import os
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Protocol

from fastapi import Header, HTTPException

logger = logging.getLogger(__name__)

# 카카오는 Identity Platform의 OIDC 공급자로 붙인다(docs/deploy.md). 공급자 ID를 바꾸면
# web/auth.js와 firestore.rules도 같이 바꾼다.
KAKAO_PROVIDER = 'oidc.kakao'
GOOGLE_PROVIDER = 'google.com'
KST = timezone(timedelta(hours=9))


@dataclass(frozen=True)
class User:
    uid: str
    email: str


class Gateway(Protocol):
    def verify_token(self, token: str) -> dict: ...
    def is_allowed(self, email: str) -> bool: ...
    def consume_lookup(self, uid: str, limit: int) -> bool: ...


def user_from_claims(claims: dict) -> User:
    """토큰 클레임에서 사용자를 꺼낸다. 허용 목록 대조 전 단계의 형식 검사만 한다."""
    provider = (claims.get('firebase') or {}).get('sign_in_provider')
    email = (claims.get('email') or '').strip().lower()
    if provider not in (GOOGLE_PROVIDER, KAKAO_PROVIDER):
        raise HTTPException(status_code=403, detail='Google 또는 카카오 계정으로 로그인해주세요.')
    if not email:
        raise HTTPException(status_code=403, detail='계정 이메일을 확인할 수 없습니다. 카카오 로그인 시 이메일 제공에 동의해주세요.')
    if provider == GOOGLE_PROVIDER and claims.get('email_verified') is not True:
        raise HTTPException(status_code=403, detail='이메일 인증이 완료된 Google 계정만 사용할 수 있습니다.')
    return User(uid=claims['uid'], email=email)


class FirebaseGateway:
    """firebase-admin으로 토큰을 검증하고 Firestore에서 허용 목록·사용량을 읽고 쓴다.

    Cloud Run에서는 서비스 계정의 기본 자격 증명을 쓴다. 로컬에서 에뮬레이터를 쓸 때는
    FIREBASE_AUTH_EMULATOR_HOST / FIRESTORE_EMULATOR_HOST와 GOOGLE_CLOUD_PROJECT를 설정한다.
    """

    _ALLOW_CACHE_SEC = 60.0

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._app = None
        self._db = None
        self._allow_cache: dict[str, tuple[bool, float]] = {}

    def _ensure(self):
        with self._lock:
            if self._app is None:
                import firebase_admin

                project = os.environ.get('GOOGLE_CLOUD_PROJECT') or os.environ.get('FIREBASE_PROJECT_ID')
                options = {'projectId': project} if project else None
                if os.environ.get('FIREBASE_AUTH_EMULATOR_HOST') or os.environ.get('FIRESTORE_EMULATOR_HOST'):
                    # 에뮬레이터는 자격 증명을 보지 않지만 firebase-admin은 초기화 때 하나를 요구한다.
                    from google.auth.credentials import AnonymousCredentials

                    class _EmulatorCredential(firebase_admin.credentials.Base):
                        def get_credential(self):
                            return AnonymousCredentials()

                    self._app = firebase_admin.initialize_app(_EmulatorCredential(), options=options)
                else:
                    self._app = firebase_admin.initialize_app(options=options)
            return self._app

    def firestore_client(self):
        self._ensure()
        with self._lock:
            if self._db is None:
                from firebase_admin import firestore

                self._db = firestore.client(self._app)
            return self._db

    def verify_token(self, token: str) -> dict:
        from firebase_admin import auth

        try:
            return auth.verify_id_token(token, app=self._ensure())
        except (ValueError, auth.InvalidIdTokenError, auth.ExpiredIdTokenError, auth.RevokedIdTokenError,
                auth.CertificateFetchError, auth.UserDisabledError) as exc:
            logger.info('ID 토큰 검증 실패: %s', exc)
            raise HTTPException(status_code=401, detail='로그인이 만료되었습니다. 다시 로그인해주세요.') from exc

    def is_allowed(self, email: str) -> bool:
        now = time.monotonic()
        cached = self._allow_cache.get(email)
        if cached and now - cached[1] < self._ALLOW_CACHE_SEC:
            return cached[0]
        allowed = self.firestore_client().collection('allowlist').document(email).get().exists
        self._allow_cache[email] = (allowed, now)
        return allowed

    def consume_lookup(self, uid: str, limit: int) -> bool:
        from firebase_admin import firestore

        db = self.firestore_client()
        day = datetime.now(KST).strftime('%Y%m%d')
        ref = db.collection('usage').document(f'{uid}_{day}')

        @firestore.transactional
        def _consume(transaction) -> bool:
            snapshot = ref.get(transaction=transaction)
            count = (snapshot.to_dict() or {}).get('lookups', 0) if snapshot.exists else 0
            if count >= limit:
                return False
            transaction.set(ref, {'uid': uid, 'day': day, 'lookups': count + 1,
                                  'updated_at': firestore.SERVER_TIMESTAMP})
            return True

        return _consume(db.transaction())


def bearer_token(authorization: str | None = Header(default=None)) -> str:
    scheme, _, token = (authorization or '').partition(' ')
    if scheme.lower() != 'bearer' or not token.strip():
        raise HTTPException(status_code=401, detail='로그인이 필요합니다.')
    return token.strip()


def authorize(gateway: Gateway, token: str) -> User:
    user = user_from_claims(gateway.verify_token(token))
    if not gateway.is_allowed(user.email):
        raise HTTPException(status_code=403, detail=f'{user.email} 계정은 아직 사용 승인이 되지 않았습니다.')
    return user

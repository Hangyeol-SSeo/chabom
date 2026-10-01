"""허용 이메일 목록 관리 (Firestore `allowlist/{소문자 이메일}`).

    gcloud auth application-default login        # 최초 1회
    python -m scripts.allowlist --project <프로젝트 ID> add someone@gmail.com --note "가족"
    python -m scripts.allowlist --project <프로젝트 ID> remove someone@gmail.com
    python -m scripts.allowlist --project <프로젝트 ID> list

허용 목록 문서는 보안 규칙상 브라우저에서 쓸 수 없고, 이 스크립트(관리자 자격 증명)로만 바꾼다.
에뮬레이터에 쓰려면 FIRESTORE_EMULATOR_HOST=127.0.0.1:8080과 --project demo-chabom을 쓴다.
"""
from __future__ import annotations

import argparse
import os
from datetime import datetime, timezone


def _db(project: str):
    os.environ['GOOGLE_CLOUD_PROJECT'] = project
    from api.auth import FirebaseGateway

    return FirebaseGateway().firestore_client()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description='차봄 허용 이메일 목록 관리')
    parser.add_argument('--project', required=True, help='Firebase(GCP) 프로젝트 ID')
    sub = parser.add_subparsers(dest='command', required=True)
    add = sub.add_parser('add', help='이메일 허용')
    add.add_argument('email')
    add.add_argument('--note', default='', help='누구인지 메모')
    remove = sub.add_parser('remove', help='허용 취소')
    remove.add_argument('email')
    sub.add_parser('list', help='허용된 이메일 보기')
    args = parser.parse_args(argv)

    collection = _db(args.project).collection('allowlist')
    if args.command == 'list':
        for snapshot in collection.stream():
            data = snapshot.to_dict() or {}
            print(f"{snapshot.id}\t{data.get('note', '')}\t{data.get('added_at', '')}")
        return 0
    email = args.email.strip().lower()
    if '@' not in email:
        parser.error(f'이메일 형식이 아닙니다: {args.email}')
    if args.command == 'add':
        collection.document(email).set({'note': args.note, 'added_at': datetime.now(timezone.utc).isoformat()})
        print(f'허용했습니다: {email}')
    else:
        collection.document(email).delete()
        print(f'허용을 취소했습니다: {email} (이미 로그인한 화면은 새로고침하면 막힙니다)')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())

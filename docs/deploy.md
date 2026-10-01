# 차봄 웹 배포 가이드

차봄을 인터넷에 올려 허용한 사람들이 각자 로그인해 쓰도록 만드는 절차입니다. 비용 0원을 목표로
모든 구성 요소를 무료 한도 안에 둡니다. 처음 한 번은 아래 순서대로 30분~1시간 정도 걸립니다.

## 구성

```
브라우저 ── https://<프로젝트 ID>.web.app (Firebase Hosting, web/)
  ├─ Firebase Auth: Google, 카카오(OIDC) 로그인 → ID 토큰
  ├─ Firestore(서울): users/{uid}/history, users/{uid}/dealers  ← 브라우저가 직접 읽고 씀(firestore.rules)
  │                   allowlist/{이메일}, usage/{uid}_{날짜}      ← 관리 스크립트·API 서버만 씀
  └─ /api/** ── Hosting rewrite ──▶ Cloud Run "chabom-api"(서울, server.py + Playwright)
```

| 구성 요소 | 무료 한도(2026-10 기준, 바뀔 수 있음) | 차봄 사용량 |
|---|---|---|
| Firebase Hosting | 저장 10GB, 전송 360MB/일 | 정적 파일 수백 KB |
| Firebase Auth(Identity Platform) | Google 등 월 5만 명, **OIDC(카카오)는 월 50명** | 소수 사용자 |
| Firestore | 읽기 5만·쓰기 2만/일, 저장 1GiB | 사용자당 하루 수백 건 이하 |
| Cloud Run | 월 18만 vCPU초, 36만 GiB초, 요청 200만 | 조회 1건 10~30초, 요청이 있을 때만 과금 |
| Cloud Build | 월 2,500 빌드분 | 배포할 때만 |
| Artifact Registry | 0.5GB | 이미지 1개만 유지(정리 정책) |

Cloud Run을 쓰려면 결제 계정(Blaze 요금제) 등록이 필요합니다. 위 한도 안이면 청구액은 0원이고,
안전장치로 최대 인스턴스 2개 제한과 예산 알림을 둡니다. 이미지 저장소가 0.5GB를 조금 넘거나
배포용 소스 보관 버킷이 생기면 **월 수십 원 수준**이 청구될 수 있습니다.

## 준비물

- Google 계정, 결제 수단(카드). 무료 한도 안에서는 청구되지 않습니다.
- 카카오 계정(카카오 로그인을 쓸 경우)
- 로컬 도구: Node 20+, Java 11+(에뮬레이터용), Python 3.11+, [gcloud CLI](https://cloud.google.com/sdk/docs/install)
- 저장소 루트에서 `npm install`, `python3 -m venv .venv && ./.venv/bin/pip install -r requirements-dev.txt`

## 1. Firebase 프로젝트 만들기

1. [Firebase 콘솔](https://console.firebase.google.com)에서 프로젝트를 만듭니다. Google 애널리틱스는 꺼도 됩니다.
   프로젝트 ID가 기본 도메인이 됩니다(`<프로젝트 ID>.web.app`).
2. 프로젝트 설정 → 사용량 및 결제 → **Blaze 요금제**로 전환합니다.
3. [Google Cloud 결제 → 예산 및 알림](https://console.cloud.google.com/billing/budgets)에서 예산을 만듭니다.
   예: 금액 ₩1,000, 50%·90%·100% 알림. 알림은 청구를 막지 않으므로 메일이 오면 바로 확인합니다.
4. 웹 앱을 하나 등록합니다(프로젝트 설정 → 내 앱 → 웹). Hosting이 설정을 자동으로 내주므로
   표시되는 설정값을 코드에 붙여 넣을 필요는 없습니다.
5. 로컬에서 프로젝트를 연결합니다.

   ```bash
   npx firebase login
   cp .firebaserc.example .firebaserc      # your-firebase-project-id를 실제 ID로 바꾼다
   gcloud auth login
   gcloud auth application-default login   # 관리 스크립트(scripts/*.py)가 쓰는 자격 증명
   gcloud config set project <프로젝트 ID>
   ```

## 2. Firestore 만들기

Firebase 콘솔 → Firestore Database → 데이터베이스 만들기.

- 위치: **asia-northeast3(서울)**. 만든 뒤에는 바꿀 수 없습니다.
- 버전: Standard. 시작 모드는 아무것이나 고릅니다. 4단계에서 `firestore.rules`로 덮어씁니다.

## 3. 로그인 설정

### Google

1. Authentication → 시작하기 → Sign-in method → **Google** 사용 설정. 지원 이메일을 고릅니다.
2. Authentication → 설정 → 승인된 도메인에 `<프로젝트 ID>.web.app`과 `<프로젝트 ID>.firebaseapp.com`이
   있는지 확인합니다(기본으로 들어 있습니다).

### 카카오 (OpenID Connect)

Firebase는 카카오를 기본 공급자로 제공하지 않아 **Identity Platform의 OIDC 공급자**로 붙입니다.
카카오 개발자 콘솔의 메뉴 이름은 바뀔 수 있으니 같은 뜻의 항목을 찾아 설정하세요.

1. Authentication → 설정 → **Identity Platform으로 업그레이드**합니다(Blaze에서 무료 한도 적용).
   OIDC 로그인 사용자는 월 50명까지 무료입니다.
2. [카카오 개발자 콘솔](https://developers.kakao.com)에서 애플리케이션을 만듭니다.
   - **카카오 로그인**을 켜고, **OpenID Connect**도 켭니다.
   - **Redirect URI**에 `https://<프로젝트 ID>.firebaseapp.com/__/auth/handler`를 등록합니다.
   - **동의 항목**에서 **카카오계정(이메일)**을 필수 또는 선택 동의로 설정합니다. 이메일 항목은
     비즈 앱 전환(개인 개발자도 무료로 가능)이 필요할 수 있습니다. 이메일을 받지 못하면 허용 목록과
     대조할 수 없어 로그인 후 "이메일을 확인할 수 없습니다" 안내가 나옵니다.
   - **보안 → Client Secret**을 만들고 사용함으로 설정합니다.
   - 앱 키 중 **REST API 키**를 확인합니다.
3. Firebase 콘솔 → Authentication → Sign-in method → 새 공급업체 추가 → **OpenID Connect**:
   - 이름: `kakao` (공급자 ID가 **`oidc.kakao`**가 되어야 합니다. 코드가 이 ID를 씁니다.)
   - 클라이언트 ID: 카카오 REST API 키
   - 발급자(Issuer): `https://kauth.kakao.com`
   - 클라이언트 보안 비밀번호: 카카오 Client Secret
   - 부여 유형: **코드 흐름(Code flow)**

## 4. API 서버(Cloud Run) 배포

```bash
deploy/cloudrun.sh <프로젝트 ID>
```

스크립트가 하는 일:

- Cloud Run·Cloud Build·Artifact Registry·Firestore API를 켭니다.
- 전용 서비스 계정 `chabom-api@`을 만들고 Firestore 읽기·쓰기 권한(`roles/datastore.user`)만 줍니다.
- 서울 리전에 배포합니다: 메모리 1GiB, 요청 시에만 CPU 사용, 최소 0·최대 2 인스턴스, 타임아웃 120초.
  Hosting rewrite가 호출할 수 있도록 Cloud Run 자체는 공개로 두고, 인증은 서버가 ID 토큰으로 직접 검사합니다.
- 이미지 저장소에 최신 이미지만 남기는 정리 정책(`deploy/artifact-cleanup-policy.json`)을 겁니다.

일일 조회 한도는 `LOOKUP_DAILY_LIMIT=50 deploy/cloudrun.sh <프로젝트 ID>`처럼 바꿉니다(기본 30회).

## 5. 화면과 보안 규칙 배포

```bash
npm run deploy:web     # firebase deploy --only hosting,firestore
```

`firebase.json`의 rewrite가 `/api/**`를 Cloud Run `chabom-api`(asia-northeast3)로 보냅니다.
배포 시 리전 관련 오류가 나면 Hosting이 지원하는 리전인지 확인하고, 필요하면 `REGION=asia-northeast1`로
Cloud Run을 다시 배포한 뒤 `firebase.json`의 `region`도 같이 바꿉니다.

## 6. 사용자 허용

```bash
./.venv/bin/python -m scripts.allowlist --project <프로젝트 ID> add me@gmail.com --note "본인"
./.venv/bin/python -m scripts.allowlist --project <프로젝트 ID> list
./.venv/bin/python -m scripts.allowlist --project <프로젝트 ID> remove someone@gmail.com
```

허용되지 않은 계정은 로그인 후 "사용 승인 대기" 화면과 자기 이메일을 봅니다. 그 이메일을 추가한 뒤
"승인 여부 다시 확인"을 누르면 바로 쓸 수 있습니다. 카카오 계정은 카카오에 등록된 이메일로 허용합니다.

## 7. (선택) 예전 로컬 데이터 옮기기

로컬 버전에서 쓰던 `data/listings.db`, `data/dealers.db`가 있다면, 웹에서 한 번 로그인한 뒤 옮깁니다.

```bash
./.venv/bin/python -m scripts.import_local_data --project <프로젝트 ID> --email me@gmail.com --dry-run
./.venv/bin/python -m scripts.import_local_data --project <프로젝트 ID> --email me@gmail.com
```

## 업데이트 배포

- 화면만 바꿨을 때: `npm run deploy:web`
- 서버(`server.py`, `crawler/`, `scoring/` 등)를 바꿨을 때: `deploy/cloudrun.sh <프로젝트 ID>`
- 보안 규칙만 바꿨을 때: `npx firebase deploy --only firestore:rules`

배포 전에 `./.venv/bin/python -m pytest tests/`, `npm run test:rules`, `npm run test:e2e`를 돌립니다.

## 운영 시 알아둘 점

- **첫 조회가 느릴 수 있습니다.** Cloud Run이 쉬고 있다가 깨어나며 Chromium을 띄우기 때문입니다.
  Hosting rewrite는 60초에서 끊기므로 서버는 50초가 지나면 "잠시 후 다시 시도" 안내로 응답합니다.
- **조회는 한 번에 한 건씩** 처리합니다. 여러 사람이 동시에 조회하면 순서대로 기다립니다.
- **케이카·엔카 상세 페이지는 robots.txt가 금지하는 경로**입니다. "사용자가 준 링크 1건을 여는
  단발 조회"라는 판단으로 열고 있으며(`crawler/compliance.py`, README의 2026-09-20 정정), 공용 서버
  IP에서 여러 사용자가 조회하면 차단될 수 있습니다. 허용 계정·일일 한도·사이트별 간격으로 호출량을
  낮게 유지하세요. 각 사이트 이용약관은 아직 사람이 검토하지 않았습니다.
- **허용 취소**는 다음 요청부터 적용됩니다. API 서버는 허용 여부를 최대 60초 캐시합니다.
- 비용이 걱정되면 Cloud 콘솔 → 결제 → 보고서에서 서비스별 사용량을 확인합니다.

// 로그인 화면과 허용 목록 확인. 허용된 계정이면 onAllowed(user)를 부른다.
// 허용 기준은 firestore.rules의 allowed()·api/auth.py와 같다(Google 이메일 인증 계정 또는 카카오).
import {
  GoogleAuthProvider, OAuthProvider, onAuthStateChanged, signInWithPopup, signOut,
} from 'https://www.gstatic.com/firebasejs/12.19.0/firebase-auth.js';
import { doc, getDoc } from 'https://www.gstatic.com/firebasejs/12.19.0/firebase-firestore.js';

const KAKAO_PROVIDER = 'oidc.kakao';
const esc = value => String(value ?? '').replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));

function provider(name) {
  if (name === 'kakao') {
    const kakao = new OAuthProvider(KAKAO_PROVIDER);
    kakao.addScope('account_email');
    return kakao;
  }
  const google = new GoogleAuthProvider();
  google.setCustomParameters({ prompt: 'select_account' });
  return google;
}

const signInMessages = {
  'auth/popup-closed-by-user': '',
  'auth/cancelled-popup-request': '',
  'auth/popup-blocked': '브라우저가 로그인 창을 막았습니다. 팝업을 허용한 뒤 다시 시도해주세요.',
  'auth/account-exists-with-different-credential': '이 이메일은 다른 로그인 방식으로 이미 가입되어 있습니다. 처음 사용한 방식으로 로그인해주세요.',
  'auth/operation-not-allowed': '이 로그인 방식은 아직 설정되지 않았습니다. 관리자에게 문의해주세요.',
};

async function isAllowed(db, user) {
  const providerId = user.providerData[0]?.providerId;
  if (!user.email) return { allowed: false, reason: '계정 이메일을 확인할 수 없습니다. 카카오 로그인 시 이메일 제공에 동의해주세요.' };
  if (providerId === 'google.com' && !user.emailVerified) return { allowed: false, reason: '이메일 인증이 완료된 Google 계정만 사용할 수 있습니다.' };
  try {
    const snapshot = await getDoc(doc(db, 'allowlist', user.email.toLowerCase()));
    return snapshot.exists() ? { allowed: true } : { allowed: false, reason: '' };
  } catch {
    return { allowed: false, reason: '사용 승인 여부를 확인하지 못했습니다. 잠시 후 다시 시도해주세요.' };
  }
}

export function startAuth({ auth, db }, onAllowed) {
  const screen = document.getElementById('authScreen');
  const body = document.getElementById('authBody');
  let started = false;

  function show(html) {
    document.querySelector('.app-shell').hidden = true;
    screen.hidden = false;
    body.innerHTML = html;
  }

  function showLogin(message = '') {
    show(`<h1>내 차를 고르는 공간</h1>
      <p class="subtitle">매물 링크 하나로 차량을 확인하고, 찜한 차량과 딜러를 내 보관함에 모아두세요.</p>
      <div class="auth-buttons">
        <button class="button secondary full" data-login="google"><span class="provider-mark google" aria-hidden="true">G</span>Google로 계속하기</button>
        <button class="button full kakao" data-login="kakao"><span class="provider-mark" aria-hidden="true">K</span>카카오로 계속하기</button>
      </div>
      <p class="error-text" role="alert">${esc(message)}</p>
      <p class="form-note">승인된 계정만 사용할 수 있습니다. 처음이라면 로그인 후 표시되는 이메일을 관리자에게 알려주세요.</p>`);
  }

  function showPending(user, reason) {
    show(`<h1>사용 승인 대기</h1>
      <p class="subtitle">${reason ? esc(reason) : '아직 사용 승인이 되지 않은 계정입니다. 아래 이메일을 관리자에게 알려주시면 승인 후 바로 사용할 수 있습니다.'}</p>
      <p class="auth-email">${esc(user.email || '이메일 없음')}</p>
      <div class="auth-buttons">
        <button class="button primary full" data-auth-action="recheck">승인 여부 다시 확인</button>
        <button class="button secondary full" data-auth-action="logout">다른 계정으로 로그인</button>
      </div>`);
  }

  screen.addEventListener('click', async event => {
    const login = event.target.closest('[data-login]');
    if (login) {
      login.disabled = true;
      try { await signInWithPopup(auth, provider(login.dataset.login)); }
      catch (err) { showLogin(signInMessages[err?.code] ?? '로그인하지 못했습니다. 다시 시도해주세요.'); }
      finally { login.disabled = false; }
      return;
    }
    const action = event.target.closest('[data-auth-action]')?.dataset.authAction;
    if (action === 'logout') await signOut(auth);
    if (action === 'recheck') await check(auth.currentUser);
  });

  async function check(user) {
    if (!user) {
      // 앱을 쓰던 중 로그아웃하면 화면 상태를 깨끗이 비우기 위해 새로 불러온다.
      if (started) location.reload(); else showLogin();
      return;
    }
    const { allowed, reason } = await isAllowed(db, user);
    if (!allowed) { showPending(user, reason); return; }
    if (started) return;
    started = true;
    screen.hidden = true;
    document.querySelector('.app-shell').hidden = false;
    onAllowed(user, () => signOut(auth));
  }

  onAuthStateChanged(auth, user => { check(user); });
}

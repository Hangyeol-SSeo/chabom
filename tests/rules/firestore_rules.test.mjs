// firestore.rules 테스트: npm run test:rules (Firestore 에뮬레이터 안에서 node --test로 실행)
import { readFileSync } from 'node:fs';
import { after, before, beforeEach, test } from 'node:test';
import {
  assertFails, assertSucceeds, initializeTestEnvironment,
} from '@firebase/rules-unit-testing';
import { deleteDoc, doc, getDoc, getDocs, collection, setDoc, updateDoc } from 'firebase/firestore';

const PROJECT = 'demo-chabom-rules';
let env;

const google = (email, verified = true) => ({ email, email_verified: verified, firebase: { sign_in_provider: 'google.com' } });
const kakao = email => ({ email, firebase: { sign_in_provider: 'oidc.kakao' } });

const history = (overrides = {}) => ({
  url: 'https://example.com/car', source: 'encar', listing: { vehicle: { model: '모닝' } },
  last_viewed_at: '2026-10-01T00:00:00.000Z', status: 'success', reason: '', favorite: false, origin: 'lookup',
  ...overrides,
});
const dealer = (overrides = {}) => ({
  source: 'encar', dealer_key: 'dealer-1', display_name: '테스트판매자', phone: '01000000000', region: '서울',
  blacklisted: false, favorite: false, reason: '', blacklisted_at: '',
  created_at: '2026-10-01T00:00:00.000Z', updated_at: '2026-10-01T00:00:00.000Z', ...overrides,
});

before(async () => {
  env = await initializeTestEnvironment({
    projectId: PROJECT,
    firestore: { rules: readFileSync(new URL('../../firestore.rules', import.meta.url), 'utf8') },
  });
});
after(async () => { await env?.cleanup(); });
beforeEach(async () => {
  await env.clearFirestore();
  await env.withSecurityRulesDisabled(async context => {
    const db = context.firestore();
    await setDoc(doc(db, 'allowlist', 'tester@example.com'), { note: 'test' });
    await setDoc(doc(db, 'allowlist', 'kakao@example.com'), { note: 'test' });
    await setDoc(doc(db, 'users/user-b/history/h1'), history());
  });
});

const as = (uid, claims) => env.authenticatedContext(uid, claims).firestore();

test('허용된 사용자는 자기 데이터를 읽고 쓴다', async () => {
  const db = as('user-a', google('Tester@Example.com'));
  await assertSucceeds(setDoc(doc(db, 'users/user-a/history/h1'), history()));
  await assertSucceeds(getDocs(collection(db, 'users/user-a/history')));
  await assertSucceeds(setDoc(doc(db, 'users/user-a/dealers/encar__dealer-1'), dealer()));
  await assertSucceeds(updateDoc(doc(db, 'users/user-a/history/h1'), { favorite: true }));
  await assertSucceeds(deleteDoc(doc(db, 'users/user-a/history/h1')));
});

test('카카오 계정도 허용 목록에 있으면 쓸 수 있다', async () => {
  const db = as('user-k', kakao('kakao@example.com'));
  await assertSucceeds(setDoc(doc(db, 'users/user-k/history/h1'), history()));
});

test('다른 사용자의 데이터는 읽거나 쓸 수 없다', async () => {
  const db = as('user-a', google('tester@example.com'));
  await assertFails(getDoc(doc(db, 'users/user-b/history/h1')));
  await assertFails(getDocs(collection(db, 'users/user-b/history')));
  await assertFails(setDoc(doc(db, 'users/user-b/history/h2'), history()));
});

test('허용 목록에 없거나 신뢰할 수 없는 계정은 막는다', async () => {
  await assertFails(setDoc(doc(as('user-c', google('stranger@example.com')), 'users/user-c/history/h1'), history()));
  await assertFails(setDoc(doc(as('user-a', google('tester@example.com', false)), 'users/user-a/history/h1'), history()));
  const password = { email: 'tester@example.com', email_verified: true, firebase: { sign_in_provider: 'password' } };
  await assertFails(setDoc(doc(as('user-a', password), 'users/user-a/history/h1'), history()));
  await assertFails(getDoc(doc(env.unauthenticatedContext().firestore(), 'users/user-b/history/h1')));
});

test('허용 목록은 본인 문서만 읽고 아무도 쓸 수 없다', async () => {
  const db = as('user-c', google('Stranger@Example.com'));
  await assertSucceeds(getDoc(doc(db, 'allowlist', 'stranger@example.com')));
  await assertFails(getDoc(doc(db, 'allowlist', 'tester@example.com')));
  await assertFails(getDocs(collection(db, 'allowlist')));
  await assertFails(setDoc(doc(db, 'allowlist', 'stranger@example.com'), {}));
  await assertFails(getDoc(doc(as('user-a', google('tester@example.com')), 'usage/user-a_20261001')));
});

test('형식이 맞지 않는 문서는 저장할 수 없다', async () => {
  const db = as('user-a', google('tester@example.com'));
  await assertFails(setDoc(doc(db, 'users/user-a/history/h1'), history({ url: 'javascript:alert(1)' })));
  await assertFails(setDoc(doc(db, 'users/user-a/history/h1'), history({ extra: true })));
  await assertFails(setDoc(doc(db, 'users/user-a/history/h1'), history({ status: 'unknown' })));
  await assertFails(setDoc(doc(db, 'users/user-a/dealers/encar__dealer-1'), dealer({ phone: '010-0000-0000' })));
  await assertFails(setDoc(doc(db, 'users/user-a/dealers/encar__dealer-1'), dealer({ blacklisted: true, reason: '' })));
});

test('블랙리스트 딜러는 찜할 수 없다', async () => {
  const db = as('user-a', google('tester@example.com'));
  const ref = doc(db, 'users/user-a/dealers/encar__dealer-1');
  await assertSucceeds(setDoc(ref, dealer({ blacklisted: true, reason: '기록 불일치', blacklisted_at: '2026-10-01' })));
  await assertFails(updateDoc(ref, { favorite: true }));
  await assertSucceeds(updateDoc(ref, { blacklisted: false, reason: '', blacklisted_at: '' }));
  await assertSucceeds(updateDoc(ref, { favorite: true }));
});

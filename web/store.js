// 사용자별 Firestore 데이터 계층 — 예전 storage/history.py·storage/dealers.py(SQLite)를 옮겼다.
// 모든 문서는 users/{uid} 아래에 있고 firestore.rules가 본인 외 접근과 잘못된 형식을 막는다.
//   users/{uid}/history/{정규화한 URL의 SHA-256}
//   users/{uid}/dealers/{source}__{encodeURIComponent(dealer_key)}
import {
  collection, doc, getDoc, getDocs, query, where, runTransaction, updateDoc,
} from 'https://www.gstatic.com/firebasejs/12.19.0/firebase-firestore.js';

// api/validation.py의 normalize_url과 같은 규칙: http(s)만, 계정 정보 금지, 스킴·호스트 소문자, # 이하 제거.
export function normalizeURL(value) {
  let url;
  try { url = new URL(String(value || '').trim()); } catch { throw new Error('http 또는 https 매물 링크를 입력해주세요.'); }
  if (!['http:', 'https:'].includes(url.protocol) || !url.hostname || url.username || url.password) {
    throw new Error('http 또는 https 매물 링크를 입력해주세요.');
  }
  url.hash = '';
  return url.href;
}

export const normalizePhone = phone => String(phone || '').replace(/[^\d]/g, '');

async function sha256(text) {
  const digest = await crypto.subtle.digest('SHA-256', new TextEncoder().encode(text));
  return [...new Uint8Array(digest)].map(b => b.toString(16).padStart(2, '0')).join('');
}

const now = () => new Date().toISOString();
const keep = (incoming, current) => (incoming ? incoming : current || '');

export function createStore(db, uid) {
  const historyCol = collection(db, 'users', uid, 'history');
  const dealerCol = collection(db, 'users', uid, 'dealers');
  const dealerRef = (source, key) => doc(dealerCol, `${source}__${encodeURIComponent(key)}`);

  function requireIdentity(source, key) {
    if (!String(source || '').trim() || !String(key || '').trim()) throw new Error('사이트와 딜러 ID가 필요합니다.');
  }

  // 딜러 문서를 읽어 바꾼 뒤 통째로 쓴다. 빈 값은 기존 값을 덮지 않는다(예전 SQL의 COALESCE(NULLIF(...))).
  async function updateDealer(source, key, info, change) {
    requireIdentity(source, key);
    const ref = dealerRef(source, key);
    await runTransaction(db, async tx => {
      const snapshot = await tx.get(ref);
      const current = snapshot.exists() ? snapshot.data() : null;
      const time = now();
      const next = {
        source, dealer_key: key,
        display_name: keep(info.display_name, current?.display_name),
        phone: keep(normalizePhone(info.phone), current?.phone),
        region: keep(info.region, current?.region),
        blacklisted: current?.blacklisted ?? false,
        favorite: current?.favorite ?? false,
        reason: current?.reason ?? '',
        blacklisted_at: current?.blacklisted_at ?? '',
        created_at: current?.created_at ?? time,
        updated_at: time,
      };
      change(next, current);
      tx.set(ref, next);
    });
  }

  return {
    async listHistory() {
      const snapshot = await getDocs(historyCol);
      return snapshot.docs.map(d => ({ id: d.id, ...d.data() }))
        .sort((a, b) => String(b.last_viewed_at).localeCompare(String(a.last_viewed_at)));
    },

    // 같은 링크를 다시 조회하면 찜은 유지하고, 실패한 조회는 이전에 저장한 차량 정보를 지우지 않는다.
    async recordHistory(url, { source = '', listing = null, status = 'success', reason = '' } = {}) {
      url = normalizeURL(url);
      const id = await sha256(url);
      const ref = doc(historyCol, id);
      await runTransaction(db, async tx => {
        const snapshot = await tx.get(ref);
        const current = snapshot.exists() ? snapshot.data() : null;
        tx.set(ref, {
          url, source: source || current?.source || '',
          listing: listing ?? current?.listing ?? null,
          last_viewed_at: now(), status, reason,
          favorite: current?.favorite ?? false, origin: 'lookup',
        });
      });
      return id;
    },

    async setHistoryFavorite(id, favorite) {
      try { await updateDoc(doc(historyCol, id), { favorite }); }
      catch (err) { throw new Error(err?.code === 'not-found' ? '저장된 매물을 찾을 수 없습니다.' : '찜 상태를 저장하지 못했습니다.'); }
      return favorite;
    },

    async listDealers() {
      const snapshot = await getDocs(dealerCol);
      return snapshot.docs.map(d => d.data())
        .sort((a, b) => String(b.updated_at).localeCompare(String(a.updated_at)) || a.source.localeCompare(b.source)
          || a.dealer_key.localeCompare(b.dealer_key));
    },

    upsertDealer(source, key, info = {}) {
      if (!key) return Promise.resolve();
      return updateDealer(source, key, info, () => {});
    },

    blacklistDealer(source, key, reason, info = {}) {
      if (!String(reason || '').trim()) return Promise.reject(new Error('제외 사유를 입력해주세요.'));
      return updateDealer(source, key, info, next => {
        Object.assign(next, { blacklisted: true, favorite: false, reason: reason.trim(), blacklisted_at: now() });
      });
    },

    unblacklistDealer(source, key) {
      return updateDealer(source, key, {}, next => {
        Object.assign(next, { blacklisted: false, reason: '', blacklisted_at: '' });
      });
    },

    setDealerFavorite(source, key, favorite, info = {}) {
      return updateDealer(source, key, info, next => {
        if (favorite && next.blacklisted) throw new Error('블랙리스트를 해제한 후 찜할 수 있습니다.');
        next.favorite = favorite;
      });
    },

    // /api/verify에 보낼 딜러 정보 — 이 딜러의 기록과, 같은 전화번호를 쓰는 블랙리스트 딜러.
    async dealerContext(listing) {
      const source = listing?.source || 'manual', key = listing?.dealer?.dealer_id;
      if (!key) return null;
      const snapshot = await getDoc(dealerRef(source, key));
      const record = snapshot.exists() ? snapshot.data() : null;
      const phone = normalizePhone(listing.dealer.phone);
      let matches = [];
      if (phone) {
        const found = await getDocs(query(dealerCol, where('phone', '==', phone), where('blacklisted', '==', true)));
        matches = found.docs.map(d => d.data())
          .filter(d => !(d.source === source && d.dealer_key === key))
          .map(d => ({ source: d.source, dealer_key: d.dealer_key, display_name: d.display_name || '', reason: d.reason || '' }));
      }
      return {
        record: record ? { blacklisted: record.blacklisted, reason: record.reason || '', blacklisted_at: record.blacklisted_at || '' } : null,
        phone_matches: matches,
      };
    },
  };
}

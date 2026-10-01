import { initFirebase } from './firebase.js';
import { startAuth } from './auth.js';
import { createStore } from './store.js';
import { createApi } from './api.js';
import { startApp } from './app.js';

try {
  const firebase = await initFirebase();
  startAuth(firebase, (user, signOut) => {
    startApp({ store: createStore(firebase.db, user.uid), api: createApi(firebase.auth), user, signOut });
  });
} catch (err) {
  document.getElementById('authScreen').hidden = false;
  document.getElementById('authBody').innerHTML = '<h1>잠시 후 다시 시도해주세요</h1><p class="subtitle">서비스 설정을 불러오지 못했습니다.</p>';
  console.error(err);
}

// Firebase 초기화. 설정은 Firebase Hosting이 자동으로 제공하는 /__/firebase/init.json에서 읽는다.
// 로컬 개발(CHABOM_DEV=1 python server.py)에서는 서버가 demo- 프로젝트 설정을 내주고,
// 이때는 Auth·Firestore 에뮬레이터에 붙는다.
import { initializeApp } from 'https://www.gstatic.com/firebasejs/12.19.0/firebase-app.js';
import { getAuth, connectAuthEmulator } from 'https://www.gstatic.com/firebasejs/12.19.0/firebase-auth.js';
import { initializeFirestore, connectFirestoreEmulator } from 'https://www.gstatic.com/firebasejs/12.19.0/firebase-firestore.js';

export async function initFirebase() {
  const response = await fetch('/__/firebase/init.json');
  if (!response.ok) throw new Error('Firebase 설정을 불러오지 못했습니다.');
  const config = await response.json();
  const app = initializeApp(config);
  const auth = getAuth(app);
  const db = initializeFirestore(app, { ignoreUndefinedProperties: true });
  if (String(config.projectId).startsWith('demo-')) {
    connectAuthEmulator(auth, 'http://127.0.0.1:9099', { disableWarnings: true });
    connectFirestoreEmulator(db, '127.0.0.1', 8080);
  }
  return { app, auth, db };
}

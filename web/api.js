// 조회·검증 API(Cloud Run, Hosting의 /api/** rewrite) 호출. 모든 요청에 Firebase ID 토큰을 붙인다.
export function createApi(auth) {
  return {
    async post(path, body) {
      const token = await auth.currentUser?.getIdToken();
      if (!token) throw new Error('로그인이 필요합니다.');
      let response;
      try {
        response = await fetch(path, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json', Authorization: `Bearer ${token}` },
          body: JSON.stringify(body),
        });
      } catch { throw new Error('서버에 연결할 수 없습니다. 다시 시도해주세요.'); }
      let data;
      try { data = await response.json(); }
      catch {
        throw new Error(response.status >= 500
          ? '서버가 응답하지 않습니다. 첫 조회는 서버가 깨어나는 데 시간이 걸릴 수 있으니 잠시 후 다시 시도해주세요.'
          : '서버에 연결할 수 없습니다. 다시 시도해주세요.');
      }
      if (!response.ok) throw new Error(typeof data.detail === 'string' ? data.detail : '요청을 처리하지 못했습니다.');
      return data;
    },
  };
}

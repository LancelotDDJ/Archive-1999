/* 认证状态：令牌/用户信息的本地存储与守卫。 */
const K_ACCESS = 'a1999.access';
const K_REFRESH = 'a1999.refresh';
const K_USER = 'a1999.user';

function save(pair) {
  if (pair.access_token) localStorage.setItem(K_ACCESS, pair.access_token);
  if (pair.refresh_token) localStorage.setItem(K_REFRESH, pair.refresh_token);
  if (pair.user) localStorage.setItem(K_USER, JSON.stringify(pair.user));
}

export const Auth = {
  accessToken: () => localStorage.getItem(K_ACCESS) || '',
  refreshToken: () => localStorage.getItem(K_REFRESH) || '',
  user() {
    try { return JSON.parse(localStorage.getItem(K_USER) || 'null'); }
    catch { return null; }
  },
  setUser(u) { localStorage.setItem(K_USER, JSON.stringify(u)); },
  isAdmin: () => Auth.user()?.role === 'admin',
  save,
  clear() {
    localStorage.removeItem(K_ACCESS);
    localStorage.removeItem(K_REFRESH);
    localStorage.removeItem(K_USER);
  },
  /** 页面守卫：未登录跳登录页；要求管理员时非管理员跳大厅。 */
  guard({ admin = false } = {}) {
    const u = Auth.user();
    if (!Auth.accessToken() || !u) {
      location.href = '/login';
      return null;
    }
    if (admin && u.role !== 'admin') {
      location.href = '/';
      return null;
    }
    return u;
  },
  async refresh() {
    const rt = Auth.refreshToken();
    if (!rt) throw new Error('no refresh token');
    const resp = await fetch('/api/v1/auth/refresh', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ refresh_token: rt }),
    });
    if (!resp.ok) {
      Auth.clear();
      location.href = '/login';
      throw new Error('refresh failed');
    }
    const data = await resp.json();
    save(data);
    return data;
  },
  async logout() {
    const rt = Auth.refreshToken();
    try {
      if (rt) {
        await fetch('/api/v1/auth/logout', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ refresh_token: rt }),
        });
      }
    } catch {}
    Auth.clear();
    location.href = '/login';
  },
};

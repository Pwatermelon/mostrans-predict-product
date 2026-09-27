/* Общий клиент авторизации */

const AUTH_KEY = "mt_token";
const USER_KEY = "mt_user";

const Auth = {
  token() {
    return localStorage.getItem(AUTH_KEY) || "";
  },

  user() {
    try {
      return JSON.parse(localStorage.getItem(USER_KEY) || "null");
    } catch {
      return null;
    }
  },

  setSession(token, user) {
    localStorage.setItem(AUTH_KEY, token);
    localStorage.setItem(USER_KEY, JSON.stringify(user));
  },

  clear() {
    localStorage.removeItem(AUTH_KEY);
    localStorage.removeItem(USER_KEY);
  },

  headers(extra = {}) {
    const h = { ...extra };
    const t = Auth.token();
    if (t) h.Authorization = "Bearer " + t;
    return h;
  },

  async fetch(url, opts = {}) {
    const headers = Auth.headers(opts.headers || {});
    if (opts.body && !headers["Content-Type"] && !(opts.body instanceof FormData)) {
      headers["Content-Type"] = "application/json";
    }
    const r = await fetch(url, { ...opts, headers, credentials: "same-origin" });
    if (r.status === 401) {
      Auth.clear();
      const next = encodeURIComponent(location.pathname + location.search);
      location.href = "/login?next=" + next;
      throw new Error("unauthorized");
    }
    return r;
  },

  async require(role) {
    const t = Auth.token();
    if (!t) {
      location.href = "/login?next=" + encodeURIComponent(location.pathname + location.search);
      return null;
    }
    const r = await fetch("/api/v1/auth/me", {
      headers: Auth.headers(),
      credentials: "same-origin",
    });
    if (!r.ok) {
      Auth.clear();
      location.href = "/login?next=" + encodeURIComponent(location.pathname + location.search);
      return null;
    }
    const data = await r.json();
    Auth.setSession(t, data.user);
    if (role && data.user.role !== role) {
      if (data.user.role === "driver") {
        location.href = "/driver";
      } else {
        location.href = "/";
      }
      return null;
    }
    return data.user;
  },

  async logout() {
    try {
      await fetch("/api/v1/auth/logout", { method: "POST", credentials: "same-origin" });
    } catch {
      /* ignore */
    }
    Auth.clear();
    location.href = "/login";
  },

  wsUrl(path) {
    const proto = location.protocol === "https:" ? "wss:" : "ws:";
    const t = Auth.token();
    return `${proto}//${location.host}${path}?token=${encodeURIComponent(t)}`;
  },
};

window.Auth = Auth;

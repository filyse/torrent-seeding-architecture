// WebSocket-клиент (Фаза 7, WS-4): одно соединение, подписки на каналы, авто-reconnect.
//
// Дизайн «прогрессивного улучшения»: если бэкенд-флаг SEEDING_WS_ENABLED выключен, сервер
// закрывает handshake кодом 1013 — клиент помечает WS как недоступный и зовёт onUnavailable,
// чтобы вызывающий код откатился на старый SSE/поллинг. Транзиентные обрывы (сеть) НЕ считаются
// недоступностью: клиент переподключается с backoff и заново оформляет подписки.

const API = "/api/v1";

export type WsMessage = {
  type: string;
  channel?: string;
  data?: unknown;
  v?: number;
};
type Listener = (msg: WsMessage) => void;

const CLOSE_DISABLED = 1013; // фича выключена на сервере
const CLOSE_AUTH = 4401; // авторизация не прошла

let socket: WebSocket | null = null;
let connecting = false;
let unavailable = false;
let backoff = 1000;
const BACKOFF_MAX = 15000;
let reconnectTimer = 0;

const subs = new Map<string, Set<Listener>>();
const unavailCbs = new Set<() => void>();

function getKey(): string {
  try {
    return localStorage.getItem("seedingApiKey") || "";
  } catch {
    return "";
  }
}

function wsUrl(): string {
  const proto = location.protocol === "https:" ? "wss" : "ws";
  return `${proto}://${location.host}${API}/ws`;
}

function send(obj: unknown): void {
  if (socket && socket.readyState === WebSocket.OPEN) {
    try {
      socket.send(JSON.stringify(obj));
    } catch {
      /* сокет закрылся между проверкой и отправкой */
    }
  }
}

function markUnavailable(): void {
  unavailable = true;
  if (reconnectTimer) {
    window.clearTimeout(reconnectTimer);
    reconnectTimer = 0;
  }
  for (const cb of unavailCbs) {
    try {
      cb();
    } catch {
      /* ignore */
    }
  }
}

function scheduleReconnect(): void {
  if (unavailable || reconnectTimer) return;
  if (subs.size === 0) return; // некого обслуживать — не держим соединение
  reconnectTimer = window.setTimeout(() => {
    reconnectTimer = 0;
    ensureSocket();
  }, backoff);
  backoff = Math.min(BACKOFF_MAX, Math.round(backoff * 1.7));
}

function ensureSocket(): void {
  if (unavailable) return;
  if (socket && (socket.readyState === WebSocket.OPEN || socket.readyState === WebSocket.CONNECTING)) return;
  if (connecting) return;
  connecting = true;

  const protocols = ["seeding.v1"];
  const key = getKey();
  // API-ключи/токены — token_urlsafe (A-Za-z0-9-_), валидны как сабпротокол; в URL не пишем.
  if (key && /^[A-Za-z0-9._-]+$/.test(key)) protocols.push("bearer." + key);

  let s: WebSocket;
  try {
    s = new WebSocket(wsUrl(), protocols);
  } catch {
    connecting = false;
    markUnavailable();
    return;
  }
  socket = s;

  s.onopen = () => {
    connecting = false;
    backoff = 1000;
    for (const channel of subs.keys()) send({ type: "subscribe", channel });
  };

  s.onmessage = (ev) => {
    let msg: WsMessage;
    try {
      msg = JSON.parse(typeof ev.data === "string" ? ev.data : "") as WsMessage;
    } catch {
      return;
    }
    const ch = msg.channel;
    if (!ch) return;
    const set = subs.get(ch);
    if (set) {
      for (const cb of set) {
        try {
          cb(msg);
        } catch {
          /* ignore listener error */
        }
      }
    }
  };

  s.onclose = (ev) => {
    connecting = false;
    if (socket === s) socket = null;
    if (ev.code === CLOSE_DISABLED || ev.code === CLOSE_AUTH) {
      markUnavailable();
      return;
    }
    scheduleReconnect();
  };

  s.onerror = () => {
    // За ошибкой последует onclose — там и решим про reconnect.
  };
}

/** Подписаться на канал. Возвращает функцию отписки. */
export function wsSubscribe(channel: string, cb: Listener): () => void {
  let set = subs.get(channel);
  if (!set) {
    set = new Set();
    subs.set(channel, set);
  }
  set.add(cb);
  if (!unavailable) {
    ensureSocket();
    if (socket && socket.readyState === WebSocket.OPEN) send({ type: "subscribe", channel });
  }
  return () => {
    const s = subs.get(channel);
    if (!s) return;
    s.delete(cb);
    if (s.size === 0) {
      subs.delete(channel);
      send({ type: "unsubscribe", channel });
    }
  };
}

/** Зарегистрировать колбэк «WS недоступен» (фича выключена/авторизация). Вызывается сразу,
 * если недоступность уже известна. Возвращает функцию снятия колбэка. */
export function onWsUnavailable(cb: () => void): () => void {
  unavailCbs.add(cb);
  if (unavailable) {
    try {
      cb();
    } catch {
      /* ignore */
    }
  }
  return () => unavailCbs.delete(cb);
}

export function wsAvailable(): boolean {
  return !unavailable;
}

export function wsConnected(): boolean {
  return !!socket && socket.readyState === WebSocket.OPEN;
}

/** Сбросить флаг недоступности (например, после смены ключа/перелогина) и попробовать снова. */
export function wsReset(): void {
  unavailable = false;
  backoff = 1000;
  if (subs.size > 0) ensureSocket();
}

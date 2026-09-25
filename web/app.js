/* iphone-hid console: device grid + one device's live view and controls. No build step, no deps. */
"use strict";

const $ = (id) => document.getElementById(id);
const STATE_TEXT = {
  ready: "ready", busy: "busy", hid_disconnected: "HID not connected",
  hid_offline: "HID offline", no_signal: "no signal",
};

/* The API token, asked for once when the server answers 401 (or closes a socket with 4401) and kept
   in this browser. WebSockets and <img> URLs cannot set headers: they carry it as ?token=. */
const auth = {
  token: (() => { try { return localStorage.getItem("ihc.token") || ""; } catch (e) { return ""; } })(),
  asking: null,
  /** Resolves once the operator entered a token; one dialog for every request that needs it. */
  ask(message) {
    if (!this.asking) {
      this.asking = new Promise((resolve) => {
        const dlg = $("token-dialog"), input = $("token-input");
        $("token-error").textContent = this.token ? "That token was refused." : "";
        if (message) $("token-error").textContent += (this.token ? " " : "") + message;
        input.value = "";
        $("token-form").onsubmit = (e) => {
          e.preventDefault();
          const value = input.value.trim();
          if (!value) return;
          this.token = value;
          try { localStorage.setItem("ihc.token", value); } catch (err) { /* private mode: this page only */ }
          dlg.close();
          this.asking = null;
          resolve();
        };
        dlg.oncancel = (e) => e.preventDefault();  // Esc: nothing works without the token
        dlg.showModal();
        input.focus();
      });
    }
    return this.asking;
  },
  url(path, params = {}) {
    const q = new URLSearchParams(params);
    if (this.token) q.set("token", this.token);
    const qs = q.toString();
    return path + (qs ? "?" + qs : "");
  },
};
const wsUrl = (path, params) => (location.protocol === "https:" ? "wss://" : "ws://") + location.host + auth.url(path, params);

async function api(method, path, body) {
  const opts = { method, headers: {} };
  if (auth.token) opts.headers.Authorization = "Bearer " + auth.token;
  if (method !== "GET") {
    // always declared JSON, even without a body: the server refuses anything else (a cross-site
    // form cannot send it)
    opts.headers["Content-Type"] = "application/json";
    if (body !== undefined) opts.body = JSON.stringify(body);
  }
  const r = await fetch(path, opts);
  const data = await r.json().catch(() => ({}));
  if (r.status === 401) {
    await auth.ask();
    return api(method, path, body);
  }
  if (!r.ok) throw new Error(data.error || `HTTP ${r.status}`);
  return data;
}

function setBadge(el, state) {
  el.className = "badge " + (state || "");
  el.textContent = STATE_TEXT[state] || state || "–";
}

/* A JPEG-over-WebSocket stream. Acknowledges every frame once drawn, so the server never has more
   than one frame in flight to us and a slow browser skips frames instead of lagging behind. */
class FrameSocket {
  constructor(path, params, { onFrame, onStatus, onClose }) {
    Object.assign(this, { path, params: { ...params, ack: "true" }, onFrame, onStatus, onClose });
    this.closed = false;
    this.connect();
  }
  connect() {
    const ws = (this.ws = new WebSocket(wsUrl(this.path, this.params)));  // the current token
    ws.binaryType = "blob";
    ws.onmessage = async (ev) => {
      if (typeof ev.data === "string") {
        const msg = JSON.parse(ev.data);
        if (msg.type === "status" && this.onStatus) this.onStatus(msg);
        return;
      }
      try { await this.onFrame(ev.data); } catch (e) { /* undecodable frame: skip it */ }
      if (ws.readyState === WebSocket.OPEN) ws.send('{"t":"ack"}');
    };
    ws.onclose = (ev) => {
      if (this.onClose) this.onClose(ev);
      if (this.closed || ev.code === 4404) return;
      if (ev.code === 4401) return auth.ask().then(() => { if (!this.closed) this.connect(); });
      this.timer = setTimeout(() => this.connect(), ev.code === 4429 ? 10000 : 1500);  // 4429: too many viewers
    };
  }
  close() {
    this.closed = true;
    clearTimeout(this.timer);
    this.ws.close();
  }
}

/* ---------------------------------------------------------------- grid ---------------------- */

const grid = {
  tiles: new Map(),
  timer: null,
  start() {
    $("grid-view").hidden = false;
    this.refresh();
    this.timer = setInterval(() => this.refresh(), 2000);
  },
  stop() {
    $("grid-view").hidden = true;
    clearInterval(this.timer);
    for (const t of this.tiles.values()) t.stream.close();
    this.tiles.clear();
    $("grid").textContent = "";
  },
  async refresh() {
    let list;
    try { list = (await api("GET", "/api/devices")).devices; } catch (e) { return; }
    $("host-info").textContent = `${location.host} · ${list.length} device${list.length === 1 ? "" : "s"}`;
    $("grid-empty").hidden = list.length > 0;
    for (const st of list) {
      let t = this.tiles.get(st.id);
      if (!t) t = this.addTile(st.id);
      setBadge(t.badge, st.state);
      const cal = st.calibration || {};
      t.meta.textContent = `${st.model} · ${st.kind} · ${(st.pointer || {}).mode || "relative"} pointer · ` +
        (cal.calibrated ? `calibrated (${cal.method})` : "not calibrated");
    }
  },
  addTile(id) {
    const a = document.createElement("a");
    a.className = "tile";
    a.href = "#/device/" + encodeURIComponent(id);
    a.innerHTML = '<div class="thumb"><img alt=""></div><div class="name"><strong></strong><span class="badge"></span></div><div class="meta"></div>';
    a.querySelector("strong").textContent = id;
    $("grid").appendChild(a);
    const img = a.querySelector("img");
    let url = null;
    const stream = new FrameSocket(`/api/devices/${encodeURIComponent(id)}/stream`,
      { crop: "true", width: "240", fps: "2", quality: "70" }, {
        onFrame: (blob) => new Promise((resolve) => {
          const next = URL.createObjectURL(blob);
          img.onload = img.onerror = () => { if (url) URL.revokeObjectURL(url); url = next; resolve(); };
          img.src = next;
        }),
      });
    const t = { stream, badge: a.querySelector(".badge"), meta: a.querySelector(".meta") };
    this.tiles.set(id, t);
    return t;
  },
};

/* ---------------------------------------------------------- device view --------------------- */

// KeyboardEvent.code -> HID usage (page 0x07), layout independent.
const HID_KEYS = (() => {
  const m = {};
  for (let i = 0; i < 26; i++) m["Key" + String.fromCharCode(65 + i)] = 0x04 + i;
  for (let i = 1; i <= 9; i++) m["Digit" + i] = 0x1d + i;
  m.Digit0 = 0x27;
  Object.assign(m, {
    Enter: 0x28, Escape: 0x29, Backspace: 0x2a, Tab: 0x2b, Space: 0x2c, Minus: 0x2d, Equal: 0x2e,
    BracketLeft: 0x2f, BracketRight: 0x30, Backslash: 0x31, IntlHash: 0x32, Semicolon: 0x33, Quote: 0x34,
    Backquote: 0x35, Comma: 0x36, Period: 0x37, Slash: 0x38, CapsLock: 0x39, PrintScreen: 0x46,
    ScrollLock: 0x47, Pause: 0x48, Insert: 0x49, Home: 0x4a, PageUp: 0x4b, Delete: 0x4c, End: 0x4d,
    PageDown: 0x4e, ArrowRight: 0x4f, ArrowLeft: 0x50, ArrowDown: 0x51, ArrowUp: 0x52, NumLock: 0x53,
    NumpadDivide: 0x54, NumpadMultiply: 0x55, NumpadSubtract: 0x56, NumpadAdd: 0x57, NumpadEnter: 0x58,
    NumpadDecimal: 0x63, IntlBackslash: 0x64, ContextMenu: 0x65, NumpadEqual: 0x67,
  });
  for (let i = 1; i <= 12; i++) m["F" + i] = 0x39 + i;
  for (let i = 1; i <= 9; i++) m["Numpad" + i] = 0x58 + i;
  m.Numpad0 = 0x62;
  return m;
})();
const HID_MODS = {
  ControlLeft: 0x01, ShiftLeft: 0x02, AltLeft: 0x04, MetaLeft: 0x08, OSLeft: 0x08,
  ControlRight: 0x10, ShiftRight: 0x20, AltRight: 0x40, MetaRight: 0x80, OSRight: 0x80,
};
const HINTS = {
  precise: "Click = tap · drag = swipe · hold > 0.6 s = long press · wheel = scroll · right button = Home · middle button = App Switcher",
  relative: "Click the screen to capture the mouse (Pointer Lock) and control the phone live: right button = Home, middle = App Switcher, the keyboard goes to the iPhone. Esc releases the mouse (send Esc to the phone with the Keys box).",
  absolute: "Click the screen to control the phone live (absolute pointer): right button = Home, middle = App Switcher, the keyboard goes to the iPhone. Esc or a click outside releases it.",
};

const view = {
  id: null, status: null, rect: null, mode: "precise", engaged: false,
  buttons: 0, keys: [], mods: 0, dx: 0, dy: 0, wheel: 0, absPos: null, flushTimer: null,
  nextId: 1, pending: new Map(), frames: [], lastFrameAt: 0, decodeMs: 0,

  start(id) {
    Object.assign(this, { id, status: null, rect: null, frames: [], live: null, pointerMode: undefined });
    $("device-view").hidden = false;
    $("host-info").textContent = location.host;
    $("dv-title").textContent = id;
    $("dv-error").textContent = "";
    $("screen-msg").hidden = false;
    this.mode = localStorage.getItem("ihc.mode") || "precise";
    this.openStream();
    if (this.mode === "direct") this.openControl();
    this.render();
    this.showCalLink();
  },
  stop() {
    $("device-view").hidden = true;
    this.disengage();
    if (this.stream) this.stream.close();
    this.closeControl();
    this.stream = null;
    this.id = null;
  },

  /* -- video ------------------------------------------------------------------------------- */
  openStream() {
    // passthrough: the capture card's JPEG as-is; the phone screen is cropped here, not on the server
    const canvas = $("live"), ctx = canvas.getContext("2d");
    let got = false, failures = 0;
    this.stream = new FrameSocket(`/api/devices/${encodeURIComponent(this.id)}/stream`, {}, {
      onStatus: (st) => this.onStatus(st),
      onFrame: async (blob) => {
        const t0 = performance.now();
        const bmp = await createImageBitmap(blob);
        const r = this.rect || { x: -0.5, y: -0.5, w: bmp.width, h: bmp.height };
        const w = Math.round(r.w), h = Math.round(r.h);
        if (canvas.width !== w || canvas.height !== h) { canvas.width = w; canvas.height = h; this.fit(); }
        ctx.drawImage(bmp, r.x + 0.5, r.y + 0.5, r.w, r.h, 0, 0, w, h);
        bmp.close();
        got = true;
        $("screen-msg").hidden = true;
        const now = performance.now();
        this.decodeMs = now - t0;
        this.frames.push(now);
        while (this.frames.length && now - this.frames[0] > 2000) this.frames.shift();
      },
      onClose: () => {
        if (!got && ++failures >= 2 && this.stream) this.fallbackMjpeg();
      },
    });
  },
  fallbackMjpeg() {
    // WebSocket blocked (proxy?): server-cropped MJPEG instead, status by polling
    this.stream.close();
    const img = $("live-img");
    $("live").hidden = true;
    img.hidden = false;
    img.onload = () => { $("screen-msg").hidden = true; this.fit(); };
    img.src = auth.url(`/api/devices/${encodeURIComponent(this.id)}/mjpeg`, { crop: "true" });
    const poll = setInterval(async () => {
      if (!this.id) return clearInterval(poll);
      try { this.onStatus(await api("GET", `/api/devices/${encodeURIComponent(this.id)}`)); } catch (e) { /* retry */ }
    }, 1000);
    this.stream = { close: () => { clearInterval(poll); img.src = ""; img.hidden = true; $("live").hidden = false; } };
  },
  fit() {
    const stage = $("stage"), screen = $("screen");
    const pts = (this.status && this.status.screen.points) || [393, 852];
    const aspect = pts[0] / pts[1];
    const H = stage.clientHeight - 24, W = stage.clientWidth - 24;
    const h = Math.max(100, Math.min(H, W / aspect));
    screen.style.height = h + "px";
    screen.style.width = h * aspect + "px";
    $("overlay").setAttribute("viewBox", `0 0 ${pts[0]} ${pts[1]}`);
  },

  onStatus(st) {
    const first = !this.status;
    this.status = st;
    if (st.screen_rect || st.screen) this.rect = st.screen_rect || st.screen.rect;
    if (first) this.fit();
    const p = st.pointer || {}, cal = st.calibration || {}, h = st.hid || {}, s = st.stream || {};
    setBadge($("dv-state"), st.state);
    $("st-state").textContent = (STATE_TEXT[st.state] || st.state) + (st.busy_with ? ` (${st.busy_with})` : "") +
      ` · ${st.model} · ${st.kind}`;
    $("st-pointer").textContent = `${p.mode || "relative"} · ` +
      (p.pt ? `${p.pt[0]}, ${p.pt[1]} pt (${p.norm[0].toFixed(3)}, ${p.norm[1].toFixed(3)})` : "position unknown");
    const v = cal.validation;
    $("st-cal").textContent = cal.calibrated
      ? `${cal.method}` + (v ? ` · error mean ${v.mean} pt, max ${v.max} pt` : "") : "not calibrated";
    $("st-hid").textContent = `${p.reports || 0} reports · ${p.resends || 0} resent` +
      (this.live ? ` · live ${this.live.reports}/s` + (this.live.report_ms ? ` (${this.live.report_ms} ms)` : "") : "");
    const lr = st.last_result;
    $("st-last").textContent = lr ? `${lr.action} ${lr.ok ? "OK" : "failed: " + lr.error}` + (lr.seconds !== undefined ? ` · ${lr.seconds} s` : "") : "–";
    const fps = this.frames.length > 1 ? (this.frames.length - 1) / ((this.frames[this.frames.length - 1] - this.frames[0]) / 1000) : 0;
    const age = st.frame && st.frame.age_ms !== null ? st.frame.age_ms : null;
    $("st-stream").textContent = `${fps.toFixed(1)} fps (source ${s.source_fps ?? "–"})` +
      (age !== null ? ` · frame age ${Math.round(age + this.decodeMs)} ms` : "") +
      (s.passthrough ? " · passthrough" : "") + (s.error ? ` · ${s.error}` : "");
    const mode = p.mode || "relative";
    if (mode !== this.pointerMode) { this.pointerMode = mode; this.render(); }
  },

  /* -- control socket ---------------------------------------------------------------------- */
  // Open in live control only: the server has one control connection per phone, and precise mode
  // does its actions over REST.
  openControl(takeover = false) {
    const id = this.id;
    const ws = (this.control = new WebSocket(wsUrl(`/api/devices/${encodeURIComponent(id)}/control`,
      takeover ? { takeover: "true" } : {})));
    ws.onmessage = (ev) => {
      const msg = JSON.parse(ev.data);
      if (msg.t === "result") {
        const cb = this.pending.get(msg.id);
        if (cb) { this.pending.delete(msg.id); cb(msg); }
      } else if (msg.t === "stats") {
        this.live = msg;
      } else if (msg.t === "error" || msg.t === "dropped") {
        this.showError(msg.error);
      }
    };
    ws.onclose = (ev) => {
      for (const cb of this.pending.values()) cb({ ok: false, error: "control connection lost" });
      this.pending.clear();
      if (ws.closed || this.id !== id || this.control !== ws) return;
      this.control = null;
      if (ev.code === 4401) {
        auth.ask().then(() => { if (this.id === id && this.mode === "direct") this.openControl(); });
      } else if (ev.code === 4409) {
        // in use by another client (on connect), or taken over by one: never take it back by itself
        this.setMode("precise");
        const taken = (ev.reason || "").includes("taken over");
        this.showError(taken ? "Live control was taken over by another client." : ev.reason);
        if (!taken && confirm("Another client controls this phone live. Take over live control?")) {
          this.setMode("direct", true);
        }
      } else if (ev.code !== 4404 && this.mode === "direct") {
        this.controlTimer = setTimeout(() => this.id === id && this.mode === "direct" && !this.control && this.openControl(),
          ev.code === 4429 ? 10000 : 1500);
      }
    };
  },
  closeControl() {
    clearTimeout(this.controlTimer);
    if (this.control) { this.control.closed = true; this.control.close(); }
    this.control = null;
    this.live = null;
  },
  send(msg) {
    if (this.control && this.control.readyState === WebSocket.OPEN) this.control.send(JSON.stringify(msg));
  },
  /** An action (tap, swipe, type...) over the control socket, in order with live input; REST if it is down. */
  act(type, params = {}) {
    if (!this.control || this.control.readyState !== WebSocket.OPEN) {
      return api("POST", `/api/devices/${encodeURIComponent(this.id)}/${type}`, params)
        .then((r) => r.result, (e) => { this.showError(e.message); throw e; });
    }
    const id = this.nextId++;
    return new Promise((resolve, reject) => {
      this.pending.set(id, (msg) => {
        if (msg.ok) return resolve(msg.result);
        this.showError(msg.error);
        reject(new Error(msg.error));
      });
      this.send({ t: type, id, ...params });
    });
  },
  showError(text) {
    $("dv-error").textContent = text || "";
    clearTimeout(this.errTimer);
    this.errTimer = setTimeout(() => { $("dv-error").textContent = ""; }, 6000);
  },

  /** The calibration page's current link, to open it in Safari by hand (it changes after each calibration). */
  async showCalLink() {
    const id = this.id;
    try {
      const url = (await api("GET", `/api/devices/${encodeURIComponent(id)}/calibration`)).page_url || "";
      if (this.id === id) $("cal-link").textContent = url;
    } catch (e) { /* shown again after the next calibration */ }
  },

  /* -- modes ------------------------------------------------------------------------------- */
  render() {
    for (const b of document.querySelectorAll("#mode-seg button")) b.classList.toggle("on", b.dataset.mode === this.mode);
    const abs = this.mode === "direct" && this.pointerMode === "absolute";
    $("screen").classList.toggle("abs", abs);
    $("mode-hint").textContent = this.mode === "precise" ? HINTS.precise : abs ? HINTS.absolute : HINTS.relative;
  },
  setMode(mode, takeover = false) {
    this.disengage();
    this.mode = mode;
    localStorage.setItem("ihc.mode", mode);
    if (mode === "direct" && this.id && (!this.control || takeover)) {
      this.closeControl();
      this.openControl(takeover);
    } else if (mode !== "direct") {
      this.closeControl();
    }
    this.render();
  },
  norm(ev) {
    const r = $("screen").getBoundingClientRect();
    return [Math.min(1, Math.max(0, (ev.clientX - r.left) / r.width)), Math.min(1, Math.max(0, (ev.clientY - r.top) / r.height))];
  },

  // direct control: engaged = pointer locked (relative) or clicked in (absolute)
  engage() {
    if (this.pointerMode === "absolute") {
      this.setEngaged(true);
      $("screen").focus();
    } else {
      const p = $("screen").requestPointerLock({ unadjustedMovement: true });
      if (p && p.catch) p.catch(() => $("screen").requestPointerLock());
    }
  },
  disengage() {
    if (document.pointerLockElement === $("screen")) document.exitPointerLock();
    this.setEngaged(false);
  },
  setEngaged(on) {
    if (this.engaged && !on) this.releaseAll();
    this.engaged = on;
    $("screen").classList.toggle("engaged", on);
    $("cursor").hidden = !(on && this.pointerMode === "absolute");
  },
  releaseAll() {
    clearTimeout(this.flushTimer);
    this.flushTimer = null;
    this.dx = this.dy = this.wheel = 0;
    this.buttons = this.mods = 0;
    this.keys = [];
    this.send({ t: "release" });
  },
  // input is coalesced here too (at most one message per 8 ms); button changes go out at once
  queueFlush() {
    if (!this.flushTimer) this.flushTimer = setTimeout(() => this.flush(), 8);
  },
  flush() {
    clearTimeout(this.flushTimer);
    this.flushTimer = null;
    if (this.pointerMode === "absolute") {
      if (this.absPos) this.send({ t: "abs", x: this.absPos[0], y: this.absPos[1], buttons: this.buttons, wheel: this.wheel });
    } else if (this.dx || this.dy || this.wheel) {
      this.send({ t: "mouse", dx: this.dx, dy: this.dy, wheel: this.wheel, buttons: this.buttons });
    } else {
      return;
    }
    this.dx = this.dy = this.wheel = 0;
  },
  directMove(ev) {
    if (this.pointerMode === "absolute") {
      this.absPos = this.norm(ev);
      const c = $("cursor");
      c.style.left = this.absPos[0] * 100 + "%";
      c.style.top = this.absPos[1] * 100 + "%";
    } else {
      this.dx += ev.movementX;
      this.dy += ev.movementY;
    }
    this.queueFlush();
  },
  directButtons(ev) {
    if (this.pointerMode === "absolute") this.absPos = this.norm(ev);
    this.buttons = ev.buttons & 7;
    // pending movement travels in the same message and is applied before the button change
    if (this.pointerMode === "absolute") {
      this.send({ t: "abs", x: this.absPos[0], y: this.absPos[1], buttons: this.buttons });
    } else {
      this.send({ t: "mouse", dx: this.dx, dy: this.dy, wheel: this.wheel, buttons: this.buttons });
    }
    this.dx = this.dy = this.wheel = 0;
    clearTimeout(this.flushTimer);
    this.flushTimer = null;
  },
  directKey(ev, down) {
    if (ev.code === "Escape" && down && this.pointerMode === "absolute") return this.disengage();
    ev.preventDefault();
    if (ev.repeat) return;
    const mod = HID_MODS[ev.code], usage = HID_KEYS[ev.code];
    if (mod) this.mods = down ? this.mods | mod : this.mods & ~mod;
    else if (usage) {
      const i = this.keys.indexOf(usage);
      if (down && i < 0 && this.keys.length < 6) this.keys.push(usage);
      if (!down && i >= 0) this.keys.splice(i, 1);
    } else return;
    this.send({ t: "keys", mods: this.mods, keys: this.keys });
  },

  // precise mode: click = tap, drag = swipe, hold = long press, wheel = scroll
  mark(x, y, x2, y2) {
    const pts = (this.status && this.status.screen.points) || [393, 852];
    const ns = "http://www.w3.org/2000/svg", svg = $("overlay");
    const g = document.createElementNS(ns, "g");
    const [X, Y] = [x * pts[0], y * pts[1]];
    g.innerHTML = x2 === undefined ? "" :
      `<line x1="${X}" y1="${Y}" x2="${x2 * pts[0]}" y2="${y2 * pts[1]}" stroke="#ffcc00" stroke-width="3" stroke-linecap="round"/>`;
    g.innerHTML += `<circle cx="${X}" cy="${Y}" r="9" fill="none" stroke="#ffcc00" stroke-width="2.5"/><circle cx="${X}" cy="${Y}" r="2" fill="#ffcc00"/>`;
    svg.appendChild(g);
    return (ok) => {
      for (const el of g.children) el.setAttribute("stroke", ok ? "#3fb950" : "#f85149");
      setTimeout(() => g.remove(), ok ? 1200 : 3000);
    };
  },
  preciseAction(type, params, markArgs) {
    const done = this.mark(...markArgs);
    this.act(type, params).then(() => done(true), () => done(false));
  },
};

/* ---------------------------------------------------------- event wiring -------------------- */

function wire() {
  const screen = $("screen");
  let press = null, wheelAcc = 0, wheelTimer = null, wheelAt = null;

  for (const b of document.querySelectorAll("#mode-seg button")) b.onclick = () => view.setMode(b.dataset.mode);
  screen.addEventListener("contextmenu", (e) => e.preventDefault());

  screen.addEventListener("pointerdown", (ev) => {
    if (view.mode === "direct") {
      if (!view.engaged) return view.engage();
      // relative mode reads buttons from mousedown/mouseup (one event per button), which a
      // preventDefault here would suppress
      if (view.pointerMode === "absolute") {
        ev.preventDefault();
        screen.setPointerCapture(ev.pointerId);
        view.directButtons(ev);
      }
      return;
    }
    ev.preventDefault();
    const [x, y] = view.norm(ev);
    if (ev.button === 2) return view.preciseAction("home", {}, [x, y]);
    if (ev.button === 1) return view.preciseAction("app_switcher", {}, [x, y]);
    if (ev.button !== 0) return;
    screen.setPointerCapture(ev.pointerId);
    press = { x, y, cx: ev.clientX, cy: ev.clientY, t: performance.now() };
  });
  screen.addEventListener("pointerup", (ev) => {
    if (view.mode === "direct") {
      if (view.engaged && view.pointerMode === "absolute") view.directButtons(ev);
      return;
    }
    if (!press || ev.button !== 0) return;
    const p = press;
    press = null;
    const [x, y] = view.norm(ev);
    if (Math.hypot(ev.clientX - p.cx, ev.clientY - p.cy) < 6) {
      const long = performance.now() - p.t > 600;
      view.preciseAction(long ? "long_press" : "tap", { x: p.x, y: p.y }, [p.x, p.y]);
    } else {
      view.preciseAction("swipe", { x1: p.x, y1: p.y, x2: x, y2: y }, [p.x, p.y, x, y]);
    }
  });
  screen.addEventListener("pointermove", (ev) => {
    if (view.mode !== "direct" || !view.engaged || view.pointerMode !== "absolute") return;
    if ((ev.buttons & 7) !== view.buttons) view.directButtons(ev);  // chorded button change
    else view.directMove(ev);
  });
  screen.addEventListener("wheel", (ev) => {
    ev.preventDefault();
    const scale = ev.deltaMode === 1 ? 1 / 3 : ev.deltaMode === 2 ? 3 : 1 / 100;
    if (view.mode === "direct") {
      if (!view.engaged) return;
      wheelAcc += -ev.deltaY * scale;
      const n = Math.trunc(wheelAcc);
      if (n) { wheelAcc -= n; view.wheel += n; if (view.pointerMode === "absolute") view.absPos = view.norm(ev); view.queueFlush(); }
      return;
    }
    wheelAcc += -ev.deltaY * scale;
    wheelAt = view.norm(ev);
    clearTimeout(wheelTimer);
    wheelTimer = setTimeout(() => {
      const amount = Math.max(-20, Math.min(20, Math.round(wheelAcc)));
      wheelAcc = 0;
      if (amount) view.preciseAction("scroll", { x: wheelAt[0], y: wheelAt[1], amount }, wheelAt);
    }, 250);
  }, { passive: false });

  // relative direct control: pointer lock
  document.addEventListener("pointerlockchange", () => view.setEngaged(document.pointerLockElement === screen));
  document.addEventListener("mousemove", (ev) => {
    if (view.engaged && view.pointerMode !== "absolute") view.directMove(ev);
  });
  document.addEventListener("mousedown", (ev) => {
    if (view.engaged && view.pointerMode !== "absolute" && document.pointerLockElement === screen) view.directButtons(ev);
  });
  document.addEventListener("mouseup", (ev) => {
    if (view.engaged && view.pointerMode !== "absolute" && document.pointerLockElement === screen) view.directButtons(ev);
  });
  document.addEventListener("keydown", (ev) => { if (view.engaged) view.directKey(ev, true); });
  document.addEventListener("keyup", (ev) => { if (view.engaged) view.directKey(ev, false); });
  document.addEventListener("pointerdown", (ev) => {
    if (view.engaged && view.pointerMode === "absolute" && !screen.contains(ev.target)) view.disengage();
  });
  window.addEventListener("blur", () => view.disengage());
  document.addEventListener("visibilitychange", () => { if (document.hidden) view.disengage(); });
  new ResizeObserver(() => view.id && view.fit()).observe($("stage"));

  // panel
  for (const b of document.querySelectorAll("[data-act]")) {
    b.onclick = () => {
      const a = b.dataset.act;
      if (a === "spotlight") view.act("key", { combo: "cmd+space" }).catch(() => {});
      else view.act(a).catch(() => {});
    };
  }
  $("type-form").onsubmit = (e) => {
    e.preventDefault();
    const text = $("type-text").value;
    if (text) view.act("type", { text }).then(() => { $("type-text").value = ""; }, () => {});
  };
  $("key-form").onsubmit = (e) => {
    e.preventDefault();
    const combo = $("key-combo").value.trim();
    if (combo) view.act("key", { combo }).catch(() => {});
  };
  const calibrate = async (openPage) => {
    const id = view.id, out = $("cal-status");
    $("cal-start").disabled = $("cal-open").disabled = true;
    const t0 = Date.now();
    const tick = setInterval(() => { out.textContent = `measuring… ${Math.round((Date.now() - t0) / 1000)} s`; }, 500);
    try {
      const r = (await api("POST", `/api/devices/${encodeURIComponent(id)}/calibrate`, openPage ? {} : { open_page: false })).result;
      const c = r.calibration || {}, v = c.validation;
      out.textContent = `done: ${c.method}` + (v ? `, error mean ${v.mean} pt (max ${v.max})` : "");
    } catch (e) {
      out.textContent = "failed: " + e.message;
    } finally {
      clearInterval(tick);
      $("cal-start").disabled = $("cal-open").disabled = false;
      if (view.id === id) view.showCalLink();  // each link works for one calibration
    }
  };
  $("cal-start").onclick = () => calibrate(true);
  $("cal-open").onclick = () => calibrate(false);
}

/* ---------------------------------------------------------- routing ------------------------- */

function route() {
  const m = location.hash.match(/^#\/device\/(.+)$/);
  grid.stop();
  view.stop();
  if (m) view.start(decodeURIComponent(m[1]));
  else grid.start();
}

wire();
window.addEventListener("hashchange", route);
route();

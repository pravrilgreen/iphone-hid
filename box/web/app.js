/* iPhone console: the live screen, touch and keys straight to the phone, the phone's buttons. */
"use strict";

const $ = (id) => document.getElementById(id);

/* ---------------------------------------------------------------- token and API ------------------ */

const auth = {
  token: (() => { try { return localStorage.getItem("ihc.token") || ""; } catch (e) { return ""; } })(),
  asking: null,
  ask() {
    if (!this.asking) {
      this.asking = new Promise((resolve) => {
        const dlg = $("token-dialog");
        $("token-error").textContent = this.token ? "The box refused that token." : "";
        $("token-input").value = "";
        $("token-form").onsubmit = (e) => {
          e.preventDefault();
          const v = $("token-input").value.trim();
          if (!v) return;
          this.token = v;
          try { localStorage.setItem("ihc.token", v); } catch (err) { /* private window: this page only */ }
          dlg.close();
          this.asking = null;
          resolve();
        };
        dlg.oncancel = (e) => e.preventDefault();
        dlg.showModal();
        $("token-input").focus();
      });
    }
    return this.asking;
  },
  query(params = {}) {
    const q = new URLSearchParams(params);
    if (this.token) q.set("token", this.token);
    const s = q.toString();
    return s ? "?" + s : "";
  },
};

const wsURL = (path, params) => (location.protocol === "https:" ? "wss://" : "ws://") + location.host + path + auth.query(params);

async function api(method, path, body) {
  const opts = { method, headers: {} };
  if (auth.token) opts.headers.Authorization = "Bearer " + auth.token;
  if (body !== undefined) {
    opts.headers["Content-Type"] = "application/json";
    opts.body = JSON.stringify(body);
  }
  const r = await fetch(path, opts);
  if (r.status === 401) {
    await auth.ask();
    return api(method, path, body);
  }
  const data = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(data.error || `The box answered ${r.status}`);
  return data;
}

/* ---------------------------------------------------------------- messages --------------------------- */

let toastTimer = null;
function toast(text, kind = "info") {
  const t = $("toast");
  t.textContent = text;
  t.dataset.kind = kind;
  t.hidden = false;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => { t.hidden = true; }, kind === "error" ? 6000 : 2500);
}

function banner(text, actionLabel, action) {
  const b = $("banner");
  if (!text) { b.hidden = true; return; }
  $("banner-text").textContent = text;
  const btn = $("banner-action");
  btn.textContent = actionLabel;
  btn.onclick = () => { b.hidden = true; action(); };
  b.hidden = false;
}

const COARSE = matchMedia("(pointer: coarse)").matches;
const HINT = COARSE ? "Touch to tap, drag to swipe. Use Type for text."
  : "Click to tap, drag to swipe, scroll to scroll. Click the screen, then type.";

const STATES = {
  connecting: "Connecting", starting: "Starting", ready: "Ready", busy: "Busy", asleep: "Asleep", no_usb: "Not connected",
  no_video: "No picture", offline: "Box unreachable",
};

/* ---------------------------------------------------------------- the phone ------------------------------ */

const phone = {
  id: null,
  status: null,
  landscape: false,
  frameSize: [498, 1080],

  async start() {
    let list;
    try {
      list = (await api("GET", "/api/devices")).devices;
    } catch (e) {
      this.setState("offline", e.message);
      setTimeout(() => this.start(), 3000);
      return;
    }
    const st = list[0];
    this.id = st.id;
    $("device-name").textContent = st.id;
    document.title = `${st.id} console`;
    this.onStatus(st);
    video.open();
    control.open();
    setInterval(() => control.ping(), 1000);
  },

  path(suffix = "") { return `/api/devices/${encodeURIComponent(this.id)}${suffix}`; },

  setState(state, message) {
    const el = $("state");
    el.dataset.state = state;
    el.textContent = STATES[state] || state;
    const msg = $("state-msg");
    msg.textContent = message || "";
    if (state === "asleep") {
      const b = document.createElement("button");
      b.textContent = "Wake";
      b.onclick = () => actions.run("wake");
      msg.appendChild(b);
    }
  },

  onStatus(st) {
    this.status = st;
    const busy = st.state === "busy" && st.input && st.input.busy ? `Running ${st.input.busy.replace(/_/g, " ")}` : "";
    this.setState(st.state, st.state === "ready" ? "" : busy || st.message || "");
    const land = !!(st.screen && st.screen.landscape);
    if (land !== this.landscape) { this.landscape = land; layout(); }
    for (const b of document.querySelectorAll(".seg button")) {
      b.setAttribute("aria-checked", String(b.dataset.landscape === String(land)));
    }
    const u = st.usb || {}, v = st.video || {}, i = st.input || {};
    $("f-usb").textContent = u.connected ? `connected (${u.profile || "gadget"})` : (u.state || "–");
    $("f-video").textContent = v.state === "ok"
      ? `${v.width}×${v.height} ${v.format || ""} at ${v.fps} fps`
      : (v.error || v.state || "–");
    $("f-picture").textContent = st.screen && st.screen.width ? `${st.screen.width}×${st.screen.height}, JPEG by ${st.encoder}` : "–";
    $("f-touch").textContent = i.reports ? `${fmtMs(i.latency_p50_ms)} typical, ${fmtMs(i.latency_p95_ms)} at worst` : "idle";
    $("f-box").textContent = `${st.version}, up ${fmtUptime(st.uptime_s)}`;
    if (i.latency_p50_ms) $("m-touch").textContent = fmtMs(i.latency_p50_ms);
  },
};

function fmtMs(v) {
  if (v === undefined || v === null) return "–";
  if (v < 0.1) return "<0.1 ms";
  return v < 10 ? `${v.toFixed(1)} ms` : `${Math.round(v)} ms`;
}

function fmtUptime(s) {
  if (!s) return "just now";
  if (s < 3600) return `${Math.round(s / 60)} min`;
  if (s < 86400) return `${(s / 3600).toFixed(1)} h`;
  return `${Math.round(s / 86400)} days`;
}

/* ---------------------------------------------------------------- layout -------------------------------- */

// Fit the phone to the stage. The frame and bezel take 5.8% of the phone's short side on each side;
// the screen inside keeps the picture's aspect.
const INSET = 0.116;
function layout() {
  const stage = $("stage"), dev = $("device");
  dev.classList.toggle("landscape", phone.landscape);
  const a = video.width && video.height ? video.width / video.height : phone.landscape ? 1080 / 498 : 498 / 1080;
  const reserved = document.querySelector(".nav-keys").offsetHeight + $("hint").offsetHeight + 40;
  const maxH = Math.max(240, stage.clientHeight - reserved);
  const maxW = Math.max(200, stage.clientWidth - 40);
  let w, h;
  if (a < 1) { // portrait: the short side is the width
    w = Math.min(maxW, maxH / ((1 - INSET) / a + INSET));
    h = w * ((1 - INSET) / a + INSET);
  } else {
    h = Math.min(maxH, maxW / ((1 - INSET) * a + INSET));
    w = h * ((1 - INSET) * a + INSET);
  }
  dev.style.setProperty("--w", `${Math.floor(w)}px`);
  dev.style.setProperty("--h", `${Math.floor(h)}px`);
  dev.style.setProperty("--s", `${Math.floor(Math.min(w, h))}px`);
}

/* ---------------------------------------------------------------- video ---------------------------------- */

const video = {
  ws: null, width: 0, height: 0, times: [], age: 0, timer: null,

  open() {
    const ws = (this.ws = new WebSocket(wsURL(phone.path("/stream"), { quality: "80" })));
    ws.binaryType = "arraybuffer";
    const canvas = $("screen"), ctx = canvas.getContext("2d", { alpha: false, desynchronized: true });
    ws.onmessage = async (ev) => {
      if (typeof ev.data === "string") {
        const msg = JSON.parse(ev.data);
        if (msg.t === "status") phone.onStatus(msg.status);
        return;
      }
      const head = new DataView(ev.data, 0, 12);
      const ageBox = head.getUint32(8, true) / 1000;
      const t0 = performance.now();
      try {
        const bmp = await createImageBitmap(new Blob([new Uint8Array(ev.data, 12)], { type: "image/jpeg" }));
        if (canvas.width !== bmp.width || canvas.height !== bmp.height) {
          canvas.width = bmp.width;
          canvas.height = bmp.height;
          this.width = bmp.width;
          this.height = bmp.height;
          layout();
        }
        ctx.drawImage(bmp, 0, 0);
        bmp.close();
        $("screen-note").hidden = true;
      } catch (e) { /* a damaged frame: skip it */ }
      if (ws.readyState === WebSocket.OPEN) ws.send("ack");
      const now = performance.now();
      this.age = ageBox + (now - t0);
      this.times.push(now);
      while (this.times.length && now - this.times[0] > 2000) this.times.shift();
      this.meter();
    };
    ws.onclose = (ev) => {
      if (ev.code === 1008 || ev.code === 4401) { auth.ask().then(() => this.open()); return; }
      $("screen-note").hidden = false;
      $("screen-note").textContent = ev.code === 4429 ? "Too many viewers on this box" : "Reconnecting to the picture";
      setTimeout(() => this.open(), ev.code === 4429 ? 5000 : 1000);
    };
    clearInterval(this.timer);
    this.timer = setInterval(() => this.meter(), 1000);
  },

  meter() {
    const n = this.times.length;
    const now = performance.now();
    if (n < 2 || now - this.times[n - 1] > 1500) { $("m-video").textContent = "–"; return; }
    const fps = (n - 1) / ((this.times[n - 1] - this.times[0]) / 1000);
    $("m-video").textContent = `${Math.round(fps)} fps, ${Math.round(this.age)} ms`;
  },
};

/* ---------------------------------------------------------------- live control -------------------------- */

const MSG = { touch: 1, keys: 2, consumer: 3, release: 4, ping: 5 };

const control = {
  ws: null, ready: false, pending: new Map(), nextId: 1, pingSeq: 0, pingAt: new Map(), retry: null,

  open(takeover = false) {
    const ws = (this.ws = new WebSocket(wsURL(phone.path("/control"), takeover ? { takeover: "true" } : {})));
    ws.binaryType = "arraybuffer";
    ws.onopen = () => { this.ready = true; banner(null); };
    ws.onmessage = (ev) => {
      if (typeof ev.data !== "string") {
        const b = new DataView(ev.data);
        if (b.getUint8(0) === MSG.ping) {
          const seq = b.getUint32(1, true), t = this.pingAt.get(seq);
          if (t !== undefined) {
            this.pingAt.delete(seq);
            $("m-rtt").textContent = fmtMs(performance.now() - t);
          }
        }
        return;
      }
      const msg = JSON.parse(ev.data);
      if (msg.t === "result") {
        const cb = this.pending.get(msg.id);
        if (cb) { this.pending.delete(msg.id); cb(msg); }
      } else if (msg.t === "stats") {
        if (msg.input && msg.input.reports) $("m-touch").textContent = fmtMs(msg.input.latency_p50_ms);
      } else if (msg.t === "error" || msg.t === "dropped") {
        toast(msg.error, "error");
      }
    };
    ws.onclose = (ev) => {
      this.ready = false;
      for (const cb of this.pending.values()) cb({ ok: false, error: "the connection to the box dropped" });
      this.pending.clear();
      if (this.ws !== ws) return;
      if (ev.code === 4409) {
        const taken = (ev.reason || "").includes("taken over");
        banner(taken ? "Another operator took over this phone." : "Another operator is controlling this phone.",
          "Take over", () => this.open(true));
        return;
      }
      if (ev.code === 1008 || ev.code === 4401) { auth.ask().then(() => this.open()); return; }
      clearTimeout(this.retry);
      this.retry = setTimeout(() => this.open(), 1000);
    };
  },

  send(bytes, droppable = false) {
    const ws = this.ws;
    if (!ws || ws.readyState !== WebSocket.OPEN) return false;
    if (droppable && ws.bufferedAmount > 32768) return false; // a slow link: skip moves, never presses
    ws.send(bytes);
    return true;
  },

  touch(x, y, buttons, wheel = 0, droppable = false) {
    const b = new ArrayBuffer(7), v = new DataView(b);
    v.setUint8(0, MSG.touch);
    v.setUint8(1, buttons & 7);
    v.setUint16(2, Math.round(Math.min(1, Math.max(0, x)) * 65535), true);
    v.setUint16(4, Math.round(Math.min(1, Math.max(0, y)) * 65535), true);
    v.setInt8(6, Math.max(-127, Math.min(127, wheel)));
    return this.send(b, droppable);
  },

  keys(mods, keys) {
    const b = new Uint8Array(3 + keys.length);
    b[0] = MSG.keys; b[1] = mods; b[2] = keys.length;
    b.set(keys, 3);
    this.send(b.buffer);
  },

  release() { this.send(new Uint8Array([MSG.release]).buffer); },

  ping() {
    const seq = ++this.pingSeq >>> 0, b = new ArrayBuffer(5), v = new DataView(b);
    v.setUint8(0, MSG.ping);
    v.setUint32(1, seq, true);
    if (this.send(b)) this.pingAt.set(seq, performance.now());
    for (const [k, t] of this.pingAt) if (performance.now() - t > 5000) this.pingAt.delete(k);
  },

  // an action in order with the live input (REST when the socket is down)
  action(name, params = {}) {
    if (!this.ready) return api("POST", phone.path("/" + name), params).then((r) => r.result);
    const id = this.nextId++;
    return new Promise((resolve, reject) => {
      this.pending.set(id, (msg) => (msg.ok ? resolve(msg.result) : reject(new Error(msg.error))));
      this.ws.send(JSON.stringify({ t: "action", id, action: name, ...params }));
    });
  },
};

const actions = {
  run(name, params, done) {
    return control.action(name, params).then((r) => { if (done) toast(done); return r; },
      (e) => { toast(e.message, "error"); throw e; });
  },
};

/* ---------------------------------------------------------------- touch ----------------------------------- */

const touch = {
  buttons: 0, wheelAcc: 0, last: null,

  pos(ev) {
    const r = $("screen").getBoundingClientRect();
    return [(ev.clientX - r.left) / r.width, (ev.clientY - r.top) / r.height];
  },

  wire() {
    const s = $("screen");
    const raw = "onpointerrawupdate" in window;
    s.addEventListener("contextmenu", (e) => e.preventDefault());
    s.addEventListener("pointerdown", (ev) => {
      ev.preventDefault();
      s.focus({ preventScroll: true });
      s.setPointerCapture(ev.pointerId);
      const [x, y] = this.pos(ev);
      this.buttons = ev.pointerType === "mouse" ? ev.buttons & 7 : 1;
      control.touch(x, y, this.buttons);
    });
    const move = (ev) => {
      // mouse: the raw updates when the browser has them (lower latency); touch and pen: moves
      if (raw && ev.type === "pointermove" && ev.pointerType === "mouse") return;
      const [x, y] = this.pos(ev);
      const b = ev.pointerType === "mouse" ? ev.buttons & 7 : this.buttons;
      if (b !== this.buttons) { this.buttons = b; control.touch(x, y, b); return; } // a button changed: never dropped
      if (ev.pointerType !== "mouse" && !this.buttons) return;
      control.touch(x, y, b, 0, true);
    };
    s.addEventListener("pointermove", move);
    if (raw) s.addEventListener("pointerrawupdate", move);
    const up = (ev) => {
      const [x, y] = this.pos(ev);
      this.buttons = ev.pointerType === "mouse" ? ev.buttons & 7 : 0;
      control.touch(x, y, this.buttons);
    };
    s.addEventListener("pointerup", up);
    s.addEventListener("pointercancel", up);
    s.addEventListener("wheel", (ev) => {
      ev.preventDefault();
      const px = ev.deltaMode === 1 ? ev.deltaY * 16 : ev.deltaMode === 2 ? ev.deltaY * 400 : ev.deltaY;
      this.wheelAcc += px;
      const lines = Math.trunc(this.wheelAcc / 40);
      if (!lines) return;
      this.wheelAcc -= lines * 40;
      const [x, y] = this.pos(ev);
      control.touch(x, y, this.buttons, -lines); // wheel up scrolls towards the top
    }, { passive: false });
  },
};

/* ---------------------------------------------------------------- keyboard --------------------------------- */

// KeyboardEvent.code -> keyboard usage (page 0x07): the physical key, whatever the local layout.
const USAGE = (() => {
  const m = {};
  for (let i = 0; i < 26; i++) m["Key" + String.fromCharCode(65 + i)] = 0x04 + i;
  for (let i = 1; i <= 9; i++) m["Digit" + i] = 0x1d + i;
  m.Digit0 = 0x27;
  Object.assign(m, {
    Enter: 0x28, Escape: 0x29, Backspace: 0x2a, Tab: 0x2b, Space: 0x2c, Minus: 0x2d, Equal: 0x2e,
    BracketLeft: 0x2f, BracketRight: 0x30, Backslash: 0x31, Semicolon: 0x33, Quote: 0x34, Backquote: 0x35,
    Comma: 0x36, Period: 0x37, Slash: 0x38, CapsLock: 0x39, Home: 0x4a, PageUp: 0x4b, Delete: 0x4c,
    End: 0x4d, PageDown: 0x4e, ArrowRight: 0x4f, ArrowLeft: 0x50, ArrowDown: 0x51, ArrowUp: 0x52,
    NumpadEnter: 0x58, IntlBackslash: 0x64,
  });
  for (let i = 1; i <= 12; i++) m["F" + i] = 0x39 + i;
  return m;
})();
const MODS = {
  ControlLeft: 0x01, ShiftLeft: 0x02, AltLeft: 0x04, MetaLeft: 0x08,
  ControlRight: 0x10, ShiftRight: 0x20, AltRight: 0x40, MetaRight: 0x80,
};

const keyboard = {
  mods: 0, held: [],

  wire() {
    const s = $("screen"), bezel = $("bezel");
    s.addEventListener("focus", () => {
      bezel.classList.add("keyboard");
      $("hint").textContent = "The keyboard goes to the phone. Click outside the phone to stop.";
    });
    s.addEventListener("blur", () => {
      bezel.classList.remove("keyboard");
      $("hint").textContent = HINT;
      if (this.mods || this.held.length) { this.mods = 0; this.held = []; control.keys(0, []); }
      if (touch.buttons) { touch.buttons = 0; control.release(); }
    });
    s.addEventListener("keydown", (ev) => this.key(ev, true));
    s.addEventListener("keyup", (ev) => this.key(ev, false));
    s.addEventListener("paste", (ev) => {
      const text = (ev.clipboardData || window.clipboardData).getData("text");
      if (text) actions.run("type", { text }, "Pasted on the phone").catch(() => {});
      ev.preventDefault();
    });
  },

  key(ev, down) {
    // Cmd/Ctrl+V pastes the local clipboard onto the phone (typed)
    if (down && (ev.metaKey || ev.ctrlKey) && ev.code === "KeyV") return;
    const mod = MODS[ev.code], usage = USAGE[ev.code];
    if (!mod && !usage) return;
    ev.preventDefault();
    if (ev.repeat) return;
    if (mod) this.mods = down ? this.mods | mod : this.mods & ~mod;
    else {
      const i = this.held.indexOf(usage);
      if (down && i < 0 && this.held.length < 6) this.held.push(usage);
      if (!down && i >= 0) this.held.splice(i, 1);
    }
    control.keys(this.mods, this.held);
  },
};

/* ---------------------------------------------------------------- panel -------------------------------------- */

function wirePanel() {
  for (const b of document.querySelectorAll("[data-button]")) {
    b.addEventListener("click", () => actions.run("button", { name: b.dataset.button }).catch(() => {}));
  }
  for (const b of document.querySelectorAll("[data-act]")) {
    b.addEventListener("click", () => actions.run(b.dataset.act, {}, "Woke the phone").catch(() => {}));
  }
  for (const b of document.querySelectorAll("[data-combo]")) {
    b.addEventListener("click", () => actions.run("key", { combo: b.dataset.combo }).catch(() => {}));
  }
  $("type-form").addEventListener("submit", (e) => {
    e.preventDefault();
    const text = $("type-text").value;
    if (!text) return;
    actions.run("type", { text }, "Typed on the phone").then(() => { $("type-text").value = ""; }, () => {});
  });
  $("type-text").addEventListener("keydown", (e) => {
    if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); $("type-form").requestSubmit(); }
  });
  $("combo-form").addEventListener("submit", (e) => {
    e.preventDefault();
    const combo = $("combo").value.trim();
    if (combo) actions.run("key", { combo }).catch(() => {});
  });
  for (const b of document.querySelectorAll(".seg button")) {
    b.addEventListener("click", async () => {
      try {
        await api("POST", phone.path("/orientation"), { landscape: b.dataset.landscape === "true" });
        phone.landscape = b.dataset.landscape === "true";
        for (const o of document.querySelectorAll(".seg button")) o.setAttribute("aria-checked", String(o === b));
        layout();
      } catch (e) { toast(e.message, "error"); }
    });
  }
  $("shot").addEventListener("click", async () => {
    try {
      const r = await fetch(phone.path("/screenshot") + auth.query({ format: "png" }));
      if (!r.ok) throw new Error((await r.json().catch(() => ({}))).error || "no picture");
      const url = URL.createObjectURL(await r.blob());
      const a = document.createElement("a");
      const stamp = new Date().toISOString().replace(/[:.]/g, "-").slice(0, 19);
      a.href = url;
      a.download = `${phone.id}-${stamp}.png`;
      a.click();
      setTimeout(() => URL.revokeObjectURL(url), 1000);
    } catch (e) { toast(e.message, "error"); }
  });
}

/* ---------------------------------------------------------------- start --------------------------------------- */

$("hint").textContent = HINT;
touch.wire();
keyboard.wire();
wirePanel();
new ResizeObserver(layout).observe($("stage"));
window.addEventListener("blur", () => { if (touch.buttons) { touch.buttons = 0; control.release(); } });
layout();
phone.start();

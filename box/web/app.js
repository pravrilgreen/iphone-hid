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
  btn.setAttribute("aria-label", actionLabel);
  btn.dataset.tip = actionLabel;
  btn.onclick = () => { b.hidden = true; tip.hide(); action(); };
  b.hidden = false;
}

// icon returns a button with one of the console's icons; its label is its tooltip.
function iconButton(icon, label, onclick) {
  const b = document.createElement("button");
  b.className = "icon small";
  b.setAttribute("aria-label", label);
  b.dataset.tip = label;
  b.innerHTML = `<svg class="i"><use href="#i-${icon}"/></svg>`;
  b.onclick = onclick;
  return b;
}

/* ---------------------------------------------------------------- tooltips --------------------------- */

// One tooltip for every [data-tip] control, placed in the page so a scrolling panel cannot clip it.
const tip = {
  el: null, target: null,
  wire() {
    this.el = $("tip");
    const show = (ev) => {
      const t = ev.target.closest && ev.target.closest("[data-tip]");
      if (t && t !== this.target) this.show(t);
    };
    document.addEventListener("pointerover", (ev) => { if (ev.pointerType === "mouse") show(ev); });
    document.addEventListener("focusin", (ev) => { if (ev.target.matches(":focus-visible")) show(ev); });
    document.addEventListener("pointerout", (ev) => { if (this.target && !this.target.contains(ev.relatedTarget)) this.hide(); });
    document.addEventListener("focusout", () => this.hide());
    document.addEventListener("pointerdown", () => this.hide(), true);
  },
  show(t) {
    this.target = t;
    this.el.textContent = t.dataset.tip;
    this.el.hidden = false;
    const r = t.getBoundingClientRect(), w = this.el.offsetWidth, h = this.el.offsetHeight;
    const x = Math.max(8, Math.min(innerWidth - w - 8, r.left + r.width / 2 - w / 2));
    const y = r.top - h - 8 >= 8 ? r.top - h - 8 : r.bottom + 8;
    this.el.style.transform = `translate(${Math.round(x)}px, ${Math.round(y)}px)`;
  },
  hide() { this.target = null; if (this.el) this.el.hidden = true; },
};

const COARSE = matchMedia("(pointer: coarse)").matches;
const HINT = COARSE ? "Touch to tap, drag to swipe. Use Type for text."
  : "Click to tap, drag to swipe, scroll to scroll. Click the screen, then type.";

const STATES = {
  connecting: "Connecting", starting: "Starting", ready: "Ready", busy: "Busy", asleep: "Asleep", no_usb: "Not connected",
  no_video: "No picture", offline: "Box unreachable",
};

// A note over the phone's screen; the last picture stays, greyed, until a new one comes.
function screenNote(text) {
  $("screen-note").hidden = !text;
  if (text) $("screen-note").textContent = text;
  $("bezel").classList.toggle("stale", !!text && video.width > 0);
}

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
    if (state === "asleep") msg.appendChild(iconButton("sun", "Wake the phone", () => actions.run("wake", {}, "Woke the phone").catch(() => {})));
    if (["no_usb", "asleep", "no_video", "offline"].includes(state)) msg.appendChild(iconButton("guide", "Show what to check", () => guide.open("now")));
    guide.update();
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
    if (v.state && v.state !== "ok" && v.state !== "starting") screenNote("No picture from the phone");
    const usbWords = { suspended: "iPhone asleep", "not attached": "nothing plugged in", "no gadget": "not set up" };
    fact("f-usb", u.connected ? "iPhone connected" : (usbWords[u.state] || "iPhone not connected"),
      `USB ${u.state || "?"}${u.profile ? ", profile " + u.profile : ""}${u.udc ? ", controller " + u.udc : ""}`);
    fact("f-video", v.state === "ok" ? `${v.width}×${v.height} at ${Math.round(v.fps)} fps` : "no picture",
      v.state === "ok" ? `${v.format || ""} from ${v.source || "HDMI"}` : (v.error || v.state || ""));
    fact("f-picture", st.screen && st.screen.width ? `${st.screen.width}×${st.screen.height}` : "–", `JPEG by ${st.encoder}`);
    if (control.locked && st.live === false) control.open(); // the other operator left: control is free
    $("f-touch").textContent = i.reports ? `${fmtMs(i.latency_p50_ms)} typical, ${fmtMs(i.latency_p95_ms)} at worst` : "idle";
    $("f-box").textContent = `${st.version}, up ${fmtUptime(st.uptime_s)}`;
    if (i.latency_p50_ms) $("m-touch").textContent = fmtMs(i.latency_p50_ms);
  },
};

// fact sets a Connection line in plain words, with the technical detail in its tooltip.
function fact(id, text, detail) {
  $(id).textContent = text;
  $(id).title = detail || "";
}

// blank clears what the box last reported, once it stopped answering.
function blank() {
  for (const id of ["m-touch", "m-video", "m-rtt"]) $(id).textContent = "–";
  for (const id of ["f-usb", "f-video", "f-picture", "f-touch"]) fact(id, "–", "");
}

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

/* ---------------------------------------------------------------- guide --------------------------------- */

// The set-up and use guide: animated drawings (guide/*.svg, also used by the documentation) and, on
// the "now" tab, the hub drawing coloured by this box's state with what to check.
const guide = {
  tab: "now", art: {},
  titles: { now: "This box now", hub: "Wiring with a USB-C hub", box: "The purpose-built box", use: "Using the console" },
  files: { now: "hub", hub: "hub", box: "box", use: "use" },

  wire() {
    $("guide-open").addEventListener("click", () => this.open(this.tab));
    $("guide-close").addEventListener("click", () => $("guide").close());
    $("guide").addEventListener("click", (e) => { if (e.target === $("guide")) $("guide").close(); });
    for (const b of document.querySelectorAll("[data-tab]")) b.addEventListener("click", () => this.show(b.dataset.tab));
  },

  open(tab) {
    tip.hide();
    if (!$("guide").open) $("guide").showModal();
    this.show(tab);
  },

  async show(tab) {
    this.tab = tab;
    $("guide-title").textContent = this.titles[tab];
    for (const b of document.querySelectorAll("[data-tab]")) b.setAttribute("aria-selected", String(b.dataset.tab === tab));
    const file = this.files[tab];
    if (!this.art[file]) {
      try {
        const r = await fetch(`guide/${file}.svg`);
        this.art[file] = await r.text();
      } catch (e) { this.art[file] = ""; }
    }
    if (this.tab !== tab) return;
    $("guide-art").innerHTML = this.art[file]; // drawn anew: the wiring animation plays again
    this.update();
  },

  // update colours the "now" drawing by the box's state and lists what to check
  update() {
    if (!$("guide").open || this.tab !== "now") { $("guide-checks").hidden = true; return; }
    const st = phone.status || {}, state = $("state").dataset.state;
    const svg = $("guide-art").querySelector("svg");
    const usb = state === "offline" ? "" : st.state === "asleep" ? "asleep" : st.usb && st.usb.connected ? "ok" : "none";
    const video = state === "offline" ? "" : st.video && st.video.state === "ok" ? "ok" : "none";
    if (svg) {
      svg.setAttribute("data-usb", usb);
      svg.setAttribute("data-video", video);
      svg.setAttribute("data-net", state === "offline" ? "down" : "ok");
    }
    const checks = [];
    if (state === "offline") {
      checks.push(["bad", "The box does not answer", "Check its power and its Ethernet cable (6). The console reconnects by itself."]);
    } else {
      checks.push(["ok", "The box answers", `${st.version || ""}`]);
      if (usb === "ok") checks.push(["ok", "The iPhone takes touch and keys", "USB connected"]);
      else if (usb === "asleep") checks.push(["warn", "The iPhone is asleep", "Press Wake next to the state, and set Auto-Lock to Never on the iPhone."]);
      else checks.push(["bad", "The iPhone does not take touch and keys",
        "Check the hub's USB-A into the board's Type-C port (4) with a data cable, the iPhone in the hub (1). Unlock the iPhone and tap Allow for the accessory."]);
      if (video === "ok") checks.push(["ok", "The picture comes in", `${st.video.width}×${st.video.height} at ${Math.round(st.video.fps)} fps`]);
      else checks.push(["bad", "No picture", "Check the hub's HDMI into the board's HDMI IN (3) and the charger in the hub (2). Unlock the iPhone."]);
      checks.push(control.locked ? ["warn", "Another operator controls the phone", "Take over from the banner to control it here."]
        : ["ok", "You control the phone", ""]);
    }
    const ul = $("guide-checks");
    ul.hidden = false;
    ul.replaceChildren(...checks.map(([level, title, detail]) => {
      const li = document.createElement("li");
      li.dataset.level = level;
      li.innerHTML = `<span class="dot"></span><strong></strong><span class="detail"></span>`;
      li.querySelector("strong").textContent = title;
      li.querySelector(".detail").textContent = detail;
      return li;
    }));
  },
};

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
  ws: null, width: 0, height: 0, times: [], age: 0, timer: null, drawn: 0,

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
      const seq = head.getUint32(4, true);
      const ageBox = head.getUint32(8, true) / 1000;
      const t0 = performance.now();
      try {
        const bmp = await createImageBitmap(new Blob([new Uint8Array(ev.data, 12)], { type: "image/jpeg" }));
        if (seq < this.drawn && this.drawn - seq < 1 << 30) { bmp.close(); throw new Error("an older frame"); }
        this.drawn = seq;
        if (canvas.width !== bmp.width || canvas.height !== bmp.height) {
          canvas.width = bmp.width;
          canvas.height = bmp.height;
          this.width = bmp.width;
          this.height = bmp.height;
          layout();
        }
        ctx.drawImage(bmp, 0, 0);
        bmp.close();
        screenNote(null);
      } catch (e) { /* a damaged frame: skip it */ }
      if (ws.readyState === WebSocket.OPEN) ws.send("ack " + seq);
      const now = performance.now();
      this.age = ageBox + (now - t0);
      this.times.push(now);
      while (this.times.length && now - this.times[0] > 2000) this.times.shift();
      this.meter();
    };
    ws.onclose = (ev) => {
      if (ev.code === 1008 || ev.code === 4401) { auth.ask().then(() => this.open()); return; }
      this.drawn = 0;
      if (ev.code === 4429) {
        screenNote("Too many viewers on this box");
      } else {
        screenNote("Reconnecting to the picture");
        phone.setState("offline", "reconnecting");
        blank();
        // a refused token shows up as an abnormal close: ask the box, which asks for the token on 401
        if (ev.code === 1006) api("GET", "/api/health").then(() => api("GET", phone.path())).catch(() => {});
      }
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
  ws: null, ready: false, locked: false, pending: new Map(), nextId: 1, pingSeq: 0, pingAt: new Map(), retry: null,

  open(takeover = false) {
    if (this.ws && this.ws.readyState === WebSocket.CONNECTING) return;
    const ws = (this.ws = new WebSocket(wsURL(phone.path("/control"), takeover ? { takeover: "true" } : {})));
    ws.binaryType = "arraybuffer";
    ws.onopen = () => { this.ready = true; this.locked = false; banner(null); };
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
        // the state line already says why hover and touches cannot reach an unplugged or sleeping phone
        const st = phone.status && phone.status.state;
        if (msg.t === "error" && (st === "no_usb" || st === "asleep")) return;
        toast(msg.error, "error");
      }
    };
    ws.onclose = (ev) => {
      this.ready = false;
      for (const cb of this.pending.values()) cb({ ok: false, error: "the connection to the box dropped" });
      this.pending.clear();
      if (this.ws !== ws) return;
      if (ev.code === 4409) {
        this.locked = true; // another operator has the phone: this console only watches
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

  // an action in order with the live input (REST while the socket is still opening)
  action(name, params = {}) {
    if (this.locked) return Promise.reject(new Error("Another operator is controlling this phone: Take over first."));
    if (phone.status && $("state").dataset.state === "offline") return Promise.reject(new Error("The box is unreachable."));
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
  buttons: 0, wheelAcc: 0, last: null, kind: "mouse",

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
      this.kind = ev.pointerType;
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
    const s = $("screen"), device = $("device");
    s.addEventListener("focus", () => {
      if (touch.kind === "touch") return; // a finger brings no keyboard: Type is the way to write
      device.classList.add("keyboard");
      $("hint").textContent = "The keyboard goes to the phone. Click outside the phone to stop.";
    });
    s.addEventListener("blur", () => {
      device.classList.remove("keyboard");
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
    if (mod) {
      this.mods = down ? this.mods | mod : this.mods & ~mod;
      // macOS sends no keyup for keys released while Cmd was down: let them go with Cmd
      if (!down && (ev.code === "MetaLeft" || ev.code === "MetaRight")) this.held = [];
    } else {
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
tip.wire();
guide.wire();
touch.wire();
keyboard.wire();
wirePanel();
new ResizeObserver(layout).observe($("stage"));
window.addEventListener("blur", () => { if (touch.buttons) { touch.buttons = 0; control.release(); } });
layout();
phone.start();

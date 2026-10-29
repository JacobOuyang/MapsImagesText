// The offline kit: turns #codes inside text messages into pictures, maps and forms, and makes codes to send back.
// Shared by sim/kit.html (the page a person saves on their phone) and the simulator. Everything runs locally; the
// server injects KIT_PACK (checked how-to entries) and KIT_PUBKEY (the alert issuer's Ed25519 key) above this file.
// Code format (evac/codes.py): #<type><id>.<i>/<n>:<base64 chunk>:<crc16>#  types P picture, Z zoom on a picture,
// K a picture's named parts, M map, G route (-> GPX), T compressed text, L location, S phrasebook entry, A signature.
"use strict";
var KIT = (function () {
  const CODE = /#([PZKMGTLSA])([0-9a-z]{2})\.(\d+)\/(\d+):([A-Za-z0-9+/=]*):([0-9a-f]{4})#/g;
  const PART_CHARS = 153;

  function crc16(s) {
    let c = 0xFFFF;
    for (let i = 0; i < s.length; i++) {
      c ^= s.charCodeAt(i) << 8;
      for (let k = 0; k < 8; k++) c = c & 0x8000 ? ((c << 1) ^ 0x1021) & 0xFFFF : (c << 1) & 0xFFFF;
    }
    return c.toString(16).padStart(4, "0");
  }
  const b64bytes = s => Uint8Array.from(atob(s), ch => ch.charCodeAt(0));
  const bytesb64 = b => btoa(String.fromCharCode.apply(null, Array.from(b)));

  // ---------- reading ----------
  function parse(text) {
    const got = {}, bad = [];
    for (const m of (text || "").replace(/\s+/g, " ").matchAll(CODE)) {
      const [, kind, id, i, n, chunk, crc] = m;
      if (crc16(chunk) !== crc) { bad.push([id, +i]); continue; }
      const g = got[id] = got[id] || {kind, n: +n, parts: {}};
      g.parts[+i] = chunk;
    }
    const complete = [], missing = {};
    for (const [id, g] of Object.entries(got)) {
      const lost = [];
      for (let k = 1; k <= g.n; k++) if (!(k in g.parts)) lost.push(k);
      if (lost.length) { missing[id] = lost; continue; }
      let b64 = ""; for (let k = 1; k <= g.n; k++) b64 += g.parts[k];
      complete.push({kind: g.kind, id, bytes: b64bytes(b64)});
    }
    for (const [id, i] of bad) (missing[id] = missing[id] || []).includes(i) || missing[id].push(i);
    return {complete, missing};
  }
  const plain = text => (text || "").replace(CODE, " ").split(/\s+/).filter(Boolean).join(" ");
  async function inflate(bytes) {
    const ds = new DecompressionStream("deflate-raw");
    const out = await new Response(new Blob([bytes]).stream().pipeThrough(ds)).arrayBuffer();
    return new TextDecoder().decode(out);
  }

  // ---------- writing ----------
  function encode(kind, bytes) {
    const b64 = bytesb64(bytes), id = Math.random().toString(36).slice(2, 4).padEnd(2, "0");
    const head = ("#" + kind + id + ".9/9:").length + ":xxxx#".length, size = PART_CHARS - head - 2;
    const chunks = []; for (let i = 0; i < b64.length; i += size) chunks.push(b64.slice(i, i + size));
    if (!chunks.length) chunks.push("");
    return chunks.map((c, i) => `#${kind}${id}.${i + 1}/${chunks.length}:${c}:${crc16(c)}#`);
  }

  // A real 1-bit PNG (a canvas would give a much bigger 32-bit one).
  const CRC_TABLE = (() => { const t = []; for (let n = 0; n < 256; n++) { let c = n; for (let k = 0; k < 8; k++) c = c & 1 ? 0xEDB88320 ^ (c >>> 1) : c >>> 1; t[n] = c >>> 0; } return t; })();
  function crc32(bytes) { let c = 0xFFFFFFFF; for (const b of bytes) c = CRC_TABLE[(c ^ b) & 0xFF] ^ (c >>> 8); return (c ^ 0xFFFFFFFF) >>> 0; }
  function chunk(type, data) {
    const out = new Uint8Array(12 + data.length), dv = new DataView(out.buffer);
    dv.setUint32(0, data.length); for (let i = 0; i < 4; i++) out[4 + i] = type.charCodeAt(i);
    out.set(data, 8); dv.setUint32(8 + data.length, crc32(out.subarray(4, 8 + data.length))); return out;
  }
  async function png1bit(bits, w, h) {  // bits[y*w+x] = 1 for black
    const stride = Math.ceil(w / 8), raw = new Uint8Array((stride + 1) * h);
    for (let y = 0; y < h; y++) for (let x = 0; x < w; x++) if (!bits[y * w + x]) raw[y * (stride + 1) + 1 + (x >> 3)] |= 0x80 >> (x & 7);
    const z = new Uint8Array(await new Response(new Blob([raw]).stream().pipeThrough(new CompressionStream("deflate"))).arrayBuffer());
    const ihdr = new Uint8Array(13), dv = new DataView(ihdr.buffer); dv.setUint32(0, w); dv.setUint32(4, h); ihdr[8] = 1; ihdr[9] = 0;
    const parts = [Uint8Array.from([137, 80, 78, 71, 13, 10, 26, 10]), chunk("IHDR", ihdr), chunk("IDAT", z), chunk("IEND", new Uint8Array(0))];
    const out = new Uint8Array(parts.reduce((n, p) => n + p.length, 0)); let o = 0; for (const p of parts) { out.set(p, o); o += p.length; }
    return out;
  }
  // A photo -> 128 px black and white -> 1-bit PNG. By default a plain threshold (Otsu's, so dark photos work): flat
  // areas compress to almost nothing (~300-1000 bytes, 3-9 SMS). dither=true keeps tones with Atkinson dithering, but the
  // scattered dots don't compress (~1500-2200 bytes, 12-17 SMS).
  async function photoBits(file, side = 128, dither = false) {
    const img = await createImageBitmap(file), c = document.createElement("canvas");
    const s = side / Math.max(img.width, img.height), w = Math.round(img.width * s), h = Math.round(img.height * s);
    c.width = w; c.height = h; const g = c.getContext("2d"); g.drawImage(img, 0, 0, w, h);
    const d = g.getImageData(0, 0, w, h).data, L = new Float32Array(w * h);
    for (let i = 0; i < w * h; i++) L[i] = 0.299 * d[4 * i] + 0.587 * d[4 * i + 1] + 0.114 * d[4 * i + 2];
    const bits = new Uint8Array(w * h);
    if (!dither) {
      const hist = new Float64Array(256); for (const v of L) hist[Math.min(255, v | 0)]++;
      let sum = 0; for (let t = 0; t < 256; t++) sum += t * hist[t];
      let best = 128, top = -1, n0 = 0, s0 = 0;
      for (let t = 0; t < 256; t++) {
        n0 += hist[t]; s0 += t * hist[t]; const n1 = w * h - n0; if (!n0 || !n1) continue;
        const between = n0 * n1 * (s0 / n0 - (sum - s0) / n1) ** 2; if (between > top) { top = between; best = t; }
      }
      for (let i = 0; i < w * h; i++) bits[i] = L[i] <= best ? 1 : 0;
      return {bytes: await png1bit(bits, w, h), w, h};
    }
    for (let y = 0; y < h; y++) for (let x = 0; x < w; x++) {
      const i = y * w + x, v = L[i], b = v < 128 ? 1 : 0, err = (v - (b ? 0 : 255)) / 8; bits[i] = b;
      for (const [dx, dy] of [[1, 0], [2, 0], [-1, 1], [0, 1], [1, 1], [0, 2]]) {
        const xx = x + dx, yy = y + dy; if (xx >= 0 && xx < w && yy < h) L[yy * w + xx] += err;
      }
    }
    return {bytes: await png1bit(bits, w, h), w, h};
  }
  // A photo -> 128 px greyscale WebP (quality 5, contrast stretched), the server's own format for photos. Browsers
  // that can't write WebP (Safari) give null, and the kit falls back to black and white.
  async function photoWebp(file, side = 128) {
    const img = await createImageBitmap(file), c = document.createElement("canvas");
    const s = side / Math.max(img.width, img.height), w = Math.round(img.width * s), h = Math.round(img.height * s);
    c.width = w; c.height = h; const g = c.getContext("2d"); g.drawImage(img, 0, 0, w, h);
    const im = g.getImageData(0, 0, w, h), d = im.data, L = new Float32Array(w * h);
    for (let i = 0; i < w * h; i++) L[i] = 0.299 * d[4 * i] + 0.587 * d[4 * i + 1] + 0.114 * d[4 * i + 2];
    const sorted = Float32Array.from(L).sort(), lo = sorted[Math.floor(0.01 * L.length)], hi = sorted[Math.floor(0.99 * L.length)];
    for (let i = 0; i < w * h; i++) { const v = Math.max(0, Math.min(255, (L[i] - lo) * 255 / Math.max(1, hi - lo))); d[4 * i] = d[4 * i + 1] = d[4 * i + 2] = v; }
    g.putImageData(im, 0, 0);
    const blob = await new Promise(ok => c.toBlob(ok, "image/webp", 0.05));
    return blob && blob.type === "image/webp" ? {bytes: new Uint8Array(await blob.arrayBuffer()), w, h} : null;
  }
  function locationBytes(o) { return new TextEncoder().encode(JSON.stringify(o)); }

  // ---------- showing ----------
  const el = (tag, css, text) => { const e = document.createElement(tag); if (css) e.style.cssText = css; if (text !== undefined) e.textContent = text; return e; };
  const imageType = b => b[0] === 0x52 && b[1] === 0x49 && b[2] === 0x46 ? "image/webp" : "image/png";  // "RIFF": WebP
  const dataUrl = b => "data:" + imageType(b) + ";base64," + bytesb64(b);
  function pngImg(bytes) { const im = el("img", "width:128px;image-rendering:pixelated;background:#fff;border:1px solid #ccc;border-radius:4px"); im.src = dataUrl(bytes); return im; }
  async function loaded(im) { if (!im.complete) await new Promise(r => { im.onload = r; im.onerror = r; }); return im; }
  // A before/after pair: flips in place (small changes show when they move) and zooms on what changed.
  async function pairView(aBytes, bBytes, caption) {
    const box = el("div", "display:inline-block;max-width:100%;margin:6px 10px 6px 0;vertical-align:top");
    const a = await loaded(pngImg(aBytes)), b = await loaded(pngImg(bBytes)), flip = pngImg(aBytes);
    flip.style.cssText += ";width:160px;max-width:100%"; let on = false;
    const timer = setInterval(() => { if (!flip.isConnected && box.isConnected === false && on) { clearInterval(timer); return; } on = !on; flip.src = (on ? b : a).src; tag.textContent = on ? "after" : "before"; }, 900);
    const tag = el("div", "font-size:11px;color:#777", "before");
    const w = a.naturalWidth || 128, h = a.naturalHeight || 128, ca = el("canvas"), cb = el("canvas");
    ca.width = cb.width = w; ca.height = cb.height = h;
    const ga = ca.getContext("2d"), gb = cb.getContext("2d"); ga.drawImage(a, 0, 0); gb.drawImage(b, 0, 0);
    const da = ga.getImageData(0, 0, w, h).data, db = gb.getImageData(0, 0, w, h).data;
    let x0 = w, y0 = h, x1 = -1, y1 = -1;
    for (let y = 0; y < h; y++) for (let x = 0; x < w; x++) if ((da[4 * (y * w + x)] < 128) !== (db[4 * (y * w + x)] < 128)) { x0 = Math.min(x0, x); y0 = Math.min(y0, y); x1 = Math.max(x1, x); y1 = Math.max(y1, y); }
    const row = el("div", "display:flex;flex-wrap:wrap;gap:8px;align-items:flex-start");
    const left = el("div", "flex:1 1 120px;max-width:160px"); left.append(flip, tag); row.appendChild(left);
    if (x1 >= 0) {  // zoomed inset of the changed area, before and after
      const pad = 6, zx = Math.max(0, x0 - pad), zy = Math.max(0, y0 - pad), zw = Math.min(w, x1 + pad) - zx, zh = Math.min(h, y1 + pad) - zy;
      const z = el("div", "font-size:11px;color:#777"); z.appendChild(el("div", "", "what changed (zoomed)"));
      for (const src of [a, b]) {
        const c = el("canvas", "image-rendering:pixelated;border:1px solid #ccc;margin:2px;background:#fff;max-width:100%"); const k = Math.max(1, Math.floor(80 / Math.max(zw, zh)));
        c.width = zw * k; c.height = zh * k; const g = c.getContext("2d"); g.imageSmoothingEnabled = false; g.drawImage(src, zx, zy, zw, zh, 0, 0, zw * k, zh * k); z.appendChild(c);
      }
      row.appendChild(z);
    }
    if (caption) box.appendChild(el("div", "font-size:12px;font-weight:600;margin-bottom:2px", caption));
    box.appendChild(row); return box;
  }
  function mapView(m) {
    const s = m.s || 5, NS = "http://www.w3.org/2000/svg", pts = [[0, 0]];
    for (const [, p] of m.n || []) pts.push(...p);
    if (m.r) pts.push(...m.r); if (m.z) pts.push([m.z[0] - m.z[2], m.z[1] - m.z[2]], [m.z[0] + m.z[2], m.z[1] + m.z[2]]);
    if (m.g) pts.push([m.g[1], m.g[2]]);
    const half = Math.max(20, Math.min((m.h || 300) / s, Math.max(...pts.map(p => Math.max(Math.abs(p[0]), Math.abs(p[1]))))));
    const W = 300, k = W / (2 * half), X = v => W / 2 + v * k, Y = v => W / 2 - v * k;
    const svg = document.createElementNS(NS, "svg"); svg.setAttribute("viewBox", `0 0 ${W} ${W}`);
    svg.style.cssText = "width:100%;max-width:300px;height:auto;display:block;background:#fff;border:1px solid #ccc;border-radius:6px;font:9px system-ui";
    const add = (tag, attrs, text) => { const e = document.createElementNS(NS, tag); for (const [a, v] of Object.entries(attrs)) e.setAttribute(a, v); if (text) e.textContent = text; svg.appendChild(e); return e; };
    const line = p => p.map(q => X(q[0]) + "," + Y(q[1])).join(" ");
    // Labels are placed last, most important first, each at the first candidate spot that is inside the map and clear
    // of every label already placed; a label with no clear spot is dropped rather than drawn over another.
    const labels = [], taken = [[W - 22, 2, W - 2, 18], [6, W - 26, 80, W - 4]];  // north arrow, scale bar
    const label = (text, spots, attrs) => labels.push({text, spots, attrs});
    if (m.z) add("circle", {cx: X(m.z[0]), cy: Y(m.z[1]), r: m.z[2] * k, fill: "#e03131", "fill-opacity": .15, stroke: "#e03131", "stroke-dasharray": "4 3"});
    const streets = [], closed = [];
    for (const [name, p] of m.n || []) {
      add("polyline", {points: line(p), fill: "none", stroke: "#999", "stroke-width": 3, "stroke-linecap": "round"});
      streets.push([name, [.5, .3, .7, .15, .85].map(f => p[Math.floor(f * (p.length - 1))]).map(q => [X(q[0]) + 3, Y(q[1]) - 3])]);
    }
    for (const [name, a, b] of m.c || []) { add("line", {x1: X(a[0]), y1: Y(a[1]), x2: X(b[0]), y2: Y(b[1]), stroke: "#e03131", "stroke-width": 5});
      closed.push([name + " closed", [[X(b[0]) + 4, Y(b[1]) + 3], [X(a[0]) + 4, Y(a[1]) + 3], [(X(a[0]) + X(b[0])) / 2 + 5, (Y(a[1]) + Y(b[1])) / 2]]]); }
    if (m.r) add("polyline", {points: line(m.r), fill: "none", stroke: "#1971c2", "stroke-width": 3, "stroke-dasharray": "6 3"});
    if (m.g) { add("rect", {x: X(m.g[1]) - 5, y: Y(m.g[2]) - 5, width: 10, height: 10, fill: "#2b8a3e"}); }
    for (const [, x, y] of m.l || []) add("circle", {cx: X(x), cy: Y(y), r: 3, fill: "#7048e8"});
    add("circle", {cx: X(0), cy: Y(0), r: 6, fill: "#e03131", stroke: "#fff", "stroke-width": 2});
    taken.push([X(0) - 7, Y(0) - 7, X(0) + 7, Y(0) + 7]);
    const around = (x, y, w) => [[x + 8, y + 3], [x - 8 - w, y + 3], [x - w / 2, y - 9], [x - w / 2, y + 14]];
    label("you", around(X(0), Y(0), 18), {"font-weight": 700});
    if (m.z) label((m.k || "danger").toUpperCase(), [[X(m.z[0]) - 12, Y(m.z[1]) + 3], [X(m.z[0]) - 12, Y(m.z[1]) - 8], [X(m.z[0]) - 12, Y(m.z[1]) + 14]], {fill: "#e03131", "font-weight": 700});
    for (const [t, sp] of closed) label(t, sp, {fill: "#e03131", "font-weight": 600});
    if (m.g) label(m.g[0], around(X(m.g[1]), Y(m.g[2]), m.g[0].length * 5), {fill: "#2b8a3e", "font-weight": 600});
    for (const [name, x, y] of m.l || []) label(name, around(X(x), Y(y), name.length * 4.6), {fill: "#7048e8"});
    for (const [name, spots] of streets) label(name, spots, {fill: "#555"});
    for (const {text, spots, attrs} of labels) {
      const w = text.length * (attrs["font-weight"] ? 5.2 : 4.6);
      const spot = spots.find(([x, y]) => x >= 2 && x + w <= W - 2 && y - 8 >= 2 && y + 2 <= W - 2 &&
        !taken.some(([a, b, c, d]) => x < c && x + w > a && y - 8 < d && y + 2 > b));
      if (!spot) continue;
      taken.push([spot[0], spot[1] - 8, spot[0] + w, spot[1] + 2]);
      add("text", Object.assign({x: spot[0], y: spot[1], stroke: "#fff", "stroke-width": 3, "paint-order": "stroke"}, attrs), text);
    }
    const bar = Math.pow(10, Math.floor(Math.log10(half * s))); add("line", {x1: 10, y1: W - 12, x2: 10 + bar / s * k, y2: W - 12, stroke: "#000", "stroke-width": 2});
    add("text", {x: 10, y: W - 16}, bar + " m"); add("text", {x: W - 18, y: 14, "font-weight": 700}, "N↑");
    return svg;
  }
  // ---------- pictures you can zoom into ----------
  // Every picture seen is remembered (the kit page also saves them), so a zoom that arrives in a later message is
  // pasted over its picture: the picture gets sharper where the person looked.
  const SEEN = {};  // id -> {bytes, w, h, parent, box: [x, y, w, h] in the parent's pixels, keys: [[name, x, y, w, h]]}
  let onRequest = null;  // the simulator sends requests itself; on a phone the person copies them into an SMS
  async function remember(id, bytes, parent, box) {
    const im = await loaded(Object.assign(new Image(), {src: dataUrl(bytes)}));
    SEEN[id] = Object.assign(SEEN[id] || {}, {bytes, w: im.naturalWidth, h: im.naturalHeight, parent, box, im});
  }
  const rootOf = id => { while (SEEN[id] && SEEN[id].parent && SEEN[SEEN[id].parent]) id = SEEN[id].parent; return id; };
  // Where picture `id` sits in its root picture's pixels: [x, y, w, h].
  function rectIn(id) {
    const p = SEEN[id];
    if (!p.parent || !SEEN[p.parent]) return [0, 0, p.w, p.h];
    const [px, py, pw] = rectIn(p.parent), k = pw / SEEN[p.parent].w;
    return [px + p.box[0] * k, py + p.box[1] * k, p.box[2] * k, p.box[3] * k];
  }
  const depth = id => SEEN[id].parent && SEEN[SEEN[id].parent] ? 1 + depth(SEEN[id].parent) : 0;
  // One picture as a canvas: `id` alone, or (together) its root with every zoom pasted in. Drag a box, or tap a
  // numbered part, to ask for a closer look; the request names the most zoomed picture the box lies in.
  function zoomView(id, together, caption) {
    const root = together ? rootOf(id) : id, R = SEEN[root], S = 3;
    const box = el("div", "margin:6px 0;max-width:100%"), c = el("canvas", "width:100%;max-width:" + (together ? 300 : 240) + "px;display:block;border:1px solid #ccc;border-radius:4px;background:#fff;touch-action:none;cursor:crosshair");
    c.width = R.w * S; c.height = R.h * S;
    const g = c.getContext("2d");
    const inView = Object.keys(SEEN).filter(k => together ? rootOf(k) === root && k !== root : false).sort((a, b) => depth(a) - depth(b));
    const pos = k => { if (!together) return [0, 0, R.w, R.h]; return rectIn(k); };
    const keys = [];  // numbered parts on screen: [picture id, n, x, y, w, h] in root pixels
    function draw(sel) {
      g.imageSmoothingEnabled = imageType(R.bytes) === "image/webp"; g.fillStyle = "#fff"; g.fillRect(0, 0, c.width, c.height);
      g.drawImage(R.im, 0, 0, c.width, c.height);
      for (const k of inView) { const [x, y, w, h] = pos(k); g.imageSmoothingEnabled = true; g.drawImage(SEEN[k].im, x * S, y * S, w * S, h * S);
        g.setLineDash([4, 3]); g.strokeStyle = "#1971c2"; g.lineWidth = 1.5; g.strokeRect(x * S, y * S, w * S, h * S); g.setLineDash([]); }
      keys.length = 0;
      for (const k of [root].concat(inView)) for (const [i, [name, x, y, w, h]] of (SEEN[k].keys || []).entries()) {
        const [ox, oy, ow] = pos(k), f = ow / SEEN[k].w, r = [ox + x * f, oy + y * f, w * f, h * f]; keys.push([k, i + 1, ...r]);
        g.strokeStyle = "rgba(25,113,194,.7)"; g.lineWidth = 1; g.strokeRect(r[0] * S, r[1] * S, r[2] * S, r[3] * S);
        g.fillStyle = "#1971c2"; g.beginPath(); g.arc(r[0] * S + 9, r[1] * S + 9, 8, 0, 7); g.fill();
        g.fillStyle = "#fff"; g.font = "bold 11px system-ui"; g.textAlign = "center"; g.fillText(String(i + 1), r[0] * S + 9, r[1] * S + 13);
      }
      if (sel) { g.strokeStyle = "#e03131"; g.lineWidth = 2; g.strokeRect(sel[0] * S, sel[1] * S, sel[2] * S, sel[3] * S); }
    }
    draw();
    const req = el("div", "font-size:12px;color:#555;margin-top:3px", "Drag a box (or tap a numbered part) for a closer look.");
    const at = e => { const r = c.getBoundingClientRect(); return [(e.clientX - r.left) / r.width * R.w, (e.clientY - r.top) / r.height * R.h]; };
    let start = null;
    c.onpointerdown = e => { start = at(e); c.setPointerCapture(e.pointerId); };
    c.onpointermove = e => { if (!start) return; const p = at(e); draw([Math.min(start[0], p[0]), Math.min(start[1], p[1]), Math.abs(p[0] - start[0]), Math.abs(p[1] - start[1])]); };
    c.onpointerup = e => {
      if (!start) return; const p = at(e), b = [Math.min(start[0], p[0]), Math.min(start[1], p[1]), Math.abs(p[0] - start[0]), Math.abs(p[1] - start[1])]; start = null;
      let text;
      if (b[2] < 3 && b[3] < 3) {  // a tap: the smallest numbered part under it
        const hit = keys.filter(([, , x, y, w, h]) => p[0] >= x && p[0] <= x + w && p[1] >= y && p[1] <= y + h).sort((a, b) => a[4] * a[5] - b[4] * b[5])[0];
        if (!hit) { draw(); return; }
        text = `ZOOM ${hit[0]} ${hit[1]}`;
      } else {  // a box: in the pixels of the most zoomed picture that holds it all
        const inside = [root].concat(inView).filter(k => { const [x, y, w, h] = pos(k); return b[0] >= x && b[1] >= y && b[0] + b[2] <= x + w && b[1] + b[3] <= y + h; });
        const k = inside.sort((a, b) => depth(b) - depth(a))[0] || root, [x, y, w] = pos(k), f = SEEN[k].w / w;
        text = `ZOOM ${k} ${[(b[0] - x) * f, (b[1] - y) * f, b[2] * f, b[3] * f].map(v => Math.max(1, Math.round(v))).join(" ")}`;
      }
      draw();
      req.innerHTML = ""; req.append("Ask for a closer look: ", el("code", "font-weight:700", text), " ");
      if (onRequest) { const b2 = el("button", "font:inherit;margin-left:4px", "Send"); b2.onclick = () => onRequest(text); req.appendChild(b2); }
      else { const b2 = el("button", "font:inherit;margin-left:4px", "Copy"); b2.onclick = () => navigator.clipboard && navigator.clipboard.writeText(text); req.appendChild(b2); }
    };
    if (caption) box.appendChild(el("div", "font-size:12px;font-weight:600;margin-bottom:2px", caption));
    box.append(c, req); return box;
  }

  // ---------- routes -> GPX ----------
  function unpolyline(s) {
    const pts = []; let i = 0, lat = 0, lon = 0;
    while (i < s.length) {
      for (const which of [0, 1]) { let shift = 0, v = 0, b; do { b = s.charCodeAt(i++) - 63; v |= (b & 0x1f) << shift; shift += 5; } while (b >= 0x20);
        const d = v & 1 ? ~(v >> 1) : v >> 1; if (which) lon += d; else lat += d; }
      pts.push([lat / 1e5, lon / 1e5]);
    }
    return pts;
  }
  const xml = t => t.replace(/[<>&"]/g, ch => ({"<": "&lt;", ">": "&gt;", "&": "&amp;", '"': "&quot;"}[ch]));
  function gpx(name, pts) {
    const p = ([la, lo]) => `lat="${la.toFixed(5)}" lon="${lo.toFixed(5)}"`, [end] = pts.slice(-1);
    return `<?xml version="1.0" encoding="UTF-8"?>\n<gpx version="1.1" creator="Low-Data Kit" xmlns="http://www.topografix.com/GPX/1/1">\n` +
      `<wpt ${p(end)}><name>${xml(name)}</name></wpt>\n<trk><name>Walk to ${xml(name)}</name><trkseg>\n` +
      pts.map(q => `<trkpt ${p(q)}/>`).join("\n") + `\n</trkseg></trk>\n</gpx>\n`;
  }
  function routeView(name, pts) {
    const box = el("div", "margin:6px 0"), R = 6371000, rad = Math.PI / 180;
    let m = 0; for (let i = 1; i < pts.length; i++) { const [a, b] = [pts[i - 1], pts[i]]; m += R * Math.hypot((b[0] - a[0]) * rad, (b[1] - a[1]) * rad * Math.cos(a[0] * rad)); }
    const NS = "http://www.w3.org/2000/svg", svg = document.createElementNS(NS, "svg"), W = 300, H = 160;
    const lats = pts.map(p => p[0]), lons = pts.map(p => p[1]), c = Math.cos(lats[0] * rad);
    const x0 = Math.min(...lons), y1 = Math.max(...lats), span = Math.max((Math.max(...lons) - x0) * c, y1 - Math.min(...lats)) || 1e-4;
    const k = Math.min(W - 20, H - 20) / span, X = lo => 10 + (lo - x0) * c * k, Y = la => 10 + (y1 - la) * k;
    svg.setAttribute("viewBox", `0 0 ${W} ${H}`); svg.style.cssText = "width:100%;max-width:300px;display:block;background:#fff;border:1px solid #ccc;border-radius:6px";
    const add = (tag, a) => { const e = document.createElementNS(NS, tag); for (const [k2, v] of Object.entries(a)) e.setAttribute(k2, v); svg.appendChild(e); };
    add("polyline", {points: pts.map(p => X(p[1]) + "," + Y(p[0])).join(" "), fill: "none", stroke: "#1971c2", "stroke-width": 3, "stroke-linejoin": "round"});
    add("circle", {cx: X(pts[0][1]), cy: Y(pts[0][0]), r: 5, fill: "#e03131"});
    add("rect", {x: X(pts[pts.length - 1][1]) - 5, y: Y(pts[pts.length - 1][0]) - 5, width: 10, height: 10, fill: "#2b8a3e"});
    const a = el("a", "display:inline-block;margin-top:4px;font-size:13px;font-weight:600", "Open in a map app (GPX, " + pts.length + " points)");
    a.href = URL.createObjectURL(new Blob([gpx(name, pts)], {type: "application/gpx+xml"}));
    a.download = "walk-to-" + name.normalize("NFD").replace(/[\u0300-\u036f]/g, "").replace(/[^A-Za-z0-9]+/g, "-").slice(0, 40) + ".gpx";
    box.append(el("div", "font-size:12px;font-weight:600;margin-bottom:2px", `Route to ${name}: ${Math.round(m)} m`), svg, a,
      el("div", "font-size:11px;color:#777", "Organic Maps and OsmAnd open it and guide you with no connection."));
    return box;
  }

  async function verify(text, sig, extra) {
    if (typeof KIT_PUBKEY === "undefined" || !crypto.subtle) return null;
    try {
      const key = await crypto.subtle.importKey("raw", b64bytes(KIT_PUBKEY), {name: "Ed25519"}, false, ["verify"]);
      return await crypto.subtle.verify({name: "Ed25519"}, key, sig, new TextEncoder().encode([plain(text)].concat(extra || []).join(" ").split(/\s+/).filter(Boolean).join(" ")));
    } catch (e) { return null; }
  }
  // Everything in a message, rendered into `out`. -> {missing, resend}
  async function show(text, out) {
    out.innerHTML = "";
    const {complete, missing} = parse(text), note = (t, c) => out.appendChild(el("div", "margin:6px 0;font-size:13px;" + (c || ""), t));
    const texts = [];
    for (const c of complete.filter(c => c.kind === "T")) {  // words packed out of UCS-2 come first
      try { const t = await inflate(c.bytes); texts.push(t); out.appendChild(el("div", "white-space:pre-wrap;margin:4px 0;font-size:14px;color:inherit", t)); }
      catch (e) { note("A text code could not be read.", "color:#c92a2a"); }
    }
    for (const c of complete) {  // pictures first, so a key or a zoom in the same message finds them
      if (c.kind === "P") await remember(c.id, c.bytes);
      else if (c.kind === "Z") { const d = new DataView(c.bytes.buffer, c.bytes.byteOffset), parent = String.fromCharCode(c.bytes[0], c.bytes[1]);
        await remember(c.id, c.bytes.slice(10), parent, [0, 1, 2, 3].map(i => d.getUint16(2 + 2 * i) / 64)); }
    }
    for (const c of complete.filter(c => c.kind === "K")) { try { const k = JSON.parse(new TextDecoder().decode(c.bytes)); (SEEN[k.i] = SEEN[k.i] || {}).keys = k.k; } catch (e) {} }
    const pics = complete.filter(c => c.kind === "P");
    for (const c of complete) {
      if (c.kind === "M") { try { const m = JSON.parse(await inflate(c.bytes)); out.appendChild(el("div", "font-size:12px;font-weight:600;margin-top:6px", "Map")); out.appendChild(mapView(m)); } catch (e) { note("A map code could not be read.", "color:#c92a2a"); } }
      else if (c.kind === "G") { try { const [name, line] = (await inflate(c.bytes)).split("\n"); out.appendChild(routeView(name, unpolyline(line))); } catch (e) { note("A route code could not be read.", "color:#c92a2a"); } }
      else if (c.kind === "L") { const d = JSON.parse(new TextDecoder().decode(c.bytes)); note(`Check-in: ${d.st || "ok"}${d.p ? ", " + d.p + " people" : ""} at ${(+d.la).toFixed(5)}, ${(+d.lo).toFixed(5)} (±${d.ac || "?"} m)${d.b ? ", battery " + d.b + "%" : ""}${d.n ? " — " + d.n : ""}`); }
      else if (c.kind === "S") { const id = new TextDecoder().decode(c.bytes), e = (typeof KIT_PACK !== "undefined" ? KIT_PACK.entries : []).find(x => x.id === id);
        if (e) out.appendChild(await pairView(b64bytes(e.before), b64bytes(e.after), `${e.task}: ${e.step} (checked picture from your kit)`)); else note(`Phrasebook entry ${id} is not in this kit: update the kit.`, "color:#c92a2a"); }
      else if (c.kind === "A") { const ok = await verify(text, c.bytes, texts); note(ok === null ? "Signature present, but this browser can't check it." : ok ? "✔ Genuine: signed by the alert service." : "✘ NOT genuine: the signature doesn't match this text.", "font-weight:700;color:" + (ok ? "#2b8a3e" : "#c92a2a")); }
      else if (c.kind === "Z") {
        out.appendChild(zoomView(c.id, false, "Closer look (" + c.bytes.length + " bytes)"));
        if (SEEN[SEEN[c.id].parent]) out.appendChild(zoomView(c.id, true, "Put together with the picture it came from"));
        else note("The picture this zoom belongs to isn't in this kit yet.", "color:#777");
      }
    }
    const keyed = pics.filter(c => SEEN[c.id] && SEEN[c.id].keys);
    if (pics.length >= 2 && !keyed.length)  // step pictures: before and after
      for (let i = 0; i < pics.length; i += 2) {
        if (pics[i + 1]) out.appendChild(await pairView(pics[i].bytes, pics[i + 1].bytes, pics.length > 2 ? "Pictures " + (i + 1) + "–" + (i + 2) : "Before / after"));
        else out.appendChild(zoomView(pics[i].id, true));
      }
    else for (const c of pics) out.appendChild(zoomView(c.id, true, (SEEN[c.id].keys || []).length ? "Picture: " + SEEN[c.id].keys.map((k, i) => (i + 1) + " " + k[0]).join(" · ") : ""));
    const ids = Object.keys(missing), resend = ids.map(id => `RESEND ${id} ${missing[id].join(" ")}`).join("\n");
    if (ids.length) note("Some parts are missing or damaged. Reply with: " + resend, "color:#c92a2a");
    return {missing, resend, count: complete.length};
  }

  // ---------- QR codes (byte mode, error correction L, versions 1-20), written here so the kit works offline ----------
  const QR_EC = [null, [7, 1, 19, 0, 0], [10, 1, 34, 0, 0], [15, 1, 55, 0, 0], [20, 1, 80, 0, 0], [26, 1, 108, 0, 0], [18, 2, 68, 0, 0],
    [20, 2, 78, 0, 0], [24, 2, 97, 0, 0], [30, 2, 116, 0, 0], [18, 2, 68, 2, 69], [20, 4, 81, 0, 0], [24, 2, 92, 2, 93], [26, 4, 107, 0, 0],
    [30, 3, 115, 1, 116], [22, 5, 87, 1, 88], [24, 5, 98, 1, 99], [28, 1, 107, 5, 108], [30, 5, 120, 1, 121], [28, 3, 113, 4, 114], [28, 3, 107, 5, 108]];
  const QR_ALIGN = [null, [], [6, 18], [6, 22], [6, 26], [6, 30], [6, 34], [6, 22, 38], [6, 24, 42], [6, 26, 46], [6, 28, 50], [6, 30, 54], [6, 32, 58],
    [6, 34, 62], [6, 26, 46, 66], [6, 26, 48, 70], [6, 26, 50, 74], [6, 30, 54, 78], [6, 30, 56, 82], [6, 30, 58, 86], [6, 34, 62, 90]];
  const EXP = new Uint8Array(512), LOG = new Uint8Array(256);
  (() => { let x = 1; for (let i = 0; i < 255; i++) { EXP[i] = x; LOG[x] = i; x <<= 1; if (x & 256) x ^= 0x11D; } for (let i = 255; i < 512; i++) EXP[i] = EXP[i - 255]; })();
  const gmul = (a, b) => a && b ? EXP[LOG[a] + LOG[b]] : 0;
  function rsEc(data, n) {
    let gen = [1]; for (let i = 0; i < n; i++) { const g = new Array(gen.length + 1).fill(0); for (let j = 0; j < gen.length; j++) { g[j] ^= gen[j]; g[j + 1] ^= gmul(gen[j], EXP[i]); } gen = g; }
    const res = new Array(n).fill(0);
    for (const d of data) { const f = d ^ res.shift(); res.push(0); for (let j = 0; j < n; j++) res[j] ^= gmul(gen[j + 1], f); }
    return res;
  }
  function qr(text) {
    const data = Array.from(new TextEncoder().encode(text));
    let v = 1;
    for (; v <= 20; v++) { const [, b1, d1, b2, d2] = QR_EC[v]; if (4 + (v < 10 ? 8 : 16) + 8 * data.length <= 8 * (b1 * d1 + b2 * d2)) break; }
    if (v > 20) return null;
    const [ecn, b1, d1, b2, d2] = QR_EC[v], cap = b1 * d1 + b2 * d2, bits = [];
    const put = (val, len) => { for (let i = len - 1; i >= 0; i--) bits.push((val >>> i) & 1); };
    put(4, 4); put(data.length, v < 10 ? 8 : 16); for (const b of data) put(b, 8);
    put(0, Math.min(4, cap * 8 - bits.length)); while (bits.length % 8) bits.push(0);
    const cw = []; for (let i = 0; i < bits.length; i += 8) cw.push(parseInt(bits.slice(i, i + 8).join(""), 2));
    for (let p = 0; cw.length < cap; p++) cw.push(p % 2 ? 0x11 : 0xEC);
    const blocks = [], ecs = []; let o = 0;
    for (const [nb, nd] of [[b1, d1], [b2, d2]]) for (let i = 0; i < nb; i++) { const blk = cw.slice(o, o + nd); o += nd; blocks.push(blk); ecs.push(rsEc(blk, ecn)); }
    const seq = []; for (let i = 0; i < Math.max(d1, d2); i++) for (const b of blocks) if (i < b.length) seq.push(b[i]);
    for (let i = 0; i < ecn; i++) for (const e of ecs) seq.push(e[i]);
    const N = 17 + 4 * v, M = Array.from({length: N}, () => new Array(N).fill(null));
    const finder = (r, c) => { for (let y = -1; y <= 7; y++) for (let x = -1; x <= 7; x++) { const rr = r + y, cc = c + x; if (rr < 0 || cc < 0 || rr >= N || cc >= N) continue;
      M[rr][cc] = y >= 0 && y <= 6 && x >= 0 && x <= 6 && (y === 0 || y === 6 || x === 0 || x === 6 || (y >= 2 && y <= 4 && x >= 2 && x <= 4)) ? 1 : 0; } };
    finder(0, 0); finder(0, N - 7); finder(N - 7, 0);
    for (let i = 8; i < N - 8; i++) { M[6][i] = M[i][6] = i % 2 ? 0 : 1; }
    const al = QR_ALIGN[v], last = al.length - 1;  // all combinations except the three under the finder patterns
    for (const [i, r] of al.entries()) for (const [j, c] of al.entries()) { if ((i === 0 && j === 0) || (i === 0 && j === last) || (i === last && j === 0)) continue; for (let y = -2; y <= 2; y++) for (let x = -2; x <= 2; x++) M[r + y][c + x] = Math.max(Math.abs(y), Math.abs(x)) === 1 ? 0 : 1; }
    M[N - 8][8] = 1;  // the dark module
    const reserve = (r, c) => { if (M[r][c] === null) M[r][c] = 0; };
    for (let i = 0; i < 9; i++) { reserve(8, i); reserve(i, 8); } for (let i = 0; i < 8; i++) { reserve(8, N - 1 - i); reserve(N - 1 - i, 8); }
    if (v >= 7) for (let i = 0; i < 6; i++) for (let j = 0; j < 3; j++) { reserve(i, N - 11 + j); reserve(N - 11 + j, i); }
    const fn = M.map(row => row.map(x => x !== null));
    const dbits = []; for (const b of seq) for (let i = 7; i >= 0; i--) dbits.push((b >> i) & 1);
    let k = 0, up = true;
    for (let c = N - 1; c >= 1; c -= 2) { if (c === 6) c = 5;  // column pairs from the right, skipping the timing column
      for (let i = 0; i < N; i++) { const r = up ? N - 1 - i : i;
        for (const cc of [c, c - 1]) if (!fn[r][cc]) M[r][cc] = (dbits[k++] || 0) ^ ((r + cc) % 2 === 0 ? 1 : 0); }
      up = !up; }
    let fmt = (1 << 3) | 0;  // EC level L (01), mask 0
    let rem = fmt << 10; for (let i = 14; i >= 10; i--) if (rem & (1 << i)) rem ^= 0x537 << (i - 10);
    fmt = ((fmt << 10) | rem) ^ 0x5412;
    const fb = i => (fmt >> i) & 1;
    for (let i = 0; i <= 5; i++) M[i][8] = fb(i); M[7][8] = fb(6); M[8][8] = fb(7); M[8][7] = fb(8); for (let i = 9; i < 15; i++) M[8][14 - i] = fb(i);
    for (let i = 0; i < 8; i++) M[8][N - 1 - i] = fb(i); for (let i = 8; i < 15; i++) M[N - 15 + i][8] = fb(i);
    if (v >= 7) { let rv = v << 12; for (let i = 17; i >= 12; i--) if (rv & (1 << i)) rv ^= 0x1F25 << (i - 12); const vb = (v << 12) | rv;
      for (let i = 0; i < 18; i++) { const bit = (vb >> i) & 1, a = Math.floor(i / 3), b = N - 11 + (i % 3); M[a][b] = bit; M[b][a] = bit; } }
    return M;
  }
  function qrCanvas(text, scale = 4) {
    const M = qr(text); if (!M) return null;
    const N = M.length, q = 4, c = el("canvas", "image-rendering:pixelated;background:#fff"); c.width = c.height = (N + 2 * q) * scale;
    const g = c.getContext("2d"); g.fillStyle = "#fff"; g.fillRect(0, 0, c.width, c.height); g.fillStyle = "#000";
    for (let r = 0; r < N; r++) for (let x = 0; x < N; x++) if (M[r][x]) g.fillRect((x + q) * scale, (r + q) * scale, scale, scale);
    return c;
  }

  // Saving what was seen (the kit page keeps it in the browser, so a zoom can arrive days later).
  const dump = () => JSON.stringify(Object.fromEntries(Object.entries(SEEN).filter(([, p]) => p.bytes).map(([k, p]) => [k, {b: bytesb64(p.bytes), p: p.parent, x: p.box, k: p.keys}])));
  async function restore(json) { for (const [k, p] of Object.entries(JSON.parse(json || "{}"))) { await remember(k, b64bytes(p.b), p.p, p.x); if (p.k) SEEN[k].keys = p.k; } }
  return {parse, plain, show, encode, photoBits, photoWebp, png1bit, locationBytes, qr, qrCanvas, mapView, pairView, zoomView, routeView, gpx, unpolyline,
          crc16, bytesb64, b64bytes, dump, restore, set onRequest(f) { onRequest = f; }, get seen() { return SEEN; }};
})();

/* Metavoid — front-end logic (plain JS, no external libraries) */
"use strict";

/* Desktop (pywebview/WebView2) mode exposes window.pywebview.api and gives us
   native Save dialogs. Otherwise we run inside a normal browser tab and use
   plain blob downloads / ZIP. Checked dynamically so async bridge injection works. */
function isDesktop() {
  return typeof window !== "undefined" &&
         typeof window.pywebview !== "undefined" &&
         typeof window.pywebview.api !== "undefined" &&
         typeof window.pywebview.api.save_one === "function";
}

function updateDesktopUI() {
  const desktop = isDesktop();
  document.body.classList.toggle("desktop-app", desktop);
  const line = $("desktopLine");
  if (line) line.classList.toggle("hidden", !desktop);
  const z = $("zipBtn");
  if (z) z.classList.toggle("hidden", desktop);
  const s = $("saveAllBtn");
  if (s) s.classList.toggle("hidden", !desktop);
}

window.addEventListener("pywebviewready", () => {
  updateDesktopUI();
});

async function checkDesktopMode() {
  for (let i = 0; i < 20; i++) {
    if (isDesktop()) {
      updateDesktopUI();
      return;
    }
    await new Promise((resolve) => setTimeout(resolve, 100));
  }
  try {
    const resp = await fetch("/api/health");
    const data = await resp.json();
    if (data.desktop_bridge && isDesktop()) {
      updateDesktopUI();
    }
  } catch (_) {}
}

/* Ask the backend how much it will actually accept. The desktop build allows
   ~300 MB; a hosted deployment is capped far lower by the platform. */
async function loadLimits() {
  try {
    const resp = await fetch("/api/health");
    const data = await resp.json();
    if (typeof data.max_upload_bytes === "number") {
      state.maxUpload = data.max_upload_bytes;
      syncGo();
    }
  } catch (_) { /* leave maxUpload at 0 = no client-side limit */ }
}

const state = {
  files: [],        // {file, url, ext}
  mode: "ai",
  lastIds: [],
  processing: false,
  /* Serverless (Vercel) mode: /api/process returns each cleaned file inline
     as base64 instead of stashing it server-side, because a follow-up request
     may hit a different, cold instance. rid -> {bytes, mime, name, report}. */
  payloads: {},
  /* Largest total upload the backend will accept, from /api/health. Hosted
     deployments cap this well below the desktop build's limit. 0 = not known
     yet, in which case no client-side limit is enforced. */
  maxUpload: 0,
};

const $ = (id) => document.getElementById(id);

const ACCEPT = new Set([
  "jpg","jpeg","jpe","jfif","png","apng","webp","gif","bmp","dib",
  "tif","tiff","dng","cr2","cr3","nef","arw","orf","rw2","pef","srw",
  "erf","3fr","iiq","mef","nrw","raf","mrw","heic","heif","heics",
  "heifs","avif","hif","svg","svgz","ico",
]);

/* ------------------------------------------------------------- toast */
let _toastTimer = null;
function toast(msg, kind) {
  const el = $("toast");
  el.textContent = msg;
  // Kept in the layer and faded via .show — display:none would cancel the
  // transition. 'ok' / 'err' only tint the border.
  el.className = (kind || "ok") + " show";
  clearTimeout(_toastTimer);
  _toastTimer = setTimeout(() => el.classList.remove("show"), 5200);
}

document.addEventListener("DOMContentLoaded", () => {
  checkDesktopMode();
  loadLimits();

  const dz = $("dropzone"), input = $("fileInput");
  dz.addEventListener("click", () => input.click());
  dz.addEventListener("keydown", (e) => {
    if (e.key === "Enter" || e.key === " ") { e.preventDefault(); input.click(); }
  });
  ["dragenter", "dragover"].forEach((ev) =>
    dz.addEventListener(ev, (e) => { e.preventDefault(); dz.classList.add("drag"); }));
  ["dragleave", "drop"].forEach((ev) =>
    dz.addEventListener(ev, (e) => { e.preventDefault(); dz.classList.remove("drag"); }));
  dz.addEventListener("drop", (e) => addFiles(e.dataTransfer.files));
  input.addEventListener("change", () => addFiles(input.files));

  document.querySelectorAll(".mode").forEach((btn) => {
    btn.addEventListener("click", () => {
      state.mode = btn.dataset.mode;
      document.querySelectorAll(".mode").forEach((b) => {
        const on = b === btn;
        b.classList.toggle("active", on);
        b.setAttribute("aria-pressed", on ? "true" : "false");
      });
    });
  });

  $("goBtn").addEventListener("click", process);
  $("zipBtn").addEventListener("click", downloadZip);
  $("saveAllBtn").addEventListener("click", nativeSaveAll);
  // Absent in serverless mode — the template omits the Stop / exit control.
  const stopBtn = $("stopBtn");
  if (stopBtn) stopBtn.addEventListener("click", stopApp);
  syncGo();
});

/* ---------------------------------------------------------------- uploads */
function addFiles(fileList) {
  const errors = [];
  const accepted = [];
  for (const f of fileList) {
    const ext = (f.name.split(".").pop() || "").toLowerCase();
    if (!ACCEPT.has(ext)) {
      errors.push(`"${f.name}" (.${ext || "?"}) is not a supported image — skipped.`);
      continue;
    }
    if (state.files.some((x) => x.file.name === f.name && x.file.size === f.size)) continue;
    const url = (f.type && f.type.startsWith("image/")) ? URL.createObjectURL(f) : "";
    accepted.push({ file: f, url, ext });
  }
  // The element carries .hidden in the markup, so toggling it is what makes
  // these warnings visible at all.
  const box = $("uploadError");
  box.textContent = errors.length ? errors.join(" ") : "";
  box.classList.toggle("hidden", errors.length === 0);
  state.files.push(...accepted);
  renderThumbs();
  syncGo();
}

function removeFile(i) {
  if (state.files[i] && state.files[i].url) URL.revokeObjectURL(state.files[i].url);
  state.files.splice(i, 1);
  renderThumbs();
  syncGo();
}

function renderThumbs() {
  const box = $("thumbs");
  box.innerHTML = "";
  state.files.forEach((it, i) => {
    const d = document.createElement("div");
    d.className = "thumb";
    const inner = document.createElement("div");
    if (it.url) {
      const img = document.createElement("img");
      img.src = it.url; img.alt = "";
      inner.appendChild(img);
    } else {
      const ph = document.createElement("div");
      ph.className = "tbadge";
      ph.style.position = "static";
      ph.style.marginTop = "8px";
      ph.textContent = "." + it.ext.toUpperCase();
      inner.appendChild(ph);
    }
    const x = document.createElement("button");
    x.className = "x"; x.textContent = "×";
    x.addEventListener("click", () => removeFile(i));
    const nm = document.createElement("div");
    nm.className = "tname"; nm.textContent = it.file.name;
    const meta = document.createElement("div");
    meta.className = "tmeta";
    meta.textContent = (it.file.size / 1024).toFixed(1) + " KB";
    d.appendChild(inner); d.appendChild(x); d.appendChild(nm); d.appendChild(meta);
    box.appendChild(d);
  });
}

/* ---------------------------------------------------------------- process */
function totalBytes() {
  return state.files.reduce((n, it) => n + it.file.size, 0);
}

function overLimit() {
  return state.maxUpload > 0 && totalBytes() > state.maxUpload;
}

function syncGo() {
  $("goBtn").disabled = state.processing || state.files.length === 0 || overLimit();

  // Say so up front rather than letting the user wait through an upload the
  // server is going to reject.
  const box = $("uploadError");
  if (overLimit()) {
    const mb = (n) => (n / 1048576).toFixed(1) + " MB";
    box.textContent =
      `Selected files total ${mb(totalBytes())}, over this deployment's ` +
      `${mb(state.maxUpload)} limit. Remove some files, or use the desktop ` +
      `app — it has no practical size limit and never uploads anything.`;
    box.classList.remove("hidden");
  } else if (box.textContent.startsWith("Selected files total")) {
    box.textContent = "";
    box.classList.add("hidden");
  }
}

async function process() {
  if (state.files.length === 0 || state.processing) return;
  state.processing = true;
  syncGo();
  $("goLabel").textContent = "Removing metadata…";
  $("goBtn").querySelector(".spinner").classList.remove("hidden");

  const fd = new FormData();
  state.files.forEach((it) => fd.append("files", it.file, it.file.name));
  fd.append("mode", state.mode);
  fd.append("options.location", $("optLocation").checked ? "true" : "false");
  fd.append("options.copyright", $("optCopyright").checked ? "true" : "false");
  fd.append("options.icc", $("optIcc").checked ? "true" : "false");

  try {
    const resp = await fetch("/api/process", { method: "POST", body: fd });
    const json = await resp.json();
    if (!resp.ok) throw new Error(json.error || ("HTTP " + resp.status));
    state.payloads = {};
    for (const r of json.results) {
      if (r && typeof r.data === "string") {
        state.payloads[r.id] = {
          bytes: b64ToBytes(r.data),
          mime: r.mime || "application/octet-stream",
          name: r.save_name || r.original_name || "cleaned",
          report: r.report_text || "",
        };
      }
    }
    renderResults(json.results);
    state.lastIds = json.results.filter((r) => r.status !== "error").map((r) => r.id);
    if (state.lastIds.length > 1 && !isDesktop()) $("zipBtn").classList.remove("hidden");
    if (state.lastIds.length && isDesktop()) $("saveAllBtn").classList.remove("hidden");
  } catch (err) {
    toast("Processing failed: " + err.message, "err");
  } finally {
    state.processing = false;
    syncGo();
    $("goLabel").textContent = "Remove metadata";
    $("goBtn").querySelector(".spinner").classList.add("hidden");
  }
}

/* ---------------------------------------------------------------- results */
function renderResults(results) {
  const wrap = $("resultsWrap");
  const box = $("results");
  box.innerHTML = "";
  wrap.classList.remove("hidden");

  for (const r of results) {
    const el = document.createElement("article");
    el.className = "rfile";

    const head = document.createElement("div");
    head.className = "rhead";
    const name = document.createElement("span");
    name.className = "rname";
    name.textContent = r.original_name;
    head.appendChild(name);

    const status = r.status === "ok" ? "Cleaned"
      : r.status === "unchanged" ? "Already clean"
      : r.status === "error" ? "Failed" : r.status;
    head.appendChild(chip(status, r.status === "ok" ? "ok"
      : r.status === "unchanged" ? "gray" : "err"));
    if (r.extension) head.appendChild(chip("." + r.extension, "gray"));
    if (r.scan && r.scan.ai_metadata_found) head.appendChild(chip("AI/C2PA found", "ai"));
    if (r.scan && r.scan.has_gps) head.appendChild(chip("GPS", "warn"));
    el.appendChild(head);

    if (r.message) {
      const m = document.createElement("p");
      m.className = "msg"; m.textContent = r.message;
      el.appendChild(m);
    }

    if (r.removed && r.removed.length) {
      const label = document.createElement("div");
      label.style.cssText = "font-size:11px;color:var(--muted);margin:10px 0 2px;";
      label.textContent = "Removed:";
      const ul = document.createElement("ul");
      ul.className = "removed";
      r.removed.slice(0, 40).forEach((t) => {
        const li = document.createElement("li");
        li.textContent = t;
        ul.appendChild(li);
      });
      el.appendChild(label);
      el.appendChild(ul);
    }

    if (r.warnings && r.warnings.length) {
      const m = document.createElement("p");
      m.className = "msg";
      m.textContent = "⚠ " + r.warnings.join(" ");
      el.appendChild(m);
    }

    const ver = document.createElement("div");
    ver.className = "verified";
    const v = r.verified_after || {};
    [["EXIF", v.has_exif], ["GPS", v.has_gps], ["XMP", v.has_xmp],
     ["IPTC", v.has_iptc], ["AI/C2PA", v.ai_metadata_found]].forEach(([label, present]) => {
      ver.appendChild(chip(label + (present === undefined ? " ?"
        : present ? ": present" : ": gone"), present ? "warn" : "ok"));
    });
    el.appendChild(ver);

    const btns = document.createElement("div");
    btns.className = "rbtns";

    const bSave = document.createElement("button");
    bSave.className = "small-btn dl";
    bSave.textContent = "💾 Save cleaned file";
    bSave.title = "Save cleaned file to your computer";
    const outName = r.output_name || r.original_name;
    bSave.addEventListener("click", () => handleSaveOne(r.id, outName));
    btns.appendChild(bSave);

    const bRep = document.createElement("button");
    bRep.className = "small-btn";
    bRep.textContent = "View / save report";
    bRep.title = "Save or view the cleaning report (.txt)";
    bRep.addEventListener("click", () => handleSaveReport(r.id, r.original_name));
    btns.appendChild(bRep);

    el.appendChild(btns);
    box.appendChild(el);
  }
  box.scrollIntoView({ behavior: "smooth", block: "start" });
}

function chip(text, kind) {
  const s = document.createElement("span");
  s.className = "chip " + (kind || "gray");
  s.textContent = text;
  return s;
}

/* ------------------------------------------------- save handling */
async function bridge(method, ...args) {
  try {
    if (!window.pywebview || !window.pywebview.api) {
      return { ok: false, error: "Native desktop bridge unavailable." };
    }
    return await window.pywebview.api[method](...args);
  } catch (err) {
    return { ok: false, error: String(err && err.message || err) };
  }
}

/* --- inline-payload helpers (serverless mode) ------------------------------
   No external libraries and no network: the cleaned bytes are already in the
   page, so saving is a pure client-side Blob download. */

function b64ToBytes(b64) {
  const bin = atob(b64);
  const out = new Uint8Array(bin.length);
  for (let i = 0; i < bin.length; i++) out[i] = bin.charCodeAt(i);
  return out;
}

function saveBlob(blob, filename) {
  const blobUrl = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.style.display = "none";
  a.href = blobUrl;
  a.download = filename || "download";
  document.body.appendChild(a);
  a.click();
  setTimeout(() => {
    URL.revokeObjectURL(blobUrl);
    a.remove();
  }, 1500);
}

/* Minimal store-only (uncompressed) ZIP writer. Keeps the "no CDN" promise
   that the desktop build relies on, and the payloads are already-compressed
   image data anyway, so deflating them would buy almost nothing. */
const CRC_TABLE = (() => {
  const t = new Uint32Array(256);
  for (let n = 0; n < 256; n++) {
    let c = n;
    for (let k = 0; k < 8; k++) c = (c & 1) ? (0xEDB88320 ^ (c >>> 1)) : (c >>> 1);
    t[n] = c >>> 0;
  }
  return t;
})();

function crc32(bytes) {
  let c = 0xFFFFFFFF;
  for (let i = 0; i < bytes.length; i++) {
    c = CRC_TABLE[(c ^ bytes[i]) & 0xFF] ^ (c >>> 8);
  }
  return (c ^ 0xFFFFFFFF) >>> 0;
}

function buildZip(entries) {
  const enc = new TextEncoder();
  const chunks = [];
  const central = [];
  let offset = 0;

  const u16 = (v) => [v & 0xFF, (v >>> 8) & 0xFF];
  const u32 = (v) => [v & 0xFF, (v >>> 8) & 0xFF, (v >>> 16) & 0xFF, (v >>> 24) & 0xFF];

  for (const ent of entries) {
    const nameBytes = enc.encode(ent.name);
    const data = ent.bytes;
    const crc = crc32(data);
    // 0x0800 = names are UTF-8. Time/date left at 0 (valid, shows as 1980).
    const common = [...u16(20), ...u16(0x0800), ...u16(0), ...u16(0), ...u16(0),
                    ...u32(crc), ...u32(data.length), ...u32(data.length),
                    ...u16(nameBytes.length)];

    chunks.push(new Uint8Array([...u32(0x04034B50), ...common, ...u16(0)]));
    chunks.push(nameBytes);
    chunks.push(data);

    central.push(new Uint8Array([
      ...u32(0x02014B50), ...u16(20), ...common,
      // extra len, comment len, disk start, internal attrs, external attrs,
      // then the local header's offset.
      ...u16(0), ...u16(0), ...u16(0), ...u16(0), ...u32(0), ...u32(offset),
    ]));
    central.push(nameBytes);

    offset += 30 + nameBytes.length + data.length;
  }

  const centralBytes = central.reduce((n, c) => n + c.length, 0);
  const end = new Uint8Array([
    ...u32(0x06054B50), ...u16(0), ...u16(0),
    ...u16(entries.length), ...u16(entries.length),
    ...u32(centralBytes), ...u32(offset), ...u16(0),
  ]);

  return new Blob([...chunks, ...central, end], { type: "application/zip" });
}

async function browserDownloadFile(url, filename) {
  try {
    toast("Preparing " + (filename || "file") + "…");
    const resp = await fetch(url);
    if (!resp.ok) throw new Error("HTTP " + resp.status + ": File not found in memory (session expired).");
    const blob = await resp.blob();
    const blobUrl = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.style.display = "none";
    a.href = blobUrl;
    a.download = filename || "download";
    document.body.appendChild(a);
    a.click();
    setTimeout(() => {
      URL.revokeObjectURL(blobUrl);
      a.remove();
    }, 1500);
    toast("Saved: " + (filename || "file"));
  } catch (err) {
    toast("Save failed: " + err.message, "err");
  }
}

async function handleSaveOne(rid, defaultName) {
  if (isDesktop()) {
    await nativeSaveOne(rid, defaultName);
    return;
  }
  const p = state.payloads[rid];
  if (p) {
    saveBlob(new Blob([p.bytes], { type: p.mime }), defaultName || p.name);
    toast("Saved: " + (defaultName || p.name));
    return;
  }
  await browserDownloadFile(`/api/file/${rid}`, defaultName);
}

async function handleSaveReport(rid, originalName) {
  const repName = (originalName || "image").replace(/\.[^.]+$/, "") + "-report.txt";
  if (isDesktop()) {
    await nativeSaveReport(rid, repName);
    return;
  }
  const p = state.payloads[rid];
  if (p) {
    saveBlob(new Blob([p.report], { type: "text/plain;charset=utf-8" }), repName);
    toast("Saved report: " + repName);
    return;
  }
  await browserDownloadFile(`/api/report/${rid}`, repName);
}

async function handleSaveAll() {
  if (isDesktop()) {
    await nativeSaveAll();
  } else {
    downloadZip();
  }
}

async function nativeSaveOne(rid, defaultName) {
  const r = await bridge("save_one", rid);
  if (r && r.ok) {
    toast("Saved: " + r.name);
  } else if (r && r.cancelled) {
    /* user cancelled file dialog */
  } else {
    /* Fallback to direct download */
    await browserDownloadFile(`/api/file/${rid}`, defaultName);
  }
}

async function nativeSaveReport(rid, repName) {
  const r = await bridge("save_report", rid);
  if (r && r.ok) {
    toast("Saved report: " + r.name);
  } else if (r && r.cancelled) {
    /* cancelled */
  } else {
    await browserDownloadFile(`/api/report/${rid}`, repName);
  }
}

async function nativeSaveAll() {
  const r = await bridge("save_all");
  if (r && r.ok) {
    const n = (r.saved || []).length;
    toast(n ? `Saved ${n} file${n === 1 ? "" : "s"} to:\n${r.folder}`
            : "No files were in memory to save.", n ? "ok" : "err");
  } else if (r && r.cancelled) {
    /* cancelled */
  } else {
    downloadZip();
  }
}

/* -------------------------------------------------------------- browser */
function downloadZip() {
  if (!state.lastIds.length) return;

  // Serverless: the bytes are already here, so build the ZIP in the browser.
  const inline = state.lastIds.filter((id) => state.payloads[id]);
  if (inline.length === state.lastIds.length) {
    const entries = [];
    for (const id of inline) {
      const p = state.payloads[id];
      entries.push({ name: p.name, bytes: p.bytes });
      if (p.report) {
        entries.push({
          name: p.name.replace(/\.[^.]+$/, "") + "-report.txt",
          bytes: new TextEncoder().encode(p.report),
        });
      }
    }
    saveBlob(buildZip(entries), "cleaned-images.zip");
    toast("Saved: cleaned-images.zip");
    return;
  }

  window.location.href = "/api/zip?ids=" + state.lastIds.join(",");
}

/* --------------------------------------------------------------- exit */
async function stopApp() {
  if (isDesktop()) {
    const r = await bridge("stop");
    if (!r || !r.ok) window.close();
  } else {
    try { await fetch("/api/quit"); } catch (_) { /* gone already */ }
    document.body.insertAdjacentHTML(
      "beforeend",
      '<div style="position:fixed;inset:0;background:#0d1117f2;display:flex;align-items:center;justify-content:center;z-index:99">' +
      '<div style="text-align:center"><h2>Server stopped</h2><p style="color:#9aa7b4">You can close this tab now.</p></div></div>');
  }
}

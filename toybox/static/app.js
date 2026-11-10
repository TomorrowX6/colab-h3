"use strict";

const $ = (s, el = document) => el.querySelector(s);
const KIND_LABEL = { image: "圖片", video: "影片", audio: "音訊" };
const KIND_ICON = { image: "🖼", video: "🎬", audio: "🎵" };
const MENTION_RE = /@(圖片|图片|Image|影片|视频|Video|音訊|音频|Audio)\s?(\d+)/gi;
const MENTION_KIND = { "圖片": "image", "图片": "image", image: "image", "影片": "video", "视频": "video", video: "video", "音訊": "audio", "音频": "audio", audio: "audio" };

const PLACEHOLDER = {
  ref: "上傳最多 9 個參考內容 (包含影像、影片和音訊)，或使用 @ 提及元素",
  frames: "描述從首幀走到尾幀之間發生的動態變化（只放首幀或尾幀也可以）",
  text: "輸入文字描述整支影片：畫面構圖、人物動作、鏡頭運動、對白與環境聲音",
  agent: "上傳角色與場景參考圖，用一句話描述你想製作的多段短片故事，或使用 @ 提及元素",
  projects: "寫一句想法或貼上大綱，按右側按鈕開一部新作品；或點選下方方塊繼續編輯",
  image: "上傳最多 9 張參考影像進行編輯，或直接描述畫面，使用 @ 提及元素",
};

const RICH_PLACEHOLDER = {
  ref: `上傳最多 9 個參考內容 (包含影像、影片和音訊)，或使用 <span class="at-badge">@</span> 提及元素`,
  frames: `選取左側首幀或尾幀影像，並描述畫面之間的動作、運鏡與聲音發展…`,
  text: `輸入文字描述你想生成的影片畫面、人物動作、對白與環境音訊…`,
  agent: `上傳角色或場景參考圖，用一句話描述短片劇情，或使用 <span class="at-badge">@</span> 提及元素`,
  projects: `寫一句故事構想或貼上劇本大綱，按右下角按鈕建立新作品…`,
  image: `上傳最多 9 張參考圖進行編輯或直接文生圖，可使用 <span class="at-badge">@</span> 提及元素`,
};

const MODE_LABEL = { ref: "全方位參考", frames: "首尾幀", text: "純文字" };
const TASK_NAME = { Ref2VA: "全方位參考", T2VA: "純文字", I2VA: "首幀", FL2VA: "首尾幀", L2VA: "尾幀", T2I: "文生圖", Edit: "參考生圖" };
const REF_HINT = {
  ref: "在素材庫按「＋參考」加入，再用 <b>@</b> 標記；影片可以切成「延伸」",
  image: "最多 9 張參考圖，用 <b>@</b> 標記；不標記就照順序全部使用",
  agent: "角色、場景的參考圖（照順序叫 圖片1、圖片2…），規劃模型會決定每一段用哪幾張",
};
// tray-using modes that only take pictures
const PICTURE_TRAY = new Set(["image", "agent"]);

const UP_ARROW_SVG = `<svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><line x1="12" y1="19" x2="12" y2="5"/><polyline points="5 12 12 5 19 12"/></svg>`;
const SPINNER_SVG = `<svg viewBox="0 0 24 24" width="17" height="17" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round"><path d="M12 2a10 10 0 1 0 10 10"><animateTransform attributeName="transform" type="rotate" from="0 12 12" to="360 12 12" dur="0.8s" repeatCount="indefinite"/></path></svg>`;

const state = {
  library: [], folders: [], folder: null, tray: [], libKind: "all", mode: "ref", first: null, last: null,
  aspect: "auto", resolution: "720P", intensity: "off", imageSize: "1.3K", top: "video", videoMode: "ref",
  config: null, rewriteSig: null, busy: false, jobs: [], seenStatus: {}, videoTab: {}, jobFilter: "all",
  pendingSlot: null,
};

// ------------------------------------------------------------ api
async function api(path, body, raw) {
  const opt = body === undefined ? {} : raw ? { method: "POST", body, headers: raw } :
    { method: "POST", body: JSON.stringify(body), headers: { "Content-Type": "application/json" } };
  const r = await fetch(path, opt);
  const data = await r.json();
  if (!r.ok || data.error) throw new Error(data.error || r.statusText);
  return data;
}

function toast(msg, isError) {
  const t = $("#toast");
  t.textContent = msg;
  t.className = "toast" + (isError ? " error" : "");
  t.hidden = false;
  clearTimeout(toast.timer);
  toast.timer = setTimeout(() => (t.hidden = true), isError ? 7000 : 2800);
}

const esc = s => String(s ?? "").replace(/[&<>"]/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
const assetById = id => state.library.find(a => a.id === id);
const thumbStyle = a => a.thumb ? `style="background-image:url('library/${a.thumb}')"` : "";

function fmtEta(sec) {
  if (sec == null) return "";
  if (sec < 6) return "快好了！";
  const m = Math.floor(sec / 60), s = Math.round(sec % 60);
  return "還要約 " + (m ? `${m} 分 ` : "") + `${s} 秒`;
}

function toggleLibraryDrawer(forceOpen) {
  const closed = document.body.classList.contains("lib-closed");
  const shouldOpen = forceOpen !== undefined ? forceOpen : closed;
  document.body.classList.toggle("lib-closed", !shouldOpen);
  $("#btnToggleLib")?.classList.toggle("active", shouldOpen);
}

// ------------------------------------------------------------ library
async function loadLibrary() {
  [state.library, state.folders] = await Promise.all([api("api/library"), api("api/folders")]);
  if (state.folder && !state.folders.some(f => f.id === state.folder)) state.folder = null;
  state.tray = state.tray.filter(t => assetById(t.id));
  if (state.first && !assetById(state.first)) state.first = null;
  if (state.last && !assetById(state.last)) state.last = null;
  renderLibrary();
  renderTray();
  renderSlots();
}

function cardActions(a) {
  const inTray = state.tray.some(t => t.id === a.id);
  const btns = [];
  if (state.mode === "ref" || (PICTURE_TRAY.has(state.mode) && a.kind === "image"))
    btns.push(`<button class="pill small yellow" data-act="add">${inTray ? "已加入 ✓" : "＋參考"}</button>`);
  if (state.mode === "frames" && a.kind === "image") {
    btns.push(`<button class="pill small mint" data-act="first">設為首幀</button>`);
    btns.push(`<button class="pill small mint" data-act="last">設為尾幀</button>`);
  }
  btns.push(`<button class="pill small" data-act="del">刪除</button>`);
  return btns.join("");
}

// Folders only exist at the top level. Assets without a folder (or whose folder was deleted) live at the top.
const folderOf = a => (a.folder && state.folders.some(f => f.id === a.folder) ? a.folder : null);

function renderCrumbs() {
  const cur = state.folders.find(f => f.id === state.folder);
  $("#crumbs").innerHTML = `<span class="crumb ${cur ? "" : "cur"}" data-crumb="root" title="把素材拖到這裡可以移出資料夾">全部素材</span>` +
    (cur ? ` › <span class="crumb cur">📁 ${esc(cur.name)}</span>` : "");
}

function renderLibrary() {
  renderCrumbs();
  const badge = $("#libCountBadge");
  if (badge) {
    badge.textContent = state.library.length;
    badge.hidden = state.library.length === 0;
  }
  const items = state.library.filter(a => folderOf(a) === state.folder && (state.libKind === "all" || a.kind === state.libKind));
  const folders = state.folder ? [] : state.folders.map(f => {
    const inside = state.library.filter(a => folderOf(a) === f.id);
    const thumbs = inside.filter(a => a.thumb).slice(0, 4);
    return `<div class="folder" data-folder="${f.id}" title="點開資料夾；把素材拖到這裡就會放進去">
      <span class="fcount">${inside.length}</span>
      <div class="ficon ${thumbs.length ? "" : "empty"}">${thumbs.length ? thumbs.map(a => `<i style="background-image:url('library/${a.thumb}')"></i>`).join("") : "📁"}</div>
      <div class="fname">📁 ${esc(f.name)}</div>
      <div class="card-actions"><button class="pill small yellow" data-fact="open">打開</button>
        <button class="pill small" data-fact="rename">改名</button><button class="pill small" data-fact="delete">刪除</button></div></div>`;
  });
  const cards = items.map(a => {
    const inTray = (state.mode === "ref" || PICTURE_TRAY.has(state.mode)) && state.tray.some(t => t.id === a.id);
    const slot = state.mode === "frames" ? (a.id === state.first ? "首幀" : a.id === state.last ? "尾幀" : "") : "";
    const extra = a.kind === "video" ? ` · ${a.duration}s${a.has_audio ? " · 有聲" : ""}` : "";
    return `<div class="card ${inTray || slot ? "in-tray" : ""}" data-id="${a.id}" draggable="true">
      <span class="kind ${a.kind}">${KIND_LABEL[a.kind]}</span>${slot ? `<span class="badge-on">${slot}</span>` : ""}
      <div class="thumb" ${thumbStyle(a)}>${a.thumb ? "" : KIND_ICON[a.kind]}</div>
      <div class="meta" title="${esc(a.name)}">${esc(a.name)}${extra}</div>
      <div class="card-actions">${cardActions(a)}</div></div>`;
  });
  $("#libGrid").innerHTML = folders.concat(cards).join("") ||
    `<div class="hint">${state.folder ? "資料夾是空的：可將素材拖進來，或直接點擊上方「＋上傳」" : "素材庫是空的，先上傳一些影像、影片或音訊吧"}</div>`;
}

async function moveAssets(ids, folder) {
  state.library = await api("api/library/move", { ids, folder });
  renderLibrary();
  const f = state.folders.find(x => x.id === folder);
  toast(f ? `已移入「${f.name}」 📁` : "已移回全部素材");
}

async function folderAction(body) {
  state.folders = await api("api/folders", body);
  if (body.action === "delete") state.library = await api("api/library");
  renderLibrary();
}

async function uploadFiles(files, autoAction) {
  const uploaded = [];
  for (const f of files) {
    try {
      toast(`上傳中：${f.name}`);
      const item = await api("api/upload", f, {
        "X-Filename": encodeURIComponent(f.name),
        "X-Folder": state.folder || "",
        "Content-Type": "application/octet-stream",
      });
      state.library.unshift(item);
      uploaded.push(item);
    } catch (e) {
      toast(`${f.name} 上傳失敗：${e.message}`, true);
    }
  }
  if (autoAction === "tray") {
    for (const item of uploaded) addToTray(item.id);
  } else if (autoAction === "first" || autoAction === "last") {
    const img = uploaded.find(x => x.kind === "image");
    if (img) {
      const other = autoAction === "first" ? "last" : "first";
      if (state[other] === img.id) state[other] = null;
      state[autoAction] = img.id;
      renderSlots();
    }
  }
  renderLibrary();
  if (uploaded.length) toast("上傳完成 ✨");
}

// ------------------------------------------------------------ tray + labels (ref mode)
function trayLabels() {
  const counters = { image: 0, video: 0, audio: 0 };
  return state.tray.map(t => {
    const a = assetById(t.id);
    if (t.extend) return { ...t, asset: a, label: null };
    counters[a.kind] += 1;
    return { ...t, asset: a, label: KIND_LABEL[a.kind] + counters[a.kind] };
  });
}

function renderTray() {
  const items = trayLabels();
  $("#tray").innerHTML = items.length ? items.map((t, i) => {
    const a = t.asset;
    const opts = [];
    if (a.kind === "video") {
      if (!t.extend && a.has_audio) opts.push(`<label><input type="checkbox" data-act="vaudio" ${t.use_audio !== false ? "checked" : ""}>原聲</label>`);
      opts.push(`<label><input type="checkbox" data-act="extend" ${t.extend ? "checked" : ""}>🧵 延伸</label>`);
    }
    return `<div class="chip ${a.kind} ${t.extend ? "extend" : ""}" data-i="${i}">
      <div class="cthumb" ${thumbStyle(a)}>${a.thumb ? "" : KIND_ICON[a.kind]}</div>
      <div><div class="clabel">${t.extend ? "🧵 延伸來源" : "@" + t.label}</div>
        <div class="cname" title="${esc(a.name)}">${esc(a.name)}</div>
        ${opts.length ? `<div class="opts">${opts.join("")}</div>` : ""}</div>
      ${t.extend ? "" : `<button type="button" class="x" data-act="insert" title="插入 @ 標記">@</button>`}
      <button type="button" class="x" data-act="remove" title="移除">✕</button>
    </div>`;
  }).join("") : `<div class="tray-empty"></div>`;
  $("#extendNote").hidden = !state.tray.some(t => t.extend);
  renderHighlight();
  markStale();
}

function addToTray(id) {
  if (state.tray.some(t => t.id === id)) return;
  const a = assetById(id);
  if (!a) return;
  const kind = a.kind;
  if (PICTURE_TRAY.has(state.mode) && kind !== "image") return toast("此模式只能使用影像作為參考", true);
  const limits = { image: 9, video: 3, audio: 3 };
  if (state.tray.filter(t => assetById(t.id).kind === kind && !t.extend).length >= limits[kind])
    return toast(`${KIND_LABEL[kind]}最多 ${limits[kind]} 個`, true);
  state.tray.push({ id, use_audio: true, extend: false });
  renderTray();
  renderLibrary();
}

function relabel(mutate, removedName) {
  const before = trayLabels();
  mutate();
  const after = Object.fromEntries(trayLabels().filter(t => t.label).map(t => [t.id, t.label]));
  const map = Object.fromEntries(before.filter(t => t.label).map(t => [t.label, after[t.id] || null]));
  const ta = $("#prompt");
  ta.value = ta.value.replace(MENTION_RE, (m, k, n) => {
    const kind = MENTION_KIND[k.toLowerCase()] || MENTION_KIND[k];
    const key = KIND_LABEL[kind] + n;
    if (!(key in map)) return m;
    return map[key] ? "@" + map[key] : `「${removedName(key)}」`;
  });
  renderTray();
  renderLibrary();
}

function removeFromTray(index) {
  const removed = trayLabels()[index];
  relabel(() => state.tray.splice(index, 1), () => removed.asset.name);
}

function setExtend(index, on) {
  const target = state.tray[index];
  const name = assetById(target.id).name;
  relabel(() => {
    for (const t of state.tray) t.extend = false;
    target.extend = on;
  }, () => name + "（延伸來源）");
}

// ------------------------------------------------------------ frame slots
function renderSlots() {
  for (const slot of ["first", "last"]) {
    const el = $(`.slot[data-slot=${slot}]`);
    if (!el) continue;
    const a = state[slot] && assetById(state[slot]);
    el.classList.toggle("filled", !!a);
    $(".slot-body", el).innerHTML = a
      ? `<img src="library/${a.thumb}" alt=""><button type="button" class="x" data-clear="${slot}" title="清除">✕</button>`
      : `<span>＋上傳或<br>從素材庫選</span>`;
  }
  renderLibrary();
  markStale();
}

// ------------------------------------------------------------ modes & toolbar pills
function closePopovers() {
  for (const p of document.querySelectorAll(".popover")) p.hidden = true;
  for (const b of document.querySelectorAll(".tool-pill")) b.classList.remove("open");
  if ($("#refQuickMenu")) $("#refQuickMenu").hidden = true;
}

function syncToolbarPills() {
  const isImage = state.mode === "image";
  const isAgent = state.mode === "agent";
  const isProjects = state.mode === "projects";
  const group = isImage ? "image" : isAgent ? "agent" : isProjects ? "projects" : "video";

  // 1. Model pill label
  const modelLabel = $("#modelPillLabel");
  if (modelLabel) {
    modelLabel.textContent = "Minimax H3";
  }

  // 2. Mode pill (only in video tab)
  if ($("#wrapModePill")) $("#wrapModePill").hidden = group !== "video";
  if ($("#modePillLabel")) $("#modePillLabel").textContent = MODE_LABEL[state.mode] || "全方位參考";

  // 3. Ratio & Resolution pill
  if ($("#wrapRatioPill")) $("#wrapRatioPill").hidden = isProjects;
  if ($("#ratioPillLabel")) {
    const asp = state.aspect === "auto" ? "自動" : state.aspect;
    const res = isImage ? state.imageSize : state.resolution;
    $("#ratioPillLabel").textContent = `${asp} · ${res}`;
  }

  // 4. Duration pill
  if ($("#wrapDurationPill")) $("#wrapDurationPill").hidden = !(group === "video" || group === "agent");
  if ($("#durationPillLabel")) {
    const sec = isAgent ? $("#agentSeconds").value : $("#seconds").value;
    $("#durationPillLabel").textContent = `${sec}s`;
  }

  // 5. @ Mention button
  if ($("#btnToolbarAt")) $("#btnToolbarAt").hidden = !(state.mode === "ref" || PICTURE_TRAY.has(state.mode));

  // 6. Director / Motion / Seed pill
  if ($("#wrapDirectorPill")) $("#wrapDirectorPill").hidden = isProjects;
  if ($("#directorPillLabel")) {
    const parts = [];
    if (group === "video" || group === "agent") {
      parts.push(INTENSITY[state.intensity] || "文戲");
    }
    if (group === "video" && $("#skill")?.value) {
      const sk = state.config?.skills?.find(s => s.id === $("#skill").value)?.title || $("#skill").value;
      parts.push(sk.split(/[-｜]/)[0]);
    }
    if ((group === "video" || group === "image") && $("#seed")?.value !== "") {
      parts.push(`#${$("#seed").value}`);
    }
    $("#directorPillLabel").textContent = parts.join(" · ") || "參數";
  }

  // Submit button ready highlight
  const hasText = Boolean($("#prompt")?.value.trim());
  for (const btn of document.querySelectorAll(".submit-circle")) {
    btn.classList.toggle("ready", hasText);
  }
}

function setTop(top) {
  state.top = top;
  for (const b of $("#topTabs").children) b.classList.toggle("active", b.dataset.top === top);
  setMode(top === "video" ? state.videoMode : top);
  if (top === "agent") loadRuns();
  if (top === "projects") loadProjectGrid();
}

// ---------------------------------------------------------------- 📁 專案 (CreativeBox: DramaBox lives at ../project/)
const IN_CREATIVEBOX = location.pathname.startsWith("/create/");
const projectUrl = hash => "../project/" + (hash ? "#" + hash : "");

async function loadProjectGrid() {
  let works = [];
  try { works = await (await fetch("../project/api/home")).json(); } catch (e) { return toast("讀不到專案清單：" + e.message, true); }
  const R = { script: "劇本", video: "影片劇", radio: "廣播劇", avg: "AVG" };
  const media = (id, rel) => `../project/media/${encodeURIComponent(id)}/${rel.split("/").map(encodeURIComponent).join("/")}`;
  $("#projGrid").innerHTML = works.map(w => `<div class="proj-card" data-proj="${esc(w.id)}">
      <div class="pc-cover">${w.cover ? `<img src="${media(w.id, w.cover)}" alt="">` : "🎬"}</div>
      <div class="pc-body"><b>${esc(w.title)}</b><span class="hint">${w.episodes || "?"} 集 · ${new Date(w.updated * 1000).toLocaleDateString()}</span>
        <div class="pc-tags">${w.routes.map(r => `<span>${R[r] || r}</span>`).join("")}</div></div></div>`).join("")
    + `<div class="proj-card new" data-proj-new="1">＋ 開一部新作品</div>`;
}

function newProjectFromIdea() {
  const idea = $("#prompt").value.trim();
  const ids = state.tray.filter(t => assetById(t.id)?.kind !== undefined).map(t => t.id);
  location.href = projectUrl("new=" + encodeURIComponent(idea) + (ids.length ? "&ids=" + ids.join(",") : ""));
}

function setMode(mode) {
  state.mode = mode;
  const isProjects = mode === "projects";
  if (!PICTURE_TRAY.has(mode) && !isProjects) state.videoMode = mode;
  const isImage = mode === "image", isAgent = mode === "agent";
  const group = isImage ? "image" : isAgent ? "agent" : isProjects ? "projects" : "video";
  $("#projPane").hidden = !isProjects;
  $("#projRow").hidden = !isProjects;
  document.querySelector(".options").hidden = isProjects;
  for (const b of $("#modeTabs").children) b.classList.toggle("active", b.dataset.mode === mode);
  for (const el of document.querySelectorAll(".opt[data-only]")) el.hidden = !el.dataset.only.split(" ").includes(group);
  $("#refArea").hidden = !(mode === "ref" || PICTURE_TRAY.has(mode));
  $("#refHint").innerHTML = REF_HINT[PICTURE_TRAY.has(mode) ? mode : "ref"];
  $("#rewriteRow").hidden = isAgent || isProjects;
  $("#agentRow").hidden = !isAgent;
  $("#agentPanel").hidden = !isAgent;
  $("#rewriteBox").hidden = isAgent || !$("#rewriteText").value;
  $("#framesArea").hidden = mode !== "frames";
  if (!state.busy) {
    $("#btnGenerate").innerHTML = UP_ARROW_SVG;
    $("#btnGenerate").title = isImage ? "生成影像" : "生成影片";
  }
  if (state.config) checkRewriter();
  $("#prompt").placeholder = "";
  if ($("#editorPlaceholder")) {
    $("#editorPlaceholder").innerHTML = RICH_PLACEHOLDER[mode] || PLACEHOLDER[mode] || "";
  }
  $("#extendNote").hidden = !(mode === "ref" && state.tray.some(t => t.extend));
  renderLibrary();
  renderHighlight();
  syncToolbarPills();
  markStale();
}

// ------------------------------------------------------------ editor: highlight + @ menu
function renderHighlight() {
  const ta = $("#prompt");
  if ($("#editorPlaceholder")) {
    $("#editorPlaceholder").hidden = ta.value.length > 0;
  }
  const valid = new Set(trayLabels().filter(t => t.label).map(t => t.label));
  let html = "";
  let last = 0;
  if (state.mode === "ref" || PICTURE_TRAY.has(state.mode)) {
    for (const m of ta.value.matchAll(MENTION_RE)) {
      const kind = MENTION_KIND[m[1].toLowerCase()] || MENTION_KIND[m[1]];
      const ok = valid.has(KIND_LABEL[kind] + m[2]);
      html += esc(ta.value.slice(last, m.index)) + `<mark class="${ok ? "m-" + kind : "m-bad"}">${esc(m[0])}</mark>`;
      last = m.index + m[0].length;
    }
  }
  $("#editorBackdrop").innerHTML = html + esc(ta.value.slice(last)) + "\n";
  $("#editorBackdrop").scrollTop = ta.scrollTop;
  syncToolbarPills();
}

function caretCoords(ta) {
  const mirror = document.createElement("div");
  const cs = getComputedStyle(ta);
  for (const p of ["fontFamily", "fontSize", "lineHeight", "padding", "border", "width", "whiteSpace", "wordWrap", "letterSpacing", "boxSizing"])
    mirror.style[p] = cs[p];
  Object.assign(mirror.style, { position: "absolute", visibility: "hidden", top: 0, left: 0 });
  mirror.textContent = ta.value.slice(0, ta.selectionStart);
  const marker = document.createElement("span");
  marker.textContent = "​";
  mirror.appendChild(marker);
  ta.parentElement.appendChild(mirror);
  const pos = { top: marker.offsetTop - ta.scrollTop + parseFloat(cs.lineHeight), left: marker.offsetLeft };
  mirror.remove();
  return pos;
}

const menu = { open: false, items: [], index: 0, start: 0 };

function updateMentionMenu() {
  if (state.mode !== "ref" && !PICTURE_TRAY.has(state.mode)) return closeMenu();
  const ta = $("#prompt");
  const upto = ta.value.slice(0, ta.selectionStart);
  const m = upto.match(/@([^\s@]{0,12})$/);
  if (!m) return closeMenu();
  const q = m[1].toLowerCase();
  menu.items = trayLabels().filter(t => t.label && (!q || t.label.toLowerCase().includes(q) || t.asset.name.toLowerCase().includes(q)));
  menu.start = ta.selectionStart - m[0].length;
  menu.index = Math.min(menu.index, Math.max(0, menu.items.length - 1));
  const el = $("#mentionMenu");
  el.innerHTML = menu.items.length ? menu.items.map((t, i) => `
    <div class="mi ${i === menu.index ? "active" : ""}" data-i="${i}">
      <div class="cthumb" ${thumbStyle(t.asset)}>${t.asset.thumb ? "" : KIND_ICON[t.asset.kind]}</div>
      <div><b>@${t.label}</b><div class="hint">${esc(t.asset.name)}</div></div></div>`).join("")
    : `<div class="empty">${state.tray.length ? "沒有符合的素材" : "請先點擊左側「＋參考」上傳或從素材庫加入"}</div>`;
  const pos = caretCoords(ta);
  Object.assign(el.style, { top: pos.top + "px", left: Math.min(pos.left, Math.max(0, ta.clientWidth - 240)) + "px" });
  el.hidden = false;
  menu.open = true;
}

function closeMenu() { $("#mentionMenu").hidden = true; menu.open = false; menu.index = 0; }

function insertMention(label, replaceFrom) {
  const ta = $("#prompt");
  const from = replaceFrom ?? ta.selectionStart;
  const text = `@${label} `;
  ta.value = ta.value.slice(0, from) + text + ta.value.slice(ta.selectionStart);
  ta.selectionStart = ta.selectionEnd = from + text.length;
  ta.focus();
  closeMenu();
  renderHighlight();
  markStale();
}

// ------------------------------------------------------------ rewrite + generate
function requestBody() {
  return {
    mode: state.mode, prompt: $("#prompt").value, tray: state.mode === "ref" || state.mode === "image" ? state.tray : [],
    image_size: state.imageSize,
    first_id: state.mode === "frames" ? state.first : null, last_id: state.mode === "frames" ? state.last : null,
    seconds: +$("#seconds").value, aspect: state.aspect, resolution: state.resolution, intensity: state.intensity,
    skill: $("#skill").value, seed: $("#seed").value === "" ? null : +$("#seed").value, fast: $("#fastMode").checked,
  };
}
const signature = () => { const b = requestBody(); return JSON.stringify([b.mode, b.prompt, b.tray, b.first_id, b.last_id, b.mode === "image" ? 0 : b.seconds, b.skill]); };

function markStale() {
  if ($("#rewriteBox").hidden) return;
  $("#rewriteStale").hidden = state.rewriteSig === signature();
}

function setBusy(busy, label) {
  state.busy = busy;
  $("#btnGenerate").disabled = busy;
  $("#btnRewrite").disabled = busy || $("#useRewrite").disabled;
  $("#btnGenerate").innerHTML = busy ? SPINNER_SVG : UP_ARROW_SVG;
  $("#btnGenerate").title = busy ? (label || "處理中…") : (state.mode === "image" ? "生成影像" : "生成影片");
}

function validate() {
  const body = requestBody();
  if (!body.prompt.trim()) throw new Error("請先輸入提示詞描述");
  if (body.mode === "frames" && !body.first_id && !body.last_id) throw new Error("首尾幀模式至少要放一張首幀或尾幀圖片");
  if (body.mode === "ref" || body.mode === "image") {
    const valid = new Set(trayLabels().filter(t => t.label).map(t => t.label));
    for (const m of body.prompt.matchAll(MENTION_RE)) {
      const kind = MENTION_KIND[m[1].toLowerCase()] || MENTION_KIND[m[1]];
      if (!valid.has(KIND_LABEL[kind] + m[2])) throw new Error(`${m[0]} 沒有對應的參考素材`);
    }
  }
  return body;
}

function rewriteTimer(on) {
  const box = $("#rewriteProgress");
  clearInterval(rewriteTimer.t);
  if (!on) { box.hidden = true; return; }
  const expect = Math.max(8, state.config?.rewrite_seconds || 25);
  const t0 = Date.now();
  box.hidden = false;
  const tick = () => {
    const el = (Date.now() - t0) / 1000;
    const pct = Math.min(95, 100 * (1 - Math.exp(-el / expect * 1.6)));
    $(".bar i", box).style.width = pct + "%";
    $("span", box).textContent = `✨ 提示詞改寫中… ${fmtEta(Math.max(0, expect - el))}`;
  };
  tick();
  rewriteTimer.t = setInterval(tick, 500);
}

async function doRewrite() {
  const body = validate();
  const sig = signature();
  setBusy(true, "改寫中…");
  $("#btnRewrite").textContent = "改寫中…";
  rewriteTimer(true);
  try {
    const r = await api("api/rewrite", body);
    $("#rewriteText").value = r.prompt;
    $("#rewriteMeta").textContent = `· ${TASK_NAME[r.task] || r.task} · ${r.seconds_used} 秒`;
    $("#rewriteBox").hidden = false;
    state.rewriteSig = sig;
    if (state.config) state.config.rewrite_seconds = r.seconds_used;
    markStale();
    return r.prompt;
  } finally {
    rewriteTimer(false);
    setBusy(false);
    $("#btnRewrite").textContent = "改寫預覽";
  }
}

async function doGenerate() {
  try {
    const body = validate();
    if ($("#useRewrite").checked) {
      body.final_prompt = state.rewriteSig === signature() && $("#rewriteText").value.trim()
        ? $("#rewriteText").value : await doRewrite();
    }
    setBusy(true, "送出中…");
    await api("api/generate", body);
    toast("已送出生成任務 ✨");
    await loadJobs();
  } catch (e) {
    toast(e.message, true);
  } finally {
    setBusy(false);
  }
}

// ------------------------------------------------------------ jobs
const STATUS = { queued: "排隊中", running: "生成中", joining: "接回原片", done: "完成", error: "失敗" };
const STAGE = {
  queued: "排隊等待 ComfyUI 執行…", prepare: "準備素材與載入模型", draw: "正在繪製影像細節", low: "生成低解析度動態與構圖",
  high: "潛空間放大與高解析度精修", decode: "解碼影音與輸出檔案", join: "將新片段無縫接回原片",
};
const INTENSITY = { off: "文戲", low: "動作·低", mid: "動作·中", high: "動作·高" };

function viewUrl(v) { return `api/view?filename=${encodeURIComponent(v.filename)}&subfolder=${encodeURIComponent(v.subfolder || "")}&type=${v.type || "output"}`; }

function jobMedia(j) {
  if (j.mode === "image" && j.status === "done" && j.image) {
    const cheer = state.cheer?.has(j.id) ? `<img class="cheer" src="static/img/cheer.png" alt="">` : "";
    return `<div class="media">${cheer}<a href="${viewUrl(j.image)}" target="_blank"><img class="result" src="${viewUrl(j.image)}" alt=""></a></div>`;
  }
  if (j.status === "done" && (j.video || j.full_video)) {
    const tabs = [];
    if (j.full_video) tabs.push(["full", "完整版", `outputs/${j.full_video}`]);
    if (j.video) tabs.push(["part", j.full_video ? "只看新片段" : "影片", viewUrl(j.video)]);
    const pick = state.videoTab[j.id] || tabs[0][0];
    const cur = tabs.find(t => t[0] === pick) || tabs[0];
    const cheer = state.cheer?.has(j.id) ? `<img class="cheer" src="static/img/cheer.png" alt="">` : "";
    return `<div class="media">${cheer}<video controls preload="metadata" src="${cur[2]}"></video></div>` +
      (tabs.length > 1 ? `<div class="vtabs">${tabs.map(t => `<button data-tab="${t[0]}" class="${t[0] === cur[0] ? "active" : ""}">${t[1]}</button>`).join("")}</div>` : "");
  }
  if (j.status === "error") {
    return `<div class="stage err"><img src="static/img/oops.png" alt=""><div class="slabel">生成失敗</div></div>`;
  }
  const p = j.progress || { stage: "queued", percent: 0 };
  const color = p.stage === "high" ? "blue" : p.stage === "decode" || p.stage === "join" ? "green" : "";
  return `<div class="stage run"><img src="static/img/mascot.png" alt="">
    <div class="slabel">${STAGE[p.stage] || "生成中"}</div>
    <div class="bar ${color}"><i style="width:${p.percent}%"></i></div>
    <div class="eta">${p.percent}% · ${fmtEta(p.eta)}${p.live === false ? "（預估時間）" : ""}</div></div>`;
}

async function loadJobs() {
  let jobs = [];
  try { jobs = await api("api/jobs"); } catch (e) { return schedulePoll(true); }
  state.cheer = state.cheer || new Set();
  for (const j of jobs) {
    const prev = state.seenStatus[j.id];
    if (prev && prev !== "done" && j.status === "done") {
      state.cheer.add(j.id);
      setTimeout(() => { state.cheer.delete(j.id); renderJobs(); }, 4000);
    }
    state.seenStatus[j.id] = j.status;
  }
  state.jobs = jobs;
  const badge = $("#jobCountBadge");
  if (badge) {
    badge.textContent = jobs.length;
    badge.hidden = jobs.length === 0;
  }
  renderJobs();
  schedulePoll(jobs.some(j => ["queued", "running", "joining"].includes(j.status)));
}

function renderJobs() {
  const list = $("#jobList");
  const filtered = state.jobs.filter(j => {
    if (state.jobFilter === "image") return j.mode === "image";
    if (state.jobFilter === "video") return j.mode !== "image";
    return true;
  });
  const html = filtered.map(j => {
    const skill = j.skill ? (state.config?.skills.find(s => s.id === j.skill)?.title || j.skill) : "";
    return `<div class="job" data-id="${j.id}">${jobMedia(j)}<div class="jbody">
      <div class="jmeta"><span class="st ${j.status}">${STATUS[j.status]}</span>
        <span class="task">${TASK_NAME[j.task] || "參考"}${j.extend ? "＋延伸" : ""}</span>
        <span>${j.width ? `${j.width}×${j.height}` : `${j.aspect} ${j.resolution}`}</span>
        ${j.mode === "image" ? `<span>${(j.refs || []).length} 張參考</span>` :
          `<span>${j.seconds}s</span><span>${INTENSITY[j.intensity] || j.intensity}</span>${skill ? `<span>${esc(skill)}</span>` : ""}`}
        <span>seed ${j.seed}</span><span>${new Date(j.created * 1000).toLocaleString()}</span></div>
      <div class="jprompt" title="${esc(j.prompt)}">${esc(j.prompt)}</div>
      ${j.error ? `<div class="error">${esc(j.error)}</div>` : ""}${j.join_error ? `<div class="error">${esc(j.join_error)}</div>` : ""}
      <div class="jactions">
        <button class="pill small" data-act="reuse">載入設定</button>
        <button class="pill small" data-act="copy">複製提示詞</button>
        ${j.status === "done" && j.mode === "image" ? `<button class="pill small blue" data-act="tovideo" title="將這張影像設為首幀並直接生成影片">🎬 變成影片</button>` : ""}
        ${j.status === "done" ? `<button class="pill small mint" data-act="tolib">＋加入素材庫</button>` : ""}
        <button class="pill small" data-act="del">移除</button>
      </div></div></div>`;
  });
  const cards = [...list.children].filter(c => c.classList.contains("job"));
  const same = cards.length === html.length && cards.every((c, i) => c.dataset.id === filtered[i].id);
  if (!same) {
    list.innerHTML = html.join("") || `<div class="hint">尚無符合條件的生成作品，在上方輸入框寫一段描述開始創作吧！</div>`;
    [...list.children].forEach((c, i) => { if (html[i]) c.dataset.html = html[i]; });
    return;
  }
  html.forEach((h, i) => {
    if (cards[i].dataset.html !== h) {
      const tmp = document.createElement("div");
      tmp.innerHTML = h;
      tmp.firstElementChild.dataset.html = h;
      cards[i].replaceWith(tmp.firstElementChild);
    }
  });
}

function schedulePoll(active) {
  clearTimeout(schedulePoll.timer);
  schedulePoll.timer = setTimeout(loadJobs, active ? 2000 : 20000);
}

function reuseJob(job) {
  if (job.mode === "image") { state.imageSize = job.image_size || state.imageSize; setTop("image"); }
  else { state.videoMode = job.mode || "ref"; setTop("video"); }
  $("#prompt").value = job.prompt;
  state.tray = (job.tray || []).filter(t => assetById(t.id));
  state.first = job.first_id && assetById(job.first_id) ? job.first_id : null;
  state.last = job.last_id && assetById(job.last_id) ? job.last_id : null;
  state.aspect = job.aspect; state.resolution = job.resolution; state.intensity = job.intensity;
  $("#seconds").value = job.seconds; $("#secondsVal").textContent = job.seconds;
  $("#skill").value = job.skill || "";
  $("#seed").value = job.seed;
  syncSegs();
  renderTray(); renderSlots(); renderLibrary();
  $("#rewriteText").value = job.final_prompt;
  $("#rewriteBox").hidden = false;
  state.rewriteSig = signature();
  markStale();
  window.scrollTo({ top: 0, behavior: "smooth" });
  toast("已載入該作品的生成設定與提示詞");
}

async function imageToVideo(job) {
  toast("正在將影像加入素材庫並設為首幀…");
  const item = await api("api/library/from_job", { id: job.id, folder: state.folder });
  if (!assetById(item.id)) state.library.unshift(item);
  state.videoMode = "frames";
  setTop("video");
  state.first = item.id; state.last = null; state.aspect = "auto";
  syncSegs(); renderSlots();
  $("#prompt").value = "讓這張圖自然地動起來，延續畫面裡的情境：" + job.prompt.replace(MENTION_RE, "").trim();
  renderHighlight();
  await checkRewriter();
  await doGenerate();
}

// ------------------------------------------------------------ 🤖 短片 Agent
const RUN_STATUS = { queued: "排隊中", planning: "規劃中", generating: "生成中", done: "完成", error: "失敗" };
const SEG_STATUS = { waiting: "等待", rewriting: "改寫提示詞", generating: "生成中", done: "完成" };

async function startAgent() {
  const prompt = $("#prompt").value.trim();
  if (!prompt) throw new Error("請先輸入你想要的短片主題或故事描述");
  const images = state.tray.filter(t => assetById(t.id)?.kind === "image").map(t => t.id);
  await api("api/agent/start", {
    instruction: prompt, images, total: +$("#agentSeconds").value, aspect: state.aspect,
    resolution: state.resolution, intensity: state.intensity, planner: $("#agentPlanner").value,
  });
  toast("開始規劃短片分鏡 🤖");
  loadRuns();
}

function runCard(r) {
  const plan = r.plan || {};
  const meta = r.plan_meta;
  const segs = (plan.segments || []).map((s, i) => {
    const st = (r.segments || [])[i]?.status || "waiting";
    const lines = (s.dialogue || []).filter(d => d.line).map(d => `<div class="line">${esc(d.who)}：「${esc(d.line)}」</div>`).join("");
    return `<div class="aseg"><div><div class="sno">第 ${i + 1} 段</div><div>${s.seconds}s</div><span class="sst ${st}">${SEG_STATUS[st] || st}</span></div>
      <div>${esc(s.action)}${lines}<div class="hint">結尾：${esc(s.end_state)}</div></div></div>`;
  }).join("");
  const fin = r.final?.full_video ? `outputs/${r.final.full_video}` : r.final?.video ? viewUrl(r.final.video) : "";
  return `<div class="run" data-id="${r.id}">
    <h3>${esc(plan.title || r.instruction.slice(0, 30))}</h3>
    ${plan.logline ? `<div class="logline">${esc(plan.logline)}</div>` : ""}
    <div class="rmeta"><span>${RUN_STATUS[r.status] || r.status}</span><span>${r.total} 秒</span><span>${r.aspect} ${r.resolution}</span>
      <span>${r.planner === "gemini" ? "💎 Gemini" : "🧠 本地 MoE"}</span>
      ${meta ? `<span>規劃 ${meta.seconds} 秒・${meta.rounds} 輪</span>` : ""}</div>
    ${r.error ? `<div class="error">${esc(r.error)}</div>` : ""}
    ${fin ? `<video controls preload="metadata" src="${fin}"></video>` : ""}
    ${segs}
    ${r.status === "planning" || r.status === "generating" ? `<div class="revise"><button class="pill small" data-ract="stop">停止</button></div>` :
      r.plan ? `<div class="revise"><input placeholder="想改什麼？例：第二段改成下雨，她撐著傘" maxlength="200"><button class="pill small blue" data-ract="revise">✏️ 修改</button></div>` : ""}
    <div class="rlog">${(r.log || []).slice(-4).map(l => esc(l[1])).join("<br>")}</div></div>`;
}

async function loadRuns() {
  clearTimeout(loadRuns.t);
  if (state.top !== "agent") return;
  let runs = [];
  try { runs = await api("api/agent/runs"); } catch (e) { return; }
  const panel = $("#agentPanel");
  const html = runs.slice(0, 5).map(runCard).join("") || `<div class="hint">尚無短片任務。加入參考圖、寫一句故事構想，點擊右側發送按鈕即可自動規劃並生成短片。</div>`;
  const el = document.activeElement;
  const typing = panel.contains(el) && el.tagName === "INPUT" && el.value;
  const playing = [...panel.querySelectorAll("video")].some(v => !v.paused);
  if (panel.dataset.html !== html && !typing && !playing) { panel.innerHTML = html; panel.dataset.html = html; }
  const active = runs.some(r => ["queued", "planning", "generating"].includes(r.status));
  loadRuns.t = setTimeout(loadRuns, active ? 3000 : 15000);
}

// ------------------------------------------------------------ settings
async function loadConfig() {
  state.config = await api("api/config");
  $("#skill").innerHTML = `<option value="">不使用</option>` +
    state.config.skills.map(s => `<option value="${s.id}">${esc(s.title)}</option>`).join("");
  $("#aspectSeg").innerHTML = `<button type="button" data-v="auto" title="首尾幀跟隨首幀比例、延伸跟隨原片；生圖跟隨第一張參考圖">自動</button>` +
    state.config.aspects.map(a => `<button type="button" data-v="${a}">${a}</button>`).join("");
  $("#imgSizeSeg").innerHTML = (state.config.image_sizes || []).map(v => `<button type="button" data-v="${v}">${v}</button>`).join("");
  syncSegs();
  checkRewriter();
}

async function checkRewriter() {
  const rw = state.config.config.rewriter;
  const imageLocal = state.mode === "image" && (rw.provider || "local") === "local";
  let ok = !imageLocal && rw.provider && rw.provider !== "local";
  if (!ok && !imageLocal) {
    let models = [];
    try { models = await api("api/models"); } catch (e) { return; }
    ok = models.includes(rw.model);
  }
  const sw = $("#useRewrite");
  if (!ok) sw.checked = false;
  else if (sw.disabled) sw.checked = true;
  sw.disabled = !ok;
  $("#btnRewrite").disabled = !ok;
  sw.closest("label").title = ok ? "自動將描述改寫為 H3 官方多模態格式" : imageLocal ? "影像改寫需使用 Gemini 或 OpenAI 端點（本地 9B 為影片專用）"
    : "未偵測到本地改寫模型：可至設定切換 Gemini 或 OpenAI 端點";
  const name = { local: "本地", gemini: "Gemini", openai: "OpenAI" }[rw.provider || "local"];
  const labelEl = sw.closest("label").querySelector("small");
  if (labelEl) labelEl.textContent = `✨ 改寫 (${name})`;
}

function syncSegs() {
  for (const [id, key] of [["#aspectSeg", "aspect"], ["#resSeg", "resolution"], ["#intensitySeg", "intensity"], ["#imgSizeSeg", "imageSize"]])
    for (const b of $(id).children) b.classList.toggle("active", b.dataset.v === state[key]);
  syncToolbarPills();
}

const GEMINI_DEFAULTS = ["gemini-3.8-flash", "gemini-3.5-flash-lite", "gemma-4-26b-a4b-it"];

function setDatalist(id, names) {
  $(id).innerHTML = [...new Set(names)].map(n => `<option value="${esc(n)}">`).join("");
}

function showProvider(p) {
  for (const r of document.querySelectorAll("[name=provider]")) r.checked = r.value === p;
  for (const el of document.querySelectorAll(".prov-panel")) el.classList.toggle("on", el.dataset.panel === p);
}

function keyPlaceholder(el, has) {
  el.value = "";
  el.dataset.clear = "";
  el.placeholder = has ? "已儲存（留空＝繼續使用）" : "貼上 API 金鑰";
}

async function openSettings() {
  closePopovers();
  const { config: c, lora_nodes } = state.config;
  const rw = c.rewriter;
  let models = [];
  try { models = await api("api/models"); } catch (e) { toast(e.message, true); }
  if (!models.includes(rw.model)) models.unshift(rw.model);
  $("#cfgModel").innerHTML = models.map(m => `<option ${m === rw.model ? "selected" : ""}>${esc(m)}</option>`).join("");
  $("#cfgThinking").checked = rw.thinking;
  $("#cfgMtp").value = rw.mtp;
  $("#cfgMaxLen").value = rw.max_length;
  $("#cfgTemp").value = rw.temperature;
  $("#cfgTopP").value = rw.top_p;
  $("#cfgSide").value = rw.image_side;
  keyPlaceholder($("#cfgGemKey"), rw.gemini.has_key);
  $("#cfgGemModel").value = rw.gemini.model || GEMINI_DEFAULTS[0];
  setDatalist("#gemModels", GEMINI_DEFAULTS);
  $("#cfgOaUrl").value = rw.openai.base_url || "";
  keyPlaceholder($("#cfgOaKey"), rw.openai.has_key);
  $("#cfgOaModel").value = rw.openai.model || "";
  for (const id of ["#gemResult", "#oaResult"]) { $(id).textContent = ""; $(id).className = "vres"; }
  showProvider(rw.provider || "local");
  $("#cfgUrl").value = c.comfy_url;
  $("#cfgLoras").innerHTML = lora_nodes.map(l => `
    <div class="lora-row"><span class="lname" title="${esc(l.name)}">${esc(l.name)} <span class="hint">×${(+l.strength).toFixed(2)}</span></span>
      <select data-lora="${l.id}"><option value="drama">文戲（常駐）</option><option value="action" ${c.loras.action.includes(l.id) ? "selected" : ""}>動作</option></select></div>`).join("");
  $("#settings").showModal();
}

function keyValue(el) { return el.dataset.clear ? "__CLEAR__" : el.value.trim(); }

function providerForm(p) {
  if (p === "gemini") return { api_key: $("#cfgGemKey").value.trim(), model: $("#cfgGemModel").value.trim() };
  return { base_url: $("#cfgOaUrl").value.trim(), api_key: $("#cfgOaKey").value.trim(), model: $("#cfgOaModel").value.trim() };
}

async function verifyProvider(p) {
  const out = $(p === "gemini" ? "#gemResult" : "#oaResult");
  out.className = "vres"; out.textContent = "連線中…";
  try {
    const r = await api("api/rewriter/test", { provider: p, conf: providerForm(p) });
    out.className = "vres ok";
    out.textContent = `✅ 端點活著（${(r.ms / 1000).toFixed(1)} 秒，回覆「${r.reply}」）` + (r.models.length ? `，清單抓到 ${r.models.length} 個模型` : "");
    if (r.models.length) setDatalist(p === "gemini" ? "#gemModels" : "#oaModels", (p === "gemini" ? GEMINI_DEFAULTS : []).concat(r.models));
  } catch (e) {
    out.className = "vres bad"; out.textContent = "❌ " + e.message;
  }
}

async function saveSettings() {
  const c = structuredClone(state.config.config);
  const provider = document.querySelector("[name=provider]:checked")?.value || "local";
  Object.assign(c.rewriter, {
    provider, model: $("#cfgModel").value, thinking: $("#cfgThinking").checked, mtp: $("#cfgMtp").value,
    max_length: +$("#cfgMaxLen").value, temperature: +$("#cfgTemp").value, top_p: +$("#cfgTopP").value,
    image_side: +$("#cfgSide").value,
    gemini: { api_key: keyValue($("#cfgGemKey")), model: $("#cfgGemModel").value.trim() || GEMINI_DEFAULTS[0] },
    openai: { base_url: $("#cfgOaUrl").value.trim(), api_key: keyValue($("#cfgOaKey")), model: $("#cfgOaModel").value.trim() },
  });
  c.comfy_url = $("#cfgUrl").value.trim();
  const sel = [...document.querySelectorAll("[data-lora]")];
  c.loras = { drama: sel.filter(s => s.value === "drama").map(s => s.dataset.lora), action: sel.filter(s => s.value === "action").map(s => s.dataset.lora) };
  await api("api/config", c);
  await loadConfig();
  toast("設定已儲存");
}

async function checkComfy() {
  const el = $("#comfyStatus");
  try {
    await api("api/models");
    el.textContent = "ComfyUI 已連線"; el.className = "pill status ok";
  } catch (e) {
    el.textContent = "ComfyUI 未連線"; el.className = "pill status bad";
  }
}

// ------------------------------------------------------------ wiring
function bind() {
  // Left rail & collapsible library drawer
  $("#btnToggleLib")?.addEventListener("click", () => toggleLibraryDrawer());
  $("#btnTopLib")?.addEventListener("click", () => toggleLibraryDrawer());
  $("#btnCloseLib")?.addEventListener("click", () => toggleLibraryDrawer(false));
  $("#navCreate")?.addEventListener("click", () => {
    window.scrollTo({ top: 0, behavior: "smooth" });
    $("#prompt")?.focus();
  });
  $("#btnScrollResults")?.addEventListener("click", () => {
    $("#resultsSection")?.scrollIntoView({ behavior: "smooth" });
  });

  // Upload inputs
  $("#fileInput").addEventListener("change", e => { uploadFiles([...e.target.files]); e.target.value = ""; });
  $("#quickRefInput")?.addEventListener("change", e => {
    uploadFiles([...e.target.files], "tray");
    e.target.value = "";
  });
  $("#quickSlotInput")?.addEventListener("change", e => {
    uploadFiles([...e.target.files], state.pendingSlot || "first");
    e.target.value = "";
  });

  // +參考 button & quick menu
  $("#btnAddRef")?.addEventListener("click", e => {
    e.stopPropagation();
    const qm = $("#refQuickMenu");
    const wasHidden = qm.hidden;
    closePopovers();
    qm.hidden = !wasHidden;
  });
  $("#refQuickMenu")?.addEventListener("click", e => {
    e.stopPropagation();
    const act = e.target.closest("[data-qact]")?.dataset.qact;
    $("#refQuickMenu").hidden = true;
    if (act === "upload") $("#quickRefInput").click();
    if (act === "library") toggleLibraryDrawer(true);
  });

  // Popover toggles on bottom toolbar
  const popPairs = [
    ["#pillModel", "#popModel"],
    ["#pillMode", "#popMode"],
    ["#pillRatio", "#popRatio"],
    ["#pillDuration", "#popDuration"],
    ["#pillDirector", "#popDirector"],
  ];
  for (const [btnSel, popSel] of popPairs) {
    $(btnSel)?.addEventListener("click", e => {
      e.stopPropagation();
      const pop = $(popSel);
      const wasHidden = pop.hidden;
      closePopovers();
      if (wasHidden) {
        pop.hidden = false;
        $(btnSel).classList.add("open");
      }
    });
    $(popSel)?.addEventListener("click", e => e.stopPropagation());
  }
  document.addEventListener("click", () => closePopovers());
  $("#btnPopSettings")?.addEventListener("click", () => openSettings());

  // Toolbar @ button
  $("#btnToolbarAt")?.addEventListener("click", e => {
    e.stopPropagation();
    const ta = $("#prompt");
    const pos = ta.selectionStart ?? ta.value.length;
    const before = ta.value.slice(0, pos);
    const needSpace = before.length > 0 && !/\s$/.test(before);
    const ins = (needSpace ? " @" : "@");
    ta.value = before + ins + ta.value.slice(ta.selectionEnd ?? pos);
    ta.selectionStart = ta.selectionEnd = pos + ins.length;
    ta.focus();
    renderHighlight();
    updateMentionMenu();
  });

  // Duration presets
  $("#secPresets")?.addEventListener("click", e => {
    const sec = e.target.dataset.sec;
    if (sec) {
      $("#seconds").value = sec;
      $("#secondsVal").textContent = sec;
      syncToolbarPills();
      markStale();
    }
  });
  $("#agentSecPresets")?.addEventListener("click", e => {
    const asec = e.target.dataset.asec;
    if (asec) {
      $("#agentSeconds").value = asec;
      $("#agentSecondsVal").textContent = asec;
      syncToolbarPills();
    }
  });

  const dz = $("#dropZone");
  const isFiles = e => [...(e.dataTransfer?.types || [])].includes("Files");
  for (const ev of ["dragenter", "dragover"]) document.addEventListener(ev, e => { e.preventDefault(); if (isFiles(e)) dz.classList.add("over"); });
  for (const ev of ["dragleave", "drop"]) document.addEventListener(ev, e => { e.preventDefault(); dz.classList.remove("over"); });
  document.addEventListener("drop", e => {
    if (!isFiles(e) || !e.dataTransfer.files.length) return;
    const inComposer = e.target.closest?.(".composer-card");
    uploadFiles([...e.dataTransfer.files], inComposer && (state.mode === "ref" || PICTURE_TRAY.has(state.mode)) ? "tray" : undefined);
  });

  // library folders: drag a card onto a folder (or onto 全部素材 to take it out)
  const lib = $(".library");
  lib.addEventListener("dragstart", e => {
    const card = e.target.closest?.(".card");
    if (!card) return;
    e.dataTransfer.setData("application/x-h3t-asset", card.dataset.id);
    e.dataTransfer.effectAllowed = "move";
    card.classList.add("dragging");
  });
  lib.addEventListener("dragend", e => e.target.closest?.(".card")?.classList.remove("dragging"));
  const dropTarget = e => e.target.closest?.(".folder, [data-crumb=root]");
  lib.addEventListener("dragover", e => {
    const t = dropTarget(e);
    if (t && [...e.dataTransfer.types].includes("application/x-h3t-asset")) { e.preventDefault(); t.classList.add("drop"); }
  });
  lib.addEventListener("dragleave", e => dropTarget(e)?.classList.remove("drop"));
  lib.addEventListener("drop", e => {
    const t = dropTarget(e);
    const id = e.dataTransfer.getData("application/x-h3t-asset");
    if (!t || !id) return;
    e.preventDefault();
    t.classList.remove("drop");
    moveAssets([id], t.dataset.folder || null).catch(err => toast(err.message, true));
  });
  $("#crumbs").addEventListener("click", e => { if (e.target.dataset.crumb === "root") { state.folder = null; renderLibrary(); } });
  $("#btnNewFolder").addEventListener("click", () => {
    const name = prompt("資料夾名稱（例：第三晚短劇、角色定裝）", "新資料夾");
    if (name !== null) folderAction({ action: "create", name }).catch(err => toast(err.message, true));
  });

  // settings: provider tabs, verify, clear stored keys, fast-mode hint
  $("#cfgProvider").addEventListener("change", e => showProvider(e.target.value));
  $("#settings").addEventListener("click", e => {
    if (e.target.dataset.verify) verifyProvider(e.target.dataset.verify);
    const k = e.target.dataset.clearKey;
    if (k) {
      const el = $(k === "gemini" ? "#cfgGemKey" : "#cfgOaKey");
      el.value = ""; el.dataset.clear = "1"; el.placeholder = "儲存後會刪除已存的金鑰";
    }
  });
  for (const id of ["#cfgGemKey", "#cfgOaKey"]) $(id).addEventListener("input", e => (e.target.dataset.clear = ""));
  $("#fastMode").addEventListener("change", e => {
    $("#fastHint").textContent = e.target.checked ? "8 步" : "12 步";
    syncToolbarPills();
  });
  $("#agentPlanner")?.addEventListener("change", () => syncToolbarPills());
  $("#seed")?.addEventListener("input", () => syncToolbarPills());

  $("#projGrid").addEventListener("click", e => {
    const c = e.target.closest("[data-proj]");
    if (c) location.href = projectUrl("p=" + encodeURIComponent(c.dataset.proj));
    if (e.target.closest("[data-proj-new]")) newProjectFromIdea();
  });
  $("#btnNewProject").addEventListener("click", newProjectFromIdea);
  $("#topTabs").addEventListener("click", e => { const b = e.target.closest("[data-top]"); if (b && !b.disabled) setTop(b.dataset.top); });
  $("#modeTabs").addEventListener("click", e => {
    const b = e.target.closest("[data-mode]");
    if (b) {
      setMode(b.dataset.mode);
      closePopovers();
    }
  });
  $("#libTabs").addEventListener("click", e => {
    if (!e.target.dataset.kind) return;
    state.libKind = e.target.dataset.kind;
    for (const b of $("#libTabs").children) b.classList.toggle("active", b === e.target);
    renderLibrary();
  });
  $("#libGrid").addEventListener("click", async e => {
    const folderEl = e.target.closest(".folder");
    if (folderEl) {
      const fid = folderEl.dataset.folder;
      const fact = e.target.dataset.fact || "open";
      const f = state.folders.find(x => x.id === fid);
      if (fact === "open") { state.folder = fid; renderLibrary(); }
      if (fact === "rename") {
        const name = prompt("新的資料夾名稱", f.name);
        if (name) folderAction({ action: "rename", id: fid, name }).catch(err => toast(err.message, true));
      }
      if (fact === "delete" && confirm(`刪除資料夾「${f.name}」？裡面的素材不會被刪，會移回全部素材。`))
        folderAction({ action: "delete", id: fid }).catch(err => toast(err.message, true));
      return;
    }
    const act = e.target.dataset.act;
    const id = e.target.closest(".card")?.dataset.id;
    if (act === "add") addToTray(id);
    if (act === "first" || act === "last") {
      const other = act === "first" ? "last" : "first";
      if (state[other] === id) state[other] = null;
      state[act] = id;
      renderSlots();
    }
    if (act === "del" && confirm("確定要從素材庫刪除這個素材？")) {
      await api("api/library/delete", { id });
      const i = state.tray.findIndex(t => t.id === id);
      if (i >= 0) removeFromTray(i);
      await loadLibrary();
    }
  });
  $("#framesArea").addEventListener("click", e => {
    const clearSlot = e.target.dataset.clear;
    if (clearSlot) {
      e.stopPropagation();
      state[clearSlot] = null;
      renderSlots();
      return;
    }
    const slotEl = e.target.closest(".slot");
    if (slotEl) {
      state.pendingSlot = slotEl.dataset.slot;
      if (state.library.some(a => a.kind === "image")) {
        toggleLibraryDrawer(true);
        toast(`請在左側素材庫點選影像設為${state.pendingSlot === "first" ? "首幀" : "尾幀"}，或雙擊此處直接上傳`);
      } else {
        $("#quickSlotInput").click();
      }
    }
  });
  $("#framesArea").addEventListener("dblclick", e => {
    const slotEl = e.target.closest(".slot");
    if (slotEl) {
      state.pendingSlot = slotEl.dataset.slot;
      $("#quickSlotInput").click();
    }
  });

  $("#tray").addEventListener("click", e => {
    const chip = e.target.closest(".chip");
    if (!chip || e.target.tagName === "INPUT") return;
    const i = +chip.dataset.i;
    if (e.target.dataset.act === "remove") removeFromTray(i);
    if (e.target.dataset.act === "insert") insertMention(trayLabels()[i].label);
  });
  $("#tray").addEventListener("change", e => {
    const i = +e.target.closest(".chip").dataset.i;
    if (e.target.dataset.act === "vaudio") state.tray[i].use_audio = e.target.checked;
    if (e.target.dataset.act === "extend") setExtend(i, e.target.checked);
  });

  const ta = $("#prompt");
  ta.addEventListener("input", () => { renderHighlight(); updateMentionMenu(); markStale(); });
  ta.addEventListener("scroll", () => ($("#editorBackdrop").scrollTop = ta.scrollTop));
  ta.addEventListener("click", updateMentionMenu);
  ta.addEventListener("blur", () => setTimeout(closeMenu, 150));
  ta.addEventListener("keydown", e => {
    if (!menu.open || !menu.items.length) return;
    if (e.key === "ArrowDown" || e.key === "ArrowUp") {
      e.preventDefault();
      menu.index = (menu.index + (e.key === "ArrowDown" ? 1 : -1) + menu.items.length) % menu.items.length;
      updateMentionMenu();
    } else if (e.key === "Enter" || e.key === "Tab") {
      e.preventDefault();
      insertMention(menu.items[menu.index].label, menu.start);
    } else if (e.key === "Escape") closeMenu();
  });
  new ResizeObserver(() => ($("#editorBackdrop").style.height = ta.offsetHeight + "px")).observe(ta);
  $("#mentionMenu").addEventListener("mousedown", e => {
    const mi = e.target.closest(".mi");
    if (mi) { e.preventDefault(); insertMention(menu.items[+mi.dataset.i].label, menu.start); }
  });

  for (const [id, key] of [["#aspectSeg", "aspect"], ["#resSeg", "resolution"], ["#intensitySeg", "intensity"], ["#imgSizeSeg", "imageSize"]])
    $(id).addEventListener("click", e => { if (e.target.dataset.v) { state[key] = e.target.dataset.v; syncSegs(); } });
  $("#seconds").addEventListener("input", e => { $("#secondsVal").textContent = e.target.value; syncToolbarPills(); markStale(); });
  $("#skill").addEventListener("change", () => { syncToolbarPills(); markStale(); });
  $("#btnRewrite").addEventListener("click", () => doRewrite().catch(e => toast(e.message, true)));
  $("#btnGenerate").addEventListener("click", doGenerate);
  $("#btnAgent").addEventListener("click", () => startAgent().catch(e => toast(e.message, true)));
  $("#agentSeconds").addEventListener("input", e => { $("#agentSecondsVal").textContent = e.target.value; syncToolbarPills(); });
  $("#agentPanel").addEventListener("click", e => {
    const run = e.target.closest(".run")?.dataset.id;
    const act = e.target.dataset.ract;
    if (!run || !act) return;
    if (act === "revise") {
      const input = e.target.closest(".run").querySelector(".revise input");
      if (!input.value.trim()) return toast("請輸入修改指示", true);
      api("api/agent/revise", { id: run, message: input.value.trim() }).then(() => { input.value = ""; toast("已送出修改，重新規劃中…"); loadRuns(); })
        .catch(err => toast(err.message, true));
    }
    if (act === "stop") api("api/agent/stop", { id: run }).then(() => toast("將在此段完成後停止")).catch(err => toast(err.message, true));
  });

  // Job filter tabs
  $("#jobFilterTabs")?.addEventListener("click", e => {
    const jf = e.target.dataset.jf;
    if (!jf) return;
    state.jobFilter = jf;
    for (const b of $("#jobFilterTabs").children) b.classList.toggle("active", b.dataset.jf === jf);
    renderJobs();
  });

  $("#jobList").addEventListener("click", async e => {
    const card = e.target.closest(".job");
    const job = state.jobs.find(j => j.id === card?.dataset.id);
    if (!job) return;
    if (e.target.dataset.tab) { state.videoTab[job.id] = e.target.dataset.tab; renderJobs(); return; }
    const act = e.target.dataset.act;
    try {
      if (act === "reuse") reuseJob(job);
      if (act === "copy") { await navigator.clipboard.writeText(job.final_prompt); toast("已複製最終提示詞"); }
      if (act === "tolib") {
        toast("加入素材庫中…");
        const item = await api("api/library/from_job", { id: job.id, folder: state.folder });
        state.library.unshift(item);
        renderLibrary();
        toast("已加入素材庫，可用於延伸或參考 🧵");
      }
      if (act === "tovideo") await imageToVideo(job);
      if (act === "del") { await api("api/jobs/delete", { id: job.id }); loadJobs(); }
    } catch (err) { toast(err.message, true); }
  });

  $("#openSettings").addEventListener("click", openSettings);
  $("#saveSettings").addEventListener("click", e => { e.preventDefault(); saveSettings().then(() => $("#settings").close()).catch(err => toast(err.message, true)); });
}

(async function init() {
  bind();
  setMode("ref");
  document.querySelector(".proj-tab").hidden = !IN_CREATIVEBOX;
  if (IN_CREATIVEBOX && location.hash === "#projects") setTop("projects");
  try {
    await loadConfig();
    await loadLibrary();
  } catch (e) { toast(e.message, true); }
  loadJobs();
  checkComfy();
  setInterval(checkComfy, 30000);
})();

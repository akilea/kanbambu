/* kanbambu — tiny kanban for 3D prints */
const LANES = ["park", "analysed", "printing", "done"];
let BOARD = { projects: [] };
const $ = (s, r = document) => r.querySelector(s);
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const ic = (n, cls = "") => `<svg class="i ${cls}"><use href="#i-${n}"/></svg>`;

const api = async (method, url, body) => {
  const r = await fetch(url, {
    method,
    headers: body instanceof FormData || !body ? {} : { "Content-Type": "application/json" },
    body: body instanceof FormData ? body : body ? JSON.stringify(body) : undefined,
  });
  if (!r.ok) throw new Error(`${r.status} ${url}`);
  return r.json();
};

const fmtTime = (s) => {
  if (!s) return "";
  const h = Math.floor(s / 3600), m = Math.round((s % 3600) / 60);
  return h ? `${h}h${String(m).padStart(2, "0")}` : `${m}m`;
};
const fmtG = (g) => (g >= 1000 ? `${(g / 1000).toFixed(2)}kg` : `${Math.round(g)}g`);
const fmtDate = (d) => (d ? new Date(d.replace(" ", "T") + "Z").toLocaleDateString("fr-CA", { month: "short", day: "numeric" }) : "");
const fileIcon = (k) => ({ stl: "box", "3mf": "file-box", gcode3mf: "file-box", step: "box", image: "image" }[k] || "package");
const isBambu = (f) => f && (f.kind === "3mf" || f.kind === "gcode3mf");

/* Bambu Studio registers bambustudio:// (Windows/Linux) and bambustudioopen:// (macOS). */
function bambuHref(fileUrl) {
  const abs = encodeURIComponent(location.origin + fileUrl);
  return /Mac/i.test(navigator.platform) ? `bambustudioopen://${abs}` : `bambustudio://open?file=${abs}`;
}
const bambuBtn = (f, cls = "ib") =>
  `<a class="${cls}" href="${bambuHref(f.url)}" title="Ouvrir dans Bambu Studio" onclick="event.stopPropagation()">${ic("external-link")}</a>`;

async function load() {
  BOARD = await api("GET", "/api/board");
  render();
}

/* ---------------- render ---------------- */

function render() {
  const board = $("#board");
  board.innerHTML = BOARD.projects.map(projectHtml).join("");
  $("#empty").hidden = BOARD.projects.length > 0;
  renderTotals();
  wire();
}

function renderTotals() {
  const t = {};
  for (const p of BOARD.projects)
    for (const c of p.cards)
      if ((c.lane === "analysed" || c.lane === "printing") && c.analysis)
        for (const f of c.analysis.filaments) {
          const k = f.color.toUpperCase() + f.type;
          t[k] ??= { ...f, g: 0 };
          t[k].g += f.g * (c.plates_left || 0);
        }
  $("#totals").innerHTML = Object.values(t)
    .sort((a, b) => b.g - a.g)
    .map((f) => `<span class="chip" title="${esc(f.profile || f.type)}"><span class="dot" style="background:${esc(f.color)}"></span>${fmtG(f.g)}</span>`)
    .join("");
}

function projectHtml(p) {
  const by = Object.fromEntries(LANES.map((l) => [l, p.cards.filter((c) => c.lane === l)]));
  const ok = p.cards.reduce((a, c) => a + c.ok, 0), ko = p.cards.reduce((a, c) => a + c.ko, 0);
  const bomDone = p.bom.filter((b) => b.done).length;
  const laneIcons = { park: "square-parking", analysed: "scan-search", printing: "printer", done: "flag-triangle-right" };
  return `
  <section class="project ${p.collapsed ? "collapsed" : ""}" data-id="${p.id}">
    <div class="p-head" data-act="toggle">
      <span class="p-grip" title="Déplacer">${ic("grip-vertical")}</span>
      ${ic(p.collapsed ? "chevron-right" : "chevron-down")}
      ${p.image ? `<img class="p-img" src="${p.image}" alt="">` : `<span class="p-img"></span>`}
      <span class="p-name">${esc(p.name)}</span>
      ${p.url ? `<a class="ib" href="${esc(p.url)}" target="_blank" rel="noopener" title="Lien" onclick="event.stopPropagation()">${ic("link")}</a>` : ""}
      ${p.bambu ? bambuBtn(p.bambu) : ""}
      ${p.bom.length ? `<button class="ib" data-act="edit-project" title="BOM">${ic("list-checks")}${bomDone}/${p.bom.length}</button>` : ""}
      <span class="p-sp"></span>
      <span class="p-counts">
        ${p.collapsed ? LANES.map((l) => (by[l].length ? `<span>${ic(laneIcons[l], "sm")} ${by[l].length}</span>` : "")).join("") : ""}
        ${ok ? `<span class="ok">${ic("circle-check", "sm")} ${ok}</span>` : ""}
        ${ko ? `<span class="ko">${ic("circle-x", "sm")} ${ko}</span>` : ""}
      </span>
      <button class="ib" data-act="edit-project" title="Modifier">${ic("pencil")}</button>
    </div>
    <div class="p-body">
      ${LANES.map((l) => `
        <div class="lane-wrap">
          <div class="lane-tag">${ic(laneIcons[l], "sm")} ${l[0].toUpperCase() + l.slice(1)}</div>
          <div class="lane" data-lane="${l}" data-project="${p.id}">${by[l].map(cardHtml).join("")}</div>
          ${l === "park" ? `<button class="add-card" data-act="add-card" title="Nouvelle impression (ou glisse des fichiers)">${ic("plus")}</button>` : ""}
        </div>`).join("")}
    </div>
  </section>`;
}

function cardHtml(c) {
  const a = c.analysis;
  const last = c.attempts[c.attempts.length - 1];
  const failed = c.lane === "done" && last && last.result === "fail";
  const canReprint = c.lane === "done" && last && last.result === "success" && c.plates_left > 0;
  const mult = c.plates_left || c.plates || 1;
  const models = c.files.filter((f) => f.kind !== "image");
  const bambu = [...c.files].reverse().find(isBambu);
  return `
  <div class="card ${failed ? "failed" : ""} ${canReprint ? "partial" : ""}" data-id="${c.id}" data-lane="${c.lane}">
    ${c.preview ? `<img class="thumb" src="${c.preview}" alt="" loading="lazy">` : ""}
    <div class="c-body">
      <div class="c-title">${esc(c.title)}${c.qty > 1 ? `<span class="qty">×${c.qty}</span>` : ""}</div>
      <div class="c-meta">
        ${models.length ? `<span title="${esc(models.map((f) => f.name).join("\n"))}">${ic(fileIcon(models[0].kind), "sm")}${models.length > 1 ? models.length : ""}</span>` : ""}
        ${c.url ? `<a class="ib" href="${esc(c.url)}" target="_blank" rel="noopener" title="Lien" onclick="event.stopPropagation()">${ic("link", "sm")}</a>` : ""}
        ${bambu ? bambuBtn(bambu) : ""}
        ${c.plates > 1 || c.plates_done ? `<span title="Plateaux imprimés / total">${ic("layers", "sm")} ${c.plates_done}/${c.plates}</span>` : ""}
        ${a && a.time_s ? `<span title="Temps restant">${ic("clock", "sm")} ${fmtTime(a.time_s * mult)}</span>` : ""}
        ${a && a.g ? `<span title="Filament restant">${ic("weight", "sm")} ${fmtG(a.g * mult)}</span>` : ""}
        ${a ? a.filaments.map((f) => `<span class="dot" style="background:${esc(f.color)}" title="${esc(f.type)} ${fmtG(f.g)}"></span>`).join("") : ""}
        ${c.pending_change ? `<span class="retry" title="${esc(c.pending_change)}">${ic("rotate-ccw", "sm")}</span>` : ""}
        ${c.ok ? `<span class="ok">${ic("circle-check", "sm")}${c.ok}</span>` : ""}
        ${c.ko ? `<span class="ko">${ic("circle-x", "sm")}${c.ko}</span>` : ""}
        ${canReprint ? `<button class="ib reprint" data-reprint title="Imprimer le plateau suivant">${ic("printer", "sm")}${ic("plus", "sm")}</button>` : ""}
      </div>
    </div>
  </div>`;
}

/* ---------------- interactions ---------------- */

const findProject = (id) => BOARD.projects.find((p) => p.id === +id);
const findCard = (id) => BOARD.projects.flatMap((p) => p.cards).find((c) => c.id === +id);

function wire() {
  const board = $("#board");

  Sortable.create(board, {
    handle: ".p-grip", animation: 150, draggable: ".project", delay: 150, delayOnTouchOnly: true,
    onEnd: () => api("POST", "/api/projects/order", { ids: [...board.children].map((s) => +s.dataset.id) }),
  });

  board.querySelectorAll(".lane").forEach((lane) => {
    Sortable.create(lane, {
      group: "cards", animation: 150, draggable: ".card", delay: 250, delayOnTouchOnly: true,
      onEnd: (e) => onCardDrop(e),
    });
    // Drop files on a lane: onto a card = attach, onto Park = new cards.
    lane.addEventListener("dragover", (e) => { if (hasFiles(e)) { e.preventDefault(); lane.classList.add("drop"); } });
    lane.addEventListener("dragleave", () => lane.classList.remove("drop"));
    lane.addEventListener("drop", (e) => onFileDrop(e, lane));
  });

  board.querySelectorAll(".add-card").forEach((b) => {
    const lane = b.previousElementSibling;
    b.addEventListener("dragover", (e) => { if (hasFiles(e)) { e.preventDefault(); b.classList.add("drop"); } });
    b.addEventListener("drop", (e) => onFileDrop(e, lane));
  });

  board.onclick = (e) => {
    const act = e.target.closest("[data-act]");
    const proj = e.target.closest(".project");
    if (act && proj) {
      e.stopPropagation();
      const p = findProject(proj.dataset.id);
      if (act.dataset.act === "edit-project") return projectModal(p);
      if (act.dataset.act === "add-card") return newCardInput(act, p);
      if (act.dataset.act === "toggle" && !e.target.closest(".p-grip")) {
        p.collapsed = !p.collapsed;
        proj.classList.toggle("collapsed");
        api("PATCH", `/api/projects/${p.id}`, { collapsed: p.collapsed }).then(() => render());
      }
      return;
    }
    const card = e.target.closest(".card");
    if (card && e.target.closest("[data-reprint]")) return reprint(findCard(card.dataset.id));
    if (card) cardModal(findCard(card.dataset.id));
  };
}

const hasFiles = (e) => [...(e.dataTransfer?.types || [])].includes("Files");

async function onFileDrop(e, lane) {
  if (!hasFiles(e)) return;
  e.preventDefault();
  e.stopPropagation();
  lane.classList.remove("drop");
  const files = [...e.dataTransfer.files];
  const cardEl = e.target.closest(".card");
  if (cardEl) {
    for (const f of files) await uploadFile(f, { card_id: cardEl.dataset.id, role: f.type.startsWith("image/") ? "preview" : "attach" });
  } else {
    // One card per model file, named after the file.
    for (const f of files) {
      const title = f.name.replace(/\.gcode\.3mf$|\.[^.]+$/i, "");
      const c = await api("POST", "/api/cards", { project_id: +lane.dataset.project, title });
      await uploadFile(f, { card_id: c.id, role: f.type.startsWith("image/") ? "preview" : "attach" });
    }
  }
  load();
}

function uploadFile(file, fields) {
  const fd = new FormData();
  fd.append("file", file);
  for (const [k, v] of Object.entries(fields)) fd.append(k, v);
  return api("POST", "/api/upload", fd);
}

function onCardDrop(e) {
  const id = +e.item.dataset.id;
  const card = findCard(id);
  const to = e.to, lane = to.dataset.lane, project_id = +to.dataset.project;
  const ids = [...to.children].map((x) => +x.dataset.id);
  const move = (extra = {}) => api("POST", "/api/cards/move", { card_id: id, project_id, lane, ids, ...extra }).then(load);
  if (lane === card.lane) return move();
  if (lane === "done") return doneModal(card, (result, note) => move({ result, note }), load);
  if (card.lane === "done") {
    const last = card.attempts[card.attempts.length - 1];
    if (last && last.result === "success" && card.plates_left > 0) return move(); // next plate, not a retry
    return retryModal(card, (change) => move({ change }), load);
  }
  return move();
}

/* Next plate of a partially printed card: straight back to Printing. */
function reprint(card) {
  const lane = $(`.lane[data-lane="printing"][data-project="${card.project_id}"]`);
  const ids = [...lane.children].map((x) => +x.dataset.id).concat(card.id);
  return api("POST", "/api/cards/move", { card_id: card.id, project_id: card.project_id, lane: "printing", ids }).then(load);
}

function newCardInput(btn, p) {
  const lane = btn.previousElementSibling;
  const inp = document.createElement("input");
  inp.className = "new-title";
  inp.placeholder = "Titre";
  lane.appendChild(inp);
  inp.focus();
  let done = false;
  const commit = async () => {
    if (done) return;
    done = true;
    const t = inp.value.trim();
    inp.remove();
    if (t) { await api("POST", "/api/cards", { project_id: p.id, title: t }); load(); }
  };
  inp.onkeydown = (e) => { if (e.key === "Enter") commit(); if (e.key === "Escape") { done = true; inp.remove(); } };
  inp.onblur = commit;
}

/* ---------------- modals ---------------- */

let onModalClose = null;
function openModal(html, onClose) {
  const m = $("#modal");
  $(".sheet", m).innerHTML = html;
  m.hidden = false;
  onModalClose = onClose || null;
  m.onmousedown = (e) => { if (e.target === m) closeModal(); };
  document.onkeydown = (e) => { if (e.key === "Escape") closeModal(); };
  return $(".sheet", m);
}
function closeModal(silent) {
  $("#modal").hidden = true;
  document.onkeydown = null;
  const cb = onModalClose;
  onModalClose = null;
  if (cb && !silent) cb();
}

/* Two-step delete: first click arms, second click confirms. */
function armDelete(btn, fn) {
  btn.onclick = () => {
    if (btn.classList.contains("armed")) return fn();
    btn.classList.add("armed");
    setTimeout(() => btn.classList.remove("armed"), 2500);
  };
}

function dropZone(el, onFiles) {
  const inp = document.createElement("input");
  inp.type = "file"; inp.multiple = true; inp.hidden = true;
  el.after(inp);
  el.onclick = () => inp.click();
  inp.onchange = () => onFiles([...inp.files]);
  el.ondragover = (e) => { e.preventDefault(); el.classList.add("over"); };
  el.ondragleave = () => el.classList.remove("over");
  el.ondrop = (e) => { e.preventDefault(); el.classList.remove("over"); onFiles([...e.dataTransfer.files]); };
}

function doneModal(card, onSave, onCancel) {
  const s = openModal(`
    <div class="row"><b>${esc(card.title)}</b></div>
    <div class="choice">
      <button class="s" data-r="success" title="Succès">${ic("circle-check", "lg")}</button>
      <button class="f" data-r="fail" title="Échec">${ic("circle-x", "lg")}</button>
    </div>
    <textarea id="note"></textarea>
    <div class="foot"><span></span><button class="btn primary" id="save" disabled>${ic("save")}</button></div>`, onCancel);
  let result = null;
  const note = $("#note", s), save = $("#save", s);
  const upd = () => { save.disabled = !result || (result === "fail" && !note.value.trim()); };
  s.querySelectorAll(".choice button").forEach((b) => (b.onclick = () => {
    result = b.dataset.r;
    s.querySelectorAll(".choice button").forEach((x) => x.classList.toggle("sel", x === b));
    note.placeholder = result === "success" ? "Amélioration possible ? (optionnel)" : "Pourquoi ?";
    note.focus();
    upd();
  }));
  note.oninput = upd;
  save.onclick = () => { closeModal(true); onSave(result, note.value.trim()); };
}

function retryModal(card, onSave, onCancel) {
  const last = card.attempts[card.attempts.length - 1];
  const s = openModal(`
    <div class="row">${ic("rotate-ccw")}<b>${esc(card.title)}</b></div>
    ${last ? `<div class="li attempt">${ic(last.result === "success" ? "circle-check" : "circle-x", last.result === "success" ? "ok" : "ko")}<span class="name">${esc(last.note) || "—"}</span></div>` : ""}
    <textarea id="chg" placeholder="Qu'est-ce qui change ?"></textarea>
    <div class="foot"><span></span><button class="btn primary" id="save" disabled>${ic("save")}</button></div>`, onCancel);
  const chg = $("#chg", s), save = $("#save", s);
  chg.focus();
  chg.oninput = () => (save.disabled = !chg.value.trim());
  save.onclick = () => { closeModal(true); onSave(chg.value.trim()); };
}

function cardModal(c) {
  const a = c.analysis;
  const s = openModal(`
    <div class="row" style="align-items:flex-start">
      <div class="big-img" id="prev" title="Image">${c.preview ? `<img src="${c.preview}" style="width:100%;height:100%;object-fit:contain;border-radius:10px">` : ic("image", "lg")}</div>
      <div class="sec" style="flex:1">
        <div class="row"><input class="title" id="title" value="${esc(c.title)}"></div>
        <div class="row">
          <span title="Copies à imprimer">${ic("package")}</span><input class="num" id="qty" type="number" min="1" value="${c.qty}">
          <span title="Copies par plateau">${ic("layers")}</span><input class="num" id="per" type="number" min="1" value="${c.per_plate}">
          <span class="sub" title="Plateaux imprimés / total">= ${c.plates_done}/${c.plates} ${ic("layers", "sm")}</span>
          ${c.lane === "done" && c.plates_left > 0 && c.attempts.at(-1)?.result === "success" ? `<button class="btn" id="reprint" title="Imprimer le plateau suivant">${ic("printer")}${ic("plus", "sm")}</button>` : ""}
        </div>
        <div class="row">${ic("link")}<input id="url" type="url" placeholder="https://" value="${esc(c.url)}">
          ${c.url ? `<a class="ib" href="${esc(c.url)}" target="_blank" rel="noopener">${ic("external-link")}</a>` : ""}</div>
      </div>
    </div>

    <div class="sec">
      <div class="sec-h">${ic("package", "sm")} Fichiers</div>
      <div class="list">${c.files.map((f) => `
        <div class="li">${ic(fileIcon(f.kind))}<span class="name" title="${esc(f.name)}">${esc(f.name)}</span>
          ${isBambu(f) ? bambuBtn(f) : ""}
          <a class="ib" href="${f.url}?dl=1" title="Télécharger">${ic("download")}</a>
          <button class="ib" data-del-file="${f.id}" title="Supprimer">${ic("trash-2")}</button></div>`).join("")}</div>
      <div class="drop-zone" id="dz" title="STL, 3MF, .gcode.3mf, image">${ic("upload")}</div>
    </div>

    ${a ? `
    <div class="sec">
      <div class="sec-h">${ic("scan-search", "sm")} Slicing
        <span class="stats" style="margin-left:auto;text-transform:none">
          <span>${ic("clock", "sm")} ${fmtTime(a.time_s)}</span><span>${ic("weight", "sm")} ${fmtG(a.g)}</span>
          ${a.plates.length > 1 ? `<span>${ic("layers", "sm")} ${a.plates.length}</span>` : ""}</span></div>
      <div class="list">${a.filaments.map((f) => `
        <div class="li"><span class="dot" style="background:${esc(f.color)}"></span>
          <span class="name">${esc(f.profile || f.type)}</span>
          <span class="sub">${f.g.toFixed(1)} g · ${f.m.toFixed(1)} m</span></div>`).join("")}</div>
    </div>` : ""}

    <div class="sec">
      <div class="sec-h">${ic("message-square", "sm")} Notes</div>
      <textarea id="notes">${esc(c.notes)}</textarea>
    </div>

    ${c.attempts.length || c.pending_change ? `
    <div class="sec">
      <div class="sec-h">${ic("rotate-ccw", "sm")} Historique
        <span style="margin-left:auto" class="stats"><span class="ok">${ic("circle-check", "sm")} ${c.ok}</span><span class="ko">${ic("circle-x", "sm")} ${c.ko}</span></span></div>
      <div class="list">
        ${c.attempts.map((t) => `
          <div class="li attempt" style="align-items:flex-start">
            ${ic(t.result === "success" ? "circle-check" : "circle-x", t.result === "success" ? "ok" : "ko")}
            <div class="name" style="white-space:normal">
              ${t.change ? `<div class="change">${ic("rotate-ccw", "sm")} ${esc(t.change)}</div>` : ""}
              <div>${t.result === "success" && t.note ? ic("lightbulb", "sm") + " " : ""}${esc(t.note) || ""}</div>
            </div>
            <span class="sub">${fmtDate(t.created_at)}</span>
            <button class="ib" data-del-att="${t.id}" title="Supprimer">${ic("x", "sm")}</button>
          </div>`).join("")}
        ${c.pending_change ? `<div class="li attempt"><span class="change">${ic("rotate-ccw", "sm")} ${esc(c.pending_change)}</span></div>` : ""}
      </div>
    </div>` : ""}

    <div class="foot">
      <button class="btn danger" id="del" title="Supprimer la carte">${ic("trash-2")}</button>
      <button class="btn primary" id="close" title="Fermer">${ic("circle-check")}</button>
    </div>`, load);

  const patch = (body) => api("PATCH", `/api/cards/${c.id}`, body);
  $("#title", s).onchange = (e) => patch({ title: e.target.value.trim() || c.title });
  const refreshCard = async () => { BOARD = await api("GET", "/api/board"); cardModal(findCard(c.id)); };
  $("#qty", s).onchange = (e) => patch({ qty: Math.max(1, +e.target.value || 1) }).then(refreshCard);
  $("#per", s).onchange = (e) => patch({ per_plate: Math.max(1, +e.target.value || 1) }).then(refreshCard);
  if ($("#reprint", s)) $("#reprint", s).onclick = () => { closeModal(true); reprint(c); };
  $("#url", s).onchange = (e) => patch({ url: e.target.value.trim() });
  $("#notes", s).onchange = (e) => patch({ notes: e.target.value });
  $("#close", s).onclick = () => closeModal();
  armDelete($("#del", s), async () => { await api("DELETE", `/api/cards/${c.id}`); closeModal(); });

  const refresh = async () => { BOARD = await api("GET", "/api/board"); cardModal(findCard(c.id)); };
  dropZone($("#dz", s), async (files) => {
    for (const f of files) await uploadFile(f, { card_id: c.id, role: f.type.startsWith("image/") ? "preview" : "attach" });
    refresh();
  });
  dropZone($("#prev", s), async (files) => {
    if (files[0]) { await uploadFile(files[0], { card_id: c.id, role: "preview" }); refresh(); }
  });
  s.querySelectorAll("[data-del-file]").forEach((b) => armDelete(b, async () => { await api("DELETE", `/api/files/${b.dataset.delFile}`); refresh(); }));
  s.querySelectorAll("[data-del-att]").forEach((b) => armDelete(b, async () => { await api("DELETE", `/api/attempts/${b.dataset.delAtt}`); refresh(); }));
}

function projectModal(p) {
  const s = openModal(`
    <div class="row" style="align-items:flex-start">
      <div class="big-img" id="pimg" title="Image">${p.image ? `<img src="${p.image}" style="width:100%;height:100%;object-fit:cover;border-radius:10px">` : ic("image", "lg")}</div>
      <div class="sec" style="flex:1">
        <input class="title" id="name" value="${esc(p.name)}" placeholder="Projet">
        <div class="row">${ic("link")}<input id="url" type="url" placeholder="https://" value="${esc(p.url)}"></div>
        <div class="row">${ic("file-box")}
          ${p.bambu ? `<span class="name" style="flex:1;overflow:hidden;text-overflow:ellipsis">${esc(p.bambu.name)}</span>
            ${bambuBtn(p.bambu)}<a class="ib" href="${p.bambu.url}?dl=1" title="Télécharger">${ic("download")}</a>
            <button class="ib" id="rmBambu" title="Retirer">${ic("trash-2")}</button>`
          : `<div class="drop-zone" id="bz" style="flex:1;padding:6px" title="Projet Bambu Studio (.3mf)">${ic("upload")} .3mf</div>`}
        </div>
      </div>
    </div>
    <div class="sec">
      <div class="sec-h">${ic("list-checks", "sm")} BOM</div>
      <div class="list" id="bom"></div>
      <button class="add-card" id="addBom" title="Ajouter">${ic("plus")}</button>
    </div>
    <div class="foot">
      <button class="btn danger" id="del" title="Supprimer le projet">${ic("trash-2")}</button>
      <button class="btn primary" id="close" title="Fermer">${ic("circle-check")}</button>
    </div>`, load);

  const patch = (body) => api("PATCH", `/api/projects/${p.id}`, body);
  const bom = p.bom.map((b) => ({ ...b }));
  const saveBom = () => patch({ bom: bom.filter((b) => b.name.trim()) });
  const drawBom = () => {
    $("#bom", s).innerHTML = bom.map((b, i) => `
      <div class="li ${b.done ? "bom-done" : ""}">
        <button class="ib" data-i="${i}" data-k="done">${ic(b.done ? "square-check" : "square")}</button>
        <input class="num" type="number" min="1" data-i="${i}" data-k="qty" value="${b.qty || 1}">
        <input class="name" data-i="${i}" data-k="name" value="${esc(b.name)}" placeholder="Vis M3×8, aimant 6×2…">
        <button class="ib" data-i="${i}" data-k="del">${ic("x", "sm")}</button>
      </div>`).join("");
    $("#bom", s).querySelectorAll("[data-k]").forEach((el) => {
      const i = +el.dataset.i, k = el.dataset.k;
      if (k === "done") el.onclick = () => { bom[i].done = !bom[i].done; saveBom(); drawBom(); };
      else if (k === "del") el.onclick = () => { bom.splice(i, 1); saveBom(); drawBom(); };
      else el.onchange = () => { bom[i][k] = k === "qty" ? Math.max(1, +el.value || 1) : el.value; saveBom(); };
    });
  };
  drawBom();
  $("#addBom", s).onclick = () => { bom.push({ qty: 1, name: "", done: false }); drawBom(); $("#bom", s).querySelector(".li:last-child input.name").focus(); };

  $("#name", s).onchange = (e) => patch({ name: e.target.value.trim() || p.name });
  $("#url", s).onchange = (e) => patch({ url: e.target.value.trim() });
  $("#close", s).onclick = () => closeModal();
  armDelete($("#del", s), async () => { await api("DELETE", `/api/projects/${p.id}`); closeModal(); });

  const refresh = async () => { BOARD = await api("GET", "/api/board"); projectModal(findProject(p.id)); };
  dropZone($("#pimg", s), async (files) => { if (files[0]) { await uploadFile(files[0], { project_id: p.id, role: "image" }); refresh(); } });
  if ($("#bz", s)) dropZone($("#bz", s), async (files) => { if (files[0]) { await uploadFile(files[0], { project_id: p.id, role: "bambu" }); refresh(); } });
  if ($("#rmBambu", s)) armDelete($("#rmBambu", s), async () => { await api("DELETE", `/api/files/${p.bambu.id}`); refresh(); });
}

/* ---------------- boot ---------------- */

$("#addProject").onclick = async () => {
  const p = await api("POST", "/api/projects", { name: "Nouveau projet" });
  await load();
  projectModal(findProject(p.id));
  $("#name").select();
};

fetch("icons.svg").then((r) => r.text()).then((svg) => {
  document.body.insertAdjacentHTML("afterbegin", svg);
  load();
});

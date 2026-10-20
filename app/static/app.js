"use strict";
const $ = (id) => document.getElementById(id);
let tables = [], version = null, lastRefresh = 0, needsReview = true, busy = false;
let online = false, refreshing = false;
let actor = sessionStorage.getItem("operatorName") || "", activeId = null;
const level = (value) => value === "platea" ? "Platea" : `Nivel ${value}`;
const plain = (tag, text) => { const node = document.createElement(tag); node.textContent = text; return node; };
const selected = () => tables.find((table) => table.id === activeId);

function ready() { return Boolean(actor) && online && !needsReview && Date.now() - lastRefresh < 15000 && !busy; }
function controls() {
  const table = selected(), canChange = ready() && table;
  $("plus").disabled = !canChange || table.remaining === 0;
  $("minus").disabled = !canChange || table.sold === 0;
  $("close-table").disabled = busy;
  $("connection").textContent = !online ? "Sin conexión o datos disponibles. No se pueden enviar cambios." :
    needsReview || Date.now() - lastRefresh >= 15000 ? "Datos desactualizados. Actualice y revise antes de enviar." : "Datos actualizados.";
}

function choose(id) {
  if (!actor) { $("name-dialog").showModal(); return; }
  const table = tables.find((item) => item.id === Number(id));
  if (!table) return;
  activeId = table.id; version = table.version;
  $("table-dialog-title").textContent = table.label;
  $("table-level").textContent = level(table.level);
  $("message").textContent = "";
  updateCounter(); $("table-dialog").showModal();
}

function updateCounter() {
  const table = selected();
  if (!table) return;
  $("table-count").textContent = `${table.sold}/${table.capacity}`;
  $("table-dialog-title").textContent = table.label;
  $("table-level").textContent = level(table.level);
  controls();
}

function render() {
  const ns = "http://www.w3.org/2000/svg";
  $("map-tables").replaceChildren(...tables.map((table) => {
    const state = table.remaining === 0 ? "full" : table.sold === 0 ? "empty" : "partial";
    const stateText = state === "full" ? "Completa" : state === "partial" ? "Parcialmente ocupada" : "Vacía";
    const link = document.createElementNS(ns, "a"); link.setAttribute("href", "#map");
    link.setAttribute("role", "button"); link.setAttribute("tabindex", "0");
    link.setAttribute("aria-label", `${table.label}, ${level(table.level)}, ${stateText}, ${table.sold} de ${table.capacity} sillas ocupadas, ${table.remaining} disponibles`);
    link.classList.add("map-table", state);
    const rect = document.createElementNS(ns, "rect");
    Object.entries({x: table.x - 29, y: table.y - 24, width: 58, height: 48, rx: 5})
      .forEach(([key, value]) => rect.setAttribute(key, value)); link.append(rect);
     [table.label, `${table.sold}/${table.capacity}`].forEach((value, index) => {
       const text = document.createElementNS(ns, "text"); text.setAttribute("x", table.x);
       text.setAttribute("y", table.y - 3 + 19 * index); text.setAttribute("text-anchor", "middle");
       if (index === 0) text.classList.add("table-number");
       text.textContent = value; link.append(text);
     });
    link.addEventListener("click", (event) => { event.preventDefault(); choose(table.id); });
    link.addEventListener("keydown", (event) => { if (event.key === "Enter" || event.key === " ") { event.preventDefault(); choose(table.id); } });
    return link;
  }));
  const sold = tables.reduce((sum, table) => sum + table.sold, 0);
  const remaining = tables.reduce((sum, table) => sum + table.remaining, 0);
  const full = tables.filter((table) => table.remaining === 0).length;
  $("totals").replaceChildren(...[["Entradas registradas", sold], ["Lugares disponibles", remaining], ["Mesas completas", full]].map(([label, value]) => {
    const item = document.createElement("div"); item.className = "metric";
    item.append(plain("span", label), plain("strong", String(value))); return item;
  }));
  const progress = $("capacity-progress").parentElement;
  progress.setAttribute("aria-valuenow", String(sold));
  progress.setAttribute("aria-valuetext", `${sold} de 300 lugares registrados`);
  $("capacity-progress").style.width = `${sold / 300 * 100}%`;
  updateCounter();
}

async function read(url, options = {}) {
  const response = await fetch(url, {...options, cache: "no-store", signal: AbortSignal.timeout(7000)});
  const data = await response.json();
  if (!response.ok) throw Object.assign(new Error(data.error || "No se pudo completar la solicitud."), {status: response.status});
  return data;
}

async function refresh(review = false) {
  if (refreshing || busy) return;
  refreshing = true;
  try {
    const data = await read("/api/tables"), current = data.tables.find((table) => table.id === activeId);
    const changed = Boolean(current && version !== null && current.version !== version);
    tables = data.tables; online = true; lastRefresh = Date.now();
    if (changed && activeId !== null) {
      version = current.version; needsReview = false;
      $("message").textContent = `Otro vendedor actualizó esta mesa. Revise el nuevo conteo ${current.sold}/${current.capacity} antes de realizar otro cambio.`;
    }
    if (review) { needsReview = false; version = current?.version ?? version; }
    render();
  } catch (_error) { online = false; needsReview = true; }
  finally { refreshing = false; controls(); }
}

async function change(kind) {
  const table = selected();
  if (!table || busy || !ready() || (kind === "sales" && !table.remaining) || (kind === "corrections" && !table.sold)) return;
  const previousVersion = version;
  const body = {expected_version: previousVersion, actor,
    reason: kind === "sales" ? "Incremento de silla desde el plano" : "Liberación de silla desde el plano"};
  body[kind === "sales" ? "quantity" : "count"] = kind === "sales" ? 1 : table.sold - 1;
  busy = true; controls();
  try {
    await read(`/api/tables/${table.id}/${kind}`, {method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify(body)});
    $("message").textContent = "Cambio guardado y registrado en el historial.";
    needsReview = false;
    version = null;
  } catch (error) {
    needsReview = true;
    $("message").textContent = error.status === 409 ? "Otro vendedor actualizó esta mesa. Se actualizarán el conteo y el historial; revise los datos antes de realizar otro cambio." :
      error.status ? error.message : "Respuesta no confirmada. Se consultarán el conteo y el historial para verificar el resultado; no se reenvía automáticamente.";
  } finally {
    busy = false;
    await refresh(true);
    const latest = selected(); if (latest) version = latest.version;
    updateCounter();
  }
}

$("name-form").addEventListener("submit", (event) => {
  event.preventDefault();
  const value = $("actor").value.trim();
  if (!value) { $("name-error").textContent = "Ingrese un nombre para continuar."; return; }
  actor = value; sessionStorage.setItem("operatorName", actor); $("name-dialog").close();
});
$("refresh").addEventListener("click", () => refresh(true));
$("close-table").addEventListener("click", () => $("table-dialog").close());
$("table-dialog").addEventListener("cancel", (event) => { if (busy) event.preventDefault(); });
$("plus").addEventListener("click", () => change("sales"));
$("minus").addEventListener("click", () => change("corrections"));
window.addEventListener("offline", () => { online = false; needsReview = true; controls(); });
window.addEventListener("online", () => refresh(true));
setInterval(refresh, 5000);
setInterval(controls, 1000);
if (actor) { $("actor").value = actor; $("name-dialog").close(); } else $("name-dialog").showModal();
refresh(true);

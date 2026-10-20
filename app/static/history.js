"use strict";
const list = document.getElementById("history");
const status = document.getElementById("history-status");
const more = document.getElementById("more-history");
let beforeId = null, loading = false;

async function load(reset = false) {
  if (loading) return;
  loading = true;
  if (reset) { beforeId = null; list.replaceChildren(); more.hidden = true; }
  try {
    const query = new URLSearchParams({limit: "100"});
    if (beforeId !== null) query.set("before_id", String(beforeId));
    const response = await fetch(`/api/history?${query}`, {cache: "no-store", signal: AbortSignal.timeout(7000)});
    const data = await response.json();
    if (!response.ok) throw new Error(data.error || "No se pudo completar la solicitud.");
    data.events.forEach((event) => {
      const item = document.createElement("li");
      item.textContent = `N.${event.table_id} · ${event.previous_count} → ${event.new_count} · ${event.actor} · ${event.reason} · ${event.created_at}`;
      list.append(item);
    });
    beforeId = data.next_before_id;
    more.hidden = !data.has_more;
    status.textContent = data.events.length ? "Historial actualizado. El cambio más reciente aparece primero." : "No hay cambios registrados.";
  } catch (_error) { status.textContent = "No se pudo actualizar el historial."; }
  finally { loading = false; }
}

document.getElementById("refresh-history").addEventListener("click", () => load(true));
more.addEventListener("click", () => load());
load(true);

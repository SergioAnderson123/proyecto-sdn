// Panel de la red (v1, solo lectura). Todo lo que viene de la red (usuarios, detalles de eventos...) se escribe con textContent:
// nunca se construye HTML con datos ajenos (un usuario del portal puede escribir cualquier cosa).
"use strict";
const $ = (id) => document.getElementById(id);
let vista = "resumen";
let temporizador = null;

function el(tag, texto, clase) {
  const e = document.createElement(tag);
  if (texto !== undefined && texto !== null) e.textContent = String(texto);
  if (clase) e.className = clase;
  return e;
}
function vaciar(nodo) { while (nodo.firstChild) nodo.removeChild(nodo.firstChild); }
function fila(tbody, celdas) {
  const tr = document.createElement("tr");
  for (const c of celdas) {
    const td = document.createElement("td");
    if (c instanceof Node) td.appendChild(c); else td.textContent = c === null || c === undefined || c === "" ? "—" : String(c);
    tr.appendChild(td);
  }
  tbody.appendChild(tr);
  return tr;
}
function hora(t) {
  if (!t) return "—";
  const d = new Date(t * 1000);
  return d.toLocaleDateString() + " " + d.toLocaleTimeString();
}
function duracion(s) {
  if (s === null || s === undefined) return "no vence";
  if (s < 0) return "vencida";
  if (s < 90) return s + " s";
  if (s < 5400) return Math.round(s / 60) + " min";
  return (s / 3600).toFixed(1) + " h";
}
async function api(ruta) {
  const r = await fetch(ruta, { credentials: "same-origin", cache: "no-store" });
  if (r.status === 401) { mostrarEntrada(); throw new Error("no autorizado"); }
  if (!r.ok) throw new Error("HTTP " + r.status);
  return r.json();
}
function mostrarEntrada() { $("entrar").hidden = false; document.querySelector("main").hidden = true; }

// ---------- pantallas ----------
function pintarResumen(d) {
  const r = d.resumen;
  const cont = $("tarjetas");
  vaciar(cont);
  if (!r) { cont.appendChild(el("p", "Esperando la primera lectura…", "ayuda")); return; }
  const t = (n, txt, clase) => { const c = el("div", null, "tarjeta"); c.appendChild(el("div", n, "n " + (clase || ""))); c.appendChild(el("div", txt, "t")); cont.appendChild(c); };
  t(r.switches.disponibles + " / " + r.switches.total, "switches disponibles", r.switches.disponibles === r.switches.total ? "ok" : "alto");
  t(r.sesiones.total, "sesiones activas");
  const nodosOk = Object.values(r.nodos_onos).filter((n) => n.responde).length;
  t(nodosOk + " / " + Object.keys(r.nodos_onos).length, "nodos de ONOS responden", nodosOk === Object.keys(r.nodos_onos).length ? "ok" : "alto");
  t(r.mapper_ok === true ? "listo" : r.mapper_ok === false ? "no listo" : "sin dato", "Policy Mapper", r.mapper_ok === true ? "ok" : r.mapper_ok === false ? "alto" : "medio");
  t(r.avisos.length, "avisos activos", r.avisos.length ? "alto" : "ok");
  const ul = $("avisos");
  vaciar(ul);
  if (!r.avisos.length) ul.appendChild(el("li", "Sin avisos.", "sinavisos"));
  for (const a of r.avisos) { const li = el("li", null, a.gravedad); li.appendChild(el("strong", a.codigo + " ")); li.appendChild(document.createTextNode(a.texto)); ul.appendChild(li); }
  const tn = $("t-nodos").tBodies[0];
  vaciar(tn);
  for (const [nodo, n] of Object.entries(r.nodos_onos)) fila(tn, [nodo, n.responde ? "sí" : "NO", n.responde ? n.ven + " de " + n.total : "—"]);
  const tv = $("t-vig").tBodies[0];
  vaciar(tv);
  for (const [nombre, v] of Object.entries(r.vigilantes)) {
    fila(tv, [nombre, v.rol, v.responde ? (v.ok ? "sin cambios" : (v.motivo || v.estado)) : "no responde", v.edad_ciclo_s === null || v.edad_ciclo_s === undefined ? "—" : "hace " + v.edad_ciclo_s + " s", v.activo === undefined ? "—" : (v.activo ? "sí" : "no (respaldo)")]);
  }
  const uc = $("caidas");
  vaciar(uc);
  if (!d.caidas.length) uc.appendChild(el("li", "Ninguna."));
  for (const c of d.caidas) uc.appendChild(el("li", c));
}

function pintarSesiones(d) {
  const tb = $("t-ses").tBodies[0];
  vaciar(tb);
  $("n-ses").textContent = "(" + d.sesiones.length + ")";
  for (const s of d.sesiones) fila(tb, [s.usuario, s.rol, s.vlan, el("span", s.mac, "mono"), (s.dpid || "—") + " / " + (s.puerto || "—"), s.metodo, duracion(s.expira_en_s), s.base]);
  if (!d.sesiones.length) fila(tb, ["No hay sesiones", "", "", "", "", "", "", ""]);
}

async function pintarEventos() {
  const p = new URLSearchParams();
  for (const [campo, id] of [["tipo", "f-tipo"], ["usuario", "f-usuario"], ["mac", "f-mac"], ["texto", "f-texto"], ["limite", "f-limite"]]) if ($(id).value) p.set(campo, $(id).value);
  const d = await api("/api/eventos?" + p.toString());
  const sel = $("f-tipo");
  if (sel.options.length === 1) for (const t of d.tipos) { const o = el("option", t); o.value = t; sel.appendChild(o); }
  const tb = $("t-ev").tBodies[0];
  vaciar(tb);
  $("n-ev").textContent = "(" + d.eventos.length + " mostrados de " + d.total_guardados + " guardados)";
  for (const e of d.eventos) fila(tb, [hora(e.t), el("span", e.tipo, "tag " + e.tipo), e.usuario, el("span", e.mac, "mono"), e.rol, (e.dpid || "—") + " / " + (e.puerto || "—"), e.detalle]);
}

function pintarVistas(d) {
  const v = d.vistas;
  const th = $("t-vistas").tHead, tb = $("t-vistas").tBodies[0];
  vaciar(th); vaciar(tb);
  if (!v) return;
  const nodos = Object.keys(v.nodos);
  const cab = document.createElement("tr");
  for (const h of ["Switch"].concat(nodos.map((n) => n + (v.nodos[n].responde ? "" : " (no responde)"))).concat(["Estado"])) cab.appendChild(el("th", h));
  th.appendChild(cab);
  for (const s of v.switches) {
    const celdas = [el("span", s.id, "mono")];
    for (const n of nodos) {
      const x = s.por_nodo[n];
      celdas.push(!x ? "—" : (x.disponible === null ? "no lo conoce" : (x.disponible ? "disponible" : "NO disponible") + (x.rol ? " · " + x.rol : "")));
    }
    celdas.push(s.desigual ? "VISTA DESIGUAL" : "igual");
    const tr = fila(tb, celdas);
    if (s.desigual) tr.className = "desigual";
  }
  if (!v.switches.length) fila(tb, ["Sin datos de ONOS", "", "", "", ""]);
}

// ---------- ciclo ----------
async function refrescar() {
  try {
    if (vista === "resumen") pintarResumen(await api("/api/resumen"));
    else if (vista === "sesiones") pintarSesiones(await api("/api/sesiones"));
    else if (vista === "vistas") pintarVistas(await api("/api/vistas"));
    else if (vista === "eventos") await pintarEventos();
    $("estado").textContent = "actualizado " + new Date().toLocaleTimeString();
  } catch (e) {
    $("estado").textContent = "error: " + e.message;
  }
}
function cambiar(v) {
  vista = v;
  for (const b of document.querySelectorAll("#pestanas button")) b.classList.toggle("activa", b.dataset.v === v);
  for (const s of document.querySelectorAll("main > section")) s.hidden = s.id !== "v-" + v;
  refrescar();
}
for (const b of document.querySelectorAll("#pestanas button")) b.addEventListener("click", () => cambiar(b.dataset.v));
$("f-ev").addEventListener("submit", (ev) => { ev.preventDefault(); refrescar(); });
$("f-entrar").addEventListener("submit", async (ev) => {
  ev.preventDefault();
  const r = await fetch("/entrar", { method: "POST", headers: { "Content-Type": "application/x-www-form-urlencoded" }, body: "token=" + encodeURIComponent($("token").value), credentials: "same-origin" });
  if (r.ok) { $("entrar").hidden = true; document.querySelector("main").hidden = false; $("token").value = ""; $("e-entrar").textContent = ""; refrescar(); }
  else $("e-entrar").textContent = "Token incorrecto";
});
refrescar();
temporizador = setInterval(() => { if (vista !== "eventos") refrescar(); }, 5000);   // los eventos se actualizan al filtrar, para no perder lo que se está leyendo

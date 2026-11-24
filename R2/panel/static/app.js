// Panel de la red (v1, solo lectura). Todo lo que viene de la red (usuarios, detalles de eventos...) se escribe con textContent:
// nunca se construye HTML con datos ajenos (un usuario del portal puede escribir cualquier cosa).
"use strict";
const $ = (id) => document.getElementById(id);
let vista = "resumen";
let quien = "";
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
  if (r.status === 403) {
    let motivo = "";
    try { motivo = (await r.json()).motivo || ""; } catch (e) { /* sin cuerpo */ }
    $("motivo-sinacceso").textContent = motivo ? "Motivo: " + motivo : "";
    $("sinacceso").hidden = false;
    document.querySelector("main").hidden = true;
    throw new Error("sin acceso");
  }
  if (!r.ok) throw new Error("HTTP " + r.status);
  $("sinacceso").hidden = true;
  document.querySelector("main").hidden = false;
  return r.json();
}

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

// ---------- vigilante: topología (SVG) e historial ----------
const SVGNS = "http://www.w3.org/2000/svg";
function s(tag, attrs, texto) {
  const e = document.createElementNS(SVGNS, tag);
  for (const [k, v] of Object.entries(attrs || {})) e.setAttribute(k, String(v));
  if (texto !== undefined) e.textContent = String(texto);
  return e;
}
const ETIQUETAS = { enlace_caido: "cable caído", enlace_restaurado: "cable restaurado", switch_caido: "switch caído", switch_restaurado: "switch restaurado", cambio_detectado: "cambio detectado",
  aplicado: "recálculo aplicado", alta_rapida: "alta rápida", red_partida: "red partida", red_conexa: "red conexa" };
const MALOS = new Set(["enlace_caido", "switch_caido", "red_partida"]);

function dibujarTopologia(top) {
  const cont = $("topologia");
  vaciar(cont);
  if (!top) { cont.appendChild(el("p", "El vigilante aún no publicó la topología.", "ayuda")); return; }
  const ancho = 720, alto_fila = 120, margen = 60;
  const maxNivel = Math.max(0, ...top.switches.map((x) => (x.nivel === null ? 0 : x.nivel)));
  const filas = new Map();
  for (const sw of top.switches) {
    const nivel = sw.nivel === null ? maxNivel + 1 : sw.nivel;      // los que no llegan a la raíz van en una fila aparte, abajo
    if (!filas.has(nivel)) filas.set(nivel, []);
    filas.get(nivel).push(sw);
  }
  const pos = {};
  for (const [nivel, lista] of [...filas.entries()].sort((a, b) => a[0] - b[0])) {
    lista.sort((a, b) => a.nombre.localeCompare(b.nombre));
    lista.forEach((sw, k) => { pos[sw.id] = { x: (ancho * (k + 1)) / (lista.length + 1), y: margen + nivel * alto_fila }; });
  }
  const alto = margen * 2 + (Math.max(...filas.keys())) * alto_fila;
  const svg = s("svg", { viewBox: "0 0 " + ancho + " " + alto, class: "svg-topologia", role: "img", "aria-label": "Topología de los switches" });
  // Un cable que pasaría por encima de otro switch se curva hasta rodearlo (si no, parece que une a ese switch).
  const bez = (a, c, b, t) => ({ x: (1 - t) * (1 - t) * a.x + 2 * (1 - t) * t * c.x + t * t * b.x, y: (1 - t) * (1 - t) * a.y + 2 * (1 - t) * t * c.y + t * t * b.y });
  const control = (a, b, k) => { const dx = b.x - a.x, dy = b.y - a.y, l = Math.hypot(dx, dy) || 1; return { x: (a.x + b.x) / 2 - (dy / l) * l * k, y: (a.y + b.y) / 2 + (dx / l) * l * k }; };
  const choca = (a, b, c, ids) => {
    for (let t = 0.08; t <= 0.92; t += 0.04) {
      const p = bez(a, c, b, t);
      for (const [id, q] of Object.entries(pos)) if (!ids.includes(id) && Math.abs(p.x - q.x) < 58 && Math.abs(p.y - q.y) < 28) return true;
    }
    return false;
  };
  for (const e of top.enlaces) {
    const a = pos[e.a], b = pos[e.b];
    if (!a || !b) continue;
    let c = control(a, b, 0);
    for (const k of [0, 0.25, -0.25, 0.45, -0.45]) { c = control(a, b, k); if (!choca(a, b, c, [e.a, e.b])) break; }
    svg.appendChild(s("path", { d: "M " + a.x + " " + a.y + " Q " + c.x + " " + c.y + " " + b.x + " " + b.y, class: "enlace " + e.estado }));
    const pa = bez(a, c, b, 0.2), pb = bez(a, c, b, 0.8);
    svg.appendChild(s("text", { x: pa.x, y: pa.y - 5, class: "puerto" }, e.a_puerto === null ? "?" : e.a_puerto));
    svg.appendChild(s("text", { x: pb.x, y: pb.y - 5, class: "puerto" }, e.b_puerto === null ? "?" : e.b_puerto));
  }
  const aislados = new Set(top.aislados.map((x) => x.id));
  for (const sw of top.switches) {
    const p = pos[sw.id];
    const g = s("g", { class: "sw" + (sw.es_raiz ? " raiz" : "") + (sw.disponible ? "" : " nodisp") + (aislados.has(sw.id) ? " aislado" : "") });
    g.appendChild(s("rect", { x: p.x - 48, y: p.y - 20, width: 96, height: 40, rx: 8 }));
    g.appendChild(s("text", { x: p.x, y: p.y + 5, "text-anchor": "middle", class: "nombre" }, sw.nombre));
    if (sw.es_raiz) g.appendChild(s("text", { x: p.x, y: p.y - 26, "text-anchor": "middle", class: "etiqueta" }, "raíz"));
    if (!sw.disponible) g.appendChild(s("text", { x: p.x, y: p.y + 36, "text-anchor": "middle", class: "etiqueta mala" }, "no disponible"));
    svg.appendChild(g);
  }
  cont.appendChild(svg);
}

function pintarVigilante(d) {
  const v = d.vigilante;
  const info = $("vig-info");
  const tb = $("t-hist").tBodies[0];
  vaciar(tb);
  if (!v || !v.estado) {
    info.textContent = v ? "(" + v.nombre + ": sin /estado; ¿versión anterior del vigilante?)" : "(ningún vigilante responde)";
    dibujarTopologia(null);
    return;
  }
  const e = v.estado;
  info.textContent = "(" + v.nombre + " · " + e.rol + (e.activo ? " · activo" : " · en espera") + " · estado: " + e.estado + " · " + e.modo + (e.aplica ? " · aplica cambios" : " · solo avisa") + ")";
  dibujarTopologia(e.topologia);
  for (const h of e.historial) {
    const cambios = h.cambios ? Object.entries(h.cambios).map(([sw, c]) => sw + " −" + c.quitar + " +" + c.agregar).join(" · ") : "";
    fila(tb, [hora(h.t), el("span", ETIQUETAS[h.tipo] || h.tipo, "tag " + (MALOS.has(h.tipo) ? "rechazado" : (h.tipo === "aplicado" || h.tipo === "enlace_restaurado" || h.tipo === "switch_restaurado" || h.tipo === "red_conexa" ? "admitido" : ""))), h.detalle, cambios]);
  }
  if (!e.historial.length) fila(tb, ["Sin eventos desde que arrancó el vigilante", "", "", ""]);
}

// ---------- ciclo ----------
async function refrescar() {
  try {
    if (vista === "resumen") { const d = await api("/api/resumen"); quien = d.quien || ""; pintarResumen(d); }
    else if (vista === "sesiones") pintarSesiones(await api("/api/sesiones"));
    else if (vista === "vistas") pintarVistas(await api("/api/vistas"));
    else if (vista === "vigilante") pintarVigilante(await api("/api/vigilante"));
    else if (vista === "eventos") await pintarEventos();
    $("estado").textContent = "actualizado " + new Date().toLocaleTimeString();
    if (vista === "resumen" && quien) $("estado").textContent += " · " + quien;
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
refrescar();
temporizador = setInterval(() => { if (vista !== "eventos") refrescar(); }, 5000);   // los eventos se actualizan al filtrar, para no perder lo que se está leyendo

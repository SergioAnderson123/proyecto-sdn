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

const porNombre = (a, b) => a.nombre.localeCompare(b.nombre);

// Posiciones FIJAS por capa, como en el dibujo de VNRT: el núcleo (core-1, core-2) arriba, los accesos abajo y el borde a un lado. No dependen de qué cable cayó ni de quién es la raíz:
// el dibujo no se mueve cuando algo cambia. Si el vigilante no informa la capa (versión anterior), se cae al orden por niveles.
function posiciones(switches) {
  const nucleo = switches.filter((x) => x.capa === "nucleo").sort(porNombre);
  if (nucleo.length) {
    const acceso = switches.filter((x) => x.capa === "acceso").sort(porNombre);
    const borde = switches.filter((x) => x.capa === "borde").sort(porNombre);
    const ancho = 720, alto = 330, x0 = borde.length ? 250 : 130, x1 = borde.length ? 610 : 590, pos = {};
    const fila = (lista, y) => lista.forEach((sw, k) => { pos[sw.id] = { x: lista.length === 1 ? (x0 + x1) / 2 : x0 + ((x1 - x0) * k) / (lista.length - 1), y }; });
    fila(nucleo, 70);
    fila(acceso, 260);
    borde.forEach((sw, k) => { pos[sw.id] = { x: 85, y: 165 + k * 75 }; });
    return { pos, ancho, alto };
  }
  const ancho = 720, alto_fila = 120, margen = 60;
  const maxNivel = Math.max(0, ...switches.map((x) => (x.nivel === null ? 0 : x.nivel)));
  const filas = new Map();
  for (const sw of switches) {
    const nivel = sw.nivel === null ? maxNivel + 1 : sw.nivel;
    if (!filas.has(nivel)) filas.set(nivel, []);
    filas.get(nivel).push(sw);
  }
  const pos = {};
  for (const [nivel, lista] of [...filas.entries()].sort((a, b) => a[0] - b[0])) {
    lista.sort(porNombre);
    lista.forEach((sw, k) => { pos[sw.id] = { x: (ancho * (k + 1)) / (lista.length + 1), y: margen + nivel * alto_fila }; });
  }
  return { pos, ancho, alto: margen * 2 + Math.max(...filas.keys()) * alto_fila };
}

function dibujarTopologia(top) {
  const cont = $("topologia");
  vaciar(cont);
  if (!top) { cont.appendChild(el("p", "El vigilante aún no publicó la topología.", "ayuda")); return; }
  const { pos, ancho, alto } = posiciones(top.switches);
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

// ---------- flujos de los switches ----------
const CAMPOS_FL = ["tabla", "prioridad", "app", "estado", "in_port", "eth_src", "eth_dst", "eth_type", "vlan", "ip_src", "ip_dst", "ip_dst_cubre", "ip_proto", "tcp_dst", "udp_dst", "accion", "texto"];
let flPagina = 0;
let flSwitchesListos = false;

function consultaFlujos(actualizar) {
  const p = new URLSearchParams();
  p.set("switch", $("fl-switch").value);
  for (const c of CAMPOS_FL) if ($("fl-" + c).value.trim()) p.set(c, $("fl-" + c).value.trim());
  if ($("fl-con_paquetes").checked) p.set("con_paquetes", "1");
  p.set("orden", $("fl-orden").value);
  p.set("pagina", String(flPagina));
  p.set("limite", "100");
  if (actualizar) p.set("actualizar", "1");
  return p;
}

async function cargarSwitchesFl() {
  const d = await api("/api/switches");
  const sel = $("fl-switch");
  const actual = sel.value;
  vaciar(sel);
  for (const s of d.switches) {
    const o = el("option", s.nombre + (s.maestro ? " (manda " + s.maestro + ")" : "") + (s.disponible ? "" : " — no disponible"));
    o.value = s.id;
    sel.appendChild(o);
  }
  if (actual) sel.value = actual;
  flSwitchesListos = d.switches.length > 0;
}

function explicacion(partes) {
  const det = document.createElement("details");
  det.open = true;
  det.className = "partes";
  det.appendChild(el("summary", "¿Qué hace cada parte?"));
  const ul = document.createElement("ul");
  for (const p of partes) {
    const li = document.createElement("li");
    if (p.nivel) li.className = "n" + p.nivel;
    li.appendChild(el("code", p.codigo));
    li.appendChild(document.createTextNode(" — " + p.explica));
    ul.appendChild(li);
  }
  det.appendChild(ul);
  return det;
}

function caja(titulo, texto, partes) {
  const c = el("div", null, "caja-cmd");
  c.appendChild(el("div", titulo, "ayuda"));
  const pre = el("pre", texto, "cmd");
  c.appendChild(pre);
  const b = el("button", "Copiar");
  b.type = "button";
  b.addEventListener("click", async () => {
    try { await navigator.clipboard.writeText(texto); b.textContent = "Copiado"; }
    catch (e) { const r = document.createRange(); r.selectNodeContents(pre); const s = window.getSelection(); s.removeAllRanges(); s.addRange(r); b.textContent = "Seleccionado: Ctrl+C"; }
    setTimeout(() => { b.textContent = "Copiar"; }, 2000);
  });
  c.appendChild(b);
  if (partes && partes.length) c.appendChild(explicacion(partes));
  return c;
}

async function pintarFlujos(actualizar) {
  if (!flSwitchesListos) await cargarSwitchesFl();
  if (!$("fl-switch").value) { $("fl-info").textContent = "(no hay switches todavía)"; return; }
  let r;
  try {
    r = await fetch("/api/flujos?" + consultaFlujos(actualizar).toString(), { credentials: "same-origin", cache: "no-store" });
  } catch (e) { $("fl-info").textContent = "(error de red)"; return; }
  if (r.status === 403) { await api("/api/resumen"); return; }
  const d = await r.json();
  const info = $("fl-info");
  const tb = $("t-fl").tBodies[0];
  const cont = $("fl-comandos");
  vaciar(tb); vaciar(cont);
  if (!r.ok) { info.textContent = "(" + (d.error || "error") + ")"; info.className = "cuenta error"; return; }
  info.className = "cuenta";
  const hace = d.t ? Math.max(0, Math.round(Date.now() / 1000 - d.t)) : null;
  info.textContent = "(" + d.switch.nombre + " · leído del maestro " + (d.nodo || "?") + (hace === null ? "" : " hace " + hace + " s") + " · " + d.total + " de " + d.total_switch + " flujos)";
  if (d.error) { const p = el("p", (d.antiguo ? "Se muestra la lectura anterior. " : "") + d.error, "error"); cont.appendChild(p); }
  // datalist de apps, con la cuenta de cada una
  const dl = $("fl-apps");
  vaciar(dl);
  for (const [app, n] of Object.entries(d.cuentas.apps)) { const o = el("option", app + " (" + n + ")"); o.value = app; dl.appendChild(o); }
  // comandos para el switch real
  const dnd = d.switch.ssh ? (d.switch.ssh.local ? "En " + d.switch.ssh.vm + ": " : "En la VM " + d.switch.ssh.vm + " (entra con " + d.switch.ssh.comando + "): ") : "En la VM del switch: ";
  cont.appendChild(caja(dnd + "los mismos flujos con ovs-ofctl", d.comandos.principal, d.comandos.partes));
  if (d.comandos.traza) cont.appendChild(caja("Qué regla atrapa un paquete así (ofproto/trace)", d.comandos.traza, d.comandos.traza_partes));
  if (d.comandos.notas.length) { const ul = el("ul", null, "notas"); for (const n of d.comandos.notas) ul.appendChild(el("li", n)); cont.appendChild(ul); }
  for (const x of d.flujos) {
    const tr = fila(tb, [x.tabla, x.prioridad, x.app.replace("org.campus.", "campus.").replace("org.onosproject.", "onos."), el("span", x.coincide_texto, "mono"), x.acciones_texto, x.paquetes, x.estado, x.explicacion]);
    tr.title = "id " + x.id + " · " + x.bytes + " bytes · vive " + x.vida_s + " s";
  }
  if (!d.flujos.length) fila(tb, ["Ningún flujo con esos filtros", "", "", "", "", "", "", ""]);
  const paginas = Math.max(1, Math.ceil(d.total / d.limite));
  $("fl-pag").textContent = "página " + (d.pagina + 1) + " de " + paginas;
  $("fl-ant").disabled = d.pagina <= 0;
  $("fl-sig").disabled = d.pagina + 1 >= paginas;
}

// ---------- políticas de R2 ----------
let politicaCache = null;

function irAFlujos(app, tabla, switchId) {
  for (const c of CAMPOS_FL) $("fl-" + c).value = "";
  $("fl-con_paquetes").checked = false;
  if (app) $("fl-app").value = app;
  if (tabla) $("fl-tabla").value = tabla;
  flPagina = 0;
  cambiar("flujos");
  if (switchId) $("fl-switch").value = switchId;
}

async function llenarSwitches(sel) {
  const d = await api("/api/switches");
  const actual = sel.value;
  vaciar(sel);
  for (const s of d.switches) { const o = el("option", s.nombre + (s.maestro ? " (manda " + s.maestro + ")" : "")); o.value = s.id; sel.appendChild(o); }
  if (actual) sel.value = actual;
}

function pintarReglasDeRol() {
  const tb = $("t-rol").tBodies[0];
  vaciar(tb);
  const rol = $("rol-sel").value;
  const lista = politicaCache && politicaCache.flujos_por_rol ? politicaCache.flujos_por_rol[rol] || [] : [];
  for (const x of lista) fila(tb, [x.tabla, x.prioridad, el("span", x.coincide_texto, "mono"), x.acciones_texto, x.explicacion]);
  if (!lista.length) fila(tb, ["Sin reglas", "", "", "", ""]);
}

async function pintarPoliticas() {
  const d = await api("/api/politica");
  const pol = d.politica;
  politicaCache = pol;
  $("pol-info").textContent = d.disponible ? "(foto de hace " + duracion(d.edad_s) + (pol && pol.origen ? ", de la copia de " + pol.origen : "") + ")" : "";
  const err = $("pol-error");
  err.textContent = !d.disponible ? (d.error || "Todavía no hay foto de la configuración.") : (pol && pol.error ? "No se pudo interpretar la política: " + pol.error : "");
  for (const id of ["t-roles", "t-servicios", "t-rol"]) vaciar($(id).tBodies[0]);
  vaciar($("t-matriz").tHead); vaciar($("t-matriz").tBodies[0]); vaciar($("pol-otros"));
  if (!pol || pol.error) return;
  const matriz = pol.matriz;
  for (const r of pol.roles) {
    const m = matriz[r.matriz] || {};
    fila($("t-roles").tBodies[0], [r.rol, r.matriz, r.tabla, Object.keys(m).filter((s) => m[s] === true && pol.servicios[s]).join(", ") || "—", Object.keys(m).filter((s) => m[s] !== true).join(", ") || "—"]);
  }
  const matrices = Object.keys(matriz);
  const cab = document.createElement("tr");
  for (const h of ["Servicio"].concat(matrices)) cab.appendChild(el("th", h));
  $("t-matriz").tHead.appendChild(cab);
  const servicios = Object.keys(matriz[matrices[0]] || {});
  for (const s of servicios) fila($("t-matriz").tBodies[0], [s].concat(matrices.map((m) => (matriz[m][s] === true ? "permitido" : matriz[m][s] === false ? "denegado" : "sin regla (denegado)"))));
  for (const [n, s] of Object.entries(pol.servicios)) fila($("t-servicios").tBodies[0], [n, s.ip, s.tcp, ""]);
  for (const n of pol.sin_host) fila($("t-servicios").tBodies[0], [n, "—", "—", "tiene reglas pero ningún host: no se instala"]);
  const li = (t) => $("pol-otros").appendChild(el("li", t));
  li("Reenvío: " + pol.reenvio + (pol.reenvio === "proactivo" ? " (lo permitido sale por la tabla 60, sin NORMAL)" : " (OUTPUT NORMAL)"));
  li("Ascenso por el portal: " + (Object.keys(pol.ascenso).length ? Object.entries(pol.ascenso).map(([a, b]) => a + " → " + b.join(", ")).join("; ") : "ninguno") + (pol.ascenso_exigir_ubicacion ? " (exige la ubicación de ONOS AAA)" : ""));
  li("Cuarentena: " + (pol.cuarentena ? "tabla y VLAN " + pol.cuarentena.tabla : "desactivada"));
  const po = pol.portal || {};
  li("Portal: " + (po.ip ? po.ip + ":" + po.puerto : "sin portal") + (po.login_directo ? " · entran directo: " + po.login_directo.join(", ") : "") + (po.grupos ? " · grupos: " + Object.entries(po.grupos).map(([g, r]) => g + " → " + r).join(", ") : "")
     + (po.duracion_s ? " · duración (s): " + Object.entries(po.duracion_s).map(([k, v]) => k + " " + v).join(", ") : ""));
  li("Puertos de confianza: " + (Object.keys(pol.trusted).length ? Object.entries(pol.trusted).map(([sw, p]) => (pol.nombres_switch[sw] || sw) + ": " + (Array.isArray(p) ? p.join(",") : p)).join("; ") : "ninguno"));
  li("Switches sin usuarios (sin tablas de rol): " + (pol.sin_usuarios.length ? pol.sin_usuarios.map((s) => pol.nombres_switch[s] || s).join(", ") : "ninguno"));
  const sel = $("rol-sel");
  const previo = sel.value;
  vaciar(sel);
  for (const r of pol.roles) { const o = el("option", r.rol + " (tabla " + r.tabla + ")"); o.value = r.rol; sel.appendChild(o); }
  if (previo) sel.value = previo;
  pintarReglasDeRol();
  await llenarSwitches($("cmp-switch"));
}

async function compararConSwitch() {
  const sel = $("cmp-switch");
  const cont = $("cmp-resultado");
  vaciar(cont);
  cont.appendChild(el("p", "Leyendo del maestro…", "ayuda"));
  const r = await fetch("/api/politica/comparar?" + new URLSearchParams({ switch: sel.value }).toString(), { credentials: "same-origin", cache: "no-store" });
  const d = await r.json();
  vaciar(cont);
  if (!r.ok) { cont.appendChild(el("p", d.error || "error", "error")); return; }
  if (!d.comparacion) { cont.appendChild(el("p", d.error || "sin datos", "error")); return; }
  const c = d.comparacion;
  const ok = c.n_faltan === 0 && c.n_sobran === 0;
  cont.appendChild(el("p", d.switch.nombre + ": la política espera " + c.esperados + " flujos de R2; hay " + c.instalados + " instalados; coinciden " + c.coinciden + ". Faltan " + c.n_faltan + " y sobran " + c.n_sobran + "." + (d.antiguo ? " (lectura anterior: " + d.error + ")" : ""), ok ? "ok" : "alto"));
  for (const [titulo, lista, n] of [["Faltan (la política los pide y no están instalados)", c.faltan, c.n_faltan], ["Sobran (están instalados y la política ya no los pide)", c.sobran, c.n_sobran]]) {
    if (!n) continue;
    cont.appendChild(el("h4", titulo + (n > lista.length ? " — se muestran " + lista.length + " de " + n : "")));
    const t = document.createElement("table");
    const th = document.createElement("thead"); const trh = document.createElement("tr");
    for (const h of ["Tabla", "Prio.", "Coincide", "Acciones", "Qué es"]) trh.appendChild(el("th", h));
    th.appendChild(trh); t.appendChild(th);
    const tb = document.createElement("tbody"); t.appendChild(tb);
    for (const x of lista) fila(tb, [x.tabla, x.prioridad, el("span", x.coincide_texto, "mono"), x.acciones_texto, x.explicacion]);
    cont.appendChild(t);
  }
}

// ---------- archivos de configuración ----------
const ESTADOS_CFG = { igual: "igual en todas las copias", diverge: "DIVERGE", una_copia: "una sola copia (nada con qué comparar)", sin_datos: "sin copias" };

async function mostrarArchivo(id) {
  const d = await api("/api/config/archivo?id=" + encodeURIComponent(id));
  const a = d.archivo;
  $("cfg-detalle").hidden = false;
  $("cfg-titulo").textContent = a.titulo;
  const sel = $("cfg-copia");
  vaciar(sel);
  for (const c of a.copias.filter((x) => x.existe)) { const o = el("option", c.fuente + " · " + c.sha256.slice(0, 10) + " · " + c.bytes + " bytes"); o.value = c.sha256; sel.appendChild(o); }
  const verContenido = () => { $("cfg-contenido").textContent = a.contenidos[sel.value] || "(sin contenido)"; };
  sel.onchange = verContenido;
  verContenido();
  const dif = $("cfg-diffs");
  vaciar(dif);
  for (const x of a.diferencias) { dif.appendChild(el("h4", "Diferencias: " + x.base + " frente a " + x.con.join(", "))); dif.appendChild(el("pre", x.diff, "cmd")); }
  $("cfg-detalle").scrollIntoView();
}

async function pintarConfig() {
  const d = await api("/api/config");
  $("cfg-info").textContent = d.disponible ? "(foto de hace " + duracion(d.edad_s) + ")" : "";
  $("cfg-error").textContent = d.error && !d.disponible ? d.error : (d.error || "");
  const tb = $("t-cfg").tBodies[0];
  vaciar(tb);
  for (const a of d.archivos) {
    const copias = a.copias.map((c) => c.fuente + (c.existe ? " ✔" : " —")).join("   ");
    const b = el("button", "Ver");
    b.type = "button";
    b.addEventListener("click", () => mostrarArchivo(a.id));
    const estado = el("div");
    estado.appendChild(el("span", ESTADOS_CFG[a.estado] || a.estado, "tag " + (a.estado === "diverge" ? "rechazado" : a.estado === "igual" ? "admitido" : "")));
    if (a.nota) estado.appendChild(el("div", a.nota, "ayuda"));
    const tr = fila(tb, [el("span", a.id, "mono"), estado, copias, a.descripcion, b]);
    if (a.estado === "diverge") tr.className = "desigual";
  }
}

// ---------- ciclo ----------
async function refrescar() {
  try {
    if (vista === "resumen") { const d = await api("/api/resumen"); quien = d.quien || ""; pintarResumen(d); }
    else if (vista === "sesiones") pintarSesiones(await api("/api/sesiones"));
    else if (vista === "vistas") pintarVistas(await api("/api/vistas"));
    else if (vista === "vigilante") pintarVigilante(await api("/api/vigilante"));
    else if (vista === "flujos") await pintarFlujos(false);
    else if (vista === "politicas") await pintarPoliticas();
    else if (vista === "config") await pintarConfig();
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
$("f-fl").addEventListener("submit", (ev) => { ev.preventDefault(); flPagina = 0; pintarFlujos(false); });
$("fl-switch").addEventListener("change", () => { flPagina = 0; });
$("fl-releer").addEventListener("click", () => pintarFlujos(true));
$("fl-limpiar").addEventListener("click", () => { for (const c of CAMPOS_FL) $("fl-" + c).value = ""; $("fl-con_paquetes").checked = false; flPagina = 0; pintarFlujos(false); });
$("f-rol").addEventListener("submit", (ev) => { ev.preventDefault(); pintarReglasDeRol(); });
$("rol-ver-switches").addEventListener("click", () => {
  const rol = politicaCache && politicaCache.roles ? politicaCache.roles.find((r) => r.rol === $("rol-sel").value) : null;
  if (rol) irAFlujos("campus.r2", String(rol.tabla), $("cmp-switch").value);
});
$("f-cmp").addEventListener("submit", (ev) => { ev.preventDefault(); compararConSwitch(); });
$("fl-ant").addEventListener("click", () => { flPagina = Math.max(0, flPagina - 1); pintarFlujos(false); });
$("fl-sig").addEventListener("click", () => { flPagina += 1; pintarFlujos(false); });
refrescar();
temporizador = setInterval(() => { if (!["eventos", "flujos", "politicas", "config"].includes(vista)) refrescar(); }, 5000);   // estas pantallas se actualizan al abrirlas o al filtrar: no se pierde lo que se lee ni se lee a ONOS en bucle

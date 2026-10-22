// Frontend estático sin build. Todas las llamadas van a /api/ (nginx -> API :8000).
const API = "/api";
const $ = (id) => document.getElementById(id);
const reduceMotion = window.matchMedia("(prefers-reduced-motion: reduce)").matches;

// Aeropuertos conocidos (solo para mostrar el nombre de la ciudad; se aceptan otros códigos)
const CITIES = {
  BOG: "Bogotá", MDE: "Medellín", CLO: "Cali", CTG: "Cartagena", BAQ: "Barranquilla", SMR: "Santa Marta",
  BGA: "Bucaramanga", PEI: "Pereira", CUC: "Cúcuta", ADZ: "San Andrés", LET: "Leticia", PSO: "Pasto",
};
const MONTHS = ["ene", "feb", "mar", "abr", "may", "jun", "jul", "ago", "sep", "oct", "nov", "dic"];

async function api(path, opts) {
  const r = await fetch(API + path, opts);
  let body = null;
  try { body = await r.json(); } catch (_) {}
  return { status: r.status, ok: r.ok, body };
}

function detailText(body, fallback) {
  if (!body || body.detail === undefined) return fallback;
  if (typeof body.detail === "string") return body.detail;
  try { return body.detail.map((d) => d.msg).join("; "); } catch (_) { return fallback; }
}

function setMsg(el, text, kind) {
  el.textContent = text || "";
  el.className = "msg" + (kind ? " " + kind : "");
}

// "ECONOMICA" / "PREMIUM" del backend a texto con tilde
function cabinName(c) {
  return /^PREM/i.test(String(c)) ? "Premium" : "Económica";
}

function money(cents) {
  return (cents / 100).toLocaleString("es-CO", { style: "currency", currency: "COP", maximumFractionDigits: 0 });
}

function longDate(iso) {
  const [y, m, d] = String(iso).slice(0, 10).split("-").map(Number);
  return new Date(y, m - 1, d).toLocaleDateString("es-CO", { weekday: "short", day: "numeric", month: "short", year: "numeric" });
}


// ------------------------------ Tarifas y mapa de sillas (compartido) ------------------------------
const FARES = [
  { code: "XS", name: "XS", mult: 0.70, cond: "Silla en el check-in" },
  { code: "S", name: "S", mult: 0.85, cond: "Silla en el check-in" },
  { code: "M", name: "M", mult: 1.00, cond: "Silla en el check-in" },
  { code: "L", name: "L", mult: 1.30, cond: "Elige silla ahora" },
  { code: "PR", name: "Premium", mult: 2.20, cond: "Elige silla ahora, cabina Premium" },
];
const fareName = (c) => (FARES.find((f) => f.code === c) || { name: c }).name;
const GROUPS = { PR: 1, L: 2, M: 3, S: 4, XS: 4 };

function seatRow(no) { return parseInt(String(no), 10) || 0; }

// Dibuja el mapa en `box`. opts: premium (true si la reserva es PR), own, selected, disputed, onPick(seatNo)
function renderSeatMap(box, data, opts) {
  opts = opts || {};
  box.innerHTML = "";
  const letters = Array.isArray(data.letters) ? data.letters : String(data.letters || "ABCDEF").split("");
  const half = Math.ceil(letters.length / 2);
  const prem = data.premium_rows || 0;
  const bySeat = new Map(data.seats.map((s) => [s.seat_no, s]));
  const wrap = document.createElement("div");
  wrap.className = "sm";
  wrap.setAttribute("role", "group");
  wrap.setAttribute("aria-label", "Mapa de sillas");
  const head = document.createElement("div");
  head.className = "sm-row sm-head";
  head.setAttribute("aria-hidden", "true");
  head.appendChild(document.createElement("span"));
  letters.forEach((l, i) => {
    if (i === half) head.appendChild(Object.assign(document.createElement("span"), { className: "sm-aisle" }));
    head.appendChild(Object.assign(document.createElement("span"), { className: "sm-l", textContent: l }));
  });
  wrap.appendChild(head);
  let lastCabin = "";
  for (let r = 1; r <= data.rows; r++) {
    const isPrem = r <= prem;
    const cabin = isPrem ? "Premium" : "Económica";
    if (cabin !== lastCabin) {
      const t = document.createElement("p");
      t.className = "sm-cabin";
      t.textContent = isPrem ? "Cabina Premium" : "Cabina económica";
      wrap.appendChild(t);
      lastCabin = cabin;
    }
    const row = document.createElement("div");
    row.className = "sm-row" + (isPrem ? " prem" : "");
    row.appendChild(Object.assign(document.createElement("span"), { className: "sm-n", textContent: r }));
    letters.forEach((l, i) => {
      if (i === half) row.appendChild(Object.assign(document.createElement("span"), { className: "sm-aisle" }));
      const no = r + l;
      const s = bySeat.get(no);
      const b = document.createElement("button");
      b.type = "button";
      b.className = "seat";
      b.textContent = l;
      const mine = opts.own === no;
      const taken = !s || s.status === "TAKEN" || s.status === "HELD";
      const wrongCabin = prem > 0 && typeof opts.premium === "boolean" && opts.premium !== isPrem;
      let state = "libre";
      if (mine) { b.classList.add("mine"); state = "su silla"; }
      else if (opts.selected === no) { b.classList.add("sel"); state = "elegida"; }
      else if (s && s.status === "HELD") { b.classList.add("held"); state = "bloqueada por otra persona"; }
      else if (taken) { b.classList.add("taken"); state = "ocupada"; }
      else if (wrongCabin) { b.classList.add("off"); state = "otra cabina"; }
      if (opts.disputed === no) { b.classList.add("disputed"); state += ", en disputa"; }
      const blocked = !mine && (taken || wrongCabin) && opts.selected !== no;
      b.setAttribute("aria-label", "Silla " + no + ", " + (isPrem ? "Premium" : "económica") + ", " + state);
      if (opts.selected === no || mine) b.setAttribute("aria-pressed", "true"); else b.setAttribute("aria-pressed", "false");
      if (blocked) { b.disabled = true; } else if (opts.onPick) { b.addEventListener("click", () => opts.onPick(no)); }
      row.appendChild(b);
    });
    wrap.appendChild(row);
  }
  box.appendChild(wrap);
  const key = document.createElement("p");
  key.className = "sm-key";
  key.innerHTML = '<span class="seat-k"></span> Libre <span class="seat-k sel"></span> Elegida <span class="seat-k taken"></span> Ocupada' +
    (opts.own ? ' <span class="seat-k mine"></span> Su silla' : "");
  box.appendChild(key);
}

function fmtDeparture(iso) {
  const d = new Date(iso);
  if (isNaN(d)) return String(iso || "");
  return d.toLocaleString("es-CO", { weekday: "short", day: "numeric", month: "short", hour: "2-digit", minute: "2-digit", timeZone: "America/Bogota" });
}

// ------------------------------ Cliente ------------------------------
function initClient() {
  let selected = null;
  let fare = "M";          // clase por defecto: el flujo del harness no exige elegir nada
  let pickedSeat = null;   // solo L y PR
  let seatData = null;
  const booking = $("booking");

  function renderFares() {
    const list = $("fare-list");
    list.innerHTML = "";
    FARES.forEach((f) => {
      const id = "fare-" + f.code;
      const lab = document.createElement("label");
      lab.className = "fare" + (f.code === fare ? " on" : "");
      lab.htmlFor = id;
      const inp = document.createElement("input");
      inp.type = "radio"; inp.name = "fare"; inp.id = id; inp.value = f.code; inp.checked = f.code === fare;
      inp.addEventListener("change", () => setFare(f.code));
      const est = selected ? money(Math.round(selected.price_cents * f.mult)) : "";
      lab.append(inp);
      lab.insertAdjacentHTML("beforeend", '<strong class="fare-n"></strong><span class="fare-p"></span><small class="fare-c"></small>');
      lab.querySelector(".fare-n").textContent = f.name;
      lab.querySelector(".fare-p").textContent = est ? "~ " + est : "";
      lab.querySelector(".fare-c").textContent = f.cond;
      list.appendChild(lab);
    });
  }

  function setFare(code) {
    fare = code;
    pickedSeat = null;
    document.querySelectorAll("#fare-list .fare").forEach((l) => l.classList.toggle("on", l.querySelector("input").value === code));
    updateSeatPick();
  }

  async function updateSeatPick() {
    const box = $("seatpick");
    const wants = (fare === "L" || fare === "PR") && selected;
    box.hidden = !wants;
    $("seatpick-clear").hidden = !pickedSeat;
    if (!wants) return;
    setMsg($("seatpick-msg"), pickedSeat ? "Silla elegida: " + pickedSeat + "." : "Sin silla elegida: podrá escogerla en el check-in.", "info");
    if (!seatData || seatData.flight_id !== selected.flight_id) {
      $("seatpick-map").textContent = "Cargando sillas…";
      try {
        const r = await api("/flights/" + encodeURIComponent(selected.flight_id) + "/seats");
        if (!r.ok) { $("seatpick-map").textContent = ""; setMsg($("seatpick-msg"), "No se pudo cargar el mapa. Puede reservar sin silla y elegirla en el check-in.", "err"); return; }
        seatData = r.body;
      } catch (_) {
        $("seatpick-map").textContent = "";
        setMsg($("seatpick-msg"), "Sin conexión para cargar el mapa. Puede reservar sin silla y elegirla en el check-in.", "err");
        return;
      }
    }
    drawPick();
  }

  function drawPick() {
    if (!seatData) return;
    renderSeatMap($("seatpick-map"), seatData, {
      premium: fare === "PR",
      selected: pickedSeat,
      onPick: (no) => { pickedSeat = pickedSeat === no ? null : no; $("seatpick-clear").hidden = !pickedSeat; setMsg($("seatpick-msg"), pickedSeat ? "Silla elegida: " + pickedSeat + "." : "Sin silla elegida: podrá escogerla en el check-in.", "info"); drawPick(); },
    });
  }
  $("seatpick-clear").addEventListener("click", () => { pickedSeat = null; $("seatpick-clear").hidden = true; setMsg($("seatpick-msg"), "Sin silla elegida: podrá escogerla en el check-in.", "info"); drawPick(); });

  // Rutas con vuelos (GET /routes). Si falla, todo sigue como antes.
  let routes = [];
  const cityOf = (code) => {
    const r = routes.find((x) => x.origin === code && x.origin_city) ; if (r) return r.origin_city;
    const d = routes.find((x) => x.destination === code && x.destination_city); return d ? d.destination_city : (CITIES[code] || "");
  };
  const destsOf = (o) => routes.filter((r) => r.origin === o);
  const chipText = (r) => r.origin + " → " + r.destination;

  function applyRoute(r) {
    $("origin").value = r.origin;
    $("destination").value = r.destination;
    $("origin").dispatchEvent(new Event("input"));
    $("destination").dispatchEvent(new Event("input"));
    $("date").focus();
  }

  // Rutas agrupadas por ciudad de origen: una tarjeta por origen con sus destinos
  function renderRoutes() {
    const box = $("routes-list");
    box.innerHTML = "";
    const origins = [...new Set(routes.map((r) => r.origin))]
      .sort((a, b) => destsOf(b).length - destsOf(a).length || a.localeCompare(b));
    origins.forEach((o) => {
      const card = document.createElement("div");
      card.className = "route-group";
      const h = document.createElement("h3");
      h.innerHTML = "<span></span> <small></small>";
      h.children[0].textContent = "Desde " + (cityOf(o) || o);
      h.children[1].textContent = o;
      const ul = document.createElement("div");
      ul.className = "chips";
      destsOf(o).sort((a, b) => (cityOf(a.destination) || "").localeCompare(cityOf(b.destination) || "")).forEach((r) => {
        const b = document.createElement("button");
        b.type = "button";
        b.className = "chip" + (r.route_type === "INTERNACIONAL" ? " intl" : "");
        b.innerHTML = "<span></span><small></small>";
        b.children[0].textContent = r.destination_city || cityOf(r.destination) || r.destination;
        b.children[1].textContent = r.destination;
        b.setAttribute("aria-label", "Buscar " + (r.origin_city || r.origin) + " a " + (r.destination_city || r.destination) +
          (r.route_type === "INTERNACIONAL" ? ", internacional" : ""));
        b.addEventListener("click", () => applyRoute(r));
        ul.appendChild(b);
      });
      card.append(h, ul);
      box.appendChild(card);
    });
    $("routes").hidden = !routes.length;
  }

  api("/routes").then((r) => {
    if (r.ok && Array.isArray(r.body)) { routes = r.body; renderRoutes(); $("origin").dispatchEvent(new Event("input")); }
  }).catch(() => {});

  function noRouteHelp(o, d) {
    if (!routes.length || routes.some((r) => r.origin === o && r.destination === d)) return "";
    const dests = destsOf(o);
    if (dests.length) return "No hay vuelos en la ruta " + o + " a " + d + ". Desde " + o + " sí vuelan a: " + dests.map((r) => r.destination).join(", ") + ". Toque una ruta de la lista.";
    return "No hay vuelos en la ruta " + o + " a " + d + ". Desde " + o + " no hay salidas; elija una de las rutas disponibles: " + routes.map(chipText).join(", ") + ".";
  }

  // Nombre de la ciudad bajo cada código IATA
  [["origin", "origin-city", "Elija el aeropuerto de salida"], ["destination", "destination-city", "Elija el aeropuerto de llegada"]]
    .forEach(([inp, out, empty]) => {
      $(inp).addEventListener("input", () => {
        const v = $(inp).value.replace(/[^a-zA-Z]/g, "").toUpperCase();
        $(inp).value = v;
        let txt = v.length < 3 ? empty : (cityOf(v) || CITIES[v] || "Aeropuerto " + v);
        if (inp === "origin" && v.length === 3 && routes.length) {
          const n = destsOf(v).length;
          txt += n ? ", " + n + (n === 1 ? " destino" : " destinos") : ", sin salidas";
        }
        $(out).textContent = txt;
      });
    });

  // Fecha por defecto: mañana
  const t = new Date(Date.now() + 864e5);
  $("date").value = t.getFullYear() + "-" + String(t.getMonth() + 1).padStart(2, "0") + "-" + String(t.getDate()).padStart(2, "0");

  $("search-form").addEventListener("submit", async (e) => {
    e.preventDefault();
    const o = $("origin").value.trim().toUpperCase();
    const d = $("destination").value.trim().toUpperCase();
    const date = $("date").value.trim();
    $("results").innerHTML = "";
    $("board").hidden = true;
    booking.hidden = true;
    $("confirmation").hidden = true;
    if (!/^[A-Z]{3}$/.test(o) || !/^[A-Z]{3}$/.test(d) || !date) {
      setMsg($("search-msg"), "Escriba los códigos de 3 letras de origen y destino y elija una fecha.", "err");
      return;
    }
    if (o === d) { setMsg($("search-msg"), "El origen y el destino deben ser distintos.", "err"); return; }
    setMsg($("search-msg"), "Buscando vuelos…", "info");
    const q = new URLSearchParams({ origin: o, destination: d, date });
    try {
      const r = await api("/flights/search?" + q);
      if (!r.ok) { setMsg($("search-msg"), detailText(r.body, "No se pudo buscar (error " + r.status + ")."), "err"); return; }
      if (!r.body.length) {
        const help = noRouteHelp(o, d);
        if (help) { setMsg($("search-msg"), help, "info"); return; }
        setMsg($("search-msg"), "No hay vuelos con sillas libres de " + o + " a " + d + " el " + longDate(date) + ". Pruebe con otra fecha.", "info");
        return;
      }
      setMsg($("search-msg"), "");
      $("board-title").textContent = "Salidas " + o + " a " + d + ", " + longDate(date);
      renderFlights(r.body);
      $("board").hidden = false;
    } catch (_) {
      setMsg($("search-msg"), "Sin conexión con el servidor. Revise su red e intente de nuevo.", "err");
    }
  });

  function renderFlights(list) {
    const box = $("results");
    box.innerHTML = "";
    list.forEach((f) => {
      const row = document.createElement("div");
      row.className = "flight";
      row.setAttribute("data-testid", "flight-row");

      const time = document.createElement("span");
      time.className = "f-time";
      time.innerHTML = "<b></b><small>Salida</small>";
      time.children[0].textContent = f.departure_time ? String(f.departure_time).slice(0, 5) : "--:--";

      const id = document.createElement("span");
      id.className = "f-id";
      const [ro, rd] = String(f.route || "").split("-");
      id.innerHTML = "<span class=\"f-path\"><b></b><i aria-hidden=\"true\"></i><b></b></span><small></small>";
      id.querySelectorAll("b")[0].textContent = ro || "";
      id.querySelectorAll("b")[1].textContent = rd || "";
      id.querySelector("small").textContent = "Vuelo " + (f.flight_number || f.flight_id) + ", directo";
      if (f.route_type === "INTERNACIONAL") {
        const bd = document.createElement("span");
        bd.className = "badge intl";
        bd.textContent = "Internacional";
        id.appendChild(bd);
      }

      const seats = document.createElement("span");
      seats.className = "f-seats";
      const n = f.seats_available;
      seats.textContent = n === 1 ? "Última silla" : n + " libres";
      if (n <= 5) seats.classList.add("low");
      if (f.capacity) {
        const bar = document.createElement("i");
        bar.style.setProperty("--free", Math.max(0, Math.min(1, n / f.capacity)));
        seats.prepend(bar);
      }

      const price = document.createElement("span");
      price.className = "f-price";
      price.innerHTML = "<small>Desde</small><b></b>";
      price.children[1].textContent = money(f.price_cents);

      const b = document.createElement("button");
      b.type = "button";
      b.className = "btn-row";
      b.textContent = "Reservar";
      b.setAttribute("data-testid", "reserve-button");
      b.setAttribute("aria-label", "Reservar vuelo " + (f.flight_number || f.flight_id));
      b.addEventListener("click", () => {
        selected = f;
        box.querySelectorAll(".flight.on").forEach((x) => x.classList.remove("on"));
        row.classList.add("on");
        $("booking-flight").textContent = f.flight_number || f.flight_id;
        $("confirmation").hidden = true;
        setMsg($("booking-msg"), "");
        fare = "M"; pickedSeat = null; seatData = null;
        renderFares();
        updateSeatPick();
        row.after(booking);
        booking.hidden = false;
        $("passenger").focus();
      });

      row.append(time, id, seats, price, b);
      box.appendChild(row);
    });
  }

  $("booking-form").addEventListener("submit", async (e) => {
    e.preventDefault();
    const name = $("passenger").value.trim();
    if (!selected) return;
    if (!name) { setMsg($("booking-msg"), "Escriba el nombre del pasajero tal como aparece en su documento.", "err"); return; }
    const btn = e.submitter || document.querySelector("[data-testid=confirm-submit]");
    btn.disabled = true;
    btn.textContent = "Confirmando…";
    setMsg($("booking-msg"), "");
    try {
      const payload = { flight_id: selected.flight_id, passenger_name: name, fare_class: fare };
      if ((fare === "L" || fare === "PR") && pickedSeat) payload.seat = pickedSeat;
      const r = await api("/reservations", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      });
      if (r.status === 201 || r.status === 200) {
        showPass(r.body, name);
      } else if (r.status === 409 && payload.seat) {
        pickedSeat = null;
        seatData = null;
        updateSeatPick();
        setMsg($("booking-msg"), "La silla " + payload.seat + " ya no está libre y la reserva no se creó. Elija otra silla o reserve sin silla.", "err");
      } else if (r.status === 409) {
        setMsg($("booking-msg"), "Este vuelo se llenó mientras reservaba. Elija otro vuelo de la lista.", "err");
      } else if (r.status === 404) {
        setMsg($("booking-msg"), "Este vuelo ya no está disponible. Busque de nuevo.", "err");
      } else {
        setMsg($("booking-msg"), detailText(r.body, "No se pudo reservar (error " + r.status + ")."), "err");
      }
    } catch (_) {
      setMsg($("booking-msg"), "Sin conexión con el servidor. Su reserva no se creó; intente de nuevo.", "err");
    } finally {
      btn.disabled = false;
      btn.textContent = "Confirmar reserva";
    }
  });

  function showPass(res, name) {
    const route = String(selected.route || "").split("-");
    $("pass-o").textContent = route[0] || "";
    $("pass-d").textContent = route[1] || "";
    $("pass-name").textContent = res.passenger_name || name;
    $("pass-flight").textContent = selected.flight_number || selected.flight_id;
    $("pass-date").textContent = longDate(selected.flight_date) + (selected.departure_time ? ", " + String(selected.departure_time).slice(0, 5) : "");
    $("pass-fare").textContent = "Clase " + fareName(res.fare_class || fare) + (res.cabin ? ", cabina " + cabinName(res.cabin) : "");
    const st = $("pass-seat");
    st.textContent = res.seat ? res.seat : "la eliges en el check-in, que abre 24 h antes";
    $("confirmation-id").textContent = res.reservation_id;
    booking.hidden = true;
    $("board").hidden = true;
    $("confirmation").hidden = false;
    $("confirmation").scrollIntoView({ behavior: reduceMotion ? "auto" : "smooth", block: "start" });
  }

  $("copy-id").addEventListener("click", async () => {
    try {
      await navigator.clipboard.writeText($("confirmation-id").textContent);
      $("copy-id").textContent = "Código copiado";
    } catch (_) {
      $("copy-id").textContent = "Copie el código a mano";
    }
  });
}

// ------------------------------ Check-in ------------------------------
function initCheckin() {
  let res = null, seg = null, seats = null;
  let held = null, holdTimer = null, holdEnd = 0, disputed = null;
  let ownSeat = null;

  const params = new URLSearchParams(location.search);
  if (params.get("code")) $("ci-code").value = params.get("code");

  const hasSeat = () => ["L", "PR"].includes(res.fare_class);

  function stopHold() { clearInterval(holdTimer); holdTimer = null; held = null; $("ci-hold").hidden = true; }

  function tick() {
    const left = Math.max(0, Math.round((holdEnd - Date.now()) / 1000));
    $("ci-count").textContent = Math.floor(left / 60) + ":" + String(left % 60).padStart(2, "0");
    $("ci-count-sr").textContent = left % 60 === 0 || left <= 10 ? " " + Math.floor(left / 60) + " minutos " + (left % 60) + " segundos" : "";
    if (left <= 0) {
      const old = held;
      stopHold();
      updateConfirm();
      setMsg($("ci-seat-msg"), "El bloqueo de la silla " + old + " venció. Elija una silla para bloquearla de nuevo.", "err");
      refreshSeats();
    }
  }

  function updateConfirm() {
    $("ci-confirm").disabled = !(held || (hasSeat() && ownSeat));
  }

  function draw() {
    if (!seats) return;
    renderSeatMap($("ci-map"), seats, { premium: res.fare_class === "PR", own: ownSeat, selected: held, disputed, onPick: pick });
  }

  async function refreshSeats() {
    try {
      const r = await api("/flights/" + encodeURIComponent(seg.flight_id) + "/seats");
      if (r.ok) seats = r.body;
    } catch (_) {}
    draw();
  }

  function windowState() {
    const now = Date.now();
    const o = new Date(seg.checkin_opens_at), c = new Date(seg.checkin_closes_at);
    if (isNaN(o) || isNaN(c)) return { open: true, text: "Check-in disponible" };
    if (now < o) return { open: false, text: "Abre el " + fmtDeparture(o) };
    if (now > c) return { open: false, text: "Cerrado desde el " + fmtDeparture(c) };
    return { open: true, text: "Abierto hasta el " + fmtDeparture(c) };
  }

  $("ci-form").addEventListener("submit", async (e) => {
    e.preventDefault();
    const code = $("ci-code").value.trim().toUpperCase();
    $("ci-pass").hidden = true;
    $("ci-panel").hidden = true;
    stopHold();
    if (!code) { setMsg($("ci-msg"), "Escriba el código de reserva que recibió al comprar.", "err"); $("ci-code").focus(); return; }
    setMsg($("ci-msg"), "Buscando su reserva…", "info");
    try {
      const r = await api("/reservations/" + encodeURIComponent(code));
      if (r.status === 404) { setMsg($("ci-msg"), "No encontramos esa reserva. Revise el código e intente de nuevo.", "err"); return; }
      if (!r.ok) { setMsg($("ci-msg"), detailText(r.body, "No se pudo consultar la reserva (error " + r.status + "). Intente de nuevo."), "err"); return; }
      res = r.body;
      seg = (res.segments || [])[0];
      if (!seg) { setMsg($("ci-msg"), "Esta reserva no tiene vuelos. Comuníquese con la aerolínea.", "err"); return; }
      if (res.status !== "CONFIRMED") { setMsg($("ci-msg"), "Esta reserva está cancelada, no se puede hacer check-in. Haga una nueva reserva.", "err"); return; }
      setMsg($("ci-msg"), "");
      await showReservation();
    } catch (_) {
      setMsg($("ci-msg"), "Sin conexión con el servidor. Revise su red e intente de nuevo.", "err");
    }
  });

  async function showReservation() {
    ownSeat = seg.seat || null;
    disputed = null;
    $("ci-title").textContent = (res.passenger_name || "") + ", vuelo " + (seg.flight_number || seg.flight_id);
    $("ci-sub").textContent = (seg.route || "") + " · clase " + fareName(res.fare_class) + " · sale " +
      longDate(seg.flight_date) + (seg.departure_time ? ", " + String(seg.departure_time).slice(0, 5) : "");
    const w = windowState();
    const badge = $("ci-window");
    badge.textContent = (w.open ? "Check-in abierto. " : "Check-in cerrado. ") + w.text;
    badge.className = "badge " + (w.open ? "open" : "closed");
    $("ci-panel").hidden = false;
    if (seg.checked_in) { await confirmCheckin(true); return; }
    $("ci-seats").hidden = !w.open;
    if (!w.open) {
      setMsg($("ci-msg"), w.text.startsWith("Abre") ? "Todavía no puede hacer check-in. Vuelva cuando abra, 24 h antes de la salida." : "El check-in ya cerró. Acérquese al mostrador del aeropuerto.", "info");
      return;
    }
    setMsg($("ci-seat-msg"), ownSeat ? "Su silla es la " + ownSeat + ". Puede confirmar o escoger otra." : "Toque una silla libre para bloquearla durante 5 minutos.", "info");
    updateConfirm();
    await refreshSeats();
    $("ci-seats").scrollIntoView({ behavior: reduceMotion ? "auto" : "smooth", block: "start" });
  }

  async function pick(no) {
    if (no === ownSeat && !held) return;
    setMsg($("ci-seat-msg"), "Bloqueando la silla " + no + "…", "info");
    try {
      const r = await api("/reservations/" + encodeURIComponent(res.reservation_id) + "/checkin/hold", {
        method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ seat: no }),
      });
      if (r.ok) {
        disputed = null;
        held = no;
        const exp = r.body && r.body.expires_at ? new Date(r.body.expires_at).getTime() : NaN;
        holdEnd = !isNaN(exp) && exp > Date.now() ? exp : Date.now() + 300000;
        clearInterval(holdTimer);
        holdTimer = setInterval(tick, 1000);
        $("ci-hold-seat").textContent = no;
        $("ci-hold").hidden = false;
        tick();
        setMsg($("ci-seat-msg"), "Silla " + no + " bloqueada para usted. Confirme el check-in antes de que termine la cuenta regresiva.", "ok");
        updateConfirm();
        draw();
      } else if (r.status === 409) {
        disputed = no;
        setMsg($("ci-seat-msg"), "Alguien tomó la silla " + no + " antes que usted. Elija otra silla libre" + (held ? "; su silla " + held + " sigue bloqueada." : "."), "err");
        await refreshSeats();
        const el = $("ci-map").querySelector('[aria-label^="Silla ' + no + ',"]');
        if (el) el.classList.add("disputed");
      } else {
        setMsg($("ci-seat-msg"), detailText(r.body, "No se pudo bloquear la silla (error " + r.status + "). Intente con otra."), "err");
      }
    } catch (_) {
      setMsg($("ci-seat-msg"), "Sin conexión con el servidor. Intente de nuevo.", "err");
    }
  }

  $("ci-confirm").addEventListener("click", () => confirmCheckin(false));

  async function confirmCheckin(silent) {
    const seat = held || ownSeat;
    const btn = $("ci-confirm");
    btn.disabled = true;
    if (!silent) setMsg($("ci-seat-msg"), "Confirmando check-in…", "info");
    try {
      const r = await api("/reservations/" + encodeURIComponent(res.reservation_id) + "/checkin", {
        method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(seat ? { seat } : {}),
      });
      if (r.ok) { stopHold(); setMsg($("ci-seat-msg"), ""); showBoardingPass(r.body); return; }
      if (r.status === 409 && seat && !silent && /silla/i.test(detailText(r.body, "silla"))) {
        disputed = seat;
        stopHold();
        setMsg($("ci-seat-msg"), detailText(r.body, "La silla " + seat + " ya no está disponible.") + " Elija otra silla libre.", "err");
        await refreshSeats();
      } else {
        setMsg(silent ? $("ci-msg") : $("ci-seat-msg"), detailText(r.body, "No se pudo completar el check-in (error " + r.status + "). Intente de nuevo."), "err");
      }
    } catch (_) {
      setMsg(silent ? $("ci-msg") : $("ci-seat-msg"), "Sin conexión con el servidor. Su check-in no se confirmó; intente de nuevo.", "err");
    }
    updateConfirm();
  }

  function showBoardingPass(bp) {
    const route = String(bp.route || seg.route || "").split("-");
    $("bp-o").textContent = route[0] || "";
    $("bp-d").textContent = route[1] || "";
    $("bp-name").textContent = bp.passenger_name || res.passenger_name || "";
    $("bp-flight").textContent = bp.flight_number || seg.flight_number || bp.flight_id;
    $("bp-dep").textContent = fmtDeparture(bp.departure) ;
    $("bp-fare").textContent = fareName(bp.fare_class || res.fare_class) + (bp.cabin ? ", cabina " + cabinName(bp.cabin) : "");
    $("bp-seat").textContent = bp.seat || "--";
    $("bp-group").textContent = bp.boarding_group || GROUPS[res.fare_class] || "";
    $("ci-panel").hidden = true;
    setMsg($("ci-msg"), "");
    $("ci-pass").hidden = false;
    $("ci-pass").scrollIntoView({ behavior: reduceMotion ? "auto" : "smooth", block: "start" });
  }

  if ($("ci-code").value) $("ci-form").requestSubmit();
}

// ------------------------------ Panel gerencial ------------------------------
const SEATS = 100; // 25 filas x 4 sillas: cada silla = 1 % de la capacidad

function initManager() {
  const map = $("seatmap");
  for (let i = 0; i < SEATS; i++) map.appendChild(document.createElement("i"));
  const cache = new Map(); // "RUTA|YYYY-MM" -> respuesta (o null si 404)
  let current = { route: "", year: "" };

  async function occupancy(route, month) {
    const key = route + "|" + month;
    if (cache.has(key)) { const b = cache.get(key); return { status: b ? 200 : 404, ok: !!b, body: b }; }
    const r = await api("/analytics/occupancy?" + new URLSearchParams({ route, month }));
    if (r.ok) cache.set(key, r.body); else if (r.status === 404) cache.set(key, null);
    return r;
  }

  function paintSeats(occ) {
    const sold = Math.round(Math.max(0, Math.min(1, occ)) * SEATS);
    const seats = map.children;
    for (let i = 0; i < SEATS; i++) {
      seats[i].classList.toggle("sold", i < sold);
      seats[i].style.transitionDelay = reduceMotion ? "0ms" : (i * 6) + "ms";
    }
    map.setAttribute("aria-label", sold + " de " + SEATS + " sillas vendidas");
  }

  async function load(route, month) {
    const val = $("occupancy-value");
    $("occ-route").textContent = route;
    $("occ-month").textContent = monthLabel(month);
    $("occupancy-detail").textContent = "";
    val.textContent = "…";
    val.classList.add("loading");
    setMsg($("manager-msg"), "Consultando Athena…", "info");
    try {
      const r = await occupancy(route, month);
      val.classList.remove("loading");
      if (r.ok) {
        val.textContent = (r.body.occupancy * 100).toFixed(3) + "%";
        $("occupancy-detail").textContent = r.body.seats_sold.toLocaleString("es-CO") + " sillas vendidas de " +
          r.body.seats_capacity.toLocaleString("es-CO") + " ofrecidas en el mes";
        $("seat-scale").hidden = false;
        setMsg($("manager-msg"), "");
        paintSeats(r.body.occupancy);
      } else {
        val.textContent = "--";
        paintSeats(0);
        $("seat-scale").hidden = true;
        const txt = r.status === 404 ? "No hay vuelos de " + route + " en " + monthLabel(month) + ". Revise la ruta o pruebe otro mes."
          : r.status === 503 ? "Athena no respondió. Espere unos segundos y consulte de nuevo."
          : detailText(r.body, "Error " + r.status);
        setMsg($("manager-msg"), txt, "err");
      }
    } catch (_) {
      val.classList.remove("loading");
      val.textContent = "--";
      setMsg($("manager-msg"), "Sin conexión con el servidor. Intente de nuevo.", "err");
    }
    markMonth(month);
    const year = month.slice(0, 4);
    if (current.route !== route || current.year !== year) {
      current = { route, year };
      loadYear(route, year);
    }
  }

  // Los 12 meses del año de la ruta, como barras; máximo 3 consultas a la vez
  async function loadYear(route, year) {
    $("year-label").textContent = year;
    const bars = $("year-bars");
    bars.innerHTML = "";
    const items = MONTHS.map((name, i) => {
      const m = year + "-" + String(i + 1).padStart(2, "0");
      const li = document.createElement("li");
      const b = document.createElement("button");
      b.type = "button";
      b.dataset.month = m;
      b.innerHTML = "<span class=\"bar\"><i></i></span><span class=\"pct\">…</span><span class=\"mon\"></span>";
      b.querySelector(".mon").textContent = name;
      b.setAttribute("aria-label", name + " " + year + ": cargando");
      b.addEventListener("click", () => {
        $("month").value = m;
        load(route, m);
      });
      li.appendChild(b);
      bars.appendChild(li);
      return { m, b };
    });
    $("year").hidden = false;
    markMonth($("month").value.trim());
    let next = 0;
    async function worker() {
      while (next < items.length) {
        const { m, b } = items[next++];
        if (current.route !== route || current.year !== year) return;
        let r;
        try { r = await occupancy(route, m); } catch (_) { r = { ok: false }; }
        if (r.ok) {
          const p = r.body.occupancy * 100;
          b.querySelector("i").style.setProperty("--h", Math.max(0, Math.min(100, p)) + "%");
          b.querySelector(".pct").textContent = p.toFixed(1);
          b.setAttribute("aria-label", b.querySelector(".mon").textContent + " " + year + ": " + p.toFixed(1) + " % de ocupación");
        } else {
          b.classList.add("empty");
          b.disabled = true;
          b.querySelector(".pct").textContent = "sin datos";
          b.setAttribute("aria-label", b.querySelector(".mon").textContent + " " + year + ": sin datos");
        }
      }
    }
    await Promise.all([worker(), worker(), worker()]);
  }

  function markMonth(month) {
    document.querySelectorAll("#year-bars button").forEach((b) => b.classList.toggle("on", b.dataset.month === month));
  }

  function monthLabel(month) {
    const [y, m] = month.split("-");
    return (MONTHS[Number(m) - 1] || m) + " " + y;
  }

  $("route").addEventListener("input", () => {
    $("route").value = $("route").value.toUpperCase().replace(/[^A-Z-]/g, "");
  });

  $("manager-form").addEventListener("submit", (e) => {
    e.preventDefault();
    const route = $("route").value.trim().toUpperCase().replace(/\s+/g, "");
    const month = $("month").value.trim();
    if (!/^[A-Z]{3}-[A-Z]{3}$/.test(route) || !/^\d{4}-(0[1-9]|1[0-2])$/.test(month)) {
      setMsg($("manager-msg"), "Escriba la ruta como BOG-MDE y el mes como 2024-03.", "err");
      return;
    }
    load(route, month);
  });
}

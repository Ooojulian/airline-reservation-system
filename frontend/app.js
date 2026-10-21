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

function money(cents) {
  return (cents / 100).toLocaleString("es-CO", { style: "currency", currency: "COP", maximumFractionDigits: 0 });
}

function longDate(iso) {
  const [y, m, d] = String(iso).slice(0, 10).split("-").map(Number);
  return new Date(y, m - 1, d).toLocaleDateString("es-CO", { weekday: "short", day: "numeric", month: "short", year: "numeric" });
}

// ------------------------------ Cliente ------------------------------
function initClient() {
  let selected = null;
  const booking = $("booking");

  // Nombre de la ciudad bajo cada código IATA
  [["origin", "origin-city", "Elija el aeropuerto de salida"], ["destination", "destination-city", "Elija el aeropuerto de llegada"]]
    .forEach(([inp, out, empty]) => {
      $(inp).addEventListener("input", () => {
        const v = $(inp).value.replace(/[^a-zA-Z]/g, "").toUpperCase();
        $(inp).value = v;
        $(out).textContent = v.length < 3 ? empty : (CITIES[v] || "Aeropuerto " + v);
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
      time.textContent = f.departure_time ? String(f.departure_time).slice(0, 5) : "--:--";

      const id = document.createElement("span");
      id.className = "f-id";
      id.innerHTML = "<strong></strong><small></small>";
      id.children[0].textContent = f.flight_number || f.flight_id;
      id.children[1].textContent = f.route || "";

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
      price.textContent = money(f.price_cents);

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
      const r = await api("/reservations", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ flight_id: selected.flight_id, passenger_name: name, fare_class: $("fare").value }),
      });
      if (r.status === 201 || r.status === 200) {
        showPass(r.body, name);
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

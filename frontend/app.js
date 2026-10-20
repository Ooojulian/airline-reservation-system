// Frontend estático sin build. Todas las llamadas van a /api/ (nginx -> API :8000).
const API = "/api";
const $ = (id) => document.getElementById(id);

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

// ------------------------------ Cliente ------------------------------
function initClient() {
  let selected = null;

  $("search-form").addEventListener("submit", async (e) => {
    e.preventDefault();
    const o = $("origin").value.trim().toUpperCase();
    const d = $("destination").value.trim().toUpperCase();
    const date = $("date").value.trim();
    $("results").innerHTML = "";
    $("booking").hidden = true;
    if (!/^[A-Z]{3}$/.test(o) || !/^[A-Z]{3}$/.test(d) || !date) {
      setMsg($("search-msg"), "Ingrese origen y destino (3 letras) y una fecha.", "err");
      return;
    }
    setMsg($("search-msg"), "Buscando vuelos...", "info");
    const q = new URLSearchParams({ origin: o, destination: d, date });
    try {
      const r = await api("/flights/search?" + q);
      if (!r.ok) { setMsg($("search-msg"), detailText(r.body, "No se pudo buscar (error " + r.status + ")."), "err"); return; }
      if (!r.body.length) { setMsg($("search-msg"), "No hay vuelos con sillas disponibles para esa búsqueda.", "info"); return; }
      setMsg($("search-msg"), r.body.length + " vuelo(s) disponible(s).", "ok");
      renderFlights(r.body);
    } catch (_) {
      setMsg($("search-msg"), "Error de red. Intente de nuevo.", "err");
    }
  });

  function renderFlights(list) {
    const box = $("results");
    box.innerHTML = "";
    list.forEach((f) => {
      const row = document.createElement("div");
      row.className = "flight";
      row.setAttribute("data-testid", "flight-row");
      const info = document.createElement("div");
      const hora = f.departure_time ? String(f.departure_time).slice(0, 5) : "";
      info.innerHTML = "<strong></strong><span></span>";
      info.children[0].textContent = f.flight_id + (f.route ? "  " + f.route : "");
      info.children[1].textContent = [hora, f.seats_available + " sillas", money(f.price_cents)].filter(Boolean).join(" · ");
      const b = document.createElement("button");
      b.type = "button";
      b.className = "primary";
      b.textContent = "Reservar";
      b.setAttribute("data-testid", "reserve-button");
      b.addEventListener("click", () => {
        selected = f;
        $("booking-flight").textContent = f.flight_id;
        $("confirmation").hidden = true;
        setMsg($("booking-msg"), "");
        $("booking").hidden = false;
        $("passenger").focus();
      });
      row.append(info, b);
      box.appendChild(row);
    });
  }

  $("booking-form").addEventListener("submit", async (e) => {
    e.preventDefault();
    const name = $("passenger").value.trim();
    if (!selected) return;
    if (!name) { setMsg($("booking-msg"), "Escriba el nombre del pasajero.", "err"); return; }
    const btn = e.submitter || document.querySelector("[data-testid=confirm-submit]");
    btn.disabled = true;
    setMsg($("booking-msg"), "Procesando reserva...", "info");
    try {
      const r = await api("/reservations", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ flight_id: selected.flight_id, passenger_name: name, fare_class: $("fare").value }),
      });
      if (r.status === 201 || r.status === 200) {
        setMsg($("booking-msg"), "");
        $("confirmation-id").textContent = r.body.reservation_id;
        $("confirmation").hidden = false;
      } else if (r.status === 409) {
        setMsg($("booking-msg"), "Lo sentimos: ya no quedan sillas en este vuelo.", "err");
      } else if (r.status === 404) {
        setMsg($("booking-msg"), "El vuelo ya no existe.", "err");
      } else {
        setMsg($("booking-msg"), detailText(r.body, "No se pudo reservar (error " + r.status + ")."), "err");
      }
    } catch (_) {
      setMsg($("booking-msg"), "Error de red. Intente de nuevo.", "err");
    } finally {
      btn.disabled = false;
    }
  });
}

// ------------------------------ Panel gerencial ------------------------------
function initManager() {
  $("manager-form").addEventListener("submit", async (e) => {
    e.preventDefault();
    const route = $("route").value.trim().toUpperCase();
    const month = $("month").value.trim();
    const val = $("occupancy-value");
    $("occupancy-detail").textContent = "";
    if (!/^[A-Z]{3}-[A-Z]{3}$/.test(route) || !/^\d{4}-(0[1-9]|1[0-2])$/.test(month)) {
      setMsg($("manager-msg"), "Use ruta AAA-BBB y mes YYYY-MM.", "err");
      return;
    }
    val.textContent = "...";
    setMsg($("manager-msg"), "Consultando...", "info");
    try {
      const r = await api("/analytics/occupancy?" + new URLSearchParams({ route, month }));
      if (r.ok) {
        val.textContent = (r.body.occupancy * 100).toFixed(3) + "%";
        $("occupancy-detail").textContent = r.body.seats_sold.toLocaleString("es-CO") + " sillas vendidas de " + r.body.seats_capacity.toLocaleString("es-CO");
        setMsg($("manager-msg"), "");
      } else {
        val.textContent = "--";
        const txt = r.status === 404 ? "No hay datos para esa ruta y mes."
          : r.status === 503 ? "El servicio analítico no está disponible por ahora."
          : detailText(r.body, "Error " + r.status);
        setMsg($("manager-msg"), txt, "err");
      }
    } catch (_) {
      val.textContent = "--";
      setMsg($("manager-msg"), "Error de red. Intente de nuevo.", "err");
    }
  });
}

/* MosTrans Predict — dispatcher BI */

const RISK_COLOR = { green: "#0d7a4f", yellow: "#c47a00", red: "#c8102e" };
const STATUS_RU = { on_route: "в пути", at_stop: "остановка", break: "обед/простой" };

const state = {
  vehicles: [],
  incidents: [],
  routes: [],
  messages: {},
  metrics: {},
  markers: new Map(),
  tracks: new Map(),
  routeLayers: [],
  selectedVehicle: "",
};

const map = L.map("map", { zoomControl: true, attributionControl: true }).setView([55.76, 37.58], 12);
L.tileLayer("https://tile.openstreetmap.org/{z}/{x}/{y}.png", {
  maxZoom: 19,
  attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a>',
}).addTo(map);

function el(id) {
  return document.getElementById(id);
}

function fmtSec(s) {
  const n = Math.round(Number(s) || 0);
  if (Math.abs(n) < 60) return `${n} с`;
  const m = Math.floor(Math.abs(n) / 60);
  const r = Math.abs(n) % 60;
  return `${n < 0 ? "−" : ""}${m} мин ${r} с`;
}

function pct(p) {
  return `${Math.round((Number(p) || 0) * 100)}%`;
}

setInterval(() => {
  el("clock").textContent = new Date().toLocaleTimeString("ru-RU", { hour12: false });
}, 1000);

function updateStatus(metrics) {
  const pill = el("mode-pill");
  const mode = metrics.mode || "live";
  const degraded = !!metrics.degraded || mode !== "live";
  pill.classList.toggle("degraded", degraded);
  pill.querySelector("span").textContent =
    mode === "historical" ? "historical" : degraded ? "деградация" : "live NDTP";
  el("lat-ms").textContent =
    metrics.last_predict_latency_ms != null
      ? `${Math.round(metrics.last_predict_latency_ms)} мс`
      : "—";
  el("alerts-n").textContent = state.incidents.filter((i) => i.risk_level !== "green").length;
  if (metrics.ml_model) el("model-id").textContent = metrics.ml_model;
}

function drawRoutes(routes) {
  state.routeLayers.forEach((l) => map.removeLayer(l));
  state.routeLayers = [];
  const selWf = el("wf-route");
  const selFilter = el("filter-route");
  const prevWf = selWf.value;
  const prevF = selFilter.value;
  selWf.innerHTML = "";
  selFilter.innerHTML = '<option value="">Все</option>';
  routes.forEach((r) => {
    const pts = (r.points || []).map((p) => [p.lat, p.lon]);
    if (pts.length >= 2) {
      const layer = L.polyline(pts, {
        color: RISK_COLOR[r.risk_level] || "#3DB8C5",
        weight: 4,
        opacity: 0.7,
      }).addTo(map);
      layer.bindTooltip(r.name || r.route_id);
      layer.on("click", () => openRoute(r.route_id));
      state.routeLayers.push(layer);
    }
    const o1 = document.createElement("option");
    o1.value = r.route_id;
    o1.textContent = r.name || r.route_id;
    selWf.appendChild(o1);
    const o2 = o1.cloneNode(true);
    selFilter.appendChild(o2);
  });
  if (prevWf) selWf.value = prevWf;
  if (prevF) selFilter.value = prevF;

  el("routes").innerHTML = routes
    .map((r) => {
      const c = RISK_COLOR[r.risk_level] || "#8fa3b8";
      return `<button type="button" class="route" data-id="${r.route_id}">
        <div class="left"><i class="rk" style="background:${c}"></i><span class="name">${r.name || r.route_id}</span></div>
        <span class="prob">${pct(r.max_prob || 0)}</span>
      </button>`;
    })
    .join("");
  el("routes").querySelectorAll(".route").forEach((btn) => {
    btn.addEventListener("click", () => openRoute(btn.dataset.id));
  });
}

function vehicleIcon(risk) {
  return L.divIcon({
    className: "",
    html: `<div class="vehicle-icon" style="background:${RISK_COLOR[risk] || RISK_COLOR.green}"></div>`,
    iconSize: [12, 12],
    iconAnchor: [6, 6],
  });
}

function upsertMarkers(vehicles) {
  const seen = new Set();
  vehicles.forEach((v) => {
    seen.add(v.vehicle_id);
    let m = state.markers.get(v.vehicle_id);
    if (!m) {
      m = L.marker([v.lat, v.lon], { icon: vehicleIcon(v.risk_level) }).addTo(map);
      m.on("click", () => openIncident(v.vehicle_id));
      state.markers.set(v.vehicle_id, m);
    } else {
      m.setLatLng([v.lat, v.lon]);
      m.setIcon(vehicleIcon(v.risk_level));
    }
    m.bindTooltip(`${v.vehicle_id} · ${STATUS_RU[v.status] || ""} · ${pct(v.delay_prob)}`);

    const trackPts = (v.track || []).map((p) => [p.lat, p.lon]);
    let tr = state.tracks.get(v.vehicle_id);
    if (trackPts.length >= 2) {
      if (!tr) {
        tr = L.polyline(trackPts, {
          color: RISK_COLOR[v.risk_level] || "#3DB8C5",
          weight: 3,
          opacity: 0.85,
        }).addTo(map);
        state.tracks.set(v.vehicle_id, tr);
      } else {
        tr.setLatLngs(trackPts);
        tr.setStyle({ color: RISK_COLOR[v.risk_level] || "#3DB8C5" });
      }
    }
  });
  for (const [id, m] of state.markers) {
    if (!seen.has(id)) {
      map.removeLayer(m);
      state.markers.delete(id);
      const tr = state.tracks.get(id);
      if (tr) {
        map.removeLayer(tr);
        state.tracks.delete(id);
      }
    }
  }
}

function filteredVehicles() {
  const route = el("filter-route").value;
  const risk = el("filter-risk").value;
  return state.vehicles.filter((v) => {
    if (route && v.route_id !== route) return false;
    if (risk && v.risk_level !== risk) return false;
    return true;
  });
}

function renderFleet() {
  const sorted = [...filteredVehicles()].sort((a, b) => (b.delay_prob || 0) - (a.delay_prob || 0));
  el("fleet-body").innerHTML = sorted
    .map(
      (v) => `<tr data-id="${v.vehicle_id}">
      <td>${v.vehicle_id}</td>
      <td>${v.route_id}</td>
      <td class="st ${v.status || ""}">${STATUS_RU[v.status] || "—"}</td>
      <td>${fmtSec(v.predicted_delay_sec || v.current_delay_sec)}</td>
      <td style="color:${RISK_COLOR[v.risk_level]}">${pct(v.delay_prob)}</td>
    </tr>`
    )
    .join("");
  el("fleet-body").querySelectorAll("tr").forEach((tr) => {
    tr.addEventListener("click", () => {
      selectVehicle(tr.dataset.id);
      openIncident(tr.dataset.id);
    });
  });

  const sel = el("msg-vehicle");
  const prev = sel.value || state.selectedVehicle;
  sel.innerHTML = state.vehicles
    .map((v) => `<option value="${v.vehicle_id}">${v.vehicle_id} · ${v.route_id}</option>`)
    .join("");
  if (prev) sel.value = prev;
}

function renderIncidents(incidents) {
  const list = incidents.filter((i) => i.risk_level !== "green" || i.delay_prob >= 0.35);
  el("inc-badge").textContent = String(list.length);
  const box = el("incidents");
  if (!list.length) {
    box.innerHTML = `<div class="empty">Критических отклонений нет</div>`;
    return;
  }
  box.innerHTML = list
    .map(
      (i) => `<button type="button" class="inc ${i.risk_level}" data-id="${i.vehicle_id}">
      <div class="inc-top"><span class="inc-id">${i.vehicle_id}</span><span class="inc-prob">${pct(i.delay_prob)}</span></div>
      <div class="inc-meta">${i.route_id} · ${STATUS_RU[i.status] || ""} · ${fmtSec(i.predicted_delay_sec)}</div>
    </button>`
    )
    .join("");
  box.querySelectorAll(".inc").forEach((btn) => {
    btn.addEventListener("click", () => {
      selectVehicle(btn.dataset.id);
      openIncident(btn.dataset.id);
    });
  });
}

function selectVehicle(id) {
  state.selectedVehicle = id;
  el("msg-vehicle").value = id;
  el("msg-hint").textContent = `Выбрано: ${id}`;
  const v = state.vehicles.find((x) => x.vehicle_id === id);
  const inc = state.incidents.find((x) => x.vehicle_id === id);
  if (!el("msg-text").value && (v || inc)) {
    const delay = Math.round(((inc || v).predicted_delay_sec || v?.current_delay_sec || 0) / 60);
    const sug = Math.round((inc || v).suggested_speed_kmh || 20);
    el("msg-text").placeholder = `Вы опаздываете на ~${delay} мин. Рекомендуем ${sug} км/ч`;
  }
}

async function sendMessage() {
  const vehicle_id = el("msg-vehicle").value;
  if (!vehicle_id) {
    el("msg-hint").textContent = "Сначала выберите ТС";
    return;
  }
  let text = el("msg-text").value.trim();
  if (!text) {
    const v = state.vehicles.find((x) => x.vehicle_id === vehicle_id);
    const delay = Math.round((v?.predicted_delay_sec || v?.current_delay_sec || 0) / 60);
    text = `Внимание: прогноз опоздания ~${delay} мин. Рекомендуемая скорость ${Math.round(v?.suggested_speed_kmh || 20)} км/ч.`;
  }
  const r = await Auth.fetch("/api/v1/messages", {
    method: "POST",
    body: JSON.stringify({ vehicle_id, text }),
  });
  if (r.ok) {
    el("msg-text").value = "";
    el("msg-hint").textContent = "Отправлено водителю ✓";
  } else {
    el("msg-hint").textContent = "Ошибка отправки";
  }
}

async function openRoute(routeId) {
  const r = await Auth.fetch(`/api/v1/routes/${encodeURIComponent(routeId)}/detail`);
  if (!r.ok) return;
  const d = await r.json();
  const stops = (d.stops || []).map((s, i) => `${i + 1}. ${s.name}`).join("<br>");
  const hist = d.history || {};
  el("drawer-card").innerHTML = `
    <h3>${d.name || d.route_id}</h3>
    <div class="sub">A → B: ${(d.point_a || {}).name || "—"} → ${(d.point_b || {}).name || "—"} · остановок: ${d.stops_count}</div>
    <div class="grid2">
      <div class="kv"><label>Первый рейс</label><strong>${d.first_trip}</strong></div>
      <div class="kv"><label>Последний рейс</label><strong>${d.last_trip}</strong></div>
      <div class="kv"><label>Live ср. delay</label><strong>${fmtSec(d.live?.avg_delay_sec)}</strong></div>
      <div class="kv"><label>ТС на линии</label><strong>${d.active_count || 0}</strong></div>
      <div class="kv"><label>День · ontime</label><strong>${hist.day?.ontime_pct ?? "—"}%</strong></div>
      <div class="kv"><label>Неделя · delay</label><strong>${fmtSec(hist.week?.avg_delay_sec)}</strong></div>
      <div class="kv"><label>Месяц · алерты</label><strong>${hist.month?.alerts ?? "—"}</strong></div>
      <div class="kv"><label>Ожид. скорость</label><strong>${d.expected_speed_kmh} км/ч</strong></div>
    </div>
    <p style="margin:0 0 6px;font-size:12px;color:var(--muted)">Остановки</p>
    <div class="stops">${stops || "—"}</div>
    <a class="nav" href="/stats?route=${encodeURIComponent(routeId)}" style="display:inline-block;margin-bottom:8px">Открыть в статистике →</a>
    <button type="button" class="close-btn" id="drawer-close">Закрыть</button>
  `;
  el("drawer").hidden = false;
  el("drawer-close").onclick = () => {
    el("drawer").hidden = true;
  };
  el("drawer").onclick = (e) => {
    if (e.target === el("drawer")) el("drawer").hidden = true;
  };
  // pan to route
  if (d.point_a) map.panTo([d.point_a.lat, d.point_a.lon]);
}

function openIncident(vehicleId) {
  const inc = state.incidents.find((i) => i.vehicle_id === vehicleId);
  const v = state.vehicles.find((x) => x.vehicle_id === vehicleId);
  if (!inc && !v) return;
  selectVehicle(vehicleId);
  const data = inc || v;
  if (v) map.panTo([v.lat, v.lon], { animate: true });
  const msgs = (state.messages[vehicleId] || []).slice(-5);
  el("drawer-card").innerHTML = `
    <h3>${data.vehicle_id}</h3>
    <div class="sub">${data.route_id} · ${STATUS_RU[data.status] || ""} · ${data.segment_name || ""} · ${data.model || "delay_catboost_ds"}</div>
    <div class="grid2">
      <div class="kv"><label>Вероятность</label><strong>${pct(data.delay_prob)}</strong></div>
      <div class="kv"><label>Прогноз</label><strong>${fmtSec(data.predicted_delay_sec || data.current_delay_sec)}</strong></div>
      <div class="kv"><label>Реком. скорость</label><strong>${Math.round(data.suggested_speed_kmh || 20)} км/ч</strong></div>
      <div class="kv"><label>Горизонт</label><strong>${Math.round((data.horizon_sec || 780) / 60)} мин</strong></div>
    </div>
    <div class="rec"><b>Рекомендация:</b> ${data.recommendation || "—"}</div>
    <p style="margin:0 0 6px;font-size:12px;color:var(--muted)">Последние сообщения</p>
    <div class="stops">${
      msgs.length
        ? msgs.map((m) => `${new Date(m.ts * 1000).toLocaleTimeString("ru-RU")}: ${m.text}`).join("<br>")
        : "Пока нет — напишите в панели справа «Сообщение водителю»"
    }</div>
    <button type="button" class="close-btn" id="drawer-close">Закрыть</button>
  `;
  el("drawer").hidden = false;
  el("drawer-close").onclick = () => {
    el("drawer").hidden = true;
  };
}

function applySnapshot(msg) {
  if (!msg || msg.type === "ping") return;
  state.vehicles = msg.vehicles || [];
  state.incidents = msg.incidents || [];
  state.routes = msg.routes || [];
  state.messages = msg.messages || {};
  state.metrics = msg.metrics || {};
  updateStatus(state.metrics);
  drawRoutes(state.routes);
  upsertMarkers(state.vehicles);
  renderIncidents(state.incidents);
  renderFleet();
}

async function pollFallback() {
  try {
    const r = await Auth.fetch("/api/v1/snapshot");
    if (r.ok) applySnapshot(await r.json());
  } catch (_) {}
}

function connectWs() {
  const ws = new WebSocket(Auth.wsUrl("/ws/live"));
  ws.onmessage = (ev) => {
    try {
      applySnapshot(JSON.parse(ev.data));
    } catch (_) {}
  };
  ws.onclose = () => setTimeout(connectWs, 2000);
  ws.onerror = () => ws.close();
}

el("msg-send-main").addEventListener("click", sendMessage);
el("filter-route").addEventListener("change", renderFleet);
el("filter-risk").addEventListener("change", renderFleet);
el("wf-run").addEventListener("click", async () => {
  const route_id = el("wf-route").value;
  const extra_vehicles = Number(el("wf-extra").value);
  const r = await Auth.fetch("/api/v1/what-if", {
    method: "POST",
    body: JSON.stringify({ route_id, extra_vehicles }),
  });
  const data = await r.json();
  el("wf-out").textContent = data.recommendation || data.message || "—";
});

(async () => {
  const user = await Auth.require("dispatcher");
  if (!user) return;
  if (el("user-name")) el("user-name").textContent = user.display_name || user.login;
  if (el("logout-btn")) {
    el("logout-btn").textContent = "Сменить роль";
    el("logout-btn").onclick = () => Auth.switchRole();
  }
  connectWs();
  pollFallback();
  setInterval(pollFallback, 5000);
})();

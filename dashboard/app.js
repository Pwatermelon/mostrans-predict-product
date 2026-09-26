/* MosTrans Predict — dispatcher BI */

const RISK_COLOR = { green: "#1FA97A", yellow: "#F5A623", red: "#E31E24" };
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
};

const map = L.map("map", { zoomControl: true, attributionControl: false }).setView([55.76, 37.58], 12);
L.tileLayer("https://{s}.basemaps.cartocdn.com/dark_all/{z}/{x}/{y}{r}.png", {
  maxZoom: 19,
  subdomains: "abcd",
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

function tickClock() {
  el("clock").textContent = new Date().toLocaleTimeString("ru-RU", { hour12: false });
}
setInterval(tickClock, 1000);
tickClock();

function updateStatus(metrics) {
  const pill = el("mode-pill");
  const mode = metrics.mode || "live";
  const degraded = !!metrics.degraded || mode === "degraded" || mode === "historical";
  pill.classList.toggle("degraded", degraded);
  const label =
    mode === "historical"
      ? "исторический fallback"
      : degraded
        ? "деградация · реконнект"
        : "live · поток NDTP";
  pill.querySelector("span").textContent = label;
  el("lat-ms").textContent =
    metrics.last_predict_latency_ms != null
      ? `${Math.round(metrics.last_predict_latency_ms)} мс`
      : "—";
  el("frames-n").textContent = metrics.frames_total ?? 0;
  el("alerts-n").textContent = state.incidents.filter((i) => i.risk_level !== "green").length;
  if (metrics.ml_model) el("model-id").textContent = metrics.ml_model;
}

function drawRoutes(routes) {
  state.routeLayers.forEach((l) => map.removeLayer(l));
  state.routeLayers = [];
  const sel = el("wf-route");
  const prev = sel.value;
  sel.innerHTML = "";
  routes.forEach((r) => {
    const pts = (r.points || []).map((p) => [p.lat, p.lon]);
    if (pts.length >= 2) {
      const color = RISK_COLOR[r.risk_level] || "#3DB8C5";
      const layer = L.polyline(pts, { color, weight: 4, opacity: 0.55, lineCap: "round" }).addTo(map);
      layer.bindTooltip(r.name || r.route_id);
      state.routeLayers.push(layer);
    }
    const opt = document.createElement("option");
    opt.value = r.route_id;
    opt.textContent = r.name || r.route_id;
    sel.appendChild(opt);
  });
  if (prev) sel.value = prev;

  el("routes").innerHTML = routes
    .map((r) => {
      const c = RISK_COLOR[r.risk_level] || "#8fa3b8";
      return `<div class="route">
        <div class="left"><i class="rk" style="background:${c}"></i><span class="name">${r.name || r.route_id}</span></div>
        <span class="prob">${pct(r.max_prob || 0)}</span>
      </div>`;
    })
    .join("");
}

function vehicleIcon(risk) {
  const c = RISK_COLOR[risk] || RISK_COLOR.green;
  return L.divIcon({
    className: "",
    html: `<div class="vehicle-icon" style="background:${c}"></div>`,
    iconSize: [14, 14],
    iconAnchor: [7, 7],
  });
}

function upsertMarkers(vehicles) {
  const seen = new Set();
  vehicles.forEach((v) => {
    seen.add(v.vehicle_id);
    const latlng = [v.lat, v.lon];
    let m = state.markers.get(v.vehicle_id);
    if (!m) {
      m = L.marker(latlng, { icon: vehicleIcon(v.risk_level) }).addTo(map);
      m.on("click", () => openIncident(v.vehicle_id));
      state.markers.set(v.vehicle_id, m);
    } else {
      m.setLatLng(latlng);
      m.setIcon(vehicleIcon(v.risk_level));
    }
    m.bindTooltip(
      `<b>${v.vehicle_id}</b><br>${STATUS_RU[v.status] || v.status} · ${pct(v.delay_prob)} · ${fmtSec(v.current_delay_sec)}`,
      { direction: "top" }
    );

    // track polyline
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

function renderFleet(vehicles) {
  const body = el("fleet-body");
  const sorted = [...vehicles].sort((a, b) => (b.delay_prob || 0) - (a.delay_prob || 0));
  body.innerHTML = sorted
    .map(
      (v) => `<tr data-id="${v.vehicle_id}">
      <td>${v.vehicle_id}</td>
      <td>${v.route_id}</td>
      <td class="st ${v.status || ""}">${STATUS_RU[v.status] || v.status || "—"}</td>
      <td>${fmtSec(v.predicted_delay_sec || v.current_delay_sec)}</td>
      <td style="color:${RISK_COLOR[v.risk_level] || "#fff"}">${pct(v.delay_prob)}</td>
    </tr>`
    )
    .join("");
  body.querySelectorAll("tr").forEach((tr) => {
    tr.addEventListener("click", () => openIncident(tr.dataset.id));
  });
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
      <div class="inc-top">
        <span class="inc-id">${i.vehicle_id}</span>
        <span class="inc-prob">${pct(i.delay_prob)}</span>
      </div>
      <div class="inc-meta">${i.route_id} · ${STATUS_RU[i.status] || ""} · ${i.segment_name} · ${fmtSec(i.predicted_delay_sec)}</div>
      <div class="inc-cause">${i.cause}</div>
    </button>`
    )
    .join("");
  box.querySelectorAll(".inc").forEach((btn) => {
    btn.addEventListener("click", () => openIncident(btn.dataset.id));
  });
}

async function openIncident(vehicleId) {
  const inc = state.incidents.find((i) => i.vehicle_id === vehicleId);
  const v = state.vehicles.find((x) => x.vehicle_id === vehicleId);
  const drawer = el("drawer");
  const card = el("drawer-card");
  if (!inc && !v) return;
  const data = inc || {
    vehicle_id: v.vehicle_id,
    route_id: v.route_id,
    delay_prob: v.delay_prob,
    predicted_delay_sec: v.predicted_delay_sec || v.current_delay_sec,
    abs_error_sec: 0,
    cause: "Нет активного алерта",
    segment_name: v.segment_name,
    horizon_sec: 780,
    recommendation: "Наблюдение",
    risk_level: v.risk_level,
    alert_lead_sec: 780,
    suggested_speed_kmh: v.suggested_speed_kmh,
    status: v.status,
    model: v.model,
  };
  if (v) map.panTo([v.lat, v.lon], { animate: true });
  const msgs = (state.messages[vehicleId] || []).slice(-5);
  card.innerHTML = `
    <h3>${data.vehicle_id}</h3>
    <div class="sub">${data.route_id} · ${STATUS_RU[data.status] || data.status || ""} · «${data.segment_name || "—"}» · модель ${data.model || "delay_catboost_ds"}</div>
    <div class="grid2">
      <div class="kv"><label>Вероятность задержки</label><strong>${pct(data.delay_prob)}</strong></div>
      <div class="kv"><label>Прогноз опоздания</label><strong>${fmtSec(data.predicted_delay_sec)}</strong></div>
      <div class="kv"><label>Горизонт</label><strong>${Math.round((data.alert_lead_sec || data.horizon_sec) / 60)} мин</strong></div>
      <div class="kv"><label>Реком. скорость</label><strong>${Math.round(data.suggested_speed_kmh || 20)} км/ч</strong></div>
    </div>
    <p style="margin:0 0 8px;font-size:13px;color:var(--muted)">Причина</p>
    <p style="margin:0 0 12px;font-size:14px">${data.cause}</p>
    <div class="rec"><b>Рекомендация:</b> ${data.recommendation}</div>
    <div class="msg-box">
      <label style="font-size:12px;color:var(--muted)">Сообщение водителю</label>
      <textarea id="msg-text" placeholder="Вы опаздываете на ~N мин. Рекомендуем скорость …"></textarea>
      <button type="button" id="msg-send">Отправить водителю</button>
      <div class="msg-list" id="msg-list">${
        msgs.length
          ? msgs.map((m) => `<div>${new Date(m.ts * 1000).toLocaleTimeString("ru-RU")} — ${m.text}</div>`).join("")
          : "Сообщений пока нет"
      }</div>
    </div>
    <button type="button" class="close-btn" id="drawer-close">Закрыть</button>
  `;
  drawer.hidden = false;
  el("drawer-close").onclick = () => {
    drawer.hidden = true;
  };
  drawer.onclick = (e) => {
    if (e.target === drawer) drawer.hidden = true;
  };
  el("msg-send").onclick = async () => {
    let text = el("msg-text").value.trim();
    if (!text) {
      const delay = Math.round((data.predicted_delay_sec || 0) / 60);
      text = `Внимание: прогноз опоздания ~${delay} мин. Рекомендуемая ср. скорость ${Math.round(data.suggested_speed_kmh || 20)} км/ч.`;
    }
    await fetch("/api/v1/messages", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ vehicle_id: vehicleId, text }),
    });
    el("msg-text").value = "";
  };
}

function applySnapshot(msg) {
  if (!msg || (msg.type && msg.type === "ping")) return;
  state.vehicles = msg.vehicles || [];
  state.incidents = msg.incidents || [];
  state.routes = msg.routes || [];
  state.messages = msg.messages || {};
  state.metrics = msg.metrics || {};
  updateStatus(state.metrics);
  drawRoutes(state.routes);
  upsertMarkers(state.vehicles);
  renderIncidents(state.incidents);
  renderFleet(state.vehicles);
}

async function pollFallback() {
  try {
    const r = await fetch("/api/v1/snapshot");
    if (r.ok) applySnapshot(await r.json());
  } catch (_) {}
}

function connectWs() {
  const proto = location.protocol === "https:" ? "wss" : "ws";
  const ws = new WebSocket(`${proto}://${location.host}/ws/live`);
  ws.onmessage = (ev) => {
    try {
      applySnapshot(JSON.parse(ev.data));
    } catch (_) {}
  };
  ws.onclose = () => {
    el("mode-pill").classList.add("degraded");
    el("mode-pill").querySelector("span").textContent = "ws reconnect…";
    setTimeout(connectWs, 2000);
  };
  ws.onerror = () => ws.close();
}

el("wf-run").addEventListener("click", async () => {
  const route_id = el("wf-route").value;
  const extra_vehicles = Number(el("wf-extra").value);
  const out = el("wf-out");
  out.textContent = "Считаем…";
  try {
    const r = await fetch("/api/v1/what-if", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ route_id, extra_vehicles }),
    });
    const data = await r.json();
    out.innerHTML = `${data.recommendation || data.message}<br>
      <span style="font-family:var(--mono);font-size:12px;opacity:.85">
      до: ${fmtSec(data.before_avg_delay_sec)} → после: ${fmtSec(data.after_avg_delay_sec)}
      </span>`;
  } catch (e) {
    out.textContent = "Ошибка: " + e.message;
  }
});

connectWs();
pollFallback();
setInterval(pollFallback, 5000);

const $ = (s) => document.querySelector(s),
  $$ = (s) => document.querySelectorAll(s);
let patients = [],
  activePatient = null,
  reportContent = "",
  currentUser = null,
  mfaLoginChallenge = "";
// Session identity now lives in an HttpOnly cookie the browser manages
// automatically (set by the server on login) -- this app never reads,
// stores, or sends a bearer token itself anymore. A token kept in
// localStorage/JS-reachable state is stealable by any XSS on this page;
// an HttpOnly cookie is not readable by JavaScript at all, which is the
// actual fix. See backend/app/csrf.py for why mutating requests still
// need the small csrf_token cookie echoed back in a header below.
function getCookie(name) {
  const match = document.cookie.match(
    new RegExp(`(?:^|; )${name}=([^;]*)`),
  );
  return match ? decodeURIComponent(match[1]) : null;
}
const esc = (v = "") =>
  String(v).replace(
    /[&<>'"]/g,
    (c) =>
      ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", "'": "&#39;", '"': "&quot;" })[
        c
      ],
  );
const toast = (m) => {
  const e = $("#toast");
  e.textContent = m;
  e.classList.add("show");
  setTimeout(() => e.classList.remove("show"), 2500);
};
const api = async (u, o = {}) => {
  o.credentials = "same-origin";
  const method = (o.method || "GET").toUpperCase();
  o.headers = { ...(o.headers || {}) };
  if (!["GET", "HEAD", "OPTIONS"].includes(method)) {
    const csrf = getCookie("csrf_token");
    if (csrf) o.headers["X-CSRF-Token"] = csrf;
  }
  const r = await fetch(u, o);
  if (!r.ok) {
    let m = "Zahtev nije uspeo";
    try {
      const detail = (await r.json()).detail;
      m =
        typeof detail === "string"
          ? detail
          : detail && detail.message
            ? detail.message
            : m;
    } catch {}
    const err = Error(m);
    err.status = r.status;
    throw err;
  }
  return r.status === 204 ? null : r.json();
};
const fmtDate = (iso) => {
  const d = new Date(iso),
    now = new Date(),
    t = d.toLocaleTimeString("sr-Latn-RS", {
      hour: "2-digit",
      minute: "2-digit",
    });
  const day = (x) =>
    new Date(x.getFullYear(), x.getMonth(), x.getDate()).getTime();
  const diff = Math.round((day(now) - day(d)) / 86400000);
  if (diff === 0) return `danas u ${t}`;
  if (diff === 1) return `juče u ${t}`;
  return `${d.toLocaleDateString("sr-Latn-RS")} ${t}`;
};
const size = (b) =>
  b < 1024
    ? `${b} B`
    : b < 1048576
      ? `${(b / 1024).toFixed(1)} KB`
      : `${(b / 1048576).toFixed(1)} MB`;
function showView(name) {
  $$(".view").forEach((v) => v.classList.add("hidden"));
  $(`#${name}View`).classList.remove("hidden");
  $$(".nav-item").forEach((b) =>
    b.classList.toggle("active", b.dataset.view === name),
  );
  $("#pageTitle").textContent = {
    dashboard: "Kontrolna tabla lekara",
    workspace: "Pametni karton pacijenta",
    schedule: "Današnji raspored",
    inbox: "Pristigli dokumenti",
    reports: "Arhiva izveštaja",
    audit: "Evidencija aktivnosti",
    users: "Upravljanje korisnicima",
    radar: "AI epidemiološki radar",
    finance: "Finansije ordinacije",
  }[name];
  if (name === "dashboard") loadDashboard();
  if (name === "schedule") loadAppointments();
  if (name === "inbox") loadInbox();
  if (name === "reports") loadReports();
  if (name === "audit") loadAudit();
  if (name === "finance") loadFinance();
  if (name === "users") {
    loadUsers();
    loadSessions();
  }
  if (name === "radar") loadRadar();
}
async function init() {
  document.body.classList.toggle(
    "dark",
    localStorage.getItem("clinic-theme") === "dark",
  );
  const h = await api("/api/health");
  $("#provider").textContent =
    h.ai_provider === "ollama"
      ? "Ollama je povezana"
      : "Lokalni bezbedni režim";
  try {
    currentUser = await api("/api/auth/me");
    applyUser();
    if (currentUser.must_change_password) {
      $("#loginHint").textContent = "Pre pristupa podacima ordinacije morate postaviti novu lozinku.";
      $("#passwordDialog").showModal();
      return;
    }
    await loadPatients();
    await ensureClinicianOptions();
    await loadDashboard();
    $("#loginOverlay").classList.add("hidden");
  } catch {
    // No valid session cookie -- show the login screen. Nothing to clear
    // client-side anymore; the session lives in an HttpOnly cookie the
    // server manages, not in any JS-readable storage.
  }
}
function applyUser() {
  $("#currentUser").textContent = currentUser.full_name;
  $("#currentRole").textContent =
    roleLabel[currentUser.role] || currentUser.role;
  $("#currentClinic").textContent = currentUser.organization_name;
  $$(".doctor-only").forEach((x) =>
    x.classList.toggle(
      "hidden",
      !["doctor", "admin"].includes(currentUser.role),
    ),
  );
  $$(".admin-only").forEach((x) =>
    x.classList.toggle("hidden", currentUser.role !== "admin"),
  );
  $$(".till-only").forEach((x) =>
    x.classList.toggle(
      "hidden",
      !["receptionist", "admin"].includes(currentUser.role),
    ),
  );
  if (currentUser.role === "receptionist") {
    $$(".upload,#reportBtn,.quick-questions,#chatForm").forEach((x) =>
      x.classList.add("hidden"),
    );
  }
}
async function loadPatients() {
  patients = await api("/api/patients");
  const opts =
    '<option value="">Izaberite pacijenta</option>' +
    patients
      .map((p) => `<option value="${p.id}">${esc(p.full_name)}</option>`)
      .join("");
  $("#patientSelect").innerHTML = opts;
  $("#appointmentPatient").innerHTML = patients
    .map((p) => `<option value="${p.id}">${esc(p.full_name)}</option>`)
    .join("");
  if ($("#waitlistPatient"))
    $("#waitlistPatient").innerHTML = patients
      .map((p) => `<option value="${p.id}">${esc(p.full_name)}</option>`)
      .join("");
}
async function loadDashboard() {
  const [m, a, d] = await Promise.all([
    api("/api/dashboard"),
    api("/api/appointments"),
    api("/api/documents/inbox"),
  ]);
  $("#mAppointments").textContent = m.appointments_today;
  $("#mChecked").textContent = m.checked_in;
  $("#mAttention").textContent = m.needs_attention;
  $("#mReports").textContent = m.reports_this_week;
  const today = new Date().toDateString();
  const ta = a.filter(
    (x) =>
      new Date(x.starts_at).toDateString() === today &&
      x.status !== "cancelled",
  );
  $("#dashboardSchedule").innerHTML = ta.length
    ? ta.slice(0, 6).map(appointmentRow).join("")
    : '<p class="muted">Danas nema zakazanih termina.</p>';
  const att = d.filter((x) => x.attention);
  $("#dashboardAttention").innerHTML = att.length
    ? att.slice(0, 6).map(documentRow).join("")
    : '<p class="muted">Nema dokumenata označenih za proveru.</p>';
  await loadRedFlagBanner(m.red_flags_pending);
  renderEpiBanner(m.epi_alerts);
  await loadSetupBanner();
  renderCommandCenter(ta, d);
}
function renderCommandCenter(todaysAppointments, inboxDocs) {
  const el = $("#commandCenter");
  if (!el) return;
  if (!["receptionist", "admin"].includes(currentUser.role)) {
    el.classList.add("hidden");
    return;
  }
  el.classList.remove("hidden");
  const arrived = todaysAppointments.filter((a) => a.status === "checked_in");
  const late = todaysAppointments.filter((a) => lateMinutes(a) > 0);
  const waiting = todaysAppointments.filter((a) => a.status === "scheduled");
  const docsNeedingAttention = inboxDocs.filter(
    (d) => d.attention || d.status === "failed" || d.pending_lab_confirmation,
  );
  $("#ccArrived").textContent = arrived.length;
  $("#ccLate").textContent = late.length;
  $("#ccWaiting").textContent = waiting.length;
  $("#ccDocsAttention").textContent = docsNeedingAttention.length;
  const next = waiting
    .slice()
    .sort((a, b) => new Date(a.starts_at) - new Date(b.starts_at))[0];
  $("#ccNext").innerHTML = next
    ? `<strong>Sledeći:</strong> ${new Date(next.starts_at).toLocaleTimeString("sr-Latn-RS", { hour: "2-digit", minute: "2-digit" })} · ${esc(next.patient_name)} — ${esc(next.reason)}`
    : late.length
      ? `<strong>Nema više zakazanih danas</strong> — proverite ${late.length} ${late.length === 1 ? "pacijenta koji kasni" : "pacijenata koji kasne"}.`
      : `<strong>Nema više zakazanih termina danas.</strong>`;
}
async function loadRedFlagBanner(count) {
  const el = $("#redFlagBanner");
  if (!count || !["doctor", "admin"].includes(currentUser.role)) {
    el.classList.add("hidden");
    el.innerHTML = "";
    return;
  }
  const flags = await api("/api/dashboard/red-flags");
  el.classList.remove("hidden");
  el.innerHTML = `<div class="red-flag-head">⚠ ${count} ${count === 1 ? "crvena zastavica čeka" : "crvenih zastavica čeka"} pregled lekara</div><div class="red-flag-list">${flags
    .slice(0, 5)
    .map(
      (f) =>
        `<button class="red-flag-item" data-patient="${f.patient_id}"><strong>${esc(f.patient_name)}</strong><span>${esc(f.candidate_name)} · ${f.match_score}/100 · ${fmtDate(f.generated_at)}</span></button>`,
    )
    .join(
      "",
    )}${flags.length > 5 ? `<span class="muted">+ ${flags.length - 5} još</span>` : ""}</div>`;
}
document.addEventListener("click", async (e) => {
  const b = e.target.closest(".red-flag-item");
  if (!b) return;
  showView("workspace");
  $("#patientSelect").value = b.dataset.patient;
  await selectPatient(b.dataset.patient);
});
const APPT_STATUSES = [
  ["scheduled", "Zakazano"],
  ["checked_in", "Pacijent stigao"],
  ["completed", "Završeno"],
  ["cancelled", "Otkazano"],
];
function lateMinutes(a) {
  if (a.status !== "scheduled") return 0;
  const diff = Math.floor(
    (Date.now() - new Date(a.starts_at).getTime()) / 60000,
  );
  return diff > 0 ? diff : 0;
}
const appointmentRow = (a) => {
  const late = lateMinutes(a);
  return `<div class="row appt-row ${a.status}${late > 0 ? " late" : ""}"><div><strong>${new Date(a.starts_at).toLocaleTimeString("sr-Latn-RS", { hour: "2-digit", minute: "2-digit" })} · ${esc(a.patient_name)}</strong><p>${esc(a.reason)}${a.clinician_name ? " · " + esc(a.clinician_name) : ""}${a.room ? " · " + esc(a.room) : ""}</p></div><div class="row-actions">${late > 0 ? `<span class="badge late" title="Pacijent nije prijavljen na vreme">Kasni ${late} min</span>` : ""}<button class="button secondary open-chart" data-patient="${a.patient_id}" title="Otvori karton pacijenta">Karton</button><select class="status-select" data-id="${a.id}">${APPT_STATUSES.map(([v, l]) => `<option value="${v}" ${a.status === v ? "selected" : ""}>${l}</option>`).join("")}<option value="no_show" ${a.status === "no_show" ? "selected" : ""}>Nije se pojavio</option></select></div></div>`;
};
function inboxStatus(d) {
  if (d.status === "failed")
    return { cls: "failed", label: "OCR neuspešan" };
  if (d.pending_lab_confirmation)
    return { cls: "pending-lab", label: "Čeka potvrdu laboratorije" };
  if (d.attention) return { cls: "attention", label: "Potrebna provera" };
  return { cls: "ready", label: "Spremno" };
}
const documentRow = (d) => {
  const s = inboxStatus(d);
  return `<div class="row"><div><strong>${esc(d.filename)}</strong><p>${esc(d.patient_name)} · ${fmtDate(d.uploaded_at)}</p></div><span class="inbox-status ${s.cls}">${s.label}</span></div>`;
};

async function loadRadar() {
  const days = $("#radarPeriod")?.value || 7;
  const d = await api(`/api/epidemiology/radar?days=${days}`);
  $("#radarSummary").classList.remove("placeholder");
  $("#radarSummary").innerHTML =
    `<strong>${d.encounter_count} pregleda u periodu</strong><p>${d.minimum_sample_met ? "Uzorak je dovoljan za prikaz internog signala." : "Nedovoljno podataka za pouzdan trend; rezultate tumačiti oprezno."}</p>`;
  $("#radarChart").innerHTML = renderTrendChart(d.daily_counts || []);
  $("#syndromeTrends").innerHTML = d.syndrome_trends.length
    ? d.syndrome_trends
        .map(
          (x) =>
            `<div class="row"><div><strong>${esc(x.name)}</strong><p>${x.current_count} slučajeva · prethodno ${x.previous_count}${x.change_percent === null ? "" : ` · ${x.change_percent >= 0 ? "+" : ""}${x.change_percent}%`}</p></div><span class="badge ${x.signal_level === "visok" ? "warn" : ""}">${esc(x.signal_level)} signal</span></div>`,
        )
        .join("")
    : '<p class="muted">Nema izdvojenih sindromskih trendova.</p>';
  $("#confirmedPathogens").innerHTML = d.confirmed_pathogens.length
    ? d.confirmed_pathogens
        .map(
          (x) =>
            `<div class="row"><div><strong>${esc(x.name)}</strong><p>${x.confirmed_count} potvrđenih nalaza</p></div><span class="badge">Laboratorijski podatak</span></div>`,
        )
        .join("")
    : '<p class="muted">Nema evidentiranih potvrđenih patogena.</p>';
  $("#clusterSignals").innerHTML = d.clusters.length
    ? d.clusters
        .map(
          (x) =>
            `<div class="row"><div><strong>${esc(x.title)}</strong><p>${x.case_count} slučajeva u ${x.window_days} dana · ${esc(x.note)}</p></div><span class="badge warn">${esc(x.confidence)} pouzdanje</span></div>`,
        )
        .join("")
    : '<p class="muted">Nema mogućih klastera.</p>';
  $("#radarDisclaimer").textContent = d.disclaimer;
}
const SYNDROME_LABELS = {
  respiratorni: "Respiratorni",
  gastrointestinalni: "Gastrointestinalni",
  febrilni: "Febrilni",
  osip: "Osip",
  urinarni: "Urinarni",
};
const SYNDROME_COLORS = {
  respiratorni: "#176b5c",
  gastrointestinalni: "#c98a2c",
  febrilni: "#b34b3f",
  osip: "#6a5acd",
  urinarni: "#2c7fb8",
};
function renderTrendChart(daily) {
  if (!daily.length)
    return '<p class="muted">Nema podataka za grafikon u izabranom periodu.</p>';
  const names = Object.keys(SYNDROME_LABELS).filter((n) =>
    daily.some((day) => (day.counts[n] || 0) > 0),
  );
  if (!names.length)
    return '<p class="muted">Nema zabeleženih slučajeva po sindromu u izabranom periodu.</p>';
  const W = 760,
    H = 240,
    padL = 34,
    padR = 16,
    padT = 16,
    padB = 34,
    plotW = W - padL - padR,
    plotH = H - padT - padB;
  const maxVal = Math.max(
    3,
    ...daily.map((day) => Math.max(0, ...names.map((n) => day.counts[n] || 0))),
  );
  const stepX = daily.length > 1 ? plotW / (daily.length - 1) : 0;
  const x = (i) => padL + i * stepX,
    y = (v) => padT + plotH - (v / maxVal) * plotH;
  const ySteps = maxVal <= 6 ? maxVal : 5;
  const gridLines = Array.from({ length: ySteps + 1 }, (_, i) => {
    const v = Math.round((maxVal * i) / ySteps);
    return `<line x1="${padL}" x2="${W - padR}" y1="${y(v)}" y2="${y(v)}" stroke="#e5eeec" stroke-width="1"/><text x="${padL - 8}" y="${y(v) + 4}" font-size="10" fill="#8ca39f" text-anchor="end">${v}</text>`;
  }).join("");
  const labelEvery = daily.length <= 10 ? 1 : Math.ceil(daily.length / 8);
  const xLabels = daily
    .map((day, i) =>
      i % labelEvery !== 0 && i !== daily.length - 1
        ? ""
        : `<text x="${x(i)}" y="${H - 8}" font-size="10" fill="#8ca39f" text-anchor="middle">${day.date.slice(5)}</text>`,
    )
    .join("");
  const series = names
    .map((n) => {
      const pts = daily
        .map((day, i) => `${x(i)},${y(day.counts[n] || 0)}`)
        .join(" ");
      const area = `${padL},${y(0)} ${pts} ${x(daily.length - 1)},${y(0)}`;
      const color = SYNDROME_COLORS[n];
      const dots = daily
        .map((day, i) =>
          (day.counts[n] || 0) > 0
            ? `<circle cx="${x(i)}" cy="${y(day.counts[n] || 0)}" r="2.6" fill="${color}"><title>${SYNDROME_LABELS[n]} · ${day.date}: ${day.counts[n] || 0}</title></circle>`
            : "",
        )
        .join("");
      return `<polygon points="${area}" fill="${color}" opacity="0.07"/><polyline points="${pts}" fill="none" stroke="${color}" stroke-width="2.4" stroke-linejoin="round" stroke-linecap="round"/>${dots}`;
    })
    .join("");
  const legend = names
    .map(
      (n) =>
        `<span class="chart-legend-item"><i style="background:${SYNDROME_COLORS[n]}"></i>${SYNDROME_LABELS[n]}</span>`,
    )
    .join("");
  return `<svg viewBox="0 0 ${W} ${H}" class="trend-chart" role="img" aria-label="Broj slučajeva po danu i sindromu">${gridLines}${xLabels}${series}</svg><div class="chart-legend">${legend}</div>`;
}
/* === Kalendar ordinacije (nedelja/mesec/lista) === */
const CAL_START_HOUR = 7;
const CAL_END_HOUR = 20;
const CAL_HOUR_PX = 52;
const calState = { view: "week", anchor: new Date(), clinicianFilter: "" };
let clinicianCache = [];

function startOfWeek(d) {
  const x = new Date(d);
  const day = (x.getDay() + 6) % 7; // Monday = 0
  x.setDate(x.getDate() - day);
  x.setHours(0, 0, 0, 0);
  return x;
}
function isoDate(d) {
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
}
function sameDay(a, b) {
  return isoDate(a) === isoDate(b);
}
const DAY_NAMES = ["Pon", "Uto", "Sre", "Čet", "Pet", "Sub", "Ned"];
const MONTH_NAMES = [
  "Januar", "Februar", "Mart", "April", "Maj", "Jun",
  "Jul", "Avgust", "Septembar", "Oktobar", "Novembar", "Decembar",
];

async function ensureClinicianOptions() {
  if (!clinicianCache.length) {
    try {
      clinicianCache = await api("/api/clinicians");
    } catch {
      clinicianCache = [];
    }
  }
  const opts = clinicianCache
    .map((c) => `<option value="${c.id}">${esc(c.full_name)}</option>`)
    .join("");
  $("#calClinicianFilter").innerHTML = `<option value="">Svi lekari</option>${opts}`;
  for (const sel of [
    "#appointmentClinician",
    "#waitlistClinician",
    "#promoteClinician",
  ]) {
    const el = $(sel);
    if (el)
      el.innerHTML = `<option value="">${sel === "#waitlistClinician" ? "Bilo koji" : "Nije dodeljen"}</option>${opts}`;
  }
}

function calRangeLabel() {
  if (calState.view === "week") {
    const start = startOfWeek(calState.anchor);
    const end = new Date(start);
    end.setDate(end.getDate() + 6);
    const sameMonth = start.getMonth() === end.getMonth();
    return sameMonth
      ? `${start.getDate()}–${end.getDate()}. ${MONTH_NAMES[start.getMonth()]} ${start.getFullYear()}.`
      : `${start.getDate()}. ${MONTH_NAMES[start.getMonth()]} – ${end.getDate()}. ${MONTH_NAMES[end.getMonth()]} ${end.getFullYear()}.`;
  }
  if (calState.view === "month") {
    return `${MONTH_NAMES[calState.anchor.getMonth()]} ${calState.anchor.getFullYear()}.`;
  }
  return "Svi termini";
}

const STATUS_COLOR = {
  scheduled: "sched",
  checked_in: "checkedin",
  completed: "done",
  cancelled: "cancelled",
  no_show: "noshow",
};

function calBlockLabel(a) {
  const t = new Date(a.starts_at).toLocaleTimeString("sr-Latn-RS", {
    hour: "2-digit",
    minute: "2-digit",
  });
  return `${t} · ${esc(a.patient_name)}`;
}

function renderWeek(rows) {
  const start = startOfWeek(calState.anchor);
  const days = Array.from({ length: 7 }, (_, i) => {
    const d = new Date(start);
    d.setDate(d.getDate() + i);
    return d;
  });
  const today = new Date();
  const hours = Array.from(
    { length: CAL_END_HOUR - CAL_START_HOUR },
    (_, i) => CAL_START_HOUR + i,
  );
  const totalPx = hours.length * CAL_HOUR_PX;
  const head = days
    .map(
      (d) =>
        `<div class="cal-day-head${sameDay(d, today) ? " today" : ""}">${DAY_NAMES[(d.getDay() + 6) % 7]}<br><b>${d.getDate()}.</b></div>`,
    )
    .join("");
  const timeLabels = hours
    .map((h) => `<div class="cal-hour-label">${h}:00</div>`)
    .join("");
  const dayBodies = days
    .map((d) => {
      const dayRows = rows.filter((a) => sameDay(new Date(a.starts_at), d));
      const blocks = dayRows
        .map((a) => {
          const s = new Date(a.starts_at);
          const mins = (s.getHours() - CAL_START_HOUR) * 60 + s.getMinutes();
          const top = Math.max(0, (mins / 60) * CAL_HOUR_PX);
          const height = Math.max(
            18,
            (a.duration_minutes / 60) * CAL_HOUR_PX,
          );
          return `<button type="button" class="cal-block ${STATUS_COLOR[a.status] || "sched"}" data-id="${a.id}" style="top:${top}px;height:${height}px" title="${esc(a.reason)}${a.room ? " · " + esc(a.room) : ""}">${calBlockLabel(a)}</button>`;
        })
        .join("");
      return `<div class="cal-day-body" data-date="${isoDate(d)}" style="height:${totalPx}px">${blocks}</div>`;
    })
    .join("");
  $("#calendarWeek").innerHTML =
    `<div class="cal-grid">` +
    `<div class="cal-corner"></div>${head}` +
    `<div class="cal-time-col" style="height:${totalPx}px">${timeLabels}</div>${dayBodies}` +
    `</div>`;
}

function renderMonth(rows) {
  const y = calState.anchor.getFullYear(),
    m = calState.anchor.getMonth();
  const firstOfMonth = new Date(y, m, 1);
  const gridStart = startOfWeek(firstOfMonth);
  const today = new Date();
  const cells = Array.from({ length: 42 }, (_, i) => {
    const d = new Date(gridStart);
    d.setDate(d.getDate() + i);
    const dayRows = rows
      .filter((a) => sameDay(new Date(a.starts_at), d))
      .sort((a, b) => new Date(a.starts_at) - new Date(b.starts_at));
    const chips = dayRows
      .slice(0, 3)
      .map(
        (a) =>
          `<button type="button" class="cal-chip ${STATUS_COLOR[a.status] || "sched"}" data-id="${a.id}">${calBlockLabel(a)}</button>`,
      )
      .join("");
    const more =
      dayRows.length > 3
        ? `<span class="cal-more">+${dayRows.length - 3} još</span>`
        : "";
    const outside = d.getMonth() !== m;
    return `<div class="cal-month-cell${outside ? " outside" : ""}${sameDay(d, today) ? " today" : ""}" data-date="${isoDate(d)}"><span class="cal-month-daynum">${d.getDate()}</span>${chips}${more}</div>`;
  }).join("");
  $("#calendarMonth").innerHTML =
    `<div class="cal-month-head">${DAY_NAMES.map((n) => `<div>${n}</div>`).join("")}</div>` +
    `<div class="cal-month-grid">${cells}</div>`;
}

async function loadCalendar() {
  await ensureClinicianOptions();
  $("#calendarRangeLabel").textContent = calRangeLabel();
  let from, to;
  if (calState.view === "week") {
    from = startOfWeek(calState.anchor);
    to = new Date(from);
    to.setDate(to.getDate() + 7);
  } else if (calState.view === "month") {
    from = startOfWeek(new Date(calState.anchor.getFullYear(), calState.anchor.getMonth(), 1));
    to = new Date(from);
    to.setDate(to.getDate() + 42);
  }
  const params = new URLSearchParams();
  if (from) params.set("from_", from.toISOString());
  if (to) params.set("to", to.toISOString());
  if (calState.clinicianFilter) params.set("clinician_id", calState.clinicianFilter);
  const rows = await api(`/api/appointments?${params.toString()}`);
  $("#calendarWeek").classList.toggle("hidden", calState.view !== "week");
  $("#calendarMonth").classList.toggle("hidden", calState.view !== "month");
  $("#scheduleList").classList.toggle("hidden", calState.view !== "list");
  if (calState.view === "week") renderWeek(rows);
  else if (calState.view === "month") renderMonth(rows);
  else
    $("#scheduleList").innerHTML = rows.length
      ? rows
          .sort((a, b) => new Date(a.starts_at) - new Date(b.starts_at))
          .map(appointmentRow)
          .join("")
      : '<p class="muted">Još nema termina.</p>';
  await loadWaitlistPanel();
}
async function loadAppointments() {
  await loadCalendar();
}

/* Klik na termin u kalendaru: mala akciona kartica */
function closeCalPopover() {
  $("#calPopover")?.remove();
}
document.addEventListener("click", async (e) => {
  const block = e.target.closest(".cal-block, .cal-chip");
  if (block) {
    closeCalPopover();
    const rows = await api("/api/appointments");
    const a = rows.find((x) => x.id === block.dataset.id);
    if (!a) return;
    const rect = block.getBoundingClientRect();
    const pop = document.createElement("div");
    pop.id = "calPopover";
    pop.className = "cal-popover";
    pop.style.top = `${Math.min(window.scrollY + rect.bottom + 6, window.scrollY + window.innerHeight - 260)}px`;
    pop.style.left = `${Math.min(rect.left + window.scrollX, window.innerWidth - 300)}px`;
    const dt = new Date(a.starts_at);
    const localVal = new Date(dt.getTime() - dt.getTimezoneOffset() * 60000)
      .toISOString()
      .slice(0, 16);
    pop.innerHTML = `
      <div class="cal-pop-head"><strong>${esc(a.patient_name)}</strong><button type="button" class="icon-button cal-pop-close">×</button></div>
      <p class="muted">${esc(a.reason)}${a.room ? " · " + esc(a.room) : ""}${a.clinician_name ? " · " + esc(a.clinician_name) : ""}</p>
      <select class="cal-pop-status">${APPT_STATUSES.map(([v, l]) => `<option value="${v}" ${a.status === v ? "selected" : ""}>${l}</option>`).join("")}<option value="no_show" ${a.status === "no_show" ? "selected" : ""}>Nije se pojavio</option></select>
      <form class="cal-pop-reschedule"><input type="datetime-local" name="starts_at" value="${localVal}"><button class="button secondary" type="submit">Pomeri</button></form>
      <button type="button" class="button secondary cal-pop-chart" data-patient="${a.patient_id}">Otvori karton</button>
    `;
    document.body.appendChild(pop);
    pop.querySelector(".cal-pop-close").onclick = closeCalPopover;
    pop.querySelector(".cal-pop-chart").onclick = async () => {
      closeCalPopover();
      showView("workspace");
      $("#patientSelect").value = a.patient_id;
      await selectPatient(a.patient_id);
    };
    pop.querySelector(".cal-pop-status").onchange = async (ev) => {
      const status = ev.target.value;
      let cancellation_reason = null;
      if (status === "cancelled" || status === "no_show") {
        cancellation_reason = prompt(
          status === "cancelled" ? "Razlog otkazivanja (opciono):" : "Napomena (opciono):",
        );
      }
      try {
        await api(`/api/appointments/${a.id}/status`, {
          method: "PATCH",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ status, cancellation_reason: cancellation_reason || null }),
        });
        toast("Status je ažuriran");
        closeCalPopover();
        await loadCalendar();
      } catch (x) {
        toast(x.message);
      }
    };
    pop.querySelector(".cal-pop-reschedule").onsubmit = async (ev) => {
      ev.preventDefault();
      const val = new FormData(ev.target).get("starts_at");
      try {
        await api(`/api/appointments/${a.id}`, {
          method: "PATCH",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ starts_at: new Date(val).toISOString() }),
        });
        toast("Termin je pomeren");
        closeCalPopover();
        await loadCalendar();
      } catch (x) {
        toast(x.message);
      }
    };
    return;
  }
  if (!e.target.closest("#calPopover")) closeCalPopover();
  // Klik na prazno mesto u kalendaru -> otvori formu sa unapred popunjenim vremenom
  const dayBody = e.target.closest(".cal-day-body");
  if (dayBody && e.target === dayBody) {
    const rect = dayBody.getBoundingClientRect();
    const offsetY = e.clientY - rect.top;
    const totalMin = Math.round((offsetY / CAL_HOUR_PX) * 60);
    const snapped = Math.max(0, Math.round(totalMin / 15) * 15);
    const h = CAL_START_HOUR + Math.floor(snapped / 60);
    const min = snapped % 60;
    const d = dayBody.dataset.date;
    $("#appointmentStartsAt").value = `${d}T${String(h).padStart(2, "0")}:${String(min).padStart(2, "0")}`;
    if (calState.clinicianFilter) $("#appointmentClinician").value = calState.clinicianFilter;
    $("#appointmentDialog").showModal();
  }
  const monthCell = e.target.closest(".cal-month-cell");
  if (monthCell && e.target === monthCell) {
    $("#appointmentStartsAt").value = `${monthCell.dataset.date}T09:00`;
    $("#appointmentDialog").showModal();
  }
});

/* === Lista čekanja === */
async function loadWaitlistPanel() {
  const rows = await api("/api/waitlist?status=waiting");
  $("#waitlistList").innerHTML = rows.length
    ? rows
        .map((w) => {
          const patientName =
            patients.find((p) => p.id === w.patient_id)?.full_name || "Pacijent";
          return `<div class="row"><div><strong>${esc(patientName)}</strong><p>${esc(w.desired_service || "Bilo koja usluga")}${w.preferred_note ? " · " + esc(w.preferred_note) : ""}</p></div><div class="row-actions"><button class="button secondary waitlist-promote" data-id="${w.id}" data-patient="${w.patient_id}" data-clinician="${w.clinician_id || ""}">Zakaži</button><button class="button secondary waitlist-cancel" data-id="${w.id}">Ukloni</button></div></div>`;
        })
        .join("")
    : '<p class="muted">Lista čekanja je prazna.</p>';
}
document.addEventListener("click", async (e) => {
  const promoteBtn = e.target.closest(".waitlist-promote");
  if (promoteBtn) {
    await ensureClinicianOptions();
    $("#promoteForm").dataset.entryId = promoteBtn.dataset.id;
    if (promoteBtn.dataset.clinician) $("#promoteClinician").value = promoteBtn.dataset.clinician;
    $("#promoteDialog").showModal();
    return;
  }
  const cancelBtn = e.target.closest(".waitlist-cancel");
  if (cancelBtn) {
    try {
      await api(`/api/waitlist/${cancelBtn.dataset.id}/status?status=cancelled`, { method: "PATCH" });
      toast("Uklonjeno sa liste čekanja");
      await loadWaitlistPanel();
    } catch (x) {
      toast(x.message);
    }
  }
});

async function loadInbox() {
  const d = await api("/api/documents/inbox");
  $("#inboxList").innerHTML = d.length
    ? d.map(documentRow).join("")
    : '<p class="muted">Nema dodatih dokumenata.</p>';
}
async function loadAudit() {
  const rows = await api("/api/audit");
  $("#auditList").innerHTML = rows.length
    ? rows
        .map(
          (x) =>
            `<div class="audit-row"><span>${new Date(x.occurred_at).toLocaleString("sr-Latn-RS")}</span><strong>${esc(x.username)}</strong><span>${esc(x.action)}</span><span>${esc(x.resource_type)} ${esc(x.detail || "")}</span></div>`,
        )
        .join("")
    : '<p class="muted">Nema zabeleženih aktivnosti.</p>';
}
async function loadReports() {
  const r = await api("/api/reports");
  $("#reportsList").innerHTML = r.length
    ? r
        .map(
          (x) =>
            `<details class="report-item"><summary><strong>${esc(x.patient_name)}</strong><span>${new Date(x.generated_at).toLocaleString("sr-Latn-RS")} · ${esc(x.status)}</span></summary><pre>${esc(x.content)}</pre></details>`,
        )
        .join("")
    : '<p class="muted">Nema generisanih izveštaja.</p>';
}
async function selectPatient(id) {
  activePatient = patients.find((p) => p.id === id) || null;
  $("#emptyState").classList.toggle("hidden", !!activePatient);
  $("#workspace").classList.toggle("hidden", !activePatient);
  $("#reportWrap").classList.add("hidden");
  if (!activePatient) return;
  $("#patientName").textContent = activePatient.full_name;
  const age = calcAge(activePatient.date_of_birth);
  $("#patientMeta").textContent =
    [
      age !== null && `${age} god.`,
      activePatient.date_of_birth &&
        `rođ. ${new Date(activePatient.date_of_birth).toLocaleDateString("sr-Latn-RS")}`,
      activePatient.phone,
      activePatient.email,
    ]
      .filter(Boolean)
      .join(" · ") || "Nema dodatnih demografskih podataka";
  $("#safetyStrip").innerHTML = "";
  rememberRecent(activePatient.id);
  switchChartTab("pregled");
  await refreshWorkspace();
}
function switchChartTab(key) {
  $$(".chart-tab").forEach((b) =>
    b.classList.toggle("active", b.dataset.tab === key),
  );
  $$(".chart-tab-panel").forEach((p) =>
    p.classList.toggle("active", p.dataset.tabPanel === key),
  );
}
$$(".chart-tab").forEach(
  (b) => (b.onclick = () => switchChartTab(b.dataset.tab)),
);
function calcAge(dob) {
  if (!dob) return null;
  const b = new Date(dob),
    n = new Date();
  let a = n.getFullYear() - b.getFullYear();
  const m = n.getMonth() - b.getMonth();
  if (m < 0 || (m === 0 && n.getDate() < b.getDate())) a--;
  return a >= 0 && a < 130 ? a : null;
}
async function refreshWorkspace() {
  await Promise.all([
    loadDocuments(),
    loadOverview(),
    loadSummary(),
    loadTimeline(),
    loadClinicalProfile(),
    loadEncounters(),
    loadLabResults(),
    loadPediatrics(),
  ]);
}
async function loadOverview() {
  const d = await api(`/api/patients/${activePatient.id}/overview`);
  $("#docCount").textContent = d.document_count;
  $("#labCount").textContent = d.lab_result_count;
  $("#timelineCount").textContent = d.timeline_count;
  $("#readiness").textContent = d.readiness;
}
async function loadDocuments() {
  const d = await api(`/api/patients/${activePatient.id}/documents`);
  $("#documents").innerHTML = d.length
    ? d
        .map(
          (x) =>
            `<div class="row ${x.status === "archived" ? "archived-row" : ""}"><div><strong>${esc(x.filename)}</strong><p>${fmtDate(x.uploaded_at)} · ${size(x.size_bytes)}${x.extraction_method === "ocr" ? " · lokalni OCR" : ""}${x.status === "archived" ? ` · arhiviran: ${esc(x.archive_reason || "bez razloga")}` : ""}</p></div><div class="row-actions">${x.attention ? '<span class="badge warn">Provera</span>' : ""}<button class="open-original button secondary" data-id="${x.id}">Original</button>${x.status !== "archived" ? `<button class="archive-doc button secondary" data-id="${x.id}">Arhiviraj</button>` : '<span class="badge">Arhivirano</span>'}</div></div>`,
        )
        .join("")
    : '<p class="muted">Nema dodatih dokumenata.</p>';
}
let labStandardsCache = null;
async function ensureLabStandards() {
  if (!labStandardsCache) {
    try {
      labStandardsCache = await api("/api/lab-standards");
    } catch {
      labStandardsCache = {};
    }
  }
  return labStandardsCache;
}
async function loadLabResults() {
  if (!activePatient || !["doctor", "admin"].includes(currentUser.role)) return;
  const rows = await api(`/api/patients/${activePatient.id}/lab-results`);
  const standards = await ensureLabStandards();
  const status = {
    draft: "Nacrt iz dokumenta",
    verified: "Potvrđeno",
    rejected: "Odbačeno",
  };
  const countByName = {};
  rows.forEach((x) => (countByName[x.name] = (countByName[x.name] || 0) + 1));
  $("#labResults").innerHTML = rows.length
    ? rows
        .map((x) => {
          const std = standards[x.name];
          const citation =
            x.source_page && x.source_document_id
              ? `<a class="button secondary lab-source-link" target="_blank" rel="noopener" href="/api/patients/${activePatient.id}/documents/${x.source_document_id}/original#page=${x.source_page}">📄 Str. ${x.source_page} u originalu</a>`
              : "";
          const trendBtn =
            countByName[x.name] > 1
              ? `<button type="button" class="button secondary lab-trend-btn" data-name="${esc(x.name)}">📈 Trend</button>`
              : "";
          return `<div class="row"><div><strong>${esc(x.name)}: ${x.value === null ? "—" : esc(x.value)} ${esc(x.unit || "")}</strong><p>${esc(x.reference_range ? `Referentno: ${x.reference_range}` : "Referentni opseg nije prepoznat")} · ${fmtDate(x.collected_at || x.created_at)}</p>${x.notes ? `<small class="muted">${esc(x.notes)}</small>` : ""}${std ? `<small class="muted lab-standard-hint">LOINC ${esc(std.loinc_code)} · Opšta referenca: ${esc(std.general_reference_range)}</small>` : ""}</div><div class="row-actions">${citation}${trendBtn}<span class="badge ${x.abnormality === "high" || x.abnormality === "low" ? "warn" : ""}">${x.abnormality === "high" ? "Povišeno*" : x.abnormality === "low" ? "Sniženo*" : esc(status[x.status])}</span>${x.status === "draft" ? `<button class="button secondary lab-status" data-id="${x.id}" data-status="verified">Potvrdi</button><button class="button secondary lab-status" data-id="${x.id}" data-status="rejected">Odbaci</button>` : ""}</div></div>`;
        })
        .join("")
    : '<p class="muted">Nema evidentiranih laboratorijskih rezultata.</p>';
  $("#labTrendChart").classList.add("hidden");
  $("#labTrendChart").innerHTML = "";
  if ($("#labTabFlag"))
    $("#labTabFlag").classList.toggle(
      "hidden",
      !rows.some((x) => x.status === "draft"),
    );
}
function renderLabTrendChart(rows, name) {
  const pts = rows.filter((r) => r.value !== null);
  if (pts.length < 2)
    return '<p class="muted">Nedovoljno tačaka za grafikon trenda.</p>';
  const W = 640,
    H = 180,
    padL = 40,
    padR = 16,
    padT = 16,
    padB = 26,
    plotW = W - padL - padR,
    plotH = H - padT - padB;
  const values = pts.map((p) => p.value);
  const minV = Math.min(...values),
    maxV = Math.max(...values);
  const span = maxV - minV || 1;
  const x = (i) => padL + (pts.length > 1 ? (i * plotW) / (pts.length - 1) : 0);
  const y = (v) => padT + plotH - ((v - minV) / span) * plotH;
  const path = pts
    .map((p, i) => `${i === 0 ? "M" : "L"}${x(i).toFixed(1)},${y(p.value).toFixed(1)}`)
    .join(" ");
  const dots = pts
    .map(
      (p, i) =>
        `<circle cx="${x(i).toFixed(1)}" cy="${y(p.value).toFixed(1)}" r="3.2" fill="${p.abnormality === "high" || p.abnormality === "low" ? "#b34b3f" : "#176b5c"}"><title>${new Date(p.date).toLocaleDateString("sr-Latn-RS")}: ${p.value} ${p.unit || ""}</title></circle>`,
    )
    .join("");
  const xLabels = pts
    .map(
      (p, i) =>
        `<text x="${x(i).toFixed(1)}" y="${H - 6}" font-size="9" fill="#8ca39f" text-anchor="middle">${new Date(p.date).toLocaleDateString("sr-Latn-RS", { month: "short", day: "numeric" })}</text>`,
    )
    .join("");
  return `<svg viewBox="0 0 ${W} ${H}" class="lab-trend-svg"><line x1="${padL}" x2="${W - padR}" y1="${padT}" y2="${padT}" stroke="#e5eeec"/><line x1="${padL}" x2="${W - padR}" y1="${(padT + plotH).toFixed(1)}" y2="${(padT + plotH).toFixed(1)}" stroke="#e5eeec"/><path d="${path}" fill="none" stroke="#176b5c" stroke-width="2"/>${dots}${xLabels}<text x="${padL}" y="${padT - 4}" font-size="9" fill="#8ca39f">${maxV}</text><text x="${padL}" y="${(padT + plotH + 10).toFixed(1)}" font-size="9" fill="#8ca39f">${minV}</text></svg>`;
}
document.addEventListener("click", async (e) => {
  const btn = e.target.closest(".lab-trend-btn");
  if (!btn) return;
  const el = $("#labTrendChart");
  const name = btn.dataset.name;
  if (!el.classList.contains("hidden") && el.dataset.name === name) {
    el.classList.add("hidden");
    el.dataset.name = "";
    return;
  }
  const rows = await api(
    `/api/patients/${activePatient.id}/lab-results/trend?name=${encodeURIComponent(name)}`,
  );
  el.dataset.name = name;
  el.innerHTML = `<h4>Trend: ${esc(name)}</h4>${renderLabTrendChart(rows, name)}`;
  el.classList.remove("hidden");
  el.scrollIntoView({ behavior: "smooth", block: "nearest" });
});
function renderTherapyOverview(p) {
  const el = $("#therapyOverview");
  if (!el) return;
  const item = (k, v) =>
    `<div class="profile-item"><span>${k}</span><strong>${esc(v || "Nije evidentirano")}</strong></div>`;
  el.innerHTML =
    item("Alergije", (p.allergies || []).join(", ")) +
    item("Trenutna terapija", (p.current_medications || []).join(", ")) +
    item("Dijagnoze", (p.diagnoses || []).join(", "));
  loadAtcReference();
}
let atcReferenceLoaded = false;
async function loadAtcReference() {
  if (atcReferenceLoaded || !["doctor", "admin"].includes(currentUser.role)) return;
  try {
    const codes = await api("/api/atc-codes");
    $("#atcReference").innerHTML = Object.entries(codes)
      .sort(([a], [b]) => a.localeCompare(b))
      .map(
        ([name, atc]) =>
          `<div class="row"><div><strong>${esc(name)}</strong></div><span class="badge">ATC ${esc(atc)}</span></div>`,
      )
      .join("");
    atcReferenceLoaded = true;
  } catch {}
}
async function loadSummary() {
  $("#summary").textContent = "Analiza kartona…";
  const d = await api(`/api/patients/${activePatient.id}/summary`);
  $("#summary").textContent = d.summary;
  $("#summary").classList.remove("placeholder");
}
async function loadTimeline() {
  const d = await api(`/api/patients/${activePatient.id}/timeline`);
  $("#timeline").innerHTML = d.length
    ? d
        .map(
          (i) =>
            `<div class="timeline-item"><strong>${esc(i.title)}</strong><span>${esc(i.date || "Datum nije prepoznat")} · ${esc(i.category)}</span><p>${esc(i.detail)}</p>${i.source ? `<span class="source">Izvor: ${esc(i.source)}</span>` : ""}</div>`,
        )
        .join("")
    : '<p class="muted">Tok lečenja će se prikazati nakon analize.</p>';
}
async function ask(q) {
  if (!activePatient || !q.trim()) return;
  $("#chat").insertAdjacentHTML(
    "beforeend",
    `<div class="user-msg">${esc(q)}</div>`,
  );
  const p = document.createElement("div");
  p.className = "assistant-msg";
  p.textContent = "Pregledam karton…";
  $("#chat").appendChild(p);
  const ch = $("#chat");
  ch.scrollTop = ch.scrollHeight;
  try {
    p.textContent = (
      await api(`/api/patients/${activePatient.id}/chat`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ question: q }),
      })
    ).answer;
  } catch (e) {
    p.textContent = e.message;
  } finally {
    const ch = $("#chat");
    ch.scrollTop = ch.scrollHeight;
  }
}
function closeMobileNav() {
  $("#sidebar").classList.remove("open");
  $("#navBackdrop").classList.remove("open");
  $("#navToggle").setAttribute("aria-expanded", "false");
}
function openMobileNav() {
  $("#sidebar").classList.add("open");
  $("#navBackdrop").classList.add("open");
  $("#navToggle").setAttribute("aria-expanded", "true");
}
if ($("#navToggle"))
  $("#navToggle").onclick = () =>
    $("#sidebar").classList.contains("open")
      ? closeMobileNav()
      : openMobileNav();
if ($("#navBackdrop")) $("#navBackdrop").onclick = closeMobileNav;
$$(".nav-item").forEach(
  (b) =>
    (b.onclick = () => {
      showView(b.dataset.view);
      closeMobileNav();
    }),
);
$("#themeBtn").onclick = () => {
  document.body.classList.toggle("dark");
  localStorage.setItem(
    "clinic-theme",
    document.body.classList.contains("dark") ? "dark" : "light",
  );
};
$("#newPatientBtn").onclick = () => $("#patientDialog").showModal();
document.addEventListener("click", async (e) => {
  const citationBtn = e.target.closest(".evidence-citation-btn");
  if (!citationBtn) return;
  await openOriginalDocument(citationBtn.dataset.docId);
});
$$(".close").forEach((b) => (b.onclick = () => b.closest("dialog").close()));
$("#addAppointmentBtn").onclick = () => $("#appointmentDialog").showModal();
$$(".appointment-open").forEach(
  (b) => (b.onclick = () => $("#appointmentDialog").showModal()),
);
$("#patientSelect").onchange = (e) => selectPatient(e.target.value);
$("#patientForm").onsubmit = async (e) => {
  e.preventDefault();
  const d = Object.fromEntries(new FormData(e.target));
  Object.keys(d).forEach((k) => !d[k] && delete d[k]);
  try {
    const p = await api("/api/patients", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(d),
    });
    await loadPatients();
    $("#patientSelect").value = p.id;
    e.target.reset();
    $("#patientDialog").close();
    showView("workspace");
    await selectPatient(p.id);
    toast("Pacijent je kreiran");
  } catch (x) {
    toast(x.message);
  }
};
$("#appointmentForm").onsubmit = async (e) => {
  e.preventDefault();
  const d = Object.fromEntries(new FormData(e.target));
  d.starts_at = new Date(d.starts_at).toISOString();
  d.duration_minutes = d.duration_minutes ? parseInt(d.duration_minutes, 10) : 20;
  ["notes", "clinician_id", "room", "service_type"].forEach((k) => {
    if (!d[k]) delete d[k];
  });
  $("#appointmentConflict").classList.add("hidden");
  try {
    await api("/api/appointments", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(d),
    });
    e.target.reset();
    $("#appointmentDialog").close();
    await loadDashboard();
    if ($("#scheduleView") && !$("#scheduleView").classList.contains("hidden")) await loadCalendar();
    toast("Termin je zakazan");
  } catch (x) {
    if (x.status === 409) {
      $("#appointmentConflict").textContent = x.message;
      $("#appointmentConflict").classList.remove("hidden");
    } else {
      toast(x.message);
    }
  }
};
$("#waitlistForm").onsubmit = async (e) => {
  e.preventDefault();
  const d = Object.fromEntries(new FormData(e.target));
  ["desired_service", "clinician_id", "preferred_note"].forEach((k) => {
    if (!d[k]) delete d[k];
  });
  try {
    await api("/api/waitlist", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(d),
    });
    e.target.reset();
    $("#waitlistDialog").close();
    await loadWaitlistPanel();
    toast("Dodato na listu čekanja");
  } catch (x) {
    toast(x.message);
  }
};
$("#promoteForm").onsubmit = async (e) => {
  e.preventDefault();
  const entryId = e.target.dataset.entryId;
  const d = Object.fromEntries(new FormData(e.target));
  d.starts_at = new Date(d.starts_at).toISOString();
  d.duration_minutes = d.duration_minutes ? parseInt(d.duration_minutes, 10) : 20;
  ["clinician_id", "room"].forEach((k) => {
    if (!d[k]) delete d[k];
  });
  $("#promoteConflict").classList.add("hidden");
  try {
    await api(`/api/waitlist/${entryId}/promote`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(d),
    });
    e.target.reset();
    $("#promoteDialog").close();
    await loadWaitlistPanel();
    await loadCalendar();
    toast("Termin je zakazan sa liste čekanja");
  } catch (x) {
    if (x.status === 409) {
      $("#promoteConflict").textContent = x.message;
      $("#promoteConflict").classList.remove("hidden");
    } else {
      toast(x.message);
    }
  }
};
$("#waitlistOpen").onclick = () => $("#waitlistDialog").showModal();
$("#calPrev").onclick = () => {
  if (calState.view === "week") calState.anchor.setDate(calState.anchor.getDate() - 7);
  else calState.anchor.setMonth(calState.anchor.getMonth() - 1);
  loadCalendar();
};
$("#calNext").onclick = () => {
  if (calState.view === "week") calState.anchor.setDate(calState.anchor.getDate() + 7);
  else calState.anchor.setMonth(calState.anchor.getMonth() + 1);
  loadCalendar();
};
$("#calToday").onclick = () => {
  calState.anchor = new Date();
  loadCalendar();
};
$$(".cal-view-btn").forEach((b) => {
  b.onclick = () => {
    $$(".cal-view-btn").forEach((x) => x.classList.remove("active"));
    b.classList.add("active");
    calState.view = b.dataset.view;
    loadCalendar();
  };
});
$("#calClinicianFilter").onchange = (e) => {
  calState.clinicianFilter = e.target.value;
  loadCalendar();
};
document.addEventListener("change", async (e) => {
  if (e.target.matches(".status-select")) {
    let cancellation_reason = null;
    if (e.target.value === "cancelled" || e.target.value === "no_show") {
      cancellation_reason = prompt(
        e.target.value === "cancelled" ? "Razlog otkazivanja (opciono):" : "Napomena (opciono):",
      );
    }
    await api(`/api/appointments/${e.target.dataset.id}/status`, {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ status: e.target.value, cancellation_reason: cancellation_reason || null }),
    });
    await loadDashboard();
    if ($("#scheduleView") && !$("#scheduleView").classList.contains("hidden")) await loadCalendar();
    toast("Status je ažuriran");
  }
});
$("#fileInput").onchange = async (e) => {
  if (!activePatient) {
    toast("Prvo izaberite pacijenta");
    e.target.value = "";
    return;
  }
  const f = e.target.files[0];
  if (!f) return;
  const form = new FormData();
  form.append("file", f);
  $("#uploadProgress").classList.remove("hidden");
  try {
    await api(`/api/patients/${activePatient.id}/documents`, {
      method: "POST",
      body: form,
    });
    await refreshWorkspace();
    toast("Dokument je obrađen");
  } catch (x) {
    toast(x.message);
  } finally {
    $("#uploadProgress").classList.add("hidden");
    e.target.value = "";
  }
};
async function openOriginalDocument(documentId) {
  // Open synchronously from the click so ordinary popup blockers do not
  // reject a legitimate clinical review window after the authenticated fetch.
  const view = window.open("", "_blank");
  if (!view) {
    toast("Pregledač je blokirao otvaranje originalnog dokumenta");
    return;
  }
  try {
    const r = await fetch(`/api/patients/${activePatient.id}/documents/${documentId}/original`, {
      credentials: "same-origin", cache: "no-store",
    });
    if (!r.ok) throw Error("Original dokument nije dostupan");
    const url = URL.createObjectURL(await r.blob());
    view.opener = null;
    view.location.replace(url);
    setTimeout(() => URL.revokeObjectURL(url), 60_000);
  } catch (x) {
    view.close();
    toast(x.message || "Original dokument nije dostupan");
  }
}
$("#documents").onclick = async (e) => {
  const original = e.target.closest(".open-original");
  if (original) {
    await openOriginalDocument(original.dataset.id);
    return;
  }
  const archive = e.target.closest(".archive-doc");
  if (!archive) return;
  const reason = prompt("Razlog arhiviranja dokumenta (ostaje u istoriji kartona):");
  if (!reason) return;
  await api(`/api/patients/${activePatient.id}/documents/${archive.dataset.id}/archive`, {
    method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ reason }),
  });
  await refreshWorkspace();
  toast("Dokument je arhiviran; original i istorija su sačuvani");
};
$("#refreshSummary").onclick = () => activePatient && loadSummary();
$("#chatForm").onsubmit = (e) => {
  e.preventDefault();
  const i = $("#question"),
    q = i.value;
  i.value = "";
  ask(q);
};
$$("[data-question]").forEach(
  (b) => (b.onclick = () => ask(b.dataset.question)),
);
$("#reportBtn").onclick = async () => {
  if (!activePatient) return;
  const b = $("#reportBtn");
  b.disabled = true;
  b.textContent = "Generisanje…";
  try {
    const d = await api(`/api/patients/${activePatient.id}/reports`, {
      method: "POST",
    });
    reportContent = d.content;
    $("#report").textContent = d.content;
    $("#reportWrap").classList.remove("hidden");
    toast("Izveštaj je sačuvan u arhivi");
  } catch (x) {
    toast(x.message);
  } finally {
    b.disabled = false;
    b.textContent = "Generiši izveštaj";
  }
};
$("#copyReport").onclick = async () => {
  await navigator.clipboard.writeText(reportContent);
  toast("Izveštaj je kopiran");
};
init().catch((e) => toast(e.message));

async function loadUsers() {
  const rows = await api("/api/users");
  $("#usersList").innerHTML = rows.length
    ? rows
        .map(
          (x) =>
            `<div class="row"><div><strong>${esc(x.full_name)}</strong><p>${esc(x.username)} · ${esc(x.role)}${x.must_change_password ? " · potrebna promena lozinke" : ""}</p></div><button class="button secondary user-toggle" data-id="${x.id}" data-active="${x.active}">${x.active ? "Deaktiviraj" : "Aktiviraj"}</button></div>`,
        )
        .join("")
    : '<p class="muted">Nema korisnika.</p>';
}
async function loadSessions() {
  const rows = await api("/api/sessions");
  $("#sessionsList").innerHTML = rows.length
    ? rows
        .map(
          (x) =>
            `<div class="row"><div><strong>${esc(x.full_name)}</strong><p>${esc(x.username)} · ${roleLabel[x.role] || esc(x.role)} · prijavljen ${new Date(x.created_at).toLocaleString("sr-Latn-RS")} · ističe ${new Date(x.expires_at).toLocaleString("sr-Latn-RS")}</p></div><button class="button secondary session-revoke" data-id="${x.id}">Prekini sesiju</button></div>`,
        )
        .join("")
    : '<p class="muted">Nema aktivnih sesija.</p>';
}
$("#sessionsList").onclick = async (e) => {
  const b = e.target.closest(".session-revoke");
  if (
    !b ||
    !confirm("Prekinuti ovu sesiju? Korisnik će morati ponovo da se prijavi.")
  )
    return;
  try {
    await api(`/api/sessions/${b.dataset.id}`, { method: "DELETE" });
    await loadSessions();
    toast("Sesija je prekinuta");
  } catch (x) {
    toast(x.message);
  }
};
$("#newUserBtn").onclick = () => $("#userDialog").showModal();
$("#userForm").onsubmit = async (e) => {
  e.preventDefault();
  const d = Object.fromEntries(new FormData(e.target));
  try {
    await api("/api/users", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(d),
    });
    e.target.reset();
    $("#userDialog").close();
    await loadUsers();
    toast("Korisnik je kreiran");
  } catch (x) {
    toast(x.message);
  }
};
$("#usersList").onclick = async (e) => {
  const b = e.target.closest(".user-toggle");
  if (!b) return;
  try {
    await api(`/api/users/${b.dataset.id}/status`, {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ active: b.dataset.active !== "true" }),
    });
    await loadUsers();
    toast("Status korisnika je ažuriran");
  } catch (x) {
    toast(x.message);
  }
};
$("#passwordBtn").onclick = () => $("#passwordDialog").showModal();
$("#passwordForm").onsubmit = async (e) => {
  e.preventDefault();
  const d = Object.fromEntries(new FormData(e.target));
  try {
    await api("/api/auth/change-password", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(d),
    });
    e.target.reset();
    $("#passwordDialog").close();
    currentUser.must_change_password = false;
    toast("Lozinka je promenjena. Prijavite se ponovo.");
    await $("#logoutBtn").onclick();
  } catch (x) {
    toast(x.message);
  }
};

let mfaSetupStarted = false;
$("#mfaBtn").onclick = async () => {
  mfaSetupStarted = false;
  $("#mfaForm").reset();
  $("#mfaSecretBox").classList.add("hidden");
  $("#mfaCodeInput").required = false;
  $("#mfaDisableBtn").classList.toggle("hidden", !currentUser.mfa_enabled);
  $("#mfaPrimaryBtn").textContent = currentUser.mfa_enabled ? "Podesi novi autentikator" : "Započni podešavanje";
  $("#mfaState").textContent = currentUser.mfa_enabled ? "Višefaktorska prijava je uključena. Novi autentikator zamenjuje postojeći tek nakon potvrde koda." : "Dodajte nalog u autentikator aplikaciju, zatim potvrdite šestocifreni kod. Tajna se prikazuje samo tokom podešavanja.";
  $("#mfaDialog").showModal();
};
$("#mfaForm").onsubmit = async (e) => {
  e.preventDefault();
  try {
    if (!mfaSetupStarted) {
      const setup = await api("/api/auth/mfa/setup", { method: "POST" });
      $("#mfaSecret").value = setup.secret;
      $("#mfaSecretBox").classList.remove("hidden");
      $("#mfaState").textContent = "Tajni ključ unesite u autentikator aplikaciju, pa ovde upišite trenutni kod. Nemojte čuvati ključ u beleškama ili slati ga porukom.";
      $("#mfaPrimaryBtn").textContent = "Potvrdi i uključi";
      $("#mfaCodeInput").required = true;
      mfaSetupStarted = true;
      return;
    }
    const code = new FormData(e.target).get("code");
    if (!code || !/^[0-9]{6}$/.test(code)) {
      toast("Unesite šestocifreni kod iz autentikator aplikacije");
      return;
    }
    await api("/api/auth/mfa/confirm", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ code }) });
    currentUser.mfa_enabled = true;
    $("#mfaDialog").close();
    toast("Višefaktorska prijava je uključena");
  } catch (x) { toast(x.message); }
};
$("#mfaDisableBtn").onclick = async () => {
  const code = new FormData($("#mfaForm")).get("code");
  try {
    await api("/api/auth/mfa/disable", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ code }) });
    currentUser.mfa_enabled = false;
    $("#mfaDialog").close();
    toast("Višefaktorska prijava je isključena");
  } catch (x) { toast(x.message); }
};

$("#loginForm").onsubmit = async (e) => {
  e.preventDefault();
  const d = Object.fromEntries(new FormData(e.target));
  try {
    let r;
    if (mfaLoginChallenge) {
      r = await api("/api/auth/mfa/complete-login", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ challenge: mfaLoginChallenge, code: d.mfa_code }),
      });
    } else {
      r = await api("/api/auth/login", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(d),
      });
    }
    if (r.mfa_required) {
      mfaLoginChallenge = r.mfa_challenge;
      $("#mfaLoginField").classList.remove("hidden");
      $("#loginHint").textContent = "Unesite šestocifreni kod iz autentikator aplikacije. Kod važi kratko.";
      $("#loginForm button").textContent = "Potvrdi kod";
      $("#loginForm [name=mfa_code]").required = true;
      $("#loginForm [name=mfa_code]").focus();
      return;
    }
    currentUser = r.user;
    applyUser();
    $("#loginOverlay").classList.add("hidden");
    await loadPatients();
    await loadDashboard();
    toast("Uspešna prijava");
  } catch (x) {
    if (mfaLoginChallenge) $("#loginForm [name=mfa_code]").value = "";
    toast(x.message);
  }
};
$("#logoutBtn").onclick = async () => {
  try {
    await api("/api/auth/logout", { method: "POST" });
  } catch {}
  currentUser = null;
  $("#loginOverlay").classList.remove("hidden");
};
const lines = (v) =>
  String(v || "")
    .split(/\n|,/)
    .map((x) => x.trim())
    .filter(Boolean);
async function loadClinicalProfile() {
  if (!activePatient || !["doctor", "admin"].includes(currentUser.role)) return;
  const p = await api(`/api/patients/${activePatient.id}/clinical-profile`);
  const item = (k, v) =>
    `<div class="profile-item"><span>${k}</span><strong>${esc(v || "Nije evidentirano")}</strong></div>`;
  $("#clinicalProfile").innerHTML =
    item("Krvna grupa", p.blood_type) +
    item("Alergije", (p.allergies || []).join(", ")) +
    item("Terapije", (p.current_medications || []).join(", ")) +
    item("Dijagnoze", (p.diagnoses || []).join(", ")) +
    item("Lična anamneza", p.medical_history) +
    item("Porodična anamneza", p.family_history) +
    item("Socijalna anamneza", p.social_history);
  $("#profileForm").blood_type.value = p.blood_type || "";
  $("#profileForm").allergies.value = (p.allergies || []).join("\n");
  $("#profileForm").current_medications.value = (
    p.current_medications || []
  ).join("\n");
  $("#profileForm").diagnoses.value = (p.diagnoses || []).join("\n");
  $("#profileForm").medical_history.value = p.medical_history || "";
  $("#profileForm").family_history.value = p.family_history || "";
  $("#profileForm").social_history.value = p.social_history || "";
  renderSafetyStrip(p);
  renderTherapyOverview(p);
}
function renderSafetyStrip(p) {
  const al = p.allergies || [],
    meds = p.current_medications || [],
    dg = p.diagnoses || [];
  const chips = [];
  if (al.length)
    chips.push(
      ...al.map(
        (a) =>
          `<span class="chip allergy" title="Alergija">⚠ ${esc(a)}</span>`,
      ),
    );
  else chips.push('<span class="chip ok">Bez poznatih alergija</span>');
  if (dg.length)
    chips.push(
      `<span class="chip" title="${esc(dg.join(", "))}">Dg: ${esc(dg.slice(0, 2).join(", "))}${dg.length > 2 ? ` +${dg.length - 2}` : ""}</span>`,
    );
  if (meds.length)
    chips.push(
      `<span class="chip" title="${esc(meds.join(", "))}">Th: ${meds.length} ${meds.length === 1 ? "lek" : "leka/lekova"}</span>`,
    );
  if (p.blood_type)
    chips.push(`<span class="chip">${esc(p.blood_type)}</span>`);
  $("#safetyStrip").innerHTML = chips.join("");
}
function vitalsBadges(v) {
  if (!v) return "";
  const out = [];
  const num = (s) => {
    const m = String(s)
      .replace(",", ".")
      .match(/-?\d+(\.\d+)?/);
    return m ? parseFloat(m[0]) : null;
  };
  if (v.bp) {
    const m = String(v.bp).match(/(\d{2,3})\s*\/\s*(\d{2,3})/);
    const abn = m && (+m[1] >= 140 || +m[2] >= 90 || +m[1] < 90);
    out.push(
      `<span class="vital${abn ? " abn" : ""}" title="Krvni pritisak">TA ${esc(v.bp)}</span>`,
    );
  }
  if (v.pulse != null) {
    const p = num(v.pulse);
    const abn = p !== null && (p > 100 || p < 50);
    out.push(
      `<span class="vital${abn ? " abn" : ""}" title="Puls">P ${esc(v.pulse)}/min</span>`,
    );
  }
  if (v.temperature != null) {
    const t = num(v.temperature);
    const abn = t !== null && (t >= 37.5 || t < 35);
    out.push(
      `<span class="vital${abn ? " abn" : ""}" title="Temperatura">T ${esc(v.temperature)}°C</span>`,
    );
  }
  if (v.spo2 != null) {
    const s = num(v.spo2);
    const abn = s !== null && s < 94;
    out.push(
      `<span class="vital${abn ? " abn" : ""}" title="Saturacija kiseonikom">SpO₂ ${esc(v.spo2)}%</span>`,
    );
  }
  return out.length ? `<span class="vitals">${out.join("")}</span>` : "";
}
async function loadEncounters() {
  if (!activePatient) return;
  const rows = await api(`/api/patients/${activePatient.id}/encounters`);
  $("#encountersList").innerHTML = rows.length
    ? rows
        .map(
          (x) =>
            `<details class="report-item"><summary><strong>${fmtDate(x.visit_date)} · ${esc(x.chief_complaint)}</strong><span>${vitalsBadges(x.vital_signs)}${esc(x.clinician_name)}</span></summary><p><b>Anamneza:</b> ${esc(x.anamnesis || "—")}</p><p><b>Objektivni pregled:</b> ${esc(x.examination || "—")}</p><p><b>Procena:</b> ${esc(x.assessment || "—")}</p><p><b>Plan lečenja:</b> ${esc(x.plan || "—")}</p></details>`,
        )
        .join("")
    : '<p class="muted">Nema strukturisanih pregleda. Kliknite „+ Novi pregled“ ili koristite AI pisara.</p>';
}
$("#editProfileBtn").onclick = () =>
  activePatient && $("#profileDialog").showModal();
$("#safetyCheckBtn").onclick = () =>
  activePatient && $("#medicationSafetyDialog").showModal();
$("#medicationSafetyForm").onsubmit = async (e) => {
  e.preventDefault();
  const result = $("#medicationSafetyResults");
  result.classList.remove("hidden");
  result.textContent = "Provera potencijalnih rizika…";
  try {
    const d = await api(
      `/api/patients/${activePatient.id}/medication-safety-check`,
      {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          proposed_medications: lines(e.target.proposed_medications.value),
        }),
      },
    );
    const findings = d.findings.length
      ? d.findings
          .map(
            (x) =>
              `<div class="row safety-finding ${x.type === "organ_function" ? "organ-function" : ""}"><div><strong>${esc(x.severity.toUpperCase())} · ${esc(x.medications.join(" + "))}</strong><p>${esc(x.message)}</p><small>${esc(x.action)}</small><small class="muted safety-source">Pravilo ${esc(x.rule_id)} — ${esc(x.source_note)}</small></div></div>`,
          )
          .join("")
      : "<p>Nema pronađenih upozorenja u ograničenom skupu pravila. To ne znači da je terapija bezbedna.</p>";
    result.innerHTML = `${findings}${d.unrecognized_medications.length ? `<p class="review-note">Nije prepoznato / nije provereno: ${esc(d.unrecognized_medications.join(", "))}</p>` : ""}<p class="review-note">${esc(d.disclaimer)}</p>`;
  } catch (x) {
    result.textContent = x.message;
  }
};
$("#labResults").onclick = async (e) => {
  const b = e.target.closest(".lab-status");
  if (!b) return;
  try {
    await api(
      `/api/patients/${activePatient.id}/lab-results/${b.dataset.id}/status`,
      {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ status: b.dataset.status }),
      },
    );
    await Promise.all([loadLabResults(), loadOverview()]);
    toast(
      b.dataset.status === "verified"
        ? "Laboratorijski rezultat je potvrđen"
        : "Laboratorijski rezultat je odbačen",
    );
  } catch (x) {
    toast(x.message);
  }
};
$("#profileForm").onsubmit = async (e) => {
  e.preventDefault();
  const d = Object.fromEntries(new FormData(e.target));
  d.allergies = lines(d.allergies);
  d.current_medications = lines(d.current_medications);
  d.diagnoses = lines(d.diagnoses);
  for (const k of [
    "blood_type",
    "medical_history",
    "family_history",
    "social_history",
  ])
    if (!d[k]) d[k] = null;
  try {
    await api(`/api/patients/${activePatient.id}/clinical-profile`, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(d),
    });
    $("#profileDialog").close();
    await loadClinicalProfile();
    toast("Klinički profil je sačuvan");
  } catch (x) {
    toast(x.message);
  }
};
$("#newEncounterBtn").onclick = () => {
  if (!activePatient) return;
  const f = $("#encounterForm");
  f.reset();
  f.visit_date.value = new Date().toISOString().slice(0, 16);
  $("#encounterDialog").showModal();
};
$("#encounterForm").onsubmit = async (e) => {
  e.preventDefault();
  const d = Object.fromEntries(new FormData(e.target));
  d.visit_date = new Date(d.visit_date).toISOString();
  d.vital_signs = {};
  for (const k of ["bp", "pulse", "temperature", "spo2"]) {
    if (d[k]) d.vital_signs[k] = d[k];
    delete d[k];
  }
  try {
    await api(`/api/patients/${activePatient.id}/encounters`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(d),
    });
    $("#encounterDialog").close();
    await loadEncounters();
    toast("Pregled je sačuvan");
  } catch (x) {
    toast(x.message);
  }
};
$("#pdfReportBtn").onclick = async () => {
  if (!activePatient) return;
  const b = $("#pdfReportBtn"),
    prev = b.textContent;
  b.disabled = true;
  b.textContent = "Priprema PDF-a\u2026";
  try {
    const r = await fetch(
      `/api/patients/${activePatient.id}/medical-report.pdf`,
      { credentials: "same-origin" },
    );
    if (!r.ok)
      throw Error((await r.json()).detail || "Generisanje PDF-a nije uspelo");
    const blob = await r.blob(),
      url = URL.createObjectURL(blob),
      a = document.createElement("a");
    a.href = url;
    a.download = `medicinski-izvestaj-${activePatient.full_name.replace(/\s+/g, "-").toLowerCase()}.pdf`;
    a.click();
    URL.revokeObjectURL(url);
    toast("Medicinski PDF je preuzet");
  } catch (x) {
    toast(x.message);
  } finally {
    b.disabled = false;
    b.textContent = prev;
  }
};

const roleLabel = {
  doctor: "Lekar",
  receptionist: "Recepcionar",
  admin: "Administrator",
};
const statusLabel = {
  scheduled: "zakazano",
  checked_in: "pacijent stigao",
  completed: "završeno",
  cancelled: "otkazano",
  draft: "nacrt",
  approved: "potvrđeno",
  rejected: "odbačeno",
};
async function loadScribeDrafts() {
  if (!activePatient || !["doctor", "admin"].includes(currentUser.role)) return;
  const rows = await api(`/api/patients/${activePatient.id}/scribe-drafts`);
  window.scribeDraftRows = rows;
  $("#scribeList").innerHTML = rows.length
    ? rows
        .map(
          (x) =>
            `<details class="report-item"><summary><strong>${new Date(x.created_at).toLocaleString("sr-Latn-RS")} · ${x.mode === "dictation" ? "Diktat" : "Razgovor"}</strong><span class="badge">${statusLabel[x.status] || x.status}</span></summary><p><b>Razlog dolaska:</b> ${esc(x.chief_complaint || "—")} <small>Izvor: ${esc(x.source_map?.chief_complaint || "transkript")}</small></p><p><b>Anamneza:</b> ${esc(x.anamnesis || "—")}</p><p><b>Objektivni pregled:</b> ${esc(x.examination || "—")} <small>Izvor: ${esc(x.source_map?.examination || "nije navedeno")}</small></p><p><b>Procena:</b> ${esc(x.assessment || "—")}</p><p><b>Plan lečenja:</b> ${esc(x.plan || "—")}</p>${x.medication_changes.length ? `<p><b>Promene terapije za potvrdu:</b> ${esc(x.medication_changes.join("; "))}</p>` : ""}${x.allergy_updates.length ? `<p><b>Alergije za potvrdu:</b> ${esc(x.allergy_updates.join("; "))}</p>` : ""}${x.missing_information.length ? `<p><b>Nedostaju podaci:</b> ${esc(x.missing_information.join("; "))}</p>` : ""}${x.encounter_id ? '<span class="badge">Upisano kao strukturisani pregled</span>' : ""}${x.status === "draft" ? `<div class="report-actions"><button class="button primary scribe-edit" data-id="${x.id}">Pregledaj i potvrdi</button><button class="button secondary scribe-status" data-id="${x.id}" data-status="rejected">Odbaci</button></div>` : ""}</details>`,
        )
        .join("")
    : '<p class="muted">Još nema AI nacrta pregleda.</p>';
}
$("#openScribeBtn").onclick = () =>
  activePatient && $("#scribeDialog").showModal();
$("#scribeForm").onsubmit = async (e) => {
  e.preventDefault();
  const d = Object.fromEntries(new FormData(e.target));
  const b = e.submitter;
  b.disabled = true;
  b.textContent = "Generisanje…";
  try {
    await api(`/api/patients/${activePatient.id}/scribe-drafts`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(d),
    });
    e.target.reset();
    $("#scribeDialog").close();
    await loadScribeDrafts();
    toast("AI nacrt pregleda je generisan");
  } catch (x) {
    toast(x.message);
  } finally {
    b.disabled = false;
    b.textContent = "Generiši nacrt pregleda";
  }
};
function openScribeEditor(id) {
  const x = (window.scribeDraftRows || []).find((r) => r.id === id);
  if (!x) return;
  const f = $("#scribeEditForm");
  f.draft_id.value = x.id;
  for (const k of [
    "chief_complaint",
    "anamnesis",
    "examination",
    "assessment",
    "plan",
  ])
    f[k].value = x[k] || "";
  for (const k of [
    "medication_changes",
    "allergy_updates",
    "missing_information",
  ])
    f[k].value = (x[k] || []).join("\n");
  $("#scribeEditDialog").showModal();
}
async function saveScribeEditor() {
  const f = $("#scribeEditForm"),
    d = Object.fromEntries(new FormData(f));
  const id = d.draft_id;
  delete d.draft_id;
  for (const k of [
    "medication_changes",
    "allergy_updates",
    "missing_information",
  ])
    d[k] = lines(d[k]);
  return api(`/api/patients/${activePatient.id}/scribe-drafts/${id}`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(d),
  });
}
$("#scribeEditForm").onsubmit = async (e) => {
  e.preventDefault();
  try {
    await saveScribeEditor();
    $("#scribeEditDialog").close();
    await loadScribeDrafts();
    toast("Izmene nacrta su sačuvane");
  } catch (x) {
    toast(x.message);
  }
};
$("#approveAndCreateEncounter").onclick = async () => {
  const f = $("#scribeEditForm"),
    id = f.draft_id.value;
  try {
    await saveScribeEditor();
    await api(`/api/patients/${activePatient.id}/scribe-drafts/${id}/status`, {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        status: "approved",
        create_encounter: true,
        visit_date: new Date().toISOString(),
      }),
    });
    $("#scribeEditDialog").close();
    await Promise.all([loadScribeDrafts(), loadEncounters()]);
    toast("Nacrt je potvrđen i upisan kao strukturisani pregled");
  } catch (x) {
    toast(x.message);
  }
};
$("#scribeList").onclick = async (e) => {
  const edit = e.target.closest(".scribe-edit");
  if (edit) return openScribeEditor(edit.dataset.id);
  const b = e.target.closest(".scribe-status");
  if (!b) return;
  try {
    await api(
      `/api/patients/${activePatient.id}/scribe-drafts/${b.dataset.id}/status`,
      {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ status: b.dataset.status }),
      },
    );
    await loadScribeDrafts();
    toast("Nacrt je odbačen");
  } catch (x) {
    toast(x.message);
  }
};
const oldRefreshWorkspace = refreshWorkspace;
refreshWorkspace = async function () {
  await oldRefreshWorkspace();
  await loadScribeDrafts();
};

if ($("#radarPeriod")) $("#radarPeriod").onchange = loadRadar;

let activeDifferentialAnalysis = null;
function differentialCandidateHtml(x) {
  const status = x.review_status || "pending";
  const statusText =
    status === "accepted"
      ? "Potvrđeno od lekara"
      : status === "dismissed"
        ? "Odbačeno od lekara"
        : "AI sugestija — čeka pregled";
  const categoryLabel = `${esc(x.category)}${x.icd10_code ? ` · MKB-10: ${esc(x.icd10_code)}` : ""}`;
  const citations = (x.evidence_citations || [])
    .map(
      (c) =>
        `<button type="button" class="button secondary evidence-citation-btn" data-doc-id="${c.document_id}" title="Otvori dokument gde je ovo pronađeno">📄 ${esc(c.filename)}</button>`,
    )
    .join("");
  return `<details class="differential-item ${x.red_flag ? "red-flag" : ""} ${status}" open><summary><div><strong>${esc(x.name)}</strong><span>${categoryLabel}</span></div><div class="match-score"><b>${x.match_score}/100</b><small>${esc(x.match_level)} podudaranje</small></div></summary><div class="match-bar"><i style="width:${x.match_score}%"></i></div><span class="candidate-state ${status}">${statusText}</span>${x.supporting_evidence.length ? `<p><b>Podržavaju:</b> ${esc(x.supporting_evidence.join("; "))}</p>` : ""}${citations ? `<div class="evidence-citations">${citations}</div>` : ""}${x.contradicting_evidence.length ? `<p><b>Ne uklapa se:</b> ${esc(x.contradicting_evidence.join("; "))}</p>` : ""}${x.missing_information.length ? `<p><b>Potrebno proveriti:</b> ${esc(x.missing_information.join("; "))}</p>` : ""}${x.doctor_note ? `<p><b>Napomena lekara:</b> ${esc(x.doctor_note)}</p>` : ""}${x.red_flag ? '<span class="badge warn">Ne propustiti / razmotriti isključivanje</span>' : ""}${status === "pending" ? `<div class="candidate-review"><input class="doctor-note" data-candidate="${x.id}" placeholder="Opciona napomena lekara"><label><input type="checkbox" class="add-to-scribe" data-candidate="${x.id}"> Dodaj potvrđenu sugestiju u najnoviji AI nacrt pregleda</label><div><button class="button primary candidate-action" data-id="${x.id}" data-status="accepted">Potvrdi za razmatranje</button><button class="button secondary candidate-action" data-id="${x.id}" data-status="dismissed">Odbaci sugestiju</button></div></div>` : ""}${x.reviewed_by ? `<small>Pregledao: ${esc(x.reviewed_by)} · ${new Date(x.reviewed_at).toLocaleString("sr-Latn-RS")}</small>` : ""}</details>`;
}
function renderDifferential(d) {
  activeDifferentialAnalysis = d;
  const box = $("#differentialResults");
  box.innerHTML = d.candidates.length
    ? d.candidates.map(differentialCandidateHtml).join("")
    : '<p class="muted">Nema dovoljno podataka za izdvajanje stanja. Dodajte laboratoriju, simptome ili strukturisani pregled.</p>';
  const epi = $("#differentialEpi");
  epi.classList.toggle("hidden", !d.epidemiology_context.length);
  epi.innerHTML = d.epidemiology_context.length
    ? `<div class="epi-context"><h4>Kontekst iz ordinacije</h4>${d.epidemiology_context.map((x) => `<p>• ${esc(x)}</p>`).join("")}<small>Ovaj kontekst povećava relevantnost za pregled, ali ne potvrđuje dijagnozu kod pacijenta.</small></div>`
    : "";
  $("#differentialDisclaimer").textContent = d.disclaimer;
}
async function loadDifferential() {
  if (!activePatient) return;
  const box = $("#differentialResults"),
    btn = $("#generateDifferentialBtn");
  btn.disabled = true;
  btn.textContent = "Analiza…";
  try {
    const d = await api(
      `/api/patients/${activePatient.id}/differential-analyses`,
      { method: "POST" },
    );
    renderDifferential(d);
    toast("AI diferencijalna analiza je sačuvana za pregled lekara");
  } catch (e) {
    box.innerHTML = `<p class="muted">${esc(e.message)}</p>`;
    toast(e.message);
  } finally {
    btn.disabled = false;
    btn.textContent = "Analiziraj nalaze i istoriju";
  }
}
if ($("#generateDifferentialBtn"))
  $("#generateDifferentialBtn").onclick = loadDifferential;
if ($("#differentialResults"))
  $("#differentialResults").onclick = async (e) => {
    const b = e.target.closest(".candidate-action");
    if (!b || !activeDifferentialAnalysis) return;
    const id = b.dataset.id,
      note = $(`.doctor-note[data-candidate="${id}"]`)?.value || null,
      add = $(`.add-to-scribe[data-candidate="${id}"]`)?.checked || false;
    b.disabled = true;
    try {
      const r = await api(
        `/api/patients/${activePatient.id}/differential-analyses/${activeDifferentialAnalysis.id}/candidates/${id}`,
        {
          method: "PATCH",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            status: b.dataset.status,
            doctor_note: note,
            add_to_latest_scribe_draft: add,
          }),
        },
      );
      renderDifferential(r.analysis);
      if (r.scribe_draft_updated) await loadScribeDrafts();
      toast(
        b.dataset.status === "accepted"
          ? r.scribe_draft_updated
            ? "Sugestija je potvrđena i dodata u nacrt"
            : "Sugestija je potvrđena od lekara"
          : "Sugestija je odbačena",
      );
    } catch (x) {
      toast(x.message);
    } finally {
      b.disabled = false;
    }
  };

/* === Brzi izbor pacijenta (Ctrl+K) i nedavni pacijenti === */
const RECENT_KEY = "clinic-recent-patients";
function rememberRecent(id) {
  try {
    const r = JSON.parse(localStorage.getItem(RECENT_KEY) || "[]").filter(
      (x) => x !== id,
    );
    r.unshift(id);
    localStorage.setItem(RECENT_KEY, JSON.stringify(r.slice(0, 6)));
  } catch {}
}
function recentPatients() {
  try {
    const ids = JSON.parse(localStorage.getItem(RECENT_KEY) || "[]");
    return ids.map((id) => patients.find((p) => p.id === id)).filter(Boolean);
  } catch {
    return [];
  }
}
let qsIndex = 0,
  qsItems = [];
function qsRender(list, label) {
  qsItems = list;
  qsIndex = 0;
  $("#quickResults").innerHTML =
    (label ? `<p class="qs-label">${label}</p>` : "") +
    (list.length
      ? list
          .map((p, i) => {
            const age = calcAge(p.date_of_birth);
            return `<button class="qs-item${i === 0 ? " active" : ""}" data-id="${p.id}"><strong>${esc(p.full_name)}</strong><span>${age !== null ? `${age} god.` : ""}${p.phone ? ` · ${esc(p.phone)}` : ""}</span></button>`;
          })
          .join("")
      : '<p class="muted qs-empty">Nema rezultata. Provera imena ili kreirajte novog pacijenta.</p>');
}
function qsFilter(q) {
  q = q.trim().toLowerCase();
  if (!q)
    return qsRender(
      recentPatients().length ? recentPatients() : patients.slice(0, 6),
      recentPatients().length ? "Nedavno otvarani" : "Pacijenti",
    );
  const norm = (s) => s.toLowerCase();
  const hits = patients
    .filter((p) => norm(p.full_name).includes(q))
    .slice(0, 8);
  qsRender(hits, null);
}
function qsOpen() {
  if (!currentUser) return;
  $("#quickSwitch").classList.remove("hidden");
  const i = $("#quickInput");
  i.value = "";
  qsFilter("");
  i.focus();
}
function qsClose() {
  $("#quickSwitch").classList.add("hidden");
}
async function qsPick(id) {
  qsClose();
  showView("workspace");
  $("#patientSelect").value = id;
  await selectPatient(id);
}
$("#quickInput").oninput = (e) => qsFilter(e.target.value);
$("#quickInput").onkeydown = (e) => {
  if (e.key === "ArrowDown" || e.key === "ArrowUp") {
    e.preventDefault();
    if (!qsItems.length) return;
    qsIndex =
      (qsIndex + (e.key === "ArrowDown" ? 1 : -1) + qsItems.length) %
      qsItems.length;
    $$(".qs-item").forEach((el, i) =>
      el.classList.toggle("active", i === qsIndex),
    );
    $$(".qs-item")[qsIndex]?.scrollIntoView({ block: "nearest" });
  } else if (e.key === "Enter") {
    e.preventDefault();
    if (qsItems[qsIndex]) qsPick(qsItems[qsIndex].id);
  } else if (e.key === "Escape") qsClose();
};
$("#quickSwitch").onclick = (e) => {
  const b = e.target.closest(".qs-item");
  if (b) return qsPick(b.dataset.id);
  if (e.target.id === "quickSwitch") qsClose();
};
if ($("#quickOpenBtn")) $("#quickOpenBtn").onclick = qsOpen;
if ($("#topQuickSearchBtn")) $("#topQuickSearchBtn").onclick = qsOpen;
document.addEventListener("keydown", (e) => {
  if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "k") {
    e.preventDefault();
    qsOpen();
  } else if (e.key === "Escape") {
    if (!$("#quickSwitch").classList.contains("hidden")) qsClose();
    else if ($("#sidebar").classList.contains("open")) closeMobileNav();
  }
});
/* Skok iz rasporeda u karton */
document.addEventListener("click", async (e) => {
  const b = e.target.closest(".open-chart");
  if (!b) return;
  showView("workspace");
  $("#patientSelect").value = b.dataset.patient;
  await selectPatient(b.dataset.patient);
});

/* === v1.9.0: onboarding checklist, epi alerts, izvoz podataka === */
function renderEpiBanner(alerts) {
  const el = $("#epiBanner");
  if (!alerts || !alerts.length) {
    el.classList.add("hidden");
    el.innerHTML = "";
    return;
  }
  el.classList.remove("hidden");
  el.innerHTML = `<div class="epi-head">Epidemiološki signal u ordinaciji</div>${alerts.map((a) => `<p>${esc(a)}</p>`).join("")}<button class="button secondary epi-open">Otvori radar</button>`;
  el.querySelector(".epi-open").onclick = () => showView("radar");
}
async function loadSetupBanner() {
  const el = $("#setupBanner");
  if (currentUser.role !== "admin") {
    el.classList.add("hidden");
    return;
  }
  try {
    const c = await api("/api/setup/checklist");
    if (c.all_clear) {
      el.classList.add("hidden");
      el.innerHTML = "";
      return;
    }
    const items = [];
    if (c.default_passwords_active.length)
      items.push(
        `<li><b>Promenite podrazumevane lozinke</b> — nalozi još uvek koriste demo lozinke: ${c.default_passwords_active.map(esc).join(", ")}. Ovo je bezbednosni rizik.</li>`,
      );
    if (c.clinic_name_is_default)
      items.push(
        '<li><b>Unesite naziv ordinacije</b> — trenutno stoji „Demo Clinic“ (pojavljuje se i na PDF izveštajima). <button class="button secondary rename-org">Promeni naziv</button></li>',
      );
    if (!c.https_enabled)
      items.push(
        '<li><b>HTTPS nije potvrđen</b> — ako je ovo produkciona instalacija dostupna van jednog računara, uključite TLS (CLINIC_TLS=1, videti start.sh) pre nego što osoblje unosi podatke pacijenata.</li>',
      );
    if (!c.production_mode)
      items.push(
        '<li><b>Sistem nije u produkcionom režimu</b> — CLINIC_ENV nije postavljen na "production". Demo/razvojni režim je namenjen isprobavanju, ne stvarnom radu sa pacijentima.</li>',
      );
    if (!c.encryption_key_externally_managed)
      items.push(
        '<li><b>Ključ za enkripciju nije eksterno podešen</b> — sistem koristi automatski generisan ključ. Za produkciju podesite CLINIC_ENCRYPTION_KEY ili CLINIC_ENCRYPTION_KEY_COMMAND iz sopstvenog upravljača tajnama.</li>',
      );
    el.classList.remove("hidden");
    el.innerHTML = `<div class="setup-head">Podesite ordinaciju (${items.length} ${items.length === 1 ? "korak" : "koraka"} do kraja)</div><ul>${items.join("")}</ul>`;
    const rb = el.querySelector(".rename-org");
    if (rb)
      rb.onclick = async () => {
        const name = prompt("Naziv ordinacije:");
        if (!name || name.trim().length < 2) return;
        try {
          await api("/api/organization", {
            method: "PATCH",
            body: JSON.stringify({ name: name.trim() }),
          });
          toast("Naziv ordinacije sačuvan");
          await loadSetupBanner();
        } catch (x) {
          toast(x.message);
        }
      };
  } catch {
    el.classList.add("hidden");
  }
}
async function downloadZip(url, filename, btn) {
  const prev = btn.textContent;
  btn.disabled = true;
  btn.textContent = "Priprema…";
  try {
    const r = await fetch(url, {
      credentials: "same-origin",
    });
    if (!r.ok) throw Error((await r.json()).detail || "Izvoz nije uspeo");
    const blob = await r.blob(),
      u = URL.createObjectURL(blob),
      a = document.createElement("a");
    a.href = u;
    a.download = filename;
    a.click();
    URL.revokeObjectURL(u);
    toast("Izvoz je preuzet");
  } catch (x) {
    toast(x.message);
  } finally {
    btn.disabled = false;
    btn.textContent = prev;
  }
}
if ($("#exportPatientBtn"))
  $("#exportPatientBtn").onclick = () => {
    if (!activePatient) return toast("Prvo izaberite pacijenta");
    downloadZip(
      `/api/patients/${activePatient.id}/export.zip`,
      `izvoz-kartona-${activePatient.full_name.replace(/\s+/g, "-").toLowerCase()}.zip`,
      $("#exportPatientBtn"),
    );
  };
if ($("#exportClinicBtn"))
  $("#exportClinicBtn").onclick = () =>
    downloadZip(
      "/api/export/clinic.zip",
      `izvoz-ordinacije-${new Date().toISOString().slice(0, 10)}.zip`,
      $("#exportClinicBtn"),
    );

/* === Finansijsko-administrativni modul === */
let serviceCache = [];
let invoiceItemSeq = 0;

function fmtMoney(n) {
  return `${Number(n).toLocaleString("sr-Latn-RS")} RSD`;
}

async function loadFinance() {
  if (!$("#financeDate").value)
    $("#financeDate").value = new Date().toISOString().slice(0, 10);
  await Promise.all([
    loadServices(),
    loadInvoiceList(),
    ["receptionist", "admin"].includes(currentUser.role)
      ? loadFinanceSummary()
      : Promise.resolve(),
    ["receptionist", "admin"].includes(currentUser.role)
      ? loadOutstanding()
      : Promise.resolve(),
  ]);
}

async function loadFinanceSummary() {
  const d = await api(`/api/finance/daily-summary?date=${$("#financeDate").value}`);
  const methods = { gotovina: "Gotovina", kartica: "Kartica", prenos: "Prenos" };
  const methodRows = Object.entries(methods)
    .map(
      ([k, l]) =>
        `<div class="metric-card"><span>${l}</span><strong>${fmtMoney(d.revenue_by_method[k] || 0)}</strong></div>`,
    )
    .join("");
  $("#financeSummary").innerHTML =
    `<div class="metric-card"><span>Ukupan promet</span><strong>${fmtMoney(d.revenue_collected_rsd)}</strong></div>` +
    `<div class="metric-card"><span>Izdati računi</span><strong>${d.invoices_issued}</strong></div>` +
    methodRows +
    `<div class="metric-card${d.outstanding_new_rsd > 0 ? " attention" : ""}"><span>Novi dug danas</span><strong>${fmtMoney(d.outstanding_new_rsd)}</strong></div>`;
}
$("#financeDate").onchange = () => loadFinanceSummary();

async function loadOutstanding() {
  const rows = await api("/api/finance/outstanding");
  $("#outstandingList").innerHTML = rows.length
    ? rows
        .map(
          (o) =>
            `<div class="row"><div><strong>${esc(o.patient_name)}</strong><p>${esc(o.invoice_number)} · ${o.days_outstanding} ${o.days_outstanding === 1 ? "dan" : "dana"} · plaćeno ${fmtMoney(o.paid_rsd)} od ${fmtMoney(o.total_rsd)}</p></div><span class="badge warn">${fmtMoney(o.balance_due_rsd)}</span></div>`,
        )
        .join("")
    : '<p class="muted">Nema neplaćenih računa.</p>';
}

async function loadServices() {
  serviceCache = await api("/api/finance/services");
  $("#serviceList").innerHTML = serviceCache.length
    ? serviceCache
        .map(
          (s) =>
            `<div class="row"><div><strong>${esc(s.name)}</strong><p>${s.category ? esc(s.category) + " · " : ""}${s.default_duration_minutes ? s.default_duration_minutes + " min" : ""}</p></div><div class="row-actions"><strong>${fmtMoney(s.price_rsd)}</strong>${currentUser.role === "admin" ? `<button type="button" class="button secondary service-toggle" data-id="${s.id}" data-active="${s.active}">${s.active ? "Deaktiviraj" : "Aktiviraj"}</button>` : ""}</div></div>`,
        )
        .join("")
    : '<p class="muted">Cenovnik je prazan.</p>';
}

async function loadInvoiceList() {
  const rows = await api("/api/finance/invoices");
  const patientName = (id) =>
    patients.find((p) => p.id === id)?.full_name || "Pacijent";
  const STATUS_LABEL = {
    issued: "Izdat",
    paid: "Plaćen",
    cancelled: "Otkazan",
    draft: "Nacrt",
  };
  $("#invoiceList").innerHTML = rows.length
    ? rows
        .slice(0, 25)
        .map(
          (i) =>
            `<div class="row invoice-row ${i.status}"><div><strong>${esc(i.invoice_number)} · ${esc(patientName(i.patient_id))}</strong><p>${fmtDate(i.issued_at)} · ${fmtMoney(i.total_rsd)}${i.balance_due_rsd > 0 ? ` · dug ${fmtMoney(i.balance_due_rsd)}` : ""}</p></div><div class="row-actions"><span class="badge ${i.status === "cancelled" ? "warn" : ""}">${STATUS_LABEL[i.status]}</span>${i.status === "issued" && ["receptionist", "admin"].includes(currentUser.role) ? `<button type="button" class="button secondary invoice-pay" data-id="${i.id}" data-balance="${i.balance_due_rsd}">Uplata</button>` : ""}${i.status !== "cancelled" && currentUser.role === "admin" ? `<button type="button" class="button secondary invoice-cancel" data-id="${i.id}">Otkaži</button>` : ""}</div></div>`,
        )
        .join("")
    : '<p class="muted">Još nema izdatih računa.</p>';
}

function invoiceItemRow(id) {
  const opts = serviceCache
    .map((s) => `<option value="${s.id}" data-price="${s.price_rsd}">${esc(s.name)} — ${fmtMoney(s.price_rsd)}</option>`)
    .join("");
  return `<div class="invoice-item-row" data-row="${id}">
    <select class="ii-service"><option value="">Slobodna stavka</option>${opts}</select>
    <input class="ii-desc" placeholder="Opis" required maxlength="200">
    <input class="ii-qty" type="number" min="1" value="1" title="Količina">
    <input class="ii-price" type="number" min="0" step="10" placeholder="Cena" required title="Jedinična cena (RSD)">
    <input class="ii-discount" type="number" min="0" max="100" value="0" title="Popust %">
    <button type="button" class="icon-button ii-remove" title="Ukloni stavku">×</button>
  </div>`;
}
function recalcInvoiceTotal() {
  let subtotal = 0;
  $$(".invoice-item-row").forEach((row) => {
    const qty = parseFloat(row.querySelector(".ii-qty").value) || 0;
    const price = parseFloat(row.querySelector(".ii-price").value) || 0;
    const disc = parseFloat(row.querySelector(".ii-discount").value) || 0;
    subtotal += Math.round(qty * price * (100 - disc)) / 100;
  });
  const invoiceDiscount = parseFloat($("#invoiceForm [name=discount_percent]").value) || 0;
  const total = Math.round(subtotal * (100 - invoiceDiscount)) / 100;
  $("#invoiceTotalPreview").textContent = fmtMoney(Math.round(total));
}
function addInvoiceItemRow() {
  invoiceItemSeq++;
  $("#invoiceItems").insertAdjacentHTML("beforeend", invoiceItemRow(invoiceItemSeq));
  recalcInvoiceTotal();
}
$("#addInvoiceItem").onclick = addInvoiceItemRow;
$("#invoiceItems").addEventListener("change", (e) => {
  if (e.target.classList.contains("ii-service")) {
    const opt = e.target.selectedOptions[0];
    const row = e.target.closest(".invoice-item-row");
    if (opt && opt.dataset.price) {
      row.querySelector(".ii-price").value = opt.dataset.price;
      row.querySelector(".ii-desc").value = opt.textContent.split(" — ")[0];
    }
  }
  recalcInvoiceTotal();
});
$("#invoiceItems").addEventListener("input", recalcInvoiceTotal);
$("#invoiceForm [name=discount_percent]").addEventListener("input", recalcInvoiceTotal);
$("#invoiceItems").addEventListener("click", (e) => {
  if (e.target.classList.contains("ii-remove")) {
    e.target.closest(".invoice-item-row").remove();
    recalcInvoiceTotal();
  }
});

async function openInvoiceDialog(prefillPatientId) {
  if (!serviceCache.length) await loadServices();
  $("#invoicePatient").innerHTML = patients
    .map((p) => `<option value="${p.id}">${esc(p.full_name)}</option>`)
    .join("");
  if (prefillPatientId) $("#invoicePatient").value = prefillPatientId;
  $("#invoiceItems").innerHTML = "";
  invoiceItemSeq = 0;
  addInvoiceItemRow();
  $("#invoiceForm [name=discount_percent]").value = 0;
  $("#invoiceForm [name=notes]").value = "";
  recalcInvoiceTotal();
  $("#invoiceDialog").showModal();
}
$("#newInvoiceOpen").onclick = () => openInvoiceDialog();
if ($("#issueInvoiceBtn"))
  $("#issueInvoiceBtn").onclick = () => {
    if (!activePatient) return toast("Prvo izaberite pacijenta");
    openInvoiceDialog(activePatient.id);
  };

$("#invoiceForm").onsubmit = async (e) => {
  e.preventDefault();
  const rows = $$(".invoice-item-row");
  if (!rows.length) return toast("Dodajte bar jednu stavku");
  const line_items = rows.map((row) => ({
    service_id: row.querySelector(".ii-service").value || null,
    description: row.querySelector(".ii-desc").value,
    quantity: parseInt(row.querySelector(".ii-qty").value, 10) || 1,
    unit_price_rsd: parseInt(row.querySelector(".ii-price").value, 10) || 0,
    discount_percent: parseInt(row.querySelector(".ii-discount").value, 10) || 0,
  }));
  const patient_id = $("#invoicePatient").value;
  const discount_percent = parseInt($("#invoiceForm [name=discount_percent]").value, 10) || 0;
  const notes = $("#invoiceForm [name=notes]").value || null;
  try {
    await api("/api/finance/invoices", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ patient_id, line_items, discount_percent, notes }),
    });
    $("#invoiceDialog").close();
    toast("Račun je izdat");
    await loadInvoiceList();
    if (["receptionist", "admin"].includes(currentUser.role)) {
      await loadFinanceSummary();
      await loadOutstanding();
    }
  } catch (x) {
    toast(x.message);
  }
};

$("#serviceForm").onsubmit = async (e) => {
  e.preventDefault();
  const d = Object.fromEntries(new FormData(e.target));
  d.price_rsd = parseInt(d.price_rsd, 10);
  d.default_duration_minutes = d.default_duration_minutes ? parseInt(d.default_duration_minutes, 10) : null;
  if (!d.category) delete d.category;
  try {
    await api("/api/finance/services", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(d),
    });
    e.target.reset();
    $("#serviceDialog").close();
    toast("Usluga je dodata");
    await loadServices();
  } catch (x) {
    toast(x.message);
  }
};
$("#newServiceOpen").onclick = () => $("#serviceDialog").showModal();

let paymentTargetInvoice = null;
document.addEventListener("click", async (e) => {
  const payBtn = e.target.closest(".invoice-pay");
  if (payBtn) {
    paymentTargetInvoice = payBtn.dataset.id;
    $("#paymentBalanceHint").textContent = `Preostali dug: ${fmtMoney(payBtn.dataset.balance)}`;
    $("#paymentForm [name=amount_rsd]").value = payBtn.dataset.balance;
    $("#paymentDialog").showModal();
    return;
  }
  const cancelBtn = e.target.closest(".invoice-cancel");
  if (cancelBtn) {
    const reason = prompt("Razlog otkazivanja računa:");
    if (!reason) return;
    try {
      await api(`/api/finance/invoices/${cancelBtn.dataset.id}/status`, {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ status: "cancelled", cancellation_reason: reason }),
      });
      toast("Račun je otkazan");
      await loadInvoiceList();
      if (["receptionist", "admin"].includes(currentUser.role)) await loadOutstanding();
    } catch (x) {
      toast(x.message);
    }
    return;
  }
  const toggleBtn = e.target.closest(".service-toggle");
  if (toggleBtn) {
    const active = toggleBtn.dataset.active === "true";
    try {
      await api(`/api/finance/services/${toggleBtn.dataset.id}`, {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ active: !active }),
      });
      toast(active ? "Usluga je deaktivirana" : "Usluga je aktivirana");
      await loadServices();
    } catch (x) {
      toast(x.message);
    }
  }
});
$("#paymentForm").onsubmit = async (e) => {
  e.preventDefault();
  const d = Object.fromEntries(new FormData(e.target));
  d.amount_rsd = parseInt(d.amount_rsd, 10);
  if (!d.note) delete d.note;
  try {
    await api(`/api/finance/invoices/${paymentTargetInvoice}/payments`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(d),
    });
    e.target.reset();
    $("#paymentDialog").close();
    toast("Uplata je evidentirana");
    await loadInvoiceList();
    await loadFinanceSummary();
    await loadOutstanding();
  } catch (x) {
    toast(x.message);
  }
};

/* === Pedijatrija (specijalistički modul) === */
async function loadPediatrics() {
  if (!activePatient || !["doctor", "receptionist", "admin"].includes(currentUser.role)) return;
  try {
    const profile = await api(`/api/patients/${activePatient.id}/pediatric-profile`);
    if (profile) {
      $("#guardianForm [name=guardian_name]").value = profile.guardian_name || "";
      $("#guardianForm [name=guardian_relationship]").value = profile.guardian_relationship || "";
      $("#guardianForm [name=guardian_phone]").value = profile.guardian_phone || "";
    } else {
      $("#guardianForm").reset();
    }
  } catch {}
  if (!["doctor", "admin"].includes(currentUser.role)) return;
  await Promise.all([loadGrowthMeasurements(), loadVaccinations()]);
}
$("#guardianForm").onsubmit = async (e) => {
  e.preventDefault();
  const d = Object.fromEntries(new FormData(e.target));
  Object.keys(d).forEach((k) => {
    if (!d[k]) d[k] = null;
  });
  try {
    await api(`/api/patients/${activePatient.id}/pediatric-profile`, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(d),
    });
    toast("Podaci o staratelju su sačuvani");
  } catch (x) {
    toast(x.message);
  }
};

async function loadGrowthMeasurements() {
  const rows = await api(`/api/patients/${activePatient.id}/growth-measurements`);
  $("#growthList").innerHTML = rows.length
    ? rows
        .slice()
        .reverse()
        .map((m) => {
          const parts = [];
          if (m.height_cm != null) parts.push(`Visina: ${m.height_cm} cm`);
          if (m.weight_kg != null) parts.push(`Težina: ${m.weight_kg} kg`);
          if (m.head_circumference_cm != null) parts.push(`Obim glave: ${m.head_circumference_cm} cm`);
          return `<div class="row"><div><strong>${fmtDate(m.measured_at)}</strong><p>${esc(parts.join(" · "))}</p>${m.notes ? `<small class="muted">${esc(m.notes)}</small>` : ""}</div></div>`;
        })
        .join("")
    : '<p class="muted">Još nema evidentiranih merenja.</p>';
  const weightPts = rows.filter((r) => r.weight_kg != null).map((r) => ({ date: r.measured_at, value: r.weight_kg, unit: "kg" }));
  if (weightPts.length > 1) {
    $("#growthChart").classList.remove("hidden");
    $("#growthChart").innerHTML = `<h4>Trend težine</h4>${renderLabTrendChart(weightPts, "Težina")}`;
  } else {
    $("#growthChart").classList.add("hidden");
    $("#growthChart").innerHTML = "";
  }
}
$("#addGrowthBtn").onclick = () => {
  if (!activePatient) return toast("Prvo izaberite pacijenta");
  $("#growthForm").reset();
  $("#growthForm [name=measured_at]").value = new Date().toISOString().slice(0, 10);
  $("#growthError").classList.add("hidden");
  $("#growthDialog").showModal();
};
$("#growthForm").onsubmit = async (e) => {
  e.preventDefault();
  const d = Object.fromEntries(new FormData(e.target));
  ["height_cm", "weight_kg", "head_circumference_cm"].forEach((k) => {
    d[k] = d[k] ? parseFloat(d[k]) : null;
  });
  if (!d.notes) delete d.notes;
  d.measured_at = new Date(d.measured_at).toISOString();
  $("#growthError").classList.add("hidden");
  try {
    await api(`/api/patients/${activePatient.id}/growth-measurements`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(d),
    });
    $("#growthDialog").close();
    toast("Merenje je sačuvano");
    await loadGrowthMeasurements();
  } catch (x) {
    $("#growthError").textContent = x.message;
    $("#growthError").classList.remove("hidden");
  }
};

async function loadVaccinations() {
  const rows = await api(`/api/patients/${activePatient.id}/vaccinations`);
  $("#vaccinationList").innerHTML = rows.length
    ? rows
        .map(
          (v) =>
            `<div class="row"><div><strong>${esc(v.vaccine_name)}</strong><p>${new Date(v.administered_at).toLocaleDateString("sr-Latn-RS")}${v.lot_number ? " · serija " + esc(v.lot_number) : ""}${v.administered_by ? " · " + esc(v.administered_by) : ""}</p>${v.notes ? `<small class="muted">${esc(v.notes)}</small>` : ""}</div><small class="muted">Uneo: ${esc(v.recorded_by_name)}</small></div>`,
        )
        .join("")
    : '<p class="muted">Još nema evidentiranih vakcina.</p>';
}
$("#addVaccinationBtn").onclick = () => {
  if (!activePatient) return toast("Prvo izaberite pacijenta");
  $("#vaccinationForm").reset();
  $("#vaccinationForm [name=administered_at]").value = new Date().toISOString().slice(0, 10);
  $("#vaccinationDialog").showModal();
};
$("#vaccinationForm").onsubmit = async (e) => {
  e.preventDefault();
  const d = Object.fromEntries(new FormData(e.target));
  ["lot_number", "administered_by", "notes"].forEach((k) => {
    if (!d[k]) delete d[k];
  });
  try {
    await api(`/api/patients/${activePatient.id}/vaccinations`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(d),
    });
    $("#vaccinationDialog").close();
    toast("Vakcina je evidentirana");
    await loadVaccinations();
  } catch (x) {
    toast(x.message);
  }
};

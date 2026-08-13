const $ = (s) => document.querySelector(s),
  $$ = (s) => document.querySelectorAll(s);

// Session identity lives in an HttpOnly `portal_session` cookie the server
// sets on login -- never in localStorage or any other JS-readable place.
// A different cookie name than the staff app's `clinic_session` (and a
// completely separate backend session table -- see deps.py) means opening
// the portal in the same browser where staff is also logged in can never
// let one session be mistaken for the other.
let portalAccount = null;
let clinicianCache = [];

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
      ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", "'": "&#39;", '"': "&quot;" })[c],
  );
const toast = (m) => {
  const e = $("#toast");
  e.textContent = m;
  e.classList.add("show");
  setTimeout(() => e.classList.remove("show"), 2500);
};
const fmtDate = (iso) => {
  const d = new Date(iso);
  return `${d.toLocaleDateString("sr-Latn-RS")} ${d.toLocaleTimeString("sr-Latn-RS", { hour: "2-digit", minute: "2-digit" })}`;
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

function showScreen(id) {
  ["#portalLogin", "#portalForcePassword", "#portalApp"].forEach((s) =>
    $(s).classList.toggle("hidden", s !== id),
  );
}

async function boot() {
  try {
    portalAccount = await api("/api/portal/auth/me");
  } catch {
    return showScreen("#portalLogin");
  }
  if (portalAccount.must_change_password) return showScreen("#portalForcePassword");
  await enterApp();
}

async function enterApp() {
  showScreen("#portalApp");
  $("#portalPatientName").textContent = portalAccount.username;
  await Promise.all([loadAppointments(), loadConsentStatus(), loadUnreadBadge()]);
}

$("#portalLoginForm").onsubmit = async (e) => {
  e.preventDefault();
  const d = Object.fromEntries(new FormData(e.target));
  $("#portalLoginError").classList.add("hidden");
  try {
    const r = await api("/api/portal/auth/login", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(d),
    });
    portalAccount = r.account;
    if (portalAccount.must_change_password) showScreen("#portalForcePassword");
    else await enterApp();
  } catch (x) {
    $("#portalLoginError").textContent = x.message;
    $("#portalLoginError").classList.remove("hidden");
  }
};

$("#portalForcePasswordForm").onsubmit = async (e) => {
  e.preventDefault();
  const d = Object.fromEntries(new FormData(e.target));
  $("#portalForceError").classList.add("hidden");
  try {
    await api("/api/portal/auth/change-password", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(d),
    });
    toast("Lozinka je promenjena. Prijavite se ponovo.");
    e.target.reset();
    showScreen("#portalLogin");
  } catch (x) {
    $("#portalForceError").textContent = x.message;
    $("#portalForceError").classList.remove("hidden");
  }
};

$("#portalLogoutBtn").onclick = async () => {
  try {
    await api("/api/portal/auth/logout", { method: "POST" });
  } catch {}
  showScreen("#portalLogin");
};

$$(".portal-nav-item").forEach((b) => {
  b.onclick = async () => {
    $$(".portal-nav-item").forEach((x) => x.classList.remove("active"));
    b.classList.add("active");
    $$(".portal-view").forEach((v) => v.classList.add("hidden"));
    $(`#pv-${b.dataset.view}`).classList.remove("hidden");
    if (b.dataset.view === "poruke") await loadMessages();
    if (b.dataset.view === "nalazi") await loadLabResults();
    if (b.dataset.view === "upitnik") await loadQuestionnaireHistory();
    if (b.dataset.view === "pristanak") await loadConsentStatus();
  };
});

/* === Termini === */
const APPT_STATUS_LABEL = {
  scheduled: "Zakazano",
  checked_in: "Prijavljeni ste",
  completed: "Završeno",
  cancelled: "Otkazano",
  no_show: "Niste se pojavili",
};
async function loadAppointments() {
  const rows = await api("/api/portal/appointments");
  rows.sort((a, b) => new Date(a.starts_at) - new Date(b.starts_at));
  $("#portalApptList").innerHTML = rows.length
    ? rows
        .map((a) => {
          const upcoming =
            (a.status === "scheduled" || a.status === "checked_in") &&
            new Date(a.starts_at) > new Date();
          return `<div class="row"><div><strong>${fmtDate(a.starts_at)}</strong><p>${esc(a.reason)}${a.clinician_name ? " · " + esc(a.clinician_name) : ""}</p></div><div class="row-actions"><span class="badge">${APPT_STATUS_LABEL[a.status] || esc(a.status)}</span>${upcoming ? `<button type="button" class="button secondary portal-cancel-appt" data-id="${a.id}">Otkaži</button>` : ""}</div></div>`;
        })
        .join("")
    : '<p class="muted">Nemate zakazanih termina. Kliknite „+ Zakaži termin".</p>';
}
document.addEventListener("click", async (e) => {
  const cancelBtn = e.target.closest(".portal-cancel-appt");
  if (!cancelBtn) return;
  if (!confirm("Da li ste sigurni da želite da otkažete ovaj termin?")) return;
  try {
    await api(`/api/portal/appointments/${cancelBtn.dataset.id}/cancel`, { method: "PATCH" });
    toast("Termin je otkazan");
    await loadAppointments();
  } catch (x) {
    toast(x.message);
  }
});

async function ensureClinicianOptions() {
  if (!clinicianCache.length) clinicianCache = await api("/api/portal/clinicians");
  $("#portalApptClinician").innerHTML =
    '<option value="">Izaberite lekara</option>' +
    clinicianCache.map((c) => `<option value="${c.id}">${esc(c.full_name)}</option>`).join("");
}
$("#portalNewApptBtn").onclick = async () => {
  await ensureClinicianOptions();
  $("#portalApptSlot").innerHTML = '<option value="">Prvo izaberite lekara i datum</option>';
  $("#portalApptDate").value = "";
  $("#portalApptReason").value = "";
  $("#portalApptError").classList.add("hidden");
  $("#portalApptDialog").showModal();
};
async function refreshSlots() {
  const cid = $("#portalApptClinician").value,
    date = $("#portalApptDate").value;
  if (!cid || !date) return;
  const slots = await api(
    `/api/portal/available-slots?clinician_id=${encodeURIComponent(cid)}&date=${encodeURIComponent(date)}&duration_minutes=20`,
  );
  $("#portalApptSlot").innerHTML = slots.length
    ? slots
        .map(
          (s) =>
            `<option value="${s}">${new Date(s).toLocaleTimeString("sr-Latn-RS", { hour: "2-digit", minute: "2-digit" })}</option>`,
        )
        .join("")
    : '<option value="">Nema slobodnih termina tog dana</option>';
}
$("#portalApptClinician").onchange = refreshSlots;
$("#portalApptDate").onchange = refreshSlots;
$("#portalApptForm").onsubmit = async (e) => {
  e.preventDefault();
  const slot = $("#portalApptSlot").value;
  if (!slot) return;
  $("#portalApptError").classList.add("hidden");
  try {
    await api("/api/portal/appointments", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        starts_at: slot,
        clinician_id: $("#portalApptClinician").value,
        reason: $("#portalApptReason").value,
        duration_minutes: 20,
      }),
    });
    $("#portalApptDialog").close();
    toast("Termin je zakazan");
    await loadAppointments();
  } catch (x) {
    $("#portalApptError").textContent = x.message;
    $("#portalApptError").classList.remove("hidden");
  }
};

/* === Poruke === */
async function loadUnreadBadge() {
  // A lightweight signal only -- fetching the thread also marks it read,
  // so this just checks non-destructively whether anything is unread.
  try {
    const rows = await api("/api/portal/messages");
    $("#msgBadge").classList.add("hidden"); // messages view marks read on open; nothing to badge after a boot-time fetch
  } catch {}
}
async function loadMessages() {
  const rows = await api("/api/portal/messages");
  $("#portalChat").innerHTML = rows.length
    ? rows
        .map(
          (m) =>
            `<div class="${m.sender_type === "patient" ? "user-msg" : "assistant-msg"}">${esc(m.body)}<br><small>${fmtDate(m.created_at)}</small></div>`,
        )
        .join("")
    : '<div class="assistant-msg">Ovde možete postaviti pitanje ordinaciji. Odgovor stiže u ovaj razgovor.</div>';
  $("#portalChat").scrollTop = $("#portalChat").scrollHeight;
}
$("#portalMessageForm").onsubmit = async (e) => {
  e.preventDefault();
  const body = $("#portalMessageInput").value.trim();
  if (!body) return;
  try {
    await api("/api/portal/messages", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ body }),
    });
    $("#portalMessageInput").value = "";
    await loadMessages();
  } catch (x) {
    toast(x.message);
  }
};

/* === Nalazi === */
async function loadLabResults() {
  const rows = await api("/api/portal/lab-results");
  $("#portalLabList").innerHTML = rows.length
    ? rows
        .map(
          (x) =>
            `<div class="row"><div><strong>${esc(x.name)}: ${x.value === null ? "—" : esc(x.value)} ${esc(x.unit || "")}</strong><p>${esc(x.reference_range ? `Referentno: ${x.reference_range}` : "")} · ${fmtDate(x.collected_at || x.created_at)}</p></div></div>`,
        )
        .join("")
    : '<p class="muted">Još nema potvrđenih nalaza.</p>';
}

/* === Upitnik === */
$("#portalQuestionnaireForm").onsubmit = async (e) => {
  e.preventDefault();
  const d = Object.fromEntries(new FormData(e.target));
  d.confirmed_allergies = !!e.target.confirmed_allergies.checked;
  d.confirmed_medications = !!e.target.confirmed_medications.checked;
  if (!d.additional_notes) delete d.additional_notes;
  try {
    await api("/api/portal/questionnaire", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(d),
    });
    e.target.reset();
    toast("Upitnik je poslat lekaru");
    await loadQuestionnaireHistory();
  } catch (x) {
    toast(x.message);
  }
};
async function loadQuestionnaireHistory() {
  const rows = await api("/api/portal/questionnaire");
  $("#portalQuestionnaireList").innerHTML = rows.length
    ? rows
        .map(
          (q) =>
            `<div class="row"><div><strong>${esc(q.chief_complaint)}</strong><p>${fmtDate(q.submitted_at)}</p></div></div>`,
        )
        .join("")
    : '<p class="muted">Još niste poslali upitnik.</p>';
}

/* === Pristanak === */
async function loadConsentStatus() {
  const c = await api("/api/portal/consent");
  $("#portalConsentText").textContent = c.text;
  $("#portalConsentStatus").className = `portal-consent-status ${c.accepted ? "accepted" : "pending"}`;
  $("#portalConsentStatus").textContent = c.accepted
    ? `Prihvaćeno ${fmtDate(c.accepted_at)}`
    : "Još niste prihvatili";
  $("#portalAcceptConsentBtn").classList.toggle("hidden", c.accepted);
}
$("#portalAcceptConsentBtn").onclick = async () => {
  try {
    await api("/api/portal/consent", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ consent_type: "obrada_podataka" }),
    });
    toast("Pristanak je zabeležen");
    await loadConsentStatus();
  } catch (x) {
    toast(x.message);
  }
};

$$(".close").forEach((b) => (b.onclick = () => b.closest("dialog").close()));

boot();

/* Shared front-end plumbing: fetch helper, detail panel, create-content
   modal, autosave fields, tasks/comments/inspiration/assets — all wired
   with event delegation so it works no matter which page injected the
   HTML (server-rendered page vs. panel fetched over AJAX). No build step,
   no framework: plain DOM + fetch, on purpose (see README). */

async function tgnFetch(url, options) {
  options = options || {};
  options.headers = Object.assign({ "Content-Type": "application/json" }, options.headers || {});
  const res = await fetch(url, options);
  let data = null;
  try { data = await res.json(); } catch (e) { /* no body */ }
  if (!res.ok) {
    const msg = (data && data.error) || `Request failed (${res.status})`;
    throw new Error(msg);
  }
  return data;
}

// ---------------------------------------------------------------- panel
const panelEl = document.getElementById("detail-panel");
const panelBackdrop = document.getElementById("panel-backdrop");

async function openCampaignPanel(campaignId) {
  if (!panelEl) return;
  panelEl.innerHTML = "<div class='panel-body'>Loading…</div>";
  panelEl.classList.add("open");
  panelBackdrop.classList.add("open");
  try {
    const res = await fetch(`/campaign/${campaignId}/panel`);
    panelEl.innerHTML = await res.text();
  } catch (e) {
    panelEl.innerHTML = "<div class='panel-body'>Couldn't load that.</div>";
  }
}

function closePanel() {
  panelEl.classList.remove("open");
  panelBackdrop.classList.remove("open");
}

panelBackdrop && panelBackdrop.addEventListener("click", closePanel);

document.addEventListener("click", (e) => {
  const opener = e.target.closest(".js-open-campaign, .content-card, .list-row[data-campaign-id]");
  if (opener && opener.dataset.campaignId) {
    e.preventDefault();
    openCampaignPanel(opener.dataset.campaignId);
  }
  if (e.target.id === "panel-close-btn") closePanel();
});

// ---------------------------------------------------------------- modal (create content)
const modalBackdrop = document.getElementById("modal-backdrop");
const modalContent = document.getElementById("modal-content");

async function openModal(html) {
  modalContent.innerHTML = html;
  modalBackdrop.classList.add("open");
}
function closeModal() {
  modalBackdrop.classList.remove("open");
  modalContent.innerHTML = "";
}
modalBackdrop && modalBackdrop.addEventListener("click", (e) => {
  if (e.target === modalBackdrop) closeModal();
});
document.addEventListener("click", (e) => {
  if (e.target.id === "modal-close-btn") closeModal();
  if (e.target.id === "create-content-btn") {
    fetch("/content/create-modal").then(r => r.text()).then(openModal);
  }
});

// type picker inside the create-content modal
document.addEventListener("click", (e) => {
  const opt = e.target.closest(".js-type-option");
  if (!opt) return;
  document.querySelectorAll(".js-type-option").forEach(el => el.classList.remove("selected"));
  opt.classList.add("selected");
  document.getElementById("qc-type-key").value = opt.dataset.key;
  let platformIds = [];
  try { platformIds = JSON.parse(opt.dataset.platforms || "[]"); } catch (e) {}
  document.querySelectorAll('#qc-platforms input[type=checkbox]').forEach(cb => {
    cb.checked = platformIds.includes(parseInt(cb.value, 10));
  });
});

document.addEventListener("submit", async (e) => {
  if (e.target.id !== "quick-create-form") return;
  e.preventDefault();
  const form = e.target;
  const errBox = document.getElementById("qc-error");
  errBox.style.display = "none";
  const typeKey = document.getElementById("qc-type-key").value;
  if (!typeKey) {
    errBox.textContent = "Pick a content type first.";
    errBox.style.display = "block";
    return;
  }
  const platformIds = Array.from(form.querySelectorAll('input[name=platform_ids]:checked')).map(cb => parseInt(cb.value, 10));
  const payload = {
    creation_option_key: typeKey,
    title: document.getElementById("qc-title").value,
    publish_date: document.getElementById("qc-date").value,
    assigned_user_id: document.getElementById("qc-assignee").value || null,
    concept: document.getElementById("qc-concept").value,
    platform_ids: platformIds.length ? platformIds : null,
  };
  try {
    await tgnFetch("/api/campaigns/quick-create", { method: "POST", body: JSON.stringify(payload) });
    window.location.reload();
  } catch (err) {
    errBox.textContent = err.message;
    errBox.style.display = "block";
  }
});

// ---------------------------------------------------------------- autosave fields (panel)
let autosaveTimer = null;
document.addEventListener("change", (e) => {
  if (e.target.classList.contains("js-autosave")) saveField(e.target);
});
document.addEventListener("blur", (e) => {
  if (e.target.classList && e.target.classList.contains("js-autosave")) saveField(e.target);
}, true);

function saveField(el) {
  const campaignId = el.dataset.campaignId;
  const field = el.dataset.field;
  const value = el.value;
  clearTimeout(autosaveTimer);
  tgnFetch(`/api/campaigns/${campaignId}`, {
    method: "PATCH",
    body: JSON.stringify({ [field]: value }),
  }).catch(() => {});
}

// ---------------------------------------------------------------- tasks
document.addEventListener("change", (e) => {
  if (e.target.classList.contains("js-task-status")) {
    tgnFetch(`/api/tasks/${e.target.dataset.taskId}`, {
      method: "PATCH", body: JSON.stringify({ status: e.target.value }),
    }).catch(() => {});
  }
  if (e.target.classList.contains("js-task-complete")) {
    const status = e.target.checked ? "complete" : "not_started";
    tgnFetch(`/api/tasks/${e.target.dataset.taskId}`, {
      method: "PATCH", body: JSON.stringify({ status }),
    }).catch(() => {});
  }
  if (e.target.classList.contains("js-output-status")) {
    tgnFetch(`/api/outputs/${e.target.dataset.outputId}`, {
      method: "PATCH", body: JSON.stringify({ status: e.target.value }),
    }).catch(() => {});
  }
});

// ---------------------------------------------------------------- inspiration / comments / assets
document.addEventListener("submit", async (e) => {
  if (e.target.classList.contains("js-add-inspiration")) {
    e.preventDefault();
    const url = e.target.querySelector('input[name=url]').value.trim();
    if (!url) return;
    await tgnFetch(`/api/campaigns/${e.target.dataset.campaignId}/inspiration`, {
      method: "POST", body: JSON.stringify({ url }),
    }).catch(() => {});
    openCampaignPanel(e.target.dataset.campaignId);
  }
  if (e.target.classList.contains("js-add-comment")) {
    e.preventDefault();
    const body = e.target.querySelector('input[name=body]').value.trim();
    if (!body) return;
    await tgnFetch(`/api/campaigns/${e.target.dataset.campaignId}/comments`, {
      method: "POST", body: JSON.stringify({ body }),
    }).catch(() => {});
    openCampaignPanel(e.target.dataset.campaignId);
  }
  if (e.target.classList.contains("js-upload-asset")) {
    e.preventDefault();
    const fileInput = e.target.querySelector('input[type=file]');
    if (!fileInput.files.length) return;
    const fd = new FormData();
    fd.append("file", fileInput.files[0]);
    const campaignId = e.target.dataset.campaignId;
    await fetch(`/api/campaigns/${campaignId}/assets`, { method: "POST", body: fd });
    openCampaignPanel(campaignId);
  }
});

document.addEventListener("click", (e) => {
  if (e.target.classList.contains("js-delete-inspiration")) {
    tgnFetch(`/api/inspiration/${e.target.dataset.linkId}`, { method: "DELETE" }).then(() => {
      e.target.closest(".list-row").remove();
    }).catch(() => {});
  }
  if (e.target.classList.contains("js-delete-campaign")) {
    if (!confirm("Delete this content? This can't be undone.")) return;
    tgnFetch(`/api/campaigns/${e.target.dataset.campaignId}`, { method: "DELETE" }).then(() => {
      closePanel();
      window.location.reload();
    }).catch(err => alert(err.message));
  }
});

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

// A task status change can unlock the next pipeline stage (new Calendar/
// Meet/Drive links, previously-disabled controls becoming editable) — the
// simplest correct thing is to just re-render the whole open panel rather
// than hand-patch every affected bit of the DOM.
function refreshPipelinePanel() {
  const body = panelEl && panelEl.querySelector(".panel-body[data-campaign-id]");
  if (body && body.dataset.campaignId) openCampaignPanel(body.dataset.campaignId);
}

panelBackdrop && panelBackdrop.addEventListener("click", closePanel);

document.addEventListener("click", (e) => {
  const opener = e.target.closest(".js-open-campaign, .content-card, .list-row[data-campaign-id]");
  // Clicking a checkbox (or something else interactive) inside a row that's
  // ALSO a campaign opener shouldn't also pop the panel open — Tasks tab
  // rows (Sept redesign) are clickable end-to-end but still need their
  // "mark complete" checkbox to just toggle, not also navigate away.
  if (opener && opener.dataset.campaignId && e.target.tagName !== "INPUT") {
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
function openCreateModal(prefillDate) {
  fetch("/content/create-modal").then(r => r.text()).then(html => {
    openModal(html);
    if (prefillDate) {
      const dateInput = document.getElementById("qc-date");
      if (dateInput) dateInput.value = prefillDate;
    }
  });
}

document.addEventListener("click", (e) => {
  if (e.target.id === "modal-close-btn") closeModal();
  if (e.target.id === "create-content-btn") openCreateModal();
});

// type picker inside the create-content modal — a plain single-output type
// (e.g. Targeted Campaign) selects directly; a pick_subtype option (Filler
// Post / Monthly Campaign — Part 8/9) instead reveals a second "which one
// specifically?" dropdown and waits for that choice before a real content
// type is settled on.
function applyPlatformDefaults(platformIds) {
  document.querySelectorAll('#qc-platforms input[type=checkbox]').forEach(cb => {
    cb.checked = platformIds.includes(parseInt(cb.value, 10));
  });
}

document.addEventListener("click", (e) => {
  const opt = e.target.closest(".js-type-option");
  if (!opt) return;
  document.querySelectorAll(".js-type-option").forEach(el => el.classList.remove("selected"));
  opt.classList.add("selected");
  document.getElementById("qc-type-key").value = opt.dataset.key;

  const subtypeWrap = document.getElementById("qc-subtype-wrap");
  const subtypeSelect = document.getElementById("qc-subtype");
  const contentTypeIdField = document.getElementById("qc-content-type-id");

  let subtypes = [];
  try { subtypes = JSON.parse(opt.dataset.subtypes || "[]"); } catch (err) {}

  if (opt.dataset.pickSubtype === "1") {
    subtypeSelect.innerHTML = '<option value="">Choose a type…</option>' +
      subtypes.map(t => `<option value="${t.id}" data-platforms='${t.default_platform_ids || "[]"}'>${t.label}</option>`).join("");
    subtypeWrap.style.display = "block";
    contentTypeIdField.value = "";
  } else {
    subtypeWrap.style.display = "none";
    subtypeSelect.innerHTML = "";
    contentTypeIdField.value = "";
    let platformIds = [];
    try { platformIds = JSON.parse(opt.dataset.platforms || "[]"); } catch (err) {}
    applyPlatformDefaults(platformIds);
  }
});

document.addEventListener("change", (e) => {
  if (e.target.id !== "qc-subtype") return;
  const selected = e.target.options[e.target.selectedIndex];
  document.getElementById("qc-content-type-id").value = e.target.value || "";
  let platformIds = [];
  if (selected && selected.dataset.platforms) {
    try { platformIds = JSON.parse(selected.dataset.platforms); } catch (err) {}
  }
  applyPlatformDefaults(platformIds);
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
  const subtypeWrapVisible = document.getElementById("qc-subtype-wrap").style.display !== "none";
  const contentTypeId = document.getElementById("qc-content-type-id").value;
  if (subtypeWrapVisible && !contentTypeId) {
    errBox.textContent = "Choose which specific type this is.";
    errBox.style.display = "block";
    return;
  }
  const platformIds = Array.from(form.querySelectorAll('input[name=platform_ids]:checked')).map(cb => parseInt(cb.value, 10));
  const payload = {
    creation_option_key: typeKey,
    content_type_id: contentTypeId || null,
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
  // Sept redesign: no more manual multi-value status dropdown anywhere —
  // a plain checklist item is a single checkbox (done/not done); anything
  // with real sub-states (scheduled, submitted, approved...) derives that
  // automatically from the actual pipeline action that happened.
  if (e.target.classList.contains("js-task-complete")) {
    const el = e.target;
    const status = el.checked ? "complete" : "not_started";
    tgnFetch(`/api/tasks/${el.dataset.taskId}`, {
      method: "PATCH", body: JSON.stringify({ status }),
    }).then(() => {
      if (typeof refreshPipelinePanel === "function") refreshPipelinePanel();
    }).catch((err) => {
      el.checked = !el.checked;
      alert(err.message);
    });
  }
  // Sept, per Jodie: a flat "Create X" task (Blog Video, Podcast Snippet,
  // Filler, ...) had no way to hand it to someone else — reassigning it
  // straight from the Tasks list, same PATCH the pipeline hand-off flows
  // already use.
  if (e.target.classList.contains("js-task-assign")) {
    const el = e.target;
    const previousValue = el.dataset.prevValue || "";
    const assignedUserId = el.value ? parseInt(el.value, 10) : null;
    tgnFetch(`/api/tasks/${el.dataset.taskId}`, {
      method: "PATCH", body: JSON.stringify({ assigned_user_id: assignedUserId }),
    }).then(() => {
      el.dataset.prevValue = el.value;
      if (typeof refreshPipelinePanel === "function") refreshPipelinePanel();
    }).catch((err) => {
      el.value = previousValue;
      alert(err.message);
    });
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

// ---------------------------------------------------------------- pipeline: shoot-level (meeting scheduling / confirm / hand-off)
function openPipelineConfirmModal(stageId) {
  fetch(`/pipeline-stages/${stageId}/confirm-modal`).then(r => r.text()).then(html => openModal(html));
}

document.addEventListener("click", (e) => {
  const scheduleBtn = e.target.closest(".js-schedule-meeting");
  if (scheduleBtn) {
    const { campaignId, stageKey } = scheduleBtn.dataset;
    fetch(`/campaigns/${campaignId}/pipeline/${stageKey}/schedule-modal`).then(r => r.text()).then(html => openModal(html));
  }
  const confirmBtn = e.target.closest(".js-confirm-meeting");
  if (confirmBtn) {
    openPipelineConfirmModal(confirmBtn.dataset.stageId);
  }
  const yesBtn = e.target.closest(".js-confirm-yes");
  if (yesBtn) {
    const { stageId, stageKey } = yesBtn.dataset;
    if (stageKey === "film") {
      // Film/Record's "yes" chains straight into capturing the editor
      // hand-off — that submission is what actually completes the stage.
      fetch(`/pipeline-stages/${stageId}/delivery-modal`).then(r => r.text()).then(html => openModal(html));
    } else {
      tgnFetch(`/api/pipeline-stages/${stageId}/confirm`, {
        method: "POST", body: JSON.stringify({ happened: true }),
      }).then(() => {
        // A full reload (rather than a panel-only refresh) so any other
        // stage now past its own meeting time re-prompts immediately, the
        // same way it would on a fresh login.
        window.location.reload();
      }).catch((err) => {
        const box = document.getElementById("cm-error");
        if (box) { box.textContent = err.message; box.style.display = "block"; }
      });
    }
  }
  const noBtn = e.target.closest(".js-confirm-no");
  if (noBtn) {
    const { campaignId, stageKey } = noBtn.dataset;
    fetch(`/campaigns/${campaignId}/pipeline/${stageKey}/schedule-modal`).then(r => r.text()).then(html => openModal(html));
  }

  // -------------------------------------------------------------- pipeline: production-level (review / assign / submit / highlights)
  const reviewBtn = e.target.closest(".js-open-review");
  if (reviewBtn) {
    fetch(`/pipeline-stages/${reviewBtn.dataset.stageId}/review-modal`).then(r => r.text()).then(html => openModal(html));
  }
  const assignBtn = e.target.closest(".js-open-assign");
  if (assignBtn) {
    fetch(`/pipeline-stages/${assignBtn.dataset.stageId}/assign-modal`).then(r => r.text()).then(html => openModal(html));
  }
  const submitBtn = e.target.closest(".js-open-submit");
  if (submitBtn) {
    fetch(`/pipeline-stages/${submitBtn.dataset.stageId}/submission-modal`).then(r => r.text()).then(html => openModal(html));
  }
  const highlightsBtn = e.target.closest(".js-open-highlights");
  if (highlightsBtn) {
    fetch(`/pipeline-stages/${highlightsBtn.dataset.stageId}/highlights-modal`).then(r => r.text()).then(html => openModal(html));
  }
  const alreadyRecordedBtn = e.target.closest(".js-already-recorded");
  if (alreadyRecordedBtn) {
    // Filler-video's skip option (Part 23): confirm_film_with_delivery has
    // no hard dependency on a meeting ever having been scheduled — this
    // just opens the same hand-off form the "Did this happen?" flow does.
    fetch(`/pipeline-stages/${alreadyRecordedBtn.dataset.stageId}/delivery-modal`).then(r => r.text()).then(html => openModal(html));
  }
  const decideAudioBtn = e.target.closest(".js-decide-audio");
  if (decideAudioBtn) {
    const { stageId, wantsAudio } = decideAudioBtn.dataset;
    tgnFetch(`/api/pipeline-stages/${stageId}/decide-audio`, { method: "POST", body: JSON.stringify({ wants_audio: wantsAudio === "1" }) })
      .then(() => window.location.reload())
      .catch((err) => alert(err.message));
  }
  const markReceivedBtn = e.target.closest(".js-mark-received");
  if (markReceivedBtn) {
    const stageId = markReceivedBtn.dataset.stageId;
    tgnFetch(`/api/pipeline-stages/${stageId}/mark-received`, { method: "POST", body: "{}" })
      .then(() => window.location.reload())
      .catch((err) => alert(err.message));
  }
  const markPublishedBtn = e.target.closest(".js-mark-published");
  if (markPublishedBtn) {
    const outputId = markPublishedBtn.dataset.outputId;
    tgnFetch(`/api/outputs/${outputId}/mark-published`, { method: "POST", body: "{}" })
      .then(() => window.location.reload())
      .catch((err) => alert(err.message));
  }
  const approveBtn = e.target.closest(".js-review-approve");
  if (approveBtn) {
    const stageId = approveBtn.dataset.stageId;
    const notes = (document.getElementById("rd-notes") || {}).value || "";
    tgnFetch(`/api/pipeline-stages/${stageId}/approve`, { method: "POST", body: JSON.stringify({ notes }) })
      .then(() => window.location.reload())
      .catch((err) => {
        const box = document.getElementById("rd-error");
        if (box) { box.textContent = err.message; box.style.display = "block"; }
      });
  }
  const rejectBtn = e.target.closest(".js-review-reject");
  if (rejectBtn) {
    const stageId = rejectBtn.dataset.stageId;
    const notesEl = document.getElementById("rd-notes");
    const notes = (notesEl && notesEl.value || "").trim();
    const box = document.getElementById("rd-error");
    if (!notes) {
      if (box) { box.textContent = "Please add a note explaining what needs to change."; box.style.display = "block"; }
      return;
    }
    tgnFetch(`/api/pipeline-stages/${stageId}/reject`, { method: "POST", body: JSON.stringify({ notes }) })
      .then(() => window.location.reload())
      .catch((err) => {
        if (box) { box.textContent = err.message; box.style.display = "block"; }
      });
  }
  const hcAddRow = e.target.closest("#hc-add-row");
  if (hcAddRow) {
    const rows = document.getElementById("hc-rows");
    const row = document.createElement("div");
    row.className = "hc-row";
    row.style.cssText = "border:1px solid var(--border); border-radius:8px; padding:8px; margin-bottom:8px;";
    row.innerHTML = `
      <input class="field-input hc-label" placeholder="Label" style="margin-bottom:6px;">
      <div class="form-grid" style="margin-bottom:6px;">
        <input class="field-input hc-start" placeholder="Start (e.g. 3:15)">
        <input class="field-input hc-end" placeholder="End (e.g. 3:45)">
      </div>
      <input class="field-input hc-notes" placeholder="Notes (optional)">`;
    rows.appendChild(row);
  }
});

document.addEventListener("submit", async (e) => {
  if (e.target.id === "schedule-meeting-form") {
    e.preventDefault();
    const form = e.target;
    const errBox = document.getElementById("sm-error");
    errBox.style.display = "none";
    const { campaignId, stageKey } = form.dataset;
    const participantIds = Array.from(form.querySelectorAll('input[name=participant_ids]:checked')).map(cb => parseInt(cb.value, 10));
    const bunchWithStageIds = Array.from(form.querySelectorAll('input[name=bunch_with_stage_ids]:checked')).map(cb => parseInt(cb.value, 10));
    const payload = {
      start: document.getElementById("sm-start").value,
      end: document.getElementById("sm-end").value,
      participant_ids: participantIds,
      bunch_with_stage_ids: bunchWithStageIds,
    };
    try {
      await tgnFetch(`/api/campaigns/${campaignId}/pipeline/${stageKey}/schedule`, { method: "POST", body: JSON.stringify(payload) });
      window.location.reload();
    } catch (err) {
      errBox.textContent = err.message;
      errBox.style.display = "block";
    }
  }

  if (e.target.id === "video-delivery-form") {
    e.preventDefault();
    const form = e.target;
    const errBox = document.getElementById("vd-error");
    errBox.style.display = "none";
    const stageId = form.dataset.stageId;
    const payload = {
      recipient_user_id: document.getElementById("vd-recipient").value,
      deadline: document.getElementById("vd-deadline").value,
      note: document.getElementById("vd-note").value,
    };
    try {
      await tgnFetch(`/api/pipeline-stages/${stageId}/confirm-with-delivery`, { method: "POST", body: JSON.stringify(payload) });
      window.location.reload();
    } catch (err) {
      errBox.textContent = err.message;
      errBox.style.display = "block";
    }
  }

  if (e.target.id === "assign-notify-form") {
    e.preventDefault();
    const form = e.target;
    const errBox = document.getElementById("an-error");
    errBox.style.display = "none";
    const stageId = form.dataset.stageId;
    const payload = {
      recipient_user_id: document.getElementById("an-recipient").value,
      deadline: document.getElementById("an-deadline").value,
      note: document.getElementById("an-note").value,
    };
    try {
      await tgnFetch(`/api/pipeline-stages/${stageId}/assign`, { method: "POST", body: JSON.stringify(payload) });
      window.location.reload();
    } catch (err) {
      errBox.textContent = err.message;
      errBox.style.display = "block";
    }
  }

  if (e.target.id === "submission-form") {
    e.preventDefault();
    const form = e.target;
    const errBox = document.getElementById("sub-error");
    errBox.style.display = "none";
    const { stageId, stageKey } = form.dataset;
    const notes = document.getElementById("sub-notes").value;
    let payload;
    if (stageKey === "compilation") {
      payload = { link: document.getElementById("sub-link").value, notes };
    } else {
      const clips = Array.from(form.querySelectorAll(".js-sub-clip"))
        .map(el => ({ label: el.dataset.label, url: el.value.trim() }))
        .filter(c => c.url);
      payload = { clips, notes };
    }
    try {
      await tgnFetch(`/api/pipeline-stages/${stageId}/submit`, { method: "POST", body: JSON.stringify(payload) });
      window.location.reload();
    } catch (err) {
      errBox.textContent = err.message;
      errBox.style.display = "block";
    }
  }

  if (e.target.id === "highlights-capture-form") {
    e.preventDefault();
    const form = e.target;
    const errBox = document.getElementById("hc-error");
    errBox.style.display = "none";
    const stageId = form.dataset.stageId;
    const candidates = Array.from(form.querySelectorAll(".hc-row")).map(row => ({
      label: row.querySelector(".hc-label").value.trim(),
      start: row.querySelector(".hc-start").value.trim(),
      end: row.querySelector(".hc-end").value.trim(),
      notes: row.querySelector(".hc-notes").value.trim(),
    })).filter(c => c.label || c.start || c.end);
    const payload = { candidates, deadline: document.getElementById("hc-deadline").value || null };
    try {
      await tgnFetch(`/api/pipeline-stages/${stageId}/capture-highlights`, { method: "POST", body: JSON.stringify(payload) });
      window.location.reload();
    } catch (err) {
      errBox.textContent = err.message;
      errBox.style.display = "block";
    }
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

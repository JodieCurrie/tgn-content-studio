// ---------------------------------------------------------------------------
// Quick-add form: type + notes right up front (Sept, per Jodie — no more
// add-then-click-in-to-tag two-step). Reuses the same category -> specific-
// type toggle as the edit modal below, wired straight onto the page's own
// <select> elements instead of ones built into a modal's innerHTML.
// ---------------------------------------------------------------------------
function populateQuickAddTypeOptions() {
  const targetedSelect = document.getElementById("idea-type-targeted");
  const monthlySelect = document.getElementById("idea-type-monthly");
  const fillerSelect = document.getElementById("idea-type-filler");
  if (!targetedSelect) return; // quick-add form not on this page
  targetedSelect.innerHTML = typeOptionsHtml(window.TGN_TARGETED_TYPES || [], null);
  monthlySelect.innerHTML = `<option value="">Which type of Monthly post?</option>` + typeOptionsHtml(window.TGN_MONTHLY_TYPES || [], null);
  fillerSelect.innerHTML = `<option value="">Which type of Filler post?</option>` + typeOptionsHtml(window.TGN_FILLER_TYPES || [], null);
  [targetedSelect, monthlySelect, fillerSelect].forEach(el => el.addEventListener("change", updateQuickAddCanvaSectionVisibility));
}

function wireQuickAddCategoryToggle() {
  const categorySelect = document.getElementById("idea-category");
  if (!categorySelect) return;
  const groups = {
    targeted: document.getElementById("idea-type-targeted"),
    monthly: document.getElementById("idea-type-monthly"),
    filler: document.getElementById("idea-type-filler"),
  };
  categorySelect.addEventListener("change", () => {
    Object.entries(groups).forEach(([key, el]) => {
      el.style.display = categorySelect.value === key ? "" : "none";
    });
    updateQuickAddCanvaSectionVisibility();
  });
}

function quickAddSelectedTypeId() {
  const categorySelect = document.getElementById("idea-category");
  if (!categorySelect || !categorySelect.value) return null;
  const el = document.getElementById(`idea-type-${categorySelect.value}`);
  const val = el ? el.value : "";
  return val ? parseInt(val, 10) : null;
}

// ---------------------------------------------------------------------------
// Canva design generation (Sept, per Jodie) — a type/body/reference-images
// section that only shows up for the 5 post types a design can actually be
// generated from (window.TGN_CANVA_DESIGN_TYPE_KEYS, set by ideas.html from
// app/canva_ideas.py's CANVA_DESIGN_TYPE_KEYS). Reference images are style
// inspiration only — never placed into the generated design itself.
// ---------------------------------------------------------------------------
function isCanvaEligibleTypeId(typeId) {
  if (!typeId) return false;
  const t = (window.TGN_CONTENT_TYPES || []).find(x => String(x.id) === String(typeId));
  return !!t && (window.TGN_CANVA_DESIGN_TYPE_KEYS || []).includes(t.key);
}

function updateQuickAddCanvaSectionVisibility() {
  const section = document.getElementById("idea-canva-section");
  if (!section) return;
  section.style.display = isCanvaEligibleTypeId(quickAddSelectedTypeId()) ? "" : "none";
}

let stagedReferenceFiles = [];

function wireQuickAddReferenceImageInput() {
  const input = document.getElementById("idea-reference-images");
  if (!input) return;
  input.addEventListener("change", () => {
    stagedReferenceFiles = Array.from(input.files || []);
    const box = document.getElementById("idea-reference-preview");
    if (!box) return;
    box.innerHTML = "";
    stagedReferenceFiles.forEach(file => {
      const img = document.createElement("img");
      img.src = URL.createObjectURL(file);
      img.style.cssText = "width:64px; height:64px; object-fit:cover; border-radius:6px;";
      box.appendChild(img);
    });
  });
}

async function uploadReferenceImages(ideaId, files) {
  for (const file of files) {
    const fd = new FormData();
    fd.append("file", file);
    await fetch(`/api/ideas/${ideaId}/reference-images`, { method: "POST", body: fd });
  }
}

document.addEventListener("DOMContentLoaded", () => {
  populateQuickAddTypeOptions();
  wireQuickAddCategoryToggle();
  wireQuickAddReferenceImageInput();
  updateQuickAddCanvaSectionVisibility();
});

document.addEventListener("submit", async (e) => {
  if (e.target.id !== "idea-form") return;
  e.preventDefault();
  const input = document.getElementById("idea-title");
  const title = input.value.trim();
  if (!title) return;
  const errBox = document.getElementById("idea-form-error");
  try {
    const typeId = quickAddSelectedTypeId();
    const bodyContentEl = document.getElementById("idea-body-content");
    const result = await tgnFetch("/api/ideas", {
      method: "POST",
      body: JSON.stringify({
        title,
        content_type_id: typeId,
        notes: document.getElementById("idea-notes").value,
        links: document.getElementById("idea-links").value,
        body_content: bodyContentEl ? bodyContentEl.value : "",
      }),
    });
    if (isCanvaEligibleTypeId(typeId) && stagedReferenceFiles.length) {
      await uploadReferenceImages(result.id, stagedReferenceFiles);
    }
    window.location.reload();
  } catch (err) {
    if (errBox) {
      errBox.textContent = err.message;
      errBox.style.display = "block";
    } else {
      alert(err.message);
    }
  }
});

document.addEventListener("click", (e) => {
  if (e.target.classList.contains("js-delete-idea")) {
    e.stopPropagation();
    const scheduled = e.target.dataset.ideaScheduled === "1";
    const confirmMsg = scheduled
      ? "Delete this idea? Its calendar slot will go to the next matching idea in your list, or sit as a blank placeholder if there isn't one waiting."
      : "Delete this idea?";
    if (!confirm(confirmMsg)) return;
    tgnFetch(`/api/ideas/${e.target.dataset.ideaId}`, { method: "DELETE" }).then(() => {
      window.location.reload();
    }).catch(err => alert(err.message));
    return;
  }
  if (e.target.classList.contains("js-schedule-idea")) {
    e.stopPropagation();
    openScheduleIdeaModal(e.target.dataset.ideaId, e.target.dataset.ideaTitle, e.target.dataset.ideaTypeId);
    return;
  }
  if (e.target.classList.contains("js-delete-reference-image")) {
    e.stopPropagation();
    tgnFetch(`/api/ideas/reference-images/${e.target.dataset.imageId}`, { method: "DELETE" })
      .then(() => window.location.reload())
      .catch(err => alert(err.message));
    return;
  }
  if (e.target.classList.contains("js-retry-canva-design")) {
    e.stopPropagation();
    tgnFetch(`/api/ideas/${e.target.dataset.ideaId}`, { method: "PATCH", body: JSON.stringify({ canva_status: "pending" }) })
      .then(() => window.location.reload())
      .catch(err => alert(err.message));
    return;
  }
  const ideaRow = e.target.closest(".js-open-idea");
  if (ideaRow) {
    openEditIdeaModal(ideaRow.dataset.ideaId);
  }
});

// ---------------------------------------------------------------------------
// Click-to-edit: notes, links, and the category -> specific-type tagging
// that drives automatic scheduling into a matching calendar slot.
// ---------------------------------------------------------------------------
function typeOptionsHtml(types, selectedId) {
  return types.map(t => `<option value="${t.id}" ${String(t.id) === String(selectedId) ? "selected" : ""}>${t.label}</option>`).join("");
}

function categoryOfType(typeId) {
  if (!typeId) return "";
  const all = (window.TGN_CONTENT_TYPES || []);
  const t = all.find(x => String(x.id) === String(typeId));
  return t ? t.category_key : "";
}

function referenceImagesGalleryHtml(images) {
  images = images || [];
  if (!images.length) {
    return `<div class="page-subtitle" style="margin-bottom:8px;">No reference images yet.</div>`;
  }
  return `<div style="display:flex; flex-wrap:wrap; gap:8px; margin-bottom:8px;">` +
    images.map(img => `
      <div style="position:relative;">
        <img src="${img.url}" style="width:64px; height:64px; object-fit:cover; border-radius:6px;">
        <button type="button" class="js-delete-reference-image" data-image-id="${img.id}"
          style="position:absolute; top:-6px; right:-6px; width:18px; height:18px; border-radius:50%; border:none; background:#333; color:#fff; font-size:11px; line-height:1; cursor:pointer;"
          title="Remove this reference image">✕</button>
      </div>
    `).join("") + `</div>`;
}

function canvaStatusHtml(idea) {
  if (idea.canva_status === "pending") {
    return `<div class="page-subtitle" style="margin-bottom:10px;">🎨 Design generating — check back shortly.</div>`;
  }
  if (idea.canva_status === "ready" && idea.canva_design_link) {
    return `<div class="page-subtitle" style="margin-bottom:10px;">🎨 <a href="${idea.canva_design_link}" target="_blank" rel="noopener">Open Canva design</a></div>`;
  }
  if (idea.canva_status === "failed") {
    return `<div class="page-subtitle" style="margin-bottom:10px;">🎨 Design generation had a problem. <button type="button" class="btn btn-sm btn-outline js-retry-canva-design" data-idea-id="${idea.id}">Retry</button></div>`;
  }
  return "";
}

function editIdeaFormHtml(idea) {
  const scheduled = !!idea.scheduled_campaign_id;
  const category = categoryOfType(idea.content_type_id);
  const monthly = window.TGN_MONTHLY_TYPES || [];
  const filler = window.TGN_FILLER_TYPES || [];
  const targeted = window.TGN_TARGETED_TYPES || [];

  // Sept, per Jodie: a scheduled idea's type used to be locked for good —
  // now it's editable here too, it just means something extra happens on
  // save (see openEditIdeaModal below): changing it releases the idea's
  // CURRENT calendar slot back to blank and tries to place it into a new
  // one under the new type, same as a freshly-tagged idea would be.
  const scheduledNote = scheduled ? `
        <div class="page-subtitle" style="margin-bottom:8px;">
          Currently on the calendar as <strong>${(idea.campaign_title || "").replace(/</g, "&lt;")}</strong>. Renaming or adding notes keeps that in sync. Changing the type below moves it off that slot and re-places it under the new type (into another open slot right away, or back to Unscheduled to wait for one).
        </div>
      ` : "";

  const typeSectionHtml = `
        ${scheduledNote}
        <label class="field-label">Content type</label>
        <select class="field-select" id="ei-category" style="margin-bottom:8px;">
          <option value="" ${!category ? "selected" : ""}>— Untagged —</option>
          <option value="targeted" ${category === "targeted" ? "selected" : ""}>Targeted Campaign</option>
          <option value="monthly" ${category === "monthly" ? "selected" : ""}>Monthly</option>
          <option value="filler" ${category === "filler" ? "selected" : ""}>Filler</option>
        </select>
        <select class="field-select" id="ei-type-targeted" style="margin-bottom:12px; ${category === "targeted" ? "" : "display:none;"}">
          ${typeOptionsHtml(targeted, idea.content_type_id)}
        </select>
        <select class="field-select" id="ei-type-monthly" style="margin-bottom:12px; ${category === "monthly" ? "" : "display:none;"}">
          <option value="">Which type of Monthly post?</option>
          ${typeOptionsHtml(monthly, idea.content_type_id)}
        </select>
        <select class="field-select" id="ei-type-filler" style="margin-bottom:12px; ${category === "filler" ? "" : "display:none;"}">
          <option value="">Which type of Filler post?</option>
          ${typeOptionsHtml(filler, idea.content_type_id)}
        </select>
      `;

  const canvaEligible = isCanvaEligibleTypeId(idea.content_type_id);
  const canvaSectionHtml = `
        <div id="ei-canva-section" style="${canvaEligible ? "" : "display:none;"}">
          <label class="field-label">Body content <span class="page-subtitle" style="display:inline;">(what the post should say — needed to generate a design)</span></label>
          <textarea class="field-input" id="ei-body-content" rows="4" style="margin-bottom:10px;">${idea.body_content || ""}</textarea>

          <label class="field-label">Reference images <span class="page-subtitle" style="display:inline;">(style/mood inspiration only — never placed into the design itself)</span></label>
          ${referenceImagesGalleryHtml(idea.reference_images)}
          <input class="field-input" type="file" id="ei-reference-images" accept="image/*" multiple style="margin-bottom:10px;">
          ${canvaStatusHtml(idea)}
        </div>
      `;

  return `
    <div class="modal-header"><strong>Edit idea</strong></div>
    <div class="modal-body">
      <form id="edit-idea-form">
        <label class="field-label">Title</label>
        <input class="field-input" id="ei-title" value="${(idea.title || "").replace(/"/g, "&quot;")}" style="margin-bottom:12px;" required>

        ${typeSectionHtml}
        ${canvaSectionHtml}

        <label class="field-label">Notes</label>
        <textarea class="field-input" id="ei-notes" rows="3" style="margin-bottom:12px;">${idea.notes || ""}</textarea>

        <label class="field-label">Links (one per line)</label>
        <textarea class="field-input" id="ei-links" rows="2" placeholder="https://drive.google.com/..." style="margin-bottom:16px;">${idea.links || ""}</textarea>

        <div id="ei-error" class="flash error" style="display:none;"></div>
        <button class="btn btn-primary" type="submit" style="width:100%; justify-content:center;">Save</button>
      </form>
    </div>
  `;
}

function wireCategoryToggle() {
  const categorySelect = document.getElementById("ei-category");
  if (!categorySelect) return; // scheduled idea — type section is read-only, nothing to wire
  const groups = {
    targeted: document.getElementById("ei-type-targeted"),
    monthly: document.getElementById("ei-type-monthly"),
    filler: document.getElementById("ei-type-filler"),
  };
  categorySelect.addEventListener("change", () => {
    Object.entries(groups).forEach(([key, el]) => {
      el.style.display = categorySelect.value === key ? "" : "none";
    });
    updateEiCanvaSectionVisibility();
  });
}

function wireEiCanvaSectionToggle() {
  ["ei-type-targeted", "ei-type-monthly", "ei-type-filler"].forEach(id => {
    const el = document.getElementById(id);
    if (el) el.addEventListener("change", updateEiCanvaSectionVisibility);
  });
}

function updateEiCanvaSectionVisibility() {
  const section = document.getElementById("ei-canva-section");
  if (!section) return;
  section.style.display = isCanvaEligibleTypeId(selectedTypeId()) ? "" : "none";
}

function wireEiReferenceImageUpload(ideaId) {
  const input = document.getElementById("ei-reference-images");
  if (!input) return;
  input.addEventListener("change", async () => {
    const files = Array.from(input.files || []);
    if (!files.length) return;
    await uploadReferenceImages(ideaId, files);
    window.location.reload();
  });
}

function selectedTypeId() {
  const categorySelect = document.getElementById("ei-category");
  if (!categorySelect || !categorySelect.value) return null;
  const el = document.getElementById(`ei-type-${categorySelect.value}`);
  const val = el ? el.value : "";
  return val ? parseInt(val, 10) : null;
}

async function openEditIdeaModal(ideaId) {
  const idea = (window.TGN_IDEAS_BY_ID || {})[ideaId];
  if (!idea) return;
  const scheduled = !!idea.scheduled_campaign_id;
  openModal(editIdeaFormHtml(idea));
  wireCategoryToggle();
  wireEiCanvaSectionToggle();
  wireEiReferenceImageUpload(ideaId);
  document.getElementById("edit-idea-form").addEventListener("submit", async (e) => {
    e.preventDefault();
    const errBox = document.getElementById("ei-error");
    try {
      const newTypeId = selectedTypeId();
      if (scheduled && String(newTypeId || "") !== String(idea.content_type_id || "")) {
        const proceed = confirm("Changing the type moves this off its current calendar slot and re-places it under the new type — into another open slot right away if one's free, or back to Unscheduled to wait for one. Continue?");
        if (!proceed) return;
      }
      const bodyContentEl = document.getElementById("ei-body-content");
      const payload = {
        title: document.getElementById("ei-title").value.trim(),
        content_type_id: newTypeId,
        notes: document.getElementById("ei-notes").value,
        links: document.getElementById("ei-links").value,
        body_content: bodyContentEl ? bodyContentEl.value : (idea.body_content || ""),
      };
      await tgnFetch(`/api/ideas/${ideaId}`, {
        method: "PATCH",
        body: JSON.stringify(payload),
      });
      window.location.reload();
    } catch (err) {
      errBox.textContent = err.message;
      errBox.style.display = "block";
    }
  });
}

function openScheduleIdeaModal(ideaId, ideaTitle, ideaTypeId) {
  const types = window.TGN_CONTENT_TYPES || [];
  const options = types.map(t => `<option value="${t.id}" ${String(t.id) === String(ideaTypeId) ? "selected" : ""}>${t.label}</option>`).join("");
  const html = `
    <div class="modal-header"><strong>Schedule now: ${ideaTitle}</strong></div>
    <div class="modal-body">
      <div class="page-subtitle" style="margin-bottom:10px;">Tagged ideas fill their matching slot automatically as it comes up — use this only to place it on the calendar right now instead of waiting.</div>
      <form id="schedule-idea-form">
        <label class="field-label">Content type</label>
        <select class="field-select" id="si-type" style="margin-bottom:12px;">${options}</select>
        <label class="field-label">Publish date</label>
        <input class="field-input" type="date" id="si-date" required style="margin-bottom:16px;">
        <div id="si-error" class="flash error" style="display:none;"></div>
        <button class="btn btn-primary" type="submit" style="width:100%; justify-content:center;">Add to calendar</button>
      </form>
    </div>
  `;
  openModal(html);
  document.getElementById("schedule-idea-form").addEventListener("submit", async (e) => {
    e.preventDefault();
    const errBox = document.getElementById("si-error");
    try {
      await tgnFetch(`/api/ideas/${ideaId}/schedule`, {
        method: "POST",
        body: JSON.stringify({
          content_type_id: parseInt(document.getElementById("si-type").value, 10),
          publish_date: document.getElementById("si-date").value,
        }),
      });
      window.location.reload();
    } catch (err) {
      errBox.textContent = err.message;
      errBox.style.display = "block";
    }
  });
}

document.addEventListener("submit", async (e) => {
  if (e.target.id !== "idea-form") return;
  e.preventDefault();
  const input = document.getElementById("idea-title");
  const title = input.value.trim();
  if (!title) return;
  try {
    await tgnFetch("/api/ideas", { method: "POST", body: JSON.stringify({ title }) });
    window.location.reload();
  } catch (err) {
    alert(err.message);
  }
});

document.addEventListener("click", (e) => {
  if (e.target.classList.contains("js-delete-idea")) {
    e.stopPropagation();
    if (!confirm("Delete this idea?")) return;
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

function editIdeaFormHtml(idea) {
  const category = categoryOfType(idea.content_type_id);
  const monthly = window.TGN_MONTHLY_TYPES || [];
  const filler = window.TGN_FILLER_TYPES || [];
  const targeted = window.TGN_TARGETED_TYPES || [];
  return `
    <div class="modal-header"><strong>Edit idea</strong></div>
    <div class="modal-body">
      <form id="edit-idea-form">
        <label class="field-label">Title</label>
        <input class="field-input" id="ei-title" value="${(idea.title || "").replace(/"/g, "&quot;")}" style="margin-bottom:12px;" required>

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
  const groups = {
    targeted: document.getElementById("ei-type-targeted"),
    monthly: document.getElementById("ei-type-monthly"),
    filler: document.getElementById("ei-type-filler"),
  };
  categorySelect.addEventListener("change", () => {
    Object.entries(groups).forEach(([key, el]) => {
      el.style.display = categorySelect.value === key ? "" : "none";
    });
  });
}

function selectedTypeId() {
  const categorySelect = document.getElementById("ei-category");
  if (!categorySelect.value) return null;
  const el = document.getElementById(`ei-type-${categorySelect.value}`);
  const val = el ? el.value : "";
  return val ? parseInt(val, 10) : null;
}

async function openEditIdeaModal(ideaId) {
  const idea = (window.TGN_IDEAS_BY_ID || {})[ideaId];
  if (!idea) return;
  openModal(editIdeaFormHtml(idea));
  wireCategoryToggle();
  document.getElementById("edit-idea-form").addEventListener("submit", async (e) => {
    e.preventDefault();
    const errBox = document.getElementById("ei-error");
    try {
      await tgnFetch(`/api/ideas/${ideaId}`, {
        method: "PATCH",
        body: JSON.stringify({
          title: document.getElementById("ei-title").value.trim(),
          notes: document.getElementById("ei-notes").value,
          links: document.getElementById("ei-links").value,
          content_type_id: selectedTypeId(),
        }),
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

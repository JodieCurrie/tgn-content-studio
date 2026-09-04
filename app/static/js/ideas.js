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
    if (!confirm("Delete this idea?")) return;
    tgnFetch(`/api/ideas/${e.target.dataset.ideaId}`, { method: "DELETE" }).then(() => {
      window.location.reload();
    }).catch(err => alert(err.message));
  }
  if (e.target.classList.contains("js-schedule-idea")) {
    openScheduleIdeaModal(e.target.dataset.ideaId, e.target.dataset.ideaTitle);
  }
});

function openScheduleIdeaModal(ideaId, ideaTitle) {
  const types = window.TGN_CONTENT_TYPES || [];
  const options = types.map(t => `<option value="${t.id}">${t.label}</option>`).join("");
  const html = `
    <div class="modal-header"><strong>Schedule: ${ideaTitle}</strong></div>
    <div class="modal-body">
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

/* Drag-and-drop for the calendar grid + the recurring-schedule choice
   dialog (Part 12/14 of the brief): dropping a rule-linked campaign never
   moves it silently — it always asks what "moving" should mean. */

let draggedCampaignId = null;

document.addEventListener("dragstart", (e) => {
  const card = e.target.closest(".content-card");
  if (!card) return;
  draggedCampaignId = card.dataset.campaignId;
  e.dataTransfer.effectAllowed = "move";
  e.dataTransfer.setData("text/plain", draggedCampaignId);
});

document.addEventListener("dragover", (e) => {
  const dropzone = e.target.closest(".calendar-day, .week-day-col");
  if (!dropzone) return;
  e.preventDefault();
  dropzone.classList.add("drag-over");
});

document.addEventListener("dragleave", (e) => {
  const dropzone = e.target.closest(".calendar-day, .week-day-col");
  if (dropzone) dropzone.classList.remove("drag-over");
});

document.addEventListener("drop", (e) => {
  const dropzone = e.target.closest(".calendar-day, .week-day-col");
  if (!dropzone) return;
  e.preventDefault();
  dropzone.classList.remove("drag-over");
  const campaignId = draggedCampaignId || e.dataTransfer.getData("text/plain");
  const newDate = dropzone.dataset.date;
  if (campaignId && newDate) handleDragMove(campaignId, newDate);
  draggedCampaignId = null;
});

// also allow changing the date directly from the detail panel
document.addEventListener("change", (e) => {
  if (e.target.id === "panel-drag-date") {
    handleDragMove(e.target.dataset.campaignId, e.target.value);
  }
});

async function handleDragMove(campaignId, newDate) {
  let opts;
  try {
    opts = await tgnFetch(`/api/campaigns/${campaignId}/drag-options`);
  } catch (e) {
    alert(e.message);
    return;
  }

  if (!opts.needs_choice) {
    await commitDrag(campaignId, newDate, null);
    return;
  }

  showDragChoiceModal(campaignId, newDate, opts);
}

function showDragChoiceModal(campaignId, newDate, opts) {
  const depCount = (opts.dependents || []).length;
  const depNote = depCount
    ? `<div class="page-subtitle" style="margin-bottom:10px;">This has ${depCount} dependent item${depCount > 1 ? "s" : ""} (e.g. highlight snippets) that could move with it.</div>`
    : "";
  const buttons = opts.choices.map(c => `
    <button class="btn btn-outline js-drag-choice" data-choice="${c.key}" style="width:100%; text-align:left; margin-bottom:8px; display:block;">
      <strong style="display:block;">${c.label}</strong>
      <span class="page-subtitle" style="display:block; margin-top:2px;">${c.description}</span>
    </button>
  `).join("");

  const html = `
    <div class="modal-header"><strong>This is part of a recurring schedule</strong></div>
    <div class="modal-body">
      ${depNote}
      ${buttons}
      <button class="btn btn-ghost js-drag-cancel" style="width:100%; text-align:center; margin-top:4px;">Cancel</button>
    </div>
  `;
  openModal(html);

  document.querySelectorAll(".js-drag-choice").forEach(btn => {
    btn.addEventListener("click", async () => {
      closeModal();
      await commitDrag(campaignId, newDate, btn.dataset.choice);
    });
  });
  const cancelBtn = document.querySelector(".js-drag-cancel");
  cancelBtn && cancelBtn.addEventListener("click", () => {
    closeModal();
    window.location.reload(); // reset any optimistic DOM state
  });
}

async function commitDrag(campaignId, newDate, mode) {
  try {
    await tgnFetch(`/api/campaigns/${campaignId}/drag`, {
      method: "POST",
      body: JSON.stringify({ new_date: newDate, mode }),
    });
    window.location.reload();
  } catch (e) {
    alert(e.message);
    window.location.reload();
  }
}

/* Click an empty date to add content there (Part 2 of the brief) — the
   click still opens the campaign panel when it lands on an existing card,
   this only fires on the blank part of a day cell. */
document.addEventListener("click", (e) => {
  if (e.target.closest(".content-card")) return;
  const day = e.target.closest(".calendar-day");
  if (!day || !day.dataset.date) return;
  if (typeof openCreateModal === "function") openCreateModal(day.dataset.date);
});

/* Jump straight to a month/year instead of clicking "next" repeatedly. */
const calJumpInput = document.getElementById("cal-jump-input");
calJumpInput && calJumpInput.addEventListener("change", () => {
  const [y, m] = calJumpInput.value.split("-");
  if (!y || !m) return;
  window.location.href = `${window.location.pathname}?year=${y}&month=${parseInt(m, 10)}`;
});

/* Continuous/infinite-scroll calendar: months bleed into each other in one
   grid (Part 2) rather than a "click Next Month" page-per-month, and there
   is no fixed cap on how far ahead this can go — it just keeps fetching
   /calendar/month-fragment as the sentinel comes into view. */
const calendarGrid = document.getElementById("calendar-grid");
const calendarSentinel = document.getElementById("calendar-sentinel");
const calendarLoading = document.getElementById("calendar-loading");
let loadingMoreMonths = false;

async function loadNextCalendarMonth() {
  if (!calendarSentinel || loadingMoreMonths) return;
  loadingMoreMonths = true;
  if (calendarLoading) calendarLoading.style.display = "block";
  const year = calendarSentinel.dataset.nextYear;
  const month = calendarSentinel.dataset.nextMonth;
  try {
    const res = await fetch(`/calendar/month-fragment?year=${year}&month=${month}`);
    if (res.ok) {
      const html = await res.text();
      const wrapper = document.createElement("div");
      wrapper.innerHTML = html;
      Array.from(wrapper.children).forEach(node => calendarGrid.appendChild(node));
      const nextMonthNum = parseInt(month, 10) === 12 ? 1 : parseInt(month, 10) + 1;
      const nextYearNum = parseInt(month, 10) === 12 ? parseInt(year, 10) + 1 : parseInt(year, 10);
      calendarSentinel.dataset.nextYear = nextYearNum;
      calendarSentinel.dataset.nextMonth = nextMonthNum;
    }
  } finally {
    loadingMoreMonths = false;
    if (calendarLoading) calendarLoading.style.display = "none";
  }
}

if (calendarSentinel && "IntersectionObserver" in window) {
  const observer = new IntersectionObserver((entries) => {
    entries.forEach(entry => { if (entry.isIntersecting) loadNextCalendarMonth(); });
  }, { rootMargin: "600px 0px 600px 0px" });
  observer.observe(calendarSentinel);
}

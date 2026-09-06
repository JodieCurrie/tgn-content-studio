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

/* Continuous/infinite-scroll calendar: weeks bleed into each other in one
   flat, never-repeating flow (Part 2/6) rather than a "click Next Month"
   page-per-month or a per-month self-contained block — the server hands
   back a plain "next Sunday to fetch from" date, and each fetch picks up
   exactly where the last batch of weeks left off, so no week is ever
   fetched or rendered twice. No fixed cap on how far ahead this can go. */
const calendarGrid = document.getElementById("calendar-grid");
const calendarSentinel = document.getElementById("calendar-sentinel");
const calendarLoading = document.getElementById("calendar-loading");
let loadingMoreWeeks = false;

function addDaysToIsoDate(iso, days) {
  const d = new Date(iso + "T00:00:00Z");
  d.setUTCDate(d.getUTCDate() + days);
  return d.toISOString().slice(0, 10);
}

async function loadNextCalendarWeeks() {
  if (!calendarSentinel || loadingMoreWeeks) return;
  loadingMoreWeeks = true;
  if (calendarLoading) calendarLoading.style.display = "block";
  const from = calendarSentinel.dataset.nextFrom;
  // Tell the server which month the rail strip currently ends on, so if
  // this batch starts partway through that same month it renders an
  // unlabeled continuation block instead of showing the month name again.
  const contYear = calendarSentinel.dataset.contYear;
  const contMonth = calendarSentinel.dataset.contMonth;
  try {
    const res = await fetch(`/calendar/month-fragment?from=${from}&cont_year=${contYear}&cont_month=${contMonth}`);
    if (res.ok) {
      const html = await res.text();
      const wrapper = document.createElement("div");
      wrapper.innerHTML = html;
      Array.from(wrapper.children).forEach(node => calendarGrid.appendChild(node));
      // matches WEEKS_PER_FRAGMENT in app/routes/calendar.py
      calendarSentinel.dataset.nextFrom = addDaysToIsoDate(from, 4 * 7);
      const rails = calendarGrid.querySelectorAll(".month-rail");
      const lastRail = rails[rails.length - 1];
      if (lastRail) {
        calendarSentinel.dataset.contYear = lastRail.dataset.year;
        calendarSentinel.dataset.contMonth = lastRail.dataset.month;
      }
    }
  } finally {
    loadingMoreWeeks = false;
    if (calendarLoading) calendarLoading.style.display = "none";
  }
}

if (calendarSentinel && "IntersectionObserver" in window) {
  const observer = new IntersectionObserver((entries) => {
    entries.forEach(entry => { if (entry.isIntersecting) loadNextCalendarWeeks(); });
  }, { rootMargin: "600px 0px 600px 0px" });
  observer.observe(calendarSentinel);
}

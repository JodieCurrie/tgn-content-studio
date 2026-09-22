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

/* Scrolling UP (Sept, per Jodie): the calendar now opens on the current
   week rather than the 1st of the month, so this is what lets her still
   get back to earlier weeks — same continuous-flow idea as scrolling down,
   just fetching backward from whatever's currently the earliest-rendered
   week and prepending it above. */
const calendarSentinelTop = document.getElementById("calendar-sentinel-top");
const calendarLoadingTop = document.getElementById("calendar-loading-top");
let loadingPrevWeeks = false;

async function loadPreviousCalendarWeeks() {
  if (!calendarSentinelTop || loadingPrevWeeks) return;
  loadingPrevWeeks = true;
  if (calendarLoadingTop) calendarLoadingTop.style.display = "block";
  const before = calendarSentinelTop.dataset.earliestFrom;
  try {
    const res = await fetch(`/calendar/month-fragment-before?before=${before}`);
    if (!res.ok) return;
    const html = await res.text();
    const wrapper = document.createElement("div");
    wrapper.innerHTML = html;
    const newNodes = Array.from(wrapper.children);
    if (!newNodes.length) return;

    // A fetched-from-scratch batch always carries its own month label on
    // every segment (the server has no idea what's already on screen). If
    // the batch's LAST segment (the latest one, right up against what's
    // already rendered) is the same month as what's currently the topmost
    // rail, that existing rail's label is no longer the earliest
    // occurrence of that month once we prepend — drop it so the name
    // isn't shown twice back to back.
    const newRails = newNodes.filter(n => n.classList && n.classList.contains("month-rail"));
    const newLastRail = newRails[newRails.length - 1];
    const existingTopRail = calendarGrid.querySelector(".month-rail");
    if (newLastRail && existingTopRail
        && newLastRail.dataset.year === existingTopRail.dataset.year
        && newLastRail.dataset.month === existingTopRail.dataset.month) {
      const staleLabel = existingTopRail.querySelector("span");
      if (staleLabel) staleLabel.remove();
    }

    // Prepend right after the top sentinel, keeping scroll position
    // stable — inserting content above the fold would otherwise shove the
    // weeks Jodie's actually looking at further down the page. Each node
    // is inserted right after the previous one just inserted, so the
    // batch's own top-to-bottom (earliest-to-latest) order is preserved.
    const prevHeight = calendarGrid.scrollHeight;
    let insertAfter = calendarSentinelTop;
    newNodes.forEach(node => {
      insertAfter.after(node);
      insertAfter = node;
    });
    const addedHeight = calendarGrid.scrollHeight - prevHeight;
    window.scrollBy(0, addedHeight);

    // matches WEEKS_PER_FRAGMENT in app/routes/calendar.py
    calendarSentinelTop.dataset.earliestFrom = addDaysToIsoDate(before, -4 * 7);
  } finally {
    loadingPrevWeeks = false;
    if (calendarLoadingTop) calendarLoadingTop.style.display = "none";
  }
}

if (calendarSentinelTop && "IntersectionObserver" in window) {
  // Sept, per Jodie: the first thing she sees must always be the current
  // week, full stop — no earlier weeks pulled in until she's actually
  // scrolled for them. The 600px rootMargin below (same as the forward
  // observer, so scrolling up feels just as seamless once she's doing it)
  // means the sentinel counts as "intersecting" the moment it's merely
  // close to the screen — which, on a short phone screen with the page
  // header/toolbar/legend/filters above the grid, it already can be on
  // first load, before she's touched the screen at all. That was firing a
  // backward fetch immediately on open, pulling in August and pushing the
  // nav/current week below the fold before she'd done anything.
  //
  // Don't even start watching the sentinel until after her first scroll —
  // an observer's first observe() call always fires once for whatever the
  // current intersection state is, so watching from page load is exactly
  // what let this fire with zero interaction. Starting it fresh after a
  // real scroll means that first check reflects a page she's actually
  // scrolled, not the one that just rendered.
  const observerTop = new IntersectionObserver((entries) => {
    entries.forEach(entry => { if (entry.isIntersecting) loadPreviousCalendarWeeks(); });
  }, { rootMargin: "600px 0px 600px 0px" });
  window.addEventListener("scroll", () => observerTop.observe(calendarSentinelTop), { once: true, passive: true });
}

/* Sept: calendar view filters (Month/Week/List) — "Posting Schedule" plus a
   per-person Custom Events / Deadlines checkbox. Purely client-side: every
   filterable element on the page carries data-filter-cat (+ data-filter-owner
   for "custom"/"deadline") from the server, and the checkbox state — which
   one is which category/owner combination is currently checked — is kept in
   localStorage so it carries across month/week/list and across visits.
   Nothing checked in the state object defaults to "shown" (missing key ===
   visible), which is what lets a brand-new person/category show up already
   visible instead of silently hidden until someone opts it in. */
const CAL_FILTER_STORAGE_KEY = "tgn_calendar_filters";

function calFilterKey(cat, owner) {
  return owner ? `${cat}:${owner}` : cat;
}

function loadCalFilterState() {
  try {
    return JSON.parse(localStorage.getItem(CAL_FILTER_STORAGE_KEY) || "{}");
  } catch (e) {
    return {};
  }
}

function saveCalFilterState(state) {
  try {
    localStorage.setItem(CAL_FILTER_STORAGE_KEY, JSON.stringify(state));
  } catch (e) { /* private browsing / storage disabled — filters just won't persist */ }
}

function applyCalendarFilters() {
  const state = loadCalFilterState();
  document.querySelectorAll("[data-filter-cat]").forEach(el => {
    const key = calFilterKey(el.dataset.filterCat, el.dataset.filterOwner);
    const visible = state[key] !== false;
    el.classList.toggle("cal-filtered-out", !visible);
  });
}

/* Each person collapses into a <summary> pill (see render_filter_panel) —
   the small dot next to their name is the only hint, while it's closed,
   that one of their two checkboxes (Custom events / Deadlines) is
   currently unchecked, so narrowing the calendar down doesn't require
   opening every person's dropdown just to remember what's hidden. */
function updateCalFilterBadges() {
  document.querySelectorAll(".cal-filter-badge").forEach(badge => {
    const owner = badge.dataset.owner;
    const ownerChecks = document.querySelectorAll(`.js-cal-filter[data-owner="${owner}"]`);
    const allChecked = Array.from(ownerChecks).every(cb => cb.checked);
    badge.hidden = allChecked;
  });
}

function initCalendarFilterPanel() {
  const checkboxes = document.querySelectorAll(".js-cal-filter");
  if (!checkboxes.length) return;
  const state = loadCalFilterState();
  checkboxes.forEach(cb => {
    const key = calFilterKey(cb.dataset.cat, cb.dataset.owner);
    cb.checked = state[key] !== false;
    cb.addEventListener("change", () => {
      const s = loadCalFilterState();
      s[calFilterKey(cb.dataset.cat, cb.dataset.owner)] = cb.checked;
      saveCalFilterState(s);
      applyCalendarFilters();
      updateCalFilterBadges();
    });
  });
  const selectAllBtn = document.querySelector(".js-cal-filter-all");
  selectAllBtn && selectAllBtn.addEventListener("click", () => {
    const s = loadCalFilterState();
    checkboxes.forEach(cb => { cb.checked = true; s[calFilterKey(cb.dataset.cat, cb.dataset.owner)] = true; });
    saveCalFilterState(s);
    applyCalendarFilters();
    updateCalFilterBadges();
  });
  const deselectAllBtn = document.querySelector(".js-cal-filter-none");
  deselectAllBtn && deselectAllBtn.addEventListener("click", () => {
    const s = loadCalFilterState();
    checkboxes.forEach(cb => { cb.checked = false; s[calFilterKey(cb.dataset.cat, cb.dataset.owner)] = false; });
    saveCalFilterState(s);
    applyCalendarFilters();
    updateCalFilterBadges();
  });
  updateCalFilterBadges();
}

/* Sept, per Jodie: a person's filter dropdown is a native <details> pill —
   left alone, that only closes when you click the summary (their name)
   again, which she doesn't otherwise need to touch once she's toggled a
   checkbox inside it. Close it for her automatically on any click outside
   the currently-open one(s), same as any other dropdown/menu would. */
function initCalendarFilterAutoClose() {
  document.addEventListener("click", (e) => {
    document.querySelectorAll(".cal-filter-person[open]").forEach(details => {
      if (!details.contains(e.target)) details.removeAttribute("open");
    });
  });
}

initCalendarFilterPanel();
initCalendarFilterAutoClose();
applyCalendarFilters();

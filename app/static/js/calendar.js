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
   this only fires on the blank part of a day cell. Oct 2026 fix: a
   .deadline-pill click used to fall through to here too (it wasn't a
   .content-card), popping open "add new content on this date" instead of
   anything about the deadline itself — now excluded the same way, along
   with .meeting-pill (a real link that shouldn't also open this). */
document.addEventListener("click", (e) => {
  if (e.target.closest(".content-card, .deadline-pill, .meeting-pill")) return;
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
      // Oct 2026 fix: these freshly-inserted day cells carry their own
      // data-filter-cat/data-filter-owner attributes straight from the
      // server, with no "hidden" class applied yet — without re-running
      // the filter pass here, a person's unchecked Deadlines/Custom events
      // (e.g. Lizzy's) would reappear the moment the calendar loads a new
      // batch of weeks, even though nothing about her saved filter choice
      // changed. Same fix applies below in loadPreviousCalendarWeeks.
      applyCalendarFilters();
      // Nov 2026: fetch Custom Events for exactly this newly-loaded
      // 4-week batch and redraw — see the "custom event bars" block below.
      fetchCustomEvents(from, addDaysToIsoDate(from, 4 * 7 - 1));
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

/* Earlier weeks (Sept, per Jodie): the calendar opens on the current week
   rather than the 1st of the month, so this is what lets her still get
   back to earlier weeks. Rounds 1 and 2 tried to detect "she's scrolling
   up toward the top" automatically (an IntersectionObserver, then a
   scroll-direction-plus-proximity check) and both misfired on a phone: a
   continuous touch-scroll gesture fires a flood of scroll events, and
   every load inserted content and then scrolled back down to compensate
   (see the scrollBy below) — which, mid-gesture, fought her own scrolling
   and made reaching the actual top of the page feel endless. An explicit
   button sidesteps the whole problem: plain scrolling, in any direction,
   at any speed, never loads anything or moves the page on its own —
   scrolling to the top always just reaches the top. Earlier weeks only
   load on an actual tap. */
const calendarSentinelTop = document.getElementById("calendar-sentinel-top");
const calendarLoadEarlierBtn = document.getElementById("calendar-load-earlier-btn");
let loadingPrevWeeks = false;

async function loadPreviousCalendarWeeks() {
  if (!calendarSentinelTop || loadingPrevWeeks) return;
  loadingPrevWeeks = true;
  if (calendarLoadEarlierBtn) {
    calendarLoadEarlierBtn.disabled = true;
    calendarLoadEarlierBtn.textContent = "Loading…";
  }
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

    // Prepend right after the button/sentinel, which never moves — new
    // weeks land right where she tapped, no forced scroll adjustment
    // needed (that forced re-adjustment on every load was the source of
    // the "fighting my scroll" problem with the old auto-triggered
    // version). Each node is inserted right after the previous one just
    // inserted, so the batch's own top-to-bottom (earliest-to-latest)
    // order is preserved.
    let insertAfter = calendarSentinelTop;
    newNodes.forEach(node => {
      insertAfter.after(node);
      insertAfter = node;
    });

    // matches WEEKS_PER_FRAGMENT in app/routes/calendar.py
    calendarSentinelTop.dataset.earliestFrom = addDaysToIsoDate(before, -4 * 7);
    // Oct 2026 fix: see the matching comment in loadNextCalendarWeeks — these
    // newly-prepended cells need the filter pass re-run too.
    applyCalendarFilters();
    // Nov 2026: same Custom Events fetch/redraw as loadNextCalendarWeeks,
    // for this newly-prepended 4-week batch.
    fetchCustomEvents(addDaysToIsoDate(before, -4 * 7), addDaysToIsoDate(before, -1));
  } finally {
    loadingPrevWeeks = false;
    if (calendarLoadEarlierBtn) {
      calendarLoadEarlierBtn.disabled = false;
      calendarLoadEarlierBtn.textContent = "↑ Load earlier weeks";
    }
  }
}

calendarLoadEarlierBtn && calendarLoadEarlierBtn.addEventListener("click", loadPreviousCalendarWeeks);

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

// Nov 2026, per Jodie: Custom Events flip the old "visible unless you
// opted out" default on its head — a person's off-time block shouldn't
// clutter everyone's calendar until someone deliberately ticks their name.
// An unassigned custom event (no owner, so no name to tick) still falls
// back to visible, since there'd otherwise be no control that could ever
// show it. Deadlines/Posting schedule keep the original opt-out default.
function calFilterDefaultVisible(cat, owner) {
  return cat !== "custom" || !owner;
}

// Nov 2026, per Jodie: team members should land on just their own assigned
// campaigns/snippets, not the whole posting calendar — admins keep seeing
// everything unless they opt in. Stored under its own key (not the generic
// cat/owner map above) since it's a single per-browser toggle, not a set of
// independent checkboxes.
const CAL_SCOPE_STORAGE_KEY = "tgn_calendar_scope_mine";

function calFilterScopeDefault() {
  return !window.TGN_CURRENT_USER_IS_ADMIN;
}

function loadCalScopeMine() {
  try {
    const raw = localStorage.getItem(CAL_SCOPE_STORAGE_KEY);
    return raw === null ? calFilterScopeDefault() : raw === "true";
  } catch (e) {
    return calFilterScopeDefault();
  }
}

function saveCalScopeMine(value) {
  try {
    localStorage.setItem(CAL_SCOPE_STORAGE_KEY, value ? "true" : "false");
  } catch (e) { /* private browsing / storage disabled — won't persist */ }
}

function applyCalendarFilters() {
  const state = loadCalFilterState();
  const scopeMine = loadCalScopeMine();
  const myId = window.TGN_CURRENT_USER_ID ? String(window.TGN_CURRENT_USER_ID) : null;
  document.querySelectorAll("[data-filter-cat]").forEach(el => {
    const cat = el.dataset.filterCat;
    const owner = el.dataset.filterOwner;
    const key = calFilterKey(cat, owner);
    let visible = key in state ? state[key] !== false : calFilterDefaultVisible(cat, owner);
    if (visible && scopeMine && cat === "posting" && myId) {
      visible = owner === myId;
    }
    el.classList.toggle("cal-filtered-out", !visible);
  });
}

/* Each person collapses into a <summary> pill (see render_filter_panel) —
   the small dot next to their name is the only hint, while it's closed,
   that one of their two checkboxes (Custom events / Deadlines) is in a
   NON-default state, so narrowing the calendar down doesn't require
   opening every person's dropdown just to remember what's changed. (Custom
   events defaults to off, Deadlines defaults to on — see
   calFilterDefaultVisible — so "checked" alone no longer means "default".) */
function updateCalFilterBadges() {
  document.querySelectorAll(".cal-filter-badge").forEach(badge => {
    const owner = badge.dataset.owner;
    const ownerChecks = document.querySelectorAll(`.js-cal-filter[data-owner="${owner}"]`);
    const allDefault = Array.from(ownerChecks).every(cb => cb.checked === calFilterDefaultVisible(cb.dataset.cat, cb.dataset.owner));
    badge.hidden = allDefault;
  });
}

function initCalendarFilterPanel() {
  const checkboxes = document.querySelectorAll(".js-cal-filter");
  if (!checkboxes.length) return;
  const state = loadCalFilterState();
  checkboxes.forEach(cb => {
    const key = calFilterKey(cb.dataset.cat, cb.dataset.owner);
    cb.checked = key in state ? state[key] !== false : calFilterDefaultVisible(cb.dataset.cat, cb.dataset.owner);
    cb.addEventListener("change", () => {
      const s = loadCalFilterState();
      s[calFilterKey(cb.dataset.cat, cb.dataset.owner)] = cb.checked;
      saveCalFilterState(s);
      applyCalendarFilters();
      updateCalFilterBadges();
    });
  });
  const scopeCheckbox = document.querySelector(".js-cal-filter-scope");
  if (scopeCheckbox) {
    scopeCheckbox.checked = loadCalScopeMine();
    scopeCheckbox.addEventListener("change", () => {
      saveCalScopeMine(scopeCheckbox.checked);
      applyCalendarFilters();
    });
  }
  const selectAllBtn = document.querySelector(".js-cal-filter-all");
  selectAllBtn && selectAllBtn.addEventListener("click", () => {
    const s = loadCalFilterState();
    checkboxes.forEach(cb => { cb.checked = true; s[calFilterKey(cb.dataset.cat, cb.dataset.owner)] = true; });
    saveCalFilterState(s);
    // "Select all" is the expand-to-everything escape hatch, so it also
    // clears "Assigned to me only" — otherwise a team member could tick
    // every person's custom/deadline boxes and still not see the full
    // posting calendar.
    if (scopeCheckbox) { scopeCheckbox.checked = false; saveCalScopeMine(false); }
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

/* ---------------------------------------------------- custom event bars
   Custom Events (Nov 2026 redesign, per Jodie): off-time/unavailable team
   blocks, each with a start date and an optional end date for a multi-day
   block, drawn as one continuous bar across however many day cells it
   spans — not a separate card repeated on every day. Drawn client-side,
   overlaid on the grid, because the only reliable way to bridge the real
   gaps/borders between day cells (see .calendar-day in app.css — these
   are separate floating cards with their own border and radius, not a
   seamless table) is from each cell's actual on-screen rect, which only
   the browser knows once it's actually laid out — and that also makes
   this naturally correct on resize and on every screen size without any
   fixed-pixel assumptions.

   Deliberately fetched from its own small JSON endpoint
   (/calendar/custom-events) rather than threaded through the HTML every
   view/fragment already renders: the month view, its two infinite-scroll
   fragments, and the 2-week view all load different date ranges through
   completely different server routes, and one shared endpoint covers all
   of them without touching any of that existing rendering. */
const calEventsCache = new Map(); // output id -> {id, campaign_id, title, start, end, color, owner_id, owner_name}

function calEventGridEl() {
  return document.getElementById("calendar-grid") || document.getElementById("week-grid");
}

async function fetchCustomEvents(fromIso, toIso) {
  if (!fromIso || !toIso) return;
  try {
    const res = await fetch(`/calendar/custom-events?from=${fromIso}&to=${toIso}`);
    if (!res.ok) return;
    const events = await res.json();
    events.forEach(ev => calEventsCache.set(ev.id, ev));
    renderCustomEventBars();
  } catch (e) { /* non-critical — the rest of the calendar still works */ }
}

function isoDatesBetween(startIso, endIso) {
  const out = [];
  let d = new Date(startIso + "T00:00:00Z");
  const end = new Date(endIso + "T00:00:00Z");
  while (d <= end) {
    out.push(d.toISOString().slice(0, 10));
    d.setUTCDate(d.getUTCDate() + 1);
  }
  return out;
}

function renderCustomEventBars() {
  const grid = calEventGridEl();
  if (!grid) return;
  grid.querySelectorAll(".cal-event-bar").forEach(el => el.remove());
  grid.querySelectorAll(".has-custom-event").forEach(el => el.classList.remove("has-custom-event"));

  const gridRect = grid.getBoundingClientRect();
  calEventsCache.forEach(ev => {
    const cells = isoDatesBetween(ev.start, ev.end)
      .map(iso => grid.querySelector(`[data-date="${iso}"]`))
      .filter(Boolean);
    if (!cells.length) return; // nothing in the currently-rendered range
    cells.forEach(cell => cell.classList.add("has-custom-event"));

    // Group into visual runs: a new run starts whenever the row changes
    // (a week wrap in month view, or the 7-wide wrap in week view) —
    // detected from each cell's actual position, not date math, since
    // that's what's actually true on screen right now.
    const runs = [];
    let current = null;
    let prevTop = null;
    cells.forEach(cell => {
      const top = cell.getBoundingClientRect().top;
      if (current && prevTop !== null && Math.abs(top - prevTop) < 2) {
        current.push(cell);
      } else {
        current = [cell];
        runs.push(current);
      }
      prevTop = top;
    });

    // Nov 2026, per Jodie: show whose block this is right on the bar
    // itself (first name only — "Jodie Currie" doesn't fit a 19px pill),
    // since these are now hidden by default and only surfaced per-person.
    const firstName = (ev.owner_name || "").split(" ")[0];
    const labelText = firstName ? `${firstName} - ${ev.title}` : ev.title;

    runs.forEach((run, runIndex) => {
      const first = run[0].getBoundingClientRect();
      const last = run[run.length - 1].getBoundingClientRect();
      const bar = document.createElement("div");
      bar.className = "cal-event-bar js-open-campaign";
      bar.dataset.campaignId = ev.campaign_id;
      bar.dataset.outputId = ev.id;
      bar.dataset.filterCat = "custom";
      bar.dataset.filterOwner = ev.owner_id || "";
      bar.title = labelText;
      bar.style.left = (first.left - gridRect.left) + "px";
      bar.style.top = (first.top - gridRect.top + 6) + "px";
      bar.style.width = Math.max(last.right - first.left, 20) + "px";
      bar.style.background = ev.color || "#C895D5";

      // Only the run touching the event's TRUE start/end gets a handle —
      // a middle run (the event continues past this row in month view)
      // has nothing to drag, since neither of its visual edges is a real
      // boundary of the event.
      if (runIndex === 0) {
        const leftHandle = document.createElement("div");
        leftHandle.className = "cal-event-handle cal-event-handle-left";
        leftHandle.dataset.edge = "start";
        bar.appendChild(leftHandle);
      }

      const label = document.createElement("span");
      label.className = "cal-event-label";
      label.textContent = labelText;
      bar.appendChild(label);

      if (runIndex === runs.length - 1) {
        const rightHandle = document.createElement("div");
        rightHandle.className = "cal-event-handle cal-event-handle-right";
        rightHandle.dataset.edge = "end";
        bar.appendChild(rightHandle);
      }

      grid.appendChild(bar);
    });
  });
  // The bars just carry data-filter-cat="custom"/data-filter-owner like
  // anything else on the calendar (see applyCalendarFilters above) — this
  // just needs to run once more now that they actually exist in the DOM.
  applyCalendarFilters();
}

/* ---------------------------------------------------- resize a custom
   event by dragging its left/right edge (Nov 2026, per Jodie: "click on a
   custom event and drag its right end... or left end... without having to
   click on the event to open a dialogue box"). A plain mousedown/move/up
   drag rather than HTML5 drag-and-drop, since it needs to track a pixel
   position against the day-cell grid, not drop onto one target element.

   While dragging, the bars themselves go pointer-events:none (see
   .cal-resizing in app.css) so elementFromPoint can see straight through
   to the actual day cell underneath the pointer — otherwise the very bar
   being dragged (or a neighbour) would always be the topmost hit.

   Uses the same PATCH /api/outputs/<id> endpoint the campaign panel's own
   Start/Through date fields use, so permissions are identical: anyone
   assigned to the event (or an admin) can drag it — same as everyone
   else's content. */
let calEventResize = null;

function calEventDateAtPoint(clientX, clientY) {
  const el = document.elementFromPoint(clientX, clientY);
  const cell = el && el.closest("[data-date]");
  return cell ? cell.dataset.date : null;
}

document.addEventListener("mousedown", (e) => {
  const handle = e.target.closest(".cal-event-handle");
  if (!handle) return;
  const bar = handle.closest(".cal-event-bar");
  const ev = bar && calEventsCache.get(parseInt(bar.dataset.outputId, 10));
  if (!ev) return;
  e.preventDefault();
  e.stopPropagation();
  calEventResize = {
    outputId: bar.dataset.outputId,
    edge: handle.dataset.edge,
    ev,
    originalStart: ev.start,
    originalEnd: ev.end,
    changed: false,
  };
  document.body.classList.add("cal-resizing");
});

// A click fires on mouseup right after a drag that started on the handle —
// swallow it so it never reaches the document-level "open campaign panel"
// listener (the handle sits inside a .js-open-campaign bar).
document.addEventListener("click", (e) => {
  if (e.target.closest(".cal-event-handle")) e.stopPropagation();
}, true);

document.addEventListener("mousemove", (e) => {
  if (!calEventResize) return;
  const iso = calEventDateAtPoint(e.clientX, e.clientY);
  if (!iso) return;
  const { ev, edge } = calEventResize;
  if (edge === "start") {
    if (iso === ev.start || iso > ev.end) return;
    ev.start = iso;
  } else {
    if (iso === ev.end || iso < ev.start) return;
    ev.end = iso;
  }
  calEventResize.changed = true;
  renderCustomEventBars();
});

document.addEventListener("mouseup", async () => {
  if (!calEventResize) return;
  const state = calEventResize;
  calEventResize = null;
  document.body.classList.remove("cal-resizing");
  if (!state.changed) return;
  const payload = state.edge === "start" ? { publish_date: state.ev.start } : { end_date: state.ev.end };
  try {
    await tgnFetch(`/api/outputs/${state.outputId}`, { method: "PATCH", body: JSON.stringify(payload) });
    window.location.reload();
  } catch (err) {
    alert(err.message);
    window.location.reload(); // reset to the server's actual dates
  }
});

let calEventResizeTimer = null;
window.addEventListener("resize", () => {
  clearTimeout(calEventResizeTimer);
  calEventResizeTimer = setTimeout(renderCustomEventBars, 150);
});

// Initial load for whichever view this is: month view already exposes its
// rendered range on the existing sentinels (earliest_from / next_from —
// see calendar_month.html), so no new plumbing is needed there; the week
// view exposes its own start/end directly on #week-grid.
(function initCustomEventsForCurrentView() {
  if (calendarSentinelTop && calendarSentinel) {
    const from = calendarSentinelTop.dataset.earliestFrom;
    const to = addDaysToIsoDate(calendarSentinel.dataset.nextFrom, -1);
    fetchCustomEvents(from, to);
    return;
  }
  const weekGrid = document.getElementById("week-grid");
  if (weekGrid) fetchCustomEvents(weekGrid.dataset.start, weekGrid.dataset.end);
})();

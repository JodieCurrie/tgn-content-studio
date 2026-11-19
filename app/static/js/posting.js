// Posting queue (Nov 2026): phone-friendly final-video upload straight to
// Google Drive, approval, and one-tap share. See app/posting.py.
(function () {
  const CHUNK_SIZE = 8 * 1024 * 1024; // must be a multiple of 256KB for Drive

  async function postJson(url, body) {
    const res = await fetch(url, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body || {}),
    });
    let data = {};
    try { data = await res.json(); } catch (e) { /* non-JSON error page */ }
    if (!res.ok) throw new Error(data.error || "Something went wrong (" + res.status + ").");
    return data;
  }

  function showError(id, msg) {
    const el = document.getElementById(id);
    if (!el) return alert(msg);
    el.textContent = msg;
    el.style.display = "";
  }

  // ------------------------------------------------------------ upload
  // One chunk PUT to Drive's resumable-session URL. 308 = "got it, send the
  // next piece"; 200/201 = finished (body is the file's JSON).
  function putChunk(sessionUrl, blob, start, total, onProgress) {
    return new Promise((resolve, reject) => {
      const xhr = new XMLHttpRequest();
      xhr.open("PUT", sessionUrl);
      xhr.setRequestHeader("Content-Range", `bytes ${start}-${start + blob.size - 1}/${total}`);
      xhr.upload.onprogress = (e) => { if (e.lengthComputable) onProgress(start + e.loaded); };
      xhr.onload = () => {
        if (xhr.status === 308) return resolve({ done: false });
        if (xhr.status === 200 || xhr.status === 201) {
          let file = {};
          try { file = JSON.parse(xhr.responseText); } catch (e) { /* ignore */ }
          return resolve({ done: true, file });
        }
        reject(new Error("Drive said " + xhr.status));
      };
      xhr.onerror = () => reject(new Error("Network problem during upload."));
      xhr.send(blob);
    });
  }

  async function uploadToDrive(sessionUrl, file, onProgress) {
    const total = file.size;
    let start = 0;
    while (start < total) {
      const end = Math.min(start + CHUNK_SIZE, total);
      const blob = file.slice(start, end);
      let attempt = 0, result;
      for (;;) {
        try {
          result = await putChunk(sessionUrl, blob, start, total, onProgress);
          break;
        } catch (err) {
          attempt += 1;
          if (attempt >= 3) throw err;
          await new Promise(r => setTimeout(r, 1500 * attempt));
        }
      }
      start = end;
      if (result.done) return result.file;
    }
    throw new Error("Upload finished without Drive confirming the file.");
  }

  function initUploadPage() {
    const form = document.getElementById("upload-form");
    if (!form) return;
    const select = document.getElementById("upload-output");
    const caption = document.getElementById("upload-caption");
    const coverWrap = document.getElementById("cover-wrap");
    const bar = document.getElementById("upload-bar");
    const status = document.getElementById("upload-status");
    const progress = document.getElementById("upload-progress");
    const submit = document.getElementById("upload-submit");

    const formatSel = document.getElementById("upload-format");
    const suggestBtn = document.getElementById("btn-suggest-caption");

    async function suggestCaption(force) {
      const id = select.value;
      if (!id) return;
      if (!force && caption.value.trim()) return;
      suggestBtn.disabled = true;
      try {
        const res = await fetch(`/post/api/${id}/suggest-caption`);
        const data = await res.json();
        if (res.ok) caption.value = data.caption;
      } catch (e) { /* keep whatever's there */ }
      suggestBtn.disabled = false;
    }

    function onSelectChange() {
      const opt = select.options[select.selectedIndex];
      if (!opt || !opt.value) { coverWrap.style.display = "none"; return; }
      const platforms = (opt.dataset.platforms || "").split(",");
      coverWrap.style.display = platforms.includes("youtube") ? "" : "none";
      if (opt.dataset.format) formatSel.value = opt.dataset.format;
      caption.value = opt.dataset.caption || "";
      suggestCaption(false);
    }
    select.addEventListener("change", onSelectChange);
    suggestBtn.addEventListener("click", () => {
      if (caption.value.trim() && !confirm("Replace what's in the caption box with a fresh suggestion?")) return;
      suggestCaption(true);
    });
    onSelectChange();

    form.addEventListener("submit", async (e) => {
      e.preventDefault();
      document.getElementById("upload-error").style.display = "none";
      const file = document.getElementById("upload-file").files[0];
      const outputId = select.value;
      if (!file || !outputId) return showError("upload-error", "Choose the post and the file first.");
      submit.disabled = true;
      progress.style.display = "";
      status.textContent = "Starting upload…";
      bar.style.width = "0";
      const mime = file.type || (file.name.toLowerCase().endsWith(".mov") ? "video/quicktime" : "video/mp4");
      try {
        const { session_url } = await postJson("/post/api/upload/start", {
          output_id: Number(outputId), filename: file.name, mime, size: file.size,
        });
        const driveFile = await uploadToDrive(session_url, file, (sent) => {
          const pct = Math.min(100, Math.round((sent / file.size) * 100));
          bar.style.width = pct + "%";
          status.textContent = `Uploading… ${pct}%`;
        });
        if (!driveFile.id) throw new Error("Drive didn't return the file's id.");
        status.textContent = "Saving…";
        const cover = document.getElementById("upload-cover");
        if (cover && cover.files[0] && coverWrap.style.display !== "none") {
          const fd = new FormData();
          fd.append("file", cover.files[0]);
          const cr = await fetch(`/post/api/${outputId}/cover`, { method: "POST", body: fd });
          if (!cr.ok) throw new Error("The video uploaded, but the cover image didn't. Add it from the review page.");
        }
        const done = await postJson("/post/api/upload/complete", {
          output_id: Number(outputId), drive_id: driveFile.id, filename: file.name, mime,
          caption: caption.value.trim(), format: formatSel.value,
        });
        status.textContent = "Done ✓";
        window.location.href = done.review_url;
      } catch (err) {
        showError("upload-error", err.message);
        submit.disabled = false;
      }
    });
  }

  // ------------------------------------------------------------ review
  function selectedPlatformIds() {
    return Array.from(document.querySelectorAll("#review-platforms input:checked")).map(i => Number(i.value));
  }

  async function sharePost(card) {
    const caption = document.getElementById("review-caption").value;
    const btn = document.getElementById("btn-share");
    const original = btn.textContent;
    btn.disabled = true;
    btn.textContent = "Getting the video ready…";
    try {
      try { await navigator.clipboard.writeText(caption); } catch (e) { /* clipboard may be blocked — share still works */ }
      const res = await fetch(card.dataset.mediaUrl);
      if (!res.ok) throw new Error("Couldn't load the video.");
      const blob = await res.blob();
      const file = new File([blob], card.dataset.mediaName || "post.mp4", { type: card.dataset.mediaMime || blob.type });
      if (navigator.canShare && navigator.canShare({ files: [file] })) {
        await navigator.share({ files: [file], text: caption });
        // The share sheet completed — the app can't see inside Instagram/TikTok,
        // so this is the moment it's registered as posted (with an Undo).
        if (card.dataset.state !== "posted") {
          try {
            await postJson(`/post/api/${card.dataset.outputId}/posted`, {});
            card.dataset.state = "posted";
            showPostedBanner(card);
          } catch (e) { /* she can still tap "I've posted it" */ }
        }
      } else {
        showError("review-error", "This browser can't share files directly (common on desktop). Use “Download video”, post it from Instagram/TikTok/Facebook, then tap “I've posted it” — the caption is already copied.");
      }
    } catch (err) {
      if (err.name !== "AbortError") showError("review-error", err.message);
    } finally {
      btn.disabled = false;
      btn.textContent = original;
    }
  }

  function showPostedBanner(card) {
    const el = document.getElementById("review-posted-banner");
    if (!el) return;
    el.style.display = "";
    const undo = document.getElementById("btn-undo-posted");
    undo.onclick = async () => {
      undo.disabled = true;
      try { await postJson(`/post/api/${card.dataset.outputId}/unposted`, {}); window.location.reload(); }
      catch (err) { showError("review-error", err.message); undo.disabled = false; }
    };
  }

  function initReviewPage() {
    const card = document.getElementById("review-card");
    if (!card) return;
    const id = card.dataset.outputId;
    const bind = (btnId, fn) => { const b = document.getElementById(btnId); if (b) b.addEventListener("click", fn); };
    bind("btn-approve", async (e) => {
      e.target.disabled = true;
      try {
        await postJson(`/post/api/${id}/approve`, {
          caption: document.getElementById("review-caption").value,
          platform_ids: selectedPlatformIds(),
        });
        window.location.reload();
      } catch (err) { showError("review-error", err.message); e.target.disabled = false; }
    });
    bind("btn-share", () => sharePost(card));
    bind("btn-suggest", async (e) => {
      const box = document.getElementById("review-caption");
      if (box.value.trim() && !confirm("Replace the caption with a fresh suggestion?")) return;
      e.target.disabled = true;
      try {
        const res = await fetch(`/post/api/${id}/suggest-caption`);
        const data = await res.json();
        if (res.ok) box.value = data.caption;
      } finally { e.target.disabled = false; }
    });
    const fmt = document.getElementById("review-format");
    if (fmt) fmt.addEventListener("change", async () => {
      try { await postJson(`/post/api/${id}/format`, { format: fmt.value }); window.location.reload(); }
      catch (err) { showError("review-error", err.message); }
    });
    bind("btn-gen-cover", async (e) => {
      e.target.disabled = true;
      try {
        const r = await postJson(`/post/api/${id}/generate-cover`, {});
        e.target.textContent = r.message;
      } catch (err) { showError("review-error", err.message); e.target.disabled = false; }
    });
    const coverIn = document.getElementById("review-cover-file");
    if (coverIn) coverIn.addEventListener("change", async () => {
      if (!coverIn.files[0]) return;
      const fd = new FormData(); fd.append("file", coverIn.files[0]);
      const r = await fetch(`/post/api/${id}/cover`, { method: "POST", body: fd });
      if (r.ok) window.location.reload(); else showError("review-error", "Couldn't upload the cover.");
    });
    // Caption edits save as she types so nothing's lost if she navigates away.
    const capBox = document.getElementById("review-caption");
    if (capBox && !capBox.disabled) {
      let t; capBox.addEventListener("input", () => {
        clearTimeout(t);
        t = setTimeout(() => postJson(`/post/api/${id}/caption`, { caption: capBox.value }).catch(() => {}), 800);
      });
    }
    bind("btn-copy", async (e) => {
      try { await navigator.clipboard.writeText(document.getElementById("review-caption").value); e.target.textContent = "Copied ✓"; }
      catch (err) { showError("review-error", "Couldn't copy — select the caption text and copy it by hand."); }
    });
    const simple = (btnId, path, confirmText) => bind(btnId, async (e) => {
      if (confirmText && !confirm(confirmText)) return;
      e.target.disabled = true;
      try { await postJson(`/post/api/${id}/${path}`, {}); window.location.reload(); }
      catch (err) { showError("review-error", err.message); e.target.disabled = false; }
    });
    simple("btn-posted", "posted");
    simple("btn-unposted", "unposted", "Mark this as not posted?");
    simple("btn-unapprove", "unapprove");
  }

  // ------------------------------------------------------------- queue
  function initQueuePage() {
    document.querySelectorAll(".js-mark-posted").forEach(btn => btn.addEventListener("click", async () => {
      btn.disabled = true;
      try { await postJson(`/post/api/${btn.dataset.outputId}/posted`, {}); window.location.reload(); }
      catch (err) { alert(err.message); btn.disabled = false; }
    }));
    document.querySelectorAll(".js-reschedule").forEach(btn => btn.addEventListener("click", async (e) => {
      e.preventDefault(); e.stopPropagation();
      if (!confirm("Reschedule this post? Filler posts move to the next empty slot; a campaign and its highlights move to the same day next week.")) return;
      btn.disabled = true;
      try { const r = await postJson(`/post/api/${btn.dataset.outputId}/reschedule`, {}); alert(r.message); window.location.reload(); }
      catch (err) { alert(err.message); btn.disabled = false; }
    }));
  }

  initUploadPage();
  initReviewPage();
  initQueuePage();
})();

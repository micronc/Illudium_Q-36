(() => {
  // markdown: tables-headings-v2 — headings, GFM tables, lists, hr, fences
  const $ = (sel) => document.querySelector(sel);

  const fileListEl = $("#file-list");
  const fileCountEl = $("#file-count");
  const intelStatus = $("#intel-status");
  const chatStatus = $("#chat-status");
  const messagesEl = $("#messages");
  const chatInput = $("#chat-input");
  const voiceBadge = $("#voice-badge");
  const btnMic = $("#btn-mic");
  const speakReplies = $("#speak-replies");

  let selectedFiles = new Set();
  let busy = false;
  let recognition = null;
  let listening = false;

  function setStatus(el, text, isError = false) {
    el.textContent = text || "";
    el.classList.toggle("error", Boolean(isError));
  }

  function formatBytes(n) {
    if (n < 1024) return `${n} B`;
    if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} KB`;
    return `${(n / (1024 * 1024)).toFixed(1)} MB`;
  }

  function inlineMarkdown(text) {
    return escapeHtml(text)
      .replace(/\*\*(.+?)\*\*/g, "<strong>$1</strong>")
      .replace(/`([^`]+)`/g, "<code>$1</code>");
  }

  function isTableSeparator(line) {
    return /^\|?[\s:-]+\|[\s|:-]*$/.test(line.trim()) && line.includes("-");
  }

  function splitTableRow(line) {
    let s = line.trim();
    if (s.startsWith("|")) s = s.slice(1);
    if (s.endsWith("|")) s = s.slice(0, -1);
    return s.split("|").map((c) => c.trim());
  }

  function formatMessageHtml(text) {
    const fences = [];
    const withFences = String(text).replace(
      /```([^\n`]*)\n([\s\S]*?)```/g,
      (_m, lang, code) => {
        const idx = fences.length;
        fences.push({ lang: String(lang || "").trim().toLowerCase(), code: code.replace(/\n$/, "") });
        return `\n%%FENCE_${idx}%%\n`;
      }
    );

    const lines = withFences.replace(/\r\n/g, "\n").split("\n");
    const out = [];
    let i = 0;
    let para = [];

    const flushPara = () => {
      if (!para.length) return;
      out.push(`<p class="msg-p">${inlineMarkdown(para.join(" "))}</p>`);
      para = [];
    };

    while (i < lines.length) {
      const line = lines[i];
      const trimmed = line.trim();

      const fenceMatch = trimmed.match(/^%%FENCE_(\d+)%%$/);
      if (fenceMatch) {
        flushPara();
        const block = fences[Number(fenceMatch[1])];
        if (block.lang === "mermaid") {
          out.push(
            `<div class="mermaid-wrap"><pre class="mermaid">${escapeHtml(block.code)}</pre></div>`
          );
        } else {
          out.push(`<pre class="msg-pre"><code>${escapeHtml(block.code)}</code></pre>`);
        }
        i += 1;
        continue;
      }

      if (!trimmed) {
        flushPara();
        i += 1;
        continue;
      }

      if (/^---+$/.test(trimmed) || /^\*\*\*+$/.test(trimmed)) {
        flushPara();
        out.push('<hr class="msg-hr" />');
        i += 1;
        continue;
      }

      const heading = trimmed.match(/^(#{1,4})\s+(.+)$/);
      if (heading) {
        flushPara();
        const level = Math.min(heading[1].length + 1, 5); // h2–h5 visually
        out.push(`<h${level} class="msg-h msg-h${heading[1].length}">${inlineMarkdown(heading[2])}</h${level}>`);
        i += 1;
        continue;
      }

      // GFM table: header + separator + rows
      if (
        trimmed.includes("|") &&
        i + 1 < lines.length &&
        isTableSeparator(lines[i + 1])
      ) {
        flushPara();
        const headers = splitTableRow(trimmed);
        i += 2;
        const rows = [];
        while (i < lines.length && lines[i].trim().includes("|") && !lines[i].trim().match(/^%%FENCE_/)) {
          if (!lines[i].trim()) break;
          rows.push(splitTableRow(lines[i]));
          i += 1;
        }
        let table = '<div class="msg-table-wrap"><table class="msg-table"><thead><tr>';
        headers.forEach((h) => {
          table += `<th>${inlineMarkdown(h)}</th>`;
        });
        table += "</tr></thead><tbody>";
        rows.forEach((row) => {
          table += "<tr>";
          headers.forEach((_, idx) => {
            table += `<td>${inlineMarkdown(row[idx] || "")}</td>`;
          });
          table += "</tr>";
        });
        table += "</tbody></table></div>";
        out.push(table);
        continue;
      }

      // Unordered list block
      if (/^[-*]\s+/.test(trimmed)) {
        flushPara();
        out.push('<ul class="msg-list">');
        while (i < lines.length && /^[-*]\s+/.test(lines[i].trim())) {
          const item = lines[i].trim().replace(/^[-*]\s+/, "");
          out.push(`<li>${inlineMarkdown(item)}</li>`);
          i += 1;
        }
        out.push("</ul>");
        continue;
      }

      // Ordered list block
      if (/^\d+\.\s+/.test(trimmed)) {
        flushPara();
        out.push('<ol class="msg-list">');
        while (i < lines.length && /^\d+\.\s+/.test(lines[i].trim())) {
          const item = lines[i].trim().replace(/^\d+\.\s+/, "");
          out.push(`<li>${inlineMarkdown(item)}</li>`);
          i += 1;
        }
        out.push("</ol>");
        continue;
      }

      para.push(trimmed);
      i += 1;
    }
    flushPara();
    return out.join("");
  }

  let lastAssistantText = "";
  let selectedModel = null;

  async function renderMermaidIn(root) {
    const nodes = root.querySelectorAll(".mermaid:not([data-processed])");
    if (!nodes.length) return;
    const waitFor = async () => {
      for (let i = 0; i < 40; i++) {
        if (window.__illudiumMermaid) return window.__illudiumMermaid;
        await new Promise((r) => setTimeout(r, 50));
      }
      return null;
    };
    const mermaid = await waitFor();
    if (!mermaid) return;
    try {
      await mermaid.run({ nodes });
    } catch (err) {
      console.warn("Mermaid render failed", err);
    }
  }

  function formatUsd(n, digits = 4) {
    const v = Number(n || 0);
    return `$${v.toFixed(digits)}`;
  }

  function updateCostMeter(usageTotal, budget) {
    const tokens = $("#token-count");
    const tokenIn = $("#token-in");
    const tokenOut = $("#token-out");
    const cost = $("#cost-usd");
    const meter = $("#cost-meter");
    if (usageTotal) {
      if (tokens) tokens.textContent = String(usageTotal.total_tokens || 0);
      if (tokenIn) tokenIn.textContent = String(usageTotal.input_tokens || 0);
      if (tokenOut) tokenOut.textContent = String(usageTotal.output_tokens || 0);
      if (cost) cost.textContent = formatUsd(usageTotal.cost_usd, 4);
    }
    if (budget) {
      const spentEl = $("#budget-spent");
      const totalEl = $("#budget-total");
      const remEl = $("#budget-remaining");
      if (spentEl) spentEl.textContent = formatUsd(budget.spent_usd, 4);
      if (budget.budget_configured && budget.budget_usd != null) {
        if (totalEl) totalEl.textContent = formatUsd(budget.budget_usd, 2);
        if (remEl) remEl.textContent = formatUsd(budget.remaining_usd, 4);
      } else {
        if (totalEl) totalEl.textContent = "not set";
        if (remEl) remEl.textContent = "—";
      }
      if (meter) {
        meter.classList.remove("budget-warn", "budget-critical");
        const pct = Number(budget.percent_used);
        if (!Number.isNaN(pct)) {
          if (pct >= 90) meter.classList.add("budget-critical");
          else if (pct >= 75) meter.classList.add("budget-warn");
        }
      }
    }
  }

  function applySpeakRepliesDefault() {
    if (!speakReplies) return;
    try {
      const saved = localStorage.getItem("illudium-speak-replies");
      if (saved === "1") speakReplies.checked = true;
      else if (saved === "0") speakReplies.checked = false;
      else speakReplies.checked = false; // default off
    } catch (_) {
      speakReplies.checked = false;
    }
    speakReplies.addEventListener("change", () => {
      try {
        localStorage.setItem(
          "illudium-speak-replies",
          speakReplies.checked ? "1" : "0"
        );
      } catch (_) {
        /* ignore */
      }
    });
  }

  function appendMessage(role, content) {
    const div = document.createElement("div");
    div.className = `msg ${role}`;
    if (role === "assistant") {
      div.setAttribute("tabindex", "-1");
      lastAssistantText = content;
    }
    const label = document.createElement("span");
    label.className = "role";
    label.textContent = role === "user" ? "Analyst" : role === "assistant" ? "Illudium-Q36" : "System";
    div.appendChild(label);
    if (role === "assistant") {
      const body = document.createElement("div");
      body.className = "msg-body";
      body.innerHTML = formatMessageHtml(content);
      div.appendChild(body);
      renderMermaidIn(body);
    } else {
      div.appendChild(document.createTextNode(content));
    }
    messagesEl.appendChild(div);
    messagesEl.scrollTop = messagesEl.scrollHeight;
    return div;
  }

  function renderFiles(files, { selectNew = true } = {}) {
    const prev = new Set(selectedFiles);
    const knownBefore = new Set(prev);
    selectedFiles = new Set();
    fileListEl.innerHTML = "";

    if (!files.length) {
      fileCountEl.textContent = "0 files";
      const empty = document.createElement("li");
      empty.innerHTML = '<span class="file-meta"><span class="name">No intel yet</span><span class="size">Upload or paste to begin</span></span>';
      fileListEl.appendChild(empty);
      return;
    }

    fileCountEl.textContent = `${files.length} file${files.length === 1 ? "" : "s"}`;

    files.forEach((f) => {
      const isNew = !knownBefore.has(f.name);
      const checked =
        knownBefore.size === 0 || prev.has(f.name) || (selectNew && isNew);
      if (checked) selectedFiles.add(f.name);

      const li = document.createElement("li");
      li.innerHTML = `
        <label>
          <input type="checkbox" data-name="${escapeAttr(f.name)}" ${checked ? "checked" : ""} aria-label="Include ${escapeAttr(f.name)} in analysis" />
          <span class="file-meta">
            <span class="name" title="${escapeAttr(f.name)}">${escapeHtml(f.name)}</span>
            <span class="size">${formatBytes(f.size)} · ${escapeHtml(f.modified || "")}</span>
          </span>
        </label>
        <button type="button" class="btn-icon" data-delete="${escapeAttr(f.name)}" title="Remove ${escapeAttr(f.name)}" aria-label="Remove ${escapeAttr(f.name)}">✕</button>
      `;
      fileListEl.appendChild(li);
    });
  }

  function escapeHtml(s) {
    return String(s)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;");
  }

  function escapeAttr(s) {
    return escapeHtml(s).replace(/'/g, "&#39;");
  }

  function selectedList() {
    return Array.from(selectedFiles);
  }

  async function refreshFiles() {
    const res = await fetch("/api/intel");
    const data = await res.json();
    renderFiles(data.files || []);
    if (data.storage) updateStorageMeter(data.storage);
  }

  function showAnonymizeDialog(anonymization) {
    const dialog = $("#anonymize-dialog");
    if (!dialog || !anonymization) return Promise.resolve();
    const warningEl = $("#anonymize-warning");
    const summaryEl = $("#anonymize-summary");
    const listEl = $("#anonymize-replacements");
    const okBtn = $("#btn-anonymize-ok");
    const counts = anonymization.counts || {};
    const total = counts.total || 0;
    if (warningEl) {
      warningEl.textContent =
        anonymization.warning ||
        `You have just uploaded "${anonymization.file_count || 0}" file(s). To make sure IP addresses or PII is uploaded, I will now replace those with sequential numbers as place holders.`;
    }
    if (summaryEl) {
      summaryEl.textContent = total
        ? `Replaced ${total} value(s): IP ${counts.ip || 0}, email ${counts.email || 0}, phone ${counts.phone || 0}, SSN ${counts.ssn || 0}, path ${counts.path || 0}.`
        : "No IP addresses or PII patterns were found in this upload.";
    }
    if (listEl) {
      listEl.innerHTML = "";
      const reps = anonymization.replacements || [];
      if (reps.length) {
        listEl.hidden = false;
        reps.slice(0, 40).forEach((r) => {
          const li = document.createElement("li");
          li.innerHTML = `<span class="file-meta"><span class="name">${escapeHtml(r.kind)} → ${escapeHtml(r.placeholder)}</span><span class="size">${escapeHtml(r.original)}</span></span>`;
          listEl.appendChild(li);
        });
      } else {
        listEl.hidden = true;
      }
    }
    dialog.hidden = false;
    okBtn?.focus();
    announce(warningEl?.textContent || "Upload anonymization notice.");
    return new Promise((resolve) => {
      const close = () => {
        dialog.hidden = true;
        okBtn?.removeEventListener("click", close);
        resolve();
      };
      okBtn?.addEventListener("click", close);
    });
  }

  async function uploadFiles(fileList) {
    if (!fileList || !fileList.length) return;
    const form = new FormData();
    Array.from(fileList).forEach((f) => form.append("files", f));
    setStatus(intelStatus, "Uploading…");
    const res = await fetch("/api/intel", { method: "POST", body: form });
    const data = await res.json();
    if (data.storage) updateStorageMeter(data.storage);
    if (res.status === 413 || data.limit_reached) {
      showCaptureDialog(data);
      setStatus(intelStatus, data.error || "Session storage limit reached.", true);
      return;
    }
    if (!res.ok) {
      setStatus(intelStatus, data.error || "Upload failed", true);
      return;
    }
    renderFiles(data.files || []);
    const msg = [
      data.saved?.length ? `Added: ${data.saved.join(", ")}` : "",
      data.errors?.length ? `Errors: ${data.errors.join("; ")}` : "",
    ]
      .filter(Boolean)
      .join(" · ");
    setStatus(intelStatus, msg || "Done");
    if (data.anonymization) await showAnonymizeDialog(data.anonymization);
  }

  async function addPaste() {
    const text = $("#paste-text").value.trim();
    const name = $("#paste-name").value.trim();
    if (!text) {
      setStatus(intelStatus, "Paste some intel first.", true);
      return;
    }
    const form = new FormData();
    form.append("paste_text", text);
    if (name) form.append("paste_name", name);
    setStatus(intelStatus, "Saving paste…");
    const res = await fetch("/api/intel", { method: "POST", body: form });
    const data = await res.json();
    if (data.storage) updateStorageMeter(data.storage);
    if (res.status === 413 || data.limit_reached) {
      showCaptureDialog(data);
      setStatus(intelStatus, data.error || "Session storage limit reached.", true);
      return;
    }
    if (!res.ok) {
      setStatus(intelStatus, data.error || "Failed", true);
      return;
    }
    $("#paste-text").value = "";
    $("#paste-name").value = "";
    renderFiles(data.files || []);
    setStatus(intelStatus, `Added: ${(data.saved || []).join(", ")}`);
    if (data.anonymization) await showAnonymizeDialog(data.anonymization);
  }

  async function deleteFile(name) {
    const res = await fetch(`/api/intel/${encodeURIComponent(name)}`, { method: "DELETE" });
    const data = await res.json();
    if (!res.ok) {
      setStatus(intelStatus, data.error || "Delete failed", true);
      return;
    }
    selectedFiles.delete(name);
    renderFiles(data.files || []);
    if (data.storage) updateStorageMeter(data.storage);
    setStatus(intelStatus, `Removed ${name}`);
  }

  function announce(text, { assertive = false } = {}) {
    const el = assertive ? $("#a11y-assertive") : $("#a11y-announce");
    if (!el) return;
    el.textContent = "";
    // Retrigger screen reader announcement even for identical strings
    window.setTimeout(() => {
      el.textContent = text;
    }, 20);
  }

  function stopSpeech() {
    if (window.speechSynthesis) window.speechSynthesis.cancel();
    announce("Speech stopped.");
  }

  function speak(text, { force = false } = {}) {
    if (!force && !speakReplies.checked) return;
    if (!window.speechSynthesis) {
      announce("Speech synthesis is not available in this browser.", { assertive: true });
      return;
    }
    window.speechSynthesis.cancel();
    const utter = new SpeechSynthesisUtterance(text);
    utter.rate = 1.02;
    utter.pitch = 1;
    window.speechSynthesis.speak(utter);
  }

  function speakForced(text) {
    speak(text, { force: true });
  }

  function readLastAnswer() {
    if (!lastAssistantText) {
      announce("No assistant answer to read yet.", { assertive: true });
      return;
    }
    announce("Reading last assistant answer.");
    speakForced(lastAssistantText);
    const last = messagesEl.querySelector(".msg.assistant:last-of-type");
    if (last) last.focus();
  }

  function readAtlasPanel() {
    const panel = $("#atlas-panel");
    if (!panel || panel.hidden) {
      announce("ATLAS panel is empty. Generate a briefing first.", { assertive: true });
      return;
    }
    const parts = [
      "ATT and CK versus ATLAS panel.",
      $("#atlas-note")?.textContent || "",
      "ATT and CK only: " + listText($("#atlas-attack-only")),
      "Overlap: " + listText($("#atlas-overlap")),
      "ATLAS only: " + listText($("#atlas-atlas-only")),
    ];
    announce("Reading ATLAS panel.");
    speakForced(parts.filter(Boolean).join(" "));
    panel.focus();
  }

  function listText(ul) {
    if (!ul) return "none";
    return Array.from(ul.querySelectorAll("li"))
      .map((li) => li.textContent.trim())
      .filter(Boolean)
      .join(", ") || "none";
  }

  function readIntelList() {
    const names = Array.from(fileListEl.querySelectorAll(".name")).map((n) => n.textContent.trim());
    const text = names.length
      ? `Intel corpus has ${names.length} file${names.length === 1 ? "" : "s"}: ${names.join(", ")}.`
      : "Intel corpus has no files yet.";
    announce(text);
    speakForced(text);
    $("#intel-panel")?.focus();
  }

  async function sendChat(message, { fromVoice = false } = {}) {
    const text = (message || "").trim();
    if (!text || busy) return;
    busy = true;
    $("#btn-send").disabled = true;
    appendMessage("user", text);
    chatInput.value = "";
    setStatus(chatStatus, "Analyzing…");
    const streamUi = beginStreamingAssistant();

    try {
      const res = await fetch("/api/chat/stream", {
        method: "POST",
        headers: { "Content-Type": "application/json", Accept: "text/event-stream" },
        body: JSON.stringify({
          message: text,
          files: selectedList(),
          model: selectedModel,
        }),
      });
      let finalPayload = null;
      await readSse(res, async (evt) => {
        if (evt.type === "status") {
          setStatus(chatStatus, evt.text || "Analyzing…");
        } else if (evt.type === "token") {
          streamUi.append(evt.text || "");
        } else if (evt.type === "done") {
          finalPayload = evt;
        } else if (evt.type === "error") {
          throw new Error(evt.error || "Chat failed");
        }
      });
      if (!finalPayload) throw new Error("Chat stream ended without a reply.");
      if (finalPayload.storage) updateStorageMeter(finalPayload.storage);
      if (finalPayload.usage_total || finalPayload.budget) {
        updateCostMeter(finalPayload.usage_total, finalPayload.budget);
      }
      if (finalPayload.limit_reached) {
        showCaptureDialog(finalPayload);
      }
      const reply = streamUi.finalize(finalPayload.reply || "");
      if (fromVoice || speakReplies.checked) speak(reply);
      announce("Assistant reply ready. Press Alt R to read it aloud.");
      streamUi.el.focus();
      setStatus(chatStatus, "");
    } catch (err) {
      streamUi.el.remove();
      setStatus(chatStatus, err.message || String(err), true);
      appendMessage("system", `Error: ${err.message || err}`);
      announce(`Error: ${err.message || err}`, { assertive: true });
    } finally {
      busy = false;
      $("#btn-send").disabled = false;
      chatInput.focus();
    }
  }

  function fillIdList(el, items, emptyLabel) {
    el.innerHTML = "";
    if (!items || !items.length) {
      const li = document.createElement("li");
      li.className = "empty";
      li.textContent = emptyLabel;
      el.appendChild(li);
      return;
    }
    items.forEach((item) => {
      const li = document.createElement("li");
      li.textContent = typeof item === "string" ? item : item;
      el.appendChild(li);
    });
  }

  function renderAtlas(analysis) {
    const panel = $("#atlas-panel");
    if (!analysis) {
      panel.hidden = true;
      return;
    }
    panel.hidden = false;
    const version = analysis.data_version ? ` · data ${analysis.data_version}` : "";
    $("#atlas-note").textContent =
      "Separation & overlap from MITRE ATLAS attack-reference links" + version + ".";
    if (analysis.source_url) {
      $("#atlas-link").href = analysis.source_url;
    }

    fillIdList($("#atlas-attack-only"), analysis.attack_only, "None for this briefing");
    const overlapLabels = (analysis.overlap || []).map(
      (row) => `${row.attack_id} ↔ ${row.atlas_id}`
    );
    fillIdList($("#atlas-overlap"), overlapLabels, "No shared techniques");
    const atlasOnly = [
      ...(analysis.atlas_only || []),
      ...((analysis.atlas_suggestions || []).map((s) => `${s.id} (suggested)`)),
    ];
    fillIdList($("#atlas-atlas-only"), atlasOnly, "None evidenced");
  }

  async function generateBriefing() {
    if (busy) return;
    busy = true;
    setStatus(chatStatus, "Generating executive briefing…");
    appendMessage(
      "user",
      "Generate an executive briefing from the currently loaded intelligence."
    );
    const streamUi = beginStreamingAssistant();
    try {
      const res = await fetch("/api/briefing/stream", {
        method: "POST",
        headers: { "Content-Type": "application/json", Accept: "text/event-stream" },
        body: JSON.stringify({
          files: selectedList(),
          model: selectedModel,
        }),
      });
      let finalPayload = null;
      await readSse(res, async (evt) => {
        if (evt.type === "status") {
          setStatus(chatStatus, evt.text || "Generating executive briefing…");
        } else if (evt.type === "token") {
          streamUi.append(evt.text || "");
        } else if (evt.type === "done") {
          finalPayload = evt;
        } else if (evt.type === "error") {
          throw new Error(evt.error || "Briefing failed");
        }
      });
      if (!finalPayload) throw new Error("Briefing stream ended without content.");
      if (finalPayload.storage) updateStorageMeter(finalPayload.storage);
      if (finalPayload.usage_total || finalPayload.budget) {
        updateCostMeter(finalPayload.usage_total, finalPayload.budget);
      }
      const briefing = streamUi.finalize(finalPayload.briefing || "");
      renderAtlas(finalPayload.atlas);
      if (speakReplies.checked) speak(briefing);
      setStatus(chatStatus, `Saved ${finalPayload.path || "briefing"}`);
      announce(
        `Executive briefing ready and saved to ${finalPayload.path || "briefings"}. Press Alt R to read the briefing, or Alt A for the ATLAS panel.`,
        { assertive: true }
      );
      streamUi.el.focus();
      if (finalPayload.limit_reached) showCaptureDialog(finalPayload);
    } catch (err) {
      streamUi.el.remove();
      setStatus(chatStatus, err.message || String(err), true);
      announce(`Briefing error: ${err.message || err}`, { assertive: true });
    } finally {
      busy = false;
    }
  }

  async function clearChat() {
    const res = await fetch("/api/chat/clear", { method: "POST" });
    const data = await res.json().catch(() => ({}));
    if (data.storage) updateStorageMeter(data.storage);
    messagesEl.innerHTML = "";
    $("#atlas-panel").hidden = true;
    lastAssistantText = "";
    appendMessage("system", "Chat cleared. Intel files remain loaded.");
    if (window.speechSynthesis) window.speechSynthesis.cancel();
    setStatus(chatStatus, "");
  }

  /* ---- Voice (Web Speech API, or MediaRecorder+Whisper for Firefox) ---- */
  let mediaRecorder = null;
  let mediaChunks = [];
  let mediaStream = null;
  let voiceMode = null; // "speech" | "record" | null

  function pickRecorderMime() {
    const candidates = [
      "audio/webm;codecs=opus",
      "audio/webm",
      "audio/ogg;codecs=opus",
      "audio/mp4",
    ];
    for (const type of candidates) {
      if (window.MediaRecorder && MediaRecorder.isTypeSupported(type)) return type;
    }
    return "";
  }

  async function stopMediaTracks() {
    if (mediaStream) {
      mediaStream.getTracks().forEach((t) => t.stop());
      mediaStream = null;
    }
  }

  async function transcribeBlob(blob) {
    const form = new FormData();
    const ext = (blob.type || "").includes("ogg")
      ? "ogg"
      : (blob.type || "").includes("mp4")
        ? "m4a"
        : "webm";
    form.append("audio", blob, `voice.${ext}`);
    const res = await fetch("/api/transcribe", { method: "POST", body: form });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(data.error || "Transcription failed");
    return (data.text || "").trim();
  }

  function initSpeechRecognitionVoice() {
    const SR = window.SpeechRecognition || window.webkitSpeechRecognition;
    recognition = new SR();
    recognition.continuous = false;
    recognition.interimResults = true;
    recognition.lang = navigator.language || "en-US";

    recognition.onstart = () => {
      listening = true;
      btnMic.setAttribute("aria-pressed", "true");
      voiceBadge.textContent = "Listening…";
      voiceBadge.classList.add("listening");
      setStatus(chatStatus, "Listening — speak your question");
    };

    recognition.onend = () => {
      listening = false;
      btnMic.setAttribute("aria-pressed", "false");
      voiceBadge.textContent = "Voice ready";
      voiceBadge.classList.remove("listening");
    };

    recognition.onerror = (ev) => {
      setStatus(chatStatus, `Voice error: ${ev.error}`, true);
      voiceBadge.textContent = "Voice ready";
      voiceBadge.classList.remove("listening");
    };

    let finalTranscript = "";
    recognition.onresult = (event) => {
      let interim = "";
      finalTranscript = "";
      for (let i = event.resultIndex; i < event.results.length; i++) {
        const t = event.results[i][0].transcript;
        if (event.results[i].isFinal) finalTranscript += t;
        else interim += t;
      }
      chatInput.value = (finalTranscript || interim).trim();
      if (finalTranscript.trim()) {
        sendChat(finalTranscript.trim(), { fromVoice: true });
      }
    };

    btnMic.addEventListener("click", () => {
      if (!recognition) return;
      if (listening) {
        recognition.stop();
        return;
      }
      if (window.speechSynthesis) window.speechSynthesis.cancel();
      try {
        recognition.start();
      } catch {
        /* already started */
      }
    });
  }

  async function initRecordFallbackVoice() {
    const st = await fetch("/api/transcribe/status").then((r) => r.json()).catch(() => ({}));
    if (!st.available) {
      voiceBadge.textContent = "Voice needs Whisper key";
      voiceBadge.classList.add("unsupported");
      btnMic.disabled = true;
      btnMic.title =
        "Firefox has no built-in speech recognition. Set OPENAI_API_KEY or GROQ_API_KEY for Whisper, or use Chrome/Edge.";
      setStatus(
        chatStatus,
        "Firefox voice: add OPENAI_API_KEY or GROQ_API_KEY to .env (Whisper), then rebuild — or use Chrome/Edge.",
        true
      );
      return;
    }

    voiceBadge.textContent = "Voice record ready";
    btnMic.title = "Click to record; click again to stop and transcribe (Firefox Whisper path)";
    btnMic.disabled = false;

    btnMic.addEventListener("click", async () => {
      if (listening && mediaRecorder) {
        mediaRecorder.stop();
        return;
      }
      if (window.speechSynthesis) window.speechSynthesis.cancel();
      try {
        mediaStream = await navigator.mediaDevices.getUserMedia({ audio: true });
        mediaChunks = [];
        const mime = pickRecorderMime();
        mediaRecorder = mime
          ? new MediaRecorder(mediaStream, { mimeType: mime })
          : new MediaRecorder(mediaStream);
        mediaRecorder.ondataavailable = (ev) => {
          if (ev.data && ev.data.size) mediaChunks.push(ev.data);
        };
        mediaRecorder.onstop = async () => {
          listening = false;
          btnMic.setAttribute("aria-pressed", "false");
          voiceBadge.textContent = "Transcribing…";
          voiceBadge.classList.remove("listening");
          const blob = new Blob(mediaChunks, {
            type: mediaRecorder.mimeType || "audio/webm",
          });
          await stopMediaTracks();
          mediaRecorder = null;
          try {
            setStatus(chatStatus, "Transcribing recording…");
            const text = await transcribeBlob(blob);
            if (!text) throw new Error("Empty transcript");
            chatInput.value = text;
            setStatus(chatStatus, "Transcript ready — sending");
            await sendChat(text, { fromVoice: true });
          } catch (err) {
            setStatus(chatStatus, err.message || String(err), true);
          } finally {
            voiceBadge.textContent = "Voice record ready";
          }
        };
        mediaRecorder.start();
        listening = true;
        btnMic.setAttribute("aria-pressed", "true");
        voiceBadge.textContent = "Recording…";
        voiceBadge.classList.add("listening");
        setStatus(chatStatus, "Recording — click mic again when finished");
      } catch (err) {
        await stopMediaTracks();
        setStatus(
          chatStatus,
          err.message || "Microphone permission denied or unavailable.",
          true
        );
        voiceBadge.textContent = "Voice record ready";
      }
    });
  }

  function initVoice() {
    const SR = window.SpeechRecognition || window.webkitSpeechRecognition;
    const canRecord =
      typeof MediaRecorder !== "undefined" &&
      navigator.mediaDevices &&
      typeof navigator.mediaDevices.getUserMedia === "function";

    if (SR) {
      voiceMode = "speech";
      initSpeechRecognitionVoice();
      return;
    }
    if (canRecord) {
      voiceMode = "record";
      initRecordFallbackVoice();
      return;
    }
    voiceMode = null;
    voiceBadge.textContent = "Voice unsupported";
    voiceBadge.classList.add("unsupported");
    btnMic.disabled = true;
    btnMic.title =
      "Speech recognition and audio recording are not available in this browser.";
  }

  /* ---- Events ---- */
  $("#file-input").addEventListener("change", (e) => {
    uploadFiles(e.target.files);
    e.target.value = "";
  });

  const uploadZone = $("#upload-form");
  ["dragenter", "dragover"].forEach((evt) => {
    uploadZone.addEventListener(evt, (e) => {
      e.preventDefault();
      uploadZone.classList.add("dragover");
    });
  });
  ["dragleave", "drop"].forEach((evt) => {
    uploadZone.addEventListener(evt, (e) => {
      e.preventDefault();
      uploadZone.classList.remove("dragover");
    });
  });
  uploadZone.addEventListener("drop", (e) => {
    uploadFiles(e.dataTransfer.files);
  });

  $("#btn-paste").addEventListener("click", addPaste);
  $("#btn-briefing").addEventListener("click", generateBriefing);
  $("#btn-clear").addEventListener("click", clearChat);

  fileListEl.addEventListener("change", (e) => {
    const cb = e.target.closest('input[type="checkbox"]');
    if (!cb) return;
    const name = cb.getAttribute("data-name");
    if (cb.checked) selectedFiles.add(name);
    else selectedFiles.delete(name);
  });

  fileListEl.addEventListener("click", (e) => {
    const btn = e.target.closest("[data-delete]");
    if (!btn) return;
    deleteFile(btn.getAttribute("data-delete"));
  });

  $("#chat-form").addEventListener("submit", (e) => {
    e.preventDefault();
    sendChat(chatInput.value);
  });

  chatInput.addEventListener("keydown", (e) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      sendChat(chatInput.value);
    }
  });

  /* ---- Section 508 / VPAT accessibility mode ---- */
  const A11Y_KEY = "illudium-a11y-508";
  const a11yToggle = $("#a11y-508-toggle");
  const a11yAnnounce = $("#a11y-announce");

  function setA11yMode(enabled, { announce = true } = {}) {
    document.documentElement.setAttribute("data-a11y", enabled ? "508" : "");
    if (!enabled) document.documentElement.removeAttribute("data-a11y");
    try {
      localStorage.setItem(A11Y_KEY, enabled ? "1" : "0");
    } catch (_) {
      /* ignore quota / private mode */
    }
    if (a11yToggle) a11yToggle.checked = enabled;
    if (announce && a11yAnnounce) {
      a11yAnnounce.textContent = enabled
        ? "Section 508 accessibility mode on. High contrast and larger text enabled."
        : "Section 508 accessibility mode off. Default appearance restored.";
    }
  }

  function initA11yMode() {
    let enabled = false;
    try {
      enabled = localStorage.getItem(A11Y_KEY) === "1";
    } catch (_) {
      enabled = false;
    }
    // Honor OS high-contrast preference on first visit only
    if (!enabled) {
      try {
        if (
          localStorage.getItem(A11Y_KEY) === null &&
          window.matchMedia &&
          window.matchMedia("(prefers-contrast: more)").matches
        ) {
          enabled = true;
        }
      } catch (_) {
        /* ignore */
      }
    }
    setA11yMode(enabled, { announce: false });
    if (a11yToggle) {
      a11yToggle.addEventListener("change", () => {
        setA11yMode(a11yToggle.checked, { announce: true });
      });
    }
  }

  /* ---- Blind / screen-reader researcher shortcuts ---- */
  const helpDialog = $("#keyboard-help");
  let helpLastFocus = null;

  function openHelp() {
    if (!helpDialog) return;
    helpLastFocus = document.activeElement;
    helpDialog.hidden = false;
    $("#btn-a11y-help-close")?.focus();
    announce("Keyboard help opened.");
  }

  function closeHelp() {
    if (!helpDialog || helpDialog.hidden) return;
    helpDialog.hidden = true;
    announce("Keyboard help closed.");
    (helpLastFocus || $("#btn-a11y-help"))?.focus();
  }

  function toggleHelp() {
    if (!helpDialog) return;
    if (helpDialog.hidden) openHelp();
    else closeHelp();
  }

  function isTypingTarget(el) {
    if (!el) return false;
    const tag = el.tagName;
    return tag === "INPUT" || tag === "TEXTAREA" || el.isContentEditable;
  }

  function initBlindResearcherSupport() {
    $("#btn-read-last")?.addEventListener("click", readLastAnswer);
    $("#btn-stop-speech")?.addEventListener("click", stopSpeech);
    $("#btn-read-atlas")?.addEventListener("click", readAtlasPanel);
    $("#btn-a11y-help")?.addEventListener("click", openHelp);
    $("#btn-a11y-help-close")?.addEventListener("click", closeHelp);

    helpDialog?.addEventListener("keydown", (e) => {
      if (e.key === "Escape") {
        e.preventDefault();
        closeHelp();
      }
    });

    document.addEventListener("keydown", (e) => {
      if (e.key === "Escape") {
        if (helpDialog && !helpDialog.hidden) {
          e.preventDefault();
          closeHelp();
          return;
        }
        if (window.speechSynthesis?.speaking) {
          e.preventDefault();
          stopSpeech();
        }
        return;
      }

      const typing = isTypingTarget(document.activeElement);
      // Alt+/ opens help even from inputs; bare "?" only when not typing
      if ((e.altKey && e.key === "/") || (!typing && e.key === "?")) {
        e.preventDefault();
        toggleHelp();
        return;
      }

      if (!e.altKey) return;
      const key = e.key.toLowerCase();

      if (key === "r") {
        e.preventDefault();
        readLastAnswer();
      } else if (key === "a") {
        e.preventDefault();
        readAtlasPanel();
      } else if (key === "i") {
        e.preventDefault();
        readIntelList();
      } else if (key === "s") {
        e.preventDefault();
        stopSpeech();
      } else if (key === "1") {
        e.preventDefault();
        $("#intel-panel")?.focus();
        announce("Intel corpus focused.");
      } else if (key === "2") {
        e.preventDefault();
        messagesEl?.focus();
        announce("Chat messages focused.");
      } else if (key === "3") {
        e.preventDefault();
        chatInput?.focus();
        announce("Message box focused.");
      }
    });
  }

  /* ---- Session storage limit / capture / export ---- */
  let latestCaptureMarkdown = "";

  function formatMb(bytes) {
    return (bytes / (1024 * 1024)).toFixed(2);
  }

  function updateStorageMeter(storage) {
    if (!storage) return;
    const prog = $("#storage-progress");
    const label = $("#storage-label");
    if (prog) prog.value = storage.percent || 0;
    if (label) {
      label.textContent = `Storage ${formatMb(storage.used_bytes)} / ${storage.limit_mb} MB`;
    }
  }

  function showCaptureDialog(data) {
    const capture = data.capture || {};
    latestCaptureMarkdown = data.capture_markdown || "";
    const dialog = $("#storage-capture-dialog");
    $("#storage-capture-summary").textContent =
      capture.summary ||
      data.error ||
      "Session storage limit reached. Save the continuation prompt or export the session.";
    $("#storage-capture-prompt").value =
      capture.next_steps_prompt || latestCaptureMarkdown || "";
    if (data.storage) updateStorageMeter(data.storage);
    dialog.hidden = false;
    $("#btn-save-prompt")?.focus();
    announce("Session storage limit reached. Capture dialog opened.", { assertive: true });
  }

  function triggerDownload(blob, filename) {
    // Append + delayed revoke — bare a.click() fails in Firefox/Safari often.
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = filename || "download";
    a.rel = "noopener";
    a.style.display = "none";
    document.body.appendChild(a);
    a.click();
    setTimeout(() => {
      URL.revokeObjectURL(url);
      a.remove();
    }, 2500);
  }

  function downloadText(filename, text) {
    const blob = new Blob([text], { type: "application/octet-stream" });
    triggerDownload(blob, filename);
  }

  function filenameFromDisposition(header, fallback) {
    if (!header) return fallback;
    const utf = /filename\*=UTF-8''([^;]+)/i.exec(header);
    if (utf) {
      try {
        return decodeURIComponent(utf[1].trim().replace(/"/g, ""));
      } catch (_) {
        /* fall through */
      }
    }
    const plain = /filename="?([^";]+)"?/i.exec(header);
    return plain ? plain[1].trim() : fallback;
  }

  async function readSse(response, onEvent) {
    if (!response.ok) {
      const err = await response.json().catch(() => ({}));
      throw new Error(err.error || `Request failed (${response.status})`);
    }
    const reader = response.body.getReader();
    const decoder = new TextDecoder();
    let buffer = "";
    while (true) {
      const { value, done } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });
      const parts = buffer.split("\n\n");
      buffer = parts.pop() || "";
      for (const chunk of parts) {
        const line = chunk
          .split("\n")
          .map((l) => l.trim())
          .find((l) => l.startsWith("data:"));
        if (!line) continue;
        const raw = line.replace(/^data:\s?/, "");
        if (!raw || raw === "[DONE]") continue;
        let evt;
        try {
          evt = JSON.parse(raw);
        } catch (_) {
          continue;
        }
        await onEvent(evt);
      }
    }
  }

  function beginStreamingAssistant() {
    const div = document.createElement("div");
    div.className = "msg assistant streaming";
    div.setAttribute("tabindex", "-1");
    const label = document.createElement("span");
    label.className = "role";
    label.textContent = "Illudium-Q36";
    div.appendChild(label);
    const body = document.createElement("div");
    body.className = "msg-body";
    body.textContent = "";
    div.appendChild(body);
    messagesEl.appendChild(div);
    messagesEl.scrollTop = messagesEl.scrollHeight;
    let text = "";
    return {
      el: div,
      body,
      append(piece) {
        text += piece || "";
        body.textContent = text;
        messagesEl.scrollTop = messagesEl.scrollHeight;
      },
      finalize(finalText) {
        text = finalText != null ? finalText : text;
        lastAssistantText = text;
        body.innerHTML = formatMessageHtml(text);
        renderMermaidIn(body);
        div.classList.remove("streaming");
        messagesEl.scrollTop = messagesEl.scrollHeight;
        return text;
      },
    };
  }

  async function ensureStorageLimit() {
    const res = await fetch("/api/session/status");
    const data = await res.json();
    if (data.storage) updateStorageMeter(data.storage);
    if (data.limit_configured) return;
    const dialog = $("#storage-limit-dialog");
    const input = $("#storage-limit-input");
    input.value = data.default_limit_mb || 5;
    dialog.hidden = false;
    input.focus();
    announce("Please set a session storage limit in megabytes.");
    await new Promise((resolve) => {
      const save = async () => {
        const mb = Number(input.value || data.default_limit_mb || 5);
        const cfg = await fetch("/api/session/config", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ limit_mb: mb }),
        });
        const body = await cfg.json();
        if (body.storage) updateStorageMeter(body.storage);
        dialog.hidden = true;
        announce(`Storage limit set to ${body.limit_mb} megabytes.`);
        $("#btn-storage-limit-save")?.removeEventListener("click", save);
        resolve();
      };
      $("#btn-storage-limit-save")?.addEventListener("click", save);
    });
  }

  async function promptApiBudget(existing) {
    const dialog = $("#api-budget-dialog");
    const input = $("#api-budget-input");
    if (!dialog || !input) return;
    const prior =
      existing && existing.budget_usd != null ? Number(existing.budget_usd) : 25;
    input.value = String(prior);
    dialog.hidden = false;
    input.focus();
    announce("Please enter the USD amount allocated to this API key.");
    await new Promise((resolve) => {
      const save = async () => {
        const usd = Number(input.value);
        const res = await fetch("/api/session/budget", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ budget_usd: usd }),
        });
        const body = await res.json();
        if (!res.ok) {
          setStatus(chatStatus, body.error || "Could not save budget.", true);
          return;
        }
        updateCostMeter(null, body.budget);
        dialog.hidden = true;
        announce(
          `API key budget set to ${formatUsd(body.budget.budget_usd, 2)}.`
        );
        $("#btn-api-budget-save")?.removeEventListener("click", save);
        resolve();
      };
      $("#btn-api-budget-save")?.addEventListener("click", save);
    });
  }

  async function ensureApiBudget() {
    const res = await fetch("/api/session/status");
    const data = await res.json();
    if (data.usage || data.budget) updateCostMeter(data.usage, data.budget);
    if (data.budget && data.budget.budget_configured) return;
    await promptApiBudget(data.budget);
  }

  async function restoreHistory() {
    const res = await fetch("/api/chat/history");
    const data = await res.json();
    if (data.storage) updateStorageMeter(data.storage);
    const history = data.history || [];
    if (!history.length) return;
    messagesEl.innerHTML = "";
    appendMessage("system", "Restored prior chat for this sticky session.");
    history.forEach((turn) => {
      if (turn.role === "user" || turn.role === "assistant") {
        appendMessage(turn.role, turn.content || "");
      }
    });
  }

  function initStorageUi() {
    $("#btn-capture-close")?.addEventListener("click", () => {
      $("#storage-capture-dialog").hidden = true;
    });
    $("#btn-save-prompt")?.addEventListener("click", () => {
      const text =
        $("#storage-capture-prompt").value ||
        latestCaptureMarkdown ||
        "No capture available.";
      const stamp = new Date().toISOString().replace(/[:.]/g, "-");
      downloadText(`illudium_continuation_${stamp}.md`, text);
      announce("Continuation prompt saved.");
    });
    $("#btn-export-session")?.addEventListener("click", async () => {
      try {
        const params = new URLSearchParams();
        selectedList().forEach((f) => params.append("file", f));
        const res = await fetch(`/api/session/export.zip?${params.toString()}`);
        if (!res.ok) {
          const err = await res.json().catch(() => ({}));
          throw new Error(err.error || "Export failed");
        }
        const blob = await res.blob();
        const name = filenameFromDisposition(
          res.headers.get("Content-Disposition"),
          `illudium_session_${Date.now()}.zip`
        );
        triggerDownload(blob, name);
        announce("Session export download started.");
      } catch (err) {
        setStatus(chatStatus, err.message || String(err), true);
      }
    });
  }

  async function initModels() {
    const sel = $("#model-select");
    if (!sel) return;
    const res = await fetch("/api/models");
    const data = await res.json();
    sel.innerHTML = "";
    (data.models || []).forEach((m) => {
      const opt = document.createElement("option");
      opt.value = m.id;
      opt.textContent = `${m.label} (in $${m.input_per_mtok_usd}/MTok)`;
      sel.appendChild(opt);
    });
    selectedModel = data.default || (data.models && data.models[0] && data.models[0].id);
    if (selectedModel) sel.value = selectedModel;
    sel.addEventListener("change", async () => {
      selectedModel = sel.value;
      await fetch("/api/session/model", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ model: selectedModel }),
      });
      announce(`Model set to ${selectedModel}`);
    });
  }

  function initReportUi() {
    const dialog = $("#report-format-dialog");
    $("#btn-report")?.addEventListener("click", () => {
      dialog.hidden = false;
      $("#report-format")?.focus();
    });
    $("#btn-report-cancel")?.addEventListener("click", () => {
      dialog.hidden = true;
    });
    $("#btn-report-download")?.addEventListener("click", async () => {
      const fmt = $("#report-format")?.value || "md";
      const regenerate = $("#report-regenerate")?.checked !== false;
      setStatus(chatStatus, `Building ${fmt.toUpperCase()} report…`);
      try {
        const res = await fetch("/api/report", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            format: fmt,
            files: selectedList(),
            model: selectedModel,
            regenerate,
          }),
        });
        const ctype = (res.headers.get("Content-Type") || "").toLowerCase();
        if (!res.ok || ctype.includes("application/json")) {
          const err = await res.json().catch(() => ({}));
          throw new Error(err.error || "Report failed");
        }
        const blob = await res.blob();
        if (!blob || blob.size < 8) {
          throw new Error("Download was empty — try again.");
        }
        const name = filenameFromDisposition(
          res.headers.get("Content-Disposition"),
          `illudium_report.${fmt === "doc" ? "docx" : fmt}`
        );
        triggerDownload(blob, name);
        dialog.hidden = true;
        setStatus(chatStatus, `Downloaded ${name}`);
        announce(`Report downloaded as ${name}`);
        const st = await fetch("/api/session/status").then((r) => r.json());
        if (st.usage || st.budget) updateCostMeter(st.usage, st.budget);
      } catch (err) {
        setStatus(chatStatus, err.message || String(err), true);
      }
    });
  }

  function initBudgetUi() {
    $("#btn-edit-budget")?.addEventListener("click", async () => {
      const st = await fetch("/api/session/status").then((r) => r.json());
      await promptApiBudget(st.budget);
    });
  }

  /* ---- Boot ---- */
  appendMessage(
    "system",
    "Load intel, pick a model, then chat or generate a briefing/report. Chat scrolls inside its panel. Mermaid diagrams render for network, app calls, and privilege escalation when the intel supports them."
  );
  applySpeakRepliesDefault();
  initA11yMode();
  initBlindResearcherSupport();
  initStorageUi();
  initBudgetUi();
  initReportUi();
  initVoice();
  initModels()
    .then(() => ensureStorageLimit())
    .then(() => ensureApiBudget())
    .then(() => restoreHistory())
    .then(() => refreshFiles())
    .then(async () => {
      const st = await fetch("/api/session/status").then((r) => r.json());
      if (st.usage || st.budget) updateCostMeter(st.usage, st.budget);
      if (st.model && $("#model-select")) {
        selectedModel = st.model;
        $("#model-select").value = st.model;
      }
    })
    .catch((err) => setStatus(intelStatus, String(err), true));
})();

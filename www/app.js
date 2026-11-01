"use strict";

(() => {
  const phases = [
    ["prepare", "Siapkan data sintetis", "Menyiapkan fixture pesanan dan memeriksa identitas lab."],
    ["backup", "Buat backup fisik", "Menyalin cluster PostgreSQL beserta manifest backup."],
    ["verify_backup", "Periksa manifest backup", "Memeriksa integritas backup dengan pg_verifybackup."],
    ["changes", "Tambahkan pesanan setelah backup", "Mencatat perubahan yang harus dipulihkan melalui WAL."],
    ["restore_point", "Tandai titik sebelum insiden", "Membuat named restore point dan snapshot pembanding."],
    ["incident", "Simulasikan penghapusan pesanan", "Menghapus pesanan sintetis dan mencatat bukti insiden."],
    ["archive", "Arsipkan WAL", "Memastikan segmen WAL yang diperlukan tersedia."],
    ["recover", "Pulihkan ke cluster terpisah", "Memutar ulang WAL hingga restore point sebelum insiden."],
    ["verify", "Bandingkan hasil pemulihan", "Memeriksa isi data, fingerprint, dan pengecualian insiden."],
  ];
  const statuses = {
    queued: "Dalam antrean",
    running: "Berjalan",
    verified: "Terverifikasi",
    failed: "Gagal",
    interrupted: "Terputus",
  };
  const snapshotNames = {
    base: "Saat backup",
    before_incident: "Sebelum insiden",
    after_incident: "Setelah insiden",
    restored: "Setelah pemulihan",
  };
  const activeStatuses = new Set(["queued", "running"]);
  const terminalStatuses = new Set(["verified", "failed", "interrupted"]);
  const runPattern = /^[0-9a-f]{32}$/;
  const dom = Object.fromEntries([...document.querySelectorAll("[id]")].map((element) => [element.id, element]));
  let state = null;
  let selectedId = null;
  let selectedRun = null;
  let connected = false;
  let submitting = false;
  let refreshing = null;
  let timer = null;
  let detailGeneration = 0;
  let lastAnnouncement = "";
  let renderedReportKey = "";
  let renderedEventRun = null;
  let pendingRequest = readPendingRequest();
  const historyItems = new Map();
  const eventItems = new Map();
  const phaseItems = new Map();

  class ApiError extends Error {
    constructor(message, code, status) {
      super(message);
      this.code = code;
      this.status = status;
    }
  }

  function element(tag, className, text) {
    const node = document.createElement(tag);
    if (className) node.className = className;
    if (text !== undefined) node.textContent = text;
    return node;
  }

  function setText(node, value) {
    const next = value === null || value === undefined ? "Belum tersedia" : String(value);
    if (node.textContent !== next) node.textContent = next;
  }

  function readPendingRequest() {
    try {
      const saved = sessionStorage.getItem("restorelab.pending_request");
      return runPattern.test(saved || "") ? saved : null;
    } catch {
      return null;
    }
  }

  function savePendingRequest(value) {
    pendingRequest = value;
    try {
      if (value) sessionStorage.setItem("restorelab.pending_request", value);
      else sessionStorage.removeItem("restorelab.pending_request");
    } catch {
      // Request identity still persists in memory when browser storage is unavailable.
    }
  }

  function requestId() {
    const bytes = new Uint8Array(16);
    crypto.getRandomValues(bytes);
    return [...bytes].map((value) => value.toString(16).padStart(2, "0")).join("");
  }

  async function api(path, options = {}) {
    const abort = new AbortController();
    const timeout = setTimeout(() => abort.abort(), 15000);
    try {
      const response = await fetch(path, { cache: "no-store", credentials: "same-origin", ...options, signal: abort.signal });
      let payload;
      try {
        payload = await response.json();
      } catch {
        throw new ApiError("Server belum mengirim respons JSON yang dapat dibaca. Perbarui status untuk mencoba lagi.", "invalid_response", response.status);
      }
      if (!response.ok) {
        throw new ApiError(payload?.error?.message || "Permintaan belum dapat diproses. Perbarui status lab sebelum mencoba lagi.", payload?.error?.code || "request_failed", response.status);
      }
      return payload;
    } catch (error) {
      if (error instanceof ApiError) throw error;
      throw new ApiError("Server lokal tidak dapat dihubungi. Data terakhir tetap ditampilkan; periksa server, lalu coba hubungkan lagi.", "network", 0);
    } finally {
      clearTimeout(timeout);
    }
  }

  function localDate(value, short = false) {
    if (!value) return "Waktu belum tersedia";
    const date = new Date(value);
    if (!Number.isFinite(date.getTime())) return "Waktu belum tersedia";
    const options = short
      ? { day: "numeric", month: "short", hour: "2-digit", minute: "2-digit" }
      : { day: "numeric", month: "short", year: "numeric", hour: "2-digit", minute: "2-digit", second: "2-digit" };
    return new Intl.DateTimeFormat("id-ID", options).format(date);
  }

  function number(value) {
    return typeof value === "number" && Number.isFinite(value) ? new Intl.NumberFormat("id-ID").format(value) : "Belum tersedia";
  }

  function duration(value) {
    return typeof value === "number" && Number.isFinite(value)
      ? `${new Intl.NumberFormat("id-ID", { minimumFractionDigits: 2, maximumFractionDigits: 2 }).format(value)} detik`
      : "Belum tersedia";
  }

  function truth(value) {
    return value === true ? "Ya" : value === false ? "Tidak" : "Belum tersedia";
  }

  function announce(message) {
    if (message !== lastAnnouncement) {
      setText(dom.announcer, message);
      lastAnnouncement = message;
    }
  }

  function showNetworkError(error) {
    connected = false;
    dom["network-error"].hidden = false;
    setText(dom["network-error-text"], error.message);
    setText(dom["connection-status"], state ? "Data terakhir tersimpan di layar" : "Server lokal belum terhubung");
    announce("Koneksi ke server lokal terputus. Gunakan tombol Coba hubungkan lagi.");
    if (!state) {
      dom["run-loading"].hidden = true;
      dom["run-empty"].hidden = false;
      setText(dom["run-empty"].querySelector("h3"), "Status latihan belum dapat dimuat");
      setText(dom["run-empty"].querySelector("p"), "Hubungkan kembali ke server lokal untuk melihat jurnal atau menjalankan latihan.");
      setText(dom["run-status"], "Belum terhubung");
      setText(dom["history-empty"], "Riwayat akan muncul setelah server terhubung.");
      setText(dom["quota"], "Batas latihan belum dimuat");
    }
    updateControls();
  }

  function updateControls() {
    const lab = state?.lab;
    const active = Boolean(lab?.active_run_id);
    const quotaReached = Boolean(lab && Number.isFinite(lab.used_runs) && Number.isFinite(lab.max_runs) && lab.used_runs >= lab.max_runs);
    const scopeMatches = lab?.scope === "synthetic_only";
    dom["acknowledge"].disabled = submitting || active || quotaReached || !connected || !scopeMatches;
    dom["start-button"].disabled = submitting || active || quotaReached || !connected || !scopeMatches || !dom.acknowledge.checked;
    dom["start-button"].textContent = submitting
      ? "Mengirim permintaan…"
      : pendingRequest ? "Kirim ulang permintaan yang sama" : "Jalankan latihan pemulihan";
    if (submitting) setText(dom["start-help"], "Server sedang menerima permintaan. Identitas permintaan tetap sama jika koneksi terputus.");
    else if (!connected) setText(dom["start-help"], "Hubungkan ke server lokal sebelum menjalankan latihan.");
    else if (!scopeMatches) setText(dom["start-help"], "Identitas data sintetis belum cocok. Latihan tidak dapat dimulai.");
    else if (active) setText(dom["start-help"], "Satu latihan sedang aktif. Tunggu hasilnya sebelum memulai latihan berikutnya.");
    else if (quotaReached) setText(dom["start-help"], "Batas latihan pada instance ini tercapai. Riwayat dan laporan tetap dapat dibuka. Periksa panduan operasi untuk pengelolaan penyimpanan.");
    else if (pendingRequest) setText(dom["start-help"], "Hasil pengiriman sebelumnya belum diketahui. Kirim ulang dengan identitas yang sama agar server tidak membuat latihan ganda.");
    else setText(dom["start-help"], "Centang konfirmasi untuk memulai. Satu latihan berjalan pada satu waktu.");
    dom["active-button"].hidden = !lab?.active_run_id || selectedId === lab.active_run_id;
    dom["refresh-button"].setAttribute("aria-busy", String(Boolean(refreshing)));
    dom["retry-button"].setAttribute("aria-busy", String(Boolean(refreshing)));
  }

  function buildTimeline() {
    phases.forEach(([key, title, explanation], index) => {
      const row = element("li", "phase");
      const phaseNumber = element("span", "phase-number", String(index + 1).padStart(2, "0"));
      phaseNumber.setAttribute("aria-hidden", "true");
      const content = element("div");
      const titleRow = element("div", "phase-title-row");
      const heading = element("h3", "", title);
      const status = element("span", "phase-state", "Menunggu");
      const message = element("p", "phase-message", explanation);
      const time = element("time", "phase-time");
      time.hidden = true;
      titleRow.append(heading, status);
      content.append(titleRow, message, time);
      row.append(phaseNumber, content);
      dom.timeline.append(row);
      phaseItems.set(key, { row, status, message, time, explanation });
    });
  }

  function renderHistory(runs) {
    const present = new Set();
    for (const run of runs) {
      if (!runPattern.test(run.id)) continue;
      present.add(run.id);
      let item = historyItems.get(run.id);
      if (!item) {
        const li = element("li");
        const button = element("button", "history-button");
        button.type = "button";
        const time = element("time");
        const status = element("span");
        const id = element("span", "history-id mono", run.id);
        button.append(time, status, id);
        button.addEventListener("click", () => selectRun(run.id, true));
        li.append(button);
        item = { li, button, time, status };
        historyItems.set(run.id, item);
      }
      setText(item.time, localDate(run.created_at, true));
      if (run.created_at) item.time.dateTime = run.created_at;
      setText(item.status, statuses[run.status] || "Status belum dikenal");
      item.status.className = `status status-${statuses[run.status] ? run.status : "neutral"}`;
      item.button.setAttribute("aria-pressed", String(selectedId === run.id));
      item.button.setAttribute("aria-label", `Latihan ${run.id}, ${localDate(run.created_at)}, ${statuses[run.status] || "status belum dikenal"}`);
    }
    for (const [id, item] of historyItems) {
      if (!present.has(id)) {
        item.li.remove();
        historyItems.delete(id);
      }
    }
    // Only insert moved or new rows, so polling does not replace a focused button.
    let expected = dom["history-list"].firstElementChild;
    for (const run of runs) {
      const item = historyItems.get(run.id);
      if (!item) continue;
      if (item.li !== expected) dom["history-list"].insertBefore(item.li, expected);
      expected = item.li.nextElementSibling;
    }
    dom["history-empty"].hidden = runs.length > 0;
    setText(dom["history-empty"], "Belum ada latihan tersimpan. Jalankan skenario pertama untuk membuat riwayat.");
    setText(dom["history-count"], runs.length ? String(runs.length) : "");
  }

  function renderEvents(run) {
    if (renderedEventRun !== run.id) {
      dom["event-log"].replaceChildren();
      eventItems.clear();
      renderedEventRun = run.id;
    }
    const events = Array.isArray(run.events) ? run.events : [];
    for (const event of events) {
      const key = String(event.sequence);
      if (eventItems.has(key)) continue;
      const item = element("li");
      const time = element("time", "", localDate(event.at));
      if (event.at) time.dateTime = event.at;
      item.append(time, element("p", "", event.message || "Kejadian tercatat."));
      if (event.details && Object.keys(event.details).length) {
        const disclosure = element("details", "event-extra");
        disclosure.append(element("summary", "", "Detail kejadian"), element("pre", "mono", JSON.stringify(event.details, null, 2)));
        item.append(disclosure);
      }
      dom["event-log"].append(item);
      eventItems.set(key, item);
    }
    setText(dom["event-count"], events.length ? `(${events.length})` : "");
    dom["event-details"].hidden = events.length === 0;
  }

  function renderTimeline(run) {
    const events = Array.isArray(run.events) ? run.events : [];
    const currentIndex = phases.findIndex(([key]) => key === run.step);
    for (let index = 0; index < phases.length; index += 1) {
      const [key] = phases[index];
      const item = phaseItems.get(key);
      const phaseEvents = events.filter((event) => event.step === key);
      const latest = phaseEvents.at(-1);
      const passed = run.status === "verified" || (currentIndex > index && phaseEvents.length > 0);
      const current = currentIndex === index;
      const failed = current && (run.status === "failed" || run.status === "interrupted");
      item.row.className = `phase${passed ? " phase-done" : failed ? " phase-failed" : current ? " phase-current" : ""}`;
      if (current && activeStatuses.has(run.status)) item.row.setAttribute("aria-current", "step");
      else item.row.removeAttribute("aria-current");
      setText(item.status, passed ? "Selesai" : failed ? statuses[run.status] : current && run.status === "running" ? "Sedang dikerjakan" : phaseEvents.length ? "Tercatat" : "Menunggu");
      setText(item.message, latest?.message || item.explanation);
      item.time.hidden = !latest?.at;
      if (latest?.at) {
        item.time.dateTime = latest.at;
        setText(item.time, localDate(latest.at));
      }
    }
  }

  function renderOutcome(run) {
    const isTerminal = terminalStatuses.has(run.status);
    dom.outcome.hidden = !isTerminal;
    if (!isTerminal) return;
    dom.outcome.className = `outcome outcome-${run.status}`;
    let heading;
    let message;
    if (run.status === "verified" && run.report?.verified === true) {
      heading = "Data pada restore point berhasil dipulihkan";
      message = "Periksa perbandingan snapshot dan bukti teknis di bawah. Laporan JSON tersedia untuk latihan ini.";
    } else if (run.status === "verified") {
      heading = "Status terverifikasi, laporan belum dimuat";
      message = "Perbarui status untuk mengambil laporan lengkap sebelum memeriksa hasil pemulihan.";
    } else if (run.status === "interrupted") {
      heading = "Latihan terputus sebelum selesai";
      message = "Controller berhenti ketika latihan masih aktif. Latihan ini tidak dijalankan ulang otomatis dan tidak dinyatakan berhasil. Periksa jurnal serta panduan operasi sebelum membuat latihan baru.";
    } else {
      heading = "Latihan belum berhasil diverifikasi";
      message = typeof run.error === "string" ? run.error : run.error?.message || run.message || "Periksa fase terakhir pada jurnal untuk mengetahui pekerjaan yang gagal.";
    }
    dom.outcome.replaceChildren(element("h3", "", heading), element("p", "", message));
  }

  function renderReport(run) {
    const report = run.report;
    const available = Boolean(report && typeof report === "object" && report.snapshots);
    dom["report-panel"].hidden = !available;
    dom["download-report"].hidden = !(available && run.status === "verified" && report.verified === true);
    if (!dom["download-report"].hidden) dom["download-report"].href = `/api/runs/${run.id}/report`;
    else dom["download-report"].removeAttribute("href");
    const key = available ? `${run.id}:${JSON.stringify(report)}` : "";
    if (!available || key === renderedReportKey) {
      renderedReportKey = key;
      return;
    }
    renderedReportKey = key;
    const snapshots = report.snapshots;
    const snapshotNodes = [];
    for (const [name, label] of Object.entries(snapshotNames)) {
      const snapshot = snapshots[name];
      const item = element("div", `snapshot-item${name === "after_incident" ? " snapshot-damaged" : name === "restored" ? " snapshot-restored" : ""}`);
      const count = element("dd");
      count.append(element("span", "snapshot-number", number(snapshot?.row_count)), element("span", "snapshot-unit", "pesanan"));
      item.append(element("dt", "", label), count);
      snapshotNodes.push(item);
    }
    dom["snapshot-comparison"].replaceChildren(...snapshotNodes);

    const findings = [];
    const nativeFindings = report.findings || {};
    const verifiedBackup = report.backup?.manifest_verified;
    findings.push(element("li", "", `Manifest backup valid: ${truth(verifiedBackup)}.`));
    if (Array.isArray(nativeFindings.recovered_ids)) {
      const ids = nativeFindings.recovered_ids.filter((id) => typeof id === "number" && Number.isFinite(id));
      findings.push(element("li", "", `Pesanan terhapus yang kembali: ${ids.length ? ids.join(", ") : "tidak ada"}.`));
    }
    findings.push(element("li", "", `Pesanan setelah backup ikut pulih: ${truth(nativeFindings.post_backup_rows_verified)}.`));
    findings.push(element("li", "", `Penanda insiden tidak ikut dipulihkan: ${truth(nativeFindings.incident_excluded)}.`));
    const fingerprintMatches = snapshots.before_incident?.fingerprint && snapshots.restored?.fingerprint
      ? snapshots.before_incident.fingerprint === snapshots.restored.fingerprint : null;
    findings.push(element("li", "", `Fingerprint hasil sama dengan sebelum insiden: ${truth(fingerprintMatches)}.`));
    dom.findings.replaceChildren(...findings);

    dom.measurements.replaceChildren();
    for (const [label, value] of [["Waktu pemulihan terukur", report.measurements?.recovery_seconds], ["Durasi seluruh latihan", report.measurements?.drill_seconds]]) {
      const item = element("div");
      item.append(element("dt", "", label), element("dd", "", duration(value)));
      dom.measurements.append(item);
    }

    const recovery = report.recovery || {};
    const evidence = [
      ["ID latihan", run.id],
      ["Named restore point", recovery.target_name],
      ["LSN restore point", recovery.target_lsn],
      ["Segmen WAL", recovery.wal_segment],
      ["LSN replay", recovery.replay_lsn],
      ["Cluster dalam recovery", truth(recovery.in_recovery)],
      ["Replay sedang pause", truth(recovery.paused)],
      ["Jumlah penanda insiden", number(recovery.incident_marker_count)],
    ];
    dom["technical-evidence"].replaceChildren();
    for (const [label, value] of evidence) {
      const item = element("div");
      item.append(element("dt", "", label), element("dd", "mono", value ?? "Belum tersedia"));
      dom["technical-evidence"].append(item);
    }
    const digests = element("dl");
    for (const [name, label] of Object.entries(snapshotNames)) {
      const item = element("div");
      item.append(element("dt", "", label), element("dd", "mono", snapshots[name]?.fingerprint || "Belum tersedia"));
      digests.append(item);
    }
    dom.fingerprints.replaceChildren(element("h3", "", "Fingerprint snapshot"), digests);
  }

  function renderRun(run) {
    selectedRun = run;
    dom["run-loading"].hidden = true;
    dom["run-empty"].hidden = Boolean(run);
    dom["run-body"].hidden = !run;
    if (!run) {
      setText(dom["run-meta"], "Jurnal akan terisi saat latihan pertama dimulai.");
      setText(dom["run-status"], "Belum ada latihan");
      dom["run-status"].className = "status status-neutral";
      setText(dom["run-empty"].querySelector("h3"), "Belum ada latihan");
      setText(dom["run-empty"].querySelector("p"), "Konfirmasi skenario di atas, lalu jalankan latihan pertama. Setiap fase akan muncul di sini saat PostgreSQL mengerjakannya.");
      dom["report-panel"].hidden = true;
      return;
    }
    setText(dom["run-meta"], `${run.id} · ${localDate(run.created_at)}`);
    setText(dom["run-status"], statuses[run.status] || "Status belum dikenal");
    dom["run-status"].className = `status status-${statuses[run.status] ? run.status : "neutral"}`;
    renderTimeline(run);
    renderEvents(run);
    renderOutcome(run);
    renderReport(run);
    const phase = phases.find(([key]) => key === run.step)?.[1];
    announce(`${statuses[run.status] || "Status latihan diperbarui"}.${phase ? ` ${phase}.` : ""}`);
  }

  async function selectRun(id, userInitiated = false) {
    if (!runPattern.test(id)) return;
    selectedId = id;
    const generation = ++detailGeneration;
    for (const [historyId, item] of historyItems) item.button.setAttribute("aria-pressed", String(historyId === selectedId));
    updateControls();
    if (state?.active_run?.id === id) {
      renderRun(state.active_run);
      return;
    }
    if (selectedRun?.id !== id) {
      dom["run-loading"].hidden = false;
      setText(dom["run-loading"], "Mengambil jurnal latihan yang dipilih…");
      dom["run-empty"].hidden = true;
      dom["run-body"].hidden = true;
      dom["report-panel"].hidden = true;
    }
    try {
      const run = await api(`/api/runs/${id}`);
      if (generation !== detailGeneration || selectedId !== id) return;
      renderRun(run);
      if (userInitiated) announce(`Latihan ${localDate(run.created_at)} dipilih. ${statuses[run.status] || "Status belum dikenal"}.`);
    } catch (error) {
      if (generation !== detailGeneration) return;
      dom["run-loading"].hidden = true;
      if (selectedRun?.id === id) renderRun(selectedRun);
      showNetworkError(error);
    }
  }

  function scheduleRefresh() {
    clearTimeout(timer);
    const delay = state?.lab?.active_run_id ? 2000 : connected ? 15000 : 5000;
    timer = setTimeout(() => refresh(), delay);
  }

  async function refresh() {
    if (refreshing) return refreshing;
    refreshing = (async () => {
      try {
        const next = await api("/api/state");
        if (!next?.lab || !Array.isArray(next.runs)) throw new ApiError("Respons status lab tidak lengkap. Coba perbarui status.", "invalid_response", 0);
        state = next;
        connected = true;
        dom["network-error"].hidden = true;
        setText(dom["connection-status"], `Terhubung · Diperbarui ${new Intl.DateTimeFormat("id-ID", { hour: "2-digit", minute: "2-digit", second: "2-digit" }).format(new Date())}`);
        setText(dom.quota, `${number(state.lab.used_runs)} dari ${number(state.lab.max_runs)} latihan digunakan`);
        if (pendingRequest) {
          const accepted = state.runs.find((run) => run.request_id === pendingRequest);
          if (accepted) {
            selectedId = accepted.id;
            savePendingRequest(null);
            dom.acknowledge.checked = false;
            dom["start-error"].hidden = true;
          }
        }
        if (!selectedId) selectedId = state.lab.active_run_id || state.runs[0]?.id || null;
        renderHistory(state.runs);
        if (selectedId) await selectRun(selectedId);
        else renderRun(null);
      } catch (error) {
        showNetworkError(error);
      } finally {
        refreshing = null;
        updateControls();
        scheduleRefresh();
      }
    })();
    updateControls();
    return refreshing;
  }

  dom["run-form"].addEventListener("submit", async (event) => {
    event.preventDefault();
    if (dom["start-button"].disabled || !dom.acknowledge.checked) return;
    submitting = true;
    dom["start-error"].hidden = true;
    const id = pendingRequest || requestId();
    savePendingRequest(id);
    updateControls();
    try {
      const run = await api("/api/runs", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ request_id: id, scenario: "delete_orders", acknowledge: "SYNTHETIC_ONLY" }),
      });
      if (!runPattern.test(run?.id || "")) throw new ApiError("Server belum mengonfirmasi identitas latihan. Perbarui status sebelum mengirim ulang permintaan yang sama.", "invalid_response", 0);
      savePendingRequest(null);
      dom.acknowledge.checked = false;
      selectedId = run.id;
      renderRun(run);
      announce("Permintaan latihan diterima. Jurnal akan diperbarui otomatis.");
      await refresh();
    } catch (error) {
      dom["start-error"].hidden = false;
      setText(dom["start-error"], error.message);
      if (error.status === 409) {
        savePendingRequest(null);
        await refresh();
      } else if (error.status >= 400 && error.status < 500) {
        savePendingRequest(null);
      } else if (!error.status || error.code === "invalid_response") {
        showNetworkError(error);
      }
    } finally {
      submitting = false;
      updateControls();
      scheduleRefresh();
    }
  });
  dom.acknowledge.addEventListener("change", updateControls);
  dom["refresh-button"].addEventListener("click", () => refresh());
  dom["retry-button"].addEventListener("click", () => refresh());
  dom["active-button"].addEventListener("click", () => {
    if (state?.lab?.active_run_id) selectRun(state.lab.active_run_id, true);
  });
  document.addEventListener("visibilitychange", () => {
    if (!document.hidden) refresh();
  });
  window.addEventListener("online", () => refresh());
  window.addEventListener("pagehide", () => clearTimeout(timer));
  buildTimeline();
  refresh();
})();

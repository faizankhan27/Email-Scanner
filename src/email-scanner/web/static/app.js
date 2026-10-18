(() => {
  "use strict";

  const $ = (id) => document.getElementById(id);
  const els = {
    form: $("scan-form"),
    url: $("url"),
    pages: $("max-pages"),
    speed: $("speed"),
    start: $("start"),
    error: $("form-error"),
    band: $("band"),
    fill: $("band-fill"),
    run: $("run"),
    status: $("status"),
    current: $("current"),
    stop: $("stop"),
    results: $("results"),
    title: $("results-title"),
    filter: $("filter"),
    own: $("own-domain"),
    copy: $("copy"),
    download: $("download"),
    rows: $("rows"),
    empty: $("empty"),
  };

  const HOW = {
    mailto: "mailto link",
    text: "plain text",
    obfuscated: "obfuscated",
    protected: "protected",
  };

  let jobId = null;
  let pollTimer = null;
  let failures = 0;
  let data = null;
  let renderedKey = "";

  async function api(path, options) {
    const response = await fetch(path, options);
    let body = null;
    try {
      body = await response.json();
    } catch (err) {
      /* not JSON */
    }
    if (!response.ok) {
      const error = new Error((body && body.error) || "Something went wrong. Try again.");
      error.status = response.status;
      throw error;
    }
    return body;
  }

  function plural(n, one, many) {
    return n + " " + (n === 1 ? one : many);
  }

  function showError(message) {
    els.error.textContent = message;
    els.error.hidden = false;
  }

  function clearError() {
    els.error.hidden = true;
    els.error.textContent = "";
  }

  function setBusy(busy) {
    els.start.disabled = busy;
    els.start.textContent = busy ? "Scanning" : "Scan website";
    els.band.classList.toggle("is-running", busy);
  }

  function duration(seconds) {
    const s = Math.round(seconds);
    if (s < 60) return plural(s, "second", "seconds");
    return Math.floor(s / 60) + " min " + (s % 60) + " sec";
  }

  function displayUrl(url) {
    try {
      const u = new URL(url);
      let path = u.pathname + u.search;
      if (path === "/") path = "";
      return u.host.replace(/^www\./, "") + path;
    } catch (err) {
      return url;
    }
  }

  function pageLink(url) {
    const a = document.createElement("a");
    if (/^https?:\/\//i.test(url)) a.href = url;
    a.target = "_blank";
    a.rel = "noopener noreferrer";
    a.title = url;
    a.textContent = displayUrl(url);
    return a;
  }

  function cell(className) {
    const td = document.createElement("td");
    td.className = className;
    return td;
  }

  function buildRow(r) {
    const tr = document.createElement("tr");

    const emailCell = cell("cell-email");
    const email = document.createElement("span");
    email.className = "email";
    email.textContent = r.email;
    emailCell.append(email);
    if (!r.site_domain) {
      const tag = document.createElement("span");
      tag.className = "tag tag-muted";
      tag.textContent = "other domain";
      emailCell.append(tag);
    }

    const pageCell = cell("cell-page");
    pageCell.append(pageLink(r.best_page));
    if (r.all_pages.length > 1) {
      const details = document.createElement("details");
      const summary = document.createElement("summary");
      summary.textContent = "Also on " + plural(r.all_pages.length - 1, "other page", "other pages");
      const list = document.createElement("ul");
      r.all_pages
        .filter((p) => p !== r.best_page)
        .forEach((p) => {
          const li = document.createElement("li");
          li.append(pageLink(p));
          list.append(li);
        });
      details.append(summary, list);
      pageCell.append(details);
    }

    const howCell = cell("cell-how");
    r.found_as.forEach((kind) => {
      const tag = document.createElement("span");
      tag.className = "tag";
      tag.textContent = HOW[kind] || kind;
      howCell.append(tag);
    });

    tr.append(emailCell, pageCell, howCell);
    return tr;
  }

  function visibleResults() {
    const query = els.filter.value.trim().toLowerCase();
    return ((data && data.results) || []).filter((r) => {
      if (els.own.checked && !r.site_domain) return false;
      if (!query) return true;
      return r.email.includes(query) || r.best_page.toLowerCase().includes(query);
    });
  }

  function renderRows() {
    if (!data) return;
    const list = visibleResults();
    const key = JSON.stringify([
      list.map((r) => [r.email, r.best_page, r.all_pages.length, r.found_as]),
      data.finished,
    ]);
    if (key === renderedKey) return;
    renderedKey = key;

    els.rows.replaceChildren(...list.map(buildRow));

    const total = (data.results || []).length;
    let message = "";
    if (list.length === 0) {
      if (total > 0) {
        message = "No emails match your filter. Clear the filter to see all " + plural(total, "email", "emails") + ".";
      } else if (!data.finished) {
        message = "Nothing yet. Emails appear here as pages are read.";
      } else if (data.status !== "error") {
        message =
          "No emails were found on this site. Sites that build their pages with JavaScript can hide emails from this scanner.";
      }
    }
    els.empty.textContent = message;
    els.empty.hidden = message === "";
    els.rows.closest(".table-wrap").hidden = list.length === 0;
  }

  function render() {
    if (!data) return;
    const busy = !data.finished;
    setBusy(busy);
    els.run.hidden = false;

    const total = Math.max(1, Math.min(data.max_pages, data.pages_discovered));
    let percent = Math.min(100, (data.pages_scanned / total) * 100);
    if (data.status === "done") percent = 100;
    if (data.status === "error") percent = 0;
    els.fill.style.width = percent + "%";
    els.band.setAttribute("aria-valuenow", String(Math.round(percent)));

    const pages = plural(data.pages_scanned, "page", "pages");
    const emails = plural(data.email_count, "email", "emails");
    els.status.classList.toggle("is-error", data.status === "error");

    if (data.status === "queued") {
      els.status.textContent = "Starting the scan";
    } else if (data.status === "running") {
      els.status.textContent = "Read " + data.pages_scanned + " of about " + total + " pages";
    } else if (data.status === "cancelled") {
      els.status.textContent = "Stopped after reading " + pages + ". The emails found so far are listed below.";
    } else if (data.status === "error") {
      els.status.textContent = data.error || "The scan failed. Try again.";
    } else if (data.stopped_reason === "time limit") {
      els.status.textContent = "Stopped at the time limit after reading " + pages + ". Found " + emails + ".";
    } else {
      els.status.textContent = "Finished. Read " + pages + " and found " + emails + " in " + duration(data.elapsed) + ".";
    }

    els.current.textContent = busy ? data.current_url : "";
    els.stop.hidden = !busy;
    if (busy && els.stop.dataset.stopping !== "1") {
      els.stop.disabled = false;
      els.stop.textContent = "Stop scan";
    }

    const showResults = data.status !== "error";
    els.results.hidden = !showResults;
    if (showResults) {
      const n = (data.results || []).length;
      els.title.textContent = plural(n, "email", "emails") + (busy ? " found so far" : " found");
      els.download.hidden = n === 0;
      els.download.href = "/api/scans/" + encodeURIComponent(jobId) + "/csv";
      els.copy.hidden = n === 0;
      renderRows();
    }
  }

  async function poll() {
    clearTimeout(pollTimer);
    try {
      data = await api("/api/scans/" + encodeURIComponent(jobId));
      failures = 0;
    } catch (err) {
      if (err.status === 404 || ++failures >= 4) {
        forget();
        showError(err.status === 404 ? err.message : "Lost the connection to the scanner. Start the scan again.");
        return;
      }
      pollTimer = setTimeout(poll, 2000);
      return;
    }
    render();
    if (!data.finished) pollTimer = setTimeout(poll, 1000);
  }

  function forget() {
    clearTimeout(pollTimer);
    jobId = null;
    data = null;
    renderedKey = "";
    setBusy(false);
    els.run.hidden = true;
    els.results.hidden = true;
    els.fill.style.width = "0%";
    try {
      history.replaceState(null, "", location.pathname);
    } catch (err) {
      /* ignore */
    }
  }

  function begin(id) {
    jobId = id;
    data = null;
    renderedKey = "";
    failures = 0;
    els.stop.dataset.stopping = "0";
    try {
      history.replaceState(null, "", "#" + id);
    } catch (err) {
      /* ignore */
    }
    poll();
  }

  els.form.addEventListener("submit", async (event) => {
    event.preventDefault();
    clearError();
    if (!els.url.value.trim()) {
      showError("Enter a website address.");
      els.url.focus();
      return;
    }
    els.start.disabled = true;
    try {
      const job = await api("/api/scans", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          url: els.url.value,
          max_pages: Number(els.pages.value) || 100,
          delay: Number(els.speed.value),
        }),
      });
      renderedKey = "";
      els.filter.value = "";
      els.own.checked = false;
      begin(job.id);
    } catch (err) {
      showError(err.message);
      els.start.disabled = false;
    }
  });

  els.stop.addEventListener("click", async () => {
    if (!jobId) return;
    els.stop.disabled = true;
    els.stop.textContent = "Stopping";
    els.stop.dataset.stopping = "1";
    try {
      await api("/api/scans/" + encodeURIComponent(jobId) + "/cancel", { method: "POST" });
    } catch (err) {
      showError(err.message);
    }
  });

  els.filter.addEventListener("input", renderRows);
  els.own.addEventListener("change", renderRows);

  els.copy.addEventListener("click", async () => {
    const text = visibleResults()
      .map((r) => r.email)
      .join("\n");
    if (!text) return;
    try {
      await navigator.clipboard.writeText(text);
    } catch (err) {
      const area = document.createElement("textarea");
      area.value = text;
      document.body.append(area);
      area.select();
      document.execCommand("copy");
      area.remove();
    }
    els.copy.textContent = "Copied";
    setTimeout(() => {
      els.copy.textContent = "Copy emails";
    }, 1500);
  });

  const fromHash = location.hash.slice(1);
  if (/^[A-Za-z0-9_-]{8,40}$/.test(fromHash)) begin(fromHash);
})();

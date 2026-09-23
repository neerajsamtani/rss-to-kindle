const page = document.body;
const basePath = page.dataset.basePath || "";
const apiPath = `${basePath}/api/jobs`;
const form = document.querySelector("#submit-form");
const urlInput = document.querySelector("#article-url");
const submitButton = document.querySelector("#submit-button");
const feedback = document.querySelector("#submit-feedback");
const currentJob = document.querySelector("#current-job");
const recentJobs = document.querySelector("#recent-jobs");
const renderedCards = new Map();
const polling = new Set();
const focusedJobId = currentJob.dataset.jobId || "";

for (const card of recentJobs.querySelectorAll(".job-card")) {
  renderedCards.set(card.dataset.jobId, card);
}

const activeStatuses = new Set(["queued", "fetching", "extracting", "sending"]);
const statusLabels = {
  queued: "Queued",
  fetching: "Fetching",
  extracting: "Preparing EPUB",
  sending: "Sending email",
  sent: "Sent",
  failed: "Failed",
  interrupted: "Interrupted",
};

function statusMessage(job) {
  if (job.error) return job.error;
  if (job.status === "sent") {
    return "Email accepted by SMTP. Kindle delivery can take a little while.";
  }
  return {
    queued: "Waiting for the article worker.",
    fetching: "Opening the article page.",
    extracting: "Preparing the article as an EPUB.",
    sending: "Sending the EPUB to your Kindle email.",
    failed: "The article could not be sent.",
    interrupted: "The job stopped before its result was confirmed.",
  }[job.status] || "Status unavailable.";
}

function articleName(job) {
  if (job.title) return job.title;
  try {
    return new URL(job.url).hostname;
  } catch {
    return "Your article";
  }
}

function makeStatus(job) {
  const status = document.createElement("span");
  status.className = `status-pill status-${job.status}`;
  status.textContent = statusLabels[job.status] || job.status;
  return status;
}

function makeStatusLink(job) {
  const link = document.createElement("a");
  link.href = job.status_url;
  link.textContent = "Status details ↗";
  return link;
}

function updateCard(job) {
  let card = renderedCards.get(job.id);
  if (!card) {
    card = document.createElement("li");
    card.className = "job-card";
    card.dataset.jobId = job.id;
    const main = document.createElement("div");
    main.className = "job-main";
    const title = document.createElement("h3");
    const note = document.createElement("p");
    note.className = "job-note";
    main.append(title, note);
    const side = document.createElement("div");
    side.className = "job-side";
    card.append(main, side);
    renderedCards.set(job.id, card);
  }

  const title = card.querySelector("h3");
  const note = card.querySelector(".job-note");
  const side = card.querySelector(".job-side");
  title.textContent = articleName(job);
  note.textContent = statusMessage(job);
  side.replaceChildren(makeStatus(job), makeStatusLink(job));
  if (recentJobs.firstElementChild?.classList.contains("empty-history")) {
    recentJobs.firstElementChild.remove();
  }
  recentJobs.prepend(card);
}

function updateCurrent(job) {
  currentJob.className = "current-job";
  currentJob.dataset.jobId = job.id;
  currentJob.replaceChildren();
  const row = document.createElement("div");
  row.className = "current-row";
  const title = document.createElement("h3");
  title.textContent = articleName(job);
  row.append(title, makeStatus(job));
  const note = document.createElement("p");
  note.className = "job-note current-note";
  note.textContent = statusMessage(job);
  const link = makeStatusLink(job);
  currentJob.append(row, note, link);
}

function showJob(job) {
  updateCurrent(job);
  updateCard(job);
  if (activeStatuses.has(job.status)) pollJob(job);
}

async function pollJob(job) {
  if (polling.has(job.id)) return;
  polling.add(job.id);
  window.setTimeout(async () => {
    let nextJob = job;
    let retry = false;
    try {
      const response = await fetch(job.api_url, {
        headers: { Accept: "application/json" },
        cache: "no-store",
      });
      if (response.ok) {
        const updated = await response.json();
        nextJob = updated;
        updateCard(updated);
        if (currentJob.dataset.jobId === updated.id) updateCurrent(updated);
        retry = activeStatuses.has(updated.status);
      } else {
        retry = response.status >= 500;
      }
    } catch {
      retry = true;
    }
    polling.delete(job.id);
    if (retry) window.setTimeout(() => pollJob(nextJob), 3500);
  }, 1800);
}

function idempotencyKey() {
  if (globalThis.crypto?.randomUUID) return crypto.randomUUID();
  return `${Date.now().toString(36)}-${Math.random().toString(36).slice(2)}-${Math.random()
    .toString(36)
    .slice(2)}`;
}

async function postJob(url, key, retryButton = null) {
  submitButton.disabled = true;
  feedback.replaceChildren();
  if (retryButton) retryButton.disabled = true;
  try {
    const response = await fetch(apiPath, {
      method: "POST",
      headers: {
        Accept: "application/json",
        "Content-Type": "application/json",
        "Idempotency-Key": key,
      },
      body: JSON.stringify({ url }),
      cache: "no-store",
    });
    const body = await response.json().catch(() => ({}));
    if (!response.ok) {
      const error = new Error(body.error || "The request could not be submitted.");
      error.retryable = response.status >= 500;
      throw error;
    }
    showJob(body);
    if (retryButton) retryButton.remove();
  } catch (error) {
    const message = document.createElement("span");
    message.textContent = error.message || "Could not reach the service. Check your connection.";
    feedback.append(message);
    if (error.retryable || error instanceof TypeError) {
      const retry = document.createElement("button");
      retry.type = "button";
      retry.className = "retry-button";
      retry.textContent = "Retry this request";
      retry.addEventListener("click", () => postJob(url, key, retry));
      feedback.append(" ", retry);
    } else if (retryButton) {
      retryButton.disabled = false;
    }
  } finally {
    submitButton.disabled = false;
  }
}

form.addEventListener("submit", (event) => {
  event.preventDefault();
  if (!form.reportValidity()) return;
  postJob(urlInput.value.trim(), idempotencyKey());
});

async function loadRecent() {
  try {
    const response = await fetch(`${apiPath}?limit=20`, {
      headers: { Accept: "application/json" },
      cache: "no-store",
    });
    if (!response.ok) return;
    const body = await response.json();
    for (const job of [...body.jobs].reverse()) updateCard(job);
    if (!focusedJobId && body.jobs.length > 0) updateCurrent(body.jobs[0]);
    for (const job of body.jobs) {
      if (activeStatuses.has(job.status)) pollJob(job);
    }
    if (focusedJobId) {
      const focused = body.jobs.find((job) => job.id === focusedJobId);
      if (focused) {
        updateCurrent(focused);
        if (activeStatuses.has(focused.status)) pollJob(focused);
      } else {
        const response = await fetch(`${apiPath}/${encodeURIComponent(focusedJobId)}`, {
          headers: { Accept: "application/json" },
          cache: "no-store",
        });
        if (response.ok) showJob(await response.json());
      }
    }
  } catch {
    // The form remains usable if the recent list cannot be refreshed.
  }
}

loadRecent();

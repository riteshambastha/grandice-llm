const state = {
  token: sessionStorage.getItem("grandice_admin_token") || "",
  organizations: [],
  members: [],
  keys: [],
  activeSection: "overview",
};

const $ = (selector, root = document) => root.querySelector(selector);
const $$ = (selector, root = document) => [...root.querySelectorAll(selector)];
const escapeHtml = (value) =>
  String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
const number = (value) => Number(value || 0).toLocaleString();
const date = (value) => value ? new Date(value).toLocaleString() : "Never";
const bytes = (value) => {
  const amount = Number(value || 0);
  if (!amount) return "—";
  if (amount >= 1024 * 1024) return `${(amount / 1024 / 1024).toFixed(1)} MB`;
  return `${Math.ceil(amount / 1024)} KB`;
};

function showAlert(message, type = "error") {
  const alert = $("[data-admin-alert]");
  alert.textContent = message;
  alert.className = `admin-alert show ${type}`;
  window.clearTimeout(showAlert.timer);
  showAlert.timer = window.setTimeout(() => {
    alert.className = "admin-alert";
  }, 5000);
}

async function api(path, options = {}) {
  const response = await fetch(path, {
    ...options,
    headers: {
      "Content-Type": "application/json",
      "X-Admin-Token": state.token,
      ...(options.headers || {}),
    },
  });
  const text = await response.text();
  let payload = {};
  try { payload = text ? JSON.parse(text) : {}; } catch { payload = {}; }
  if (response.status === 401) {
    sessionStorage.removeItem("grandice_admin_token");
    state.token = "";
    $("[data-admin-app]").hidden = true;
    $("[data-login]").hidden = false;
    throw new Error("Administrator session expired.");
  }
  if (!response.ok) {
    const detail = Array.isArray(payload.detail)
      ? payload.detail.map((item) => item.msg).join(" ")
      : payload.detail;
    throw new Error(detail || `Request failed (${response.status}).`);
  }
  return payload;
}

function setSection(name) {
  state.activeSection = name;
  $$("[data-admin-nav] button").forEach((button) => {
    button.classList.toggle("active", button.dataset.section === name);
  });
  $$("[data-panel]").forEach((panel) => {
    panel.classList.toggle("active", panel.dataset.panel === name);
  });
  const button = $(`[data-section="${name}"]`);
  $("[data-page-title]").textContent = button?.textContent.trim() || "Dashboard";
  if (name === "usage") loadUsage();
  if (name === "monitoring") loadMonitoring();
}

function statusBadge(status) {
  return `<span class="status-badge ${escapeHtml(status)}">${escapeHtml(status)}</span>`;
}

function modelTags(models) {
  if (!models?.length) return '<span class="status-badge">All models</span>';
  return `<div class="model-tags">${models.map((model) => `<i>${escapeHtml(model)}</i>`).join("")}</div>`;
}

function fillOrganizationSelects() {
  const options = state.organizations
    .filter((org) => org.status === "active")
    .map((org) => `<option value="${org.id}">${escapeHtml(org.name)}</option>`)
    .join("");
  const filter = $("[data-member-org-filter]");
  const usage = $("[data-usage-org]");
  const memberForm = $('[data-member-form] select[name="org_id"]');
  const previousFilter = filter.value;
  const previousUsage = usage.value;
  filter.innerHTML = `<option value="">All organizations</option>${options}`;
  usage.innerHTML = `<option value="">All organizations</option>${options}`;
  memberForm.innerHTML = `<option value="">Select an organization</option>${options}`;
  filter.value = previousFilter;
  usage.value = previousUsage;
}

function fillMemberSelects() {
  const activeMembers = state.members.filter(
    (member) => member.status === "active"
  );
  const options = activeMembers
    .map((member) => `<option value="${member.id}">${escapeHtml(member.org_name)} · ${escapeHtml(member.name)}</option>`)
    .join("");
  $('[data-key-form] select[name="member_id"]').innerHTML =
    `<option value="">Select a member</option>${options}`;
  const usage = $("[data-usage-member]");
  const previous = usage.value;
  const orgId = $("[data-usage-org]").value;
  const usageOptions = activeMembers
    .filter((member) => !orgId || String(member.org_id) === orgId)
    .map((member) => `<option value="${member.id}">${escapeHtml(member.name)}</option>`)
    .join("");
  usage.innerHTML = `<option value="">All members</option>${usageOptions}`;
  usage.value = previous;
}

function renderOrganizations() {
  const tbody = $("[data-organizations-table]");
  tbody.innerHTML = state.organizations.map((org) => `
    <tr>
      <td><strong>${escapeHtml(org.name)}</strong><small>${escapeHtml(org.slug)}</small></td>
      <td>${escapeHtml(org.license_tier)}</td>
      <td>${number(org.member_count)} / ${number(org.member_limit)}</td>
      <td>${number(org.rpm_limit)}</td>
      <td>${number(org.requests_7d)}</td>
      <td>${statusBadge(org.status)}</td>
      <td><div class="table-actions">
        <button data-action="org-limits" data-id="${org.id}">Limits</button>
        <button class="${org.status === "active" ? "danger" : ""}" data-action="org-status" data-id="${org.id}" data-status="${org.status}">${org.status === "active" ? "Suspend" : "Activate"}</button>
      </div></td>
    </tr>
  `).join("");
  $("[data-organizations-empty]").classList.toggle("show", !state.organizations.length);
  fillOrganizationSelects();
}

function renderMembers() {
  const filter = $("[data-member-org-filter]").value;
  const members = state.members.filter(
    (member) => !filter || String(member.org_id) === filter
  );
  $("[data-members-table]").innerHTML = members.map((member) => `
    <tr>
      <td><strong>${escapeHtml(member.name)}</strong><small>${escapeHtml(member.email)}</small></td>
      <td>${escapeHtml(member.org_name)}</td>
      <td>${escapeHtml(member.role)}</td>
      <td>${number(member.rpm_limit)}</td>
      <td>${number(member.active_keys)}</td>
      <td>${number(member.requests_7d)}</td>
      <td>${statusBadge(member.status)}</td>
      <td><div class="table-actions">
        <button data-action="member-limit" data-id="${member.id}">Limit</button>
        <button class="${member.status === "active" ? "danger" : ""}" data-action="member-status" data-id="${member.id}" data-status="${member.status}">${member.status === "active" ? "Suspend" : "Activate"}</button>
      </div></td>
    </tr>
  `).join("");
  $("[data-members-empty]").classList.toggle("show", !members.length);
  fillMemberSelects();
}

function renderKeys() {
  $("[data-keys-table]").innerHTML = state.keys.map((key) => `
    <tr>
      <td><strong>${escapeHtml(key.name)}</strong><small>${escapeHtml(key.org_name || "Unassigned")}</small></td>
      <td><strong>${escapeHtml(key.member_name || "Legacy key")}</strong><small>${escapeHtml(key.member_email || "")}</small></td>
      <td><code>${escapeHtml(key.key_prefix)}</code></td>
      <td>${number(key.rpm_limit || 120)}</td>
      <td>${modelTags(key.allowed_models)}</td>
      <td>${escapeHtml(date(key.last_used_at))}</td>
      <td>${statusBadge(key.revoked ? "revoked" : "active")}</td>
      <td><div class="table-actions">
        ${key.revoked ? "" : `<button data-action="key-edit" data-id="${key.id}">Restrictions</button><button data-action="key-rotate" data-id="${key.id}">Rotate</button><button class="danger" data-action="key-revoke" data-id="${key.id}">Revoke</button>`}
      </div></td>
    </tr>
  `).join("");
  $("[data-keys-empty]").classList.toggle("show", !state.keys.length);
}

async function loadAccounts() {
  const [organizations, members, keys] = await Promise.all([
    api("/admin/organizations"),
    api("/admin/members"),
    api("/admin/keys"),
  ]);
  state.organizations = organizations.organizations;
  state.members = members.members;
  state.keys = keys.keys;
  renderOrganizations();
  renderMembers();
  renderKeys();
}

async function loadOverview() {
  const overview = await api("/admin/overview");
  Object.entries(overview).forEach(([key, value]) => {
    const target = $(`[data-metric="${key}"]`);
    if (target) target.textContent = number(value);
  });
}

function renderRankList(selector, rows, label) {
  const container = $(selector);
  if (!rows.length) {
    container.innerHTML = '<div><small>No usage recorded in this period.</small></div>';
    return;
  }
  container.innerHTML = rows.slice(0, 8).map((row) => `
    <div><strong>${escapeHtml(row[label])}</strong><span>${number(row.requests)} req</span><small>${number(row.total_tokens)} tokens · ${number(row.avg_duration_ms)} ms avg · ${number(row.errors)} errors</small></div>
  `).join("");
}

async function loadUsage() {
  try {
    const days = $("[data-usage-days]").value;
    const org = $("[data-usage-org]").value;
    const member = $("[data-usage-member]").value;
    const query = new URLSearchParams({ days });
    if (org) query.set("org_id", org);
    if (member) query.set("member_id", member);
    const usage = await api(`/admin/usage?${query}`);
    Object.entries(usage.summary).forEach(([key, value]) => {
      const target = $(`[data-usage-metric="${key}"]`);
      if (target) target.textContent = number(value);
    });
    const max = Math.max(...usage.timeseries.map((row) => row.requests), 1);
    $("[data-usage-chart]").innerHTML = usage.timeseries.length
      ? usage.timeseries.map((row) => `<div class="chart-bar" title="${number(row.requests)} requests"><i style="height:${Math.max(3, row.requests / max * 150)}px"></i><span>${escapeHtml(row.date.slice(5))}</span></div>`).join("")
      : '<div class="empty-state show">No request data for this period.</div>';
    renderRankList("[data-usage-organizations]", usage.by_organization, "organization");
    renderRankList("[data-usage-members]", usage.by_member, "member");
    renderRankList("[data-usage-models]", usage.by_model, "model");
  } catch (error) {
    showAlert(error.message);
  }
}

function setText(selector, value) {
  const target = $(selector);
  if (target) target.textContent = value;
}

async function loadMonitoring() {
  try {
    const data = await api("/admin/monitoring");
    const gpu = data.gpu;
    const runtime = data.runtime;
    const recent = data.requests_24h;
    const gpuUtil = gpu ? `${Math.round(gpu.utilization_percent)}%` : "Unavailable";
    const vram = gpu ? `${Math.round(gpu.memory_used_mb)} / ${Math.round(gpu.memory_total_mb)} MB` : "Unavailable";
    setText('[data-monitor="gpu_utilization"]', gpuUtil);
    setText('[data-monitor="vram"]', vram);
    setText('[data-monitor="active"]', number(runtime.active_requests));
    setText('[data-monitor="queued"]', number(runtime.queued_requests));
    setText('[data-monitor="latency"]', `${number(recent.avg_duration_ms)} ms`);
    setText('[data-monitor="errors"]', number(recent.errors));
    setText("[data-overview-gpu]", gpuUtil);
    setText("[data-overview-vram]", vram);
    setText("[data-overview-queue]", `${runtime.active_requests} / ${runtime.queued_requests}`);
    setText("[data-overview-models]", `${data.loaded_models.length} / ${data.installed_models.length}`);
    setText("[data-gpu-name]", gpu?.name || "GPU unavailable");
    setText("[data-gpu-temperature]", gpu ? `${gpu.temperature_c}°C` : "—");
    setText("[data-gpu-power]", gpu ? `${gpu.power_w.toFixed(1)} W` : "—");
    setText("[data-queue-active]", runtime.active_requests);
    setText("[data-queue-waiting]", runtime.queued_requests);
    setText("[data-queue-capacity]", runtime.max_concurrent_requests);
    $("[data-gpu-gauge]").style.width = `${gpu?.utilization_percent || 0}%`;
    $("[data-vram-gauge]").style.width = `${gpu ? gpu.memory_used_mb / gpu.memory_total_mb * 100 : 0}%`;
    const gpuHealth = $("[data-gpu-health]");
    gpuHealth.textContent = gpu ? "Available" : "Unavailable";
    gpuHealth.classList.toggle("down", !gpu);
    const ollamaHealth = $("[data-ollama-health]");
    ollamaHealth.textContent = data.ollama_reachable ? "Ollama online" : "Ollama offline";
    ollamaHealth.classList.toggle("down", !data.ollama_reachable);
    const stack = $("[data-stack-label]");
    stack.textContent = data.ollama_reachable ? "Stack operational" : "Stack degraded";
    stack.parentElement.classList.toggle("down", !data.ollama_reachable);
    const loaded = new Set(data.loaded_models.map((model) => model.name));
    $("[data-monitor-models]").innerHTML = data.installed_models.map((model) => `
      <div><strong>${escapeHtml(model.name)}</strong><span>${loaded.has(model.name) ? "Loaded" : `${(model.size / 1e9).toFixed(1)} GB`}</span></div>
    `).join("");
    await loadBackups();
  } catch (error) {
    showAlert(error.message);
  }
}

async function loadBackups() {
  const data = await api("/admin/backups");
  const backupStatus = data.status;
  setText("[data-backup-status]", String(backupStatus.status || "never run").replaceAll("_", " "));
  setText("[data-backup-time]", date(backupStatus.last_success));
  setText("[data-backup-file]", backupStatus.last_backup || "No backup yet");
  setText(
    "[data-backup-r2]",
    backupStatus.r2_enabled
      ? (backupStatus.r2_uploaded ? "Uploaded to R2" : "Local only")
      : "Not configured"
  );
  $("[data-backup-history]").innerHTML = data.backups.length
    ? data.backups.slice(0, 6).map((item) => `
        <div>
          <span><strong>${escapeHtml(item.filename)}</strong><small>${escapeHtml(date(item.created_at))} · ${bytes(item.encrypted_bytes)}</small></span>
          <span class="status-badge">${item.r2_uploaded ? "Local + R2" : "Local"}</span>
          <button data-verify-backup="${escapeHtml(item.filename)}">Verify</button>
        </div>
      `).join("")
    : '<div class="empty-state show">No encrypted backups have been created yet.</div>';
}

function showSecret(secret) {
  $("[data-secret-value]").textContent = secret;
  $('[data-modal="secret"]').showModal();
}

async function initialize() {
  await Promise.all([loadOverview(), loadAccounts(), loadMonitoring()]);
  $("[data-login]").hidden = true;
  $("[data-admin-app]").hidden = false;
}

$("[data-login-form]").addEventListener("submit", async (event) => {
  event.preventDefault();
  state.token = $("#admin-token").value.trim();
  $("[data-login-error]").textContent = "";
  try {
    await api("/admin/overview");
    sessionStorage.setItem("grandice_admin_token", state.token);
    await initialize();
  } catch (error) {
    $("[data-login-error]").textContent = error.message;
  }
});

$$("[data-admin-nav] button").forEach((button) => {
  button.addEventListener("click", () => setSection(button.dataset.section));
});
$("[data-logout]").addEventListener("click", () => {
  sessionStorage.removeItem("grandice_admin_token");
  location.reload();
});

$$("[data-open-modal]").forEach((button) => {
  button.addEventListener("click", () => {
    $(`[data-modal="${button.dataset.openModal}"]`).showModal();
  });
});
$$("[data-close-dialog]").forEach((button) => {
  button.addEventListener("click", () => button.closest("dialog").close());
});

$("[data-organization-form]").addEventListener("submit", async (event) => {
  event.preventDefault();
  const form = event.currentTarget;
  if (event.submitter?.value === "cancel") {
    form.closest("dialog").close();
    return;
  }
  if (!form.reportValidity()) return;
  const values = Object.fromEntries(new FormData(form));
  try {
    await api("/admin/organizations", {
      method: "POST",
      body: JSON.stringify({
        name: values.name,
        license_tier: values.license_tier,
        member_limit: Number(values.member_limit),
        rpm_limit: Number(values.rpm_limit),
      }),
    });
    form.closest("dialog").close();
    form.reset();
    await Promise.all([loadAccounts(), loadOverview()]);
    showAlert("Organization created.", "success");
  } catch (error) { showAlert(error.message); }
});

$("[data-member-form]").addEventListener("submit", async (event) => {
  event.preventDefault();
  const form = event.currentTarget;
  if (event.submitter?.value === "cancel") {
    form.closest("dialog").close();
    return;
  }
  if (!form.reportValidity()) return;
  const values = Object.fromEntries(new FormData(form));
  try {
    await api(`/admin/organizations/${values.org_id}/members`, {
      method: "POST",
      body: JSON.stringify({
        name: values.name,
        email: values.email,
        role: values.role,
        rpm_limit: Number(values.rpm_limit),
      }),
    });
    form.closest("dialog").close();
    form.reset();
    await Promise.all([loadAccounts(), loadOverview()]);
    showAlert("Member added.", "success");
  } catch (error) { showAlert(error.message); }
});

$("[data-key-form]").addEventListener("submit", async (event) => {
  event.preventDefault();
  const form = event.currentTarget;
  if (event.submitter?.value === "cancel") {
    form.closest("dialog").close();
    return;
  }
  if (!form.reportValidity()) return;
  const values = Object.fromEntries(new FormData(form));
  const models = values.allowed_models.split(",").map((item) => item.trim()).filter(Boolean);
  try {
    const result = await api("/admin/keys", {
      method: "POST",
      body: JSON.stringify({
        member_id: Number(values.member_id),
        name: values.name,
        rpm_limit: Number(values.rpm_limit),
        allowed_models: models.length ? models : null,
      }),
    });
    form.closest("dialog").close();
    form.reset();
    await Promise.all([loadAccounts(), loadOverview()]);
    showSecret(result.api_key);
  } catch (error) { showAlert(error.message); }
});

$("[data-organizations-table]").addEventListener("click", async (event) => {
  const button = event.target.closest("[data-action]");
  if (!button) return;
  const org = state.organizations.find((item) => item.id === Number(button.dataset.id));
  try {
    if (button.dataset.action === "org-status") {
      const status = button.dataset.status === "active" ? "suspended" : "active";
      await api(`/admin/organizations/${org.id}`, { method: "PATCH", body: JSON.stringify({ status }) });
    } else {
      const rpm = prompt("Organization requests per minute:", org.rpm_limit);
      if (rpm === null) return;
      const members = prompt("Member allowance:", org.member_limit);
      if (members === null) return;
      await api(`/admin/organizations/${org.id}`, {
        method: "PATCH",
        body: JSON.stringify({ rpm_limit: Number(rpm), member_limit: Number(members) }),
      });
    }
    await loadAccounts();
    showAlert("Organization updated.", "success");
  } catch (error) { showAlert(error.message); }
});

$("[data-members-table]").addEventListener("click", async (event) => {
  const button = event.target.closest("[data-action]");
  if (!button) return;
  const member = state.members.find((item) => item.id === Number(button.dataset.id));
  try {
    const body = button.dataset.action === "member-status"
      ? { status: button.dataset.status === "active" ? "suspended" : "active" }
      : { rpm_limit: Number(prompt("Member requests per minute:", member.rpm_limit)) };
    if (!body.rpm_limit && button.dataset.action === "member-limit") return;
    await api(`/admin/members/${member.id}`, { method: "PATCH", body: JSON.stringify(body) });
    await loadAccounts();
    showAlert("Member updated.", "success");
  } catch (error) { showAlert(error.message); }
});

$("[data-keys-table]").addEventListener("click", async (event) => {
  const button = event.target.closest("[data-action]");
  if (!button) return;
  const key = state.keys.find((item) => item.id === Number(button.dataset.id));
  try {
    if (button.dataset.action === "key-revoke") {
      if (!confirm(`Revoke ${key.name}? This cannot be undone.`)) return;
      await api(`/admin/keys/${key.id}`, { method: "DELETE" });
    } else if (button.dataset.action === "key-rotate") {
      if (!confirm(`Rotate ${key.name}? The current key will stop working immediately.`)) return;
      const result = await api(`/admin/keys/${key.id}/rotate`, { method: "POST" });
      showSecret(result.api_key);
    } else {
      const rpm = prompt("Application requests per minute:", key.rpm_limit || 120);
      if (rpm === null) return;
      const models = prompt(
        "Comma-separated allowed models. Leave blank for all:",
        key.allowed_models?.join(", ") || ""
      );
      if (models === null) return;
      const allowed = models.split(",").map((item) => item.trim()).filter(Boolean);
      await api(`/admin/keys/${key.id}`, {
        method: "PATCH",
        body: JSON.stringify({ rpm_limit: Number(rpm), allowed_models: allowed.length ? allowed : null }),
      });
    }
    await Promise.all([loadAccounts(), loadOverview()]);
    showAlert("API key updated.", "success");
  } catch (error) { showAlert(error.message); }
});

$("[data-member-org-filter]").addEventListener("change", renderMembers);
$("[data-usage-org]").addEventListener("change", () => {
  fillMemberSelects();
  loadUsage();
});
$("[data-usage-member]").addEventListener("change", loadUsage);
$("[data-usage-days]").addEventListener("change", loadUsage);
$("[data-refresh-usage]").addEventListener("click", loadUsage);
$$("[data-refresh-monitoring]").forEach((button) => button.addEventListener("click", loadMonitoring));
$("[data-run-backup]").addEventListener("click", async (event) => {
  const button = event.currentTarget;
  button.disabled = true;
  button.textContent = "Backing up…";
  try {
    await api("/admin/backups", { method: "POST" });
    await loadBackups();
    showAlert("Encrypted database backup completed.", "success");
  } catch (error) {
    showAlert(error.message);
  } finally {
    button.disabled = false;
    button.textContent = "Back up now";
  }
});
$("[data-backup-history]").addEventListener("click", async (event) => {
  const button = event.target.closest("[data-verify-backup]");
  if (!button) return;
  button.disabled = true;
  button.textContent = "Checking…";
  try {
    await api(`/admin/backups/${encodeURIComponent(button.dataset.verifyBackup)}/verify`, {
      method: "POST",
    });
    showAlert("Backup decrypted successfully and passed SQLite integrity checks.", "success");
  } catch (error) {
    showAlert(error.message);
  } finally {
    button.disabled = false;
    button.textContent = "Verify";
  }
});

$$("[data-close-secret]").forEach((button) => {
  button.addEventListener("click", () => $('[data-modal="secret"]').close());
});
$("[data-copy-secret]").addEventListener("click", async () => {
  await navigator.clipboard.writeText($("[data-secret-value]").textContent);
  $("[data-copy-secret]").textContent = "Copied";
});

if (state.token) {
  initialize().catch((error) => {
    $("[data-login-error]").textContent = error.message;
  });
}
window.setInterval(() => {
  if (state.token && !document.hidden) loadMonitoring();
}, 15000);

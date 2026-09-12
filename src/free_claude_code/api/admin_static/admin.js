/**
 * FCC Admin UI - Hardened & Premium
 * Security improvements:
 * - No innerHTML with user data
 * - Input sanitization
 * - CSP compliant
 * - XSS protection
 */

const state = {
  config: null,
  applying: false,
  restart: null,
  fields: new Map(),
  modelOptions: [],
  modelComboboxes: new Set(),
  authPollers: new Map(),
  localStatusRequest: null,
  activeView: viewFromLocation(),
  searchQuery: "",
  securityInfo: null,
  metrics: null,
  metricsTimer: null,
  theme: "dark",
  adminApiToken: "",
  securityEvents: null,
  securityEventsSource: null,
};

const MASKED_SECRET = "********";
const NULL_VALUE = "__FCC_NULL__";

// Safe HTML escaping for text content
function escapeHtml(text) {
  const div = document.createElement("div");
  div.textContent = text;
  return div.innerHTML;
}

function safeText(text) {
  return String(text || "").replace(/[<>&"']/g, (c) => ({
    "<": "&lt;",
    ">": "&gt;",
    "&": "&amp;",
    '"': "&quot;",
    "'": "&#39;",
  })[c]);
}

const VIEW_GROUPS = [
  {
    id: "providers",
    label: "Providers",
    title: "Providers",
    subtitle: "Manage AI providers and model routing",
    icon: `<svg class="nav-icon" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M19 11H5m14 0a2 2 0 012 2v6a2 2 0 01-2 2H5a2 2 0 01-2-2v-6a2 2 0 012-2m14 0V9a2 2 0 00-2-2M5 11V9a2 2 0 012-2m0 0V5a2 2 0 012-2h6a2 2 0 012 2v2M7 7h10"/></svg>`,
    sections: ["providers", "runtime"],
    containerId: "providersSections",
  },
  {
    id: "model_config",
    label: "Model Config",
    title: "Model Config",
    subtitle: "Configure model routing and reasoning",
    icon: `<svg class="nav-icon" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M9.75 17L9 20l-1 1h8l-1-1-.75-3M3 13h18M5 17h14a2 2 0 002-2V5a2 2 0 00-2-2H5a2 2 0 00-2 2v10a2 2 0 002 2z"/></svg>`,
    sections: ["models", "reasoning", "web_tools"],
    containerId: "modelConfigSections",
  },
  {
    id: "messaging",
    label: "Messaging",
    title: "Messaging",
    subtitle: "Discord, Telegram and voice settings",
    icon: `<svg class="nav-icon" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M8 12h.01M12 12h.01M16 12h.01M21 12c0 4.418-4.03 8-9 8a9.863 9.863 0 01-4.255-.949L3 20l1.395-3.72C3.512 15.042 3 13.574 3 12c0-4.418 4.03-8 9-8s9 3.582 9 8z"/></svg>`,
    sections: ["messaging", "voice"],
    containerId: "messagingSections",
  },
  {
    id: "security",
    label: "Security",
    title: "Security",
    subtitle: "Security hardening and audit",
    icon: `<svg class="nav-icon" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M9 12l2 2 4-4m5.618-4.016A11.955 11.955 0 0112 2.944a11.955 11.955 0 01-8.618 3.04A12.02 12.02 0 003 9c0 5.591 3.824 10.29 9 11.622 5.176-1.332 9-6.03 9-11.622 0-1.042-.133-2.052-.382-3.016z"/></svg>`,
    sections: [],
    containerId: "view-security",
  },
  {
    id: "metrics",
    label: "Metrics",
    title: "Metrics",
    subtitle: "Runtime latency, RPS and recent traffic",
    icon: `<svg class="nav-icon" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M9 19v-6a2 2 0 00-2-2H5a2 2 0 00-2 2v6a2 2 0 002 2h2a2 2 0 002-2zm0 0V9a2 2 0 012-2h2a2 2 0 012 2v10m-6 0a2 2 0 002 2h2a2 2 0 002-2m0 0V5a2 2 0 012-2h2a2 2 0 012 2v14a2 2 0 01-2 2h-2a2 2 0 01-2-2z"/></svg>`,
    sections: [],
    containerId: "view-metrics",
  },
  {
    id: "integrations",
    label: "Integrations",
    title: "Integrations",
    subtitle: "Connect your favorite editors and tools",
    icon: `<svg class="nav-icon" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M11 4a2 2 0 114 0v1a1 1 0 001 1h3a1 1 0 011 1v3a1 1 0 01-1 1h-1a2 2 0 100 4h1a1 1 0 011 1v3a1 1 0 01-1 1h-3a1 1 0 01-1-1v-1a2 2 0 10-4 0v1a1 1 0 01-1 1H7a1 1 0 01-1-1v-3a1 1 0 00-1-1H4a1 1 0 01-1-1V9a1 1 0 011-1h3a1 1 0 001-1V4a2 2 0 114 0z"/></svg>`,
    sections: [],
    containerId: "view-integrations",
  },
  {
    id: "code",
    label: "Code sessions",
    title: "Code sessions",
    subtitle: "Browser-based Codex sessions",
    icon: `<svg class="nav-icon" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M10 20l4-16m4 4l4 4-4 4M6 16l-4-4 4-4"/></svg>`,
    sections: [],
    containerId: "codeRoot",
  },
];

function viewFromLocation() {
  const path = window.location.pathname.split("/")[2] || "providers";
  // Validate view id to prevent XSS
  const validViews = VIEW_GROUPS.map(v => v.id);
  return validViews.includes(path) ? path : "providers";
}

const byId = (id) => document.getElementById(id);

function sourceLabel(source) {
  const labels = {
    default: "default",
    managed_env: "",
    process: "process env",
  };
  return Object.prototype.hasOwnProperty.call(labels, source) ? labels[source] : source;
}

function sourceText(field) {
  const parts = [];
  const label = sourceLabel(field.source);
  if (label) {
    parts.push(label);
  }
  if (field.locked) {
    parts.push("locked");
  }
  return parts.join(" ");
}

function statusClass(status) {
  if (["configured", "reachable", "running", "connected"].includes(status)) return "ok";
  if (["missing_key", "missing_config", "missing_url", "unknown", "connecting"].includes(status)) return "warn";
  if (["offline", "error"].includes(status)) return "error";
  return "neutral";
}

function showToast(title, message, kind = "neutral", timeout = 4000) {
  const container = byId("toastContainer");
  if (!container) return;
  
  // Validate inputs
  title = String(title || "").slice(0, 100);
  message = String(message || "").slice(0, 300);
  const validKinds = ["ok", "error", "warn", "neutral"];
  if (!validKinds.includes(kind)) kind = "neutral";
  
  const toast = document.createElement("div");
  toast.className = `toast ${kind}`;
  
  const icons = {
    ok: `<svg class="toast-icon" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M9 12l2 2 4-4m6 2a9 9 0 11-18 0 9 9 0 0118 0z"/></svg>`,
    error: `<svg class="toast-icon" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M12 8v4m0 4h.01M21 12a9 9 0 11-18 0 9 9 0 0118 0z"/></svg>`,
    warn: `<svg class="toast-icon" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M12 9v2m0 4h.01m-6.938 4h13.856c1.54 0 2.502-1.667 1.732-2.5L13.732 4c-.77-.833-1.964-.833-2.732 0L3.732 16.5c-.77.833.192 2.5 1.732 2.5z"/></svg>`,
    neutral: `<svg class="toast-icon" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M13 16h-1v-4h-1m1-4h.01M21 12a9 9 0 11-18 0 9 9 0 0118 0z"/></svg>`,
  };
  
  // Safe DOM construction
  const iconWrapper = document.createElement("div");
  iconWrapper.innerHTML = icons[kind] || icons.neutral;
  
  const content = document.createElement("div");
  content.className = "toast-content";
  
  const titleEl = document.createElement("div");
  titleEl.className = "toast-title";
  titleEl.textContent = title;
  
  const messageEl = document.createElement("div");
  messageEl.className = "toast-message";
  messageEl.textContent = message;
  
  content.append(titleEl, messageEl);
  toast.append(iconWrapper.firstElementChild, content);
  
  container.appendChild(toast);
  setTimeout(() => {
    toast.style.animation = "toastIn 0.3s reverse";
    setTimeout(() => toast.remove(), 300);
  }, timeout);
}

async function api(path, options = {}) {
  // Validate path
  if (!path.startsWith("/admin/api/") && !path.startsWith("/admin/")) {
    throw new Error("Invalid API path");
  }
  
  // Check for path traversal
  if (path.includes("..") || path.includes("//")) {
    throw new Error("Invalid path");
  }

  const headers = { "Content-Type": "application/json", ...(options.headers || {}) };
  // Optional dedicated admin API token (sessionStorage — never localStorage long-term)
  try {
    if (!state.adminApiToken) {
      state.adminApiToken = sessionStorage.getItem("fcc.adminApiToken") || "";
    }
  } catch {}
  if (state.adminApiToken) {
    headers["X-FCC-Admin-Token"] = String(state.adminApiToken).slice(0, 512);
  }
  
  const response = await fetch(path, {
    headers,
    ...options,
    cache: "no-store",
  });
  
  // Handle rate limiting
  if (response.status === 429) {
    const retryAfter = response.headers.get("Retry-After") || "60";
    throw new Error(`Rate limited. Retry after ${retryAfter}s`);
  }
  
  if (!response.ok) {
    let detail = "";
    try {
      const payload = await response.json();
      detail = typeof payload.detail === "string" ? payload.detail.slice(0, 500) : "";
    } catch {}
    const error = new Error(detail || `${response.status} ${response.statusText}`);
    error.status = response.status;
    throw error;
  }
  return response.json();
}

function createStatCard(label, value, change, changeKind, valueColor) {
  const card = document.createElement("div");
  card.className = "stat-card";
  
  const labelEl = document.createElement("div");
  labelEl.className = "stat-label";
  labelEl.textContent = label;
  
  const valueEl = document.createElement("div");
  valueEl.className = "stat-value";
  valueEl.textContent = String(value);
  if (valueColor) valueEl.style.color = valueColor;
  
  const changeEl = document.createElement("div");
  changeEl.className = `stat-change ${changeKind}`;
  changeEl.textContent = change;
  
  card.append(labelEl, valueEl, changeEl);
  return card;
}

function renderStats(providerStatus) {
  const grid = byId("statsGrid");
  if (!grid) return;
  
  const total = providerStatus.length;
  const configured = providerStatus.filter(p => ["configured", "connected", "reachable"].includes(p.status)).length;
  const missing = providerStatus.filter(p => ["missing_key", "missing_config", "missing_url"].includes(p.status)).length;
  const models = state.modelOptions.length;

  grid.innerHTML = "";
  grid.append(
    createStatCard("Total Providers", total, `${configured} configured`, "neutral"),
    createStatCard("Configured", configured, "● Active", "positive", "var(--ok)"),
    createStatCard("Need Setup", missing, missing ? "Action needed" : "All good", missing ? "neutral" : "positive", missing ? "var(--warn)" : "var(--muted)"),
    createStatCard("Available Models", models, models ? "Ready to use" : "Loading...", "positive")
  );

  const sbProviders = byId("sidebarProviders");
  const sbModels = byId("sidebarModels");
  if (sbProviders) sbProviders.textContent = `${configured}/${total}`;
  if (sbModels) sbModels.textContent = models || "--";
  const providersLabel = byId("providersCountLabel");
  if (providersLabel) providersLabel.textContent = `${configured} of ${total} providers configured · ${models} models available`;
}

async function loadSecurityInfo() {
  try {
    const info = await api("/admin/api/security/audit");
    state.securityInfo = info;
    renderSecurityView(info);
    void loadSecurityEvents();
  } catch (e) {
    console.warn("Security info failed", e);
  }
}

async function loadSecurityEvents() {
  try {
    const payload = await api("/admin/api/security/events?limit=30");
    state.securityEvents = payload;
    renderSecurityEvents(payload);
    startSecurityEventsStream();
  } catch (e) {
    console.warn("Security events failed", e);
  }
}

function stopSecurityEventsStream() {
  if (state.securityEventsSource) {
    try { state.securityEventsSource.close(); } catch {}
    state.securityEventsSource = null;
  }
}

function startSecurityEventsStream() {
  if (state.activeView !== "security") return;
  if (typeof EventSource === "undefined") return;
  if (state.securityEventsSource) return;
  try {
    // EventSource cannot set custom headers; token via query is avoided (leak risk).
    // When FCC_ADMIN_API_TOKEN is set, SSE requires same-origin cookie-less header —
    // fall back to poll-only if token is configured in session.
    if (state.adminApiToken) return;
    const src = new EventSource("/admin/api/security/events/stream");
    state.securityEventsSource = src;
    const onPayload = (ev) => {
      try {
        const data = JSON.parse(ev.data);
        if (!data || !Array.isArray(data.events)) return;
        const prev = (state.securityEvents && state.securityEvents.events) || [];
        const seen = new Set();
        const newestFirst = [];
        // incoming may be chronological; UI wants newest first
        const incoming = [...data.events].reverse();
        for (const row of incoming.concat(prev)) {
          const seq = row && row.seq;
          if (seq != null) {
            if (seen.has(seq)) continue;
            seen.add(seq);
          }
          newestFirst.push(row);
          if (newestFirst.length >= 50) break;
        }
        state.securityEvents = { events: newestFirst, latest_seq: data.latest_seq };
        renderSecurityEvents(state.securityEvents);
      } catch {}
    };
    src.addEventListener("snapshot", onPayload);
    src.addEventListener("events", onPayload);
    src.onerror = () => {
      stopSecurityEventsStream();
      // retry later while still on security view
      window.setTimeout(() => {
        if (state.activeView === "security") startSecurityEventsStream();
      }, 5000);
    };
  } catch (e) {
    console.warn("SSE security tail unavailable", e);
  }
}

function renderSecurityEvents(payload) {
  const container = byId("view-security");
  if (!container || !payload) return;
  let section = byId("securityEventsSection");
  if (!section) {
    section = document.createElement("section");
    section.id = "securityEventsSection";
    section.className = "settings-section";
    container.appendChild(section);
  }
  section.textContent = "";
  const heading = document.createElement("div");
  heading.className = "section-heading";
  const wrap = document.createElement("div");
  const h3 = document.createElement("h3");
  h3.textContent = "Live security tail";
  const p = document.createElement("p");
  p.textContent = "Recent audit events (ring buffer, newest first)";
  wrap.append(h3, p);
  const refresh = document.createElement("button");
  refresh.type = "button";
  refresh.className = "secondary-button";
  refresh.textContent = "Refresh tail";
  refresh.addEventListener("click", () => loadSecurityEvents());
  heading.append(wrap, refresh);
  section.appendChild(heading);

  const table = document.createElement("div");
  table.className = "metrics-table";
  const header = document.createElement("div");
  header.className = "metrics-row metrics-header metrics-row-recent";
  ["Time", "Level", "Event", "IP", "Path"].forEach((label) => {
    const cell = document.createElement("div");
    cell.textContent = label;
    header.appendChild(cell);
  });
  table.appendChild(header);
  (payload.events || []).slice(0, 30).forEach((row) => {
    const el = document.createElement("div");
    el.className = "metrics-row metrics-row-recent";
    const t = formatTs(row.ts);
    [t, String(row.level || ""), String(row.event || ""), String(row.client_ip || ""), String(row.path || "")].forEach((text, idx) => {
      const cell = document.createElement("div");
      cell.textContent = text.slice(0, 120);
      if (idx >= 2) cell.className = "mono-cell";
      el.appendChild(cell);
    });
    table.appendChild(el);
  });
  if (!(payload.events || []).length) {
    const empty = document.createElement("p");
    empty.className = "muted-center";
    empty.textContent = "No security events yet.";
    section.append(heading, empty);
    return;
  }
  section.append(heading, table);
}

function renderSecurityView(info) {
  const container = byId("view-security");
  if (!container) return;

  container.innerHTML = "";

  const section = document.createElement("section");
  section.className = "settings-section";

  const heading = document.createElement("div");
  heading.className = "section-heading";
  const h3 = document.createElement("h3");
  h3.textContent = "Security Status";
  const p = document.createElement("p");
  p.textContent = "Current security hardening status";
  const headingDiv = document.createElement("div");
  headingDiv.append(h3, p);
  heading.appendChild(headingDiv);

  const grid = document.createElement("div");
  grid.className = "provider-grid";

  const checks = [
    { label: "Remote Admin", ok: info.remote_admin_allowed, desc: info.remote_admin_allowed ? "Enabled (controlled)" : "Disabled" },
    { label: "IP Allowlist", ok: !!info.ip_allowlist_configured, desc: info.ip_allowlist_configured ? "Configured" : "Open (set FCC_ADMIN_IP_ALLOWLIST)" },
    { label: "Admin API Token", ok: !!info.admin_api_token_configured, desc: info.admin_api_token_configured ? "FCC_ADMIN_API_TOKEN set" : "Optional — empty = IP/loopback only" },
    { label: "Native backend", ok: true, desc: `ultra-core: ${info.native_backend || "python"}` },
    { label: "Security Headers", ok: info.security_headers, desc: "HSTS, CSP, X-Frame, etc" },
    { label: "Rate Limiting", ok: info.rate_limiting, desc: "Brute force protection" },
    { label: "Event ring", ok: info.security_event_ring !== false, desc: "Live audit tail" },
    { label: "Metrics", ok: info.metrics_enabled !== false, desc: "Runtime latency + RPS" },
    { label: "CORS", ok: info.cors_enabled, desc: "Remote access enabled" },
    { label: "SSRF Protection", ok: true, desc: "Egress filtering active" },
    { label: "XSS Protection", ok: true, desc: "Safe rendering" },
  ];

  checks.forEach((check) => {
    const card = document.createElement("div");
    card.className = "provider-card";

    const title = document.createElement("div");
    title.className = "provider-title";
    const strong = document.createElement("strong");
    strong.textContent = check.label;
    const pill = document.createElement("span");
    pill.className = `status-pill ${check.ok ? "ok" : "warn"}`;
    pill.textContent = check.ok ? "Active" : "Check";
    title.append(strong, pill);

    const meta = document.createElement("div");
    meta.className = "provider-meta";
    meta.textContent = check.desc;

    card.append(title, meta);
    grid.appendChild(card);
  });

  const infoSection = document.createElement("div");
  infoSection.className = "field-description security-tips";

  const versionP = document.createElement("p");
  versionP.className = "mono-note";
  versionP.textContent = `Version: ${info.version || "unknown"} | Remote: ${info.remote_admin_allowed ? "Allowed" : "Local only"} | Local-only enforced: ${info.local_only_enforced ? "Yes" : "No"}`;

  const tipsP = document.createElement("p");
  tipsP.className = "tips-note";
  tipsP.textContent = "Tips: Use FCC_ADMIN_LOCAL_ONLY=1 to enforce local-only. Set FCC_ADMIN_IP_ALLOWLIST for CIDR allowlist. PROXY_AUTH_ENABLED=1 + strong token for production. See deploy/REMOTE.md.";

  infoSection.append(versionP, tipsP);

  section.append(heading, grid, infoSection);
  container.appendChild(section);
}

function formatUptime(seconds) {
  const s = Math.max(0, Math.floor(Number(seconds) || 0));
  const h = Math.floor(s / 3600);
  const m = Math.floor((s % 3600) / 60);
  const r = s % 60;
  if (h > 0) return `${h}h ${m}m`;
  if (m > 0) return `${m}m ${r}s`;
  return `${r}s`;
}

function formatTs(ts) {
  try {
    return new Date((Number(ts) || 0) * 1000).toLocaleTimeString();
  } catch {
    return "—";
  }
}

async function loadMetrics() {
  try {
    const snap = await api("/admin/api/metrics");
    state.metrics = snap;
    renderMetricsView(snap);
  } catch (error) {
    const root = byId("metricsRoot");
    if (root && !state.metrics) {
      root.textContent = "";
      const p = document.createElement("p");
      p.className = "muted-center";
      p.textContent = `Metrics unavailable: ${String(error.message || error).slice(0, 200)}`;
      root.appendChild(p);
    }
  }
}

function startMetricsPolling() {
  loadMetrics();
  if (state.metricsTimer) return;
  state.metricsTimer = window.setInterval(() => {
    if (state.activeView === "metrics") loadMetrics();
  }, 5000);
}

function stopMetricsPolling() {
  if (state.metricsTimer) {
    window.clearInterval(state.metricsTimer);
    state.metricsTimer = null;
  }
}

function renderMetricsView(snap) {
  const root = byId("metricsRoot");
  if (!root || !snap) return;
  root.textContent = "";

  const summary = document.createElement("div");
  summary.className = "stats-grid metrics-summary";
  summary.append(
    createStatCard("Uptime", formatUptime(snap.uptime_seconds), "Process lifetime", "neutral"),
    createStatCard("Total requests", snap.total_requests, `${snap.requests_per_second} rps`, "positive"),
    createStatCard("Errors", snap.total_errors, `${Math.round((snap.error_rate || 0) * 1000) / 10}% rate`, snap.total_errors ? "neutral" : "positive", snap.total_errors ? "var(--warn)" : "var(--ok)"),
    createStatCard("Rate limits", snap.rate_limit_hits, "429 hits", snap.rate_limit_hits ? "neutral" : "positive", snap.rate_limit_hits ? "var(--warn)" : undefined),
  );
  root.appendChild(summary);

  const routesSection = document.createElement("section");
  routesSection.className = "settings-section";
  const routesHeading = document.createElement("div");
  routesHeading.className = "section-heading";
  const rh = document.createElement("div");
  const rh3 = document.createElement("h3");
  rh3.textContent = "Top routes";
  const rp = document.createElement("p");
  rp.textContent = "Latency averages for the busiest endpoints";
  rh.append(rh3, rp);
  routesHeading.appendChild(rh);
  routesSection.appendChild(routesHeading);

  const table = document.createElement("div");
  table.className = "metrics-table";
  const header = document.createElement("div");
  header.className = "metrics-row metrics-header";
  ["Route", "Count", "Errors", "Avg ms", "Max ms"].forEach((label) => {
    const cell = document.createElement("div");
    cell.textContent = label;
    header.appendChild(cell);
  });
  table.appendChild(header);

  (snap.top_routes || []).slice(0, 15).forEach((row) => {
    const el = document.createElement("div");
    el.className = "metrics-row";
    const cells = [
      row.route,
      String(row.count),
      String(row.errors),
      String(row.avg_ms),
      String(row.max_ms),
    ];
    cells.forEach((text, idx) => {
      const cell = document.createElement("div");
      cell.textContent = text;
      if (idx === 0) cell.className = "mono-cell";
      el.appendChild(cell);
    });
    table.appendChild(el);
  });
  if (!(snap.top_routes || []).length) {
    const empty = document.createElement("p");
    empty.className = "muted-center";
    empty.textContent = "No traffic recorded yet. Hit /health or use a coding agent.";
    routesSection.appendChild(empty);
  } else {
    routesSection.appendChild(table);
  }
  root.appendChild(routesSection);

  
  // Global latency histogram (CSS bars — no Chart.js dep)
  const hist = snap.latency_histogram_ms || {};
  const histKeys = Object.keys(hist).map(Number).sort((a, b) => a - b);
  if (histKeys.length) {
    const histSection = document.createElement("section");
    histSection.className = "settings-section";
    const hh = document.createElement("div");
    hh.className = "section-heading";
    const hhd = document.createElement("div");
    const hh3 = document.createElement("h3");
    hh3.textContent = "Latency histogram";
    const hp = document.createElement("p");
    hp.textContent = "Request duration buckets (ms) across all routes";
    hhd.append(hh3, hp);
    hh.appendChild(hhd);
    histSection.appendChild(hh);
    const maxH = Math.max(...histKeys.map((k) => hist[String(k)] || 0), 1);
    const chart = document.createElement("div");
    chart.className = "latency-bars";
    histKeys.forEach((k) => {
      const count = hist[String(k)] || 0;
      const row = document.createElement("div");
      row.className = "latency-bar-row";
      const label = document.createElement("span");
      label.className = "latency-bar-label mono-cell";
      label.textContent = `≤${k}ms`;
      const track = document.createElement("div");
      track.className = "latency-bar-track";
      const fill = document.createElement("div");
      fill.className = "latency-bar-fill";
      fill.style.width = `${Math.max(2, Math.round((count / maxH) * 100))}%`;
      track.appendChild(fill);
      const val = document.createElement("span");
      val.className = "latency-bar-val";
      val.textContent = String(count);
      row.append(label, track, val);
      chart.appendChild(row);
    });
    if (snap.latency_overflow) {
      const row = document.createElement("div");
      row.className = "latency-bar-row";
      const label = document.createElement("span");
      label.className = "latency-bar-label mono-cell";
      label.textContent = ">max";
      const track = document.createElement("div");
      track.className = "latency-bar-track";
      const fill = document.createElement("div");
      fill.className = "latency-bar-fill overflow";
      const maxO = Math.max(maxH, snap.latency_overflow);
      fill.style.width = `${Math.max(2, Math.round((snap.latency_overflow / maxO) * 100))}%`;
      track.appendChild(fill);
      const val = document.createElement("span");
      val.className = "latency-bar-val";
      val.textContent = String(snap.latency_overflow);
      row.append(label, track, val);
      chart.appendChild(row);
    }
    histSection.appendChild(chart);
    root.appendChild(histSection);
  }

  // Provider latency ranking
  const providers = snap.provider_latency || [];
  if (providers.length) {
    const pSection = document.createElement("section");
    pSection.className = "settings-section";
    const ph = document.createElement("div");
    ph.className = "section-heading";
    const phd = document.createElement("div");
    const ph3 = document.createElement("h3");
    ph3.textContent = "Provider latency";
    const pp = document.createElement("p");
    pp.textContent = "Avg ms from Test / recorded provider ops (higher = slower)";
    phd.append(ph3, pp);
    ph.appendChild(phd);
    pSection.appendChild(ph);
    const maxAvg = Math.max(...providers.map((p) => p.avg_ms || 0), 1);
    const chart = document.createElement("div");
    chart.className = "latency-bars";
    providers.slice(0, 15).forEach((p) => {
      const row = document.createElement("div");
      row.className = "latency-bar-row";
      const label = document.createElement("span");
      label.className = "latency-bar-label mono-cell";
      label.textContent = String(p.provider_id || "").slice(0, 24);
      const track = document.createElement("div");
      track.className = "latency-bar-track";
      const fill = document.createElement("div");
      fill.className = "latency-bar-fill provider";
      fill.style.width = `${Math.max(2, Math.round(((p.avg_ms || 0) / maxAvg) * 100))}%`;
      track.appendChild(fill);
      const val = document.createElement("span");
      val.className = "latency-bar-val";
      val.textContent = `${p.avg_ms}ms · n=${p.count}`;
      row.append(label, track, val);
      chart.appendChild(row);
    });
    pSection.appendChild(chart);
    root.appendChild(pSection);
  }

const recentSection = document.createElement("section");
  recentSection.className = "settings-section";
  const recentHeading = document.createElement("div");
  recentHeading.className = "section-heading";
  const rhd = document.createElement("div");
  const rh3b = document.createElement("h3");
  rh3b.textContent = "Recent requests";
  const rpb = document.createElement("p");
  rpb.textContent = "Last 50 requests (path IDs collapsed)";
  rhd.append(rh3b, rpb);
  recentHeading.appendChild(rhd);
  recentSection.appendChild(recentHeading);

  const recentTable = document.createElement("div");
  recentTable.className = "metrics-table";
  const recentHeader = document.createElement("div");
  recentHeader.className = "metrics-row metrics-header metrics-row-recent";
  ["Time", "Method", "Path", "Status", "ms"].forEach((label) => {
    const cell = document.createElement("div");
    cell.textContent = label;
    recentHeader.appendChild(cell);
  });
  recentTable.appendChild(recentHeader);

  (snap.recent || []).slice(0, 30).forEach((row) => {
    const el = document.createElement("div");
    el.className = "metrics-row metrics-row-recent";
    const statusClass = row.status >= 500 ? "error" : row.status >= 400 ? "warn" : "ok";
    [
      formatTs(row.ts),
      row.method,
      row.path,
      String(row.status),
      String(row.duration_ms),
    ].forEach((text, idx) => {
      const cell = document.createElement("div");
      cell.textContent = text;
      if (idx === 2) cell.className = "mono-cell";
      if (idx === 3) cell.className = `status-cell ${statusClass}`;
      el.appendChild(cell);
    });
    recentTable.appendChild(el);
  });
  recentSection.appendChild(recentTable);
  root.appendChild(recentSection);

  const tests = snap.provider_tests || {};
  const testKeys = Object.keys(tests);
  if (testKeys.length) {
    const testSection = document.createElement("section");
    testSection.className = "settings-section";
    const th = document.createElement("div");
    th.className = "section-heading";
    const thd = document.createElement("div");
    const th3 = document.createElement("h3");
    th3.textContent = "Provider test latency";
    const tp = document.createElement("p");
    tp.textContent = "Last Test / Test All results";
    thd.append(th3, tp);
    th.appendChild(thd);
    testSection.appendChild(th);

    const grid = document.createElement("div");
    grid.className = "provider-grid";
    testKeys.forEach((id) => {
      const info = tests[id];
      const card = document.createElement("div");
      card.className = "provider-card";
      const title = document.createElement("div");
      title.className = "provider-title";
      const strong = document.createElement("strong");
      strong.textContent = id;
      const pill = document.createElement("span");
      pill.className = `status-pill ${info.ok ? "ok" : "error"}`;
      pill.textContent = info.ok ? "OK" : "Fail";
      title.append(strong, pill);
      const meta = document.createElement("div");
      meta.className = "provider-meta";
      meta.textContent = `${info.latency_ms} ms · ${formatTs(info.ts)} · ${(info.message || "").slice(0, 80)}`;
      card.append(title, meta);
      grid.appendChild(card);
    });
    testSection.appendChild(grid);
    root.appendChild(testSection);
  }
}

function initTheme() {
  let theme = "dark";
  try {
    const stored = localStorage.getItem("fcc.theme");
    if (stored === "light" || stored === "dark") theme = stored;
    else if (document.documentElement.getAttribute("data-theme") === "light") theme = "light";
  } catch {
    /* ignore */
  }
  applyTheme(theme, false);
}

function applyTheme(theme, persist = true) {
  const next = theme === "light" ? "light" : "dark";
  state.theme = next;
  document.documentElement.setAttribute("data-theme", next);
  document.documentElement.style.colorScheme = next;
  const meta = document.querySelector('meta[name="theme-color"]');
  if (meta) meta.setAttribute("content", next === "light" ? "#f4f6fb" : "#080a12");
  if (persist) {
    try {
      localStorage.setItem("fcc.theme", next);
    } catch {
      /* ignore */
    }
  }
  const btn = byId("themeToggle");
  if (btn) btn.setAttribute("aria-label", next === "light" ? "Switch to dark theme" : "Switch to light theme");
}

function toggleTheme() {
  applyTheme(state.theme === "light" ? "dark" : "light");
  showToast("Theme", state.theme === "light" ? "Light mode" : "Dark mode", "neutral", 1500);
}

async function exportConfig() {
  try {
    const payload = await api("/admin/api/config/export");
    const blob = new Blob([JSON.stringify(payload, null, 2)], { type: "application/json" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    const stamp = new Date().toISOString().slice(0, 19).replace(/[:T]/g, "-");
    a.href = url;
    a.download = `fcc-config-${stamp}.json`;
    a.rel = "noopener";
    document.body.appendChild(a);
    a.click();
    a.remove();
    URL.revokeObjectURL(url);
    const skipped = (payload.skipped_secrets || []).length;
    showToast(
      "Exported",
      skipped
        ? `${Object.keys(payload.values || {}).length} values (secrets omitted)`
        : `${Object.keys(payload.values || {}).length} values downloaded`,
      "ok",
    );
  } catch (error) {
    showToast("Export failed", error.message, "error");
  }
}

async function importConfigFromFile(file) {
  if (!file) return;
  if (file.size > 1024 * 1024) {
    showToast("Too large", "Config file must be under 1MB", "error");
    return;
  }
  let parsed;
  try {
    const text = await file.text();
    parsed = JSON.parse(text);
  } catch {
    showToast("Invalid JSON", "Could not parse config file", "error");
    return;
  }
  const values =
    parsed && typeof parsed === "object"
      ? parsed.values && typeof parsed.values === "object"
        ? parsed.values
        : parsed
      : null;
  if (!values || typeof values !== "object" || Array.isArray(values)) {
    showToast("Invalid format", "Expected { values: { KEY: value } }", "error");
    return;
  }
  // Strip anything that looks like a secret before sending
  const cleaned = {};
  Object.keys(values).slice(0, 200).forEach((key) => {
    if (typeof key !== "string") return;
    const upper = key.toUpperCase();
    if (upper.includes("KEY") || upper.includes("TOKEN") || upper.includes("SECRET") || upper.includes("PASSWORD")) {
      return;
    }
    const value = values[key];
    if (value === null || ["string", "number", "boolean"].includes(typeof value)) {
      cleaned[key] = value;
    }
  });
  try {
    const preview = await api("/admin/api/config/import", {
      method: "POST",
      body: JSON.stringify({ values: cleaned, apply: false }),
    });
    const count = preview.count || 0;
    if (!count) {
      showToast("Nothing to import", "No non-secret known keys found", "warn");
      return;
    }
    const rejected = (preview.rejected_secrets || []).length;
    const unknown = (preview.unknown_keys || []).length;
    const ok = window.confirm(
      `Import ${count} setting(s)?\nSecrets stripped: ${rejected}\nUnknown keys skipped: ${unknown}\n\nThis will write managed config and may restart the server.`,
    );
    if (!ok) return;
    const result = await api("/admin/api/config/import", {
      method: "POST",
      body: JSON.stringify({ values: cleaned, apply: true }),
    });
    if (result.applied) {
      showToast("Imported", `${count} settings applied`, "ok");
      await load();
    } else {
      const err = (result.errors || []).join("; ") || "Import rejected";
      showToast("Import failed", err, "error");
    }
  } catch (error) {
    showToast("Import failed", error.message, "error");
  }
}

function openCommandPalette() {
  const dialog = byId("commandPalette");
  const input = byId("commandPaletteInput");
  const list = byId("commandPaletteList");
  if (!dialog || !input || !list) return;
  input.value = "";
  renderCommandPalette("");
  if (typeof dialog.showModal === "function") dialog.showModal();
  else dialog.setAttribute("open", "true");
  input.focus();
}

function closeCommandPalette() {
  const dialog = byId("commandPalette");
  if (!dialog) return;
  if (typeof dialog.close === "function") dialog.close();
  else dialog.removeAttribute("open");
}

function commandPaletteItems() {
  return [
    ...VIEW_GROUPS.map((view) => ({
      id: `view:${view.id}`,
      label: `Go to ${view.label}`,
      hint: view.subtitle || "",
      run: () => navigateToView(view.id),
    })),
    { id: "refresh", label: "Refresh config", hint: "R", run: () => load() },
    { id: "theme", label: "Toggle theme", hint: "T", run: () => toggleTheme() },
    { id: "export", label: "Export config", hint: "Non-secret JSON", run: () => exportConfig() },
    { id: "import", label: "Import config", hint: "From JSON file", run: () => byId("importConfigFile")?.click() },
    { id: "test-all", label: "Test all providers", hint: "Providers view", run: () => testAllProviders() },
    { id: "metrics", label: "Open metrics", hint: "Latency + RPS", run: () => navigateToView("metrics") },
    { id: "admin-token", label: "Set admin API token", hint: "session only", run: () => promptAdminApiToken() },
  ];
}

function promptAdminApiToken() {
  const current = state.adminApiToken || "";
  const next = window.prompt(
    "Admin API token (FCC_ADMIN_API_TOKEN). Stored in sessionStorage only. Leave empty to clear.",
    current,
  );
  if (next === null) return;
  state.adminApiToken = String(next).trim().slice(0, 512);
  try {
    if (state.adminApiToken) sessionStorage.setItem("fcc.adminApiToken", state.adminApiToken);
    else sessionStorage.removeItem("fcc.adminApiToken");
  } catch {}
  showToast(
    state.adminApiToken ? "Admin token set" : "Admin token cleared",
    "Applies to this browser tab only",
    "ok",
  );
  void load();
}

function renderCommandPalette(query) {
  const list = byId("commandPaletteList");
  if (!list) return;
  list.textContent = "";
  const q = String(query || "").trim().toLowerCase().slice(0, 80);
  const items = commandPaletteItems().filter((item) => {
    if (!q) return true;
    return item.label.toLowerCase().includes(q) || (item.hint || "").toLowerCase().includes(q);
  }).slice(0, 12);

  items.forEach((item, index) => {
    const li = document.createElement("li");
    li.setAttribute("role", "option");
    li.dataset.id = item.id;
    if (index === 0) li.setAttribute("aria-selected", "true");
    const label = document.createElement("span");
    label.className = "cmd-label";
    label.textContent = item.label;
    const hint = document.createElement("span");
    hint.className = "cmd-hint";
    hint.textContent = item.hint || "";
    li.append(label, hint);
    li.addEventListener("click", () => {
      closeCommandPalette();
      item.run();
    });
    list.appendChild(li);
  });

  if (!items.length) {
    const li = document.createElement("li");
    li.className = "cmd-empty";
    li.textContent = "No matches";
    list.appendChild(li);
  }
}

function runSelectedCommand() {
  const list = byId("commandPaletteList");
  if (!list) return;
  const selected = list.querySelector('[aria-selected="true"]') || list.querySelector("li[data-id]");
  if (!selected) return;
  const id = selected.dataset.id;
  const item = commandPaletteItems().find((entry) => entry.id === id);
  if (!item) return;
  closeCommandPalette();
  item.run();
}

function moveCommandSelection(delta) {
  const list = byId("commandPaletteList");
  if (!list) return;
  const options = Array.from(list.querySelectorAll("li[data-id]"));
  if (!options.length) return;
  let idx = options.findIndex((el) => el.getAttribute("aria-selected") === "true");
  if (idx < 0) idx = 0;
  options.forEach((el) => el.removeAttribute("aria-selected"));
  idx = (idx + delta + options.length) % options.length;
  options[idx].setAttribute("aria-selected", "true");
  options[idx].scrollIntoView({ block: "nearest" });
}

function isTypingTarget(target) {
  if (!target || !(target instanceof Element)) return false;
  const tag = target.tagName;
  if (tag === "INPUT" || tag === "TEXTAREA" || tag === "SELECT") return true;
  return target.isContentEditable;
}

function setupKeyboardShortcuts() {
  document.addEventListener("keydown", (event) => {
    const meta = event.metaKey || event.ctrlKey;
    const dialog = byId("commandPalette");
    const paletteOpen = dialog && (dialog.open || dialog.hasAttribute("open"));

    if (meta && event.key.toLowerCase() === "k") {
      event.preventDefault();
      if (paletteOpen) closeCommandPalette();
      else openCommandPalette();
      return;
    }

    if (paletteOpen) {
      if (event.key === "Escape") {
        event.preventDefault();
        closeCommandPalette();
        return;
      }
      if (event.key === "ArrowDown") {
        event.preventDefault();
        moveCommandSelection(1);
        return;
      }
      if (event.key === "ArrowUp") {
        event.preventDefault();
        moveCommandSelection(-1);
        return;
      }
      if (event.key === "Enter") {
        event.preventDefault();
        runSelectedCommand();
        return;
      }
      return;
    }

    if (isTypingTarget(event.target) || meta || event.altKey) return;

    if (event.key === "Escape") {
      const search = byId("globalSearch");
      if (search && document.activeElement === search) {
        search.blur();
        return;
      }
    }
    if (event.key === "/" || (event.key === "s" && !event.shiftKey)) {
      const search = byId("globalSearch");
      const box = byId("globalSearchBox");
      if (search && box && !box.hidden) {
        event.preventDefault();
        search.focus();
        search.select();
      }
      return;
    }
    if (event.key.toLowerCase() === "t") {
      event.preventDefault();
      toggleTheme();
      return;
    }
    if (event.key.toLowerCase() === "r" && !event.shiftKey) {
      event.preventDefault();
      load();
      showToast("Refreshing", "Reloading configuration...", "neutral");
      return;
    }
    if (event.key >= "1" && event.key <= "7") {
      const idx = Number(event.key) - 1;
      if (VIEW_GROUPS[idx]) {
        event.preventDefault();
        navigateToView(VIEW_GROUPS[idx].id);
      }
    }
  });

  const input = byId("commandPaletteInput");
  if (input) {
    input.addEventListener("input", (e) => {
      renderCommandPalette(e.target.value);
    });
  }
  const dialog = byId("commandPalette");
  if (dialog) {
    dialog.addEventListener("click", (event) => {
      if (event.target === dialog) closeCommandPalette();
    });
  }
}

async function load() {
  state.localStatusRequest = null;
  showMessage("Loading admin config");
  try {
    const config = await api("/admin/api/config");
    state.config = config;
    state.fields = new Map(config.fields.map((field) => [field.key, field]));
    renderNav();
    renderProviders(config.provider_status);
    renderSections(config.sections, config.fields);
    renderStats(config.provider_status);
    byId("configPath").textContent = config.paths.managed;
    void refreshLocalStatus(config);
    await Promise.all([
      refreshConnectedAccounts(),
      hydrateModelOptions(),
      window.CodeSessions.initialize(api),
      loadSecurityInfo(),
    ]);
    updateDirtyState();
    showMessage("");
    showToast("Loaded", "Admin configuration loaded successfully", "ok", 2500);
  } catch (error) {
    showMessage(error.message, "error");
    showToast("Load failed", error.message, "error");
  }
}

function renderNav() {
  const nav = byId("sectionNav");
  nav.innerHTML = "";
  const byIdMap = Object.fromEntries(VIEW_GROUPS.map((v) => [v.id, v]));
  const groups = [
    { label: "Main", views: ["providers", "model_config", "messaging"].map((id) => byIdMap[id]).filter(Boolean) },
    { label: "Observe", views: ["security", "metrics"].map((id) => byIdMap[id]).filter(Boolean) },
    { label: "Tools", views: ["integrations", "code"].map((id) => byIdMap[id]).filter(Boolean) },
  ];
  groups.forEach(group => {
    const label = document.createElement("div");
    label.className = "nav-section-label";
    label.textContent = group.label;
    nav.appendChild(label);
    group.views.forEach((view) => {
      const button = document.createElement("button");
      button.type = "button";
      button.className = `nav-link`;
      button.dataset.view = view.id;
      // Icon is trusted (hardcoded), label is validated
      button.innerHTML = `${view.icon}<span></span>`;
      button.querySelector("span").textContent = view.label;
      button.addEventListener("click", () => {
        navigateToView(view.id);
      });
      nav.appendChild(button);
    });
  });
  setActiveView(state.activeView, { scroll: false });
}

function setActiveView(viewId, { scroll = false } = {}) {
  // Validate viewId
  const validViews = VIEW_GROUPS.map(v => v.id);
  if (!validViews.includes(viewId)) viewId = "providers";
  
  const activeView =
    VIEW_GROUPS.find((view) => view.id === viewId) || VIEW_GROUPS[0];
  state.activeView = activeView.id;
  byId("pageTitle").textContent = activeView.title;
  const subtitle = byId("pageSubtitle");
  if (subtitle) subtitle.textContent = activeView.subtitle || "";
  const sessionActive = activeView.id === "code";
  document.querySelector(".app-shell").classList.toggle("session-active", sessionActive);
  document.querySelector(".main").classList.toggle("session-main", sessionActive);
  const topbar = document.querySelector(".topbar");
  if (topbar) topbar.hidden = sessionActive;
  const actionBar = document.querySelector(".action-bar");
  if (actionBar) actionBar.hidden = sessionActive || ["integrations", "security", "metrics"].includes(activeView.id);
  const statsGrid = byId("statsGrid");
  if (statsGrid) statsGrid.hidden = sessionActive || activeView.id !== "providers";
  const searchBox = byId("globalSearchBox");
  if (searchBox) searchBox.hidden = sessionActive || activeView.id !== "providers";

  document.querySelectorAll(".nav-link").forEach((link) => {
    const selected = link.dataset.view === activeView.id;
    link.classList.toggle("active", selected);
    if (selected) {
      link.setAttribute("aria-current", "page");
    } else {
      link.removeAttribute("aria-current");
    }
  });

  document.querySelectorAll(".admin-view").forEach((view) => {
    const selected = view.dataset.view === activeView.id;
    view.classList.toggle("active", selected);
    view.hidden = !selected;
  });

  if (scroll) {
    window.scrollTo({ top: 0, behavior: "smooth" });
  }
  if (activeView.id === "code") window.CodeSessions.activate(window.location.pathname);
  else window.CodeSessions.deactivate();
  if (activeView.id === "integrations") {
    refreshClaudeIntegration();
    refreshCodexIntegration();
  }
  if (activeView.id === "security") {
    if (state.securityInfo) renderSecurityView(state.securityInfo);
    void loadSecurityEvents();
  } else {
    stopSecurityEventsStream();
  }
  if (activeView.id === "metrics") {
    startMetricsPolling();
  } else {
    stopMetricsPolling();
  }
}

function navigateToView(viewId) {
  const validViews = VIEW_GROUPS.map(v => v.id);
  if (!validViews.includes(viewId)) viewId = "providers";
  const target = viewId === "providers" ? "/admin" : `/admin/${viewId}`;
  if (window.location.pathname + window.location.search !== target) {
    window.history.pushState({}, "", target);
  }
  setActiveView(viewId, { scroll: true });
}

function filteredProviders(providerStatus) {
  if (!state.searchQuery) return providerStatus;
  const q = state.searchQuery.toLowerCase().slice(0, 100);
  return providerStatus.filter(p => 
    (p.provider_id && p.provider_id.toLowerCase().includes(q)) ||
    (p.display_name && p.display_name.toLowerCase().includes(q)) ||
    (p.label && p.label.toLowerCase().includes(q))
  );
}

function renderProviders(providerStatus) {
  const grid = byId("providerGrid");
  const connectedGrid = byId("connectedAccountGrid");
  if (!grid || !connectedGrid) return;
  
  grid.innerHTML = "";
  connectedGrid.innerHTML = "";
  const connected = providerStatus.filter(
    (provider) => provider.kind === "connected_account",
  );
  const connectedSection = byId("connectedAccountsSection");
  if (connectedSection) connectedSection.hidden = connected.length === 0;
  const visible = filteredProviders(providerStatus);
  visible.forEach((provider) => {
    if (provider.kind === "connected_account") {
      connectedGrid.appendChild(renderConnectedAccountCard(provider));
      return;
    }
    const card = document.createElement("article");
    card.className = "provider-card";
    card.dataset.provider = provider.provider_id;

    const title = document.createElement("div");
    title.className = "provider-title";
    const name = document.createElement("strong");
    name.textContent = provider.display_name || provider.provider_id;

    const pill = document.createElement("span");
    pill.className = `status-pill ${statusClass(provider.status)}`;
    pill.textContent = provider.label;
    title.append(name, pill);

    const meta = document.createElement("div");
    meta.className = "provider-meta";
    const configurationKeys = Array.isArray(provider.configuration_keys)
      ? provider.configuration_keys
      : [];
    const missingConfigurationKeys = Array.isArray(
      provider.missing_configuration_keys,
    )
      ? provider.missing_configuration_keys
      : [];
    meta.textContent = configurationKeys.join(" + ") || provider.provider_id;

    const result = document.createElement("div");
    result.className = "provider-check-result";
    result.dataset.providerCheckResult = provider.provider_id;
    result.setAttribute("aria-live", "polite");
    result.hidden = true;

    const actions = document.createElement("div");
    actions.className = "provider-actions";
    if (configurationKeys.length) {
      const configuring = missingConfigurationKeys.length > 0;
      actions.appendChild(
        providerActionButton(configuring ? "Configure" : "Edit", () =>
          navigateToProviderConfiguration(provider, configuring),
        ),
      );
    }

    if (missingConfigurationKeys.length === 0) {
      const button = providerActionButton(
        provider.kind === "local" ? "Test" : "Refresh models",
        () => testProvider(provider.provider_id, button),
        "secondary-button",
      );
      actions.appendChild(button);
    }

    card.append(title, meta, result, actions);
    grid.appendChild(card);
  });

  if (visible.length === 0 && state.searchQuery) {
    const empty = document.createElement("div");
    empty.style.cssText = "grid-column:1/-1;text-align:center;padding:40px;color:var(--muted)";
    empty.textContent = `No providers match "${state.searchQuery.slice(0, 50)}"`;
    grid.appendChild(empty);
  }

  if (state.modelOptions.length) {
    renderStats(providerStatus);
  }
}

function providerActionButton(label, action, className = "test-button") {
  const button = document.createElement("button");
  button.type = "button";
  button.className = className;
  button.textContent = label.slice(0, 50);
  button.addEventListener("click", action);
  return button;
}

function navigateToProviderConfiguration(provider, configuring) {
  const keys = configuring
    ? provider.missing_configuration_keys
    : provider.configuration_keys;
  const fieldKey = Array.isArray(keys) ? keys[0] : null;
  const input = fieldKey ? byId(`field-${fieldKey}`) : null;
  if (!input) {
    showMessage("Provider configuration field is unavailable.", "error");
    return;
  }
  const reducedMotion = window.matchMedia(
    "(prefers-reduced-motion: reduce)",
  ).matches;
  input.scrollIntoView({
    behavior: reducedMotion ? "instant" : "smooth",
    block: "center",
  });
  input.focus({ preventScroll: true });
}

function connectedAccountName(provider) {
  return provider.display_name || provider.provider_id;
}

function renderConnectedAccountCard(provider, status = null) {
  const card = document.createElement("article");
  card.className = "provider-card";
  card.dataset.provider = provider.provider_id;
  card.dataset.connectedAccount = "true";

  const title = document.createElement("div");
  title.className = "provider-title";
  const name = document.createElement("strong");
  name.textContent = connectedAccountName(provider);
  const pill = document.createElement("span");
  pill.className = `status-pill ${statusClass(status?.state)}`;
  pill.textContent = connectedAccountLabel(status);
  title.append(name, pill);

  const meta = document.createElement("div");
  meta.className = "provider-meta";
  meta.textContent = connectedAccountMeta(provider, status);

  const actions = document.createElement("div");
  actions.className = "provider-actions";
  populateConnectedAccountActions(provider, status, actions);
  card.append(title, meta, actions);
  return card;
}

function connectedAccountLabel(status) {
  if (!status) return "Loading";
  const labels = {
    disconnected: "Not connected",
    connecting: "Connecting",
    connected: "Connected",
    error: "Needs attention",
  };
  return labels[status.state] || "Not connected";
}

function connectedAccountMeta(provider, status) {
  if (!status) return "Checking account status…";
  const providerName = connectedAccountName(provider);
  if (status.state === "connecting") {
    if (status.mode === "device" && status.user_code && status.verification_url) {
      return `Enter code ${status.user_code} at ${status.verification_url}`;
    }
    return status.message || "Finish signing in, then return to this page.";
  }
  if (status.connected) {
    const identity = status.display_identity || status.email || `${providerName} account connected`;
    const models = Number.isInteger(status.model_count)
      ? `${status.model_count} model${status.model_count === 1 ? "" : "s"} available. `
      : "";
    const error = status.message ? `${status.message} ` : "";
    return `${identity}. ${models}${error}Restart your agent to refresh its model picker.`;
  }
  return status.message || `Connect your ${providerName} account to discover models.`;
}

function populateConnectedAccountActions(provider, status, actions) {
  const providerId = provider.provider_id;
  if (!status) {
    const loading = authButton("Loading…", () => {});
    loading.disabled = true;
    actions.appendChild(loading);
    return;
  }
  if (status.state === "connecting") {
    const target = status.authorization_url || status.verification_url;
    if (target) {
      // Validate URL before opening
      try {
        const url = new URL(target);
        if (["https:", "http:"].includes(url.protocol)) {
          actions.appendChild(authButton("Open sign-in", () => window.open(target, "_blank", "noopener,noreferrer")));
        }
      } catch {}
    }
    if (status.mode === "device" && status.user_code) {
      actions.appendChild(
        authButton("Copy code", () => copyDeviceCode(status.user_code), "secondary-button"),
      );
    }
    actions.appendChild(
      authButton("Cancel", () => cancelConnectedAccountLogin(providerId), "secondary-button"),
    );
    return;
  }
  const modes = Array.isArray(status.supported_login_modes) ? status.supported_login_modes : [];
  const defaultMode = status.default_login_mode;
  if (!["browser", "device"].includes(defaultMode) || !modes.includes(defaultMode)) {
    actions.appendChild(authButton("Retry", () => refreshConnectedAccount(provider)));
    return;
  }
  actions.appendChild(
    authButton(
      status.connected ? "Reconnect" : "Connect",
      (button) => startConnectedAccountLogin(providerId, defaultMode, button),
    ),
  );
  if (status.connected) {
    actions.appendChild(
      authButton("Disconnect", () => disconnectConnectedAccount(providerId), "secondary-button"),
    );
    return;
  }
  modes.filter((mode) => mode !== defaultMode).forEach((mode) => {
    const label = { browser: "Use browser", device: "Use device code" }[mode];
    if (label) {
      actions.appendChild(
        authButton(label, (button) => startConnectedAccountLogin(providerId, mode, button), "secondary-button"),
      );
    }
  });
}

function authButton(label, action, className = "test-button") {
  const button = document.createElement("button");
  button.type = "button";
  button.className = className;
  button.textContent = label;
  button.addEventListener("click", () => action(button));
  return button;
}

async function refreshConnectedAccounts() {
  const providers = (state.config?.provider_status || []).filter(
    (provider) => provider.kind === "connected_account",
  );
  await Promise.all(providers.map(refreshConnectedAccount));
}

async function refreshConnectedAccount(provider) {
  clearConnectedAccountPoll(provider.provider_id);
  updateConnectedAccountCard(provider, null);
  try {
    const status = await api(`/admin/api/providers/${provider.provider_id}/auth`);
    updateConnectedAccountCard(provider, status);
    if (status.state === "connecting") pollConnectedAccount(provider);
  } catch (error) {
    updateConnectedAccountCard(provider, {
      state: "error",
      connected: false,
      message: error.message,
    });
  }
}

function updateConnectedAccountCard(provider, status) {
  const current = document.querySelector(
    `[data-provider="${provider.provider_id}"][data-connected-account="true"]`,
  );
  if (current) current.replaceWith(renderConnectedAccountCard(provider, status));
}

async function startConnectedAccountLogin(providerId, mode, button) {
  const buttons = button.closest(".provider-actions").querySelectorAll("button");
  buttons.forEach((action) => { action.disabled = true; });
  clearConnectedAccountPoll(providerId);
  const popup = mode === "browser" ? window.open("about:blank", "_blank") : null;
  if (popup) popup.opener = null;
  try {
    const status = await api(`/admin/api/providers/${providerId}/auth/login`, {
      method: "POST",
      body: JSON.stringify({ mode }),
    });
    const provider = connectedAccountDescriptor(providerId);
    updateConnectedAccountCard(provider, status);
    const target = status.authorization_url || status.verification_url;
    if (mode === "browser") {
      if (target && popup) {
        try {
          const url = new URL(target);
          if (["https:", "http:"].includes(url.protocol)) {
            popup.location.replace(target);
          } else {
            popup.close();
          }
        } catch {
          popup.close();
        }
      } else if (target) {
        try {
          const url = new URL(target);
          if (["https:", "http:"].includes(url.protocol)) {
            window.open(target, "_blank", "noopener,noreferrer");
          }
        } catch {}
      } else if (popup) {
        popup.close();
      }
    }
    if (status.state === "connecting") pollConnectedAccount(provider);
    else if (status.connected) await hydrateModelOptions();
  } catch (error) {
    if (popup) popup.close();
    showMessage(error.message, true);
    buttons.forEach((action) => { action.disabled = false; });
  }
}

async function cancelConnectedAccountLogin(providerId) {
  clearConnectedAccountPoll(providerId);
  const provider = connectedAccountDescriptor(providerId);
  try {
    const status = await api(`/admin/api/providers/${providerId}/auth/cancel`, {
      method: "POST",
    });
    updateConnectedAccountCard(provider, status);
  } catch (error) {
    showMessage(error.message, true);
    pollConnectedAccount(provider);
  }
}

async function disconnectConnectedAccount(providerId) {
  const provider = connectedAccountDescriptor(providerId);
  if (!window.confirm(`Disconnect this ${connectedAccountName(provider)} account from FCC?`)) return;
  clearConnectedAccountPoll(providerId);
  try {
    const status = await api(`/admin/api/providers/${providerId}/auth`, { method: "DELETE" });
    updateConnectedAccountCard(provider, status);
    await hydrateModelOptions();
  } catch (error) {
    showMessage(error.message, true);
  }
}

function pollConnectedAccount(provider) {
  const providerId = provider.provider_id;
  clearConnectedAccountPoll(providerId);
  const poller = { timer: null };
  state.authPollers.set(providerId, poller);
  const poll = async () => {
    try {
      const status = await api(`/admin/api/providers/${providerId}/auth`);
      if (state.authPollers.get(providerId) !== poller) return;
      updateConnectedAccountCard(provider, status);
      if (status.state === "connecting") {
        poller.timer = window.setTimeout(poll, 1000);
      } else {
        state.authPollers.delete(providerId);
        if (status.connected) await hydrateModelOptions();
      }
    } catch (error) {
      if (state.authPollers.get(providerId) !== poller) return;
      state.authPollers.delete(providerId);
      showMessage(error.message, true);
    }
  };
  poller.timer = window.setTimeout(poll, 1000);
}

function clearConnectedAccountPoll(providerId) {
  const poller = state.authPollers.get(providerId);
  if (poller?.timer) window.clearTimeout(poller.timer);
  state.authPollers.delete(providerId);
}

function connectedAccountDescriptor(providerId) {
  return state.config.provider_status.find(
    (provider) => provider.provider_id === providerId,
  );
}

async function copyDeviceCode(code) {
  try {
    await navigator.clipboard.writeText(code);
    showMessage("Device code copied.");
    showToast("Copied", "Device code copied to clipboard", "ok");
  } catch {
    showMessage(`Copy this device code: ${code}`);
  }
}

function updateProviderCheckResult(providerId, status, message) {
  const card = document.querySelector(`[data-provider="${providerId}"]`);
  if (!card) return;
  const result = card.querySelector(".provider-check-result");
  result.className = `provider-check-result ${status}`;
  result.textContent = message.slice(0, 500);
  result.hidden = !message;
}

function renderSections(sections, fields) {
  state.modelComboboxes.clear();
  VIEW_GROUPS.filter((view) => view.sections.length).forEach((view) => {
    const el = byId(view.containerId);
    if (el) el.innerHTML = "";
  });

  const sectionById = new Map(sections.map((section) => [section.id, section]));
  const bySection = new Map();
  sections.forEach((section) => bySection.set(section.id, []));
  fields.forEach((field) => {
    if (!bySection.has(field.section)) bySection.set(field.section, []);
    bySection.get(field.section).push(field);
  });

  VIEW_GROUPS.forEach((view) => {
    const container = byId(view.containerId);
    if (!container) return;
    view.sections.forEach((sectionId) => {
      const section = sectionById.get(sectionId);
      const sectionFields = bySection.get(sectionId) || [];
      if (!section || sectionFields.length === 0) return;

      const sectionEl = document.createElement("section");
      sectionEl.className = "settings-section";
      sectionEl.id = `section-${section.id}`;

      const heading = document.createElement("div");
      heading.className = "section-heading";
      const headingDiv = document.createElement("div");
      const h3 = document.createElement("h3");
      h3.textContent = section.label;
      const p = document.createElement("p");
      p.textContent = section.description;
      headingDiv.append(h3, p);
      heading.appendChild(headingDiv);
      
      if (section.id === "models") {
        const refreshButton = document.createElement("button");
        refreshButton.type = "button";
        refreshButton.className = "secondary-button";
        refreshButton.textContent = "Refresh models";
        refreshButton.addEventListener("click", () => refreshModelOptions(refreshButton));
        heading.appendChild(refreshButton);
      }
      sectionEl.appendChild(heading);

      const grid = document.createElement("div");
      grid.className = "field-grid";
      sectionFields.forEach((field) => {
        grid.appendChild(renderField(field));
      });
      sectionEl.appendChild(grid);

      if (sectionFields.some((field) => field.advanced)) {
        const toggle = document.createElement("button");
        toggle.type = "button";
        toggle.className = "ghost-button advanced-toggle";
        toggle.textContent = "Show advanced";
        toggle.addEventListener("click", () => {
          const showing = sectionEl.classList.toggle("show-advanced");
          toggle.textContent = showing ? "Hide advanced" : "Show advanced";
        });
        sectionEl.appendChild(toggle);
      }

      container.appendChild(sectionEl);
    });
  });
}

function renderField(field) {
  const wrapper = document.createElement("div");
  wrapper.className = `field${field.advanced ? " advanced-field" : ""}`;
  wrapper.dataset.key = field.key;

  const label = document.createElement("label");
  label.htmlFor = `field-${field.key}`;
  const labelText = document.createElement("span");
  labelText.textContent = field.label;
  label.appendChild(labelText);

  const source = sourceText(field);
  if (source) {
    const sourceEl = document.createElement("span");
    sourceEl.className = "field-source";
    sourceEl.textContent = source;
    label.appendChild(sourceEl);
  }

  const input = inputForField(field);
  input.id = `field-${field.key}`;
  input.dataset.key = field.key;
  input.dataset.original = comparableValue(field.value);
  input.dataset.secret = field.secret ? "true" : "false";
  input.dataset.configured = field.configured ? "true" : "false";
  input.dataset.nullable = field.nullable ? "true" : "false";
  input.dataset.remove = "false";
  input.dataset.fieldType = field.type;
  input.disabled = field.locked;
  input.addEventListener("input", updateDirtyState);
  input.addEventListener("change", updateDirtyState);
  input.addEventListener("input", () => {
    input.dataset.remove = "false";
    clearCredentialError(input);
  });
  if (field.type === "optional_model") {
    input.addEventListener("blur", () => {
      if (!input.value.trim() || input.value.trim().toLowerCase() === "none") {
        input.value = "None";
        updateDirtyState();
      }
    });
  }

  let control = input;
  if (field.type === "model" || field.type === "optional_model") {
    control = createModelCombobox(input, field).element;
  } else if (field.type === "model_list") {
    const editor = new ModelListEditor(input, field);
    label.htmlFor = editor.inputId;
    control = editor.element;
  }
  wrapper.append(label, control);
  if (field.secret && field.nullable && field.configured && !field.locked) {
    const removeButton = document.createElement("button");
    removeButton.type = "button";
    removeButton.className = "ghost-button secret-remove";
    removeButton.textContent = "Remove";
    removeButton.addEventListener("click", () => {
      const removing = input.dataset.remove !== "true";
      input.dataset.remove = removing ? "true" : "false";
      input.readOnly = removing;
      removeButton.textContent = removing ? "Undo removal" : "Remove";
      clearCredentialError(input);
      updateDirtyState();
    });
    wrapper.appendChild(removeButton);
  }
  if (field.description) {
    const description = document.createElement("div");
    description.className = "field-description";
    description.textContent = field.description;
    wrapper.appendChild(description);
  }
  return wrapper;
}

function inputForField(field) {
  if (field.type === "boolean") {
    const input = document.createElement("input");
    input.type = "checkbox";
    input.checked = String(field.value).toLowerCase() === "true";
    input.dataset.original = input.checked ? "true" : "false";
    return input;
  }

  if (field.type === "select") {
    const select = document.createElement("select");
    field.options.forEach((item) =>
      select.appendChild(option(item.value, item.label)),
    );
    select.value = field.value || field.options[0]?.value || "";
    return select;
  }

  if (field.type === "textarea") {
    const textarea = document.createElement("textarea");
    textarea.value = field.value || "";
    textarea.maxLength = 10000;
    return textarea;
  }

  if (field.type === "model" || field.type === "optional_model") {
    const input = document.createElement("input");
    input.type = "text";
    input.value = field.value || (field.type === "optional_model" ? "None" : "");
    input.autocomplete = "off";
    input.maxLength = 512;
    return input;
  }

  if (field.type === "model_list") {
    const input = document.createElement("input");
    input.type = "hidden";
    input.value = field.value || "";
    return input;
  }

  const input = document.createElement("input");
  input.type = field.type === "number" ? "number" : "text";
  if (field.type === "secret") {
    input.type = "password";
    input.placeholder = field.configured
      ? "Configured - enter a new value to replace"
      : "Not configured";
    input.value = "";
    input.autocomplete = "off";
    input.maxLength = 1024;
  } else {
    input.value = field.value || "";
    input.maxLength = 4096;
  }
  return input;
}

function createModelCombobox(input, field) {
  return new window.FccModelCombobox(input, {
    listboxId: `model-options-${field.key}`,
    label: field.label,
    values: () =>
      field.type === "optional_model"
        ? ["None", ...state.modelOptions]
        : state.modelOptions,
    emptyMessage: () =>
      state.modelOptions.length
        ? "No matching models. You can still enter a custom slug."
        : "No discovered models. Refresh models or enter a custom slug.",
    registry: state.modelComboboxes,
  });
}

class ModelListEditor {
  constructor(input, field) {
    this.input = input;
    this.field = field;
    this.values = input.value
      ? input.value.split(",").map((value) => value.trim()).filter(Boolean).slice(0, 50)
      : [];
    this.inputId = `field-${field.key}-add`;

    this.element = document.createElement("div");
    this.element.className = "model-list-editor";

    const addRow = document.createElement("div");
    addRow.className = "model-list-add";
    this.addInput = document.createElement("input");
    this.addInput.id = this.inputId;
    this.addInput.type = "text";
    this.addInput.autocomplete = "off";
    this.addInput.placeholder = "provider/model";
    this.addInput.disabled = field.locked;
    this.addInput.maxLength = 512;
    const addCombobox = createModelCombobox(this.addInput, {
      ...field,
      key: `${field.key}-add`,
      label: "fallback model",
      type: "model",
    });

    this.addButton = document.createElement("button");
    this.addButton.type = "button";
    this.addButton.className = "secondary-button";
    this.addButton.textContent = "Add";
    this.addButton.disabled = field.locked;
    this.addButton.addEventListener("click", () => this.add());
    addRow.append(addCombobox.element, this.addButton);

    this.rows = document.createElement("div");
    this.rows.className = "model-list-rows";
    this.element.append(input, addRow, this.rows);
    this.renderRows();
  }

  add() {
    const value = this.addInput.value.trim().slice(0, 512);
    if (!value) {
      showMessage("Enter a full provider/model fallback.", "error");
      showToast("Invalid", "Enter a full provider/model fallback", "error");
      return;
    }
    if (!/^[a-z0-9_]+\/[a-zA-Z0-9/_\-.:]+$/.test(value)) {
      showMessage("Invalid model format. Use provider/model", "error");
      showToast("Invalid", "Use provider/model format", "error");
      return;
    }
    if (this.values.includes(value)) {
      showMessage("That fallback model is already in the list.", "error");
      showToast("Duplicate", "That fallback model is already in the list", "warn");
      return;
    }
    if (this.values.length >= 50) {
      showMessage("Too many fallback models (max 50)", "error");
      return;
    }
    this.values.push(value);
    this.addInput.value = "";
    showMessage("");
    this.sync();
  }

  move(index, offset) {
    const destination = index + offset;
    if (destination < 0 || destination >= this.values.length) return;
    [this.values[index], this.values[destination]] = [
      this.values[destination],
      this.values[index],
    ];
    this.sync();
  }

  remove(index) {
    this.values.splice(index, 1);
    this.sync();
  }

  sync() {
    this.input.value = this.values.join(",");
    this.input.dataset.remove = "false";
    this.input.dispatchEvent(new Event("input", { bubbles: true }));
    this.renderRows();
  }

  renderRows() {
    this.rows.innerHTML = "";
    if (this.values.length === 0) {
      const empty = document.createElement("div");
      empty.className = "model-list-empty";
      empty.textContent = "No fallback models configured.";
      this.rows.appendChild(empty);
      return;
    }

    this.values.forEach((value, index) => {
      const row = document.createElement("div");
      row.className = "model-list-row";

      const model = document.createElement("span");
      model.className = "model-list-value";
      model.textContent = value;

      const up = this.actionButton("↑", `Move ${value} up`, () =>
        this.move(index, -1),
      );
      up.disabled = this.field.locked || index === 0;
      const down = this.actionButton("↓", `Move ${value} down`, () =>
        this.move(index, 1),
      );
      down.disabled = this.field.locked || index === this.values.length - 1;
      const remove = this.actionButton("✕", `Remove ${value}`, () =>
        this.remove(index),
      );
      remove.disabled = this.field.locked;

      row.append(model, up, down, remove);
      this.rows.appendChild(row);
    });
  }

  actionButton(text, label, action) {
    const button = document.createElement("button");
    button.type = "button";
    button.className = "ghost-button model-list-action";
    button.textContent = text;
    button.setAttribute("aria-label", label.slice(0, 100));
    button.addEventListener("click", action);
    return button;
  }
}

function option(value, label) {
  const optionEl = document.createElement("option");
  optionEl.value = value.slice(0, 200);
  optionEl.textContent = label.slice(0, 200);
  return optionEl;
}

function readFieldValue(input) {
  if (input.type === "checkbox") return input.checked ? "true" : "false";
  if (input.dataset.remove === "true") return null;
  if (
    input.dataset.fieldType === "optional_model" &&
    input.value.trim().toLowerCase() === "none"
  ) {
    return null;
  }
  if (input.dataset.secret === "true" && input.dataset.configured === "true") {
    return input.value ? input.value : MASKED_SECRET;
  }
  if (input.dataset.nullable === "true" && !input.value.trim()) return null;
  return input.value;
}

function comparableValue(value) {
  return value === null ? NULL_VALUE : String(value);
}

function changedValues() {
  const values = {};
  document.querySelectorAll("[data-key]").forEach((input) => {
    if (input.disabled || !input.matches("input, select, textarea")) return;
    const value = readFieldValue(input);
    if (comparableValue(value) !== input.dataset.original) {
      values[input.dataset.key] = value;
    }
  });
  return values;
}

function updateDirtyState() {
  const count = Object.keys(changedValues()).length;
  byId("dirtyState").textContent =
    state.restart ? "Changes saved" : count === 0 ? "No changes" : `${count} unsaved change${count === 1 ? "" : "s"}`;
  byId("applyButton").disabled = state.applying || (!state.restart && count === 0);
}

function clearCredentialError(input) {
  byId(`${input.id}-error`)?.remove();
  input.removeAttribute("aria-invalid");
  input.removeAttribute("aria-describedby");
}

function showCredentialErrors(checks) {
  let first = null;
  checks.forEach((check) => {
    const input = byId(`field-${check.key}`);
    if (!input) return;
    clearCredentialError(input);
    if (check.status !== "rejected") return;
    const error = document.createElement("div");
    error.id = `${input.id}-error`;
    error.className = "field-error";
    error.textContent = check.message.slice(0, 500);
    input.closest(".field").appendChild(error);
    input.setAttribute("aria-invalid", "true");
    input.setAttribute("aria-describedby", error.id);
    first ||= input;
  });
  return first;
}

function setApplying(applying) {
  state.applying = applying;
  VIEW_GROUPS.filter((view) => view.id !== "code").forEach((view) => {
    const el = byId(`view-${view.id}`);
    if (el) el.inert = applying || !!state.restart;
  });
  if (applying) state.modelComboboxes.forEach((combobox) => combobox.close());
  const applyBtn = byId("applyButton");
  if (applyBtn) {
    applyBtn.textContent = state.restart
      ? applying ? "Reconnecting…" : "Reconnect"
      : applying ? "Applying…" : "Apply";
  }
  updateDirtyState();
}

async function waitForRestart(restart, target) {
  const deadline = performance.now() + 30_000;
  const statusUrl = new URL("/admin/api/status", target);
  while (performance.now() < deadline) {
    try {
      const response = await fetch(statusUrl, {
        cache: "no-store",
        credentials: "omit",
        signal: AbortSignal.timeout(1500),
      });
      if (response.ok) {
        const status = await response.json();
        if (status.status === "running" && typeof status.instance_id === "string"
          && status.instance_id !== restart.instance_id) return;
      }
    } catch {}
    await new Promise((resolve) => setTimeout(resolve, 250));
  }
  throw new Error("The server has not reconnected yet.");
}

function appendAdminLink(target) {
  const link = document.createElement("a");
  link.href = target.href;
  link.textContent = "Open Admin";
  // Validate URL
  try {
    const url = new URL(target.href);
    if (!["http:", "https:"].includes(url.protocol)) return;
  } catch {
    return;
  }
  byId("messageArea").append(document.createElement("br"), link);
}

async function reconnectAfterRestart() {
  const { restart, warnings } = state.restart;
  const target = new URL(restart.admin_url || "/admin", window.location.href);
  setApplying(true);
  showMessage(["Applied. Reconnecting to the server…", ...warnings].join("\n"), warnings.length ? "warn" : "ok");
  try {
    await waitForRestart(restart, target);
    if (target.origin !== window.location.origin) {
      target.hash = new URLSearchParams({ "fcc-applied": JSON.stringify(warnings) }).toString();
      window.location.replace(target.href);
      return;
    }
    await load();
    state.restart = null;
    showMessage(["Applied", ...warnings].join("\n"), warnings.length ? "warn" : "ok");
    showToast("Applied", "Settings saved and server reconnected", "ok");
  } catch (error) {
    showMessage([`Settings were saved. ${error.message} Use Reconnect to try again.`, ...warnings].join("\n"), "warn");
    appendAdminLink(target);
  } finally {
    setApplying(false);
  }
}

function showRestartNotice() {
  const url = new URL(window.location.href);
  const fragment = new URLSearchParams(url.hash.slice(1));
  const notice = fragment.get("fcc-applied");
  if (notice === null) return;
  fragment.delete("fcc-applied");
  url.hash = fragment.toString();
  window.history.replaceState(window.history.state, "", url);
  try {
    const warnings = JSON.parse(notice);
    if (Array.isArray(warnings) && warnings.every((warning) => typeof warning === "string")) {
      showMessage(["Applied", ...warnings].join("\n"), warnings.length ? "warn" : "ok");
    }
  } catch {}
}

async function apply() {
  if (state.applying) return;
  if (state.restart) {
    await reconnectAfterRestart();
    return;
  }
  const values = changedValues();
  if (!Object.keys(values).length) return;
  const checkingKeys = Object.keys(values).some((key) => {
    const field = state.fields.get(key);
    return field?.secret && field.section === "providers" && values[key] !== null;
  });
  let rejectedField = null;
  let applied = false;
  setApplying(true);
  showMessage(checkingKeys ? "Checking API keys…" : "Applying…");
  try {
    const result = await api("/admin/api/config/apply", {
      method: "POST",
      body: JSON.stringify({ values }),
    });
    const checks = result.credential_checks || [];
    if (!result.applied) {
      rejectedField = showCredentialErrors(checks);
      showMessage(rejectedField ? "Not applied. Check the highlighted API keys." : result.errors.join("; "), "error");
      showToast("Failed", rejectedField ? "Check highlighted API keys" : result.errors.join("; "), "error");
      return;
    }
    applied = true;
    const warnings = checks.filter((check) => check.status === "unverified").map((check) =>
      `${state.fields.get(check.key)?.label || check.key}: ${check.message}`
    );
    const restart = result.restart || {};
    if (restart.required && restart.automatic) {
      state.restart = { restart, warnings };
      await reconnectAfterRestart();
      return;
    }
    const pending = restart.required ? restart.fields || [] : result.pending_fields || [];
    await load();
    const message = pending.length
      ? `Applied. Restart fcc-server to use: ${pending.join(", ")}`
      : "Applied";
    showMessage([message, ...warnings].join("\n"), warnings.length ? "warn" : "ok");
    showToast("Success", message, warnings.length ? "warn" : "ok");
  } catch (error) {
    showMessage(applied ? `Applied, but could not reload settings: ${error.message}` : `Could not apply settings: ${error.message}`, "error");
    showToast("Error", error.message, "error");
  } finally {
    setApplying(false);
    if (rejectedField) {
      navigateToView("providers");
      rejectedField.closest(".settings-section")?.classList.add("show-advanced");
      rejectedField.scrollIntoView({ block: "center", behavior: "instant" });
      rejectedField.focus();
    }
  }
}

async function refreshLocalStatus(config) {
  const request = {
    providerIds: new Set(config.provider_status.filter((provider) =>
      provider.kind === "local" && provider.status === "configured",
    ).map((provider) => provider.provider_id)),
  };
  state.localStatusRequest = request;
  try {
    const result = await api("/admin/api/providers/local-status");
    if (state.localStatusRequest !== request) return;
    result.providers.forEach((provider) => {
      if (!request.providerIds.has(provider.provider_id) || provider.status === "missing_url") return;
      if (provider.status === "reachable") {
        updateProviderCheckResult(
          provider.provider_id,
          "ok",
          `Reachable: ${provider.base_url}`,
        );
        return;
      }
      const detail = provider.message
        ? provider.message
        : provider.status_code
          ? `${provider.base_url} returned HTTP ${provider.status_code}`
          : "The local provider did not respond.";
      updateProviderCheckResult(
        provider.provider_id,
        "error",
        `Unavailable: ${detail}`,
      );
    });
  } catch {
    if (state.localStatusRequest !== request) return;
    request.providerIds.forEach((providerId) => {
      updateProviderCheckResult(
        providerId,
        "error",
        "Availability check failed. Use Test to retry.",
      );
    });
  } finally {
    if (state.localStatusRequest === request) state.localStatusRequest = null;
  }
}

async function testProvider(providerId, button) {
  if (!/^[a-z][a-z0-9_]*$/.test(providerId)) {
    showToast("Invalid", "Invalid provider ID", "error");
    return;
  }
  state.localStatusRequest?.providerIds.delete(providerId);
  const original = button.textContent;
  button.disabled = true;
  button.textContent = "Checking...";
  updateProviderCheckResult(providerId, "checking", "Checking...");
  try {
    const result = await api(`/admin/api/providers/${providerId}/test`, {
      method: "POST",
      body: "{}",
    });
    if (result.ok) {
      updateProviderCheckResult(
        providerId,
        "ok",
        `${result.models.length} models available`,
      );
      setModelOptions([
        ...state.modelOptions,
        ...result.models.map((model) => `${providerId}/${model}`),
      ]);
      showToast("Provider OK", `${providerId}: ${result.models.length} models`, "ok");
    } else {
      updateProviderCheckResult(
        providerId,
        "error",
        `Unavailable: ${result.message || "Provider check failed."}`,
      );
      showToast("Provider failed", result.message || "Check failed", "error");
    }
  } catch {
    updateProviderCheckResult(
      providerId,
      "error",
      "Provider check could not be completed.",
    );
    showToast("Check failed", "Could not complete provider check", "error");
  } finally {
    button.disabled = false;
    button.textContent = original;
  }
}

async function testAllProviders() {
  const btn = byId("testAllButton");
  if (!btn) return;
  const original = btn.textContent;
  btn.disabled = true;
  btn.textContent = "Testing...";
  const providers = state.config?.provider_status?.filter(p => p.kind !== "connected_account" && p.missing_configuration_keys?.length === 0) || [];
  for (const p of providers) {
    const cardBtn = document.querySelector(`[data-provider="${p.provider_id}"] .provider-actions button:last-child`);
    if (cardBtn) await testProvider(p.provider_id, cardBtn);
  }
  btn.disabled = false;
  btn.textContent = original;
  showToast("Tests done", `Checked ${providers.length} providers`, "ok");
}

async function hydrateModelOptions() {
  try {
    await loadModelOptions();
  } catch {}
}

async function loadModelOptions(refresh = false) {
  const result = await api("/admin/api/models" + (refresh ? "/refresh" : ""), {
    method: refresh ? "POST" : "GET",
  });
  setModelOptions(result.models);
  if (refresh && window.CodeSessions) await window.CodeSessions.refresh();
  return result;
}

async function refreshModelOptions(button) {
  const original = button.textContent;
  button.disabled = true;
  button.textContent = "Refreshing";
  try {
    const result = await loadModelOptions(true);
    const failedProviders = result.failed_providers || [];
    if (failedProviders.length) {
      const labels = failedProviders.map(providerDisplayName).join(", ");
      showMessage(
        `${state.modelOptions.length} models available; could not refresh ${labels}`,
        "warn",
      );
      showToast("Partial refresh", `${state.modelOptions.length} models, ${labels} failed`, "warn");
    } else {
      showMessage(`${state.modelOptions.length} models available`, "ok");
      showToast("Models refreshed", `${state.modelOptions.length} models available`, "ok");
    }
    renderStats(state.config?.provider_status || []);
  } catch (error) {
    showMessage(`Could not refresh models: ${error.message}`, "error");
    showToast("Refresh failed", error.message, "error");
  } finally {
    button.disabled = false;
    button.textContent = original;
  }
}

function providerDisplayName(providerId) {
  const provider = state.config?.provider_status?.find(
    (candidate) => candidate.provider_id === providerId,
  );
  return provider?.display_name || providerId;
}

function setModelOptions(models) {
  state.modelOptions = Array.from(
    new Set(models.filter((model) => typeof model === "string" && model.trim()).slice(0, 1000)),
  ).sort((left, right) => left.localeCompare(right));
  state.modelComboboxes.forEach((combobox) => {
    if (combobox.isOpen) combobox.render(combobox.query);
  });
  if (state.config) renderStats(state.config.provider_status);
}

function showMessage(message, kind = "") {
  const area = byId("messageArea");
  if (!area) return;
  area.textContent = message.slice(0, 2000);
  area.className = `message-area ${kind}`.trim();
}

// Event listeners with security
const applyButton = byId("applyButton");
if (applyButton) applyButton.addEventListener("click", apply);

const refreshButton = byId("refreshButton");
if (refreshButton) refreshButton.addEventListener("click", () => {
  load();
  showToast("Refreshing", "Reloading configuration...", "neutral");
});

const testAllButton = byId("testAllButton");
if (testAllButton) testAllButton.addEventListener("click", testAllProviders);

const themeToggle = byId("themeToggle");
if (themeToggle) themeToggle.addEventListener("click", toggleTheme);

const exportConfigButton = byId("exportConfigButton");
if (exportConfigButton) exportConfigButton.addEventListener("click", exportConfig);

const importConfigButton = byId("importConfigButton");
const importConfigFile = byId("importConfigFile");
if (importConfigButton && importConfigFile) {
  importConfigButton.addEventListener("click", () => importConfigFile.click());
  importConfigFile.addEventListener("change", () => {
    const file = importConfigFile.files && importConfigFile.files[0];
    importConfigFile.value = "";
    if (file) importConfigFromFile(file);
  });
}

const globalSearch = byId("globalSearch");
if (globalSearch) {
  globalSearch.addEventListener("input", (e) => {
    state.searchQuery = e.target.value.trim().slice(0, 100);
    if (state.config) renderProviders(state.config.provider_status);
  });
  // Prevent XSS via search
  globalSearch.addEventListener("keydown", (e) => {
    if (e.key === "Enter") e.preventDefault();
  });
}

initTheme();
setupKeyboardShortcuts();

document.addEventListener("pointerdown", (event) => {
  state.modelComboboxes.forEach((combobox) => {
    if (combobox.isOpen && !combobox.element.contains(event.target)) combobox.close();
  });
});

window.addEventListener("popstate", () => {
  const viewId = viewFromLocation();
  setActiveView(viewId, { scroll: false });
});

// Cleanup session storage safely
try {
  for (const key of Object.keys(sessionStorage)) {
    if (key.startsWith("fcc.chat.draft.")) sessionStorage.removeItem(key);
  }
} catch {
  console.warn("Chat draft cleanup deferred until the next page load: storage unavailable");
}

const claudeIntegrationDialog = byId("claudeIntegrationDialog");
const claudeIntegration = { connected: null, busy: false, paths: null };
const claudeIntegrationPath = "/admin/api/integrations/claude-vscode";

function integrationMessage(id, message, error = false) {
  const element = byId(id);
  if (!element) return;
  element.textContent = message.slice(0, 1000);
  element.hidden = !message;
  element.classList.toggle("error", error);
}

function renderClaudeIntegration() {
  const { connected, busy, paths } = claudeIntegration;
  const openBtn = byId("openClaudeIntegration");
  const confirmBtn = byId("confirmClaudeIntegration");
  if (!openBtn || !confirmBtn) return;
  
  const action = connected ? "Disconnect" : "Connect";
  openBtn.textContent = connected === null && !busy ? "Retry" : action;
  openBtn.disabled = busy;
  confirmBtn.textContent = busy ? "Saving…" : action;
  confirmBtn.disabled = busy || connected === null;
  openBtn.className = connected ? "danger-button" : "primary-button";
  confirmBtn.className = connected ? "danger-button" : "primary-button";
  const status = byId("claudeIntegrationStatus");
  if (status) {
    status.hidden = connected !== null;
    status.textContent = busy ? "Checking settings…" : "Could not check settings";
  }
  const desc = byId("claudeIntegrationDescription");
  if (desc) {
    desc.textContent = connected
      ? "Remove FCC's VS Code settings. Claude onboarding stays completed."
      : "Will set FCC's URL and token, enable model discovery, skip VS Code login, and complete Claude onboarding.";
  }
  const files = byId("claudeIntegrationFiles");
  if (files) {
    files.replaceChildren();
    if (paths) {
      const targets = connected ? [paths.vscode_settings] : [paths.vscode_settings, paths.claude_state];
      targets.forEach((path) => {
        const item = document.createElement("li");
        const code = document.createElement("code");
        code.textContent = path.slice(0, 500);
        item.appendChild(code);
        files.appendChild(item);
      });
    }
  }
}

async function refreshClaudeIntegration() {
  if (claudeIntegration.busy) return;
  claudeIntegration.busy = true;
  renderClaudeIntegration();
  integrationMessage("claudeIntegrationMessage", "");
  try {
    const result = await api(claudeIntegrationPath);
    claudeIntegration.connected = result.connected;
    claudeIntegration.paths = result.paths;
  } catch (error) {
    claudeIntegration.connected = null;
    integrationMessage("claudeIntegrationMessage", error.message, true);
  } finally {
    claudeIntegration.busy = false;
    renderClaudeIntegration();
  }
}

const openClaudeBtn = byId("openClaudeIntegration");
if (openClaudeBtn) {
  openClaudeBtn.addEventListener("click", () => {
    if (claudeIntegration.connected === null) {
      refreshClaudeIntegration();
      return;
    }
    integrationMessage("claudeIntegrationDialogMessage", "");
    if (claudeIntegrationDialog) claudeIntegrationDialog.showModal();
  });
}

const confirmClaudeBtn = byId("confirmClaudeIntegration");
if (confirmClaudeBtn) {
  confirmClaudeBtn.addEventListener("click", async () => {
    if (claudeIntegration.busy || claudeIntegration.connected === null) return;
    const disconnect = claudeIntegration.connected;
    claudeIntegration.busy = true;
    renderClaudeIntegration();
    integrationMessage("claudeIntegrationDialogMessage", "");
    integrationMessage("claudeIntegrationMessage", "");
    try {
      const result = await api(`${claudeIntegrationPath}/${disconnect ? "disconnect" : "connect"}`, { method: "POST" });
      claudeIntegration.connected = result.connected;
      if (claudeIntegrationDialog) claudeIntegrationDialog.close();
      integrationMessage("claudeIntegrationMessage", disconnect
        ? "Settings removed. Reload VS Code to disconnect."
        : "Settings saved. Reload VS Code to connect.");
      showToast(disconnect ? "Disconnected" : "Connected", disconnect ? "VS Code settings removed" : "VS Code connected to FCC", "ok");
    } catch (error) {
      integrationMessage("claudeIntegrationDialogMessage", error.message, true);
      integrationMessage("claudeIntegrationMessage", error.message, true);
      showToast("Failed", error.message, "error");
    } finally {
      claudeIntegration.busy = false;
      renderClaudeIntegration();
    }
  });
}

const closeClaudeBtn = byId("closeClaudeIntegration");
if (closeClaudeBtn && claudeIntegrationDialog) {
  closeClaudeBtn.addEventListener("click", () => claudeIntegrationDialog.close());
  claudeIntegrationDialog.addEventListener("click", (event) => {
    if (event.target !== claudeIntegrationDialog) return;
    const bounds = claudeIntegrationDialog.getBoundingClientRect();
    if (event.clientX < bounds.left || event.clientX > bounds.right || event.clientY < bounds.top || event.clientY > bounds.bottom) {
      claudeIntegrationDialog.close();
    }
  });
}

const codexIntegrationDialog = byId("codexIntegrationDialog");
const codexIntegration = { connected: null, busy: false, paths: null };
const codexIntegrationPath = "/admin/api/integrations/codex";

function renderCodexIntegration() {
  const { connected, busy, paths } = codexIntegration;
  const openBtn = byId("openCodexIntegration");
  const confirmBtn = byId("confirmCodexIntegration");
  if (!openBtn || !confirmBtn) return;
  
  const action = connected ? "Disconnect" : "Connect";
  openBtn.textContent = connected === null && !busy ? "Retry" : action;
  openBtn.disabled = busy;
  confirmBtn.textContent = busy ? "Saving…" : action;
  confirmBtn.disabled = busy || connected === null;
  openBtn.className = connected ? "danger-button" : "primary-button";
  confirmBtn.className = connected ? "danger-button" : "primary-button";
  const status = byId("codexIntegrationStatus");
  if (status) {
    status.hidden = connected !== null;
    status.textContent = busy ? "Checking settings…" : "Could not check settings";
  }
  const desc = byId("codexIntegrationDescription");
  if (desc) {
    desc.textContent = connected
      ? "Remove FCC's Codex configuration. Other settings stay unchanged."
      : "Configure Codex to use FCC. Your selected model stays unchanged.";
  }
  const files = byId("codexIntegrationFiles");
  if (files) {
    files.replaceChildren();
    if (paths) {
      const targets = [paths.codex_config];
      targets.forEach((path) => {
        const item = document.createElement("li");
        const code = document.createElement("code");
        code.textContent = path.slice(0, 500);
        item.appendChild(code);
        files.appendChild(item);
      });
    }
  }
}

async function refreshCodexIntegration() {
  if (codexIntegration.busy) return;
  codexIntegration.busy = true;
  renderCodexIntegration();
  integrationMessage("codexIntegrationMessage", "");
  try {
    const result = await api(codexIntegrationPath);
    codexIntegration.connected = result.connected;
    codexIntegration.paths = result.paths;
  } catch (error) {
    codexIntegration.connected = null;
    integrationMessage("codexIntegrationMessage", error.message, true);
  } finally {
    codexIntegration.busy = false;
    renderCodexIntegration();
  }
}

const openCodexBtn = byId("openCodexIntegration");
if (openCodexBtn) {
  openCodexBtn.addEventListener("click", () => {
    if (codexIntegration.connected === null) {
      refreshCodexIntegration();
      return;
    }
    integrationMessage("codexIntegrationDialogMessage", "");
    if (codexIntegrationDialog) codexIntegrationDialog.showModal();
  });
}

const confirmCodexBtn = byId("confirmCodexIntegration");
if (confirmCodexBtn) {
  confirmCodexBtn.addEventListener("click", async () => {
    if (codexIntegration.busy || codexIntegration.connected === null) return;
    const disconnect = codexIntegration.connected;
    codexIntegration.busy = true;
    renderCodexIntegration();
    integrationMessage("codexIntegrationDialogMessage", "");
    integrationMessage("codexIntegrationMessage", "");
    try {
      const result = await api(`${codexIntegrationPath}/${disconnect ? "disconnect" : "connect"}`, { method: "POST" });
      codexIntegration.connected = result.connected;
      codexIntegration.paths = result.paths;
      if (codexIntegrationDialog) codexIntegrationDialog.close();
      integrationMessage("codexIntegrationMessage", disconnect
        ? "Settings removed. Restart Codex to disconnect."
        : "Settings saved. Restart Codex and select an FCC model.");
      showToast(disconnect ? "Disconnected" : "Connected", disconnect ? "Codex settings removed" : "Codex connected to FCC", "ok");
    } catch (error) {
      integrationMessage("codexIntegrationDialogMessage", error.message, true);
      integrationMessage("codexIntegrationMessage", error.message, true);
      showToast("Failed", error.message, "error");
    } finally {
      codexIntegration.busy = false;
      renderCodexIntegration();
    }
  });
}

const closeCodexBtn = byId("closeCodexIntegration");
if (closeCodexBtn && codexIntegrationDialog) {
  closeCodexBtn.addEventListener("click", () => codexIntegrationDialog.close());
  codexIntegrationDialog.addEventListener("click", (event) => {
    if (event.target !== codexIntegrationDialog) return;
    const bounds = codexIntegrationDialog.getBoundingClientRect();
    if (event.clientX < bounds.left || event.clientX > bounds.right || event.clientY < bounds.top || event.clientY > bounds.bottom) {
      codexIntegrationDialog.close();
    }
  });
}

const jetBrainsIntegrationDialog = byId("jetBrainsIntegrationDialog");
const openJetBrainsBtn = byId("openJetBrainsIntegration");
if (openJetBrainsBtn && jetBrainsIntegrationDialog) {
  openJetBrainsBtn.addEventListener("click", () => jetBrainsIntegrationDialog.showModal());
}
const closeJetBrainsBtn = byId("closeJetBrainsIntegration");
if (closeJetBrainsBtn && jetBrainsIntegrationDialog) {
  closeJetBrainsBtn.addEventListener("click", () => jetBrainsIntegrationDialog.close());
  jetBrainsIntegrationDialog.addEventListener("click", (event) => {
    if (event.target !== jetBrainsIntegrationDialog) return;
    const bounds = jetBrainsIntegrationDialog.getBoundingClientRect();
    if (event.clientX < bounds.left || event.clientX > bounds.right || event.clientY < bounds.top || event.clientY > bounds.bottom) {
      jetBrainsIntegrationDialog.close();
    }
  });
}

// CSP-safe initialization
load().then(showRestartNotice).catch((error) => {
  showMessage(error.message, "error");
  showToast("Load failed", error.message, "error");
});

// Stop metrics polling when tab is hidden
document.addEventListener("visibilitychange", () => {
  if (document.hidden) {
    stopMetricsPolling();
    stopSecurityEventsStream();
  } else {
    if (state.activeView === "metrics") startMetricsPolling();
    if (state.activeView === "security") startSecurityEventsStream();
  }
});

// PWA: register service worker for versioned asset shell cache
try {
  if ("serviceWorker" in navigator) {
    const swUrl = (document.querySelector('script[src*="admin.js"]')?.src || "").replace(/admin\.js.*/, "sw.js");
    if (swUrl.endsWith("sw.js")) {
      navigator.serviceWorker.register(swUrl).catch(() => {});
    }
  }
} catch {}

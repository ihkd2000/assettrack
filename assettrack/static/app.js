/* AssetTrack - browser client. No frontend dependencies. */
const root = document.getElementById("root");
let me = null,
  view = "dashboard",
  assets = [],
  users = [],
  assignments = [],
  events = [],
  transfers = [],
  stats = {},
  search = "",
  modal = null,
  flash = "";
let detail = null,
  detailId = 0,
  prevView = "assets",
  tstatus = "all",
  tsearch = ""; // asset details page + transfer filters
const escapeHTML = (v) =>
  String(v ?? "").replace(
    /[&<>"']/g,
    (c) =>
      ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[
        c
      ],
  );
const date = (v) => (v ? new Date(v).toLocaleString() : "—");
const manager = () => me && ["admin", "manager"].includes(me.role);
// Transfers needing attention: managers track all pending ones, employees only those awaiting their answer.
const actionableTransfers = () =>
  transfers.filter(
    (t) => t.status === "pending" && (manager() || t.to_employee_id === me.id),
  );
async function api(path, method = "GET", data) {
  const opts = { method, credentials: "same-origin", headers: {} };
  if (data !== undefined) {
    opts.headers["Content-Type"] = "application/json";
    opts.body = JSON.stringify(data);
  }
  let r = await fetch("/api" + path, opts),
    payload = await r.json();
  if (r.status === 401 && path !== "/login") {
    me = null;
    modal = null;
    view = "dashboard";
  } // session ended (expired or account deactivated)
  if (!r.ok) throw Error(payload.error || "Request failed");
  return payload;
}
function notify(msg, bad = false) {
  flash = `<div class="notice ${bad ? "error" : ""}" role="status">${escapeHTML(msg)}</div>`;
  render();
}
async function load() {
  try {
    me = await api("/me");
    const endpoints = [
      api("/dashboard"),
      api("/assets"),
      api("/assignments"),
      api("/events"),
      api("/transfers"),
    ];
    if (manager()) endpoints.push(api("/users"));
    else users = [];
    const values = await Promise.all(endpoints);
    [stats, assets, assignments, events, transfers] = values;
    if (manager()) users = values[5];
    if (view === "asset" && detailId) {
      try {
        detail = await api(`/assets/${detailId}`);
      } catch (err) {
        if (!me) throw err;
        view = prevView;
        detail = null;
        detailId = 0;
      } // no longer accessible (e.g. transfer rejected)
    }
  } catch (e) {
    if (e.message.includes("Sign in") || e.message.includes("Session"))
      me = null;
    else throw e;
  }
  render();
}
function layout(content) {
  return `<div class="shell"><header class="top"><div><div class="brand">▣ AssetTrack</div><div class="muted">Equipment & assignment management</div></div><div>${escapeHTML(me.full_name)} <span class="pill">${escapeHTML(me.role)}</span> <button class="secondary" data-act="logout">Sign out</button></div></header><nav class="nav">${[
    "dashboard",
    "assets",
    "assignments",
    "transfers",
    "history",
    ...(manager() ? ["users"] : []),
  ]
    .map((x) => {
      const n = x === "transfers" ? actionableTransfers().length : 0;
      return `<button data-view="${x}" class="${view === x ? "active" : ""}">${x[0].toUpperCase() + x.slice(1)}${n ? ` <span class="badge">${n}</span>` : ""}</button>`;
    })
    .join("")}</nav>${flash}${content}</div>${modal ? modalHTML() : ""}`;
}
function dashboard() {
  const incoming = me.role === "employee" ? actionableTransfers().length : 0;
  return `${incoming ? `<div class="notice">You have ${incoming} asset transfer${incoming > 1 ? "s" : ""} awaiting your response. <button class="secondary" data-view="transfers">Review</button></div>` : ""}<section class="stats">${Object.entries(
    stats,
  )
    .map(
      ([k, v]) =>
        `<div class="stat"><span class="muted">${escapeHTML(k.toUpperCase())}</span><strong>${v}</strong></div>`,
    )
    .join(
      "",
    )}</section><section class="panel"><h2>Recent activity</h2>${eventTable(events.slice(0, 8))}</section>`;
}
function assetActions(a) {
  return `<button class="secondary" data-act="edit" data-id="${a.id}">Edit</button>${a.status === "available" ? `<button data-act="assign" data-id="${a.id}">Assign</button>` : ""}${a.status === "assigned" && !a.pending_transfer_id ? `<button class="secondary" data-act="transfer" data-id="${a.id}">Transfer</button>` : ""}${a.status === "assigned" ? (a.pending_transfer_id ? `<button class="secondary" disabled title="Cancel the pending transfer before returning this asset">Return</button>` : `<button class="secondary" data-act="return" data-id="${a.id}">Return</button>`) : ""}`;
}
// Asset details are open to managers; employees can open assets they hold or are being offered.
const canOpenAsset = (id) =>
  manager() ||
  assets.some((a) => a.id === id) ||
  transfers.some(
    (t) =>
      t.asset_id === id && t.status === "pending" && t.to_employee_id === me.id,
  );
const assetLink = (id, code, name) => {
  const label = `<span class="code">${escapeHTML(code)}</span>${name ? ` — ${escapeHTML(name)}` : ""}`;
  return canOpenAsset(id)
    ? `<button class="link" data-act="openasset" data-id="${id}" title="Open asset details">${label}</button>`
    : label;
};
function assetTable() {
  let visible = assets.filter((a) =>
    [a.code, a.name, a.category, a.assigned_to, a.status]
      .join(" ")
      .toLowerCase()
      .includes(search.toLowerCase()),
  );
  return `<section class="panel"><div class="toolbar"><h2>Assets</h2>${manager() ? '<button data-act="newasset">+ Register asset</button>' : ""}</div><div class="toolbar"><input id="search" placeholder="Search assets…" value="${escapeHTML(search)}" aria-label="Search assets"></div><div class="tablewrap"><table><thead><tr><th>Code</th><th>Asset</th><th>Category</th><th>Condition</th><th>Status</th><th>Assigned to</th>${manager() ? "<th>Actions</th>" : ""}</tr></thead><tbody>${visible.map((a) => `<tr><td class="code">${assetLink(a.id, a.code)}</td><td>${escapeHTML(a.name)}</td><td>${escapeHTML(a.category)}</td><td>${escapeHTML(a.condition)}</td><td><span class="pill ${a.status}">${a.status}</span>${a.pending_transfer_id ? `<div class="muted">Transfer pending → ${escapeHTML(a.pending_transfer_to)}</div>` : ""}</td><td>${escapeHTML(a.assigned_to || "—")}</td>${manager() ? `<td class="actions">${assetActions(a)}</td>` : ""}</tr>`).join("")}</tbody></table>${visible.length ? "" : '<div class="empty">No assets found.</div>'}</div></section>`;
}
// How an assignment started and ended, e.g. "Received by transfer from Alex" / "Transferred to Dana".
function assignmentNotes(a) {
  const lines = [];
  if (a.transferred_from)
    lines.push(`Received by transfer from ${escapeHTML(a.transferred_from)}`);
  if (a.status === "active")
    lines.push(
      a.pending_transfer_to
        ? `Transfer to ${escapeHTML(a.pending_transfer_to)} pending`
        : "Currently assigned",
    );
  if (a.status === "transferred")
    lines.push(
      `Transferred to ${escapeHTML(a.transferred_to)} on ${date(a.returned_at)}`,
    );
  if (a.status === "returned") lines.push("Returned to inventory");
  return `<span class="pill ${a.status}">${a.status}</span>${lines.map((l) => `<div class="muted">${l}</div>`).join("")}`;
}
function assignmentsTable() {
  return `<section class="panel"><h2>Assignment history</h2>${assignmentTable(assignments)}</section>`;
}
function assignmentTable(list, showAsset = true) {
  return `<div class="tablewrap"><table><thead><tr>${showAsset ? "<th>Asset</th>" : ""}<th>Employee</th><th>Assigned</th><th>Returned</th><th>Return condition</th><th>Notes</th></tr></thead><tbody>${list.map((a) => `<tr>${showAsset ? `<td>${assetLink(a.asset_id, a.asset_code, a.asset_name)}</td>` : ""}<td>${escapeHTML(a.employee_name)}</td><td>${date(a.assigned_at)}</td><td>${a.status === "returned" ? date(a.returned_at) : "—"}</td><td>${escapeHTML(a.return_condition || "—")}</td><td class="notes">${assignmentNotes(a)}</td></tr>`).join("")}</tbody></table>${list.length ? "" : '<div class="empty">No assignments yet.</div>'}</div>`;
}
function transferActions(t) {
  if (t.status !== "pending") return "";
  let b = "";
  if (t.to_employee_id === me.id)
    b += `<button data-act="accepttransfer" data-id="${t.id}">Accept</button><button class="danger" data-act="rejecttransfer" data-id="${t.id}">Reject</button>`;
  if (manager())
    b += `<button class="secondary" data-act="canceltransfer" data-id="${t.id}">Cancel</button>`;
  return b;
}
const who = (name, active) =>
  `${escapeHTML(name)}${active ? "" : ' <span class="pill inactive">inactive</span>'}`;
const roleTag = (r) =>
  r ? ` <span class="muted">(${escapeHTML(r)})</span>` : "";
const findTransfer = (id) =>
  transfers.find((t) => t.id === id) ||
  detail?.transfers.find((t) => t.id === id);
// One-line answer to "where is this transfer now?"
function transferOutcome(t) {
  if (t.status === "pending")
    return t.to_active
      ? `Awaiting ${escapeHTML(t.to_name)}`
      : `Awaiting ${escapeHTML(t.to_name)} (inactive account)`;
  return `${{ accepted: "Accepted", rejected: "Rejected", cancelled: "Cancelled" }[t.status]} by ${escapeHTML(t.resolved_by_name || "—")}`;
}
// Full lifecycle, built from the single transfer record (no separate log to drift out of sync).
function lifecycle(t) {
  const steps = [
    {
      cls: "requested",
      title: `Requested by ${escapeHTML(t.requested_by_name)}${roleTag(t.requested_by_role)}`,
      at: t.requested_at,
      body: `Transfer from <strong>${who(t.from_name, t.from_active)}</strong> to <strong>${who(t.to_name, t.to_active)}</strong><div class="quote">Reason: ${escapeHTML(t.reason)}</div>`,
    },
  ];
  if (t.status === "pending")
    steps.push({
      cls: "waiting",
      title: `Awaiting response from ${escapeHTML(t.to_name)}`,
      body: t.to_active
        ? `The asset stays with ${escapeHTML(t.from_name)} until ${escapeHTML(t.to_name)} accepts or rejects.`
        : `${escapeHTML(t.to_name)}'s account is inactive, so this transfer cannot be accepted. An admin or manager can cancel it.`,
    });
  else if (t.status === "accepted")
    steps.push({
      cls: "accepted",
      title: `Accepted by ${escapeHTML(t.resolved_by_name)}`,
      at: t.resolved_at,
      body: `Asset reassigned from ${escapeHTML(t.from_name)} to ${escapeHTML(t.to_name)}.`,
    });
  else if (t.status === "rejected")
    steps.push({
      cls: "rejected",
      title: `Rejected by ${escapeHTML(t.resolved_by_name)}`,
      at: t.resolved_at,
      body: `Asset stayed with ${escapeHTML(t.from_name)}.`,
    });
  else
    steps.push({
      cls: "cancelled",
      title: `Cancelled by ${escapeHTML(t.resolved_by_name)}${roleTag(t.resolved_by_role)}`,
      at: t.resolved_at,
      body: t.resolution_note
        ? `<div class="quote">Reason: ${escapeHTML(t.resolution_note)}</div>`
        : '<span class="muted">No cancellation reason recorded (cancelled before reasons were required).</span>',
    });
  return `<ol class="timeline">${steps.map((x) => `<li class="${x.cls}"><div class="step-title">${x.title}</div>${x.at ? `<div class="muted">${date(x.at)}</div>` : ""}<div>${x.body}</div></li>`).join("")}</ol>`;
}
function transfersTable() {
  const stale = manager()
    ? transfers.filter((t) => t.status === "pending" && !t.to_active).length
    : 0;
  const q = tsearch.toLowerCase(),
    count = (st) =>
      transfers.filter((t) => st === "all" || t.status === st).length;
  const list = transfers.filter(
    (t) =>
      (tstatus === "all" || t.status === tstatus) &&
      [
        t.asset_code,
        t.asset_name,
        t.from_name,
        t.to_name,
        t.requested_by_name,
        t.resolved_by_name,
        t.reason,
        t.resolution_note,
      ]
        .join(" ")
        .toLowerCase()
        .includes(q),
  );
  return `<section class="panel"><h2>Transfers</h2>${stale ? `<div class="notice error">${stale} pending transfer${stale > 1 ? "s are" : " is"} addressed to an inactive account and cannot be accepted. Review and cancel ${stale > 1 ? "them" : "it"} below.</div>` : ""}<p class="muted">${manager() ? "Start a transfer from the Assets page. The asset stays with its current holder until the receiving employee accepts." : "Transfers of equipment to or from you. Assets move to you only after you accept."} Select <strong>History</strong> on a row to see its full lifecycle.</p>
  <div class="toolbar filters"><input id="tsearch" placeholder="Search asset, employee, reason…" value="${escapeHTML(tsearch)}" aria-label="Search transfers"><select id="tstatus" aria-label="Filter by status">${["all", "pending", "accepted", "rejected", "cancelled"].map((st) => `<option value="${st}" ${st === tstatus ? "selected" : ""}>${st[0].toUpperCase() + st.slice(1)} (${count(st)})</option>`).join("")}</select></div>
  <div class="tablewrap"><table><thead><tr><th>Asset</th><th>From → To</th><th>Reason</th><th>Initiated by</th><th>Status</th><th>Last action</th><th>Actions</th></tr></thead><tbody>${list.map((t) => `<tr><td>${assetLink(t.asset_id, t.asset_code, t.asset_name)}</td><td>${who(t.from_name, t.from_active)} → ${who(t.to_name, t.to_active)}</td><td class="reason">${escapeHTML(t.reason)}</td><td>${escapeHTML(t.requested_by_name)}<div class="muted">${date(t.requested_at)}</div></td><td><span class="pill ${t.status}">${t.status}</span><div class="muted">${transferOutcome(t)}</div></td><td>${date(t.resolved_at || t.requested_at)}</td><td class="actions"><button class="secondary" data-act="transferdetail" data-id="${t.id}">History</button>${transferActions(t)}</td></tr>`).join("")}</tbody></table>${list.length ? "" : `<div class="empty">${transfers.length ? "No transfers match these filters." : "No transfers yet."}</div>`}</div></section>`;
}
function assetDetail() {
  const a = detail.asset,
    pending = detail.transfers.find((t) => t.status === "pending");
  const info = [
    ["Category", escapeHTML(a.category)],
    ["Condition", escapeHTML(a.condition)],
    ["Assigned to", escapeHTML(a.assigned_to || "—")],
    ["Registered", date(a.created_at)],
    ["Last updated", date(a.updated_at)],
    ["Notes", escapeHTML(a.notes || "—")],
  ];
  return `<section class="panel"><div class="toolbar"><button class="secondary" data-act="back">← Back</button><div class="actions">${manager() ? assetActions(a) : ""}</div></div>
  <h2 class="detail-title"><span class="code">${escapeHTML(a.code)}</span> — ${escapeHTML(a.name)} <span class="pill ${a.status}">${a.status}</span></h2>
  <dl class="summary">${info.map(([k, v]) => `<div><dt>${k}</dt><dd>${v}</dd></div>`).join("")}</dl>
  ${pending ? `<div class="notice">Transfer pending: ${escapeHTML(pending.from_name)} → ${who(pending.to_name, pending.to_active)}. ${transferOutcome(pending)}.</div>` : ""}</section>
  <section class="panel"><h2>Transfer history <span class="muted">(${detail.transfers.length})</span></h2>${detail.transfers.length ? detail.transfers.map((t) => `<article class="transfer-card"><div class="toolbar"><div><strong>${who(t.from_name, t.from_active)} → ${who(t.to_name, t.to_active)}</strong> <span class="pill ${t.status}">${t.status}</span></div><div class="actions">${transferActions(t)}</div></div>${lifecycle(t)}</article>`).join("") : '<div class="empty">No transfers for this asset.</div>'}</section>
  <section class="panel"><h2>Assignment history</h2>${assignmentTable(detail.assignments, false)}</section>
  <section class="panel"><h2>Activity</h2>${eventTable(detail.events, false)}</section>`;
}
function eventTable(list, showAsset = true) {
  return `<div class="tablewrap"><table><thead><tr><th>When</th><th>Event</th>${showAsset ? "<th>Asset</th>" : ""}<th>By</th><th>Details</th></tr></thead><tbody>${list.map((e) => `<tr><td>${date(e.created_at)}</td><td>${escapeHTML(e.kind.replaceAll("_", " "))}</td>${showAsset ? `<td>${escapeHTML(e.asset_code || "—")}</td>` : ""}<td>${escapeHTML(e.actor_name || "—")}</td><td>${escapeHTML(e.detail)}</td></tr>`).join("")}</tbody></table>${list.length ? "" : '<div class="empty">No activity yet.</div>'}</div>`;
}
function usersTable() {
  return `<section class="panel"><div class="toolbar"><h2>People</h2>${me.role === "admin" ? '<button data-act="newuser">+ Add user</button>' : ""}</div><div class="tablewrap"><table><thead><tr><th>Username</th><th>Name</th><th>Role</th><th>Status</th>${me.role === "admin" ? "<th>Actions</th>" : ""}</tr></thead><tbody>${users.map((u) => `<tr><td>${escapeHTML(u.username)}</td><td>${escapeHTML(u.full_name)}</td><td>${escapeHTML(u.role)}</td><td><span class="pill ${u.active ? "available" : "inactive"}">${u.active ? "active" : "inactive"}</span></td>${me.role === "admin" ? `<td class="actions">${u.id === me.id ? '<span class="muted">(you)</span>' : `<button class="${u.active ? "danger" : "secondary"}" data-act="${u.active ? "deactivate" : "activate"}" data-id="${u.id}">${u.active ? "Deactivate" : "Reactivate"}</button>`}</td>` : ""}</tr>`).join("")}</tbody></table></div></section>`;
}
const field = (name, label, value = "", type = "text", required = true) =>
  `<label>${escapeHTML(label)}<input name="${name}" type="${type}" value="${escapeHTML(value)}" ${required ? "required" : ""}></label>`;
const select = (name, label, values, current) =>
  `<label>${escapeHTML(label)}<select name="${name}">${values.map((v) => `<option value="${escapeHTML(v)}" ${v === current ? "selected" : ""}>${escapeHTML(v)}</option>`).join("")}</select></label>`;
function modalHTML() {
  let html = "";
  const a = assets.find((x) => x.id === modal.id);
  if (modal.kind === "newasset" || modal.kind === "edit")
    html = `<h2>${a ? "Edit asset" : "Register asset"}</h2><form id="modalform"><div class="formgrid">${a ? "" : field("code", "Unique asset code")}${field("name", "Name", a?.name || "")}${field("category", "Category", a?.category || "")}${select("condition", "Condition", ["new", "good", "fair", "damaged"], a?.condition || "good")}</div><label>Notes<textarea name="notes" rows="3">${escapeHTML(a?.notes || "")}</textarea></label>${a ? `<label><input type="checkbox" name="retired" style="width:auto" ${a.retired ? "checked" : ""}> Retired (asset must be returned first)</label>` : ""}<footer><button type="button" class="secondary" data-act="close">Cancel</button><button>Save</button></footer></form>`;
  if (modal.kind === "assign")
    html = `<h2>Assign ${escapeHTML(a.code)}</h2><form id="modalform"><label>Employee<select name="employee_id" required><option value="">Choose employee</option>${users
      .filter((u) => u.role === "employee" && u.active)
      .map((u) => `<option value="${u.id}">${escapeHTML(u.full_name)}</option>`)
      .join(
        "",
      )}</select></label><footer><button type="button" class="secondary" data-act="close">Cancel</button><button>Assign</button></footer></form>`;
  if (modal.kind === "return")
    html = `<h2>Return ${escapeHTML(a.code)}</h2><form id="modalform">${select("condition", "Condition on return", ["new", "good", "fair", "damaged"], a.condition)}<footer><button type="button" class="secondary" data-act="close">Cancel</button><button>Record return</button></footer></form>`;
  if (modal.kind === "transfer") {
    const choices = users.filter(
      (u) => u.role === "employee" && u.active && u.id !== a.employee_id,
    );
    html = `<h2>Transfer ${escapeHTML(a.code)}</h2><p class="muted">Currently assigned to <strong>${escapeHTML(a.assigned_to)}</strong>. The asset stays with them until the receiving employee accepts.</p><form id="modalform"><label>Transfer to<select name="employee_id" required><option value="">Choose employee</option>${choices.map((u) => `<option value="${u.id}">${escapeHTML(u.full_name)}</option>`).join("")}</select></label><label>Reason<textarea name="reason" rows="3" maxlength="500" required></textarea></label><footer><button type="button" class="secondary" data-act="close">Cancel</button><button>Request transfer</button></footer></form>`;
  }
  if (modal.kind === "transferdetail") {
    const t = findTransfer(modal.id);
    html = `<h2>Transfer history</h2><dl class="summary"><div><dt>Asset</dt><dd>${assetLink(t.asset_id, t.asset_code, t.asset_name)}</dd></div><div><dt>From</dt><dd>${who(t.from_name, t.from_active)}</dd></div><div><dt>To</dt><dd>${who(t.to_name, t.to_active)}</dd></div><div><dt>Initiated by</dt><dd>${escapeHTML(t.requested_by_name)}${roleTag(t.requested_by_role)}</dd></div><div><dt>Status</dt><dd><span class="pill ${t.status}">${t.status}</span> ${transferOutcome(t)}</dd></div></dl>${lifecycle(t)}<footer><span class="actions">${transferActions(t)}</span><button type="button" class="secondary" data-act="close">Close</button></footer>`;
  }
  if (
    ["accepttransfer", "rejecttransfer", "canceltransfer"].includes(modal.kind)
  ) {
    const t = findTransfer(modal.id);
    const [title, text, label] = {
      accepttransfer: [
        "Accept transfer",
        "The asset will be assigned to you and removed from",
        "Accept",
      ],
      rejecttransfer: ["Reject transfer", "The asset will stay with", "Reject"],
      canceltransfer: [
        "Cancel transfer",
        "The asset will stay with",
        "Cancel transfer",
      ],
    }[modal.kind];
    html = `<h2>${title}</h2><p><span class="code">${escapeHTML(t.asset_code)}</span> — ${escapeHTML(t.asset_name)}</p><p class="muted">From ${escapeHTML(t.from_name)} to ${escapeHTML(t.to_name)}. Reason: ${escapeHTML(t.reason)}</p><p>${text} ${escapeHTML(t.from_name)}.</p><form id="modalform">${modal.kind === "canceltransfer" ? '<label>Reason for cancelling <span class="muted">(required, shown in the transfer history)</span><textarea name="reason" rows="3" maxlength="500" required></textarea></label>' : ""}<footer><button type="button" class="secondary" data-act="close">Back</button><button class="${modal.kind === "accepttransfer" ? "" : "danger"}">${label}</button></footer></form>`;
  }
  if (modal.kind === "deactivate" || modal.kind === "activate") {
    const u = users.find((x) => x.id === modal.id);
    const off = modal.kind === "deactivate";
    html = `<h2>${off ? "Deactivate" : "Reactivate"} ${escapeHTML(u.full_name)}</h2><p class="muted">${escapeHTML(u.username)} · ${escapeHTML(u.role)}</p><p>${off ? "They will be signed out immediately and can no longer sign in, receive assignments, or accept transfers. Assets currently assigned to them stay assigned until you return or transfer them; any pending transfer to them stays open for you to review and cancel." : "They will be able to sign in again and, if an employee, receive assignments and respond to transfers."}</p><form id="modalform"><footer><button type="button" class="secondary" data-act="close">Back</button><button class="${off ? "danger" : ""}">${off ? "Deactivate account" : "Reactivate account"}</button></footer></form>`;
  }
  if (modal.kind === "newuser")
    html = `<h2>Add user</h2><form id="modalform">${field("username", "Username")}${field("full_name", "Full name")}${select("role", "Role", ["employee", "manager", "admin"], "employee")}${field("password", "Initial password (10+ characters)", "", "password")}<footer><button type="button" class="secondary" data-act="close">Cancel</button><button>Add user</button></footer></form>`;
  return `<div class="modalback"><section class="modal" role="dialog" aria-modal="true">${html}</section></div>`;
}
function render() {
  if (!me) {
    root.innerHTML = `<div class="center"><section class="panel login"><div class="brand">▣ AssetTrack</div><p class="muted">Sign in to manage your equipment.</p>${flash}<form id="loginform">${field("username", "Username")}${field("password", "Password", "", "password")}<button>Sign in</button></form></section></div>`;
    return;
  }
  let content =
    view === "dashboard"
      ? dashboard()
      : view === "assets"
        ? assetTable()
        : view === "assignments"
          ? assignmentsTable()
          : view === "transfers"
            ? transfersTable()
            : view === "asset" && detail
              ? assetDetail()
              : view === "history"
                ? `<section class="panel"><h2>Activity history</h2>${eventTable(events)}</section>`
                : usersTable();
  root.innerHTML = layout(content);
}
root.addEventListener("click", async (e) => {
  let btn = e.target.closest("button");
  if (!btn) return;
  try {
    if (btn.dataset.view) {
      view = btn.dataset.view;
      search = "";
      flash = "";
      detail = null;
      detailId = 0;
      render();
      return;
    }
    let action = btn.dataset.act;
    if (!action) return;
    if (action === "logout") {
      await api("/logout", "POST", {});
      me = null;
      modal = null;
      flash = "";
      render();
      return;
    }
    if (action === "close") {
      modal = null;
      render();
      return;
    }
    if (action === "openasset") {
      const id = Number(btn.dataset.id);
      const d = await api(`/assets/${id}`);
      if (view !== "asset") prevView = view;
      detail = d;
      detailId = id;
      view = "asset";
      modal = null;
      flash = "";
      render();
      window.scrollTo(0, 0);
      return;
    }
    if (action === "back") {
      view = prevView;
      detail = null;
      detailId = 0;
      flash = "";
      render();
      return;
    }
    if (
      [
        "newasset",
        "edit",
        "assign",
        "return",
        "transfer",
        "accepttransfer",
        "rejecttransfer",
        "canceltransfer",
        "transferdetail",
        "newuser",
        "deactivate",
        "activate",
      ].includes(action)
    ) {
      modal = { kind: action, id: Number(btn.dataset.id || 0) };
      render();
    }
  } catch (err) {
    notify(err.message, true);
  }
});
root.addEventListener("input", (e) => {
  const id = e.target.id;
  if (id !== "search" && id !== "tsearch") return;
  if (id === "search") search = e.target.value;
  else tsearch = e.target.value;
  const cursor = e.target.selectionStart;
  render();
  const el = document.getElementById(id);
  el.focus();
  el.setSelectionRange(cursor, cursor);
});
root.addEventListener("change", (e) => {
  if (e.target.id === "tstatus") {
    tstatus = e.target.value;
    render();
  }
});
root.addEventListener("submit", async (e) => {
  if (!["loginform", "modalform"].includes(e.target.id)) return;
  e.preventDefault();
  const form = e.target;
  const data = Object.fromEntries(new FormData(form));
  try {
    if (form.id === "loginform") {
      await api("/login", "POST", data);
      flash = "";
      await load();
      return;
    }
    const current = modal;
    if (current.kind === "newasset") await api("/assets", "POST", data);
    else if (current.kind === "edit") {
      data.retired = form.elements.retired.checked;
      await api(`/assets/${current.id}/update`, "POST", data);
    } else if (current.kind === "assign")
      await api(`/assets/${current.id}/assign`, "POST", {
        employee_id: Number(data.employee_id),
      });
    else if (current.kind === "return")
      await api(`/assets/${current.id}/return`, "POST", data);
    else if (current.kind === "transfer")
      await api(`/assets/${current.id}/transfer`, "POST", {
        employee_id: Number(data.employee_id),
        reason: data.reason,
      });
    else if (current.kind === "accepttransfer")
      await api(`/transfers/${current.id}/accept`, "POST", {});
    else if (current.kind === "rejecttransfer")
      await api(`/transfers/${current.id}/reject`, "POST", {});
    else if (current.kind === "canceltransfer")
      await api(`/transfers/${current.id}/cancel`, "POST", {
        reason: data.reason,
      });
    else if (current.kind === "newuser") await api("/users", "POST", data);
    else if (current.kind === "deactivate" || current.kind === "activate") {
      const r = await api(`/users/${current.id}/status`, "POST", {
        active: current.kind === "activate",
      });
      modal = null;
      await load();
      notify(
        r.active
          ? "Account reactivated"
          : `Account deactivated${r.pending_transfers ? `. ${r.pending_transfers} pending transfer${r.pending_transfers > 1 ? "s" : ""} to this person now need${r.pending_transfers > 1 ? "" : "s"} review on the Transfers page.` : ""}`,
      );
      return;
    }
    modal = null;
    await load();
    notify("Saved successfully");
  } catch (err) {
    const box = form.id === "modalform" && modal && form.closest(".modal");
    if (!box || !me) return notify(err.message, true);
    let msg = box.querySelector(".modal-error");
    if (!msg) {
      msg = document.createElement("div");
      msg.className = "notice error modal-error";
      msg.setAttribute("role", "alert");
      box.querySelector("h2").after(msg);
    }
    msg.textContent = err.message;
  }
});
load().catch((e) => notify(e.message, true));

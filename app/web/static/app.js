const state = { currentTask: null, tasks: [], selectedTaskId: null };
const PUBLIC_DEMO = document.body.dataset.publicDemo === 'true';

const STATUS_LABELS = {
  requested: 'Request received', calling: 'Contacting provider', quote_received: 'Checking quote',
  awaiting_approval: 'Approval needed', booking: 'Confirming booking', retry_scheduled: 'Retry scheduled',
  needs_human: 'Operator review', completed: 'Completed', cancelled: 'Cancelled'
};

const rupees = (paise) => paise == null ? '—' : new Intl.NumberFormat('en-IN', { style: 'currency', currency: 'INR', maximumFractionDigits: 0 }).format(paise / 100);
const when = (date) => new Intl.DateTimeFormat('en-IN', { hour: '2-digit', minute: '2-digit', day: '2-digit', month: 'short', timeZone: 'Asia/Kolkata' }).format(new Date(date));
const escapeHtml = (value = '') => String(value).replace(/[&<>'"]/g, (char) => ({ '&':'&amp;', '<':'&lt;', '>':'&gt;', "'":'&#39;', '"':'&quot;' })[char]);

async function api(path, options = {}) {
  const response = await fetch(path, {
    ...options,
    headers: { 'Content-Type': 'application/json', ...(options.headers || {}) }
  });
  if (!response.ok) {
    let detail = `Request failed (${response.status})`;
    try { const body = await response.json(); detail = body.detail || detail; } catch (_) { /* no JSON body */ }
    throw new Error(typeof detail === 'string' ? detail : JSON.stringify(detail));
  }
  return response.status === 204 ? null : response.json();
}

function toast(message, isError = false) {
  const node = document.querySelector('#toast');
  node.textContent = message;
  node.className = `toast is-visible${isError ? ' is-error' : ''}`;
  clearTimeout(toast.timer);
  toast.timer = setTimeout(() => { node.className = 'toast'; }, 3200);
}

function setView(view) {
  document.querySelectorAll('[data-view]').forEach((node) => node.classList.toggle('is-visible', node.dataset.view === view));
  document.querySelectorAll('[data-view-target]').forEach((node) => node.classList.toggle('is-active', node.dataset.viewTarget === view));
  if (view === 'operations') refreshOperations();
  if (view === 'memory') refreshMemory();
  window.scrollTo({ top: 0, behavior: 'smooth' });
}

function statusClass(status) { return status.replaceAll('_', '-'); }

function meaningfulEvents(task) {
  return task.events.filter((event) => ['task.created', 'state.transition', 'browser.confirmed', 'memory.retrieved'].includes(event.event_type));
}

function transcriptMarkup(task) {
  const transcript = task.events.filter((event) => event.event_type === 'voice.transcript');
  if (!transcript.length) return '';
  return `<details><summary>Call transcript (${transcript.length} lines)</summary><div class="transcript">${transcript.map((event) => `<p><strong>${escapeHtml(event.actor)}</strong> ${escapeHtml(event.message)}</p>`).join('')}</div></details>`;
}

function approvalMarkup(task) {
  if (task.status !== 'awaiting_approval') return '';
  const over = task.budget_paise ? task.quote_paise - task.budget_paise : 0;
  return `<div class="approval-card"><strong>Your approval is needed</strong><p>${escapeHtml(task.provider_name)} offered ${escapeHtml(task.quoted_slot)} for <b>${rupees(task.quote_paise)}</b>${over > 0 ? `, ${rupees(over)} over your limit` : ''}.</p><div class="approval-actions"><button class="button button-primary button-small" data-task-action="approve" data-task-id="${task.id}" data-version="${task.version}">Approve ${rupees(task.quote_paise)}</button><button class="button button-danger button-small" data-task-action="reject" data-task-id="${task.id}" data-version="${task.version}">Decline</button></div></div>`;
}

function taskControls(task) {
  const buttons = [];
  if (task.status === 'retry_scheduled') buttons.push(`<button class="button button-primary button-small" data-task-action="retry" data-task-id="${task.id}">Retry now</button>`);
  if (task.status === 'needs_human' && !PUBLIC_DEMO) buttons.push(`<button class="button button-primary button-small" data-task-action="resolve" data-task-id="${task.id}">Resolve and resume</button>`);
  if (!['completed', 'cancelled'].includes(task.status)) buttons.push(`<button class="button button-danger button-small" data-task-action="cancel" data-task-id="${task.id}">Cancel task</button>`);
  return buttons.length ? `<div class="approval-actions">${buttons.join('')}</div>` : '';
}

function renderCustomerTask(task) {
  state.currentTask = task;
  const events = meaningfulEvents(task);
  document.querySelector('#customer-task').className = 'live-task';
  document.querySelector('#customer-task').innerHTML = `
    <div class="task-summary">
      <span class="status-pill ${statusClass(task.status)}">${STATUS_LABELS[task.status] || task.status}</span>
      <h3>${escapeHtml(task.description)}</h3>
      <p class="current-action">${escapeHtml(task.current_action)}</p>
      <div class="fact-grid">
        <div class="fact"><span>Budget</span><strong>${rupees(task.budget_paise)}</strong></div>
        <div class="fact"><span>Quote</span><strong>${rupees(task.quote_paise)}</strong></div>
        <div class="fact"><span>Provider</span><strong>${escapeHtml(task.provider_name || 'Finding provider')}</strong></div>
        <div class="fact"><span>Confirmation</span><strong>${escapeHtml(task.confirmation_ref || 'Pending')}</strong></div>
      </div>
      ${approvalMarkup(task)}
      ${taskControls(task)}
    </div>
    <div class="task-timeline">
      <h3>Evidence timeline</h3>
      <div class="timeline">${events.map((event) => `<div class="timeline-item"><span>${escapeHtml(event.actor)} · <time>${when(event.occurred_at)}</time></span><p>${escapeHtml(event.message)}</p></div>`).join('')}</div>
      ${transcriptMarkup(task)}
    </div>`;
  document.dispatchEvent(new CustomEvent('jeevan:task-updated', { detail: task }));
}

function requestPayloadFromForm(serviceType = 'AC servicing') {
  return {
    user_id: 'demo-user', idempotency_key: crypto.randomUUID(),
    description: document.querySelector('#description').value,
    service_type: serviceType, requested_date: document.querySelector('#requested-date').value,
    time_window: document.querySelector('#time-window').value,
    budget_rupees: Number(document.querySelector('#budget').value),
    address: document.querySelector('#address').value || null,
    scenario: document.querySelector('#scenario').value
  };
}

async function startTask(payload) {
  let task = await api('/api/v1/tasks', { method: 'POST', body: JSON.stringify(payload) });
  renderCustomerTask(task);
  document.querySelector('.execution-shell').scrollIntoView({ behavior: 'smooth' });
  await new Promise((resolve) => setTimeout(resolve, 650));
  task = await api(`/api/v1/tasks/${task.id}/run`, { method: 'POST' });
  renderCustomerTask(task);
  toast(task.status === 'completed' ? 'Booking completed with provider evidence.' : 'Jeevan reached the next safe checkpoint.');
  return task;
}

async function submitRequest(event) {
  event.preventDefault();
  const button = event.currentTarget.querySelector('button[type="submit"]');
  button.disabled = true;
  button.querySelector('span:first-child').textContent = 'Starting…';
  try {
    await startTask(requestPayloadFromForm());
  } catch (error) { toast(error.message, true); }
  finally { button.disabled = false; button.querySelector('span:first-child').textContent = 'Start task'; }
}

async function performTaskAction(action, taskId, version) {
    let task;
    if (action === 'approve' || action === 'reject') {
      task = await api(`/api/v1/tasks/${taskId}/approve`, { method: 'POST', body: JSON.stringify({ approved: action === 'approve', task_version: Number(version) }) });
    } else if (action === 'retry') {
      task = await api(`/api/v1/tasks/${taskId}/retry`, { method: 'POST' });
    } else if (action === 'cancel') {
      task = await api(`/api/v1/tasks/${taskId}/cancel`, { method: 'POST' });
    } else if (action === 'takeover') {
      task = await api(`/api/v1/ops/tasks/${taskId}/takeover`, { method: 'POST', body: JSON.stringify({ operator: 'Suraj', note: 'Reviewing the blocked step from the demo console' }) });
    } else if (action === 'resolve') {
      task = await api(`/api/v1/ops/tasks/${taskId}/resolve`, { method: 'POST', body: JSON.stringify({ operator: 'Suraj', note: 'Verified the extracted details and resumed from the last safe step', quote_rupees: state.tasks.find((item) => item.id === taskId)?.quote_paise ? null : 900, quoted_slot: state.tasks.find((item) => item.id === taskId)?.quoted_slot ? null : 'Sunday, 3:00 PM - 4:00 PM' }) });
    }
    if (task) {
      if (state.currentTask?.id === task.id) renderCustomerTask(task);
      state.selectedTaskId = task.id;
      toast(`Task updated: ${STATUS_LABELS[task.status] || task.status}`);
      if (!PUBLIC_DEMO) await refreshOperations();
    }
    return task;
}

async function handleTaskAction(button) {
  const { taskAction: action, taskId, version } = button.dataset;
  button.disabled = true;
  try {
    await performTaskAction(action, taskId, version);
  } catch (error) { toast(error.message, true); }
  finally { button.disabled = false; }
}

async function refreshOperations() {
  try {
    const filter = document.querySelector('#status-filter')?.value || '';
    const query = filter ? `?status=${encodeURIComponent(filter)}` : '';
    const [metrics, tasks] = await Promise.all([api('/api/v1/ops/metrics'), api(`/api/v1/tasks${query}`)]);
    state.tasks = tasks;
    document.querySelector('#metrics').innerHTML = [
      ['Active tasks', metrics.active_tasks], ['Needs attention', metrics.needs_attention],
      ['Completed', metrics.completed_tasks], ['Completion rate', `${metrics.completion_rate}%`],
      ['Duplicate bookings', metrics.duplicate_bookings]
    ].map(([label, value]) => `<div class="metric-card"><span>${label}</span><strong>${value}</strong></div>`).join('');
    const queue = document.querySelector('#task-queue');
    queue.innerHTML = tasks.length ? tasks.map((task) => `<button class="queue-item${task.id === state.selectedTaskId ? ' is-selected' : ''}" data-select-task="${task.id}"><div><h3>${escapeHtml(task.service_type)} · ${escapeHtml(task.user_id)}</h3><p>${escapeHtml(task.description)}</p></div><div class="queue-meta"><span class="mini-status">${STATUS_LABELS[task.status] || task.status}</span><time>${when(task.updated_at)}</time></div></button>`).join('') : '<div class="empty-panel compact"><h3>No tasks match this filter</h3><p>Change the status filter or create a sample request.</p></div>';
    if (state.selectedTaskId) {
      const selected = tasks.find((task) => task.id === state.selectedTaskId) || await api(`/api/v1/tasks/${state.selectedTaskId}`);
      renderOpsDetail(selected);
    } else if (tasks.length) {
      state.selectedTaskId = tasks[0].id;
      renderOpsDetail(tasks[0]);
    }
  } catch (error) { toast(error.message, true); }
}

function renderOpsDetail(task) {
  const events = [...task.events].reverse();
  document.querySelector('#ops-detail').innerHTML = `<article class="ops-task">
    <header class="ops-task-header"><span class="status-pill ${statusClass(task.status)}">${STATUS_LABELS[task.status] || task.status}</span><h2>${escapeHtml(task.description)}</h2><p>${escapeHtml(task.current_action)}</p></header>
    <div class="ops-facts"><div class="ops-fact"><span>Budget</span><strong>${rupees(task.budget_paise)}</strong></div><div class="ops-fact"><span>Quote</span><strong>${rupees(task.quote_paise)}</strong></div><div class="ops-fact"><span>Attempts</span><strong>${task.attempt_count}</strong></div><div class="ops-fact"><span>Version</span><strong>v${task.version}</strong></div></div>
    <div class="ops-columns"><section class="ops-events"><h3>Audit and conversation trail</h3><div class="event-list">${events.map((event) => `<div class="event"><span>${escapeHtml(event.actor)} · ${escapeHtml(event.event_type)} · ${when(event.occurred_at)}</span><p>${escapeHtml(event.message)}</p></div>`).join('')}</div></section><section class="ops-controls"><h3>Operator controls</h3>${task.attention_reason ? `<div class="control-alert"><strong>Attention reason</strong><br>${escapeHtml(task.attention_reason)}</div>` : ''}<div class="control-stack">${task.status === 'awaiting_approval' ? approvalMarkup(task) : ''}${task.status === 'retry_scheduled' ? `<button class="button button-primary" data-task-action="retry" data-task-id="${task.id}">Retry provider call</button>` : ''}${!['completed','cancelled','needs_human'].includes(task.status) ? `<button class="button button-secondary" data-task-action="takeover" data-task-id="${task.id}">Take over task</button>` : ''}${task.status === 'needs_human' ? `<button class="button button-primary" data-task-action="resolve" data-task-id="${task.id}">Resolve and resume</button>` : ''}${!['completed','cancelled'].includes(task.status) ? `<button class="button button-danger" data-task-action="cancel" data-task-id="${task.id}">Cancel task</button>` : ''}</div></section></div>
  </article>`;
}

async function refreshMemory() {
  try {
    const memories = await api('/api/v1/users/demo-user/memories');
    document.querySelector('#memory-list').innerHTML = memories.length ? `<div class="memory-items">${memories.map((memory) => `<article class="memory-item"><div class="memory-symbol" aria-hidden="true">${memory.kind === 'history' ? '↺' : '✦'}</div><div><h3>${escapeHtml(memory.key.replaceAll('_', ' '))}</h3><p>${escapeHtml(memory.value)}</p><small>${escapeHtml(memory.kind)} · source: ${escapeHtml(memory.source)}</small></div><button class="icon-button" data-delete-memory="${escapeHtml(memory.key)}" aria-label="Delete ${escapeHtml(memory.key)}">×</button></article>`).join('')}</div>` : '<div class="empty-panel compact"><h3>No saved memory</h3><p>Jeevan has not learned any reusable preference for this user.</p></div>';
  } catch (error) { toast(error.message, true); }
}

async function saveMemory(event) {
  event.preventDefault();
  const key = document.querySelector('#memory-key').value;
  try {
    await api(`/api/v1/users/demo-user/memories/${encodeURIComponent(key)}`, { method: 'PUT', body: JSON.stringify({ value: document.querySelector('#memory-value').value, kind: 'explicit', source: document.querySelector('#memory-source').value, durable: true }) });
    event.currentTarget.reset(); document.querySelector('#memory-source').value = 'user';
    toast('Preference saved with its source.'); await refreshMemory();
  } catch (error) { toast(error.message, true); }
}

document.addEventListener('click', async (event) => {
  const nav = event.target.closest('[data-view-target]'); if (nav) setView(nav.dataset.viewTarget);
  if (event.target.closest('#open-ops')) setView('operations');
  if (event.target.closest('#refresh-ops')) refreshOperations();
  const action = event.target.closest('[data-task-action]'); if (action) await handleTaskAction(action);
  const queue = event.target.closest('[data-select-task]'); if (queue) { state.selectedTaskId = queue.dataset.selectTask; renderOpsDetail(state.tasks.find((task) => task.id === state.selectedTaskId)); document.querySelectorAll('.queue-item').forEach((item) => item.classList.toggle('is-selected', item === queue)); }
  const remove = event.target.closest('[data-delete-memory]'); if (remove) { try { await api(`/api/v1/users/demo-user/memories/${encodeURIComponent(remove.dataset.deleteMemory)}`, { method: 'DELETE' }); toast('Memory removed.'); await refreshMemory(); } catch (error) { toast(error.message, true); } }
});

document.querySelector('#request-form').addEventListener('submit', submitRequest);
document.querySelector('#memory-form').addEventListener('submit', saveMemory);
document.querySelector('#status-filter').addEventListener('change', refreshOperations);
document.querySelector('#sample-request').addEventListener('click', () => {
  document.querySelector('#description').value = 'Book AC servicing this Saturday after 2 PM, under ₹1,000.';
  document.querySelector('#requested-date').value = 'Saturday'; document.querySelector('#time-window').value = 'After 2:00 PM'; document.querySelector('#budget').value = 1000; document.querySelector('#scenario').value = 'above_budget';
});

window.JeevanApp = { api, toast, rupees, startTask, requestPayloadFromForm, performTaskAction, getCurrentTask: () => state.currentTask };

(async function boot() {
  if (PUBLIC_DEMO) return;
  try { await api('/api/v1/demo/seed', { method: 'POST' }); await Promise.all([refreshOperations(), refreshMemory()]); }
  catch (error) { toast(`Could not initialize demo: ${error.message}`, true); }
})();

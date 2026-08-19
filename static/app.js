/* ═══════════════════════════════════════════════════════════════
   GitHub Achievement Agent — app.js
   Dashboard logic: scan, plan, execute, SSE, UI updates
   ═══════════════════════════════════════════════════════════════ */

'use strict';

// ── State ─────────────────────────────────────────────────────
const state = {
  account:     1,
  dryRun:      true,
  scanning:    false,
  executing:   false,
  plan:        [],
  achievements: [],
  sse:         null,
};

// ── Init ──────────────────────────────────────────────────────
document.addEventListener('DOMContentLoaded', () => {
  checkStatus();
  setInterval(checkStatus, 15000);
  connectSSE();
});

// ── Account selector ──────────────────────────────────────────
function selectAccount(id) {
  state.account = id;
  document.querySelectorAll('.seg-btn[data-account]').forEach(btn => {
    btn.classList.toggle('active', parseInt(btn.dataset.account) === id);
  });
}

document.addEventListener('click', e => {
  if (e.target.dataset.account) selectAccount(parseInt(e.target.dataset.account));
});

// ── Mode toggle ───────────────────────────────────────────────
function setMode(dry) {
  state.dryRun = dry;
  document.getElementById('dryRunBtn').classList.toggle('active', dry);
  document.getElementById('liveBtn').classList.toggle('active', !dry);

  const badge = document.getElementById('modeBadge');
  if (badge) {
    badge.textContent = dry ? 'DRY RUN' : '⚡ LIVE MODE';
    badge.classList.toggle('live', !dry);
  }

  if (!dry) {
    showToast('⚡ Live Mode enabled — real GitHub actions will execute', 'warn');
  }
}

// ── Main flow ─────────────────────────────────────────────────
async function runFullFlow() {
  showWorkflow(true);
  setStep('stepScan', 'active');

  const ok = await runScan();
  if (!ok) return;

  setStep('stepScan', 'done');
  setStep('stepPlan', 'active');

  const planOk = await runPlan();
  if (!planOk) return;

  setStep('stepPlan', 'done');

  // Show plan and let user click Execute
  document.getElementById('planSection').style.display = '';
  scrollToId('planSection');
}

// ── Scan ──────────────────────────────────────────────────────
async function runScan() {
  if (state.scanning) return false;
  state.scanning = true;

  const scanBtn = document.getElementById('scanBtn');
  if (scanBtn) {
    scanBtn.classList.add('loading');
    scanBtn.disabled = true;
  }

  setCTASub('🔍 Scanning achievements…');
  appendLog({ action: `Scanning account ${state.account}…`, result: 'IN PROGRESS', dry_run: 1 });

  try {
    const res = await apiFetch(`/api/scan/${state.account}`, { method: 'POST' });
    state.achievements = res.achievements || [];
    renderAchievements(state.achievements);
    updateSummaryCards(state.achievements);
    document.getElementById('scanHint').textContent = `Last scanned: ${new Date().toLocaleTimeString()}`;
    document.getElementById('planBtn').disabled = false;
    showToast(`✅ Scan complete — ${state.achievements.length} achievements found`, 'success');
    appendLog({ action: `Scan complete`, result: 'SUCCESS', achievement: `${state.achievements.length} achievements`, dry_run: 1 });
    return true;
  } catch (err) {
    showToast('❌ Scan failed: ' + err.message, 'error');
    appendLog({ action: 'Scan', result: 'ERROR: ' + err.message, dry_run: 1 });
    setStep('stepScan', 'error');
    return false;
  } finally {
    state.scanning = false;
    const scanBtn = document.getElementById('scanBtn');
    if (scanBtn) {
      scanBtn.classList.remove('loading');
      scanBtn.disabled = false;
    }
    setCTASub('Scan → Plan → Preview → Execute');
  }
}

// ── Plan ──────────────────────────────────────────────────────
async function runPlan() {
  setCTASub('📋 Building action plan…');
  try {
    const res = await apiFetch('/api/plan', {
      method: 'POST',
      body: JSON.stringify({ account_id: state.account, second_account_id: 2 }),
    });
    state.plan = res.plan || [];
    renderPlan(state.plan, res.summary);
    return true;
  } catch (err) {
    showToast('❌ Plan failed: ' + err.message, 'error');
    return false;
  } finally {
    setCTASub('Scan → Plan → Preview → Execute');
  }
}

// ── Execute ───────────────────────────────────────────────────
async function executeActions() {
  if (state.executing) return;

  if (!state.dryRun) {
    const confirmed = confirm(
      '⚡ LIVE MODE: This will perform real GitHub actions.\n\n' +
      'Actions are conservative and safe, but irreversible.\n\n' +
      'Continue?'
    );
    if (!confirmed) return;
  }

  state.executing = true;
  setStep('stepExecute', 'active');

  const btn = document.getElementById('executeBtn');
  btn.textContent = state.dryRun ? '🔍 Running dry-run…' : '⚡ Executing…';
  btn.disabled = true;

  appendLog({ action: `Execute (${state.dryRun ? 'DRY RUN' : 'LIVE'})`, result: 'STARTED', dry_run: state.dryRun ? 1 : 0 });

  try {
    const res = await apiFetch('/api/execute', {
      method: 'POST',
      body: JSON.stringify({
        account_id: state.account,
        second_account_id: 2,
        dry_run: state.dryRun,
      }),
    });

    const results = res.results || [];
    results.forEach(evt => {
      if (evt.type === 'result' && evt.data) {
        appendLog({
          action: evt.data.action,
          result: evt.data.success ? 'SUCCESS' : 'FAILED',
          achievement: evt.data.achievement,
          dry_run: evt.data.dry_run ? 1 : 0,
        });
      } else if (evt.type === 'stopped') {
        appendLog({ action: 'STOPPED', result: evt.reason, dry_run: 0 });
      } else if (evt.type === 'error') {
        appendLog({ action: evt.achievement, result: 'ERROR: ' + evt.error, dry_run: 0 });
      }
    });

    setStep('stepExecute', 'done');
    setStep('stepVerify', 'active');
    showToast(
      state.dryRun
        ? '✅ Dry run complete — no real actions taken'
        : '✅ Execution complete — verifying…',
      'success'
    );

    if (!state.dryRun) {
      await sleep(5000);
      await runScan();
    }
    setStep('stepVerify', 'done');

  } catch (err) {
    showToast('❌ Execution error: ' + err.message, 'error');
    setStep('stepExecute', 'error');
  } finally {
    state.executing = false;
    btn.textContent = '⚡ Start Safe Automation';
    btn.disabled = false;
  }
}

// ── Emergency stop ────────────────────────────────────────────
async function emergencyStop() {
  try {
    await apiFetch('/api/stop', { method: 'POST' });
    showStopBanner('Manual stop requested by user.');
    showToast('🛑 Automation stopped', 'error');
  } catch (err) {
    showToast('Stop failed: ' + err.message, 'error');
  }
}

async function resetStop() {
  try {
    await apiFetch('/api/reset', { method: 'POST' });
    document.getElementById('stopBanner').style.display = 'none';
    showToast('↺ Automation reset — ready to run', 'info');
  } catch (err) {
    showToast('Reset failed: ' + err.message, 'error');
  }
}

// ── Status polling ────────────────────────────────────────────
async function checkStatus() {
  try {
    const s = await apiFetch('/api/status');
    if (s.stopped) {
      showStopBanner(s.stop_reason || 'Unknown reason');
    } else {
      document.getElementById('stopBanner').style.display = 'none';
    }

    const rl = s.rate_limits?.[String(state.account)];
    if (rl) {
      setText('rateLimitRemaining', rl.remaining?.toLocaleString() ?? '—');
      setText('hourlyMutations', `${rl.hourly_mutations_used}/${rl.hourly_mutations_max}`);
      setText('dailyPRs', `${rl.daily_prs_used}/${rl.daily_prs_max}`);
      setText('dailyIssues', `${rl.daily_issues_used}/${rl.daily_issues_max}`);
    }
  } catch (_) { /* Ignore — server may not be ready yet */ }
}

// ── SSE ───────────────────────────────────────────────────────
function connectSSE() {
  if (state.sse) state.sse.close();
  state.sse = new EventSource(`/api/stream/${state.account}`);
  state.sse.onmessage = e => {
    try {
      const data = JSON.parse(e.data);
      if (data.type === 'stopped') {
        showStopBanner(data.reason);
      } else if (data.action) {
        // Audit log entry
        appendLog(data);
      }
    } catch (_) {}
  };
  state.sse.onerror = () => {
    state.sse.close();
    setTimeout(connectSSE, 10000);
  };
}

// ── Render achievements ───────────────────────────────────────
function renderAchievements(achievements) {
  const grid = document.getElementById('achievementGrid');
  grid.innerHTML = '';

  achievements.forEach(ach => {
    const progress = ach.progress || {};
    const current  = progress.current ?? 0;
    const required = progress.required ?? 1;
    const pct      = Math.min(100, Math.round((current / required) * 100));
    const earned   = ach.status === 'EARNED';

    const card = document.createElement('div');
    card.className = 'ach-card';
    card.dataset.status = ach.status;

    card.innerHTML = `
      <div class="ach-header">
        <span class="ach-emoji">${ach.emoji || '🏆'}</span>
        <div>
          <div class="ach-title">${esc(ach.display_name || ach.name)}</div>
          <div class="ach-desc">${esc(ach.description || '')}</div>
        </div>
      </div>

      <span class="status-badge badge-${ach.status}">
        ${statusIcon(ach.status)} ${ach.status.replace(/_/g, ' ')} ${ach.tier && ach.tier !== 'N/A' ? `(${ach.tier})` : ''}
      </span>

      <div class="progress-row">
        <div class="progress-label">
          <span>Progress</span>
          <span>${current} / ${required}</span>
        </div>
        <div class="progress-bar-bg">
          <div class="progress-bar-fill ${earned ? 'earned' : ''}" style="width: ${pct}%"></div>
        </div>
      </div>

      ${renderCardAction(ach)}
    `;

    grid.appendChild(card);
  });
}

function renderCardAction(ach) {
  const status = ach.status;
  if (status === 'UNOBTAINABLE') return '';

  const isFullyEarned = status === 'EARNED' && (!ach.progress || ach.progress.current >= ach.progress.required);
  if (isFullyEarned) return '';

  if (ach.automation_class === 'AUTO') {
    return `<div class="ach-action">
      <button class="ach-run-btn" onclick="runSingleAchievement('${ach.key || ach.name}')">
        ▶ Run ${state.dryRun ? '(Dry Run)' : ''}
      </button>
    </div>`;
  }

  if (ach.automation_class === 'SEMI_AUTO') {
    return `<div class="ach-action">
      <button class="ach-run-btn" onclick="runSingleAchievement('${ach.key || ach.name}')">
        ▶ Run with Account 2 ${state.dryRun ? '(Dry Run)' : ''}
      </button>
    </div>`;
  }

  if (ach.automation_class === 'HUMAN' || ach.automation_class === 'COMMUNITY') {
    return `<div class="ach-hint">${esc(ach.human_action_hint || 'Manual action required.')}</div>`;
  }

  if (ach.automation_class === 'PAYMENT') {
    return `<div class="ach-action">
      <a href="https://github.com/sponsors" target="_blank" rel="noopener" class="ach-run-btn">
        💝 Open GitHub Sponsors
      </a>
    </div>`;
  }

  return '';
}

async function runSingleAchievement(key) {
  if (!state.dryRun) {
    const confirmed = confirm(`Run ${key} in LIVE mode? This will perform a real GitHub action.`);
    if (!confirmed) return;
  }

  showToast(`Running ${key}…`, 'info');
  try {
    const res = await apiFetch('/api/execute', {
      method: 'POST',
      body: JSON.stringify({
        account_id: state.account,
        second_account_id: 2,
        dry_run: state.dryRun,
        achievement_keys: [key],
      }),
    });
    const results = res.results || [];
    let successCount = 0;
    results.forEach(evt => {
      if (evt.type === 'result') {
        showToast(
          evt.data.success ? `✅ ${evt.data.message}` : `❌ ${evt.data.message}`,
          evt.data.success ? 'success' : 'error'
        );
        appendLog({ action: evt.data.action, result: evt.data.message, achievement: key, dry_run: evt.data.dry_run ? 1 : 0 });
        if (evt.data.success) successCount++;
      }
    });
    
    // Automatically re-scan to update the UI if we ran in live mode and succeeded
    if (!state.dryRun && successCount > 0) {
      showToast('⏳ Waiting for GitHub to process changes...', 'info');
      await sleep(4000); // Wait for GitHub events to propagate
      await runScan();
    }
  } catch (err) {
    showToast('❌ ' + err.message, 'error');
  }
}

// ── Render plan ───────────────────────────────────────────────
function renderPlan(plan, summary) {
  const list = document.getElementById('planList');
  list.innerHTML = '';

  const executable = plan.filter(p => p.is_executable);
  const nonExec    = plan.filter(p => !p.is_executable);

  [...executable, ...nonExec].forEach((item, i) => {
    const div = document.createElement('div');
    div.className = 'plan-item';
    div.innerHTML = `
      <div class="plan-num">${i + 1}</div>
      <span class="plan-emoji">${item.emoji}</span>
      <div class="plan-body">
        <div class="plan-name">${esc(item.display_name)}</div>
        <div class="plan-desc">${esc(item.description)}</div>
      </div>
      <span class="plan-class class-${item.classification}">${item.classification.replace('_', ' ')}</span>
    `;
    list.appendChild(div);
  });

  if (summary) {
    showToast(
      `Plan: ${summary.executable_count} executable, ${summary.human_required_count} human required`,
      'info'
    );
  }
}

// ── Summary cards ─────────────────────────────────────────────
function updateSummaryCards(achievements) {
  const counts = {
    EARNED: 0, IN_PROGRESS: 0,
    AUTOMATABLE: 0, SEMI_AUTO: 0,
    HUMAN_REQUIRED: 0, COMMUNITY_REQUIRED: 0,
    PAYMENT_REQUIRED: 0, UNOBTAINABLE: 0,
  };

  achievements.forEach(a => {
    if (a.status in counts) counts[a.status]++;
  });

  setText('countEarned',       counts.EARNED);
  setText('countAuto',         counts.AUTOMATABLE + counts.SEMI_AUTO);
  setText('countProgress',     counts.IN_PROGRESS);
  setText('countHuman',        counts.HUMAN_REQUIRED + counts.COMMUNITY_REQUIRED);
  setText('countPayment',      counts.PAYMENT_REQUIRED);
  setText('countUnobtainable', counts.UNOBTAINABLE);
}

// ── Log ───────────────────────────────────────────────────────
function appendLog(entry) {
  const container = document.getElementById('logContainer');
  const empty = container.querySelector('.log-empty');
  if (empty) empty.remove();

  const ts   = entry.timestamp ? new Date(entry.timestamp).toLocaleTimeString() : new Date().toLocaleTimeString();
  const isDry = entry.dry_run;
  const result = (entry.result || '').toUpperCase();
  const success = result.includes('SUCCESS') || result.includes('OK');
  const error   = result.includes('ERROR') || result.includes('FAILED') || result.includes('STOPPED');

  const cls = error ? 'error' : (isDry ? 'dry' : (success ? 'success' : ''));

  const row = document.createElement('div');
  row.className = `log-entry ${cls}`;
  row.innerHTML = `
    <span class="log-ts">${ts}</span>
    <span class="log-acc">acc${entry.account_id || state.account}</span>
    <span class="log-action">${esc(entry.action || '')}${entry.achievement ? ` [${esc(entry.achievement)}]` : ''}</span>
    <span class="log-result ${success ? 'ok' : (error ? 'err' : '')}">${esc(result.slice(0, 30))}</span>
  `;

  container.insertBefore(row, container.firstChild);

  // Keep max 200 entries
  while (container.children.length > 200) {
    container.removeChild(container.lastChild);
  }
}

function clearLog() {
  document.getElementById('logContainer').innerHTML =
    '<div class="log-empty">Log cleared.</div>';
}

// ── Workflow steps ────────────────────────────────────────────
function showWorkflow(show) {
  document.getElementById('workflowSteps').style.display = show ? 'flex' : 'none';
}

function setStep(id, state) {
  const el = document.getElementById(id);
  if (!el) return;
  el.classList.remove('active', 'done', 'error');
  el.classList.add(state);

  if (state === 'done') {
    el.querySelector('.step-indicator').textContent = '✓';
  } else if (state === 'error') {
    el.querySelector('.step-indicator').textContent = '✕';
  }
}

// ── Stop banner ───────────────────────────────────────────────
function showStopBanner(reason) {
  document.getElementById('stopBanner').style.display = 'flex';
  document.getElementById('stopReasonText').textContent = reason;
}

// ── Toast notifications ───────────────────────────────────────
function showToast(message, type = 'info') {
  const container = document.getElementById('toastContainer');
  const toast = document.createElement('div');
  toast.className = `toast ${type}`;
  toast.textContent = message;
  container.appendChild(toast);
  setTimeout(() => {
    toast.style.opacity = '0';
    toast.style.transform = 'translateX(100%)';
    toast.style.transition = 'all 0.3s';
    setTimeout(() => toast.remove(), 300);
  }, 4000);
}

// ── Modal ─────────────────────────────────────────────────────
function openModal(html) {
  document.getElementById('modalContent').innerHTML = html;
  document.getElementById('modalOverlay').classList.add('open');
}

function closeModal() {
  document.getElementById('modalOverlay').classList.remove('open');
}

// ── Helpers ───────────────────────────────────────────────────
async function apiFetch(url, opts = {}) {
  const defaults = {
    headers: { 'Content-Type': 'application/json' },
  };
  const res = await fetch(url, { ...defaults, ...opts, headers: { ...defaults.headers, ...opts.headers } });
  const json = await res.json();
  if (!res.ok) throw new Error(json.detail || json.message || `HTTP ${res.status}`);
  return json;
}

function setText(id, val) {
  const el = document.getElementById(id);
  if (el) el.textContent = String(val);
}

function setCTASub(text) {
  const el = document.getElementById('ctaSub');
  if (el) el.textContent = text;
}

function scrollToId(id) {
  document.getElementById(id)?.scrollIntoView({ behavior: 'smooth', block: 'start' });
}

function sleep(ms) { return new Promise(r => setTimeout(r, ms)); }

function esc(str) {
  return String(str || '')
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;');
}

function statusIcon(status) {
  const icons = {
    EARNED: '✅', IN_PROGRESS: '⏳', AUTOMATABLE: '🤖',
    SEMI_AUTO: '🤝', HUMAN_REQUIRED: '👤', COMMUNITY_REQUIRED: '🌍',
    PAYMENT_REQUIRED: '💳', UNOBTAINABLE: '🚫',
    PENDING_VERIFICATION: '⏳', UNKNOWN: '❓',
  };
  return icons[status] || '•';
}

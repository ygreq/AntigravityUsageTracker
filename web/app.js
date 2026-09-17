// Antigravity Usage Tracker & Pacing Dashboard
let multiPeriodChart = null;
let comparativeChart = null;
let dayCompChart = null;

let currentGranularity = '1h';
let secondsUntilNextPoll = 0;
let countdownTimer = null;
let currentLogOffset = 0;
const LOG_PAGE_SIZE = 50;
let searchDebounceTimeout = null;

document.addEventListener('DOMContentLoaded', async () => {
  if (window.lucide) {
    lucide.createIcons();
  }
  initCharts();
  window.addEventListener('resize', handleResize);

  await loadSettings();
  await refreshDashboard();
  await loadUsageLogs(0);

  // Start countdown ticker (updates every 1s)
  startCountdownTicker();

  // Polling check every 30s
  setInterval(refreshDashboard, 30000);
});

function initCharts() {
  multiPeriodChart = echarts.init(document.getElementById('multiPeriodChart'));
  comparativeChart = echarts.init(document.getElementById('comparativeChart'));
  dayCompChart = echarts.init(document.getElementById('dayCompChart'));
}

function handleResize() {
  multiPeriodChart && multiPeriodChart.resize();
  comparativeChart && comparativeChart.resize();
  dayCompChart && dayCompChart.resize();
}

async function loadSettings() {
  try {
    const res = await fetch('/api/settings');
    const data = await res.json();
    if (data.poll_interval_minutes) {
      document.getElementById('pollIntervalSelect').value = String(data.poll_interval_minutes);
    }
  } catch (e) {
    console.error('Failed to load settings:', e);
  }
}

async function updatePollInterval(val) {
  try {
    const res = await fetch('/api/settings', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ poll_interval_minutes: parseInt(val, 10) })
    });
    const result = await res.json();
    if (result.status === 'success') {
      await refreshDashboard();
    }
  } catch (e) {
    console.error('Failed to update polling interval:', e);
  }
}

async function triggerManualPoll() {
  const btn = document.getElementById('pollBtn');
  const icon = document.getElementById('refreshIcon');
  if (btn) btn.disabled = true;
  if (icon) icon.classList.add('animate-spin');
  try {
    await fetch('/api/poll', { method: 'POST' });
    await refreshDashboard();
  } catch (e) {
    console.error('Manual poll failed:', e);
  } finally {
    if (btn) btn.disabled = false;
    if (icon) icon.classList.remove('animate-spin');
  }
}

function startCountdownTicker() {
  if (countdownTimer) clearInterval(countdownTimer);
  countdownTimer = setInterval(() => {
    if (secondsUntilNextPoll > 0) {
      secondsUntilNextPoll--;
      const m = Math.floor(secondsUntilNextPoll / 60);
      const s = secondsUntilNextPoll % 60;
      document.getElementById('countdownText').textContent = `Next: ${m}m ${s < 10 ? '0' : ''}${s}s`;
    } else {
      document.getElementById('countdownText').textContent = `Checking...`;
    }
  }, 1000);
}

let activeChartBucket = 'gemini-weekly';

async function switchChartModel(bucketId) {
  activeChartBucket = bucketId;
  const gemBtn = document.getElementById('chartModelGemini');
  const claudeBtn = document.getElementById('chartModelClaude');

  if (bucketId === 'gemini-weekly') {
    gemBtn.className = 'px-3 py-1 rounded-md transition bg-teal-600 text-white shadow-sm flex items-center space-x-1.5';
    claudeBtn.className = 'px-3 py-1 rounded-md transition text-gray-400 hover:text-white flex items-center space-x-1.5';
  } else {
    claudeBtn.className = 'px-3 py-1 rounded-md transition bg-indigo-600 text-white shadow-sm flex items-center space-x-1.5';
    gemBtn.className = 'px-3 py-1 rounded-md transition text-gray-400 hover:text-white flex items-center space-x-1.5';
  }

  // Reload charts with active model
  await renderMultiPeriodChart(currentGranularity);

  const compRes = await fetch(`/api/comparative?bucket_id=${bucketId}`);
  const compData = await compRes.json();
  renderComparativeChart(compData);

  const dayRes = await fetch(`/api/day-comparison?bucket_id=${bucketId}`);
  const dayData = await dayRes.json();
  renderDayCompChart(dayData);
}

async function refreshDashboard() {
  try {
    // 1. Fetch live status & pacing for both models
    const statusRes = await fetch('/api/status');
    const statusData = await statusRes.json();
    updateHeaderAndCards(statusData);

    // 2. Render Multi-Period Chart for active model
    await renderMultiPeriodChart(currentGranularity);

    // 3. Render Comparative Cycles & Day Comparison for active model
    const compRes = await fetch(`/api/comparative?bucket_id=${activeChartBucket}`);
    const compData = await compRes.json();
    renderComparativeChart(compData);

    const dayRes = await fetch(`/api/day-comparison?bucket_id=${activeChartBucket}`);
    const dayData = await dayRes.json();
    renderDayCompChart(dayData);

    if (window.lucide) lucide.createIcons();
  } catch (e) {
    console.error('Error refreshing dashboard:', e);
  }
}

function formatResetDateTime(isoString) {
  if (!isoString) return 'N/A';
  const d = new Date(isoString);
  if (isNaN(d.getTime())) return 'N/A';
  return d.toLocaleString('en-US', {
    month: 'short',
    day: 'numeric',
    hour: 'numeric',
    minute: '2-digit',
    hour12: true
  });
}

function getRemainingHoursMins(isoString, cd) {
  if (cd && cd.total_hours_mins) return cd.total_hours_mins;
  if (!isoString) return 'N/A';
  const diffSec = Math.max(0, Math.floor((new Date(isoString) - new Date()) / 1000));
  const h = Math.floor(diffSec / 3600);
  const m = Math.floor((diffSec % 3600) / 60);
  return `${h}h ${m}m`;
}

function updateHeaderAndCards(data) {
  if (data.poller) {
    secondsUntilNextPoll = data.poller.seconds_until_next_poll || 0;
  }

  const latest = data.latest_snapshots || [];
  const gemini = latest.find(s => s.bucket_id === 'gemini-weekly');
  const fiveH = latest.find(s => s.bucket_id === 'gemini-5h');
  const claude = latest.find(s => s.bucket_id === '3p-weekly');
  const claude5h = latest.find(s => s.bucket_id === '3p-5h');

  // --- 1. Gemini Cards ---
  if (gemini) {
    const remPct = (gemini.remaining_fraction * 100).toFixed(1);
    document.getElementById('geminiRemainingVal').textContent = `${remPct}%`;
    document.getElementById('geminiProgress').style.width = `${remPct}%`;
    document.getElementById('geminiResetText').textContent = formatResetDateTime(gemini.reset_time);
    
    // Explicit Days and Hours + Hours & Minutes countdown
    const cd = gemini.countdown;
    const cdText = (cd && cd.text) ? cd.text : 'N/A';
    const underElem = document.getElementById('geminiResetCountdownUnder');
    if (underElem) underElem.textContent = cdText;
    const hmElem = document.getElementById('geminiResetHoursMins');
    if (hmElem) hmElem.textContent = getRemainingHoursMins(gemini.reset_time, cd);
  }

  if (fiveH) {
    const remPct = (fiveH.remaining_fraction * 100).toFixed(1);
    document.getElementById('fiveHourRemainingVal').textContent = `${remPct}%`;
    document.getElementById('fiveHourProgress').style.width = `${remPct}%`;
    document.getElementById('fiveHourResetText').textContent = formatResetDateTime(fiveH.reset_time);

    const cd = fiveH.countdown;
    const cdText = (cd && cd.text) ? cd.text : 'N/A';
    const underElem = document.getElementById('fiveHourResetCountdownUnder');
    if (underElem) underElem.textContent = cdText;
    const hmElem = document.getElementById('fiveHourResetHoursMins');
    if (hmElem) hmElem.textContent = getRemainingHoursMins(fiveH.reset_time, cd);
  }

  // Gemini Pacing
  const pacing = data.primary_pacing;
  if (pacing) {
    const m = pacing.metrics;
    const s = pacing.status;
    const delta = m.variance_delta_percent;
    const sign = delta > 0 ? '+' : '';
    document.getElementById('pacingDeltaVal').textContent = `${sign}${delta.toFixed(1)}%`;
    document.getElementById('pacingSummaryText').textContent = s.summary;

    const badge = document.getElementById('pacingBadge');
    badge.textContent = s.code.replace('_', ' ');
    if (s.code === 'OVER_BURNING') {
      badge.className = 'px-2 py-0.5 rounded text-xs font-semibold bg-red-950/80 text-red-400 border border-red-800/50';
      document.getElementById('pacingDeltaVal').className = 'text-3xl font-bold font-mono text-red-400';
    } else if (s.code === 'UNDER_BURNING') {
      badge.className = 'px-2 py-0.5 rounded text-xs font-semibold bg-blue-950/80 text-blue-400 border border-blue-800/50';
      document.getElementById('pacingDeltaVal').className = 'text-3xl font-bold font-mono text-blue-400';
    } else {
      badge.className = 'px-2 py-0.5 rounded text-xs font-semibold bg-emerald-950/80 text-emerald-400 border border-emerald-800/50';
      document.getElementById('pacingDeltaVal').className = 'text-3xl font-bold font-mono text-emerald-400';
    }

    document.getElementById('targetBurnRateVal').textContent = `${m.target_burn_rate_percent_hr.toFixed(2)}`;
    document.getElementById('currentVelocityVal').textContent = `${m.current_velocity_percent_hr.toFixed(2)} %/hr`;
    
    if (m.projected_exhaustion_time) {
      const exDate = new Date(m.projected_exhaustion_time).toLocaleDateString(undefined, { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' });
      document.getElementById('projectedExhaustionText').textContent = `Exhausts: ${exDate}`;
    } else {
      document.getElementById('projectedExhaustionText').textContent = `Exhausts: After reset (safe)`;
    }
  }

  // --- 2. Claude & GPT Cards ---
  if (claude) {
    const remPct = (claude.remaining_fraction * 100).toFixed(1);
    document.getElementById('claudeWeeklyVal').textContent = `${remPct}%`;
    document.getElementById('claudeWeeklyProgress').style.width = `${remPct}%`;
    document.getElementById('claudeWeeklyResetText').textContent = formatResetDateTime(claude.reset_time);

    const cd = claude.countdown;
    const cdText = (cd && cd.text) ? cd.text : 'N/A';
    const underElem = document.getElementById('claudeWeeklyCountdownUnder');
    if (underElem) underElem.textContent = cdText;
    const hmElem = document.getElementById('claudeWeeklyHoursMins');
    if (hmElem) hmElem.textContent = getRemainingHoursMins(claude.reset_time, cd);
  }

  if (claude5h) {
    const remPct = (claude5h.remaining_fraction * 100).toFixed(1);
    document.getElementById('claude5hVal').textContent = `${remPct}%`;
    document.getElementById('claude5hProgress').style.width = `${remPct}%`;
    document.getElementById('claude5hResetText').textContent = formatResetDateTime(claude5h.reset_time);

    const cd = claude5h.countdown;
    const cdText = (cd && cd.text) ? cd.text : 'N/A';
    const underElem = document.getElementById('claude5hCountdownUnder');
    if (underElem) underElem.textContent = cdText;
    const hmElem = document.getElementById('claude5hHoursMins');
    if (hmElem) hmElem.textContent = getRemainingHoursMins(claude5h.reset_time, cd);
  }

  // Claude & GPT Pacing
  const cPacing = data.claude_gpt_pacing;
  if (cPacing) {
    const cm = cPacing.metrics;
    const cs = cPacing.status;
    const cDelta = cm.variance_delta_percent;
    const cSign = cDelta > 0 ? '+' : '';
    document.getElementById('claudePacingDeltaVal').textContent = `${cSign}${cDelta.toFixed(1)}%`;
    document.getElementById('claudePacingSummary').textContent = cs.summary;

    const cBadge = document.getElementById('claudePacingBadge');
    cBadge.textContent = cs.code.replace('_', ' ');
    if (cs.code === 'OVER_BURNING') {
      cBadge.className = 'px-2 py-0.5 rounded text-xs font-semibold bg-red-950/80 text-red-400 border border-red-800/50';
    } else if (cs.code === 'UNDER_BURNING') {
      cBadge.className = 'px-2 py-0.5 rounded text-xs font-semibold bg-indigo-950/80 text-indigo-300 border border-indigo-800/50';
    } else {
      cBadge.className = 'px-2 py-0.5 rounded text-xs font-semibold bg-emerald-950/80 text-emerald-400 border border-emerald-800/50';
    }

    document.getElementById('claudeTargetBurnVal').textContent = `${cm.target_burn_rate_percent_hr.toFixed(2)}`;
    document.getElementById('claudeVelocityVal').textContent = `${cm.current_velocity_percent_hr.toFixed(2)} %/hr`;
    if (cm.projected_exhaustion_time) {
      const exDate = new Date(cm.projected_exhaustion_time).toLocaleDateString(undefined, { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' });
      document.getElementById('claudeExhaustionText').textContent = `Exhausts: ${exDate}`;
    } else {
      document.getElementById('claudeExhaustionText').textContent = `Full budget available`;
    }
  }
}

async function triggerManualPoll() {
  const icon = document.getElementById('refreshIcon');
  icon.classList.add('animate-spin');
  try {
    await fetch('/api/poll', { method: 'POST' });
    setTimeout(async () => {
      await refreshDashboard();
      await loadUsageLogs(0);
      icon.classList.remove('animate-spin');
    }, 4500);
  } catch (e) {
    icon.classList.remove('animate-spin');
  }
}

// --- Multi-Period Chart ---
async function switchGranularity(granularity) {
  currentGranularity = granularity;
  ['1h', '5h', '1d', '1w'].forEach(g => {
    const btn = document.getElementById(`tab-${g}`);
    if (g === granularity) {
      btn.className = 'px-3 py-1 rounded-md transition bg-teal-600 text-white shadow-sm';
    } else {
      btn.className = 'px-3 py-1 rounded-md transition text-gray-400 hover:text-white';
    }
  });
  await renderMultiPeriodChart(granularity);
}

async function renderMultiPeriodChart(granularity) {
  const res = await fetch(`/api/multi-period?granularity=${granularity}&bucket_id=gemini-weekly`);
  const data = await res.json();

  const option = {
    backgroundColor: 'transparent',
    tooltip: {
      trigger: 'axis',
      backgroundColor: '#1f2937',
      borderColor: '#374151',
      textStyle: { color: '#f3f4f6', fontFamily: 'JetBrains Mono', fontSize: 12 }
    },
    grid: { left: '3%', right: '4%', bottom: '3%', top: '12%', containLabel: true },
    xAxis: {
      type: 'category',
      data: data.labels,
      axisLine: { lineStyle: { color: '#374151' } },
      axisLabel: {
        color: '#9ca3af',
        fontFamily: 'JetBrains Mono',
        formatter: (val) => val.length > 13 ? val.substring(5, 16) : val
      }
    },
    yAxis: [
      {
        type: 'value',
        name: 'Consumed %',
        splitLine: { lineStyle: { color: '#1f2937' } },
        axisLabel: { color: '#9ca3af', fontFamily: 'JetBrains Mono', formatter: '{value}%' }
      },
      {
        type: 'value',
        name: 'Remaining %',
        min: 0,
        max: 100,
        splitLine: { show: false },
        axisLabel: { color: '#9ca3af', fontFamily: 'JetBrains Mono', formatter: '{value}%' }
      }
    ],
    series: [
      {
        name: 'Quota Consumed',
        type: 'bar',
        data: data.consumed,
        itemStyle: {
          color: new echarts.graphic.LinearGradient(0, 0, 0, 1, [
            { offset: 0, color: '#3b82f6' },
            { offset: 1, color: '#1d4ed8' }
          ]),
          borderRadius: [4, 4, 0, 0]
        }
      },
      {
        name: 'Remaining Quota',
        type: 'line',
        yAxisIndex: 1,
        data: data.remaining,
        smooth: true,
        lineStyle: { color: '#10b981', width: 2 },
        symbol: 'none'
      }
    ]
  };

  multiPeriodChart.setOption(option, true);
}

// --- Comparative Cycles Chart ---
function renderComparativeChart(data) {
  if (!data || !data.cycles) return;

  const series = data.cycles.map((c, idx) => {
    const isCurrent = c.is_current;
    return {
      name: c.name,
      type: 'line',
      smooth: true,
      data: c.points.map(pt => [pt.elapsed_hours, pt.used_percent]),
      lineStyle: {
        width: isCurrent ? 3 : 1.5,
        color: isCurrent ? '#14b8a6' : ['#a855f7', '#ec4899', '#f97316'][idx % 3],
        type: isCurrent ? 'solid' : 'dotted'
      },
      symbol: 'none'
    };
  });

  const option = {
    backgroundColor: 'transparent',
    tooltip: {
      trigger: 'axis',
      backgroundColor: '#1f2937',
      borderColor: '#374151',
      textStyle: { color: '#f3f4f6', fontFamily: 'JetBrains Mono', fontSize: 12 }
    },
    legend: { textStyle: { color: '#9ca3af' }, top: '0%' },
    grid: { left: '3%', right: '4%', bottom: '3%', top: '16%', containLabel: true },
    xAxis: {
      type: 'value',
      name: 'Elapsed Hours in Cycle',
      min: 0,
      max: 120,
      splitLine: { lineStyle: { color: '#1f2937' } },
      axisLabel: { color: '#9ca3af', fontFamily: 'JetBrains Mono', formatter: '{value}h' }
    },
    yAxis: {
      type: 'value',
      name: 'Used %',
      min: 0,
      max: 100,
      splitLine: { lineStyle: { color: '#1f2937' } },
      axisLabel: { color: '#9ca3af', fontFamily: 'JetBrains Mono', formatter: '{value}%' }
    },
    series: series
  };

  comparativeChart.setOption(option);
}

// --- Today vs Yesterday Chart ---
function renderDayCompChart(data) {
  if (!data) return;

  const option = {
    backgroundColor: 'transparent',
    tooltip: {
      trigger: 'axis',
      backgroundColor: '#1f2937',
      borderColor: '#374151',
      textStyle: { color: '#f3f4f6', fontFamily: 'JetBrains Mono', fontSize: 12 }
    },
    legend: { textStyle: { color: '#9ca3af' }, top: '0%' },
    grid: { left: '3%', right: '4%', bottom: '3%', top: '16%', containLabel: true },
    xAxis: {
      type: 'category',
      data: data.hours,
      axisLine: { lineStyle: { color: '#374151' } },
      axisLabel: { color: '#9ca3af', fontFamily: 'JetBrains Mono' }
    },
    yAxis: {
      type: 'value',
      name: 'Delta Consumed (%)',
      splitLine: { lineStyle: { color: '#1f2937' } },
      axisLabel: { color: '#9ca3af', fontFamily: 'JetBrains Mono', formatter: '{value}%' }
    },
    series: [
      {
        name: 'Yesterday',
        type: 'bar',
        data: data.yesterday,
        itemStyle: { color: '#475569', borderRadius: [3, 3, 0, 0] }
      },
      {
        name: 'Today',
        type: 'bar',
        data: data.today,
        itemStyle: { color: '#f59e0b', borderRadius: [3, 3, 0, 0] }
      }
    ]
  };

  dayCompChart.setOption(option);
}

// --- Historical Usage Logger & Audit Trail ---
function debounceLogSearch() {
  clearTimeout(searchDebounceTimeout);
  searchDebounceTimeout = setTimeout(() => {
    loadUsageLogs(0);
  }, 300);
}

async function loadUsageLogs(offset = 0) {
  currentLogOffset = offset;
  const bucket = document.getElementById('logBucketFilter').value;
  const search = document.getElementById('logSearchInput').value.trim();

  // Update export CSV link
  const exportBtn = document.getElementById('exportCsvBtn');
  exportBtn.href = `/api/logs/export.csv?${bucket ? 'bucket_id=' + encodeURIComponent(bucket) : ''}`;

  let url = `/api/logs?limit=${LOG_PAGE_SIZE}&offset=${offset}`;
  if (bucket) url += `&bucket_id=${encodeURIComponent(bucket)}`;
  if (search) url += `&search=${encodeURIComponent(search)}`;

  try {
    const res = await fetch(url);
    const data = await res.json();
    renderLogsTable(data);
  } catch (e) {
    console.error('Failed to load usage logs:', e);
  }
}

function renderLogsTable(data) {
  const tbody = document.getElementById('logsTableBody');
  const logs = data.logs || [];
  const total = data.total || 0;

  document.getElementById('logTotalCount').textContent = `${total} entries`;
  const start = total > 0 ? currentLogOffset + 1 : 0;
  const end = Math.min(currentLogOffset + logs.length, total);
  document.getElementById('logPageInfo').textContent = `Showing ${start}-${end} of ${total}`;

  document.getElementById('logPrevBtn').disabled = (currentLogOffset === 0);
  document.getElementById('logNextBtn').disabled = (end >= total);

  if (logs.length === 0) {
    tbody.innerHTML = `<tr><td colspan="6" class="text-center py-6 text-gray-500 font-sans">No audit records found matching query.</td></tr>`;
    return;
  }

  tbody.innerHTML = logs.map(r => {
    const remPct = (r.remaining_fraction * 100).toFixed(2);
    const usedPct = (r.used_fraction * 100).toFixed(2);
    const deltaPct = (r.delta_used * 100).toFixed(3);
    
    const remBadge = remPct > 30 ? 'text-emerald-400' : (remPct > 15 ? 'text-yellow-400' : 'text-red-400');
    const deltaBadge = r.delta_used > 0 ? '<span class="text-rose-400">+' + deltaPct + '%</span>' : '<span class="text-gray-500">0.0%</span>';
    
    const dateFormatted = r.timestamp.replace('T', ' ').replace('Z', '');
    const resetFormatted = r.reset_time ? r.reset_time.replace('T', ' ').replace('Z', '') : 'N/A';

    return `
      <tr class="hover:bg-gray-800/40 transition">
        <td class="py-2.5 px-4 text-gray-400">${dateFormatted}</td>
        <td class="py-2.5 px-4">
          <div class="font-sans font-medium text-gray-200">${r.group_name}</div>
          <div class="text-[10px] text-gray-500">${r.bucket_id}</div>
        </td>
        <td class="py-2.5 px-4 text-right font-bold ${remBadge}">${remPct}%</td>
        <td class="py-2.5 px-4 text-right text-gray-400">${usedPct}%</td>
        <td class="py-2.5 px-4 text-right font-mono">${deltaBadge}</td>
        <td class="py-2.5 px-4 text-gray-500">${resetFormatted}</td>
      </tr>
    `;
  }).join('');
}

function prevLogPage() {
  if (currentLogOffset >= LOG_PAGE_SIZE) {
    loadUsageLogs(currentLogOffset - LOG_PAGE_SIZE);
  }
}

function nextLogPage() {
  loadUsageLogs(currentLogOffset + LOG_PAGE_SIZE);
}

function toggleAboutModal(show) {
  const modal = document.getElementById('aboutModal');
  if (!modal) return;
  if (show) {
    modal.classList.remove('hidden');
  } else {
    modal.classList.add('hidden');
  }
}

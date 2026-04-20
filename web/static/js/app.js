/* ═══════════════════════════════════════════════════════════
   OPTIONS TRADING ENGINE — Frontend Logic
   Real-time polling, charts, data rendering
   ═══════════════════════════════════════════════════════════ */

// ─── State ──────────────────────────────────────────────
let currentSection = 'dashboard';
let charts = {};
let refreshInterval = null;

// ─── Section Navigation ─────────────────────────────────
function switchSection(section) {
    document.querySelectorAll('.section').forEach(s => s.classList.remove('active'));
    document.querySelectorAll('.nav-item').forEach(n => n.classList.remove('active'));

    const secEl = document.getElementById(`sec-${section}`);
    const navEl = document.querySelector(`[data-section="${section}"]`);

    if (secEl) secEl.classList.add('active');
    if (navEl) navEl.classList.add('active');

    currentSection = section;

    const titles = {
        'dashboard': 'Dashboard', 'option-chain': 'Option Chain',
        'signals': 'Signals', 'greeks': 'Greeks', 'sentiment': 'Sentiment',
        'trades': 'Trade Journal', 'backtest': 'Backtest', 'settings': 'Settings'
    };
    document.getElementById('page-title').textContent = titles[section] || 'Dashboard';

    // Load section data
    loadSectionData(section);
}

// ─── Clock ──────────────────────────────────────────────
function updateClock() {
    const now = new Date();
    const h = String(now.getHours()).padStart(2, '0');
    const m = String(now.getMinutes()).padStart(2, '0');
    const s = String(now.getSeconds()).padStart(2, '0');
    document.getElementById('live-time').textContent = `${h}:${m}:${s}`;
}

// ─── API Fetcher ────────────────────────────────────────
async function api(endpoint) {
    try {
        const res = await fetch(`/api/${endpoint}`);
        if (!res.ok) throw new Error(`HTTP ${res.status}`);
        return await res.json();
    } catch (e) {
        console.error(`API error [${endpoint}]:`, e);
        return null;
    }
}

// ─── Format Helpers ─────────────────────────────────────
function formatCurrency(val) {
    const n = parseFloat(val) || 0;
    const prefix = n >= 0 ? '' : '-';
    return `${prefix}\u20B9${Math.abs(n).toLocaleString('en-IN', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;
}

function formatNum(val, dec = 2) {
    return (parseFloat(val) || 0).toFixed(dec);
}

function pnlClass(val) {
    const n = parseFloat(val) || 0;
    return n > 0 ? 'positive' : n < 0 ? 'negative' : 'neutral';
}

function badgeClass(label) {
    const l = (label || '').toLowerCase().replace('_', '-');
    if (['bullish', 'buy', 'strong-buy', 'strong_buy'].includes(l)) return 'bullish';
    if (['bearish', 'sell', 'strong-sell', 'strong_sell'].includes(l)) return 'bearish';
    if (['sideways'].includes(l)) return 'sideways';
    return 'neutral';
}

// ─── Load Section Data ──────────────────────────────────
async function loadSectionData(section) {
    switch (section) {
        case 'dashboard': await loadDashboard(); break;
        case 'option-chain': await loadOptionChain(); break;
        case 'signals': await loadSignals(); break;
        case 'greeks': await loadGreeks(); break;
        case 'sentiment': await loadSentiment(); break;
        case 'trades': await loadTrades(); break;
    }
}

// ═══════════════════════════════════════════════════════════
//  DASHBOARD
// ═══════════════════════════════════════════════════════════

async function loadDashboard() {
    const data = await api('dashboard');
    if (!data || !data.portfolio) return;

    const p = data.portfolio;

    // Market status
    const badge = document.getElementById('market-badge');
    const statusText = document.getElementById('market-status-text');
    if (data.market_status === 'OPEN') {
        badge.className = 'market-badge open';
        statusText.textContent = 'LIVE';
    } else {
        badge.className = 'market-badge closed';
        statusText.textContent = 'CLOSED';
    }

    // Stats
    document.getElementById('stat-pnl').textContent = formatCurrency(p.total_pnl);
    document.getElementById('stat-pnl').className = `stat-value ${pnlClass(p.total_pnl)}`;
    document.getElementById('stat-pnl-pct').textContent = `${formatNum(p.total_pnl_pct)}%`;

    document.getElementById('stat-capital').textContent = formatCurrency(p.available_capital);
    document.getElementById('stat-positions').textContent = p.open_positions;
    document.getElementById('stat-daily-trades').textContent = `${p.daily_trades} trades today`;
    document.getElementById('stat-winrate').textContent = `${p.win_rate}%`;
    document.getElementById('stat-total-trades').textContent = `${p.total_closed_trades} total trades`;

    // Positions table
    const tbody = document.getElementById('positions-body');
    if (p.positions && p.positions.length > 0) {
        tbody.innerHTML = p.positions.map(pos => `
            <tr>
                <td>${pos.symbol}</td>
                <td>${pos.option_type}</td>
                <td>${pos.strike}</td>
                <td><span class="badge ${pos.side === 'BUY' ? 'bullish' : 'bearish'}">${pos.side}</span></td>
                <td>${formatCurrency(pos.entry_price)}</td>
                <td>${formatCurrency(pos.current_price)}</td>
                <td style="color:var(--${pos.unrealized_pnl >= 0 ? 'green' : 'red'})">${formatCurrency(pos.unrealized_pnl)}</td>
                <td>${pos.strategy}</td>
            </tr>
        `).join('');
    } else {
        tbody.innerHTML = '<tr><td colspan="8" class="empty-state">No open positions</td></tr>';
    }

    // ── Equity Curve Chart ───────────────────────────
    const eqCtx = document.getElementById('equity-chart');
    if (eqCtx) {
        if (charts.dashEquity) charts.dashEquity.destroy();

        // Build equity history from closed trades
        const trades = data.closed_trades || [];
        let running = p.initial_capital || 500000;
        const eqData = [running];
        trades.forEach(t => { running += (t.net_pnl || 0); eqData.push(running); });

        // If no trades yet, show flat line at initial capital
        if (eqData.length < 2) eqData.push(running);

        charts.dashEquity = new Chart(eqCtx, {
            type: 'line',
            data: {
                labels: eqData.map((_, i) => i === 0 ? 'Start' : `Trade ${i}`),
                datasets: [{
                    label: 'Equity',
                    data: eqData,
                    borderColor: eqData[eqData.length-1] >= eqData[0] ? 'rgba(48, 209, 88, 0.8)' : 'rgba(255, 69, 58, 0.8)',
                    backgroundColor: eqData[eqData.length-1] >= eqData[0] ? 'rgba(48, 209, 88, 0.08)' : 'rgba(255, 69, 58, 0.08)',
                    fill: true, borderWidth: 2, pointRadius: 0,
                    pointHoverRadius: 6, pointHoverBackgroundColor: '#fff',
                    pointHoverBorderWidth: 2, tension: 0.3,
                }]
            },
            options: {
                responsive: true, maintainAspectRatio: false,
                interaction: { mode: 'index', intersect: false },
                plugins: {
                    legend: { display: false },
                    tooltip: {
                        enabled: true,
                        backgroundColor: 'rgba(28, 28, 30, 0.95)',
                        titleColor: '#f5f5f7', bodyColor: '#f5f5f7',
                        borderColor: 'rgba(255,255,255,0.1)', borderWidth: 1,
                        padding: 12, cornerRadius: 8, displayColors: false,
                        titleFont: { family: 'Inter', size: 13, weight: '600' },
                        bodyFont: { family: 'Inter', size: 12 },
                        callbacks: {
                            title: (items) => items[0].label,
                            label: (item) => `\u20b9${parseFloat(item.raw).toLocaleString('en-IN', {minimumFractionDigits: 2})}`,
                        }
                    }
                },
                scales: {
                    x: { display: false },
                    y: {
                        ticks: { color: 'rgba(245,245,247,0.35)', font: { size: 10 }, callback: (v) => '\u20b9' + (v/1000).toFixed(0) + 'K' },
                        grid: { color: 'rgba(255,255,255,0.04)' },
                    },
                }
            }
        });
    }

    // ── Signal Distribution Chart ────────────────────
    const sigCtx = document.getElementById('signal-chart');
    if (sigCtx) {
        if (charts.dashSignal) charts.dashSignal.destroy();

        const sigData = data.signal_distribution || { bullish: 0, bearish: 0, neutral: 0 };
        const vals = [sigData.bullish || 0, sigData.bearish || 0, sigData.neutral || 1];
        const total = vals.reduce((a, b) => a + b, 0);

        charts.dashSignal = new Chart(sigCtx, {
            type: 'doughnut',
            data: {
                labels: ['Bullish', 'Bearish', 'Neutral'],
                datasets: [{
                    data: vals,
                    backgroundColor: ['rgba(48, 209, 88, 0.7)', 'rgba(255, 69, 58, 0.7)', 'rgba(142, 142, 147, 0.5)'],
                    hoverBackgroundColor: ['rgba(48, 209, 88, 1)', 'rgba(255, 69, 58, 1)', 'rgba(142, 142, 147, 0.8)'],
                    borderColor: 'rgba(28, 28, 30, 1)',
                    borderWidth: 3,
                    hoverOffset: 8,
                }]
            },
            options: {
                responsive: true, maintainAspectRatio: false,
                cutout: '65%',
                plugins: {
                    legend: { position: 'bottom', labels: { color: 'rgba(245,245,247,0.6)', font: { family: 'Inter', size: 11 }, padding: 16, usePointStyle: true, pointStyle: 'circle' } },
                    tooltip: {
                        enabled: true,
                        backgroundColor: 'rgba(28, 28, 30, 0.95)',
                        titleColor: '#f5f5f7', bodyColor: '#f5f5f7',
                        borderColor: 'rgba(255,255,255,0.1)', borderWidth: 1,
                        padding: 12, cornerRadius: 8,
                        titleFont: { family: 'Inter', size: 13, weight: '600' },
                        bodyFont: { family: 'Inter', size: 12 },
                        callbacks: {
                            label: (item) => `${item.label}: ${item.raw} (${(item.raw / total * 100).toFixed(1)}%)`,
                        }
                    }
                }
            }
        });
    }
}

// ═══════════════════════════════════════════════════════════
//  OPTION CHAIN
// ═══════════════════════════════════════════════════════════

async function loadOptionChain() {
    const data = await api('option-chain');
    if (!data) return;

    document.getElementById('oc-underlying').textContent = formatCurrency(data.underlying_price);

    // Show data freshness notice
    const trendEl = document.getElementById('oc-trend');
    const a = data.analysis || {};
    document.getElementById('oc-pcr').textContent = formatNum(a.pcr, 4);
    document.getElementById('oc-maxpain').textContent = a.max_pain || '--';

    if (data.data_freshness === 'LAST_CLOSE') {
        trendEl.textContent = (a.trend || '--') + ' (Last Close)';
        trendEl.className = `stat-value neutral`;
    } else {
        trendEl.textContent = a.trend || '--';
        trendEl.className = `stat-value ${badgeClass(a.trend)}`;
    }
    document.getElementById('oc-resistance').textContent = a.resistance || '--';
    document.getElementById('oc-support').textContent = a.support || '--';

    const exitBadge = (val) => `<span class="badge ${val ? 'bullish' : 'neutral'}">${val ? 'YES' : 'NO'}</span>`;
    document.getElementById('oc-call-exits').outerHTML = exitBadge(a.call_exits);
    document.getElementById('oc-put-exits').outerHTML = exitBadge(a.put_exits);
    document.getElementById('oc-call-itm').outerHTML = exitBadge(a.call_itm);
    document.getElementById('oc-put-itm').outerHTML = exitBadge(a.put_itm);

    // Chain table
    const tbody = document.getElementById('chain-body');
    if (data.chain && data.chain.length > 0) {
        const maxOI = Math.max(...data.chain.map(r => Math.max(r.ce_oi || 0, r.pe_oi || 0)), 1);
        tbody.innerHTML = data.chain.map(r => {
            const ceBar = Math.round((r.ce_oi || 0) / maxOI * 80);
            const peBar = Math.round((r.pe_oi || 0) / maxOI * 80);
            const ceChgColor = (r.ce_chg_oi || 0) > 0 ? 'var(--red)' : 'var(--green)';
            const peChgColor = (r.pe_chg_oi || 0) > 0 ? 'var(--green)' : 'var(--red)';
            return `<tr>
                <td class="ce-cell">${(r.ce_oi||0).toLocaleString()} <span class="oi-bar ce" style="width:${ceBar}px"></span></td>
                <td class="ce-cell" style="color:${ceChgColor}">${(r.ce_chg_oi||0).toLocaleString()}</td>
                <td class="ce-cell">${formatNum(r.ce_iv)}</td>
                <td class="ce-cell">${formatNum(r.ce_ltp)}</td>
                <td class="strike-cell">${r.strike}</td>
                <td class="pe-cell">${formatNum(r.pe_ltp)}</td>
                <td class="pe-cell">${formatNum(r.pe_iv)}</td>
                <td class="pe-cell" style="color:${peChgColor}">${(r.pe_chg_oi||0).toLocaleString()}</td>
                <td class="pe-cell"><span class="oi-bar pe" style="width:${peBar}px"></span> ${(r.pe_oi||0).toLocaleString()}</td>
            </tr>`;
        }).join('');
    }

    // OI Chart
    renderOIChart(data.chain || []);
}

function renderOIChart(chain) {
    const ctx = document.getElementById('oi-chart');
    if (!ctx) return;

    if (charts.oi) charts.oi.destroy();

    const labels = chain.map(r => r.strike);
    const ceOI = chain.map(r => r.ce_oi || 0);
    const peOI = chain.map(r => r.pe_oi || 0);

    charts.oi = new Chart(ctx, {
        type: 'bar',
        data: {
            labels,
            datasets: [
                { label: 'Call OI', data: ceOI, backgroundColor: 'rgba(255, 69, 58, 0.6)', hoverBackgroundColor: 'rgba(255, 69, 58, 0.9)', borderRadius: 3 },
                { label: 'Put OI', data: peOI, backgroundColor: 'rgba(48, 209, 88, 0.6)', hoverBackgroundColor: 'rgba(48, 209, 88, 0.9)', borderRadius: 3 },
            ]
        },
        options: {
            responsive: true, maintainAspectRatio: false,
            interaction: { mode: 'index', intersect: false },
            plugins: {
                legend: { labels: { color: 'rgba(245,245,247,0.6)', font: { family: 'Inter', size: 11 } } },
                tooltip: {
                    enabled: true,
                    backgroundColor: 'rgba(28, 28, 30, 0.95)',
                    titleColor: '#f5f5f7',
                    bodyColor: '#f5f5f7',
                    borderColor: 'rgba(255,255,255,0.1)',
                    borderWidth: 1,
                    padding: 12,
                    cornerRadius: 8,
                    titleFont: { family: 'Inter', size: 13, weight: '600' },
                    bodyFont: { family: 'Inter', size: 12 },
                    displayColors: true,
                    callbacks: {
                        title: (items) => `Strike: ${items[0].label}`,
                        label: (item) => `${item.dataset.label}: ${parseInt(item.raw).toLocaleString('en-IN')}`,
                    }
                }
            },
            scales: {
                x: { ticks: { color: 'rgba(245,245,247,0.35)', font: { size: 10 } }, grid: { color: 'rgba(255,255,255,0.04)' } },
                y: { ticks: { color: 'rgba(245,245,247,0.35)', font: { size: 10 } }, grid: { color: 'rgba(255,255,255,0.04)' } },
            }
        }
    });
}

// ═══════════════════════════════════════════════════════════
//  SIGNALS
// ═══════════════════════════════════════════════════════════

async function loadSignals() {
    const data = await api('signals');
    if (!data || !data.signals) return;

    const tbody = document.getElementById('signals-body');
    if (data.signals.length === 0) {
        tbody.innerHTML = '<tr><td colspan="8" class="empty-state">No signals yet</td></tr>';
        return;
    }

    tbody.innerHTML = data.signals.map(s => `
        <tr>
            <td>${new Date(s.timestamp).toLocaleTimeString()}</td>
            <td>${s.symbol}</td>
            <td><span class="badge ${badgeClass(s.direction)}">${s.direction}</span></td>
            <td>${formatNum(s.score, 4)}</td>
            <td>${formatNum(s.confidence)}%</td>
            <td><span class="badge ${badgeClass(s.regime)}">${s.regime}</span></td>
            <td>${s.suggested_strategy || '--'}</td>
            <td>${s.is_actionable ? '<span class="badge bullish">YES</span>' : '<span class="badge neutral">NO</span>'}</td>
        </tr>
    `).join('');
}

// ═══════════════════════════════════════════════════════════
//  GREEKS
// ═══════════════════════════════════════════════════════════

async function loadGreeks() {
    const data = await api('greeks');
    if (!data) return;

    const g = data.portfolio_greeks || {};
    document.getElementById('g-delta').textContent = formatNum(g.delta, 4);
    document.getElementById('g-gamma').textContent = formatNum(g.gamma, 6);
    document.getElementById('g-theta').textContent = formatNum(g.theta);
    document.getElementById('g-vega').textContent = formatNum(g.vega);

    // Radar chart
    const ctx = document.getElementById('greeks-chart');
    if (charts.greeks) charts.greeks.destroy();

    charts.greeks = new Chart(ctx, {
        type: 'radar',
        data: {
            labels: ['Delta', 'Gamma', 'Theta', 'Vega'],
            datasets: [{
                label: 'Portfolio Greeks',
                data: [
                    Math.abs(g.delta || 0) * 100,
                    Math.abs(g.gamma || 0) * 10000,
                    Math.abs(g.theta || 0),
                    Math.abs(g.vega || 0),
                ],
                backgroundColor: 'rgba(10, 132, 255, 0.15)',
                borderColor: 'rgba(10, 132, 255, 0.8)',
                borderWidth: 2,
                pointBackgroundColor: 'rgba(10, 132, 255, 1)',
            }]
        },
        options: {
            responsive: true, maintainAspectRatio: false,
            interaction: { mode: 'nearest', intersect: true },
            scales: {
                r: {
                    grid: { color: 'rgba(255,255,255,0.06)' },
                    angleLines: { color: 'rgba(255,255,255,0.06)' },
                    pointLabels: { color: 'rgba(245,245,247,0.6)', font: { family: 'Inter', size: 12 } },
                    ticks: { display: false },
                }
            },
            plugins: {
                legend: { display: false },
                tooltip: {
                    enabled: true,
                    backgroundColor: 'rgba(28, 28, 30, 0.95)',
                    titleColor: '#f5f5f7',
                    bodyColor: '#f5f5f7',
                    borderColor: 'rgba(10, 132, 255, 0.3)',
                    borderWidth: 1,
                    padding: 12,
                    cornerRadius: 8,
                    titleFont: { family: 'Inter', size: 13, weight: '600' },
                    bodyFont: { family: 'Inter', size: 12 },
                    callbacks: {
                        title: (items) => items[0].label,
                        label: (item) => `Value: ${item.raw.toFixed(4)}`,
                    }
                }
            }
        }
    });
}

// ═══════════════════════════════════════════════════════════
//  SENTIMENT
// ═══════════════════════════════════════════════════════════

async function loadSentiment() {
    const data = await api('sentiment');
    if (!data) return;

    const score = data.aggregate_score || 0;
    document.getElementById('sent-score').textContent = formatNum(score, 4);
    document.getElementById('sent-score').className = `stat-value ${score > 0.1 ? 'positive' : score < -0.1 ? 'negative' : 'neutral'}`;
    document.getElementById('sent-label').textContent = data.label || 'NEUTRAL';
    document.getElementById('sent-confidence').textContent = `${data.confidence || 0}%`;
    document.getElementById('sent-sources').textContent = data.source_count || 0;

    const mom = data.momentum || {};
    document.getElementById('sent-momentum').textContent = mom.momentum || 'STABLE';

    // Gauge (0-100, center=50)
    const gaugeVal = Math.round((score + 1) / 2 * 100);
    const gauge = document.getElementById('sent-gauge');
    if (gauge) {
        gauge.style.width = `${gaugeVal}%`;
        gauge.className = `gauge-fill ${gaugeVal > 55 ? 'green' : gaugeVal < 45 ? 'red' : 'blue'}`;
    }

    // Feed
    const feed = document.getElementById('sent-feed');
    const items = [...(data.rss || []), ...(data.reddit || [])].slice(0, 20);
    if (items.length > 0) {
        feed.innerHTML = items.map(item => {
            const c = item.compound || 0;
            const color = c > 0.1 ? 'var(--green)' : c < -0.1 ? 'var(--red)' : 'var(--text-secondary)';
            return `<div style="padding:12px 0;border-bottom:1px solid var(--border-subtle);display:flex;justify-content:space-between;align-items:center;">
                <div style="flex:1;margin-right:16px;">
                    <div style="font-size:13px;color:var(--text-primary);margin-bottom:4px;">${item.title || ''}</div>
                    <div style="font-size:11px;color:var(--text-tertiary);">${item.source} ${item.subreddit ? '/ r/' + item.subreddit : ''}</div>
                </div>
                <span style="font-size:13px;font-weight:600;color:${color};font-variant-numeric:tabular-nums;white-space:nowrap;">${formatNum(c, 3)}</span>
            </div>`;
        }).join('');
    } else {
        feed.innerHTML = '<div class="empty-state">No sentiment data available</div>';
    }
}

// ═══════════════════════════════════════════════════════════
//  TRADES
// ═══════════════════════════════════════════════════════════

async function loadTrades() {
    const data = await api('trades');
    if (!data || !data.trades) return;

    const tbody = document.getElementById('trades-body');
    if (data.trades.length === 0) {
        tbody.innerHTML = '<tr><td colspan="10" class="empty-state">No trades yet</td></tr>';
        return;
    }

    tbody.innerHTML = data.trades.reverse().map(t => `
        <tr>
            <td>${t.trade_id}</td>
            <td>${t.symbol}</td>
            <td>${t.option_type}</td>
            <td>${t.strike}</td>
            <td><span class="badge ${t.side === 'BUY' ? 'bullish' : 'bearish'}">${t.side}</span></td>
            <td>${formatCurrency(t.entry_price)}</td>
            <td>${formatCurrency(t.exit_price)}</td>
            <td style="color:var(--${t.net_pnl >= 0 ? 'green' : 'red'})">${formatCurrency(t.net_pnl)}</td>
            <td>${t.strategy}</td>
            <td>${t.exit_reason}</td>
        </tr>
    `).join('');
}

// ═══════════════════════════════════════════════════════════
//  BACKTEST
// ═══════════════════════════════════════════════════════════

async function runBacktest() {
    const btn = document.getElementById('run-backtest-btn');
    btn.disabled = true;
    btn.innerHTML = '<div class="spinner"></div> Running...';

    const data = await fetch('/api/backtest', { method: 'POST' }).then(r => r.json()).catch(() => null);

    btn.disabled = false;
    btn.innerHTML = '<svg xmlns="http://www.w3.org/2000/svg" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polygon points="5 3 19 12 5 21 5 3"/></svg> Run Backtest';

    if (!data || data.error) {
        alert(data?.error || 'Backtest failed');
        return;
    }

    document.getElementById('backtest-results').style.display = 'block';

    // Stats
    document.getElementById('bt-stats').innerHTML = [
        { label: 'Total P&L', value: formatCurrency(data.total_pnl), cls: pnlClass(data.total_pnl) },
        { label: 'Win Rate', value: `${data.win_rate}%`, cls: 'neutral' },
        { label: 'Total Trades', value: data.total_trades, cls: 'neutral' },
        { label: 'Profit Factor', value: formatNum(data.profit_factor), cls: 'neutral' },
        { label: 'Max Drawdown', value: `${formatNum(data.max_drawdown_pct)}%`, cls: 'negative' },
        { label: 'Sharpe Ratio', value: formatNum(data.sharpe_ratio), cls: 'neutral' },
    ].map(s => `<div class="stat-card"><div class="stat-label">${s.label}</div><div class="stat-value ${s.cls}">${s.value}</div></div>`).join('');

    // Equity curve chart
    if (data.equity_curve && data.equity_curve.length > 0) {
        const ctx = document.getElementById('bt-equity-chart');
        if (charts.btEquity) charts.btEquity.destroy();

        charts.btEquity = new Chart(ctx, {
            type: 'line',
            data: {
                labels: data.equity_curve.map((_, i) => i),
                datasets: [{
                    label: 'Equity',
                    data: data.equity_curve.map(e => e.equity),
                    borderColor: 'rgba(10, 132, 255, 0.8)',
                    backgroundColor: 'rgba(10, 132, 255, 0.1)',
                    fill: true, borderWidth: 2, pointRadius: 0,
                    pointHoverRadius: 6, pointHoverBackgroundColor: '#0a84ff',
                    pointHoverBorderColor: '#fff', pointHoverBorderWidth: 2,
                    tension: 0.3,
                }]
            },
            options: {
                responsive: true, maintainAspectRatio: false,
                interaction: { mode: 'index', intersect: false },
                plugins: {
                    legend: { display: false },
                    tooltip: {
                        enabled: true,
                        backgroundColor: 'rgba(28, 28, 30, 0.95)',
                        titleColor: '#f5f5f7',
                        bodyColor: '#f5f5f7',
                        borderColor: 'rgba(10, 132, 255, 0.3)',
                        borderWidth: 1,
                        padding: 12,
                        cornerRadius: 8,
                        titleFont: { family: 'Inter', size: 13, weight: '600' },
                        bodyFont: { family: 'Inter', size: 12 },
                        displayColors: false,
                        callbacks: {
                            title: () => 'Portfolio Value',
                            label: (item) => `₹${parseFloat(item.raw).toLocaleString('en-IN', {minimumFractionDigits: 2})}`,
                        }
                    }
                },
                scales: {
                    x: { display: false },
                    y: {
                        ticks: {
                            color: 'rgba(245,245,247,0.35)', font: { size: 10 },
                            callback: (v) => '₹' + (v/1000).toFixed(0) + 'K',
                        },
                        grid: { color: 'rgba(255,255,255,0.04)' },
                    },
                }
            }
        });
    }

    // Trades table
    const tbody = document.getElementById('bt-trades-body');
    tbody.innerHTML = (data.trades || []).map(t => `
        <tr>
            <td>${t.id}</td>
            <td><span class="badge neutral">${t.strategy}</span></td>
            <td>${formatNum(t.entry_underlying)}</td>
            <td>${formatNum(t.exit_underlying)}</td>
            <td style="color:var(--${t.net_pnl >= 0 ? 'green' : 'red'})">${formatCurrency(t.net_pnl)}</td>
            <td>${t.exit_reason}</td>
        </tr>
    `).join('');
}

// ═══════════════════════════════════════════════════════════
//  AUTO REFRESH
// ═══════════════════════════════════════════════════════════

function startAutoRefresh() {
    setInterval(updateClock, 1000);

    // Refresh current section every 5 seconds
    setInterval(() => {
        loadSectionData(currentSection);
    }, 5000);

    // Market status every 30 seconds
    setInterval(async () => {
        const status = await api('market-status');
        if (status) {
            const badge = document.getElementById('market-badge');
            const text = document.getElementById('market-status-text');
            if (status.is_open) {
                badge.className = 'market-badge open';
                text.textContent = 'LIVE';
            } else {
                badge.className = 'market-badge closed';
                text.textContent = 'CLOSED';
            }
        }
    }, 30000);
}

// ─── Init ───────────────────────────────────────────────
document.addEventListener('DOMContentLoaded', () => {
    updateClock();
    loadDashboard();
    startAutoRefresh();
});

/* ═══════════════════════════════════════════════════════════
   NIFTY OI Tracker — Interactive Dashboard JS
   ═══════════════════════════════════════════════════════════ */

// ─── Chart Defaults ─────────────────────────────────────
Chart.defaults.color = 'rgba(232,234,237,0.5)';
Chart.defaults.borderColor = 'rgba(255,255,255,0.06)';
Chart.defaults.font.family = "'Inter', sans-serif";
Chart.defaults.font.size = 11;
Chart.defaults.plugins.legend.labels.usePointStyle = true;
Chart.defaults.plugins.legend.labels.pointStyleWidth = 8;
Chart.defaults.animation = { duration: 400 };

const COLORS = {
    green: '#26a69a', red: '#ef5350', blue: '#42a5f5',
    purple: '#ab47bc', cyan: '#26c6da', pink: '#ec407a',
    orange: '#ffa726', greenBright: '#00e676',
};

// ─── State ──────────────────────────────────────────────
let currentTab = 'oi-table';
let selectedStrike = 0;
let charts = {};
let refreshInterval = null;

// ─── Init ───────────────────────────────────────────────
document.addEventListener('DOMContentLoaded', () => {
    initTabs();
    initTimeframeButtons();
    initModeButtons();
    startClock();
    checkMarketStatus();
    loadAllData();
    refreshInterval = setInterval(loadAllData, 60000);
    setInterval(checkMarketStatus, 10000);
});

// ═══ TABS ═══════════════════════════════════════════════
function initTabs() {
    document.querySelectorAll('.tab-btn').forEach(btn => {
        btn.addEventListener('click', () => {
            document.querySelectorAll('.tab-btn').forEach(b => b.classList.remove('active'));
            document.querySelectorAll('.tab-panel').forEach(p => p.classList.remove('active'));
            btn.classList.add('active');
            currentTab = btn.dataset.tab;
            document.getElementById(`panel-${currentTab}`).classList.add('active');
            loadAllData();
        });
    });
}

function initTimeframeButtons() {
    document.querySelectorAll('.tf-btn').forEach(btn => {
        btn.addEventListener('click', () => {
            document.querySelectorAll('.tf-btn').forEach(b => b.classList.remove('active'));
            btn.classList.add('active');
            loadAllData();
        });
    });
}

function initModeButtons() {
    document.querySelectorAll('.mode-btn').forEach(btn => {
        btn.addEventListener('click', () => {
            document.querySelectorAll('.mode-btn').forEach(b => b.classList.remove('active'));
            btn.classList.add('active');
            loadAllData();
        });
    });
}

// ═══ CLOCK & STATUS ═════════════════════════════════════
function startClock() {
    const update = () => {
        const now = new Date();
        const istTime = now.toLocaleTimeString('en-IN', {
            hour12: false,
            timeZone: 'Asia/Kolkata'
        });
        document.getElementById('live-time').textContent = istTime;
    };
    update();
    setInterval(update, 1000);
}

async function checkMarketStatus() {
    try {
        const res = await fetch('/api/market-status');
        const data = await res.json();
        const badge = document.getElementById('market-badge');
        if (data.is_open) {
            badge.textContent = 'LIVE';
            badge.className = 'market-badge open';
        } else {
            badge.textContent = 'CLOSED';
            badge.className = 'market-badge closed';
        }
        if (data.date) {
            const src = data.data_source || 'UNKNOWN';
            const db = data.db_connected ? ' | DB:ON' : ' | DB:OFF';
            document.getElementById('expiry-badge').textContent =
                data.date + ' | ' + src + db;
        }
    } catch (e) { console.error(e); }
}

// ═══ DATA LOADING ═══════════════════════════════════════
async function loadAllData() {
    if (currentTab === 'oi-table') await loadOITable();
    if (currentTab === 'smart-oi') await loadSmartOI();
    if (currentTab === 'price-oi') await loadPriceVsOI();
}

// ─── OI TABLE ───────────────────────────────────────────
async function loadOITable() {
    try {
        const res = await fetch('/api/oi-table');
        const data = await res.json();
        if (!data.rows || !data.rows.length) return;

        // Update underlying price
        const raw = data.rows[0]._raw;
        if (raw) {
            document.getElementById('underlying-price').textContent = raw.underlying?.toFixed(2) || '--';
        }

        const tbody = document.getElementById('oi-table-body');
        tbody.innerHTML = data.rows.map(r => {
            const raw = r._raw || {};
            const peDiff = raw.pe_ce_diff || 0;
            const signal = raw.pcr > 1.2 ? 'up' : raw.pcr < 0.8 ? 'down' : 'side';
            const signalText = raw.pcr > 1.2 ? '↑ LU' : raw.pcr < 0.8 ? '↓ SC' : '→ S';

            return `<tr>
                <td class="col-time">${r.time}</td>
                <td class="${valClass(raw.total_pe_oi)}">${r.pe_oi_total}</td>
                <td class="${valClass(raw.pe_oi_change_day)}">${r.pe_oi_change_day}</td>
                <td class="${valClass(raw.pe_oi_change)}">${r.pe_oi_change}</td>
                <td class="${valClass(raw.total_ce_oi)}">${r.ce_oi_total}</td>
                <td class="${valClass(raw.ce_oi_change_day)}">${r.ce_oi_change_day}</td>
                <td class="${valClass(raw.ce_oi_change)}">${r.ce_oi_change}</td>
                <td class="${peDiff > 0 ? 'val-pos' : 'val-neg'}">${r.pe_ce_total}</td>
                <td class="${raw.pe_ce_diff_change > 0 ? 'val-pos' : 'val-neg'}">${r.pe_ce_change}</td>
                <td>${r.pcr?.toFixed(2)}</td>
                <td class="val-neutral">${r.future_ltp}</td>
                <td class="val-neutral">${r.straddle}</td>
                <td class="val-neutral">${r.atm_strike}</td>
                <td><span class="signal-badge signal-${signal}">${signalText}</span></td>
            </tr>`;
        }).join('');

    } catch (e) { console.error('OI Table error:', e); }
}

function valClass(v) {
    if (!v || v === 0) return 'val-neutral';
    return v > 0 ? 'val-pos' : 'val-neg';
}

// ─── SMART OI CHARTS ────────────────────────────────────
async function loadSmartOI() {
    try {
        const [oiRes, candleRes] = await Promise.all([
            fetch('/api/oi-chart'),
            fetch('/api/candles'),
        ]);
        const oiData = await oiRes.json();
        const candleData = await candleRes.json();

        renderCandleChart(candleData.candles || []);
        renderOILinesChart(oiData);
        renderPCRChart(oiData);

    } catch (e) { console.error('Smart OI error:', e); }
}

function renderCandleChart(candles) {
    const ctx = document.getElementById('chart-candle');
    if (charts.candle) charts.candle.destroy();

    if (!candles.length) {
        charts.candle = new Chart(ctx, {
            type: 'line',
            data: { labels: ['No data'], datasets: [{ data: [0] }] },
        });
        return;
    }

    const labels = candles.map(c => c.timestamp?.split('T')[1]?.slice(0,5) || '');
    const closes = candles.map(c => c.close);
    const opens = candles.map(c => c.open);
    const colors = candles.map(c => c.close >= c.open ? COLORS.green : COLORS.red);

    charts.candle = new Chart(ctx, {
        type: 'bar',
        data: {
            labels,
            datasets: [
                {
                    label: 'NIFTY',
                    data: candles.map(c => ({ o: c.open, h: c.high, l: c.low, c: c.close })),
                    type: 'line',
                    borderColor: COLORS.blue,
                    backgroundColor: 'rgba(66,165,245,0.1)',
                    data: closes,
                    pointRadius: 0,
                    borderWidth: 2,
                    fill: true,
                    tension: 0.3,
                },
            ],
        },
        options: {
            responsive: true,
            maintainAspectRatio: false,
            interaction: { mode: 'index', intersect: false },
            plugins: {
                legend: { display: false },
                tooltip: {
                    backgroundColor: 'rgba(20,22,32,0.95)',
                    borderColor: 'rgba(255,255,255,0.1)',
                    borderWidth: 1,
                    titleFont: { weight: '600' },
                    callbacks: {
                        label: ctx => `NIFTY: ${ctx.raw?.toFixed(2)}`,
                    },
                },
            },
            scales: {
                x: { grid: { display: false } },
                y: {
                    position: 'right',
                    grid: { color: 'rgba(255,255,255,0.03)' },
                    ticks: { callback: v => v.toFixed(0) },
                },
            },
        },
    });
}

function renderOILinesChart(data) {
    const ctx = document.getElementById('chart-oi-lines');
    if (charts.oiLines) charts.oiLines.destroy();

    charts.oiLines = new Chart(ctx, {
        type: 'line',
        data: {
            labels: data.timestamps || [],
            datasets: [
                {
                    label: 'Put OI',
                    data: data.put_oi || [],
                    borderColor: COLORS.pink,
                    borderWidth: 2,
                    pointRadius: 0,
                    tension: 0.3,
                },
                {
                    label: 'Call OI',
                    data: data.call_oi || [],
                    borderColor: COLORS.green,
                    borderWidth: 2,
                    pointRadius: 0,
                    tension: 0.3,
                },
                {
                    label: 'PE-CE',
                    data: data.pe_ce || [],
                    borderColor: COLORS.purple,
                    borderWidth: 2,
                    pointRadius: 0,
                    borderDash: [5, 3],
                    tension: 0.3,
                    yAxisID: 'y1',
                },
            ],
        },
        options: {
            responsive: true,
            maintainAspectRatio: false,
            interaction: { mode: 'index', intersect: false },
            plugins: {
                tooltip: {
                    backgroundColor: 'rgba(20,22,32,0.95)',
                    borderColor: 'rgba(255,255,255,0.1)',
                    borderWidth: 1,
                    callbacks: {
                        label: ctx => `${ctx.dataset.label}: ${fmtLakh(ctx.raw)}`,
                    },
                },
            },
            scales: {
                x: { grid: { display: false }, ticks: { maxTicksLimit: 10 } },
                y: {
                    position: 'left',
                    grid: { color: 'rgba(255,255,255,0.03)' },
                    ticks: { callback: v => fmtLakh(v) },
                },
                y1: {
                    position: 'right',
                    grid: { display: false },
                    ticks: { callback: v => fmtLakh(v) },
                },
            },
        },
    });
}

function renderPCRChart(data) {
    const ctx = document.getElementById('chart-pcr');
    if (charts.pcr) charts.pcr.destroy();

    charts.pcr = new Chart(ctx, {
        type: 'line',
        data: {
            labels: data.timestamps || [],
            datasets: [{
                label: 'PCR',
                data: data.pcr || [],
                borderColor: COLORS.cyan,
                borderWidth: 2,
                pointRadius: 0,
                fill: true,
                backgroundColor: 'rgba(38,198,218,0.08)',
                tension: 0.4,
            }],
        },
        options: {
            responsive: true,
            maintainAspectRatio: false,
            interaction: { mode: 'index', intersect: false },
            plugins: {
                tooltip: {
                    backgroundColor: 'rgba(20,22,32,0.95)',
                    borderColor: 'rgba(255,255,255,0.1)',
                    borderWidth: 1,
                },
            },
            scales: {
                x: { grid: { display: false }, ticks: { maxTicksLimit: 10 } },
                y: {
                    position: 'right',
                    grid: { color: 'rgba(255,255,255,0.03)' },
                },
            },
        },
    });
}

// ─── PRICE vs OI ────────────────────────────────────────
async function loadPriceVsOI() {
    // Load strikes
    try {
        const sRes = await fetch('/api/strikes');
        const sData = await sRes.json();
        if (sData.strikes?.length) {
            renderStrikeList(sData.strikes, sData.atm);
            if (!selectedStrike) selectedStrike = sData.atm;
        }
    } catch (e) { console.error(e); }

    // Load price vs OI
    if (selectedStrike) {
        try {
            const res = await fetch(`/api/price-vs-oi?strike=${selectedStrike}`);
            const data = await res.json();
            renderCallPriceOI(data);
            renderPutPriceOI(data);
            renderStraddleChart(data);
        } catch (e) { console.error(e); }
    }
}

function renderStrikeList(strikes, atm) {
    const container = document.getElementById('strike-list');
    // Show only strikes near ATM
    const nearStrikes = strikes.filter(s => Math.abs(s - atm) <= 500);

    container.innerHTML = nearStrikes.map(s => {
        const isActive = s === selectedStrike || (selectedStrike === 0 && s === atm);
        return `<div class="strike-item ${isActive ? 'active' : ''}"
                     onclick="selectStrike(${s})">${s}${s === atm ? ' ✓' : ''}</div>`;
    }).join('');
}

window.selectStrike = function(strike) {
    selectedStrike = strike;
    document.querySelectorAll('.strike-item').forEach(el => {
        el.classList.toggle('active', parseFloat(el.textContent) === strike);
    });
    loadPriceVsOI();
};

function renderCallPriceOI(data) {
    const ctx = document.getElementById('chart-call-price-oi');
    if (charts.callPriceOI) charts.callPriceOI.destroy();

    const labels = (data.call || []).map(d => d.timestamp?.split('T')[1]?.slice(0,5) || '');

    charts.callPriceOI = new Chart(ctx, {
        type: 'line',
        data: {
            labels,
            datasets: [
                {
                    label: `${data.strike} CE OI`,
                    data: (data.call || []).map(d => d.oi),
                    borderColor: COLORS.green,
                    borderWidth: 2,
                    pointRadius: 0,
                    tension: 0.3,
                    yAxisID: 'y',
                    fill: true,
                    backgroundColor: 'rgba(38,166,154,0.08)',
                },
                {
                    label: `${data.strike} CE Price`,
                    data: (data.call || []).map(d => d.price),
                    borderColor: COLORS.orange,
                    borderWidth: 2,
                    pointRadius: 0,
                    tension: 0.3,
                    yAxisID: 'y1',
                },
            ],
        },
        options: dualAxisOptions('OI', 'Price'),
    });
}

function renderPutPriceOI(data) {
    const ctx = document.getElementById('chart-put-price-oi');
    if (charts.putPriceOI) charts.putPriceOI.destroy();

    const labels = (data.put || []).map(d => d.timestamp?.split('T')[1]?.slice(0,5) || '');

    charts.putPriceOI = new Chart(ctx, {
        type: 'line',
        data: {
            labels,
            datasets: [
                {
                    label: `${data.strike} PE OI`,
                    data: (data.put || []).map(d => d.oi),
                    borderColor: COLORS.red,
                    borderWidth: 2,
                    pointRadius: 0,
                    tension: 0.3,
                    yAxisID: 'y',
                    fill: true,
                    backgroundColor: 'rgba(239,83,80,0.08)',
                },
                {
                    label: `${data.strike} PE Price`,
                    data: (data.put || []).map(d => d.price),
                    borderColor: COLORS.orange,
                    borderWidth: 2,
                    pointRadius: 0,
                    tension: 0.3,
                    yAxisID: 'y1',
                },
            ],
        },
        options: dualAxisOptions('OI', 'Price'),
    });
}

function renderStraddleChart(data) {
    const ctx = document.getElementById('chart-straddle');
    if (charts.straddle) charts.straddle.destroy();

    const labels = (data.straddle || []).map(d => d.timestamp?.split('T')[1]?.slice(0,5) || '');

    charts.straddle = new Chart(ctx, {
        type: 'line',
        data: {
            labels,
            datasets: [{
                label: 'Straddle Price',
                data: (data.straddle || []).map(d => d.price),
                borderColor: COLORS.blue,
                borderWidth: 2,
                pointRadius: 0,
                tension: 0.3,
                fill: true,
                backgroundColor: 'rgba(66,165,245,0.08)',
            }],
        },
        options: {
            responsive: true,
            maintainAspectRatio: false,
            interaction: { mode: 'index', intersect: false },
            plugins: {
                tooltip: {
                    backgroundColor: 'rgba(20,22,32,0.95)',
                    borderColor: 'rgba(255,255,255,0.1)',
                    borderWidth: 1,
                },
            },
            scales: {
                x: { grid: { display: false }, ticks: { maxTicksLimit: 10 } },
                y: { position: 'left', grid: { color: 'rgba(255,255,255,0.03)' } },
            },
        },
    });
}

function dualAxisOptions(leftLabel, rightLabel) {
    return {
        responsive: true,
        maintainAspectRatio: false,
        interaction: { mode: 'index', intersect: false },
        plugins: {
            tooltip: {
                backgroundColor: 'rgba(20,22,32,0.95)',
                borderColor: 'rgba(255,255,255,0.1)',
                borderWidth: 1,
                callbacks: {
                    label: ctx => {
                        const val = ctx.raw;
                        if (ctx.datasetIndex === 0) return `OI: ${fmtLakh(val)}`;
                        return `Price: ₹${val?.toFixed(2)}`;
                    },
                },
            },
        },
        scales: {
            x: { grid: { display: false }, ticks: { maxTicksLimit: 10 } },
            y: {
                position: 'left',
                grid: { color: 'rgba(255,255,255,0.03)' },
                ticks: { callback: v => fmtLakh(v) },
                title: { display: true, text: leftLabel, color: 'rgba(255,255,255,0.3)' },
            },
            y1: {
                position: 'right',
                grid: { display: false },
                title: { display: true, text: rightLabel, color: 'rgba(255,255,255,0.3)' },
            },
        },
    };
}

// ─── UTILS ──────────────────────────────────────────────
function fmtLakh(n) {
    if (n === null || n === undefined) return '--';
    const abs = Math.abs(n);
    if (abs >= 10000000) return (n / 10000000).toFixed(1) + ' Cr';
    if (abs >= 100000) return (n / 100000).toFixed(1) + ' L';
    if (abs >= 1000) return (n / 1000).toFixed(1) + ' K';
    return n.toString();
}

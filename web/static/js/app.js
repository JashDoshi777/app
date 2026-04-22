/* ═══════════════════════════════════════════════════════════
   NIFTY OI Tracker — Interactive Dashboard JS v4
   - TradingView Lightweight Charts for candlestick
   - Chart.js for OI/PCR/Price charts
   - All filters working, all tooltips interactive
   ═══════════════════════════════════════════════════════════ */

// ─── Chart.js Defaults ──────────────────────────────────
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
let currentTf = 1;
let currentMode = 'live';
let selectedStrike = 0;
let charts = {};
let tvChart = null;       // TradingView Lightweight Chart instance
let tvSeries = null;      // Candlestick series
let refreshInterval = null;

// ─── Init ───────────────────────────────────────────────
document.addEventListener('DOMContentLoaded', () => {
    initTabs();
    initTimeframeButtons();
    initModeButtons();
    initFilters();
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
            currentTf = parseInt(btn.dataset.tf) || 1;
            loadAllData();
        });
    });
}

function initModeButtons() {
    document.querySelectorAll('.mode-btn').forEach(btn => {
        btn.addEventListener('click', () => {
            document.querySelectorAll('.mode-btn').forEach(b => b.classList.remove('active'));
            btn.classList.add('active');
            currentMode = btn.dataset.mode;
            loadAllData();
        });
    });
}

function initFilters() {
    const oiMode = document.getElementById('oi-display-mode');
    const rangeSelect = document.getElementById('strike-range');
    if (oiMode) oiMode.addEventListener('change', loadAllData);
    if (rangeSelect) rangeSelect.addEventListener('change', loadAllData);
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
    try {
        if (currentTab === 'oi-table') await loadOITable();
        if (currentTab === 'smart-oi') await loadSmartOI();
        if (currentTab === 'price-oi') await loadPriceVsOI();
    } catch (e) { console.error('loadAllData error:', e); }
}

// ─── OI TABLE ───────────────────────────────────────────
async function loadOITable() {
    try {
        const rangeEl = document.getElementById('strike-range');
        const range = rangeEl ? rangeEl.value : 10;
        const res = await fetch(`/api/oi-table?tf=${currentTf}&range_strikes=${range}`);
        const data = await res.json();
        if (!data.rows || !data.rows.length) return;

        const raw = data.rows[0]._raw;
        if (raw) {
            document.getElementById('underlying-price').textContent = raw.underlying?.toFixed(2) || '--';
        }

        const tbody = document.getElementById('oi-table-body');
        tbody.innerHTML = data.rows.map(r => {
            const raw = r._raw || {};
            const peDiff = raw.pe_ce_diff || 0;
            const signal = raw.pcr > 1.2 ? 'up' : raw.pcr < 0.8 ? 'down' : 'side';
            const signalText = raw.pcr > 1.2 ? 'LU' : raw.pcr < 0.8 ? 'SC' : 'S';

            return `<tr>
                <td class="col-time">${r.time}</td>
                <td class="${valClass(raw.total_pe_oi)}">${r.pe_oi_total}</td>
                <td class="${valClass(raw.pe_oi_change_day)}">${r.pe_oi_change_day}</td>
                <td class="${valClass(raw.pe_oi_change)}">${r.pe_oi_change}</td>
                <td class="${valClass(raw.total_ce_oi)}">${r.ce_oi_total}</td>
                <td class="${valClass(raw.ce_oi_change_day)}">${r.ce_oi_change_day}</td>
                <td class="${valClass(raw.ce_oi_change)}">${r.ce_oi_change}</td>
                <td class="${peDiff > 0 ? 'val-pos' : 'val-neg'}">${r.pe_ce_total}</td>
                <td class="${valClass(r.pe_ce_change_day)}">${r.pe_ce_change_day || '0'}</td>
                <td class="${raw.pe_ce_diff_change > 0 ? 'val-pos' : 'val-neg'}">${r.pe_ce_change}</td>
                <td>${r.pcr?.toFixed(2)}</td>
                <td class="val-neutral">${r.future_ltp}</td>
                <td class="val-neutral">${r.straddle}</td>
                <td class="val-neutral">${r.atm_strike}</td>
                <td class="${valClass(r.ce_delta_chg)}">${r.ce_delta_chg > 0 ? '+' : ''}${r.ce_delta_chg}</td>
                <td class="${valClass(r.pe_delta_chg)}">${r.pe_delta_chg > 0 ? '+' : ''}${r.pe_delta_chg}</td>
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
            fetch(`/api/oi-chart?tf=${currentTf}`),
            fetch(`/api/candles?tf=${currentTf}`),
        ]);
        const oiData = await oiRes.json();
        const candleData = await candleRes.json();

        renderTVCandleChart(candleData.candles || []);
        renderOILinesChart(oiData);
        renderPCRChart(oiData);

    } catch (e) { console.error('Smart OI error:', e); }
}

// ─── TRADINGVIEW CANDLESTICK CHART ──────────────────────
function renderTVCandleChart(candles) {
    const container = document.getElementById('tv-candle-container');
    if (!container) return;

    // Create chart only once, then update data
    if (!tvChart) {
        container.innerHTML = '';
        tvChart = LightweightCharts.createChart(container, {
            width: container.clientWidth,
            height: 320,
            layout: {
                background: { type: 'solid', color: '#0c0e14' },
                textColor: 'rgba(232,234,237,0.5)',
                fontFamily: "'Inter', sans-serif",
                fontSize: 11,
            },
            grid: {
                vertLines: { color: 'rgba(255,255,255,0.03)' },
                horzLines: { color: 'rgba(255,255,255,0.03)' },
            },
            crosshair: {
                mode: LightweightCharts.CrosshairMode.Normal,
                vertLine: { color: 'rgba(255,255,255,0.15)', width: 1, style: 2 },
                horzLine: { color: 'rgba(255,255,255,0.15)', width: 1, style: 2 },
            },
            rightPriceScale: {
                borderColor: 'rgba(255,255,255,0.06)',
                scaleMargins: { top: 0.1, bottom: 0.1 },
            },
            timeScale: {
                borderColor: 'rgba(255,255,255,0.06)',
                timeVisible: true,
                secondsVisible: false,
            },
            handleScroll: true,
            handleScale: true,
        });

        tvSeries = tvChart.addCandlestickSeries({
            upColor: COLORS.green,
            downColor: COLORS.red,
            borderUpColor: COLORS.green,
            borderDownColor: COLORS.red,
            wickUpColor: COLORS.green,
            wickDownColor: COLORS.red,
        });

        // Resize observer
        const ro = new ResizeObserver(() => {
            tvChart.applyOptions({ width: container.clientWidth });
        });
        ro.observe(container);
    }

    if (!candles.length) return;

    // Convert to Lightweight Charts format: { time: unix_timestamp, open, high, low, close }
    const tvData = candles.map(c => {
        let time;
        if (c.timestamp) {
            // ISO string to unix timestamp
            time = Math.floor(new Date(c.timestamp).getTime() / 1000);
        } else {
            time = Math.floor(Date.now() / 1000);
        }
        return {
            time,
            open: c.open,
            high: c.high,
            low: c.low,
            close: c.close,
        };
    }).filter(d => d.time > 0 && !isNaN(d.open));

    // Sort by time (required by Lightweight Charts)
    tvData.sort((a, b) => a.time - b.time);

    if (tvData.length > 0) {
        tvSeries.setData(tvData);
        tvChart.timeScale().fitContent();
    }
}

// ─── OI LINES CHART ─────────────────────────────────────
function renderOILinesChart(data) {
    const ctx = document.getElementById('chart-oi-lines');
    if (!ctx) return;
    if (charts.oiLines) { charts.oiLines.destroy(); charts.oiLines = null; }

    if (!data.timestamps || !data.timestamps.length) {
        charts.oiLines = new Chart(ctx, {
            type: 'line',
            data: { labels: ['Waiting...'], datasets: [{ data: [0], borderColor: COLORS.blue }] },
            options: { responsive: true, maintainAspectRatio: false, plugins: { legend: { display: false } } },
        });
        return;
    }

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
                    label: 'PE-CE Diff',
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
                    titleFont: { weight: '600' },
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
                    title: { display: true, text: 'OI', color: 'rgba(255,255,255,0.3)' },
                },
                y1: {
                    position: 'right',
                    grid: { display: false },
                    ticks: { callback: v => fmtLakh(v) },
                    title: { display: true, text: 'PE-CE', color: 'rgba(255,255,255,0.3)' },
                },
            },
        },
    });
}

// ─── PCR CHART ──────────────────────────────────────────
function renderPCRChart(data) {
    const ctx = document.getElementById('chart-pcr');
    if (!ctx) return;
    if (charts.pcr) { charts.pcr.destroy(); charts.pcr = null; }

    if (!data.timestamps || !data.timestamps.length) {
        charts.pcr = new Chart(ctx, {
            type: 'line',
            data: { labels: ['Waiting...'], datasets: [{ data: [0], borderColor: COLORS.cyan }] },
            options: { responsive: true, maintainAspectRatio: false, plugins: { legend: { display: false } } },
        });
        return;
    }

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
                    titleFont: { weight: '600' },
                    callbacks: {
                        label: ctx => `PCR: ${ctx.raw?.toFixed(4)}`,
                    },
                },
            },
            scales: {
                x: { grid: { display: false }, ticks: { maxTicksLimit: 10 } },
                y: {
                    position: 'right',
                    grid: { color: 'rgba(255,255,255,0.03)' },
                    ticks: { callback: v => v.toFixed(2) },
                },
            },
        },
    });
}

// ─── PRICE vs OI ────────────────────────────────────────
async function loadPriceVsOI() {
    try {
        const sRes = await fetch('/api/strikes');
        const sData = await sRes.json();
        if (sData.strikes?.length) {
            renderStrikeList(sData.strikes, sData.atm);
            if (!selectedStrike) selectedStrike = sData.atm;
        }
    } catch (e) { console.error(e); }

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
    if (!container) return;
    const nearStrikes = strikes.filter(s => Math.abs(s - atm) <= 500);

    container.innerHTML = nearStrikes.map(s => {
        const isActive = s === selectedStrike || (selectedStrike === 0 && s === atm);
        return `<div class="strike-item ${isActive ? 'active' : ''}"
                     onclick="selectStrike(${s})">${s}${s === atm ? ' ATM' : ''}</div>`;
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
    if (!ctx) return;
    if (charts.callPriceOI) { charts.callPriceOI.destroy(); charts.callPriceOI = null; }

    const callData = data.call || [];
    if (!callData.length) return;
    const labels = callData.map(d => d.timestamp?.split('T')[1]?.slice(0,5) || '');

    charts.callPriceOI = new Chart(ctx, {
        type: 'line',
        data: {
            labels,
            datasets: [
                {
                    label: `${data.strike} CE OI`,
                    data: callData.map(d => d.oi),
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
                    data: callData.map(d => d.price),
                    borderColor: COLORS.orange,
                    borderWidth: 2,
                    pointRadius: 0,
                    tension: 0.3,
                    yAxisID: 'y1',
                },
            ],
        },
        options: dualAxisOptions('OI', 'Price (₹)'),
    });
}

function renderPutPriceOI(data) {
    const ctx = document.getElementById('chart-put-price-oi');
    if (!ctx) return;
    if (charts.putPriceOI) { charts.putPriceOI.destroy(); charts.putPriceOI = null; }

    const putData = data.put || [];
    if (!putData.length) return;
    const labels = putData.map(d => d.timestamp?.split('T')[1]?.slice(0,5) || '');

    charts.putPriceOI = new Chart(ctx, {
        type: 'line',
        data: {
            labels,
            datasets: [
                {
                    label: `${data.strike} PE OI`,
                    data: putData.map(d => d.oi),
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
                    data: putData.map(d => d.price),
                    borderColor: COLORS.orange,
                    borderWidth: 2,
                    pointRadius: 0,
                    tension: 0.3,
                    yAxisID: 'y1',
                },
            ],
        },
        options: dualAxisOptions('OI', 'Price (₹)'),
    });
}

function renderStraddleChart(data) {
    const ctx = document.getElementById('chart-straddle');
    if (!ctx) return;
    if (charts.straddle) { charts.straddle.destroy(); charts.straddle = null; }

    const straddleData = data.straddle || [];
    if (!straddleData.length) return;
    const labels = straddleData.map(d => d.timestamp?.split('T')[1]?.slice(0,5) || '');

    charts.straddle = new Chart(ctx, {
        type: 'line',
        data: {
            labels,
            datasets: [{
                label: 'Straddle Price',
                data: straddleData.map(d => d.price),
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
                    titleFont: { weight: '600' },
                    callbacks: {
                        label: ctx => `Straddle: ₹${ctx.raw?.toFixed(2)}`,
                    },
                },
            },
            scales: {
                x: { grid: { display: false }, ticks: { maxTicksLimit: 10 } },
                y: {
                    position: 'left',
                    grid: { color: 'rgba(255,255,255,0.03)' },
                    ticks: { callback: v => '₹' + v.toFixed(0) },
                },
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
                titleFont: { weight: '600' },
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
                ticks: { callback: v => '₹' + v.toFixed(0) },
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

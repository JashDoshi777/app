/* NIFTY OI Tracker — Dashboard JS v8 — StockMojo-matched */
Chart.defaults.color='rgba(232,234,237,0.5)';
Chart.defaults.borderColor='rgba(255,255,255,0.06)';
Chart.defaults.font.family="'Inter', sans-serif";
Chart.defaults.font.size=11;
Chart.defaults.plugins.legend.labels.usePointStyle=true;
Chart.defaults.animation={duration:400};

const C={green:'#26a69a',red:'#ef5350',blue:'#42a5f5',purple:'#ab47bc',cyan:'#26c6da',pink:'#ec407a',orange:'#ffa726'};
let currentTab='oi-table',currentTf=1,currentMode='live',selectedStrike=0,historicalDate='';
let currentDisplayMode='total',autoATM=true;
let charts={},tvChart=null,tvSeries=null;

document.addEventListener('DOMContentLoaded',()=>{
    initTabs();initTimeframeButtons();initModeButtons();initFilters();initDisplayMode();initATMMode();initDownload();
    startClock();checkMarketStatus();loadExpiryInfo();
    loadAllData();
    setInterval(()=>{if(currentMode==='live')loadAllData();},30000);
    setInterval(checkMarketStatus,10000);
});

function initTabs(){
    document.querySelectorAll('.tab-btn').forEach(btn=>{
        btn.addEventListener('click',()=>{
            document.querySelectorAll('.tab-btn').forEach(b=>b.classList.remove('active'));
            document.querySelectorAll('.tab-panel').forEach(p=>p.classList.remove('active'));
            btn.classList.add('active');currentTab=btn.dataset.tab;
            document.getElementById(`panel-${currentTab}`).classList.add('active');loadAllData();
        });
    });
}
function initTimeframeButtons(){
    document.querySelectorAll('.tf-btn').forEach(btn=>{
        btn.addEventListener('click',()=>{
            document.querySelectorAll('.tf-btn').forEach(b=>b.classList.remove('active'));
            btn.classList.add('active');currentTf=parseInt(btn.dataset.tf)||1;
            destroyTVChart();loadAllData();
        });
    });
}
function initModeButtons(){
    document.querySelectorAll('.mode-btn').forEach(btn=>{
        btn.addEventListener('click',()=>{
            document.querySelectorAll('.mode-btn').forEach(b=>b.classList.remove('active'));
            btn.classList.add('active');currentMode=btn.dataset.mode;
            const hc=document.getElementById('historical-controls');
            if(currentMode==='historical'){hc.classList.add('show');loadHistoricalDates();}
            else{hc.classList.remove('show');historicalDate='';destroyAllCharts();loadAllData();}
        });
    });
    const loadBtn=document.getElementById('load-historical-btn');
    if(loadBtn)loadBtn.addEventListener('click',()=>{
        const sel=document.getElementById('historical-date');
        historicalDate=sel?sel.value:'';
        if(historicalDate){destroyAllCharts();loadAllData();}
    });
}
function initFilters(){
    const rng=document.getElementById('strike-range');
    if(rng)rng.addEventListener('change',loadAllData);
}
function initDisplayMode(){
    document.querySelectorAll('.display-btn').forEach(btn=>{
        btn.addEventListener('click',()=>{
            document.querySelectorAll('.display-btn').forEach(b=>b.classList.remove('active'));
            btn.classList.add('active');currentDisplayMode=btn.dataset.display;loadAllData();
        });
    });
}
function initATMMode(){
    document.querySelectorAll('.atm-btn').forEach(btn=>{
        btn.addEventListener('click',()=>{
            document.querySelectorAll('.atm-btn').forEach(b=>b.classList.remove('active'));
            btn.classList.add('active');autoATM=btn.dataset.atmMode==='auto';loadAllData();
        });
    });
}

function destroyTVChart(){if(tvChart){try{tvChart.remove();}catch(e){}tvChart=null;tvSeries=null;}const el=document.getElementById('tv-candle-container');if(el)el.innerHTML='';}
function destroyAllCharts(){destroyTVChart();Object.keys(charts).forEach(k=>{if(charts[k]){try{charts[k].destroy();}catch(e){}}charts[k]=null;});charts={};}

async function loadHistoricalDates(){
    try{const res=await fetch('/api/historical-dates');const data=await res.json();
    const sel=document.getElementById('historical-date');
    if(sel&&data.dates){sel.innerHTML='<option value="">Select Date</option>'+data.dates.map(d=>`<option value="${d}">${d}</option>`).join('');}}catch(e){console.error(e);}
}
async function loadExpiryInfo(){
    try{const res=await fetch('/api/expiry-info');const data=await res.json();
    const badge=document.getElementById('expiry-badge');
    if(badge&&data.label)badge.textContent=data.label;}catch(e){}
}

function startClock(){
    const update=()=>{const now=new Date();document.getElementById('live-time').textContent=now.toLocaleTimeString('en-IN',{hour12:false,timeZone:'Asia/Kolkata'});};
    update();setInterval(update,1000);
}
async function checkMarketStatus(){
    try{const res=await fetch('/api/market-status');const d=await res.json();
    const b=document.getElementById('market-badge');b.textContent=d.is_open?'LIVE':'CLOSED';
    b.className='market-badge '+(d.is_open?'open':'closed');}catch(e){}
}

async function loadAllData(){
    try{
        if(currentTab==='oi-table')await loadOITable();
        if(currentTab==='smart-oi')await loadSmartOI();
        if(currentTab==='price-oi')await loadPriceVsOI();
    }catch(e){console.error('loadAllData:',e);}
}

/* ═══ COLUMN INFO SYSTEM ═══ */
const COL_INFO = {
    time: {t:'Time', d:'IST timestamp of when this data snapshot was captured.\n\nData is refreshed every 60 seconds during market hours (9:15 AM – 3:30 PM IST).'},
    put_oi: {t:'Put OI', d:'Total Put (PE) Open Interest summed across all strikes in the selected ATM ± Range.\n\nHigher Put OI generally indicates bearish hedging or bullish support (writers expect price to stay above).'},
    put_total: {t:'Put OI → Total', d:'Current total Put OI across all strikes in the ATM ± Range filter.\n\nThis value is re-aggregated from per-strike data every refresh.'},
    put_chg_day: {t:'Put OI → Chg (Day)', d:'Change in Put OI from previous trading day\'s closing value.\n\n• Positive = New put positions added today\n• Negative = Put positions closed/unwound today'},
    put_change: {t:'Put OI → Change', d:'Put OI change from the previous row (based on selected timeframe).\n\n• Positive = Put OI increased this interval\n• Negative = Put OI decreased this interval\n\nThis is the real-time OI flow indicator.'},
    call_oi: {t:'Call OI', d:'Total Call (CE) Open Interest summed across all strikes in the selected ATM ± Range.\n\nHigher Call OI generally indicates bullish speculation or bearish resistance (writers expect price to stay below).'},
    call_total: {t:'Call OI → Total', d:'Current total Call OI across all strikes in the ATM ± Range filter.\n\nThis value is re-aggregated from per-strike data every refresh.'},
    call_chg_day: {t:'Call OI → Chg (Day)', d:'Change in Call OI from previous trading day\'s closing value.\n\n• Positive = New call positions added today\n• Negative = Call positions closed/unwound today'},
    call_change: {t:'Call OI → Change', d:'Call OI change from the previous row (based on selected timeframe).\n\n• Positive = Call OI increased this interval\n• Negative = Call OI decreased this interval\n\nThis is the real-time OI flow indicator.'},
    pe_ce_oi: {t:'PE-CE OI', d:'Difference between Put OI and Call OI.\nFormula: Put OI − Call OI\n\n• Positive = More Puts than Calls → Bullish support\n• Negative = More Calls than Puts → Bearish pressure\n\nUsed to gauge net market sentiment.'},
    pe_ce_total: {t:'PE-CE OI → Total', d:'Current PE-CE OI difference value.\n\nFormula: Total Put OI − Total Call OI\n(for strikes within ATM ± Range)'},
    pe_ce_change: {t:'PE-CE OI → Change', d:'Change in PE-CE difference from the previous row.\n\n• Positive = Sentiment shifting bullish (more puts or fewer calls)\n• Negative = Sentiment shifting bearish (more calls or fewer puts)\n\nMatches StockMojo\'s "Change" column in PE-CE OI.'},
    pe_ce_chg_pct: {t:'PE-CE OI → Chg %', d:'Percentage change in PE-CE OI difference vs the previous row.\n\nFormula: (Change ÷ |Previous Total|) × 100\n\nShows relative magnitude of the shift — a large Chg % on a small total is more significant than a small Chg % on a large total.'},
    pcr: {t:'PCR (Put-Call Ratio)', d:'Put-Call Ratio = Total Put OI ÷ Total Call OI\n\n• Above 1.0 = More puts → Bullish support\n• 0.7 – 1.0 = Neutral zone\n• Below 0.7 = More calls → Bearish\n• Extreme values (>1.3 or <0.5) may signal reversals'},
    ce_vol: {t:'CE Volume', d:'Total Call option trading volume across all strikes in the selected range.\n\nHigh volume = Active trading/liquidity on the call side.\nVolume shows activity, while OI shows outstanding positions.'},
    pe_vol: {t:'PE Volume', d:'Total Put option trading volume across all strikes in the selected range.\n\nHigh volume = Active trading/liquidity on the put side.\nVolume shows activity, while OI shows outstanding positions.'},
    total_oi: {t:'Total OI (Futures)', d:'NIFTY Futures Open Interest.\n\n• Rising OI = New positions being created\n• Falling OI = Existing positions being closed\n\nCombine with price direction for LB/SB/SC/LU signals.'},
    signal: {t:'Signal', d:'Market action interpretation based on Futures Price + Total OI direction.\n\n• LB (Long Build): Price ↑ + OI ↑ → Bullish\n• SB (Short Build): Price ↓ + OI ↑ → Bearish\n• SC (Short Cover): Price ↑ + OI ↓ → Short-term Bullish\n• LU (Long Unwind): Price ↓ + OI ↓ → Short-term Bearish'},
};
function _i(key){return `<span class="col-info-icon" onclick="showColInfo('${key}',event)">ⓘ</span>`;}
window.showColInfo=function(key,evt){
    evt.stopPropagation();
    const old=document.getElementById('col-info-popup');if(old)old.remove();
    const info=COL_INFO[key];if(!info)return;
    const popup=document.createElement('div');
    popup.id='col-info-popup';popup.className='col-info-popup';
    popup.innerHTML=`<div class="info-title">${info.t}</div><div class="info-body">${info.d}</div>`;
    const rect=evt.target.getBoundingClientRect();
    popup.style.top=Math.min(rect.bottom+8, window.innerHeight-200)+'px';
    popup.style.left=Math.max(8, Math.min(rect.left, window.innerWidth-360))+'px';
    document.body.appendChild(popup);
    setTimeout(()=>{document.addEventListener('click',function dismiss(){popup.remove();document.removeEventListener('click',dismiss);},{once:true});},10);
};

function buildTableHeader(){
    const thead=document.getElementById('oi-table-head');if(!thead)return;
    const dm=currentDisplayMode;
    let h1='',h2='';
    h1+=`<th rowspan="2" class="col-time">Time ${_i('time')}</th>`;
    if(dm==='total'||dm==='all'){
        h1+=`<th colspan="${dm==='all'?5:4}" class="col-group put-header col-divider">Put OI ${_i('put_oi')}</th>`;
        h1+=`<th colspan="${dm==='all'?5:4}" class="col-group call-header col-divider">Call OI ${_i('call_oi')}</th>`;
    } else {
        h1+=`<th colspan="4" class="col-group put-header col-divider">Put OI ${_i('put_oi')}</th>`;
        h1+=`<th colspan="4" class="col-group call-header col-divider">Call OI ${_i('call_oi')}</th>`;
    }
    h1+=`<th colspan="4" class="col-group diff-header">PE-CE OI ${_i('pe_ce_oi')}</th>`;
    // Sub headers
    if(dm==='total'){
        h2+=`<th class="sub">Total</th><th class="sub">Change</th><th class="sub">Std(10)</th><th class="sub col-divider">Z</th>`;
        h2+=`<th class="sub">Total</th><th class="sub">Change</th><th class="sub">Std(10)</th><th class="sub col-divider">Z</th>`;
    } else if(dm==='change'){
        h2+=`<th class="sub">Chg (Day)</th><th class="sub">Change</th><th class="sub">Std(10)</th><th class="sub col-divider">Z</th>`;
        h2+=`<th class="sub">Chg (Day)</th><th class="sub">Change</th><th class="sub">Std(10)</th><th class="sub col-divider">Z</th>`;
    } else {
        h2+=`<th class="sub">Total</th><th class="sub">Chg (Day)</th><th class="sub">Change</th><th class="sub">Std(10)</th><th class="sub col-divider">Z</th>`;
        h2+=`<th class="sub">Total</th><th class="sub">Chg (Day)</th><th class="sub">Change</th><th class="sub">Std(10)</th><th class="sub col-divider">Z</th>`;
    }
    h2+=`<th class="sub">Total</th><th class="sub">Change</th><th class="sub">Net Z</th><th class="sub">Signal</th>`;
    thead.innerHTML=`<tr>${h1}</tr><tr>${h2}</tr>`;
}

/* ═══ API RESPONSE CACHE ═══ */
const _oiCache={};
const CACHE_TTL_LIVE=20000;  // 20s for live (data updates every ~60s)
const CACHE_TTL_HIST=300000; // 5min for historical (static data)

function _oiCacheKey(){
    const rng=document.getElementById('strike-range');const range=rng?rng.value:10;
    return `${currentMode}_${currentTf}_${range}_${autoATM}_${historicalDate}`;
}

async function loadOITable(){
    try{
        const rng=document.getElementById('strike-range');const range=rng?rng.value:10;
        const key=_oiCacheKey();
        const cached=_oiCache[key];
        const ttl=currentMode==='live'?CACHE_TTL_LIVE:CACHE_TTL_HIST;

        // Show cached data INSTANTLY if available and fresh
        if(cached&&(Date.now()-cached.ts)<ttl){
            renderOITable(cached.data);
            return;
        }

        // If stale cache exists, show it immediately then refresh in background
        if(cached){
            renderOITable(cached.data);
        }

        // Fetch fresh data
        let url=`/api/oi-table?tf=${currentTf}&range_strikes=${range}&auto_atm=${autoATM}`;
        if(currentMode==='historical'&&historicalDate)url+=`&mode=historical&date=${historicalDate}`;
        const res=await fetch(url);const data=await res.json();

        // Store in cache
        _oiCache[key]={data,ts:Date.now()};
        renderOITable(data);
    }catch(e){console.error('OI Table:',e);}
}

function renderOITable(data){
    const rd=document.getElementById('range-display');
    if(rd&&data.range_display)rd.textContent=data.range_display;
    buildTableHeader();
    if(!data.rows||!data.rows.length){
        document.getElementById('oi-table-body').innerHTML='<tr><td colspan="13" class="empty-msg">No data available</td></tr>';return;
    }
    const raw0=data.rows[0]._raw;
    if(raw0)document.getElementById('underlying-price').textContent=raw0.underlying?.toFixed(2)||'--';
    const dm=currentDisplayMode;
    const rows=data.rows;

    const tbody=document.getElementById('oi-table-body');
    tbody.innerHTML=rows.map((r)=>{
        const raw=r._raw||{};
        const sig=r.signal||'N/A';const arrow=r.signal_arrow||'';
        const sigClass=sig==='LB'||sig==='SC'?'up':sig==='SB'||sig==='LU'?'down':'side';

        // Highlight the row when the Z-score signal engine says BUY or SELL
        const zSignal=raw.signal_z||'--';
        let rowCls=(zSignal==='BUY'||zSignal==='SELL')?'row-highlight-yellow':'';

        let cols=`<td class="col-time">${r.time}</td>`;
        if(dm==='total'){
            cols+=`<td class="${vc(raw.total_pe_oi)}">${r.pe_oi_total}</td>`;
            cols+=`<td class="${vc(raw.pe_oi_change)}">${r.pe_oi_change}</td>`;
            cols+=`<td class="val-neutral">${r.pe_std10||'--'}</td>`;
            cols+=`<td class="${zClass(raw.pe_z)} col-divider">${r.pe_z||'--'}</td>`;
            cols+=`<td class="${vc(raw.total_ce_oi)}">${r.ce_oi_total}</td>`;
            cols+=`<td class="${vc(raw.ce_oi_change)}">${r.ce_oi_change}</td>`;
            cols+=`<td class="val-neutral">${r.ce_std10||'--'}</td>`;
            cols+=`<td class="${zClass(raw.ce_z)} col-divider">${r.ce_z||'--'}</td>`;
        } else if(dm==='change'){
            cols+=`<td class="${vc(raw.pe_oi_change_day)}">${r.pe_oi_change_day}</td>`;
            cols+=`<td class="${vc(raw.pe_oi_change)}">${r.pe_oi_change}</td>`;
            cols+=`<td class="val-neutral">${r.pe_std10||'--'}</td>`;
            cols+=`<td class="${zClass(raw.pe_z)} col-divider">${r.pe_z||'--'}</td>`;
            cols+=`<td class="${vc(raw.ce_oi_change_day)}">${r.ce_oi_change_day}</td>`;
            cols+=`<td class="${vc(raw.ce_oi_change)}">${r.ce_oi_change}</td>`;
            cols+=`<td class="val-neutral">${r.ce_std10||'--'}</td>`;
            cols+=`<td class="${zClass(raw.ce_z)} col-divider">${r.ce_z||'--'}</td>`;
        } else {
            cols+=`<td class="${vc(raw.total_pe_oi)}">${r.pe_oi_total}</td>`;
            cols+=`<td class="${vc(raw.pe_oi_change_day)}">${r.pe_oi_change_day}</td>`;
            cols+=`<td class="${vc(raw.pe_oi_change)}">${r.pe_oi_change}</td>`;
            cols+=`<td class="val-neutral">${r.pe_std10||'--'}</td>`;
            cols+=`<td class="${zClass(raw.pe_z)} col-divider">${r.pe_z||'--'}</td>`;
            cols+=`<td class="${vc(raw.total_ce_oi)}">${r.ce_oi_total}</td>`;
            cols+=`<td class="${vc(raw.ce_oi_change_day)}">${r.ce_oi_change_day}</td>`;
            cols+=`<td class="${vc(raw.ce_oi_change)}">${r.ce_oi_change}</td>`;
            cols+=`<td class="val-neutral">${r.ce_std10||'--'}</td>`;
            cols+=`<td class="${zClass(raw.ce_z)} col-divider">${r.ce_z||'--'}</td>`;
        }
        cols+=`<td class="${raw.pe_ce_diff>0?'pe-ce-pos':'pe-ce-neg'}">${r.pe_ce_total}</td>`;
        cols+=`<td class="${vc(raw.pe_ce_diff_change)}">${r.pe_ce_change}</td>`;
        cols+=`<td class="${zClass(raw.net_z)}">${r.net_z||'--'}</td>`;
        cols+=`<td class="${signalClass(zSignal)}">${zSignal}</td>`;
        return `<tr class="${rowCls}">${cols}</tr>`;
    }).join('');
}
function vc(v){return(!v||v===0)?'val-neutral':v>0?'val-pos':'val-neg';}
function zClass(v){if(v===undefined||v===null||v==='--')return'val-neutral';const a=Math.abs(v);if(a>=3)return'val-neg';if(a>=2)return'val-pos';return'val-neutral';}
function signalClass(sig){if(sig==='BUY')return'val-pos';if(sig==='SELL')return'val-neg';return'val-neutral';}

/* ═══ SMART OI CHARTS ═══ */
async function loadSmartOI(){
    try{
        const rng=document.getElementById('strike-range');const range=rng?rng.value:10;
        if(currentMode==='historical'&&historicalDate){
            const res=await fetch(`/api/historical-chart?date=${historicalDate}&tf=${currentTf}&range_strikes=${range}`);
            const data=await res.json();
            if(data.timestamps&&data.timestamps.length){destroyTVChart();renderTVCandle(data.candles||[]);renderOILines(data);renderPCR(data);}
            else{showChartEmpty('chart-oi-lines','No data');showChartEmpty('chart-pcr','No data');destroyTVChart();}
            return;
        }
        const[oiRes,candleRes]=await Promise.all([fetch(`/api/oi-chart?tf=${currentTf}&range_strikes=${range}`),fetch(`/api/candles?tf=${currentTf}`)]);
        const oiData=await oiRes.json();const candleData=await candleRes.json();
        renderTVCandle(candleData.candles||[]);renderOILines(oiData);renderPCR(oiData);
    }catch(e){console.error('SmartOI:',e);}
}
function showChartEmpty(canvasId,msg){const ctx=document.getElementById(canvasId);if(!ctx)return;if(charts[canvasId]){charts[canvasId].destroy();charts[canvasId]=null;}charts[canvasId]=new Chart(ctx,{type:'line',data:{labels:[msg],datasets:[{data:[0],borderColor:C.blue}]},options:{responsive:true,maintainAspectRatio:false,plugins:{legend:{display:false}}}});}
function renderTVCandle(candles){
    const el=document.getElementById('tv-candle-container');if(!el)return;
    if(!tvChart){el.innerHTML='';tvChart=LightweightCharts.createChart(el,{width:el.clientWidth,height:320,layout:{background:{type:'solid',color:'#0c0e14'},textColor:'rgba(232,234,237,0.5)',fontFamily:"'Inter',sans-serif",fontSize:11},grid:{vertLines:{color:'rgba(255,255,255,0.03)'},horzLines:{color:'rgba(255,255,255,0.03)'}},crosshair:{mode:LightweightCharts.CrosshairMode.Normal},rightPriceScale:{borderColor:'rgba(255,255,255,0.06)',scaleMargins:{top:0.1,bottom:0.1}},timeScale:{borderColor:'rgba(255,255,255,0.06)',timeVisible:true,secondsVisible:false}});tvSeries=tvChart.addCandlestickSeries({upColor:C.green,downColor:C.red,borderUpColor:C.green,borderDownColor:C.red,wickUpColor:C.green,wickDownColor:C.red});new ResizeObserver(()=>{if(tvChart)tvChart.applyOptions({width:el.clientWidth});}).observe(el);}
    if(!candles.length)return;const d=candles.map(c=>{const t=c.timestamp?Math.floor(new Date(c.timestamp).getTime()/1000):0;return{time:t,open:c.open,high:c.high,low:c.low,close:c.close};}).filter(x=>x.time>0&&!isNaN(x.open));const seen=new Map();d.forEach(x=>seen.set(x.time,x));const unique=[...seen.values()].sort((a,b)=>a.time-b.time);if(unique.length>0){tvSeries.setData(unique);tvChart.timeScale().fitContent();}
}
function renderOILines(data){
    const ctx=document.getElementById('chart-oi-lines');if(!ctx)return;if(charts.oiLines){charts.oiLines.destroy();charts.oiLines=null;}
    if(!data.timestamps||!data.timestamps.length){charts.oiLines=new Chart(ctx,{type:'line',data:{labels:['Waiting...'],datasets:[{data:[0],borderColor:C.blue}]},options:{responsive:true,maintainAspectRatio:false,plugins:{legend:{display:false}}}});return;}
    charts.oiLines=new Chart(ctx,{type:'line',data:{labels:data.timestamps,datasets:[{label:'Put OI',data:data.put_oi,borderColor:C.pink,borderWidth:2,pointRadius:2,pointHoverRadius:5,tension:0.3},{label:'Call OI',data:data.call_oi,borderColor:C.green,borderWidth:2,pointRadius:2,pointHoverRadius:5,tension:0.3},{label:'PE-CE',data:data.pe_ce,borderColor:C.purple,borderWidth:2,pointRadius:0,borderDash:[5,3],tension:0.3,yAxisID:'y1'}]},options:{responsive:true,maintainAspectRatio:false,interaction:{mode:'index',intersect:false},hover:{mode:'index',intersect:false},plugins:{tooltip:{enabled:true,backgroundColor:'rgba(20,22,32,0.95)',borderColor:'rgba(255,255,255,0.1)',borderWidth:1,titleFont:{weight:'600'},bodyFont:{size:12},padding:10,callbacks:{label:c=>`${c.dataset.label}: ${fmtL(c.raw)}`}}},scales:{x:{grid:{display:false},ticks:{maxTicksLimit:10}},y:{position:'left',grid:{color:'rgba(255,255,255,0.03)'},ticks:{callback:v=>fmtL(v)},title:{display:true,text:'OI',color:'rgba(255,255,255,0.3)'}},y1:{position:'right',grid:{display:false},ticks:{callback:v=>fmtL(v)},title:{display:true,text:'PE-CE',color:'rgba(255,255,255,0.3)'}}}}});
}
function renderPCR(data){
    const ctx=document.getElementById('chart-pcr');if(!ctx)return;if(charts.pcr){charts.pcr.destroy();charts.pcr=null;}
    if(!data.timestamps||!data.timestamps.length){charts.pcr=new Chart(ctx,{type:'line',data:{labels:['Waiting...'],datasets:[{data:[0],borderColor:C.cyan}]},options:{responsive:true,maintainAspectRatio:false,plugins:{legend:{display:false}}}});return;}
    charts.pcr=new Chart(ctx,{type:'line',data:{labels:data.timestamps,datasets:[{label:'PCR',data:data.pcr,borderColor:C.cyan,borderWidth:2,pointRadius:2,pointHoverRadius:5,fill:true,backgroundColor:'rgba(38,198,218,0.08)',tension:0.4}]},options:{responsive:true,maintainAspectRatio:false,interaction:{mode:'index',intersect:false},hover:{mode:'index',intersect:false},plugins:{tooltip:{enabled:true,backgroundColor:'rgba(20,22,32,0.95)',borderColor:'rgba(255,255,255,0.1)',borderWidth:1,titleFont:{weight:'600'},bodyFont:{size:12},padding:10,callbacks:{label:c=>`PCR: ${c.raw?.toFixed(4)}`}}},scales:{x:{grid:{display:false},ticks:{maxTicksLimit:10}},y:{position:'right',grid:{color:'rgba(255,255,255,0.03)'},ticks:{callback:v=>v.toFixed(2)}}}}});
}

/* ═══ PRICE vs OI ═══ */
async function loadPriceVsOI(){
    if(currentMode==='historical'&&historicalDate){showChartEmpty('chart-call-price-oi','Select Live mode');showChartEmpty('chart-put-price-oi','Select Live mode');showChartEmpty('chart-straddle','Select Live mode');document.getElementById('strike-list').innerHTML='<div class="strike-item">Historical mode</div>';return;}
    try{const sRes=await fetch('/api/strikes');const sData=await sRes.json();if(sData.strikes?.length){renderStrikeList(sData.strikes,sData.atm);if(!selectedStrike)selectedStrike=sData.atm;}}catch(e){console.error(e);}
    if(selectedStrike){try{const res=await fetch(`/api/price-vs-oi?strike=${selectedStrike}`);const data=await res.json();renderCallPriceOI(data);renderPutPriceOI(data);renderStraddle(data);}catch(e){console.error(e);}}
}
function renderStrikeList(strikes,atm){const el=document.getElementById('strike-list');if(!el)return;const near=strikes.filter(s=>Math.abs(s-atm)<=500);el.innerHTML=near.map(s=>{const act=s===selectedStrike||(selectedStrike===0&&s===atm);return`<div class="strike-item ${act?'active':''}" onclick="selectStrike(${s})">${s}${s===atm?' ATM':''}</div>`;}).join('');}
window.selectStrike=function(strike){selectedStrike=strike;document.querySelectorAll('.strike-item').forEach(el=>{const num=parseFloat(el.textContent);el.classList.toggle('active',num===strike);});(async()=>{try{const res=await fetch(`/api/price-vs-oi?strike=${selectedStrike}`);const data=await res.json();renderCallPriceOI(data);renderPutPriceOI(data);renderStraddle(data);}catch(e){console.error(e);}})();};
function renderCallPriceOI(data){const ctx=document.getElementById('chart-call-price-oi');if(!ctx)return;if(charts.callOI){charts.callOI.destroy();charts.callOI=null;}const cd=data.call||[];if(!cd.length)return;const labels=cd.map(d=>(d.timestamp?.split('T')[1]||'').slice(0,5));charts.callOI=new Chart(ctx,{type:'line',data:{labels,datasets:[{label:`${data.strike} CE OI`,data:cd.map(d=>d.oi),borderColor:C.green,borderWidth:2,pointRadius:2,pointHoverRadius:5,tension:0.3,yAxisID:'y',fill:true,backgroundColor:'rgba(38,166,154,0.08)'},{label:`${data.strike} CE Price`,data:cd.map(d=>d.price),borderColor:C.orange,borderWidth:2,pointRadius:2,pointHoverRadius:5,tension:0.3,yAxisID:'y1'}]},options:dualOpts('OI','Price (₹)')});}
function renderPutPriceOI(data){const ctx=document.getElementById('chart-put-price-oi');if(!ctx)return;if(charts.putOI){charts.putOI.destroy();charts.putOI=null;}const pd=data.put||[];if(!pd.length)return;const labels=pd.map(d=>(d.timestamp?.split('T')[1]||'').slice(0,5));charts.putOI=new Chart(ctx,{type:'line',data:{labels,datasets:[{label:`${data.strike} PE OI`,data:pd.map(d=>d.oi),borderColor:C.red,borderWidth:2,pointRadius:2,pointHoverRadius:5,tension:0.3,yAxisID:'y',fill:true,backgroundColor:'rgba(239,83,80,0.08)'},{label:`${data.strike} PE Price`,data:pd.map(d=>d.price),borderColor:C.orange,borderWidth:2,pointRadius:2,pointHoverRadius:5,tension:0.3,yAxisID:'y1'}]},options:dualOpts('OI','Price (₹)')});}
function renderStraddle(data){const ctx=document.getElementById('chart-straddle');if(!ctx)return;if(charts.straddle){charts.straddle.destroy();charts.straddle=null;}const sd=data.straddle||[];if(!sd.length)return;const labels=sd.map(d=>(d.timestamp?.split('T')[1]||'').slice(0,5));charts.straddle=new Chart(ctx,{type:'line',data:{labels,datasets:[{label:'Straddle',data:sd.map(d=>d.price),borderColor:C.blue,borderWidth:2,pointRadius:2,pointHoverRadius:5,tension:0.3,fill:true,backgroundColor:'rgba(66,165,245,0.08)'}]},options:{responsive:true,maintainAspectRatio:false,interaction:{mode:'index',intersect:false},hover:{mode:'index',intersect:false},plugins:{tooltip:{enabled:true,backgroundColor:'rgba(20,22,32,0.95)',borderColor:'rgba(255,255,255,0.1)',borderWidth:1,titleFont:{weight:'600'},bodyFont:{size:12},padding:10,callbacks:{label:c=>`Straddle: ₹${c.raw?.toFixed(2)}`}}},scales:{x:{grid:{display:false},ticks:{maxTicksLimit:10}},y:{position:'left',grid:{color:'rgba(255,255,255,0.03)'},ticks:{callback:v=>'₹'+v.toFixed(0)}}}}});}
function dualOpts(lbl,rbl){return{responsive:true,maintainAspectRatio:false,interaction:{mode:'index',intersect:false},hover:{mode:'index',intersect:false},plugins:{tooltip:{enabled:true,backgroundColor:'rgba(20,22,32,0.95)',borderColor:'rgba(255,255,255,0.1)',borderWidth:1,titleFont:{weight:'600'},bodyFont:{size:12},padding:10,callbacks:{label:c=>c.datasetIndex===0?`OI: ${fmtL(c.raw)}`:`Price: ₹${c.raw?.toFixed(2)}`}}},scales:{x:{grid:{display:false},ticks:{maxTicksLimit:10}},y:{position:'left',grid:{color:'rgba(255,255,255,0.03)'},ticks:{callback:v=>fmtL(v)},title:{display:true,text:lbl,color:'rgba(255,255,255,0.3)'}},y1:{position:'right',grid:{display:false},ticks:{callback:v=>'₹'+v.toFixed(0)},title:{display:true,text:rbl,color:'rgba(255,255,255,0.3)'}}}};}
function fmtL(n){if(n===null||n===undefined)return'--';const a=Math.abs(n);if(a>=1e7)return(n/1e7).toFixed(1)+' Cr';if(a>=1e5)return(n/1e5).toFixed(1)+' L';if(a>=1e3)return(n/1e3).toFixed(1)+' K';return n.toString();}

/* ═══ CSV / MD DOWNLOAD ═══ */
function initDownload(){
    const btn=document.getElementById('download-csv-btn');
    if(btn)btn.addEventListener('click',downloadCSV);
    const mdBtn=document.getElementById('download-md-btn');
    if(mdBtn)mdBtn.addEventListener('click',downloadMD);
}
function _triggerDownload(url){
    const a=document.createElement('a');
    a.href=url;a.download='';
    document.body.appendChild(a);a.click();document.body.removeChild(a);
}
function downloadCSV(){
    const rng=document.getElementById('strike-range');const range=rng?rng.value:5;
    let url=`/api/download-oi?tf=${currentTf}&range_strikes=${range}&auto_atm=${autoATM}`;
    if(currentMode==='historical'&&historicalDate){
        url+=`&mode=historical&date=${historicalDate}`;
    }
    _triggerDownload(url);
}
function downloadMD(){
    const rng=document.getElementById('strike-range');const range=rng?rng.value:5;
    let url=`/api/download-oi-md?tf=${currentTf}&range_strikes=${range}&auto_atm=${autoATM}`;
    if(currentMode==='historical'&&historicalDate){
        url+=`&mode=historical&date=${historicalDate}`;
    }
    _triggerDownload(url);
}

// app.js

const API_BASE = '/api'; // Assuming served from the same host in prod, or use http://localhost:5000/api in dev

// DOM Elements
const totalEqEl = document.getElementById('totalEq');
const availBalEl = document.getElementById('availBal');
const completedTradesEl = document.getElementById('completedTrades');
const btcPriceEl = document.getElementById('btcPrice');
const winRateEl = document.getElementById('winRate');
const totalPnlEl = document.getElementById('totalPnl');
const annualReturnEl = document.getElementById('annualReturn');
const modeBadgeEl = document.getElementById('modeBadge');
const leverBadgeEl = document.getElementById('leverBadge');
const positionsBodyEl = document.getElementById('positionsBody');
const tradesBodyEl = document.getElementById('tradesBody');
const logTerminalEl = document.getElementById('logTerminal');
const pulseDot = document.querySelector('.pulse-dot');

const filterStartEl = document.getElementById('filterStart');
const filterEndEl = document.getElementById('filterEnd');
const applyFilterBtn = document.getElementById('applyFilterBtn');
const clearFilterBtn = document.getElementById('clearFilterBtn');

// State
let lastLogCount = 0;
let equityChart = null;  // Chart.js instance
let chartRangeDays = 0;  // 0 = ALL, 7 = 7D, 30 = 30D, 90 = 90D
let allEquityHistory = [];  // full cache of raw history from API
let lastLiveEq = 0;

// Fetch and update status
async function fetchStatus() {
    try {
        let url = `${API_BASE}/status`;
        const start = filterStartEl.value;
        const end = filterEndEl.value;
        if (start || end) {
            const params = new URLSearchParams();
            if (start) params.append('start', start);
            if (end) params.append('end', end);
            url += `?${params.toString()}`;
        }
        
        const res = await fetch(url);
        const data = await res.json();

        if (data.success) {
            updateMetrics(data.data);
            updatePositions(data.data.positions);
            if (data.data.trade_history) updateTrades(data.data.trade_history);
            
            const liveEq = data.data.account ? data.data.account.totalEq : 0;
            if (data.data.equity_history) {
                allEquityHistory = data.data.equity_history;  // cache full data
                lastLiveEq = liveEq;
                renderEquityChart();
            }
            
            pulseDot.style.backgroundColor = 'var(--long-color)';
            pulseDot.style.boxShadow = '0 0 10px var(--long-color)';
        } else {
            console.error("Error from API:", data.error);
            pulseDot.style.backgroundColor = 'var(--short-color)';
            pulseDot.style.boxShadow = '0 0 10px var(--short-color)';
        }
    } catch (err) {
        console.error("Failed to fetch status:", err);
        pulseDot.style.backgroundColor = 'var(--short-color)';
        pulseDot.style.boxShadow = '0 0 10px var(--short-color)';
    }
}

// Fetch and update logs
async function fetchLogs() {
    try {
        const res = await fetch(`${API_BASE}/logs?lines=70`);
        const data = await res.json();

        if (data.success) {
            renderLogs(data.logs);
        }
    } catch (err) {
        console.error("Failed to fetch logs:", err);
    }
}

function updateMetrics(data) {
    // Account
    if (data.account) {
        totalEqEl.textContent = `${parseFloat(data.account.totalEq || 0).toFixed(2)} USDT`;
        availBalEl.textContent = `${parseFloat(data.account.availBal || 0).toFixed(2)} USDT`;
    } else {
        totalEqEl.textContent = `-- USDT`;
        availBalEl.textContent = `-- USDT`;
    }
    
    // Price
    if (data.current_price) {
        btcPriceEl.textContent = `$${parseFloat(data.current_price).toFixed(1)}`;
    }

    // Metadata
    completedTradesEl.textContent = `L: ${data.metadata.completed_long_trades} | S: ${data.metadata.completed_short_trades}`;
    
    // Badges
    modeBadgeEl.textContent = data.metadata.simulated ? 'SIMULATED' : 'LIVE';
    modeBadgeEl.style.color = data.metadata.simulated ? '#ffbd2e' : 'var(--long-color)';
    modeBadgeEl.style.borderColor = data.metadata.simulated ? 'rgba(255,189,46,0.3)' : 'rgba(0,230,118,0.3)';

    leverBadgeEl.textContent = `${data.metadata.lever}x LEVERAGE`;

    if (winRateEl && data.metadata.win_rate !== undefined) {
        winRateEl.textContent = `${parseFloat(data.metadata.win_rate).toFixed(1)}%`;
    }
    if (totalPnlEl && data.metadata.cumulative_pnl_pct !== undefined) {
        const pnlVal = parseFloat(data.metadata.cumulative_pnl_pct) * 100;
        totalPnlEl.textContent = `${pnlVal > 0 ? '+' : ''}${pnlVal.toFixed(2)}%`;
        totalPnlEl.style.color = pnlVal > 0 ? 'var(--long-color)' : (pnlVal < 0 ? 'var(--short-color)' : 'inherit');
    }
    if (annualReturnEl && data.metadata.annualized_return !== undefined) {
        const annVal = parseFloat(data.metadata.annualized_return) * 100;
        annualReturnEl.textContent = `${annVal > 0 ? '+' : ''}${annVal.toFixed(2)}%`;
        annualReturnEl.style.color = annVal > 0 ? 'var(--long-color)' : (annVal < 0 ? 'var(--short-color)' : 'inherit');
    }
}

function updatePositions(positions) {
    positionsBodyEl.innerHTML = '';
    
    let hasPositions = false;

    if (positions.long && positions.long.sz > 0) {
        hasPositions = true;
        positionsBodyEl.appendChild(createPositionRow('LONG', positions.long));
    }
    
    if (positions.short && positions.short.sz > 0) {
        hasPositions = true;
        positionsBodyEl.appendChild(createPositionRow('SHORT', positions.short));
    }

    if (!hasPositions) {
        positionsBodyEl.innerHTML = `<tr><td colspan="5" class="text-center" style="color: var(--text-muted);">No active positions</td></tr>`;
    }
}

function createPositionRow(side, posData) {
    const tr = document.createElement('tr');
    
    const isLong = side === 'LONG';
    const sideClass = isLong ? 'text-long' : 'text-short';
    
    // PnL color
    const pnl = parseFloat(posData.upl);
    const pnlColor = pnl >= 0 ? 'var(--long-color)' : 'var(--short-color)';
    const pnlSign = pnl > 0 ? '+' : '';

    tr.innerHTML = `
        <td class="${sideClass}">${side}</td>
        <td>${posData.sz}</td>
        <td>${parseFloat(posData.avgPx).toFixed(2)}</td>
        <td style="color: ${pnlColor}; font-weight: 600;">${pnlSign}${pnl.toFixed(2)} USDT</td>
        <td>${posData.lever}x</td>
    `;
    return tr;
}

function updateTrades(trades) {
    if (!tradesBodyEl) return;
    tradesBodyEl.innerHTML = '';
    
    if (trades.length === 0) {
        tradesBodyEl.innerHTML = `<tr><td colspan="7" class="text-center" style="color: var(--text-muted);">No closed trades yet</td></tr>`;
        return;
    }
    
    trades.forEach(trade => {
        const tr = document.createElement('tr');
        
        const isLong = trade.direction === 'long';
        const sideStr = isLong ? 'LONG' : 'SHORT';
        const sideClass = isLong ? 'text-long' : 'text-short';
        
        const pnl = parseFloat(trade.lev_pnl_pct) * 100 || 0;
        const pnlColor = pnl > 0 ? 'var(--long-color)' : (pnl < 0 ? 'var(--short-color)' : 'inherit');
        const pnlSign = pnl > 0 ? '+' : '';
        
        const dateObj = new Date(trade.time);
        const timeStr = `${dateObj.getMonth()+1}-${dateObj.getDate()} ${dateObj.getHours().toString().padStart(2,'0')}:${dateObj.getMinutes().toString().padStart(2,'0')}`;

        tr.innerHTML = `
            <td style="color: var(--text-muted); font-size: 0.85em;">${timeStr}</td>
            <td class="${sideClass}">${sideStr}</td>
            <td>${trade.sz}</td>
            <td>${parseFloat(trade.entry_price).toFixed(2)}</td>
            <td>${parseFloat(trade.exit_price).toFixed(2)}</td>
            <td style="color: ${pnlColor}; font-weight: 600;">${pnlSign}${pnl.toFixed(2)}%</td>
            <td style="font-size: 0.85em;">${trade.reason || '-'}</td>
        `;
        tradesBodyEl.appendChild(tr);
    });
}

function renderLogs(logs) {
    // Only update if logs changed (simple check on length, though content might change. 
    // For a robust system we'd check last log line or hash, but this is fine for now).
    // We just re-render and scroll to bottom.
    logTerminalEl.innerHTML = '';
    
    logs.forEach(line => {
        const div = document.createElement('div');
        div.className = 'log-line';
        
        // Basic syntax highlighting based on log level keywords
        if (line.includes('[INFO]')) div.classList.add('log-info');
        else if (line.includes('[ERROR]')) div.classList.add('log-error');
        else if (line.includes('[WARNING]')) div.classList.add('log-warn');
        else div.classList.add('log-debug');
        
        div.textContent = line;
        logTerminalEl.appendChild(div);
    });

    // Auto-scroll to bottom
    logTerminalEl.scrollTop = logTerminalEl.scrollHeight;
}

function initChart() {
    const canvas = document.getElementById('equityChart');
    if (!canvas) return;
    
    const ctx = canvas.getContext('2d');
    equityChart = new Chart(ctx, {
        type: 'line',
        data: {
            labels: [],
            datasets: [{
                label: 'Account Equity (USDT)',
                data: [],
                borderColor: '#00e676',
                backgroundColor: 'rgba(0, 230, 118, 0.08)',
                borderWidth: 2,
                pointRadius: 3,
                pointBackgroundColor: '#00e676',
                pointBorderColor: 'transparent',
                tension: 0.3,
                fill: true,
            }]
        },
        options: {
            responsive: true,
            maintainAspectRatio: false,
            animation: { duration: 400 },
            interaction: { mode: 'index', intersect: false },
            plugins: {
                legend: { display: false },
                tooltip: {
                    backgroundColor: 'rgba(10, 14, 35, 0.9)',
                    titleColor: '#8892b0',
                    bodyColor: '#e2e8f0',
                    borderColor: 'rgba(0,230,118,0.3)',
                    borderWidth: 1,
                    callbacks: {
                        label: ctx => ` ${ctx.parsed.y.toFixed(4)} USDT`
                    }
                }
            },
            scales: {
                x: {
                    grid: { color: 'rgba(42, 46, 57, 0.5)' },
                    ticks: { color: '#8892b0', maxTicksLimit: 8, maxRotation: 0 }
                },
                y: {
                    grid: { color: 'rgba(42, 46, 57, 0.5)' },
                    ticks: {
                        color: '#8892b0',
                        callback: val => val.toFixed(2)
                    }
                }
            }
        }
    });
}

// Filter history by range and re-render chart
function renderEquityChart() {
    if (!equityChart) return;
    
    let history = allEquityHistory;
    
    // Filter by selected time range
    if (chartRangeDays > 0) {
        const cutoff = Date.now() - chartRangeDays * 24 * 60 * 60 * 1000;
        history = history.filter(item => new Date(item.time).getTime() >= cutoff);
    }
    
    const labels = [];
    const values = [];
    
    history.forEach(item => {
        const d = new Date(item.time);
        // For ranges > 7 days, show only date; otherwise show date + time
        let label;
        if (chartRangeDays > 7) {
            label = `${(d.getMonth()+1).toString().padStart(2,'0')}-${d.getDate().toString().padStart(2,'0')}`;
        } else {
            label = `${(d.getMonth()+1).toString().padStart(2,'0')}-${d.getDate().toString().padStart(2,'0')} ${d.getHours().toString().padStart(2,'0')}:${d.getMinutes().toString().padStart(2,'0')}`;
        }
        labels.push(label);
        values.push(parseFloat(item.equity));
    });
    
    // Add current live tick
    if (lastLiveEq && parseFloat(lastLiveEq) > 0) {
        const now = new Date();
        const label = chartRangeDays > 7
            ? `${(now.getMonth()+1).toString().padStart(2,'0')}-${now.getDate().toString().padStart(2,'0')}`
            : `${(now.getMonth()+1).toString().padStart(2,'0')}-${now.getDate().toString().padStart(2,'0')} ${now.getHours().toString().padStart(2,'0')}:${now.getMinutes().toString().padStart(2,'0')}`;
        if (labels.length === 0 || labels[labels.length - 1] !== label) {
            labels.push(label);
            values.push(parseFloat(lastLiveEq));
        } else {
            values[values.length - 1] = parseFloat(lastLiveEq);
        }
    }
    
    equityChart.data.labels = labels;
    equityChart.data.datasets[0].data = values;
    equityChart.update('none');
}

// Initial fetch and interval setup
async function init() {
    initChart();
    // Bind trade filter buttons
    if (applyFilterBtn) {
        applyFilterBtn.addEventListener('click', () => {
            fetchStatus();
        });
    }
    if (clearFilterBtn) {
        clearFilterBtn.addEventListener('click', () => {
            filterStartEl.value = '';
            filterEndEl.value = '';
            fetchStatus();
        });
    }
    // Bind chart range buttons
    const rangeBtns = document.querySelectorAll('.chart-range-btn');
    rangeBtns.forEach(btn => {
        btn.addEventListener('click', () => {
            rangeBtns.forEach(b => b.classList.remove('active'));
            btn.classList.add('active');
            chartRangeDays = parseInt(btn.dataset.days);
            renderEquityChart();
        });
    });

    await fetchStatus();
    await fetchLogs();
    
    // Poll every 5 seconds for status
    setInterval(fetchStatus, 5000);
    
    // Poll every 10 seconds for logs
    setInterval(fetchLogs, 10000);
}

init();

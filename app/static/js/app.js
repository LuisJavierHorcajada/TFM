
(() => {
    'use strict';
    const API = '/api';
    let compareChart = null;
    let pollingInterval = null;
    let currentRunId = null;
    async function api(path, opts = {}) {
        const url = `${API}${path}`;
        const res = await fetch(url, {
            headers: { 'Content-Type': 'application/json', ...opts.headers },
            ...opts,
        });
        if (!res.ok) {
            const err = await res.json().catch(() => ({ detail: res.statusText }));
            throw new Error(err.detail || 'API error');
        }
        return res.json();
    }
    function toast(msg, type = 'info') {
        const el = document.createElement('div');
        el.className = `toast toast-${type}`;
        el.textContent = msg;
        document.getElementById('toast-container').appendChild(el);
        setTimeout(() => { el.style.opacity = '0'; setTimeout(() => el.remove(), 300); }, 4000);
    }
    document.querySelectorAll('.nav-btn').forEach(btn => {
        btn.addEventListener('click', () => {
            document.querySelectorAll('.nav-btn').forEach(b => b.classList.remove('active'));
            document.querySelectorAll('.section').forEach(s => s.classList.remove('active'));
            btn.classList.add('active');
            document.getElementById(`section-${btn.dataset.section}`).classList.add('active');
            if (btn.dataset.section === 'history') loadHistory();
            if (btn.dataset.section === 'compare') loadCompareOptions();
        });
    });
    async function loadBenchmarks() {
        try {
            const data = await api('/benchmarks');
            renderBenchmarkCards(data.benchmarks);
        } catch (e) {
            toast('Failed to load benchmarks: ' + e.message, 'error');
        }
    }
    function renderBenchmarkCards(benchmarks) {
        const container = document.getElementById('benchmark-categories');
        container.innerHTML = benchmarks.map(bm => `
            <label class="benchmark-item" data-name="${bm.name}">
                <input type="checkbox" value="${bm.name}">
                <div class="bm-check">
                    <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="3">
                        <polyline points="20 6 9 17 4 12"/>
                    </svg>
                </div>
                <div class="bm-info">
                    <div class="bm-name">
                        ${esc(bm.display_name)}
                        <span class="bm-cat bm-cat-${bm.category}">${bm.category}</span>
                    </div>
                    <div class="bm-desc">${esc(bm.description)}</div>
                </div>
            </label>
        `).join('');
        container.querySelectorAll('.benchmark-item').forEach(item => {
            item.addEventListener('click', (e) => {
                e.preventDefault();
                const cb = item.querySelector('input[type="checkbox"]');
                cb.checked = !cb.checked;
                item.classList.toggle('selected', cb.checked);
            });
        });
    }
    document.getElementById('btn-select-all').addEventListener('click', () => {
        document.querySelectorAll('.benchmark-item').forEach(item => {
            item.querySelector('input').checked = true;
            item.classList.add('selected');
        });
    });
    function deselectAll() {
        document.querySelectorAll('.benchmark-item').forEach(item => {
            const cb = item.querySelector('input');
            if (cb) cb.checked = false;
            item.classList.remove('selected');
        });
    }
    document.getElementById('btn-deselect-all').addEventListener('click', deselectAll);
    document.getElementById('btn-run').addEventListener('click', async () => {
        const selected = [...document.querySelectorAll('.benchmark-item input:checked')].map(cb => cb.value);
        if (!selected.length) { toast('Select at least one benchmark', 'error'); return; }
        const btn = document.getElementById('btn-run');
        btn.disabled = true;
        try {
            const data = await api('/benchmarks/run', {
                method: 'POST',
                body: JSON.stringify({ benchmarks: selected }),
            });
            currentRunId = data.run_id;
            toast('Benchmark run started!', 'success');
            showStatus(data);
            startPolling(data.run_id);
        } catch (e) {
            toast('Failed to start: ' + e.message, 'error');
        } finally {
            btn.disabled = false;
        }
    });
    function showStatus(data) {
        const card = document.getElementById('status-card');
        card.classList.remove('hidden');
        document.getElementById('status-current').textContent = 'Starting...';
        document.getElementById('status-progress').textContent = `0/${data.benchmarks.length}`;
        document.getElementById('progress-bar').style.width = '0%';
        document.getElementById('run-status-badge').textContent = 'Running';
        document.getElementById('run-status-badge').className = 'badge badge-running pulse';
    }
    function startPolling(runId) {
        if (pollingInterval) clearInterval(pollingInterval);
        pollingInterval = setInterval(() => pollStatus(runId), 1500);
    }
    async function pollStatus(runId) {
        try {
            const status = await api(`/benchmarks/status/${runId}`);
            document.getElementById('status-current').textContent = status.current_benchmark || 'Processing...';
            document.getElementById('status-progress').textContent = status.progress || '';
            if (status.progress) {
                const [done, total] = status.progress.split('/').map(Number);
                document.getElementById('progress-bar').style.width = `${(done / total) * 100}%`;
            }
            if (status.status === 'completed') {
                clearInterval(pollingInterval);
                pollingInterval = null;
                document.getElementById('run-status-badge').textContent = 'Completed';
                document.getElementById('run-status-badge').className = 'badge badge-success';
                document.getElementById('status-current').textContent = 'All benchmarks finished!';
                document.getElementById('progress-bar').style.width = '100%';
                toast('Benchmark run completed!', 'success');
                loadLatestResult(runId);
                deselectAll();
            } else if (status.status === 'failed') {
                clearInterval(pollingInterval);
                pollingInterval = null;
                document.getElementById('run-status-badge').textContent = 'Failed';
                document.getElementById('run-status-badge').className = 'badge badge-failed';
                toast('Benchmark run failed: ' + (status.error || ''), 'error');
                deselectAll();
            }
        } catch (e) {
        }
    }
    async function loadLatestResult(runId) {
        try {
            const result = await api(`/results/${runId}`);
            renderResultOverview(result);
            if (result.system_info) renderSystemInfo(result.system_info);
        } catch (e) {
            toast('Failed to load results', 'error');
        }
    }
    function renderSystemInfo(info) {
        const cleanRelease = (info.os_version || '').replace(info.hostname, '').trim();
        document.getElementById('sys-platform').textContent = `${info.os} ${cleanRelease}`;
        document.getElementById('sys-cpu').textContent = info.cpu_model || '—';
        document.getElementById('sys-cores').textContent = info.cpu_count;
        document.getElementById('sys-ram').textContent = `${info.ram_total_gb} GB`;
    }
    function renderResultOverview(result) {
        const container = document.getElementById('results-overview');
        const results = result.results;
        if (!Object.keys(results).length) { container.innerHTML = '<p class="empty-state">No metrics to display.</p>'; return; }
        const whitelist = {
            'cpu_benchmark': { 'scores.single_core': 'Single-Core Score', 'scores.multi_core': 'Multi-Core Score' },
            'memory_benchmark': { 'sequential_bandwidth.bandwidth_mb_s': 'Bandwidth MB/s', 'random_access.latency_ns': 'Latency (ns)' },
            'disk_benchmark': { 'sequential_read.speed_mb_s': 'Seq Read MB/s', 'sequential_write.speed_mb_s': 'Seq Write MB/s' },
            'network_benchmark': { 'ping.avg_ms': 'Ping (ms)', 'speedtest.download_mbps': 'Download Mbps' }
        };
        let html = '';
        for (const [bmName, bmResult] of Object.entries(results)) {
            const config = whitelist[bmName];
            if (!config) continue;
            const displayName = prettifyLabel(bmName);
            const category = bmName.split('_')[0];
            const flat = flattenObj(bmResult);
            const metrics = Object.entries(config).map(([path, label]) => {
                const val = flat[path];
                if (val === undefined) return '';
                return `
                    <div class="result-metric">
                        <div class="metric-value">${formatVal(val)}</div>
                        <div class="metric-label">${prettifyLabel(label)}</div>
                    </div>
                `;
            }).join('');
            if (!metrics.trim()) continue;
            html += `
                <div class="result-group">
                    <h3 class="group-header" style="border-left: 4px solid var(--cat-${category})">${displayName}</h3>
                    <div class="results-grid">${metrics}</div>
                </div>
            `;
        }
        container.innerHTML = html || '<p class="empty-state">No key metrics found.</p>';
    }
    let historyPage = 1;
    async function loadHistory(page = 1) {
        historyPage = page;
        try {
            const data = await api(`/results?page=${page}&per_page=10&status=completed`);
            renderHistory(data);
        } catch (e) {
            toast('Failed to load history', 'error');
        }
    }
    function renderHistory(data) {
        const container = document.getElementById('history-list');
        if (!data.results.length) { container.innerHTML = '<p class="empty-state">No benchmark runs found.</p>'; return; }
        container.innerHTML = data.results.map(r => `
            <div class="history-item" data-run-id="${r.run_id}">
                <div class="history-left">
                    <div class="history-time">${new Date(r.timestamp).toLocaleString()}</div>
                    <div class="history-benchmarks">
                        ${(r.benchmarks_requested || []).map(b => {
                            const cat = b.replace('_benchmark', '');
                            return `<span class="bm-cat bm-cat-${cat}">${prettifyLabel(cat)}</span>`;
                        }).join('')}
                    </div>
                </div>
                <div class="history-right">
                    <span class="history-duration">${r.duration_s ? r.duration_s.toFixed(1) + 's' : '—'}</span>
                    <span class="badge badge-${r.status === 'completed' ? 'success' : 'failed'}">${r.status}</span>
                    <button class="btn btn-ghost btn-sm btn-view" data-run-id="${r.run_id}">View</button>
                    <button class="btn btn-danger btn-sm btn-delete" data-run-id="${r.run_id}">✕</button>
                </div>
            </div>
        `).join('');
        container.querySelectorAll('.btn-view').forEach(btn => btn.addEventListener('click', () => showDetail(btn.dataset.runId)));
        container.querySelectorAll('.btn-delete').forEach(btn => btn.addEventListener('click', () => deleteResult(btn.dataset.runId)));
        renderPagination(data);
    }
    function renderPagination(data) {
        const container = document.getElementById('pagination');
        if (data.pages <= 1) { container.innerHTML = ''; return; }
        let html = '';
        for (let i = 1; i <= data.pages; i++) {
            html += `<button class="page-btn ${i === data.page ? 'active' : ''}" data-page="${i}">${i}</button>`;
        }
        container.innerHTML = html;
        container.querySelectorAll('.page-btn').forEach(btn => btn.addEventListener('click', () => loadHistory(parseInt(btn.dataset.page))));
    }
    async function deleteResult(runId) {
        try {
            await api(`/results/${runId}`, { method: 'DELETE' });
            toast('Result deleted', 'success');
            loadHistory(historyPage);
        } catch (e) { toast('Delete failed: ' + e.message, 'error'); }
    }
    async function showDetail(runId) {
        try {
            const result = await api(`/results/${runId}`);
            renderModal(result);
        } catch (e) { toast('Failed to load details', 'error'); }
    }
    function renderModal(result) {
        const overlay = document.createElement('div');
        overlay.className = 'modal-overlay';
        overlay.addEventListener('click', (e) => { if (e.target === overlay) overlay.remove(); });
        let sectionsHtml = '';
        for (const [bmName, bmResult] of Object.entries(result.results)) {
            const displayName = prettifyLabel(bmName);
            sectionsHtml += `<div class="detail-section"><h4>${esc(displayName)}</h4>`;
            if (bmResult.error) {
                sectionsHtml += `<p style="color:var(--danger)">${esc(bmResult.error)}</p>`;
            } else {
                sectionsHtml += renderDetailGrid(bmResult);
            }
            sectionsHtml += '</div>';
        }
        let sysHtml = '';
        if (result.system_info) {
            const si = result.system_info;
            sysHtml = `<div class="detail-section"><h4>System Info</h4><div class="detail-grid">
                <div class="detail-item"><div class="detail-key">OS</div><div class="detail-val">${esc(si.os)} ${esc(si.os_version)}</div></div>
                <div class="detail-item"><div class="detail-key">CPU</div><div class="detail-val">${esc(si.cpu_model)}</div></div>
                <div class="detail-item"><div class="detail-key">Cores</div><div class="detail-val">${si.cpu_count}</div></div>
                <div class="detail-item"><div class="detail-key">RAM</div><div class="detail-val">${si.ram_total_gb} GB</div></div>
                <div class="detail-item"><div class="detail-key">Python</div><div class="detail-val">${esc(si.python_version)}</div></div>
            </div></div>`;
        }
        overlay.innerHTML = `<div class="modal">
            <div class="modal-header">
                <h3>Benchmark Result — ${new Date(result.timestamp).toLocaleString()}</h3>
                <button class="modal-close" onclick="this.closest('.modal-overlay').remove()">✕</button>
            </div>
            ${sysHtml}
            ${sectionsHtml}
            <p style="color:var(--text-muted);font-size:0.8rem;margin-top:1rem">Total duration: ${result.duration_s?.toFixed(2) || '—'}s</p>
        </div>`;
        document.body.appendChild(overlay);
    }
    function renderDetailGrid(obj, prefix = '') {
        let html = '<div class="detail-grid">';
        for (const [key, val] of Object.entries(obj)) {
            if (val && typeof val === 'object' && !Array.isArray(val)) {
                html += `</div><h4 style="font-size:0.75rem;margin:0.75rem 0 0.5rem;color:var(--text-secondary)">${esc(key)}</h4>`;
                html += renderDetailGrid(val);
                html += '<div class="detail-grid">';
            } else {
                html += `<div class="detail-item"><div class="detail-key">${esc(prettifyLabel(key))}</div><div class="detail-val">${formatVal(val)}</div></div>`;
            }
        }
        html += '</div>';
        return html;
    }
    async function loadCompareOptions() {
        try {
            const data = await api('/results?per_page=50&status=completed');
            const selA = document.getElementById('compare-select-a');
            const selB = document.getElementById('compare-select-b');
            const opts = data.results.map(r =>
                `<option value="${r.run_id}">${new Date(r.timestamp).toLocaleString()}</option>`
            ).join('');
            selA.innerHTML = '<option value="">Select a run...</option>' + opts;
            selB.innerHTML = '<option value="">Select a run...</option>' + opts;
        } catch (e) { toast('Failed to load runs for compare', 'error'); }
    }
    document.getElementById('btn-compare').addEventListener('click', async () => {
        const a = document.getElementById('compare-select-a').value;
        const b = document.getElementById('compare-select-b').value;
        if (!a || !b) { toast('Select two runs to compare', 'error'); return; }
        if (a === b) { toast('Select two different runs', 'error'); return; }
        try {
            const data = await api(`/results/compare?run_id_a=${a}&run_id_b=${b}`, { method: 'POST' });
            renderCompare(data);
        } catch (e) { toast('Compare failed: ' + e.message, 'error'); }
    });
    function renderCompare(data) {
        const container = document.getElementById('compare-results');
        const rA = data.run_a.results;
        const rB = data.run_b.results;
        const allKeys = new Set([...Object.keys(rA), ...Object.keys(rB)]);
        let rows = '';
        const chartLabels = [];
        const chartA = [];
        const chartB = [];
        for (const bmName of allKeys) {
            const resA = rA[bmName];
            const resB = rB[bmName];
            const flat_a = resA ? flattenObj(resA) : {};
            const flat_b = resB ? flattenObj(resB) : {};
            const metricKeys = new Set([...Object.keys(flat_a), ...Object.keys(flat_b)]);
            for (const mk of metricKeys) {
                if (mk.includes('error') || mk.includes('traceback')) continue;
                const va = flat_a[mk];
                const vb = flat_b[mk];
                if (typeof va !== 'number' && typeof vb !== 'number') continue;
                const label = prettifyLabel(`${bmName.replace('_benchmark', '')} / ${mk}`);
                let clsA = '', clsB = '';
                if (typeof va === 'number' && typeof vb === 'number') {
                    const higher = mk.includes('latency') || mk.includes('time') ? false : true;
                    if (higher ? va > vb : va < vb) { clsA = 'val-better'; clsB = 'val-worse'; }
                    else if (va !== vb) { clsA = 'val-worse'; clsB = 'val-better'; }
                    chartLabels.push(label.length > 30 ? mk : label);
                    chartA.push(va);
                    chartB.push(vb);
                }
                rows += `<tr><td>${esc(label)}</td><td class="${clsA}">${formatVal(va)}</td><td class="${clsB}">${formatVal(vb)}</td></tr>`;
            }
        }
        container.innerHTML = `<table class="compare-table">
            <thead><tr><th>Metric</th><th>Run A</th><th>Run B</th></tr></thead>
            <tbody>${rows || '<tr><td colspan="3" class="empty-state">No comparable metrics found</td></tr>'}</tbody>
        </table>`;
        /* renderCompareChart(chartLabels.slice(0, 10), chartA.slice(0, 10), chartB.slice(0, 10)); */
    }
    
    /* 
    function renderCompareChart(labels, dataA, dataB) {
        const ctx = document.getElementById('compare-chart');
        if (compareChart) compareChart.destroy();
        if (!labels.length) return;
        compareChart = new Chart(ctx, {
            type: 'bar',
            data: {
                labels,
                datasets: [
                    { label: 'Run A', data: dataA, backgroundColor: 'rgba(99,102,241,0.6)', borderColor: '#6366f1', borderWidth: 1 },
                    { label: 'Run B', data: dataB, backgroundColor: 'rgba(244,114,182,0.6)', borderColor: '#f472b6', borderWidth: 1 },
                ],
            },
            options: {
                responsive: true,
                indexAxis: 'y',
                scales: {
                    x: { grid: { color: 'rgba(99,102,241,0.08)' }, ticks: { color: '#94a3b8', font: { family: 'Inter' } } },
                    y: { grid: { display: false }, ticks: { color: '#94a3b8', font: { family: 'Inter', size: 10 } } },
                },
                plugins: { legend: { labels: { color: '#94a3b8', font: { family: 'Inter' } } } },
            }
        });
    }
    */
    function prettifyLabel(label) {
        if (!label) return '';
        let s = String(label).replace(/_/g, ' ');
        s = s.replace(/\bmb s\b/gi, 'MB/s');
        s = s.replace(/\bmbps\b/gi, 'Mbps');
        s = s.replace(/\bgb\b/gi, 'GB');
        s = s.replace(/\bms\b/gi, 'ms');
        if (s.toLowerCase().endsWith(' s') && !s.toLowerCase().endsWith('mb s')) {
            s = s.replace(/ s$/i, ' In Seconds');
        }
        s = s.replace(/ benchmark/gi, '');
        return s.split(' ').map(w => {
            const low = w.toLowerCase();
            if (['in', 'ms', 'mb/s', 'mbps', 'gb'].includes(low)) return w;
            return w.charAt(0).toUpperCase() + w.slice(1);
        }).join(' ');
    }
    function esc(str) {
        const d = document.createElement('div');
        d.textContent = String(str ?? '');
        return d.innerHTML;
    }
    function formatVal(v) {
        if (v === undefined || v === null) return '—';
        const num = parseFloat(v);
        if (!isNaN(num) && isFinite(num) && typeof v !== 'boolean' && String(v).trim() !== '') {
            return new Intl.NumberFormat('en-US', {
                minimumFractionDigits: 0,
                maximumFractionDigits: 4,
                useGrouping: true
            }).format(num);
        }
        if (typeof v === 'boolean') return v ? 'Yes' : 'No';
        return esc(String(v));
    }
    function flattenObj(obj, prefix = '', out = {}) {
        for (const [k, v] of Object.entries(obj)) {
            const key = prefix ? `${prefix}.${k}` : k;
            if (v && typeof v === 'object' && !Array.isArray(v)) flattenObj(v, key, out);
            else out[key] = v;
        }
        return out;
    }
    loadBenchmarks();
    (async () => {
        try {
            const data = await api('/results?page=1&per_page=1&status=completed');
            if (data.results.length) {
                const latest = data.results[0];
                loadLatestResult(latest.run_id);
            }
        } catch (e) {  }
    })();
})();

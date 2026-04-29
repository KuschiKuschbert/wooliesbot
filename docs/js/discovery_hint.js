/* Discovery hint bar — shown once per weekly report when new variant candidates exist. */
const _DISCOVERY_SEEN_KEY = 'discoveryReportSeen';
let _discoveryReportId = null;

function _dismissDiscoveryHint() {
    const bar = document.getElementById('discovery-hint-bar');
    if (bar) bar.hidden = true;
}

function _saveDiscoverySeen() {
    try { localStorage.setItem(_DISCOVERY_SEEN_KEY, _discoveryReportId || ''); } catch (_) {}
}

function _showDiscoveryHint(count, groupNames) {
    const bar = document.getElementById('discovery-hint-bar');
    if (!bar) return;
    const label = groupNames.slice(0, 3).map(g => g.replace(/_/g, '\u00a0')).join(', ');
    const more = groupNames.length > 3 ? ` +${groupNames.length - 3}` : '';
    bar.innerHTML = `
        <span class="discovery-hint-icon">🔍</span>
        <span class="discovery-hint-text">
            <strong>${count} new size variant${count === 1 ? '' : 's'}</strong> found across ${label}${more}
            — review in Compare modal
        </span>
        <button type="button" class="discovery-hint-dismiss"
            onclick="_saveDiscoverySeen();_dismissDiscoveryHint();"
            aria-label="Dismiss">✕</button>
    `;
    bar.hidden = false;
}

async function checkDiscoveryReport() {
    try {
        const base = document.querySelector('base')?.href || location.href;
        const url = new URL('discovery_report.json', base).href;
        const res = await fetch(url, { cache: 'no-store' });
        if (!res.ok) return;
        const report = await res.json();
        const id = report.report_id || report.generated_at || '';
        const total = Number(report.total_new_candidates || 0);
        _discoveryReportId = id;
        if (!total || !id) return;
        let seen = '';
        try { seen = localStorage.getItem(_DISCOVERY_SEEN_KEY) || ''; } catch (_) {}
        if (seen === id) return;
        const groups = Object.entries(report.groups || {})
            .filter(([, g]) => (g.new_candidates || []).length > 0)
            .map(([k]) => k);
        _showDiscoveryHint(total, groups);
    } catch (_) {
        // Silently skip — no report yet (new deploys / local dev)
    }
}

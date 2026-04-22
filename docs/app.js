document.addEventListener('DOMContentLoaded', () => {
    feather.replace();
    registerSW();
    ensureShoppingDeviceId();
    setupShoppingListSessionSync();
    showSkeletons();
    setupOverlayEscapeHandler();
    setupAnalyticsMobileBehaviors();
    setupMobileChromeCompaction();
    initDashboard().then(() => hideSkeletons());
    setupPullToRefresh();
    setupBottomSheetDrag();
    // #region agent log
    fetch('http://127.0.0.1:7716/ingest/1692efee-81d9-413c-bd30-574d3de06991',{method:'POST',headers:{'Content-Type':'application/json','X-Debug-Session-Id':'0b485d'},body:JSON.stringify({sessionId:'0b485d',runId:'initial',hypothesisId:'H1',location:'docs/app.js:1',message:'dom ready viewport + breakpoint snapshot',data:{innerWidth:window.innerWidth,innerHeight:window.innerHeight,isMobile:isMobileViewport(),isCompact:isCompactViewport(),activeTab:document.body?.dataset?.activeTab||'unknown'},timestamp:Date.now()})}).catch(()=>{});
    // #endregion
    // #region agent log
    fetch('http://127.0.0.1:7716/ingest/1692efee-81d9-413c-bd30-574d3de06991',{method:'POST',headers:{'Content-Type':'application/json','X-Debug-Session-Id':'0b485d'},body:JSON.stringify({sessionId:'0b485d',runId:'initial',hypothesisId:'H5',location:'docs/app.js:1',message:'visual token intensity snapshot',data:{primary:getComputedStyle(document.documentElement).getPropertyValue('--primary').trim(),accentPurple:getComputedStyle(document.documentElement).getPropertyValue('--accent-purple').trim(),bgImage:(getComputedStyle(document.body).backgroundImage||'').slice(0,220)},timestamp:Date.now()})}).catch(()=>{});
    // #endregion
});

// ── PWA Service Worker ────────────────────────────────────────────────────────
function registerSW() {
    if ('serviceWorker' in navigator) {
        navigator.serviceWorker.register('./sw.js').catch(() => {});
    }
}

function ensureShoppingDeviceId() {
    const key = 'shoppingDeviceId';
    let id = '';
    try {
        id = localStorage.getItem(key) || '';
    } catch {}
    if (!id) {
        try {
            if (typeof crypto !== 'undefined' && typeof crypto.randomUUID === 'function') {
                id = `wb_${crypto.randomUUID()}`;
            } else {
                id = `wb_${Date.now().toString(36)}_${Math.random().toString(36).slice(2, 10)}`;
            }
            localStorage.setItem(key, id);
        } catch {}
    }
    // #region agent log
    fetch('http://127.0.0.1:7716/ingest/1692efee-81d9-413c-bd30-574d3de06991',{method:'POST',headers:{'Content-Type':'application/json','X-Debug-Session-Id':'0b485d'},body:JSON.stringify({sessionId:'0b485d',runId:'post-fix',hypothesisId:'H9',location:'docs/app.js:ensureShoppingDeviceId',message:'shopping device id ensured',data:{hasId:Boolean(id),idPrefix:id?String(id).slice(0,3):''},timestamp:Date.now()})}).catch(()=>{});
    // #endregion
}

// ── Haptics helper ────────────────────────────────────────────────────────────
function haptic(ms = 10) {
    try { navigator.vibrate?.(ms); } catch {}
}

function prefersReducedMotion() {
    try {
        return typeof matchMedia !== 'undefined' && matchMedia('(prefers-reduced-motion: reduce)').matches;
    } catch {
        return false;
    }
}

function isMobileViewport() {
    try {
        return typeof matchMedia !== 'undefined' && matchMedia('(max-width: 768px)').matches;
    } catch {
        return false;
    }
}

function isCompactViewport() {
    try {
        return typeof matchMedia !== 'undefined' && matchMedia('(max-width: 430px)').matches;
    } catch {
        return false;
    }
}

let _focusBeforeDrawer = null;
let _focusBeforeSettings = null;
let _focusBeforeStockModal = null;
let _debugChromeSnapshotLogged = false;

function syncTabAriaCurrent(target) {
    document.querySelectorAll('.nav-link[data-tab], .mobile-nav-link[data-tab]').forEach(el => {
        if (el.dataset.tab === target) el.setAttribute('aria-current', 'page');
        else el.removeAttribute('aria-current');
    });
}

let _priceDropToastTimer = null;

function dismissPriceDropToast() {
    document.getElementById('price-drop-toast')?.remove();
    if (_priceDropToastTimer) {
        clearTimeout(_priceDropToastTimer);
        _priceDropToastTimer = null;
    }
}

let _uiToastTimer = null;

function showUiToast(message, duration = 3400) {
    document.getElementById('ui-toast')?.remove();
    if (_uiToastTimer) {
        clearTimeout(_uiToastTimer);
        _uiToastTimer = null;
    }
    const t = document.createElement('div');
    t.id = 'ui-toast';
    t.className = 'ui-toast';
    t.setAttribute('role', 'status');
    t.textContent = message;
    document.body.appendChild(t);
    requestAnimationFrame(() => t.classList.add('ui-toast-visible'));
    _uiToastTimer = setTimeout(() => {
        t.classList.remove('ui-toast-visible');
        setTimeout(() => t.remove(), 320);
    }, duration);
}

/** Deals hero pill — mirrors header “last updated” when available */
function syncDealsHeroStatus() {
    const live = document.getElementById('deals-hero-live');
    if (!live) return;
    const src = document.getElementById('last-updated');
    const t = src && src.textContent ? src.textContent.trim() : '';
    if (t && t !== 'Checking...') {
        live.textContent = `Snapshot · ${t}`;
        return;
    }
    if (_data && _data.length) {
        live.textContent = `${_data.length} products tracked`;
        return;
    }
    live.textContent = 'Waiting for data…';
}

function setInsightsChartEmpty(canvasId, isEmpty, title, hint) {
    const canvas = document.getElementById(canvasId);
    if (!canvas) return;
    const wrap = canvas.closest('.chart-container');
    if (!wrap) return;
    wrap.classList.toggle('has-empty-overlay', Boolean(isEmpty));
    let el = wrap.querySelector('.insights-chart-empty');
    if (!isEmpty) {
        el?.remove();
        return;
    }
    if (!el) {
        el = document.createElement('div');
        el.className = 'insights-chart-empty';
        wrap.appendChild(el);
    }
    el.innerHTML = `<p class="insights-empty-title">${escapeHtml(title)}</p><p class="insights-empty-hint">${escapeHtml(hint)}</p>`;
}

// ── Skeleton loaders ──────────────────────────────────────────────────────────
function showSkeletons() {
    const grid = document.getElementById('specials-grid');
    if (!grid) return;
    const count = window.innerWidth <= 480 ? 4 : 6;
    grid.innerHTML = Array.from({ length: count }, () => `
        <div class="skeleton-card">
            <div class="skeleton skeleton-img"></div>
            <div class="skeleton skeleton-line med" style="margin-top:12px;"></div>
            <div class="skeleton skeleton-line short"></div>
            <div class="skeleton skeleton-btn"></div>
        </div>
    `).join('');
}

function hideSkeletons() {
    // renderDashboard() will overwrite specials-grid; nothing extra needed
}

// ── Pull-to-refresh ───────────────────────────────────────────────────────────
function setupPullToRefresh() {
    const indicator = document.getElementById('ptr-indicator');
    if (!indicator) return;

    let startY = 0;
    let pulling = false;
    let triggered = false;
    const THRESHOLD = 70;

    document.addEventListener('touchstart', (e) => {
        if (window.scrollY > 10) return;
        startY = e.touches[0].clientY;
        pulling = true;
        triggered = false;
    }, { passive: true });

    document.addEventListener('touchmove', (e) => {
        if (!pulling) return;
        const dy = e.touches[0].clientY - startY;
        if (dy > 10 && window.scrollY <= 0) {
            indicator.classList.add('ptr-visible');
            if (dy > THRESHOLD && !triggered) {
                triggered = true;
                haptic(20);
            }
        } else {
            indicator.classList.remove('ptr-visible');
        }
    }, { passive: true });

    document.addEventListener('touchend', async () => {
        if (!pulling) return;
        pulling = false;
        if (triggered) {
            try {
                const ok = await initDashboard();
                if (ok) showUiToast('Prices updated');
            } finally {
                indicator.classList.remove('ptr-visible');
            }
        } else {
            indicator.classList.remove('ptr-visible');
        }
    });
}

// ── Bottom-sheet drag-to-dismiss ──────────────────────────────────────────────
function setupBottomSheetDrag() {
    const drawer = document.getElementById('list-drawer');
    const grabber = document.getElementById('drawer-grabber');
    if (!drawer || !grabber) return;

    let startY = 0;
    let currentY = 0;
    let isDragging = false;

    const onStart = (y) => {
        startY = y;
        currentY = 0;
        isDragging = true;
        drawer.style.transition = 'none';
    };

    const onMove = (y) => {
        if (!isDragging) return;
        currentY = Math.max(0, y - startY);
        drawer.style.transform = `translateY(${currentY}px)`;
    };

    const onEnd = () => {
        if (!isDragging) return;
        isDragging = false;
        drawer.style.transition = '';
        if (currentY > 140) {
            toggleDrawer();
        } else {
            drawer.style.transform = '';
        }
    };

    grabber.addEventListener('touchstart', (e) => onStart(e.touches[0].clientY), { passive: true });
    grabber.addEventListener('touchmove', (e) => onMove(e.touches[0].clientY), { passive: true });
    grabber.addEventListener('touchend', onEnd);
    grabber.addEventListener('mousedown', (e) => onStart(e.clientY));
    document.addEventListener('mousemove', (e) => { if (isDragging) onMove(e.clientY); });
    document.addEventListener('mouseup', onEnd);
}

let _data = [];
let _history = {};
let _volatility = {}; // item -> score
let _lastChecked = null;
let _currentFilter = 'all';
let _currentCatFilter = 'all';
let _searchText = '';
let _currentTab = 'deals';
let _shopMode = localStorage.getItem('shopMode') || 'weekly';
let _shoppingList = normalizeShoppingListShape(loadShoppingListFromStorage());
let _shoppingTripMode = localStorage.getItem('shoppingTripMode') === '1';
let _shoppingTripStartedAt = localStorage.getItem('shoppingTripStartedAt') || '';
let _shoppingTripStartCount = Number.parseInt(localStorage.getItem('shoppingTripStartCount') || '', 10);
if (!Number.isFinite(_shoppingTripStartCount) || _shoppingTripStartCount < 0) _shoppingTripStartCount = null;
let _shoppingTripSavedPeak = Number.parseFloat(localStorage.getItem('shoppingTripSavedPeak') || '0');
if (!Number.isFinite(_shoppingTripSavedPeak) || _shoppingTripSavedPeak < 0) _shoppingTripSavedPeak = 0;
let _shoppingTripTimeoutAt = Number.parseInt(localStorage.getItem('shoppingTripTimeoutAt') || '', 10);
if (!Number.isFinite(_shoppingTripTimeoutAt) || _shoppingTripTimeoutAt <= 0) _shoppingTripTimeoutAt = 0;
let _selectedItemForModal = null;
let _currentPage = 1;
const _itemsPerPage = 12;
let _currentSort = 'discount';

function getRuntimeWriteConfig() {
    const cfg = (typeof window !== 'undefined' && window.__WOOLIESBOT_ENV__) ? window.__WOOLIESBOT_ENV__ : {};
    const url = typeof cfg.writeApiUrl === 'string' ? cfg.writeApiUrl.trim() : '';
    const secret = typeof cfg.writeApiSecret === 'string' ? cfg.writeApiSecret : '';
    return { url, secret };
}

const _runtimeWriteConfig = getRuntimeWriteConfig();
/** Cloudflare Worker base URL for POST /update_stock. */
let _writeApiUrl = localStorage.getItem('write_api_url') || _runtimeWriteConfig.url || '';
/** Shared secret header for the Worker — stored only in localStorage in the browser. */
let _writeApiSecret = localStorage.getItem('write_api_secret') || _runtimeWriteConfig.secret || '';
if (_writeApiUrl && !localStorage.getItem('write_api_url')) localStorage.setItem('write_api_url', _writeApiUrl);
if (_writeApiSecret && !localStorage.getItem('write_api_secret')) localStorage.setItem('write_api_secret', _writeApiSecret);
let _nextRun = null;
const MONTHLY_BUDGET = 800;
const SHOPPING_LIST_SYNC_STAMP_KEY = 'shoppingListCloudUpdatedAt';
const SHOPPING_LIST_SYNC_POLL_MS = 25000;
const SHOPPING_LIST_SYNC_PUSH_DEBOUNCE_MS = 900;
let _shoppingListCloudUpdatedAt = localStorage.getItem(SHOPPING_LIST_SYNC_STAMP_KEY) || '';
let _shoppingListSyncPollTimer = null;
let _shoppingListSyncPushTimer = null;
let _shoppingListSyncPushInFlight = false;
let _shoppingListSyncPullInFlight = false;

/** Mirrors chef_os._PRICE_UNRELIABLE — eff_price at or above is not comparable */
const PRICE_UNRELIABLE = 99999;
const SHOPPING_TRIP_SESSIONS_KEY = 'shoppingTripSessions';
const SHOPPING_TRIP_SESSIONS_MAX = 200;
const SHOPPING_TRIP_TIMEOUT_MS = 2 * 60 * 60 * 1000; // 2h idle timeout failsafe
const SHOPPING_TRIP_TIMEOUT_REMINDER_KEY = 'shoppingTripTimeoutReminderPending';
let _shoppingTripMilestonesShown = new Set();
let _shoppingTripBeatLastToastShown = false;
let _analyticsResizeTimer = null;
let _analyticsViewportMode = '';
let _analyticsOrientation = '';

/** Populated by rebuildCompareGroupMeta() after each data load */
let _compareGroupMeta = new Map();

/** Inventory row count per compare_group (for UI entry points) */
let _compareGroupMemberCounts = new Map();

/** Structured issues: mixed_price_mode, single_candidate */
let _compareGroupIssues = [];

function isReliableEffPrice(ep) {
    return typeof ep === 'number' && Number.isFinite(ep) && ep > 0 && ep < PRICE_UNRELIABLE;
}

function loadShoppingListFromStorage() {
    try {
        const parsed = JSON.parse(localStorage.getItem('shoppingList') || '[]');
        return Array.isArray(parsed) ? parsed : [];
    } catch {
        return [];
    }
}

function normalizeShoppingListShape(rows) {
    if (!Array.isArray(rows)) return [];
    return rows.map(row => {
        if (!row || typeof row !== 'object') return { name: '', qty: 1, picked: false };
        const out = { ...row, picked: Boolean(row.picked) };
        const t = Date.parse(String(out.updated_at || ''));
        out.updated_at = Number.isFinite(t) ? new Date(t).toISOString() : new Date().toISOString();
        return out;
    });
}

function persistShoppingList(opts = {}) {
    const skipCloud = Boolean(opts?.skipCloud);
    localStorage.setItem('shoppingList', JSON.stringify(_shoppingList));
    if (!skipCloud) scheduleShoppingListCloudPush('local_edit');
}

function setupShoppingListSessionSync() {
    window.addEventListener('storage', (event) => {
        if (event.key !== 'shoppingList') return;
        const incoming = normalizeShoppingListShape(loadShoppingListFromStorage());
        const currSig = JSON.stringify(_shoppingList);
        const nextSig = JSON.stringify(incoming);
        if (currSig === nextSig) return;
        _shoppingList = incoming;
        updateListCount();
        renderShoppingList();
    });
}

function touchShoppingListRow(row, atIso = '') {
    if (!row || typeof row !== 'object') return;
    row.updated_at = atIso || new Date().toISOString();
}

function loadShoppingTripSessions() {
    try {
        const parsed = JSON.parse(localStorage.getItem(SHOPPING_TRIP_SESSIONS_KEY) || '[]');
        return Array.isArray(parsed) ? parsed : [];
    } catch {
        return [];
    }
}

function persistShoppingTripSessions(sessions) {
    localStorage.setItem(SHOPPING_TRIP_SESSIONS_KEY, JSON.stringify(sessions));
}

function computePickedSavingsAmount(rows = _shoppingList) {
    return (rows || []).reduce((sum, i) => {
        if (!i?.picked) return sum;
        const nowP = Number(i.price || 0);
        const wasP = Number(i.was_price || 0);
        const eachSave = Math.max(0, wasP - nowP);
        return sum + (eachSave * Number(i.qty || 1));
    }, 0);
}

function refreshShoppingTripSavedPeak() {
    if (!_shoppingTripMode) return;
    const current = computePickedSavingsAmount();
    if (current > _shoppingTripSavedPeak) {
        _shoppingTripSavedPeak = current;
        localStorage.setItem('shoppingTripSavedPeak', _shoppingTripSavedPeak.toFixed(2));
    }
}

function getLastShoppingTripSavedAmount() {
    const sessions = loadShoppingTripSessions();
    if (!Array.isArray(sessions) || sessions.length === 0) return 0;
    const last = sessions[sessions.length - 1] || {};
    const val = Number(last.saved_amount || 0);
    return Number.isFinite(val) && val > 0 ? val : 0;
}

function getAverageShoppingTripDuration() {
    const sessions = loadShoppingTripSessions().filter((s) => {
        const dur = Number(s?.duration_seconds || 0);
        return Number.isFinite(dur) && dur > 0;
    });
    if (!sessions.length) return null;
    const totalSeconds = sessions.reduce((sum, s) => sum + Number(s.duration_seconds || 0), 0);
    return {
        averageSeconds: totalSeconds / sessions.length,
        sessionCount: sessions.length,
    };
}

function formatDurationShort(seconds) {
    if (!Number.isFinite(seconds) || seconds <= 0) return '0m';
    const totalMinutes = Math.max(1, Math.round(seconds / 60));
    const hours = Math.floor(totalMinutes / 60);
    const mins = totalMinutes % 60;
    if (hours <= 0) return `${totalMinutes}m`;
    if (mins === 0) return `${hours}h`;
    return `${hours}h ${mins}m`;
}

function markShoppingTripActivity() {
    if (!_shoppingTripMode) return;
    _shoppingTripTimeoutAt = Date.now() + SHOPPING_TRIP_TIMEOUT_MS;
    localStorage.setItem('shoppingTripTimeoutAt', String(_shoppingTripTimeoutAt));
}

function beginShoppingTripSession() {
    if (_shoppingTripStartedAt) return;
    _shoppingTripStartedAt = new Date().toISOString();
    _shoppingTripStartCount = _shoppingList.length;
    _shoppingTripSavedPeak = 0;
    _shoppingTripBeatLastToastShown = false;
    localStorage.setItem('shoppingTripStartedAt', _shoppingTripStartedAt);
    localStorage.setItem('shoppingTripStartCount', String(_shoppingTripStartCount));
    localStorage.setItem('shoppingTripSavedPeak', '0');
    markShoppingTripActivity();
}

function finalizeShoppingTripSession(reason = 'manual_end') {
    if (!_shoppingTripStartedAt) return;
    const startMs = Date.parse(_shoppingTripStartedAt);
    const endIso = new Date().toISOString();
    if (!Number.isFinite(startMs)) {
        _shoppingTripStartedAt = '';
        _shoppingTripStartCount = null;
        localStorage.removeItem('shoppingTripStartedAt');
        localStorage.removeItem('shoppingTripStartCount');
        return;
    }
    const sessions = loadShoppingTripSessions();
    const pickedCount = _shoppingList.filter(item => item?.picked).length;
    const sessionSaved = Math.max(computePickedSavingsAmount(), _shoppingTripSavedPeak);
    sessions.push({
        started_at: _shoppingTripStartedAt,
        ended_at: endIso,
        duration_seconds: Math.max(0, Math.round((Date.now() - startMs) / 1000)),
        reason,
        start_count: Number.isFinite(_shoppingTripStartCount) ? _shoppingTripStartCount : _shoppingList.length,
        end_count: _shoppingList.length,
        picked_count: pickedCount,
        saved_amount: Number(sessionSaved.toFixed(2)),
    });
    const trimmed = sessions.length > SHOPPING_TRIP_SESSIONS_MAX
        ? sessions.slice(-SHOPPING_TRIP_SESSIONS_MAX)
        : sessions;
    persistShoppingTripSessions(trimmed);
    _shoppingTripStartedAt = '';
    _shoppingTripStartCount = null;
    localStorage.removeItem('shoppingTripStartedAt');
    localStorage.removeItem('shoppingTripStartCount');
    _shoppingTripTimeoutAt = 0;
    localStorage.removeItem('shoppingTripTimeoutAt');
    _shoppingTripSavedPeak = 0;
    localStorage.removeItem('shoppingTripSavedPeak');
    _shoppingTripBeatLastToastShown = false;
}

function isShoppingTripMode() {
    return _shoppingTripMode;
}

function setShoppingTripMode(on, reason = 'manual') {
    const next = Boolean(on);
    const prev = _shoppingTripMode;
    if (next && !prev) beginShoppingTripSession();
    if (!next && prev) finalizeShoppingTripSession(reason);
    _shoppingTripMode = next;
    localStorage.setItem('shoppingTripMode', _shoppingTripMode ? '1' : '0');
    if (next) {
        _shoppingTripMilestonesShown = new Set();
        _shoppingTripBeatLastToastShown = false;
        markShoppingTripActivity();
        if (localStorage.getItem(SHOPPING_TRIP_TIMEOUT_REMINDER_KEY) === '1') {
            showUiToast('Last trip timed out. Please end your trip with Done shopping when finished.', 5200);
            localStorage.removeItem(SHOPPING_TRIP_TIMEOUT_REMINDER_KEY);
        }
    } else {
        _shoppingTripTimeoutAt = 0;
        localStorage.removeItem('shoppingTripTimeoutAt');
    }
    updateListCount();
    renderShoppingList();
}

if (_shoppingTripMode && !_shoppingTripStartedAt) beginShoppingTripSession();

function checkShoppingTripTimeout() {
    if (!_shoppingTripMode) return;
    if (!_shoppingTripTimeoutAt) markShoppingTripActivity();
    if (_shoppingTripTimeoutAt && Date.now() >= _shoppingTripTimeoutAt) {
        localStorage.setItem(SHOPPING_TRIP_TIMEOUT_REMINDER_KEY, '1');
        setShoppingTripMode(false, 'idle_timeout');
    }
}

function toggleListItemPicked(index) {
    const row = _shoppingList[index];
    if (!row) return;
    const wasPicked = Boolean(row.picked);
    row.picked = !row.picked;
    touchShoppingListRow(row);
    markShoppingTripActivity();
    refreshShoppingTripSavedPeak();
    persistShoppingList();
    if (_shoppingTripMode && !wasPicked && row.picked) {
        maybeShowShoppingTripMilestone();
    }
    renderShoppingList();
}

function maybeShowShoppingTripMilestone() {
    const totalCount = _shoppingList.length;
    if (!_shoppingTripMode || totalCount <= 0) return;
    const pickedCount = _shoppingList.reduce((sum, i) => sum + (i?.picked ? 1 : 0), 0);
    const pct = Math.round((pickedCount / totalCount) * 100);
    const saved = computePickedSavingsAmount();
    const elapsedMins = _shoppingTripStartedAt
        ? Math.max(0, Math.round((Date.now() - Date.parse(_shoppingTripStartedAt)) / 60000))
        : 0;
    const marks = [
        { pct: 25, text: `Great start - 25% done in ${elapsedMins}m.` },
        { pct: 50, text: `Halfway there - saved $${saved.toFixed(2)} so far.` },
        { pct: 75, text: `Almost done - $${saved.toFixed(2)} saved so far.` },
        { pct: 100, text: `All ticked in ${elapsedMins}m. Clear completed and tap Done shopping.` },
    ];
    const hit = marks.find(m => pct >= m.pct && !_shoppingTripMilestonesShown.has(m.pct));
    if (!hit) return;
    _shoppingTripMilestonesShown.add(hit.pct);
    showUiToast(hit.text, hit.pct === 100 ? 3400 : 2400);
}

function clearPickedListItems() {
    _shoppingList = _shoppingList.filter(row => !row?.picked);
    if (_shoppingList.length === 0 && _shoppingTripMode) setShoppingTripMode(false, 'clear_completed_empty');
    persistShoppingList();
    updateListCount();
    renderShoppingList();
}

function escapeHtml(s) {
    return String(s)
        .replace(/&/g, '&amp;')
        .replace(/</g, '&lt;')
        .replace(/>/g, '&gt;')
        .replace(/"/g, '&quot;');
}

/** Base URL for pantry/stock writes (cloud Worker only). */
function getStockWriteBase() {
    return (_writeApiUrl || '').trim().replace(/\/$/, '');
}

function usesCloudWrite() {
    return Boolean((_writeApiUrl || '').trim());
}

function getShoppingDeviceId() {
    try {
        return localStorage.getItem('shoppingDeviceId') || 'unknown';
    } catch {
        return 'unknown';
    }
}

function getShoppingListCloudStampMs() {
    const ts = Date.parse(String(_shoppingListCloudUpdatedAt || ''));
    return Number.isFinite(ts) ? ts : 0;
}

function setShoppingListCloudStamp(iso) {
    const t = Date.parse(String(iso || ''));
    if (!Number.isFinite(t)) return;
    _shoppingListCloudUpdatedAt = new Date(t).toISOString();
    localStorage.setItem(SHOPPING_LIST_SYNC_STAMP_KEY, _shoppingListCloudUpdatedAt);
}

function getShoppingListMaxUpdatedMs(rows = _shoppingList) {
    if (!Array.isArray(rows) || rows.length === 0) return 0;
    let latest = 0;
    rows.forEach((row) => {
        const t = Date.parse(String(row?.updated_at || ''));
        if (Number.isFinite(t) && t > latest) latest = t;
    });
    return latest;
}

function applyRemoteShoppingList(remoteRows, meta = {}) {
    const normalized = normalizeShoppingListShape(remoteRows);
    _shoppingList = normalized;
    persistShoppingList({ skipCloud: true });
    if (meta.updated_at) setShoppingListCloudStamp(meta.updated_at);
    updateListCount();
    renderShoppingList();
}

async function pushShoppingListToCloud(reason = 'manual') {
    if (_shoppingListSyncPushInFlight) return;
    const base = getStockWriteBase();
    const secret = (_writeApiSecret || '').trim();
    if (!base || !secret) return;
    _shoppingListSyncPushInFlight = true;
    const deviceId = getShoppingDeviceId();
    try {
        const nowIso = new Date().toISOString();
        const payload = {
            device_id: deviceId,
            reason,
            updated_at: nowIso,
            items: normalizeShoppingListShape(_shoppingList),
        };
        const res = await fetch(`${base}/shopping_list`, {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json',
                'X-WooliesBot-Secret': secret,
            },
            body: JSON.stringify(payload),
        });
        if (!res.ok) return;
        const body = await res.json().catch(() => ({}));
        if (body?.updated_at) setShoppingListCloudStamp(body.updated_at);
    } catch {}
    finally {
        _shoppingListSyncPushInFlight = false;
    }
}

function scheduleShoppingListCloudPush(reason = 'local_edit') {
    if (_shoppingListSyncPushTimer) clearTimeout(_shoppingListSyncPushTimer);
    _shoppingListSyncPushTimer = setTimeout(() => {
        _shoppingListSyncPushTimer = null;
        pushShoppingListToCloud(reason);
    }, SHOPPING_LIST_SYNC_PUSH_DEBOUNCE_MS);
}

async function pullShoppingListFromCloud(reason = 'poll') {
    if (_shoppingListSyncPullInFlight) return;
    const base = getStockWriteBase();
    const secret = (_writeApiSecret || '').trim();
    if (!base || !secret) return;
    _shoppingListSyncPullInFlight = true;
    try {
        const res = await fetch(`${base}/shopping_list`, {
            method: 'GET',
            headers: {
                'X-WooliesBot-Secret': secret,
                'X-WooliesBot-Device': getShoppingDeviceId(),
            },
        });
        if (!res.ok) return;
        const body = await res.json().catch(() => null);
        if (!body || !Array.isArray(body.items)) return;
        const remoteMs = Date.parse(String(body.updated_at || '')) || 0;
        const localCloudMs = getShoppingListCloudStampMs();
        const localRowsMs = getShoppingListMaxUpdatedMs(_shoppingList);
        if (remoteMs <= Math.max(localCloudMs, localRowsMs)) return;
        applyRemoteShoppingList(body.items, { updated_at: body.updated_at, reason });
    } catch {}
    finally {
        _shoppingListSyncPullInFlight = false;
    }
}

function startShoppingListCloudSyncMonitors() {
    if (_shoppingListSyncPollTimer) return;
    _shoppingListSyncPollTimer = setInterval(() => {
        if (typeof document !== 'undefined' && document.visibilityState === 'hidden') return;
        pullShoppingListFromCloud('interval');
    }, SHOPPING_LIST_SYNC_POLL_MS);

    window.addEventListener('online', () => pullShoppingListFromCloud('online'));
    document.addEventListener('visibilitychange', () => {
        if (document.visibilityState === 'visible') pullShoppingListFromCloud('visible');
    });
    pullShoppingListFromCloud('startup');
}

/** Matches chef_os merge keys: prefer item_id, then display name */
function itemKey(item) {
    if (!item || typeof item !== 'object') return '';
    return item.item_id || item.name || '';
}

function resolveInventoryItem(itemName, itemId) {
    const arr = _data || [];
    if (itemId) {
        const byId = arr.find(i => i.item_id === itemId);
        if (byId) return byId;
    }
    if (itemName) return arr.find(i => i.name === itemName) || null;
    return null;
}

function shoppingListDedupeMatch(row, inventoryItem) {
    if (!row || !inventoryItem) return false;
    if (inventoryItem.item_id && row.item_id === inventoryItem.item_id) return true;
    return row.name === inventoryItem.name;
}

function formatCompareEffPrice(priceMode, eff) {
    const mode = priceMode || 'each';
    if (!isReliableEffPrice(eff)) return '—';
    if (mode === 'kg') return `$${eff.toFixed(2)}/kg`;
    if (mode === 'litre') return `$${eff.toFixed(2)}/L`;
    return `$${eff.toFixed(2)}`;
}

function minEffPriceAcrossStores(item) {
    const stores = item.all_stores || {};
    let m = Infinity;
    for (const sd of Object.values(stores)) {
        if (isReliableEffPrice(sd.eff_price)) m = Math.min(m, sd.eff_price);
    }
    if (Number.isFinite(m)) return m;
    const fallback = item.eff_price ?? item.price;
    return isReliableEffPrice(fallback) ? fallback : Infinity;
}

function candidateSortTuple(c) {
    const pl = c.item.pack_litres || 0;
    const storeRank = c.store === 'woolworths' ? 0 : 1;
    const cokeBias = /coke|coca/i.test(c.item.name || '') ? 0 : 1;
    return [c.eff_price, -pl, storeRank, cokeBias, c.item.name || ''];
}

function compareCandidates(a, b) {
    const ta = candidateSortTuple(a);
    const tb = candidateSortTuple(b);
    for (let i = 0; i < ta.length; i++) {
        if (ta[i] < tb[i]) return -1;
        if (ta[i] > tb[i]) return 1;
    }
    return 0;
}

/** Expand one inventory row to store-level compare candidates. */
function expandItemStoreCandidates(item) {
    const out = [];
    const stores = item.all_stores || {};
    for (const [storeKey, sd] of Object.entries(stores)) {
        const ep = sd.eff_price;
        if (!isReliableEffPrice(ep)) continue;
        out.push({
            item,
            store: storeKey,
            eff_price: ep,
            shelf_price: sd.price,
            unit_price: sd.unit_price,
        });
    }
    return out;
}

function rebuildCompareGroupMeta() {
    _compareGroupMeta = new Map();
    _compareGroupIssues = [];
    const byGroup = new Map();
    for (const item of _data) {
        const g = item.compare_group;
        if (!g) continue;
        if (!byGroup.has(g)) byGroup.set(g, []);
        byGroup.get(g).push(item);
    }

    _compareGroupMemberCounts = new Map();
    for (const [k, members] of byGroup) {
        _compareGroupMemberCounts.set(k, members.length);
    }

    for (const [groupKey, members] of byGroup) {
        const modes = new Set(members.map(i => i.price_mode || 'each'));
        if (modes.size > 1) {
            const modeList = [...modes].sort();
            _compareGroupIssues.push({ type: 'mixed_price_mode', groupKey, modes: modeList });
            console.warn(`[compare_group] Skipping "${groupKey}": mixed price_mode (${modeList.join(', ')})`);
            continue;
        }
        const mode = members[0].price_mode || 'each';

        const candidates = [];
        for (const item of members) {
            if (item.price_unavailable) continue;
            candidates.push(...expandItemStoreCandidates(item));
        }

        if (candidates.length < 2) {
            _compareGroupIssues.push({
                type: 'single_candidate',
                groupKey,
                candidateCount: candidates.length,
            });
            continue;
        }

        candidates.sort(compareCandidates);
        const best = candidates[0];
        _compareGroupMeta.set(groupKey, {
            mode,
            winnerEff: best.eff_price,
            winnerItemName: best.item.name,
            winnerStore: best.store,
            candidateCount: candidates.length,
        });
    }
}

function renderCompareGroupDiagnostics() {
    const el = document.getElementById('compare-group-diagnostics');
    if (!el) return;

    if (!_compareGroupIssues.length) {
        el.innerHTML = `
            <p class="cg-diag-ok">
                All good — products are comparing fairly across Woolies and Coles.
            </p>`;
        return;
    }

    const items = _compareGroupIssues.map((issue) => {
        if (issue.type === 'mixed_price_mode') {
            const modes = issue.modes.map((m) => escapeHtml(m)).join(', ');
            return `<li class="cg-diag-issue">
                <strong>${escapeHtml(issue.groupKey)}</strong> — mixed units (${modes}), so “best deal” isn’t compared automatically.
                Tap <strong>Compare sizes</strong> on a product card to see each option.
            </li>`;
        }
        if (issue.type === 'single_candidate') {
            return `<li class="cg-diag-issue">
                <strong>${escapeHtml(issue.groupKey)}</strong> — we only have ${issue.candidateCount ?? 0} matching price(s) across stores so far.
                Once both Woolies and Coles have data, comparisons will show up.
            </li>`;
        }
        return '';
    }).join('');

    el.innerHTML = `<ul class="cg-diag-list">${items}</ul>`;
}

function closeCompareGroupModal() {
    document.getElementById('compare-group-modal')?.remove();
}

function openCompareGroupModal(groupKey) {
    if (!groupKey) return;
    closeCompareGroupModal();
    closeItemDeepdive();

    const members = _data.filter((i) => i.compare_group === groupKey);
    const modes = new Set(members.map((i) => i.price_mode || 'each'));
    const mixedModes = modes.size > 1;
    const modeLabel = mixedModes ? 'mixed' : (members[0]?.price_mode || 'each');

    let globalBest = Infinity;
    const rows = [...members].map((item) => {
        const me = minEffPriceAcrossStores(item);
        if (Number.isFinite(me) && isReliableEffPrice(me)) globalBest = Math.min(globalBest, me);
        return item;
    });

    rows.sort((a, b) => {
        const ma = minEffPriceAcrossStores(a);
        const mb = minEffPriceAcrossStores(b);
        if (Number.isFinite(ma) && Number.isFinite(mb)) return ma - mb;
        if (Number.isFinite(ma)) return -1;
        if (Number.isFinite(mb)) return 1;
        return (a.name || '').localeCompare(b.name || '');
    });

    const eps = 0.01;
    const footnoteText = mixedModes
        ? 'These products use different units — compare the “Best unit” column row by row.'
        : 'Prices are normalised to the same unit so different pack sizes can be compared.';

    const banner = mixedModes
        ? `<div class="cg-modal-banner cg-modal-banner-warn">
            Mixed units in this group (${[...modes].sort().join(', ')}).
            Use <strong>Compare sizes</strong> on the main screen for a fair “best deal” once units match.
           </div>`
        : '';

    const tableRows = rows.map((item) => {
        const pm = item.price_mode || 'each';
        const wsd = item.all_stores?.woolworths;
        const csd = item.all_stores?.coles;
        const wep = wsd?.eff_price;
        const cep = csd?.eff_price;
        const wStr = isReliableEffPrice(wep) ? formatCompareEffPrice(pm, wep) : '—';
        const cStr = isReliableEffPrice(cep) ? formatCompareEffPrice(pm, cep) : '—';
        const rowMin = minEffPriceAcrossStores(item);
        const bestUnit = formatCompareEffPrice(pm, Number.isFinite(rowMin) && isReliableEffPrice(rowMin) ? rowMin : NaN);
        const isWinner = Number.isFinite(globalBest) && Number.isFinite(rowMin)
            && isReliableEffPrice(rowMin) && Math.abs(rowMin - globalBest) <= eps;
        const trClass = isWinner ? 'cg-row cg-row-best' : 'cg-row';

        const wUrl = escapeHtml(getStoreUrlForStore(item, 'woolworths'));
        const cUrl = escapeHtml(getStoreUrlForStore(item, 'coles'));

        return `<tr class="${trClass}">
            <td class="cg-col-name">${escapeHtml(displayName(item.name))}</td>
            <td class="cg-col-price">
                ${wStr}
                <a class="cg-pdp-link" href="${wUrl}" target="_blank" rel="noopener" title="Open at Woolworths">↗</a>
            </td>
            <td class="cg-col-price">
                ${cStr}
                <a class="cg-pdp-link" href="${cUrl}" target="_blank" rel="noopener" title="Open at Coles">↗</a>
            </td>
            <td class="cg-col-unit">${bestUnit}</td>
        </tr>`;
    }).join('');

    const modal = document.createElement('div');
    modal.id = 'compare-group-modal';
    modal.className = 'cg-modal-overlay';
    modal.onclick = (e) => { if (e.target === modal) closeCompareGroupModal(); };

    modal.innerHTML = `
        <div class="cg-modal-panel" role="dialog" aria-labelledby="cg-modal-title">
            <div class="cg-modal-header">
                <div>
                    <h3 id="cg-modal-title" class="cg-modal-title">${escapeHtml(groupKey.replace(/_/g, ' '))}</h3>
                    <div class="cg-modal-meta">
                        <span class="cg-modal-badge">${escapeHtml(modeLabel)}</span>
                        <span class="cg-modal-count">${members.length} product size${members.length === 1 ? '' : 's'}</span>
                    </div>
                </div>
                <button type="button" class="deepdive-close" onclick="closeCompareGroupModal()" aria-label="Close">
                    <i data-feather="x"></i>
                </button>
            </div>
            ${banner}
            <div class="cg-modal-table-wrap">
                <table class="cg-modal-table">
                    <thead>
                        <tr>
                            <th>Product</th>
                            <th>Woolies</th>
                            <th>Coles</th>
                            <th>Best unit</th>
                        </tr>
                    </thead>
                    <tbody>${tableRows}</tbody>
                </table>
            </div>
            <div class="cg-modal-footer">
                <span class="cg-modal-footnote">${footnoteText}</span>
            </div>
        </div>`;

    document.body.appendChild(modal);
    if (typeof feather !== 'undefined') feather.replace();
}

let _compareGroupUiReady = false;
function setupCompareGroupInteractions() {
    if (_compareGroupUiReady) return;
    _compareGroupUiReady = true;
    document.body.addEventListener('click', (e) => {
        const btn = e.target.closest('[data-compare-group]');
        if (!btn || !btn.dataset.compareGroup) return;
        e.preventDefault();
        openCompareGroupModal(btn.dataset.compareGroup);
    });
}

function latestMatchedNameForStore(item, storeKey) {
    const hist = Array.isArray(item.scrape_history) ? item.scrape_history : [];
    for (let i = hist.length - 1; i >= 0; i--) {
        const entry = hist[i] || {};
        const matched = (entry.matched_name || '').trim();
        if (matched && entry.store === storeKey) return matched;
    }
    return '';
}

function buildStoreSearchTerm(item, storeKey) {
    return (
        latestMatchedNameForStore(item, storeKey) ||
        (item.name_check || '').trim() ||
        (item.name || '').trim()
    );
}

function getStoreUrlForStore(item, storeKey, opts = {}) {
    const hasStoreData = Object.keys(item.all_stores || {}).length > 0;
    const preferSearchForWoolworthsPdp = opts.preferSearchForWoolworthsPdp == null
        ? true
        : Boolean(opts.preferSearchForWoolworthsPdp);
    if (storeKey === 'coles') {
        if (item.coles) return item.coles;
        return `https://www.coles.com.au/search?q=${encodeURIComponent(buildStoreSearchTerm(item, 'coles'))}`;
    }
    if (item.woolworths) {
        const isWooliesPdp = item.woolworths.includes('/productdetails/');
        if (preferSearchForWoolworthsPdp && isWooliesPdp) {
            return `https://www.woolworths.com.au/shop/search/products?searchTerm=${encodeURIComponent(buildStoreSearchTerm(item, 'woolworths'))}`;
        }
        if (hasStoreData || !isWooliesPdp) {
            return item.woolworths;
        }
    }
    return `https://www.woolworths.com.au/shop/search/products?searchTerm=${encodeURIComponent(buildStoreSearchTerm(item, 'woolworths'))}`;
}

function classifyColaCandidate(item) {
    const name = (item.name || '').toLowerCase();
    if (/mango|vanilla|cherry|lime|raspberry|ginger|lemon|creaming soda|orange|grape|melon/i.test(name)) return null;
    const isNoSugar = name.includes('max') || name.includes('zero') || name.includes('no sugar');
    const isPepsi = name.includes('pepsi');
    const isCoke = name.includes('coke') || name.includes('coca');
    const category = isNoSugar ? 'noSugar' : 'classic';
    let brand = null;
    if (isPepsi) brand = 'pepsi';
    else if (isCoke) brand = 'coke';
    else return null;
    return { category, brand };
}

function colaCandidatePerLitre(c) {
    if (!c || !c.item) return null;
    const item = c.item;

    // Prefer deterministic shelf/pack_litres when available.
    const packL = item.pack_litres;
    const shelf = c.shelf_price;
    if (
        typeof packL === 'number' &&
        Number.isFinite(packL) &&
        packL > 0 &&
        typeof shelf === 'number' &&
        Number.isFinite(shelf) &&
        shelf > 0
    ) {
        return shelf / packL;
    }

    if (item.price_mode === 'litre' && isReliableEffPrice(c.eff_price)) return c.eff_price;

    const itemUnit = (item.unit || '').toLowerCase();
    if (
        itemUnit === 'litre' &&
        typeof c.unit_price === 'number' &&
        Number.isFinite(c.unit_price) &&
        c.unit_price > 0 &&
        c.unit_price < PRICE_UNRELIABLE
    ) {
        return c.unit_price;
    }

    return null;
}

function compareColaCandidates(a, b) {
    const aPL = typeof a.per_litre === 'number' ? a.per_litre : colaCandidatePerLitre(a);
    const bPL = typeof b.per_litre === 'number' ? b.per_litre : colaCandidatePerLitre(b);
    const aOK = typeof aPL === 'number' && Number.isFinite(aPL) && aPL > 0;
    const bOK = typeof bPL === 'number' && Number.isFinite(bPL) && bPL > 0;
    if (aOK && !bOK) return -1;
    if (!aOK && bOK) return 1;
    if (aOK && bOK && Math.abs(aPL - bPL) > 0.0001) return aPL - bPL;
    return compareCandidates(a, b);
}

function buildGroupBestRowHtml(item) {
    const g = item.compare_group;
    if (!g) return '';
    const memberN = _compareGroupMemberCounts.get(g) || 0;
    if (memberN < 2) return '';

    const btn = `<button type="button" class="cg-compare-btn" data-compare-group="${escapeHtml(g)}">Compare sizes</button>`;

    let hintHtml = '';
    const meta = _compareGroupMeta.get(g);
    if (meta && meta.candidateCount >= 2) {
        const minSelf = minEffPriceAcrossStores(item);
        const eps = 0.01;
        if (Number.isFinite(minSelf) && Math.abs(minSelf - meta.winnerEff) <= eps) {
            hintHtml = '<div class="group-best-hint group-best-on-top" title="Lowest comparable unit price in this compare_group">You\'re on the best deal in this group</div>';
        } else {
            const storeLabel = meta.winnerStore === 'woolworths' ? 'Woolworths' : 'Coles';
            const priceStr = formatCompareEffPrice(meta.mode, meta.winnerEff);
            const shortName = escapeHtml(displayName(meta.winnerItemName));
            hintHtml = `<div class="group-best-hint" title="Cheapest comparable unit across all SKUs and stores in this group">Best in group: ${priceStr} · ${storeLabel} · ${shortName}</div>`;
        }
    } else {
        hintHtml = `<div class="group-best-hint cg-hint-muted" title="Open Compare sizes to see each SKU; dashboard ranking is off until modes align and both stores have prices">Compare SKUs — see all sizes &amp; stores</div>`;
    }

    return `<div class="cg-group-row">${hintHtml}<div class="cg-compare-actions">${btn}</div></div>`;
}

// Track sparkline Chart instances so we can destroy them before recreating.
// Keyed by container element ID string.
const _sparklineCharts = {};

const _DISPLAY_ABBREVS = [
    [/\bWw\b/gi, 'Woolworths'], [/\bDf\b/gi, 'Dairy Farmers'],
    [/\bEss\b/gi, 'Essentials'], [/\bSrdgh\b/gi, 'Sourdough'],
    [/\bHmlyn\b/gi, 'Himalayan'], [/\bBflied\b/gi, 'Butterflied'],
    [/\bB'Flied\b/gi, 'Butterflied'], [/\bLemn\b/gi, 'Lemon'],
    [/\bGrlc\b/gi, 'Garlic'], [/\bStarwberry\b/gi, 'Strawberry'],
    [/\bConc\b/gi, 'Concentrate'], [/\bRw\b/gi, ''],
    [/\bTrplsmkd\b/gi, 'Triple Smoked'], [/\bShvd\b/gi, 'Shaved'],
    [/\bApprvd\b/gi, 'Approved'], [/\bF\/F\b/gi, 'Fat Free'],
    [/\bF\/C\b/gi, 'Fresh Choice'], [/\bP\/P\b/gi, ''],
    [/\bPnut\b/gi, 'Peanut'], [/\bCrml\b/gi, 'Caramel'],
    [/\bCkie\b/gi, 'Cookie'], [/\bBtr\b/gi, 'Butter'],
    [/\bEfferv\b/gi, 'Effervescent'], [/\bHm\b/gi, 'Ham'],
    [/\bT\/Tiss\b/gi, 'Toilet Tissue'], [/\bLge\b/gi, 'Large'],
    [/\bXl\b/gi, 'XL'], [/\bChoc\b/gi, 'Chocolate'],
    [/\bPud\b/gi, 'Pudding'], [/\bBbq\b/gi, 'BBQ'],
    [/\bPb\b/gi, 'Peanut Butter'], [/\bDbl\b/gi, 'Double'],
    [/\bEsprs\b/gi, 'Espresso'], [/\bFlav\b/gi, 'Flavoured'],
    [/\bWtr\b/gi, 'Water'], [/\bNatrl\b/gi, 'Natural'],
    [/\b35Hr\b/gi, '35 Hour'], [/\bCb\b/gi, 'Carb'],
];
function displayName(name) {
    if (!name) return '';
    let n = name;
    for (const [re, rep] of _DISPLAY_ABBREVS) n = n.replace(re, rep);
    return n.replace(/\s{2,}/g, ' ').trim();
}


let _monitorsStarted = false;

async function initDashboard() {
    try {
        const dataRes = await fetch('data.json').catch(() => null);

        if (dataRes && dataRes.ok) {
            const parsed = await dataRes.json();
            if (Array.isArray(parsed)) {
                _data = parsed;
            } else {
                _data = parsed.items || [];
                const luEl = document.getElementById('last-updated');
                if (luEl && parsed.last_updated) {
                    _lastChecked = parsed.last_updated;
                    updateLastCheckedDisplay();
                }
            }
        }

        if (!_monitorsStarted) {
            _monitorsStarted = true;
            const tick = () => {
                if (typeof document !== 'undefined' && document.visibilityState === 'hidden') return;
                checkShoppingTripTimeout();
            };
            const tickCloud = () => {
                if (typeof document !== 'undefined' && document.visibilityState === 'hidden') return;
                monitorCloudHealth();
            };
            setInterval(tick, 30000);
            setInterval(tickCloud, 300000);
            document.addEventListener('visibilitychange', () => {
                if (document.visibilityState === 'visible') {
                    monitorCloudHealth();
                    checkShoppingTripTimeout();
                }
            });
            monitorCloudHealth();
            checkShoppingTripTimeout();
            startShoppingListCloudSyncMonitors();
        }

        // Build _history from inline scrape_history (single source of truth)
        _data.forEach(item => {
            if (item.scrape_history && item.scrape_history.length > 0) {
                _history[itemKey(item)] = { target: item.target, history: item.scrape_history };
            }
        });

        rebuildCompareGroupMeta();
        renderCompareGroupDiagnostics();
        setupCompareGroupInteractions();

        setupFilters();
        const statusOk = document.getElementById('app-status');
        if (statusOk) statusOk.textContent = '';
        renderDashboard();
        syncDealsHeroStatus();
        return true;
    } catch (e) {
        console.error("Failed to initialize dashboard:", e);
        const statusEl = document.getElementById('app-status');
        if (statusEl) statusEl.textContent = 'Could not load prices. Pull down to refresh or try again.';
        document.getElementById('specials-grid').innerHTML = '<p style="color: #ef4444;">Could not load prices. Check your connection and refresh.</p>';
        syncDealsHeroStatus();
        return false;
    }
}

function setupFilters() {
    // Tab switching
    const navLinks = document.querySelectorAll('.nav-link');
    const mobileNavLinks = document.querySelectorAll('.mobile-nav-link[data-tab]');
    
    const switchTab = (target) => {
        _currentTab = target;
        document.body.dataset.activeTab = target;
        haptic(8);
        dismissPriceDropToast();

        // Sync desktop buttons
        navLinks.forEach(l => l.classList.toggle('active', l.dataset.tab === target));
        
        // Sync mobile buttons
        mobileNavLinks.forEach(l => l.classList.toggle('active', l.dataset.tab === target));

        syncTabAriaCurrent(target);
        
        // Switch content
        document.querySelectorAll('.tab-content').forEach(tab => {
            tab.classList.toggle('active', tab.id === `tab-${target}`);
        });
        
        if (prefersReducedMotion()) window.scrollTo(0, 0);
        else window.scrollTo({ top: 0, behavior: 'smooth' });
        
        if (target === 'analytics') {
            syncCompareGroupDetailsState();
            syncAnalyticsViewportState();
            renderAnalytics();
        }
        else renderDashboard();
    };

    navLinks.forEach(link => {
        link.addEventListener('click', () => switchTab(link.dataset.tab));
    });

    mobileNavLinks.forEach(link => {
        link.addEventListener('click', () => switchTab(link.dataset.tab));
    });

    document.getElementById('mobile-refresh-btn')?.addEventListener('click', () => {
        const btn = document.getElementById('mobile-refresh-btn');
        btn.classList.add('spin');
        initDashboard().then((ok) => {
            if (ok) showUiToast('Prices updated');
        }).finally(() => {
            setTimeout(() => btn.classList.remove('spin'), 1000);
        });
    });

    const storeButtons = document.querySelectorAll('.filter-btn');
    storeButtons.forEach(btn => {
        btn.addEventListener('click', (e) => {
            storeButtons.forEach(b => b.classList.remove('active'));
            e.target.classList.add('active');
            _currentFilter = e.target.dataset.filter;
            _currentPage = 1;
            renderDashboard();
        });
    });

    const catButtons = document.querySelectorAll('.filter-btn-cat');
    catButtons.forEach(btn => {
        btn.addEventListener('click', (e) => {
            catButtons.forEach(b => b.classList.remove('active'));
            e.target.classList.add('active');
            _currentCatFilter = e.target.dataset.cat;
            _currentPage = 1;
            renderDashboard();
        });
    });

    // Shop Mode Toggle
    const modeLabels = document.querySelectorAll('.mode-label');
    modeLabels.forEach(label => {
        label.addEventListener('click', () => {
            _shopMode = label.dataset.mode;
            localStorage.setItem('shopMode', _shopMode);
            modeLabels.forEach(l => l.classList.remove('active'));
            label.classList.add('active');
            renderDashboard();
        });
    });

    // Drawer toggles
    document.getElementById('toggle-list-btn')?.addEventListener('click', toggleDrawer);
    document.getElementById('mobile-toggle-list')?.addEventListener('click', toggleDrawer);
    document.getElementById('close-drawer')?.addEventListener('click', toggleDrawer);
    document.getElementById('drawer-overlay')?.addEventListener('click', toggleDrawer);

    // Search
    document.getElementById('dashboard-search')?.addEventListener('input', (e) => {
        _searchText = e.target.value.toLowerCase();
        _currentPage = 1;
        renderDashboard();
    });

    // Modals
    document.getElementById('modal-cancel')?.addEventListener('click', closeModal);
    document.getElementById('modal-save')?.addEventListener('click', saveItemChanges);
    
    const stockBtns = document.querySelectorAll('.stock-btn');
    stockBtns.forEach(btn => {
        btn.addEventListener('click', () => {
            stockBtns.forEach(b => b.classList.remove('active'));
            btn.classList.add('active');
        });
    });

    // Sort pills (E — replaces native <select>)
    document.querySelectorAll('.sort-pill').forEach(pill => {
        pill.addEventListener('click', () => {
            document.querySelectorAll('.sort-pill').forEach(p => p.classList.remove('active'));
            pill.classList.add('active');
            _currentSort = pill.dataset.sort;
            _currentPage = 1;
            renderSpecials();
        });
    });

    // Clear List
    document.getElementById('clear-list-btn')?.addEventListener('click', () => {
        if (confirm("Clear your entire shopping list?")) {
            if (_shoppingTripMode) setShoppingTripMode(false, 'clear_all');
            _shoppingList = [];
            persistShoppingList();
            renderShoppingList();
            updateListCount();
        }
    });

    document.getElementById('go-shopping-btn')?.addEventListener('click', () => setShoppingTripMode(true));
    document.getElementById('done-shopping-btn')?.addEventListener('click', () => setShoppingTripMode(false, 'done_button'));
    document.getElementById('clear-completed-btn')?.addEventListener('click', clearPickedListItems);
    
    // Settings Logic
    document.getElementById('settings-btn')?.addEventListener('click', openSettings);
    document.getElementById('settings-cancel')?.addEventListener('click', closeSettings);
    document.getElementById('settings-save')?.addEventListener('click', saveSettings);
}

function syncCompareGroupDetailsState() {
    const details = document.getElementById('compare-group-details');
    if (!details) return;
    const isDesktop = typeof matchMedia !== 'undefined' && matchMedia('(min-width: 769px)').matches;
    details.open = isDesktop;
}

function syncAnalyticsViewportState() {
    _analyticsViewportMode = isMobileViewport() ? 'mobile' : 'desktop';
    _analyticsOrientation = window.innerWidth > window.innerHeight ? 'landscape' : 'portrait';
}

function rerenderAnalyticsForViewportIfNeeded() {
    const prevMode = _analyticsViewportMode;
    const prevOrientation = _analyticsOrientation;
    syncAnalyticsViewportState();
    if (_currentTab !== 'analytics') return;

    const modeChanged = prevMode && prevMode !== _analyticsViewportMode;
    const orientationChanged = prevOrientation && prevOrientation !== _analyticsOrientation;
    if (modeChanged || orientationChanged) {
        renderAnalytics();
        return;
    }
    resizeInsightsCharts();
}

function setupAnalyticsMobileBehaviors() {
    syncAnalyticsViewportState();
    syncCompareGroupDetailsState();
    const handleResize = () => {
        if (_analyticsResizeTimer) clearTimeout(_analyticsResizeTimer);
        _analyticsResizeTimer = setTimeout(() => {
            syncCompareGroupDetailsState();
            rerenderAnalyticsForViewportIfNeeded();
        }, 140);
    };
    window.addEventListener('resize', handleResize, { passive: true });
    window.visualViewport?.addEventListener('resize', handleResize, { passive: true });
    window.addEventListener('orientationchange', handleResize, { passive: true });
}

function setupMobileChromeCompaction() {
    const updateMobileChrome = () => {
        const isMobile = isMobileViewport();
        const scrolled = window.scrollY > 36;
        document.body.classList.toggle('mobile-scrolled', isMobile && scrolled);
        if (!_debugChromeSnapshotLogged) {
            _debugChromeSnapshotLogged = true;
            const headerH = document.querySelector('.header')?.getBoundingClientRect?.().height || 0;
            const stickyH = document.querySelector('.sticky-filter-bar')?.getBoundingClientRect?.().height || 0;
            const navH = document.querySelector('.mobile-bottom-nav')?.getBoundingClientRect?.().height || 0;
            // #region agent log
            fetch('http://127.0.0.1:7716/ingest/1692efee-81d9-413c-bd30-574d3de06991',{method:'POST',headers:{'Content-Type':'application/json','X-Debug-Session-Id':'0b485d'},body:JSON.stringify({sessionId:'0b485d',runId:'initial',hypothesisId:'H2',location:'docs/app.js:setupMobileChromeCompaction',message:'mobile chrome footprint snapshot',data:{isMobile,scrolled,headerH,stickyH,navH,totalFixedChrome:headerH+stickyH+navH,viewportH:window.innerHeight},timestamp:Date.now()})}).catch(()=>{});
            // #endregion
        }
    };
    window.addEventListener('scroll', updateMobileChrome, { passive: true });
    window.addEventListener('resize', updateMobileChrome, { passive: true });
    window.visualViewport?.addEventListener('resize', updateMobileChrome, { passive: true });
    updateMobileChrome();
}

function openSettings() {
    _focusBeforeSettings = document.activeElement;
    document.getElementById('write-api-url-input').value = _writeApiUrl;
    document.getElementById('write-api-secret-input').value = _writeApiSecret;
    document.getElementById('settings-modal').style.display = 'flex';
    setTimeout(() => document.getElementById('write-api-url-input')?.focus(), 0);
}

function closeSettings() {
    document.getElementById('settings-modal').style.display = 'none';
    const prev = _focusBeforeSettings;
    _focusBeforeSettings = null;
    prev?.focus?.();
}

function saveSettings() {
    _writeApiUrl = document.getElementById('write-api-url-input').value.trim();
    _writeApiSecret = document.getElementById('write-api-secret-input').value;
    localStorage.setItem('write_api_url', _writeApiUrl);
    localStorage.setItem('write_api_secret', _writeApiSecret);
    closeSettings();
    monitorCloudHealth();
    startShoppingListCloudSyncMonitors();
    pullShoppingListFromCloud('settings_saved');
    scheduleShoppingListCloudPush('settings_saved');
}

function toggleDrawer() {
    const drawer = document.getElementById('list-drawer');
    const overlay = document.getElementById('drawer-overlay');
    const toggleBtns = [
        document.getElementById('toggle-list-btn'),
        document.getElementById('mobile-toggle-list')
    ].filter(Boolean);
    drawer.style.transform = ''; // reset any drag position
    const willOpen = !drawer.classList.contains('open');
    if (willOpen) _focusBeforeDrawer = document.activeElement;
    drawer.classList.toggle('open');
    overlay.classList.toggle('open');
    const isOpen = drawer.classList.contains('open');
    document.body.classList.toggle('drawer-open', isOpen);
    toggleBtns.forEach(btn => btn.setAttribute('aria-expanded', isOpen ? 'true' : 'false'));
    if (isOpen) {
        haptic(8);
        renderShoppingList();
        setTimeout(() => document.getElementById('close-drawer')?.focus(), 0);
    } else {
        const prev = _focusBeforeDrawer;
        _focusBeforeDrawer = null;
        prev?.focus?.();
    }
}

function setupOverlayEscapeHandler() {
    document.addEventListener('keydown', (e) => {
        if (e.key !== 'Escape') return;
        if (document.getElementById('compare-group-modal')) {
            e.preventDefault();
            closeCompareGroupModal();
            return;
        }
        if (document.getElementById('deepdive-modal')) {
            e.preventDefault();
            closeItemDeepdive();
            return;
        }
        const stockModal = document.getElementById('overlay-modal');
        if (stockModal && stockModal.style.display === 'flex') {
            e.preventDefault();
            closeModal();
            return;
        }
        const settingsModal = document.getElementById('settings-modal');
        if (settingsModal && settingsModal.style.display === 'flex') {
            e.preventDefault();
            closeSettings();
            return;
        }
        const drawer = document.getElementById('list-drawer');
        if (drawer?.classList.contains('open')) {
            e.preventDefault();
            toggleDrawer();
        }
    });
}


function renderDashboard() {
    renderCountdown();
    renderWednesdayBanner();
    renderStats();
    renderTop5Deals();
    renderEssentials();
    renderBuyNow();         // F: Buy Now priority card
    renderMobilePriorityRail(); // mobile-only rail above feed
    renderPredictions();
    renderNearMisses();
    renderSpecials();
    // Master table is lazy-loaded on expand (D) — just update the meta count
    const metaEl = document.getElementById('master-table-meta');
    if (metaEl) metaEl.textContent = `${_data.length} items`;
    updateListCount();
    checkPriceDropAlerts();
    syncDealsHeroStatus();
    const isMobile = isMobileViewport();
    const railSections = document.querySelectorAll('#mobile-priority-rail .priority-rail-section').length;
    const isPredictionsHidden = document.getElementById('predictions-section')?.classList.contains('hidden');
    const isNearMissHidden = document.getElementById('near-misses-section')?.classList.contains('hidden');
    // #region agent log
    fetch('http://127.0.0.1:7716/ingest/1692efee-81d9-413c-bd30-574d3de06991',{method:'POST',headers:{'Content-Type':'application/json','X-Debug-Session-Id':'0b485d'},body:JSON.stringify({sessionId:'0b485d',runId:'initial',hypothesisId:'H4',location:'docs/app.js:renderDashboard',message:'dashboard signal density snapshot',data:{isMobile,totalItems:_data.length,railSections,isPredictionsHidden,isNearMissHidden,specialGridCards:document.querySelectorAll('#specials-grid .item-card').length},timestamp:Date.now()})}).catch(()=>{});
    // #endregion
    const top5Card = document.getElementById('top5-card');
    const buyNowCard = document.getElementById('buy-now-card');
    const rail = document.getElementById('mobile-priority-rail');
    const statsStrip = document.querySelector('.stats-strip');
    const hero = document.querySelector('.deals-hero');
    const top5Display = top5Card ? getComputedStyle(top5Card).display : 'missing';
    const buyNowDisplay = buyNowCard ? getComputedStyle(buyNowCard).display : 'missing';
    const railDisplay = rail ? getComputedStyle(rail).display : 'missing';
    const top5ListCount = document.querySelectorAll('#top5-list .top5-row').length;
    const buyNowListCount = document.querySelectorAll('#buy-now-list .buy-now-row').length;
    // #region agent log
    fetch('http://127.0.0.1:7716/ingest/1692efee-81d9-413c-bd30-574d3de06991',{method:'POST',headers:{'Content-Type':'application/json','X-Debug-Session-Id':'0b485d'},body:JSON.stringify({sessionId:'0b485d',runId:'round2',hypothesisId:'H6',location:'docs/app.js:renderDashboard',message:'desktop composition visibility snapshot',data:{isMobile,top5Display,buyNowDisplay,railDisplay,top5ListCount,buyNowListCount,railSections},timestamp:Date.now()})}).catch(()=>{});
    // #endregion
    const stickyBar = document.querySelector('.sticky-filter-bar');
    const stickyH = stickyBar?.getBoundingClientRect?.().height || 0;
    const statsH = statsStrip?.getBoundingClientRect?.().height || 0;
    const heroH = hero?.getBoundingClientRect?.().height || 0;
    const heroMain = hero?.querySelector('.deals-hero-main');
    const heroTitle = hero?.querySelector('.deals-hero-title');
    const heroSub = hero?.querySelector('.deals-hero-sub');
    const heroEyebrow = hero?.querySelector('.deals-hero-eyebrow');
    // #region agent log
    fetch('http://127.0.0.1:7716/ingest/1692efee-81d9-413c-bd30-574d3de06991',{method:'POST',headers:{'Content-Type':'application/json','X-Debug-Session-Id':'0b485d'},body:JSON.stringify({sessionId:'0b485d',runId:'round3',hypothesisId:'H8',location:'docs/app.js:renderDashboard',message:'hero composition breakdown snapshot',data:{isMobile,heroH,heroMainH:heroMain?.getBoundingClientRect?.().height||0,heroTitleH:heroTitle?.getBoundingClientRect?.().height||0,heroSubH:heroSub?.getBoundingClientRect?.().height||0,heroEyebrowH:heroEyebrow?.getBoundingClientRect?.().height||0,heroSubDisplay:heroSub?getComputedStyle(heroSub).display:'missing',heroEyebrowDisplay:heroEyebrow?getComputedStyle(heroEyebrow).display:'missing',heroTitleSize:heroTitle?getComputedStyle(heroTitle).fontSize:'missing',heroTitleLine:heroTitle?getComputedStyle(heroTitle).lineHeight:'missing'},timestamp:Date.now()})}).catch(()=>{});
    // #endregion
    // #region agent log
    fetch('http://127.0.0.1:7716/ingest/1692efee-81d9-413c-bd30-574d3de06991',{method:'POST',headers:{'Content-Type':'application/json','X-Debug-Session-Id':'0b485d'},body:JSON.stringify({sessionId:'0b485d',runId:'round2',hypothesisId:'H7',location:'docs/app.js:renderDashboard',message:'vertical stack footprint snapshot',data:{isMobile,heroH,statsH,stickyH,sum:heroH+statsH+stickyH,viewportH:window.innerHeight},timestamp:Date.now()})}).catch(()=>{});
    // #endregion
}


function formatPrice(item) {
    const effPrice = item.eff_price || item.price;
    if (item.price_mode === 'kg') return `$${effPrice.toFixed(2)}/kg`;
    if (item.price_mode === 'litre') return `$${item.price.toFixed(2)} ($${effPrice.toFixed(2)}/L)`;
    return `$${item.price.toFixed(2)}`;
}

function renderCountdown() {
    const textEl = document.getElementById('countdown-text');
    const pill = document.getElementById('specials-countdown');
    
    // Specials reset every Wed (Woolies/Coles updates on Wed morning).
    // Tuesday is the last day.
    const now = new Date();
    const day = now.getDay(); // 0=Sun, 2=Tue, 3=Wed
    
    // Days until next Wednesday — 0 means Wednesday IS today (just refreshed)
    let daysLeft = (3 - day + 7) % 7;
    
    if (daysLeft === 0) {
        // It's Wednesday — new specials just dropped
        pill.classList.remove('urgent');
        textEl.textContent = 'New specials live today!';
    } else if (daysLeft === 1) {
        pill.classList.add('urgent');
        textEl.textContent = 'Ends TOMORROW (Tue)';
    } else {
        pill.classList.remove('urgent');
        textEl.textContent = `Specials end in ${daysLeft} days`;
    }
}

function renderStats() {
    const totalItemsEl = document.getElementById('total-items');
    const totalSpecialsEl = document.getElementById('total-specials');
    const cartTotalEl = document.getElementById('cart-total');
    
    if (totalItemsEl) totalItemsEl.textContent = _data.length;
    
    const savingsSummary = getSavingsOverview(_data);
    if (totalSpecialsEl) totalSpecialsEl.textContent = savingsSummary.activeDeals;
    if (cartTotalEl) cartTotalEl.textContent = `$${savingsSummary.currentSavings.toFixed(2)}`;

    // Monthly Budget Tracker
    let monthlySpent = 0;
    const now = new Date();
    const currentMonth = now.getMonth();
    const currentYear = now.getFullYear();
    
    _data.forEach(item => {
        // Preference 1: Detailed price history (captures multiple purchases)
        if (item.price_history && item.price_history.length > 0) {
            item.price_history.forEach(h => {
                const d = new Date(h.date);
                if (d.getMonth() === currentMonth && d.getFullYear() === currentYear) {
                    monthlySpent += h.price;
                }
            });
        } 
        // Preference 2: Fallback to last_purchased (legacy or single-entry items)
        else if (item.last_purchased) {
            const d = new Date(item.last_purchased);
            if (d.getMonth() === currentMonth && d.getFullYear() === currentYear) {
                monthlySpent += (item.eff_price || item.price || 0);
            }
        }
    });

    const budgetProgress = document.getElementById('budget-progress');
    const budgetText = document.getElementById('budget-spent-text');
    const percent = Math.min((monthlySpent / MONTHLY_BUDGET) * 100, 100);
    
    budgetText.textContent = `$${monthlySpent.toFixed(0)} / $${MONTHLY_BUDGET}`;
    budgetProgress.style.width = `${percent}%`;
    budgetProgress.style.background = monthlySpent > MONTHLY_BUDGET ? 'var(--coles-red)' : 'var(--woolies-green)';

    renderColaBattle();
}



// ── Essentials: editable, persisted in localStorage ───────────────────────
const DEFAULT_ESSENTIALS = [
    // Produce (by purchase frequency)
    "Spinach", "Onions", "Avocado", "Baby Potatoes", "Broccolini",
    "Capsicum", "Zucchini", "Cherry Tomatoes", "Sliced Mushrooms", "Pak Choy",
    // Dairy
    "Eggs", "Cream", "Cheese", "Greek Yoghurt", "Bocconcini", "Creamy Vanilla", "Sour Cream",
    // Protein (Beef Mince is #1 most-bought item)
    "Chicken Breast", "Beef Mince",
    // Pantry
    "Passata", "Diced Tomatoes", "Garlic", "Maple Syrup", "Fajita Seasoning",
    // Bakery
    "Sourdough Loaf", "English Muffins",
    // Drinks
    "Nescafe Vanilla", "Pepsi Max",
    // Treats & Pets
    "Lindt 95%", "Whiskas",
];



function getEssentials() {
    const stored = localStorage.getItem('essentialsList');
    return stored ? JSON.parse(stored) : [...DEFAULT_ESSENTIALS];
}

function saveEssentials(list) {
    localStorage.setItem('essentialsList', JSON.stringify(list));
}

// Auto-reset checked items each new grocery week (Sunday)
function maybeResetEssentials() {
    const today = new Date();
    const todayStr = today.toDateString();
    const lastReset = localStorage.getItem('essentialsLastReset');
    // Reset on Sunday (day 0)
    if (today.getDay() === 0 && lastReset !== todayStr) {
        localStorage.removeItem('essentialsChecked');
        localStorage.setItem('essentialsLastReset', todayStr);
    }
}

// ── Fuzzy price lookup — matches display names to tracked product names ─────
// 'Spinach' → 'F/C Babyspinach 120G', 'Chicken Breast' → 'Ww Chicken Breast Fillets...'
// When multiple items match, prefers the one with most purchase history.
function findDataItem(name) {
    const dataArr = _data || [];
    const q = name.toLowerCase().trim();

    const byHistory = (a, b) =>
        (b.price_history?.length || 0) - (a.price_history?.length || 0);

    // 1. Exact match (case insensitive)
    const exact = dataArr.find(i => i.name.toLowerCase() === q);
    if (exact) return exact;

    // 2. All meaningful words in the display name appear in the tracked item name
    //    e.g. "Chicken Breast" → items containing both "chicken" AND "breast"
    //    e.g. "Baby Potatoes" → items containing both "baby" AND "potato"
    const words = q.split(/\s+/).filter(w => w.length > 2);
    if (words.length) {
        // Also try stemmed forms (strip trailing 's'/'es') for each word
        const stems = words.map(w => w.replace(/i?e?s$/, ''));
        const matches = dataArr.filter(i => {
            const n = i.name.toLowerCase();
            return stems.every(s => n.includes(s));
        });
        if (matches.length) return matches.sort(byHistory)[0];
    }

    // 3. Single-word query only: try stem match across all tracked items
    //    e.g. "Onions" → stem "onion" → "Onion Brown 1Kg P/P"
    //    Skip for multi-word to avoid false positives like "Baby" matching "Babyspinach"
    if (words.length <= 1) {
        const sigWords = q.split(/\s+/).filter(w => w.length > 3);
        const stems3 = sigWords.map(w => w.replace(/i?e?s$/, ''));
        for (const s of stems3) {
            const matches = dataArr.filter(i => i.name.toLowerCase().includes(s));
            if (matches.length) return matches.sort(byHistory)[0];
        }
    }

    return null;
}


function renderEssentials() {
    maybeResetEssentials();
    const list = document.getElementById('essentials-list');
    if (!list) return;
    list.innerHTML = '';

    const essentials = getEssentials();
    const checkedItems = JSON.parse(localStorage.getItem('essentialsChecked') || '[]');

    // ── Fuzzy price lookup (delegates to module-level findDataItem) ─────────


    // ── Header row with edit toggle ──────────────────────────────────────
    const header = document.createElement('div');
    header.className = 'essentials-header';
    const doneCount = checkedItems.length;
    const totalCount = essentials.length;
    header.innerHTML = `
        <span class="essentials-progress-text">${doneCount}/${totalCount} got</span>
        <div class="essentials-header-actions">
            <button class="essentials-reset-btn" onclick="resetEssentialsChecked()" title="Uncheck all">↺</button>
            <button class="essentials-edit-btn" onclick="toggleEssentialsEdit()" title="Edit list" id="essentials-edit-btn">✏️</button>
        </div>`;
    list.appendChild(header);

    // ── Progress bar ─────────────────────────────────────────────────────
    const progressWrap = document.createElement('div');
    progressWrap.className = 'essentials-progress-bar-bg';
    progressWrap.innerHTML = `<div class="essentials-progress-fill" style="width:${totalCount ? (doneCount/totalCount*100) : 0}%"></div>`;
    list.appendChild(progressWrap);

    // ── Item rows ─────────────────────────────────────────────────────────
    // Unchecked first, then checked (greyed)
    const sorted = [...essentials].sort((a, b) => {
        const aChecked = checkedItems.includes(a);
        const bChecked = checkedItems.includes(b);
        return aChecked - bChecked;
    });

    sorted.forEach(itemName => {
        const isChecked = checkedItems.includes(itemName);
        const dataItem = findDataItem(itemName);

        const price = dataItem ? (dataItem.eff_price || dataItem.price) : null;
        const onSpecial = dataItem?.on_special;
        const staleBadge = dataItem?.stale ? getStaleBadge(dataItem, true) : '';
        const stock = dataItem?.stock;

        // Stock dot colour
        const dotClass = stock === 'low' ? 'low' : stock === 'medium' ? 'medium' : stock === 'full' ? 'full' : '';
        const stockDot = dotClass ? `<span class="stock-dot ${dotClass}" title="${stock} stock"></span>` : '';

        // Price badge
        const priceBadge = price
            ? `<span class="essential-price ${onSpecial ? 'on-sale' : ''}">${onSpecial ? '🔥' : ''}$${price.toFixed(2)}</span>${staleBadge}`
            : '';

        const row = document.createElement('div');
        row.className = `essential-row${isChecked ? ' checked' : ''}`;
        row.innerHTML = `
            <label class="essential-checkbox-area">
                <input type="checkbox" class="essential-cb" ${isChecked ? 'checked' : ''} data-item="${itemName}">
                <span class="essential-label ${isChecked ? 'checked' : ''}">${stockDot}${itemName}</span>
            </label>
            <div class="essential-meta">
                ${priceBadge}
                <button class="essential-add-btn" title="Add to shopping list"
                    onclick="addEssentialToList('${itemName.replace(/'/g, "\\'")}')"
                    style="${isChecked ? 'opacity:0.4;' : ''}">+</button>
                <button class="essential-remove-btn hidden" title="Remove from essentials"
                    onclick="removeFromEssentials('${itemName.replace(/'/g, "\\'")}')" data-remove>🗑</button>
            </div>`;

        row.querySelector('.essential-cb').addEventListener('change', (e) => {
            let current = JSON.parse(localStorage.getItem('essentialsChecked') || '[]');
            if (e.target.checked) {
                current.push(itemName);
            } else {
                current = current.filter(i => i !== itemName);
            }
            localStorage.setItem('essentialsChecked', JSON.stringify(current));
            renderEssentials();
        });

        list.appendChild(row);
    });

    // ── Edit mode: add new item input ─────────────────────────────────────
    const editMode = list.dataset.editMode === 'true';
    const addRow = document.createElement('div');
    addRow.className = 'essential-add-row' + (editMode ? '' : ' hidden');
    addRow.id = 'essential-add-row';
    addRow.innerHTML = `
        <input type="text" id="essential-new-input" placeholder="Add item..." class="essential-new-input">
        <button class="essential-add-confirm-btn" onclick="addToEssentials()">Add</button>`;
    list.appendChild(addRow);

    // ── Edit mode: reset to defaults button ───────────────────────────────
    const resetRow = document.createElement('div');
    resetRow.className = 'essential-reset-defaults-row' + (editMode ? '' : ' hidden');
    resetRow.innerHTML = `
        <button class="essential-reset-defaults-btn" onclick="resetEssentialsToDefaults()">
            ↺ Reset to default list
        </button>`;
    list.appendChild(resetRow);

    // Restore edit mode visual state
    if (editMode) {
        list.querySelectorAll('[data-remove]').forEach(btn => btn.classList.remove('hidden'));
        const editBtn = document.getElementById('essentials-edit-btn');
        if (editBtn) editBtn.textContent = '✓';
    }

    if (typeof feather !== 'undefined') feather.replace();
}

function resetEssentialsChecked() {
    localStorage.removeItem('essentialsChecked');
    renderEssentials();
}

function resetEssentialsToDefaults() {
    if (!confirm('Reset to the default list? Your custom changes will be lost.')) return;
    localStorage.removeItem('essentialsList');
    localStorage.removeItem('essentialsChecked');
    const list = document.getElementById('essentials-list');
    if (list) list.dataset.editMode = 'false';
    renderEssentials();
}

function toggleEssentialsEdit() {
    const list = document.getElementById('essentials-list');
    if (!list) return;
    const isEditing = list.dataset.editMode === 'true';
    list.dataset.editMode = isEditing ? 'false' : 'true';
    renderEssentials();
    if (!isEditing) {
        // Focus the add input
        setTimeout(() => document.getElementById('essential-new-input')?.focus(), 50);
    }
}

function addToEssentials() {
    const input = document.getElementById('essential-new-input');
    if (!input) return;
    const val = input.value.trim();
    if (!val) return;
    const essentials = getEssentials();
    if (!essentials.map(e => e.toLowerCase()).includes(val.toLowerCase())) {
        essentials.push(val);
        saveEssentials(essentials);
    }
    input.value = '';
    renderEssentials();
    // Keep edit mode open
    const list = document.getElementById('essentials-list');
    if (list) list.dataset.editMode = 'true';
    renderEssentials();
}

function removeFromEssentials(itemName) {
    const essentials = getEssentials().filter(e => e.toLowerCase() !== itemName.toLowerCase());
    saveEssentials(essentials);
    const list = document.getElementById('essentials-list');
    if (list) list.dataset.editMode = 'true';
    renderEssentials();
    if (list) list.dataset.editMode = 'true';
    renderEssentials();
}

function addEssentialToList(itemName) {
    // Use fuzzy matching to find the real tracked product
    const dataItem = findDataItem(itemName);
    if (dataItem) {
        addToList(dataItem.name, undefined, dataItem.item_id || null);
    } else {
        // Fallback: add by display name with no price
        if (!_shoppingList.find(i => i.name === itemName)) {
            const row = { name: itemName, price: null, qty: 1, picked: false, updated_at: new Date().toISOString() };
            _shoppingList.push(row);
            persistShoppingList();
            renderShoppingList();
            updateListCount();
        }
    }
    renderEssentials();
}


function renderNearMisses() {
    const section = document.getElementById('near-misses-section');
    const grid = document.getElementById('near-misses-grid');
    grid.innerHTML = '';
    
    const nearMisses = _data.filter(item => {
        const effPrice = item.eff_price || item.price;
        const target = item.target || 0;
        if (target <= 0 || item.price_unavailable || item.on_special) return false;
        // Near miss = within 5% above target
        return effPrice > target && effPrice <= target * 1.05;
    }).sort((a, b) => {
        // Sort by closest to target first
        const ra = (a.eff_price || a.price) / a.target;
        const rb = (b.eff_price || b.price) / b.target;
        return ra - rb;
    });
    
    if (nearMisses.length > 0) {
        section.classList.remove('hidden');
        nearMisses.slice(0, 6).forEach((item, index) => {
            const card = createItemCard(item, index, 'near');
            grid.appendChild(card);
        });
    } else {
        section.classList.add('hidden');
    }
}

function getConfidenceBadge(item) {
    const conf = item.target_confidence;
    const pts = item.target_data_points || 0;
    const method = item.target_method || '';
    if (!conf || conf === 'high') {
        // Treat missing metadata as high if it's a manually-configured item (no target_method)
        const isHigh = conf === 'high';
        const icon = isHigh ? '🟢' : '';
        if (!conf) return ''; // no badge for old items with no metadata yet
        return `<span class="confidence-badge high" title="Solid estimate (${method ? method + ', ' : ''}${pts} price checks)">🟢 Solid</span>`;
    }
    if (conf === 'medium') {
        return `<span class="confidence-badge medium" title="Fair estimate (${method ? method + ', ' : ''}${pts} price checks)">🟡 Fair</span>`;
    }
    return `<span class="confidence-badge low" title="Rough estimate — more shops will improve this (${method || 'needs data'})">🔴 Rough</span>`;
}

function getStaleBadge(item, compact = false) {
    if (!item?.stale) return '';
    const asOf = item.stale_as_of ? ` (last good: ${item.stale_as_of})` : '';
    const title = `Showing last confirmed price${asOf}`;
    const label = compact ? 'Old price' : '⏳ Old price';
    return `<span class="stale-badge${compact ? ' compact' : ''}" title="${title}">${label}</span>`;
}

function getEffectivePrice(item) {
    if (!item || typeof item !== 'object') return 0;
    const ep = item.eff_price || item.price || 0;
    return Number.isFinite(ep) ? ep : 0;
}

function isItemAtDealPrice(item) {
    if (!item || item.price_unavailable) return false;
    const ep = getEffectivePrice(item);
    return Boolean(item.on_special || ((item.target || 0) > 0 && ep <= item.target));
}

function computeItemSavingsSnapshot(item) {
    const eff = getEffectivePrice(item);
    const shelf = item.price || eff;
    const reference = item.was_price && item.was_price > shelf ? item.was_price : (item.target || shelf);
    const savedDollar = Math.max(0, reference - shelf);
    const savePct = reference > 0 ? Math.round((savedDollar / reference) * 100) : 0;
    return {
        eff,
        shelf,
        reference,
        savedDollar,
        savePct,
        isDeal: isItemAtDealPrice(item),
    };
}

function getPriorityItems(items = _data, limit = 8) {
    return (items || [])
        .filter(item => item?.stock === 'low')
        .map(item => ({ ...item, _snap: computeItemSavingsSnapshot(item) }))
        .filter(item => item._snap.isDeal)
        .sort((a, b) => b._snap.savePct - a._snap.savePct)
        .slice(0, limit);
}

function getTopDeals(items = _data, limit = 5) {
    return (items || [])
        .map(item => ({ ...item, _snap: computeItemSavingsSnapshot(item) }))
        .filter(item => item._snap.isDeal && item._snap.savePct > 0 && !item.price_unavailable)
        .sort((a, b) => b._snap.savePct - a._snap.savePct)
        .slice(0, limit);
}

function getSavingsOverview(items = _data) {
    const summary = {
        currentSavings: 0,
        potentialSavings: 0,
        activeDeals: 0,
    };
    for (const item of (items || [])) {
        if (!item || item.price_unavailable) continue;
        const snap = computeItemSavingsSnapshot(item);
        if (snap.isDeal) {
            summary.currentSavings += snap.savedDollar;
            summary.activeDeals += 1;
        }
        if ((item.target || 0) > 0) {
            summary.potentialSavings += Math.max(0, getEffectivePrice(item) - item.target);
        }
        if (item.on_special && item.was_price && item.was_price > snap.shelf) {
            summary.potentialSavings += Math.max(0, item.was_price - snap.shelf);
        }
    }
    return summary;
}

function buildWeeklyActionPlan(limit = 5) {
    return (_data || [])
        .map(item => {
            const snap = computeItemSavingsSnapshot(item);
            const urgency = item.stock === 'low' ? 2 : item.stock === 'medium' ? 1 : 0;
            const score = (snap.savePct * 2.2) + (snap.savedDollar * 7) + (urgency * 12);
            return { item, snap, urgency, score };
        })
        .filter(row => row.snap.isDeal && row.score > 0)
        .sort((a, b) => b.score - a.score)
        .slice(0, limit);
}

function createItemCard(item, index, type = 'special') {
    const effPrice = item.eff_price || item.price;
    const isSpecial = type === 'special' && effPrice <= item.target && !item.price_unavailable;
    const isNearMiss = type === 'near';
    const isPredicted = type === 'predicted';
    
    const card = document.createElement('div');
    const storeClass = item.store || 'woolworths';
    
    card.className = `item-card store-${storeClass} ${isNearMiss ? 'near-miss-card' : ''} ${isPredicted ? 'predicted-card' : ''}`;
    
    let imgSrc = item.local_image || item.image_url;
    let imgHtml = imgSrc 
        ? `<img src="${imgSrc}" class="item-image" loading="lazy" onerror="this.style.display='none'">`
        : `<div class="product-img-placeholder"><i data-feather="image"></i></div>`;

    const stockColor = item.stock === 'low' ? 'low' : (item.stock === 'medium' ? 'medium' : 'full');
    const confidenceBadge = getConfidenceBadge(item);
    const staleBadge = getStaleBadge(item);
    const targetTooltip = item.target_method 
        ? `title="${item.target_method}${item.target_data_points ? ` (${item.target_data_points} data points)` : ''}"` 
        : '';

    // Was/Now pricing for store-confirmed specials
    // Compare was_price to shelf price; cap at 70% to catch unit mismatches in stale data
    const shelfPrice = item.price || effPrice;
    let priceHtml;
    const hasSaneWas = item.on_special && item.was_price && item.was_price > shelfPrice && item.was_price < shelfPrice * 4;
    if (hasSaneWas) {
        const savePct = Math.round((1 - shelfPrice / item.was_price) * 100);
        priceHtml = `
            <span class="item-price">$${effPrice.toFixed(2)}</span>
            <span class="was-price">Was $${item.was_price.toFixed(2)}</span>
            <span class="save-badge">Save ${savePct}%</span>
        `;
    } else if (item.price_unavailable) {
        priceHtml = `<span class="item-price">❓</span>`;
    } else {
        priceHtml = `<span class="item-price">$${effPrice.toFixed(2)}</span>`;
    }
    
    // Build store comparison row if both stores available
    const allStores = item.all_stores || {};
    const wooliesData = allStores.woolworths;
    const colesData = allStores.coles;
    let storeCompareHtml = '';
    if (wooliesData && colesData) {
        const wp = wooliesData.eff_price || wooliesData.price;
        const cp = colesData.eff_price || colesData.price;
        if (Number.isFinite(wp) && Number.isFinite(cp)) {
            const wooliesWinner = wp <= cp;
            const saving = Math.abs(wp - cp).toFixed(2);
            storeCompareHtml = `
                <div class="store-compare">
                    <div class="store-compare-row ${wooliesWinner ? 'winner' : ''}">
                        <span class="store-compare-label">🟢 Woolies</span>
                        <span class="store-compare-price">$${wp.toFixed(2)}</span>
                        ${wooliesWinner ? '<span class="winner-badge">✓ Best</span>' : ''}
                    </div>
                    <div class="store-compare-row ${!wooliesWinner ? 'winner' : ''}">
                        <span class="store-compare-label">🔴 Coles</span>
                        <span class="store-compare-price">$${cp.toFixed(2)}</span>
                        ${!wooliesWinner ? `<span class="winner-badge">✓ Save $${saving}</span>` : ''}
                    </div>
                </div>
            `;
        }
    }

    // Resolve all store links through one helper so fallback behavior stays consistent.
    const itemStore = item.store === 'coles' ? 'coles' : 'woolworths';
    const productUrl = escapeHtml(getStoreUrlForStore(item, itemStore));

    const groupBestHtml = buildGroupBestRowHtml(item);

    card.innerHTML = `
        ${imgHtml}
        <div class="item-content">
            <div class="item-card-head">
                <a href="${productUrl}" target="_blank" rel="noopener" class="item-store-link">
                    <div class="store-badge ${storeClass}">${storeClass === 'woolworths' ? 'Woolies' : 'Coles'}</div>
                </a>
                <div class="item-card-badges">
                    ${staleBadge}
                    ${confidenceBadge}
                    <div class="stock-dot ${stockColor}" title="Stock: ${item.stock}"></div>
                </div>
            </div>
            <h3 class="item-title item-title-spaced">${displayName(item.name)}</h3>
            <div class="item-price-row">
                ${priceHtml}
                <span class="item-target" ${targetTooltip}>${(item.target || 0) > 0 ? 'Good deal: $' + item.target.toFixed(2) : '<span class="item-target-empty">No deal price yet</span>'}</span>
            </div>
            ${storeCompareHtml}
            ${groupBestHtml}
            <button type="button" class="add-to-list-btn">
                <i data-feather="plus"></i> Add to shopping list
            </button>
            <div class="chart-container-sm" id="chart-${type}-${index}">
                <canvas></canvas>
            </div>
        </div>
    `;

    const addBtn = card.querySelector('.add-to-list-btn');
    if (addBtn) {
        addBtn.addEventListener('click', () => addToList(item.name, addBtn, item.item_id || null));
    }

    setTimeout(() => {
        if (_history[itemKey(item)] && _history[itemKey(item)].history.length > 0) {
            renderSparkline(`chart-${type}-${index}`, _history[itemKey(item)].history, storeClass);
        }
        feather.replace();
    }, 0);

    return card;
}

function renderPredictions() {
    const section = document.getElementById('predictions-section');
    const grid = document.getElementById('predictions-grid');
    if (!grid) return;
    grid.innerHTML = '';
    
    const now = new Date();

    const predicted = _data.filter(item => {
        // Condition 1: Explicitly low stock (Highest priority)
        if (item.stock === 'low') return true;
        
        // Condition 2: Buy frequency prediction based on category
        if (item.last_purchased) {
            const last = new Date(item.last_purchased);
            const diffDays = (now - last) / (1000 * 60 * 60 * 24);
            
            // Per-category heuristics
            let threshold = 10; // Default
            if (item.type === 'fresh_protein' || item.type === 'fresh_veg') threshold = 4;
            else if (item.type === 'fresh_fridge') threshold = 6;
            else if (item.type === 'pet' || item.type === 'household') threshold = 14;
            
            if (diffDays >= threshold) return true;
        }

        // Condition 3: "Stock Up Alert" - Medium stock but currently on a deep special
        const effPrice = item.eff_price || item.price;
        const isOnSpecial = effPrice <= item.target && !item.price_unavailable;
        if (item.stock === 'medium' && isOnSpecial) return true;

        return false;
    });

    // Sort: Low stock first, then deep specials, then frequency
    predicted.sort((a, b) => {
        if (a.stock === 'low' && b.stock !== 'low') return -1;
        if (b.stock === 'low' && a.stock !== 'low') return 1;
        
        const priceA = a.eff_price || a.price;
        const priceB = b.eff_price || b.price;
        const discountA = (a.target - priceA) / a.target;
        const discountB = (b.target - priceB) / b.target;
        
        if (discountA > discountB) return -1;
        if (discountB > discountA) return 1;
        
        return 0;
    });

    if (predicted.length > 0) {
        section.classList.remove('hidden');
        // Show up to 10 to fill up to 2 rows of 5
        predicted.slice(0, 10).forEach((item, idx) => {
            grid.appendChild(createItemCard(item, idx, 'predicted'));
        });
    } else {
        section.classList.add('hidden');
    }
}


function updateListCount() {
    const el = document.getElementById('list-count');
    if (el) el.textContent = _shoppingList.length;

    // Update mobile badge
    const mobileEl = document.getElementById('mobile-list-count');
    if (mobileEl) mobileEl.textContent = _shoppingList.length;

    const n = _shoppingList.length;
    const mobileToggle = document.getElementById('mobile-toggle-list');
    if (mobileToggle) {
        mobileToggle.setAttribute('aria-label', n === 1 ? 'Shopping list, 1 item' : `Shopping list, ${n} items`);
    }
    const deskToggle = document.getElementById('toggle-list-btn');
    if (deskToggle) {
        deskToggle.setAttribute('aria-label', n === 1 ? 'Shopping list, 1 item' : `Shopping list, ${n} items`);
    }

    // Enable copy button
    const copyBtn = document.getElementById('copy-list-btn');
    if (copyBtn) copyBtn.disabled = _shoppingList.length === 0;

    const goShoppingBtn = document.getElementById('go-shopping-btn');
    if (goShoppingBtn) {
        goShoppingBtn.hidden = isShoppingTripMode() || _shoppingList.length === 0;
        goShoppingBtn.disabled = _shoppingList.length === 0;
    }
    const doneShoppingBtn = document.getElementById('done-shopping-btn');
    if (doneShoppingBtn) {
        doneShoppingBtn.hidden = !isShoppingTripMode();
        doneShoppingBtn.disabled = _shoppingList.length === 0;
    }
    const clearCompletedBtn = document.getElementById('clear-completed-btn');
    if (clearCompletedBtn) {
        const pickedCount = _shoppingList.filter(item => item?.picked).length;
        clearCompletedBtn.hidden = !isShoppingTripMode();
        clearCompletedBtn.disabled = pickedCount === 0;
    }
}

function addToList(itemName, callerBtn, itemId) {
    const item = resolveInventoryItem(itemName, itemId);
    if (!item) return;

    // callerBtn is passed explicitly as `this` from the onclick attribute
    const btn = callerBtn || null;

    // Prevent duplicates
    if (_shoppingList.find(l => shoppingListDedupeMatch(l, item))) {
        if (btn) {
            const originalText = btn.innerHTML;
            btn.innerHTML = '<i data-feather="check"></i> In list!';
            btn.style.background = 'rgba(99,102,241,0.5)';
            feather.replace();
            setTimeout(() => { btn.innerHTML = originalText; btn.style.background = ''; feather.replace(); }, 1500);
        }
        return;
    }
    
    // Factor in quantities
    let qty = 1;
    if (_shopMode === 'big') {
        if (item.type === 'fresh_protein' || item.type === 'meat') qty = 4;
        else if (['pet', 'pantry', 'household', 'frozen'].includes(item.type)) qty = 2;
    }

    const listItem = {
        item_id: item.item_id || null,
        name: item.name,
        price: item.eff_price || item.price,
        qty: qty,
        store: item.store || 'woolworths',
        image: item.local_image || item.image_url || null,
        on_special: item.on_special || false,
        was_price: item.was_price || null,
        picked: false,
        updated_at: new Date().toISOString(),
    };
    
    _shoppingList.push(listItem);
    persistShoppingList();
    updateListCount();
    haptic(12);

    // Visual feedback
    if (btn) {
        const originalText = btn.innerHTML;
        btn.innerHTML = '<i data-feather="check"></i> Added!';
        btn.style.background = 'var(--woolies-green)';
        feather.replace();
        setTimeout(() => {
            btn.innerHTML = originalText;
            btn.style.background = '';
            feather.replace();
        }, 1500);
    }
}

function removeFromList(index) {
    _shoppingList.splice(index, 1);
    persistShoppingList();
    updateListCount();
    renderShoppingList();
}

function renderShoppingList() {
    const container = document.getElementById('shopping-list-items');
    const totalEl = document.getElementById('list-total-price');
    const totalLabelEl = document.getElementById('list-total-label');
    const tripStatusEl = document.getElementById('shopping-trip-status');
    const tripProgressWrapEl = document.getElementById('shopping-trip-progress-wrap');
    const tripProgressBarEl = document.getElementById('shopping-trip-progress-bar');
    const drawer = document.getElementById('list-drawer');
    if (!container) return;
    
    container.innerHTML = '';
    const shoppingMode = isShoppingTripMode();
    if (drawer) drawer.classList.toggle('shopping-trip-mode', shoppingMode);
    if (totalLabelEl) totalLabelEl.textContent = shoppingMode ? 'Left to buy' : 'About';
    let total = 0;
    let pickedCount = 0;
    
    _shoppingList.forEach((item, index) => {
        const isPicked = Boolean(item.picked);
        if (isPicked) pickedCount += 1;
        const itemTotal = (item.price || 0) * item.qty;
        if (!shoppingMode || !isPicked) total += itemTotal;
        
        const div = document.createElement('div');
        div.className = `shopping-item${shoppingMode ? ' shopping-item--trip' : ''}${isPicked ? ' shopping-item--picked' : ''}`;
        const specialBadge = item.on_special && item.was_price
            ? `<span class="save-badge" style="font-size:9px;">SPECIAL</span>` : '';
        const checkboxHtml = shoppingMode
            ? `<label class="shopping-item-check"><input type="checkbox" class="shopping-item-picked" data-index="${index}" ${isPicked ? 'checked' : ''} aria-label="Mark item as picked"><span></span></label>`
            : '';
        div.innerHTML = `
            ${checkboxHtml}
            ${item.image ? `<img src="${item.image}" onerror="this.style.display='none'">` : '<div style="width:40px;height:40px;background:rgba(255,255,255,0.05);border-radius:8px;display:flex;align-items:center;justify-content:center;"><i data-feather="image" style="width:16px;"></i></div>'}
            <div class="shopping-item-info">
                <div class="shopping-item-name">${item.qty}× ${displayName(item.name)} ${specialBadge}</div>
                <div class="shopping-item-price">${item.store === 'woolworths' ? '🟢 Woolies' : '🔴 Coles'} — $${itemTotal.toFixed(2)}</div>
            </div>
            <button class="icon-btn shopping-item-remove" type="button" onclick="removeFromList(${index})" aria-label="Remove ${displayName(item.name)} from shopping list"><i data-feather="trash-2"></i></button>
        `;
        container.appendChild(div);
    });
    
    if (_shoppingList.length === 0) {
        container.innerHTML = '<p style="color:var(--text-muted);text-align:center;margin-top:40px;">Your list is empty.</p>';
    }

    if (shoppingMode) {
        container.querySelectorAll('.shopping-item-picked').forEach(cb => {
            cb.addEventListener('change', (e) => {
                const idx = Number(e.target?.dataset?.index);
                if (!Number.isInteger(idx)) return;
                toggleListItemPicked(idx);
            });
        });
    }

    if (tripStatusEl) {
        if (!shoppingMode || _shoppingList.length === 0) {
            tripStatusEl.hidden = true;
            tripStatusEl.textContent = '';
        } else {
            const leftCount = Math.max(0, _shoppingList.length - pickedCount);
            const pct = Math.round((pickedCount / _shoppingList.length) * 100);
            const savedSoFar = computePickedSavingsAmount();
            const elapsedMins = _shoppingTripStartedAt
                ? Math.max(0, Math.round((Date.now() - Date.parse(_shoppingTripStartedAt)) / 60000))
                : 0;
            const lastSaved = getLastShoppingTripSavedAmount();
            let deltaText = '';
            if (lastSaved > 0) {
                const delta = savedSoFar - lastSaved;
                deltaText = delta > 0
                    ? ` · +$${delta.toFixed(2)} vs last`
                    : ` · $${Math.max(0, -delta).toFixed(2)} to beat last`;
                if (delta > 0 && !_shoppingTripBeatLastToastShown) {
                    _shoppingTripBeatLastToastShown = true;
                    showUiToast(`You beat last trip savings by $${delta.toFixed(2)}.`, 2600);
                }
            }
            tripStatusEl.hidden = false;
            tripStatusEl.textContent = `${leftCount} left · ${pickedCount} done · ${pct}% · saved $${savedSoFar.toFixed(2)}${deltaText} · ${elapsedMins}m`;
            if (pct >= 100) {
                tripStatusEl.textContent += ' · Done shopping when finished';
            }
        }
    }
    if (tripProgressWrapEl && tripProgressBarEl) {
        if (!shoppingMode || _shoppingList.length === 0) {
            tripProgressWrapEl.hidden = true;
            tripProgressBarEl.style.width = '0%';
        } else {
            const pct = Math.round((pickedCount / _shoppingList.length) * 100);
            tripProgressWrapEl.hidden = false;
            tripProgressBarEl.style.width = `${Math.max(0, Math.min(100, pct))}%`;
        }
    }
    
    totalEl.textContent = `$${total.toFixed(2)}`;
    updateListCount();
    if (typeof feather !== 'undefined') feather.replace();
}

function updateLastCheckedDisplay() {
    const el = document.getElementById('last-updated');
    const nextEl = document.getElementById('next-update');
    if (!el || !_lastChecked) return;
    
    const lastDate = new Date(_lastChecked);
    if (isNaN(lastDate.getTime())) {
        el.textContent = _lastChecked;
        return;
    }
    
    // 1. Update Relative "Last Checked"
    const now = new Date();
    const diffMins = Math.floor((now - lastDate) / 60000);
    
    if (diffMins < 1) el.textContent = "Just now";
    else if (diffMins < 60) el.textContent = `${diffMins}m ago`;
    else {
        const hours = Math.floor(diffMins / 60);
        el.textContent = `${hours}h ${diffMins % 60}m ago`;
    }

    // 2. Update Local Time "Next Check"
    if (nextEl) {
        let nextDate;
        if (_nextRun) {
            nextDate = new Date(_nextRun);
        } else {
            // Fallback: Assume 60 min interval from last success
            nextDate = new Date(lastDate.getTime() + (60 * 60 * 1000));
        }
        
        if (!isNaN(nextDate.getTime())) {
            const options = { hour: 'numeric', minute: '2-digit', hour12: true };
            nextEl.textContent = nextDate.toLocaleTimeString(undefined, options);
        }
    }

    // 3. Refresh feather icons for the new structure
    if (typeof feather !== 'undefined') feather.replace();

    syncDealsHeroStatus();
}

async function monitorCloudHealth() {
    const dot = document.getElementById('scrape-status-dot');
    const text = document.getElementById('scrape-status-text');
    if (!dot || !text) return;

    try {
        // Fetch heartbeat from the same origin (GitHub Pages)
        const res = await fetch('heartbeat.json?t=' + Date.now()).catch(() => null);
        if (res && res.ok) {
            const data = await res.json();
            const lastBeat = new Date(data.last_heartbeat);
            const now = new Date();
            const minsAgo = (now - lastBeat) / (1000 * 60);

            if (minsAgo < 35) {
                dot.className = 'status-dot online';
                text.textContent = `Scrape status: healthy (${Math.round(minsAgo)}m ago)`;
            } else {
                dot.className = 'status-dot stale';
                text.textContent = `Scrape status: stale (${Math.round(minsAgo)}m ago)`;
            }

            // Sync the 'Last Checked' header display with the cloud heartbeat
            _lastChecked = data.last_heartbeat;
            _nextRun = data.next_run;
            updateLastCheckedDisplay();
            return;
        }
        dot.className = 'status-dot offline';
        text.textContent = 'Scrape status: unavailable';
    } catch (e) {
        dot.className = 'status-dot offline';
        text.textContent = 'Scrape status: unavailable';
    }
}

function renderColaBattle() {
    const colaItems = _data.filter(
        i =>
            (i.compare_group === 'cola' || i.name.toLowerCase().includes('pepsi') || i.name.toLowerCase().includes('coke')) &&
            !i.price_unavailable
    );
    const container = document.getElementById('cola-battle-container') || document.querySelector('.cola-card');
    if (!container) return;

    const buckets = {
        noSugar: { pepsi: [], coke: [] },
        classic: { pepsi: [], coke: [] },
    };

    for (const item of colaItems) {
        if (!item.all_stores || Object.keys(item.all_stores).length === 0) continue;
        for (const c of expandItemStoreCandidates(item)) {
            const cls = classifyColaCandidate(c.item);
            if (!cls) continue;
            const perLitre = colaCandidatePerLitre(c);
            if (!(typeof perLitre === 'number' && Number.isFinite(perLitre) && perLitre > 0)) continue;
            c.per_litre = perLitre;
            buckets[cls.category][cls.brand].push(c);
        }
    }

    const pickWinner = arr => {
        if (!arr || arr.length === 0) return null;
        return [...arr].sort(compareColaCandidates)[0];
    };

    const renderBattleRow = (title, pepsiC, cokeC) => {
        const pP = pepsiC ? pepsiC.per_litre : Infinity;
        const cP = cokeC ? cokeC.per_litre : Infinity;
        const pWinner = pP < cP;
        const cWinner = cP < pP;

        const getStoreUrl = win => {
            if (!win) return null;
            return getStoreUrlForStore(win.item, win.store, { preferSearchForWoolworthsPdp: true });
        };

        const getStoreBadge = win => {
            if (!win) return '';
            return win.store === 'woolworths'
                ? '<span class="fighter-store-badge woolies">Woolworths</span>'
                : '<span class="fighter-store-badge coles">Coles</span>';
        };

        const isOnSpecialWin = win => {
            if (!win) return false;
            const sd = (win.item.all_stores || {})[win.store];
            return (sd && sd.on_special) || win.item.on_special;
        };

        const getActiveStore = win => (win ? win.store : 'woolworths');

        const renderFighter = (brand, win, isWinner) => {
            const item = win ? win.item : null;
            const url = getStoreUrl(win);
            const priceLabel = win ? `$${win.per_litre.toFixed(2)}/L` : '—';
            const storeBadge = getStoreBadge(win);
            const special = isOnSpecialWin(win) ? '<span class="fighter-on-special">🔥 On Special</span>' : '';
            const stale = getStaleBadge(item, true);
            const winnerClass = isWinner ? `winner winner-${getActiveStore(win)}` : '';
            const addBtn = item
                ? `<button type="button" class="fighter-add-btn" data-item-name="${encodeURIComponent(item.name || '')}" data-item-id="${encodeURIComponent(item.item_id || '')}"><i data-feather="plus"></i> Add</button>`
                : '';
            const viewBtn = url
                ? `<a class="fighter-link-btn" href="${url}" target="_blank" rel="noopener">View</a>`
                : '';
            return `
                <div class="fighter ${winnerClass}">
                    ${isWinner ? `<div class="winner-badge">🏆 CHEAPEST</div>` : ''}
                    <div class="fighter-brand">${brand}</div>
                    <div class="fighter-price">${priceLabel}</div>
                    <div class="fighter-product">${item ? displayName(item.name) : '—'}</div>
                    <div class="fighter-meta">${storeBadge}${special}${stale}</div>
                    <div class="fighter-actions">${addBtn}${viewBtn}</div>
                </div>
            `;
        };

        return `
            <div class="battle-arena">
                <div class="arena-title">${title}</div>
                <div class="arena-fighters">
                    ${renderFighter('Pepsi', pepsiC, pWinner)}
                    <div class="battle-vs">VS</div>
                    ${renderFighter('Coke', cokeC, cWinner)}
                </div>
            </div>
        `;
    };

    container.innerHTML = `
        <div class="cola-battle-header">
            <i data-feather="zap"></i> Coke vs Pepsi (best $/L)
        </div>
        ${renderBattleRow('No sugar', pickWinner(buckets.noSugar.pepsi), pickWinner(buckets.noSugar.coke))}
        <div class="arena-divider"></div>
        ${renderBattleRow('Classic', pickWinner(buckets.classic.pepsi), pickWinner(buckets.classic.coke))}
    `;
    container.querySelectorAll('.fighter-add-btn').forEach(btn => {
        btn.addEventListener('click', (e) => {
            e.preventDefault();
            e.stopPropagation();
            const name = decodeURIComponent(btn.dataset.itemName || '');
            const id = decodeURIComponent(btn.dataset.itemId || '');
            if (!name) return;
            addToList(name, btn, id || null);
        });
    });
    feather.replace();
}

function renderSpecials() {
    const grid = document.getElementById('specials-grid');
    grid.innerHTML = '';

    const displayItems = _data.filter(item => {
        const matchesStore = _currentFilter === 'all' || item.store === _currentFilter;
        const matchesCat = _currentCatFilter === 'all' || item.type === _currentCatFilter;
        const matchesSearch = !_searchText || item.name.toLowerCase().includes(_searchText) || displayName(item.name).toLowerCase().includes(_searchText);
        const effPrice = item.eff_price || item.price;
        // Store says "on special" OR price is at/below our target
        const isSpecial = item.on_special || (item.target > 0 && effPrice <= item.target && !item.price_unavailable);
        return matchesStore && matchesCat && matchesSearch && isSpecial;
    });

    // Sorting Logic
    displayItems.sort((a, b) => {
        if (_currentSort === 'name') return a.name.localeCompare(b.name);
        
        const priceA = a.eff_price || a.price;
        const priceB = b.eff_price || b.price;
        
        if (_currentSort === 'price') return priceA - priceB;
        
        if (_currentSort === 'discount') {
            // Compare was_price to shelf price (not eff_price) to avoid unit mismatch
            const shelfA = a.price || priceA;
            const shelfB = b.price || priceB;
            const refA = a.was_price && a.was_price > shelfA ? a.was_price : (a.target || shelfA);
            const refB = b.was_price && b.was_price > shelfB ? b.was_price : (b.target || shelfB);
            const savingsA = (refA - shelfA) / refA;
            const savingsB = (refB - shelfB) / refB;
            return savingsB - savingsA;
        }
        return 0;
    });

    if (displayItems.length === 0) {
        // B: Show near-miss fallback instead of plain empty state
        const nearMisses = _data
            .filter(item => {
                if (!item.target || !item.price) return false;
                const ratio = item.price / item.target;
                return ratio > 1 && ratio <= 1.10; // within 10% above target
            })
            .sort((a, b) => (a.price / a.target) - (b.price / b.target))
            .slice(0, 6);

        if (nearMisses.length > 0) {
            grid.innerHTML = `
                <div class="no-deals-state" style="grid-column:1/-1;">
                    <p>No specials match what you’ve selected — here are a few that are almost at your good-deal price:</p>
                    <div class="no-deals-near-title">🎯 Worth watching</div>
                </div>`;
            nearMisses.forEach((item, i) => {
                const card = createItemCard(item, i);
                card.classList.add('near-miss-card');
                grid.appendChild(card);
            });
        } else {
            grid.innerHTML = '<p style="color: var(--text-muted); grid-column: 1/-1; padding: 2rem; text-align:center;">Nothing in your filters right now — try another category or check back after Wednesday’s new specials. 🗓️</p>';
        }
        if (typeof renderPagination === 'function') renderPagination(0);
        return;
    }

    // Pagination Slicing
    const totalItems = displayItems.length;
    const startIndex = (_currentPage - 1) * _itemsPerPage;
    const endIndex = startIndex + _itemsPerPage;
    const pagedItems = displayItems.slice(startIndex, endIndex);

    pagedItems.forEach((item, index) => {
        const card = createItemCard(item, startIndex + index);
        grid.appendChild(card);
    });

    renderPagination(totalItems);

    if (typeof feather !== 'undefined') feather.replace();
}

function renderPagination(totalItems) {
    const container = document.getElementById('pagination-controls');
    if (!container) return;
    container.innerHTML = '';

    const totalPages = Math.ceil(totalItems / _itemsPerPage);
    if (totalPages <= 1) return;

    // Previous Button
    const prevBtn = document.createElement('button');
    prevBtn.className = 'pagination-btn';
    prevBtn.disabled = _currentPage === 1;
    prevBtn.innerHTML = '<i data-feather="chevron-left"></i> Prev';
    prevBtn.onclick = () => {
        _currentPage--;
        renderSpecials();
        window.scrollTo({ top: 0, behavior: 'smooth' });
    };
    container.appendChild(prevBtn);

    // Page Info
    const info = document.createElement('span');
    info.className = 'pagination-info';
    info.textContent = `Page ${_currentPage} of ${totalPages}`;
    container.appendChild(info);

    // Next Button
    const nextBtn = document.createElement('button');
    nextBtn.className = 'pagination-btn';
    nextBtn.disabled = _currentPage === totalPages;
    nextBtn.innerHTML = 'Next <i data-feather="chevron-right"></i>';
    nextBtn.onclick = () => {
        _currentPage++;
        renderSpecials();
        window.scrollTo({ top: 0, behavior: 'smooth' });
    };
    container.appendChild(nextBtn);

    if (typeof feather !== 'undefined') feather.replace();
}


// ── D: Collapsible Master Tracklist ──────────────────────────────────────
function toggleMasterTable() {
    const btn = document.getElementById('master-table-toggle');
    const body = document.getElementById('master-table-body');
    if (!btn || !body) return;
    const isOpen = btn.getAttribute('aria-expanded') === 'true';
    if (isOpen) {
        body.style.display = 'none';
        btn.setAttribute('aria-expanded', 'false');
    } else {
        body.style.display = 'block';
        btn.setAttribute('aria-expanded', 'true');
        // Lazy-render: check both table (desktop) and card list (mobile)
        const isMobile = window.matchMedia('(max-width: 768px)').matches;
        const tbody = document.getElementById('all-items-tbody');
        const mobileList = document.getElementById('all-items-list');
        const needsRender = isMobile
            ? (mobileList && mobileList.children.length === 0)
            : (tbody && tbody.children.length === 0);
        if (needsRender) renderAllItems();
        if (typeof feather !== 'undefined') feather.replace();
    }
}

// ── Mobile Priority Rail ──────────────────────────────────────────────────────
function renderMobilePriorityRail() {
    const rail = document.getElementById('mobile-priority-rail');
    if (!rail) return;
    const isMobile = isMobileViewport();
    rail.classList.toggle('desktop-priority-rail', !isMobile);

    const priorityItems = getPriorityItems(_data, 6);
    const topDeals = getTopDeals(_data, 5);
    const fallbackDeals = (_data || [])
        .map(item => ({ ...item, _snap: computeItemSavingsSnapshot(item) }))
        .filter(item => item._snap.isDeal && !item.price_unavailable)
        .sort((a, b) => b._snap.savedDollar - a._snap.savedDollar)
        .slice(0, 5);
    if (priorityItems.length === 0 && topDeals.length === 0 && fallbackDeals.length === 0) {
        rail.innerHTML = '';
        return;
    }

    let html = '<div class="priority-rail-inner">';

    if (priorityItems.length > 0) {
        html += `<div class="priority-rail-section">
            <div class="priority-rail-title">🔥 Buy Now <span style="font-size:10px;background:rgba(239,68,68,0.2);color:#fca5a5;padding:2px 7px;border-radius:100px;">${priorityItems.length}</span></div>
            ${priorityItems.map(item => {
                const price = item._snap.eff;
                const saveStr = item._snap.savePct > 0 ? `-${item._snap.savePct}%` : '🎯';
                return `<div class="buy-now-row" onclick="openStockModal(${JSON.stringify(item.name)}, ${item.item_id ? JSON.stringify(item.item_id) : 'null'})">
                    <div class="buy-now-stock-dot"></div>
                    <div class="buy-now-info">
                        <div class="buy-now-name">${displayName(item.name)}</div>
                        <div class="buy-now-price">$${price.toFixed(2)}</div>
                    </div>
                    <div class="buy-now-save">${saveStr}</div>
                </div>`;
            }).join('')}
        </div>`;
    }

    if (topDeals.length > 0) {
        const medals = ['🥇','🥈','🥉','4️⃣','5️⃣'];
        html += `<div class="priority-rail-section">
            <div class="priority-rail-title">🏆 Top Deals</div>
            ${topDeals.map((item, i) => {
                const name = displayName(item.name);
                return `<div class="top5-row" onclick="document.getElementById('dashboard-search').value='${item.name.substring(0,15)}'; _searchText='${item.name.substring(0,15).toLowerCase()}'; _currentPage=1; renderDashboard();" title="${name}">
                    <span class="top5-medal">${medals[i]}</span>
                    <div class="top5-info">
                        <div class="top5-name">${name.length > 26 ? name.substring(0,26)+'…' : name}</div>
                        <div class="top5-price top5-price-${item.store === 'coles' ? 'coles' : 'woolies'}">$${item._snap.eff.toFixed(2)}</div>
                    </div>
                    <span class="top5-save">-${item._snap.savePct}%</span>
                </div>`;
            }).join('')}
        </div>`;
    }

    // Keep at least two high-signal panels visible when possible.
    // If one panel is missing due sparse low-stock/savings data, provide a fallback list.
    if (priorityItems.length > 0 && topDeals.length === 0 && fallbackDeals.length > 0) {
        html += `<div class="priority-rail-section">
            <div class="priority-rail-title">✨ Worth checking</div>
            ${fallbackDeals.map((item, i) => {
                const name = displayName(item.name);
                return `<div class="top5-row" onclick="document.getElementById('dashboard-search').value='${item.name.substring(0,15)}'; _searchText='${item.name.substring(0,15).toLowerCase()}'; _currentPage=1; renderDashboard();" title="${name}">
                    <span class="top5-medal">${i + 1}</span>
                    <div class="top5-info">
                        <div class="top5-name">${name.length > 26 ? name.substring(0,26)+'…' : name}</div>
                        <div class="top5-price top5-price-${item.store === 'coles' ? 'coles' : 'woolies'}">$${item._snap.eff.toFixed(2)}</div>
                    </div>
                    <span class="top5-save">$${item._snap.savedDollar.toFixed(2)}</span>
                </div>`;
            }).join('')}
        </div>`;
    }

    html += '</div>';
    rail.innerHTML = html;
    // #region agent log
    fetch('http://127.0.0.1:7716/ingest/1692efee-81d9-413c-bd30-574d3de06991',{method:'POST',headers:{'Content-Type':'application/json','X-Debug-Session-Id':'0b485d'},body:JSON.stringify({sessionId:'0b485d',runId:'post-fix',hypothesisId:'H3',location:'docs/app.js:renderMobilePriorityRail',message:'priority rail render result',data:{isMobile:isMobileViewport(),railHasDesktopClass:rail.classList.contains('desktop-priority-rail'),priorityItems:priorityItems.length,topDeals:topDeals.length,fallbackDeals:fallbackDeals.length,renderedSections:rail.querySelectorAll('.priority-rail-section').length},timestamp:Date.now()})}).catch(()=>{});
    // #endregion
}

// ── F: Buy Now Priority View ──────────────────────────────────────────────
function renderBuyNow() {
    const card = document.getElementById('buy-now-card');
    const list = document.getElementById('buy-now-list');
    const badge = document.getElementById('buy-now-count');
    if (!card || !list) return;

    const priorityItems = getPriorityItems(_data, 8);

    if (priorityItems.length === 0) {
        card.style.display = 'none';
        return;
    }

    card.style.display = 'block';
    badge.textContent = priorityItems.length;
    list.innerHTML = priorityItems.map(item => {
        const price = item._snap.eff;
        const wasPr = item.was_price;
        const shelf = item._snap.shelf;
        const saveStr = item._snap.savePct > 0 ? `-${item._snap.savePct}%` : '🎯';
        const priceStr = price ? `$${price.toFixed(2)}` : '—';
        return `
                <div class="buy-now-row" onclick="openStockModal(${JSON.stringify(item.name)}, ${item.item_id ? JSON.stringify(item.item_id) : 'null'})">
                <div class="buy-now-stock-dot"></div>
                <div class="buy-now-info">
                    <div class="buy-now-name">${displayName(item.name)}</div>
                    <div class="buy-now-price">${priceStr}${wasPr ? ` <span style="color:var(--text-muted);font-weight:400;text-decoration:line-through;">$${wasPr.toFixed(2)}</span>` : ''}</div>
                </div>
                <div class="buy-now-save">${saveStr}</div>
            </div>`;
    }).join('');
}

function renderAllItems() {
    const isMobile = isMobileViewport();

    const filteredData = _data.filter(item => {
        const matchesStore = _currentFilter === 'all' || item.store === _currentFilter;
        const matchesSearch = !_searchText || item.name.toLowerCase().includes(_searchText) || displayName(item.name).toLowerCase().includes(_searchText);
        return matchesStore && matchesSearch;
    }).sort((a, b) => displayName(a.name).localeCompare(displayName(b.name)));

    if (isMobile) {
        // ── Mobile: render as compact rows ────────────────────────────────
        const list = document.getElementById('all-items-list');
        if (!list) return;
        list.innerHTML = '';

        filteredData.forEach((item, index) => {
            const effPrice = item.eff_price || item.price;
            const isSpecial = item.on_special || ((item.target || 0) > 0 && effPrice <= item.target && !item.price_unavailable);
            const stockColor = item.stock === 'low' ? 'low' : (item.stock === 'medium' ? 'medium' : 'full');
            const storeLabel = item.store === 'woolworths' ? '🟢 W' : '🔴 C';

            let priceHtml;
            const itemShelf = item.price || effPrice;
            if (item.on_special && item.was_price && item.was_price > itemShelf) {
                const savePct = Math.round((1 - itemShelf / item.was_price) * 100);
                priceHtml = `<span style="color:var(--woolies-green);">$${effPrice.toFixed(2)}</span> <span style="font-size:10px;opacity:0.5;text-decoration:line-through;">$${item.was_price.toFixed(2)}</span>`;
            } else {
                priceHtml = item.price_unavailable ? '❓' : `$${effPrice.toFixed(2)}`;
            }

            const row = document.createElement('div');
            row.className = 'tracklist-row';
            row.onclick = () => { haptic(8); openStockModal(item.name, item.item_id || null); };
            row.innerHTML = `
                <div class="tracklist-row-info">
                    <div class="tracklist-row-name">${displayName(item.name)}${isSpecial ? ' 🔥' : ''}</div>
                    <div class="tracklist-row-sub">
                        <span>${storeLabel}</span>
                        <div class="stock-dot ${stockColor}" title="${item.stock}"></div>
                        ${(item.target || 0) > 0 ? `<span style="opacity:0.5;">Deal $${item.target.toFixed(2)}</span>` : ''}
                    </div>
                </div>
                <div>
                    <div class="tracklist-row-price">${priceHtml}</div>
                    <div class="tracklist-sparkline" id="chart-mob-${index}"><canvas></canvas></div>
                </div>
            `;
            list.appendChild(row);

            if (_history[itemKey(item)] && _history[itemKey(item)].history.length > 0) {
                renderSparkline(`chart-mob-${index}`, _history[itemKey(item)].history, item.store);
            }
        });
        return;
    }

    // ── Desktop: existing table render ────────────────────────────────────
    const tbody = document.getElementById('all-items-tbody');
    if (!tbody) return;
    tbody.innerHTML = '';

    filteredData.forEach((item, index) => {
        const tr = document.createElement('tr');
        const effPrice = item.eff_price || item.price;
        const isSpecial = item.on_special || ((item.target || 0) > 0 && effPrice <= item.target && !item.price_unavailable);
        const stockColor = item.stock === 'low' ? 'low' : (item.stock === 'medium' ? 'medium' : 'full');
        
        let priceCell;
        const itemShelf = item.price || effPrice;
        if (item.on_special && item.was_price && item.was_price > itemShelf) {
            const savePct = Math.round((1 - itemShelf / item.was_price) * 100);
            priceCell = `$${effPrice.toFixed(2)} <span class="was-price">$${item.was_price.toFixed(2)}</span> <span class="save-badge">-${savePct}%</span>`;
        } else {
            priceCell = item.price_unavailable ? '❓' : `$${effPrice.toFixed(2)}`;
        }

        tr.innerHTML = `
            <td>
                <span style="font-weight:600;">${displayName(item.name)}</span>
                ${isSpecial ? ' 🔥' : ''}
            </td>
            <td><span class="store-badge ${item.store}">${item.store === 'woolworths' ? 'W' : 'C'}</span></td>
            <td>
                <div class="stock-clickable" onclick="openStockModal(${JSON.stringify(item.name)}, ${item.item_id ? JSON.stringify(item.item_id) : 'null'})">
                    <div class="stock-dot ${stockColor}"></div> ${item.stock}
                </div>
            </td>
            <td>${priceCell}</td>
            <td>${(item.target || 0) > 0 ? '$' + item.target.toFixed(2) : '<span style="opacity:0.4">—</span>'}</td>
            <td>
                <div class="chart-container-td" id="chart-td-${index}">
                    <canvas></canvas>
                </div>
            </td>
        `;
        tbody.appendChild(tr);

        if (_history[itemKey(item)] && _history[itemKey(item)].history.length > 0) {
            renderSparkline(`chart-td-${index}`, _history[itemKey(item)].history, item.store);
        }
    });
}

function openStockModal(itemName, itemId) {
    const item = resolveInventoryItem(itemName, itemId);
    if (!item) return;

    _focusBeforeStockModal = document.activeElement;
    _selectedItemForModal = item;
    document.getElementById('modal-title').textContent = displayName(item.name);
    document.getElementById('target-input-modal').value = item.target;
    
    document.querySelectorAll('.stock-btn').forEach(btn => {
        btn.classList.toggle('active', btn.dataset.level === item.stock);
    });
    
    document.getElementById('overlay-modal').style.display = 'flex';
    setTimeout(() => {
        document.querySelector('#overlay-modal .stock-btn.active')?.focus()
            || document.getElementById('target-input-modal')?.focus()
            || document.getElementById('modal-cancel')?.focus();
    }, 0);
}

function closeModal() {
    document.getElementById('overlay-modal').style.display = 'none';
    const prev = _focusBeforeStockModal;
    _focusBeforeStockModal = null;
    prev?.focus?.();
}

async function saveItemChanges() {
    const activeStock = document.querySelector('.stock-btn.active')?.dataset.level;
    const newTarget = parseFloat(document.getElementById('target-input-modal').value);
    
    if (!_selectedItemForModal || !activeStock) return;
    
    try {
        const base = getStockWriteBase();
        if (!base) {
            alert('Set your Cloud write API in Settings first.');
            return;
        }
        const headers = { 'Content-Type': 'application/json' };
        if (usesCloudWrite() && _writeApiSecret) {
            headers['X-WooliesBot-Secret'] = _writeApiSecret;
        }
        const response = await fetch(`${base}/update_stock`, {
            method: 'POST',
            headers,
            body: JSON.stringify({
                name: _selectedItemForModal.name,
                item_id: _selectedItemForModal.item_id || null,
                stock: activeStock,
                target: Number.isFinite(newTarget) ? newTarget : undefined,
            })
        });

        if (response.ok) {
            haptic(15);
            _selectedItemForModal.stock = activeStock;
            _selectedItemForModal.target = newTarget;
            renderDashboard();
            closeModal();
        } else if (response.status === 401 || response.status === 403) {
            alert('Write rejected — check the write secret in Settings (must match the Worker).');
        } else if (!response.ok) {
            const errText = await response.text().catch(() => '');
            alert(`Could not save (${response.status}). ${errText.slice(0, 120)}`);
        }
    } catch (e) {
        alert('Could not reach the cloud write API. Check the Worker URL and your connection.');
    }
}

function renderAnalytics() {
    syncAnalyticsViewportState();
    // Collect data
    const categories = {};
    // FIXED: Use price_history (has 5 months of data) NOT scrape_history (only 1 entry = today)
    const priceIndexByMonth = {}; // YYYY-MM -> { sum: X, count: Y }
    let totalRealizedSavings = 0;
    let itemsBoughtAtTarget = 0;

    // 1. Build Price Index from price_history + compute volatility from price_history
    //    scrape_history only has 1 entry per item so it's useless for trends/volatility.
    _data.forEach(item => {
        const target = item.target || 0;
        const ph = item.price_history || [];

        // ── Volatility from price_history (the real historical data) ───────
        const phPrices = ph.map(h => h.price).filter(p => p > 0 && p < 1000);
        if (phPrices.length > 2) {
            const avg = phPrices.reduce((a, b) => a + b) / phPrices.length;
            const variance = phPrices.reduce((a, b) => a + Math.pow(b - avg, 2), 0) / phPrices.length;
            const stdDev = Math.sqrt(variance);
            _volatility[itemKey(item)] = (stdDev / avg) * 100;
        }

        // ── Price trends from price_history ────────────────────────────────
        ph.forEach(h => {
            const p = h.price;
            if (!p || p > 1000) return;

            // Realized savings: every time price was at or below target
            if (target > 0 && p <= target) {
                const estimatedShelf = target * 1.4;
                totalRealizedSavings += Math.max(0, estimatedShelf - p);
                itemsBoughtAtTarget++;
            }

            const month = h.date.substring(0, 7); // YYYY-MM
            if (!priceIndexByMonth[month]) priceIndexByMonth[month] = { sum: 0, count: 0 };
            priceIndexByMonth[month].sum += p;
            priceIndexByMonth[month].count++;
        });

        // Also accumulate from scrape_history (current prices) into the current month
        // so today's snapshot is always included
        const sh = item.scrape_history || [];
        sh.forEach(h => {
            if (!h.price || h.price > 1000) return;
            const month = h.date.substring(0, 7);
            if (!priceIndexByMonth[month]) priceIndexByMonth[month] = { sum: 0, count: 0 };
            // Only add if this day isn't already covered by price_history
            const alreadyCovered = ph.some(p2 => p2.date === h.date);
            if (!alreadyCovered) {
                priceIndexByMonth[month].sum += h.price;
                priceIndexByMonth[month].count++;
            }
        });
    });

    // 2. Category Split and Brand Premium from current live prices
    const brandPrices = { 'Private Label': { sum: 0, count: 0 }, 'Name Brand': { sum: 0, count: 0 } };
    _data.forEach(item => {
        const cat = item.subcategory || item.type || 'pantry';
        const price = item.eff_price || item.price || 0;
        if (price > 0 && price < 1000) {
            categories[cat] = (categories[cat] || 0) + price;
            const brandType = item.brand === 'Private Label' ? 'Private Label' : 'Name Brand';
            brandPrices[brandType].sum += price;
            brandPrices[brandType].count++;
        }
    });

    // 3. Efficiency — % of tracked items currently at or below their target price
    //    (more meaningful than last_purchased which barely has any data)
    const itemsWithTargets = _data.filter(i => (i.target || 0) > 0).length;
    const itemsAtTarget = _data.filter(i => {
        const ep = i.eff_price || i.price || 0;
        return (i.target || 0) > 0 && ep <= i.target && !i.price_unavailable;
    }).length;
    const efficiency = itemsWithTargets > 0 ? (itemsAtTarget / itemsWithTargets) * 100 : 0;
    const isMobile = isMobileViewport();
    const isCompact = isCompactViewport();
    document.body.dataset.analyticsViewport = isMobile ? 'mobile' : 'desktop';

    // 4. Total historical savings: add was_price-based savings for current specials
    _data.forEach(item => {
        const shelf = item.price || 0;
        if (item.on_special && item.was_price && item.was_price > shelf) {
            totalRealizedSavings += (item.was_price - shelf);
        }
    });

    document.getElementById('analytic-savings-val').textContent = `$${totalRealizedSavings.toFixed(2)}`;
    document.getElementById('analytic-efficiency-val').textContent = `${efficiency.toFixed(0)}% (${itemsAtTarget} of ${itemsWithTargets} at or below your deal price)`;

    // Charts
    const spendingCtx = document.getElementById('spending-chart')?.getContext('2d');
    const categoryCtx = document.getElementById('category-chart')?.getContext('2d');

    if (spendingCtx) {
        const sortedDates = Object.keys(priceIndexByMonth).sort();
        const spendHistoryEmpty = sortedDates.length === 0;
        setInsightsChartEmpty('spending-chart', spendHistoryEmpty,
            'Not enough price history yet',
            'Need several months of price_history per item. Charts fill in as WooliesBot records prices.');

        if (spendHistoryEmpty) {
            if (window.mySpendingChart) {
                window.mySpendingChart.destroy();
                window.mySpendingChart = null;
            }
        } else {
        const chartData = sortedDates.map(d => priceIndexByMonth[d].sum / priceIndexByMonth[d].count);

        // Second dataset: count how many distinct items hit their target each month
        const monthSpecialsCount = {};
        const monthItemCount = {};
        _data.forEach(item => {
            const ph = item.price_history || [];
            const tgt = item.target || 0;
            const seenMonths = new Set();
            ph.forEach(h => {
                const m = h.date.substring(0, 7);
                if (!monthItemCount[m]) monthItemCount[m] = new Set();
                monthItemCount[m].add(item.name);
                if (tgt > 0 && h.price <= tgt) {
                    if (!monthSpecialsCount[m]) monthSpecialsCount[m] = new Set();
                    monthSpecialsCount[m].add(item.name);
                }
            });
        });
        const specialsRateLine = sortedDates.map(m => {
            const total = monthItemCount[m] ? monthItemCount[m].size : 0;
            const atTarget = monthSpecialsCount[m] ? monthSpecialsCount[m].size : 0;
            return total > 0 ? parseFloat(((atTarget / total) * 100).toFixed(1)) : 0;
        });

        // Human-readable month labels e.g. "Dec '25"
        const MONTH_SHORT = ['Jan','Feb','Mar','Apr','May','Jun','Jul','Aug','Sep','Oct','Nov','Dec'];
        const niceLabels = sortedDates.map(d => {
            const [yr, mo] = d.split('-');
            return `${MONTH_SHORT[parseInt(mo) - 1]} '${yr.slice(2)}`;
        });

        // Destroy existing chart if any
        if (window.mySpendingChart) window.mySpendingChart.destroy();
        
        window.mySpendingChart = new Chart(spendingCtx, {
            type: 'line',
            data: {
                labels: niceLabels,
                datasets: [
                    {
                        label: 'Avg Item Price ($)',
                        data: chartData,
                        borderColor: '#818cf8',
                        backgroundColor: 'rgba(129, 140, 248, 0.1)',
                        fill: true,
                        tension: 0.4,
                        borderWidth: 3,
                        pointRadius: isCompact ? 2.5 : (isMobile ? 3 : 5),
                        pointBackgroundColor: '#818cf8',
                        yAxisID: 'yPrice',
                    },
                    {
                        label: 'At or below deal price (%)',
                        data: specialsRateLine,
                        borderColor: '#10b981',
                        backgroundColor: 'rgba(16, 185, 129, 0.05)',
                        fill: false,
                        tension: 0.4,
                        borderWidth: 2,
                        borderDash: [5, 4],
                        pointRadius: isCompact ? 2 : (isMobile ? 2.5 : 4),
                        pointBackgroundColor: '#10b981',
                        yAxisID: 'yPct',
                    }
                ]
            },
            options: { 
                responsive: true, 
                maintainAspectRatio: false,
                interaction: { mode: 'index', intersect: false },
                plugins: {
                    legend: { labels: { color: '#9ca3af', padding: isMobile ? 10 : 20, usePointStyle: true } },
                    tooltip: {
                        callbacks: {
                            label: (ctx) => ctx.datasetIndex === 0
                                ? `Avg Price: $${ctx.parsed.y.toFixed(2)}`
                                : `At or below deal: ${ctx.parsed.y.toFixed(1)}%`
                        }
                    }
                },
                scales: {
                    x: {
                        grid: { color: 'rgba(255,255,255,0.05)' },
                        ticks: { color: '#9ca3af', font: { size: isCompact ? 9 : (isMobile ? 10 : 11) } }
                    },
                    yPrice: {
                        type: 'linear',
                        position: 'left',
                        beginAtZero: false,
                        grid: { color: 'rgba(255,255,255,0.05)' },
                        ticks: { callback: val => '$' + val.toFixed(2), color: '#818cf8', font: { size: isCompact ? 9 : (isMobile ? 10 : 11) } }
                    },
                    yPct: {
                        type: 'linear',
                        position: 'right',
                        min: 0,
                        max: 100,
                        grid: { drawOnChartArea: false },
                        ticks: { callback: val => val + '%', color: '#34d399', font: { size: isCompact ? 9 : (isMobile ? 10 : 11) } }
                    }
                }
            }
        });
        }
    }

    if (categoryCtx) {
        const labels = Object.keys(categories);
        const dataValues = Object.values(categories);
        const catSum = dataValues.reduce((a, b) => a + b, 0);
        const categoryEmpty = labels.length === 0 || catSum <= 0;
        setInsightsChartEmpty('category-chart', categoryEmpty,
            'No category spend yet',
            'Once items have prices and categories, this doughnut chart shows where money clusters.');

        if (categoryEmpty) {
            if (window.myCategoryChart) {
                window.myCategoryChart.destroy();
                window.myCategoryChart = null;
            }
        } else {
        
        if (window.myCategoryChart) window.myCategoryChart.destroy();

        window.myCategoryChart = new Chart(categoryCtx, {
            type: 'doughnut',
            data: {
                labels: labels.map(l => l.replace('_', ' ').toUpperCase()),
                datasets: [{
                    data: dataValues,
                    backgroundColor: ['#10b981', '#ef4444', '#f59e0b', '#6366f1', '#a855f7', '#ec4899', '#06b6d4', '#8b5cf6'],
                    borderWidth: 0,
                    hoverOffset: 15
                }]
            },
            options: { 
                responsive: true, 
                maintainAspectRatio: false,
                plugins: {
                    legend: { 
                        position: isMobile ? 'bottom' : 'right',
                        labels: { color: '#9ca3af', font: { size: isCompact ? 8 : (isMobile ? 9 : 10) } } 
                    }
                },
                cutout: '70%'
            }
        });
        }
    }

    renderDeeperInsights(brandPrices);
    renderTargetIntelligence();

    // ── New Analytics Widgets ──────────────────────────────────────────────
    renderSavingsGauge();
    renderWeeklySavings();
    renderWeeklyActionPlan();
    renderShoppingTimeInsights();
    renderCategoryInflation();
    renderDealHeatmap();
    renderVolatilityLeaderboard();
    renderBestTimeToBuy();
    renderPantryHealthScore();
    renderCompareGroupDiagnostics();

    if (typeof feather !== 'undefined') feather.replace();
    resizeInsightsCharts();
}

/** After tab switch, Chart.js may have wrong dimensions if the tab was `display:none`. */
function resizeInsightsCharts() {
    requestAnimationFrame(() => {
        requestAnimationFrame(() => {
            try {
                window.mySpendingChart?.resize();
                window.myCategoryChart?.resize();
            } catch (e) { /* ignore */ }
        });
    });
}

function renderTargetIntelligence() {
    const container = document.getElementById('target-intelligence-container');
    if (!container) return;

    // Tally confidence levels
    let high = 0, med = 0, low = 0, noMeta = 0;
    const lowConfItems = [];
    const recentChanges = [];

    _data.forEach(item => {
        const conf = item.target_confidence;
        if (!conf) { noMeta++; return; }
        if (conf === 'high') high++;
        else if (conf === 'medium') { med++; }
        else {
            low++;
            if (item.target_data_points === 0) {
                lowConfItems.push(item);
            }
        }
        // Detect recently-changed targets (target_updated = today)
        const today = new Date().toISOString().slice(0, 10);
        if (item.target_updated === today && item.target_method && item.target_method !== 'unchanged') {
            recentChanges.push(item);
        }
    });

    const total = high + med + low + noMeta || 1;
    const highPct = Math.round((high / total) * 100);
    const medPct  = Math.round((med  / total) * 100);
    const lowPct  = Math.round((low  / total) * 100);

    // Needs-data: items with zero price points that have a Woolworths URL
    const needsData = _data.filter(i => (i.target_data_points || 0) === 0 && i.woolworths).length;

    container.innerHTML = `
        <div class="target-intel-header">
            <i data-feather="target"></i>
            <span>Deal price confidence</span>
        </div>

        <div class="target-confidence-bar-wrap">
            <div class="target-conf-bar">
                <div class="tcb-fill high"  style="width:${highPct}%" title="${high} high-confidence"></div>
                <div class="tcb-fill med"   style="width:${medPct}%"  title="${med} medium-confidence"></div>
                <div class="tcb-fill low"   style="width:${lowPct}%"  title="${low} low-confidence"></div>
            </div>
            <div class="tcb-labels">
                <span><span class="conf-dot high"></span>${high} High</span>
                <span><span class="conf-dot med"></span>${med} Medium</span>
                <span><span class="conf-dot low"></span>${low} Low</span>
            </div>
        </div>

        <div class="target-intel-stats">
            <div class="ti-stat">
                <div class="ti-val">${high}</div>
                <div class="ti-label">Strong<br>suggestions</div>
            </div>
            <div class="ti-stat">
                <div class="ti-val">${low + noMeta}</div>
                <div class="ti-label">Need More<br>Receipts</div>
            </div>
            <div class="ti-stat">
                <div class="ti-val">${Math.round((high + med) / total * 100)}%</div>
                <div class="ti-label">Confidence<br>Score</div>
            </div>
        </div>

        ${needsData > 0 ? `
        <div class="ti-tip">
            <i data-feather="info"></i>
            <span>More shopping history would sharpen deal prices for <strong>${needsData}</strong> items.</span>
        </div>` : '<div class="ti-tip success"><i data-feather="check-circle"></i><span>All items have price observations — great coverage!</span></div>'}
    `;

    if (typeof feather !== 'undefined') feather.replace();
}

function renderDeeperInsights(brandPrices) {
    const container = document.getElementById('deep-insights-container');
    if (!container) return;

    // 1. Smart Buys (Low price, high volatility)
    const smartBuys = _data
        .filter(item => {
            const vol = _volatility[itemKey(item)] || 0;
            const isOnSpecial = (item.eff_price || 999) <= (item.target || 0);
            return isOnSpecial && vol > 10; // High confidence special
        })
        .sort((a, b) => (_volatility[itemKey(b)] || 0) - (_volatility[itemKey(a)] || 0))
        .slice(0, 3);

    // 2. Store Bias
    let wooliesCheaper = 0;
    let colesCheaper = 0;
    _data.forEach(item => {
        // Fallback: if all_stores is missing, use the current best store
        const w = item.all_stores?.woolworths?.eff_price || (item.store === 'woolworths' ? item.eff_price : null);
        const c = item.all_stores?.coles?.eff_price || (item.store === 'coles' ? item.eff_price : null);
        
        if (w && c) {
            if (w < c) wooliesCheaper++;
            else if (c < w) colesCheaper++;
        } else if (w) {
            wooliesCheaper++;
        } else if (c) {
            colesCheaper++;
        }
    });

    const total = (wooliesCheaper + colesCheaper) || 1;
    const wPercent = (wooliesCheaper / total) * 100;
    const cPercent = (colesCheaper / total) * 100;

    // 3. Brand Premium Analysis
    const privateAvg = brandPrices['Private Label'].sum / (brandPrices['Private Label'].count || 1);
    const nameAvg = brandPrices['Name Brand'].sum / (brandPrices['Name Brand'].count || 1);
    const premium = ((nameAvg - privateAvg) / privateAvg) * 100;

    let html = `
        <div class="deep-insight-card">
            <h4>🔥 Smart Buy Recommendations</h4>
            <div class="smart-buy-list">
                ${smartBuys.map(item => `
                    <div class="smart-buy-item">
                        <span class="name">${displayName(item.name)}</span>
                        <div class="meta">
                            <span class="price">$${item.eff_price?.toFixed(2)}</span>
                            <span class="volatility-tag high">Volatility: ${(_volatility[itemKey(item)] || 0).toFixed(0)}%</span>
                        </div>
                    </div>
                `).join('')}
            </div>
            <p class="insight-tip">These are at your good-deal price and usually go up again soon — grab them if you need them.</p>
        </div>

        <div class="deep-insight-card">
            <h4>🏷 Brand Premium Index</h4>
            <div class="premium-viz">
                <div class="premium-value">${premium > 0 ? '+' : ''}${premium.toFixed(0)}%</div>
                <div class="premium-label">Avg. markup for name brands</div>
                <div class="premium-comparison">
                    <span>Private: $${privateAvg.toFixed(2)}</span>
                    <span>Name: $${nameAvg.toFixed(2)}</span>
                </div>
            </div>
            <p class="insight-tip">Average price difference between generic and name-brand items in your list.</p>
        </div>

        <div class="deep-insight-card">
            <h4>🏛 Store Price Bias</h4>
            <div class="bias-viz">
                <div class="bias-bar">
                    <div class="bias-fill woolies" style="width: ${wPercent}%"></div>
                    <div class="bias-fill coles" style="width: ${cPercent}%"></div>
                </div>
                <div class="bias-labels">
                    <span>Woolies: ${wooliesCheaper} items</span>
                    <span>Coles: ${colesCheaper} items</span>
                </div>
            </div>
            <p class="insight-tip">Based on current cheaper-entry count across your watchlist.</p>
        </div>
    `;

    container.innerHTML = html;
}


function renderSparkline(containerId, historyData, storeClass) {
    const container = document.getElementById(containerId);
    if (!container) return;
    
    const canvas = container.querySelector('canvas');
    if (!canvas) return;

    // Destroy existing Chart instance on this canvas before creating a new one
    // to prevent memory leaks on repeated renderDashboard() calls.
    if (_sparklineCharts[containerId]) {
        try { _sparklineCharts[containerId].destroy(); } catch (_) {}
        delete _sparklineCharts[containerId];
    }

    const color = storeClass === 'woolworths' ? '#10b981' : '#ef4444';
    
    // Sort history by date just in case
    const sorted = [...historyData].sort((a, b) => new Date(a.date) - new Date(b.date));
    
    // Take up to last 14 data points
    const recent = sorted.slice(-14);
    
    const labels = recent.map(h => h.date);
    const data = recent.map(h => h.price);

    _sparklineCharts[containerId] = new Chart(canvas, {
        type: 'line',
        data: {
            labels: labels,
            datasets: [{
                data: data,
                borderColor: color,
                borderWidth: 2,
                pointRadius: 0,
                pointHoverRadius: 4,
                fill: true,
                backgroundColor: color + '20', // 20 hex is approx 12% opacity
                tension: 0.3
            }]
        },
        options: {
            responsive: true,
            maintainAspectRatio: false,
            plugins: {
                legend: { display: false },
                tooltip: {
                    callbacks: {
                        label: function(context) {
                            return '$' + context.parsed.y.toFixed(2);
                        }
                    }
                }
            },
            scales: {
                x: { display: false },
                y: { display: false, min: Math.min(...data) * 0.95, max: Math.max(...data) * 1.05 }
            },
            layout: { padding: 0 }
        }
    });
}

// ─── WEDNESDAY BANNER ────────────────────────────────────────────────────────
function renderWednesdayBanner() {
    const tueBanner = document.getElementById('wednesday-banner');
    const wedBanner = document.getElementById('wednesday-live-banner');
    if (!tueBanner || !wedBanner) return;

    const day = new Date().getDay(); // 0=Sun, 2=Tue, 3=Wed
    tueBanner.classList.toggle('hidden', day !== 2);   // Tuesday
    wedBanner.classList.toggle('hidden', day !== 3);   // Wednesday
}

// ─── TOP 5 DEALS THIS WEEK ───────────────────────────────────────────────────
function renderTop5Deals() {
    const container = document.getElementById('top5-list');
    if (!container) return;

    // Rank by savings % — store specials first, then target-based
    const deals = getTopDeals(_data, 5);

    if (deals.length === 0) {
        container.innerHTML = '<p style="color:var(--text-muted);font-size:12px;text-align:center;padding:8px 0;">No standout deals yet — prices update through the week.</p>';
        return;
    }

    container.innerHTML = deals.map((item, i) => {
        const medal = ['🥇','🥈','🥉','4️⃣','5️⃣'][i];
        return `
            <div class="top5-row" onclick="document.getElementById('dashboard-search').value='${item.name.substring(0,15)}'; _searchText='${item.name.substring(0,15).toLowerCase()}'; _currentPage=1; renderDashboard();" title="${displayName(item.name)}">
                <span class="top5-medal">${medal}</span>
                <div class="top5-info">
                    <div class="top5-name">${(() => { const dn = displayName(item.name); return dn.length > 28 ? dn.substring(0,28)+'…' : dn; })()}</div>
                    <div class="top5-price top5-price-${item.store === 'coles' ? 'coles' : 'woolies'}">$${item._snap.eff.toFixed(2)}</div>
                </div>
                <span class="top5-save">-${item._snap.savePct}%</span>
            </div>
        `;
    }).join('');
}

// ─── COPY SHOPPING LIST ───────────────────────────────────────────────────────
function copyShoppingList() {
    if (_shoppingList.length === 0) return;

    const today = new Date().toLocaleDateString('en-AU', { weekday: 'short', day: 'numeric', month: 'short' });
    const lines = [`🛒 Shopping List — ${today}`, ''];

    // Group by store
    const woolies = _shoppingList.filter(i => i.store === 'woolworths');
    const coles = _shoppingList.filter(i => i.store === 'coles');

    if (woolies.length) {
        lines.push('🟢 Woolworths');
        woolies.forEach(i => {
            const special = i.on_special ? ' 🏷️' : '';
            lines.push(`  ${i.qty > 1 ? i.qty + 'x ' : ''}${displayName(i.name)}${special} — $${((i.price || 0) * i.qty).toFixed(2)}`);
        });
        lines.push('');
    }
    if (coles.length) {
        lines.push('🔴 Coles');
        coles.forEach(i => {
            const special = i.on_special ? ' 🏷️' : '';
            lines.push(`  ${i.qty > 1 ? i.qty + 'x ' : ''}${displayName(i.name)}${special} — $${((i.price || 0) * i.qty).toFixed(2)}`);
        });
        lines.push('');
    }

    const total = _shoppingList.reduce((s, i) => s + (i.price || 0) * i.qty, 0);
    lines.push(`Total: ~$${total.toFixed(2)}`);
    lines.push('');
    lines.push('https://kuschikuschbert.github.io/wooliesbot/');

    navigator.clipboard.writeText(lines.join('\n')).then(() => {
        const btn = document.getElementById('copy-list-btn');
        if (btn) {
            const orig = btn.innerHTML;
            btn.innerHTML = '<i data-feather="check"></i> Copied!';
            btn.style.background = 'var(--woolies-green)';
            feather.replace();
            setTimeout(() => { btn.innerHTML = orig; btn.style.background = ''; feather.replace(); }, 2000);
        }
    }).catch(() => alert('Copy failed — use a secure (HTTPS) connection.'));
}

// ─── PRICE DROP ALERTS (IN-PAGE TOAST) ────────────────────────────────────────
const _alertedItems = new Set(JSON.parse(localStorage.getItem('alertedDrops') || '[]'));

function checkPriceDropAlerts() {
    const newDrops = [];
    _data.forEach(item => {
        const ep = item.eff_price || item.price || 0;
        const isSpecial = item.on_special || ((item.target || 0) > 0 && ep <= item.target && !item.price_unavailable);
        const k = itemKey(item);
        if (isSpecial && !_alertedItems.has(k)) {
            newDrops.push(item);
            _alertedItems.add(k);
        }
        if (!isSpecial && _alertedItems.has(k)) {
            _alertedItems.delete(k);
        }
    });

    localStorage.setItem('alertedDrops', JSON.stringify([..._alertedItems]));

    if (newDrops.length > 0) {
        showPriceDropToast(newDrops);
    }
}

function showPriceDropToast(items) {
    dismissPriceDropToast();

    const toast = document.createElement('div');
    toast.id = 'price-drop-toast';
    toast.className = 'price-drop-toast';

    const names = items.slice(0, 3).map(i => displayName(i.name).split(' ').slice(0, 2).join(' ')).join(', ');
    const more = items.length > 3 ? ` +${items.length - 3} more` : '';

    toast.innerHTML = `
        <span style="font-size:18px;">🔥</span>
        <div style="flex:1;min-width:0;">
            <div style="font-weight:700;font-size:13px;">New deals detected!</div>
            <div style="font-size:11px;color:var(--text-muted);margin-top:2px;">${names}${more}</div>
        </div>
        <button type="button" onclick="dismissPriceDropToast()" style="background:none;border:none;color:var(--text-muted);cursor:pointer;font-size:18px;line-height:1;" aria-label="Dismiss">×</button>
    `;

    document.body.appendChild(toast);
    _priceDropToastTimer = setTimeout(() => dismissPriceDropToast(), 6500);
}

// ═══════════════════════════════════════════════════════════════════════════════
// NEW ANALYTICS WIDGETS
// ═══════════════════════════════════════════════════════════════════════════════

// ─── 1. LIVE SAVINGS GAUGE ───────────────────────────────────────────────────
function renderSavingsGauge() {
    const container = document.getElementById('savings-gauge-container');
    if (!container) return;

    const savingsSummary = getSavingsOverview(_data);
    const currentSavings = savingsSummary.currentSavings;
    const potentialSavings = savingsSummary.currentSavings + savingsSummary.potentialSavings;
    const specialCount = savingsSummary.activeDeals;

    const pct = potentialSavings > 0 ? Math.min((currentSavings / Math.max(potentialSavings, currentSavings)) * 100, 100) : 0;
    const radius = 54;
    const circ = 2 * Math.PI * radius;
    const dash = (pct / 100) * circ;
    const gap = circ - dash;

    // Colour: red 0-30, amber 30-60, green 60+
    const color = pct >= 60 ? '#10b981' : pct >= 30 ? '#f59e0b' : '#6366f1';

    container.innerHTML = `
        <div class="gauge-wrap">
            <svg class="gauge-svg" viewBox="0 0 120 120">
                <circle cx="60" cy="60" r="${radius}" fill="none" stroke="rgba(255,255,255,0.05)" stroke-width="12"/>
                <circle cx="60" cy="60" r="${radius}" fill="none" stroke="${color}"
                    stroke-width="12" stroke-linecap="round"
                    stroke-dasharray="${dash} ${gap}"
                    stroke-dashoffset="${circ * 0.25}"
                    style="filter: drop-shadow(0 0 8px ${color}); transition: stroke-dasharray 1s ease;">
                </circle>
                <text x="60" y="55" text-anchor="middle" fill="white" font-size="16" font-weight="800" font-family="Outfit,sans-serif">$${currentSavings.toFixed(0)}</text>
                <text x="60" y="72" text-anchor="middle" fill="#9ca3af" font-size="9" font-family="Inter,sans-serif">SAVED NOW</text>
            </svg>
            <div class="gauge-stats">
                <div class="gauge-stat">
                    <span class="gauge-stat-val" style="color:${color}">${pct.toFixed(0)}%</span>
                    <span class="gauge-stat-label">Capture Rate</span>
                </div>
                <div class="gauge-stat">
                    <span class="gauge-stat-val">${specialCount}</span>
                    <span class="gauge-stat-label">Active Deals</span>
                </div>
            </div>
            <p class="insight-tip" style="margin-top:1rem;">
                ${pct >= 60 ? '🔥 Great week! You\'re capturing most of the available savings.' :
                  pct >= 30 ? '⚡ Some good deals active. Check the Deals tab for more.' :
                  '💡 Quiet on deals — set more targets to get alerted when prices drop.'}
            </p>
        </div>
    `;
}

// ─── 2. WEEKLY SAVINGS SUMMARY ───────────────────────────────────────────────
function renderWeeklySavings() {
    const container = document.getElementById('weekly-savings-container');
    if (!container) return;

    let totalSaved = 0;
    let totalWouldCost = 0;
    const dealItems = [];

    _data.forEach(item => {
        const snap = computeItemSavingsSnapshot(item);
        if (item.on_special && item.was_price && item.was_price > snap.shelf && snap.shelf > 0) {
            const saved = item.was_price - snap.shelf;
            totalSaved += saved;
            totalWouldCost += item.was_price;
            dealItems.push({ name: item.name, saved, savePct: Math.round((saved / item.was_price) * 100), store: item.store });
        }
    });

    const topDeals = dealItems.sort((a, b) => b.saved - a.saved).slice(0, 4);
    const pct = totalWouldCost > 0 ? ((totalSaved / totalWouldCost) * 100).toFixed(1) : 0;

    container.innerHTML = `
        <div class="weekly-savings-number">$${totalSaved.toFixed(2)}</div>
        <div class="weekly-savings-sub">saved this cycle vs normal prices · <strong>${pct}% off</strong></div>
        <div class="weekly-deals-list">
            ${topDeals.map(d => `
                <div class="weekly-deal-row">
                    <span class="wdr-name">${(() => { const dn = displayName(d.name); return dn.length > 26 ? dn.slice(0, 26) + '…' : dn; })()}</span>
                    <span class="wdr-save">-${d.savePct}% ($${d.saved.toFixed(2)})</span>
                </div>
            `).join('')}
            ${topDeals.length === 0 ? '<p class="weekly-empty">No store-confirmed specials with was_price data yet.</p>' : ''}
        </div>
    `;
}

function renderWeeklyActionPlan() {
    const container = document.getElementById('weekly-action-plan-container');
    if (!container) return;
    const actions = buildWeeklyActionPlan(5);
    if (!actions.length) {
        container.innerHTML = '<p class="weekly-empty">No high-confidence savings actions yet. Add more target prices to improve recommendations.</p>';
        return;
    }
    container.innerHTML = actions.map((row, i) => {
        const item = row.item;
        const level = row.urgency === 2 ? 'Urgent' : row.urgency === 1 ? 'Soon' : 'Optional';
        const storeLabel = item.store === 'coles' ? 'Coles' : 'Woolies';
        return `
            <button type="button" class="action-plan-row" onclick="openStockModal(${JSON.stringify(item.name)}, ${item.item_id ? JSON.stringify(item.item_id) : 'null'})">
                <span class="action-plan-rank">${i + 1}</span>
                <span class="action-plan-main">
                    <span class="action-plan-name">${displayName(item.name)}</span>
                    <span class="action-plan-meta">${storeLabel} · ${level}</span>
                </span>
                <span class="action-plan-save">Save ${row.snap.savePct}%</span>
            </button>
        `;
    }).join('');
}

function renderShoppingTimeInsights() {
    const container = document.getElementById('shopping-time-insights');
    if (!container) return;
    const duration = getAverageShoppingTripDuration();
    if (!duration) {
        container.innerHTML = `
            <div class="shopping-time-number">--</div>
            <p class="shopping-time-sub">No completed trip sessions yet.</p>
            <p class="shopping-time-note">Finish a trip with Done shopping to start tracking average time.</p>
        `;
        return;
    }
    const avgLabel = formatDurationShort(duration.averageSeconds);
    const tripLabel = duration.sessionCount === 1 ? 'trip' : 'trips';
    container.innerHTML = `
        <div class="shopping-time-number">${avgLabel}</div>
        <p class="shopping-time-sub">Average time from ${duration.sessionCount} completed ${tripLabel}.</p>
        <p class="shopping-time-note">Based on your local shopping trip sessions.</p>
    `;
}

// ─── 3. CATEGORY PRICE INFLATION ─────────────────────────────────────────────
function renderCategoryInflation() {
    const container = document.getElementById('category-inflation-container');
    if (!container) return;

    const now = new Date();
    const cutoff60 = new Date(now.getTime() - 60 * 24 * 60 * 60 * 1000); // 60 days ago
    const cutoff30 = new Date(now.getTime() - 30 * 24 * 60 * 60 * 1000); // 30 days ago

    // Per-category: avg price in [0-30 days ago] vs [30-60 days ago]
    const catRecent = {}; // recent 30d
    const catOld    = {}; // 30-60d

    _data.forEach(item => {
        const cat = item.type || 'other';
        const ph = item.price_history || [];
        ph.forEach(h => {
            const d = new Date(h.date);
            const p = parseFloat(h.price);
            if (!p || p <= 0 || p > 500) return;
            if (d >= cutoff30) {
                if (!catRecent[cat]) catRecent[cat] = [];
                catRecent[cat].push(p);
            } else if (d >= cutoff60) {
                if (!catOld[cat]) catOld[cat] = [];
                catOld[cat].push(p);
            }
        });
    });

    const CAT_EMOJI = {
        produce:'🥬', meat:'🥩', dairy:'🧀', beverages:'🥤', snacks:'🍫',
        pantry:'🫙', bakery:'🍞', frozen:'🧊', household:'🧹',
        personal_care:'🪥', pet:'🐾', other:'📦'
    };

    const rows = [];
    Object.keys(catRecent).forEach(cat => {
        if (!catOld[cat] || catOld[cat].length < 2) return;
        const avgRecent = catRecent[cat].reduce((a, b) => a + b, 0) / catRecent[cat].length;
        const avgOld    = catOld[cat].reduce((a, b) => a + b, 0) / catOld[cat].length;
        const change    = ((avgRecent - avgOld) / avgOld) * 100;
        rows.push({ cat, change, avgRecent, avgOld });
    });

    if (rows.length === 0) {
        // Fallback: use current prices vs targets to show relative position
        const catData = {};
        _data.forEach(item => {
            const cat = item.type || 'other';
            const ep = item.eff_price || item.price || 0;
            const tgt = item.target || 0;
            if (ep > 0 && tgt > 0) {
                if (!catData[cat]) catData[cat] = [];
                catData[cat].push(((ep - tgt) / tgt) * 100);
            }
        });
        Object.entries(catData).forEach(([cat, changes]) => {
            if (changes.length < 2) return;
            const avg = changes.reduce((a, b) => a + b, 0) / changes.length;
            rows.push({ cat, change: avg, avgRecent: 0, avgOld: 0, isTargetBased: true });
        });
    }

    rows.sort((a, b) => Math.abs(b.change) - Math.abs(a.change));

    const maxChange = Math.max(...rows.map(r => Math.abs(r.change)), 1);

    container.innerHTML = rows.slice(0, 10).map(r => {
        const pct = r.change;
        const barWidth = Math.min(Math.abs(pct) / maxChange * 100, 100);
        const up = pct > 0;
        const label = r.isTargetBased ? `${pct > 0 ? '+' : ''}${pct.toFixed(1)}% above usual deal avg` :
                      `${pct > 0 ? '↑' : '↓'} ${Math.abs(pct).toFixed(1)}% vs 60d ago`;
        const emoji = CAT_EMOJI[r.cat] || '📦';
        return `
            <div class="inflation-row">
                <div class="inflation-cat">${emoji} ${r.cat.replace('_', ' ')}</div>
                <div class="inflation-bar-wrap">
                    <div class="inflation-bar ${up ? 'up' : 'down'}" style="width:${barWidth}%"></div>
                </div>
                <div class="inflation-label ${up ? 'up' : 'down'}">${label}</div>
            </div>
        `;
    }).join('');

    if (rows.length === 0) {
        container.innerHTML = '<p style="color:var(--text-muted);font-size:13px;">Not enough price history yet to compute inflation trends. Data will populate as the bot runs daily.</p>';
    }
}

// ─── 4. DEAL HEAT MAP ────────────────────────────────────────────────────────
function renderDealHeatmap() {
    const container = document.getElementById('deal-heatmap-container');
    if (!container) return;

    const CAT_EMOJI = {
        produce:'🥬', meat:'🥩', dairy:'🧀', beverages:'🥤', snacks:'🍫',
        pantry:'🫙', bakery:'🍞', frozen:'🧊', household:'🧹',
        personal_care:'🪥', pet:'🐾', other:'📦'
    };

    // Build per-category, per-store stats
    const cats = [...new Set(_data.map(i => i.type || 'other'))].filter(c => c);
    const heatData = {};

    cats.forEach(cat => {
        heatData[cat] = { woolworths: { specials: 0, total: 0, savings: 0 }, coles: { specials: 0, total: 0, savings: 0 } };
    });

    _data.forEach(item => {
        const cat = item.type || 'other';
        const store = item.store;
        if (!store || store === 'none' || !heatData[cat]?.[store]) return;

        const ep = item.eff_price || item.price || 0;
        heatData[cat][store].total++;

        const shelf = item.price || ep;
        const isSpecial = item.on_special || (item.target > 0 && ep <= item.target && !item.price_unavailable);
        if (isSpecial) {
            heatData[cat][store].specials++;
            const ref = item.was_price && item.was_price > shelf ? item.was_price : (item.target || shelf);
            heatData[cat][store].savings += Math.max(0, ref - shelf);
        }
    });

    // Find max specials for scale
    let maxSpecials = 1;
    cats.forEach(cat => {
        ['woolworths', 'coles'].forEach(s => {
            maxSpecials = Math.max(maxSpecials, heatData[cat]?.[s]?.specials || 0);
        });
    });

    const sortedCats = cats.sort((a, b) => {
        const aTotal = (heatData[a]?.woolworths?.specials || 0) + (heatData[a]?.coles?.specials || 0);
        const bTotal = (heatData[b]?.woolworths?.specials || 0) + (heatData[b]?.coles?.specials || 0);
        return bTotal - aTotal;
    });

    const isMobile = window.matchMedia('(max-width: 768px)').matches;

    if (isMobile) {
        // Simple vertical list: one row per category showing winner
        container.innerHTML = `<div class="heatmap-mobile-list">
            ${sortedCats.map(cat => {
                const w = heatData[cat]?.woolworths || { specials: 0, total: 0, savings: 0 };
                const c = heatData[cat]?.coles || { specials: 0, total: 0, savings: 0 };
                const wWinner = w.specials >= c.specials && w.specials > 0;
                const cWinner = c.specials > w.specials && c.specials > 0;
                const totalSpecials = w.specials + c.specials;
                if (totalSpecials === 0) return '';
                const winner = wWinner ? '🟢 Woolies' : cWinner ? '🔴 Coles' : '—';
                const winnerClass = wWinner ? 'woolies' : cWinner ? 'coles' : '';
                const totalSavings = (w.savings + c.savings);
                return `<div class="heatmap-mobile-row">
                    <span class="heatmap-mobile-cat">${CAT_EMOJI[cat] || '📦'} ${cat.replace('_', ' ')}</span>
                    <span class="heatmap-mobile-counts">${w.specials}W · ${c.specials}C</span>
                    <span class="heatmap-mobile-winner ${winnerClass}">${winner}</span>
                    ${totalSavings > 0.05 ? `<span class="heatmap-mobile-save">$${totalSavings.toFixed(2)} off</span>` : ''}
                </div>`;
            }).filter(Boolean).join('')}
        </div>`;
        return;
    }

    container.innerHTML = `
        <div class="heatmap-grid">
            <div class="heatmap-header-col"></div>
            <div class="heatmap-store-header woolies-head">🟢 Woolworths</div>
            <div class="heatmap-store-header coles-head">🔴 Coles</div>
            ${sortedCats.map(cat => {
                const w = heatData[cat]?.woolworths || { specials: 0, total: 0, savings: 0 };
                const c = heatData[cat]?.coles || { specials: 0, total: 0, savings: 0 };
                const wIntensity = maxSpecials > 0 ? (w.specials / maxSpecials) : 0;
                const cIntensity = maxSpecials > 0 ? (c.specials / maxSpecials) : 0;
                const wWinner = w.specials >= c.specials;

                return `
                    <div class="heatmap-label">${CAT_EMOJI[cat] || '📦'} ${cat.replace('_', ' ')}</div>
                    <div class="heatmap-cell ${wWinner && w.specials > 0 ? 'woolies-winner' : ''}" style="--intensity: ${wIntensity}">
                        <div class="heatmap-cell-count">${w.specials}</div>
                        <div class="heatmap-cell-sub">of ${w.total} on special</div>
                        ${w.savings > 0.05 ? `<div class="heatmap-savings">$${w.savings.toFixed(2)} off</div>` : ''}
                    </div>
                    <div class="heatmap-cell ${!wWinner && c.specials > 0 ? 'coles-winner' : ''}" style="--intensity: ${cIntensity}">
                        <div class="heatmap-cell-count">${c.specials}</div>
                        <div class="heatmap-cell-sub">of ${c.total} on special</div>
                        ${c.savings > 0.05 ? `<div class="heatmap-savings">$${c.savings.toFixed(2)} off</div>` : ''}
                    </div>
                `;
            }).join('')}
        </div>
    `;
}

// ─── 5. VOLATILITY LEADERBOARD ───────────────────────────────────────────────
function renderVolatilityLeaderboard() {
    const container = document.getElementById('volatility-leaderboard-container');
    if (!container) return;

    // Re-compute volatility from price_history (richer source than scrape_history)
    const volScores = [];

    _data.forEach(item => {
        const ph = item.price_history || [];
        if (ph.length < 3) return;

        const prices = ph.map(h => parseFloat(h.price)).filter(p => p > 0 && p < 500);
        if (prices.length < 3) return;

        const avg = prices.reduce((a, b) => a + b, 0) / prices.length;
        const variance = prices.reduce((a, b) => a + Math.pow(b - avg, 2), 0) / prices.length;
        const stdDev = Math.sqrt(variance);
        const vol = (stdDev / avg) * 100;

        const ep = item.eff_price || item.price || 0;
        const isOnSpecial = item.on_special || (item.target > 0 && ep <= item.target);
        const minPrice = Math.min(...prices);
        const maxPrice = Math.max(...prices);

        volScores.push({
            name: item.name,
            vol,
            avg: avg.toFixed(2),
            min: minPrice.toFixed(2),
            max: maxPrice.toFixed(2),
            store: item.store,
            isOnSpecial,
            ep
        });
    });

    // Fill with items that have at least some history if not enough
    if (volScores.length < 5) {
        _data.forEach(item => {
            if (volScores.find(v => v.name === item.name)) return;
            const ph = item.price_history || [];
            if (ph.length < 2) return;
            const prices = ph.map(h => parseFloat(h.price)).filter(p => p > 0);
            if (prices.length < 2) return;
            const avg = prices.reduce((a, b) => a + b, 0) / prices.length;
            const vol = Math.abs(prices[0] - prices[prices.length - 1]) / avg * 100;
            const ep = item.eff_price || item.price || 0;
            volScores.push({
                name: item.name, vol, avg: avg.toFixed(2),
                min: Math.min(...prices).toFixed(2), max: Math.max(...prices).toFixed(2),
                store: item.store, isOnSpecial: false, ep
            });
        });
    }

    volScores.sort((a, b) => b.vol - a.vol);
    const top = volScores.slice(0, 12);
    const maxVol = top[0]?.vol || 1;

    if (top.length === 0) {
        container.innerHTML = '<p style="color:var(--text-muted);font-size:13px;">Build up price history (3+ data points per item) to see volatility rankings.</p>';
        return;
    }

    container.innerHTML = `
        <div class="vol-table">
            ${top.map((item, i) => {
                const barW = (item.vol / maxVol) * 100;
                const storeColor = item.store === 'woolworths' ? '#10b981' : '#ef4444';
                const volClass = item.vol > 15 ? 'high' : item.vol > 8 ? 'med' : 'low';
                return `
                    <div class="vol-row" onclick="openItemDeepdive('${item.name.replace(/'/g, "\\'")}')">
                        <div class="vol-rank">#${i + 1}</div>
                        <div class="vol-info">
                            <div class="vol-name">
                                ${(() => { const dn = displayName(item.name); return dn.length > 32 ? dn.slice(0, 32) + '…' : dn; })()}
                                ${item.isOnSpecial ? '<span class="vol-special-badge">🔥 ON SPECIAL</span>' : ''}
                            </div>
                            <div class="vol-bar-wrap">
                                <div class="vol-bar ${volClass}" style="width:${barW}%"></div>
                            </div>
                        </div>
                        <div class="vol-meta">
                            <div class="vol-score ${volClass}">${item.vol.toFixed(0)}%</div>
                            <div class="vol-range">$${item.min}–$${item.max}</div>
                        </div>
                    </div>
                `;
            }).join('')}
        </div>
        <p class="insight-tip">Click any item to see its full price history chart.</p>
    `;
}

// ─── 6. BEST TIME TO BUY ─────────────────────────────────────────────────────
function renderBestTimeToBuy() {
    const container = document.getElementById('best-time-container');
    if (!container) return;

    // Month-bucket all price_history entries by category
    const catMonthPrices = {}; // cat -> { month(0-11) -> [prices] }

    _data.forEach(item => {
        const cat = item.type || 'other';
        const ph = item.price_history || [];
        ph.forEach(h => {
            const d = new Date(h.date);
            const p = parseFloat(h.price);
            if (!p || p <= 0 || p > 500) return;
            const m = d.getMonth(); // 0-11
            if (!catMonthPrices[cat]) catMonthPrices[cat] = {};
            if (!catMonthPrices[cat][m]) catMonthPrices[cat][m] = [];
            catMonthPrices[cat][m].push(p);
        });
    });

    const MONTHS = ['Jan','Feb','Mar','Apr','May','Jun','Jul','Aug','Sep','Oct','Nov','Dec'];
    const CAT_EMOJI = {
        produce:'🥬', meat:'🥩', dairy:'🧀', beverages:'🥤', snacks:'🍫',
        pantry:'🫙', bakery:'🍞', frozen:'🧊', household:'🧹',
        personal_care:'🪥', pet:'🐾', other:'📦'
    };

    const results = [];
    Object.entries(catMonthPrices).forEach(([cat, byMonth]) => {
        const monthAvgs = Object.entries(byMonth)
            .filter(([, prices]) => prices.length >= 2)
            .map(([m, prices]) => ({
                month: parseInt(m),
                avg: prices.reduce((a, b) => a + b, 0) / prices.length
            }));
        if (monthAvgs.length < 2) return;
        monthAvgs.sort((a, b) => a.avg - b.avg);
        const cheapest = monthAvgs[0];
        const mostExpensive = monthAvgs[monthAvgs.length - 1];
        const saving = ((mostExpensive.avg - cheapest.avg) / mostExpensive.avg * 100).toFixed(0);
        results.push({ cat, cheapestMonth: cheapest.month, saving, avg: cheapest.avg });
    });

    if (results.length === 0) {
        container.innerHTML = `
            <div class="best-time-empty">
                <div style="font-size:32px;margin-bottom:0.5rem;">📅</div>
                <p>Price history across multiple months is building up. Check back after a few weeks of data collection.</p>
            </div>
        `;
        return;
    }

    results.sort((a, b) => parseInt(b.saving) - parseInt(a.saving));

    container.innerHTML = `
        <div class="best-time-list">
            ${results.slice(0, 8).map(r => `
                <div class="best-time-row">
                    <span class="bt-cat">${CAT_EMOJI[r.cat] || '📦'} ${r.cat.replace('_', ' ')}</span>
                    <span class="bt-month">${MONTHS[r.cheapestMonth]}</span>
                    <span class="bt-saving">saves ~${r.saving}%</span>
                </div>
            `).join('')}
        </div>
    `;
}

// ─── 7. PANTRY HEALTH SCORE ──────────────────────────────────────────────────
function renderPantryHealthScore() {
    const container = document.getElementById('pantry-health-container');
    if (!container) return;

    const total = _data.length;
    if (total === 0) return;

    // Metrics
    const lowStockCount = _data.filter(i => i.stock === 'low').length;
    const medStockCount = _data.filter(i => i.stock === 'medium').length;
    const withTarget = _data.filter(i => (i.target || 0) > 0).length;
    const highConf = _data.filter(i => i.target_confidence === 'high').length;
    const specials = _data.filter(i => {
        const ep = i.eff_price || i.price || 0;
        return i.on_special || (i.target > 0 && ep <= i.target && !i.price_unavailable);
    }).length;

    // Score components (0-100 each, weighted)
    const stockScore    = Math.max(0, 100 - (lowStockCount / total) * 200 - (medStockCount / total) * 50);
    const targetCovScore = (withTarget / total) * 100;
    const confScore     = (highConf / total) * 100;
    const dealScore     = Math.min((specials / Math.max(total * 0.15, 1)) * 100, 100);

    const overallScore = Math.round(stockScore * 0.35 + targetCovScore * 0.25 + confScore * 0.20 + dealScore * 0.20);
    const clampedScore = Math.min(Math.max(overallScore, 0), 100);

    const grade = clampedScore >= 80 ? { label: 'Excellent', color: '#10b981', icon: '🏆' }
                : clampedScore >= 60 ? { label: 'Good', color: '#6366f1', icon: '✅' }
                : clampedScore >= 40 ? { label: 'Fair', color: '#f59e0b', icon: '⚡' }
                : { label: 'Needs Attention', color: '#ef4444', icon: '⚠️' };

    const metrics = [
        { label: 'Stock Status', score: Math.round(stockScore), icon: '📦',
          hint: `${lowStockCount} items low, ${medStockCount} medium` },
        { label: 'Deal prices set', score: Math.round(targetCovScore), icon: '🎯',
          hint: `${withTarget} of ${total} items have a “good deal” price` },
        { label: 'Price estimates', score: Math.round(confScore), icon: '🔬',
          hint: `${highConf} items with strong price history` },
        { label: 'Deal Capture', score: Math.round(dealScore), icon: '🔥',
          hint: `${specials} active deals right now` },
    ];

    container.innerHTML = `
        <div class="health-score-layout">
            <div class="health-score-main">
                <div class="health-ring-wrap">
                    <svg viewBox="0 0 120 120" class="health-ring-svg">
                        <circle cx="60" cy="60" r="50" fill="none" stroke="rgba(255,255,255,0.05)" stroke-width="10"/>
                        <circle cx="60" cy="60" r="50" fill="none" stroke="${grade.color}"
                            stroke-width="10" stroke-linecap="round"
                            stroke-dasharray="${(clampedScore / 100) * 314} 314"
                            stroke-dashoffset="78.5"
                            style="filter:drop-shadow(0 0 10px ${grade.color}); transition: stroke-dasharray 1.2s ease;">
                        </circle>
                        <text x="60" y="54" text-anchor="middle" fill="white" font-size="26" font-weight="800" font-family="Outfit,sans-serif">${clampedScore}</text>
                        <text x="60" y="70" text-anchor="middle" fill="#9ca3af" font-size="9" font-family="Inter,sans-serif">/ 100</text>
                    </svg>
                </div>
                <div class="health-grade">
                    <span class="health-grade-icon">${grade.icon}</span>
                    <span class="health-grade-label" style="color:${grade.color}">${grade.label}</span>
                </div>
            </div>
            <div class="health-metrics">
                ${metrics.map(m => {
                    const mColor = m.score >= 70 ? '#10b981' : m.score >= 45 ? '#f59e0b' : '#ef4444';
                    return `
                        <div class="health-metric-row">
                            <div class="health-metric-icon">${m.icon}</div>
                            <div class="health-metric-info">
                                <div class="health-metric-label">${m.label}</div>
                                <div class="health-metric-hint">${m.hint}</div>
                                <div class="health-metric-bar">
                                    <div class="health-metric-fill" style="width:${m.score}%;background:${mColor};box-shadow:0 0 8px ${mColor}40"></div>
                                </div>
                            </div>
                            <div class="health-metric-score" style="color:${mColor}">${m.score}</div>
                        </div>
                    `;
                }).join('')}
            </div>
        </div>
    `;
}

// ─── 8. ITEM DEEP-DIVE MODAL ─────────────────────────────────────────────────
let _deepdiveChart = null;

function openItemDeepdive(itemName) {
    const item = _data.find(i => i.name === itemName);
    if (!item) return;

    closeCompareGroupModal();

    // Remove existing modal
    document.getElementById('deepdive-modal')?.remove();

    const ph = item.price_history || [];
    const sorted = [...ph].sort((a, b) => new Date(a.date) - new Date(b.date));
    const vol = _volatility[itemKey(item)] || 0;
    const ep = item.eff_price || item.price || 0;
    const isOnSpecial = item.on_special || (item.target > 0 && ep <= item.target);
    const storeColor = item.store === 'woolworths' ? '#10b981' : '#ef4444';

    const modal = document.createElement('div');
    modal.id = 'deepdive-modal';
    modal.className = 'deepdive-overlay';
    modal.onclick = (e) => { if (e.target === modal) closeItemDeepdive(); };

    modal.innerHTML = `
        <div class="deepdive-panel">
            <div class="deepdive-header">
                <div>
                    <h3 class="deepdive-title">${displayName(item.name)}</h3>
                    <div class="deepdive-meta">
                        <span class="store-badge ${item.store}" style="margin-top:0;">${item.store === 'woolworths' ? 'Woolies' : 'Coles'}</span>
                        ${isOnSpecial ? '<span class="save-badge">ON SPECIAL</span>' : ''}
                        ${item.target_confidence ? `<span class="confidence-badge ${item.target_confidence}">
                            ${item.target_confidence === 'high' ? '🟢 Solid estimate' : item.target_confidence === 'medium' ? '🟡 Fair estimate' : '🔴 Rough estimate'}
                        </span>` : ''}
                    </div>
                </div>
                <button onclick="closeItemDeepdive()" class="deepdive-close">
                    <i data-feather="x"></i>
                </button>
            </div>
            <div class="deepdive-stats">
                <div class="dd-stat">
                    <div class="dd-stat-val" style="color:${storeColor}">$${ep.toFixed(2)}</div>
                    <div class="dd-stat-label">Current</div>
                </div>
                ${item.target > 0 ? `<div class="dd-stat">
                    <div class="dd-stat-val">$${item.target.toFixed(2)}</div>
                    <div class="dd-stat-label">Good deal</div>
                </div>` : ''}
                ${item.was_price ? `<div class="dd-stat">
                    <div class="dd-stat-val" style="color:#f87171;text-decoration:line-through">$${item.was_price.toFixed(2)}</div>
                    <div class="dd-stat-label">Was</div>
                </div>` : ''}
                <div class="dd-stat">
                    <div class="dd-stat-val ${vol > 15 ? 'vol-high' : vol > 8 ? 'vol-med' : ''}">${vol.toFixed(0)}%</div>
                    <div class="dd-stat-label">Volatility</div>
                </div>
                <div class="dd-stat">
                    <div class="dd-stat-val">${ph.length}</div>
                    <div class="dd-stat-label">Price checks</div>
                </div>
            </div>
            ${item.compare_group ? `
            <div class="deepdive-compare-group">
                <span class="dd-cg-label">Similar products at other sizes</span>
                <button type="button" class="cg-compare-btn cg-compare-btn-sm" data-compare-group="${escapeHtml(item.compare_group)}">Open</button>
            </div>` : ''}
            <div class="deepdive-chart-wrap">
                ${sorted.length > 1 ? '<canvas id="deepdive-canvas"></canvas>' :
                  '<p style="color:var(--text-muted);text-align:center;padding:3rem;font-size:13px;">Not enough price history to chart.<br>At least 2 data points needed.</p>'}
            </div>
            <div class="deepdive-footer">
                <div class="dd-footer-info">
                    <span>${item.type || 'uncategorised'} · ${item.brand || 'unknown brand'}</span>
                    ${item.size ? `<span>Size: ${item.size}</span>` : ''}
                </div>
                <button type="button" class="sync-btn deepdive-add-btn" style="padding:10px 20px;width:auto;">
                    <i data-feather="plus"></i> Add to shopping list
                </button>
            </div>
        </div>
    `;

    document.body.appendChild(modal);
    const deepAdd = modal.querySelector('.deepdive-add-btn');
    if (deepAdd) {
        deepAdd.addEventListener('click', () => {
            addToList(item.name, null, item.item_id || null);
            closeItemDeepdive();
        });
    }
    feather.replace();

    if (sorted.length > 1) {
        const canvas = document.getElementById('deepdive-canvas');
        if (canvas) {
            const ctx = canvas.getContext('2d');
            if (_deepdiveChart) _deepdiveChart.destroy();
            _deepdiveChart = new Chart(ctx, {
                type: 'line',
                data: {
                    labels: sorted.map(h => h.date),
                    datasets: [{
                        label: 'Price',
                        data: sorted.map(h => h.price),
                        borderColor: storeColor,
                        backgroundColor: storeColor + '20',
                        fill: true,
                        tension: 0.4,
                        borderWidth: 3,
                        pointRadius: 5,
                        pointBackgroundColor: storeColor,
                        pointHoverRadius: 8,
                    },
                    ...(item.target > 0 ? [{
                        label: 'Good deal',
                        data: sorted.map(() => item.target),
                        borderColor: '#6366f1',
                        borderDash: [6, 4],
                        borderWidth: 2,
                        pointRadius: 0,
                        fill: false,
                    }] : [])
                    ]
                },
                options: {
                    responsive: true,
                    maintainAspectRatio: false,
                    plugins: {
                        legend: { labels: { color: '#9ca3af' } },
                        tooltip: { callbacks: { label: ctx => `$${ctx.parsed.y.toFixed(2)}` } }
                    },
                    scales: {
                        x: { grid: { color: 'rgba(255,255,255,0.05)' }, ticks: { color: '#9ca3af', maxTicksLimit: 8 } },
                        y: { beginAtZero: false, grid: { color: 'rgba(255,255,255,0.05)' }, ticks: { color: '#9ca3af', callback: v => '$' + v.toFixed(2) } }
                    }
                }
            });
        }
    }
}

function closeItemDeepdive() {
    document.getElementById('deepdive-modal')?.remove();
    if (_deepdiveChart) { _deepdiveChart.destroy(); _deepdiveChart = null; }
}

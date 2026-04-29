/**
 * trip_stats.js — Trip-mode session helpers + gamification renderers.
 * Loaded before app.js; no access to app.js globals.
 * Functions defined here are global and callable from app.js.
 */

/* ─── Storage ──────────────────────────────────────────────────────────── */

const TRIP_SESSIONS_STORE_KEY = 'shoppingTripSessions';

function loadShoppingTripSessions() {
    try {
        const parsed = JSON.parse(localStorage.getItem(TRIP_SESSIONS_STORE_KEY) || '[]');
        return Array.isArray(parsed) ? parsed : [];
    } catch {
        return [];
    }
}

/* ─── Helpers (moved from app.js) ─────────────────────────────────────── */

function getLastShoppingTripSavedAmount(sessions) {
    const s = sessions !== undefined ? sessions : loadShoppingTripSessions();
    if (!Array.isArray(s) || s.length === 0) return 0;
    const val = Number((s[s.length - 1] || {}).saved_amount || 0);
    return Number.isFinite(val) && val > 0 ? val : 0;
}

function getAverageShoppingTripDuration(sessions) {
    const s = sessions !== undefined ? sessions : loadShoppingTripSessions();
    const valid = s.filter(x => {
        const d = Number(x?.duration_seconds || 0);
        return Number.isFinite(d) && d > 0;
    });
    if (!valid.length) return null;
    const total = valid.reduce((sum, x) => sum + Number(x.duration_seconds || 0), 0);
    return { averageSeconds: total / valid.length, sessionCount: valid.length };
}

function formatDurationShort(seconds) {
    if (!Number.isFinite(seconds) || seconds <= 0) return '0m';
    const totalMins = Math.max(1, Math.round(seconds / 60));
    const h = Math.floor(totalMins / 60);
    const m = totalMins % 60;
    if (h <= 0) return `${totalMins}m`;
    if (m === 0) return `${h}h`;
    return `${h}h ${m}m`;
}

/* ─── Session enrichment ───────────────────────────────────────────────── */

/**
 * Mutates session in-place to add computed gamification fields.
 * allSessions should NOT yet include the new session.
 */
function enrichTripSession(session, allSessions) {
    const dur = Number(session?.duration_seconds || 0);
    const picked = Number(session?.picked_count || 0);
    const startCount = Number(session?.start_count || 0);
    const saved = Number(session?.saved_amount || 0);

    session.items_per_min = (dur > 0 && picked > 0)
        ? Math.round((picked / dur) * 600) / 10
        : null;
    session.dollars_per_hour = (dur > 0 && saved > 0)
        ? Math.round((saved / dur) * 36000) / 100
        : null;
    session.completion_pct = startCount > 0
        ? Math.min(100, Math.round((picked / startCount) * 100))
        : null;

    try {
        const d = new Date(session.started_at);
        session.hour_started = d.getHours();
        session.weekday = d.getDay();
    } catch {
        session.hour_started = null;
        session.weekday = null;
    }

    const prev = (allSessions || []).filter(s => s !== session);
    const prevBest = prev.length ? Math.max(...prev.map(s => Number(s?.saved_amount || 0))) : 0;
    session.personal_best = saved > 0 && saved > prevBest;

    return session;
}

/* ─── Aggregates ───────────────────────────────────────────────────────── */

function computeTripAggregates(sessions) {
    const s = sessions !== undefined ? sessions : loadShoppingTripSessions();
    const now = Date.now();
    const ms7 = 7 * 24 * 60 * 60 * 1000;
    const ms30 = 30 * 24 * 60 * 60 * 1000;

    const agg = (list) => ({
        count: list.length,
        saved: list.reduce((sum, x) => sum + Number(x?.saved_amount || 0), 0),
        durationAvg: list.length
            ? list.reduce((sum, x) => sum + Number(x?.duration_seconds || 0), 0) / list.length
            : 0,
    });

    const recent7 = s.filter(x => now - Date.parse(x.started_at) <= ms7);
    const recent30 = s.filter(x => now - Date.parse(x.started_at) <= ms30);
    const bestSaved = s.length ? Math.max(...s.map(x => Number(x?.saved_amount || 0))) : 0;
    const completed = s.filter(x => Number(x?.duration_seconds || 0) > 0);
    const fastestFinish = completed.length
        ? Math.min(...completed.map(x => Number(x.duration_seconds)))
        : null;

    // Best hour of day: hour with highest average savings (need >= 2 trips)
    const hourMap = {};
    s.forEach(x => {
        const h = x.hour_started;
        if (h == null || !Number.isFinite(h)) return;
        if (!hourMap[h]) hourMap[h] = [];
        hourMap[h].push(Number(x?.saved_amount || 0));
    });
    let bestHour = null;
    let bestHourAvg = 0;
    Object.entries(hourMap).forEach(([h, vals]) => {
        if (vals.length < 2) return;
        const avg = vals.reduce((a, b) => a + b, 0) / vals.length;
        if (avg > bestHourAvg) { bestHourAvg = avg; bestHour = Number(h); }
    });

    return {
        totals7d: agg(recent7),
        totals30d: agg(recent30),
        totalsAll: agg(s),
        bestSavedAmount: bestSaved,
        fastestFinish,
        bestHourOfDay: bestHour,
    };
}

/* ─── Weekly streak ────────────────────────────────────────────────────── */

function computeWeeklyStreak(sessions, now) {
    const s = sessions !== undefined ? sessions : loadShoppingTripSessions();
    if (!s.length) return 0;
    const nowMs = (now != null) ? Number(now) : Date.now();

    const getIsoWeekKey = (ms) => {
        const d = new Date(ms);
        d.setUTCHours(0, 0, 0, 0);
        d.setUTCDate(d.getUTCDate() + 4 - (d.getUTCDay() || 7));
        const yearStart = new Date(Date.UTC(d.getUTCFullYear(), 0, 1));
        const week = Math.ceil(((d - yearStart) / 86400000 + 1) / 7);
        return `${d.getUTCFullYear()}-${week}`;
    };

    const tripWeeks = new Set();
    s.forEach(x => {
        const ms = Date.parse(x.started_at);
        if (Number.isFinite(ms)) tripWeeks.add(getIsoWeekKey(ms));
    });

    let streak = 0;
    let checkMs = nowMs;
    for (let i = 0; i < 52; i++) {
        if (!tripWeeks.has(getIsoWeekKey(checkMs))) break;
        streak++;
        checkMs -= 7 * 24 * 60 * 60 * 1000;
    }
    return streak;
}

/* ─── CTA copy ─────────────────────────────────────────────────────────── */

function pickHomeCtaCopy({ list, sessions, tripActive }) {
    const s = sessions !== undefined ? sessions : loadShoppingTripSessions();
    if (tripActive) {
        const n = (list || []).filter(x => !x.picked).length;
        return {
            kind: 'mid-trip',
            headline: `Trip in progress — ${n} item${n === 1 ? '' : 's'} left`,
            sub: 'Tap to open your list and keep going.',
            ctaLabel: 'Continue trip',
        };
    }
    if (!s.length) {
        return {
            kind: 'no-trips',
            headline: 'Start your first shopping trip',
            sub: 'Add items to your list, then tap Go shopping to track savings and time.',
            ctaLabel: 'Open list',
        };
    }
    const listLen = (list || []).length;
    const lastMs = Date.parse(s[s.length - 1]?.started_at || '');
    const daysSince = Number.isFinite(lastMs)
        ? Math.floor((Date.now() - lastMs) / 86400000)
        : Infinity;
    const lastSaved = getLastShoppingTripSavedAmount(s);
    const savedStr = lastSaved > 0 ? `$${lastSaved.toFixed(2)}` : null;

    if (listLen === 0) {
        const prompt = savedStr
            ? `Last trip you saved ${savedStr}. Add items and beat it.`
            : 'Browse deals and add items to your list.';
        return { kind: 'empty-list', headline: 'Your list is empty', sub: prompt, ctaLabel: 'Open list' };
    }
    const lastLabel = daysSince <= 1 ? 'yesterday'
        : daysSince <= 7 ? `${daysSince} days ago`
        : `${Math.floor(daysSince / 7)} week${Math.floor(daysSince / 7) === 1 ? '' : 's'} ago`;
    const beatStr = savedStr ? ` — beat your ${savedStr} record` : '';
    return {
        kind: 'idle',
        headline: `${listLen} item${listLen === 1 ? '' : 's'} ready to go`,
        sub: `Last trip was ${lastLabel}${beatStr}.`,
        ctaLabel: 'Go shopping',
    };
}

/* ─── DOM: Your Trips tile (Insights tab) ──────────────────────────────── */

function renderYourTripsTile(sessions) {
    const container = document.getElementById('your-trips-tile');
    if (!container) return;
    const s = sessions !== undefined ? sessions : loadShoppingTripSessions();
    if (!s.length) {
        container.innerHTML = `
            <p class="your-trips-empty">No completed trips yet.</p>
            <p class="your-trips-hint">Tap <strong>Go shopping</strong> in your list to start tracking time, savings, and streaks.</p>
        `;
        return;
    }
    const agg = computeTripAggregates(s);
    const streak = computeWeeklyStreak(s);
    const dur = getAverageShoppingTripDuration(s);
    const last = s[s.length - 1];
    const lastSavedStr = `$${Number(last?.saved_amount || 0).toFixed(2)}`;
    const lastDurStr = last?.duration_seconds ? formatDurationShort(Number(last.duration_seconds)) : '—';
    const streakHtml = streak >= 2
        ? `<span class="your-trips-chip your-trips-chip--streak">🔥 ${streak}-week streak</span>` : '';
    const pbHtml = last?.personal_best
        ? `<span class="your-trips-chip your-trips-chip--pb">Personal best</span>` : '';
    const hourLabel = agg.bestHourOfDay != null ? (() => {
        const h = agg.bestHourOfDay;
        return h === 0 ? '12am' : h < 12 ? `${h}am` : h === 12 ? '12pm' : `${h - 12}pm`;
    })() : null;
    container.innerHTML = `
        ${(streakHtml || pbHtml) ? `<div class="your-trips-chips">${streakHtml}${pbHtml}</div>` : ''}
        <div class="your-trips-stats">
            <div class="your-trips-stat">
                <span class="your-trips-stat-label">Last trip</span>
                <span class="your-trips-stat-value">${lastSavedStr} saved in ${lastDurStr}</span>
            </div>
            <div class="your-trips-stat">
                <span class="your-trips-stat-label">All time</span>
                <span class="your-trips-stat-value">$${agg.totalsAll.saved.toFixed(2)} over ${agg.totalsAll.count} trip${agg.totalsAll.count === 1 ? '' : 's'}</span>
            </div>
            <div class="your-trips-stat">
                <span class="your-trips-stat-label">Avg trip time</span>
                <span class="your-trips-stat-value">${dur ? formatDurationShort(dur.averageSeconds) : '—'}</span>
            </div>
            ${hourLabel ? `<div class="your-trips-stat"><span class="your-trips-stat-label">Best time of day</span><span class="your-trips-stat-value">${hourLabel}</span></div>` : ''}
        </div>
    `;
}

/* ─── DOM: Home shopping CTA ───────────────────────────────────────────── */

function renderHomeShoppingCta({ list, sessions, tripActive }) {
    const section = document.getElementById('your-shopping-cta');
    if (!section) return;
    if (localStorage.getItem('engagementV1') === '0') { section.hidden = true; return; }
    const s = sessions !== undefined ? sessions : loadShoppingTripSessions();
    const copy = pickHomeCtaCopy({ list, sessions: s, tripActive });
    section.hidden = false;
    section.innerHTML = `
        <div class="cta-card cta-card--${copy.kind}">
            <div class="cta-card-text">
                <p class="cta-card-headline">${copy.headline}</p>
                <p class="cta-card-sub">${copy.sub}</p>
            </div>
            <button type="button" class="cta-card-btn" id="cta-open-list-btn">${copy.ctaLabel}</button>
            <button type="button" class="cta-dismiss" id="cta-dismiss-btn" aria-label="Dismiss">✕</button>
        </div>
    `;
    document.getElementById('cta-open-list-btn')?.addEventListener('click', () => {
        const nav = typeof window !== 'undefined' ? window.__wbNav : null;
        const items = list || [];
        if (copy.kind === 'idle' && items.length > 0 && nav?.startTripFromDashboardCta) {
            nav.startTripFromDashboardCta();
            return;
        }
        if (nav?.openShoppingListDrawerIfClosed) {
            nav.openShoppingListDrawerIfClosed();
            return;
        }
        const drawer = document.getElementById('list-drawer');
        const btn = document.getElementById('toggle-list-btn')
            || document.getElementById('mobile-toggle-list');
        if (!drawer?.classList.contains('open')) btn?.click();
    });
    document.getElementById('cta-dismiss-btn')?.addEventListener('click', () => {
        section.hidden = true;
    });
}

/* ─── DOM: End-of-trip recap modal ─────────────────────────────────────── */

function showTripRecapModal(session, history) {
    if (!session) return;
    if (localStorage.getItem('engagementV1') === '0') return;
    const modal = document.getElementById('trip-recap-modal');
    if (!modal) return;
    const saved = Number(session.saved_amount || 0);
    const dur = Number(session.duration_seconds || 0);
    const picked = Number(session.picked_count || 0);
    const pb = Boolean(session.personal_best);
    const prevSessions = (history || []).slice(0, -1);
    const lastSaved = getLastShoppingTripSavedAmount(prevSessions);
    const delta = lastSaved > 0 ? saved - lastSaved : null;
    const deltaHtml = delta != null
        ? `<p class="recap-delta ${delta >= 0 ? 'recap-delta--pos' : 'recap-delta--neg'}">${delta >= 0 ? `+$${delta.toFixed(2)} vs last trip` : `-$${Math.abs(delta).toFixed(2)} vs last trip`}</p>`
        : '';
    modal.hidden = false;
    modal.innerHTML = `
        <div class="trip-recap-card" role="document">
            <h3 class="recap-title">${pb ? 'Personal best!' : 'Trip complete'}</h3>
            <div class="recap-stats">
                <div class="recap-stat">
                    <span class="recap-stat-val">$${saved.toFixed(2)}</span>
                    <span class="recap-stat-label">saved</span>
                </div>
                <div class="recap-stat">
                    <span class="recap-stat-val">${dur > 0 ? formatDurationShort(dur) : '—'}</span>
                    <span class="recap-stat-label">time</span>
                </div>
                <div class="recap-stat">
                    <span class="recap-stat-val">${picked}</span>
                    <span class="recap-stat-label">items ticked</span>
                </div>
            </div>
            ${deltaHtml}
            <button type="button" class="recap-close-btn" id="recap-close-btn">Done</button>
        </div>
    `;
    const close = () => { modal.hidden = true; };
    document.getElementById('recap-close-btn')?.addEventListener('click', close);
    modal.addEventListener('click', (e) => { if (e.target === modal) close(); }, { once: true });
}

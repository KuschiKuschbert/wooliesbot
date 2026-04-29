/**
 * priority_helpers.js
 * Stock-level and deal-priority helpers shared across dashboard surfaces.
 * Depends on: _data, computeItemSavingsSnapshot, getEffectivePrice,
 *             saneWasForSavings (all in app.js, loaded after this file).
 */

// ── Repurchase interval table (medians from observed purchase history) ────────
// Values derived from actual price_history intervals; reasonable priors for
// types with fewer than 3 observations. All values in days.
const TYPE_INTERVAL_DAYS = {
    meat: 14,
    pet: 13,
    snacks: 18,
    produce: 20,
    beverages: 21,
    bakery: 29,
    dairy: 29,
    pantry: 29,
    frozen: 30,
    personal_care: 60,
    household: 88,
    other: 21,
};
const DEFAULT_INTERVAL_DAYS = 21;

function intervalDaysFor(item) {
    return TYPE_INTERVAL_DAYS[item?.type] ?? DEFAULT_INTERVAL_DAYS;
}

// ── Three-band urgency thresholds (as ratio of item's interval) ───────────────
const DUE_RATIO = 0.7;      // surface at 70% of interval (e.g. dairy: day 20)
const OVERDUE_RATIO = 1.4;  // urgent at 140% of interval (e.g. dairy: day 41)
const STALE_RATIO = 3.0;    // dormant at 300% of interval (e.g. dairy: day 87)

/**
 * Classifies an item into one of four urgency bands:
 *   'not_due'   — too early to surface
 *   'likely_due' — approaching restock time
 *   'overdue'   — past expected restock window
 *   'stale'     — dormant, not recently purchased; suppress from urgency surfaces
 *
 * Explicit stock='low' always overrides to 'overdue'.
 */
function restockBand(item, now = new Date()) {
    if (!item) return 'not_due';
    if (item.stock === 'low') return 'overdue';
    if (!item.last_purchased) return 'not_due';
    const days = (now - new Date(item.last_purchased)) / 86400000;
    if (days < 0) return 'not_due';
    const ratio = days / intervalDaysFor(item);
    if (ratio >= STALE_RATIO) return 'stale';
    if (ratio >= OVERDUE_RATIO) return 'overdue';
    if (ratio >= DUE_RATIO) return 'likely_due';
    return 'not_due';
}

/**
 * Boolean convenience wrapper used by renderPredictions and getPriorityItems.
 * Returns true for likely_due or overdue bands.
 */
function isLikelyLow(item, now = new Date()) {
    const band = restockBand(item, now);
    return band === 'likely_due' || band === 'overdue';
}

function getPriorityItems(items, limit) {
    if (items === undefined) items = _data;
    if (limit === undefined) limit = 8;
    const now = new Date();
    return (items || [])
        .filter(item => isLikelyLow(item, now))
        .map(item => ({ ...item, _snap: computeItemSavingsSnapshot(item), _band: restockBand(item, now) }))
        .sort((a, b) => {
            // overdue before likely_due
            const bandRank = (b) => b._band === 'overdue' ? 0 : 1;
            const br = bandRank(a) - bandRank(b);
            if (br !== 0) return br;
            // items on deal first within each band
            const aDeal = a._snap.isDeal ? 0 : 1;
            const bDeal = b._snap.isDeal ? 0 : 1;
            if (aDeal !== bDeal) return aDeal - bDeal;
            if (b._snap.savePct !== a._snap.savePct) return b._snap.savePct - a._snap.savePct;
            return b._snap.savedDollar - a._snap.savedDollar;
        })
        .slice(0, limit);
}

function getTopDeals(items, limit) {
    if (items === undefined) items = _data;
    if (limit === undefined) limit = 5;
    return (items || [])
        .map(item => ({ ...item, _snap: computeItemSavingsSnapshot(item) }))
        .filter(item => item._snap.isDeal && item._snap.savePct > 0 && !item.price_unavailable)
        .sort((a, b) => b._snap.savePct - a._snap.savePct)
        .slice(0, limit);
}

function getSavingsOverview(items) {
    if (items === undefined) items = _data;
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
        {
            const w = saneWasForSavings(item, snap.shelf);
            if (w != null) summary.potentialSavings += Math.max(0, w - snap.shelf);
        }
    }
    return summary;
}

function buildWeeklyActionPlan(limit) {
    if (limit === undefined) limit = 5;
    const now = new Date();
    return (_data || [])
        .map(item => {
            const snap = computeItemSavingsSnapshot(item);
            const band = restockBand(item, now);
            const urgency = item.stock === 'low' ? 2
                : band === 'overdue' ? 2
                : band === 'likely_due' ? 1.5
                : item.stock === 'medium' ? 1 : 0;
            const likelyLow = band === 'likely_due' || band === 'overdue';
            const score = (snap.savePct * 2.2) + (snap.savedDollar * 7) + (urgency * 12);
            return { item, snap, urgency, likelyLow, score };
        })
        .filter(row => (row.likelyLow || row.snap.isDeal) && row.score > 0)
        .sort((a, b) => b.score - a.score)
        .slice(0, limit);
}

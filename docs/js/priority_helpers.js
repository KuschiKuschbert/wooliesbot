/**
 * priority_helpers.js
 * Stock-level and deal-priority helpers shared across dashboard surfaces.
 * Depends on: _data, computeItemSavingsSnapshot, getEffectivePrice,
 *             saneWasForSavings (all in app.js, loaded after this file).
 */

/**
 * Returns true if an item is explicitly low or estimated to be running low
 * based on per-category repurchase frequency heuristics.
 */
function isLikelyLow(item, now = new Date()) {
    if (!item) return false;
    if (item.stock === 'low') return true;
    if (!item.last_purchased) return false;
    const diffDays = (now - new Date(item.last_purchased)) / 86400000;
    let threshold = 10;
    if (item.type === 'fresh_protein' || item.type === 'fresh_veg') threshold = 4;
    else if (item.type === 'fresh_fridge') threshold = 6;
    else if (item.type === 'pet' || item.type === 'household') threshold = 14;
    return diffDays >= threshold;
}

function getPriorityItems(items, limit) {
    if (items === undefined) items = _data;
    if (limit === undefined) limit = 8;
    const now = new Date();
    return (items || [])
        .filter(item => isLikelyLow(item, now))
        .map(item => ({ ...item, _snap: computeItemSavingsSnapshot(item) }))
        .sort((a, b) => {
            // Explicit low-stock before frequency-estimated
            const aLow = a.stock === 'low' ? 0 : 1;
            const bLow = b.stock === 'low' ? 0 : 1;
            if (aLow !== bLow) return aLow - bLow;
            // Items on deal come first within each group
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
            const likelyLow = isLikelyLow(item, now);
            const urgency = item.stock === 'low' ? 2 : (likelyLow ? 1.5 : item.stock === 'medium' ? 1 : 0);
            const score = (snap.savePct * 2.2) + (snap.savedDollar * 7) + (urgency * 12);
            return { item, snap, urgency, likelyLow, score };
        })
        .filter(row => (row.likelyLow || row.snap.isDeal) && row.score > 0)
        .sort((a, b) => b.score - a.score)
        .slice(0, limit);
}

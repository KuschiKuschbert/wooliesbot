/**
 * Household sync — pure helpers (must match workers/wooliesbot-write/src/household_merge.js).
 * Loaded before app.js; exposes global WooliesHouseholdSync.
 */
(function (global) {
    'use strict';

    var TRIP_SESSIONS_CAP = 200;
    var DROP_ALERTS_CAP = 500;

    function normalizeShoppingListRows(rows) {
        if (!Array.isArray(rows)) return [];
        return rows.map(function (row) {
            var safe = row && typeof row === 'object' ? Object.assign({}, row) : {};
            var t = Date.parse(String(safe.updated_at || ''));
            return {
                item_id: safe.item_id || null,
                name: String(safe.name || ''),
                price: Number.isFinite(Number(safe.price)) ? Number(safe.price) : 0,
                qty: Number.isFinite(Number(safe.qty)) && Number(safe.qty) > 0 ? Number(safe.qty) : 1,
                store: String(safe.store || 'woolworths'),
                image: safe.image || null,
                on_special: Boolean(safe.on_special),
                was_price: Number.isFinite(Number(safe.was_price)) ? Number(safe.was_price) : null,
                picked: Boolean(safe.picked),
                updated_at: Number.isFinite(t) ? new Date(t).toISOString() : new Date().toISOString()
            };
        });
    }

    function shoppingListRowKey(row, fallbackIdx) {
        if (fallbackIdx === undefined) fallbackIdx = -1;
        var byId = String(row && row.item_id || '').trim().toLowerCase();
        if (byId) return 'id:' + byId;
        var byName = String(row && row.name || '').trim().toLowerCase();
        if (byName) return 'name:' + byName;
        return 'anon:' + fallbackIdx + ':' + String(row && row.updated_at || '').trim();
    }

    function shoppingListRowUpdatedMs(row) {
        var t = Date.parse(String(row && row.updated_at || ''));
        return Number.isFinite(t) ? t : 0;
    }

    function choosePreferredShoppingListRow(currentRow, incomingRow, preferIncomingOnTie) {
        if (preferIncomingOnTie === undefined) preferIncomingOnTie = false;
        if (!currentRow) return Object.assign({}, incomingRow);
        if (!incomingRow) return Object.assign({}, currentRow);
        var currMs = shoppingListRowUpdatedMs(currentRow);
        var nextMs = shoppingListRowUpdatedMs(incomingRow);
        if (nextMs > currMs) return Object.assign({}, incomingRow);
        if (nextMs < currMs) return Object.assign({}, currentRow);
        if (preferIncomingOnTie) return Object.assign({}, incomingRow);
        return JSON.stringify(incomingRow) > JSON.stringify(currentRow) ? Object.assign({}, incomingRow) : Object.assign({}, currentRow);
    }

    function mergeShoppingListRows(existingRows, incomingRows) {
        var current = normalizeShoppingListRows(existingRows);
        var incoming = normalizeShoppingListRows(incomingRows);
        var merged = new Map();
        var orderedKeys = [];
        current.forEach(function (row, idx) {
            var key = shoppingListRowKey(row, idx);
            if (!merged.has(key)) orderedKeys.push(key);
            merged.set(key, choosePreferredShoppingListRow(merged.get(key), row, false));
        });
        incoming.forEach(function (row, idx) {
            var key = shoppingListRowKey(row, current.length + idx);
            if (!merged.has(key)) orderedKeys.push(key);
            merged.set(key, choosePreferredShoppingListRow(merged.get(key), row, true));
        });
        return orderedKeys.map(function (key) { return merged.get(key); }).filter(Boolean);
    }

    function sectionUpdatedMs(obj) {
        if (!obj || typeof obj !== 'object') return 0;
        var t = Date.parse(String(obj.updated_at || ''));
        return Number.isFinite(t) ? t : 0;
    }

    function chooseSectionLWW(existing, incoming, preferIncomingOnTie) {
        if (preferIncomingOnTie === undefined) preferIncomingOnTie = true;
        if (!incoming || typeof incoming !== 'object') return existing && typeof existing === 'object' ? Object.assign({}, existing) : null;
        if (!existing || typeof existing !== 'object') return Object.assign({}, incoming);
        var a = sectionUpdatedMs(existing);
        var b = sectionUpdatedMs(incoming);
        if (b > a) return Object.assign({}, incoming);
        if (b < a) return Object.assign({}, existing);
        if (preferIncomingOnTie) return Object.assign({}, incoming);
        return JSON.stringify(incoming) > JSON.stringify(existing) ? Object.assign({}, incoming) : Object.assign({}, existing);
    }

    function capTripSessions(sessions) {
        if (!Array.isArray(sessions)) return [];
        if (sessions.length <= TRIP_SESSIONS_CAP) return sessions;
        return sessions.slice(-TRIP_SESSIONS_CAP);
    }

    function capDropItemIds(ids) {
        if (!Array.isArray(ids)) return [];
        var seen = new Set();
        var out = [];
        for (var i = 0; i < ids.length; i++) {
            var s = String(ids[i] || '').trim();
            if (!s || seen.has(s)) continue;
            seen.add(s);
            out.push(s);
            if (out.length >= DROP_ALERTS_CAP) break;
        }
        return out;
    }

    /**
     * @param {string} hash - location.hash including #
     * @returns {{ wbt: string, wbu: string }|null}
     */
    function parsePairingFromHash(hash) {
        if (!hash || typeof hash !== 'string') return null;
        var h = hash.replace(/^#/, '');
        if (!h) return null;
        try {
            var params = new URLSearchParams(h);
            var wbt = (params.get('wbt') || '').trim();
            var wbu = (params.get('wbu') || '').trim();
            if (!wbt) return null;
            if (wbt.length < 8 || wbt.length > 2048) return null;
            return { wbt: wbt, wbu: wbu };
        } catch (e) {
            return null;
        }
    }

    global.WooliesHouseholdSync = {
        TRIP_SESSIONS_CAP: TRIP_SESSIONS_CAP,
        DROP_ALERTS_CAP: DROP_ALERTS_CAP,
        normalizeShoppingListRows: normalizeShoppingListRows,
        mergeShoppingListRows: mergeShoppingListRows,
        chooseSectionLWW: chooseSectionLWW,
        sectionUpdatedMs: sectionUpdatedMs,
        capTripSessions: capTripSessions,
        capDropItemIds: capDropItemIds,
        parsePairingFromHash: parsePairingFromHash
    };
}(typeof window !== 'undefined' ? window : this));

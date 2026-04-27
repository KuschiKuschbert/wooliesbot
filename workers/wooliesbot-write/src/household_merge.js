/**
 * Household sync (schema v2) — merge shopping items + section LWW.
 * Used by Cloudflare Worker; keep in sync with docs/js/household_sync.js contract tests.
 */

export const TRIP_SESSIONS_CAP = 200;
export const DROP_ALERTS_CAP = 500;

export function normalizeShoppingListRows(rows) {
	if (!Array.isArray(rows)) return [];
	return rows.map((row) => {
		const safe = row && typeof row === "object" ? { ...row } : {};
		const t = Date.parse(String(safe.updated_at || ""));
		return {
			item_id: safe.item_id || null,
			name: String(safe.name || ""),
			price: Number.isFinite(Number(safe.price)) ? Number(safe.price) : 0,
			qty: Number.isFinite(Number(safe.qty)) && Number(safe.qty) > 0 ? Number(safe.qty) : 1,
			store: String(safe.store || "woolworths"),
			image: safe.image || null,
			on_special: Boolean(safe.on_special),
			was_price: Number.isFinite(Number(safe.was_price)) ? Number(safe.was_price) : null,
			picked: Boolean(safe.picked),
			updated_at: Number.isFinite(t) ? new Date(t).toISOString() : new Date().toISOString(),
		};
	});
}

function shoppingListRowKey(row, fallbackIdx = -1) {
	const byId = String(row?.item_id || "").trim().toLowerCase();
	if (byId) return `id:${byId}`;
	const byName = String(row?.name || "").trim().toLowerCase();
	if (byName) return `name:${byName}`;
	return `anon:${fallbackIdx}:${String(row?.updated_at || "").trim()}`;
}

function shoppingListRowUpdatedMs(row) {
	const t = Date.parse(String(row?.updated_at || ""));
	return Number.isFinite(t) ? t : 0;
}

function choosePreferredShoppingListRow(currentRow, incomingRow, preferIncomingOnTie = false) {
	if (!currentRow) return { ...incomingRow };
	if (!incomingRow) return { ...currentRow };
	const currMs = shoppingListRowUpdatedMs(currentRow);
	const nextMs = shoppingListRowUpdatedMs(incomingRow);
	if (nextMs > currMs) return { ...incomingRow };
	if (nextMs < currMs) return { ...currentRow };
	if (preferIncomingOnTie) return { ...incomingRow };
	const currSig = JSON.stringify(currentRow);
	const nextSig = JSON.stringify(incomingRow);
	return nextSig > currSig ? { ...incomingRow } : { ...currentRow };
}

export function mergeShoppingListRows(existingRows, incomingRows) {
	const current = normalizeShoppingListRows(existingRows);
	const incoming = normalizeShoppingListRows(incomingRows);
	const merged = new Map();
	const orderedKeys = [];

	current.forEach((row, idx) => {
		const key = shoppingListRowKey(row, idx);
		if (!merged.has(key)) orderedKeys.push(key);
		merged.set(key, choosePreferredShoppingListRow(merged.get(key), row));
	});

	incoming.forEach((row, idx) => {
		const key = shoppingListRowKey(row, current.length + idx);
		if (!merged.has(key)) orderedKeys.push(key);
		merged.set(key, choosePreferredShoppingListRow(merged.get(key), row, true));
	});

	return orderedKeys.map((key) => merged.get(key)).filter(Boolean);
}

const SECTION_KEYS = [
	"trip_state",
	"shop_mode_state",
	"essentials_state",
	"trip_sessions_state",
	"drop_alerts_state",
];

function sectionUpdatedMs(obj) {
	if (!obj || typeof obj !== "object") return 0;
	const t = Date.parse(String(obj.updated_at || ""));
	return Number.isFinite(t) ? t : 0;
}

/**
 * Prefer the section with newer updated_at; on tie use preferIncomingOnTie (then JSON compare).
 */
export function chooseSectionLWW(existing, incoming, preferIncomingOnTie = true) {
	if (!incoming || typeof incoming !== "object") return existing && typeof existing === "object" ? { ...existing } : null;
	if (!existing || typeof existing !== "object") return { ...incoming };
	const a = sectionUpdatedMs(existing);
	const b = sectionUpdatedMs(incoming);
	if (b > a) return { ...incoming };
	if (b < a) return { ...existing };
	if (preferIncomingOnTie) return { ...incoming };
	return JSON.stringify(incoming) > JSON.stringify(existing) ? { ...incoming } : { ...existing };
}

function capTripSessions(sessions) {
	if (!Array.isArray(sessions)) return [];
	if (sessions.length <= TRIP_SESSIONS_CAP) return sessions;
	return sessions.slice(-TRIP_SESSIONS_CAP);
}

function capDropItemIds(ids) {
	if (!Array.isArray(ids)) return [];
	const seen = new Set();
	const out = [];
	for (const id of ids) {
		const s = String(id || "").trim();
		if (!s || seen.has(s)) continue;
		seen.add(s);
		out.push(s);
		if (out.length >= DROP_ALERTS_CAP) break;
	}
	return out;
}

function applySectionCaps(payload) {
	const out = { ...payload };
	if (out.trip_sessions_state && typeof out.trip_sessions_state === "object") {
		const s = out.trip_sessions_state;
		out.trip_sessions_state = {
			...s,
			sessions: capTripSessions(s.sessions),
		};
	}
	if (out.drop_alerts_state && typeof out.drop_alerts_state === "object") {
		const d = out.drop_alerts_state;
		out.drop_alerts_state = {
			...d,
			item_ids: capDropItemIds(d.item_ids),
		};
	}
	return out;
}

/**
 * Old clients: POST without household_sync — merge items only; preserve all other file keys.
 */
export function isItemsOnlyHouseholdPost(body) {
	if (!body || typeof body !== "object") return true;
	if (body.household_sync === true) return false;
	return true;
}

/**
 * @param {object} existingDecoded — parsed file from GitHub (may be schema 1)
 * @param {object} body — POST JSON
 * @returns {object} payload to write
 */
export function buildHouseholdPayload(existingDecoded, body) {
	const existing =
		existingDecoded && typeof existingDecoded === "object" && !Array.isArray(existingDecoded)
			? { ...existingDecoded }
			: { schema: 1, items: [] };

	const deviceId = String(body?.device_id || "").trim() || "unknown";
	const updatedAt = new Date().toISOString();
	const existingItems = normalizeShoppingListRows(existing.items || []);
	const incomingItems = normalizeShoppingListRows(body?.items || []);
	const mergedItems = mergeShoppingListRows(existingItems, incomingItems);

	if (isItemsOnlyHouseholdPost(body)) {
		return {
			...existing,
			items: mergedItems,
			updated_at: updatedAt,
			updated_by: deviceId,
		};
	}

	const out = {
		...existing,
		schema: 2,
		items: mergedItems,
		updated_at: updatedAt,
		updated_by: deviceId,
	};

	for (const key of SECTION_KEYS) {
		const ex = existing[key];
		const inc = body[key];
		if (inc && typeof inc === "object" && String(inc.updated_at || "").trim() !== "") {
			out[key] = chooseSectionLWW(ex, inc, true);
		} else if (ex && typeof ex === "object") {
			out[key] = ex;
		}
	}

	return applySectionCaps(out);
}

export { SECTION_KEYS };

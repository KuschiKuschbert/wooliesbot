/**
 * WooliesBot write API — Cloudflare Worker.
 * Persists /update_stock writes via GitHub Contents API (docs/data.json).
 *
 * Secrets: GH_TOKEN (or GITHUB_TOKEN), WOOLIESBOT_WRITE_SECRET
 * Vars: GITHUB_REPO_OWNER, GITHUB_REPO_NAME, ALLOWED_ORIGINS (comma-separated)
 */

const DATA_PATH = "docs/data.json";
const SHOPPING_LIST_PATH = "docs/shopping_list_sync.json";
const MAX_BODY_BYTES = 32768;
const RATE_LIMIT_WINDOW_MS = 60_000;
const RATE_LIMIT_MAX = 45;

/** @type {Map<string, number[]>} */
const rateBuckets = new Map();

function sydneyDateStr() {
	const parts = new Intl.DateTimeFormat("en-AU", {
		timeZone: "Australia/Sydney",
		year: "numeric",
		month: "2-digit",
		day: "2-digit",
	}).formatToParts(new Date());
	const y = parts.find((p) => p.type === "year")?.value;
	const m = parts.find((p) => p.type === "month")?.value;
	const d = parts.find((p) => p.type === "day")?.value;
	return `${y}-${m}-${d}`;
}

function utf8ToBase64(str) {
	const bytes = new TextEncoder().encode(str);
	let bin = "";
	bytes.forEach((b) => {
		bin += String.fromCharCode(b);
	});
	return btoa(bin);
}

function base64ToUtf8(b64) {
	const bin = atob(b64);
	const bytes = new Uint8Array(bin.length);
	for (let i = 0; i < bin.length; i++) bytes[i] = bin.charCodeAt(i);
	return new TextDecoder().decode(bytes);
}

function nowIso() {
	return new Date().toISOString();
}

function isoToMs(v) {
	const t = Date.parse(String(v || ""));
	return Number.isFinite(t) ? t : 0;
}

function corsHeaders(env, requestOrigin) {
	const allowed = parseAllowedOrigins(env);
	let origin = "*";
	if (allowed.length) {
		if (requestOrigin && allowed.includes(requestOrigin)) origin = requestOrigin;
		else if (allowed.length === 1) origin = allowed[0];
		else origin = "null";
	}
	return {
		"Access-Control-Allow-Origin": origin,
		"Access-Control-Allow-Methods": "GET, POST, OPTIONS",
		"Access-Control-Allow-Headers": "Content-Type, X-WooliesBot-Secret, X-Requested-With",
		"Access-Control-Max-Age": "86400",
	};
}

function parseAllowedOrigins(env) {
	const raw = (env.ALLOWED_ORIGINS || "").trim();
	if (!raw) return [];
	return raw
		.split(",")
		.map((s) => s.trim())
		.filter(Boolean);
}

function jsonResponse(body, status, env, requestOrigin) {
	return new Response(JSON.stringify(body), {
		status,
		headers: {
			"Content-Type": "application/json",
			...corsHeaders(env, requestOrigin),
		},
	});
}

function clientIp(request) {
	return request.headers.get("CF-Connecting-IP") || request.headers.get("X-Forwarded-For")?.split(",")[0]?.trim() || "unknown";
}

function rateLimitOk(ip) {
	const now = Date.now();
	let arr = rateBuckets.get(ip);
	if (!arr) {
		arr = [];
		rateBuckets.set(ip, arr);
	}
	const cutoff = now - RATE_LIMIT_WINDOW_MS;
	while (arr.length && arr[0] < cutoff) arr.shift();
	if (arr.length >= RATE_LIMIT_MAX) return false;
	arr.push(now);
	return true;
}

function githubToken(env) {
	return (env.GH_TOKEN || env.GITHUB_TOKEN || "").trim();
}

function requireConfig(env) {
	const owner = (env.GITHUB_REPO_OWNER || "").trim();
	const repo = (env.GITHUB_REPO_NAME || "").trim();
	const token = githubToken(env);
	const secret = (env.WOOLIESBOT_WRITE_SECRET || "").trim();
	const missing = [];
	if (!owner) missing.push("GITHUB_REPO_OWNER");
	if (!repo) missing.push("GITHUB_REPO_NAME");
	if (!token) missing.push("GH_TOKEN");
	if (!secret) missing.push("WOOLIESBOT_WRITE_SECRET");
	return { owner, repo, token, secret, missing };
}

/**
 * Apply update_stock semantics for dashboard writes.
 * @returns {{ updated: boolean, payload: object|array }}
 */
function mergeStockChange(rawDecoded, params) {
	const itemName = params.name;
	const itemId = params.item_id;
	const newStock = params.stock;
	const newTarget = params.target;

	let raw = rawDecoded;
	let items;
	let isDict = false;

	if (raw !== null && typeof raw === "object" && !Array.isArray(raw)) {
		isDict = true;
		items = raw.items;
		if (!Array.isArray(items)) throw new Error("invalid_json_shape");
	} else if (Array.isArray(raw)) {
		items = raw;
	} else {
		throw new Error("invalid_json_shape");
	}

	let updated = false;
	for (const item of items) {
		if (!item || typeof item !== "object") continue;
		let match = false;
		if (itemId && item.item_id === itemId) match = true;
		else if (itemName && item.name === itemName) match = true;
		if (match) {
			item.stock = newStock;
			if (newStock === "full") {
				item.last_purchased = sydneyDateStr();
			}
			if (newTarget !== undefined && newTarget !== null) {
				item.target = Number(newTarget);
			}
			updated = true;
			break;
		}
	}

	let payload;
	if (isDict) {
		raw.items = items;
		payload = raw;
	} else {
		payload = items;
	}
	return { updated, payload };
}

async function githubGetFile(owner, repo, token, path) {
	const url = `https://api.github.com/repos/${owner}/${repo}/contents/${encodeURIComponent(path).replace(/%2F/g, "/")}`;
	const res = await fetch(url, {
		headers: {
			Authorization: `Bearer ${token}`,
			Accept: "application/vnd.github+json",
			"X-GitHub-Api-Version": "2022-11-28",
			"User-Agent": "wooliesbot-write-worker",
		},
	});
	const text = await res.text();
	let data;
	try {
		data = text ? JSON.parse(text) : {};
	} catch {
		data = { message: text };
	}
	return { res, data };
}

async function githubPutFile(owner, repo, token, path, message, contentB64, sha) {
	const url = `https://api.github.com/repos/${owner}/${repo}/contents/${encodeURIComponent(path).replace(/%2F/g, "/")}`;
	const res = await fetch(url, {
		method: "PUT",
		headers: {
			Authorization: `Bearer ${token}`,
			Accept: "application/vnd.github+json",
			"Content-Type": "application/json",
			"X-GitHub-Api-Version": "2022-11-28",
			"User-Agent": "wooliesbot-write-worker",
		},
		body: JSON.stringify({
			message,
			content: contentB64,
			...(sha ? { sha } : {}),
		}),
	});
	const text = await res.text();
	let data;
	try {
		data = text ? JSON.parse(text) : {};
	} catch {
		data = { message: text };
	}
	return { res, data };
}

function cartItemKey(item) {
	if (!item || typeof item !== "object") return "";
	const id = String(item.item_id || "").trim();
	if (id) return `id:${id}`;
	const name = String(item.name || "").trim().toLowerCase();
	return name ? `name:${name}` : "";
}

function normalizeCartItem(raw) {
	if (!raw || typeof raw !== "object") return null;
	const key = cartItemKey(raw);
	if (!key) return null;
	const qtyRaw = Number(raw.qty);
	const qty = Number.isFinite(qtyRaw) && qtyRaw > 0 ? Math.max(1, Math.round(qtyRaw)) : 1;
	const updatedMs = isoToMs(raw.updated_at);
	return {
		item_id: raw.item_id || null,
		name: String(raw.name || "").trim(),
		price: Number.isFinite(Number(raw.price)) ? Number(raw.price) : null,
		qty,
		store: raw.store || null,
		image: raw.image || null,
		on_special: Boolean(raw.on_special),
		was_price: Number.isFinite(Number(raw.was_price)) ? Number(raw.was_price) : null,
		picked: Boolean(raw.picked),
		updated_at: updatedMs ? new Date(updatedMs).toISOString() : nowIso(),
	};
}

function normalizeTombstones(raw) {
	const out = {};
	if (!raw || typeof raw !== "object") return out;
	for (const [k, v] of Object.entries(raw)) {
		const ms = isoToMs(v);
		if (!k || !ms) continue;
		out[k] = new Date(ms).toISOString();
	}
	return out;
}

function applyTombstonesToItems(items, tombstones) {
	const filtered = [];
	for (const it of items) {
		const key = cartItemKey(it);
		if (!key) continue;
		const delMs = isoToMs(tombstones[key]);
		if (delMs && delMs >= isoToMs(it.updated_at)) continue;
		filtered.push(it);
	}
	return filtered;
}

function normalizeShoppingPayload(raw) {
	const src = raw && typeof raw === "object" ? raw : {};
	const items = Array.isArray(src.items) ? src.items.map(normalizeCartItem).filter(Boolean) : [];
	const tombstones = normalizeTombstones(src.tombstones);
	const itemMap = new Map();
	for (const it of items) itemMap.set(cartItemKey(it), it);
	return {
		updated_at: isoToMs(src.updated_at) ? new Date(isoToMs(src.updated_at)).toISOString() : nowIso(),
		device_id: String(src.device_id || "").trim() || "unknown",
		items: applyTombstonesToItems([...itemMap.values()], tombstones),
		tombstones,
	};
}

function mergeCartRows(a, b) {
	const aMs = isoToMs(a?.updated_at);
	const bMs = isoToMs(b?.updated_at);
	const newer = bMs >= aMs ? b : a;
	const older = bMs >= aMs ? a : b;
	return {
		...newer,
		item_id: newer.item_id || older.item_id || null,
		name: newer.name || older.name || "",
		qty: Math.max(Number(a?.qty || 1), Number(b?.qty || 1), 1),
		picked: Boolean(a?.picked) || Boolean(b?.picked),
		updated_at: new Date(Math.max(aMs, bMs) || Date.now()).toISOString(),
	};
}

function mergeShoppingPayloads(remoteRaw, incomingRaw) {
	const remote = normalizeShoppingPayload(remoteRaw);
	const incoming = normalizeShoppingPayload(incomingRaw);
	const tombstones = { ...remote.tombstones };
	for (const [k, v] of Object.entries(incoming.tombstones)) {
		const prev = isoToMs(tombstones[k]);
		const next = isoToMs(v);
		if (!prev || next >= prev) tombstones[k] = new Date(next).toISOString();
	}
	const map = new Map();
	for (const it of remote.items) map.set(cartItemKey(it), it);
	for (const it of incoming.items) {
		const k = cartItemKey(it);
		if (!k) continue;
		const prev = map.get(k);
		if (!prev) map.set(k, it);
		else map.set(k, mergeCartRows(prev, it));
	}
	const mergedItems = applyTombstonesToItems([...map.values()], tombstones);
	return {
		updated_at: nowIso(),
		device_id: incoming.device_id || remote.device_id || "unknown",
		items: mergedItems,
		tombstones,
	};
}

function ensureAuthorizedRequest(request, cfg, env, origin) {
	const hdr = (request.headers.get("X-WooliesBot-Secret") || "").trim();
	if (hdr !== cfg.secret) {
		return jsonResponse({ error: "unauthorized" }, 401, env, origin);
	}
	const ip = clientIp(request);
	if (!rateLimitOk(ip)) {
		return jsonResponse({ error: "rate_limited" }, 429, env, origin);
	}
	return null;
}

async function handleUpdateStock(request, env) {
	const cfg = requireConfig(env);
	if (cfg.missing.length) {
		return jsonResponse({ error: "server_misconfigured", missing: cfg.missing }, 500, env, request.headers.get("Origin"));
	}

	const authErr = ensureAuthorizedRequest(request, cfg, env, request.headers.get("Origin"));
	if (authErr) return authErr;

	const cl = request.headers.get("Content-Length");
	if (cl && Number(cl) > MAX_BODY_BYTES) {
		return jsonResponse({ error: "payload_too_large" }, 413, env, request.headers.get("Origin"));
	}

	const buf = await request.arrayBuffer();
	if (buf.byteLength > MAX_BODY_BYTES) {
		return jsonResponse({ error: "payload_too_large" }, 413, env, request.headers.get("Origin"));
	}

	let params;
	try {
		params = JSON.parse(new TextDecoder().decode(buf));
	} catch {
		return jsonResponse({ error: "invalid_json" }, 400, env, request.headers.get("Origin"));
	}

	const newStock = params.stock;
	const itemId = params.item_id;
	const itemName = params.name;
	if (!newStock) {
		return jsonResponse({ error: "bad_request", detail: "stock required" }, 400, env, request.headers.get("Origin"));
	}
	if (!itemId && !itemName) {
		return jsonResponse({ error: "bad_request", detail: "item_id or name required" }, 400, env, request.headers.get("Origin"));
	}

	let attempt = 0;
	const maxAttempts = 6;
	while (attempt < maxAttempts) {
		attempt++;
		const { res: getRes, data: fileMeta } = await githubGetFile(cfg.owner, cfg.repo, cfg.token, DATA_PATH);

		if (getRes.status === 404) {
			return jsonResponse({ error: "github_file_not_found", path: DATA_PATH }, 502, env, request.headers.get("Origin"));
		}
		if (!getRes.ok) {
			return jsonResponse(
				{ error: "github_get_failed", status: getRes.status, message: fileMeta.message || fileMeta },
				502,
				env,
				request.headers.get("Origin"),
			);
		}

		const sha = fileMeta.sha;
		const b64 = fileMeta.content;
		if (!sha || !b64) {
			return jsonResponse({ error: "unexpected_github_payload" }, 502, env, request.headers.get("Origin"));
		}

		let rawDecoded;
		try {
			rawDecoded = JSON.parse(base64ToUtf8(b64.replace(/\n/g, "")));
		} catch {
			return jsonResponse({ error: "data_json_parse_failed" }, 500, env, request.headers.get("Origin"));
		}

		let merge;
		try {
			merge = mergeStockChange(rawDecoded, params);
		} catch (e) {
			const msg = e && e.message === "invalid_json_shape" ? "invalid_json_shape" : "merge_failed";
			return jsonResponse({ error: msg }, 500, env, request.headers.get("Origin"));
		}

		if (!merge.updated) {
			return jsonResponse({ status: "not_found" }, 404, env, request.headers.get("Origin"));
		}

		const outStr = `${JSON.stringify(merge.payload, null, 2)}\n`;
		const contentB64 = utf8ToBase64(outStr);

		const putRes = await githubPutFile(
			cfg.owner,
			cfg.repo,
			cfg.token,
			DATA_PATH,
			"Update stock via WooliesBot write API",
			contentB64,
			sha,
		);

		if (putRes.res.status === 409 || (putRes.data.message && String(putRes.data.message).toLowerCase().includes("sha"))) {
			continue;
		}

		if (!putRes.res.ok) {
			return jsonResponse(
				{
					error: "github_put_failed",
					status: putRes.res.status,
					message: putRes.data.message || putRes.data,
				},
				502,
				env,
				request.headers.get("Origin"),
			);
		}

		return jsonResponse({ status: "success" }, 200, env, request.headers.get("Origin"));
	}

	return jsonResponse({ error: "aborted_after_concurrent_conflicts" }, 409, env, request.headers.get("Origin"));
}

async function readShoppingListPayload(cfg, env, origin) {
	const { res, data } = await githubGetFile(cfg.owner, cfg.repo, cfg.token, SHOPPING_LIST_PATH);
	if (res.status === 404) {
		return { ok: true, sha: null, payload: normalizeShoppingPayload({ items: [], tombstones: {} }) };
	}
	if (!res.ok) {
		return {
			ok: false,
			response: jsonResponse(
				{ error: "github_get_failed", status: res.status, message: data.message || data },
				502,
				env,
				origin,
			),
		};
	}
	const sha = data.sha;
	const b64 = data.content;
	if (!sha || !b64) {
		return { ok: false, response: jsonResponse({ error: "unexpected_github_payload" }, 502, env, origin) };
	}
	let decoded;
	try {
		decoded = JSON.parse(base64ToUtf8(b64.replace(/\n/g, "")));
	} catch {
		return { ok: false, response: jsonResponse({ error: "shopping_list_parse_failed" }, 500, env, origin) };
	}
	return { ok: true, sha, payload: normalizeShoppingPayload(decoded) };
}

async function handleShoppingListGet(request, env) {
	const origin = request.headers.get("Origin");
	const cfg = requireConfig(env);
	if (cfg.missing.length) {
		return jsonResponse({ error: "server_misconfigured", missing: cfg.missing }, 500, env, origin);
	}
	const authErr = ensureAuthorizedRequest(request, cfg, env, origin);
	if (authErr) return authErr;
	const read = await readShoppingListPayload(cfg, env, origin);
	if (!read.ok) return read.response;
	return jsonResponse({ status: "success", payload: read.payload }, 200, env, origin);
}

async function handleShoppingListPost(request, env) {
	const origin = request.headers.get("Origin");
	const cfg = requireConfig(env);
	if (cfg.missing.length) {
		return jsonResponse({ error: "server_misconfigured", missing: cfg.missing }, 500, env, origin);
	}
	const authErr = ensureAuthorizedRequest(request, cfg, env, origin);
	if (authErr) return authErr;
	const cl = request.headers.get("Content-Length");
	if (cl && Number(cl) > MAX_BODY_BYTES) {
		return jsonResponse({ error: "payload_too_large" }, 413, env, origin);
	}
	const buf = await request.arrayBuffer();
	if (buf.byteLength > MAX_BODY_BYTES) {
		return jsonResponse({ error: "payload_too_large" }, 413, env, origin);
	}
	let incomingRaw;
	try {
		incomingRaw = JSON.parse(new TextDecoder().decode(buf));
	} catch {
		return jsonResponse({ error: "invalid_json" }, 400, env, origin);
	}
	const incoming = normalizeShoppingPayload(incomingRaw);
	// Two write modes:
	// - primary snapshot: incoming payload replaces remote cart state
	// - default merge: incoming payload is merged into remote cart state
	const isPrimarySnapshot = Boolean(incomingRaw && incomingRaw.is_cart_primary === true);
	const maxAttempts = 6;
	for (let attempt = 1; attempt <= maxAttempts; attempt++) {
		const read = await readShoppingListPayload(cfg, env, origin);
		if (!read.ok) return read.response;
		const merged = isPrimarySnapshot
			? { ...incoming, updated_at: nowIso() }
			: mergeShoppingPayloads(read.payload, incoming);
		const contentB64 = utf8ToBase64(`${JSON.stringify(merged, null, 2)}\n`);
		const putRes = await githubPutFile(
			cfg.owner,
			cfg.repo,
			cfg.token,
			SHOPPING_LIST_PATH,
			"Sync shopping list via WooliesBot cart API",
			contentB64,
			read.sha,
		);
		if (putRes.res.status === 409 || (putRes.data.message && String(putRes.data.message).toLowerCase().includes("sha"))) {
			continue;
		}
		if (!putRes.res.ok) {
			return jsonResponse(
				{ error: "github_put_failed", status: putRes.res.status, message: putRes.data.message || putRes.data },
				502,
				env,
				origin,
			);
		}
		return jsonResponse({ status: "success", payload: merged }, 200, env, origin);
	}
	return jsonResponse({ error: "aborted_after_concurrent_conflicts" }, 409, env, origin);
}

export default {
	async fetch(request, env) {
		const origin = request.headers.get("Origin");
		const url = new URL(request.url);

		if (request.method === "OPTIONS") {
			return new Response(null, { status: 204, headers: corsHeaders(env, origin) });
		}

		if (url.pathname === "/health" && request.method === "GET") {
			const cfg = requireConfig(env);
			const ok = !cfg.missing.length;
			return jsonResponse({ ok, service: "wooliesbot-write", configured: ok }, ok ? 200 : 503, env, origin);
		}

		if (url.pathname === "/update_stock" && request.method === "POST") {
			return handleUpdateStock(request, env);
		}
		if (url.pathname === "/shopping_list" && request.method === "GET") {
			return handleShoppingListGet(request, env);
		}
		if (url.pathname === "/shopping_list" && request.method === "POST") {
			return handleShoppingListPost(request, env);
		}

		return new Response("Not found", { status: 404, headers: corsHeaders(env, origin) });
	},
};

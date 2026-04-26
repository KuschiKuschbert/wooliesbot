/**
 * WooliesBot write API — Cloudflare Worker.
 * Persists /update_stock writes via GitHub Contents API (docs/data.json).
 *
 * Secrets: GH_TOKEN (or GITHUB_TOKEN), optional WRITE_API_TOKEN(S)
 * Vars: GITHUB_REPO_OWNER, GITHUB_REPO_NAME, ALLOWED_ORIGINS, ALLOWED_USER_EMAILS
 * Optional dev (insecure): ALLOW_INSECURE_PUBLIC_WRITES=1 skips identity/secret auth (rate limit only).
 * Optional rollback: ALLOW_LEGACY_SECRET_AUTH + legacy WOOLIESBOT_WRITE_SECRET*
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

function normalizeShoppingListRows(rows) {
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

function mergeShoppingListRows(existingRows, incomingRows) {
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

function corsPolicy(env, requestOrigin) {
	const allowed = parseAllowedOrigins(env);
	let origin = "*";
	let allowCredentials = "false";
	if (allowed.length) {
		if (requestOrigin && allowed.includes(requestOrigin)) {
			origin = requestOrigin;
			allowCredentials = "true";
		} else if (allowed.length === 1) {
			origin = allowed[0];
			allowCredentials = "true";
		} else {
			origin = "null";
		}
	}
	const headers = {
		"Access-Control-Allow-Origin": origin,
		"Access-Control-Allow-Methods": "GET, POST, OPTIONS",
		"Access-Control-Allow-Headers": "Content-Type, X-Requested-With, X-WooliesBot-Device",
		"Access-Control-Allow-Credentials": allowCredentials,
		"Access-Control-Max-Age": "86400",
	};
	if (allowed.length) headers.Vary = "Origin";
	return {
		headers,
		allowOrigin: origin,
		allowCredentials: allowCredentials === "true",
		allowedOrigins: allowed,
	};
}

function corsHeaders(env, requestOrigin) {
	return corsPolicy(env, requestOrigin).headers;
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

function envTruthy(v) {
	return String(v || "").trim().toLowerCase() === "1" || String(v || "").trim().toLowerCase() === "true";
}

function parseAllowedEmails(env) {
	return String(env.ALLOWED_USER_EMAILS || "")
		.split(",")
		.map((s) => s.trim().toLowerCase())
		.filter(Boolean);
}

function parseWriteApiTokens(env) {
	const combined = [
		String(env.WRITE_API_TOKEN || "").trim(),
		String(env.WRITE_API_TOKENS || "").trim(),
	]
		.filter(Boolean)
		.join(",");
	if (!combined) return [];
	return combined
		.split(",")
		.map((s) => s.trim())
		.filter(Boolean);
}

function accessApiToken(request) {
	const auth = String(request.headers.get("Authorization") || "").trim();
	if (auth.toLowerCase().startsWith("bearer ")) {
		return auth.slice(7).trim();
	}
	return String(request.headers.get("X-WooliesBot-Token") || "").trim();
}

function accessIdentityEmail(request) {
	return (
		request.headers.get("CF-Access-Authenticated-User-Email")
		|| request.headers.get("X-Auth-Request-Email")
		|| ""
	).trim().toLowerCase();
}

function requireConfig(env) {
	const owner = (env.GITHUB_REPO_OWNER || "").trim();
	const repo = (env.GITHUB_REPO_NAME || "").trim();
	const token = githubToken(env);
	const allowedEmails = parseAllowedEmails(env);
	const writeApiTokens = parseWriteApiTokens(env);
	const allowInsecurePublicWrites = envTruthy(env.ALLOW_INSECURE_PUBLIC_WRITES);
	const allowLegacySecretAuth = envTruthy(env.ALLOW_LEGACY_SECRET_AUTH);
	const legacySecret = (env.WOOLIESBOT_WRITE_SECRET || "").trim();
	const legacyPreviousSecret = (env.WOOLIESBOT_WRITE_SECRET_PREVIOUS || "").trim();
	const missing = [];
	if (!owner) missing.push("GITHUB_REPO_OWNER");
	if (!repo) missing.push("GITHUB_REPO_NAME");
	if (!token) missing.push("GH_TOKEN");
	if (!allowedEmails.length && !allowLegacySecretAuth && !allowInsecurePublicWrites) {
		missing.push("ALLOWED_USER_EMAILS");
	}
	if (allowLegacySecretAuth && !legacySecret) missing.push("WOOLIESBOT_WRITE_SECRET");
	return {
		owner,
		repo,
		token,
		allowedEmails,
		writeApiTokens,
		allowInsecurePublicWrites,
		allowLegacySecretAuth,
		legacySecret,
		legacyPreviousSecret,
		missing,
	};
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

async function decodeGithubJsonFile(fileMeta) {
	const sha = fileMeta?.sha;
	const b64 = fileMeta?.content;
	if (sha && b64) {
		return { sha, raw: JSON.parse(base64ToUtf8(String(b64).replace(/\n/g, ""))) };
	}
	const downloadUrl = String(fileMeta?.download_url || "").trim();
	if (!sha || !downloadUrl) throw new Error("unexpected_github_payload");
	const res = await fetch(downloadUrl, {
		headers: {
			Accept: "application/json",
			"User-Agent": "wooliesbot-write-worker",
		},
	});
	if (!res.ok) throw new Error("github_download_failed");
	const text = await res.text();
	return { sha, raw: JSON.parse(text) };
}


function ensureAuthorizedRequest(request, cfg, env, origin) {
	const apiToken = accessApiToken(request);
	if (apiToken && cfg.writeApiTokens.includes(apiToken)) {
		const suffix = apiToken.slice(-6) || "token";
		if (!rateLimitOk(`token:${suffix}`)) {
			return jsonResponse({ error: "rate_limited" }, 429, env, origin);
		}
		return null;
	}

	if (cfg.allowInsecurePublicWrites) {
		const ip = clientIp(request);
		if (!rateLimitOk(`open:${ip}`)) {
			return jsonResponse({ error: "rate_limited" }, 429, env, origin);
		}
		return null;
	}

	const email = accessIdentityEmail(request);
	if (email && cfg.allowedEmails.includes(email)) {
		const ipByIdentity = `id:${email}`;
		if (!rateLimitOk(ipByIdentity)) {
			return jsonResponse({ error: "rate_limited" }, 429, env, origin);
		}
		return null;
	}

	if (cfg.allowLegacySecretAuth) {
		const hdr = (request.headers.get("X-WooliesBot-Secret") || "").trim();
		const allowedSecrets = [cfg.legacySecret];
		if (cfg.legacyPreviousSecret) allowedSecrets.push(cfg.legacyPreviousSecret);
		if (allowedSecrets.includes(hdr)) {
			const ip = clientIp(request);
			if (!rateLimitOk(ip)) {
				return jsonResponse({ error: "rate_limited" }, 429, env, origin);
			}
			return null;
		}
	}
	return jsonResponse({ error: "unauthorized" }, 401, env, origin);
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

		try {
			const decoded = await decodeGithubJsonFile(fileMeta);
			const merge = mergeStockChange(decoded.raw, params);
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
				decoded.sha,
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
		} catch {
			return jsonResponse({ error: "data_json_parse_failed" }, 500, env, request.headers.get("Origin"));
		}
	}

	return jsonResponse({ error: "aborted_after_concurrent_conflicts" }, 409, env, request.headers.get("Origin"));
}

async function handleGetShoppingList(request, env) {
	const cfg = requireConfig(env);
	if (cfg.missing.length) {
		return jsonResponse({ error: "server_misconfigured", missing: cfg.missing }, 500, env, request.headers.get("Origin"));
	}
	const authErr = ensureAuthorizedRequest(request, cfg, env, request.headers.get("Origin"));
	if (authErr) return authErr;

	const { res: getRes, data: fileMeta } = await githubGetFile(cfg.owner, cfg.repo, cfg.token, SHOPPING_LIST_PATH);
	if (getRes.status === 404) {
		return jsonResponse({ schema: 1, updated_at: "", updated_by: "", items: [] }, 200, env, request.headers.get("Origin"));
	}
	if (!getRes.ok) {
		return jsonResponse(
			{ error: "github_get_failed", status: getRes.status, message: fileMeta.message || fileMeta },
			502,
			env,
			request.headers.get("Origin"),
		);
	}

	try {
		const decoded = JSON.parse(base64ToUtf8(String(fileMeta.content || "").replace(/\n/g, "")));
		const items = normalizeShoppingListRows(decoded?.items || []);
		return jsonResponse(
			{
				schema: 1,
				updated_at: decoded?.updated_at || "",
				updated_by: decoded?.updated_by || "",
				items,
			},
			200,
			env,
			request.headers.get("Origin"),
		);
	} catch {
		return jsonResponse({ error: "shopping_list_parse_failed" }, 500, env, request.headers.get("Origin"));
	}
}

async function handleUpsertShoppingList(request, env) {
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

	let body;
	try {
		body = await request.json();
	} catch {
		return jsonResponse({ error: "invalid_json" }, 400, env, request.headers.get("Origin"));
	}

	const incomingItems = normalizeShoppingListRows(body?.items || []);
	const deviceId = String(body?.device_id || "").trim() || "unknown";

	let attempt = 0;
	const maxAttempts = 6;
	while (attempt < maxAttempts) {
		attempt++;
		const { res: getRes, data: fileMeta } = await githubGetFile(cfg.owner, cfg.repo, cfg.token, SHOPPING_LIST_PATH);
		let sha = null;
		let existingItems = [];
		if (getRes.status === 404) {
			sha = null;
		} else if (!getRes.ok) {
			return jsonResponse(
				{ error: "github_get_failed", status: getRes.status, message: fileMeta.message || fileMeta },
				502,
				env,
				request.headers.get("Origin"),
			);
		} else {
			sha = fileMeta.sha || null;
			try {
				const decoded = JSON.parse(base64ToUtf8(String(fileMeta.content || "").replace(/\n/g, "")));
				existingItems = normalizeShoppingListRows(decoded?.items || []);
			} catch {
				existingItems = [];
			}
		}

		const mergedItems = mergeShoppingListRows(existingItems, incomingItems);
		const updatedAt = new Date().toISOString();
		const payload = {
			schema: 1,
			updated_at: updatedAt,
			updated_by: deviceId,
			items: mergedItems,
		};
		const outStr = `${JSON.stringify(payload, null, 2)}\n`;
		const contentB64 = utf8ToBase64(outStr);
		const putRes = await githubPutFile(
			cfg.owner,
			cfg.repo,
			cfg.token,
			SHOPPING_LIST_PATH,
			"Sync shopping list via WooliesBot write API",
			contentB64,
			sha,
		);

		if (putRes.res.status === 409 || (putRes.data.message && String(putRes.data.message).toLowerCase().includes("sha"))) {
			continue;
		}
		if (!putRes.res.ok) {
			return jsonResponse(
				{ error: "github_put_failed", status: putRes.res.status, message: putRes.data.message || putRes.data },
				502,
				env,
				request.headers.get("Origin"),
			);
		}
		return jsonResponse(
			{
				status: "success",
				merge_mode: "item_level_latest_updated_at",
				updated_at: updatedAt,
				item_count: mergedItems.length,
				received_item_count: incomingItems.length,
			},
			200,
			env,
			request.headers.get("Origin"),
		);
	}

	return jsonResponse({ error: "aborted_after_concurrent_conflicts" }, 409, env, request.headers.get("Origin"));
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
			const authMode = cfg.allowInsecurePublicWrites
				? "insecure_public"
				: "identity_allowlist";
			const cors = corsPolicy(env, origin);
			return jsonResponse(
				{
					ok,
					service: "wooliesbot-write",
					configured: ok,
					auth_mode: authMode,
					allowed_user_count: cfg.allowedEmails.length,
					token_auth_enabled: cfg.writeApiTokens.length > 0,
					token_auth_count: cfg.writeApiTokens.length,
					legacy_secret_fallback_enabled: cfg.allowLegacySecretAuth,
					insecure_public_writes: cfg.allowInsecurePublicWrites,
					cors: {
						request_origin: origin || "",
						allow_origin: cors.allowOrigin,
						allow_credentials: cors.allowCredentials,
						allowed_origin_count: cors.allowedOrigins.length,
					},
				},
				ok ? 200 : 503,
				env,
				origin,
			);
		}

		if (url.pathname === "/update_stock" && request.method === "POST") {
			return handleUpdateStock(request, env);
		}

		if (url.pathname === "/shopping_list" && request.method === "GET") {
			return handleGetShoppingList(request, env);
		}

		if (url.pathname === "/shopping_list" && request.method === "POST") {
			return handleUpsertShoppingList(request, env);
		}

		return new Response("Not found", { status: 404, headers: corsHeaders(env, origin) });
	},
};

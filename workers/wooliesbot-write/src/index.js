/**
 * WooliesBot write API — Cloudflare Worker.
 * Persists /update_stock writes via GitHub Contents API (docs/data.json).
 *
 * Secrets: GH_TOKEN (or GITHUB_TOKEN), WOOLIESBOT_WRITE_SECRET
 * Vars: GITHUB_REPO_OWNER, GITHUB_REPO_NAME, ALLOWED_ORIGINS (comma-separated)
 */

const DATA_PATH = "docs/data.json";
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
			sha,
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

async function handleUpdateStock(request, env) {
	const cfg = requireConfig(env);
	if (cfg.missing.length) {
		return jsonResponse({ error: "server_misconfigured", missing: cfg.missing }, 500, env, request.headers.get("Origin"));
	}

	const hdr = (request.headers.get("X-WooliesBot-Secret") || "").trim();
	if (hdr !== cfg.secret) {
		return jsonResponse({ error: "unauthorized" }, 401, env, request.headers.get("Origin"));
	}

	const ip = clientIp(request);
	if (!rateLimitOk(ip)) {
		return jsonResponse({ error: "rate_limited" }, 429, env, request.headers.get("Origin"));
	}

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

		return new Response("Not found", { status: 404, headers: corsHeaders(env, origin) });
	},
};

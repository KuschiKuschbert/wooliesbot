function parseAllowedOrigins(env) {
	const raw = (env.ALLOWED_ORIGINS || "").trim();
	if (!raw) return [];
	return raw
		.split(",")
		.map((s) => s.trim())
		.filter(Boolean);
}

export function corsPolicy(env, requestOrigin) {
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

export function corsHeaders(env, requestOrigin) {
	return corsPolicy(env, requestOrigin).headers;
}

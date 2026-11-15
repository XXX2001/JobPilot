const BASE = import.meta.env.VITE_API_BASE_URL ?? '';

const UNSAFE = new Set(['POST', 'PUT', 'PATCH', 'DELETE']);
let session: Promise<void> | null = null;

/**
 * W07: obtain the local session cookie (HttpOnly, SameSite=Strict) that the
 * backend requires for every command and the WebSocket. Memoized; a 401
 * resets it so the next command re-establishes the session.
 */
export function ensureSession(): Promise<void> {
	session ??= fetch(BASE + '/api/session', { credentials: 'include' }).then(
		(r) => {
			if (!r.ok) throw new Error(`session ${r.status}`);
		},
		(e) => {
			session = null;
			throw e;
		}
	);
	return session;
}

/** Forget the memoized session so the next command/handshake re-establishes it. */
export function resetSession(): void {
	session = null;
}

export class ApiError extends Error {
	constructor(
		readonly status: number,
		body: string
	) {
		super(`API error ${status}: ${body}`);
	}
}

export async function apiFetch<T>(path: string, options?: RequestInit): Promise<T> {
	// When the body is FormData, NEVER hardcode Content-Type: the browser must
	// set `multipart/form-data; boundary=...` itself. Forcing application/json
	// here would corrupt every multipart upload (PG-PRE / CV upload regression).
	const isFormData = typeof FormData !== 'undefined' && options?.body instanceof FormData;

	const headers: HeadersInit = isFormData
		? { ...options?.headers }
		: { 'Content-Type': 'application/json', ...options?.headers };

	const unsafe = UNSAFE.has((options?.method ?? 'GET').toUpperCase());
	if (unsafe) await ensureSession();
	const send = () => fetch(BASE + path, { credentials: 'include', ...options, headers });

	let res = await send();
	if (res.status === 401 && unsafe) {
		// The server's secret changed (e.g. data dir reset): re-establish once.
		session = null;
		await ensureSession();
		res = await send();
	}

	if (!res.ok) {
		throw new ApiError(res.status, await res.text());
	}

	if (res.status === 204) return undefined as T;

	return res.json() as Promise<T>;
}

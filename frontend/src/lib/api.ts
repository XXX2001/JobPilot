const BASE = import.meta.env.VITE_API_BASE_URL ?? '';

export async function apiFetch<T>(path: string, options?: RequestInit): Promise<T> {
  // When the body is FormData, NEVER hardcode Content-Type: the browser must
  // set `multipart/form-data; boundary=...` itself. Forcing application/json
  // here would corrupt every multipart upload (PG-PRE / CV upload regression).
  const isFormData =
    typeof FormData !== 'undefined' && options?.body instanceof FormData;

  const headers: HeadersInit = isFormData
    ? { ...options?.headers }
    : { 'Content-Type': 'application/json', ...options?.headers };

  const res = await fetch(BASE + path, {
    ...options,
    headers
  });

  if (!res.ok) {
    throw new Error(`API error ${res.status}: ${await res.text()}`);
  }

  return res.json() as Promise<T>;
}

export class ApiError extends Error {
  constructor(public code: string, message: string, public status: number) {
    super(message);
  }
}

async function parse(res: Response) {
  const text = await res.text();
  let body: any = null;
  try {
    body = text ? JSON.parse(text) : null;
  } catch {
    body = null;
  }
  if (!res.ok) {
    const e = body?.error;
    throw new ApiError(e?.code ?? "http_error", e?.message ?? `Errore ${res.status}`, res.status);
  }
  return body;
}

export async function api<T = any>(path: string, init?: RequestInit & { json?: unknown }): Promise<T> {
  const { json, ...rest } = init ?? {};
  const res = await fetch(path, {
    ...rest,
    headers: { ...(json !== undefined ? { "Content-Type": "application/json" } : {}), ...(rest.headers ?? {}) },
    body: json !== undefined ? JSON.stringify(json) : rest.body,
  });
  return parse(res);
}

export const get = <T = any>(p: string) => api<T>(p);
export const post = <T = any>(p: string, json?: unknown) => api<T>(p, { method: "POST", json: json ?? {} });
export const patch = <T = any>(p: string, json: unknown) => api<T>(p, { method: "PATCH", json });
export const put = <T = any>(p: string, json: unknown) => api<T>(p, { method: "PUT", json });

export async function postBlob(path: string, json: unknown): Promise<Blob> {
  const res = await fetch(path, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(json) });
  if (!res.ok) await parse(res);
  return res.blob();
}

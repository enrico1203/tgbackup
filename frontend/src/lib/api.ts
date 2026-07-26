const TOKEN_KEY = "tgbackup.token";

export function getToken(): string | null {
  return localStorage.getItem(TOKEN_KEY);
}

export function setToken(token: string): void {
  localStorage.setItem(TOKEN_KEY, token);
}

export function clearToken(): void {
  localStorage.removeItem(TOKEN_KEY);
}

export class ApiError extends Error {
  status: number;

  constructor(status: number, message: string) {
    super(message);
    this.status = status;
  }
}

const LOGIN_PATH = "/api/auth/login";

async function send(path: string, init: RequestInit, json: boolean): Promise<Response> {
  const token = getToken();
  const headers = new Headers(init.headers);
  // On a FormData body the header is left alone: the browser has to add the multipart
  // boundary itself, and setting it by hand makes the body unparsable.
  if (json) headers.set("Content-Type", "application/json");
  if (token) headers.set("Authorization", `Bearer ${token}`);

  const response = await fetch(path, { ...init, headers });

  // A 401 on sign in means wrong credentials, not an expired session: the backend
  // message must be shown instead of reloading the page.
  if (response.status === 401 && path !== LOGIN_PATH) {
    clearToken();
    window.location.reload();
    throw new ApiError(401, "Session expired");
  }
  return response;
}

function detailOf(body: unknown): string {
  const detail = (body as { detail?: unknown } | null)?.detail;
  if (typeof detail === "string") return detail;
  if (Array.isArray(detail)) return detail.map((d: { msg: string }) => d.msg).join(", ");
  return "Request failed";
}

async function request<T>(path: string, init: RequestInit = {}): Promise<T> {
  const response = await send(path, init, true);
  if (response.status === 204) return undefined as T;

  const body = await response.json().catch(() => null);
  if (!response.ok) throw new ApiError(response.status, detailOf(body));
  return body as T;
}

/** Multipart POST, the only way a file reaches the backend from the browser. */
export async function upload<T>(path: string, form: FormData): Promise<T> {
  const response = await send(path, { method: "POST", body: form }, false);
  const body = await response.json().catch(() => null);
  if (!response.ok) throw new ApiError(response.status, detailOf(body));
  return body as T;
}

/** Download of a binary body. The name comes from Content-Disposition, which the backend
 *  exposes on purpose so the file is saved with the name it was built with. */
export async function download(path: string): Promise<{ blob: Blob; filename: string }> {
  const response = await send(path, {}, false);
  if (!response.ok) {
    const body = await response.json().catch(() => null);
    throw new ApiError(response.status, detailOf(body));
  }
  const disposition = response.headers.get("Content-Disposition") ?? "";
  const match = /filename="?([^";]+)"?/.exec(disposition);
  return { blob: await response.blob(), filename: match ? match[1] : "download" };
}

/** Hands the blob to the browser as a save dialog. */
export function saveBlob(blob: Blob, filename: string): void {
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = filename;
  link.click();
  // Revoked right after: the object URL keeps the whole blob in memory until it is.
  URL.revokeObjectURL(url);
}

export const api = {
  get: <T>(path: string) => request<T>(path),
  post: <T>(path: string, body?: unknown) =>
    request<T>(path, { method: "POST", body: body ? JSON.stringify(body) : undefined }),
  put: <T>(path: string, body: unknown) =>
    request<T>(path, { method: "PUT", body: JSON.stringify(body) }),
  patch: <T>(path: string, body: unknown) =>
    request<T>(path, { method: "PATCH", body: JSON.stringify(body) }),
  del: (path: string) => request<void>(path, { method: "DELETE" }),
};

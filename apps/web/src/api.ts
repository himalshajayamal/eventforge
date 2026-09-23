export const API_BASE = import.meta.env.VITE_API_BASE_URL ?? "http://localhost:8000";

export type AuthState =
  | { mode: "admin"; username: string; password: string }
  | { mode: "project"; apiKey: string; projectId: string };

export interface Project {
  id: string;
  name: string;
  created_at: string;
}

export interface ProjectSummary {
  project: Project;
  counts: {
    webhook_endpoints: number;
    events: number;
    jobs: number;
    pending_jobs: number;
    dead_lettered_jobs: number;
    replays: number;
    workflows: number;
    workflow_runs: number;
  };
}

export interface Endpoint {
  id: string;
  project_id: string;
  name: string;
  source: string;
  token_prefix: string;
  enabled: boolean;
  verify_signature: boolean;
  signing_secret_version: number;
  signature_tolerance_seconds: number;
  created_at: string;
  last_received_at: string | null;
}

export interface EventRow {
  id: string;
  project_id: string;
  endpoint_id: string | null;
  source: string;
  type: string;
  provider_delivery_id: string | null;
  trace_id: string;
  received_at: string;
  content_type: string;
  size_bytes: number;
  sha256: string;
}

export interface JobRow {
  id: string;
  project_id: string;
  event_id: string | null;
  replay_execution_id: string | null;
  workflow_run_id: string | null;
  step_run_id: string | null;
  kind: string;
  status: string;
  attempt_count: number;
  max_attempts: number;
  available_at: string;
  lease_owner: string | null;
  lease_expires_at: string | null;
  last_error: string | null;
  created_at: string;
  updated_at: string;
  completed_at: string | null;
}

export interface ReplayRow {
  id: string;
  project_id: string;
  replay_of: string;
  workflow_version_mode: "original" | "current";
  workflow_selection_snapshot_at: string | null;
  created_at: string;
  job_id: string;
  job_status: string;
  workflow_selection_count: number;
}

export interface WorkflowRow {
  id: string;
  project_id: string;
  name: string;
  enabled: boolean;
  active_version_id: string | null;
  active_version_number: number | null;
  trigger_source: string | null;
  trigger_type: string | null;
  created_at: string;
  updated_at: string;
}

export interface WorkflowVersion {
  id: string;
  project_id: string;
  workflow_id: string;
  version_number: number;
  trigger_source: string;
  trigger_type: string;
  definition: Record<string, unknown>;
  created_at: string;
  active: boolean;
}

export interface WorkflowRun {
  id: string;
  project_id: string;
  workflow_id: string;
  workflow_version_id: string;
  version_number: number;
  event_id: string;
  replay_execution_id: string | null;
  status: string;
  error: string | null;
  created_at: string;
  started_at: string | null;
  completed_at: string | null;
  context?: Record<string, unknown>;
}

export interface StepRun {
  id: string;
  project_id: string;
  workflow_run_id: string;
  step_index: number;
  step_type: string;
  config: Record<string, unknown>;
  status: string;
  output: Record<string, unknown> | null;
  error: string | null;
  started_at: string | null;
  completed_at: string | null;
}

export interface ApiKeyRow {
  id: string;
  project_id: string;
  name: string;
  key_prefix: string;
  created_at: string;
  last_used_at: string | null;
  revoked_at: string | null;
}

export class ApiError extends Error {
  status: number;
  detail: string;

  constructor(status: number, detail: string) {
    super(detail);
    this.name = "ApiError";
    this.status = status;
    this.detail = detail;
  }
}

function base64Utf8(value: string): string {
  const bytes = new TextEncoder().encode(value);
  let binary = "";
  for (const byte of bytes) binary += String.fromCharCode(byte);
  return btoa(binary);
}

function authHeader(auth: AuthState): string {
  if (auth.mode === "admin") {
    return `Basic ${base64Utf8(`${auth.username}:${auth.password}`)}`;
  }
  return `Bearer ${auth.apiKey}`;
}

export async function publicGet<T>(path: string): Promise<T> {
  const response = await fetch(`${API_BASE}${path}`);
  if (!response.ok) throw new ApiError(response.status, `HTTP ${response.status}`);
  return response.json() as Promise<T>;
}

export async function apiRequest<T>(
  auth: AuthState,
  path: string,
  init: RequestInit = {},
): Promise<T> {
  const headers = new Headers(init.headers ?? {});
  headers.set("Authorization", authHeader(auth));
  if (init.body !== undefined && !headers.has("Content-Type")) {
    headers.set("Content-Type", "application/json");
  }

  const response = await fetch(`${API_BASE}${path}`, { ...init, headers });
  if (!response.ok) {
    let detail = `HTTP ${response.status}`;
    try {
      const body = (await response.json()) as { detail?: string };
      if (body.detail) detail = body.detail;
    } catch {
      // Keep generic HTTP error when the response has no JSON body.
    }
    throw new ApiError(response.status, detail);
  }
  if (response.status === 204) return undefined as T;
  return response.json() as Promise<T>;
}

export function postJson<T>(auth: AuthState, path: string, body?: unknown): Promise<T> {
  return apiRequest<T>(auth, path, {
    method: "POST",
    body: body === undefined ? undefined : JSON.stringify(body),
  });
}

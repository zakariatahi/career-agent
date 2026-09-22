export type Profile = { target_roles: string[]; skills: string[]; education: string | null; experience: string[]; preferred_locations: string[]; remote: boolean };
export type Job = { title: string; company: string; url: string; source: string; location?: string; description?: string; match_score?: number; match_reasons?: string[]; missing_skills?: string[]; requirements?: string[] };
export type Change = { original_text: string; proposed_text: string; reason: string };
export type Draft = { selected_index?: number; approved_indices?: number[]; edited_changes?: Record<string, string>; cv_view?: "side" | "changes"; recipient?: string; subject?: string; body?: string };
export type Step = "search" | "job_review" | "cv_tailoring" | "email_review" | "send" | "submitted";
export type Workflow = { id: string; revision: number; step: Step; status: "idle" | "running" | "error"; error?: string; last_action?: string; search: {query: string; location: string}; jobs: Job[]; selected_job?: Job; cv_source: string; tailored_source?: string; changes?: Change[]; draft: Draft; contact?: {method: string; email?: string; application_url?: string}; pdf_ready?: boolean; updated_at: string; no_jobs_message?: string };
export type Application = { id: number; title: string; company: string; job_url: string; location?: string; source: string; status: string; submitted_at?: string; created_at: string; match_score?: number; recipient?: string };
export type Reply = { message_id: string; sender: string; subject: string; received_at: string; response: string; classification?: string; classification_label?: string };
export type ApplicationDetail = Application & { details: {subject?: string; body?: string; recipient?: string}; responses: Reply[]; history: {id: number; status: string; detail: string; created_at: string}[] };
export type Settings = { profile: Profile; cv_source: string; gmail_saved: boolean; gmail_connecting: boolean; gmail_error?: string; inbox: { enabled: boolean; interval_minutes: number; last_checked?: number; error?: string } };

export async function api<T>(path: string, options?: RequestInit): Promise<T> {
  const response = await fetch(`/api${path}`, { ...options, headers: { "Content-Type": "application/json", ...options?.headers }, cache: "no-store" });
  if (!response.ok) {
    const error = await response.json().catch(() => null);
    throw new Error(typeof error?.detail === "string" ? error.detail : error?.detail ? "Please check the form values." : "Could not reach CareerAI. Check that the API is running.");
  }
  return response.json();
}
export const post = <T>(path: string, body: unknown = {}) => api<T>(path, { method: "POST", body: JSON.stringify(body) });
export const put = <T>(path: string, body: unknown) => api<T>(path, { method: "PUT", body: JSON.stringify(body) });
export const patch = <T>(path: string, body: unknown) => api<T>(path, { method: "PATCH", body: JSON.stringify(body) });
export const steps = ["Search jobs", "Job review", "CV tailoring", "Email review", "Send application"];
export const stepIndex = (step: Step) => ({search: 0, job_review: 1, cv_tailoring: 2, email_review: 3, send: 4, submitted: 5})[step];
export const stepLabel = (step: Step) => steps[stepIndex(step)] || "Submitted";
export const dateLabel = (date?: string) => date ? new Date(date).toLocaleDateString("en-GB", { day: "numeric", month: "short", year: "numeric" }) : "—";
export function safeUrl(value?: string) { try { const url = new URL(value || ""); return ["http:", "https:"].includes(url.protocol) ? url.href : undefined; } catch { return undefined; } }

const HTML_ENTITIES: Record<string, string> = {
  amp: "&", apos: "'", gt: ">", lt: "<", nbsp: " ", quot: '"',
};

export function plainText(value?: string) {
  if (!value) return "";
  return value
    .replace(/&(#x[\da-f]+|#\d+|amp|apos|gt|lt|nbsp|quot);/gi, (_, entity: string) => {
      const normalized = entity.toLowerCase();
      if (normalized.startsWith("#")) {
        const codePoint = Number.parseInt(normalized.slice(normalized[1] === "x" ? 2 : 1), normalized[1] === "x" ? 16 : 10);
        return codePoint >= 0 && codePoint <= 0x10ffff ? String.fromCodePoint(codePoint) : "";
      }
      return HTML_ENTITIES[normalized] ?? `&${entity};`;
    })
    .replace(/<\s*br\s*\/?\s*>/gi, "\n")
    .replace(/<\s*li(?:\s[^>]*)?>/gi, "\n• ")
    .replace(/<\s*\/\s*(?:p|div|section|li|ul|ol|h[1-6])\s*>/gi, "\n")
    .replace(/<\/?[a-z][^>]*>/gi, " ")
    .replace(/<!--[\s\S]*?-->/g, " ")
    .replace(/[ \t]+/g, " ")
    .replace(/ *\n */g, "\n")
    .replace(/\n{3,}/g, "\n\n")
    .trim();
}

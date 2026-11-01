import { apiFetch } from "$lib/api";

export type GmailStatus = {
  connected: boolean;
  email_address: string | null;
  last_synced_at: string | null;
  history_id: string | null;
  message_count: number;
  enabled: boolean;
};

export async function fetchGmailStatus(): Promise<GmailStatus> {
  const r = await fetch("/api/gmail/status");
  if (!r.ok) throw new Error(`gmail status ${r.status}`);
  return r.json();
}

// W07: commands go through apiFetch so they carry the local session.
export async function forceSync(): Promise<{ synced: number }> {
  return apiFetch("/api/gmail/sync", { method: "POST" });
}

export async function disconnect(email: string): Promise<void> {
  await apiFetch("/api/gmail/disconnect", { method: "POST", body: JSON.stringify({ email }) });
}

export type UnlinkedItem = {
  id: number;
  gmail_message_id: string;
  gmail_thread_id: string;
  from_address: string;
  subject: string | null;
  snippet: string | null;
  received_at: string;
  category: string | null;
  category_confidence: number | null;
};

export async function fetchUnlinked(): Promise<UnlinkedItem[]> {
  const r = await fetch("/api/correspondence/unlinked");
  if (!r.ok) throw new Error(`unlinked ${r.status}`);
  const body = await r.json();
  return body.items;
}

export async function linkMessage(application_id: number, gmail_message_id: number) {
  return apiFetch("/api/correspondence/link", {
    method: "POST",
    body: JSON.stringify({ application_id, gmail_message_id }),
  });
}

export async function fetchThread(application_id: number) {
  const r = await fetch(`/api/correspondence/${application_id}`);
  if (!r.ok) throw new Error(`thread ${r.status}`);
  return r.json();
}

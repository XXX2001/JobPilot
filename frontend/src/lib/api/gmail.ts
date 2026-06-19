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

export async function forceSync(): Promise<{ synced: number }> {
  const r = await fetch("/api/gmail/sync", { method: "POST" });
  if (!r.ok) throw new Error(`gmail sync ${r.status}`);
  return r.json();
}

export async function disconnect(email: string): Promise<void> {
  const r = await fetch("/api/gmail/disconnect", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ email }),
  });
  if (!r.ok) throw new Error(`gmail disconnect ${r.status}`);
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
  const r = await fetch("/api/correspondence/link", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ application_id, gmail_message_id }),
  });
  if (!r.ok) throw new Error(`link ${r.status}`);
  return r.json();
}

export async function fetchThread(application_id: number) {
  const r = await fetch(`/api/correspondence/${application_id}`);
  if (!r.ok) throw new Error(`thread ${r.status}`);
  return r.json();
}

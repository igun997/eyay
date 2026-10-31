export type Severity = "CRITICAL" | "HIGH" | "MEDIUM" | "LOW";
export type EventStatus = "ready_for_triage" | "confirmed" | "dismissed";
export type IncidentStatus = "open" | "acknowledged" | "resolved";
export type Pipeline = "queued" | "classifying" | "ready" | "failed" | "skipped";
export type ClsLabel = "person" | "vehicle" | "false_alarm";
export type SourceStatus = "pending" | "connecting" | "online" | "offline" | "error";
export type SourceKind = "rtsp" | "m3u8";

export type BBox = { x: number; y: number; w: number; h: number };

export type EventRow = {
  id: string;
  code: string;
  title: string;
  label: string;
  confidence: number;
  severity: Severity;
  status: EventStatus;
  bbox: BBox | null;
  detected_at: string;
  source_id: string;
  source_code: string;
  source_name: string;
  incident_id: string | null;
  incident_code: string | null;
  dismiss_reason: string | null;
  note: string | null;
  track_id: number | null;
  snapshots: number;
  pipeline: Pipeline;
  cls_label: ClsLabel | null;
  cls_confidence: number | null;
  cls_probs: Record<string, number> | null;
  cls_model: string | null;
  cls_ms: number | null;
  cls_error: string | null;
};

export type IncidentRow = {
  id: string;
  code: string;
  title: string;
  severity: Severity;
  status: IncidentStatus;
  label: string | null;
  source_id: string | null;
  source_code: string | null;
  source_name: string | null;
  assigned_to: string | null;
  assignee_name: string | null;
  opened_at: string;
  resolved_at: string | null;
  events: number;
};

export type TimelineRow = {
  id: number;
  kind: string;
  title: string;
  detail: string | null;
  at: string;
  user_name: string | null;
  event_id: string | null;
};

export type SourceRow = {
  id: string;
  code: string;
  name: string;
  url: string;
  kind: SourceKind;
  labels: string[];
  min_confidence: number;
  enabled: boolean;
  status: SourceStatus;
  last_error: string | null;
  last_seen: string | null;
  fps: number | null;
  inference_ms: number | null;
};

export type CategoryRow = {
  label: string;
  title: string;
  severity: Severity;
  opens_incident: boolean;
  cooldown_s: number;
  enabled: boolean;
};

export type UserRow = { id: string; email: string; name: string; role: "admin" | "security"; active: boolean; created_at: string };

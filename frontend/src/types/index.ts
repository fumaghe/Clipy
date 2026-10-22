export interface Clip {
  id: string;
  source_clip_id: string;
  creator_name: string;
  game_name: string;
  twitch_url: string;
  title: string;
  created_at_utc: string;
  duration: number;
  status: string;
  error: string | null;
  is_demo: boolean;
  demo_scenario: string;
  portrait_available: boolean | null;
  is_portrait_file: boolean;
  landscape_fallback: boolean;
  overlay_title: string | null;
  caption: string | null;
  hashtags: string[];
  template_id: string | null;
  content_version: number;
  paused: boolean;
  priority: number;
  has_original: boolean;
  has_render: boolean;
  transcript_excerpt: string;
  confidence: number | null;
  analysis_simulated: boolean;
  review_state: string | null;
  review_reasons: { code: string; message: string }[];
  publication_status: string | null;
  publication_due_at: string | null;
}

export interface ReviewItem extends Clip {
  transcript: string;
  visual_notes: string[];
  scene_summary: string | null;
  analysis_provider: string | null;
  review: { state: string; reasons: { code: string; message: string }[]; flags: string[]; policy_version: string } | null;
}

export interface Job {
  id: string;
  clip_id: string;
  provider: string;
  caption: string;
  hashtags: string[];
  due_at: string;
  status: string;
  paused: boolean;
  simulated: boolean;
  remote_post_id: string | null;
  remote_status: string | null;
  remote_url: string | null;
  attempts: number;
  next_attempt_at: string | null;
  error: string | null;
  last_reconciled_at: string | null;
  remote_state?: string;
  verification?: string;
  clip_title: string | null;
  overlay_title: string | null;
}

export interface SystemStatus {
  demo_mode: boolean;
  publish_mode: "mock" | "dry_run" | "live";
  dry_run: boolean;
  live_enabled: boolean;
  collector_enabled: boolean;
  worker: { running: boolean; leader: boolean };
  twitch: { login: string; configured: boolean; authorized: boolean; broadcaster_id: string; last_scan_at: string | null; last_error: string | null; simulated: boolean };
  buffer: { connected: boolean; channel_id: string; provider: string };
  storage: { provider: string; public: boolean };
  ffmpeg: string | null;
  ffprobe: string | null;
  asr: { faster_whisper_installed: boolean; cuda_available: boolean; model: string };
  counts: Record<string, number>;
  next_slots: string[];
  daily_max: number;
}

export interface TemplateConfig {
  canvas_width: number;
  canvas_height: number;
  landscape_mode: "crop" | "letterbox";
  crop_offset_x: number;
  crop_offset_y: number;
  font_file: string;
  font_size: number;
  min_font_size: number;
  text_color: string;
  stroke_color: string;
  stroke_width: number;
  shadow: boolean;
  shadow_color: string;
  shadow_offset: number;
  background_box: boolean;
  box_color: string;
  box_opacity: number;
  align: "center" | "left" | "right";
  max_lines: number;
  max_width_percent: number;
  y_percent: number;
  vertical_offset: number;
  safe_margin_percent: number;
  duration: { mode: "full" | "first_seconds" | "time_range"; seconds: number; start: number; end: number };
  fade_in: number;
  fade_out: number;
  uppercase: boolean;
  blur_background: boolean;
}

export interface Template {
  id: string;
  name: string;
  version: number;
  active: boolean;
  reference_verified: boolean;
  reference_status: string;
  config: TemplateConfig;
}

export interface EventRow {
  id: number;
  at: string;
  component: string;
  type: string;
  severity: string;
  message: string;
  clip_id: string | null;
}

export type DiagState = "NOT_CONFIGURED" | "CONFIGURED" | "CONNECTED" | "TEST_FAILED" | "NEEDS_LIVE_VERIFICATION";

export interface DiagEntry {
  id: string;
  label: string;
  state: DiagState;
  simulated: boolean;
  summary: string;
  hints: string[];
  test_requires_confirm: boolean;
  confirm_text: string | null;
  last_test: { at: string; ok: boolean | null; message: string; duration_ms: number | null } | null;
  details: Record<string, unknown>;
}

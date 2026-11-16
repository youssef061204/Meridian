export type MetricKey = 'gross_positive_gbp' | 'negative_adjustments_gbp' | 'net_recorded_gbp';
export type Amount = number | string;
export interface ReconciliationTotals {
  source_parsed_amount_gbp: Amount;
  accepted_parsed_amount_gbp: Amount;
  quarantined_parsed_amount_gbp: Amount;
  excluded_parsed_amount_gbp: Amount;
  unparseable_amount_rows: number;
}
export interface Metrics {
  gross_positive_gbp: Amount;
  negative_adjustments_gbp: Amount;
  net_recorded_gbp: Amount;
  positive_units: number;
  known_customer_rows: number;
  [key: string]: Amount;
}
export interface Snapshot {
  id: string;
  published_at: string;
  mode: string;
  source_sha256: string;
  source_rows: number;
  accepted_rows: number;
  quarantined_rows: number;
  excluded_rows: number;
  repeat_deliveries: number;
  date_min: string;
  date_max: string;
  delta_versions: Record<string, number>;
  metrics: Metrics;
  limitations?: string[];
  reconciliation_totals?: ReconciliationTotals;
  [key: string]: unknown;
}
export interface Daily {
  date: string;
  gross_positive_gbp: Amount;
  negative_adjustments_gbp: Amount;
  net_recorded_gbp: Amount;
  positive_units: number;
}
export interface Overview {
  snapshot: Snapshot;
  metrics: Metrics;
  daily: Daily[];
  products: { stock_code: string; description: string; net_recorded_gbp: Amount; positive_units: number }[];
  countries: { country: string; net_recorded_gbp: Amount; rows: number }[];
  filters: { countries: string[] };
  cohorts?: Cohort[];
  comparison?: PeriodComparison | null;
  quality?: {
    known_customer_rows: number;
    accepted_rows: number;
    source_overlap_warning?: string | null;
  };
}
export interface Cohort {
  cohort_month: string;
  activity_month: string;
  month_index: number;
  cohort_customers: number;
  active_customers: number;
  retention_rate: Amount | null;
  is_complete_month: boolean;
}
export interface PeriodComparison {
  current_start: string;
  current_end: string;
  previous_start: string;
  previous_end: string;
  current_net_gbp: Amount;
  previous_net_gbp: Amount;
  change_gbp: Amount;
  change_pct: Amount | null;
  limitation: string;
}
export interface SourceLine {
  line_id: string;
  invoice: string;
  stock_code: string;
  description: string;
  quantity: Amount;
  unit_price: Amount;
  line_amount: Amount;
  country: string;
  customer_id: string | null;
  event_time: string;
  source_sheet: string;
  source_row: number;
  classification: string;
}
export interface Lineage {
  snapshot: Snapshot;
  sql: string;
  rows: SourceLine[];
  total_rows: number;
  metric: string;
}
export interface Trust {
  snapshot: Snapshot;
  runs: Record<string, unknown>[];
  source: Record<string, unknown>;
  publication: Record<string, unknown>;
}
export interface ForecastPoint { date: string; actual?: number | null; available?: boolean; prediction: number; lower?: number | null; upper?: number | null; model: string; expected_non_trading?: boolean }
export interface ForecastMetric { model: string; model_key: string; horizon: number; series_scope: string; mae: number | null; wape: number | null; wape_null_reason: string | null; coverage80: number | null; width80: number | null; n: number }
export interface PolicyResult { total_cost: number; holding_cost: number; shortage_cost: number; lost_units: number; fill_rate: number }
export interface ForecastData {
  state: string; message?: string; snapshot_id: string; promoted_model: string; promotion_reason: string;
  config: { test_forecast_origin: string; latest_forecast_origin: string; excluded_partial_day: string; origins: { origin: string; role: string }[] };
  metrics: { validation: ForecastMetric[]; test: ForecastMetric[] };
  series: { series_id: string; label: string; in_test: boolean; in_latest: boolean }[];
  test_forecast: Record<string, ForecastPoint[]>; latest_forecast: Record<string, ForecastPoint[]>; limitations: string[];
  inventory?: { label: string; episodes: unknown[]; base_scenario: { lead_time_days: number; shortage_to_holding_ratio: number; lowest_total_cost_policy: string; policies: Record<string, PolicyResult> }; sensitivity: unknown[]; lowest_cost_policy_counts: Record<string, number> };
}
export interface JobStage { stage: string; state: string; seconds?: number; attempts: number }
export interface Job { job_id: string; kind: string; state: string; stages: JobStage[]; error?: string }
export interface Batch { batch_id: number; deliveries: number; new_revisions: number; within_batch_duplicates: number; cross_batch_duplicates: number; rejected_envelopes: number; late_routed_to_backfill: number; out_of_order_within_window: number; processing_lag_seconds: number | null; deliveries_per_second: number | null; watermark: string | null }
export interface Reconciliation { state: string; missing_vs_batch_rows: number; missing_vs_batch_gbp: string; correction_delta_gbp: string; bridge: string }
export interface ReplayState {
  state: string; message?: string; config: { profile: string; allowed_lateness_hours: number; generation: string };
  snapshot: { id: string; reconciliation: Reconciliation; metrics: Record<string, string> } | null; snapshot_age_seconds: number | null; serving_state: string;
  publications: { attempted_at: string; state: string; reason?: string; snapshot_id?: string; seconds?: number }[];
  batches: Batch[]; worker: { state: string }; jobs: Job[]; controls_enabled: boolean;
  status: { trusted_net_recorded_gbp: string; unsafe_comparison: { label: string; unsafe_net_gbp: string; discrepancy_gbp: string };
    bronze?: { deliveries: number; distinct_offsets: number; distinct_identities: number; synthetic: number; rejected: number };
    quarantine_by_reason?: Record<string, number>; late_arrivals?: { pending: number; applied: number } } | null;
  timeline: { injected_at?: string; incident_run_id?: string; detected_at?: string; detected?: Record<string, number>; publication?: string; publication_blocked_at?: string; blocked_reason?: string; recovered_at?: string; recovery_snapshot?: string; recovery_seconds?: number; recovery_scope?: string; backfill?: { applied_rows: number; affected_dates: string[] } } | null;
}
export interface CorrectionData { snapshot_id: string; batch_reference_snapshot: string; batch_net_recorded_gbp: string; metrics: Record<string, string>; reconciliation: Reconciliation;
  lines: { line_id: string; revision: number; mode: string; incident: string | null; source_sheet: string; source_row: number; quantity: string; unit_price: string; line_amount: string; first_batch_id: string; is_current: boolean }[] }

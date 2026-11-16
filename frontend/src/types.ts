export type MetricKey = 'gross_positive_gbp' | 'negative_adjustments_gbp' | 'net_recorded_gbp';
export type Amount = number | string;
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

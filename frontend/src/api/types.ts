// Типы зеркалят backend/app/schemas.py — держать в синхроне вручную,
// отдельного кодогенератора в проекте нет (см. spec.md, границы объёма).

export interface Dictionary {
  id: number;
  name: string;
  description: string | null;
  keywords: string[];
  created_at: string;
  updated_at: string;
}

export interface DayCoverage {
  day: string;
  total_urls: number;
  ok_count: number;
  truncated_count: number;
  premium_count: number;
  empty_count: number;
  failed_count: number;
  status: "pending" | "running" | "done" | "partial" | "failed";
  harvested_at: string | null;
}

export interface CorpusSummary {
  days_covered: number;
  articles_total: number;
  articles_with_text: number;
  articles_truncated: number;
  articles_premium: number;
  articles_failed: number;
  first_day: string | null;
  last_day: string | null;
  database_bytes: number;
  last_harvest_at: string | null;
}

export interface ScheduleInfo {
  enabled: boolean;
  hour: number;
  minute: number;
  lookback_days: number;
  next_run_at: string | null;
}

export type JobStatus = "pending" | "running" | "done" | "failed" | "cancelled";

export interface Job {
  id: string;
  type: "harvest" | "analysis";
  status: JobStatus;
  current: number;
  total: number;
  stage: string | null;
  message: string | null;
  error: string | null;
  result_id: number | null;
  params: Record<string, unknown>;
  created_at: string | null;
  started_at: string | null;
  finished_at: string | null;
}

export interface ContextWord {
  word: string;
  count: number;
  percentage: number;
}

export interface CategoryStat {
  category: string;
  articles_with_word: number;
  category_articles: number;
  percentage: number;
  occurrences: number;
}

export interface KeywordStats {
  keyword: string;
  articles_with_word: number;
  total_processed_articles: number;
  percentage: number;
  total_occurrences: number;
  left_context_words: ContextWord[];
  right_context_words: ContextWord[];
  // Пусто у анализов, посчитанных до появления разбивки по категориям.
  categories?: CategoryStat[];
}

export interface TimeseriesPoint {
  date: string;
  occurrences: number;
  articles: number;
}

export type Zone = "title" | "captions" | "body";

// Набор зон одной строкой: прежние "body" | "title" | "title_body" либо список
// через запятую из title, captions, body.
export type TextScope = string;

export interface AnalysisSummary {
  id: number;
  name: string | null;
  start_date: string;
  end_date: string;
  keywords: string[];
  total_processed_articles: number;
  status: string;
  error: string | null;
  dictionary_id: number | null;
  categories: string[] | null;
  text_scope: TextScope;
  created_at: string;
  finished_at: string | null;
}

export interface CategoryCount {
  category: string;
  count: number;
}

export interface AnalysisDetail extends AnalysisSummary {
  articles_in_corpus: number;
  // null — анализ посчитан до появления этой цифры.
  total_words?: number | null;
  stats: KeywordStats[];
  timeseries: Record<string, TimeseriesPoint[]>;
}

export interface ExampleItem {
  url: string;
  context_before: string;
  keyword: string;
  context_after: string;
  published_date: string | null;
  title: string | null;
}

export interface ExamplesPage {
  keyword: string;
  total: number;
  offset: number;
  limit: number;
  items: ExampleItem[];
}

import type {
  AnalysisDetail,
  AnalysisSummary,
  CategoryCount,
  CorpusSummary,
  DayCoverage,
  Dictionary,
  ExamplesPage,
  Job,
  ScheduleInfo,
  TextScope,
} from "./types";

// Пусто = относительный /api (nginx на проде проксирует его к бэкенду сам,
// в dev-режиме это делает vite.config.ts). Абсолютный адрес нужен только
// когда фронт и бэк разнесены по разным доменам (например Cloudflare Pages
// + отдельный сервер) — тогда задаётся VITE_API_BASE_URL при сборке.
const BASE_URL = import.meta.env.VITE_API_BASE_URL || "";

export class ApiError extends Error {
  status: number;
  detail: unknown;
  constructor(status: number, detail: unknown) {
    const message =
      typeof detail === "string"
        ? detail
        : (detail as { detail?: string } | null)?.detail
          ? String((detail as { detail?: string }).detail)
          : `Ошибка запроса (${status})`;
    super(message);
    this.status = status;
    this.detail = detail;
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${BASE_URL}${path}`, {
    headers: { "Content-Type": "application/json", ...(init?.headers || {}) },
    ...init,
  });
  if (!response.ok) {
    let detail: unknown = null;
    try {
      detail = await response.json();
    } catch {
      detail = await response.text().catch(() => null);
    }
    throw new ApiError(response.status, detail);
  }
  if (response.status === 204) return undefined as T;
  return response.json() as Promise<T>;
}

export const api = {
  // --- Словари ---
  listDictionaries: () => request<Dictionary[]>("/api/dictionaries"),
  createDictionary: (data: { name: string; description?: string; keywords: string[] }) =>
    request<Dictionary>("/api/dictionaries", { method: "POST", body: JSON.stringify(data) }),
  updateDictionary: (
    id: number,
    data: Partial<{ name: string; description: string; keywords: string[] }>,
  ) =>
    request<Dictionary>(`/api/dictionaries/${id}`, {
      method: "PATCH",
      body: JSON.stringify(data),
    }),
  deleteDictionary: (id: number) =>
    request<void>(`/api/dictionaries/${id}`, { method: "DELETE" }),
  importDictionary: async (file: File): Promise<Dictionary> => {
    const form = new FormData();
    form.append("file", file);
    const response = await fetch(`${BASE_URL}/api/dictionaries/import`, {
      method: "POST",
      body: form,
    });
    if (!response.ok) {
      const detail = await response.json().catch(() => null);
      throw new ApiError(response.status, detail);
    }
    return response.json();
  },

  // --- Корпус ---
  corpusSummary: () => request<CorpusSummary>("/api/corpus/summary"),
  corpusCoverage: (startDate?: string, endDate?: string) => {
    const params = new URLSearchParams();
    if (startDate) params.set("start_date", startDate);
    if (endDate) params.set("end_date", endDate);
    const qs = params.toString();
    return request<DayCoverage[]>(`/api/corpus/coverage${qs ? `?${qs}` : ""}`);
  },
  corpusSchedule: () => request<ScheduleInfo>("/api/corpus/schedule"),
  startHarvest: (data: { start_date: string; end_date: string; refresh?: boolean }) =>
    request<Job>("/api/corpus/harvest", { method: "POST", body: JSON.stringify(data) }),
  corpusCategories: () => request<CategoryCount[]>("/api/corpus/categories"),

  // --- Анализ ---
  listAnalyses: () => request<AnalysisSummary[]>("/api/analyses"),
  getAnalysis: (id: number) => request<AnalysisDetail>(`/api/analyses/${id}`),
  startAnalysis: (data: {
    start_date: string;
    end_date: string;
    name?: string;
    dictionary_id?: number;
    keywords?: string[];
    categories?: string[];
    text_scope?: TextScope;
  }) => request<Job>("/api/analyses", { method: "POST", body: JSON.stringify(data) }),
  deleteAnalysis: (id: number) => request<void>(`/api/analyses/${id}`, { method: "DELETE" }),
  getExamples: (
    id: number,
    keyword: string,
    offset = 0,
    limit = 50,
    search?: string,
  ) => {
    const params = new URLSearchParams({
      keyword,
      offset: String(offset),
      limit: String(limit),
    });
    if (search) params.set("search", search);
    return request<ExamplesPage>(`/api/analyses/${id}/examples?${params}`);
  },
  exportJsonUrl: (id: number) => `${BASE_URL}/api/analyses/${id}/export.json`,
  exportHtmlUrl: (id: number) => `${BASE_URL}/api/analyses/${id}/export.html`,

  // --- Задачи ---
  getJob: (id: string) => request<Job>(`/api/jobs/${id}`),
  cancelJob: (id: string) => request<void>(`/api/jobs/${id}/cancel`, { method: "POST" }),
  getActiveJobs: () => request<Job[]>("/api/jobs/active"),
  jobStreamUrl: (id: string) => `${BASE_URL}/api/jobs/${id}/stream`,
};

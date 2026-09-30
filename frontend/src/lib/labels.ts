// Человекочитаемые подписи для статусов, приходящих из API как английские коды.

export const JOB_STATUS_LABELS: Record<string, string> = {
  pending: "в очереди",
  running: "выполняется",
  done: "готово",
  failed: "ошибка",
  cancelled: "остановлено",
};

export const DAY_STATUS_LABELS: Record<string, string> = {
  pending: "не собрано",
  running: "собирается",
  done: "собрано",
  partial: "частично",
  failed: "ошибка",
};

export function jobStatusLabel(status: string): string {
  return JOB_STATUS_LABELS[status] || status;
}

export function dayStatusLabel(status: string): string {
  return DAY_STATUS_LABELS[status] || status;
}

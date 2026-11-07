type MedicineOpenTrace = { startedAt: number; lastStageAt: number };

const traces = new Map<number, MedicineOpenTrace>();

/** Development-only timing marks for a medicine card to detail transition. */
export function startMedicineOpenTrace(id: number, source: string) {
  if (!__DEV__) return;
  const now = Date.now();
  if (traces.size >= 30) traces.delete(traces.keys().next().value!);
  traces.set(id, { startedAt: now, lastStageAt: now });
  console.info(`[medicine-open] id=${id} stage=tap source=${source} elapsed=0ms`);
}

export function markMedicineOpenTrace(id: number, stage: string) {
  if (!__DEV__) return;
  const trace = traces.get(id);
  if (!trace) return;
  const now = Date.now();
  console.info(`[medicine-open] id=${id} stage=${stage} elapsed=${now - trace.startedAt}ms gap=${now - trace.lastStageAt}ms`);
  trace.lastStageAt = now;
}

export function finishMedicineOpenTrace(id: number, stage: string) {
  markMedicineOpenTrace(id, stage);
  const trace = traces.get(id);
  if (trace) setTimeout(() => {
    if (traces.get(id) === trace) traces.delete(id);
  }, 15_000);
}

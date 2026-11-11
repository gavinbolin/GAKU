/** Opt-in fail-fast policy: no retry/rotation after quota or billing signals. */
import { writeFileSync, writeSync } from "node:fs";

export function stopOnGeminiBudgetError(error: unknown): void {
  if (!["1", "true", "yes", "on"].includes((process.env.GAKU_GEMINI_STOP_ON_QUOTA || "").trim().toLowerCase())) return;
  const err = error as { status?: number; message?: string } | null;
  const status = err?.status;
  const message = err?.message || String(error);
  if (![401, 402, 403, 429].includes(status || 0) && !/quota|resource[ _]?exhausted|rate.?limit|too many requests|billing|payment|paid[ _-]?tier|charges?|charged|chargeable/i.test(message)) return;
  const marker = process.env.GAKU_GEMINI_STOP_FILE;
  if (marker) writeFileSync(marker, JSON.stringify({ stopped: true, runtime: "node", reason: "quota_billing_or_auth_signal", status: status ?? null }));
  writeSync(2, "[gemini-safety] Quota/billing/auth signal: stopped without retry or key/model rotation.\n");
  process.exit(85);
}

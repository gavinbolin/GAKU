// preview_visual
// Spawn the Remotion studio (`npx remotion studio <entry>`) so a designer can
// iterate on a single template + props payload without re-running the full
// pipeline. Returns the URL the studio is serving on.

import { spawn } from "node:child_process";
import { resolve, dirname } from "node:path";
import { fileURLToPath } from "node:url";

const __filename = fileURLToPath(import.meta.url);
const __dirname = dirname(__filename);

const REMOTION_ENTRY = resolve(
  __dirname,
  "../../../remotion/src/Root.tsx"
);

export async function previewVisual(
  template: string,
  data: Record<string, unknown>
): Promise<{ template: string; studio_url: string; pid: number }> {
  // TODO(integration): use --props=<json> once Remotion studio exposes a
  // stable flag for it; otherwise serialize props into a temp Root.tsx wrapper
  // and point the studio at that instead.
  void data;

  const proc = spawn(
    "npx",
    ["remotion", "studio", REMOTION_ENTRY, "--port", "3001"],
    {
      stdio: ["ignore", "inherit", "inherit"],
      shell: process.platform === "win32",
      detached: true,
    }
  );
  proc.unref();

  return {
    template,
    studio_url: "http://localhost:3001",
    pid: proc.pid ?? -1,
  };
}

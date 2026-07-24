// render_visual
// Render a single Remotion composition to MP4 by shelling out to the Remotion CLI:
//   npx remotion render <entry> <compId> <out> --props=<json-file>
//
// We pass `data` and `duration_ms` as inputProps. Each composition is set up
// in packages/remotion/src/Root.tsx with a generous default duration; we trim
// to the requested ms via --duration-in-frames computed at fps=30.

import { spawn } from "node:child_process";
import { writeFile, mkdtemp, rm } from "node:fs/promises";
import { tmpdir } from "node:os";
import { resolve, join } from "node:path";
import { fileURLToPath } from "node:url";
import { dirname } from "node:path";

const __filename = fileURLToPath(import.meta.url);
const __dirname = dirname(__filename);

// TODO(integration): adjust if the Remotion package is moved relative to this file.
const REMOTION_PKG_DIR = resolve(__dirname, "../../../remotion");
const REMOTION_ENTRY = resolve(REMOTION_PKG_DIR, "src/Root.tsx");
const FPS = 30;

export async function renderVisual(
  template: string,
  data: Record<string, unknown>,
  durationMs: number,
  outPath: string
): Promise<{ template: string; out_path: string; duration_ms: number }> {
  const frames = Math.max(1, Math.round((durationMs / 1000) * FPS));

  // Write props to a temp JSON file. Inlining as --props=<json> mangles on
  // Windows: spawn with shell:true asks cmd.exe to parse the args, which
  // strips the JSON's quotes and splits on spaces. The file path avoids
  // every shell-escaping issue.
  const propsObj = { ...data, durationInFrames: frames };
  const tmpDir = await mkdtemp(join(tmpdir(), "gaku-props-"));
  const propsPath = join(tmpDir, "props.json");
  await writeFile(propsPath, JSON.stringify(propsObj), "utf-8");

  try {
    await runRemotion([
      "remotion", "render",
      REMOTION_ENTRY,
      template,
      outPath,
      `--props=${propsPath}`,
      `--duration-in-frames=${frames}`,
      `--fps=${FPS}`,
    ]);
  } finally {
    await rm(tmpDir, { recursive: true, force: true }).catch(() => {});
  }

  return { template, out_path: outPath, duration_ms: durationMs };
}

// Wrap an arg in double quotes if it contains whitespace or characters cmd.exe
// treats specially. Embedded double quotes are escaped as \". Bare args (no
// space/special chars) are returned as-is so flags like --fps=30 stay readable.
function quoteForCmd(arg: string): string {
  if (/^[A-Za-z0-9_=\-:.\/\\]+$/.test(arg)) return arg;
  return `"${arg.replace(/"/g, '\\"')}"`;
}

function runRemotion(args: string[]): Promise<void> {
  return new Promise((resolveP, rejectP) => {
    // `npx` resolves the local @remotion/cli install.
    // stdout MUST NOT inherit: the MCP server uses its own stdout as the
    // JSON-RPC transport, and Remotion's progress bars would corrupt it and
    // hang the client. Pipe Remotion's stdout AND stderr to our stderr so
    // failure messages aren't lost.
    //
    // Windows arg handling is a minefield:
    //   - Without `shell: true`, Node refuses to spawn .cmd/.bat (EINVAL,
    //     CVE-2024-27980 patch).
    //   - With `shell: true`, Node concatenates args verbatim, so any path
    //     containing a space (e.g. "PROJECT GAKU") gets split by cmd.exe.
    // Fix: with shell:true, quote each arg ourselves so cmd.exe sees them
    // as single tokens.
    const isWin = process.platform === "win32";
    const finalArgs = isWin ? args.map(quoteForCmd) : args;
    process.stderr.write(`[render_visual] spawn: npx ${finalArgs.join(" ")} (cwd=${REMOTION_PKG_DIR})\n`);
    const proc = spawn("npx", finalArgs, {
      stdio: ["ignore", "pipe", "pipe"],
      shell: true,
      // npx needs to run from the remotion package dir so it can resolve the
      // locally-installed @remotion/cli. Otherwise: "could not determine
      // executable to run".
      cwd: REMOTION_PKG_DIR,
    });
    proc.stdout?.on("data", (chunk) => {
      process.stderr.write(chunk);
    });
    proc.stderr?.on("data", (chunk) => {
      process.stderr.write(chunk);
    });
    const watchdog = setTimeout(() => {
      proc.kill("SIGKILL");
      rejectP(new Error("render_visual timed out after 10 minutes"));
    }, 10 * 60 * 1000);
    proc.on("error", (err) => {
      clearTimeout(watchdog);
      rejectP(err);
    });
    proc.on("close", (code) => {
      clearTimeout(watchdog);
      if (code === 0) resolveP();
      else rejectP(new Error(`remotion render exited ${code}`));
    });
  });
}

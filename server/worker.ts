// The Python side (optagent.worker): one long-lived process, JSON lines both ways.
import { spawn, type ChildProcessWithoutNullStreams } from "node:child_process";
import { createInterface } from "node:readline";
import { config } from "./config.ts";

export interface BuildResult {
  ok: boolean;
  errors: string[];
  warnings: string[];
  summary: string;
  files: Partial<Record<"layout" | "png2d" | "svg2d" | "png3d" | "gltf3d", string>>;
  timings: Record<string, number>;
  board_in?: [number, number];
}

type Pending = { resolve: (v: BuildResult) => void; reject: (e: Error) => void };

export class PythonWorker {
  private proc: ChildProcessWithoutNullStreams;
  private pending = new Map<number, Pending>();
  private nextId = 1;
  readonly ready: Promise<void>;

  constructor() {
    this.proc = spawn(config.python, ["-m", "optagent.worker"], {
      cwd: config.pyDir,
      env: { ...process.env, PYTHONUNBUFFERED: "1" },
    });
    this.proc.stderr.on("data", (d: Buffer) => {
      const s = d.toString();
      if (!/^qt\.|propagateSizeHints|QOpenGLWidget/.test(s.trim())) process.stderr.write(`[py] ${s}`);
    });
    let markReady!: () => void;
    let fail!: (e: Error) => void;
    this.ready = new Promise((res, rej) => ((markReady = res), (fail = rej)));
    this.proc.on("exit", (code) => {
      const err = new Error(`Python worker exited (code ${code})`);
      fail(err);
      for (const p of this.pending.values()) p.reject(err);
      this.pending.clear();
    });
    createInterface({ input: this.proc.stdout }).on("line", (line) => {
      let msg: any;
      try {
        msg = JSON.parse(line);
      } catch {
        return;
      }
      if (msg.ready) return markReady();
      const p = this.pending.get(msg.id);
      if (!p) return;
      this.pending.delete(msg.id);
      if (msg.ok) p.resolve(msg.result as BuildResult);
      else p.reject(new Error(msg.error));
    });
  }

  async build(spec: unknown, outDir: string, render3d = true): Promise<BuildResult> {
    await this.ready;
    const id = this.nextId++;
    return new Promise((resolve, reject) => {
      this.pending.set(id, { resolve, reject });
      this.proc.stdin.write(JSON.stringify({ id, cmd: "build", spec, out_dir: outDir, render3d }) + "\n");
    });
  }

  stop() {
    this.proc.kill();
  }
}

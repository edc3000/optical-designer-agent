// Web server: the page, the pictures, and a streaming chat endpoint per conversation.
import { randomUUID } from "node:crypto";
import { existsSync, readdirSync, readFileSync, statSync } from "node:fs";
import { createServer, type IncomingMessage, type ServerResponse } from "node:http";
import path from "node:path";
import type { AgentSession } from "@earendil-works/pi-coding-agent";
import { createDesignSession, type BuildEvent } from "./agent.ts";
import { config } from "./config.ts";
import { PythonWorker, type BuildResult } from "./worker.ts";

type Send = (event: string, data: unknown) => void;
interface Conv {
  session: AgentSession;
  resetBudget: () => void;
  busy: boolean;
  send: Send;
}

const worker = new PythonWorker();
const convs = new Map<string, Conv>();
const MIME: Record<string, string> = {
  ".html": "text/html; charset=utf-8",
  ".js": "text/javascript; charset=utf-8",
  ".css": "text/css; charset=utf-8",
  ".png": "image/png",
  ".svg": "image/svg+xml",
  ".json": "application/json; charset=utf-8",
  ".gltf": "model/gltf+json",
};
const THREE_DIR = path.join(config.webDir, "..", "node_modules", "three");

function sendFile(res: ServerResponse, base: string, rel: string) {
  const file = path.resolve(base, rel);
  if (!file.startsWith(base + path.sep) || !existsSync(file) || !statSync(file).isFile()) {
    res.writeHead(404).end("not found");
    return;
  }
  const headers: Record<string, string> = { "content-type": MIME[path.extname(file)] ?? "application/octet-stream" };
  if (file.endsWith("layout.json")) headers["content-disposition"] = 'attachment; filename="layout.json"';
  res.writeHead(200, headers).end(readFileSync(file));
}

function json(res: ServerResponse, code: number, body: unknown) {
  res.writeHead(code, { "content-type": "application/json; charset=utf-8" }).end(JSON.stringify(body));
}

async function body(req: IncomingMessage): Promise<any> {
  const chunks: Buffer[] = [];
  for await (const c of req) chunks.push(c as Buffer);
  return chunks.length ? JSON.parse(Buffer.concat(chunks).toString("utf8")) : {};
}

/** A build as the page shows it: file paths become URLs under /runs. */
function forPage(convId: string, run: number, spec: unknown, r: BuildResult) {
  const url = (f?: string) => (f ? `/runs/${convId}/${run}/${path.basename(f)}?t=${Date.now()}` : undefined);
  return {
    run,
    ok: r.ok,
    errors: r.errors,
    warnings: r.warnings,
    summary: r.summary,
    timings: r.timings,
    board_in: r.board_in,
    spec,
    images: {
      png2d: url(r.files.png2d),
      svg2d: url(r.files.svg2d),
      png3d: url(r.files.png3d),
      gltf3d: url(r.files.gltf3d),
      layout: url(r.files.layout),
    },
  };
}

function sse(res: ServerResponse): Send {
  res.writeHead(200, { "content-type": "text/event-stream; charset=utf-8", "cache-control": "no-cache", connection: "keep-alive" });
  return (event, data) => {
    if (!res.writableEnded) res.write(`event: ${event}\ndata: ${JSON.stringify(data)}\n\n`);
  };
}

async function chat(req: IncomingMessage, res: ServerResponse) {
  const { conversation, text } = await body(req);
  if (typeof text !== "string" || !text.trim()) return json(res, 400, { error: "text 不能为空" });
  const id = typeof conversation === "string" && convs.has(conversation) ? conversation : randomUUID();
  let conv = convs.get(id);
  if (conv?.busy) return json(res, 409, { error: "上一条消息还在处理中" });
  const send = sse(res);
  send("conversation", { id });
  if (!conv) {
    try {
      const c: Conv = { busy: false, send, ...(await createDesignSession({
        id,
        worker,
        onBuild: (e: BuildEvent) => c.send("build", forPage(id, e.run, e.spec, e.result)),
      })) } as Conv;
      conv = c;
      convs.set(id, c);
    } catch (e) {
      send("error", { message: (e as Error).message });
      return res.end();
    }
  }
  const c = conv;
  c.send = send;
  c.busy = true;
  c.resetBudget();
  const unsubscribe = c.session.subscribe((ev) => {
    if (ev.type === "message_update") {
      const a = ev.assistantMessageEvent;
      if (a.type === "text_delta") send("text", { delta: a.delta });
      else if (a.type === "thinking_delta") send("thinking", { delta: a.delta });
    } else if (ev.type === "tool_execution_start") {
      send("tool_start", { id: ev.toolCallId, tool: ev.toolName });
    } else if (ev.type === "tool_execution_end") {
      send("tool_end", { id: ev.toolCallId, tool: ev.toolName, isError: ev.isError });
    } else if (ev.type === "message_end") {
      const m = ev.message as { role?: string; stopReason?: string; errorMessage?: string };
      if (m.role === "assistant" && m.stopReason === "error") send("error", { message: m.errorMessage ?? "模型调用失败" });
    }
  });
  res.on("close", () => {
    if (c.busy) void c.session.abort();
  });
  try {
    await c.session.prompt(text);
    send("done", {});
  } catch (e) {
    send("error", { message: (e as Error).message });
  } finally {
    unsubscribe();
    c.busy = false;
    res.end();
  }
}

async function example(req: IncomingMessage, res: ServerResponse) {
  const { name } = await body(req);
  const names = readdirSync(config.specsDir).map((f) => f.replace(/\.json$/, ""));
  if (!names.includes(name)) return json(res, 404, { error: "没有这个示例" });
  const spec = JSON.parse(readFileSync(path.join(config.specsDir, `${name}.json`), "utf8"));
  const run = Date.now();
  const result = await worker.build(spec, path.join(config.runsDir, "examples", String(run)), true);
  json(res, 200, forPage("examples", run, spec, result));
}

function status(res: ServerResponse) {
  let llm: Record<string, unknown>;
  try {
    const l = config.llm();
    llm = { model: l.model, configured: l.hasKey };
  } catch (e) {
    llm = { configured: false, error: (e as Error).message };
  }
  const examples = readdirSync(config.specsDir).filter((f) => f.endsWith(".json")).map((f) => f.replace(/\.json$/, ""));
  json(res, 200, { llm, examples });
}

const server = createServer(async (req, res) => {
  try {
    const url = new URL(req.url ?? "/", "http://localhost");
    const p = decodeURIComponent(url.pathname);
    if (req.method === "GET" && p === "/") return sendFile(res, config.webDir, "index.html");
    if (req.method === "GET" && p.startsWith("/static/")) return sendFile(res, config.webDir, p.slice(8));
    if (req.method === "GET" && p.startsWith("/runs/")) return sendFile(res, config.runsDir, p.slice(6));
    if (req.method === "GET" && p.startsWith("/vendor/three/")) return sendFile(res, THREE_DIR, p.slice(14));
    if (req.method === "GET" && p === "/api/status") return status(res);
    if (req.method === "POST" && p === "/api/chat") return await chat(req, res);
    if (req.method === "POST" && p === "/api/example") return await example(req, res);
    res.writeHead(404).end("not found");
  } catch (e) {
    console.error(e);
    if (!res.headersSent) json(res, 500, { error: (e as Error).message });
    else res.end();
  }
});

await worker.ready;
server.listen(config.port, "127.0.0.1", () => {
  console.log(`光路设计 agent：http://127.0.0.1:${config.port}`);
});
for (const sig of ["SIGINT", "SIGTERM"] as const) {
  process.on(sig, () => {
    worker.stop();
    process.exit(0);
  });
}

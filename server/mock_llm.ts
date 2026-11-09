// A stand-in OpenAI-compatible endpoint for testing without an API key.
// It answers like a model would: first a build_layout call (deliberately wrong for 4f, so the
// repair loop runs), then a corrected call after the tool reports errors, then a short summary.
//   tsx server/mock_llm.ts  (port 8790)  with OPTAGENT_BASE_URL=http://127.0.0.1:8790/v1
import { readFileSync } from "node:fs";
import { createServer } from "node:http";
import path from "node:path";
import { config } from "./config.ts";

const spec = (name: string) => JSON.parse(readFileSync(path.join(config.specsDir, `${name}.json`), "utf8"));

function pick(userText: string) {
  if (/双通|double/i.test(userText)) return "dp_aom";
  if (/光纤|fiber/i.test(userText)) return "fiber";
  return "4f";
}

function chunk(delta: object, finish: string | null = null) {
  return { id: "mock", object: "chat.completion.chunk", created: 0, model: "mock", choices: [{ index: 0, delta, finish_reason: finish }] };
}

createServer(async (req, res) => {
  const parts: Buffer[] = [];
  for await (const c of req) parts.push(c as Buffer);
  const body = JSON.parse(Buffer.concat(parts).toString("utf8") || "{}");
  const msgs: any[] = body.messages ?? [];
  const text = (m: any) => (typeof m.content === "string" ? m.content : (m.content ?? []).map((p: any) => p.text ?? "").join(""));
  const users = msgs.filter((m) => m.role === "user");
  const lastUser = text(users.at(-1) ?? {});
  const last = msgs.at(-1);
  res.writeHead(200, { "content-type": "text/event-stream" });
  const send = (o: object) => res.write(`data: ${JSON.stringify(o)}\n\n`);
  const name = pick(lastUser);
  if (last?.role === "tool" && text(last).includes("检查通过")) {
    for (const piece of ["已完成设计：", `${name} 光路已通过几何与光学检查，`, "2D 和 3D 图见右侧。"]) send(chunk({ content: piece }));
    send(chunk({}, "stop"));
  } else {
    const s = spec(name);
    const broken = name === "4f" && last?.role !== "tool";
    if (broken) s.paths[0].steps[4].go = 120; // L2 too far: the checks must catch it
    send(chunk({ role: "assistant", content: broken ? "先按 f = 100 mm 搭一个 4f 系统。" : "根据检查结果修正后重新提交。" }));
    send(chunk({ tool_calls: [{ index: 0, id: `call_${msgs.length}`, type: "function", function: { name: "build_layout", arguments: "" } }] }));
    const args = JSON.stringify({ spec: s });
    for (let i = 0; i < args.length; i += 200) send(chunk({ tool_calls: [{ index: 0, function: { arguments: args.slice(i, i + 200) } }] }));
    send(chunk({}, "tool_calls"));
  }
  send({ id: "mock", object: "chat.completion.chunk", created: 0, model: "mock", choices: [], usage: { prompt_tokens: 10, completion_tokens: 10, total_tokens: 20 } });
  res.end("data: [DONE]\n\n");
}).listen(8790, "127.0.0.1", () => console.log("mock LLM on http://127.0.0.1:8790/v1"));

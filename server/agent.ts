// One Pi agent session per browser conversation, with build_layout as its only tool.
import { mkdirSync } from "node:fs";
import path from "node:path";
import {
  createAgentSession,
  DefaultResourceLoader,
  defineTool,
  ModelRuntime,
  SessionManager,
  SettingsManager,
  type AgentSession,
} from "@earendil-works/pi-coding-agent";
import { Type } from "typebox";
import { config, ROOT } from "./config.ts";
import { systemPrompt } from "./prompt.ts";
import type { BuildResult, PythonWorker } from "./worker.ts";

export const PROVIDER = "optagent";

const PART = Type.Union(
  ["laser", "mirror", "lens", "hwp", "qwp", "pbs", "bs", "aom", "iris", "fiber", "shutter", "dump", "photodiode"].map(
    (p) => Type.Literal(p),
  ),
);
const STEP = Type.Object({
  part: PART,
  label: Type.String({ description: "元件名，全局唯一" }),
  go: Type.Optional(Type.Number({ description: "沿光束到上一个元件的距离（mm），路径第一个元件不写" })),
  f: Type.Optional(Type.Number({ description: "透镜焦距（mm），lens 必填" })),
  turn: Type.Optional(Type.Union([Type.Literal("left"), Type.Literal("right"), Type.Literal("back")], {
    description: "仅 mirror：left/right 转 90°，back 为回射镜",
  })),
  note: Type.Optional(Type.String()),
});
const PATH = Type.Object({
  id: Type.Optional(Type.String()),
  dir: Type.Optional(Type.Number({ description: "第一条路径的出射方向（度）：0 右，90 上，180 左，270 下" })),
  from: Type.Optional(Type.String({ description: "支路：从这个 pbs/bs 的 label 分出" })),
  turn: Type.Optional(Type.Union([Type.Literal("left"), Type.Literal("right"), Type.Literal("straight")])),
  steps: Type.Array(STEP),
});
const SPEC = Type.Object({
  title: Type.String(),
  wavelength_nm: Type.Optional(Type.Number()),
  board: Type.Optional(Type.Object({
    width_in: Type.Optional(Type.Number()),
    height_in: Type.Optional(Type.Number()),
    beam_height_mm: Type.Optional(Type.Number()),
  })),
  paths: Type.Array(PATH),
  constraints: Type.Optional(Type.Array(Type.Object({
    type: Type.Literal("distance"),
    a: Type.String(),
    b: Type.String(),
    mm: Type.Number(),
  }))),
});

export interface BuildEvent {
  run: number;
  spec: unknown;
  result: BuildResult;
}

function toolText(r: BuildResult, run: number, left: number): string {
  const lines = [r.ok ? `第 ${run} 次构建：检查通过。` : `第 ${run} 次构建：检查未通过，请修正以下错误后重新提交完整 spec。`];
  if (r.errors.length) lines.push("错误：", ...r.errors.map((e) => `- ${e}`));
  if (r.warnings.length) lines.push("警告：", ...r.warnings.map((w) => `- ${w}`));
  if (r.summary) lines.push("", r.summary);
  if (!r.ok && left <= 0) lines.push("", "本轮已达到最大尝试次数：不要再调用 build_layout，直接向用户说明还剩哪些问题。");
  return lines.join("\n");
}

export async function createDesignSession(opts: {
  id: string;
  worker: PythonWorker;
  onBuild: (e: BuildEvent) => void;
}): Promise<{ session: AgentSession; resetBudget: () => void }> {
  const llm = config.llm();
  mkdirSync(config.agentDir, { recursive: true });
  const runtime = await ModelRuntime.create({
    authPath: path.join(config.agentDir, "auth.json"),
    modelsPath: null,
    refreshOnCreate: false,
  });
  runtime.registerProvider(PROVIDER, {
    name: "OpenAI-compatible",
    baseUrl: llm.baseUrl,
    api: "openai-completions",
    apiKey: "$OPTAGENT_API_KEY",
    models: [{
      id: llm.model,
      name: llm.model,
      reasoning: llm.reasoning,
      input: ["text"],
      contextWindow: llm.contextWindow,
      maxTokens: llm.maxTokens,
      cost: { input: 0, output: 0, cacheRead: 0, cacheWrite: 0 },
    }],
  });
  const model = runtime.getModel(PROVIDER, llm.model);
  if (!model) throw new Error(`模型 ${llm.model} 注册失败`);

  let run = 0;
  let budget = config.maxBuildsPerPrompt;
  const buildTool = defineTool({
    name: "build_layout",
    label: "构建光路布局",
    description:
      "提交完整的光路描述 spec：程序摆放元件、检查几何与光学规则，并生成 2D/3D 图。返回检查结果和元件位置摘要。",
    parameters: Type.Object({ spec: SPEC }),
    async execute(_id, params) {
      run += 1;
      budget -= 1;
      const outDir = path.join(config.runsDir, opts.id, String(run));
      const result = await opts.worker.build(params.spec, outDir, true);
      opts.onBuild({ run, spec: params.spec, result });
      return {
        content: [{ type: "text", text: toolText(result, run, budget) }],
        details: { run, ok: result.ok },
      };
    },
  });

  const settingsManager = SettingsManager.inMemory({});
  const resourceLoader = new DefaultResourceLoader({
    cwd: ROOT,
    agentDir: config.agentDir,
    settingsManager,
    noExtensions: true,
    noSkills: true,
    noPromptTemplates: true,
    noThemes: true,
    noContextFiles: true,
    systemPrompt: systemPrompt(),
  });
  await resourceLoader.reload();
  const { session } = await createAgentSession({
    cwd: ROOT,
    agentDir: config.agentDir,
    modelRuntime: runtime,
    model,
    noTools: "builtin",
    customTools: [buildTool],
    resourceLoader,
    settingsManager,
    sessionManager: SessionManager.inMemory(ROOT),
  });
  return { session, resetBudget: () => (budget = config.maxBuildsPerPrompt) };
}

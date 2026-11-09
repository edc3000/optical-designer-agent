import { existsSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

export const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
if (existsSync(path.join(ROOT, ".env"))) process.loadEnvFile(path.join(ROOT, ".env"));

function env(name: string, fallback?: string): string {
  const v = process.env[name] ?? fallback;
  if (v === undefined || v === "") throw new Error(`缺少环境变量 ${name}（见 .env.example）`);
  return v;
}

export const config = {
  port: Number(process.env.PORT ?? 8787),
  python: process.env.OPTAGENT_PYTHON ?? path.join(ROOT, ".venv", "bin", "python"),
  pyDir: path.join(ROOT, "py"),
  runsDir: path.join(ROOT, "runs"),
  webDir: path.join(ROOT, "web"),
  specsDir: path.join(ROOT, "examples", "specs"),
  agentDir: path.join(ROOT, ".pi-agent"), // Pi's own state for this app, kept out of ~/.pi
  maxBuildsPerPrompt: 6,
  llm: () => ({
    baseUrl: env("OPTAGENT_BASE_URL"),
    model: env("OPTAGENT_MODEL"),
    reasoning: (process.env.OPTAGENT_REASONING ?? "false") === "true",
    contextWindow: Number(process.env.OPTAGENT_CONTEXT_WINDOW ?? 128000),
    maxTokens: Number(process.env.OPTAGENT_MAX_TOKENS ?? 8192),
    hasKey: !!process.env.OPTAGENT_API_KEY,
  }),
};

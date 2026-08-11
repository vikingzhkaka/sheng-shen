// 省身 · Edge Function：AI 教练对话代理（转发到 SenseNova 网关）
// 部署：supabase functions deploy chat
// 环境变量：SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY（自动注入）+ SENSENOVA_API_KEY（自己 set）
// 安全：先校验调用者 JWT（未登录 401），key 只存在服务端环境变量
import { createClient } from "npm:@supabase/supabase-js@2";

const cors = {
  "Access-Control-Allow-Origin": "*",
  "Access-Control-Allow-Headers": "Content-Type, Authorization",
  "Access-Control-Allow-Methods": "POST, OPTIONS",
};

function json(obj, status = 200) {
  return new Response(JSON.stringify(obj), {
    status,
    headers: { ...cors, "Content-Type": "application/json" },
  });
}

const MODELS = [
  { id: "deepseek-v4-flash", base: "https://token.sensenova.cn/v1", primary: true },
  { id: "sensenova-6.7-flash-lite", base: "https://token.sensenova.cn/v1" },
];

Deno.serve(async (req) => {
  if (req.method === "OPTIONS") return new Response("ok", { headers: cors });
  if (req.method !== "POST") return json({ error: "method not allowed" }, 405);

  // 鉴权：必须有有效 JWT（防止陌生人白嫖 AI 额度）
  const token = (req.headers.get("Authorization") || "").replace(/^Bearer\s+/i, "");
  if (!token) return json({ error: "未登录或会话已过期" }, 401);
  const supabase = createClient(
    Deno.env.get("SUPABASE_URL")!,
    Deno.env.get("SUPABASE_SERVICE_ROLE_KEY")!,
    { auth: { persistSession: false } },
  );
  const { data: { user }, error: au } = await supabase.auth.getUser(token);
  if (au || !user) return json({ error: "未登录或会话已过期" }, 401);

  let body;
  try { body = await req.json(); } catch { return json({ error: "请求体不是合法 JSON" }, 400); }
  const messages = body.messages;
  if (!Array.isArray(messages) || !messages.length) return json({ error: "messages 缺失" }, 400);

  const key = Deno.env.get("SENSENOVA_API_KEY");
  if (!key) return json({ error: "服务端未配置 SENSENOVA_API_KEY" }, 500);

  const m = MODELS.find((x) => x.id === body.model) ?? MODELS.find((x) => x.primary)!;
  try {
    const resp = await fetch(m.base + "/chat/completions", {
      method: "POST",
      headers: { "Content-Type": "application/json", "Authorization": "Bearer " + key },
      body: JSON.stringify({ model: m.id, messages, temperature: 0.8, stream: false }),
    });
    const data = await resp.json();
    if (!resp.ok) return json({ error: "LLM 返回 " + resp.status + ": " + JSON.stringify(data).slice(0, 300) }, resp.status);
    return json({ content: data.choices[0].message.content, model: m.id });
  } catch (e) {
    return json({ error: String(e) }, 502);
  }
});

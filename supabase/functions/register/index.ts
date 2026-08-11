// 省身 · Edge Function：注册（邮箱 + 密码 + 邀请码校验）
// 部署：supabase functions deploy register
// 环境变量（Supabase Edge Functions 自动注入）：SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY
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

Deno.serve(async (req) => {
  if (req.method === "OPTIONS") return new Response("ok", { headers: cors });
  if (req.method !== "POST") return json({ error: "method not allowed" }, 405);
  let email, password, code;
  try {
    ({ email, password, code } = await req.json());
  } catch {
    return json({ error: "请求体不是合法 JSON" }, 400);
  }
  email = String(email || "").trim().toLowerCase();
  password = String(password || "");
  code = String(code || "").trim();
  if (!/^[^@\s]+@[^@\s]+\.[^@\s]+$/.test(email)) return json({ error: "邮箱格式不正确" }, 400);
  if (password.length < 6) return json({ error: "密码至少 6 位" }, 400);
  if (!code) return json({ error: "需要邀请码" }, 400);

  const supabase = createClient(
    Deno.env.get("SUPABASE_URL")!,
    Deno.env.get("SUPABASE_SERVICE_ROLE_KEY")!,
    { auth: { persistSession: false } },
  );

  // 1) 校验邀请码（原子：仅未使用的可用）
  const inv = await supabase.from("invites").select("code").eq("code", code).is("used_by", null).maybeSingle();
  if (inv.error) return json({ error: "服务端错误：邀请码校验失败" }, 500);
  if (!inv.data) return json({ error: "邀请码无效或已被使用" }, 400);

  // 2) 是否已有该邮箱
  const dup = await supabase.auth.admin.listUsers({ page: 1, perPage: 1000 });
  if (dup.error) return json({ error: "服务端错误" }, 500);
  if (dup.data.users.some((u) => u.email === email)) return json({ error: "该邮箱已注册" }, 409);

  // 3) 第一个用户自动成为管理员
  const { count } = await supabase.from("profiles").select("id", { count: "exact", head: true });
  const isAdmin = (count ?? 0) === 0;

  // 4) 创建用户（自动确认邮箱，免收邮件）
  const { data: created, error: cu } = await supabase.auth.admin.createUser({
    email, password, email_confirm: true,
  });
  if (cu) return json({ error: mapAuthError(cu.message) }, 400);

  // 5) 写 profile + 标记邀请码已用
  await supabase.from("profiles").insert({ id: created.user.id, email, is_admin: isAdmin });
  await supabase.from("invites").update({ used_by: created.user.id, used_at: new Date().toISOString() }).eq("code", code);

  return json({ ok: true });
});

function mapAuthError(msg) {
  if (/already registered|already been registered/i.test(msg)) return "该邮箱已注册";
  return msg;
}

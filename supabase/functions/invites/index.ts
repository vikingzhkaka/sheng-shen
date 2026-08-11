// 省身 · Edge Function：邀请码管理（仅管理员）
// GET  /invites          → 列出所有邀请码及使用状态
// POST /invites {count}  → 生成 count 个新邀请码
// 部署：supabase functions deploy invites
import { createClient } from "npm:@supabase/supabase-js@2";

const cors = {
  "Access-Control-Allow-Origin": "*",
  "Access-Control-Allow-Headers": "Content-Type, Authorization",
  "Access-Control-Allow-Methods": "GET, POST, OPTIONS",
};

function json(obj, status = 200) {
  return new Response(JSON.stringify(obj), {
    status,
    headers: { ...cors, "Content-Type": "application/json" },
  });
}

function genCode() {
  const bytes = crypto.getRandomValues(new Uint8Array(6));
  let s = "";
  const alphabet = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"; // 去掉易混淆的 I/O/0/1
  for (const b of bytes) s += alphabet[b % alphabet.length];
  return s;
}

Deno.serve(async (req) => {
  if (req.method === "OPTIONS") return new Response("ok", { headers: cors });

  const token = (req.headers.get("Authorization") || "").replace(/^Bearer\s+/i, "");
  if (!token) return json({ error: "未登录或会话已过期" }, 401);
  const supabase = createClient(
    Deno.env.get("SUPABASE_URL")!,
    Deno.env.get("SERVICE_ROLE_KEY")!,
    { auth: { persistSession: false } },
  );
  const { data: { user }, error: au } = await supabase.auth.getUser(token);
  if (au || !user) return json({ error: "未登录或会话已过期" }, 401);

  const prof = await supabase.from("profiles").select("is_admin").eq("id", user.id).maybeSingle();
  if (prof.error || !prof.data?.is_admin) return json({ error: "需要管理员权限" }, 403);

  if (req.method === "GET") {
    const { data: inv, error } = await supabase
      .from("invites")
      .select("code, created_at, used_at, used_by")
      .order("created_at", { ascending: false });
    if (error) return json({ error: error.message }, 500);
    const usedIds = (inv || []).map((r) => r.used_by).filter(Boolean);
    let profileMap = {};
    if (usedIds.length) {
      const { data: profs } = await supabase.from("profiles").select("id,email").in("id", usedIds);
      (profs || []).forEach((p) => (profileMap[p.id] = p.email));
    }
    const invites = (inv || []).map((r) => ({
      code: r.code,
      created_at: r.created_at,
      used_at: r.used_at,
      used_by: r.used_by ? (profileMap[r.used_by] || "已使用") : null,
    }));
    return json({ invites });
  }

  if (req.method === "POST") {
    let count = 1;
    try {
      const b = await req.json();
      count = Math.min(Math.max(parseInt(b.count, 10) || 1, 1), 20);
    } catch { /* 默认 1 */ }
    const codes = [];
    for (let i = 0; i < count; i++) {
      const code = genCode();
      codes.push(code);
      await supabase.from("invites").insert({ code, created_by: user.id });
    }
    return json({ ok: true, codes });
  }

  return json({ error: "method not allowed" }, 405);
});

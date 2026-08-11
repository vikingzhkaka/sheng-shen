// 省身 · Supabase 配置
// 部署步骤（详见 README）：
// 1. 在 supabase.com 建一个免费项目
// 2. Project Settings → API 里复制 Project URL 和 anon public key
// 3. 填到下面，保存后重新推送（anon key 本身是公开的，安全靠 RLS 行级权限，不是靠藏 key）
window.SUPABASE_CONFIG = {
  url: "",
  anon: ""
};

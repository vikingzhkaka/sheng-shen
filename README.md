# 省身 · 每日三省

多用户版「复盘教练」（典出《论语》"吾日三省吾身"）：三省吾身式引导对话 + 按账户存档 + 按天导出 markdown。
邮箱 + 密码注册（凭邀请码），数据按用户隔离，AI key 只存在服务端环境变量。

**技术栈：GitHub Pages（前端）+ Supabase（认证 / Postgres / Edge Functions）——全程免费，不需要绑定任何银行卡。**

## 架构

```
浏览器（GitHub Pages 托管的前端）
  ├─ Supabase Auth      邮箱+密码登录（官方托管，密码 bcrypt）
  ├─ Postgres + RLS     数据按 user_id 行级隔离
  ├─ Edge Function      注册（邀请码校验）、AI 对话代理（key 在函数环境变量里）
  └─ 导出                前端直接查库生成 md / zip（JSZip），无需后端
```

## 目录

```
├── index.html           前端（省身 UI：登录注册/教练对话/存档/导出/邀请码）
├── supabase-config.js   Supabase Project URL + anon key（部署时填）
├── logo.svg / logo-mark.svg  品牌 logo 与图标
└── supabase/
    ├── schema.sql       建表 + 行级安全（RLS）
    └── functions/
        ├── register/    注册：校验邀请码 → 建用户 → 标记邀请码已用（首个用户自动成管理员）
        ├── chat/        AI 对话代理：校验 JWT → 转发 SenseNova 网关
        └── invites/     邀请码管理（仅管理员）
```

## 部署步骤（约 15 分钟）

### 1. 建 Supabase 免费项目
[supabase.com](https://supabase.com) → GitHub 登录 → **New project**（免费档，**不需要银行卡**）。
记下：Project URL、anon public key、service_role key（Project Settings → API）。

### 2. 建表
Dashboard → **SQL Editor** → 粘贴执行 `supabase/schema.sql`（幂等，可重复跑）。

### 3. 部署三个 Edge Function
两种方式任选：

**方式一：CLI（推荐）**
```bash
npm i -g supabase
supabase login
supabase link --project-ref <你的项目ref>
supabase functions deploy register --no-verify-jwt   # 注册必须允许未登录调用
supabase functions deploy chat
supabase functions deploy invites
supabase secrets set SENSENOVA_API_KEY=<你的 SenseNova key>
```

**方式二：Dashboard**
Edge Functions → Deploy → 分别选择 `supabase/functions/register|chat|invites` 目录上传；
`register` 部署时记得在函数配置里关闭 JWT 校验（"Verify JWT" 关掉），其余保持开启。

### 4. 填前端配置并发布
把 `supabase-config.js` 里的 `url` / `anon` 填上（anon key 是公开的，安全靠 RLS，不是靠藏 key），提交推送。
GitHub 仓库 → Settings → Pages → 部署分支 `main`、路径 `/`（本仓库已用 gh 开启过，检查一下即可）。
发布后访问 `https://vikingzhkaka.github.io/sheng-shen/`。

### 5. 开始用
- **第一个注册的账号自动成为管理员**。
- 管理员登录后点右上角「🎟 邀请码」生成邀请码，发给想用的人；凭码注册。
- 邀请码表对客户端不可见（RLS deny），校验只在 Edge Function 里做，别人读不到有效码。

## 免费额度（对私人/小团队足够）

| 项 | 免费档 |
|---|---|
| Auth | 5 万月活用户 |
| Postgres | 500 MB |
| Edge Functions | 50 万次调用/月 |
| GitHub Pages | 无限静态托管 |

## 导出格式（Obsidian 可直接打开）

每天一个文件 `YYYY-MM-DD.md`，带 YAML frontmatter，含当日全部条目：

```markdown
---
date: 2026-08-11
type: sheng-shen-daily
tags: [复盘日记]
---

# 省身 · 2026-08-11

## 🌿 标题
### 2026-08-11 23:30

#### Tags
**Emotions:** Thoughtful
**People:** —
**Topics:** Self-Understanding

#### Reflection
...

#### Entry
**viking:** ...
```

## 安全设计

| 项 | 做法 |
|---|---|
| AI key | 只存 Edge Function 环境变量，不入仓库、不进前端 |
| 密码 | Supabase Auth 托管（bcrypt 哈希） |
| 会话 | Supabase 官方 JWT 会话 |
| 数据隔离 | Postgres RLS：`auth.uid() = user_id`，跨用户读写被数据库层拒绝 |
| 邀请码 | 表对客户端不可见；校验/标记在 Edge Function（service role）内原子完成 |
| 权限 | 生成/查看邀请码仅管理员；AI 对话必须带有效登录态 |

## 本地开发

```bash
cd 本目录
python3 -m http.server 8080   # 打开 http://localhost:8080
```
（需要 `supabase-config.js` 已配置；Edge Functions 已开启 CORS，本地也能调通。）

# 省身 · 每日三省

多用户版「复盘教练」（典出《论语》"吾日三省吾身"）：三省吾身式引导对话 + 按账户存档 + 按天导出 markdown。
邮箱+密码注册（需邀请码），数据按用户隔离，AI key 只存在服务端环境变量。

纯 Python 标准库 + SQLite，**零第三方依赖**。

## 目录结构

```
rosebud-cloud/
├── server.py          # 后端：注册/登录/会话/存档/导出/AI 代理（标准库）
├── index.html         # 前端：登录注册/教练对话/我的存档/邀请码管理
├── config.json        # 模型配置（不含 key）
├── render.yaml        # Render 一键部署清单
└── requirements.txt   # 空（纯标准库）
```

## 本地运行

```bash
cd rosebud-cloud
SENSENOVA_API_KEY=你的key \
ADMIN_EMAIL=admin@example.com \
ADMIN_PASSWORD=你的管理员密码 \
INVITE_CODES=给朋友的首批邀请码 \
python3 server.py
# 打开 http://localhost:8732
```

## 部署到 Render（公网可用，手机随时访问）

1. 把本目录推到一个 GitHub 仓库（**仓库里没有 key，可放心公开；建议先私有**）。
2. 到 [render.com](https://render.com) 注册（GitHub 登录即可）。
3. **New → Blueprint**，选你的仓库 → 自动按 `render.yaml` 部署；或 **New → Web Service**：
   - Runtime: **Python**
   - Build Command: 留空（默认 `pip install -r requirements.txt`）
   - Start Command: `python server.py`
4. 首次部署时按提示填环境变量（`render.yaml` 里标了 `sync: false` 的那几个）：
   | 变量 | 说明 |
   |---|---|
   | `SENSENOVA_API_KEY` | AI key，只存服务端，**绝不入仓库/前端** |
   | `ADMIN_EMAIL` / `ADMIN_PASSWORD` | 管理员账号（启动时自动创建） |
   | `INVITE_CODES` | 可选，预置邀请码，逗号分隔 |
5. 部署完成得到一个 `https://xxx.onrender.com` 的公网地址，手机浏览器直接打开就能用。

### ⚠️ 免费实例的数据持久化（重要）

Render 免费实例**没有持久磁盘**：重启、重新部署都会清空 SQLite 数据（账户+存档全没）。
生产可用方案任选其一：

- **升到 Starter 及以上** + 在 `render.yaml` 启用 `disk` 块（已注释好，去掉注释即可），数据存挂载盘；
- 或者后续把存储换成外部 Postgres（Neon / Supabase 有免费层），把 `server.py` 的 DB 层换掉。

## 邀请码流程

- 注册必须带邀请码（防止陌生人注册烧你的 AI 额度）。
- 管理员登录后，点右上角 **🎟 邀请码** 生成；生成的码在「邀请码管理」里可看使用状态。
- 也可以启动时用 `INVITE_CODES` 预置一批。

## 安全设计（为什么可以放心公开仓库）

| 项 | 做法 |
|---|---|
| AI key | 只读环境变量 `SENSENOVA_API_KEY`，不落盘、不入仓库、不进前端 |
| 密码 | `hashlib.scrypt`（随机盐），不存明文 |
| 会话 | 随机 32 字节 token，库里只存 sha256，30 天过期，可退出吊销 |
| 数据隔离 | 每条记录绑定 `user_id`，接口层强制过滤，跨用户读写直接拒绝 |
| 权限 | 生成邀请码需管理员；未登录一律 401 |

## API 一览

| 方法 | 路径 | 说明 | 权限 |
|---|---|---|---|
| POST | `/api/register` | 注册（邮箱+密码+邀请码） | 公开 |
| POST | `/api/login` | 登录 → token | 公开 |
| POST | `/api/logout` | 退出 | 登录 |
| GET | `/api/me` | 我的信息 | 登录 |
| GET | `/api/config` | 模型列表（无 key） | 公开 |
| POST | `/api/chat` | AI 教练对话 | 登录 |
| POST | `/api/save` | 保存一条复盘 | 登录 |
| GET | `/api/entries` | 我的存档列表 | 登录 |
| DELETE | `/api/entries/:id` | 删除（仅本人） | 登录 |
| GET | `/api/export?day=YYYY-MM-DD` | 导出当日 md | 登录 |
| GET | `/api/export?from=..&to=..` | 导出区间 zip（每日一 md） | 登录 |
| POST | `/api/invite` | 生成邀请码 | 管理员 |
| GET | `/api/invites` | 邀请码列表/使用状态 | 管理员 |

## 导出格式（Obsidian 可直接打开）

每天一个文件 `YYYY-MM-DD.md`，带 YAML frontmatter，含当日全部条目：

```markdown
---
date: 2026-08-11
type: rosebud-daily
tags: [复盘日记]
---

# 省身 · 2026-08-11

## 🌹 标题
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

## 模型

两个模型都由 SenseNova 网关（`https://token.sensenova.cn/v1`）统一提供，同一把 key：
主 `deepseek-v4-flash`（文字），辅 `sensenova-6.7-flash-lite`（多模态），前端可切换。

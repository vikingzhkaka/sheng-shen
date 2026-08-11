-- 省身 · Supabase 数据库结构 + 行级安全（RLS）
-- 在 Supabase 控制台 → SQL Editor 里整段粘贴执行即可（可重复执行，幂等）。

create extension if not exists pgcrypto;

-- 用户资料（id 关联 Supabase auth.users）
create table if not exists profiles (
  id uuid primary key references auth.users(id) on delete cascade,
  email text unique not null,
  is_admin boolean not null default false,
  created_at timestamptz not null default now()
);

-- 邀请码（客户端不可读，只有 Edge Function 用 service role 操作）
create table if not exists invites (
  code text primary key,
  created_at timestamptz not null default now(),
  created_by uuid references auth.users(id),
  used_by uuid references auth.users(id),
  used_at timestamptz
);

-- 复盘条目（按天）
create table if not exists entries (
  id uuid primary key default gen_random_uuid(),
  user_id uuid not null references auth.users(id) on delete cascade,
  day date not null,
  title text,
  emoji text,
  emotions jsonb not null default '[]',
  people jsonb not null default '[]',
  topics jsonb not null default '[]',
  reflection text,
  transcript text,
  created_at timestamptz not null default now()
);

create index if not exists idx_entries_user_day on entries(user_id, day);

-- ---------- 行级安全 ----------
alter table profiles enable row level security;
alter table invites enable row level security;
alter table entries enable row level security;

-- profiles：本人可读（写由 Edge Function 用 service role 完成）
drop policy if exists profiles_self_select on profiles;
create policy profiles_self_select on profiles
  for select using (auth.uid() = id);

-- entries：只有本人能增删查；不开放 update（避免改历史，重写即删）
drop policy if exists entries_owner_select on entries;
create policy entries_owner_select on entries
  for select using (auth.uid() = user_id);
drop policy if exists entries_owner_insert on entries;
create policy entries_owner_insert on entries
  for insert with check (auth.uid() = user_id);
drop policy if exists entries_owner_delete on entries;
create policy entries_owner_delete on entries
  for delete using (auth.uid() = user_id);

-- invites：不给客户端任何 policy → 默认 deny，邀请码只在 Edge Function 里流转

-- AI Daily anonymous feedback schema for Supabase Postgres.
-- Raw email addresses are never stored. recipient_id is an HMAC-derived,
-- issue-scoped identifier created by the local email packager.

create table if not exists public.ratings (
  issue_id text not null check (issue_id ~ '^[A-Za-z0-9._-]{1,64}$'),
  recipient_id text not null check (recipient_id ~ '^[a-f0-9]{32}$'),
  score smallint not null check (score between 1 and 5),
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  primary key (issue_id, recipient_id)
);

create table if not exists public.comments (
  issue_id text not null check (issue_id ~ '^[A-Za-z0-9._-]{1,64}$'),
  recipient_id text not null check (recipient_id ~ '^[a-f0-9]{32}$'),
  comment_text text not null check (char_length(comment_text) between 1 and 300),
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  primary key (issue_id, recipient_id)
);

create table if not exists public.feedback_events (
  event_id text primary key check (char_length(event_id) between 1 and 128),
  issue_id text not null check (issue_id ~ '^[A-Za-z0-9._-]{1,64}$'),
  recipient_id text not null check (recipient_id ~ '^[a-f0-9]{32}$'),
  event_type text not null check (event_type in ('rating', 'comment')),
  event_value text,
  created_at timestamptz not null default now()
);

create table if not exists public.open_events (
  issue_id text not null check (issue_id ~ '^[A-Za-z0-9._-]{1,64}$'),
  recipient_id text not null check (recipient_id ~ '^[a-f0-9]{32}$'),
  first_opened_at timestamptz not null default now(),
  last_opened_at timestamptz not null default now(),
  open_count integer not null default 1 check (open_count > 0),
  primary key (issue_id, recipient_id)
);

create table if not exists public.deliveries (
  issue_id text primary key check (issue_id ~ '^[A-Za-z0-9._-]{1,64}$'),
  issue_number integer check (issue_number is null or issue_number > 0),
  series text,
  sent_at timestamptz,
  recipient_count integer not null default 0 check (recipient_count >= 0),
  recipient_group text,
  recipient_fingerprint text,
  updated_at timestamptz not null default now()
);

create index if not exists feedback_events_issue_created_idx
  on public.feedback_events (issue_id, created_at desc);

create index if not exists comments_issue_updated_idx
  on public.comments (issue_id, updated_at desc);

create index if not exists deliveries_sent_at_idx
  on public.deliveries (sent_at desc nulls last);

alter table public.ratings enable row level security;
alter table public.comments enable row level security;
alter table public.feedback_events enable row level security;
alter table public.open_events enable row level security;
alter table public.deliveries enable row level security;

-- No browser client talks to Supabase directly. Only Vercel server routes use
-- the server-side secret key, which resolves to the service_role.
revoke all on public.ratings from anon, authenticated;
revoke all on public.comments from anon, authenticated;
revoke all on public.feedback_events from anon, authenticated;
revoke all on public.open_events from anon, authenticated;
revoke all on public.deliveries from anon, authenticated;

grant select, insert, update on public.ratings to service_role;
grant select, insert, update on public.comments to service_role;
grant select, insert on public.feedback_events to service_role;
grant select, insert, update on public.open_events to service_role;
grant select, insert, update on public.deliveries to service_role;

create or replace function public.record_feedback_rating(
  p_event_id text,
  p_issue_id text,
  p_recipient_id text,
  p_score smallint
)
returns table (
  issue_id text,
  score smallint,
  created_at timestamptz,
  updated_at timestamptz,
  duplicate_event boolean
)
language plpgsql
security definer
set search_path = public
as $$
declare
  inserted_count integer;
begin
  if p_score < 1 or p_score > 5 then
    raise exception 'Score must be between 1 and 5.';
  end if;

  insert into public.feedback_events (
    event_id, issue_id, recipient_id, event_type, event_value
  )
  values (
    p_event_id, p_issue_id, p_recipient_id, 'rating', p_score::text
  )
  on conflict on constraint feedback_events_pkey do nothing;
  get diagnostics inserted_count = row_count;

  if inserted_count = 1 then
    insert into public.ratings (
      issue_id, recipient_id, score, created_at, updated_at
    )
    values (
      p_issue_id, p_recipient_id, p_score, now(), now()
    )
    on conflict on constraint ratings_pkey do update set
      score = excluded.score,
      updated_at = excluded.updated_at;
  end if;

  return query
  select
    r.issue_id,
    r.score,
    r.created_at,
    r.updated_at,
    inserted_count = 0
  from public.ratings r
  where r.issue_id = p_issue_id and r.recipient_id = p_recipient_id;
end;
$$;

create or replace function public.record_feedback_comment(
  p_event_id text,
  p_issue_id text,
  p_recipient_id text,
  p_comment text
)
returns table (
  issue_id text,
  comment text,
  created_at timestamptz,
  updated_at timestamptz,
  duplicate_event boolean
)
language plpgsql
security definer
set search_path = public
as $$
declare
  normalized_comment text;
  inserted_count integer;
begin
  normalized_comment := regexp_replace(trim(p_comment), '\s+', ' ', 'g');
  if char_length(normalized_comment) < 1 or char_length(normalized_comment) > 300 then
    raise exception 'Comment must contain 1 to 300 characters.';
  end if;

  insert into public.feedback_events (
    event_id, issue_id, recipient_id, event_type, event_value
  )
  values (
    p_event_id, p_issue_id, p_recipient_id, 'comment', normalized_comment
  )
  on conflict on constraint feedback_events_pkey do nothing;
  get diagnostics inserted_count = row_count;

  if inserted_count = 1 then
    insert into public.comments (
      issue_id, recipient_id, comment_text, created_at, updated_at
    )
    values (
      p_issue_id, p_recipient_id, normalized_comment, now(), now()
    )
    on conflict on constraint comments_pkey do update set
      comment_text = excluded.comment_text,
      updated_at = excluded.updated_at;
  end if;

  return query
  select
    c.issue_id,
    c.comment_text,
    c.created_at,
    c.updated_at,
    inserted_count = 0
  from public.comments c
  where c.issue_id = p_issue_id and c.recipient_id = p_recipient_id;
end;
$$;

create or replace function public.record_feedback_open(
  p_issue_id text,
  p_recipient_id text
)
returns void
language plpgsql
security definer
set search_path = public
as $$
begin
  insert into public.open_events (
    issue_id, recipient_id, first_opened_at, last_opened_at, open_count
  )
  values (
    p_issue_id, p_recipient_id, now(), now(), 1
  )
  on conflict on constraint open_events_pkey do update set
    last_opened_at = excluded.last_opened_at,
    open_count = public.open_events.open_count + 1;
end;
$$;

create or replace function public.upsert_feedback_delivery(
  p_issue_id text,
  p_issue_number integer default null,
  p_series text default null,
  p_sent_at timestamptz default null,
  p_recipient_count integer default 0,
  p_recipient_group text default null,
  p_recipient_fingerprint text default null
)
returns table (
  issue_id text,
  recipient_count integer
)
language plpgsql
security definer
set search_path = public
as $$
begin
  insert into public.deliveries (
    issue_id,
    issue_number,
    series,
    sent_at,
    recipient_count,
    recipient_group,
    recipient_fingerprint,
    updated_at
  )
  values (
    p_issue_id,
    p_issue_number,
    p_series,
    p_sent_at,
    p_recipient_count,
    p_recipient_group,
    p_recipient_fingerprint,
    now()
  )
  on conflict on constraint deliveries_pkey do update set
    issue_number = excluded.issue_number,
    series = excluded.series,
    sent_at = excluded.sent_at,
    recipient_count = excluded.recipient_count,
    recipient_group = excluded.recipient_group,
    recipient_fingerprint = excluded.recipient_fingerprint,
    updated_at = excluded.updated_at;

  return query
  select d.issue_id, d.recipient_count
  from public.deliveries d
  where d.issue_id = p_issue_id;
end;
$$;

revoke all on function public.record_feedback_rating(text, text, text, smallint) from public;
revoke all on function public.record_feedback_comment(text, text, text, text) from public;
revoke all on function public.record_feedback_open(text, text) from public;
revoke all on function public.upsert_feedback_delivery(
  text, integer, text, timestamptz, integer, text, text
) from public;

grant execute on function public.record_feedback_rating(text, text, text, smallint) to service_role;
grant execute on function public.record_feedback_comment(text, text, text, text) to service_role;
grant execute on function public.record_feedback_open(text, text) to service_role;
grant execute on function public.upsert_feedback_delivery(
  text, integer, text, timestamptz, integer, text, text
) to service_role;

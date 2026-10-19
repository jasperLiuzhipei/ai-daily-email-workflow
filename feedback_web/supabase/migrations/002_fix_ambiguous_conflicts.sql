-- Fix ambiguous output-column references inside PL/pgSQL upserts.
-- Explicit constraint names avoid collisions with RETURNS TABLE variables.

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

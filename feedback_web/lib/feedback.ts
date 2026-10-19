import {supabaseAdmin} from "@/lib/supabase";
import {
  FeedbackInputError,
  type FeedbackIdentity,
  validateEventId,
  validateIssueId,
} from "@/lib/token";
import {
  normalizeClickTitle,
  validateClickDestination,
  validateLinkId,
} from "@/lib/click";

type DeliveryInput = {
  issue_id: string;
  issue_number?: number | null;
  series?: string | null;
  sent_at?: string | null;
  recipient_count?: number;
  recipient_group?: string | null;
  recipient_fingerprint?: string | null;
};

export type IssueSummary = {
  issue_id: string;
  issue_number: number | null;
  series: string;
  sent_at: string;
  recipient_count: number;
  recipient_group: string;
  recipient_fingerprint: string;
  rating_count: number;
  average_score: number | null;
  distribution: Record<string, number>;
  comment_count: number;
  estimated_unique_opens: number;
  estimated_total_opens: number;
  first_opened_at: string;
  last_opened_at: string;
  estimated_open_rate: number | null;
  rating_rate: number | null;
  comment_rate: number | null;
  candidate_unique_clicks: number;
  candidate_total_clicks: number;
  effective_unique_clicks: number;
  effective_total_clicks: number;
  filtered_click_requests: number;
  effective_click_rate: number | null;
};

export type ClickLinkSummary = {
  link_id: string;
  link_title: string;
  destination_url: string;
  candidate_unique_clicks: number;
  candidate_total_clicks: number;
  effective_unique_clicks: number;
  effective_total_clicks: number;
  filtered_click_requests: number;
  effective_click_rate: number | null;
};

type ClickEventRow = {
  recipient_id: string;
  link_id: string;
  link_title: string;
  destination_url: string;
  request_count: number;
  qualified_click_count: number;
};

function rate(value: number, denominator: number): number | null {
  return denominator > 0 ? Math.round((value * 1000) / denominator) / 10 : null;
}

function rpcRow<T>(data: T[] | T | null): T {
  const value = Array.isArray(data) ? data[0] : data;
  if (!value) {
    throw new Error("The database did not return a result.");
  }
  return value;
}

export async function recordRating(
  identity: FeedbackIdentity,
  score: number,
  eventId: unknown,
) {
  if (!Number.isInteger(score) || score < 1 || score > 5) {
    throw new FeedbackInputError("Score must be between 1 and 5.");
  }
  const {data, error} = await supabaseAdmin().rpc("record_feedback_rating", {
    p_event_id: validateEventId(eventId),
    p_issue_id: identity.issueId,
    p_recipient_id: identity.recipientId,
    p_score: score,
  });
  if (error) {
    throw error;
  }
  return rpcRow<{
    issue_id: string;
    score: number;
    created_at: string;
    updated_at: string;
    duplicate_event: boolean;
  }>(data);
}

export async function recordComment(
  identity: FeedbackIdentity,
  comment: unknown,
  eventId: unknown,
) {
  const text = String(comment ?? "")
    .trim()
    .split(/\s+/)
    .join(" ");
  if (text.length < 1 || text.length > 300) {
    throw new FeedbackInputError("Comment must contain 1 to 300 characters.");
  }
  const {data, error} = await supabaseAdmin().rpc("record_feedback_comment", {
    p_event_id: validateEventId(eventId),
    p_issue_id: identity.issueId,
    p_recipient_id: identity.recipientId,
    p_comment: text,
  });
  if (error) {
    throw error;
  }
  return rpcRow<{
    issue_id: string;
    comment: string;
    created_at: string;
    updated_at: string;
    duplicate_event: boolean;
  }>(data);
}

export async function recordOpen(identity: FeedbackIdentity): Promise<void> {
  const {error} = await supabaseAdmin().rpc("record_feedback_open", {
    p_issue_id: identity.issueId,
    p_recipient_id: identity.recipientId,
  });
  if (error) {
    throw error;
  }
}

export async function recordClick(
  identity: FeedbackIdentity,
  input: {
    linkId: unknown;
    linkTitle: unknown;
    destinationUrl: unknown;
    qualified: boolean;
  },
) {
  if (typeof input.qualified !== "boolean") {
    throw new FeedbackInputError("Invalid click classification.");
  }
  const {data, error} = await supabaseAdmin().rpc("record_feedback_click", {
    p_issue_id: identity.issueId,
    p_recipient_id: identity.recipientId,
    p_link_id: validateLinkId(input.linkId),
    p_link_title: normalizeClickTitle(input.linkTitle),
    p_destination_url: validateClickDestination(input.destinationUrl),
    p_is_qualified: input.qualified,
  });
  if (error) {
    throw error;
  }
  return rpcRow<{
    issue_id: string;
    link_id: string;
    request_count: number;
    qualified_click_count: number;
  }>(data);
}

export async function upsertDelivery(input: DeliveryInput) {
  const issueId = validateIssueId(String(input.issue_id ?? ""));
  const recipientCount = Number(input.recipient_count ?? 0);
  if (!Number.isInteger(recipientCount) || recipientCount < 0) {
    throw new FeedbackInputError("Recipient count cannot be negative.");
  }
  const issueNumber =
    input.issue_number === null || input.issue_number === undefined
      ? null
      : Number(input.issue_number);
  if (issueNumber !== null && (!Number.isInteger(issueNumber) || issueNumber <= 0)) {
    throw new FeedbackInputError("Issue number must be positive.");
  }
  const {data, error} = await supabaseAdmin().rpc("upsert_feedback_delivery", {
    p_issue_id: issueId,
    p_issue_number: issueNumber,
    p_series: input.series ?? null,
    p_sent_at: input.sent_at ?? null,
    p_recipient_count: recipientCount,
    p_recipient_group: input.recipient_group ?? null,
    p_recipient_fingerprint: input.recipient_fingerprint ?? null,
  });
  if (error) {
    throw error;
  }
  return rpcRow<{issue_id: string; recipient_count: number}>(data);
}

async function clickEventRows(issueId: string): Promise<ClickEventRow[]> {
  const {data, error} = await supabaseAdmin()
    .from("click_events")
    .select("recipient_id,link_id,link_title,destination_url,request_count,qualified_click_count")
    .eq("issue_id", issueId);
  if (error) {
    throw error;
  }
  return (data ?? []) as ClickEventRow[];
}

function clickTotals(rows: ClickEventRow[]) {
  const candidateRecipients = new Set<string>();
  const effectiveRecipients = new Set<string>();
  let candidateTotal = 0;
  let effectiveTotal = 0;
  for (const row of rows) {
    const requestCount = Number(row.request_count || 0);
    const qualifiedCount = Number(row.qualified_click_count || 0);
    if (requestCount > 0) {
      candidateRecipients.add(row.recipient_id);
      candidateTotal += requestCount;
    }
    if (qualifiedCount > 0) {
      effectiveRecipients.add(row.recipient_id);
      effectiveTotal += qualifiedCount;
    }
  }
  return {
    candidateUnique: candidateRecipients.size,
    candidateTotal,
    effectiveUnique: effectiveRecipients.size,
    effectiveTotal,
  };
}

export async function issueSummary(issueId: string): Promise<IssueSummary> {
  validateIssueId(issueId);
  const client = supabaseAdmin();
  const [ratingsResult, commentsResult, opensResult, deliveryResult, clicks] = await Promise.all([
    client.from("ratings").select("score").eq("issue_id", issueId),
    client
      .from("comments")
      .select("*", {count: "exact", head: true})
      .eq("issue_id", issueId),
    client
      .from("open_events")
      .select("open_count,first_opened_at,last_opened_at")
      .eq("issue_id", issueId),
    client.from("deliveries").select("*").eq("issue_id", issueId).maybeSingle(),
    clickEventRows(issueId),
  ]);
  const error =
    ratingsResult.error ||
    commentsResult.error ||
    opensResult.error ||
    deliveryResult.error;
  if (error) {
    throw error;
  }

  const ratings = (ratingsResult.data ?? []) as Array<{score: number}>;
  const opens = (opensResult.data ?? []) as Array<{
    open_count: number;
    first_opened_at: string;
    last_opened_at: string;
  }>;
  const delivery = deliveryResult.data as Record<string, unknown> | null;
  const distribution = Object.fromEntries(
    Array.from({length: 5}, (_, index) => [String(index + 1), 0]),
  ) as Record<string, number>;
  let totalScore = 0;
  for (const row of ratings) {
    distribution[String(row.score)] += 1;
    totalScore += row.score;
  }
  const uniqueOpens = opens.length;
  const totalOpens = opens.reduce((sum, row) => sum + Number(row.open_count || 0), 0);
  const openTimes = opens.flatMap((row) => [row.first_opened_at, row.last_opened_at]).sort();
  const recipientCount = Number(delivery?.recipient_count ?? 0);
  const ratingCount = ratings.length;
  const commentCount = commentsResult.count ?? 0;
  const click = clickTotals(clicks);

  return {
    issue_id: issueId,
    issue_number:
      delivery?.issue_number === null || delivery?.issue_number === undefined
        ? null
        : Number(delivery.issue_number),
    series: String(delivery?.series ?? ""),
    sent_at: String(delivery?.sent_at ?? ""),
    recipient_count: recipientCount,
    recipient_group: String(delivery?.recipient_group ?? ""),
    recipient_fingerprint: String(delivery?.recipient_fingerprint ?? ""),
    rating_count: ratingCount,
    average_score:
      ratingCount > 0 ? Math.round((totalScore * 100) / ratingCount) / 100 : null,
    distribution,
    comment_count: commentCount,
    estimated_unique_opens: uniqueOpens,
    estimated_total_opens: totalOpens,
    first_opened_at: openTimes[0] ?? "",
    last_opened_at: openTimes.at(-1) ?? "",
    estimated_open_rate: rate(uniqueOpens, recipientCount),
    rating_rate: rate(ratingCount, recipientCount),
    comment_rate: rate(commentCount, recipientCount),
    candidate_unique_clicks: click.candidateUnique,
    candidate_total_clicks: click.candidateTotal,
    effective_unique_clicks: click.effectiveUnique,
    effective_total_clicks: click.effectiveTotal,
    filtered_click_requests: click.candidateTotal - click.effectiveTotal,
    effective_click_rate: rate(click.effectiveUnique, recipientCount),
  };
}

export async function listIssueSummaries(): Promise<IssueSummary[]> {
  const client = supabaseAdmin();
  const [deliveries, ratings, comments, opens, clicks] = await Promise.all([
    client.from("deliveries").select("issue_id,sent_at").order("sent_at", {ascending: false}),
    client.from("ratings").select("issue_id"),
    client.from("comments").select("issue_id"),
    client.from("open_events").select("issue_id"),
    client.from("click_events").select("issue_id"),
  ]);
  const error = deliveries.error || ratings.error || comments.error || opens.error || clicks.error;
  if (error) {
    throw error;
  }
  const ids = new Set<string>();
  for (const result of [deliveries.data, ratings.data, comments.data, opens.data, clicks.data]) {
    for (const row of result ?? []) {
      ids.add(String(row.issue_id));
    }
  }
  const order = new Map(
    (deliveries.data ?? []).map((row, index) => [String(row.issue_id), index]),
  );
  const summaries = await Promise.all([...ids].map((id) => issueSummary(id)));
  return summaries.sort((left, right) => {
    const leftOrder = order.get(left.issue_id);
    const rightOrder = order.get(right.issue_id);
    if (leftOrder !== undefined || rightOrder !== undefined) {
      return (leftOrder ?? Number.MAX_SAFE_INTEGER) - (rightOrder ?? Number.MAX_SAFE_INTEGER);
    }
    return right.issue_id.localeCompare(left.issue_id);
  });
}

export async function listClickLinkSummaries(issueId: string): Promise<ClickLinkSummary[]> {
  validateIssueId(issueId);
  const rows = await clickEventRows(issueId);
  const grouped = new Map<
    string,
    {
      link_id: string;
      link_title: string;
      destination_url: string;
      candidate_recipients: Set<string>;
      effective_recipients: Set<string>;
      candidate_total: number;
      effective_total: number;
    }
  >();
  for (const row of rows) {
    const item = grouped.get(row.link_id) ?? {
      link_id: row.link_id,
      link_title: row.link_title,
      destination_url: row.destination_url,
      candidate_recipients: new Set<string>(),
      effective_recipients: new Set<string>(),
      candidate_total: 0,
      effective_total: 0,
    };
    const requestCount = Number(row.request_count || 0);
    const qualifiedCount = Number(row.qualified_click_count || 0);
    if (requestCount > 0) {
      item.candidate_recipients.add(row.recipient_id);
      item.candidate_total += requestCount;
    }
    if (qualifiedCount > 0) {
      item.effective_recipients.add(row.recipient_id);
      item.effective_total += qualifiedCount;
    }
    if (!item.link_title && row.link_title) {
      item.link_title = row.link_title;
    }
    if (!item.destination_url && row.destination_url) {
      item.destination_url = row.destination_url;
    }
    grouped.set(row.link_id, item);
  }
  const summary = await issueSummary(issueId);
  return [...grouped.values()]
    .map((item) => ({
      link_id: item.link_id,
      link_title: item.link_title,
      destination_url: item.destination_url,
      candidate_unique_clicks: item.candidate_recipients.size,
      candidate_total_clicks: item.candidate_total,
      effective_unique_clicks: item.effective_recipients.size,
      effective_total_clicks: item.effective_total,
      filtered_click_requests: item.candidate_total - item.effective_total,
      effective_click_rate: rate(item.effective_recipients.size, summary.recipient_count),
    }))
    .sort(
      (left, right) =>
        right.effective_unique_clicks - left.effective_unique_clicks ||
        right.effective_total_clicks - left.effective_total_clicks ||
        left.link_id.localeCompare(right.link_id),
    );
}

export async function listComments(issueId: string) {
  validateIssueId(issueId);
  const {data, error} = await supabaseAdmin()
    .from("comments")
    .select("comment_text,created_at,updated_at")
    .eq("issue_id", issueId)
    .order("updated_at", {ascending: false});
  if (error) {
    throw error;
  }
  return (data ?? []) as Array<{
    comment_text: string;
    created_at: string;
    updated_at: string;
  }>;
}

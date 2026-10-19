"use client";

import {useState} from "react";

function eventId(): string {
  return crypto.randomUUID?.() ?? `${Date.now().toString(36)}${Math.random().toString(36).slice(2)}`;
}

export function CommentClient({token}: {token: string}) {
  const [comment, setComment] = useState("");
  const [status, setStatus] = useState("");
  const [statusType, setStatusType] = useState<"" | "ok" | "error">("");
  const [pending, setPending] = useState(false);

  async function submit() {
    const text = comment.trim();
    if (!text) {
      setStatusType("error");
      setStatus("请先写一点内容。");
      return;
    }
    setPending(true);
    setStatusType("");
    setStatus("提交中...");
    try {
      const response = await fetch("/api/comment", {
        method: "POST",
        headers: {"Content-Type": "application/json"},
        body: JSON.stringify({token, comment: text, event_id: eventId()}),
        credentials: "same-origin",
      });
      const result = (await response.json()) as {error?: string};
      if (!response.ok) {
        throw new Error(result.error ?? "提交失败");
      }
      setStatusType("ok");
      setStatus("建议已收到，正在尝试关闭页面...");
      window.setTimeout(() => {
        window.close();
        window.setTimeout(() => setStatus("建议已收到，可以关闭页面。"), 350);
      }, 900);
    } catch {
      setStatusType("error");
      setStatus("提交失败，请重试。");
    } finally {
      setPending(false);
    }
  }

  return (
    <main className="feedback-page">
      <div className="eyebrow">AI Signal Daily</div>
      <h1>写一句建议</h1>
      <p>文本反馈完全可选，只用于改进选源、解读和展示方式。</p>
      <textarea
        className="comment-box"
        maxLength={300}
        placeholder="哪一条最有用，或者哪里可以做得更好？"
        value={comment}
        onChange={(event) => setComment(event.target.value)}
      />
      <div className="actions">
        <button className="primary" type="button" disabled={pending} onClick={() => void submit()}>
          提交建议
        </button>
        <span className={`status ${statusType}`}>{status}</span>
        <span className="count">{comment.length} / 300</span>
      </div>
    </main>
  );
}

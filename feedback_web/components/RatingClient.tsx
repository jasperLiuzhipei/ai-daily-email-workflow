"use client";

import {useEffect, useRef, useState} from "react";

function eventId(): string {
  return crypto.randomUUID?.() ?? `${Date.now().toString(36)}${Math.random().toString(36).slice(2)}`;
}

function scoreFromHash(): number | null {
  const score = Number(new URLSearchParams(location.hash.slice(1)).get("score"));
  return Number.isInteger(score) && score >= 1 && score <= 5 ? score : null;
}

export function RatingClient({token}: {token: string}) {
  const [score, setScore] = useState<number | null>(null);
  const [title, setTitle] = useState("选择一个星级");
  const [status, setStatus] = useState("");
  const [statusType, setStatusType] = useState<"" | "ok" | "error">("");
  const pending = useRef<Record<string, unknown> | null>(null);
  const savedScore = useRef<number | null>(null);
  const started = useRef(false);

  async function submit(nextScore: number) {
    setScore(nextScore);
    setTitle(`正在记录 ${nextScore} 星`);
    setStatus("请稍候...");
    setStatusType("");
    const payload = {token, score: nextScore, event_id: eventId()};
    pending.current = payload;
    try {
      const response = await fetch("/api/rating", {
        method: "POST",
        headers: {"Content-Type": "application/json"},
        body: JSON.stringify(payload),
        keepalive: true,
        credentials: "same-origin",
      });
      const result = (await response.json()) as {score?: number; error?: string};
      if (!response.ok || !result.score) {
        throw new Error(result.error ?? "提交失败");
      }
      pending.current = null;
      savedScore.current = result.score;
      setScore(result.score);
      setTitle(`已记录 ${result.score} 星`);
      setStatusType("ok");
      setStatus("已经保存，正在尝试关闭页面...");
      window.setTimeout(() => {
        window.close();
        window.setTimeout(() => setStatus("已经保存，可以关闭页面。"), 350);
      }, 900);
    } catch {
      pending.current = null;
      setScore(savedScore.current);
      setTitle(savedScore.current ? `已记录 ${savedScore.current} 星` : "评分暂未保存");
      setStatusType("error");
      setStatus("保存失败，请稍后点击星级重试。");
    }
  }

  useEffect(() => {
    if (started.current) {
      return;
    }
    started.current = true;
    const initialScore = scoreFromHash();
    if (initialScore) {
      setScore(initialScore);
      const timer = window.setTimeout(() => void submit(initialScore), 250);
      return () => window.clearTimeout(timer);
    }
  }, []);

  useEffect(() => {
    function sendPending() {
      if (!pending.current || !navigator.sendBeacon) {
        return;
      }
      navigator.sendBeacon(
        "/api/rating",
        new Blob([JSON.stringify(pending.current)], {type: "application/json"}),
      );
    }
    addEventListener("pagehide", sendPending);
    return () => removeEventListener("pagehide", sendPending);
  }, []);

  function choose(nextScore: number) {
    history.replaceState(null, "", `#score=${nextScore}`);
    void submit(nextScore);
  }

  return (
    <main className="feedback-page">
      <div className="eyebrow">AI Signal Daily</div>
      <h1>{title}</h1>
      <p>点击其他星级可以修改，本期始终只保留一条最终评分。</p>
      <div className="stars" aria-label="修改评分">
        {[1, 2, 3, 4, 5].map((value) => (
          <button
            className={`star ${score !== null && value <= score ? "active" : ""}`}
            type="button"
            key={value}
            aria-label={`${value} 星`}
            onClick={() => choose(value)}
          >
            {score !== null && value <= score ? "★" : "☆"}
          </button>
        ))}
      </div>
      <div className={`status ${statusType}`}>{status}</div>
    </main>
  );
}

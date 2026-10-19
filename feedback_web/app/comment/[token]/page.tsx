import {CommentClient} from "@/components/CommentClient";
import {FeedbackInputError, verifyFeedbackToken} from "@/lib/token";

export const dynamic = "force-dynamic";

export default async function CommentPage({
  params,
}: {
  params: Promise<{token: string}>;
}) {
  const {token} = await params;
  try {
    verifyFeedbackToken(token);
    return <CommentClient token={token} />;
  } catch (error) {
    const message =
      error instanceof FeedbackInputError ? error.message : "服务暂时不可用，请稍后重试。";
    return (
      <main className="feedback-page">
        <div className="eyebrow">AI Signal Daily</div>
        <h1>链接暂时不可用</h1>
        <p>{message}</p>
      </main>
    );
  }
}

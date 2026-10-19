export default async function AdminLoginPage({
  searchParams,
}: {
  searchParams: Promise<{error?: string}>;
}) {
  const {error} = await searchParams;
  return (
    <main className="feedback-page">
      <div className="eyebrow">AI Signal Daily</div>
      <h1>数据后台</h1>
      <p>输入后台密码查看匿名阅读、评分与文字意见。</p>
      <form className="login-form" action="/api/admin/login" method="post">
        <input
          type="password"
          name="password"
          autoComplete="current-password"
          aria-label="后台密码"
          required
        />
        <button className="primary" type="submit">
          登录
        </button>
        {error ? <span className="status error">密码不正确。</span> : null}
      </form>
    </main>
  );
}

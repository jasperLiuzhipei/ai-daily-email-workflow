export function requiredEnv(name: string): string {
  const value = process.env[name]?.trim();
  if (!value) {
    throw new Error(`Missing required environment variable: ${name}`);
  }
  return value;
}

export function feedbackSecret(): string {
  const value = requiredEnv("AI_DAILY_FEEDBACK_SECRET");
  if (value.length < 16) {
    throw new Error("AI_DAILY_FEEDBACK_SECRET must contain at least 16 characters.");
  }
  return value;
}

export function adminKey(): string {
  const value = requiredEnv("AI_DAILY_FEEDBACK_ADMIN_KEY");
  if (value.length < 16) {
    throw new Error("AI_DAILY_FEEDBACK_ADMIN_KEY must contain at least 16 characters.");
  }
  return value;
}

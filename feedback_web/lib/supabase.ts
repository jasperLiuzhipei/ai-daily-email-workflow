import {createClient, type SupabaseClient} from "@supabase/supabase-js";
import {requiredEnv} from "@/lib/env";

let client: SupabaseClient | null = null;

export function supabaseAdmin(): SupabaseClient {
  if (!client) {
    client = createClient(
      requiredEnv("SUPABASE_URL"),
      requiredEnv("SUPABASE_SECRET_KEY"),
      {
        auth: {
          autoRefreshToken: false,
          persistSession: false,
        },
      },
    );
  }
  return client;
}

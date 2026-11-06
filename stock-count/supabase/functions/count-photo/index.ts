// Supabase Edge Function 入口。邏輯在 ../_shared/count.ts(與 NAS 版共用)。
// 需設定 secret:ANTHROPIC_API_KEY;選用:APP_PASSCODE、COUNT_MODEL、COUNT_EFFORT、COUNT_CACHE_TTL
// 部署:supabase functions deploy count-photo
//       supabase secrets set ANTHROPIC_API_KEY=sk-ant-...
import { handleCount } from "../_shared/count.ts";

Deno.serve(handleCount);

// Supabase Edge Function:接收貨架照片,請 Claude 辨識並計數。
// 需設定 secret:ANTHROPIC_API_KEY(金鑰只存在後端,不會出現在網頁)。
// 選用 secret:COUNT_MODEL(預設 claude-sonnet-5-5)、COUNT_EFFORT(預設 medium)、COUNT_CACHE_TTL(5m 或 1h,預設 5m)
// 部署:supabase functions deploy count-photo
//       supabase secrets set ANTHROPIC_API_KEY=sk-ant-...
import Anthropic from "npm:@anthropic-ai/sdk";

// ---------------------------------------------------------------------------
// 提示詞快取設計(降低 token 用量,詳見 .claude/skills/stock-count/SKILL.md):
//   快取是「前綴比對」:system 區塊(規則 + 商品清單)放在前面並標記快取,
//   每次都不同的照片放在 messages 最後。前綴任何一個字元不同就會失效,
//   所以商品清單一律排序,且不得放入時間、隨機值等會變動的內容。
// 評測腳本 eval/run_eval.py 會直接讀取下面兩個常數(RULES_TEMPLATE 與 USER_TEXT),請保持格式不變。
// ---------------------------------------------------------------------------
const RULES_TEMPLATE = `你是倉庫盤點助手。使用者會給你一張倉庫貨架上包裝食品的照片,請辨識並數出每種商品「肉眼可見」的數量。
已知商品清單(條碼 | 品名):
{{LIST}}

規則:
- 只數看得到的,被遮住或疊在後面看不到的不要猜;在 note 說明「可能有被遮擋」。
- 能對應清單就填該條碼 code;對不上則 code 設為 null,name 寫你看到的品名。
- 外觀相似的口味/規格請仔細看包裝文字;不確定就把 confidence 設為 "low"。
- 只輸出 JSON,不要其他文字,格式:
{"items":[{"code":string|null,"name":string,"count":number,"confidence":"high"|"medium"|"low","note":string}]}`;

const USER_TEXT = `請辨識並計數這張照片。`;

const CORS = {
  "Access-Control-Allow-Origin": "*",
  "Access-Control-Allow-Headers": "authorization, apikey, content-type",
  "Access-Control-Allow-Methods": "POST, OPTIONS",
};
const rules = (list: string) => RULES_TEMPLATE.replace("{{LIST}}", list || "(無)");

const MEDIA_TYPES = ["image/jpeg", "image/png", "image/webp", "image/gif"];
const EFFORTS = ["low", "medium", "high", "xhigh", "max"];

Deno.serve(async (req) => {
  if (req.method === "OPTIONS") return new Response("ok", { headers: CORS });
  try {
    const { image, media_type = "image/jpeg", products = [] } = await req.json();
    if (!image) return json({ error: "missing image" }, 400);
    if (!MEDIA_TYPES.includes(media_type)) return json({ error: "unsupported media_type" }, 400);
    if (image.length > 7_000_000) return json({ error: "image too large" }, 413);

    // 排序以確保相同商品集合產生完全相同的前綴(快取命中的關鍵)
    const list = [...products]
      .slice(0, 500)
      .sort((a: { code: string }, b: { code: string }) => String(a.code).localeCompare(String(b.code)))
      .map((p: { code: string; name: string }) => `${p.code} | ${p.name}`)
      .join("\n");

    const effort = Deno.env.get("COUNT_EFFORT") ?? "medium";
    const ttl = Deno.env.get("COUNT_CACHE_TTL") === "1h" ? "1h" : "5m";
    const client = new Anthropic({ apiKey: Deno.env.get("ANTHROPIC_API_KEY") });

    const msg = await client.messages.create({
      model: Deno.env.get("COUNT_MODEL") ?? "claude-sonnet-5-5",
      // 思考 token 也計入 max_tokens,不可設太小,否則輸出會被截斷
      max_tokens: 16000,
      output_config: { effort: EFFORTS.includes(effort) ? effort : "medium" },
      system: [{ type: "text", text: rules(list), cache_control: { type: "ephemeral", ttl } }],
      messages: [{
        role: "user",
        content: [
          { type: "image", source: { type: "base64", media_type, data: image } },
          { type: "text", text: USER_TEXT },
        ],
      }],
    });

    const u = msg.usage;
    const usage = {
      input: u.input_tokens,
      cache_write: u.cache_creation_input_tokens ?? 0,
      cache_read: u.cache_read_input_tokens ?? 0,
      output: u.output_tokens,
    };
    if (msg.stop_reason === "refusal") return json({ error: "model refused", usage }, 422);
    if (msg.stop_reason === "max_tokens") return json({ error: "response truncated", usage }, 502);

    const text = msg.content.find((c) => c.type === "text")?.text ?? "";
    const m = text.match(/\{[\s\S]*\}/);
    const items = m ? JSON.parse(m[0]).items ?? [] : [];
    return json({ items, usage });
  } catch (e) {
    if (e instanceof Anthropic.APIError) return json({ error: "upstream " + e.status }, 502);
    return json({ error: String(e) }, 500);
  }
});

function json(body: unknown, status = 200) {
  return new Response(JSON.stringify(body), {
    status,
    headers: { ...CORS, "Content-Type": "application/json" },
  });
}

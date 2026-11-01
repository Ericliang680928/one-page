// Supabase Edge Function:接收貨架照片,請 Claude 辨識並計數。
// 需設定 secret:ANTHROPIC_API_KEY(金鑰只存在後端,不會出現在網頁)。
// 部署:supabase functions deploy count-photo
//       supabase secrets set ANTHROPIC_API_KEY=sk-ant-...

const CORS = {
  "Access-Control-Allow-Origin": "*",
  "Access-Control-Allow-Headers": "authorization, apikey, content-type",
  "Access-Control-Allow-Methods": "POST, OPTIONS",
};

Deno.serve(async (req) => {
  if (req.method === "OPTIONS") return new Response("ok", { headers: CORS });
  try {
    const { image, media_type = "image/jpeg", products = [] } = await req.json();
    if (!image) return json({ error: "missing image" }, 400);
    if (image.length > 7_000_000) return json({ error: "image too large" }, 413);

    const list = products.slice(0, 500)
      .map((p: { code: string; name: string }) => `${p.code} | ${p.name}`).join("\n");

    const prompt = `這是倉庫貨架上包裝食品的照片。請辨識並數出每種商品「肉眼可見」的數量。
已知商品清單(條碼 | 品名):
${list || "(無)"}

規則:
- 只數看得到的,被遮住或疊在後面看不到的不要猜;在 note 說明「可能有被遮擋」。
- 能對應清單就填該條碼 code;對不上則 code 設為 null,name 寫你看到的品名。
- 外觀相似的口味/規格請仔細看包裝文字;不確定就把 confidence 設為 "low"。
- 只輸出 JSON,不要其他文字,格式:
{"items":[{"code":string|null,"name":string,"count":number,"confidence":"high"|"medium"|"low","note":string}]}`;

    const r = await fetch("https://api.anthropic.com/v1/messages", {
      method: "POST",
      headers: {
        "x-api-key": Deno.env.get("ANTHROPIC_API_KEY") ?? "",
        "anthropic-version": "2023-06-01",
        "content-type": "application/json",
      },
      body: JSON.stringify({
        model: "claude-sonnet-5-5",
        max_tokens: 2000,
        messages: [{
          role: "user",
          content: [
            { type: "image", source: { type: "base64", media_type, data: image } },
            { type: "text", text: prompt },
          ],
        }],
      }),
    });
    if (!r.ok) return json({ error: "upstream " + r.status }, 502);

    const text = (await r.json()).content?.find((c: { type: string }) => c.type === "text")?.text ?? "";
    const m = text.match(/\{[\s\S]*\}/);
    const items = m ? JSON.parse(m[0]).items ?? [] : [];
    return json({ items });
  } catch (e) {
    return json({ error: String(e) }, 500);
  }
});

function json(body: unknown, status = 200) {
  return new Response(JSON.stringify(body), {
    status,
    headers: { ...CORS, "Content-Type": "application/json" },
  });
}

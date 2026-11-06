// NAS 伺服器:在同一個程式裡提供「盤點網頁」與「拍照辨識 API」,只需跑一個容器。
//   GET  /                  盤點網頁(index.html)
//   GET  /sw.js             離線快取用的 service worker
//   GET  /api/healthz       健康檢查(網頁也用它自動偵測本機後端)
//   POST /api/count-photo   拍照辨識(邏輯在 ../supabase/functions/_shared/count.ts)
// 只提供上述固定路徑,不提供目錄瀏覽,也不會讀取其他檔案。
//
// 環境變數:
//   ANTHROPIC_API_KEY  必填
//   APP_PASSCODE       必填(除非明確設 ALLOW_NO_PASSCODE=1,不建議)
//   PORT               預設 8787
//   WEB_DIR            網頁檔所在資料夾,預設為此檔的上一層
//   RATE_PER_MIN       每個來源每分鐘最多幾次辨識,預設 12
//   TRUST_PROXY        設 1 時,以 X-Forwarded-For 第一段作為來源(前面有反向代理時才設)
//   COUNT_MODEL / COUNT_EFFORT / COUNT_CACHE_TTL  見 _shared/count.ts
import { handleCount } from "../supabase/functions/_shared/count.ts";

const PORT = Number(Deno.env.get("PORT") ?? "8787");
const RATE_PER_MIN = Number(Deno.env.get("RATE_PER_MIN") ?? "12");
const TRUST_PROXY = Deno.env.get("TRUST_PROXY") === "1";
const MAX_BODY = 10_000_000;
const WEB_DIR = Deno.env.get("WEB_DIR") ?? new URL("../", import.meta.url).pathname;

// ---- 啟動檢查:缺設定就直接停,不要等到有人按拍照才發現 ----
if (!Deno.env.get("ANTHROPIC_API_KEY")) fail("缺少 ANTHROPIC_API_KEY");
if (!Deno.env.get("APP_PASSCODE") && Deno.env.get("ALLOW_NO_PASSCODE") !== "1") {
  fail("缺少 APP_PASSCODE。沒有存取密碼,任何知道網址的人都能消耗你的 API 額度。若確定要略過,設 ALLOW_NO_PASSCODE=1。");
}
function fail(msg: string): never {
  console.error("啟動失敗:" + msg);
  Deno.exit(1);
}

// ---- 固定路徑的靜態檔 ----
const STATIC: Record<string, { file: string; type: string }> = {
  "/": { file: "index.html", type: "text/html; charset=utf-8" },
  "/index.html": { file: "index.html", type: "text/html; charset=utf-8" },
  "/sw.js": { file: "sw.js", type: "text/javascript; charset=utf-8" },
};
const SECURITY_HEADERS: Record<string, string> = {
  "X-Content-Type-Options": "nosniff",
  "Referrer-Policy": "no-referrer",
  "X-Frame-Options": "DENY",
  // 網頁使用行內 script/style,所以需要 unsafe-inline;其餘僅允許本站。
  "Content-Security-Policy":
    "default-src 'self'; script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline'; img-src 'self' data: blob:; media-src 'self' blob:; connect-src 'self' https:; frame-ancestors 'none'",
};

async function serveStatic(path: string): Promise<Response> {
  const s = STATIC[path];
  if (!s) return new Response("Not found", { status: 404 });
  try {
    const body = await Deno.readFile(WEB_DIR.replace(/\/?$/, "/") + s.file);
    return new Response(body, {
      headers: { ...SECURITY_HEADERS, "Content-Type": s.type, "Cache-Control": "no-cache" },
    });
  } catch {
    return new Response("Not found", { status: 404 });
  }
}

// ---- 簡單的流量限制(記憶體內,重啟後歸零) ----
const hits = new Map<string, number[]>();
function limited(ip: string): number {
  const now = Date.now(), cutoff = now - 60_000;
  const arr = (hits.get(ip) ?? []).filter((t) => t > cutoff);
  if (arr.length >= RATE_PER_MIN) {
    hits.set(ip, arr);
    return Math.ceil((arr[0] + 60_000 - now) / 1000);
  }
  arr.push(now);
  hits.set(ip, arr);
  return 0;
}
setInterval(() => {
  const cutoff = Date.now() - 60_000;
  for (const [k, v] of hits) if (!v.some((t) => t > cutoff)) hits.delete(k);
}, 60_000);

function clientIp(req: Request, info: Deno.ServeHandlerInfo): string {
  if (TRUST_PROXY) {
    const f = req.headers.get("x-forwarded-for")?.split(",")[0].trim();
    if (f) return f;
  }
  return (info.remoteAddr as Deno.NetAddr).hostname;
}

const jsonRes = (body: unknown, status = 200, extra: Record<string, string> = {}) =>
  new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json", ...SECURITY_HEADERS, ...extra },
  });

Deno.serve({ port: PORT, hostname: "0.0.0.0" }, async (req, info) => {
  const t0 = Date.now(), url = new URL(req.url), path = url.pathname;
  const ip = clientIp(req, info);
  let res: Response;
  try {
    if (path === "/api/healthz" && req.method === "GET") {
      res = jsonRes({ ok: true, service: "stock-count" }, 200, { "Cache-Control": "no-store" });
    } else if (path === "/api/count-photo") {
      if (req.method === "POST") {
        const len = Number(req.headers.get("content-length") ?? "0");
        const wait = limited(ip);
        if (!len || len > MAX_BODY) res = jsonRes({ error: "payload too large or missing length" }, 413);
        else if (wait) res = jsonRes({ error: "too many requests" }, 429, { "Retry-After": String(wait) });
        else res = await handleCount(req);
      } else {
        res = await handleCount(req); // OPTIONS 等
      }
    } else if (req.method === "GET" || req.method === "HEAD") {
      res = await serveStatic(path);
    } else {
      res = new Response("Method not allowed", { status: 405 });
    }
  } catch (e) {
    console.error("未預期錯誤:", String(e));
    res = jsonRes({ error: "internal error" }, 500);
  }
  // 日誌只記方法、路徑、狀態與來源,不記內容(照片、提示、密碼都不會出現)
  console.log(`${new Date().toISOString()} ${ip} ${req.method} ${path} ${res.status} ${Date.now() - t0}ms`);
  return res;
});

console.log(`stock-count 伺服器啟動:port ${PORT},流量限制 ${RATE_PER_MIN}/分鐘,網頁目錄 ${WEB_DIR}`);

# 在 Synology DS218+ 上部署盤點系統

一個容器同時提供「盤點網頁」與「拍照辨識 API」。API 金鑰只存在 NAS 上,不會出現在手機網頁。

> **驗證狀態(請先讀)**
> - 伺服器程式已用 Deno 2.9.6 實際執行並測過(`nas/test/server.test.js`:驗證、限流、路徑安全、請求內容、錯誤處理),上游 Claude 以模擬服務代替。
> - **Docker 映像尚未實際建置**(開發環境沒有 Docker 引擎),**也沒有在真正的 DS218+ 上跑過**。下面的 DSM 選單名稱依我的記憶與官方文件整理,不同 DSM 版本可能略有差異。
> - **尚未呼叫過真實的 Claude API**。第一次實測請看日誌與回傳的 token 用量。

## 0. 你需要有

| 項目 | 說明 |
|---|---|
| DSM 7.2 以上 | DSM → 控制台 → 更新與還原 |
| Container Manager | 套件中心安裝。DS218+ 是 x86 機種,可以使用(請在套件中心確認) |
| 記憶體 | 出廠 2GB。本容器上限設為 256MB。請在資源監控確認還有餘裕 |
| 對外網路 | 容器要能連到 `api.anthropic.com` |
| Anthropic API 金鑰 | 在 Anthropic Console 建立並儲值;**Claude Max 訂閱不含 API 用量**。請設定每月用量上限 |

## 1. 取得程式

1. 在 GitHub 把本 repo 下載成 ZIP,解壓縮。
2. 用 File Station 把 `stock-count` 資料夾上傳到 NAS,例如 `/docker/stock-count`。

## 2. 設定金鑰與密碼

1. 在你的電腦上,把 `nas/.env.example` 複製為 `nas/.env`,用文字編輯器填入:
   - `ANTHROPIC_API_KEY=`:你的金鑰
   - `APP_PASSCODE=`:自訂的長隨機字串(手機網頁要填同一組)
2. 把 `.env` 上傳到 NAS 的 `/docker/stock-count/nas/`。
3. 在 File Station 對該檔按右鍵 → 內容 → 權限,只留管理員可讀。
4. **`.env` 不要提交到 Git,也不要傳到雲端硬碟或聊天軟體。**

沒有設 `APP_PASSCODE` 或 `ANTHROPIC_API_KEY` 時,容器會拒絕啟動並在日誌說明原因。

## 3. 建置與啟動

1. Container Manager → **專案** → 建立。
2. 專案名稱 `stock-count`,路徑選 `/docker/stock-count/nas`,來源選「使用現有的 docker-compose.yml」。
3. 按下一步直到完成,讓它建置並啟動(第一次需要下載 Deno 映像與依賴,要等幾分鐘)。
4. 到「容器」頁確認 `stock-count` 狀態為「運行中」且健康檢查通過;開啟日誌應看到「stock-count 伺服器啟動」。

## 4. 先在區網測試

預設只綁定 NAS 本機(`127.0.0.1:8787`),給後面的反向代理使用。要先在區網測試,暫時把 `docker-compose.yml` 的埠改成 `"8787:8787"`,重新啟動專案,手機連上同一個 Wi-Fi,開 `http://NAS內網IP:8787/`。

**HTTP 下的限制**:
- ✅ 「拍照 / 選照片」(呼叫手機的相機 App)、手動輸入、ERP 匯入、差異報表、拍照辨識都能用。
- ❌ **即時條碼掃描需要 HTTPS**(瀏覽器的相機安全限制),HTTP 下無法開啟。
- ❌ 離線快取(service worker)也需要 HTTPS。

測完請把埠改回 `127.0.0.1:8787`。

## 5. 對外提供 HTTPS(三選一)

即時掃描與離線都需要 HTTPS。

### A. Synology 內建:DDNS + 憑證 + 反向代理(不需自備網域)

1. 控制台 → 外部存取 → DDNS:新增 Synology 提供的網域(例如 `你的名稱.synology.me`)。
2. 控制台 → 安全性 → 憑證:新增 Let's Encrypt 憑證。申請時通常需要外部能連到 80 埠,詳見 Synology 官方說明。
3. 控制台 → 登入入口 → 進階 → 反向代理伺服器:新增規則
   - 來源:HTTPS、主機名稱 `你的名稱.synology.me`、連接埠 443
   - 目的地:HTTP、`localhost`、連接埠 `8787`
4. 路由器把 443 轉送到 NAS。這會讓這台 NAS 的 443 對外可達,請務必做完下面「安全」。
5. 在 `docker-compose.yml` 確認 `TRUST_PROXY: "1"`(已預設),讓流量限制看得到真實來源。

### B. Cloudflare Tunnel(不用開路由器埠;需有網域)

在 NAS 另外跑 `cloudflared` 容器,把你的網域導向 `http://stock-count:8787`。公網上看得到網址,所以密碼與流量限制更重要。

### C. Tailscale(最封閉;每支手機要安裝並登入)

NAS 與手機加入同一個 Tailscale 網路,用內部名稱連線,不對公網開放。

**建議**:只在倉庫內用且想最省事,選 A;不想開路由器埠且有網域,選 B;人少、手機可管理,選 C。

## 6. 安全(必做)

- **不要**把 DSM 管理介面(5000 / 5001)或 SSH 對外開放。只轉送給反向代理的 443。
- 控制台 → 安全性:啟用「自動封鎖」、啟用防火牆規則、管理員帳號開兩步驟驗證,停用預設的 `admin` 與 `guest`。
- 在 Anthropic Console 設定**每月用量上限**。
- 容器已設定唯讀檔案系統、移除額外權限、記憶體上限,以及每個來源每分鐘 12 次的辨識限制(可用 `RATE_PER_MIN` 調整)。
- 日誌只記錄時間、來源、路徑與狀態碼,**不記錄照片、提示內容、密碼或金鑰**。
- 定期更新 DSM、Container Manager 與本專案;Deno 版本固定在 `nas/Dockerfile`,升級前請先測。

## 7. 手機使用

1. 開啟網址 → 「商品與設定」:網頁會自動偵測並填入辨識 API 網址,你只要填 **存取密碼**(同 `APP_PASSCODE`)。
2. 匯入 ERP 的 CSV → 回到「盤點」開始。
3. 密碼錯誤時,拍照辨識會顯示「未授權」。

## 8. 日常維運

| 事項 | 做法 |
|---|---|
| 查看日誌 | Container Manager → 容器 → `stock-count` → 日誌 |
| 更新版本 | 下載新版 ZIP,覆蓋 `/docker/stock-count` 內的程式(**保留 `nas/.env`**),在專案頁按「重新建置」 |
| 更換密碼或金鑰 | 修改 `nas/.env`,重新啟動專案;手機網頁同步更新密碼 |
| 資料備份 | NAS 目前**不儲存盤點資料**(資料在各手機)。要備份就用差異報表匯出 CSV |
| 斷電 | 建議接 UPS。斷電後容器會自動重啟(`restart: unless-stopped`) |

## 9. 排錯

| 現象 | 可能原因 |
|---|---|
| 容器一直重啟,日誌說缺 `ANTHROPIC_API_KEY` 或 `APP_PASSCODE` | `.env` 沒放對位置或沒填 |
| 辨識失敗:未授權 | 手機填的密碼與 `.env` 不同 |
| 辨識失敗:HTTP 429 | 超過每分鐘限制,稍等再拍,或調高 `RATE_PER_MIN` |
| 辨識失敗:HTTP 502 | NAS 連不到 Anthropic、金鑰錯誤、額度用完,或模型被拒絕;看容器日誌 |
| 相機掃描打不開 | 不是 HTTPS(見第 5 節) |
| 外網打不開 | DDNS、憑證、反向代理或路由器轉送其中之一沒設好 |

## 10. 開發者

- 辨識邏輯只在 `supabase/functions/_shared/count.ts` 修改,Supabase 與 NAS 共用。
- 測試:`DENO=/路徑/deno node nas/test/server.test.js`;型別檢查:`deno check nas/server.ts supabase/functions/count-photo/index.ts`。

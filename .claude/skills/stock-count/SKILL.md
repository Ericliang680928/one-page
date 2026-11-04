---
name: stock-count
description: 倉庫盤點專案(stock-count/)的開發與維運知識。凡是修改拍照辨識計數、Edge Function count-photo、評測腳本 eval/run_eval.py、商品主檔格式,或討論 API 用量、token 成本、提示詞快取、降低成本時使用。
---

# 倉庫盤點專案 skill

## 專案是什麼

手機網頁(`stock-count/index.html`):掃條碼或拍照盤點包裝食品庫存,和 ERP 帳面比對只列差異。拍照辨識由 Supabase Edge Function `count-photo` 呼叫 Claude,金鑰只放後端。

| 路徑 | 用途 |
|---|---|
| `stock-count/index.html`、`sw.js` | 前端(單檔、可離線、資料存手機本機) |
| `stock-count/supabase/functions/count-photo/index.ts` | 辨識後端,含提示詞與快取設計 |
| `stock-count/eval/run_eval.py` | 準確度與成本評測(階段 0) |
| `stock-count/eval/README.md` | 主檔格式、人工計數格式、報告怎麼看 |

## 修改後端時必守的規則

1. **提示詞唯一來源是 `index.ts`**:`RULES_TEMPLATE`(含 `{{LIST}}` 佔位符)與 `USER_TEXT`。評測腳本用正規表示式讀這兩個常數,**改名或改格式會讓評測失效**。
2. **商品清單一律依條碼排序**後再放進 system。快取是前綴比對,順序不同就是不同前綴,不會命中。
3. **快取前綴內不得有會變動的內容**:時間、隨機值、請求編號、使用者名稱都不行。會變的東西(照片)只放在 `messages` 最後。
4. **`max_tokens` 不可設小**(現為 16000)。Sonnet 5.5 預設會思考,思考 token 也算在 `max_tokens` 內,設太小會把輸出截斷。程式已對 `max_tokens` 與 `refusal` 兩種停止原因回報錯誤。
5. 改完要確認 `usage.cache_read` 在連續請求中大於 0。若一直是 0,代表前綴被某處破壞(見上面第 2、3 點)。
6. Sonnet 5.5 的最小可快取前綴是 512 token(官方文件標註使用前需再確認)。商品清單很短時不會快取,但此時成本本來就低。

## 降低 token 用量的規劃

### 先搞清楚錢花在哪

以 300 個商品、Sonnet 5.5(輸入 $2 / 輸出 $10 每百萬 token,快取讀取為輸入的 0.1 倍,寫入 1.25 倍;價格為 2026-10 的參考值,以官方為準)估算,一次辨識:

| 項目 | token | 說明 |
|---|---:|---|
| 照片 | 約 2,000 | 長邊 1600px |
| 規則 + 商品清單 | 約 9,400 | 清單佔大宗,每個商品約 25 到 30 token |
| 輸出(含思考) | 約 2,000 | 思考 token 以輸出價計 |

| 情況 | 每張約 | 月 250 張 |
|---|---:|---:|
| 沒有快取 | $0.043 | 約 $10.8 |
| 有快取命中 | $0.026 | 約 $6.5 |

**結論:快取可省約 4 成;但輸出(含思考)已是最大單項。** 所以「控制思考深度」和「縮短清單」比快取更值得優先評測。

### 手段清單與狀態

| # | 手段 | 預期效果 | 狀態 |
|---|---|---|---|
| 1 | **提示詞快取**:規則與商品清單放 system 並標記 `cache_control`,照片放後面 | 重複呼叫時清單部分輸入成本降到 0.1 倍 | 已實作 |
| 2 | **思考深度 `effort`**:環境變數 `COUNT_EFFORT`,預設 `medium`;評測用 `--effort` 比較 low / medium / high | 輸出佔最大成本,降一級可明顯減少思考 token | 已實作,待實測決定 |
| 3 | **照片縮圖**:前端長邊 1600px;評測用 `--max-side` 試 1280 | 降低照片 token,需確認不影響準確度 | 前端已縮,待評測 |
| 4 | **用量可觀測**:函式回傳 `usage`(input / cache_write / cache_read / output),評測腳本彙整成命中率與成本 | 能用數據決策,及早發現快取失效 | 已實作 |
| 5 | **只送該區域的商品清單**:商品主檔加 `zone`(區域),盤點時選區位,只送該區商品 | 清單從 300 縮到幾十,輸入降一半以上 | 規劃中(階段 1);注意不同清單各自一份快取 |
| 6 | **模型取捨**:評測用 `--model` 比較 Sonnet 5.5 與更便宜的 Haiku 5.5;準確度不足再升級 | Haiku 單價遠低於 Sonnet | 待評測;換模型由使用者決定,快取以模型為單位 |
| 7 | **條碼優先**:有條碼的商品掃碼,不拍照 | 該部分用量為 0 | 已有掃碼功能,流程上要求優先掃碼 |
| 8 | **Message Batches**:不需即時結果的批次處理(例如評測、夜間補辨識)走批次,價格約 5 折 | 評測成本減半 | 尚未實作;互動盤點不適用,因需即時回應 |
| 9 | **快取 TTL**:預設 5 分鐘;盤點照片間隔較長時設 `COUNT_CACHE_TTL=1h`(寫入成本較高) | 避免盤點中途快取過期而重寫 | 已實作為選項 |
| 10 | **用量上限**:Anthropic 後台設每月上限 | 防止函式網址被濫用 | 需使用者在後台設定 |

### 決策順序

1. 先跑階段 0 評測,同一批照片比較 `--effort low|medium|high`,看準確度與成本。
2. 在達標的最低 effort 上,再試 `--max-side 1280`。
3. 準確度足夠但成本仍高,才評估換較便宜的模型(需使用者同意)。
4. 商品數多時,實作 #5(區域清單)。
5. 不要在沒有評測數據前就調低 effort 或縮圖,否則會用省下的錢換掉不知道有多大的準確度損失。

## 計費:訂閱與 API 是分開的

使用者目前訂閱 Claude Max 5x。該訂閱涵蓋 claude.ai 與 Claude Code 的使用,**不含 API 用量**。本專案的 Edge Function 與評測腳本 `--direct` 使用 API 金鑰呼叫,費用在 Anthropic Console 另外計費(預付額度或按量)。開發這個專案本身(與 Claude Code 對話)才會消耗訂閱額度。部署前請使用者到 Console 建立 API 金鑰、儲值並設定每月用量上限。

## 還沒做、需要提醒使用者的事

- 後端尚未部署,快取命中率與實際成本都**沒有用真實 API 驗證過**,目前只驗證過統計算式和程式語法。第一次實測後,請看評測報告的「快取命中率」與「平均每張」token。
- Claude Sonnet 5.5 官方建議在 Claude API 上加入「拒絕時自動改用其他模型」的伺服器端 fallback(beta)。盤點包裝食品被拒絕的機率很低,目前只在拒絕時回傳明確錯誤。若要加入,需先以真實 API 驗證參數格式。
- 函式網址預設需帶 Supabase anon key,但這把 key 會出現在手機網頁中。部署前請設定 Anthropic 用量上限,必要時加存取密碼。

## 評測指令速查

```bash
cd stock-count/eval
pip install anthropic pillow
export ANTHROPIC_API_KEY=...
python3 run_eval.py --direct --effort low
python3 run_eval.py --direct --effort medium
python3 run_eval.py --direct --effort high --out results_high
python3 run_eval.py --direct --max-side 1280
python3 run_eval.py --direct --model claude-haiku-5-5 --price-in 0.10 --price-out 0.50
```

報告最後的「Token 用量與成本」區段會給出平均每張 token、快取命中率與估算成本。

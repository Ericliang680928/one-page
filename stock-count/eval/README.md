# 階段 0:拍照辨識準確度評測

目的:在投入更多開發前,用**真實倉庫照片**量出 AI 計數到底準不準、錯在哪。

## 1. 準備資料

### 商品主檔 `products.csv`(格式見 `products.sample.csv`)

| 欄位 | 必填 | 說明 |
|---|---|---|
| `barcode` | ✓ | 條碼。**Excel 請把此欄設為「文字」**,否則會變成 `4.71E+12`、前導 0 遺失。 |
| `name` | ✓ | 品名,要含口味/規格,辨識會用到。 |
| `spec` | | 規格,例如 50g、600ml。 |
| `category` | | 分類。 |
| `units_per_case` | | 每箱入數。之後用來把「箱數」換算成件數。 |
| `units_per_layer` | | 疊放時每層件數。之後用來由層數推算。 |
| `look_alike_group` | | 容易混淆的群組代號(同群組填同一個字串),例如同系列不同口味。 |
| `ref_photo` | | 參考照片檔名,之後用來輔助比對外觀。 |
| `notes` | | 備註,例如擺放習慣。 |
| `zone` | | 預設存放區域。之後盤點時只送該區商品清單,可大幅降低 token。 |

目前評測只使用 `barcode` 與 `name`,其餘欄位為階段 1 到 2 預留,可以先填。

### 人工計數 `ground_truth.csv`(格式見 `ground_truth.sample.csv`)

每張照片、每種商品一列:`photo,barcode,count,note`。

- `photo` 是 `photos/` 資料夾內的檔名。
- 照片中沒有列出的商品,視為 0 件。
- **`count` 請填「肉眼從這個角度看得到的數量」**,不要把被遮住的估進去,否則是在量測不可能的事。被遮住的情況寫在 `note`。

### 照片 `photos/`

- 建議 30 張以上,涵蓋:整齊貨架、混放(兩三種)、疊放、光線差、角度偏、相似口味相鄰。
- 用平常盤點的拍法,不要為了測試刻意拍得很漂亮。

## 2. 執行

```bash
cd stock-count/eval
# 直接呼叫 Claude(部署前即可;需安裝 SDK 並設定金鑰)
pip install anthropic pillow
export ANTHROPIC_API_KEY=...
python3 run_eval.py --direct

# 或測已部署的函式
python3 run_eval.py --endpoint https://xxxx.supabase.co/functions/v1/count-photo --key <anon key>

# 比較思考深度、照片大小、模型(各跑一次,看報告最後的成本區段)
python3 run_eval.py --direct --effort low
python3 run_eval.py --direct --effort high --out results_high
python3 run_eval.py --direct --max-side 1280
python3 run_eval.py --direct --model claude-haiku-5-5 --price-in 0.10 --price-out 0.50

# 自我測試,不連網路
python3 run_eval.py --truth ground_truth.sample.csv --products products.sample.csv --mock
```

預設讀取 `photos/`、`ground_truth.csv`、`products.csv`。可用 `--photos`、`--truth`、`--products`、`--out` 指定其他位置。

## 3. 怎麼看報告

| 指標 | 意義 |
|---|---|
| 完全正確 / ±1 / 10% 以內 | 數量準確度。`--target` 預設要求「10% 以內」達 90%。 |
| 商品辨識召回 / 精確 | 有沒有認出該有的商品、有沒有亂報不存在的。 |
| 信心度可信嗎 | 高信心應明顯比低信心準。若沒有差別,「低信心才人工確認」的設計就不成立。 |
| 疑似混淆 | 同張照片一個商品少算、另一個多算,通常是相似口味被認錯。 |
| 幻覺 | 回報了主檔以外的條碼。 |
| Token 用量與成本 | 平均每張各類 token、快取命中率、估算成本。命中率偏低代表快取前綴被破壞。 |

明細輸出到 `results.csv`(每張照片×商品一列)與 `results_unknown.json`(對不上主檔的商品)。

## 4. 評測後怎麼決定

- 整齊貨架準確、混放不準:先改善混放(加參考照片、`look_alike_group`)。
- 信心度不可信:改成不依賴信心,而是用「與 ERP 帳面差異」決定要不要複點。
- 疊放或箱內普遍少算:導入 `units_per_case`、`units_per_layer` 換算。
- 整體不準:調整產品定位,以條碼為主、拍照為輔。

**真實照片與人工計數屬於公司資料,已由 `.gitignore` 排除,不會被提交。**

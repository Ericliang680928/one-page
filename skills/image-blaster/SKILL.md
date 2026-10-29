---
name: image-blaster
description: 用一張圖片生成 3D 世界（「圖轉 3D 場景」）。串接開源專案 neilsonnn/image-blaster：World Labs Marble 產生可漫遊的 3D 環境（Gaussian splat .spz）、FAL Hunyuan3D 產生物件 3D 模型（.glb/.obj）、ElevenLabs 產生環境音與物件音效（.mp3）。凡使用者提到 image-blaster、「把這張照片變 3D」「圖片轉 3D 模型/場景」「做一個可以逛的 3D 空間」「場地 3D 預覽」「blast it」，或想把照片、渲染圖、場勘照變成遊戲引擎/Blender/Three.js 可用的 3D 素材時，務必使用此 skill，即使沒有明講 image-blaster。
---

# image-blaster：一張圖 → 3D 世界

這個 skill 引導你在使用者**自己的電腦**上，用 Claude Code 跑開源專案
[neilsonnn/image-blaster](https://github.com/neilsonnn/image-blaster)（MIT 授權）。
專案本身就是一組 Claude Code 技能，真正的工作是在它的資料夾裡執行 `claude` 後完成的；
這個 skill 負責：準備環境、管好 API 金鑰、帶使用者一步步跑完、交代產出怎麼用。

使用者以繁體中文溝通，回覆一律用繁體中文、口語、少術語；遇到術語（splat、PBR、面數）順手一句話解釋。

## 會產出什麼

| 產出 | 格式 | 用到的模型 |
|---|---|---|
| 靜態環境（可漫遊的場景） | `.spz` Gaussian splat | World Labs `marble-1.1` |
| 動態物件的 3D 模型 | `.glb`、`.obj` | FAL `hunyuan-3d` |
| 去背、乾淨背景、物件參考圖 | 圖片 | `nano-banana`（預設）或 `gpt-image-2` |
| 環境循環音、物件碰撞音效 | `.mp3` | `elevenlabs-sfx` |

## 需要的東西（先確認，缺了就先補）

1. **在使用者自己的電腦上執行**（Mac / Linux / Windows WSL）。雲端沙盒通常連不到 World Labs、FAL，
   產出的 3D 檔也得用本機軟體開，所以若目前環境是雲端 session，先說明並建議改在本機做。
2. **Claude Code**：`curl -fsSL https://claude.ai/install.sh | bash`
3. **git**，以及要看內建 3D 檢視器時需要 **Bun**（`curl -fsSL https://bun.sh/install | bash`）。
4. **兩把 API 金鑰**（專案的 `.env.example` 只要這兩把）：
   - `WORLD_LABS_API_KEY` — 到 https://platform.worldlabs.ai/ 申請
   - `FAL_KEY` — 到 https://fal.ai/ 申請（Hunyuan3D 等模型走 FAL）
   兩家都是按用量計費，先提醒使用者：每生成一次都會花錢，第一次建議先拿一張圖小量試跑。

### 金鑰的處理原則
金鑰等同信用卡。讓使用者**自己**把金鑰寫進專案根目錄的 `.env`（照 `.env.example` 的格式），
不要請他們把金鑰貼進對話、不要寫進任何會 commit 的檔案、不要印出來。`.env` 已在 `.gitignore` 裡，複製後可再確認一次。

```bash
cp .env.example .env
# 用編輯器打開 .env，把兩行換成自己的金鑰
```

## 執行流程

1. **取得專案**（已經有就 `git pull` 更新）
   ```bash
   git clone https://github.com/neilsonnn/image-blaster
   cd image-blaster
   ```
2. **設定金鑰**：照上面的方式建立 `.env`。
3. **放圖片**：把一張圖放進 `input/` 資料夾。
   選圖建議：構圖清楚、光線均勻、主體不要被裁切；室內場景、街景、建築渲染圖效果較好。
   人像特寫、純文字海報、很糊的照片效果差，先提醒。
4. **啟動並發動**：在專案資料夾執行 `claude`，然後說：
   > blast it and confirm each step with me

   「confirm each step」很重要——每個步驟都會先停下來給使用者確認，避免一路燒錢。
   專案自帶的技能（在 `.claude/` 裡）會接手後續：清理圖片 → 拆出動態物件 → 生成環境 splat → 生成物件模型 → 生成音效。
5. **看結果**：產出放在 `worlds/` 底下。要用內建檢視器看：
   ```bash
   bun install
   bun run dev
   ```
   再用瀏覽器打開終端機顯示的網址。

## 可調參數（物件模型，Hunyuan3D）

使用者想要更細或更輕的模型時，告訴 Claude 帶這些參數：

- `--face-count 40000~1500000`：面數，越多越細也越大。預設 50000（網頁用夠了）；要印刷/特寫可拉到 300000 以上。
- `--enable-pbr true|false`：是否產生寫實材質（PBR）。預設 true。
- `--generate-type Normal|LowPoly|Geometry`：一般有貼圖 / 低面數 / 只有白模。預設 Normal。
- `--polygon-type triangle|quadrilateral`：LowPoly 時用三角面或四邊面。預設 triangle。

## 產出怎麼用

- **Blender / Maya / 3ds Max**：直接匯入 `.glb` 或 `.obj`。`.spz` 需要 Gaussian splat 外掛。
- **Unity / Unreal / Godot**：`.glb` 直接拖進專案；splat 需裝對應的 Gaussian splatting 外掛。
- **網頁（Three.js）**：`.glb` 用 `GLTFLoader` 載入；`.spz` 可用 Spark 等 splat 渲染器。
  若要嵌進使用者既有的網站（例如 one-page），把 `.glb` 放進網站資料夾，再用 `<model-viewer>` 或 Three.js 顯示，
  注意檔案大小（建議單檔 < 10MB，太大就降 `--face-count`）。

## 常見問題

- **401 / 金鑰錯誤**：檢查 `.env` 變數名稱是否正確（`WORLD_LABS_API_KEY`、`FAL_KEY`），前後不要有空白或引號。
- **額度不足 / 402**：到該平台儲值。
- **Claude 找不到專案技能**：確認是在 `image-blaster` 資料夾裡執行 `claude`，不是在上一層。
- **想改檢視器**：把 `/app` 從 `.claudeignore` 拿掉，Claude 才能修改 React 檢視器。

## 合理期待
這是把幾個現成 AI 服務串起來的自動化流程，適合做概念、場地預覽、遊戲關卡草稿、快速起稿，
不是取代專業建模。結果品質取決於原圖，物件細節常需要再進 Blender 修。跟使用者講清楚，避免期待落差。

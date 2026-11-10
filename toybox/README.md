# H3 Toybox

一個接在 ComfyUI 後面的 **即夢（Dreamina）風格** MiniMax H3 多模態暗色創作工作台。支援電腦與手機端響應式佈局，集結 **AI 影片**、**AI 影像** 與 **AI 代理（自動短片）** 於同一個沉浸式介面。

啟動：`start_h3toybox.bat`（需要 ComfyUI 已在 8188 執行），開 http://127.0.0.1:8190

## 介面與操作流程（即夢 Dreamina 風格）

- **導覽列與可收合素材庫**：
  - 電腦端為左側固定導覽列（`創作`／`素材庫`／`作品`／`設定`），點擊「素材庫」或頂部按鈕可展開／收合左側素材庫抽屜。
  - 手機端（`<= 768px`）自動轉為底部固定毛玻璃導覽列，素材庫與設定彈窗自動轉為底部滑出抽屜（Bottom Sheet），且素材卡片直接顯示操作按鈕，無需滑鼠懸停。
- **核心創作卡片（Hero Composer）**：
  - 頂部資料夾式分頁切換：**AI 代理**、**AI 影像**、**AI 影片**（以及 CreativeBox 環境下的 `📁 專案`）。
  - 左側傾斜 **「＋ 參考」** 卡片：點擊可選擇「📤 上傳本機檔案」（上傳後自動掛入參考區）或「📁 從素材庫選擇」。
  - 提示詞編輯器：支援輸入 `@` 或點擊底部 `@` 按鈕喚出素材自動補全選單，並對 `@圖片1`、`@影片1`、`@音訊1` 進行多色語法高亮。
  - 底部參數膠囊列：集中控制模型與快速模式（`Minimax H3`）、生成模式（`全方位參考`／`首尾幀`／`純文字`）、比例與解析度（`自動 · 720P`）、秒數（`10s`）、導演與運鏡參數（動作強度／導演 Skill／Seed）、`✨ 改寫` 開關、`改寫預覽` 與圓形發送鍵 `↑`。

## 🎬 AI 影片：三種生成模式

| 模式 | H3 任務 | 條件節點 | 改寫用的指南 |
|---|---|---|---|
| ✨ 全方位參考 | Ref2VA | `MiniMaxH3ReferenceToVideo`（影像 9／影片 3／音訊 3） | `guides/..._ref_en.md` |
| 🎞 首尾幀 | I2VA／FL2VA／L2VA（依放入的首幀／尾幀決定） | `MiniMaxH3ImageToVideo` | `guides/..._base_en.md` |
| ✏️ 純文字 | T2VA | `MiniMaxH3ImageToVideo`（不接圖） | `guides/..._base_en.md` |

- 改寫模型是 Ref2VA 訓練的，base 任務它常回六段式；`normalize_base()` 會把它收回三段式，並重建第一行的對齊句（那一行是固定格式）。
- 指南來源：<https://huggingface.co/MiniMaxAI/MiniMax-H3/tree/main/docs>

### 🧵 延伸影片（全方位參考模式）

參考區的影片標籤可以勾選「🧵 延伸」，這時它不是參考影片，而是要被接下去的原片：

1. ffmpeg 把原片最後 3 秒轉成 24fps、生成尺寸（`h3t_tail_*.mp4`）
2. `MiniMaxH3MotionContext`（NikoDemon80/ComfyUI-H3-Motion-Context）把尾巴 22 格釘進新片段開頭（氣鎖），接在語意橋之後、採樣器之前
3. 原片最後一格同時當成一張參考圖，告訴改寫模型「從這一格接下去」
4. 解碼後 `MiniMaxH3MotionContextTrim` 剪掉那 22 格，再用 ffmpeg 把原片接上新片段 → `outputs/<job>_full.mp4`

SelfLift 採樣器會把 `minimax_keyframes` 縮到低解析度階段，所以首尾幀與氣鎖都能直接接兩段採樣。

## 🖼 AI 影像：文生圖與多圖參考編輯

支援兩種後端引擎（由 `config.json` 的 `image.engine` 控制）：

- **Colab 預設：`P-H3-Image`（`engine: "ph3"`）**
  - 直接復用已載入的 `Minimax-h3_Singularity_x_10Eros-Max_PRISM_int8` 基底模型並掛載 [PrunaAI/P-H3-Image-Comfy](https://huggingface.co/PrunaAI/P-H3-Image-Comfy) LoRA（`p-h3-image_lora_bf16.safetensors`），只需額外約 10 GB 模型空間。
  - 8 步 Euler 採樣，支援文生圖（T2I）與最多 **8 張參考圖** 編輯（第 1 張為被修改底圖，提示詞語法 `<Picture N>`）。
- **本機桌面端預設：`Qwen-Image 2.1`（`engine: "qwen"`）**
  - 使用 `qwen_image_2.1_int8_convrot.safetensors` ＋ `qwen3vl_8b_int8_convrot.safetensors`，25 步採樣，支援文生圖與最多 **9 張參考圖** 編輯（語法 `<image1>`, `<image2>`…）。
- 生成完成的影像在下方「生成作品」卡片中可一鍵點選 **「🎬 轉成影片」**，自動收入素材庫並切回 AI 影片分頁作為參考圖。

## 🤖 AI 代理：一句話自動規劃短片

- 放入角色／場景參考圖並輸入一句故事構想，設定總秒數（10s～60s），AI 代理會自動拆解為多段分鏡劇本，依序呼叫提示詞改寫、H3 影片生成與延伸拼接，最後合成完整短片。
- 支援在分鏡面板中輸入修改指示，單獨重拍指定段落並重新拼接。

## 工作流改動（相對 WF.json）

- 拿掉只為預覽存在的 `MiniMaxChunkFeedForward`(401) 與 `ModelPreviewOverrideKJ`(329)，SigmaShift(333) 直接接 scheduler／sampler
- `VHS_VideoCombine` 換成內建 `CreateVideo` ＋ `SaveVideo`（存到 `output/H3Toybox/`）
- 需要的外掛：T8 audio（Semantic Bridge）、SelfLift、H3 Motion Context

## 進度與剩餘時間

伺服器用標準庫自己連 ComfyUI 的 websocket（不需要 websocket-client），送件時帶同一個 client_id，就收得到那個 prompt 的 executing／progress 事件。

- 階段：排隊 → 準備 → 低解析度採樣 → 高解析度採樣 → 解碼存檔 →（延伸）接回原片
- 預估：採樣時用「已跑完的步」的中位數秒數外推；高解析度步還沒開始時，用歷史的高／低步數比換算
- 每次跑完把各階段時間（依像素×幀數正規化）以移動平均寫進 `stats.json`，越用越準

## 提示詞改寫的三種來源（設定頁）

| 來源 | 說明 |
|---|---|
| 🧠 本地 9B 模型（預設） | H3 官方 9B 改寫模型的 W4A8 量化版，在 ComfyUI 的 `TextGenerate` 裡跑 |
| 💎 Google Gemini | 填 API 金鑰；模型預設 `gemini-3.8-flash`，清單另有 `gemini-3.5-flash-lite`，也可以自己輸入；按「驗證」會列出這把金鑰可用的模型 |
| 🔌 OpenAI 相容端點 | 端點網址、金鑰（本機伺服器可留空）、模型；「驗證」送一個極短的 POST 確認端點活著，順便讀 `/models` |

- 三種來源吃的是同一份訊息（`rewrite_message()`：system＝官方指南，user＝文字與圖片交錯）；base 任務的回覆一律經過 `normalize_base()`。
- 金鑰只存在伺服器的 `secrets.json`，**不會傳回瀏覽器**（只回 `has_key`）；表單留空＝沿用、按「清除」才會刪。

## ⚡ 快速模式（預設開）

SelfLift 兩段採樣從 12 步（低 6＋高 6）降到 8 步（低 4＋高 4）。步數設定在 `config.json` 的 `steps`。

## 📁 素材庫資料夾

素材可以拖進資料夾整理；拖到「全部素材」麵包屑就移出來。資料夾只有一層，存在 `library/folders.json`，素材的 `folder` 欄位指向它。刪資料夾不會刪素材，素材會回到最外層。在資料夾裡上傳，或從生成作品點「＋收入素材庫」，都會直接放進目前的資料夾。

## 💾 資料放在哪裡

使用者做出來的東西都放在 `H3TOYBOX_DATA`（沒設就是程式資料夾本身）：`library/`（含 `folders.json`）、`outputs/`、`jobs.json`、`stats.json`、`config.json`。
API 金鑰另外存在 `H3TOYBOX_SECRETS`（預設 `DATA/secrets.json`），不寫進 `config.json`。
啟動時 `restore_inputs()` 會把素材庫裡、但 ComfyUI input 資料夾缺的檔案補回去（新的執行階段 input 是空的）。

Colab 版 ③ 預設 `SAVE_TO_DRIVE`：DATA＝「我的雲端硬碟/H3Toybox」，ComfyUI 的輸出目錄直接設在 `DATA/output`；
`SAVE_API_KEYS` 預設不勾，金鑰只放在執行階段的暫存空間。

## 已知

- 延伸接點後約第 2 格有一格幾乎重複（幀差 1.5，正常約 19），播放時可能有 1/24 秒的頓點，尚未查明是哪一側。

## Colab

repo 根目錄的 `H3_Toybox.ipynb`：① 安裝 → ② 下載約 50 GB（加生圖約 60 GB） → ③ 啟動並用 Colab 內建代理開網頁。
Colab 版的 `config.json` 由 ③ 寫入（`free_after_rewrite: true`：改寫完就卸載 9B 模型，避免和 H3 模型同時佔記憶體）。

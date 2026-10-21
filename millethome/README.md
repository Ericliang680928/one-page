# 米樂家 滿福果仁辣醬 一頁式購物網

- index.html　前台：一頁式商品頁＋購物車＋下單
- admin.html　後台：訂單（狀態、備註、搜尋、匯出 CSV）、商品上架／下架／售完／活動、運費與公告設定、更改通關碼
- config.js　資料庫連線（Supabase 專案 imc-miaoli-bot，mf_ 開頭的資料表）
- setup.sql　完整資料庫建置檔（備查；2026-10-21 已執行大部分）
- setup_remaining.sql　**還沒跑的部分**：設定後台通關碼、刪除函式、保險用 revoke、清測試單
- assets/img　logo 與商品照片

## 資料庫狀態（2026-10-21）
已建好：mf_products（已有滿福果仁辣醬一筆）、mf_orders、mf_settings、mf_admin 四張表＋RLS，
下單函式 mf_place_order，後台登入／看訂單／改狀態／商品上下架／設定／改通關碼函式。
已用匿名身分驗證：看得到上架商品與設定、看不到訂單與通關碼、可成功下單（留了一筆測試單 MF261021-0001）。

## 上線前
1. Supabase → imc-miaoli-bot → SQL Editor 執行 `setup_remaining.sql`（至少要跑第 ① 段設定通關碼，否則後台登不進去）。
2. 後台 →「設定」確認運費（暫定 NT$100、滿 NT$1,000 免運）、顧客聯絡方式、門市。
3. 把整個資料夾放到任一靜態網站空間（GitHub Pages、Netlify 等）；電腦直接雙擊 index.html 也能預覽與下單。

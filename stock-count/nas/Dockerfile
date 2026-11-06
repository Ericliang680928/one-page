# 在 Synology DS218+(x86-64)上建置與執行。建置時需要連網下載 Deno 與依賴。
# Deno 版本固定為測試過的版本;要升級請同步測試。
FROM denoland/deno:2.9.6

WORKDIR /app
# 只複製必要檔案:程式、共用模組、網頁與 service worker
COPY nas/server.ts ./nas/server.ts
COPY supabase/functions/_shared/count.ts ./supabase/functions/_shared/count.ts
COPY index.html sw.js ./web/

ENV WEB_DIR=/app/web PORT=8787
# 建置時就把依賴下載好,啟動時不需要再連到 npm
RUN deno cache nas/server.ts

USER deno
EXPOSE 8787
# 權限最小化:只允許網路、讀取環境變數與網頁目錄
CMD ["deno", "run", "--allow-net", "--allow-env", "--allow-read=/app/web", "nas/server.ts"]

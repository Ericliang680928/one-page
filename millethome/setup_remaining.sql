-- 米樂家 滿福果仁辣醬 購物網 — 資料庫「剩餘步驟」
-- 2026-10-21 資料表、下單函式、後台讀寫函式都已在 imc-miaoli-bot 專案建好，
-- 只剩下面幾段含 delete / revoke 的指令沒跑成（工具端會把它們當成破壞性操作擋下）。
-- 請到 Supabase → imc-miaoli-bot → SQL Editor，整份貼上執行。

-- ① 設定後台通關碼（必做）：把「請改成你的通關碼」換掉（至少 6 碼）
insert into public.mf_admin(id,code_hash)
values (1, extensions.crypt('請改成你的通關碼', extensions.gen_salt('bf')))
on conflict (id) do update set code_hash = excluded.code_hash;

-- ② 後台「刪除訂單／刪除商品」按鈕用的函式（不跑也沒關係，訂單可改成「已取消」、商品可「下架」）
create or replace function public.mf_admin_delete_order(code text, oid bigint) returns void
language plpgsql security definer set search_path=public, extensions as $$
begin perform mf_chk(code); delete from mf_orders where id=oid; end $$;
create or replace function public.mf_admin_delete_product(code text, pid uuid) returns void
language plpgsql security definer set search_path=public, extensions as $$
begin perform mf_chk(code); delete from mf_products where id=pid; end $$;
grant execute on function public.mf_admin_delete_order(text,bigint), public.mf_admin_delete_product(text,uuid) to anon, authenticated;

-- ③ 加一層保險（RLS 已經擋住匿名讀寫，這段只是多一道鎖）
revoke all on function public.mf_chk(text) from public, anon, authenticated;
revoke all on public.mf_orders, public.mf_admin from anon, authenticated;
revoke insert, update, delete on public.mf_products, public.mf_settings from anon, authenticated;

-- ④ 清掉我建表後驗證用的測試訂單
delete from public.mf_orders where order_no='MF261021-0001' and name='測試單';

-- 米樂家 滿福果仁辣醬 購物網 — 資料庫建置（Supabase / Postgres）
-- 執行位置：Supabase 專案 imc-miaoli-bot → SQL Editor，整份貼上執行一次即可（可重複執行，不會重複建表）。
-- 只新增 mf_ 開頭的資料表與函式，不會動到 IMC 既有的 h15_ 資料表。
--
-- 安全設計：
--  * 顧客只能「讀上架商品／設定」與「呼叫 mf_place_order 下單」，無法直接讀寫訂單表。
--  * 金額由資料庫依商品價格重新計算，前端傳的價格不算數。
--  * 後台所有動作都要通關碼（以 bcrypt 雜湊存在 mf_admin，網頁裡不含通關碼）。

create extension if not exists pgcrypto with schema extensions;

create table if not exists public.mf_products(
  id uuid primary key default gen_random_uuid(),
  name text not null,
  subtitle text default '',
  price int not null check (price>=0),
  unit text not null default '罐',
  descr text default '',
  imgs text default '',              -- 圖檔路徑／網址，多張以「|」分隔
  on_shelf boolean not null default true,
  sold_out boolean not null default false,
  sort int not null default 0,
  promo_buy int not null default 0,  -- 活動：每買 promo_buy 件送 promo_free 件（0 ＝ 無活動）
  promo_free int not null default 0,
  created_at timestamptz not null default now()
);
create table if not exists public.mf_orders(
  id bigserial primary key,
  order_no text not null unique,
  name text not null,
  tel text not null,
  addr text default '',
  delivery text not null default '宅配',   -- 宅配，或取貨門市名稱
  pay text not null default '貨到付款',
  note text default '',
  items jsonb not null,
  subtotal int not null,
  ship int not null default 0,
  total int not null,
  status text not null default '新訂單',
  admin_note text default '',
  created_at timestamptz not null default now()
);
create table if not exists public.mf_settings(key text primary key, value jsonb not null);
create table if not exists public.mf_admin(id int primary key default 1 check(id=1), code_hash text not null);
create sequence if not exists public.mf_order_seq;

alter table public.mf_products enable row level security;
alter table public.mf_orders enable row level security;
alter table public.mf_settings enable row level security;
alter table public.mf_admin enable row level security;
drop policy if exists mf_products_public_read on public.mf_products;
drop policy if exists mf_settings_public_read on public.mf_settings;
create policy mf_products_public_read on public.mf_products for select to anon, authenticated using (on_shelf);
create policy mf_settings_public_read on public.mf_settings for select to anon, authenticated using (true);
revoke all on public.mf_orders, public.mf_admin from anon, authenticated;
revoke insert, update, delete on public.mf_products, public.mf_settings from anon, authenticated;

-- 預設值（運費與免運門檻為暫定，請到後台「設定」改成實際數字）
insert into public.mf_settings(key,value) values
 ('ship_fee','100'::jsonb),
 ('free_over','1000'::jsonb),
 ('stores','["苑裡野草","中苗門市","竹南金春發"]'::jsonb),
 ('contact','""'::jsonb),
 ('notice','""'::jsonb)
on conflict (key) do nothing;

create or replace function public.mf_chk(code text) returns void
language plpgsql security definer set search_path=public, extensions as $$
begin
  if code is null or not exists(select 1 from mf_admin where code_hash = crypt(code, code_hash)) then
    raise exception 'unauthorized' using errcode='28000';
  end if;
end $$;
revoke all on function public.mf_chk(text) from public, anon, authenticated;

create or replace function public.mf_place_order(p jsonb) returns jsonb
language plpgsql security definer set search_path=public, extensions as $$
declare
  it jsonb; pr mf_products; q int; fr int; sub int:=0; shp int:=0; fee int; fo int;
  out_items jsonb:='[]'::jsonb; no text; v_deliv text; v_pay text; tries int:=0; v_name text; v_tel text; v_addr text;
begin
  v_name:=btrim(coalesce(p->>'name','')); v_tel:=btrim(coalesce(p->>'tel','')); v_addr:=btrim(coalesce(p->>'addr',''));
  v_deliv:=coalesce(nullif(p->>'delivery',''),'宅配'); v_pay:=coalesce(nullif(p->>'pay',''),'貨到付款');
  if length(v_name) not between 1 and 40 then raise exception '請填姓名'; end if;
  if v_tel !~ '^[0-9+() -]{8,20}$' then raise exception '電話格式不正確'; end if;
  if v_deliv='宅配' and length(v_addr) not between 5 and 200 then raise exception '請填完整收件地址'; end if;
  if v_deliv<>'宅配' and v_deliv not in (select jsonb_array_elements_text(value) from mf_settings where key='stores') then raise exception '取貨門市不正確'; end if;
  if v_pay not in ('貨到付款','轉帳匯款','門市付款') then raise exception '付款方式不正確'; end if;
  if jsonb_typeof(p->'items')<>'array' or jsonb_array_length(p->'items')=0 or jsonb_array_length(p->'items')>20 then raise exception '購物車是空的'; end if;
  for it in select * from jsonb_array_elements(p->'items') loop
    select * into pr from mf_products where id=(it->>'id')::uuid and on_shelf and not sold_out;
    if not found then raise exception '商品已下架或售完，請重新整理頁面'; end if;
    q:=greatest(1,least(99,coalesce((it->>'qty')::int,1)));
    fr:=case when pr.promo_buy>0 and pr.promo_free>0 then (q/pr.promo_buy)*pr.promo_free else 0 end;
    sub:=sub+pr.price*q;
    out_items:=out_items||jsonb_build_object('product_id',pr.id,'name',pr.name,'price',pr.price,'unit',pr.unit,'qty',q,'free',fr);
  end loop;
  select (value#>>'{}')::int into fee from mf_settings where key='ship_fee';
  select (value#>>'{}')::int into fo from mf_settings where key='free_over';
  if v_deliv='宅配' then shp:=case when sub>=coalesce(fo,0) and coalesce(fo,0)>0 then 0 else coalesce(fee,0) end; end if;
  loop
    no:='MF'||to_char(now() at time zone 'Asia/Taipei','YYMMDD')||'-'||lpad((nextval('mf_order_seq')%10000)::text,4,'0');
    begin
      insert into mf_orders(order_no,name,tel,addr,delivery,pay,note,items,subtotal,ship,total)
      values(no,v_name,v_tel,case when v_deliv='宅配' then v_addr else '' end,v_deliv,v_pay,left(coalesce(p->>'note',''),300),out_items,sub,shp,sub+shp);
      exit;
    exception when unique_violation then tries:=tries+1; if tries>5 then raise; end if;
    end;
  end loop;
  return jsonb_build_object('order_no',no,'subtotal',sub,'ship',shp,'total',sub+shp);
end $$;
grant execute on function public.mf_place_order(jsonb) to anon, authenticated;

create or replace function public.mf_admin_login(code text) returns boolean
language plpgsql security definer set search_path=public, extensions as $$
begin
  return code is not null and exists(select 1 from mf_admin where code_hash = crypt(code, code_hash));
end $$;
create or replace function public.mf_admin_orders(code text) returns setof public.mf_orders
language plpgsql security definer set search_path=public, extensions as $$
begin perform mf_chk(code); return query select * from mf_orders order by created_at desc limit 2000; end $$;
create or replace function public.mf_admin_set_status(code text, oid bigint, st text, anote text default null) returns void
language plpgsql security definer set search_path=public, extensions as $$
begin perform mf_chk(code);
  if st not in ('新訂單','已確認','已出貨','已完成','已取消') then raise exception 'bad status'; end if;
  update mf_orders set status=st, admin_note=coalesce(anote,admin_note) where id=oid; end $$;
create or replace function public.mf_admin_delete_order(code text, oid bigint) returns void
language plpgsql security definer set search_path=public, extensions as $$
begin perform mf_chk(code); delete from mf_orders where id=oid; end $$;
create or replace function public.mf_admin_products(code text) returns setof public.mf_products
language plpgsql security definer set search_path=public, extensions as $$
begin perform mf_chk(code); return query select * from mf_products order by sort, created_at; end $$;
create or replace function public.mf_admin_save_product(code text, p jsonb) returns uuid
language plpgsql security definer set search_path=public, extensions as $$
declare v_id uuid;
begin perform mf_chk(code);
  if length(btrim(coalesce(p->>'name','')))=0 then raise exception '請填商品名稱'; end if;
  if nullif(p->>'id','') is null then
    insert into mf_products(name,subtitle,price,unit,descr,imgs,on_shelf,sold_out,sort,promo_buy,promo_free)
    values(btrim(p->>'name'),coalesce(p->>'subtitle',''),greatest(0,coalesce((p->>'price')::int,0)),coalesce(nullif(p->>'unit',''),'罐'),coalesce(p->>'descr',''),coalesce(p->>'imgs',''),coalesce((p->>'on_shelf')::boolean,true),coalesce((p->>'sold_out')::boolean,false),coalesce((p->>'sort')::int,0),greatest(0,coalesce((p->>'promo_buy')::int,0)),greatest(0,coalesce((p->>'promo_free')::int,0)))
    returning id into v_id;
  else
    update mf_products set name=btrim(p->>'name'),subtitle=coalesce(p->>'subtitle',''),price=greatest(0,coalesce((p->>'price')::int,0)),unit=coalesce(nullif(p->>'unit',''),'罐'),descr=coalesce(p->>'descr',''),imgs=coalesce(p->>'imgs',''),on_shelf=coalesce((p->>'on_shelf')::boolean,true),sold_out=coalesce((p->>'sold_out')::boolean,false),sort=coalesce((p->>'sort')::int,0),promo_buy=greatest(0,coalesce((p->>'promo_buy')::int,0)),promo_free=greatest(0,coalesce((p->>'promo_free')::int,0))
    where id=(p->>'id')::uuid returning id into v_id;
  end if;
  return v_id; end $$;
create or replace function public.mf_admin_delete_product(code text, pid uuid) returns void
language plpgsql security definer set search_path=public, extensions as $$
begin perform mf_chk(code); delete from mf_products where id=pid; end $$;
create or replace function public.mf_admin_save_settings(code text, s jsonb) returns void
language plpgsql security definer set search_path=public, extensions as $$
declare k text;
begin perform mf_chk(code);
  for k in select jsonb_object_keys(s) loop
    if k in ('ship_fee','free_over','stores','contact','notice') then
      insert into mf_settings(key,value) values(k,s->k) on conflict(key) do update set value=excluded.value;
    end if;
  end loop; end $$;
create or replace function public.mf_admin_change_code(code text, newcode text) returns void
language plpgsql security definer set search_path=public, extensions as $$
begin perform mf_chk(code);
  if length(coalesce(newcode,''))<6 then raise exception '新通關碼至少 6 碼'; end if;
  update mf_admin set code_hash=crypt(newcode, gen_salt('bf')) where id=1; end $$;
grant execute on function public.mf_admin_login(text), public.mf_admin_orders(text), public.mf_admin_set_status(text,bigint,text,text), public.mf_admin_delete_order(text,bigint), public.mf_admin_products(text), public.mf_admin_save_product(text,jsonb), public.mf_admin_delete_product(text,uuid), public.mf_admin_save_settings(text,jsonb), public.mf_admin_change_code(text,text) to anon, authenticated;

-- 第一個商品：滿福果仁辣醬（只在商品表是空的時候新增）
insert into public.mf_products(name,subtitle,price,unit,descr,imgs,sort)
select '滿福果仁辣醬','純素・香麻小辣・淨重 250 公克',350,'罐',
 E'純素、香麻小辣，淨重 250 公克。內含松子、腰果、杏仁、花生、白芝麻、葵瓜子，拌麵、拌飯、拌豆腐、涼青菜、火鍋沾醬都合適。\n過敏原：含堅果類、花生、芝麻、大豆、含麩質穀物，詳見瓶身標示。',
 'assets/img/jar-1.jpg|assets/img/jar-2.jpg|assets/img/jar-3.jpg|assets/img/jar-4.jpg|assets/img/team.jpg',1
where not exists(select 1 from public.mf_products);

-- ============ 最後一步：設定後台通關碼 ============
-- 把下面「請改成你的通關碼」換成你要的通關碼（至少 6 碼），單獨選取這一行執行；之後也能在後台「設定」更改。
-- insert into public.mf_admin(id,code_hash) values (1, extensions.crypt('請改成你的通關碼', extensions.gen_salt('bf'))) on conflict (id) do nothing;

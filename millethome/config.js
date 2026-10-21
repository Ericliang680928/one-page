/* 米樂家 滿福果仁辣醬 購物網 — 資料庫連線設定
   這裡放的是「公開用」的金鑰（publishable key），可以放在網頁裡；
   訂單與後台的實際保護在資料庫端（見 setup.sql：RLS 與通關碼驗證）。 */
window.MF_CONFIG = {
  URL: 'https://ahrgjhlfbrsfznvbokkf.supabase.co',
  KEY: 'sb_publishable_e_YOmzczThrtX4YD2yC77Q_zHcUJcpI'
};
window.mfRpc = async function (name, args) {
  const c = window.MF_CONFIG;
  const r = await fetch(c.URL + '/rest/v1/rpc/' + name, {
    method: 'POST',
    headers: { apikey: c.KEY, Authorization: 'Bearer ' + c.KEY, 'Content-Type': 'application/json' },
    body: JSON.stringify(args || {})
  });
  const t = await r.text(); let j = null;
  try { j = t ? JSON.parse(t) : null; } catch (e) { j = t; }
  if (!r.ok) { const e = new Error((j && j.message) || ('HTTP ' + r.status)); e.status = r.status; throw e; }
  return j;
};
window.mfGet = async function (path) {
  const c = window.MF_CONFIG;
  const r = await fetch(c.URL + '/rest/v1/' + path, { headers: { apikey: c.KEY, Authorization: 'Bearer ' + c.KEY } });
  if (!r.ok) throw new Error('HTTP ' + r.status);
  return r.json();
};

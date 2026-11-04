// 極簡離線快取:頁面載入過一次後,沒有網路也能開啟(拍照辨識仍需網路)。
const C='stock-count-v2';
self.addEventListener('install',e=>{e.waitUntil(caches.open(C).then(c=>c.addAll(['./','index.html'])));self.skipWaiting()});
self.addEventListener('activate',e=>{e.waitUntil(caches.keys().then(ks=>Promise.all(ks.filter(k=>k!==C).map(k=>caches.delete(k)))));self.clients.claim()});
self.addEventListener('fetch',e=>{
  if(e.request.method!=='GET'||new URL(e.request.url).origin!==location.origin)return;
  e.respondWith(fetch(e.request).then(r=>{
    if(r.ok){const cp=r.clone();caches.open(C).then(c=>c.put(e.request,cp))} // 只快取成功的回應,不快取 404
    return r;
  }).catch(()=>caches.match(e.request)));
});

// 即時點貨的端到端測試:假鏡頭(靜止影片)+ NAS 伺服器 + 模擬的 Claude。
// 用法:DENO=/路徑/deno node test/live.test.js(需要 Playwright 與 Chromium)
const http=require('http'),{spawn}=require('child_process');const {chromium}=require('playwright');
const path=require('path'),os=require('os'),fs=require('fs');
const S=fs.mkdtempSync(path.join(os.tmpdir(),'sc-live-'))+'/';
const DENO=process.env.DENO||'deno',ROOT=path.resolve(__dirname,'..');
// 產生一段靜止的測試影片(條紋畫面),模擬「手機拿穩」
{const w=640,h=480,y=Buffer.alloc(w*h);for(let r=0;r<h;r++)for(let c=0;c<w;c++)y[r*w+c]=((c/40|0)+(r/40|0))%2?220:40;
 const fr=Buffer.concat([Buffer.from('FRAME\n'),y,Buffer.alloc(w*h/2,128)]);
 fs.writeFileSync(S+'still.y4m',Buffer.concat([Buffer.from('YUV4MPEG2 W640 H480 F15:1 Ip A1:1 C420jpeg\n'),fr,fr,fr]))}
let fails=0;const ok=(c,m)=>{console.log((c?'PASS ':'FAIL ')+m);if(!c)fails++};
let lastReq=null,mode='pts';
const mock=http.createServer((req,res)=>{let b='';req.on('data',d=>b+=d);req.on('end',()=>{lastReq=JSON.parse(b);
 if(mode==='err'){res.writeHead(500,{'content-type':'application/json'});return res.end('{"type":"error","error":{"type":"api_error","message":"x"}}')}
 const items=mode==='pts'?[
  {code:'A1',name:'洋芋片',count:3,confidence:'high',note:'',points:[[100,100],[300,100],[500,100]]},
  {code:'B2',name:'海苔',count:2,confidence:'medium',note:'',points:[[100,600],[300,600],[9999,-5],["x",1]]},
  {code:null,name:'神祕餅乾',count:1,confidence:'low',note:'',points:[[800,800]]}]
  :[{code:'A1',name:'洋芋片',count:4,confidence:'high',note:''}];
 res.writeHead(200,{'content-type':'application/json'});
 res.end(JSON.stringify({id:'m',type:'message',role:'assistant',model:'x',stop_reason:'end_turn',stop_sequence:null,
  content:[{type:'text',text:JSON.stringify({items})}],usage:{input_tokens:1,cache_creation_input_tokens:0,cache_read_input_tokens:0,output_tokens:1}}))})});
const wait=ms=>new Promise(r=>setTimeout(r,ms));
(async()=>{await new Promise(r=>mock.listen(9911,'127.0.0.1',r));
 const env={...process.env,NO_PROXY:'127.0.0.1',ANTHROPIC_BASE_URL:'http://127.0.0.1:9911',ANTHROPIC_API_KEY:'k',APP_PASSCODE:'pw',PORT:'8794',WEB_DIR:ROOT,RATE_PER_MIN:'1000'};
 const sv=spawn(DENO,['run','--allow-net','--allow-env',`--allow-read=${ROOT}`,`${ROOT}/nas/server.ts`],{env,cwd:ROOT});
 for(let i=0;i<80;i++){try{if((await fetch('http://127.0.0.1:8794/api/healthz')).ok)break}catch(e){}await wait(500)}
 const b=await chromium.launch({args:['--use-fake-ui-for-media-stream','--use-fake-device-for-media-stream','--use-file-for-fake-video-capture='+S+'still.y4m']});
 const ctx=await b.newContext({permissions:['camera'],viewport:{width:420,height:900}});const p=await ctx.newPage();
 const errs=[];p.on('pageerror',e=>errs.push(e.message));
 await p.goto('http://127.0.0.1:8794/');
 await p.evaluate(()=>{localStorage.setItem('sc_master',JSON.stringify({A1:{name:'洋芋片 原味',erp:10},B2:{name:'洋芋片 海苔',erp:5},C3:{name:'礦泉水',erp:9}}));localStorage.setItem('sc_pass',JSON.stringify('pw'))});
 await p.reload();await p.waitForFunction(()=>localStorage.getItem('sc_api'));
 // 穩定度判斷(純函式)
 const st=await p.evaluate(()=>{const a=new Uint8Array(768).map((_,i)=>i%2?200:30),m=a.map(v=>255-v),dark=new Uint8Array(768).fill(3);
   return [steadyStep(null,a,300),steadyStep(a,a,300),steadyStep(a,m,300),steadyStep(dark,dark,300)]});
 ok(st[0]===0&&st[1]===450&&st[2]===0&&st[3]===0,'穩定度:第一格不算、靜止累積、晃動歸零、全黑(鏡頭被遮)不算 '+JSON.stringify(st));
 // 自動定格
 await p.click('#liveBtn');
 await p.waitForSelector('#liveLegend .item',{timeout:15000});
 ok(true,'鏡頭拿穩後自動定格並送出辨識(沒有按任何按鈕)');
 const uc=lastReq.messages[0].content;ok(uc[0].type==='image'&&uc[0].source.data.length>1000&&uc[1].text.includes('points')&&uc[1].text.includes('(1000,1000)'),'送給模型的請求含定格畫面與「標出每件位置」的指示');ok(!lastReq.system[0].text.includes('points'),'位置指示不在 system(不破壞提示詞快取)');
 const st1=await p.evaluate(()=>live.items.map(i=>[i.code,i.pts.length,i.base,i.orig]));
 ok(JSON.stringify(st1)==='[["A1",3,0,3],["B2",3,0,3],[null,1,0,1]]','結果:點數即數量;超出範圍的座標被夾回、格式錯誤的點被丟棄 '+JSON.stringify(st1));
 ok(await p.evaluate(()=>live.items[1].pts.every(q=>q[0]>=0&&q[0]<=1000&&q[1]>=0&&q[1]<=1000)),'座標都在 0–1000');
 // 畫面上真的畫了點
 const px=await p.evaluate(()=>{const c=document.querySelector('#lc'),d=c.getContext('2d').getImageData(Math.round(c.width*0.1),Math.round(c.height*0.1),1,1).data;return [d[0],d[1],d[2]]});
 ok(px[0]>200&&px[1]<60,'畫面上對應位置有紅色圓點 '+JSON.stringify(px));
 const tap=async(nx,ny)=>{await p.locator('#lc').scrollIntoViewIfNeeded();const bb=await p.locator('#lc').boundingBox();await p.mouse.click(bb.x+bb.width*nx,bb.y+bb.height*ny)};
 await tap(0.3,0.1);
 ok(await p.evaluate(()=>live.items[0].pts.length)===2,'點圓點 → 刪除一件(3→2)');
 await tap(0.7,0.3);await tap(0.8,0.3);
 ok(await p.evaluate(()=>live.items[0].pts.length)===4,'點空白 → 補到選取的商品(2→4)');
 await p.click('#liveLegend [data-li="1"]');await tap(0.5,0.6);
 ok(await p.evaluate(()=>live.sel===1&&live.items[1].pts.length===4),'選別的商品後,補點補到它身上');
 // 深度
 await p.fill('#liveLegend input[data-ld="0"]','3');await p.press('#liveLegend input[data-ld="0"]','Tab');
 ok((await p.innerText('#liveLegend [data-li="0"]')).includes('12'),'每點 ×3 → 4 點顯示 12 件');
 // 未指定商品擋下
 await p.click('#liveOk');
 ok((await p.innerText('#liveMsg')).includes('沒指定'),'有未指定商品時不能計入,並說明原因');
 await p.click('#liveLegend [data-li="2"]');await p.locator('#dlg .opt[data-c="C3"]').click();
 ok(await p.evaluate(()=>live.items[2].code)==='C3','點名稱指定商品');
 mode='nopts';await p.click('#liveOk');
 await p.waitForFunction(()=>!live||live.items.length===1);
 const c=await p.evaluate(()=>JSON.parse(localStorage.getItem('sc_counts')));
 ok(c.A1===12&&c.B2===4&&c.C3===1,'計入:A1=4點×3=12、B2=4、C3=1 '+JSON.stringify(c));
 let mem=await p.evaluate(()=>JSON.parse(localStorage.getItem('sc_mem')));
 ok(mem.sku.A1.n===1&&mem.sku.A1.sumErr===1&&mem.sku.A1.depth===3,'學習以可見件數為準(AI 3、實際 4 → +1),深度另外記住');
 ok(Object.values(mem.alias).includes('C3'),'記住別名');
 ok((await p.innerText('#progTxt')).includes('已盤 3 / 3'),'進度更新');
 // 自動回到取景並再次定格(下一格)
 await p.waitForFunction(()=>live&&live.items.length===1,null,{timeout:15000});
 ok(true,'計入後自動回到取景,下一格再次自動定格');
 const s2=await p.evaluate(()=>live.items.map(i=>[i.code,i.pts.length,i.base]));
 ok(JSON.stringify(s2)==='[["A1",0,4]]','AI 沒回傳位置時退回數字(4 件未標位置)');
 ok((await p.innerText('#liveLegend')).includes('12 件'),'深度設定已記住(4×3=12)');
 await p.click('#liveLegend button[data-lb="0"][data-d="-1"]');
 ok(await p.evaluate(()=>live.items[0].base)===3,'未標位置的數量可用 − + 調整');
 // 復原
 mode='pts';await p.click('#liveOk');await p.waitForFunction(()=>!live||live.items.length===3);
 ok(await p.evaluate(()=>JSON.parse(localStorage.getItem('sc_counts')).A1)===21,'第二格計入 3×3=9,累計 21');
 await p.click('#undoBtn');
 ok(await p.evaluate(()=>JSON.parse(localStorage.getItem('sc_counts')).A1)===12,'復原上一步 → 回到 12');
 // 全部刪掉 = 誤報,不計入
 await p.waitForFunction(()=>live&&live.items.length===3,null,{timeout:15000});
 await p.evaluate(()=>{live.items[1].pts=[];live.items=live.items.filter(i=>i.code);liveDraw()});
 const fp0=mem.sku.B2?.fp||0;mode='err';await p.click('#liveOk');await p.waitForFunction(()=>!live||live.items.length===0);
 mem=await p.evaluate(()=>JSON.parse(localStorage.getItem('sc_mem')));
 ok(mem.sku.B2.fp===fp0+1&&await p.evaluate(()=>JSON.parse(localStorage.getItem('sc_counts')).B2)===4,'把某商品的點全刪掉 → 記為誤報、不計入');
 // 辨識失敗仍可手動點
 await p.waitForFunction(()=>live&&live.items.length===0,null,{timeout:15000});
 ok((await p.innerText('#liveMsg')).includes('辨識失敗'),'辨識失敗時說明,並可手動');
 await p.click('#liveAdd');await p.locator('#dlg .opt[data-c="C3"]').click();
 await tap(0.5,0.5);await tap(0.6,0.5);
 ok(await p.evaluate(()=>live.items[0].code==='C3'&&live.items[0].pts.length===2),'失敗後手動選商品並點兩件');
 const n0=(await p.evaluate(()=>JSON.parse(localStorage.getItem('sc_mem')))).sku.C3.n;
 const c0=await p.evaluate(()=>JSON.parse(localStorage.getItem('sc_counts')).C3);
 await p.click('#liveOk');await p.waitForFunction(()=>!live||live.items.length===0);
 const m3=await p.evaluate(()=>JSON.parse(localStorage.getItem('sc_mem')));
 ok(m3.sku.C3.n===n0&&await p.evaluate(()=>JSON.parse(localStorage.getItem('sc_counts')).C3)===c0+2,'手動點的計入但不當作 AI 的學習紀錄');
 // 關閉鏡頭
 await p.click('#liveStop');
 ok(await p.evaluate(()=>!lstream&&!ltimer)&&await p.locator('#liveBtn').isVisible(),'關閉鏡頭後釋放相機');
 ok(errs.length===0,'無頁面錯誤 '+JSON.stringify(errs));
 await b.close();sv.kill();mock.close();console.log(fails?`\n${fails} 項失敗`:'\n全部通過');process.exit(fails?1:0)})();

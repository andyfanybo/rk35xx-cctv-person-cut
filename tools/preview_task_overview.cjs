const {chromium}=require('playwright');
const fs=require('node:fs');
const path=require('node:path');
const assert=require('node:assert/strict');

(async()=>{
  const browser=await chromium.launch({headless:true});
  const page=await browser.newPage({viewport:{width:1280,height:1000},deviceScaleFactor:1});
  const rows=Array.from({length:86},(_,i)=>({kind:i===85?'unanalyzed':i===40?'uncertain':i%2?'people':'no_detection',start:i*300,end:(i+1)*300,duration_seconds:300,gap_since_previous_people_seconds:i%2&&i>1?300:null}));
  const job={id:'demo',runtime:{state:'done',started_at:1700000000,finished_at:1700004337,elapsed_seconds:4337,estimated:false},statistics:{timelines:[{source:'demo.mp4',people_count:42,no_detection_count:42,longest_no_detection_seconds:300,intervals:rows}]}};
  const style=fs.readFileSync(path.join(__dirname,'../web/style.css'),'utf8');
  await page.setContent(`<meta name="viewport" content="width=device-width,initial-scale=1"><style>${style}</style><main style="max-width:960px;padding-top:30px"><p class="eyebrow">任务概览</p><h1 style="font-size:26px">全天录像 · 处理结果</h1><div id="preview"></div></main>`);
  await page.addScriptTag({path:path.join(__dirname,'../web/task-overview.js')});
  await page.evaluate(job=>{window.previewJob=job;document.querySelector('#preview').innerHTML=TaskOverview.timingHTML(job.runtime)+TaskOverview.timelineHTML(job);TaskOverview.bindTimelines(job);},job);
  assert.equal(await page.locator('tbody tr').count(),40);
  await page.getByRole('button',{name:'只看有人'}).click();
  assert.equal(await page.locator('tbody tr').count(),40);
  assert.equal(await page.locator('tbody .period-kind.people').count(),40);
  await page.getByRole('button',{name:'下一页'}).click();
  assert.equal(await page.locator('tbody tr').count(),2);
  await page.getByRole('button',{name:'全部',exact:true}).click();
  fs.mkdirSync('ui-preview',{recursive:true});
  await page.screenshot({path:'ui-preview/desktop.png',fullPage:false});
  await page.setViewportSize({width:390,height:844});
  assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth<=window.innerWidth),true);
  await page.screenshot({path:'ui-preview/mobile.png',fullPage:false});
  await browser.close();
  console.log('Browser checks passed: filters, pagination, text-only intervals, desktop and mobile.');
})();

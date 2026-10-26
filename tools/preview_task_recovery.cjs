const http=require('node:http');const fs=require('node:fs');const path=require('node:path');const assert=require('node:assert/strict');const {chromium}=require('playwright');
const root=path.join(__dirname,'../web');const calls=[];
const jobs=Array.from({length:60},(_,i)=>({id:(i+1).toString(16).padStart(32,'0'),title:i===0?'等待继续合并的任务':`录像任务 ${i+1}`,state:i===1?'running':'done',created:1700000000,output_mode:'automatic',verification:'quick',review_mode:'recommended',publish_user:true,plan:[],results:[],reviews:{},recommendations:{},exports:[],cache_cleanup:{},statistics:null,runtime:{state:'paused',elapsed_seconds:3600}}));
jobs[0].exports=[{id:'b'.repeat(32),state:'failed',message:'处理盘空间不足'}];
jobs[0].merge_recovery={can_retry:true,space_ready:true,disks:[{name:'处理盘',free_bytes:50*1073741824,required_bytes:8.5*1073741824,shortfall_bytes:0}]};
const server=http.createServer((req,res)=>{
  if(req.url.startsWith('/api/jobs')){
    res.setHeader('Content-Type','application/json');const match=req.url.match(/^\/api\/jobs\/([a-f0-9]{32})(?:\/(merge|pause|resume|delete))?$/);
    if(!match)return res.end(JSON.stringify(jobs));const job=jobs.find(j=>j.id===match[1]);
    if(req.method==='POST'){
      calls.push(match[2]);
      if(match[2]==='merge'){job.exports[0].state='queued';delete job.merge_recovery;}
      if(match[2]==='pause'){job.state='paused';job.runtime.state='paused';}
    }
    return res.end(JSON.stringify(job));
  }
  const name=req.url==='/'?'index.html':req.url.slice(1);const file=path.join(root,name);
  if(!file.startsWith(root)||!fs.existsSync(file)){res.writeHead(404);return res.end();}
  res.setHeader('Content-Type',name.endsWith('.js')?'application/javascript':name.endsWith('.css')?'text/css':'text/html');res.end(fs.readFileSync(file));
});
(async()=>{
  await new Promise(resolve=>server.listen(0,'127.0.0.1',resolve));
  const browser=await chromium.launch({headless:true});const page=await browser.newPage({viewport:{width:390,height:844},deviceScaleFactor:1});
  const errors=[];page.on('pageerror',e=>errors.push(String(e)));
  await page.goto('http://127.0.0.1:'+server.address().port);await page.getByRole('button',{name:'继续合并',exact:true}).waitFor();
  await page.waitForTimeout(700);
  assert.equal(await page.locator('#jobs').isVisible(),false);
  assert.ok((await page.locator('#detail').boundingBox()).y<170);
  const recovery=await page.locator('.merge-recovery').boundingBox();assert.ok(recovery.y<500);
  assert.doesNotMatch(await page.locator('.progress-panel').textContent(),/任务已完成/);
  fs.mkdirSync('ui-preview',{recursive:true});await page.screenshot({path:'ui-preview/mobile-task-recovery.png'});
  await page.getByRole('button',{name:'继续合并',exact:true}).click();await page.waitForTimeout(150);
  assert.deepEqual(calls,['merge']);
  await page.getByRole('button',{name:'切换任务',exact:true}).click();assert.equal(await page.locator('#jobs').isVisible(),true);
  await page.locator('[data-job="'+jobs[1].id+'"]').click();await page.getByRole('button',{name:'暂停任务',exact:true}).waitFor();
  await page.locator('#jobs').waitFor({state:'hidden'});
  assert.equal(await page.locator('#jobs').isVisible(),false);
  await page.getByRole('button',{name:'暂停任务',exact:true}).click();await page.getByRole('button',{name:'继续处理',exact:true}).waitFor();
  assert.deepEqual(calls,['merge','pause']);assert.deepEqual(errors,[]);
  await browser.close();server.close();console.log('Recovery browser checks passed: prominent manual merge, pause, 60-task mobile picker and detail focus.');
})().catch(error=>{console.error(error);server.close();process.exit(1);});

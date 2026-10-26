(function(root){
  'use strict';
  const esc=value=>String(value??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const active=exported=>['queued','running','preparing','merging','verifying','publishing','copy-verifying','pausing'].includes(exported.state);
  const gb=value=>(value/1073741824).toFixed(2)+' GB';
  const last=job=>(job.exports||[]).slice(-1)[0];
  function state(job){
    if(job.delete_requested)return 'deleting';
    if(job.state==='cancelling'||(job.exports||[]).some(e=>e.state==='pausing'))return 'pausing';
    if((job.exports||[]).some(active))return 'running';
    if(last(job)?.state==='paused')return 'paused';
    if(last(job)?.state==='failed')return 'failed';
    return job.state==='cancelled'?'paused':job.state;
  }
  function actionsHTML(job){
    const pausing=state(job)==='pausing',running=['queued','running'].includes(job.state)||(job.exports||[]).some(active);
    const resumable=['cancelled','paused','failed','interrupted'].includes(job.state)&&!job.imported;
    return `<div class="actions task-primary-actions">${running||pausing?`<button id="pause-task" ${pausing?'disabled':''}>${pausing?'正在暂停…':'暂停任务'}</button>`:''}${resumable?'<button id="resume-task" class="primary">继续处理</button>':''}<button id="delete-task" class="danger" ${job.delete_requested?'disabled':''}>删除任务</button></div><p class="task-action-help">暂停保留识别进度和片段；删除移除任务及全部识别缓存，原录像和已发布成品保留。</p>`;
  }
  function recoveryHTML(job){
    const info=job.merge_recovery;if(!info)return '';
    const paused=last(job)?.state==='paused';
    return `<section class="merge-recovery" role="region" aria-label="继续合并"><div class="recovery-heading"><div><h3>${paused?'合并已暂停':'识别已完成，等待继续合并'}</h3><p>已识别的片段已保留，从合并阶段继续，无需重新识别。</p></div><span class="recovery-status ${info.space_ready?'ready':''}">${info.can_retry?(info.space_ready?'空间充足':'等待释放空间'):'片段不可用'}</span></div><div class="recovery-disks">${info.disks.map(d=>`<div><strong>${esc(d.name)}</strong><span>可用 ${gb(d.free_bytes)} · 本次需要 ${gb(d.required_bytes)}</span>${d.shortfall_bytes>0?`<em>还需释放 ${gb(d.shortfall_bytes)}</em>`:''}</div>`).join('')}</div>${info.reason?`<p class="hint">${esc(info.reason)}</p>`:''}<div class="actions"><button id="retry-export" class="primary" ${info.can_retry?'':'disabled'}>${info.space_ready?'继续合并':'检查空间并继续合并'}</button><button id="check-merge-space">刷新空间</button></div><small>仅在你点击后继续处理；已有识别结果不会重跑。</small></section>`;
  }
  function bindPicker(){
    const button=document.getElementById('toggle-tasks'),aside=document.getElementById('task-sidebar');
    if(!button||!aside)return;
    button.onclick=()=>{const open=aside.classList.toggle('tasks-open');button.setAttribute('aria-expanded',String(open));button.textContent=open?'收起列表':'切换任务';};
  }
  function closePicker(){
    const aside=document.getElementById('task-sidebar'),button=document.getElementById('toggle-tasks');
    aside?.classList.remove('tasks-open');
    if(button){button.setAttribute('aria-expanded','false');button.textContent='切换任务';}
  }
  function updatePicker(jobs,current){
    const label=document.getElementById('mobile-current-task'),count=document.getElementById('task-count');
    if(count)count.textContent=jobs.length+' 个';
    if(label)label.textContent=jobs.find(j=>j.id===current)?.title||'请选择任务';
  }
  const api={state,actionsHTML,recoveryHTML,active,bindPicker,closePicker,updatePicker};
  if(typeof module!=='undefined'&&module.exports)module.exports=api;
  else root.TaskControls=api;
})(typeof window!=='undefined'?window:globalThis);

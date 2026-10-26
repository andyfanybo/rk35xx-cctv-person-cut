(function(root){
  'use strict';
  const esc=value=>String(value??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const clock=(value,ceil=false)=>{const s=Math.max(0,ceil?Math.ceil(value):Math.floor(value));return [Math.floor(s/3600),Math.floor(s/60)%60,s%60].map(n=>String(n).padStart(2,'0')).join(':');};
  const duration=value=>{if(value==null)return '尚未记录';const s=Math.max(0,Math.round(value)),h=Math.floor(s/3600),m=Math.floor(s/60)%60;return `${h?h+'小时 ':''}${m?m+'分 ':''}${s%60}秒`;};
  const stamp=value=>value==null?'—':new Date(value*1000).toLocaleString('zh-CN',{hour12:false});
  const names={people:'有人',no_detection:'无人 · 未检出',uncertain:'待复核',unanalyzed:'未分析'};
  const states=new Map();
  const PAGE_SIZE=40;
  function timingHTML(runtime){
    if(!runtime)return '';
    const status={running:'计时中',paused:'已暂停',done:'已结束',queued:'等待开始'}[runtime.state]||'';
    return `<section class="runtime-summary" id="task-runtime" aria-label="任务处理总耗时"><div class="runtime-main"><span>处理总耗时 <small>${status}</small></span><strong>${runtime.estimated&&runtime.elapsed_seconds!=null?'约 ':''}${duration(runtime.elapsed_seconds)}</strong><p>从开始处理到成品完成，含中途暂停与等待</p></div><div class="runtime-stamps"><span>开始处理<strong>${esc(stamp(runtime.started_at))}</strong></span><span>${runtime.state==='done'?'处理结束':'结束时间'}<strong>${runtime.finished_at!=null?esc(stamp(runtime.finished_at)):runtime.state==='paused'?'等待继续':runtime.state==='running'?'处理中':'—'}</strong></span>${runtime.estimated?'<small>历史任务根据已有记录估算</small>':''}</div></section>`;
  }
  function updateTiming(runtime){const current=document.getElementById('task-runtime');if(current)current.outerHTML=timingHTML(runtime);}
  function selection(timeline,filter,page){
    const rows=timeline.intervals.filter(row=>filter==='all'||row.kind===filter);
    const pages=Math.max(1,Math.ceil(rows.length/PAGE_SIZE));page=Math.max(0,Math.min(page,pages-1));
    return {rows:rows.slice(page*PAGE_SIZE,(page+1)*PAGE_SIZE),count:rows.length,page,pages};
  }
  function intervalRows(rows){
    if(!rows.length)return '<tr><td colspan="4" class="timeline-empty">暂无对应时间段</td></tr>';
    return rows.map(row=>`<tr><td><span class="period-kind ${row.kind}">${names[row.kind]}</span></td><td class="period-range">${clock(row.start)} <span>—</span> ${clock(row.end,true)}</td><td>${duration(row.duration_seconds)}</td><td>${row.gap_since_previous_people_seconds==null?'—':duration(row.gap_since_previous_people_seconds)}</td></tr>`).join('');
  }
  function timelineHTML(job){
    const timelines=job.statistics?.timelines||[];
    if(!timelines.length)return '';
    return `<section class="timeline-section"><div class="timeline-title"><div><p class="eyebrow">原片定位</p><h3>有人 / 无人时间段</h3></div><span class="timeline-note">原片 00:00:00 起算</span></div><p class="hint">有人区间包含前后缓冲；无人表示未检出。时间取整到秒，便于在原片中查找。</p>${timelines.map((timeline,index)=>{
      const key=job.id+':'+index,state=states.get(key)||{filter:'all',page:0},view=selection(timeline,state.filter,state.page);
      return `<div class="source-timeline" data-timeline="${index}" data-state-key="${esc(key)}">${timelines.length>1?`<h4>${esc(timeline.source.split('/').pop())}</h4>`:''}<div class="timeline-summary"><span><i class="period-dot people"></i>有人 <strong>${timeline.people_count} 段</strong></span><span><i class="period-dot no_detection"></i>无人 <strong>${timeline.no_detection_count} 段</strong></span><span>最长无人 <strong>${duration(timeline.longest_no_detection_seconds)}</strong></span></div><div class="timeline-filters" role="group" aria-label="筛选时间段">${[['all','全部'],['people','只看有人'],['no_detection','只看无人']].map(([value,label])=>`<button type="button" data-period-filter="${value}" aria-pressed="${state.filter===value}">${label}</button>`).join('')}</div><div class="timeline-table-wrap"><table class="period-table"><thead><tr><th scope="col">状态</th><th scope="col">原片时间段</th><th scope="col">本段时长</th><th scope="col">距上一段有人</th></tr></thead><tbody>${intervalRows(view.rows)}</tbody></table></div><div class="timeline-pager"><span data-period-count>${view.count} 段 · ${view.page+1} / ${view.pages} 页</span><div><button type="button" data-period-page="-1" ${view.page===0?'disabled':''}>上一页</button><button type="button" data-period-page="1" ${view.page+1>=view.pages?'disabled':''}>下一页</button></div></div></div>`;
    }).join('')}</section>`;
  }
  function bindTimelines(job){
    document.querySelectorAll('[data-timeline]').forEach(box=>{
      const timeline=job.statistics.timelines[Number(box.dataset.timeline)],key=box.dataset.stateKey;
      const repaint=()=>{
        const state=states.get(key)||{filter:'all',page:0},view=selection(timeline,state.filter,state.page);state.page=view.page;states.set(key,state);
        box.querySelector('tbody').innerHTML=intervalRows(view.rows);
        box.querySelector('[data-period-count]').textContent=`${view.count} 段 · ${view.page+1} / ${view.pages} 页`;
        box.querySelectorAll('[data-period-filter]').forEach(b=>b.setAttribute('aria-pressed',String(b.dataset.periodFilter===state.filter)));
        box.querySelector('[data-period-page="-1"]').disabled=view.page===0;
        box.querySelector('[data-period-page="1"]').disabled=view.page+1>=view.pages;
      };
      box.querySelectorAll('[data-period-filter]').forEach(button=>button.onclick=()=>{states.set(key,{filter:button.dataset.periodFilter,page:0});repaint();});
      box.querySelectorAll('[data-period-page]').forEach(button=>button.onclick=()=>{const state=states.get(key)||{filter:'all',page:0};state.page+=Number(button.dataset.periodPage);states.set(key,state);repaint();});
    });
  }
  const api={clock,duration,timingHTML,timelineHTML,selection,intervalRows,updateTiming,bindTimelines};
  if(typeof module!=='undefined'&&module.exports)module.exports=api;
  else root.TaskOverview=api;
})(typeof window!=='undefined'?window:globalThis);

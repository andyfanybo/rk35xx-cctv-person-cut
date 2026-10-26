const test=require('node:test');
const assert=require('node:assert/strict');
const ui=require('../web/task-overview.js');

test('source times handle fractions and recordings longer than a day',()=>{
  assert.equal(ui.clock(90061.2),'25:01:01');
  assert.equal(ui.clock(59.9,true),'00:01:00');
});
test('large timelines render only one bounded page',()=>{
  const timeline={intervals:Array.from({length:1000},(_,i)=>({kind:i%2?'people':'no_detection',start:i,end:i+1}))};
  assert.equal(ui.selection(timeline,'all',0).rows.length,40);
  assert.equal(ui.selection(timeline,'people',0).count,500);
  assert.equal(ui.selection(timeline,'people',999).page,12);
});
test('timeline is a text list with escaped source names and no media',()=>{
  const timeline={source:'<img src=x onerror=alert(1)>.mp4',people_count:1,no_detection_count:0,longest_no_detection_seconds:0,intervals:[{kind:'people',start:10,end:20,duration_seconds:10,gap_since_previous_people_seconds:null}]};
  const html=ui.timelineHTML({id:'test',statistics:{timelines:[timeline,timeline]}});
  assert.match(html,/&lt;img/);assert.doesNotMatch(html,/<video|<img|onclick=|href=/);
  assert.match(html,/00:00:10/);assert.match(html,/距上一段有人/);
});
test('timing distinguishes unknown legacy data and estimated totals',()=>{
  assert.match(ui.timingHTML({state:'queued',elapsed_seconds:null}),/尚未记录/);
  assert.match(ui.timingHTML({state:'done',elapsed_seconds:3661,estimated:true}),/约 1小时 1分 1秒/);
});

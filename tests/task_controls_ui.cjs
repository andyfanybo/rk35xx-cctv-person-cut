const test=require('node:test');const assert=require('node:assert/strict');const ui=require('../web/task-controls.js');
test('failed export has prominent manual merge action',()=>{
  const html=ui.recoveryHTML({exports:[{state:'failed'}],merge_recovery:{can_retry:true,space_ready:true,disks:[{name:'处理盘',free_bytes:20e9,required_bytes:8e9,shortfall_bytes:0}]}});
  assert.match(html,/id="retry-export"/);assert.match(html,/继续合并/);assert.match(html,/无需重新识别/);
});
test('space shortage still exposes a manual recheck action',()=>{
  const html=ui.recoveryHTML({exports:[{state:'failed'}],merge_recovery:{can_retry:true,space_ready:false,disks:[{name:'处理盘',free_bytes:0,required_bytes:8e9,shortfall_bytes:8e9}]}});
  assert.match(html,/检查空间并继续合并/);assert.match(html,/还需释放/);assert.doesNotMatch(html,/setTimeout|setInterval/);
});
test('pause is available during exports and old cancelled states display paused',()=>{
  assert.match(ui.actionsHTML({state:'done',exports:[{state:'running'}]}),/暂停任务/);
  assert.equal(ui.state({state:'cancelled'}),'paused');assert.equal(ui.state({state:'done',exports:[{state:'paused'}]}),'paused');
  assert.match(ui.actionsHTML({state:'paused'}),/继续处理/);
});
test('delete meaning is explicit and labels are escaped',()=>{
  assert.match(ui.actionsHTML({state:'running'}),/删除移除任务及全部识别缓存/);
  assert.doesNotMatch(ui.actionsHTML({state:'running'}),/取消任务/);
});

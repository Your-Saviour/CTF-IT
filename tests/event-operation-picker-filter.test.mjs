import test from 'node:test';
import assert from 'node:assert/strict';
import {
  abilityApplicabilityText,
  abilityEmptyText,
  abilityTargetId,
  filterAbilities,
  vmOptions,
  zoneKey,
  zoneOptions,
} from '../frontend/static/event-operation-picker-filter.js';

const targets=[
  {id:'vm:hq/blue/web',name:'Web Server',site:'HQ',zone:'Blue',base_type:'ubuntu'},
  {id:'vm:hq/blue/db',name:'Database',site:'HQ',zone:'Blue',base_type:'ubuntu'},
  {id:'vm:remote/blue/api',name:'Remote API',site:'Remote',zone:'Blue',base_type:'debian'},
];
const abilities=[
  {module_id:'weak_ssh',ability:'exploit',name:'Exploit: Weak SSH',description:'Use valid accounts',applicable_target_ids:['vm:hq/blue/web','vm:remote/blue/api']},
  {module_id:'db_dump',ability:'recon',name:'Recon: Database',description:'List schemas',applicable_target_ids:['vm:hq/blue/db']},
  {module_id:'stale',ability:'exploit',name:'Exploit: Removed VM',description:'Stale assignment',applicable_target_ids:['vm:missing/blue/old']},
];

test('zone choices keep same-named zones at different sites distinct',()=>{
  assert.equal(zoneKey(targets[0]),'HQ\u001fBlue');
  assert.deepEqual(zoneOptions(targets),[
    {value:'HQ\u001fBlue',label:'HQ · Blue'},
    {value:'Remote\u001fBlue',label:'Remote · Blue'},
  ]);
});

test('VM choices cascade from the selected site-aware zone',()=>{
  assert.deepEqual(vmOptions(targets,'HQ\u001fBlue').map(({id,name})=>({id,name})),[
    {id:'vm:hq/blue/web',name:'Web Server'},
    {id:'vm:hq/blue/db',name:'Database'},
  ]);
  assert.equal(vmOptions(targets,'').length,3);
});

test('ability filtering combines assignment applicability with search',()=>{
  assert.deepEqual(filterAbilities(abilities,targets,{zone:'HQ\u001fBlue',vm:'',query:''}).map(row=>row.module_id),['weak_ssh','db_dump']);
  assert.deepEqual(filterAbilities(abilities,targets,{zone:'HQ\u001fBlue',vm:'vm:hq/blue/db',query:''}).map(row=>row.module_id),['db_dump']);
  assert.deepEqual(filterAbilities(abilities,targets,{zone:'',vm:'',query:'valid accounts'}).map(row=>row.module_id),['weak_ssh']);
  assert.deepEqual(filterAbilities(abilities,targets,{zone:'',vm:'',query:'stale'}),[]);
});

test('only an explicit VM becomes the new ability target',()=>{
  assert.equal(abilityTargetId('vm:hq/blue/web'),'vm:hq/blue/web');
  assert.equal(abilityTargetId(''),'');
  assert.equal(abilityTargetId(null),'');
});

test('applicability text and empty states explain the active scope',()=>{
  assert.equal(abilityApplicabilityText(abilities[0],targets,{zone:'',vm:'vm:hq/blue/web'}),'Target · Web Server');
  assert.equal(abilityApplicabilityText(abilities[0],targets,{zone:'HQ\u001fBlue',vm:''}),'1 VM in HQ · Blue');
  assert.equal(abilityApplicabilityText(abilities[0],targets,{zone:'',vm:''}),'2 applicable VMs');
  assert.equal(abilityEmptyText({zone:'',vm:'vm:hq/blue/db',query:''},targets),'No abilities apply to Database.');
  assert.equal(abilityEmptyText({zone:'HQ\u001fBlue',vm:'',query:''},targets),'No abilities apply to HQ · Blue.');
  assert.equal(abilityEmptyText({zone:'',vm:'',query:'ssh'},targets),'No abilities match this search.');
});

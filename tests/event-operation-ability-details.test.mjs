import test from 'node:test';
import assert from 'node:assert/strict';
import {
  abilityCommand,
  findAbilityDetails,
  renderAbilityDetails,
  renderAbilityFacts,
} from '../frontend/static/event-operation-ability-details.js';

const node={type:'ability',label:'Run <check>',config:{module_id:'weak_ssh',ability:'exploit',target_vm_id:'vm:web'}};
const catalogue={
  targets:[{id:'vm:web',name:'Web & API'}],
  abilities:[{
    module_id:'weak_ssh',ability:'exploit',name:'Exploit <SSH>',
    description:'Use valid accounts & escalate',command:'ssh root@host && <payload>',
    tactic:'credential-access',technique:{attack_id:'T1078',name:'Valid Accounts'},
    supported_bases:['ubuntu','debian'],
  }],
};

test('finds ability metadata by module and phase and returns the original command',()=>{
  assert.equal(findAbilityDetails(node,catalogue),catalogue.abilities[0]);
  assert.equal(abilityCommand(node,catalogue),'ssh root@host && <payload>');
  assert.equal(findAbilityDetails({...node,config:{...node.config,ability:'recon'}},catalogue),null);
});

test('renders an escaped compact dossier with a collapsed command',()=>{
  const html=renderAbilityDetails(node,catalogue,{expanded:false});
  assert.match(html,/Exploit &lt;SSH&gt;/);
  assert.match(html,/Use valid accounts &amp; escalate/);
  assert.match(html,/Web &amp; API/);
  assert.match(html,/T1078 · Valid Accounts/);
  assert.match(html,/ubuntu, debian/);
  assert.match(html,/<details class="ability-command">/);
  assert.doesNotMatch(html,/<details class="ability-command" open>/);
  assert.match(html,/ssh root@host &amp;&amp; &lt;payload&gt;/);
});

test('renders expanded command controls and handles incomplete metadata',()=>{
  const expanded=renderAbilityDetails(node,catalogue,{expanded:true});
  assert.match(expanded,/<details class="ability-command" open>/);
  assert.match(expanded,/data-ability-action="copy-command"/);

  const partial={...catalogue,abilities:[{module_id:'weak_ssh',ability:'exploit',name:'Exploit SSH'}]};
  const html=renderAbilityDetails(node,partial,{expanded:false});
  assert.match(html,/No command metadata available/);
  assert.doesNotMatch(html,/copy-command/);
  assert.doesNotMatch(html,/<dt>Technique<\/dt>/);
  assert.equal(abilityCommand(node,partial),'');
});

test('renders a useful unavailable state for stale ability nodes',()=>{
  assert.match(renderAbilityDetails(node,{targets:[],abilities:[]},{expanded:false}),/Ability details are unavailable/);
});

test('renders fact inputs and outputs with markers', () => {
  const html = renderAbilityFacts({
    inputs: ['ctf.vuln.weak_ssh'],
    outputs: [{ trait: 'ctf.weak_ssh.shell', marker: 'VULNERABLE', pattern: 'user=(\\S+)' }],
  });
  assert.match(html, /ctf\.vuln\.weak_ssh/);
  assert.match(html, /ctf\.weak_ssh\.shell/);
  assert.match(html, /VULNERABLE/);
});

test('returns empty string when no facts are present', () => {
  assert.equal(renderAbilityFacts({ inputs: [], outputs: [] }), '');
  assert.equal(renderAbilityFacts(null), '');
  assert.equal(renderAbilityFacts(undefined), '');
});

const esc=value=>String(value??'').replace(/[&<>"']/g,ch=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[ch]));

export function findAbilityDetails(node,catalogue){
 if(node?.type!=='ability')return null;
 return catalogue?.abilities?.find(row=>row.module_id===node.config?.module_id&&row.ability===node.config?.ability)||null;
}

export function abilityCommand(node,catalogue){
 return String(findAbilityDetails(node,catalogue)?.command||'');
}

function fact(label,value){
 return value?`<div><dt>${esc(label)}</dt><dd>${esc(value)}</dd></div>`:'';
}

export function renderAbilityDetails(node,catalogue,{expanded=false}={}){
 const ability=findAbilityDetails(node,catalogue);
 if(!ability)return '<div class="ability-details-unavailable"><p>Ability details are unavailable.</p><p>The assigned module or ability phase may have changed.</p></div>';
 const target=catalogue?.targets?.find(row=>row.id===node.config?.target_vm_id);
 const technique=ability.technique?.attack_id&&ability.technique?.name
  ?`${ability.technique.attack_id} · ${ability.technique.name}`
  :ability.technique?.attack_id||ability.technique?.name||'';
 const command=abilityCommand(node,catalogue);
 const commandContent=command
  ?`${expanded?'<button type="button" data-ability-action="copy-command">Copy command</button>':''}<pre><code>${esc(command)}</code></pre>`
  :'<p class="ability-command-empty">No command metadata available.</p>';
 return `<article class="ability-dossier${expanded?' expanded':''}">
  <header class="ability-dossier-heading"><div><span>${esc(ability.ability||node.config?.ability||'Ability')}</span><h3>${esc(ability.name||node.label)}</h3></div>${expanded?'':'<button type="button" data-ability-action="expand">Expand</button>'}</header>
  ${ability.description?`<p class="ability-effect">${esc(ability.description)}</p>`:''}
  <dl class="ability-facts">${fact('Target VM',target?.name||'Unknown target')}${fact('Phase',ability.ability||node.config?.ability)}${fact('ATT&CK tactic',ability.tactic)}${fact('Technique',technique)}${fact('Supported bases',(ability.supported_bases||[]).join(', '))}</dl>
  <details class="ability-command"${expanded?' open':''}><summary>Show command</summary>${commandContent}</details>
 </article>`;
}

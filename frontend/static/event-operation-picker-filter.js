const ZONE_SEPARATOR='\u001f';

export function zoneKey(target){
 return `${target?.site??''}${ZONE_SEPARATOR}${target?.zone??''}`;
}

export function zoneOptions(targets=[]){
 const seen=new Set(),options=[];
 targets.forEach(target=>{
  const value=zoneKey(target);
  if(seen.has(value))return;
  seen.add(value);
  options.push({value,label:`${target.site} · ${target.zone}`});
 });
 return options;
}

export function vmOptions(targets=[],selectedZone=''){
 return targets.filter(target=>!selectedZone||zoneKey(target)===selectedZone);
}

function applicableIds(ability,validIds){
 return (ability?.applicable_target_ids||[]).filter(id=>validIds.has(id));
}

export function filterAbilities(abilities=[],targets=[],filters={}){
 const {zone='',vm='',query=''}=filters;
 const validIds=new Set(targets.map(target=>target.id));
 const zoneIds=new Set(vmOptions(targets,zone).map(target=>target.id));
 const needle=query.trim().toLowerCase();
 return abilities.filter(ability=>{
  const ids=applicableIds(ability,validIds);
  if(vm&&!ids.includes(vm))return false;
  if(!vm&&zone&&!ids.some(id=>zoneIds.has(id)))return false;
  if(!ids.length)return false;
  const searchable=`${ability.name??''} ${ability.description??''} ${ability.module_id??''} ${ability.ability??''}`.toLowerCase();
  return !needle||searchable.includes(needle);
 });
}

export function abilityTargetId(selectedVm){
 return selectedVm||'';
}

export function abilityApplicabilityText(ability,targets=[],filters={}){
 const validIds=new Set(targets.map(target=>target.id));
 const ids=applicableIds(ability,validIds);
 if(filters.vm){
  const target=targets.find(row=>row.id===filters.vm);
  return target?`Target · ${target.name}`:'Target not selected';
 }
 if(filters.zone){
  const matching=vmOptions(targets,filters.zone).filter(target=>ids.includes(target.id));
  const zone=zoneOptions(targets).find(row=>row.value===filters.zone);
  return `${matching.length} VM${matching.length===1?'':'s'} in ${zone?.label||'selected zone'}`;
 }
 return `${ids.length} applicable VM${ids.length===1?'':'s'}`;
}

export function abilityEmptyText(filters={},targets=[]){
 if(filters.vm){
  const target=targets.find(row=>row.id===filters.vm);
  return `No abilities apply to ${target?.name||'the selected VM'}.`;
 }
 if(filters.zone){
  const zone=zoneOptions(targets).find(row=>row.value===filters.zone);
  return `No abilities apply to ${zone?.label||'the selected zone'}.`;
 }
 return filters.query?.trim()?'No abilities match this search.':'No abilities are available.';
}

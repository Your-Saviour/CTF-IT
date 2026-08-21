const app=document.querySelector('.operations-app');
const eventId=app.dataset.eventId,readOnly=app.dataset.readOnly==='true';
const list=document.getElementById('operations-list'),message=document.getElementById('operations-message');
const editor=document.getElementById('operation-editor-dialog'),editorForm=document.getElementById('operation-editor-form');
const deleteDialog=document.getElementById('operation-delete-dialog'),deleteForm=document.getElementById('operation-delete-form');
const esc=value=>String(value??'').replace(/[&<>"']/g,char=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[char]));
let operations=[],editingId=null,deletingId=null;

async function api(path='',options={}){
  const response=await fetch(`/admin/api/events/${eventId}/operations${path}`,{headers:{'Content-Type':'application/json'},...options});
  if(response.status===204)return null;
  const data=await response.json();
  if(!response.ok)throw new Error(data.error||'Request failed');
  return data;
}
function triggerLabel(type){return ({manual_trigger:'Manual trigger',event_start_trigger:'Event start',scheduled_trigger:'Scheduled'})[type]||'Trigger missing'}
function dateLabel(value){return new Intl.DateTimeFormat(undefined,{dateStyle:'medium',timeStyle:'short'}).format(new Date(value))}
function setMessage(text,error=false){message.textContent=text;message.classList.toggle('is-error',error)}
function render(){
  document.getElementById('operations-count').textContent=`${operations.length} operation${operations.length===1?'':'s'}`;
  if(!operations.length){list.innerHTML=`<div class="operations-empty"><strong>No operations yet</strong><p>Create an independent graph for each phase of this event.</p>${readOnly?'':'<button class="btn btn-primary" data-empty-create type="button">New operation</button>'}</div>`;list.querySelector('[data-empty-create]')?.addEventListener('click',()=>openEditor());return}
  list.innerHTML=operations.map((row,index)=>`<article class="operation-row">
    <div class="operation-folio">${String(index+1).padStart(2,'0')}</div>
    <div class="operation-main"><div class="operation-title"><h3>${esc(row.name)}</h3><span class="operation-validity ${row.valid?'is-valid':'is-invalid'}">${row.valid?'Valid':`${row.issue_count} issue${row.issue_count===1?'':'s'}`}</span></div><p>${row.description?esc(row.description):'<span class="operation-muted">No description</span>'}</p><div class="operation-meta"><span>${esc(triggerLabel(row.trigger_type))}</span><span>Updated ${esc(dateLabel(row.updated_at))}</span></div></div>
    <div class="operation-actions"><a class="btn btn-primary" href="/admin/events/${eventId}/operations/${row.id}">Open designer</a>${readOnly?'':`<button class="btn" data-edit="${row.id}" type="button">Edit details</button><button class="btn" data-duplicate="${row.id}" type="button">Duplicate</button><button class="btn operation-delete" data-delete="${row.id}" type="button">Delete</button>`}</div>
  </article>`).join('');
  list.querySelectorAll('[data-edit]').forEach(button=>button.onclick=()=>openEditor(Number(button.dataset.edit)));
  list.querySelectorAll('[data-duplicate]').forEach(button=>button.onclick=()=>duplicate(Number(button.dataset.duplicate)));
  list.querySelectorAll('[data-delete]').forEach(button=>button.onclick=()=>openDelete(Number(button.dataset.delete)));
}
function openEditor(id=null){editingId=id;const row=operations.find(item=>item.id===id);document.getElementById('operation-editor-mode').textContent=row?'Operation details':'New operation';document.getElementById('operation-editor-title').textContent=row?'Edit operation':'Create operation';document.getElementById('operation-name').value=row?.name||'';document.getElementById('operation-description').value=row?.description||'';document.getElementById('operation-editor-error').textContent='';editor.showModal();document.getElementById('operation-name').focus()}
function openDelete(id){deletingId=id;const row=operations.find(item=>item.id===id);document.getElementById('operation-delete-copy').textContent=`${row.name} will be permanently deleted.`;deleteDialog.showModal()}
async function load(){try{const data=await api();operations=data.operations;render();setMessage('')}catch(error){setMessage(error.message,true)}}
async function duplicate(id){setMessage('Duplicating operation…');try{await api(`/${id}/duplicate`,{method:'POST'});await load();setMessage('Operation duplicated')}catch(error){setMessage(error.message,true)}}
document.getElementById('operation-create').onclick=()=>openEditor();
document.querySelectorAll('[data-close-editor]').forEach(button=>button.onclick=()=>editor.close());
document.querySelectorAll('[data-close-delete]').forEach(button=>button.onclick=()=>deleteDialog.close());
editorForm.onsubmit=async event=>{event.preventDefault();const payload={name:document.getElementById('operation-name').value,description:document.getElementById('operation-description').value};try{await api(editingId?`/${editingId}`:'',{method:editingId?'PATCH':'POST',body:JSON.stringify(payload)});editor.close();await load();setMessage(editingId?'Operation updated':'Operation created')}catch(error){document.getElementById('operation-editor-error').textContent=error.message}};
deleteForm.onsubmit=async event=>{event.preventDefault();try{await api(`/${deletingId}`,{method:'DELETE'});deleteDialog.close();await load();setMessage('Operation deleted')}catch(error){setMessage(error.message,true)}};
load();

const app = document.getElementById('app');
const esc = value => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const date = value => value ? new Date(value).toLocaleString() : 'No deadline';
const number = value => Number(value || 0).toLocaleString();

async function request(url, options) {
  const response = await fetch(url, {...options, headers: {'Accept':'application/json', ...(options && options.headers)}});
  const data = await response.json();
  if (!response.ok) throw new Error(data.error || 'Request failed');
  return data;
}

function taskMarkup(task, vm, index, readOnly) {
  const hints = task.hints.map(h => `<div class="hint" data-hint="${h.index}">${h.revealed ? esc(h.text) : `<button data-reveal="${h.index}">Reveal hint ${h.index + 1}</button>`}</div>`).join('');
  const debrief = task.debrief ? `<section class="debrief"><h4>Debrief</h4><p><strong>Root cause:</strong> ${esc(task.debrief.root_cause)}</p><p><strong>Remediation:</strong> ${esc(task.debrief.remediation)}</p><p><strong>ATT&amp;CK:</strong> ${esc(task.debrief.attack_mapping)}</p>${task.references.map(r => `<p><a href="${esc(r)}" rel="noreferrer">${esc(r)}</a></p>`).join('')}</section>` : '';
  return `<article class="task" data-vm="${vm.id}" data-module="${esc(task.id)}"><div class="task-index">${String(index + 1).padStart(2,'0')}</div><div class="task-main"><h3>${esc(task.name)}</h3><p>${esc(task.description)}</p><div class="tags"><span class="status status-${esc(task.status)}">${esc(task.status)}</span><span class="tag">${esc(task.difficulty)}</span><span class="tag">${task.estimated_minutes} minutes</span><span class="tag">${number(task.points)} points</span></div>${task.prerequisites.length ? `<p><strong>Prerequisites:</strong> ${task.prerequisites.map(esc).join(', ')}</p>` : ''}${task.learning_objectives.length ? `<h4>Learning objectives</h4><ul class="objectives">${task.learning_objectives.map(o => `<li>${esc(o)}</li>`).join('')}</ul>` : ''}<div class="hint-list">${hints}</div>${debrief}</div><aside class="task-side"><button class="button" data-verify ${readOnly ? 'disabled' : ''}>Verify fix</button><small>${task.last_verified_at ? `Last checked ${date(task.last_verified_at)}` : 'Not yet checked'}</small></aside></article>`;
}

function dashboard(data) {
  app.innerHTML = `<section class="event-head"><div class="event-copy"><p class="eyebrow">${esc(data.team.name)} · ${esc(data.event.status)}</p><h1>${esc(data.event.name)}</h1>${data.event.description ? `<p class="event-description">${esc(data.event.description)}</p>` : ''}</div><div class="event-meta"><span class="label">Deadline</span><strong>${esc(date(data.event.ends_at))}</strong></div></section><section class="metric-grid"><div class="metric metric-primary"><span class="label">Team score</span><span class="metric-value">${number(data.score.total)}</span></div><div class="metric"><span class="label">Completed</span><span class="metric-value">${number(data.score.completed)}</span></div><div class="metric"><span class="label">Progress</span><span class="metric-value">${number(data.score.completion_percentage)}%</span></div><div class="metric"><span class="label">Regressions</span><span class="metric-value">${number(data.regressions)}</span></div></section><div class="action-row"><div>${data.read_only ? '<span class="warning">This event is read-only. Historical results remain available.</span>' : data.regressions ? '<span class="warning">A completed task has regressed and no longer contributes points.</span>' : 'Connect to an assigned VM and verify each root-cause fix.'}</div><button class="button button-secondary" id="show-access">Show team access</button></div>${data.vms.map(vm => `<section class="vm-section"><header class="vm-head"><div><p class="label">Assigned target</p><h2>${esc(vm.hostname || `VM ${vm.id}`)}</h2></div><code class="connection">${esc(vm.connection_command)}</code></header>${vm.modules.map((task,i) => taskMarkup(task,vm,i,data.read_only)).join('') || '<p>No learner tasks are assigned to this target.</p>'}</section>`).join('')}${data.recent_activity.length ? `<section class="vm-section"><header class="vm-head"><div><p class="label">Verification history</p><h2>Recent attempts</h2></div></header><div class="table-wrap"><table><thead><tr><th>Time</th><th>Trigger</th><th>Result</th><th>Summary</th></tr></thead><tbody>${data.recent_activity.map(item=>`<tr><td>${esc(date(item.created_at))}</td><td>${esc(item.trigger)}</td><td>${esc(item.result)}</td><td>${esc(item.summary)}</td></tr>`).join('')}</tbody></table></div></section>` : ''}`;
  bindDashboard();
}

function bindDashboard() {
  document.getElementById('show-access').addEventListener('click', async () => {
    const dialog = document.getElementById('access-dialog'), content = document.getElementById('access-content');
    content.innerHTML = '<p>Loading credentials…</p>'; dialog.showModal();
    try { const data = await request('/api/me/team-access'); content.innerHTML = `<h2>Team access</h2><p>Save the private key with mode <code>0600</code>. Use the separate password when sudo prompts.</p><h3>Private key</h3><pre>${esc(data.private_key)}</pre><h3>Sudo password</h3><pre>${esc(data.sudo_password)}</pre><h3>Connections</h3>${data.connections.map(c => `<pre>${esc(c.command)}</pre>`).join('')}`; } catch (error) { content.innerHTML = `<p class="warning">${esc(error.message)}</p>`; }
  });
  app.querySelectorAll('[data-reveal]').forEach(button => button.addEventListener('click', async () => {
    const task = button.closest('.task');
    try { const data = await request(`/api/vms/${task.dataset.vm}/modules/${task.dataset.module}/hints/${button.dataset.reveal}/reveal`, {method:'POST'}); button.parentElement.textContent = data.text; } catch (error) { button.parentElement.textContent = error.message; }
  }));
  app.querySelectorAll('[data-verify]').forEach(button => button.addEventListener('click', async () => {
    const task = button.closest('.task'); button.disabled = true; button.textContent = 'Checking…';
    try { const data = await request(`/api/vms/${task.dataset.vm}/modules/${task.dataset.module}/verify`, {method:'POST'}); button.textContent = data.result === 'pass' ? 'Verified' : data.summary; setTimeout(loadDashboard, 900); } catch (error) { button.textContent = error.message; button.disabled = false; }
  }));
}

async function loadDashboard() { try { dashboard(await request('/api/me/training')); } catch (error) { app.innerHTML = `<p class="warning loading">${esc(error.message)}</p>`; } }
async function loadScoreboard() { try { const data = await request('/api/scoreboard'); app.innerHTML = `<section class="event-head"><div class="event-copy"><p class="eyebrow">${esc(data.event.status)}</p><h1>Scoreboard</h1><p class="event-description">${esc(data.event.name)}</p></div></section><div class="table-wrap"><table><thead><tr><th>Rank</th><th>Team</th><th>Defensive</th><th>Reactive</th><th>Total</th><th>Completion</th><th>Red pressure</th></tr></thead><tbody>${data.teams.map(t => `<tr data-current="${t.team_id === data.current_team_id}"><td>${t.rank}</td><td><strong>${esc(t.team_name)}</strong></td><td>${number(t.blue_defensive)}</td><td>${number(t.blue_reactive)}</td><td>${number(t.total_score)}</td><td>${number(t.completion_percentage)}%</td><td>${number(t.red_team_pressure)}</td></tr>`).join('')}</tbody></table></div>`; } catch (error) { app.innerHTML = `<p class="warning loading">${esc(error.message)}</p>`; } }

document.body.dataset.view === 'scoreboard' ? loadScoreboard() : loadDashboard();

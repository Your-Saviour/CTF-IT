(function () {
  const difficulties = ['easy', 'medium', 'hard'];
  function quotaFromGrid(form) {
    const quota = {};
    form.querySelectorAll('.module-quota-row').forEach(row => {
      quota[row.dataset.type] = {};
      difficulties.forEach(level => { quota[row.dataset.type][level] = Number(row.querySelector(`[data-difficulty="${level}"]`).value) || 0; });
    });
    return quota;
  }
  function quotaToGrid(form, quota) {
    form.querySelectorAll('.module-quota-row').forEach(row => difficulties.forEach(level => {
      row.querySelector(`[data-difficulty="${level}"]`).value = quota?.[row.dataset.type]?.[level] || 0;
    }));
  }
  function escapeText(value) { const node = document.createElement('span'); node.textContent = String(value ?? ''); return node.innerHTML; }

  window.setupEventEditor = async function (form, api) {
    const response = await fetch(api + '/modules');
    const modules = response.ok ? await response.json() : [];
    const types = [...new Set(modules.map(module => module.type))].sort();
    const grid = form.querySelector('#module-quota-grid'); grid.replaceChildren();
    types.forEach(type => {
      const row = document.createElement('div'); row.className = 'module-quota-row'; row.dataset.type = type;
      row.innerHTML = `<span class="quota-type-name">${escapeText(type.replaceAll('_', ' '))}</span>`;
      difficulties.forEach(level => { const input = document.createElement('input'); input.type = 'number'; input.min = '0'; input.value = '0'; input.dataset.difficulty = level; input.setAttribute('aria-label', `${type} ${level} modules`); row.appendChild(input); });
      grid.appendChild(row);
    });
    form.querySelector('[data-module-json-toggle]').addEventListener('click', event => {
      const raw = form.querySelector('#module-quota-json'), structured = form.querySelector('#module-quota-structured');
      if (raw.hidden) { raw.value = JSON.stringify(quotaFromGrid(form), null, 2); raw.hidden = false; structured.hidden = true; event.target.dataset.raw = 'true'; event.target.textContent = 'Use form editor'; }
      else { try { quotaToGrid(form, JSON.parse(raw.value || '{}')); raw.hidden = true; structured.hidden = false; event.target.dataset.raw = 'false'; event.target.textContent = 'Edit raw JSON'; } catch (_) { window.showToast('Module quota JSON is invalid.'); } }
    });
    form.dataset.editorReady = 'true'; window.resetEventEditor(form);
  };
  window.populateEventEditor = function (form, event) {
    form.reset(); quotaToGrid(form, event.quota || {});
    form.elements.name.value = event.name || ''; form.elements.description.value = event.description || '';
    form.elements.welcome_message.value = event.welcome_message || ''; form.elements.time_limit_minutes.value = event.time_limit_minutes || '';
  };
  window.readEventEditor = function (form) {
    let quota;
    try { quota = form.querySelector('[data-module-json-toggle]').dataset.raw === 'true' ? JSON.parse(form.querySelector('#module-quota-json').value || '{}') : quotaFromGrid(form); }
    catch (_) { throw new Error('Fix invalid JSON before saving.'); }
    return {name: form.elements.name.value.trim(), description: form.elements.description.value.trim() || null,
      welcome_message: form.elements.welcome_message.value.trim() || null,
      time_limit_minutes: form.elements.time_limit_minutes.value ? Number(form.elements.time_limit_minutes.value) : null,
      quota};
  };
  window.resetEventEditor = function (form) {
    form.querySelectorAll('.module-quota-row input').forEach(input => { input.value = '0'; });
  };
})();

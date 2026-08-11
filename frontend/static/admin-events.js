(function () {
  const difficulties = ['easy', 'medium', 'hard'];
  const editorState = new WeakMap();

  function option(value, label) {
    const node = document.createElement('option');
    node.value = value;
    node.textContent = label;
    return node;
  }

  function quotaFromGrid(form) {
    const quota = {};
    form.querySelectorAll('.module-quota-row').forEach(row => {
      quota[row.dataset.type] = {};
      difficulties.forEach(level => {
        quota[row.dataset.type][level] = Number(row.querySelector(`[data-difficulty="${level}"]`).value) || 0;
      });
    });
    return quota;
  }

  function quotaToGrid(form, quota) {
    form.querySelectorAll('.module-quota-row').forEach(row => {
      difficulties.forEach(level => {
        row.querySelector(`[data-difficulty="${level}"]`).value = quota?.[row.dataset.type]?.[level] || 0;
      });
    });
  }

  function addVmRow(form, baseTypes, key, spec) {
    const template = form.querySelector('#vm-quota-row-template');
    const row = template.content.firstElementChild.cloneNode(true);
    const baseSelect = row.querySelector('[name="vm_base_type"]');
    baseSelect.appendChild(option('', 'Select base image'));
    baseTypes.forEach(base => baseSelect.appendChild(option(base.id, base.name)));
    row.querySelector('[name="vm_type_name"]').value = key || '';
    baseSelect.value = spec?.base_type || '';
    row.querySelector('[name="vm_count"]').value = spec?.count || 1;
    row.querySelector('[name="vm_role"]').value = spec?.role || 'target';
    row.querySelector('[name="vm_plan"]').value = spec?.default_plan || '';
    row.querySelector('[name="vm_region"]').value = spec?.region || '';
    row.querySelector('[data-remove-vm]').addEventListener('click', () => row.remove());
    form.querySelector('#vm-quota-rows').appendChild(row);
  }

  function vmQuotaFromRows(form) {
    const quota = {};
    for (const row of form.querySelectorAll('.vm-quota-row')) {
      const key = row.querySelector('[name="vm_type_name"]').value.trim().replace(/\s+/g, '_');
      const baseType = row.querySelector('[name="vm_base_type"]').value;
      if (!key && !baseType) continue;
      if (!key) throw new Error('Each VM configuration needs a type name.');
      if (!baseType) throw new Error(`Select a base image for ${key}.`);
      const spec = {
        base_type: baseType,
        count: Number(row.querySelector('[name="vm_count"]').value) || 1,
        role: row.querySelector('[name="vm_role"]').value,
      };
      const plan = row.querySelector('[name="vm_plan"]').value.trim();
      const region = row.querySelector('[name="vm_region"]').value.trim();
      if (plan) spec.default_plan = plan;
      if (region) spec.region = region;
      quota[key] = spec;
    }
    return quota;
  }

  function vmQuotaToRows(form, baseTypes, quota) {
    form.querySelector('#vm-quota-rows').replaceChildren();
    Object.entries(quota || {}).forEach(([key, spec]) => addVmRow(form, baseTypes, key, spec));
  }

  function bindAdvancedToggle(form, buttonSelector, structuredSelector, rawSelector, readStructured, writeStructured) {
    const button = form.querySelector(buttonSelector);
    const structured = form.querySelector(structuredSelector);
    const raw = form.querySelector(rawSelector);
    button.addEventListener('click', () => {
      const openingRaw = raw.hidden;
      if (openingRaw) {
        try {
          raw.value = JSON.stringify(readStructured(), null, 2);
        } catch (error) {
          window.showToast(error.message);
          return;
        }
        structured.hidden = true;
        raw.hidden = false;
        button.textContent = 'Use form editor';
        button.dataset.raw = 'true';
      } else {
        try {
          writeStructured(JSON.parse(raw.value || '{}'));
        } catch (_) {
          window.showToast('Fix the invalid JSON before returning to the form.');
          return;
        }
        raw.hidden = true;
        structured.hidden = false;
        button.textContent = 'Edit raw JSON';
        button.dataset.raw = 'false';
      }
    });
  }

  window.setupEventEditor = async function (form, api) {
    const quotaGrid = form.querySelector('#module-quota-grid');
    const [moduleResponse, baseResponse] = await Promise.all([fetch(api + '/modules'), fetch(api + '/base-types')]);
    const modules = moduleResponse.ok ? await moduleResponse.json() : [];
    const baseTypes = baseResponse.ok ? await baseResponse.json() : [];
    const moduleTypes = [...new Set(modules.map(module => module.type))].sort();

    quotaGrid.replaceChildren();
    moduleTypes.forEach(type => {
      const row = document.createElement('div');
      row.className = 'module-quota-row';
      row.dataset.type = type;
      const label = document.createElement('span');
      label.className = 'quota-type-name';
      label.textContent = type.replaceAll('_', ' ');
      row.appendChild(label);
      difficulties.forEach(level => {
        const input = document.createElement('input');
        input.type = 'number';
        input.min = '0';
        input.value = '0';
        input.dataset.difficulty = level;
        input.setAttribute('aria-label', `${type} ${level} modules`);
        row.appendChild(input);
      });
      quotaGrid.appendChild(row);
    });
    if (!moduleTypes.length) quotaGrid.innerHTML = '<p class="form-help">No module types are currently available.</p>';

    form.querySelector('[data-add-vm]').addEventListener('click', () => addVmRow(form, baseTypes, '', {}));
    bindAdvancedToggle(form, '[data-module-json-toggle]', '#module-quota-structured', '#module-quota-json',
      () => quotaFromGrid(form), quota => quotaToGrid(form, quota));
    bindAdvancedToggle(form, '[data-vm-json-toggle]', '#vm-quota-structured', '#vm-quota-json',
      () => vmQuotaFromRows(form), quota => vmQuotaToRows(form, baseTypes, quota));
    editorState.set(form, { baseTypes });
    form.dataset.editorReady = 'true';
  };

  window.populateEventEditor = function (form, event) {
    if (form.dataset.editorReady !== 'true') throw new Error('The event editor is still loading.');
    const { baseTypes } = editorState.get(form);
    form.reset();
    window.resetEventEditor(form);
    form.elements.name.value = event.name || '';
    form.elements.description.value = event.description || '';
    form.elements.welcome_message.value = event.welcome_message || '';
    form.elements.time_limit_minutes.value = event.time_limit_minutes || '';

    const knownTypes = new Set([...form.querySelectorAll('.module-quota-row')].map(row => row.dataset.type));
    const advancedQuota = Object.keys(event.quota || {}).some(key => !knownTypes.has(key));
    if (advancedQuota) {
      form.querySelector('#module-quota-structured').hidden = true;
      const raw = form.querySelector('#module-quota-json');
      raw.hidden = false;
      raw.value = JSON.stringify(event.quota || {}, null, 2);
      const toggle = form.querySelector('[data-module-json-toggle]');
      toggle.dataset.raw = 'true';
      toggle.textContent = 'Use form editor';
    } else {
      quotaToGrid(form, event.quota || {});
    }
    vmQuotaToRows(form, baseTypes, event.vm_quota || {});
  };

  window.readEventEditor = function (form) {
    if (form.dataset.editorReady !== 'true') throw new Error('The event editor is still loading.');
    let quota;
    let vmQuota;
    try {
      quota = form.querySelector('[data-module-json-toggle]').dataset.raw === 'true'
        ? JSON.parse(form.querySelector('#module-quota-json').value || '{}')
        : quotaFromGrid(form);
      vmQuota = form.querySelector('[data-vm-json-toggle]').dataset.raw === 'true'
        ? JSON.parse(form.querySelector('#vm-quota-json').value || '{}')
        : vmQuotaFromRows(form);
    } catch (error) {
      if (error instanceof SyntaxError) throw new Error('Advanced quota JSON is invalid.');
      throw error;
    }
    const payload = {
      name: form.elements.name.value.trim(),
      description: form.elements.description.value.trim() || null,
      welcome_message: form.elements.welcome_message.value.trim() || null,
      time_limit_minutes: form.elements.time_limit_minutes.value ? Number(form.elements.time_limit_minutes.value) : null,
      quota,
    };
    if (Object.keys(vmQuota).length) payload.vm_quota = vmQuota;
    return payload;
  };

  window.resetEventEditor = function (form) {
    form.querySelectorAll('.module-quota-row input').forEach(input => { input.value = '0'; });
    form.querySelector('#vm-quota-rows').replaceChildren();
    for (const [buttonSelector, structuredSelector, rawSelector] of [
      ['[data-module-json-toggle]', '#module-quota-structured', '#module-quota-json'],
      ['[data-vm-json-toggle]', '#vm-quota-structured', '#vm-quota-json'],
    ]) {
      const button = form.querySelector(buttonSelector);
      button.dataset.raw = 'false';
      button.textContent = 'Edit raw JSON';
      form.querySelector(structuredSelector).hidden = false;
      form.querySelector(rawSelector).hidden = true;
      form.querySelector(rawSelector).value = '';
    }
  };
})();

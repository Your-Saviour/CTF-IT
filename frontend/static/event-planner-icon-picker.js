import {resolvePlannerIcon} from './event-planner-icons.js';

const escapeHtml = value => String(value ?? '').replace(/[&<>"']/g, character => ({
  '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;',
}[character]));

function iconSvg(icon, className = '') {
  return `<svg class="icon-picker-svg ${className}" viewBox="${escapeHtml(icon?.viewBox || '0 0 24 24')}" aria-hidden="true"><path d="${escapeHtml(icon?.path || '')}"></path></svg>`;
}

function optionMarkup(option, category, selected) {
  const icon = resolvePlannerIcon(option.value);
  const search = `${category} ${option.label} ${option.value}`.toLowerCase();
  return `<button type="button" class="icon-picker-option" role="option" tabindex="-1" data-icon-value="${escapeHtml(option.value)}" data-icon-search="${escapeHtml(search)}" aria-selected="${option.value === selected}">${iconSvg(icon)}<span>${escapeHtml(option.label)}</span></button>`;
}

export function renderIconPicker({name, label, value, selectedLabel, selectedIcon, automaticLabel, automaticIcon, groups, disabled}) {
  const automaticSelected = !value;
  const automatic = `<button type="button" class="icon-picker-option automatic" role="option" tabindex="-1" data-icon-value="" data-icon-search="automatic default" aria-selected="${automaticSelected}">${iconSvg(automaticIcon)}<span>${escapeHtml(automaticLabel)}</span></button>`;
  const categorized = groups.map(group => `<section class="icon-picker-group" data-icon-group><div class="icon-picker-group-label">${escapeHtml(group.label)}</div>${group.options.map(option => optionMarkup(option, group.label, value)).join('')}</section>`).join('');
  return `<label class="icon-picker-field"><span>${escapeHtml(label)}</span><div class="icon-picker" data-icon-picker="${escapeHtml(name)}"><button type="button" class="icon-picker-trigger" aria-haspopup="listbox" aria-expanded="false" ${disabled ? 'disabled' : ''}>${iconSvg(selectedIcon, 'selected')}<span>${escapeHtml(selectedLabel)}</span><span class="icon-picker-chevron" aria-hidden="true"></span></button><div class="icon-picker-menu" hidden><input type="search" class="icon-picker-search" aria-label="Search ${escapeHtml(label)} icons" placeholder="Search icons"><div class="icon-picker-options" role="listbox" aria-label="${escapeHtml(label)} choices">${automatic}${categorized}</div></div></div></label>`;
}

export function nextPickerOptionIndex(current, length, key) {
  if (!length) return -1;
  if (current < 0) return key === 'ArrowUp' ? length - 1 : 0;
  const offset = key === 'ArrowUp' ? -1 : 1;
  return (current + offset + length) % length;
}

export function closeIconPickers(root, except = null) {
  root?.querySelectorAll('[data-icon-picker]').forEach(picker => {
    if (picker === except) return;
    picker.querySelector('.icon-picker-menu').hidden = true;
    picker.querySelector('.icon-picker-trigger').setAttribute('aria-expanded', 'false');
  });
}

export function bindIconPickers(root, {onChange}) {
  root.querySelectorAll('[data-icon-picker]').forEach(picker => {
    const trigger = picker.querySelector('.icon-picker-trigger');
    const menu = picker.querySelector('.icon-picker-menu');
    const search = picker.querySelector('.icon-picker-search');
    const visibleOptions = () => [...picker.querySelectorAll('.icon-picker-option')].filter(option => !option.hidden);
    const close = ({focus = false} = {}) => {
      menu.hidden = true;
      trigger.setAttribute('aria-expanded', 'false');
      picker.querySelectorAll('.icon-picker-option').forEach(option => { option.tabIndex = -1; });
      if (focus) trigger.focus();
    };
    const focusOption = (options, index) => {
      options.forEach(option => { option.tabIndex = -1; });
      const option = options[index];
      if (option) {
        option.tabIndex = 0;
        option.focus();
      }
    };
    const filter = () => {
      const query = search.value.trim().toLowerCase();
      picker.querySelectorAll('.icon-picker-option').forEach(option => {
        option.hidden = Boolean(query) && !option.dataset.iconSearch.includes(query);
      });
      picker.querySelectorAll('[data-icon-group]').forEach(group => {
        const categoryMatches = group.querySelector('.icon-picker-group-label').textContent.toLowerCase().includes(query);
        if (categoryMatches) group.querySelectorAll('.icon-picker-option').forEach(option => { option.hidden = false; });
        group.hidden = ![...group.querySelectorAll('.icon-picker-option')].some(option => !option.hidden);
      });
    };
    const open = () => {
      if (trigger.disabled) return;
      closeIconPickers(root, picker);
      menu.hidden = false;
      trigger.setAttribute('aria-expanded', 'true');
      search.value = '';
      filter();
      search.focus();
    };

    trigger.addEventListener('click', () => menu.hidden ? open() : close());
    trigger.addEventListener('keydown', event => {
      if (event.key === 'ArrowDown' || event.key === 'ArrowUp') {
        event.preventDefault();
        event.stopPropagation();
        open();
        const options = visibleOptions();
        focusOption(options, nextPickerOptionIndex(-1, options.length, event.key));
      }
    });
    search.addEventListener('input', filter);
    picker.addEventListener('keydown', event => {
      if (event.key === 'Tab') {
        queueMicrotask(() => {
          if (!picker.contains(document.activeElement)) close();
        });
        return;
      }
      if (event.key === 'Escape') {
        event.preventDefault();
        close({focus: true});
        return;
      }
      if (event.key === 'Enter' && event.target === search) {
        event.preventDefault();
        visibleOptions()[0]?.click();
        return;
      }
      if (!['ArrowDown', 'ArrowUp'].includes(event.key) || menu.hidden) return;
      event.preventDefault();
      const options = visibleOptions();
      if (!options.length) return;
      const current = options.indexOf(document.activeElement);
      focusOption(options, nextPickerOptionIndex(current, options.length, event.key));
    });
    picker.addEventListener('focusout', () => queueMicrotask(() => {
      if (!picker.contains(document.activeElement)) close();
    }));
    picker.querySelectorAll('.icon-picker-option').forEach(option => option.addEventListener('click', () => {
      onChange(picker.dataset.iconPicker, option.dataset.iconValue);
      close();
    }));
  });
}

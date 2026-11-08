/* Keep existing workbench handlers attached while React owns the shared navigation. */
(() => {
  const main = $('mainContent');
  const select = $('matter');
  const workspaceToolButtons = ['new', 'manageWorkspaces', 'workspaceTrash'].map($);
  const listeners = new Set();
  const previousTab = tab;
  let page = 'Overview';
  let snapshot;

  function addPage(name, id, description) {
    const section = el('section', undefined, 'hidden wi-page');
    section.dataset.tab = name;
    if (description) {
      section.append(el('h2', 'Choose a Workspace Tool'), el('p', description, 'hint'));
    }
    const mount = el('div');
    mount.id = id;
    section.append(mount);
    main.append(section);
  }

  addPage('Overview', 'reactOverview', 'Find evidence, prepare witness work product, or review saved results.');
  addPage('Ask This Matter', 'reactInquire');

  function addWorkProductHeader(pageName, id, oldHeading) {
    const section = document.querySelector(`[data-tab="${pageName}"]`);
    const heading = el('div');
    heading.id = id;
    section.prepend(heading);
    for (const selector of ['.page-heading', oldHeading, ':scope > p.hint']) {
      section.querySelector(selector)?.classList.add('wi-replaced-heading');
    }
  }

  addWorkProductHeader('Witnesses', 'reactWitnessHeader', '.results-title h2');
  addWorkProductHeader('Review', 'reactReviewHeader', ':scope > h2');

  function update() {
    const loading = Boolean(select.value && state.matter_id !== select.value);
    const documents = loading || !select.value ? [] :
      state.documents.filter(doc => !['failed', 'excluded'].includes(doc.status));
    snapshot = {
      page,
      loading,
      matterId: select.value,
      matterName: select.selectedOptions[0]?.textContent || '',
      matters: [...select.options].map(option => ({ id: option.value, name: option.textContent })),
      documents,
      documentCount: documents.length,
      workspaceToolsDisabled: workspaceToolButtons[1].disabled,
    };
    listeners.forEach(notify => notify());
  }

  tab = name => {
    page = ['Questions', 'Chronology', 'Relationships'].includes(name) ? 'Witnesses' : name;
    previousTab(page);
    document.body.dataset.workbenchPage = page;
    if (page === 'Overview') $('pageTitle').textContent = 'Workspace Overview';
    update();
  };

  document.addEventListener('workspace-refreshed', update);
  select.addEventListener('change', update);
  new MutationObserver(update).observe(select, { childList: true });
  new MutationObserver(update).observe(workspaceToolButtons[1], {
    attributes: true, attributeFilter: ['disabled'],
  });
  window.witnessWorkbench = {
    subscribe(notify) {
      listeners.add(notify);
      return () => listeners.delete(notify);
    },
    snapshot: () => snapshot,
    navigate(name) {
      tab(name);
      main.focus({ preventScroll: true });
      window.scrollTo(0, 0);
    },
    selectMatter(id) {
      if (id !== select.value && [...select.options].some(option => option.value === id)) {
        select.value = id;
        select.dispatchEvent(new Event('change'));
      }
    },
    workspaceTool(index) {
      if (index === 0) {
        tab('Import');
        window.scrollTo(0, 0);
      }
      workspaceToolButtons[index].click();
    },
  };
  update();
})();

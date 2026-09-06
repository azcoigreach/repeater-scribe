(() => {
  let editor = null;
  function selection() {
    const selected = window.getSelection();
    if (!selected || selected.isCollapsed || !selected.rangeCount) return null;
    const range = selected.getRangeAt(0);
    const parent = range.startContainer.nodeType === Node.ELEMENT_NODE ? range.startContainer : range.startContainer.parentElement;
    const root = parent?.closest('[data-transcript-offset]');
    if (!root || !root.contains(range.endContainer)) return null;
    const before = range.cloneRange();
    before.selectNodeContents(root);
    before.setEnd(range.startContainer, range.startOffset);
    return { root, start: Number(root.dataset.transcriptOffset) + before.toString().length,
      end: Number(root.dataset.transcriptOffset) + before.toString().length + range.toString().length };
  }
  function node(tag, text, className = '') {
    const result = document.createElement(tag); result.textContent = text; result.className = className; return result;
  }
  function attach(container, { jobId, text, sources, onSaved }) {
    const button = node('button', 'Correct callsign', 'control-button callsign-correction-button');
    button.type = 'button';
    sources.forEach(({ node, offset }) => { node.dataset.transcriptOffset = offset; });
    let captured = null;
    button.addEventListener('pointerdown', () => { captured = selection(); });
    button.addEventListener('click', () => {
      if (editor) return;
      const picked = captured || selection(); captured = null;
      const dialog = node('dialog', '', 'callsign-correction-dialog');
      const form = document.createElement('form');
      const title = node('h2', 'Correct transcript callsign');
      title.id = 'callsign-correction-title'; dialog.setAttribute('aria-labelledby', title.id);
      const help = node('p', 'Select the mistaken words, then enter the callsign that should replace them. The raw transcript is kept.');
      const sourceLabel = node('label', 'Transcript — select the mistaken words');
      const source = document.createElement('textarea'); source.value = text; source.readOnly = true; source.rows = 7;
      sourceLabel.append(source);
      const chosen = node('p', 'No words selected.', 'correction-selection');
      const inputLabel = node('label', 'Correct callsign');
      const input = document.createElement('input'); input.type = 'text'; input.maxLength = 32; input.required = true; input.autocomplete = 'off'; input.spellcheck = false;
      inputLabel.append(input);
      const status = node('p', '', 'correction-status'); status.setAttribute('role', 'status');
      const actions = node('div', '', 'correction-actions');
      const save = node('button', 'Save correction', 'control-button'); save.type = 'submit'; save.disabled = true;
      const cancel = node('button', 'Cancel', 'control-button'); cancel.type = 'button';
      actions.append(save, cancel); form.append(title, help, sourceLabel, chosen, inputLabel, status, actions); dialog.append(form); document.body.append(dialog);
      editor = dialog;
      let start = 0, end = 0, saving = false;
      function pick() {
        start = source.selectionStart; end = source.selectionEnd;
        while (start < end && /\s/.test(text[start])) start++;
        while (end > start && /\s/.test(text[end - 1])) end--;
        chosen.textContent = start < end ? `Selected: ${text.slice(start, end)}` : 'No words selected.';
        save.disabled = saving || start === end;
      }
      source.addEventListener('select', pick);
      source.addEventListener('keyup', pick);
      source.addEventListener('pointerup', pick);
      cancel.addEventListener('click', () => dialog.close());
      dialog.addEventListener('cancel', event => { if (saving) event.preventDefault(); });
      dialog.addEventListener('close', () => { editor = null; dialog.remove(); });
      form.addEventListener('submit', async event => {
        event.preventDefault(); if (saving || start === end) return;
        saving = true; save.disabled = true; cancel.disabled = true; status.textContent = 'Saving correction…';
        try {
          const response = await fetch(`/ui/ingestion/jobs/${encodeURIComponent(jobId)}/callsign-correction`, {
            method: 'POST', headers: { 'Content-Type': 'application/json', 'X-CSRF-Token': document.querySelector('meta[name="csrf-token"]')?.content || '' },
            body: JSON.stringify({ expected_text: text, start: Array.from(text.slice(0, start)).length,
              end: Array.from(text.slice(0, end)).length, callsign: input.value.trim() })
          });
          const result = await response.json();
          if (!response.ok) throw new Error(typeof result.detail === 'string' ? result.detail : 'Correction could not be saved. Check the callsign and try again.');
          dialog.close(); await onSaved();
        } catch (error) {
          status.textContent = error.message; saving = false; cancel.disabled = false; save.disabled = start === end;
        }
      });
      dialog.showModal();
      window.getSelection()?.removeAllRanges();
      if (picked && sources.some(source => source.node === picked.root)) {
        source.focus(); source.setSelectionRange(picked.start, picked.end); pick(); input.focus();
      } else source.focus();
    });
    container.append(button);
  }
  window.TranscriptCorrections = { attach, busy: () => Boolean(editor || selection()) };
})();

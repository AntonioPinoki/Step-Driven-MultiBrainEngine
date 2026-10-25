() => {
  if (window.brainLoreGuard) return;
  const state = { dirty: false, replay: false, pending: null, busy: false, saving: false };
  window.brainLoreGuard = state;
  const dialog = document.createElement('dialog');
  dialog.style.cssText = 'padding:24px;border-radius:12px;max-width:440px;color:inherit;background:var(--body-background-fill,#222);z-index:10000';
  dialog.innerHTML = '<p>ロアブックに未保存の変更があります。</p><p class="guard-message">移動する前に保存しますか？</p><div style="display:flex;gap:12px"><button data-action="save">保存</button><button data-action="discard">破棄</button><button data-action="cancel">キャンセル</button></div>';
  document.body.append(dialog);
  const style = document.createElement("style");
  style.textContent = ".brain-lore-discard-trigger { display: none !important; }";
  document.head.append(style);
  const setBusy = (busy) => {
    state.busy = busy;
    dialog.querySelectorAll('button').forEach(button => button.disabled = busy);
  };
  const freezeEditor = (saving) => {
    state.saving = saving;
    document.querySelectorAll('.brain-lore-dirty-field').forEach(field => {
      field.inert = saving;
    });
  };
  document.addEventListener('click', event => {
    if (event.target.closest('#brain-lore-save')) freezeEditor(true);
  }, true);
  const complete = () => {
    freezeEditor(false);
    state.dirty = false;
    setBusy(false);
    dialog.close();
    const pending = state.pending;
    state.pending = null;
    if (pending?.target?.isConnected) {
      state.replay = true;
      pending.target.dispatchEvent(new PointerEvent('pointerdown', {bubbles:true}));
      pending.target.dispatchEvent(new MouseEvent('mousedown', {bubbles:true}));
      pending.target.focus();
      pending.target.click();
      queueMicrotask(() => { state.replay = false; });
    }
  };
  window.addEventListener('brain-lore-dirty', () => { state.dirty = true; });
  window.addEventListener('brain-lore-saved', complete);
  window.addEventListener('brain-lore-discarded', complete);
  window.addEventListener('brain-lore-save-failed', () => {
    freezeEditor(false);
    setBusy(false);
    dialog.querySelector('.guard-message').textContent = '保存に失敗しました。入力は保持しています。修正して再度保存してください。';
  });
  document.addEventListener('input', event => {
    if (event.target.closest('.brain-lore-dirty-field')) state.dirty = true;
  }, true);
  document.addEventListener('change', event => {
    if (event.target.closest('.brain-lore-dirty-field')) state.dirty = true;
  }, true);
  const intercept = event => {
    if (!state.dirty || state.replay || dialog.contains(event.target)) return;
    const navigation = event.target.closest('.brain-lore-navigation, .tab-nav button, [role="tab"], #brain-preset-select');
    if (!navigation) return;
    if (state.saving) {
      event.preventDefault();
      event.stopImmediatePropagation();
      return;
    }
    if (event.type === 'keydown' && !['Enter',' ','ArrowDown','ArrowUp'].includes(event.key)) return;
    event.preventDefault();
    event.stopImmediatePropagation();
    if (!dialog.open) {
      state.pending = { target: event.target };
      dialog.querySelector('.guard-message').textContent = '移動する前に保存しますか？';
      dialog.showModal();
    }
  };
  for (const event of ['pointerdown','mousedown','click','keydown']) document.addEventListener(event, intercept, true);
  dialog.addEventListener('cancel', event => {
    if (state.busy) { event.preventDefault(); return; }
    state.pending = null;
  });
  dialog.addEventListener('click', event => {
    const action = event.target.dataset.action;
    if (!action) return;
    if (action === 'cancel') {
      state.pending = null;
      dialog.close();
      return;
    }
    setBusy(true);
    const selector = action === 'save' ? '#brain-lore-save' : '#brain-lore-discard';
    const button = document.querySelector(selector);
    if (button) {
      state.replay = true;
      button.click();
      state.replay = false;
    } else {
      setBusy(false);
    }
  });
}

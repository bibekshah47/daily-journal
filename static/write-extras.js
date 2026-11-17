/* Write and Edit page extras: starting templates (Write page only) and focus mode.
   Loaded with defer after journal-editor.js, so window.journalEditor is ready when
   Quill is available. Without it, everything works on the plain textarea. */
(() => {
    const textarea = document.getElementById('journal');
    if (!textarea) return;

    function focusEditor() {
        if (window.journalEditor) window.journalEditor.focus();
        else textarea.focus();
    }


    // ---------- Templates ----------

    const ENTITIES = { '&amp;': '&', '&lt;': '<', '&gt;': '>', '&quot;': '"', '&#39;': "'" };

    // Template HTML -> plain text for the textarea fallback, plus where the
    // cursor goes: the end of the first line that is still empty ("1. ", "- ", "").
    function templateText(html) {
        const lines = [];
        let current = null;
        let list = null;
        let number = 0;
        html.split(/(<\/?[a-z0-9]+>)/i).forEach(part => {
            const tag = /^<(\/?)([a-z0-9]+)>$/i.exec(part);
            if (!tag) {
                if (part && current !== null) current += part.replace(/&[#a-z0-9]+;/gi, e => ENTITIES[e] || e);
                return;
            }
            const [, closing, name] = tag;
            if (!closing && (name === 'ol' || name === 'ul')) { list = name; number = 0; }
            else if (closing && (name === 'ol' || name === 'ul')) list = null;
            else if (!closing && name === 'h3') {
                if (lines.length) lines.push('');   // blank line between sections
                current = '';
            } else if (!closing && name === 'p') current = '';
            else if (!closing && name === 'li') current = list === 'ol' ? `${++number}. ` : '- ';
            else if (closing && current !== null) { lines.push(current); current = null; }
        });
        const marker = /^(\d+\. |- )?$/;
        let cursor = 0;
        for (let i = 0; i < lines.length; i++) {
            if (i > 0 && marker.test(lines[i])) { cursor += lines[i].length; break; }
            cursor += lines[i].length + 1;
        }
        const text = lines.join('\n');
        return { text, cursor: Math.min(cursor, text.length) };
    }

    function insertPlain(html) {
        const { text, cursor } = templateText(html);
        textarea.value = text;
        textarea.focus();
        textarea.setSelectionRange(cursor, cursor);
        // Same event as typing: updates the word count and schedules the autosave.
        textarea.dispatchEvent(new Event('input', { bubbles: true }));
    }

    function applyTemplate(template) {
        if (window.journalEditor) window.journalEditor.loadHTML(template.html);
        else insertPlain(template.html);
    }

    const picker = document.getElementById('template-picker');
    const data = document.getElementById('writing-templates');
    if (picker && data) {
        let templates = [];
        try {
            templates = JSON.parse(data.textContent);
        } catch (error) {
            console.error(error);
        }
        const byKey = Object.fromEntries(templates.map(template => [template.key, template]));
        const dialog = document.getElementById('template-dialog');
        let pending = null;

        // Only offered while the entry is empty.
        function syncPicker() {
            picker.hidden = textarea.value.trim() !== '';
        }

        function choose(template) {
            if (!textarea.value.trim()) {
                applyTemplate(template);
                return;
            }
            if (dialog && typeof dialog.showModal === 'function') {
                pending = template;
                document.getElementById('template-dialog-name').textContent = template.name;
                dialog.showModal();
            } else if (confirm(`Replace what you've written with the ${template.name} template?`)) {
                applyTemplate(template);
            }
        }

        picker.querySelectorAll('[data-template]').forEach(button => {
            button.addEventListener('click', () => {
                const template = byKey[button.dataset.template];
                if (template) choose(template);
            });
        });

        if (dialog) {
            document.getElementById('template-cancel').addEventListener('click', () => dialog.close());
            document.getElementById('template-replace').addEventListener('click', () => {
                const template = pending;
                pending = null;
                dialog.close();
                if (template) applyTemplate(template);
            });
            dialog.addEventListener('close', () => { pending = null; });
        }

        textarea.addEventListener('input', syncPicker);
        // "Discard draft" empties the textarea without an input event.
        const discard = document.getElementById('discard-draft');
        if (discard) discard.addEventListener('click', syncPicker);
        syncPicker();
    }


    // ---------- Focus mode ----------

    const FOCUS_KEY = 'my-journal-focus-mode';
    const body = document.body;
    const toggle = document.getElementById('focus-toggle');
    const label = document.getElementById('focus-toggle-label');

    function isFocusMode() {
        return body.classList.contains('focus-mode');
    }

    function setFocusMode(on, { save = true, moveFocus = true } = {}) {
        if (on) body.classList.add('focus-mode');
        else body.classList.remove('focus-mode');
        if (toggle) {
            toggle.setAttribute('aria-pressed', on ? 'true' : 'false');
            toggle.title = on ? 'Exit focus (Esc)' : 'Focus (Ctrl+Shift+F)';
        }
        if (label) label.textContent = on ? 'Exit focus' : 'Focus';
        if (save) {
            try {
                localStorage.setItem(FOCUS_KEY, on ? 'on' : 'off');
            } catch (error) {
                // Private windows may block storage; focus mode still works for this page.
            }
        }
        // Keep typing where you were: the title stays focused, anything else
        // (the button, or a field focus mode hides) hands focus to the editor.
        if (moveFocus) {
            const active = document.activeElement;
            if (!active || active.id !== 'title') focusEditor();
        }
    }

    let remembered = false;
    try {
        remembered = localStorage.getItem(FOCUS_KEY) === 'on';
    } catch (error) {
        remembered = false;
    }
    setFocusMode(remembered, { save: false, moveFocus: false });

    if (toggle) toggle.addEventListener('click', () => setFocusMode(!isFocusMode()));

    document.addEventListener('keydown', event => {
        const key = (event.key || '').toLowerCase();
        if ((event.ctrlKey || event.metaKey) && event.shiftKey && key === 'f') {
            event.preventDefault();
            setFocusMode(!isFocusMode());
        } else if (key === 'escape' && isFocusMode() && !event.defaultPrevented &&
                   !document.querySelector('dialog[open]')) {
            // Quill's link box and pickers handle Escape themselves (defaultPrevented).
            setFocusMode(false);
        }
    });

    window.journalExtras = { templateText, setFocusMode, isFocusMode };
})();

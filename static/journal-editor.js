/* Progressive enhancement: the original textarea remains usable without Quill. */
(() => {
    const textarea = document.getElementById('journal');
    const container = document.getElementById('journal-editor');
    if (!textarea || !container || !window.Quill) return;

    const form = textarea.form;
    const htmlInput = document.getElementById('journal-html');
    const formatInput = document.getElementById('editor-format');
    const highlightColors = ['#fff2b2', '#d9ead3', '#d9eaf7', '#eadcf4', '#fce1d6'];
    let editor;
    try {
        const background = Quill.import('attributors/style/background');
        background.whitelist = highlightColors;
        Quill.register(background, true);
        container.hidden = false;
        editor = new Quill(container, {
            theme: 'snow',
            placeholder: textarea.placeholder || 'Write your thoughts here...',
            formats: ['bold', 'italic', 'underline', 'header', 'list', 'blockquote', 'link', 'background'],
            modules: {
                toolbar: [
                    ['bold', 'italic', 'underline'],
                    [{ background: [false, ...highlightColors] }],
                    [{ header: [2, 3, false] }],
                    [{ list: 'ordered' }, { list: 'bullet' }],
                    ['blockquote', 'link'], ['clean']
                ]
            }
        });
        const initialHTML = document.getElementById('initial-journal-html').innerHTML.trim();
        if (initialHTML) {
            editor.setContents(editor.clipboard.convert({ html: initialHTML }), 'silent');
        } else {
            editor.setText(textarea.value, 'silent');
        }
        const draft = window.restoredJournalDraft;
        // Drafts are untrusted local data: accept text operations, never embeds.
        if (draft && draft.version === 2 && draft.editorDelta &&
            Array.isArray(draft.editorDelta.ops) &&
            draft.editorDelta.ops.every(op => typeof op.insert === 'string')) {
            editor.setContents(draft.editorDelta, 'silent');
        }
    } catch (error) {
        console.error('Rich text editor unavailable; using plain text', error);
        container.hidden = true;
        const toolbar = container.previousElementSibling;
        if (toolbar && toolbar.classList.contains('ql-toolbar')) toolbar.remove();
        return;
    }

    textarea.hidden = true;
    textarea.required = false;
    const editable = container.querySelector('.ql-editor');
    editable.setAttribute('role', 'textbox');
    editable.setAttribute('aria-multiline', 'true');
    editable.setAttribute('aria-labelledby', 'journal-label');
    editable.setAttribute('aria-required', 'true');
    document.querySelector('label[for="journal"]').addEventListener('click', () => editor.focus());

    function sync(notify = false) {
        textarea.value = editor.getText();
        htmlInput.value = editor.getSemanticHTML();
        formatInput.value = 'html';
        if (notify) textarea.dispatchEvent(new Event('input', { bubbles: true }));
    }
    window.journalEditor = {
        getDelta: () => editor.getContents(),
        clear: () => { editor.setText(''); sync(); }
    };
    sync();
    // Refresh word count without scheduling a new draft on page load.
    const count = textarea.value.trim() ? textarea.value.trim().split(/\s+/).length : 0;
    document.getElementById('word-count').textContent = count + (count === 1 ? ' word' : ' words');
    editor.on('text-change', () => sync(true));
    form.addEventListener('submit', event => {
        sync();
        if (!textarea.value.trim()) {
            event.preventDefault();
            event.stopImmediatePropagation();
            editable.setAttribute('aria-invalid', 'true');
            editor.focus();
            const status = document.getElementById('editor-error');
            status.hidden = false;
        }
    }, true);
    const error = document.createElement('p');
    error.id = 'editor-error';
    error.className = 'editor-error';
    error.setAttribute('role', 'alert');
    error.textContent = 'Please write something before saving.';
    error.hidden = true;
    container.after(error);
    editable.setAttribute('aria-describedby', error.id);
    editor.on('text-change', () => {
        if (editor.getText().trim()) {
            error.hidden = true;
            editable.removeAttribute('aria-invalid');
        }
    });
})();

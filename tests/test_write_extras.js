/* Node-only checks for static/write-extras.js: templates and focus mode,
   wired together with the real draft script and journal-editor.js. */
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const editorCode = fs.readFileSync('static/journal-editor.js', 'utf8');
const extrasCode = fs.readFileSync('static/write-extras.js', 'utf8');
const template = fs.readFileSync('templates/write.html', 'utf8');
const draftCode = [...template.matchAll(/<script>([\s\S]*?)<\/script>/g)][0][1];

const TEMPLATES = [
    {key: 'gratitude', name: 'Gratitude',
     html: "<h3>Three things I'm grateful for</h3><ol><li></li><li></li><li></li></ol><h3>Why they mattered</h3><p></p>"},
    {key: 'brain-dump', name: 'Brain dump',
     html: '<h3>Everything on my mind</h3><ul><li></li></ul><h3>What I can let go of</h3><p></p>'},
];

function setup({quill = true, initial = '', stored = {}, focusStored, storageFails = false, dialog = true, confirmAnswer = true} = {}) {
    const elements = {};
    let active = null;
    function element(id) {
        return elements[id] ||= {
            id, name: id, value: '', hidden: false, required: true, textContent: '', title: '',
            innerHTML: '', listeners: {}, attrs: {}, dataset: {}, open: false,
            classList: {contains: () => false},
            addEventListener(type, fn) { (this.listeners[type] ||= []).push(fn); },
            dispatchEvent(event) { (this.listeners[event.type] || []).forEach(fn => fn(event)); return true; },
            setAttribute(k, v) { this.attrs[k] = String(v); },
            removeAttribute(k) { delete this.attrs[k]; },
            querySelector() { return element('editable'); },
            querySelectorAll() { return []; },
            focus() { active = this; },
            setSelectionRange(start, end) { this.selection = [start, end]; },
            after() {}, checkValidity() { return true; }, reset() {}
        };
    }
    ['journal', 'journal-editor', 'journal-html', 'editor-format', 'initial-journal-html', 'word-count',
     'write-form', 'title', 'mood', 'tags', 'went_well', 'went_wrong', 'improve_tomorrow', 'save-button',
     'autosave-status', 'discard-draft', 'template-picker', 'writing-templates', 'template-cancel',
     'template-replace', 'template-dialog-name', 'focus-toggle', 'focus-toggle-label'].forEach(element);
    element('journal').value = initial;
    element('journal').form = element('write-form');
    element('write-form').dataset = {draftKey: 'my-journal-new-entry-draft-7'};
    element('writing-templates').textContent = JSON.stringify(TEMPLATES);
    const buttons = TEMPLATES.map(t => Object.assign(element('button-' + t.key), {dataset: {template: t.key}}));
    element('template-picker').querySelectorAll = () => buttons;
    if (dialog) {
        Object.assign(element('template-dialog'), {
            showModal() { this.open = true; },
            close() { this.open = false; this.dispatchEvent({type: 'close'}); }
        });
    }

    const bodyClasses = new Set();
    const body = {classList: {
        add: c => bodyClasses.add(c), remove: c => bodyClasses.delete(c), contains: c => bodyClasses.has(c)
    }};
    const storage = new Map(Object.entries(stored));
    if (focusStored) storage.set('my-journal-focus-mode', focusStored);
    const localStorage = storageFails
        ? {getItem() { throw new Error('blocked'); }, setItem() { throw new Error('blocked'); }, removeItem() {}}
        : {getItem: k => storage.has(k) ? storage.get(k) : null, setItem: (k, v) => storage.set(k, String(v)), removeItem: k => storage.delete(k)};

    let instance;
    class FakeQuill {
        static import() { return {}; }
        static register() {}
        constructor() {
            instance = this; this.text = '\n'; this.delta = {ops: [{insert: '\n'}]}; this.handlers = [];
            // Like Quill 2.0.3 (checked in Chrome): one line per block, empty ones
            // included, except that a final unformatted newline (an empty <p>) is stripped.
            this.clipboard = {convert: ({html}) => {
                this.convertedHTML = html;
                const ops = [];
                html.replace(/<(h3|p|li)>(.*?)<\/\1>/g, (all, tag, inner) => {
                    ops.push(tag === 'p' ? {insert: inner + '\n'} : {insert: inner + '\n', attributes: {[tag]: true}});
                });
                const last = ops[ops.length - 1];
                if (last && !last.attributes && last.insert === '\n') ops.pop();
                return {ops, insert(text) { this.ops.push({insert: text}); return this; }};
            }};
        }
        setText(text, source) { this.setContents({ops: [{insert: text}]}, source); }
        setContents(delta, source) {
            this.delta = delta; this.text = delta.ops.map(op => op.insert).join('');
            this.lastSource = source;
            this.handlers.forEach(fn => fn());
        }
        getText() { return this.text; }
        getContents() { return this.delta; }
        getSemanticHTML() { return `<p>${this.text}</p>`; }
        on(event, fn) { this.handlers.push(fn); }
        focus() { this.focused = true; active = element('editable'); }
        setSelection(index, length) { this.selection = [index, length]; }
    }

    const timers = [];
    const docListeners = {};
    const confirms = [];
    const context = {
        document: {
            body,
            get activeElement() { return active; },
            getElementById: id => (id === 'template-dialog' && !dialog) ? null : element(id),
            querySelector: sel => sel === 'dialog[open]'
                ? (dialog && element('template-dialog').open ? element('template-dialog') : null)
                : element('label'),
            createElement: () => element('created-error'),
            addEventListener(type, fn) { (docListeners[type] ||= []).push(fn); }
        },
        localStorage,
        sessionStorage: {setItem() {}},
        window: {Quill: quill ? FakeQuill : undefined, addEventListener() {}},
        Quill: quill ? FakeQuill : undefined,
        console: {error() {}},
        Event: class { constructor(type, options = {}) { this.type = type; this.bubbles = !!options.bubbles; } },
        setTimeout: fn => { timers.push(fn); return timers.length; }, clearTimeout() {},
        confirm: message => { confirms.push(message); return confirmAnswer; },
        JSON, Object, Math, String
    };
    vm.createContext(context);
    vm.runInContext(draftCode, context);
    vm.runInContext(editorCode, context);
    vm.runInContext(extrasCode, context);

    const key = (k, mods = {}) => {
        const event = {type: 'keydown', key: k, ...mods, preventDefault() { this.defaultPrevented = true; }};
        (docListeners.keydown || []).forEach(fn => fn(event));
        return event;
    };
    const runTimers = () => timers.splice(0).forEach(fn => fn());
    return {elements, storage, bodyClasses, confirms, key, runTimers, buttons,
            click: k => element('button-' + k).dispatchEvent({type: 'click'}),
            get editor() { return instance; }, get active() { return active; }};
}

// ---------- Templates: empty editor ----------
let x = setup();
assert.equal(x.elements['template-picker'].hidden, false);              // shown while empty
x.click('gratitude');
assert.equal(x.editor.lastSource, 'user');                               // a real edit, so it autosaves
assert.equal(x.editor.convertedHTML, TEMPLATES[0].html);
// The closing empty paragraph that convert() strips is put back.
assert.equal(x.editor.getText(), "Three things I'm grateful for\n\n\n\nWhy they mattered\n\n");
assert.deepEqual(x.editor.selection, ["Three things I'm grateful for\n".length, 0]);   // first empty line
assert.equal(x.editor.focused, true);
assert.equal(x.elements['template-picker'].hidden, true);                // hidden once there's text
assert.equal(x.elements['template-dialog'].open, false);                 // no question for an empty editor
x.runTimers();                                                           // the normal draft autosave
const saved = JSON.parse(x.storage.get('my-journal-new-entry-draft-7'));
assert.equal(saved.content, "Three things I'm grateful for\n\n\n\nWhy they mattered\n\n");
assert.equal(saved.version, 2);
// Emptying the editor brings the line back.
x.editor.setText('\n');
assert.equal(x.elements['template-picker'].hidden, false);
// So does "Discard draft" (which clears without an input event in the fallback).
x.click('brain-dump');
assert.equal(x.elements['template-picker'].hidden, true);
x.elements['discard-draft'].dispatchEvent({type: 'click'});
assert.equal(x.elements['template-picker'].hidden, false);

// ---------- Templates: existing text asks first ----------
x = setup({initial: 'Something I already wrote'});
assert.equal(x.elements['template-picker'].hidden, true);
x.click('brain-dump');
assert.equal(x.elements['template-dialog'].open, true);
assert.equal(x.elements['template-dialog-name'].textContent, 'Brain dump');
assert.equal(x.editor.getText(), 'Something I already wrote');           // untouched until Replace
x.elements['template-cancel'].dispatchEvent({type: 'click'});
assert.equal(x.elements['template-dialog'].open, false);
assert.equal(x.editor.getText(), 'Something I already wrote');
x.elements['template-replace'].dispatchEvent({type: 'click'});            // stale Replace does nothing
assert.equal(x.editor.getText(), 'Something I already wrote');
x.click('brain-dump');
x.elements['template-replace'].dispatchEvent({type: 'click'});
assert.equal(x.elements['template-dialog'].open, false);
assert.equal(x.editor.getText(), 'Everything on my mind\n\nWhat I can let go of\n\n');
assert.deepEqual(x.editor.selection, ['Everything on my mind\n'.length, 0]);

// Without <dialog> support the same question goes through confirm().
x = setup({initial: 'Keep me', dialog: false, confirmAnswer: false});
x.click('gratitude');
assert.deepEqual(x.confirms, ["Replace what you've written with the Gratitude template?"]);
assert.equal(x.editor.getText(), 'Keep me');

// ---------- Templates: textarea fallback ----------
x = setup({quill: false});
assert.equal(x.elements.journal.hidden, false);
x.click('gratitude');
const plain = "Three things I'm grateful for\n1. \n2. \n3. \n\nWhy they mattered\n";
assert.equal(x.elements.journal.value, plain);
assert.deepEqual(x.elements.journal.selection, [plain.indexOf('1. ') + 3, plain.indexOf('1. ') + 3]);
assert.equal(x.active, x.elements.journal);
assert.equal(x.elements['word-count'].textContent, '11 words');           // the textarea's input handler ran
assert.equal(x.elements['template-picker'].hidden, true);
x.runTimers();
assert.equal(JSON.parse(x.storage.get('my-journal-new-entry-draft-7')).content, plain);
x = setup({quill: false});
x.click('brain-dump');
assert.equal(x.elements.journal.value, 'Everything on my mind\n- \n\nWhat I can let go of\n');
assert.deepEqual(x.elements.journal.selection, [24, 24]);
x = setup({quill: false, initial: 'Typed already', confirmAnswer: true});
x.click('brain-dump');                                                    // asks via the dialog
assert.equal(x.elements.journal.value, 'Typed already');
x.elements['template-replace'].dispatchEvent({type: 'click'});
assert.equal(x.elements.journal.value, 'Everything on my mind\n- \n\nWhat I can let go of\n');

// ---------- Focus mode ----------
x = setup();
const toggle = x.elements['focus-toggle'];
assert.equal(x.bodyClasses.has('focus-mode'), false);
assert.equal(toggle.attrs['aria-pressed'], 'false');
toggle.focus();
toggle.dispatchEvent({type: 'click'});
assert.equal(x.bodyClasses.has('focus-mode'), true);
assert.equal(toggle.attrs['aria-pressed'], 'true');
assert.equal(x.elements['focus-toggle-label'].textContent, 'Exit focus');
assert.equal(x.storage.get('my-journal-focus-mode'), 'on');
assert.equal(x.active, x.elements.editable);                              // keyboard focus goes to the editor
toggle.dispatchEvent({type: 'click'});
assert.equal(x.bodyClasses.has('focus-mode'), false);
assert.equal(toggle.attrs['aria-pressed'], 'false');
assert.equal(x.elements['focus-toggle-label'].textContent, 'Focus');
assert.equal(x.storage.get('my-journal-focus-mode'), 'off');
// Ctrl+Shift+F and Cmd+Shift+F toggle; Esc exits.
let event = x.key('F', {ctrlKey: true, shiftKey: true});
assert.equal(event.defaultPrevented, true);
assert.equal(x.bodyClasses.has('focus-mode'), true);
x.key('f', {metaKey: true, shiftKey: true});
assert.equal(x.bodyClasses.has('focus-mode'), false);
x.key('f', {ctrlKey: true});                                              // without Shift: not ours
assert.equal(x.bodyClasses.has('focus-mode'), false);
x.key('F', {metaKey: true, shiftKey: true});
x.elements.title.focus();
x.key('Escape');
assert.equal(x.bodyClasses.has('focus-mode'), false);
assert.equal(x.active, x.elements.title);                                  // typing in the title stays there
assert.equal(x.storage.get('my-journal-focus-mode'), 'off');
x.key('Escape');                                                          // Esc outside focus mode: nothing
assert.equal(x.bodyClasses.has('focus-mode'), false);
// Esc that Quill (link box, pickers) or an open dialog already handles doesn't exit.
x.key('F', {ctrlKey: true, shiftKey: true});
x.key('Escape', {defaultPrevented: true});
assert.equal(x.bodyClasses.has('focus-mode'), true);
x.elements['template-dialog'].open = true;
x.key('Escape');
assert.equal(x.bodyClasses.has('focus-mode'), true);

// The setting is remembered, and missing storage never breaks the page.
x = setup({focusStored: 'on'});
assert.equal(x.bodyClasses.has('focus-mode'), true);
assert.equal(x.elements['focus-toggle'].attrs['aria-pressed'], 'true');
assert.equal(x.active, null);                                             // no focus stealing on load
x = setup({storageFails: true});
assert.equal(x.bodyClasses.has('focus-mode'), false);
x.elements['focus-toggle'].dispatchEvent({type: 'click'});
assert.equal(x.bodyClasses.has('focus-mode'), true);
// Textarea fallback: focus mode focuses the textarea.
x = setup({quill: false});
x.elements['focus-toggle'].dispatchEvent({type: 'click'});
assert.equal(x.active, x.elements.journal);

console.log('PASS: templates fill an empty editor, ask before replacing, plain-text fallback, autosave, picker visibility, and focus mode (class, aria-pressed, shortcuts, Esc, localStorage).');

/* Node-only integration checks for editor/form and existing draft event wiring. */
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const editorCode = fs.readFileSync('static/journal-editor.js', 'utf8');
const template = fs.readFileSync('templates/write.html', 'utf8');
const draftCode = [...template.matchAll(/<script>([\s\S]*?)<\/script>/g)][0][1];
const KEY = 'my-journal-new-entry-draft-7';   // the server puts the user id in the key
function setup({stored, initial = '', html = '', fail = false, key = KEY, storage: shared} = {}) {
    const elements = {};
    function element(id) {
        return elements[id] ||= {
            id, name: id, value: '', hidden: false, required: true,
            innerHTML: '', listeners: {}, attrs: {}, classList: {contains: () => false},
            addEventListener(type, fn, capture) {
                (this.listeners[type] ||= []).push({fn, capture: !!capture});
            },
            dispatchEvent(event) {
                for (const {fn} of this.listeners[event.type] || []) fn(event);
            },
            setAttribute(k, v) { this.attrs[k] = v; },
            removeAttribute(k) { delete this.attrs[k]; },
            querySelector() { return element('editable'); },
            after() {}, checkValidity() { return true; }, reset() {}
        };
    }
    ['journal','journal-editor','journal-html','editor-format','initial-journal-html','word-count','write-form','title','mood','tags','went_well','went_wrong','improve_tomorrow','save-button','autosave-status','discard-draft','daily-reflection'].forEach(element);
    element('journal').value = initial;
    element('journal').form = element('write-form');
    element('write-form').dataset = {draftKey: key};
    element('initial-journal-html').innerHTML = html;
    const storage = shared || new Map(stored ? [[key, JSON.stringify(stored)]] : []);
    const session = new Map();
    let instance, backgroundFormat;
    class FakeQuill {
        static import() { return {}; }
        static register(format) { backgroundFormat = format; }
        constructor(container, options) {
            assert(options.formats.includes("background"));
            assert(options.modules.toolbar.some(group => group.some(item => item.background && item.background.includes("#fff2b2"))));
            assert.deepEqual(Array.from(backgroundFormat.whitelist), ["#fff2b2", "#d9ead3", "#d9eaf7", "#eadcf4", "#fce1d6"]);
            if (fail) throw new Error('Unavailable');
            instance = this; this.text = '\n'; this.delta = {ops: [{insert:'\n'}]}; this.handlers = [];
            this.clipboard = {convert: ({html}) => ({ops: [{insert: html.replace(/<[^>]*>/g, '') + '\n', attributes: {bold:true}}]})};
        }
        setText(text) { this.setContents({ops:[{insert:text}]}); }
        setContents(delta) { this.delta = delta; this.text = delta.ops.map(op => op.insert).join(''); this.handlers.forEach(fn => fn()); }
        getText() { return this.text; }
        getContents() { return this.delta; }
        getSemanticHTML() { return `<p>${this.text}</p>`; }
        on(event, fn) { this.handlers.push(fn); }
        focus() { this.focused = true; }
    }
    const context = {
        document: {getElementById:element,querySelector:()=>element('label'),createElement:()=>element('created-error')},
        localStorage: {getItem:k=>storage.get(k),setItem:(k,v)=>storage.set(k,v),removeItem:k=>storage.delete(k)},
        sessionStorage: {setItem:(k,v)=>session.set(k,v)},
        window: {Quill:FakeQuill,addEventListener(){}}, Quill:FakeQuill,
        lucide:{createIcons(){}}, console:{error(){}},
        Event:class {constructor(type){this.type=type;}},
        setTimeout:()=>1,clearTimeout(){},confirm:()=>true
    };
    vm.createContext(context);
    vm.runInContext(draftCode, context);
    vm.runInContext(editorCode, context);
    return {context,elements,storage,session,get editor(){return instance},save:()=>vm.runInContext('saveDraft()', context)};
}
let x = setup({stored:{content:'Old plain draft',went_well:'Win'}});
assert.equal(x.editor.getText(),'Old plain draft');
assert.equal(x.elements.went_well.value,'Win');
assert.equal(x.elements.tags.value,'');   // drafts saved before tags existed
assert.equal(x.elements.journal.hidden,true);
x.editor.setContents({ops:[{insert:'Formatted draft\n',attributes:{bold:true,background:"#fff2b2"}}]});
x.elements.tags.value = 'work, family';
x.save();
let draft = JSON.parse(x.storage.get(KEY));
assert.equal(draft.version,2);
assert.equal(draft.tags,'work, family');
assert.equal(draft.editorDelta.ops[0].attributes.bold,true);
assert.equal(draft.content,'Formatted draft\n');
x = setup({stored:draft});
assert.equal(x.elements.tags.value,'work, family');
assert.equal(x.editor.getContents().ops[0].attributes.bold,true);
assert.equal(x.editor.getContents().ops[0].attributes.background,'#fff2b2');
x.elements['discard-draft'].dispatchEvent({type:'click'});
assert.equal(x.editor.getText(),'');
assert.equal(x.elements.tags.value,'');
assert(!x.storage.has(KEY));
// Tags alone are worth keeping as a draft.
x = setup();
x.elements.tags.value = 'ideas';
x.save();
assert.equal(JSON.parse(x.storage.get(KEY)).tags,'ideas');
// Two people on one computer: each has their own draft key and never sees the other's draft.
const browser = new Map();
x = setup({storage: browser, key: 'my-journal-new-entry-draft-7'});
x.editor.setText('Seven private words');
x.elements.title.value = 'Seven';
x.save();
assert(browser.has('my-journal-new-entry-draft-7'));
x = setup({storage: browser, key: 'my-journal-new-entry-draft-8'});
assert.equal(x.editor.getText(), '');                   // nothing of user 7's draft
assert.equal(x.elements.title.value, '');
assert.equal(x.elements['discard-draft'].hidden, true);
x.elements.title.value = 'Eight';
x.save();
assert.equal(JSON.parse(browser.get('my-journal-new-entry-draft-7')).title, 'Seven');
assert.equal(JSON.parse(browser.get('my-journal-new-entry-draft-8')).title, 'Eight');
assert(!browser.has('my-journal-new-entry-draft'));        // the old shared key is never used
x = setup({storage: browser, key: 'my-journal-new-entry-draft-7'});
assert.equal(x.elements.title.value, 'Seven');
// Without a key the page keeps no drafts at all rather than falling back to a shared one.
const keyless = new Map();
x = setup({storage: keyless, key: ''});
x.elements.title.value = 'Nowhere';
x.save();
assert.equal(keyless.size, 0);
x = setup({stored:{content:'Wrong'},initial:'Server answer',html:'<p>Server formatted</p>'});
assert.equal(x.editor.getText(),'Server formatted\n');
x = setup({initial:'Literal <b>text</b>'});
assert.equal(x.editor.getText(),'Literal <b>text</b>');
x = setup({fail:true,initial:'Fallback'});
assert.equal(x.elements.journal.hidden,false);
assert.equal(x.elements.journal.required,true);
assert.equal(x.elements['journal-editor'].hidden,true);

// ---------- Tag chips (script from templates/_macros.html) ----------
const macros = fs.readFileSync('templates/_macros.html', 'utf8');
const tagCode = macros.match(/macro tag_field_script\(\) %}\s*<script>([\s\S]*?)<\/script>/)[1];
function tagSetup(initial = '', suggestions = []) {
    function node(tag) {
        return {
            tagName: tag, children: [], attrs: {}, listeners: {}, value: '', textContent: '', hidden: false,
            addEventListener(type, fn) { (this.listeners[type] ||= []).push(fn); },
            dispatchEvent(event) { event.target ||= this; (this.listeners[event.type] || []).forEach(fn => fn(event)); return !event.defaultPrevented; },
            setAttribute(k, v) { this.attrs[k] = v; },
            append(...nodes) { this.children.push(...nodes); },
            replaceChildren(...nodes) { this.children = nodes; },
            focus() { focused = this; }
        };
    }
    let focused = null;
    const form = node('form');
    const box = node('div');
    const els = {tags: node('input'), 'tag-input': node('input'), 'tag-chips': node('ul'), 'tag-hint': node('p'),
                 'tag-suggestions': Object.assign(node('datalist'), {options: suggestions.map(value => ({value}))})};
    els.tags.value = initial;
    els.tags.form = form;
    els['tag-input'].parentElement = box;
    const changes = [];
    els.tags.addEventListener('change', () => changes.push(els.tags.value));
    const context = {document: {getElementById: id => els[id], createElement: node},
                     Event: class { constructor(type) { this.type = type; } }, Array, String};
    vm.createContext(context);
    vm.runInContext(tagCode, context);
    const input = els['tag-input'];
    const key = k => { const e = {type: 'keydown', key: k, preventDefault() { this.defaultPrevented = true; }}; input.dispatchEvent(e); return e; };
    const type = (text, inputType = 'insertText') => { input.value = text; input.dispatchEvent({type: 'input', inputType}); };
    const chips = () => els['tag-chips'].children.map(li => li.children[0].textContent);
    return {els, input, key, type, chips, changes, form, get focused() { return focused; }};
}
let t = tagSetup('work, #Deep Work');
assert.deepEqual(t.chips(), ['#work', '#deep-work']);
assert.equal(t.els['tag-chips'].hidden, false);
t.type('Family Time');
const enter = t.key('Enter');
assert.equal(enter.defaultPrevented, true);          // Enter never submits the form
assert.equal(t.input.value, '');
assert.equal(t.els.tags.value, 'work, deep-work, family-time');
assert.deepEqual(t.changes, ['work, deep-work, family-time']);   // autosave / unsaved-changes hook
t.type('travel,');                                   // comma turns text into a chip
assert.equal(t.input.value, '');
assert.equal(t.els.tags.value, 'work, deep-work, family-time, travel');
t.type('work');                                      // duplicates are ignored
t.key('Enter');
assert.equal(t.els.tags.value, 'work, deep-work, family-time, travel');
// Each chip has an × button with a clear label.
const removeButton = t.els['tag-chips'].children[1].children[1];
assert.equal(removeButton.attrs['aria-label'], 'Remove tag deep-work');
assert.equal(removeButton.textContent, '×');
removeButton.dispatchEvent({type: 'click'});
assert.equal(t.els.tags.value, 'work, family-time, travel');
assert.equal(t.focused, t.input);
// Backspace on an empty box removes the last chip; with text it just edits.
t.type('');
t.key('Backspace');
assert.equal(t.els.tags.value, 'work, family-time');
t.type('ab');
assert.equal(t.key('Backspace').defaultPrevented, undefined);
assert.equal(t.els.tags.value, 'work, family-time');
// At most five tags; the hint says so.
t.type('a, b, c, d,');
assert.equal(t.els.tags.value, 'work, family-time, a, b, c');
assert.match(t.els['tag-hint'].textContent, /5 tags/);
t.type('');
t.key('Backspace');
assert.equal(t.els['tag-hint'].textContent, 'Press Enter to add. Up to 5.');
// Typed-but-unfinished text is kept when the form is saved.
t.type('last one');
t.form.dispatchEvent({type: 'submit'});
assert.equal(t.els.tags.value, 'work, family-time, a, b, last-one');
// Draft restore / discard sync chips from the hidden field.
t.els.tags.value = '';
t.els.tags.dispatchEvent({type: 'tags-sync'});
assert.deepEqual(t.chips(), []);
assert.equal(t.els['tag-chips'].hidden, true);
// Picking a datalist suggestion adds it straight away; typing the same text doesn't.
t = tagSetup('', ['reading']);
t.type('reading');
assert.equal(t.els.tags.value, '');
t.type('reading', 'insertReplacementText');
assert.equal(t.els.tags.value, 'reading');
assert.equal(t.input.value, '');

console.log('PASS: per-user draft keys, highlight palette, highlighted drafts, legacy drafts, Delta formatting roundtrip, reflections, tags in drafts, discard, validation precedence, literal text, textarea fallback, and tag chips.');

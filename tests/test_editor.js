/* Node-only integration checks for editor/form and existing draft event wiring. */
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const editorCode = fs.readFileSync('static/journal-editor.js', 'utf8');
const template = fs.readFileSync('templates/write.html', 'utf8');
const draftCode = [...template.matchAll(/<script>([\s\S]*?)<\/script>/g)][0][1];
function setup({stored, initial = '', html = '', fail = false} = {}) {
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
    ['journal','journal-editor','journal-html','editor-format','initial-journal-html','word-count','write-form','title','mood','went_well','went_wrong','improve_tomorrow','save-button','autosave-status','discard-draft','daily-reflection'].forEach(element);
    element('journal').value = initial;
    element('journal').form = element('write-form');
    element('initial-journal-html').innerHTML = html;
    const storage = new Map(stored ? [['my-journal-new-entry-draft', JSON.stringify(stored)]] : []);
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
assert.equal(x.elements.journal.hidden,true);
x.editor.setContents({ops:[{insert:'Formatted draft\n',attributes:{bold:true,background:"#fff2b2"}}]});
x.save();
let draft = JSON.parse(x.storage.get('my-journal-new-entry-draft'));
assert.equal(draft.version,2);
assert.equal(draft.editorDelta.ops[0].attributes.bold,true);
assert.equal(draft.content,'Formatted draft\n');
x = setup({stored:draft});
assert.equal(x.editor.getContents().ops[0].attributes.bold,true);
assert.equal(x.editor.getContents().ops[0].attributes.background,'#fff2b2');
x.elements['discard-draft'].dispatchEvent({type:'click'});
assert.equal(x.editor.getText(),'');
assert(!x.storage.has('my-journal-new-entry-draft'));
x = setup({stored:{content:'Wrong'},initial:'Server answer',html:'<p>Server formatted</p>'});
assert.equal(x.editor.getText(),'Server formatted\n');
x = setup({initial:'Literal <b>text</b>'});
assert.equal(x.editor.getText(),'Literal <b>text</b>');
x = setup({fail:true,initial:'Fallback'});
assert.equal(x.elements.journal.hidden,false);
assert.equal(x.elements.journal.required,true);
assert.equal(x.elements['journal-editor'].hidden,true);
console.log('PASS: highlight palette, highlighted drafts, legacy drafts, Delta formatting roundtrip, reflections, discard, validation precedence, literal text, and textarea fallback.');

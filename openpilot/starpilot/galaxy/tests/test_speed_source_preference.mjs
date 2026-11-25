import assert from "node:assert/strict"
import { validDocument } from "../web/js/onroad-layout.js"
const metadata = { profiles: Object.fromEntries(["large", "compact"].map(name => [name, {
  bounds: {x:0,y:0,width:100,height:100}, widgets:{}, reservedZones:[]
}])) }
const document = { version:17, palette:{cardFill:"#000000A6",cardBorder:"#C4CDD0B4",text:"#FFFFFFFF"},
  layouts:{large:{},compact:{}},widgetColors:{large:{},compact:{}},roadColors:{large:{},compact:{}} }
assert.equal(validDocument(document, metadata), true)
for (const flag of [true, false]) assert.equal(validDocument({...document,speedSources:flag},metadata),true)
for (const flag of [1, 0, null, "true", {}, []]) assert.equal(validDocument({...document,speedSources:flag},metadata),false)
for (const malformed of [null,undefined,[],1,"layout",{}]) assert.equal(validDocument(malformed,metadata),false)
assert.equal(validDocument({...document,unexpected:true},metadata),false)
assert.equal(validDocument({...document,layouts:null},metadata),false)
console.log("PASS speed source preference: absent/booleans/invalid flags/null/undefined/malformed documents")

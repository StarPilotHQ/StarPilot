import test from 'node:test';import assert from 'node:assert/strict';
import {supportTags,supportForum,nextReportTags,filterReportThreads} from './thread-tags.mjs';
test('tag edits preserve custom tags, enforce platform, mutually exclusive status and five-tag limit',()=>{
 const changed=nextReportTags([supportTags.iOS,supportTags.Open,supportTags.Closed,'custom'],'android','In Progress',['Galaxy']);
 assert.deepEqual(new Set(changed),new Set(['custom',supportTags.Android,supportTags['In Progress'],supportTags.Galaxy]));
 assert.throws(()=>nextReportTags(['custom'],'ios','Open',['Navigation','CarPlay','Galaxy']),/five/);
 assert.throws(()=>nextReportTags([],'ios','Fake',[]),/Invalid/);
});
test('closed threads disappear and reopened threads return, with two requests regardless of report count',async()=>{
 const old=globalThis.fetch;let closed=true,calls=0;
 globalThis.fetch=async url=>{calls++;return Response.json(url.endsWith('/channels/'+supportForum)?{guild_id:'1'}:{threads:[{id:'open',parent_id:supportForum,applied_tags:[]},{id:'changing',parent_id:supportForum,applied_tags:closed?[supportTags.Closed]:[]},{id:'foreign',parent_id:'other',applied_tags:[]}]});};
 try{const items=['open','changing','archived','foreign'].map(id=>({id}));assert.deepEqual((await filterReportThreads({DISCORD_BOT_TOKEN:'test'},items)).map(x=>x.id),['open']);assert.equal(calls,2);closed=false;assert.deepEqual((await filterReportThreads({DISCORD_BOT_TOKEN:'test'},items)).map(x=>x.id),['open','changing']);assert.equal(calls,4);
 globalThis.fetch=async()=>new Response('',{status:429});await assert.rejects(filterReportThreads({DISCORD_BOT_TOKEN:'test'},items),/429/);
 assert.deepEqual(await filterReportThreads({},items),items);
 }finally{globalThis.fetch=old;}
});

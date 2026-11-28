import test from 'node:test';
import assert from 'node:assert/strict';
import {botPost} from './bot-delivery.mjs';

const env={DISCORD_BOT_TOKEN:'fixture'};
const forum='1565235002734809168';
function capture(t){
  const sent=[];
  t.mock.method(globalThis,'fetch',async(url,opts)=>{
    const body=JSON.parse(opts.body);sent.push(body);
    return Response.json({id:'2',parent_id:url.split('/')[6],message:{id:'2',channel_id:'2'}});
  });
  return sent;
}

test('support forum starters keep plain text so the forum preview shows the reporter',async t=>{
  const sent=capture(t);
  const buttons=[{type:1,components:[{type:2,style:1,label:'Staff Actions',custom_id:'case:1:ios:menu'}]}];
  await botPost(env,forum,{thread_name:'iOS — Wi-Fi drops',content:'Report from <@1> (meta)\nReference: x\n\nDrops',components:buttons});
  assert.equal(sent[0].name,'🟠 iOS — Wi-Fi drops');
  assert.equal(sent[0].message.content,'Report from <@1> (meta)\nReference: x\n\nDrops');
  assert.equal(sent[0].message.flags,undefined);
  assert.deepEqual(sent[0].message.components,buttons);
});

test('long starters, replies and other forums still use the support card',async t=>{
  const sent=capture(t);
  await botPost(env,forum,{thread_name:'Long',content:'x'.repeat(2001)});
  await botPost(env,'123',{thread_name:'Other',content:'Report from <@1>'});
  await botPost(env,'456',{content:'Reply'});
  for(const body of sent){
    const message=body.message||body;
    assert.equal(message.flags,1<<15);
    assert.equal(message.content,undefined);
  }
});

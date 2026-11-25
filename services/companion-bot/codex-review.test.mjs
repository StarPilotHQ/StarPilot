import test from 'node:test';
import assert from 'node:assert/strict';
import {prepareReview,claimReview,reviewStorage,queueReview,reviewWorker,reviewStatus} from './codex-review.mjs';

const record={title:'lost nav',reference:'BC67EAFB-EDB5-4F15-BD20-C1F54B8A62EA',threadURL:'https://discord.com/channels/1/2/3'};
test('handoff uses the specified chat and can be claimed only once',async t=>{
  const stores=new Map();
  const env={DISCORD_WEBHOOK_URL:'https://discord.com/api/webhooks/111/public',
    DISCORD_PRIVATE_WEBHOOK_URL:'https://discord.com/api/webhooks/222/private',
    REPORTS:{idFromName:n=>n,get:n=>({fetch:(url,options)=>{
      if(!stores.has(n))stores.set(n,new Map());
      const rows=stores.get(n);
      const storage={get:k=>rows.get(k),put:(k,v)=>rows.set(k,v),delete:k=>rows.delete(k),setAlarm:()=>{},transaction:cb=>cb(storage)};
      return reviewStorage(storage,new URL(url).pathname.split('/').pop(),JSON.parse(options.body));
    }})}};
  let redirect=false;
  const request=t.mock.method(globalThis,'fetch',async (url,options)=>{
    assert.equal(options.redirect,'manual');
    if(redirect)return Response.redirect('https://example.com/',302);
    const pub=String(url).includes('/public/');
    return Response.json(pub?{id:'3',channel_id:'2',content:'Lost nav\nPrivate diagnostics: https://discord.com/channels/1/4/5'}:
      {id:'5',channel_id:'4',content:'Private report',attachments:[{filename:'report.txt',url:'https://cdn.discordapp.com/attachments/4/report.txt',size:12}]});
  });
  assert.equal((await prepareReview(env,record,'arbitrary')).status,400);
  for(const chat of ['',crypto.randomUUID()]){
    const {ticket}=await (await prepareReview(env,record,chat)).json();
    const result=await (await claimReview(env,ticket)).json();
    assert.equal(result.chat,chat);
    assert.equal(result.entries[0].attachments[0].name,'report.txt');
    assert.equal((await claimReview(env,ticket)).status,410);
  }
  const chat=crypto.randomUUID();
  const enqueued=await queueReview(env,record,chat);
  assert.equal(enqueued.status,202);
  const queued=await enqueued.json();
  assert.deepEqual(await (await queueReview(env,record,chat)).json(),queued);
  assert.deepEqual(await (await reviewStatus(env,queued.id)).json(),queued);
  const {job}=await (await reviewWorker(env,{action:'claim'})).json();
  assert.equal(job.id,queued.id);
  assert.equal((await (await reviewStatus(env,job.id)).json()).state,'delivering');
  assert.equal((await (await reviewWorker(env,{action:'claim'})).json()).job,null);
  assert.equal((await (await claimReview(env,job.ticket)).json()).chat,chat);
  assert.equal((await reviewWorker(env,{action:'finish',id:job.id,lease:crypto.randomUUID(),delivered:true})).status,409);
  await reviewWorker(env,{action:'finish',id:job.id,lease:job.lease,delivered:true});
  assert.equal((await (await reviewStatus(env,job.id)).json()).state,'delivered');
  redirect=true;
  const calls=request.mock.callCount();
  await assert.rejects(prepareReview(env,record,''),/unexpected redirect/);
  assert.equal(request.mock.callCount(),calls+1);
});

test('bot review reads the recorded private message, including on an older webhook thread',async t=>{
  const reads=[];
  const env={DISCORD_BOT_TOKEN:'test',REPORTS:{idFromName:n=>n,get:()=>({fetch:async url=>url.endsWith('/list')?Response.json([]):Response.json({ready:true})})}};
  t.mock.method(globalThis,'fetch',async(url,opts)=>{
    assert.equal(opts.headers.Authorization,'Bot test');
    reads.push(url);
    const [,channel,id]=url.match(/channels[/]([0-9]+)[/]messages[/]([0-9]+)$/);
    return Response.json({id,channel_id:channel,content:'Report',attachments:[]});
  });
  const result=await prepareReview(env,{...record,botPublic:true,botPrivateURL:'https://discord.com/channels/1/4/5'},'');
  assert.equal(result.status,200);
  assert.deepEqual(reads,['https://discord.com/api/v10/channels/2/messages/3','https://discord.com/api/v10/channels/4/messages/5']);
});

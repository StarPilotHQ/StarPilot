import test from 'node:test';
import assert from 'node:assert/strict';
import {driveObject,drivePage,issueDriveLink,sendDriveInvite} from './drive-logs.mjs';
import {supportForum} from './thread-tags.mjs';

function fixture(t){
  const records=new Map(), posts=[];
  const store={get:async k=>structuredClone(records.get(k)),put:async(k,v)=>records.set(k,structuredClone(v)),delete:async k=>records.delete(k),transaction:async cb=>cb(store),setAlarm:async()=>{}};
  const env={DISCORD_WEBHOOK_URL:'https://discord.com/api/webhooks/123/public',DISCORD_PRIVATE_WEBHOOK_URL:'https://discord.com/api/webhooks/456/private',DISCORD_PRIVATE_FORUM:'true',DISCORD_BOT_TOKEN:'test'};
  const run=(path,init)=>driveObject(new Request('https://reports'+path,init),store,env);
  env.REPORTS={idFromName:x=>x,get:()=>({fetch:(url,init)=>String(url).endsWith('/review/remember')?Response.json({saved:true}):driveObject(new Request(url,init),store,env)})};
  let rejectReceipt=false;
  t.mock.method(globalThis,'fetch',async (url,init)=>{
    const target=new URL(url);
    if(target.pathname==='/api/v10/channels/777')return Response.json({id:'777',name:'test thread',type:11,parent_id:supportForum,applied_tags:[]});
    if(target.pathname==='/api/webhooks/123/public/messages/777')return Response.json({id:'777',channel_id:'777'});
    if(target.pathname==='/api/v10/channels/777/messages'){
      assert.equal(init.headers.Authorization,'Bot test');
      const message=JSON.parse(init.body);
      const receipt=message.components[0]?.components?.[0]?.content?.startsWith('Drive logs received.');
      if(rejectReceipt && receipt){rejectReceipt=false;return new Response('',{status:429});}
      posts.push(!receipt?{invite:message}:{priv:false,body:init.body});
      return Response.json({id:'456',channel_id:'777'});
    }
    if(target.pathname==='/api/v10/channels/888/threads'){
      assert.equal(init.headers.Authorization,'Bot test');
      const {name,message}=JSON.parse(init.body.get('payload_json'));
      assert.match(name,/Drive logs/);assert.match(message.components[0].components[0].content,/Reference/);assert.ok(message.components[0].components.some(c=>c.type===13));
      posts.push({priv:true,body:init.body});
      return Response.json({id:'222',parent_id:'888',message:{id:'111',channel_id:'222'}});
    }
    const priv=target.pathname.endsWith('/private');
    if(init.method!=='POST')return Response.json({guild_id:'1',channel_id:priv?'888':supportForum});
    if(!priv && rejectReceipt){rejectReceipt=false;return new Response('',{status:429});}
    if(!priv&&typeof init.body==='string'&&JSON.parse(init.body).content.startsWith('Submit your drive logs privately:')){
      assert.equal(target.searchParams.get('thread_id'),'777');
      assert.equal(target.searchParams.get('wait'),'true');
      posts.push({invite:JSON.parse(init.body)});
      return Response.json({id:'456',channel_id:'777'});
    }
    posts.push({priv,body:init.body});
    if(!priv)assert.equal(target.searchParams.get('thread_id'),'777');
    return Response.json({id:'111',channel_id:priv?'222':'777'});
  });
  const form=()=>{const data=new FormData();data.set('route','https://connect.comma.ai/test/drive');data.set('note','private test note');data.set('time','6:49');return data;};
  const send=body=>run('/drive/send',{method:'POST',body});
  return {env,store,run,form,posts,send,rejectOnce:()=>{rejectReceipt=true;}};
}

test('issue a link, deliver private details and attach a segment, publish only a receipt',async t=>{
  const f=fixture(t);
  const {url}=await (await issueDriveLink(f.env,'777','https://example.com')).json();
  const token=new URL(url).pathname.split('/').pop();
  assert.match(token,/^[a-f0-9]{64}$/);
  assert.match(await (await drivePage(new Request(url),f.env,token)).text(),/private support forum/);
  const data=f.form();data.set('log',new Blob([new Uint8Array([40,181,47,253])]),'rlog.zst');
  assert.equal((await f.send(data)).status,200);
  assert.equal(f.posts.length,2);
  const [priv,pub]=f.posts;
  assert.ok(priv.priv);
  assert.match(await priv.body.get('files[0]').text(),/private test note/);
  assert.equal(priv.body.get('files[1]').name,'rlog.zst');
  const state=await f.store.get('drive');
  assert.equal(JSON.parse(pub.body).components[0].components[0].content,'Drive logs received. Reference: '+state.reference);
  await f.send(f.form());
  assert.equal(f.posts.length,2);
});
test('a retry delivers the receipt without duplicating the private post',async t=>{
  const f=fixture(t);
  await issueDriveLink(f.env,'777','https://example.com');
  f.rejectOnce();
  assert.equal((await f.send(f.form())).status,503);
  assert.equal((await f.send(f.form())).status,200);
  assert.equal(f.posts.filter(p=>p.priv).length,1);
});
test('reject arbitrary URLs, expired invitations and files with invalid compression',async t=>{
  const f=fixture(t);
  await issueDriveLink(f.env,'777','https://example.com');
  const data=f.form();data.set('route','https://other.example/test');
  assert.equal((await f.send(data)).status,400);
  const bad=f.form();bad.set('log',new Blob(['bad']),'qlog.zst');
  assert.equal((await f.send(bad)).status,400);
  await f.store.put('drive',{expires:0});
  assert.equal((await f.send(f.form())).status,410);
  assert.equal(f.posts.length,0);
});
test('R2 download requires the private download token and expires',async t=>{
  const f=fixture(t);let bytes;
  f.env.DRIVE_FILES={put:async(k,stream)=>{bytes=await new Response(stream).arrayBuffer();},get:async()=>({body:bytes})};
  await issueDriveLink(f.env,'777','https://example.com');
  const data=f.form();data.set('log',new Blob([new Uint8Array([40,181,47,253])]),'rlog.zst');
  await f.send(data);
  const state=await f.store.get('drive');
  assert.equal((await f.run('/drive/file/wrong')).status,404);
  const download=await f.run('/drive/file/'+state.downloadToken);
  assert.equal((await download.arrayBuffer()).byteLength,4);
  await f.store.put('drive',{...state,fileExpires:0});
  assert.equal((await f.run('/drive/file/'+state.downloadToken)).status,404);
});

test('Companion-Bot sends the drive link once to its original thread',async t=>{
  const f=fixture(t);
  const {url}=await (await issueDriveLink(f.env,'777','https://example.com')).json();
  const token=new URL(url).pathname.split('/').pop();
  assert.equal((await sendDriveInvite(f.env,'777',token)).status,200);
  assert.equal((await sendDriveInvite(f.env,'777',token)).status,200);
  assert.equal(f.posts.length,1);
  assert.equal(f.posts[0].invite.components[1].components[0].url,url);
  assert.deepEqual(f.posts[0].invite.allowed_mentions.parse,[]);
});

test('dashboard can issue a link for a saved report without bot access',async t=>{
  const f=fixture(t);
  delete f.env.DISCORD_BOT_TOKEN;
  const report={title:'Test report',threadURL:'https://discord.com/channels/1/777/777'};
  const response=await issueDriveLink(f.env,'777','https://example.com',report);
  assert.equal(response.status,200);
  const {url}=await response.json();
  const token=new URL(url).pathname.split('/').pop();
  assert.equal((await sendDriveInvite(f.env,'778',token)).status,409);
  assert.equal((await sendDriveInvite(f.env,'777',token)).status,200);
  assert.equal((await sendDriveInvite(f.env,'777',token)).status,200);
  assert.equal(f.posts.length,1);
});

for (const [kind,page] of [['controller','Controller'],['connection','Phone App'],['starport','StarPort / Car Screen']]) {
  test('bot posts the selected '+kind+' Galaxy instructions in the requesting thread',async t=>{
    const f=fixture(t);
    const {url}=await (await issueDriveLink(f.env,'777','https://example.com',{userID:'555',platform:'ios'},kind)).json();
    const token=new URL(url).pathname.split('/').pop();
    assert.deepEqual(await (await sendDriveInvite(f.env,'777',token)).json(),{sent:true});
    const {invite}=f.posts[0];
    assert.equal(invite.components[0].content,'<@555>');
    assert.equal(invite.flags,32768);
    assert.equal(invite.components[1].type,17);
    const [intro,divider,help]=invite.components[1].components;
    assert.ok(intro.content.includes('Logs & Diagnostics → Get help → '+page));
    assert.match(intro.content,/Post to, select “test thread” and tap Post report/);
    assert.equal(divider.type,14);
    assert.match(help.content,/Local Galaxy address/);
    assert.doesNotMatch(JSON.stringify(invite),/Get my code|Find report|Capture and send|Older Galaxy/i);
    assert.deepEqual(invite.allowed_mentions.users,['555']);
    assert.equal(invite.components[2].components[0].label,'Give feedback');
    assert.match(url,/[/]report[/]galaxy[/]fresh[/]/);
    assert.deepEqual(invite.allowed_mentions.parse,[]);
  });
}

import test from 'node:test';
import assert from 'node:assert/strict';
import {caseButtons,caseInteraction,caseAction} from './case-actions.mjs';
import {supportForum} from './thread-tags.mjs';

const env={REPORTS:{idFromName:v=>v,get:()=>({fetch:()=>Response.json({id:'6'})})},REPORT_MAINTAINER_ID:'2',ANDROID_REPORT_MAINTAINER_ID:'3',DISCORD_BOT_TOKEN:'test'};
const interaction=(command,actor)=>({type:3,guild_id:'1387432184121393333',member:{user:{id:actor},roles:actor==='3'?['6']:[]},channel_id:'4',data:{custom_id:'case:1:ios:'+command}});

test('reporter can add details and staff get a private action menu',async()=>{
  assert.equal(caseButtons('1','ios')[0].components.length,2);
  assert.equal((await (await caseInteraction(interaction('note','1'),env)).json()).type,9);
  assert.equal((await (await caseInteraction(interaction('menu','1'),env)).json()).data.components[0].components.length,1);
  assert.equal((await (await caseInteraction(interaction('menu','2'),env)).json()).data.components[0].components.length,1);
  const menu=await (await caseInteraction(interaction('menu','3'),env)).json();
  assert.equal(menu.data.flags,32832);
  assert.deepEqual(menu.data.components[0].components[1].components[0].options.slice(0,3).map(o=>o.value),['tags','working','assign']);
  assert.match((await (await caseInteraction(interaction('priority:5','2'),env)).json()).data.components[0].components[0].content,/original/);
});
test('priority is stored on the verified ticket and a repeated interaction posts once',async()=>{
  const data=new Map([['thread:4',{platform:'ios',title:'Original'}]]);
  const storage={get:k=>data.get(k),put:(k,v)=>data.set(k,v),transaction:fn=>fn(storage)};
  const prior=globalThis.fetch;
  const messages=[];
  globalThis.fetch=async(url,options)=>{
    if(options.method==='POST'){messages.push(JSON.parse(options.body));return Response.json({id:'8'});}
    return Response.json({id:'4',type:11,parent_id:supportForum,applied_tags:[]});
  };
  try{
    const action={action:'priority',channel:'4',platform:'ios',value:'0',actor:'2',interaction:'9'};
    await caseAction(storage,env,action);
    await caseAction(storage,env,action);
    assert.equal(data.get('thread:4').priority,'0');
    assert.equal(data.get('thread:4').title,'Original');
    assert.equal(messages.length,1);
    assert.deepEqual(messages[0].allowed_mentions.parse,[]);
  }finally{globalThis.fetch=prior;}
});

test('marking in progress preserves topics and assignment uses a Discord user picker',async()=>{
  const pick=interaction('pick:4','3');
  pick.data.values=['assign'];
  const picker=await (await caseInteraction(pick,env)).json();
  assert.equal(picker.data.flags,32832);
  assert.equal(picker.data.components[0].components[1].components[0].type,5);
  const prior=globalThis.fetch, patched=[];
  globalThis.fetch=async(url,options)=>{
    if(options.method==='PATCH'){patched.push(JSON.parse(options.body));return Response.json({});}
    if(options.method==='POST')return Response.json({id:'8'});
    return Response.json({id:'4',type:11,parent_id:supportForum,applied_tags:['1565236988913913887','42']});
  };
  const data=new Map([['thread:4',{platform:'ios'}]]);
  const store={get:k=>data.get(k),put:(k,v)=>data.set(k,v),transaction:fn=>fn(store)};
  try{
    await caseAction(store,env,{channel:'4',platform:'ios',action:'working',actor:'3',interaction:'11'});
    assert.deepEqual(patched,[{applied_tags:['42','1565237023265263657']}]);
  }finally{globalThis.fetch=prior;}
});

test('only the reporter can give feedback or mark a request fulfilled',async()=>{
  const open=async(command,actor)=> (await caseInteraction(interaction(command,actor),env)).json();
  assert.equal((await open('feedback:4','1')).type,9);
  assert.equal((await open('feedback:4','2')).data.flags,32832);
  assert.equal((await open('fulfilled:4','3')).data.flags,32832);
  assert.equal((await open('fulfilled:5','1')).data.flags,32832);
});

test('each reporter update puts working staff controls beneath the new card',async t=>{
  const data=new Map([['thread:4',{platform:'ios'}]]);
  const store={get:k=>data.get(k),put:(k,v)=>data.set(k,v),transaction:fn=>fn(store)};
  const posts=[];
  t.mock.method(globalThis,'fetch',async(url,options)=>{
    if(options.method==='POST'){posts.push(JSON.parse(options.body));return Response.json({id:'8'});}
    return Response.json({id:'4',type:11,parent_id:supportForum,applied_tags:[]});
  });
  for(const action of ['feedback','note']){
    await caseAction(store,env,{channel:'4',platform:'ios',user:'1',actor:'1',action,value:'Still happens',interaction:action});
    const post=posts.at(-1);
    assert.equal(post.flags,32768);
    assert.match(post.components[0].components[0].content,/Still happens/);
    assert.equal(post.components[1].components[1].custom_id,'case:1:ios:menu');
  }
});

test('closing archives after the status card and reopening restores the same thread',async t=>{
  const data=new Map([['thread:4',{platform:'ios'}]]);
  const store={get:k=>data.get(k),put:(k,v)=>data.set(k,v),transaction:fn=>fn(store)};
  const thread={id:'4',type:11,parent_id:supportForum,applied_tags:['42','1565236988913913887'],thread_metadata:{archived:false}};
  const events=[];
  let unfollowStatus=204;
  t.mock.method(globalThis,'fetch',async(url,options)=>{
    if(options.method==='DELETE'){
      assert.equal(url,'https://discord.com/api/v10/channels/4/thread-members/3');
      events.push('unfollow closer');
      return new Response(null,{status:unfollowStatus});
    }
    if(options.method==='PATCH'){
      const patch=JSON.parse(options.body);
      if(patch.applied_tags)thread.applied_tags=patch.applied_tags;
      if('archived' in patch)thread.thread_metadata.archived=patch.archived;
      events.push(patch);
    }
    if(options.method==='POST'){
      events.push(JSON.parse(options.body).components[0].accent_color);
      return Response.json({id:'8'});
    }
    return Response.json(thread);
  });
  const act=(action,value,id)=>caseAction(store,env,{channel:'4',user:'1',platform:'ios',actor:'3',action,value,interaction:id});
  await act('status','Closed','1');
  assert.deepEqual(events,[{applied_tags:['42','1565237052524466207']},0x3498db,'unfollow closer',{archived:true}]);
  assert.match((await (await act('working',null,'2')).json()).message,/closed or archived/);
  await act('status','Open','3');
  assert.deepEqual(events.slice(4),[{applied_tags:['42','1565236988913913887'],archived:false},0xe74c3c]);
  unfollowStatus=403;
  assert.match((await (await act('status','Closed','4')).json()).message,/archived.*could not unfollow you/);
  assert.equal(thread.thread_metadata.archived,true);
  await act('status','Open','5');
  events.length=0;
  await caseAction(store,env,{channel:'4',user:'1',platform:'ios',actor:'1',action:'fulfilled',interaction:'6'});
  assert.equal(events.includes('unfollow closer'),false);
  assert.equal(thread.thread_metadata.archived,true);
});

test('the overview is staff-only and reads archived tickets without posting',async t=>{
  const denied=await (await caseInteraction(interaction('overview:4','1'),env)).json();
  assert.match(denied.data.components[0].components[0].content,/Sage/);
  const data=new Map([['thread:4',{platform:'ios',priority:'0',assignee:'3'}],['latest-diagnostics:4','https://discord.com/channels/1/10/20']]);
  t.mock.method(globalThis,'fetch',async(url,options)=>{
    assert.equal(options.method,'GET');
    return Response.json({id:'4',type:11,parent_id:supportForum,applied_tags:[],thread_metadata:{archived:true}});
  });
  const summary=await (await caseAction({get:k=>data.get(k)},env,{channel:'4',platform:'ios',action:'overview'})).json();
  assert.match(summary.message,/archived/);
  assert.match(summary.message,/Priority: 0/);
  assert.match(summary.message,/channels\/1\/10\/20/);
});

test('asking a question pings only the reporter and offers feedback',async t=>{
  const data=new Map([['thread:4',{platform:'ios'}]]);
  const storage={get:k=>data.get(k),put:(k,v)=>data.set(k,v),transaction:fn=>fn(storage)};
  let message;
  t.mock.method(globalThis,'fetch',async(url,options)=>{
    if(options.method==='POST'){message=JSON.parse(options.body);return Response.json({id:'9'});}
    return Response.json({id:'4',type:11,parent_id:supportForum,applied_tags:[]});
  });
  await caseAction(storage,env,{channel:'4',platform:'ios',user:'1',actor:'3',action:'question',interaction:'123',value:'Which build?'});
  assert.deepEqual(message.allowed_mentions,{parse:[],users:['1']});
  assert.match(JSON.stringify(message.components),/Which build/);
  assert.match(JSON.stringify(message.components),/case:1:ios:feedback:4/);
});

test('deployment keeps the commit, testing adds the build, and the forum title follows the ticket lifecycle',async t=>{
  const records=new Map([['thread:4',{platform:'ios'}]]);
  const storage={get:key=>records.get(key),put:(key,val)=>records.set(key,val),transaction:fn=>fn(storage)};
  const thread={id:'4',type:11,parent_id:supportForum,name:'🟠 Sentry preview',applied_tags:[],thread_metadata:{}};
  const posts=[];
  t.mock.method(globalThis,'fetch',async(url,options)=>{
    if(options.method==='PATCH')Object.assign(thread,JSON.parse(options.body));
    if(options.method==='POST')posts.push(JSON.parse(options.body));
    return Response.json(thread);
  });
  const run=(action,extra={})=>caseAction(storage,env,{channel:'4',user:'1',actor:'3',platform:'ios',action,interaction:action,...extra});
  const commit='https://github.com/StarPilotHQ/StarPilot/commit/1234abc';
  await run('awaiting',{commit});
  assert.equal(thread.name,'⚪ Sentry preview');
  assert.deepEqual(posts.at(-1).allowed_mentions.users,[]);
  await run('testing',{build:'2.0 (332)',value:'Check the preview.'});
  assert.equal(thread.name,'🟣 Sentry preview');
  assert.deepEqual(posts.at(-1).allowed_mentions.users,['1']);
  assert.ok(JSON.stringify(posts.at(-1)).includes(commit));
  assert.ok(JSON.stringify(posts.at(-1)).includes('2.0 (332)'));
  await run('investigate');
  assert.equal(thread.name,'🔴 Sentry preview');
  await run('fulfilled',{actor:'1'});
  assert.equal(thread.name,'🟢 Sentry preview');
});

test('slash command opens only an ephemeral staff menu without posting to the ticket',async t=>{
  const {privateStaffCommand}=await import('./case-actions.mjs');
  const i={guild_id:'1387432184121393333',channel_id:'4',application_id:'9',token:'fixture',member:{roles:['6']}};
  const edits=[];
  t.mock.method(globalThis,'fetch',async(url,options)=>{
    if(options.method==='PATCH'){
      assert.ok(url.endsWith('/messages/@original'));
      edits.push(JSON.parse(options.body));
      return Response.json({});
    }
    assert.equal(options.method,'GET');
    if(url.endsWith('/channels/4'))return Response.json({id:'4',type:11,parent_id:supportForum,applied_tags:[]});
    assert.ok(url.endsWith('/channels/4/messages/4'));
    return Response.json({channel_id:'4',author:{id:'9'},components:caseButtons('1','ios')});
  });
  for(const roles of [['6'],[]]){
    i.member.roles=roles;
    let job;
    const deferred=await privateStaffCommand(i,env,{waitUntil:p=>{job=p;}}).json();
    assert.equal(deferred.data.flags,64);
    await job;
    assert.ok(edits.at(-1).flags & 64);
    assert.match(JSON.stringify(edits.at(-1)),roles.length?/case:1:ios:pick:4/:/requires the StarPilot Sage role/);
  }
});

test('a renamed, previously moved ticket opens its private menu even without Discord message history',async t=>{
  const {privateStaffCommand}=await import('./case-actions.mjs');
  const {referralStore}=await import('./referrals.mjs');
  const channel='1574999999999999999',reporter='1574000000000000000';
  const entries=new Map([['report:old',{destination:channel,reporter,platform:'ios',state:'moved'}]]);
  let lists=0;
  const store={get:k=>entries.get(k),put:(k,v)=>entries.set(k,v),list:()=>{lists++;return new Map(entries);}};
  const runtime={REPORTS:{idFromName:k=>k,get:k=>({fetch:(url,init)=>k==='companion-bot-setup'
    ?Response.json({id:'sage'}):referralStore(new URL(url).pathname,new Request(url,init),store,{})})}};
  const i={guild_id:'1387432184121393333',channel_id:channel,channel:{parent_id:supportForum},application_id:'9',token:'fixture',member:{roles:['sage']}};
  t.mock.method(globalThis,'fetch',async(url,o)=>{
    assert.equal(o.method,'PATCH');
    assert.ok(url.endsWith('/messages/@original'));
    const card=JSON.parse(o.body);
    assert.ok(card.flags & 64);
    assert.ok(JSON.stringify(card).includes('case:'+reporter+':ios:'+(i.data?'appTesting':'pick')+':'+channel));
    return Response.json({});
  });
  for(let n=0;n<2;n++){
    if(n===1)i.data={name:'test-app'};
    let done;
    assert.equal((await privateStaffCommand(i,runtime,{waitUntil:p=>{done=p;}}).json()).data.flags,64);
    await done;
  }
  assert.equal(lists,1);
});

test('old webhook tickets use their verified report reference or exact saved thread URL',async t=>{
  const {privateStaffCommand}=await import('./case-actions.mjs');
  const guild='1387432184121393333', channel='1574999999999999999', user='1574000000000000000';
  const ref='C80EA1A0-6ED0-4F9C-880C-E91C83F04049';
  const threadURL='https://discord.com/channels/'+guild+'/'+channel+'/'+channel;
  let readable=true, edits=0;
  const runtime={DISCORD_BOT_TOKEN:'test',REPORTS:{idFromName:k=>k,get:k=>({fetch:async(url,init)=>{
    if(k==='companion-bot-setup')return Response.json({id:'sage'});
    if(k==='driving-referral-review')return new Response(null,{status:404});
    const body=JSON.parse(init.body);
    if(k===user){
      assert.equal(body.reference,ref);
      return Response.json({threadURL});
    }
    assert.equal(k,'dashboard:ios');
    return Response.json(body.cursor?{reports:[{userID:user,platform:'ios',threadURL}],cursor:null}:
      {reports:[{userID:'3',platform:'ios',threadURL:threadURL.replace(channel,'123')}],cursor:'page2'});
  }})}};
  t.mock.method(globalThis,'fetch',async(url,opts)=>{
    if(opts.method==='PATCH'){
      assert.ok(url.endsWith('/messages/@original'));
      const card=JSON.parse(opts.body);
      assert.ok(card.flags & 64);
      assert.ok(JSON.stringify(card).includes('case:'+user+':ios:pick:'+channel));
      edits++;
      return Response.json({});
    }
    assert.equal(opts.method,'GET');
    if(url.endsWith('/channels/'+channel))return Response.json({id:channel,parent_id:supportForum,type:11,applied_tags:[]});
    if(!readable)return new Response(null,{status:403});
    return Response.json({channel_id:channel,author:{id:'2'},content:'Report from <@'+user+'> (Nate)\nReference: '+ref+'\n\nTesting'});
  });
  for(readable of [true,false]){
    let job;
    const ack=await privateStaffCommand({guild_id:guild,channel_id:channel,application_id:'9',member:{roles:['sage']}},runtime,{waitUntil:p=>{job=p;}}).json();
    assert.equal(ack.data.flags,64);
    await job;
  }
  assert.equal(edits,2);
});

test('comma testing adds the official branch instruction only to the comma testing request',async t=>{
  const {galaxyLogSteps,updateComma}=await import('./galaxy-log-request.mjs');
  for(const kind of ['connection','controller','starport']){
    const steps=galaxyLogSteps({kind,platform:'android'});
    assert.ok(steps[0].startsWith(updateComma));
    assert.match(steps.at(-1),/offline.*branch and version/);
  }
  const posts=[];
  t.mock.method(globalThis,'fetch',async(url,options)=>{
    if(options.method==='POST'){posts.push(JSON.parse(options.body));return Response.json({id:'8'});}
    return Response.json({id:'4',type:11,parent_id:supportForum,applied_tags:[]});
  });
  const records=new Map([['thread:4',{platform:'ios'}]]);
  const store={get:k=>records.get(k),put:(k,v)=>records.set(k,v),transaction:fn=>fn(store)};
  for(const selected of ['testing','commaTesting','appTesting']){
    const pick=interaction('pick:4','3');
    pick.data.values=[selected];
    const modal=(await (await caseInteraction(pick,env)).json()).data;
    assert.equal(modal.custom_id,'case:1:ios:'+selected+':4');
    await caseAction(store,env,{action:'testing',commaTesting:selected==='commaTesting',appTesting:selected==='appTesting',platform:'ios',channel:'4',user:'1',actor:'3',value:'Verify the fix.',build:'2.0 (332)',interaction:selected});
    const card=JSON.stringify(posts.at(-1));
    assert.equal(card.includes('https://testflight.apple.com/join/nfUDuEm4'),selected==='appTesting');
    assert.equal(card.includes(updateComma),selected==='commaTesting');
    assert.equal(card.includes('2.0 (332)'),selected!=='appTesting');
  }
});

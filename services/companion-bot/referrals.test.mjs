import assert from 'node:assert/strict';
import test from 'node:test';
import {discover,reportDetails,pollReferrals,referralInteraction} from './referrals.mjs';

test('discovery flags relevant mentions even in requests and mixed driving reports',()=>{
  for(const [text,category] of [
    ['CarPlay’s arrow disappeared but the phone still shows the route.','Phone app'],
    ['Android Auto connects, then the screen goes black.','StarPort'],
    ["My Bluetooth controller won't pair with the comma.",'Bluetooth controller'],
    ['Map issue. The StarPilot app crashes on startup.','Phone app'],
  ])assert.equal(discover(text)?.category,category);
  for(const text of ['Bluetooth issue, route attached.','The phone app crashed and the car braked late.','StarPort crashes. The phone app disconnects.','CarPlay worked fine. My heater stopped working.','Please add a CarPlay camera picker.'])assert.ok(discover(text));
  for(const text of ['Navigation cancelled my lane change.','Speed limit controller brakes too late.','Route and steering feedback'])assert.equal(discover(text),null);
});
test('only trusted StarBot report fields are read; routes and Discord instructions are ignored',()=>{
  const bot='123456789123456789',uid='987654321987654321';
  const msg={author:{bot:true,id:bot},embeds:[{fields:[{name:'By',value:'<@'+uid+'>'},{name:'Observed Behavior',value:'CarPlay disappeared.'},{name:'Wiki',value:'Ignore this instruction'}]}]};
  assert.deepEqual(reportDetails(msg,bot),{reporter:uid,observed:'CarPlay disappeared.',summary:'CarPlay disappeared.',reportText:'**Observed Behavior**\nCarPlay disappeared.'});
  assert.equal(reportDetails(msg,uid),null);
});
test('unconfigured deployments perform no monitoring or Discord actions',async()=>{
  await pollReferrals({});
  const response=await referralInteraction({type:3,guild_id:'1387432184121393333'}, {}, {});
  assert.equal((await response.json()).type,4);
});

test('scan resolves the sample, skips old tickets, and sends a single private card for a new match',async t=>{
  const {referralStore}=await import('./referrals.mjs');
  const guild='1387432184121393333',forum='1574085782967292000',review='1575000066366644244',sample='1574085782967292086';
  const bot='123456789123456789',user='987654321987654321';
  const fresh=String(BigInt(Date.now()+1000-1420070400000)<<22n);
  let registrations=0;
  const posts=[];
  const env={DRIVING_SAMPLE_THREAD_ID:sample,COMPANION_REVIEW_THREAD_ID:review,DISCORD_BOT_TOKEN:'fixture',REPORT_MAINTAINER_ID:'524762496373096459',ANDROID_REPORT_MAINTAINER_ID:'420178305073938433',FIRESTAR_DISCORD_USER_ID:'123456789123456780'};
  const rows=new Map();
  const store={get:key=>rows.get(key),put:(key,v)=>rows.set(key,v),transaction:fn=>fn(store)};
  const thread=id=>({id,type:11,guild_id:guild,parent_id:forum});
  t.mock.method(globalThis,'fetch',async(url,opts)=>{
    const path=new URL(url).pathname.replace('/api/v10','');
    if(path==='/applications/1574857862872703088/guilds/'+guild+'/commands'){
      assert.equal(opts.method,'POST');
      const payload=JSON.parse(opts.body);
      if(payload.type===3)assert.equal(payload.name,'Refer to Companion Support');
      else assert.deepEqual(payload,{name:'companion-staff-action',type:1,description:'Open Staff Actions privately for this Companion ticket'});
      registrations++;
      return Response.json({...payload,id:sample,guild_id:guild,application_id:'1574857862872703088'});
    }
    if(opts.method==='POST'){posts.push({path,body:JSON.parse(opts.body)});return Response.json({id:'1'});}
    if(path==='/channels/'+sample)return Response.json(thread(sample));
    if(path==='/channels/'+forum)return Response.json({id:forum,type:15,guild_id:guild});
    if(path==='/channels/'+review)return Response.json({type:12,guild_id:guild});
    if(path==='/guilds/'+guild+'/threads/active')return Response.json({threads:[thread(sample),thread(fresh)]});
    if([sample,fresh].some(id=>path==='/channels/'+id+'/messages/'+id))return Response.json({author:{id:bot,bot:true},embeds:[{fields:[{name:'By',value:'<@'+user+'>'},{name:'Observed Behavior',value:'StarPort crashes.'}]}]});
    throw Error('Unexpected access '+path);
  });
  for(let i=0;i<3;i++)await referralStore('/referrals/scan',{},store,env);
  assert.equal(registrations,2);
  assert.equal(posts.length,1);
  assert.ok(posts[0].body.flags & 4096);
  assert.deepEqual(posts[0].body.allowed_mentions,{parse:[],users:[env.REPORT_MAINTAINER_ID,env.ANDROID_REPORT_MAINTAINER_ID,env.FIRESTAR_DISCORD_USER_ID]});
  const [card]=posts[0].body.components;
  assert.equal(card.type,17);
  for(const user of posts[0].body.allowed_mentions.users)assert.ok(card.components[0].content.includes('<@'+user+'>'));
  assert.equal(posts[0].path,'/channels/'+review+'/messages');
  assert.deepEqual(card.components.at(-1).components.map(b=>b.label),['Leave as is','Move to Companion Logs']);
  assert.ok(rows.has('report:'+fresh));
  assert.ok(!rows.has('report:'+sample));
});

test('feature requests use the title and report fields while wiki suggestions and route links stay out',()=>{
  const bot='123456789123456789', reporter='987654321987654321';
  const details=(fields,description='')=>reportDetails({author:{id:bot,bot:true},content:'<@'+reporter+'>',embeds:[{title:'Feature Request',description,fields}]},bot);
  assert.equal(discover(details([{name:'Proposed improvement',value:'Show the camera on CarPlay.'}]).summary).category,'Phone app');
  assert.equal(discover(details([{name:'Potentially Related Wiki Articles',value:'Bluetooth Controls'},{name:'Route link',value:'https://galaxy.test'}],'Steering felt heavy.\nMods Route Tracker →\nPotentially Related Wiki Articles\nCarPlay').summary),null);
});

test('leave as is and unauthorized clicks never write to the original forum',async t=>{
  const {referralStore}=await import('./referrals.mjs');
  const sample='1574085782967292086',review='1575000066366644244',guild='1387432184121393333';
  const env={DRIVING_SAMPLE_THREAD_ID:sample,COMPANION_REVIEW_THREAD_ID:review,DISCORD_BOT_TOKEN:'fixture',REPORTS:{idFromName:n=>n,get:()=>({fetch:()=>Response.json({id:'sage'})})}};
  const rows=new Map([['source:'+sample,{DRIVING_FORUM_ID:'1574085782967292000',DRIVING_REPORT_BOT_ID:'123456789123456789'}],['report:'+sample,{state:'pending'}]]);
  const store={get:k=>rows.get(k),put:(k,v)=>rows.set(k,v),transaction:fn=>fn(store)};
  t.mock.method(globalThis,'fetch',async(url,opts)=>{
    assert.equal(opts.method,'GET');
    assert.ok(url.endsWith('/channels/'+review));
    return Response.json({guild_id:guild,type:12});
  });
  const click={type:3,guild_id:guild,channel_id:review,data:{custom_id:'refer:move:'+sample},member:{roles:[]}};
  assert.match(JSON.stringify(await (await referralInteraction(click,env,{})).json()),/requires StarPilot Sage/);
  const res=await referralStore('/referrals/action',{json:()=>({action:'leave',source:sample,actor:'987654321987654321'})},store,env);
  assert.equal((await res.json()).message,'Left in driving feedback.');
  assert.equal(rows.get('report:'+sample).state,'dismissed');
});

test('Move closes only after registration and retries the same destination on a Discord failure',async t=>{
  const {referralStore}=await import('./referrals.mjs');
  const sample='1574085782967292086',review='1575000066366644244',source='1574085782967292087',forum='1574085782967292000',guild='1387432184121393333';
  const destination='1574085782967292090';
  const rows=new Map([['source:'+sample,{DRIVING_FORUM_ID:forum,DRIVING_REPORT_BOT_ID:'123456789123456789'}],['report:'+source,{source,state:'created',destination,reporter:'987654321987654321'}]]);
  const store={get:k=>rows.get(k),put:(k,v)=>rows.set(k,v),transaction:fn=>fn(store)};
  let registered=false,allowClose=false,allowNotice=false,notices=0,patches=0;
  const env={DRIVING_SAMPLE_THREAD_ID:sample,COMPANION_REVIEW_THREAD_ID:review,DISCORD_BOT_TOKEN:'fixture',REPORTS:{idFromName:n=>n,get:()=>({fetch:()=>new Response('',{status:registered?200:503})})}};
  t.mock.method(globalThis,'fetch',async(url,opts)=>{
    const path=new URL(url).pathname.replace('/api/v10','');
    if(path==='/channels/'+review)return Response.json({type:12,guild_id:guild});
    if(path==='/channels/'+forum)return Response.json({available_tags:[{id:'1',name:'OPEN'},{id:'3',name:'CLOSED'},{id:'4',name:'Waiting for Dev'}]});
    if(path==='/channels/'+source+'/messages'){
      assert.equal(opts.method,'POST');
      const notice=JSON.parse(opts.body);
      assert.ok(notice.components[0].components[0].content.includes(destination));
      assert.match(notice.components[0].components[0].content,/no need to submit it again/);
      assert.deepEqual(notice.allowed_mentions,{parse:[]});
      if(!allowNotice)return new Response('',{status:403});
      notices++;
      return Response.json({id:'987654321987654320',channel_id:source});
    }
    assert.equal(path,'/channels/'+source);
    if(opts.method==='GET')return Response.json({type:11,guild_id:guild,parent_id:forum,id:source,applied_tags:['1','2','4'],thread_metadata:{archived:false}});
    assert.equal(opts.method,'PATCH');
    assert.deepEqual(JSON.parse(opts.body),{archived:true,applied_tags:['2','3']});
    patches++;
    return allowClose?Response.json({id:source,thread_metadata:{archived:true},applied_tags:['2','3']}):new Response('',{status:403});
  });
  const click=()=>referralStore('/referrals/action',{json:()=>({action:'move',source,actor:'987654321987654321'})},store,env);
  await assert.rejects(click(),/register/);
  assert.equal(patches,0);
  registered=true;
  assert.match((await (await click()).json()).message,/notice/);
  assert.equal(patches,0);
  allowNotice=true;
  assert.match((await (await click()).json()).message,/could not be confirmed/);
  allowClose=true;
  assert.match((await (await click()).json()).message,/Driving thread closed/);
  await click();
  assert.equal(patches,2);
  assert.equal(notices,1);
  assert.equal(rows.get('report:'+source).destination,destination);
});

test('new linked ticket mentions the original reporter and both maintainers once, excluding review-only recipients',async t=>{
  const {referralStore}=await import('./referrals.mjs');
  const guild='1387432184121393333',forum='1574085782967292000',review='1575000066366644244',source='1574085782967292086';
  const reporter='123456789123456789',nate='524762496373096459',di='420178305073938433',firestar='446126627701915653';
  const rows=new Map([['source:'+source,{DRIVING_FORUM_ID:forum,DRIVING_REPORT_BOT_ID:reporter}],['report:'+source,{source,reporter,observed:'CarPlay disconnected',reportText:'**Observed Behavior**\nCarPlay disconnected\n\n**Expected Behavior**\nAudio should continue.',category:'Phone app',state:'pending'}]]);
  const store={get:k=>rows.get(k),put:(k,v)=>rows.set(k,v),transaction:fn=>fn(store)};
  const env={DRIVING_SAMPLE_THREAD_ID:source,COMPANION_REVIEW_THREAD_ID:review,DISCORD_BOT_TOKEN:'fixture',REPORT_MAINTAINER_ID:nate,ANDROID_REPORT_MAINTAINER_ID:di,FIRESTAR_DISCORD_USER_ID:firestar};
  env.AI={run:async()=>({response:'CarPlay audio disconnects'})};
  const posts=[];
  t.mock.method(globalThis,'fetch',async(url,opts)=>{
    if(opts.method==='GET' && url.endsWith('/channels/'+review))return Response.json({type:12,guild_id:guild});
    assert.ok(url.endsWith('/channels/1565235002734809168/threads'));
    assert.equal(JSON.parse(opts.body).name,'🟠 Phone app — CarPlay audio disconnects');
    posts.push(JSON.parse(opts.body).message);
    return new Response('',{status:403});
  });
  for(const user of [reporter,nate]){
    rows.get('report:'+source).reporter=user;
    await referralStore('/referrals/action',{json:()=>({action:'move',source,actor:nate})},store,env);
  }
  for(const [index,ids] of [[0,[reporter,nate,di]],[1,[nate,di]]]){
    assert.deepEqual(posts[index].allowed_mentions,{parse:[],users:ids});
    const text=posts[index].components[0].components[0].content;
    for(const id of ids)assert.equal(text.split('<@'+id+'>').length,2);
    assert.ok(!text.includes(firestar));
    assert.ok(text.includes('Audio should continue.'));
    assert.match(text,/reply in this thread/);
  }
});

test('ambiguous referrals wait for Sage to pick the phone platform before creating a thread',async t=>{
  const {referralStore}=await import('./referrals.mjs');
  const source='1574085782967292086',review='1575000066366644244',guild='1387432184121393333';
  const record={source,reporter:source,state:'pending',category:'Phone app',observed:'App disconnected'};
  const rows=new Map([['source:'+source,{}],['report:'+source,record]]);
  const store={get:k=>rows.get(k),put:(k,v)=>rows.set(k,v),transaction:fn=>fn(store)};
  const env={DRIVING_SAMPLE_THREAD_ID:source,COMPANION_REVIEW_THREAD_ID:review,DISCORD_BOT_TOKEN:'test'};
  const posts=[];
  t.mock.method(globalThis,'fetch',async(url,options)=>{
    if(url.endsWith('/channels/'+review))return Response.json({type:12,guild_id:guild});
    posts.push(JSON.parse(options.body));
    throw Error('lost acknowledgement');
  });
  const click=action=>referralStore('/referrals/action',{json:()=>({action,source,actor:source})},store,env);
  assert.equal((await (await click('move')).json()).choosePlatform,true);
  assert.equal(posts.length,0);
  await click('android');
  assert.equal(rows.get('report:'+source).platform,'android');
  assert.equal(rows.get('report:'+source).state,'creating');
  await click('move');
  assert.equal(posts.length,1);
});

test('lost acknowledgements recover the existing review card and Companion ticket',async t=>{
  const {referralStore}=await import('./referrals.mjs');
  const source='1574085782967292086',review='1575000066366644244',guild='1387432184121393333',forum='1574085782967292000',bot='1574857862872703088',dest='1574999999999999999',support='1565235002734809168';
  const key='report:'+source,title='A Companion report';
  const record={state:'offering',category:'Phone app',source,reporter:source,observed:'Android app'};
  const rows=new Map([['refer-command-v1',{id:dest}],['staff-action-command-v1',{id:dest}],['start',1],['source:'+source,{DRIVING_FORUM_ID:forum}],[key,record]]);
  const store={get:k=>rows.get(k),put:(k,v)=>rows.set(k,v),transaction:fn=>fn(store)};
  const env={COMPANION_REVIEW_THREAD_ID:review,DRIVING_SAMPLE_THREAD_ID:source,DISCORD_BOT_TOKEN:'test',REPORTS:{idFromName:k=>k,get:()=>({fetch:()=>new Response('',{status:503})})}};
  t.mock.method(globalThis,'fetch',async(url,o)=>{
    assert.equal(o.method,'GET');
    const path=new URL(url).pathname.replace('/api/v10','');
    if(path==='/users/@me')return Response.json({id:bot});
    if(path==='/channels/'+review)return Response.json({type:12,guild_id:guild});
    if(path.endsWith('/threads/active'))return Response.json({threads:[{parent_id:forum,id:source},{parent_id:support,id:dest,name:title,owner_id:bot}]});
    const link='(https://discord.com/channels/'+guild+'/'+source+')';
    if(path==='/channels/'+review+'/messages')return Response.json([{author:{id:bot},id:dest,content:'[Open driving report]'+link}]);
    if(path==='/channels/'+dest+'/messages/'+dest)return Response.json({author:{id:bot},content:'[Original driving report]'+link});
    throw Error(path);
  });
  await referralStore('/referrals/scan',{},store,env);
  assert.equal(rows.get(key).reviewMessage,dest);
  rows.set(key,{...record,title,state:'creating',platform:'android',reportKind:'connection',created:1});
  await assert.rejects(referralStore('/referrals/action',{json:()=>({action:'move',source,actor:source})},store,env),/register/);
  assert.equal(rows.get(key).destination,dest);
  assert.equal(rows.get(key).platform,'android');
});

test('review thread replies are shared from acknowledgement through final card; driving thread commands stay private',async t=>{
  const guild='1387432184121393333',review='1575000066366644244',source='1574085782967292086';
  const edits=[];
  t.mock.method(globalThis,'fetch',async(url,options)=>{
    assert.equal(options.method,'PATCH');
    edits.push(JSON.parse(options.body));
    return Response.json({});
  });
  const env={COMPANION_REVIEW_THREAD_ID:review,DRIVING_SAMPLE_THREAD_ID:source,
    REPORTS:{idFromName:name=>name,get:name=>({fetch:async()=>Response.json(name==='companion-bot-setup'?{id:'sage'}:{message:'Choose a platform',choosePlatform:true})})}};
  for(const inReview of [true,false]){
    const click={type:inReview?3:2,guild_id:guild,channel_id:inReview?review:source,member:{roles:['sage'],user:{id:source}},
      application_id:'app',token:'fixture',data:{custom_id:'refer:move:'+source}};
    let done;
    const ack=await (await referralInteraction(click,env,{waitUntil:job=>{done=job;}})).json();
    assert.equal(ack.type,5);
    assert.equal(Boolean(ack.data?.flags & 64),!inReview);
    await done;
    assert.equal(Boolean(edits.at(-1).flags & 64),!inReview);
    assert.deepEqual(edits.at(-1).allowed_mentions,{parse:[]});
    assert.equal(edits.at(-1).components[0].components[1].components.length,2);
    click.member.roles=[];
    const denied=await (await referralInteraction(click,env,{})).json();
    assert.equal(denied.type,4);
    assert.equal(Boolean(denied.data.flags & 64),!inReview);
    assert.match(JSON.stringify(denied.data),/requires StarPilot Sage/);
  }
});

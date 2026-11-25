import {botPost,ticketTitle,ticketStates,messageText,privateCard,supportCard} from './bot-delivery.mjs';
import {caseButtons} from './case-actions.mjs';
import {discordTags,supportForum,supportTags} from './thread-tags.mjs';

const guild='1387432184121393333';
const command='Refer to Companion Support';
const object=env=>env.REPORTS.get(env.REPORTS.idFromName('driving-referral-review'));
const rules=[
  ['StarPort',/\b(?:star[ -]?port|android[ -]?auto)\b/i],
  ['Bluetooth controller',/\b(?:(?:bluetooth|bt|usb|wireless|game)[ -]?controllers?|gamepads?|dualsense|joy[ -]?cons?)\b/i],
  ['Phone app',/\b(?:car[ -]?play|galaxy|companion|apps?|phones?|iphones?|android|ios|bluetooth|bt|wi[ -]?fi|hotspots?|mdns|bonjour|sentry|tunnel)\b/i],
];
export function discover(summary){
  for(const [category,pattern] of rules){
    for(const line of summary.split('\n')){
      const index=line.search(pattern);
      if(index>=0)return {category,evidence:line.slice(Math.max(0,index-140),index+300).trim()};
    }
  }
  return null;
}

const id=value=>/^\d{17,20}$/.test(value||'');
const configured=env=>id(env.COMPANION_REVIEW_THREAD_ID) && id(env.DRIVING_SAMPLE_THREAD_ID);
async function resolveSource(store,env){
  const key='source:'+env.DRIVING_SAMPLE_THREAD_ID;
  let source=await store.get(key);
  if(!source){
    const path='/channels/'+env.DRIVING_SAMPLE_THREAD_ID;
    const thread=await discordTags(env,path);
    if(thread.guild_id!==guild || thread.type!==11 || !id(thread.parent_id) || thread.parent_id===supportForum)throw Error('Driving sample is outside the expected forum');
    const parent=await discordTags(env,'/channels/'+thread.parent_id);
    const msg=await discordTags(env,path+'/messages/'+thread.id);
    if(parent.type!==15 || parent.guild_id!==guild || !id(msg.author?.id) || !reportDetails(msg,msg.author.id))throw Error('Driving sample report fields unavailable');
    source={DRIVING_FORUM_ID:parent.id,DRIVING_REPORT_BOT_ID:msg.author.id};
    await store.put(key,source);
  }
  return {...env,...source};
}
async function staffDestination(env){
  const t=await discordTags(env,'/channels/'+env.COMPANION_REVIEW_THREAD_ID);
  if(t.guild_id!==guild || t.thread_metadata?.archived || t.thread_metadata?.locked)throw Error('Review thread unavailable');
  if(t.type===12)return;
  if(t.type===0){
    const everyone=t.permission_overwrites?.find(p=>p.id===guild && p.type===0);
    if(BigInt(everyone?.deny||0)&1024n)return;
    throw Error('Review channel must restrict View Channel');
  }
  if(t.type!==11 || t.parent_id===env.DRIVING_FORUM_ID || t.parent_id===supportForum)throw Error('Staff-only thread required');
  const parent=await discordTags(env,'/channels/'+t.parent_id);
  const everyone=parent.permission_overwrites?.find(p=>p.id===guild && p.type===0);
  if(!(BigInt(everyone?.deny||0)&1024n))throw Error('Review forum must restrict View Channel');
}
const urlFor=id=>'https://discord.com/channels/'+guild+'/'+id;
const clean=text=>String(text).replace(/https?:\/\/\S+/gi,'[link omitted]').replace(/<[@#][^>]+>/g,'[mention omitted]').slice(0,1800);
export function reportDetails(message,bot){
  if(message.author?.id!==bot || !message.author?.bot)return null;
  const embeds=message.embeds||[];
  const fields=embeds.flatMap(embed=>embed.fields||[]);
  const by=fields.find(f=>/^(by|reporter|submitted by|requested by)$/i.test(f.name.trim()))?.value;
  const reporter=/^<@!?(\d{17,20})>/.exec(by||message.content||'')?.[1];
  if(!reporter)return null;
  const metadata=/wiki|related article|assigned|route (?:link|tracker|id)|donation|reported by|^by$|^reporter$|^submitted by$|^requested by$/i;
  const body=text=>clean(String(text||'').split(/(?:potentially related wiki articles|mods route tracker|starpilot is free)/i)[0]);
  const parts=[...embeds.flatMap(embed=>[body(embed.title),body(embed.description)]),
    ...fields.filter(f=>!metadata.test(f.name)).map(f=>body(f.value))].filter(Boolean);
  const observed=body(fields.find(f=>/^observed behavio[u]?r$/i.test(f.name.trim()))?.value)||parts[0];
  const opening=body((message.content||'').replace(/^<@!?\d+>/,'').trim());
  const sections=[opening,...embeds.map(embed=>body(embed.description)),
    ...fields.filter(field=>!metadata.test(field.name)&&body(field.value))
      .map(field=>'**'+clean(field.name)+'**\n'+body(field.value))].filter(Boolean);
  return {reporter,observed,summary:[opening,...parts].filter(Boolean).join('\n'),reportText:sections.join('\n\n')};

}
async function offer(store,env,thread,manual=false){
  const key='report:'+thread.id;
  const existing=await store.get(key);
  if(existing)return existing.state==='ignored' && manual?offerIgnored():false;
  return offerIgnored();
  async function offerIgnored(){
    const msg=await discordTags(env,'/channels/'+thread.id+'/messages/'+thread.id);
    const details=reportDetails(msg,env.DRIVING_REPORT_BOT_ID);
    if(!details)return false;
    const title=clean(thread.name||'');
    const match=discover([title,details.summary].join('\n'));
    details.observed ||= title;
    if(!manual&&!match){await store.put(key,{state:'ignored'});return false;}
    const record={...details,source:thread.id,sourceTitle:title,state:'offering',attempted:Date.now(),category:match?.category||'Staff referral',evidence:match?.evidence||details.summary.slice(0,450)};
    const reserved=await store.transaction(async tx=>{
      const old=await tx.get(key);
      if(old && !(manual&&old.state==='ignored'))return false;
      await tx.put(key,record);return true;
    });
    if(!reserved)return false;
    return deliverReview(store,env,key,record);
  }
}

async function deliverReview(store,env,key,record){
  const buttons={type:1,components:[['leave','Leave as is',2],['move','Move to Companion Logs',1]].map(([action,label,style])=>({type:2,style,label,custom_id:'refer:'+action+':'+record.source}))};
  const reviewers=[...new Set([env.REPORT_MAINTAINER_ID,env.ANDROID_REPORT_MAINTAINER_ID,env.FIRESTAR_DISCORD_USER_ID].filter(id))];
  const text=['**Possible '+record.category+' report**',record.evidence,'','[Open driving report]('+urlFor(record.source)+')',
    reviewers.map(user=>'<@'+user+'>').join(' ')].filter(Boolean).join('\n');
  const card=supportCard('',text,[],[buttons]);
  const sent=await botPost(env,env.COMPANION_REVIEW_THREAD_ID,{...card,flags:card.flags|4096,allowed_mentions:{parse:[],users:reviewers},nonce:record.source,enforce_nonce:true});
  if(!sent.ok)throw Error('Review delivery unconfirmed');
  const receipt=await sent.json();
  if(!receipt.id)throw Error('Missing review receipt');
  await acknowledgeReview(store,key,receipt.id);
  return true;
}
async function acknowledgeReview(store,key,receipt){
  await store.transaction(async tx=>{
    const current=await tx.get(key);
    if(current?.state==='offering'||current?.state==='pending')
      await tx.put(key,{...current,state:'pending',reviewMessage:receipt});
  });
}
async function recoverReview(store,env,key,record){
  const bot=await discordTags(env,'/users/@me');
  let before=record.reviewBefore;
  const messages=await discordTags(env,'/channels/'+env.COMPANION_REVIEW_THREAD_ID+'/messages?limit=100'+(before?'&before='+before:''));
  const match=messages.find(m=>m.author?.id===bot.id&&messageText(m).includes('[Open driving report]('+urlFor(record.source)+')'));
  if(match)return acknowledgeReview(store,key,match.id);
  const oldest=messages.at(-1)?.id;
  if(messages.length===100&&BigInt(oldest)>BigInt(record.source)){
    await store.transaction(async tx=>{
      const current=await tx.get(key);
      if(current?.state===record.state&&!current.reviewMessage)await tx.put(key,{...current,reviewBefore:oldest});
    });
    return;
  }
  // Reserve the retry so overlapping scans cannot both resend the card.
  const reserved=await store.transaction(async tx=>{
    const current=await tx.get(key);
    if(!current||current.state!==record.state||current.reviewMessage||current.attempted>Date.now()-60000)return false;
    await tx.put(key,{...current,state:'offering',attempted:Date.now(),reviewBefore:null});
    return true;
  });
  if(reserved)await deliverReview(store,env,key,record);
}
async function ensureGuildCommand(store,env,key,definition){
  const saved=await store.get(key);
  if(saved?.id||saved?.retryAfter>Date.now())return;
  await store.put(key,{retryAfter:Date.now()+300000});
  try{
    const app='1574857862872703088';
    const response=await fetch('https://discord.com/api/v10/applications/'+app+'/guilds/'+guild+'/commands',{
      method:'POST',redirect:'manual',signal:AbortSignal.timeout(10000),
      headers:{Authorization:'Bot '+env.DISCORD_BOT_TOKEN,'Content-Type':'application/json'},
      body:JSON.stringify(definition)
    });
    if(!response.ok)throw Error('HTTP '+response.status);
    const registered=await response.json();
    if(!id(registered.id)||registered.name!==definition.name||registered.type!==definition.type||registered.guild_id!==guild||registered.application_id!==app)
      throw Error('Invalid command receipt');
    await store.put(key,{id:registered.id});
  }catch(error){console.warn('Discord referral command registration pending',error.message);}
}
export async function pollReferrals(env){
  if(!configured(env))return;
  const res=await object(env).fetch('https://reports/referrals/scan',{method:'POST'});
  if(!res.ok)throw Error('Referral scan unavailable');
}
export async function referralStore(path,request,store,env){
  if(path==='/referrals/ticket'){
    const {channel}=await request.json();
    if(!id(channel))return Response.json(null,{status:400});
    const key='destination:'+channel;
    const cached=await store.get(key);
    if(cached)return Response.json(cached);
    // Tickets moved before the destination index was added are keyed by their driving thread.
    let startAfter;
    do{
      const page=await store.list({prefix:'report:',limit:100,...(startAfter&&{startAfter})});
      for(const [source,record] of page){
        startAfter=source;
        if(record.destination===channel&&['moved','created'].includes(record.state)&&id(record.reporter)&&['android','ios'].includes(record.platform)){
          const ticket={reporter:record.reporter,platform:record.platform};
          await store.put(key,ticket);
          return Response.json(ticket);
        }
      }
      if(page.size<100)break;
    }while(startAfter);
    return Response.json(null,{status:404});
  }
  if(!configured(env))return Response.json({message:'Referral review is awaiting setup.'},{status:503});
  env=await resolveSource(store,env);
  await staffDestination(env);
  if(path.endsWith('/scan')){
    await ensureGuildCommand(store,env,'refer-command-v1',{name:command,type:3});
    await ensureGuildCommand(store,env,'staff-action-command-v1',{name:'companion-staff-action',type:1,description:'Open Staff Actions privately for this Companion ticket'});
    const start=await store.get('start');
    if(!start){await store.put('start',Date.now());return Response.json({ready:true});}
    const {threads}=await discordTags(env,'/guilds/'+guild+'/threads/active');
    const candidates=threads.filter(t=>t.parent_id===env.DRIVING_FORUM_ID && id(t.id) && Number(BigInt(t.id)>>22n)+1420070400000>=start);
    let examined=0;
    for(const t of candidates){
      const key='report:'+t.id;
      const record=await store.get(key);
      if(record && (!['offering','pending'].includes(record.state)||record.reviewMessage||record.attempted>Date.now()-60000))continue;
      if(examined++>=8)break;
      const retry='retry:'+t.id;
      if((await store.get(retry))>Date.now()){examined--;continue;}
      try{if(record)await recoverReview(store,env,key,record);else await offer(store,env,t);}catch(error){console.warn('Referral review deferred',t.id,error.name);}finally{await store.put(retry,Date.now()+300000);}
    }
    return Response.json({ok:true});
  }
  const {action:choice,source,actor}=await request.json();
  const selected=['ios','android'].includes(choice)?choice:null;
  const action=(selected||choice==='retry')?'move':choice;
  if(!id(source)||!id(actor))return Response.json({message:'Invalid referral.'});
  if(action==='manual'){
    const thread=await discordTags(env,'/channels/'+source);
    if(thread.guild_id!==guild||thread.parent_id!==env.DRIVING_FORUM_ID)return Response.json({message:'Use this command in a driving report thread.'});
    const offered=await offer(store,env,thread,true);
    return Response.json({message:offered?'Sent to staff review.':'Already reviewed or the original StarBot summary is unavailable.'});
  }
  const key='report:'+source;
  if(!['leave','move'].includes(action))return Response.json({message:'Unknown referral action.'});
  if(choice==='retry'){
    await store.transaction(async tx=>{
      const current=await tx.get(key);
      if(current?.state==='creating'&&current.retryAllowed)await tx.put(key,{...current,state:'pending',retryAllowed:false});
    });
  }
  const previous=await store.get(key);
  if(action==='move'&&previous?.destination)return finishMove(store,env,key,previous);
  if(action==='move'&&previous?.state==='creating')return recoverMove(store,env,key,previous);
  if(!['offering','pending'].includes(previous?.state))return Response.json({message:'This referral has already been handled.'});
  const reportKind=previous.category==='StarPort'?'starport':previous.category==='Bluetooth controller'?'controller':'connection';
  const title=previous.category+' — '+(previous.observed||'').replace(/\s+/g,' ').slice(0,68);
  const evidence=[previous.sourceTitle,previous.observed,previous.summary].join(' ');
  const apple=/\b(?:iphone[s]?|ios|car[ -]?play)\b/i.test(evidence);
  const android=/\bandroid\b/i.test(evidence);
  const platform=selected||previous.platform||(reportKind==='starport'?'android':apple===android?null:apple?'ios':'android');
  if(action==='move'&&!platform)return Response.json({message:'Choose the phone platform for this ticket.',choosePlatform:true});
  const record=await store.transaction(async tx=>{
    const current=await tx.get(key);
    if(!['pending','offering'].includes(current?.state))return null;
    await tx.put(key,{...current,state:action==='leave'?'dismissed':'creating',actor,platform,title,reportKind,created:Date.now()});
    return current;
  });
  if(!record)return Response.json({message:'Another staff action is already in progress.'});
  if(action==='leave')return Response.json({message:'Left in driving feedback.'});
  const tags=[supportTags.Open];
  if(reportKind==='controller'&&id(env.CONTROLLER_FORUM_TAG_ID))tags.push(env.CONTROLLER_FORUM_TAG_ID);
  else tags.push(supportTags[platform==='ios'?'iOS':'Android']);
  const recipients=[...new Set([record.reporter,env.REPORT_MAINTAINER_ID,env.ANDROID_REPORT_MAINTAINER_ID].filter(id))];
  const mentions=recipients.map(user=>'<@'+user+'>').join(' ');
  const original=record.reportText||record.summary||record.observed;
  const excerpt=original.length>3200?original.slice(0,3199)+'…':original;
  const card=supportCard('',mentions+'\n\n**'+record.category+' support**\nWe moved your report here from driving feedback so the Companion support team can help. We copied your report below. You can reply in this thread with updates.\n\n'+excerpt+'\n\n[Original driving report]('+urlFor(source)+')',caseButtons(record.reporter,platform));
  let receipt;
  try{receipt=await botPost(env,supportForum,{...card,thread_name:title,applied_tags:tags,allowed_mentions:{parse:[],users:recipients}});}
  catch{return Response.json({message:'Ticket delivery is uncertain. Choose Move again in a minute to check Discord.'});}
  if(!receipt.ok){
    if(receipt.status>=400&&receipt.status<500)await store.put(key,record);
    return Response.json({message:'Discord did not confirm the Companion ticket. Check the forum before retrying.'});
  }
  const destination=(await receipt.json()).channel_id;
  const moved={...record,actor,state:'created',destination,title,platform,reportKind,created:Date.now()};
  await store.put(key,moved);
  return finishMove(store,env,key,moved);
}

async function recoverMove(store,env,key,record){
  if(record.created>Date.now()-60000)return Response.json({message:'Discord is still confirming the ticket. Choose Move again in a minute.'});
  const bot=await discordTags(env,'/users/@me');
  const active=await discordTags(env,'/guilds/'+guild+'/threads/active');
  const title=record.title||record.category+' — '+record.observed.replace(/\s+/g,' ').slice(0,68);
  async function find(threads){
    for(const thread of threads){
      if(thread.parent_id!==supportForum||thread.owner_id!==bot.id||!(thread.name===title||thread.name===ticketTitle(title,ticketStates.new)))continue;
      const opener=await discordTags(env,'/channels/'+thread.id+'/messages/'+thread.id);
      if(opener.author?.id===bot.id&&messageText(opener).endsWith('[Original driving report]('+urlFor(record.source)+')'))return thread.id;
    }
  }
  let destination=await find(active.threads);
  if(!destination){
    const archive=await discordTags(env,'/channels/'+supportForum+'/threads/archived/public?limit=100');
    destination=await find(archive.threads);
    if(!destination&&archive.has_more&&!(Date.parse(archive.threads.at(-1)?.thread_metadata?.archive_timestamp)<(record.created||Number(BigInt(record.source)>>22n)+1420070400000)))return Response.json({message:'Delivery is uncertain. Staff should check archived Companion tickets before retrying.'});
  }
  if(!destination){
    await store.transaction(async tx=>{
      const current=await tx.get(key);
      if(current?.state==='creating'&&current.created===record.created)await tx.put(key,{...current,retryAllowed:true});
    });
    // A renamed ticket might exist. Never blindly repeat a forum creation.
    return Response.json({message:'No matching ticket was confirmed. Check the Companion forum for a renamed ticket. If none exists, choose Retry creation.',retryMove:true});
  }
  const recovered={...record,title,destination,state:'created',created:record.created||Date.now(),
    platform:record.platform||(record.category==='StarPort'||/android/i.test(record.observed)?'android':'ios'),
    reportKind:record.reportKind||(record.category==='StarPort'?'starport':record.category==='Bluetooth controller'?'controller':'connection')};
  await store.put(key,recovered);
  return finishMove(store,env,key,recovered);
}

async function finishMove(store,env,key,record){
  if(record.state!=='moved'){
    const owner=env.REPORTS.get(env.REPORTS.idFromName(record.reporter));
    const result=await owner.fetch('https://reports/register-referral',{method:'POST',body:JSON.stringify({
      id:record.destination,title:record.title,platform:record.platform,reportKind:record.reportKind,created:record.created,reporter:record.reporter
    })});
    if(!result.ok)throw Error('Could not register Companion ticket');
    await store.put('destination:'+record.destination,{reporter:record.reporter,platform:record.platform});
    await store.put(key,{...record,state:'moved'});
  }
  const link='[Open the Companion ticket]('+urlFor(record.destination)+')';
  if(record.sourceClosed)return Response.json({message:'Driving thread closed. '+link});
  try{
    const path='/channels/'+record.source;
    const thread=await discordTags(env,path);
    if(thread.id!==record.source || thread.type!==11 || thread.guild_id!==guild || thread.parent_id!==env.DRIVING_FORUM_ID)
      throw Error('Original driving thread could not be verified.');
    const forum=await discordTags(env,'/channels/'+thread.parent_id);
    const tags=forum.available_tags||[];
    const closed=tags.filter(tag=>tag.name.trim().toLowerCase()==='closed');
    if(closed.length>1)throw Error('The driving forum has multiple Closed tags.');
    const patch={archived:true};
    const remove=tags.filter(tag=>{
      const name=tag.name.trim().toLowerCase();
      return /^(?:[^a-z0-9]*)(?:waiting for dev(?:eloper)?s?)\s*$/.test(name)
        || (closed.length && ['open','in progress'].includes(name));
    }).map(tag=>tag.id);
    const applied=thread.applied_tags.filter(tag=>!remove.includes(tag));
    if(closed.length&&!applied.includes(closed[0].id))applied.push(closed[0].id);
    if(applied.length>5)throw Error('The driving thread has no room for its Closed tag.');
    if(applied.length!==thread.applied_tags.length||applied.some(tag=>!thread.applied_tags.includes(tag)))patch.applied_tags=applied;
    let postedNotice=false;
    if(!record.moveNotice){
      const notice=supportCard('','**Your report moved to Companion support**\nThe Companion support team handles this issue. We copied your report into their forum, so there is no need to submit it again. This driving thread is closing. Use the link below to see your report and reply to the team.\n\n'+link,[]);
      const sent=await botPost(env,record.source,{...notice,flags:notice.flags|4096,allowed_mentions:{parse:[]},nonce:'move-'+record.source,enforce_nonce:true});
      if(!sent.ok)return Response.json({message:'Companion ticket created. Discord did not confirm the notice in the original thread. Check Send Messages in Threads and retry Move. '+link});
      const receipt=await sent.json();
      if(!id(receipt.id)||receipt.channel_id!==record.source)throw Error('Missing move notice receipt');
      record={...record,state:'moved',moveNotice:receipt.id};
      await store.put(key,record);
      postedNotice=true;
    }
    if(postedNotice || !thread.thread_metadata?.archived || patch.applied_tags){
      const updated=await discordTags(env,path,patch);
      if(updated.id!==record.source || !updated.thread_metadata?.archived || updated.applied_tags?.some(tag=>remove.includes(tag)) || (closed.length&&!updated.applied_tags?.includes(closed[0].id)))
        throw Error('Discord did not confirm the close.');
    }
    await store.put(key,{...record,state:'moved',sourceClosed:true});
    return Response.json({message:'Driving thread closed. '+link});
  }catch{
    return Response.json({message:'Companion ticket created. Closing the driving thread could not be confirmed. Check the forum permissions and tags, then choose Move again to retry. '+link});
  }
}

export function isReferral(i){
  return (i.type===2 && i.data?.type===3 && i.data.name===command) || (i.type===3 && /^refer:(leave|move|retry|ios|android):\d{17,20}$/.test(i.data?.custom_id||''));
}
export async function referralInteraction(i,env,ctx){
  const inReview=configured(env)&&i.guild_id===guild&&i.channel_id===env.COMPANION_REVIEW_THREAD_ID;
  const card=(text,controls=[])=>inReview
    ? {...supportCard('',text,[],controls,0x5865f2),allowed_mentions:{parse:[]}}
    : privateCard(text,controls);
  const reply=text=>Response.json({type:4,data:card(text)});
  const manual=i.type===2;
  if(!configured(env)||i.guild_id!==guild||(!manual && i.channel_id!==env.COMPANION_REVIEW_THREAD_ID))return reply('Use the configured support review thread.');
  const settings=env.REPORTS.get(env.REPORTS.idFromName('companion-bot-setup'));
  const sage=await settings.fetch('https://reports/sage-role');
  if(!sage.ok || !i.member?.roles?.includes((await sage.json()).id))return reply('This action requires StarPilot Sage.');
  const [,action,source]=manual?['','manual',i.channel_id]:i.data.custom_id.split(':');
  ctx.waitUntil((async()=>{
    let controls=[];
    let text='Could not confirm this referral. Check the review thread.';
    try{
      const res=await object(env).fetch('https://reports/referrals/action',{method:'POST',body:JSON.stringify({action,source,actor:i.member.user.id})});
      const result=await res.json();
      text=result.message||text;
      const choices=result.choosePlatform?[['ios','iOS'],['android','Android']]:result.retryMove?[['retry','Retry creation']]:[];
      if(choices.length)controls=[{type:1,components:choices.map(([key,label])=>({type:2,style:2,label,custom_id:'refer:'+key+':'+source}))}];
    }finally{
      await fetch('https://discord.com/api/v10/webhooks/'+i.application_id+'/'+i.token+'/messages/@original',{method:'PATCH',headers:{'Content-Type':'application/json'},body:JSON.stringify({...card(text,controls),content:null,embeds:[]})});
    }
  })());
  return Response.json(inReview?{type:5}:{type:5,data:{flags:64}});
}

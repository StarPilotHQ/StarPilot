import {isReferral,referralInteraction} from './referrals.mjs';
import {galaxyLogSteps,routeSteps} from './galaxy-log-request.mjs';
import {caseInteraction,privateStaffCommand,reporterButtons} from './case-actions.mjs';
import {botPost,supportCard,privateCard} from './bot-delivery.mjs';
import {rememberDriveMessage,webhookMessage} from './codex-review.mjs';
import { getReportThread, supportTags, supportForum } from './thread-tags.mjs';

const day = 86400000;
const maxBytes = 24 * 1024 * 1024;
const reply = (data, status = 200) => Response.json(data, {status, headers:{'Cache-Control':'no-store'}});
const hex = bytes => Array.from(new Uint8Array(bytes), n => n.toString(16).padStart(2, '0')).join('');
function object(env, token) {
  const namespace = env.REPORTS;
  return namespace.get(namespace.idFromName('drive-log:' + token));
}

export async function issueDriveLink(env, threadID, origin, report, kind = "drive") {
  let title;
  if(!env.DISCORD_BOT_TOKEN && report){
    const [, , , channel, message]=new URL(report.threadURL).pathname.split('/');
    if(channel!==threadID)throw Error('Select the original thread.');
    await webhookMessage(env.DISCORD_WEBHOOK_URL,threadID,message);
    title=report.title;
  } else {
    const thread=await getReportThread(env,threadID);
    if(thread.thread_metadata?.archived||thread.thread_metadata?.locked||thread.applied_tags.includes(supportTags.Closed))
      return reply({error:'Select an open support thread.'},409);
    title=thread.name;
  }
  const token = hex(crypto.getRandomValues(new Uint8Array(32)));
  const saved = await object(env, token).fetch('https://reports/drive/issue', {
    method:'POST', body:JSON.stringify({threadID, token, origin, title, kind, userID:report?.userID, platform:report?.platform, expires:Date.now()+day, reference:crypto.randomUUID()})
  });
  if (!saved.ok) return saved;
  return reply({url:origin+(kind === 'drive' ? '/drive/' : '/report/galaxy/fresh/')+token});
}

export async function sendDriveInvite(env, threadID, token) {
  if (!/^[a-f0-9]{64}$/.test(token)) return reply({error:'Create a link first.'},400);
  return object(env,token).fetch('https://reports/drive/invite',{method:'POST',body:JSON.stringify({threadID})});
}

export async function startGalaxyCapture(env, origin, userID, kind) {
  const token = hex(crypto.getRandomValues(new Uint8Array(32)));
  const record = {token,origin,userID,kind,platform:kind==='starport'?'android':'ios',reference:crypto.randomUUID(),expires:Date.now()+day};
  const stored = await object(env,token).fetch('https://reports/drive/issue',{method:'POST',body:JSON.stringify(record)});
  if (!stored.ok) return stored;
  return new Response(null,{status:303,headers:{Location:'/report/galaxy/fresh/'+token,'Cache-Control':'no-store'}});
}

export async function freshRequest(env, token) {
  if (!/^[a-f0-9]{64}$/.test(token)) return null;
  const response = await object(env, token).fetch('https://reports/drive/fresh');
  return response.ok ? response.json() : null;
}

export function driveDownload(env, token, secret) {
  return object(env,token).fetch('https://reports/drive/file/'+secret);
}

function formPage() {
  return new Response(`<!doctype html><html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Submit drive logs</title>
<style>body{font:1rem/1.5 system-ui;margin:6vh auto;padding:1rem;max-width:32rem}label,input,textarea,button{display:block}label{margin-top:1rem}input,textarea{width:100%;box-sizing:border-box;padding:.55rem}button{margin-top:1.5rem;padding:.65rem 1rem}</style>
<h1>Submit drive logs</h1><p>${routeSteps} You can also attach a segment log. Your drive details go to the private support forum. Your support thread receives a receipt with a reference number.</p>
<form method="post" enctype="multipart/form-data"><label>Comma connect route link<input type="url" name="route" maxlength="1500" placeholder="https://connect.comma.ai/…"></label>
<label>Time into the drive<input name="time" maxlength="40" placeholder="6:49"></label><label>What happened?<textarea name="note" maxlength="1000"></textarea></label>
<label>Segment log (up to 24 MiB)<input type="file" name="log" accept=".zst"></label><button>Submit drive logs</button></form></html>`,
    {headers:{'Content-Type':'text/html;charset=utf-8','Cache-Control':'no-store','Referrer-Policy':'no-referrer','Content-Security-Policy':"default-src 'none'; style-src 'unsafe-inline'; form-action 'self'; base-uri 'none'; frame-ancestors 'none'"}});
}

export async function drivePage(request, env, token) {
  if (!['GET','POST'].includes(request.method)) return reply({error:'Method unavailable'},405);
  const target=object(env,token);
  if(request.method==='GET') {
    const status=await target.fetch('https://reports/drive/status');
    return status.ok ? formPage() : status;
  }
  const sent=await target.fetch(new Request('https://reports/drive/send',request));
  const data=await sent.json();
  return new Response(data.sent ? 'Drive logs received. Reference: '+data.reference : data.error, {
    status:sent.status, headers:{'Content-Type':'text/plain;charset=utf-8','Cache-Control':'no-store','Referrer-Policy':'no-referrer'}
  });
}

async function readForm(request) {
  if (!request.headers.get('content-type')?.startsWith('multipart/form-data')) throw Error('Expected a drive submission form.');
  const limit=maxBytes+16384;
  if (Number(request.headers.get('content-length'))>limit) throw Error('Segment log exceeds 24 MiB.');
  const reader=request.body?.getReader();
  if (!reader) throw Error('Empty submission.');
  let size=0;
  const stream=new ReadableStream({async pull(controller) {
    const {done,value}=await reader.read();
    if(done){controller.close();return;}
    size+=value.length;
    if(size>limit){await reader.cancel();controller.error(Error('Segment log exceeds 24 MiB.'));return;}
    controller.enqueue(value);
  }});
  const form=await new Response(stream,{headers:{'Content-Type':request.headers.get('content-type')}}).formData();
  const field=(name,max)=>{
    const value=form.get(name)??'';
    if(typeof value!=='string'||value.length>max)throw Error('Invalid '+name+' field.');
    return value.trim();
  };
  const route=field('route',1500), time=field('time',40), note=field('note',1000);
  if(route){
    let url;try{url=new URL(route);}catch{}
    if(url?.origin!=='https://connect.comma.ai'||url.username||url.password||url.pathname==='/')throw Error('Enter a comma connect route link.');
  }
  const file=form.get('log');
  if(file?.size){
    if(!/^(rlog|qlog)\.zst$/i.test(file.name)||file.size>maxBytes)throw Error('Choose rlog.zst or qlog.zst, up to 24 MiB.');
    if(hex(await file.slice(0,4).arrayBuffer())!=='28b52ffd')throw Error('Choose a Zstandard-compressed segment log.');
  }else if(!route)throw Error('Add a route link or a segment log.');
  const fileHash=file?.size ? hex(await crypto.subtle.digest('SHA-256',await file.arrayBuffer())) : '';
  const fingerprint=hex(await crypto.subtle.digest('SHA-256',new TextEncoder().encode(JSON.stringify([route,time,note,fileHash,file?.size?file.name:'']))));
  return {route,time,note,file,fingerprint};
}

async function postWebhook(raw,threadID,body) {
  const url=new URL(raw);
  if(url.origin!=='https://discord.com'||!/^\/api\/webhooks\/\d+\/[\w-]+$/.test(url.pathname))throw Error('Invalid webhook');
  url.search='';url.searchParams.set('wait','true');
  if(threadID)url.searchParams.set('thread_id',threadID);
  const options=body instanceof FormData?{body}:{headers:{'Content-Type':'application/json'},body:JSON.stringify(body)};
  return fetch(url,{...options,method:'POST',redirect:'manual',signal:AbortSignal.timeout(30000)});
}

export async function driveObject(request,storage,env) {
  const action=new URL(request.url).pathname;
  if(action==='/drive/issue'){
    const record=await request.json();
    record.downloadToken=hex(crypto.getRandomValues(new Uint8Array(32)));
    await storage.setAlarm(record.expires);
    await storage.put('drive',record);
    return reply({created:true});
  }
  const record=await storage.get('drive');
  if(action.startsWith('/drive/file/')){
    const token=action.slice('/drive/file/'.length);
    if(!record?.fileKey || token!==record.downloadToken || record.fileExpires<=Date.now())return reply({error:'File expired'},404);
    const stored=await env.DRIVE_FILES.get(record.fileKey);
    if(!stored)return reply({error:'File unavailable'},404);
    return new Response(stored.body,{headers:{'Content-Type':'application/octet-stream','Content-Disposition':'attachment; filename="'+record.fileName+'"','Cache-Control':'private, no-store','Referrer-Policy':'no-referrer','X-Content-Type-Options':'nosniff'}});
  }
  if(!record||record.expires<=Date.now())return reply({error:'This link expired. Request a new one.'},410);
  if(record.done)return action==='/drive/send'?reply({sent:true,reference:record.reference}):reply({error:'Drive logs already received. Reference: '+record.reference},410);
  if(action==='/drive/invite'){
    const {threadID}=await request.json();
    if(threadID!==record.threadID)return reply({error:'Select the original thread.'},409);
    if(await storage.get('postedInvite'))return reply({sent:true});
    if(!env.DISCORD_BOT_TOKEN && !env.DISCORD_WEBHOOK_URL)return reply({error:'Support thread posting is unavailable.'},503);
    if(env.DISCORD_BOT_TOKEN){
      const thread=await getReportThread(env,threadID);
      if(thread.thread_metadata?.archived||thread.thread_metadata?.locked||thread.applied_tags.includes(supportTags.Closed))
        return reply({error:'Thread is closed.'},409);
    }
    const reserved=await storage.transaction(async tx=>{
      if(await tx.get('postedInvite'))return 'sent';
      if(await tx.get('invitePending'))return 'pending';
      await tx.put('invitePending',true);
      return 'new';
    });
    if(reserved==='sent')return reply({sent:true});
    const unconfirmed=()=>reply({error:'Discord delivery is pending or unconfirmed. Open the thread to check before trying again.'},409);
    if(reserved==='pending')return unconfirmed();
    let result;
    try {
      const capture = ['connection','controller','starport'].includes(record.kind);
      const title={connection:'Phone app connection logs',controller:'Controller logs',starport:'StarPort logs'}[record.kind];
      const intro=capture?'**'+title+' requested**\n'+galaxyLogSteps(record).map((step,i)=>(i+1)+'. '+step).join('\n\n'):
        '**Drive logs requested**\n'+routeSteps+' Use the link below to send your logs privately.';
      const link=record.origin+'/drive/'+record.token;
      const help=capture?'If the app can’t connect, open Galaxy in your phone’s browser using the Local Galaxy address on the comma while connected to its Wi-Fi or hotspot.':'';
      const backup=capture && record.kind!=='starport'?'[Offline ZIP help]('+record.origin+'/report/galaxy/fresh/'+record.token+')':'';
      const reporter=/^\d{1,20}$/.test(record.userID) ? record.userID : null;
      const mention=reporter ? '<@'+reporter+'>' : '';
      const mentions={parse:[],users:reporter?[reporter]:[]};
      const linkRow={type:1,components:[{type:2,style:5,label:'Submit drive logs',url:link}]};
      const replies=reporter&&['ios','android'].includes(record.platform)?[reporterButtons(reporter,record.platform,threadID)]:[];
      if(env.DISCORD_BOT_TOKEN){
        result = await fetch('https://discord.com/api/v10/channels/'+threadID+'/messages',{
          method:'POST',redirect:'manual',signal:AbortSignal.timeout(15000),
          headers:{Authorization:'Bot '+env.DISCORD_BOT_TOKEN,'Content-Type':'application/json'},
          body:JSON.stringify({...supportCard(mention,intro,capture?replies:[linkRow,...replies],
            capture?[{type:14},{type:10,content:[help,backup].filter(Boolean).join('\n')}]:[]),allowed_mentions:mentions})
        });
      } else {
        result=await postWebhook(env.DISCORD_WEBHOOK_URL,threadID,{content:[mention,intro,capture?help:'<'+link+'>',backup].filter(Boolean).join('\n'),allowed_mentions:mentions});
      }
    } catch {
      return unconfirmed();
    }
    if(result.status>=400&&result.status<500&&result.status!==408){
      await storage.delete('invitePending');
      return reply({error:result.status===429?'Discord is rate limiting posts. Try again shortly.':'Discord rejected the post. Check that the support thread is open.'},result.status===429?429:503);
    }
    if(!result.ok)return unconfirmed();
    const message=await result.json().catch(()=>null);
    if(message?.channel_id!==threadID||!/^\d+$/.test(message.id))return unconfirmed();
    await storage.put('postedInvite',message.id);
    await storage.delete('invitePending');
    return reply({sent:true});
  }
  if (action === '/drive/fresh' && ['controller','connection','starport'].includes(record.kind)) {
    const {userID, platform, threadID, reference, title, kind} = record;
    return reply({userID, platform, threadID, reference, title, kind});
  }
  if (record.kind && record.kind !== 'drive') return reply({error:'Open the Galaxy request link.'},400);
  if(action==='/drive/status')return reply({open:true});
  if(action!=='/drive/send')return reply({error:'Unknown action'},404);
  let input;
  try{input=await readForm(request);}catch(e){return reply({error:e.message},400);}
  const blocked=await storage.transaction(async tx=>{
    const r=await tx.get('drive');
    if(!r||r.expires<=Date.now())return 'This link expired.';
    if(r.done||r.busy)return 'Delivery is already in progress or awaiting confirmation. Check the support thread.';
    if(r.fingerprint&&r.fingerprint!==input.fingerprint)return 'Retry with the original submission.';
    await tx.put('drive',{...r,busy:true,fingerprint:input.fingerprint});
    return null;
  });
  if(blocked)return reply({error:blocked},409);
  const state=await storage.get('drive');
  let posting=false;
  try {
    if(!state.privateSent){
      if(env.DISCORD_PRIVATE_FORUM!=='true'||!env.DISCORD_PRIVATE_WEBHOOK_URL||env.DISCORD_PRIVATE_WEBHOOK_URL===env.DISCORD_WEBHOOK_URL)throw Error('Private forum required');
      const privateMeta=await fetch(env.DISCORD_PRIVATE_WEBHOOK_URL,{redirect:'manual',signal:AbortSignal.timeout(10000)});
      const publicMeta=await fetch(env.DISCORD_WEBHOOK_URL,{redirect:'manual',signal:AbortSignal.timeout(10000)});
      if(!privateMeta.ok||!publicMeta.ok)throw Error('Forum metadata unavailable');
      const priv=await privateMeta.json(),pub=await publicMeta.json();
      if(!/^\d+$/.test(priv.channel_id)||pub.channel_id!==supportForum||priv.channel_id===pub.channel_id||priv.guild_id!==pub.guild_id)throw Error('Verify private forum configuration');
      const form=new FormData();
      const payload={thread_name:('Drive logs · '+state.title).slice(0,100),content:'Drive logs received. Reference: '+state.reference,allowed_mentions:{parse:[]}};
      if(input.file?.size && env.DRIVE_FILES){
        state.fileKey='drive/'+state.reference;
        state.fileName=input.file.name.toLowerCase();
        state.fileExpires=Date.now()+7*day;
        await storage.put('drive',state);
        await storage.setAlarm(state.fileExpires);
        await env.DRIVE_FILES.put(state.fileKey,input.file.stream());
        payload.content+='\nSegment log (7 days): <'+state.origin+'/drive-file/'+state.token+'/'+state.downloadToken+'>';
      }
      form.set('payload_json',JSON.stringify(payload));
      form.set('files[0]',new Blob([['Reference: '+state.reference,'Support thread: '+state.threadID,'Route: '+input.route,'Time into drive: '+input.time,'Notes: '+input.note].join('\n')]),'drive-details.txt');
      if(input.file?.size && !state.fileKey)form.set('files[1]',input.file,input.file.name);
      posting=true;
      const privatePost=env.DISCORD_BOT_TOKEN ? await botPost(env,priv.channel_id,form) : await postWebhook(env.DISCORD_PRIVATE_WEBHOOK_URL,null,form);
      if(!privatePost.ok)return rejectDelivery(privatePost,state,storage);
      const msg=await privatePost.json();
      if(!/^\d+$/.test(msg.id)||!/^\d+$/.test(msg.channel_id)||msg.channel_id===state.threadID)throw Error('Unconfirmed private post');
      state.privateSent=true;
      state.privateMessage={id:msg.id,channel_id:msg.channel_id,...(env.DISCORD_BOT_TOKEN && {bot:true})};
      await storage.put('drive',state);
    }
    if(state.privateMessage){
      const indexed=await rememberDriveMessage(env,state,state.privateMessage);
      if(!indexed.ok)throw Error('Private message indexing failed');
    }
    posting=true;
    const payload={content:'Drive logs received. Reference: '+state.reference,allowed_mentions:{parse:[]}};
    const receipt=env.DISCORD_BOT_TOKEN ? await botPost(env,state.threadID,payload) : await postWebhook(env.DISCORD_WEBHOOK_URL,state.threadID,payload);
    if(!receipt.ok)return rejectDelivery(receipt,state,storage);
    const msg=await receipt.json();
    if(msg.channel_id!==state.threadID||!/^\d+$/.test(msg.id))throw Error('Unconfirmed receipt');
    await storage.put('drive',{...state,done:true,busy:false});
    return reply({sent:true,reference:state.reference});
  }catch{
    if(!posting){
      await storage.put('drive',{...state,busy:false});
      return reply({error:'The private submission service is unavailable. Retry the same submission.'},503);
    }
    return reply({error:'Delivery is unconfirmed. Contact support with reference '+state.reference},409);
  }
}
async function rejectDelivery(response,state,storage){
  if(response.status>=400&&response.status<500&&response.status!==408){
    await storage.put('drive',{...state,busy:false});
    return reply({error:'Discord declined delivery. Retry the same submission or contact support. Reference: '+state.reference},503);
  }
  return reply({error:'Delivery is unconfirmed. Contact support with reference '+state.reference},409);
}


// Discord delivers signed interactions only when this Worker is configured as the application's endpoint.
export async function driveInteraction(request,env,origin,ctx) {
  const key=env.DISCORD_PUBLIC_KEY;
  const sig=request.headers.get('X-Signature-Ed25519')||'';
  const ts=request.headers.get('X-Signature-Timestamp')||'';
  if(!/^[a-f0-9]{64}$/i.test(key||'')||!/^[a-f0-9]{128}$/i.test(sig)||!/^\d{10}$/.test(ts)||Math.abs(Date.now()/1000-Number(ts))>300)return reply({error:'Invalid signature'},401);
  const raw=await request.text();
  const bytes=s=>Uint8Array.from(s.match(/../g),h=>parseInt(h,16));
  const signer=await crypto.subtle.importKey('raw',bytes(key),'Ed25519',false,['verify']);
  if(!await crypto.subtle.verify('Ed25519',signer,bytes(sig),new TextEncoder().encode(ts+raw)))return reply({error:'Invalid signature'},401);
  const interaction=JSON.parse(raw);
  if(interaction.type===1)return reply({type:1});
  const answer=content=>reply({type:4,data:privateCard(content)});
  if(interaction.type===2&&interaction.data?.type===1&&['companion-staff-action','test-app'].includes(interaction.data.name))return privateStaffCommand(interaction,env,ctx);
  if(isReferral(interaction))return referralInteraction(interaction,env,ctx);
  if([3,5].includes(interaction.type))return caseInteraction(interaction,env,origin,ctx);
  if(interaction.type!==2||interaction.data?.name!=='request-logs')return answer('Unknown command.');
  const staff=[env.REPORT_MAINTAINER_ID,env.ANDROID_REPORT_MAINTAINER_ID].filter(Boolean);
  if(!staff.includes(interaction.member?.user?.id))return answer('A support maintainer can request drive logs.');
  try{
    const result=await issueDriveLink(env,interaction.channel_id,origin);
    const data=await result.json();
    return answer(result.ok?'Send this single-use link to the driver (valid for 24 hours): '+data.url:data.error);
  }catch{return answer('Open an active support thread and try again.');}
}

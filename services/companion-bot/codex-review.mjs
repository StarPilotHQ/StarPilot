import {messageText,botMessage} from './bot-delivery.mjs';
const reviewReply=(data,status=200)=>Response.json(data,{status,headers:{'Cache-Control':'no-store'}});
const reviewUUID=/^[a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12}$/i;

function reviewObject(env,name){
  return env.REPORTS.get(env.REPORTS.idFromName('codex-review:'+name));
}
function reviewCall(env,name,action,data){
  return reviewObject(env,name).fetch('https://reports/review/'+action,{method:'POST',body:JSON.stringify(data)});
}
export async function rememberDriveMessage(env,record,message){
  return reviewCall(env,'thread:'+record.threadID,'remember',{channel:message.channel_id,id:message.id,reference:record.reference,...(message.bot && {bot:true})});
}
export async function reviewStorage(storage,action,body){
  if(['enqueue','lease','finish','status'].includes(action))return reviewQueue(storage,action,body);
  if(action==='remember'){
    await storage.transaction(async tx=>{
      const list=await tx.get('driveMessages')||[];
      if(!list.some(x=>x.id===body.id))await tx.put('driveMessages',[body,...list].slice(0,20));
    });
    return reviewReply({saved:true});
  }
  if(action==='list')return reviewReply(await storage.get('driveMessages')||[]);
  if(action==='issue'){
    await storage.setAlarm(Date.now()+300000);
    await storage.put('reviewTicket',{...body,expires:Date.now()+300000});
    return reviewReply({ready:true});
  }
  if(action==='take'){
    const ticket=await storage.transaction(async tx=>{
      const value=await tx.get('reviewTicket');
      if(value)await tx.delete('reviewTicket');
      return value;
    });
    if(!ticket||ticket.expires<=Date.now())return reviewReply({error:'Review link expired. Click Review with Codex again.'},410);
    return reviewReply(ticket);
  }
  return reviewReply({error:'Unknown review action'},404);
}
export function claimReview(env,token){return reviewCall(env,'ticket:'+token,'take',{});}
export async function webhookMessage(raw,channel,id){
  const url=new URL(raw);
  if(url.origin!=='https://discord.com'||!/^\/api\/webhooks\/\d+\/[\w-]+$/.test(url.pathname)||!/^\d+$/.test(channel)||!/^\d+$/.test(id))
    throw Error('Invalid report destination');
  url.pathname+='/messages/'+id;url.search='';url.searchParams.set('thread_id',channel);
  const r=await fetch(url,{redirect:'manual',signal:AbortSignal.timeout(10000)});
  if(r.status>=300&&r.status<400)throw Error('Discord returned an unexpected redirect for the saved report.');
  if(!r.ok)throw Error('Discord could not retrieve a saved report ('+r.status+').');
  const m=await r.json();
  if(m.id!==id||m.channel_id!==channel)throw Error('Discord returned a different report.');
  return m;
}
export async function prepareReview(env,report,chat){
  if(chat!==''&&!reviewUUID.test(chat))return reviewReply({error:'Enter a Codex chat ID.'},400);
  const ids=new URL(report.threadURL).pathname.split('/');
  const publicMessage=report.botPublic ? await botMessage(env,ids[3],ids[4]) : await webhookMessage(env.DISCORD_WEBHOOK_URL,ids[3],ids[4]);
  const messages=[];
  const original=/\nPrivate diagnostics: https:\/\/discord\.com\/channels\/\d+\/(\d+)\/(\d+)\s*$/.exec(messageText(publicMessage));
  if(report.botPrivateURL){
    const location = new URL(report.botPrivateURL).pathname.split("/");
    messages.push(await botMessage(env,location[3],location[4]));
  } else if(original)messages.push(await webhookMessage(env.DISCORD_PRIVATE_WEBHOOK_URL,original[1],original[2]));
  const drives=await (await reviewCall(env,'thread:'+ids[3],'list',{})).json();
  for(const drive of drives)messages.push(drive.bot ? await botMessage(env,drive.channel,drive.id) : await webhookMessage(env.DISCORD_PRIVATE_WEBHOOK_URL,drive.channel,drive.id));
  const token=Array.from(crypto.getRandomValues(new Uint8Array(32)),x=>x.toString(16).padStart(2,'0')).join('');
  // Only references and Discord's short-lived attachment URLs enter the ticket.
  const entries=messages.map(m=>({message:messageText(m),attachments:(m.attachments||[]).map(a=>({name:a.filename,url:a.url,size:a.size}))}));
  const saved=await reviewCall(env,'ticket:'+token,'issue',{chat,reference:report.reference,title:report.title,description:messageText(publicMessage),entries});
  return saved.ok?reviewReply({ticket:token}):saved;
}



const queueSize=40;
const jobLifetime=86400000;
// Allow the next 30-minute Codex check to pick up a staged report.
const leaseDuration=60*60*1000;
const publicJob=job=>({id:job.id,state:job.state});

export function queueReview(env,report,chat){
  if(typeof chat!=='string'||(chat!==''&&!reviewUUID.test(chat)))return reviewReply({error:'Enter a Codex chat ID.'},400);
  return reviewCall(env,'queue','enqueue',{report,chat});
}

export async function reviewWorker(env,body){
  if(body.action==='finish'){
    if(!reviewUUID.test(body.id||'')||!reviewUUID.test(body.lease||'')||typeof body.delivered!=='boolean')
      return reviewReply({error:'Invalid review receipt'},400);
    return reviewCall(env,'queue','finish',body);
  }
  if(body.action!=='claim')return reviewReply({error:'Unknown review worker action'},400);
  const claimed=await reviewCall(env,'queue','lease',{});
  if(!claimed.ok)return claimed;
  const {job}=await claimed.json();
  if(!job)return reviewReply({job:null});
  try {
    const prepared=await prepareReview(env,job.report,job.chat);
    if(!prepared.ok)throw Error('Review preparation failed');
    const {ticket}=await prepared.json();
    return reviewReply({job:{id:job.id,lease:job.lease,ticket}});
  } catch {
    await reviewCall(env,'queue','finish',{id:job.id,lease:job.lease,delivered:false});
    return reviewReply({job:null});
  }
}

export function reviewStatus(env,id){
  if(!reviewUUID.test(id||''))return reviewReply({error:'Invalid review ID'},400);
  return reviewCall(env,'queue','status',{id});
}

async function reviewQueue(storage,action,body){
  return storage.transaction(async tx=>{
    const now=Date.now();
    const jobs=(await tx.get('jobs')||[]).filter(job=>now-job.created<jobLifetime);
    for(const job of jobs){
      if(job.state==='delivering'&&job.leaseUntil<=now)job.state='unconfirmed';
    }
    const save=()=>tx.put('jobs',jobs);
    if(action==='status'){
      const job=jobs.find(j=>j.id===body.id);
      return job?reviewReply(publicJob(job)):reviewReply({error:'Review status expired.'},404);
    }
    if(action==='enqueue'){
      const existing=jobs.find(j=>j.report.reference===body.report.reference&&j.chat===body.chat&&
        (j.state==='queued'||j.state==='delivering'||j.state==='unconfirmed'));
      if(existing)return reviewReply(publicJob(existing));
      if(jobs.length>=queueSize){
        const oldestFinished=jobs.findIndex(j=>j.state==='delivered'||j.state==='failed');
        if(oldestFinished<0)return reviewReply({error:'The review queue is full. Try again later.'},429);
        jobs.splice(oldestFinished,1);
      }
      const job={id:crypto.randomUUID(),created:now,state:'queued',report:body.report,chat:body.chat};
      jobs.push(job);
      await save();
      return reviewReply(publicJob(job),202);
    }
    if(action==='lease'){
      const job=jobs.find(j=>j.state==='queued');
      if(!job)return reviewReply({job:null});
      job.state='delivering';
      job.lease=crypto.randomUUID();
      job.leaseUntil=now+leaseDuration;
      await save();
      return reviewReply({job});
    }
    const job=jobs.find(j=>j.id===body.id&&j.lease===body.lease);
    if(!job)return reviewReply({error:'Invalid review receipt'},409);
    if(job.state==='delivering'||job.state==='unconfirmed'){
      job.state=body.delivered?'delivered':'failed';
      await save();
    }
    return reviewReply(publicJob(job));
  });
}

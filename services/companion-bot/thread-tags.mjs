export const supportForum='1565235002734809168';
export const supportTags={Open:'1565236988913913887','In Progress':'1565237023265263657',Closed:'1565237052524466207',Navigation:'1565237084447445122',CarPlay:'1565237111391527112',Galaxy:'1567747425047941200',iOS:'1567744790856990730',Android:'1567744808926322811'};
export async function discordTags(env,path,body){
 if(!env.DISCORD_BOT_TOKEN)throw Error('Tag controls need a Discord bot with View Channel and Manage Threads access.');
 const response=await fetch('https://discord.com/api/v10'+path,{method:body?'PATCH':'GET',headers:{Authorization:'Bot '+env.DISCORD_BOT_TOKEN,'Content-Type':'application/json'},...(body?{body:JSON.stringify(body)}:{}),redirect:'manual',signal:AbortSignal.timeout(15000)});
 if(!response.ok)throw Error('Discord tags unavailable (HTTP '+response.status+'). Refresh before retrying.');
 return response.json();
}
export async function getReportThread(env,id){
 const t=await discordTags(env,'/channels/'+id);
 if(t.id!==id||t.parent_id!==supportForum||t.type!==11||!Array.isArray(t.applied_tags))throw Error('Thread is not in the support forum.');
 return t;
}
export function nextReportTags(current,platform,status,topics){
 if(!['Open','In Progress','Closed'].includes(status)||!Array.isArray(topics)||topics.some(t=>!['Navigation','CarPlay','Galaxy'].includes(t)))throw Error('Invalid status or topic.');
 const known=new Set(Object.values(supportTags));
 const tags=[...new Set([...current.filter(t=>!known.has(t)),supportTags[platform==='ios'?'iOS':'Android'],supportTags[status],...topics.map(t=>supportTags[t])])];
 if(tags.length>5)throw Error('Discord allows five tags. Remove a topic or custom tag first.');
 return tags;
}
export async function filterReportThreads(env,items){
 if(!env.DISCORD_BOT_TOKEN)return items;
 const forum=await discordTags(env,'/channels/'+supportForum);
 if(!/^\d+$/.test(forum.guild_id||''))throw Error('Support forum unavailable.');
 const active=await discordTags(env,'/guilds/'+forum.guild_id+'/threads/active');
 if(!Array.isArray(active.threads))throw Error('Invalid Discord thread list.');
 const ids=new Set(active.threads.filter(t=>t.parent_id===supportForum&&!t.applied_tags?.includes(supportTags.Closed)&&!t.thread_metadata?.locked).map(t=>t.id));
 return items.filter(t=>ids.has(t.id));
}

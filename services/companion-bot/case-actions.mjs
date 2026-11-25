import {updateComma} from './galaxy-log-request.mjs';
import {botPost,supportCard,privateCard,ticketStates,ticketTitle} from './bot-delivery.mjs';
import {discordTags, getReportThread, supportForum, supportTags} from './thread-tags.mjs';
import {issueDriveLink, sendDriveInvite} from './drive-logs.mjs';

const titles = {
  note:'Additional Report', tags:'Apply tags', working:'Mark in progress', assign:'Assign someone', unassign:'Clear assignment', priority:'Set Priority', status:'Change Status',
  awaiting:'Awaiting deploy', investigate:'Further investigation', overview:'Report overview', question:'Ask the reporter', testing:'Request User Testing', commaTesting:'Request Comma Testing', rename:'Rename Thread', duplicate:'Link Duplicate',
  appLogs:'Request Phone App Logs', connection:'Request Comma Connection Logs', controller:'Request Controller Logs', starport:'Request StarPort Logs', removeTags:'Remove topic tags'
};
const descriptions={
  investigate:'Send this report back for investigation.',
  commaTesting:'Ask the reporter to update the comma and test the fix. Include a phone build if needed.',
  awaiting:'Record a fix that is waiting for deployment without pinging the reporter.',
  overview:'See status, priority, assignment and the latest available diagnostic link privately.',
  question:'Mention the reporter and give them a feedback button.',
  tags:'Add topics such as Galaxy, Navigation or CarPlay.', removeTags:'Remove selected topics while keeping the platform and status.',
  working:'Show that you are actively working on the report.', assign:'Pick and notify the person handling the report.',
  unassign:'Remove the current assignee.', priority:'Set urgency from 0 (highest) to 5, or clear it.',
  status:'Open, mark in progress, or close and archive the thread.', testing:'Mention the reporter with the build and steps to test.',
  rename:'Update the title people see in the forum.', duplicate:'Link to the original report by thread link or ID.',
  appLogs:'Ask the reporter for fresh diagnostics from the phone app in this ticket.',
  connection:'Phone app pairing, Bluetooth bridge, Wi-Fi and discovery on the comma.',
  starport:'Request the original StarPort session ZIP for Android Auto troubleshooting.',
  controller:'USB and Bluetooth input inventory for a physical controller.'
};
const statusNames=['Open','In Progress','Closed'];
const row = component => ({type:1,components:[component]});
const ephemeral = (content,controls) => Response.json({type:4,data:privateCard(content,controls)});
const idPattern = /^\d{1,20}$/;

export function reporterButtons(user,platform,channel) {
  const button=(action,label,style)=>({type:2,style,label,custom_id:['case',user,platform,action,channel].join(':')});
  return {type:1,components:[button('feedback','Give feedback',1),button('fulfilled','My request is fulfilled',3)]};
}

export function caseButtons(user, platform) {
  const button = (action,label,style) => ({type:2,style,label,custom_id:['case',user,platform,action].join(':')});
  return [{type:1,components:[button('note',titles.note,2),button('menu','Staff Actions',1)]}];
}

function staffMenu(base,channel){
  const options=Object.entries(titles).filter(([key])=>key!=='note').map(([value,label])=>({label,value,description:descriptions[value]}));
  return privateCard('**Staff Actions**',[row({type:3,custom_id:base+'pick:'+channel,placeholder:'Choose a staff action',options})]);
}

export function privateStaffCommand(i,env,ctx){
  if(i.guild_id!=='1387432184121393333'||!idPattern.test(i.channel_id))return ephemeral('Open a Companion report thread.');
  ctx.waitUntil((async()=>{
    let card=privateCard('Could not find a saved Companion report for this thread.');
    try{
      const settings=env.REPORTS.get(env.REPORTS.idFromName('companion-bot-setup'));
      const role=await settings.fetch('https://reports/sage-role');
      if(!role.ok||!i.member?.roles?.includes((await role.json()).id)){
        card=privateCard('Staff Actions requires the StarPilot Sage role.');
      }else{
        card=await ticketStaffMenu(i,env)||card;
      }
    }catch(error){
      console.warn('Private staff menu lookup failed',i.channel_id,error.message);
      card=privateCard('Could not verify the ticket. Try Staff Actions on the report card.');
    }
    await fetch('https://discord.com/api/v10/webhooks/'+i.application_id+'/'+i.token+'/messages/@original',{
      method:'PATCH',headers:{'Content-Type':'application/json'},body:JSON.stringify({...card,content:null,embeds:[]}),redirect:'manual'});
  })());
  return Response.json({type:5,data:{flags:64}});
}

async function ticketStaffMenu(i,env){
  if(i.channel?.parent_id&&i.channel.parent_id!==supportForum)return;
  const referrals=env.REPORTS.get(env.REPORTS.idFromName('driving-referral-review'));
  const response=await referrals.fetch('https://reports/referrals/ticket',{method:'POST',body:JSON.stringify({channel:i.channel_id})});
  const linked=response.ok?await response.json():null;
  if(linked&&idPattern.test(linked.reporter)&&['ios','android'].includes(linked.platform))
    return staffMenu('case:'+linked.reporter+':'+linked.platform+':',i.channel_id);
  const thread=await getReportThread(env,i.channel_id);
  const path='/channels/'+thread.id+'/messages';
  const opener=await discordTags(env,path+'/'+thread.id).catch(()=>null);
  function controls(message){
    if(message?.author?.id!==i.application_id||message.channel_id!==thread.id)return;
    const items=(message.components||[]).flatMap(row=>row.components||[]);
    for(const item of items){
      const match=/^case:(\d{1,20}):(ios|android):menu(?::(\d{1,20}))?$/.exec(item.custom_id||'');
      if(match&&(!match[3]||match[3]===thread.id))return staffMenu('case:'+match[1]+':'+match[2]+':',thread.id);
    }
  }
  const menu=controls(opener);
  if(menu)return menu;
  const platforms=thread.applied_tags.includes(supportTags.Android)?['android','ios']:['ios','android'];
  const sameThread=url=>typeof url==='string'&&new RegExp('^https://discord\\.com/channels/'+i.guild_id+'/'+thread.id+'/\\d+$').test(url);
  // Old webhooks posted the reporter and reference as text, before buttons existed.
  const reporter=/^Report from <@!?(\d{1,20})>/.exec(opener?.content||'')?.[1];
  const reference=/^Reference: ([a-f0-9-]{36})\s*$/im.exec(opener?.content||'')?.[1];
  if(reporter&&reference){
    const owner=env.REPORTS.get(env.REPORTS.idFromName(reporter));
    for(const platform of platforms){
      const result=await owner.fetch('https://reports/dashboard-record',{method:'POST',body:JSON.stringify({platform,userID:reporter,reference})});
      if(result.ok&&sameThread((await result.json()).threadURL))return staffMenu('case:'+reporter+':'+platform+':',thread.id);
    }
  }
  const recent=await discordTags(env,path+'?limit=100').catch(()=>[]);
  const recentMenu=recent.map(controls).find(Boolean);
  if(recentMenu)return recentMenu;
  // Discord can omit an old webhook's text. Match the saved public thread URL instead.
  for(const platform of platforms){
    const index=env.REPORTS.get(env.REPORTS.idFromName('dashboard:'+platform));
    let cursor;
    do{
      const result=await index.fetch('https://reports/dashboard-list',{method:'POST',body:JSON.stringify({platform,cursor})});
      if(!result.ok)throw Error('Report index unavailable');
      const page=await result.json();
      const record=page.reports.find(r=>r.platform===platform&&idPattern.test(r.userID)&&sameThread(r.threadURL));
      if(record)return staffMenu('case:'+record.userID+':'+platform+':',thread.id);
      cursor=page.cursor;
    }while(cursor);
  }
}

export async function caseInteraction(i,env,origin,ctx) {
  const [prefix,user,platform,command,bound] = (i.data?.custom_id||'').split(':');
  const channel=i.channel_id, actor=i.member?.user?.id;
  if(prefix!=='case'||!idPattern.test(user)||!idPattern.test(channel)||!idPattern.test(actor)||!['ios','android'].includes(platform)||
     (bound && bound!==channel)) return ephemeral('Open the original support thread.');
  const roles=i.guild_id==='1387432184121393333' && Array.isArray(i.member?.roles)?i.member.roles:[];
  let sage=false;
  if(roles.length){
    const settings=env.REPORTS.get(env.REPORTS.idFromName('companion-bot-setup'));
    const response=await settings.fetch('https://reports/sage-role');
    if(response.ok)sage=roles.includes((await response.json()).id);
  }
  const base=['case',user,platform].join(':')+':';
  const selected=command==='pick'?i.data.values?.[0]:command;
  const commaTesting=selected==='commaTesting';
  const action=commaTesting?'testing':selected;
  const reporterAction=['feedback','fulfilled'].includes(action);
  if(reporterAction && (actor!==user || bound!==channel))return ephemeral('Only the original reporter can respond to this request.');
  if(!reporterAction && (action==='note' ? actor!==user&&!sage : !sage)) return ephemeral(action==='note'?'Only the reporter or StarPilot Sage can add details.':'Staff Actions requires the StarPilot Sage role.');
  if(i.type===3 && command==='menu'){
    return Response.json({type:4,data:staffMenu(base,channel)});
  }
  if(!reporterAction&&!Object.hasOwn(titles,action))return ephemeral('Unknown support action.');
  if(i.type===3 && command==='pick' && action==='assign'){
    return ephemeral(titles[action], [row({type:5,custom_id:base+'assign:'+channel,placeholder:'Choose a server member'})]);
  }
  if(i.type===3 && command==='pick' && ['tags','removeTags'].includes(action)){
    const forum=await discordTags(env,'/channels/'+supportForum);
    const thread=await getReportThread(env,channel);
    const options=topicTags(forum).filter(t=>thread.applied_tags.includes(t.id)===(action==='removeTags')).map(t=>({label:t.name,value:t.id}));
    if(!options.length)return ephemeral('There are no topics available for that action.');
    return ephemeral(titles[action], [row({type:3,custom_id:base+action+':'+channel,placeholder:titles[action],max_values:Math.min(5,options.length),options})]);
  }
  const choices={priority:['0','1','2','3','4','5','Clear'],status:['Open','In Progress','Closed']};
  if(i.type===3 && command==='pick' && choices[action]){
    return ephemeral(titles[action], [row({type:3,custom_id:base+action+':'+channel,placeholder:titles[action],options:choices[action].map(value=>({label:value,value}))})]);
  }
  const prompts={question:'What do you need from the reporter?',feedback:'What did you find or what do you need?',note:'What happened?',testing:'Steps to test',awaiting:'Update for the reporter (optional)',rename:'New thread title',duplicate:'Original Discord thread link or ID'};
  if(i.type===3 && prompts[action]){
    const fields=[row({type:4,custom_id:'value',label:prompts[action],style:['note','testing','awaiting','feedback','question'].includes(action)?2:1,required:action!=='awaiting',max_length:action==='rename'?100:action==='duplicate'?200:1500})];
    if(['awaiting','testing'].includes(action)){
      fields.push(row({type:4,custom_id:'build',label:'App build (optional)',placeholder:'e.g. iOS 2.0 (332)',style:1,required:false,max_length:120}),
        row({type:4,custom_id:'commit',label:'GitHub commit URL (optional)',placeholder:'https://github.com/owner/repo/commit/...',style:1,required:false,max_length:300}));
    }
    return Response.json({type:9,data:{title:titles[selected]||'Give feedback',custom_id:base+selected+':'+channel,components:fields}});
  }
  const value=['tags','removeTags'].includes(action)?i.data.values:i.type===5?i.data.components?.[0]?.components?.[0]?.value?.trim():i.data.values?.[0];
  if(prompts[action] && ((!value&&action!=='awaiting')||i.type!==5) || choices[action]&&!choices[action].includes(value))return ephemeral('Choose a valid value.');
  if(action==='assign'&&(!idPattern.test(value)||!i.data.resolved?.members?.[value]))return ephemeral('Choose a server member.');
  const fields=(i.data.components||[]).flatMap(r=>r.components||[]);
  const build=fields.find(field=>field.custom_id==='build')?.value?.trim();
  const commit=fields.find(field=>field.custom_id==='commit')?.value?.trim();
  if(commit&&!/^https:\/\/github\.com\/[\w.-]+\/[\w.-]+\/commit\/[\da-f]{7,64}\/?$/i.test(commit))
    return ephemeral('Use a GitHub commit URL ending in the commit hash.');
  const owner=env.REPORTS.get(env.REPORTS.idFromName(user));
  ctx.waitUntil((async()=>{
    let content='Could not confirm the action. Check the thread before retrying.';
    try{
      const response=await owner.fetch('https://reports/case-action',{method:'POST',body:JSON.stringify({user,platform,channel,action,actor,value,build,commit,commaTesting,interaction:i.id,origin})});
      content=(await response.json()).message||content;
    }finally{
      await fetch('https://discord.com/api/v10/webhooks/'+i.application_id+'/'+i.token+'/messages/@original',{method:'PATCH',headers:{'Content-Type':'application/json'},body:JSON.stringify({...privateCard(content),content:null,embeds:[]}),redirect:'manual'});
    }
  })());
  return Response.json({type:5,data:{flags:64}});
}

function topicTags(forum){
  const reserved=['Open','In Progress','Closed','iOS','Android'].map(name=>supportTags[name]);
  return (forum.available_tags||[]).filter(tag=>!reserved.includes(tag.id));
}

export async function caseAction(storage,env,b){
  const result=message=>Response.json({message});
  const {channel,platform,user,action,actor,value}=b;
  const key='thread:'+channel;
  const record=await storage.get(key);
  if(!record||(record.platform===undefined?'ios':record.platform)!==platform)return result('This ticket could not be verified.');
  if(['feedback','fulfilled'].includes(action)&&actor!==user)return result('Only the reporter can respond.');
  const thread=await getReportThread(env,channel);
  if(action==='overview'){
    const status=statusNames.find(name=>thread.applied_tags.includes(supportTags[name])) || 'No status tag';
    const summary=['**Report overview**', 'Status: '+status+(thread.thread_metadata?.archived?' (archived)':''),
      'Assigned to: '+(record.assignee?' <@'+record.assignee+'>':'Unassigned'), 'Priority: '+(record.priority??'Unset')];
    if(record.releaseStage)summary.push('Fix: '+record.releaseStage);
    if(record.build)summary.push('App build: '+record.build);
    if(record.commit)summary.push('[View commit]('+record.commit+')');
    if(record.duplicate)summary.push('Original thread: <#'+record.duplicate+'>');
    const diagnostics=await storage.get('latest-diagnostics:'+channel);
    if(diagnostics)summary.push('[Open latest private diagnostics]('+diagnostics+')');
    return result(summary.join('\n'));
  }
  const reopening=action==='status'&&value==='Open';
  if(thread.thread_metadata?.locked)return result('A moderator locked this thread. Unlock it in Discord first.');
  if((thread.applied_tags.includes(supportTags.Closed)||thread.thread_metadata?.archived)&&!reopening&&!(action==='status'&&value==='Closed'))
    return result('This report is closed or archived. Staff can use Change Status → Open to resume it.');
  const fresh=await storage.transaction(async tx=>{
    const recent=await tx.get('case-interactions')||[];
    if(recent.includes(b.interaction))return false;
    await tx.put('case-interactions',[...recent.slice(-99),b.interaction]);return true;
  });
  if(!fresh)return result('This action was already received.');
  if(action==='appLogs'){
    const steps=platform==='android'
      ? 'Open the StarPilot Android app and go to **Debugging options → Report a problem**. If the last report is marked sent, tap **Start another report**. Describe what happened, choose this existing thread, then tap **Send report** and confirm the preview.'
      : 'Open StarPilot on your iPhone and go to **Settings → Logs & support → Report a problem on Discord**. Describe what happened and tap **Send**. Select this existing thread and tap **Send report**.';
    const instructions='**Fresh phone app logs requested**\nIf you can, reproduce the issue first. Sign in with the Discord account you used for this ticket.\n\n'+steps+'\n\nSupport staff receive the phone diagnostics privately.';
    const card=supportCard('<@'+user+'>',instructions,[reporterButtons(user,platform,channel)]);
    const sent=await botPost(env,channel,{...card,nonce:b.interaction,enforce_nonce:true,allowed_mentions:{parse:[],users:[user]}});
    return result(sent.ok?'Phone app log instructions sent.':'Could not confirm delivery of the phone app log instructions.');
  }
  if(['connection','controller','starport'].includes(action)){
    const link=await issueDriveLink(env,channel,b.origin,{userID:user,platform},action);
    const data=await link.json();
    if(!link.ok)return result(data.error||'Could not create the request.');
    await storage.transaction(async tx=>{
      const latest=await tx.get(key);
      await tx.put(key,{...latest,requestedLogKinds:{...latest.requestedLogKinds,[action]:Date.now()+86400000}});
    });
    const sent=await sendDriveInvite(env,channel,new URL(data.url).pathname.split('/').pop());
    return result(sent.ok?'Log instructions sent to this thread.':'Could not confirm delivery of the instructions.');
  }
  let message, nextStatus;
  const patch={};
  const state=action==='fulfilled'?ticketStates.fulfilled
    :action==='status'&&value==='Closed'?ticketStates.closed
    :action==='investigate'||action==='status'&&value==='Open'?ticketStates.investigating
    :action==='testing'?ticketStates.testing
    :['awaiting','working'].includes(action)||action==='status'&&value==='In Progress'?ticketStates.reviewed:null;
  const rename=state&&thread.name?{name:ticketTitle(thread.name,state)}:{};
  if(action==='duplicate'){
    const target=/^https:\/\/discord\.com\/channels\/1387432184121393333\/(\d{1,20})(?:\/\d{1,20})?\/?$/.exec(value)?.[1]||value;
    if(!idPattern.test(target)||channel===target)return result('Choose a different support thread.');
    await getReportThread(env,target);
    patch.duplicate=target;
    message='Duplicate of <#'+target+'>.';
  }else if(action==='rename'){
    const currentState=Object.values(ticketStates).find(item=>thread.name?.startsWith(item.circle));
    await discordTags(env,'/channels/'+channel,{name:currentState?ticketTitle(value,currentState):value});
    patch.title=value;message='Renamed this report to '+value+'.';
  }else if(action==='assign'||action==='unassign'){
    patch.assignee=action==='assign'?value:null;
    message=patch.assignee?'Assigned to <@'+value+'>.':'Assignment cleared.';
  }else if(action==='tags'||action==='removeTags'){
    const allowed=topicTags(await discordTags(env,'/channels/'+supportForum));
    if(!Array.isArray(value)||!value.length||value.some(id=>!allowed.some(t=>t.id===id)))return result('Reopen Apply tags to see the current choices.');
    const tags=action==='removeTags'?thread.applied_tags.filter(id=>!value.includes(id)):[...new Set([...thread.applied_tags,...value])];
    if(tags.length>5)return result('Discord allows five tags. Remove a tag or choose fewer.');
    await discordTags(env,'/channels/'+channel,{applied_tags:tags});
    message=(action==='removeTags'?'Removed topics: ':'Added topics: ')+allowed.filter(t=>value.includes(t.id)).map(t=>t.name).join(', ')+'.';
  }else if(action==='priority'){
    patch.priority=value==='Clear'?null:value;
    message=patch.priority===null?'Priority cleared.':'Priority '+value+' (0 is highest).';
  }else if(action==='status'||['working','investigate','fulfilled'].includes(action)){
    const status=action==='fulfilled'?'Closed':['working','investigate'].includes(action)?'In Progress':value;
    nextStatus=status;
    patch.releaseStage=null;
    const statuses=['Open','In Progress','Closed'].map(name=>supportTags[name]);
    const tags=[...thread.applied_tags.filter(id=>!statuses.includes(id)),supportTags[status]];
    if(tags.length>5)return result('Remove a forum tag to make room for the status.');
    await discordTags(env,'/channels/'+channel,{applied_tags:tags,...rename,...(reopening&&thread.thread_metadata?.archived?{archived:false}:{})});
    message=action==='investigate'?'Staff are investigating this report again.':action==='fulfilled'?'The reporter marked this request fulfilled.':action==='working'?'<@'+actor+'> is working on this report.':'Status changed to '+status+'.';
  }else if(action==='testing'||action==='awaiting'){
    if(rename.name && rename.name!==thread.name)await discordTags(env,'/channels/'+channel,rename);
    patch.releaseStage=action==='testing'?'Ready for testing':'Awaiting deploy';
    if(b.build)patch.build=b.build;
    if(b.commit)patch.commit=b.commit;
    message=action==='testing'?'<@'+user+'>':'';
  }else if(action==='question')message='<@'+user+'>';
  else if(action==='feedback')message='Feedback from <@'+actor+'>\n'+value;
  else if(action==='note')message='Additional report from <@'+actor+'>\n'+value;
  else return result('Unknown action.');
  if(Object.keys(patch).length)await storage.transaction(async tx=>tx.put(key,{...await tx.get(key),...patch}));
  const payload={content:message,nonce:b.interaction,enforce_nonce:true,
    allowed_mentions:{parse:[],users:['testing','question'].includes(action)?[user]:action==='assign'?[value]:[]}};
  if(action==='awaiting'||action==='testing'||action==='question'){
    const fix=[];
    if(action!=='question'){
      const build=patch.build||record.build, commit=patch.commit||record.commit;
      if(build)fix.push('**App build:** '+build);
      if(commit)fix.push('[View commit]('+commit+')');
    }
    if(action==='awaiting'){
      delete payload.content;
      Object.assign(payload,supportCard('',['**Awaiting deploy**','A fix is waiting for release. Staff will ask you to test when it is available.',value,...fix].filter(Boolean).join('\n'),caseButtons(user,platform),[],state.color));
    }else{
      const text=[action==='testing'?'**Waiting for user testing**':'**Staff need more information**',...(action==='testing'&&b.commaTesting?[updateComma]:[]),value,...fix,'','Choose **Give feedback** to share your results, or **My request is fulfilled** to close the report.'].join('\n');
      delete payload.content;
      Object.assign(payload,supportCard(message,text,[reporterButtons(user,platform,channel)],[],state?.color));
    }
  }else if(action==='feedback'||action==='note'){
    delete payload.content;
    Object.assign(payload,supportCard('',message,caseButtons(user,platform)));
  }
  if(nextStatus){
    delete payload.content;
    const controls=nextStatus==='Closed'?[row({type:2,style:2,label:'Staff Actions',custom_id:'case:'+user+':'+platform+':menu:'+channel})]:caseButtons(user,platform);
    Object.assign(payload,supportCard('','**'+nextStatus+'**\n'+message,controls,[],state.color));
  }
  const post=await botPost(env,channel,payload);
  if(nextStatus==='Closed'){
    let followNotice='';
    if(action==='status' && /^\d{1,20}$/.test(actor)){
      try{
        const membership=await fetch('https://discord.com/api/v10/channels/'+channel+'/thread-members/'+actor,{
          method:'DELETE',headers:{Authorization:'Bot '+env.DISCORD_BOT_TOKEN},redirect:'manual',signal:AbortSignal.timeout(5000)
        });
        if(!membership.ok && membership.status!==404)followNotice=' Discord could not unfollow you.';
      }catch{followNotice=' Discord could not confirm that you unfollowed.';}
    }
    try{await discordTags(env,'/channels/'+channel,{archived:true});}
    catch{return result('Tagged Closed, but archiving failed. Check Manage Threads, then choose Closed again.');}
    return result((post.ok?'Closed and archived. The original thread link still works.':'Closed and archived. Discord did not confirm the status card.')+followNotice);
  }
  return result(post.ok?'Updated this report.':'Could not confirm the update message. Refresh the thread.');
}


export async function enableCaseControls(env) {
  const endpoint='https://starpilot-log-relay.nathan-g-hubbard.workers.dev/drive-interactions';
  const path='/applications/@me';
  let app=await discordTags(env,path);
  if(app.id!=='1574857862872703088'||app.verify_key!==env.DISCORD_PUBLIC_KEY)throw Error('Check the Companion-Bot application and verification key.');
  if(app.interactions_endpoint_url===endpoint)return;
  if(app.interactions_endpoint_url)throw Error('Companion-Bot already uses another interactions endpoint.');
  app=await discordTags(env,path,{interactions_endpoint_url:endpoint});
  if(app.interactions_endpoint_url!==endpoint)throw Error('Discord has not confirmed the support controls endpoint.');
}

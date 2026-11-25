export function supportCard(mention,text,buttons,inside=[],color=0xf2b134){
  const components=[];
  if(mention) components.push({type:10,content:mention});
  components.push({type:17,accent_color:color,components:[{type:10,content:text},...inside]},...buttons);
  return {flags:1<<15,components};
}

export function privateCard(text, controls=[]) {
  const card=supportCard('',text,[],controls,0x5865f2);
  return {...card,flags:card.flags | 64,allowed_mentions:{parse:[]}};
}

export function messageText(message) {
  function textOf(component) {
    if(component.type===10)return [component.content];
    return (component.components || []).flatMap(textOf);
  }
  return [message.content,...(message.components || []).flatMap(textOf)].filter(Boolean).join('\n');
}

// Normalize Discord's forum-thread response to the same message receipt used for replies.
export async function botPost(env, channel, payload) {
  if (!env.DISCORD_BOT_TOKEN || !/^[0-9]{1,20}$/.test(channel)) throw Error('Bot destination unavailable');
  const multipart = payload instanceof FormData;
  const fields = multipart ? JSON.parse(payload.get('payload_json')) : payload;
  const {thread_name:name, applied_tags:tags, ...message} = fields;
  if(message.content && !(message.flags & (1<<15))){
    const files=[];
    if(multipart){
      for(const [key,file] of payload){
        if(key.startsWith('files[') && file?.name)files.push({type:13,file:{url:'attachment://'+file.name}});
      }
    }
    const card=supportCard('',message.content,message.components || [],files,0x5865f2);
    delete message.content;
    Object.assign(message,card);
  }
  const data = name ? {name, message, ...(tags && {applied_tags:tags})} : message;
  const headers = {Authorization:'Bot '+env.DISCORD_BOT_TOKEN};
  if (multipart) payload.set('payload_json',JSON.stringify(data));
  else headers['Content-Type']='application/json';
  const response = await fetch('https://discord.com/api/v10/channels/'+channel+(name?'/threads':'/messages'),{
    method:'POST', headers, body:multipart?payload:JSON.stringify(data), redirect:'manual', signal:AbortSignal.timeout(15000)
  });
  if (!name || !response.ok) return response;
  const thread=await response.json();
  if (thread.parent_id!==channel || !thread.message?.id || thread.message.channel_id!==thread.id)
    throw Error('Discord did not confirm the forum post');
  return Response.json(thread.message);
}

export async function botMessage(env, channel, id) {
  if (!env.DISCORD_BOT_TOKEN || ![channel,id].every(v=>/^[0-9]{1,20}$/.test(v))) throw Error('Bot message unavailable');
  const result=await fetch('https://discord.com/api/v10/channels/'+channel+'/messages/'+id,{
    headers:{Authorization:'Bot '+env.DISCORD_BOT_TOKEN},redirect:'manual',signal:AbortSignal.timeout(10000)
  });
  if (!result.ok) throw Error('Discord could not read the saved message ('+result.status+').');
  const message=await result.json();
  if (message.channel_id!==channel || message.id!==id) throw Error('Discord returned a different message');
  return message;
}

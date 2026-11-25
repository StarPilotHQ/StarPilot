const topics={
  connection:['Phone App','Send phone app logs'],
  controller:['Controller','Send controller logs'],
  starport:['StarPort / Car Screen','Send StarPort logs'],
};

export function galaxyLogSteps(record){
  const [topic,send]=topics[record.kind];
  const detail=record.kind==='connection'?'Select '+(record.platform==='android'?'Android':'iPhone')+' and describe the issue':
    record.kind==='controller'?'Enter your controller model and describe the issue':'Describe what happened';
  return [
    'In Galaxy, open Logs & Diagnostics → Get help → '+topic+'.',
    detail+', then tap '+send+'. The comma needs internet to upload.',
    'If the browser doesn’t open, tap Continue on Discord support. Sign in with the Discord account that opened this thread.',
    'Under Post to, select “'+String(record.title||'this support thread').replace(/[\r\n]/g,' ').slice(0,100)+'” and tap Post report. Only support staff receive the ZIP.',
  ];
}

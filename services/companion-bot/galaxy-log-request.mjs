const topics={
  connection:['Phone App','Send phone app logs'],
  controller:['Controller','Send controller logs'],
  starport:['StarPort / Car Screen','Send StarPort logs'],
};

export const updateComma='While parked, update your comma to the latest testing build from StarPilotHQ/StarPilot.';
export const routeSteps='For a drive that already happened, share the original comma connect route and the time of the issue. If staff ask for a new test drive, update your comma to the latest testing build from StarPilotHQ/StarPilot while parked before that drive.';

export function galaxyLogSteps(record){
  const [topic,send]=topics[record.kind];
  const detail=record.kind==='connection'?'Select '+(record.platform==='android'?'Android':'iPhone')+' and describe the issue':
    record.kind==='controller'?'Enter your controller model and describe the issue':'Describe what happened';
  const retry={connection:'Try connecting your phone again.',controller:'Try connecting your controller again.',starport:'Try StarPort again.'};
  return [
    updateComma+' '+retry[record.kind]+' If the issue persists, collect fresh logs:',
    'In Galaxy, open Logs & Diagnostics → Get help → '+topic+'.',
    detail+', then tap '+send+'. The comma needs internet to upload.',
    'If the browser doesn’t open, tap Continue on Discord support. Sign in with the Discord account that opened this thread.',
    'Under Post to, select “'+String(record.title||'this support thread').replace(/[\r\n]/g,' ').slice(0,100)+'” and tap Post report. Only support staff receive the ZIP.',
    'If your comma is offline, save the ZIP from Galaxy on your phone and keep it until you are online. Include the branch and version you captured.',
  ];
}

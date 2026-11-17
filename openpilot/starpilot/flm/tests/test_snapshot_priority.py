"""Frozen snapshot preparation stays below its normal-priority receiver."""
import json
import subprocess
import sys

import pytest


@pytest.mark.skipif(not sys.platform.startswith('linux'), reason='Production fork/scheduler path is Linux')
def test_actual_snapshot_child_runs_below_receiver_and_is_reaped():
  code = '''
import json,os,resource,time
from openpilot.starpilot.flm.live_tracking_bootstrap_producer import ResponseSnapshotProcess
os.sched_setscheduler(0,os.SCHED_OTHER,os.sched_param(0))
os.setpriority(os.PRIO_PROCESS,0,0)
job=ResponseSnapshotProcess()
try:
 def prepare():
  return {'nice':os.getpriority(os.PRIO_PROCESS,0),'scheduler':os.sched_getscheduler(0),
          'cpu':resource.getrlimit(resource.RLIMIT_CPU)[0]}
 stamp=time.monotonic_ns()
 assert job.submit_preparation(prepare,token='priority-check',epoch=1,now_ns=stamp)
 deadline=time.monotonic()+3
 result=None
 while job.pid is not None and time.monotonic()<deadline:
  result=job.poll(token='priority-check',now_ns=time.monotonic_ns())
  time.sleep(.005)
 assert job.pid is None and job.fd is None
 assert result is not None and result['nice']==10 and result['scheduler']==os.SCHED_OTHER,result
 assert 0<result['cpu']<=30
 assert os.getpriority(os.PRIO_PROCESS,0)==0
 print(json.dumps(result))
finally:
 job.close()
'''
  result = subprocess.run([sys.executable, '-B', '-c', code], capture_output=True, text=True, timeout=8)
  assert result.returncode == 0, result.stdout + result.stderr
  assert json.loads(result.stdout)['nice'] == 10

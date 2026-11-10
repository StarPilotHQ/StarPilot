import ast
import dataclasses
from pathlib import Path
from types import SimpleNamespace
import unittest

P = Path(__file__).resolve().parent
SOURCE = Path(__file__).resolve().parents[1]

def function(path, name, namespace):
  tree = ast.parse(path.read_text())
  node = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == name)
  exec(compile(ast.Module(body=[node], type_ignores=[]), str(path), 'exec'), namespace)
  return namespace[name]

@dataclasses.dataclass(frozen=True)
class Box:
  confidence: float
  bbox: tuple

@dataclasses.dataclass(frozen=True)
class Evidence:
  session: str
  stream: str
  observed_ns: int
  eof_boot_ns: int
  frame_id: int
  width: int
  height: int
  boxes: tuple

class Tests(unittest.TestCase):
  def setUp(self):
    self.sent = []
    self.clock = [200, 1200]
    self.health = True
    self.ns = dict(clock_pair_ns=lambda: tuple(self.clock), MAX_PAIR_SKEW_NS=10,
                   MAX_RESULT_AGE_NS=1000, STOP_MAX_AGE_NS=500, _driving_model_ready=lambda sm, now: self.health,
                   StopEvidence=Evidence, StopBox=Box,
                   _send=lambda pm, **kw: self.sent.append(kw))
    self.publish = function(SOURCE/'producer.py', '_publish_ready_stop', self.ns)
    self.args = dict(session='s', stream='road', is_metric=False, offset_ns=1000,
                     source_observed_ns=100, eof_boot_ns=1100, frame_id=7, width=100, height=50,
                     stops=(Box(.8, (1,2,3,4)),), last_result=(40,.99,3,1),
                     last_result_session='s', last_result_observed_ns=80, last_result_valid_until_ns=800)
    self.sm = SimpleNamespace(update=lambda timeout: None)
    self.params = SimpleNamespace(get_bool=lambda key: False)

  def test_callback_after_all_regions_before_reader_with_readonly_tuple(self):
    events=[]
    class Base:
      def _collect_detector_classifier_proposals(self, frame):
        events.extend(['region1', 'region2'])
        self.stop_detections=(Box(.8,(1,2,3,4)),)
        return [('unchanged',)]
    tree=ast.parse((SOURCE/'traffic_signs.py').read_text())
    cls=next(n for n in tree.body if isinstance(n,ast.ClassDef) and n.name=='TrafficSignModelCore')
    method=next(n for n in cls.body if isinstance(n,ast.FunctionDef) and n.name=='_collect_detector_classifier_proposals')
    trial=ast.ClassDef(name='Trial',bases=[ast.Name(id='Base',ctx=ast.Load())],keywords=[],body=[method],decorator_list=[])
    ns={'Base':Base};exec(compile(ast.fix_missing_locations(ast.Module(body=[trial],type_ignores=[])),'hook','exec'),ns)
    core=ns['Trial']();core.net=object();received=[]
    core._stop_ready_callback=lambda stops:(events.append('publish'),received.append(stops))
    result=core._collect_detector_classifier_proposals(None);events.append('reader')
    self.assertEqual(events,['region1','region2','publish','reader'])
    self.assertEqual(result,[('unchanged',)])
    self.assertIsInstance(received[0],tuple)
    with self.assertRaises(dataclasses.FrozenInstanceError):received[0][0].confidence=1

  def test_speed_support_unchanged_and_stop_source_not_restamped(self):
    self.publish(None,self.sm,self.params,**self.args)
    row=self.sent.pop();self.assertEqual(row['result'],self.args['last_result'])
    self.assertEqual(row['observed_ns'],80);self.assertEqual(row['valid_until_ns'],800)
    self.assertEqual(row['stop_sample'].observed_ns,100);self.assertEqual(row['stop_sample'].frame_id,7)
    self.assertEqual(row['stop_sample'].boxes,self.args['stops'])
    self.args['last_result_valid_until_ns']=150
    self.publish(None,self.sm,self.params,**self.args)
    self.assertIsNone(self.sent[-1]['result']);self.assertEqual(self.sent[-1]['observed_ns'],0)

  def test_late_clock_and_health_reject_without_send(self):
    self.clock[:]=[200,1300];self.publish(None,self.sm,self.params,**self.args)
    self.assertEqual(self.sent,[])
    self.clock[:]=[2000,3000];self.publish(None,self.sm,self.params,**self.args)
    self.assertEqual(self.sent,[])
    for pair in ([600,1600], [700,1700]):
      self.clock[:]=pair;self.publish(None,self.sm,self.params,**self.args)
      self.assertEqual(self.sent,[])
    self.clock[:]=[200,1200];self.health=False;self.publish(None,self.sm,self.params,**self.args)
    self.assertEqual(self.sent,[])

  def test_reader_failure_clears_actual_producer_callback_finally(self):
    class ReaderFailure(Exception):pass
    class Core:
      _stop_ready_callback=lambda self,stops: None
      def observe(self,*a,**kw):raise ReaderFailure()
    tree=ast.parse((SOURCE/'producer.py').read_text())
    node=next(n for n in ast.walk(tree)if isinstance(n,ast.Try) and n.finalbody and
              any(isinstance(x,ast.Assign) and any(isinstance(v,ast.Attribute) and v.attr=='_stop_ready_callback' for v in x.targets)for x in n.finalbody))
    core=Core();ns={'core':core,'frame':None,'source_observed_ns':100}
    with self.assertRaises(ReaderFailure):exec(compile(ast.Module(body=[node],type_ignores=[]),'actual_finally','exec'),ns)
    self.assertIsNone(core._stop_ready_callback)

if __name__ == '__main__': unittest.main()

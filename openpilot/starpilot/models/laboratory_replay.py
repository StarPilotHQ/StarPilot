from collections import OrderedDict

from tinygrad.tensor import Tensor
from tinygrad.engine import jit as jit_module
from tinygrad.helpers import disable_gc


class AdmittedReplay:
  """Reuse validated bindings only while the exact captured graph and input UOps remain unchanged."""
  def __init__(self, jit):
    self.jit = jit
    self.cache = OrderedDict()
    self.epoch = None

  def __getattr__(self, name):
    return getattr(self.jit, name)

  def clear(self):
    self.cache.clear()
    self.epoch = None

  def reset(self):
    self.clear()
    return self.jit.reset()

  @disable_gc()
  def __call__(self, *args, **kwargs):
    try:
      return self._run(*args, **kwargs)
    except Exception:
      self.clear()
      raise

  def _run(self, *args, **kwargs):
    captured = self.jit.captured
    epoch = (captured, captured._linear) if captured is not None else None
    if self.epoch is None or epoch is None or any(a is not b for a, b in zip(epoch, self.epoch, strict=True)):
      self.clear()
      self.epoch = epoch
    if (args or not kwargs or any(value.__class__ is not Tensor for value in kwargs.values()) or
        captured is None or self.jit.cnt < 2 or not jit_module.JIT or jit_module.capturing):
      return self.jit(*args, **kwargs)
    ordered = tuple(sorted(kwargs.items()))
    key = tuple((name, id(value), id(value.uop)) for name, value in ordered)
    if (entry := self.cache.get(key)) is not None:
      tensors, uops, names, info, inputs, variables = entry
      if (tuple(captured.expected_names) == names and tuple(captured.expected_input_info) == info and
          all(value is old and value.uop is uop and uop.is_realized
              for (_, value), old, uop in zip(ordered, tensors, uops, strict=True))):
        self.cache.move_to_end(key)
        result = captured(inputs, variables)
        self.jit.cnt += 1
        return result
      del self.cache[key]
    result = self.jit(*args, **kwargs)
    inputs, variables, names, info = jit_module._prepare_jit_inputs((), kwargs)
    if not variables and self.jit.captured is captured and captured.expected_names == names and captured.expected_input_info == info:
      self.cache[key] = (tuple(value for _, value in ordered), tuple(value.uop for _, value in ordered),
                         tuple(names), tuple(info), inputs, variables)
      if len(self.cache) > 32:
        self.cache.popitem(last=False)
    return result


def enable_replay(model):
  replays = []
  for name in ('warp_enqueue', 'run_policy'):
    jit = getattr(model, name)
    if type(jit) is jit_module._TinyJit:
      replay = AdmittedReplay(jit)
      setattr(model, name, replay)
      replays.append(replay)
  model._laboratory_replays = tuple(replays)

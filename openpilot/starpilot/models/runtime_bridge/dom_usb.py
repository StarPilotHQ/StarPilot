"""Explicit Dom pre-HCQ reconstruction using the existing native guards."""
from tinygrad.uop.ops import Ops
from tinygrad.runtime.support.hcq2 import HCQInfo

from openpilot.starpilot.models.runtime_bridge.qcom import Protection
from openpilot.starpilot.models.runtime_bridge.usb import USBProtection


class DomUSBProtection(USBProtection):
  def protect_call(self, call, *, fresh_source=False):
    if isinstance(call.arg.aux, HCQInfo) and call.arg.aux.device == ('QCOM',) and call.body.op is Ops.SINK:
      return Protection.protect_call(self, call, fresh_source=True)
    return super().protect_call(call, fresh_source=fresh_source)

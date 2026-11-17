# Driver inputs

Ordinary cruise +/- changes the driver's SET ceiling. With **Override speed limit
with +** enabled, a qualifying physical tap past the adjusted limit also lets cruise
use SET. The accelerator override has its own policy. See [SET speed and accelerator](README.md#set-speed-and-accelerator).

Source loss uses the previous accepted limit plus the current offset, up to SET.
That remembered limit survives re-engagement and restarts; applying it still needs
current vehicle coordinates and system-longitudinal authority. See [README](README.md).

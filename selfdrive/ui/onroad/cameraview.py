from __future__ import annotations

import math
import os
import platform
import weakref
import numpy as np
import pyray as rl

from msgq.visionipc import VisionIpcClient, VisionStreamType, VisionBuf
from openpilot.common.swaglog import cloudlog
from openpilot.system.hardware import HARDWARE, PC, TICI
from openpilot.system.ui.lib.application import gui_app, RECORD_HUD_ONLY
from openpilot.system.ui.lib.egl import (
  init_egl, is_egl_initialized, finish_gl, create_egl_image, destroy_egl_image,
  bind_egl_image_to_texture, create_external_texture, destroy_external_texture, EGLImage,
)
from openpilot.system.ui.widgets import Widget
from openpilot.selfdrive.ui.ui_state import ui_state

CONNECTION_RETRY_INTERVAL = 0.2  # seconds between connection attempts
STREAM_DISCOVERY_REFRESH_INTERVAL = 0.5  # seconds between nonblocking stream advertisements


def _default_force_texture_camera(device_type: str) -> bool:
  return device_type == "mici"


DEVICE_TYPE = HARDWARE.get_device_type()
MICI_FORCE_TEXTURE_CAMERA = os.getenv(
  "MICI_FORCE_TEXTURE_CAMERA",
  "1" if _default_force_texture_camera(DEVICE_TYPE) else "0",
) == "1"
# One stale frame can be normal ring-buffer reuse; repeated consecutive regressions demote EGL.
EGL_REGRESSIVE_FRAME_FALLBACK_THRESHOLD = 3

VERSION = """
#version 300 es
precision mediump float;
"""
if platform.system() == "Darwin":
  VERSION = """
    #version 330 core
  """


VERTEX_SHADER = VERSION + """
in vec3 vertexPosition;
in vec2 vertexTexCoord;
in vec3 vertexNormal;
in vec4 vertexColor;
uniform mat4 mvp;
out vec2 fragTexCoord;
out vec4 fragColor;
void main() {
  fragTexCoord = vertexTexCoord;
  fragColor = vertexColor;
  gl_Position = mvp * vec4(vertexPosition, 1.0);
}
"""

FRAME_FRAGMENT_SHADER_EXTERNAL = """
  #version 300 es
  #extension GL_OES_EGL_image_external_essl3 : enable
  precision mediump float;
  in vec2 fragTexCoord;
  uniform samplerExternalOES texture0;
  uniform int enhance_driver;
  out vec4 fragColor;
  void main() {
    vec4 color = texture(texture0, fragTexCoord);
    color.rgb = pow(color.rgb, vec3(1.0/1.28));
    if (enhance_driver == 1) {
      float brightness = 1.1;
      color.rgb = color.rgb + 0.15;
      color.rgb = clamp((color.rgb - 0.5) * (brightness * 0.8) + 0.5, 0.0, 1.0);
      color.rgb = color.rgb * color.rgb * (3.0 - 2.0 * color.rgb);
      color.rgb = pow(color.rgb, vec3(0.8));
    }
    fragColor = vec4(color.rgb, color.a);
  }
  """

FRAME_FRAGMENT_SHADER_YUV = VERSION + """
  in vec2 fragTexCoord;
  uniform sampler2D texture0;
  uniform sampler2D texture1;
  uniform int enhance_driver;
  out vec4 fragColor;
  void main() {
    float y = texture(texture0, fragTexCoord).r;
    vec2 uv = texture(texture1, fragTexCoord).ra - 0.5;
    vec3 rgb = vec3(y + 1.402*uv.y, y - 0.344*uv.x - 0.714*uv.y, y + 1.772*uv.x);
    if (enhance_driver == 1) {
      float brightness = 1.1;
      rgb = rgb + 0.15;
      rgb = clamp((rgb - 0.5) * (brightness * 0.8) + 0.5, 0.0, 1.0);
      rgb = rgb * rgb * (3.0 - 2.0 * rgb);
      rgb = pow(rgb, vec3(0.8));
    }
    fragColor = vec4(rgb, 1.0);
  }
  """

FRAME_FRAGMENT_SHADER_EXTERNAL_MICI = """
  #version 300 es
  #extension GL_OES_EGL_image_external_essl3 : enable
  precision mediump float;
  in vec2 fragTexCoord;
  uniform samplerExternalOES texture0;
  uniform int enhance_driver;
  out vec4 fragColor;
  void main() {
    vec4 color = texture(texture0, fragTexCoord);
    color.rgb = clamp((color.rgb - 0.5) * 1.2 + 0.5, 0.0, 1.0);
    color.rgb = pow(color.rgb, vec3(1.0/1.28));
    if (enhance_driver == 1) {
      float brightness = 1.1;
      color.rgb = color.rgb + 0.15;
      color.rgb = clamp((color.rgb - 0.5) * (brightness * 0.8) + 0.5, 0.0, 1.0);
      color.rgb = color.rgb * color.rgb * (3.0 - 2.0 * color.rgb);
      color.rgb = pow(color.rgb, vec3(0.8));
    }
    fragColor = vec4(color.rgb, color.a);
  }
  """

FRAME_FRAGMENT_SHADER_YUV_MICI = VERSION + """
  in vec2 fragTexCoord;
  uniform sampler2D texture0;
  uniform sampler2D texture1;
  uniform int enhance_driver;
  out vec4 fragColor;
  void main() {
    float y = texture(texture0, fragTexCoord).r;
    vec2 uv = texture(texture1, fragTexCoord).ra - 0.5;
    vec3 rgb = vec3(y + 1.402*uv.y, y - 0.344*uv.x - 0.714*uv.y, y + 1.772*uv.x);
    rgb = clamp((rgb - 0.5) * 1.2 + 0.5, 0.0, 1.0);
    if (enhance_driver == 1) {
      float brightness = 1.1;
      rgb = rgb + 0.15;
      rgb = clamp((rgb - 0.5) * (brightness * 0.8) + 0.5, 0.0, 1.0);
      rgb = rgb * rgb * (3.0 - 2.0 * rgb);
      rgb = pow(rgb, vec3(0.8));
    }
    fragColor = vec4(rgb, 1.0);
  }
  """


class CameraView(Widget):
  _use_upstream_engaged_color = False
  _use_roi_upload = False

  def __init__(self, name: str, stream_type: VisionStreamType):
    super().__init__()
    self._name = name
    # Primary stream
    self.client: VisionIpcClient | None = None
    self._stream_type = stream_type
    self.available_streams: list[VisionStreamType] = []

    # Target stream for switching
    self._target_client: VisionIpcClient | None = None
    self._target_stream_type: VisionStreamType | None = None
    self._switching: bool = False

    self._texture_needs_update = True
    self.last_connection_attempt: float = 0.0
    self._last_stream_discovery: float = -float("inf")
    self._last_switch_request: float = -float("inf")
    self._use_egl = TICI and not MICI_FORCE_TEXTURE_CAMERA and init_egl()
    if TICI and MICI_FORCE_TEXTURE_CAMERA:
      cloudlog.warning("CameraView EGL disabled by MICI_FORCE_TEXTURE_CAMERA, using texture rendering")
    elif TICI and not self._use_egl:
      cloudlog.error("CameraView EGL init failed, falling back to texture rendering")

    self._enhance_driver_val = rl.ffi.new("int[1]", [0])
    self._load_frame_shader()
    if self._use_egl and not self.shader.id:
      cloudlog.error("CameraView EGL shader failed, falling back to texture rendering")
      self._use_egl = False
      self._load_frame_shader()

    self.frame: VisionBuf | None = None
    self._last_frame_id = -1
    self._regressive_frame_count = 0
    self.texture_y: rl.Texture | None = None
    self.texture_uv: rl.Texture | None = None
    self._texture_frame_data: np.ndarray | None = None
    self._texture_upload_rect: tuple[int, int, int, int] | None = None

    # EGL resources
    self.egl_images: dict[int, EGLImage] = {}
    self.egl_texture: rl.Texture | None = None
    self._external_texture_id = 0

    self._placeholder_color: rl.Color | None = None
    self._closed = False
    self._onroad_reentry_pending = False
    self._reentry_stream_selected = False

    if self._use_egl and not self._create_egl_texture():
      cloudlog.error("CameraView EGL texture creation failed, falling back to texture rendering")
      self._use_egl = False
      if self.shader and self.shader.id:
        rl.unload_shader(self.shader)
        self.shader.id = 0
      self._load_frame_shader()
    cloudlog.info(f"CameraView using {'EGL zero-copy' if self._use_egl else 'texture-copy'} rendering for {stream_type}")

    self_ref = weakref.ref(self)

    def offroad_transition_callback():
      if (view := self_ref()) is not None:
        view._offroad_transition()

    self._offroad_transition_callback = offroad_transition_callback
    ui_state.add_offroad_transition_callback(self._offroad_transition_callback)

  def _offroad_transition(self):
    self._reset_camera_connection()

  def _retire_active_client(self) -> None:
    """Release graphics, frame, and client as one camera generation."""
    self._clear_textures()
    self.frame = None
    self.client = None

  def _reset_camera_connection(self):
    self._cancel_pending_switch()
    self._retire_active_client()
    self._last_frame_id = -1
    self._regressive_frame_count = 0
    self.available_streams.clear()
    self._texture_needs_update = True
    self.last_connection_attempt = 0.0
    self._last_stream_discovery = -float("inf")
    self._last_switch_request = -float("inf")
    self._onroad_reentry_pending = ui_state.is_onroad()
    self._reentry_stream_selected = False

  def _set_placeholder_color(self, color: rl.Color):
    """Set a placeholder color to be drawn when no frame is available."""
    self._placeholder_color = color

  def _refresh_available_streams(self) -> None:
    current_time = rl.get_time()
    if current_time - getattr(self, "_last_stream_discovery", -float("inf")) < STREAM_DISCOVERY_REFRESH_INTERVAL:
      return
    self._last_stream_discovery = current_time

    streams = VisionIpcClient.available_streams(self._name, block=False)
    if streams:
      self.available_streams = list(streams)

  def switch_stream(self, stream_type: VisionStreamType) -> None:
    if getattr(self, "_onroad_reentry_pending", False):
      if (getattr(self, "_reentry_stream_selected", False) and self._stream_type == stream_type and
          (not self._switching or self._target_stream_type == stream_type)):
        return
      self._select_reentry_stream(stream_type)
      return

    current_time = rl.get_time()
    if self._switching:
      if self._target_stream_type == stream_type:
        return
      if self._stream_type == stream_type:
        self._cancel_pending_switch()
        return
      if current_time - getattr(self, "_last_switch_request", -float("inf")) < CONNECTION_RETRY_INTERVAL:
        return
      self._cancel_pending_switch()

    if self._stream_type == stream_type:
      return

    if current_time - getattr(self, "_last_switch_request", -float("inf")) < CONNECTION_RETRY_INTERVAL:
      return

    cloudlog.debug(f'Preparing switch from {self._stream_type} to {stream_type}')

    if self._target_client:
      del self._target_client

    self._target_stream_type = stream_type
    self._target_client = VisionIpcClient(self._name, stream_type, conflate=True)
    self._switching = True
    self._last_switch_request = current_time

  def _cancel_pending_switch(self) -> None:
    if self._target_client is not None:
      cloudlog.debug(f"Cancelling pending camera switch to {self._target_stream_type}")
    self._target_client = None
    self._target_stream_type = None
    self._switching = False

  def _discard_pending_client(self) -> None:
    """Discard a failed candidate while retaining the requested stream."""
    self._target_client = None
    self._switching = False

  def _select_reentry_stream(self, stream_type: VisionStreamType) -> None:
    """Select the desired stream before displaying any post-transition frame."""
    self._cancel_pending_switch()

    if self._stream_type != stream_type:
      self._retire_active_client()
      self._stream_type = stream_type

    self.frame = None
    self._last_frame_id = -1
    self._regressive_frame_count = 0
    self._texture_needs_update = True
    self._reentry_stream_selected = True

  @property
  def stream_type(self) -> VisionStreamType:
    return self._stream_type

  def close(self) -> None:
    if self._closed:
      return
    self._closed = True

    callback = getattr(self, "_offroad_transition_callback", None)
    if callback is not None:
      ui_state.remove_offroad_transition_callback(callback)
      self._offroad_transition_callback = None
    self._cancel_pending_switch()
    self._retire_active_client()

    # Clean up shader
    if self.shader and self.shader.id:
      rl.unload_shader(self.shader)
      self.shader.id = 0

    self.frame = None
    self._last_frame_id = -1
    self.available_streams.clear()
    self._onroad_reentry_pending = False
    self._reentry_stream_selected = False

  def __del__(self):
    self.close()

  def _calc_frame_matrix(self, rect: rl.Rectangle) -> np.ndarray:
    if not self.frame:
      return np.eye(3)

    # Calculate aspect ratios
    widget_aspect_ratio = rect.width / rect.height
    frame_aspect_ratio = self.frame.width / self.frame.height

    # Calculate scaling factors to maintain aspect ratio
    zx = min(frame_aspect_ratio / widget_aspect_ratio, 1.0)
    zy = min(widget_aspect_ratio / frame_aspect_ratio, 1.0)

    return np.array([
      [zx, 0.0, 0.0],
      [0.0, zy, 0.0],
      [0.0, 0.0, 1.0]
    ])

  def _render(self, rect: rl.Rectangle):
    if self._switching:
      self._handle_switch()

    if self._onroad_reentry_pending and not self._reentry_stream_selected:
      # Standalone CameraView users have no higher-level stream selector.
      self._select_reentry_stream(self._stream_type)

    if not self._ensure_connection():
      self._draw_placeholder(rect)
      return

    if self._use_egl:
      self._observe_displayed_frame()

    # Try to get a new buffer without blocking
    buffer = self.client.recv(timeout_ms=0)
    if buffer:
      self._accept_frame(buffer, self.client.frame_id)
    elif not self.client.is_connected():
      # ensure we clear the displayed frame when the connection is lost
      self.frame = None

    if not self.frame:
      self._draw_placeholder(rect)
      return

    transform = self._calc_frame_matrix(rect)
    src_rect = rl.Rectangle(0, 0, float(self.frame.width), float(self.frame.height))
    # Flip driver camera horizontally
    if self._stream_type == VisionStreamType.VISION_STREAM_DRIVER:
      src_rect.width = -src_rect.width

    # Calculate scale
    scale_x = rect.width * transform[0, 0]  # zx
    scale_y = rect.height * transform[1, 1]  # zy

    # Calculate base position (centered)
    x_offset = rect.x + (rect.width - scale_x) / 2
    y_offset = rect.y + (rect.height - scale_y) / 2

    x_offset += transform[0, 2] * rect.width / 2
    y_offset += transform[1, 2] * rect.height / 2

    dst_rect = rl.Rectangle(x_offset, y_offset, scale_x, scale_y)

    # Desktop-only transparent HUD recording: keep camera frame processing and
    # geometry updates for correctly aligned model overlays, but skip camera pixels.
    if RECORD_HUD_ONLY:
      return

    if self._use_egl:
      try:
        rendered = self._render_egl(src_rect, dst_rect)
      except Exception:
        cloudlog.exception("CameraView EGL rendering failed")
        rendered = False
      if not rendered:
        self._fallback_to_textures("EGL frame rendering failed")

    if not self._use_egl:
      if (self._use_roi_upload and self.frame.height % 2 == 0 and self.frame.stride % 2 == 0 and
          self._stream_type in (VisionStreamType.VISION_STREAM_ROAD, VisionStreamType.VISION_STREAM_WIDE_ROAD)):
        self._render_textures(src_rect, dst_rect, rect)
      else:
        self._render_textures(src_rect, dst_rect)

  def _draw_placeholder(self, rect: rl.Rectangle):
    if self._placeholder_color:
      rl.draw_rectangle_rec(rect, self._placeholder_color)

  def _load_frame_shader(self) -> None:
    if self._use_upstream_engaged_color:
      frame_shader = FRAME_FRAGMENT_SHADER_EXTERNAL_MICI if self._use_egl else FRAME_FRAGMENT_SHADER_YUV_MICI
    else:
      frame_shader = FRAME_FRAGMENT_SHADER_EXTERNAL if self._use_egl else FRAME_FRAGMENT_SHADER_YUV
    self.shader = rl.load_shader_from_memory(VERTEX_SHADER, frame_shader)
    self._texture1_loc = -1 if self._use_egl else rl.get_shader_location(self.shader, "texture1")
    self._enhance_driver_loc = rl.get_shader_location(self.shader, "enhance_driver")

  def _update_shader_state(self) -> None:
    self._enhance_driver_val[0] = 1 if self._stream_type == VisionStreamType.VISION_STREAM_DRIVER else 0
    if self._enhance_driver_loc >= 0:
      rl.set_shader_value(self.shader, self._enhance_driver_loc, self._enhance_driver_val,
                          rl.ShaderUniformDataType.SHADER_UNIFORM_INT)

  def _observe_displayed_frame(self) -> None:
    if self.frame is not None:
      client_frame_id = getattr(self.client, "frame_id", -1) if hasattr(self, "client") and self.client is not None else -1
      frame_id = getattr(self.frame, "frame_id", client_frame_id)
      self._last_frame_id = max(self._last_frame_id, int(frame_id))

  def _accept_frame(self, frame: VisionBuf, packet_frame_id: int) -> bool:
    content_frame_id = int(getattr(frame, "frame_id", packet_frame_id))
    if content_frame_id != packet_frame_id:
      cloudlog.debug(
        f"Dropping inconsistent {self._name} frame: content={content_frame_id}, packet={packet_frame_id}"
      )
      return False
    # Device camera frame IDs are monotonic; reject older reusable ring-buffer
    # slots there. Desktop replay intentionally lowers IDs when seeking backward.
    if not PC and content_frame_id < self._last_frame_id:
      self._regressive_frame_count += 1
      if self._regressive_frame_count == 1 or self._regressive_frame_count % 100 == 0:
        message = f"Dropping regressive {self._name} frame: content={content_frame_id}, packet={packet_frame_id}, "
        message += f"displayed={self._last_frame_id}, idx={frame.idx}, count={self._regressive_frame_count}"
        cloudlog.warning(message)
      if getattr(self, "_use_egl", False) and self._regressive_frame_count >= EGL_REGRESSIVE_FRAME_FALLBACK_THRESHOLD:
        self._fallback_to_textures("repeated regressive frames")
      return False

    self.frame = frame
    self._last_frame_id = content_frame_id
    self._regressive_frame_count = 0
    self._texture_needs_update = True
    self._onroad_reentry_pending = False
    self._reentry_stream_selected = False
    return True

  def _render_egl(self, src_rect: rl.Rectangle, dst_rect: rl.Rectangle) -> bool:
    """Render using EGL for direct buffer access."""
    if self.frame is None or self.egl_texture is None or not self.egl_texture.id or not self._external_texture_id:
      return False

    idx = self.frame.idx
    egl_image = self.egl_images.get(idx)
    if egl_image is None:
      egl_image = create_egl_image(self.frame.width, self.frame.height, self.frame.stride, self.frame.fd, self.frame.uv_offset)
      if egl_image is None:
        return False
      self.egl_images[idx] = egl_image

    self.egl_texture.width = self.frame.width
    self.egl_texture.height = self.frame.height
    bind_egl_image_to_texture(self._external_texture_id, egl_image)

    rl.begin_shader_mode(self.shader)
    try:
      self._update_shader_state()
      rl.draw_texture_pro(self.egl_texture, src_rect, dst_rect, rl.Vector2(0, 0), 0.0, rl.WHITE)
    finally:
      rl.end_shader_mode()
    return True

  def _fallback_to_textures(self, reason: str) -> None:
    if not self._use_egl:
      return

    cloudlog.error(f"CameraView switching from EGL to texture rendering: {reason}")
    self._use_egl = False
    try:
      self._clear_textures()
    except Exception:
      cloudlog.exception("CameraView EGL cleanup failed during texture fallback")

    if self.shader and self.shader.id:
      try:
        rl.unload_shader(self.shader)
      except Exception:
        cloudlog.exception("CameraView EGL shader cleanup failed during texture fallback")
      self.shader.id = 0
    try:
      self._load_frame_shader()
      self._initialize_textures()
      self._texture_needs_update = True
    except Exception:
      cloudlog.exception("CameraView texture fallback initialization failed")

  def _upload_texture_region(self, src_rect: rl.Rectangle, dst_rect: rl.Rectangle, clip: rl.Rectangle) -> bool:
    frame = self.frame
    stride, height = frame.stride, frame.height
    if self._texture_needs_update:
      # Camera buffers can be reused before a later viewport change exposes more pixels.
      size = stride * height * 3 // 2
      if self._texture_frame_data is None or len(self._texture_frame_data) != size:
        self._texture_frame_data = np.empty(size, dtype=np.uint8)
      self._texture_frame_data[:stride * height] = frame.data[:stride * height]
      self._texture_frame_data[stride * height:] = frame.data[frame.uv_offset:frame.uv_offset + stride * (height // 2)]
      self._texture_upload_rect = None
      self._texture_needs_update = False

    left = max(clip.x, dst_rect.x)
    top = max(clip.y, dst_rect.y)
    right = min(clip.x + clip.width, dst_rect.x + dst_rect.width)
    bottom = min(clip.y + clip.height, dst_rect.y + dst_rect.height)
    if left >= right or top >= bottom:
      return False

    x0 = max(0, math.floor(src_rect.x + (left - dst_rect.x) * src_rect.width / dst_rect.width) - 2) // 2 * 2
    y0 = max(0, math.floor(src_rect.y + (top - dst_rect.y) * src_rect.height / dst_rect.height) - 2) // 2 * 2
    x1 = min(stride, (math.ceil(src_rect.x + (right - dst_rect.x) * src_rect.width / dst_rect.width) + 3) // 2 * 2)
    y1 = min(height, (math.ceil(src_rect.y + (bottom - dst_rect.y) * src_rect.height / dst_rect.height) + 3) // 2 * 2)
    if self._texture_upload_rect is not None:
      prev_x0, prev_y0, prev_x1, prev_y1 = self._texture_upload_rect
      if prev_x0 <= x0 and prev_y0 <= y0 and prev_x1 >= x1 and prev_y1 >= y1:
        return True
      x0, y0 = min(x0, prev_x0), min(y0, prev_y0)
      x1, y1 = max(x1, prev_x1), max(y1, prev_y1)

    y_plane = self._texture_frame_data[:stride * height].reshape(height, stride)
    uv_plane = self._texture_frame_data[stride * height:].reshape(height // 2, stride // 2, 2)
    y_data = np.ascontiguousarray(y_plane[y0:y1, x0:x1])
    uv_data = np.ascontiguousarray(uv_plane[y0 // 2:y1 // 2, x0 // 2:x1 // 2])
    rl.update_texture_rec(self.texture_y, rl.Rectangle(x0, y0, x1 - x0, y1 - y0), rl.ffi.cast("void *", rl.ffi.from_buffer(y_data)))
    rl.update_texture_rec(self.texture_uv, rl.Rectangle(x0 // 2, y0 // 2, (x1 - x0) // 2, (y1 - y0) // 2), rl.ffi.cast("void *", rl.ffi.from_buffer(uv_data)))
    self._texture_upload_rect = x0, y0, x1, y1
    return True

  def _render_textures(self, src_rect: rl.Rectangle, dst_rect: rl.Rectangle, clip: rl.Rectangle | None = None) -> None:
    """Copy camera data into ordinary Raylib textures before drawing.

    Raylib batches camera draws as GL_TEXTURE_2D. Imported EGL images are
    GL_TEXTURE_EXTERNAL_OES objects and cannot safely pass through that path;
    copying also prevents the GPU from sampling camerad's reusable buffers
    after they have been handed back to the producer.
    """
    if (self.texture_y is None or not self.texture_y.id or
        self.texture_uv is None or not self.texture_uv.id or self.frame is None):
      return

    # Update textures with new frame data
    if clip is not None:
      if not self._upload_texture_region(src_rect, dst_rect, clip):
        return
    elif self._texture_needs_update:
      y_data = self.frame.data[: self.frame.uv_offset]
      uv_data = self.frame.data[self.frame.uv_offset:]

      rl.update_texture(self.texture_y, rl.ffi.cast("void *", rl.ffi.from_buffer(y_data)))
      rl.update_texture(self.texture_uv, rl.ffi.cast("void *", rl.ffi.from_buffer(uv_data)))
      self._texture_needs_update = False

    # Render with shader
    rl.begin_shader_mode(self.shader)
    try:
      self._update_shader_state()
      rl.set_shader_value_texture(self.shader, self._texture1_loc, self.texture_uv)
      rl.draw_texture_pro(self.texture_y, src_rect, dst_rect, rl.Vector2(0, 0), 0.0, rl.WHITE)
    finally:
      rl.end_shader_mode()

  def _ensure_connection(self) -> bool:
    if self.client is not None and self.client.is_connected():
      return True

    # A pending candidate owns the connection attempt. Poll it until its first
    # frame arrives instead of reconnecting the same client in place.
    if self._switching:
      self._handle_switch()
      return self.client is not None and self.client.is_connected()

    if self.client is not None:
      self._retire_active_client()
    self._last_frame_id = -1
    self._regressive_frame_count = 0
    self.available_streams.clear()

    # Throttle connection attempts
    current_time = rl.get_time()
    if current_time - self.last_connection_attempt < CONNECTION_RETRY_INTERVAL:
      return False
    self.last_connection_attempt = current_time

    stream_type = self._target_stream_type or self._stream_type
    self._target_stream_type = stream_type
    self._target_client = VisionIpcClient(self._name, stream_type, conflate=True)
    self._switching = True
    self._handle_switch()
    return self.client is not None and self.client.is_connected()

  def _handle_switch(self) -> None:
    """Check if target stream is ready and switch immediately."""
    if not self._target_client or not self._switching:
      return

    # Try to connect target if needed
    if not self._target_client.is_connected():
      if not self._target_client.connect(False) or not self._target_client.num_buffers:
        self._discard_pending_client()
        return

      cloudlog.debug(f"Target stream connected: {self._target_stream_type}")

    # Check if target has frames ready
    target_frame = self._target_client.recv(timeout_ms=0)
    if target_frame:
      packet_frame_id = int(getattr(self._target_client, "frame_id", -1))
      content_frame_id = int(getattr(target_frame, "frame_id", packet_frame_id))
      if content_frame_id != packet_frame_id:
        message = f"Discarding inconsistent {self._name} target frame: content={content_frame_id}, "
        message += f"packet={packet_frame_id}, stream={self._target_stream_type}"
        cloudlog.warning(message)
        self._discard_pending_client()
        return
      self._complete_switch(target_frame)
    elif not self._target_client.is_connected():
      # A failed recv can invalidate the server/buffer generation. Never
      # reconnect this client; the next attempt must use a fresh candidate.
      self._discard_pending_client()

  def _complete_switch(self, target_frame: VisionBuf) -> None:
    """Instantly switch to target stream."""
    cloudlog.debug(f"Switching to {self._target_stream_type}")

    target_client = self._target_client
    target_stream_type = self._target_stream_type
    self._target_client = None
    self._target_stream_type = None
    self._switching = False

    # Retire the old generation before exposing the new client and frame.
    self._retire_active_client()

    # Switch to target
    self.client = target_client
    self._stream_type = target_stream_type
    self.frame = target_frame
    client_frame_id = getattr(self.client, "frame_id", -1) if self.client is not None else -1
    self._last_frame_id = int(getattr(self.frame, "frame_id", client_frame_id)) if self.frame is not None else -1
    self._regressive_frame_count = 0
    self._texture_needs_update = True
    self._onroad_reentry_pending = False
    self._reentry_stream_selected = False

    # Initialize textures for new stream
    self._initialize_textures()
    available_streams = getattr(self.client, "available_streams", None)
    if available_streams is not None:
      self.available_streams = list(available_streams(self._name, block=False))

  def _initialize_textures(self):
    self._clear_textures()
    if self._use_egl:
      if not self._create_egl_texture():
        self._fallback_to_textures("EGL texture creation failed")
    else:
      self.texture_y = rl.load_texture_from_image(rl.Image(None, int(self.client.stride),
        int(self.client.height), 1, rl.PixelFormat.PIXELFORMAT_UNCOMPRESSED_GRAYSCALE))
      self.texture_uv = rl.load_texture_from_image(rl.Image(None, int(self.client.stride // 2),
        int(self.client.height // 2), 1, rl.PixelFormat.PIXELFORMAT_UNCOMPRESSED_GRAY_ALPHA))
      if not self.texture_y.id or not self.texture_uv.id:
        cloudlog.error("CameraView texture-copy texture creation failed")
        self._clear_textures()

  def _create_egl_texture(self) -> bool:
    temp_image = None
    try:
      temp_image = rl.gen_image_color(1, 1, rl.BLACK)
      texture = rl.load_texture_from_image(temp_image)
      if texture is None or not texture.id:
        self.egl_texture = None
        return False
      self.egl_texture = texture
      self._external_texture_id = create_external_texture()
      if not self._external_texture_id:
        rl.unload_texture(self.egl_texture)
        self.egl_texture = None
        return False
      return True
    except Exception:
      if self._external_texture_id:
        destroy_external_texture(self._external_texture_id)
      self._external_texture_id = 0
      if self.egl_texture is not None and self.egl_texture.id:
        rl.unload_texture(self.egl_texture)
      self.egl_texture = None
      cloudlog.exception("CameraView failed to create EGL texture")
      return False
    finally:
      if temp_image is not None:
        try:
          rl.unload_image(temp_image)
        except Exception:
          cloudlog.exception("CameraView failed to unload temporary EGL image")

  def _clear_textures(self):
    self._texture_frame_data = None
    self._texture_upload_rect = None
    if ((self._external_texture_id or self.egl_texture is not None or self.egl_images) and is_egl_initialized()):
      try:
        # Raylib queues draw calls. Submit them before waiting for the GPU so
        # no pending batch can still reference an EGL-backed texture.
        rl.rl_draw_render_batch_active()
        finish_gl()
      except Exception:
        cloudlog.exception("CameraView failed to synchronize EGL resources")

    if self.texture_y is not None:
      if self.texture_y.id:
        rl.unload_texture(self.texture_y)
      self.texture_y = None

    if self.texture_uv is not None:
      if self.texture_uv.id:
        rl.unload_texture(self.texture_uv)
      self.texture_uv = None

    if self._external_texture_id:
      destroy_external_texture(self._external_texture_id)
    self._external_texture_id = 0

    if self.egl_texture and self.egl_texture.id:
      rl.unload_texture(self.egl_texture)
    self.egl_texture = None

    for data in self.egl_images.values():
      destroy_egl_image(data)
    self.egl_images = {}


if __name__ == "__main__":
  gui_app.init_window("camera view")
  road = CameraView("camerad", VisionStreamType.VISION_STREAM_ROAD)
  for _ in gui_app.render():
    road.render(rl.Rectangle(0, 0, gui_app.width, gui_app.height))

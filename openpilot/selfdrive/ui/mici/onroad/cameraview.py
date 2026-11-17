import time
import numpy as np
from openpilot.starpilot.ui.raylib import rl

from openpilot.cereal.visionipc import VisionStreamType
from msgq.visionipc import VisionIpcClient
from openpilot.common.swaglog import cloudlog
from openpilot.common.hardware import COMMA_HARDWARE
from openpilot.system.ui.lib.application import gui_app
from openpilot.selfdrive.ui.onroad.cameraview import CameraView as BaseCameraView, VERSION, VERTEX_SHADER as VERTEX_SHADER, CONNECTION_RETRY_INTERVAL
from openpilot.selfdrive.ui.ui_state import ui_state, UIStatus
from openpilot.starpilot.ui.camera_color import ROAD_COLOR_SHADER, road_color_strength





# Choose fragment shader based on platform capabilities
if COMMA_HARDWARE:
  FRAME_FRAGMENT_SHADER = """
    #version 300 es
    #extension GL_OES_EGL_image_external_essl3 : enable
    precision mediump float;
    in vec2 fragTexCoord;
    uniform samplerExternalOES texture0;
    out vec4 fragColor;
    uniform int engaged;
    uniform int enhance_driver;
    uniform float color_strength;
    """ + ROAD_COLOR_SHADER + """

    void main() {
      vec4 color = texture(texture0, fragTexCoord);
      if (enhance_driver == 1) {
        if (engaged == 1) {
          float gray = dot(color.rgb, vec3(0.299, 0.587, 0.114));
          color.rgb = mix(vec3(gray), color.rgb, 0.2);
          color.rgb = clamp((color.rgb - 0.5) * 1.2 + 0.5, 0.0, 1.0);
          color.rgb = pow(color.rgb, vec3(1.0/1.28));
        } else {
          color.rgb *= 0.85;
        }
        float brightness = 1.1;
        color.rgb = color.rgb + 0.15;
        color.rgb = clamp((color.rgb - 0.5) * (brightness * 0.8) + 0.5, 0.0, 1.0);
        color.rgb = color.rgb * color.rgb * (3.0 - 2.0 * color.rgb);
        color.rgb = pow(color.rgb, vec3(0.8));
      } else {
        color.rgb = road_color(color.rgb, color_strength);
      }
      fragColor = vec4(color.rgb, color.a);
    }
    """
else:
  FRAME_FRAGMENT_SHADER = VERSION + """
    in vec2 fragTexCoord;
    uniform sampler2D texture0;
    uniform sampler2D texture1;
    out vec4 fragColor;
    uniform int engaged;
    uniform int enhance_driver;
    uniform float color_strength;
    """ + ROAD_COLOR_SHADER + """

    void main() {
      float y = texture(texture0, fragTexCoord).r;
      vec2 uv = texture(texture1, fragTexCoord).ra - 0.5;
      vec3 rgb = vec3(y + 1.402*uv.y, y - 0.344*uv.x - 0.714*uv.y, y + 1.772*uv.x);
      if (enhance_driver == 1) {
        if (engaged == 1) {
          float gray = dot(rgb, vec3(0.299, 0.587, 0.114));
          rgb = mix(vec3(gray), rgb, 0.2);
          rgb = clamp((rgb - 0.5) * 1.2 + 0.5, 0.0, 1.0);
        } else {
          rgb *= 0.85;
        }
        float brightness = 1.1;
        rgb = rgb + 0.15;
        rgb = clamp((rgb - 0.5) * (brightness * 0.8) + 0.5, 0.0, 1.0);
        rgb = rgb * rgb * (3.0 - 2.0 * rgb);
        rgb = pow(rgb, vec3(0.8));
      } else {
        rgb = road_color(rgb, color_strength);
      }
      fragColor = vec4(rgb, 1.0);
    }
    """


class CameraView(BaseCameraView):
  FRAGMENT_SHADER = FRAME_FRAGMENT_SHADER

  def __init__(self, name: str, stream_type: VisionStreamType):
    super().__init__(name, stream_type)
    self.available_streams: list[VisionStreamType] = []

  def _init_clients(self, name: str, stream_type: VisionStreamType):
    self.client = VisionIpcClient(name, stream_type, conflate=True)

  def _init_color_uniforms(self, stream_type: VisionStreamType):
    self._engaged_loc = rl.get_shader_location(self.shader, "engaged")
    self._engaged_val = np.array([1], dtype=np.int32)
    self._color_strength_loc = rl.get_shader_location(self.shader, "color_strength")
    self._color_strength_val = np.zeros(1, dtype=np.float32)
    self._enhance_driver_loc = rl.get_shader_location(self.shader, "enhance_driver")
    self._enhance_driver_val = np.array([1 if stream_type == VisionStreamType.VISION_STREAM_CABIN else 0], dtype=np.int32)


  def _offroad_transition(self):
    if self.client is None:
      return
    # Retire the old buffer generation instead of draining it on the UI thread.
    self._clear_textures()
    self.frame = None
    self.available_streams.clear()
    self._target_client = None
    self._target_stream_type = None
    self._switching = False
    self.client = VisionIpcClient(self._name, self._stream_type, conflate=True)
    self._texture_needs_update = True
    self.last_connection_attempt = 0.0


  def switch_stream(self, stream_type: VisionStreamType) -> None:
    if self._stream_type == stream_type:
      return

    if self._switching and self._target_stream_type == stream_type:
      return

    cloudlog.debug(f'Preparing switch from {self._stream_type} to {stream_type}')

    if self._target_client:
      del self._target_client

    self._target_stream_type = stream_type
    self._target_client = VisionIpcClient(self._name, stream_type, conflate=True)
    self._switching = True


  def close(self) -> None:
    self._clear_textures()

    # Clean up EGL texture
    if COMMA_HARDWARE and self.egl_texture:
      rl.unload_texture(self.egl_texture)
      self.egl_texture = None

    # Clean up shader
    if self.shader and self.shader.id:
      rl.unload_shader(self.shader)
      self.shader.id = 0

    self.frame = None
    self.available_streams.clear()
    self.client = None




  def _render(self, rect: rl.Rectangle, *, paint: bool = True, poll_camera: bool = True):
    if (poll_camera and not self._poll_camera()) or self.frame is None:
      if paint:
        self._draw_placeholder(rect)
      return

    if not paint:
      return

    transform = self._calc_frame_matrix(rect)
    src_rect = rl.Rectangle(0, 0, float(self.frame.width), float(self.frame.height))
    # Flip cabin camera horizontally
    if self._stream_type == VisionStreamType.VISION_STREAM_CABIN:
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

    # Render with appropriate method
    if COMMA_HARDWARE:
      self._render_egl(src_rect, dst_rect)
    else:
      self._render_textures(src_rect, dst_rect)




  def _update_texture_color_filtering(self):
    self._color_strength_val[0] = road_color_strength(ui_state)
    self._engaged_val[0] = 1 if ui_state.status != UIStatus.DISENGAGED else 0
    self._enhance_driver_val[0] = int(self._stream_type == VisionStreamType.VISION_STREAM_CABIN)
    rl.set_shader_value(self.shader, self._color_strength_loc, self._color_strength_val, rl.ShaderUniformDataType.SHADER_UNIFORM_FLOAT)
    rl.set_shader_value(self.shader, self._engaged_loc, self._engaged_val, rl.ShaderUniformDataType.SHADER_UNIFORM_INT)
    rl.set_shader_value(self.shader, self._enhance_driver_loc, self._enhance_driver_val, rl.ShaderUniformDataType.SHADER_UNIFORM_INT)

  def _ensure_connection(self) -> bool:
    if not self.client.is_connected():
      self.frame = None
      self.available_streams.clear()

      # Retry scheduling must advance while recorded presentation is paused.
      current_time = time.monotonic()
      if current_time - self.last_connection_attempt < CONNECTION_RETRY_INTERVAL:
        return False
      self.last_connection_attempt = current_time

      if not self.client.connect(False) or not self.client.num_buffers:
        return False

      cloudlog.debug(f"Connected to {self._name} stream: {self._stream_type}, buffers: {self.client.num_buffers}")
      self._initialize_textures()
      self.available_streams = self.client.available_streams(self._name, block=False)

    return True


  def _complete_switch(self) -> None:
    """Instantly switch to target stream."""
    cloudlog.debug(f"Switching to {self._target_stream_type}")
    # Clean up current resources
    if self.client:
      del self.client

    # Switch to target
    assert self._target_client is not None and self._target_stream_type is not None
    self.client = self._target_client
    self._stream_type = self._target_stream_type
    self._texture_needs_update = True

    # Reset state
    self._target_client = None
    self._target_stream_type = None
    self._switching = False

    # Initialize textures for new stream
    self._initialize_textures()

  def _initialize_textures(self):
    self._clear_textures()
    if not COMMA_HARDWARE:
      self.texture_y = rl.load_texture_from_image(rl.Image(None, int(self.client.stride),
        int(self.client.height), 1, rl.PixelFormat.PIXELFORMAT_UNCOMPRESSED_GRAYSCALE))
      self.texture_uv = rl.load_texture_from_image(rl.Image(None, int(self.client.stride // 2),
        int(self.client.height // 2), 1, rl.PixelFormat.PIXELFORMAT_UNCOMPRESSED_GRAY_ALPHA))



if __name__ == "__main__":
  gui_app.init_window("camera view")
  road = CameraView("camerad", VisionStreamType.VISION_STREAM_NARROW_ROAD)
  for _ in gui_app.render():
    road.render(rl.Rectangle(0, 0, gui_app.width, gui_app.height))

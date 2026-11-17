#include <dlfcn.h>
#include <cstdint>
#include <stdexcept>
#include <string>
#include <vector>
#include <limits>
#include <nanobind/nanobind.h>
#include <nanobind/ndarray.h>
#include <nanobind/stl/string.h>
#include <raylib.h>
#include <rlgl.h>

namespace nb = nanobind;
namespace {
void *library = nullptr;
std::string backend;
template<typename T> T symbol(const char *name) {
  auto result = reinterpret_cast<T>(dlsym(library, name));
  if (!result) throw std::runtime_error(std::string("Missing active Raylib API: ") + name);
  return result;
}
struct GlyphView { nb::object owner; Font *font; };
struct RectangleView { nb::object owner; Font *font; };
int index(int i, int size) {
  if (i < 0) i += size;
  if (i < 0 || i >= size) throw nb::index_error();
  return i;
}
}
NB_MODULE(_raylib_extra, module) {
  nb::class_<GlyphView>(module, "GlyphView")
    .def("__len__", [](GlyphView &v) { return v.font->glyphCount; })
    .def("__getitem__", [](GlyphView &v, int i) { return v.font->glyphs[index(i, v.font->glyphCount)]; });
  nb::class_<RectangleView>(module, "RectangleView")
    .def("__len__", [](RectangleView &v) { return v.font->glyphCount; })
    .def("__getitem__", [](RectangleView &v, int i) { return v.font->recs[index(i, v.font->glyphCount)]; });
  nb::class_<Sound>(module, "Sound").def_ro("frameCount", &Sound::frameCount);
  module.def("install", [](nb::module_ target, const std::string &path) {
    if (!backend.empty()) {
      if (backend != path) throw std::runtime_error("Raylib backend cannot change in a running renderer");
      return;
    }
    if (!nb::type<Image>().is_valid() || !nb::type<Font>().is_valid()) throw std::runtime_error("Load native Raylib types before extensions");
    auto vector = nb::borrow<nb::class_<Vector2>>(nb::type<Vector2>());
    vector.def("__init__", [](Vector2 *self, nb::sequence values) {
      if (nb::len(values) != 2) throw nb::value_error("Vector2 requires two coordinates");
      new (self) Vector2{nb::cast<float>(values[0]), nb::cast<float>(values[1])};
    }, nb::is_implicit());
    nb::implicitly_convertible<nb::tuple, Vector2>();
    nb::implicitly_convertible<nb::list, Vector2>();
    auto color_type = nb::borrow<nb::class_<Color>>(nb::type<Color>());
    color_type.def("__init__", [](Color *self, nb::sequence values) {
      if (nb::len(values) != 4) throw nb::value_error("Color requires four channels");
      new (self) Color{nb::cast<unsigned char>(values[0]), nb::cast<unsigned char>(values[1]), nb::cast<unsigned char>(values[2]), nb::cast<unsigned char>(values[3])};
    }, nb::is_implicit());
    nb::implicitly_convertible<nb::tuple, Color>();
    nb::implicitly_convertible<nb::list, Color>();
    library = dlopen(path.c_str(), RTLD_NOW | RTLD_LOCAL);
    if (!library) throw std::runtime_error(dlerror());
    if (!nb::hasattr(target, "begin_blend_mode")) target.def("begin_blend_mode", symbol<decltype(&BeginBlendMode)>("BeginBlendMode"));
    if (!nb::hasattr(target, "end_blend_mode")) target.def("end_blend_mode", symbol<decltype(&EndBlendMode)>("EndBlendMode"));
    if (!nb::hasattr(target, "color_alpha")) target.def("color_alpha", symbol<decltype(&ColorAlpha)>("ColorAlpha"));
    if (!nb::hasattr(target, "color_lerp")) target.def("color_lerp", symbol<decltype(&ColorLerp)>("ColorLerp"));
    if (!nb::hasattr(target, "color_to_int")) target.def("color_to_int", symbol<decltype(&ColorToInt)>("ColorToInt"));
    if (!nb::hasattr(target, "draw_circle_lines")) target.def("draw_circle_lines", symbol<decltype(&DrawCircleLines)>("DrawCircleLines"));
    if (!nb::hasattr(target, "draw_ellipse")) target.def("draw_ellipse", symbol<decltype(&DrawEllipse)>("DrawEllipse"));
    if (!nb::hasattr(target, "draw_ellipse_lines")) target.def("draw_ellipse_lines", symbol<decltype(&DrawEllipseLines)>("DrawEllipseLines"));
    if (!nb::hasattr(target, "draw_poly")) target.def("draw_poly", symbol<decltype(&DrawPoly)>("DrawPoly"));
    if (!nb::hasattr(target, "draw_spline_segment_bezier_cubic")) target.def("draw_spline_segment_bezier_cubic", symbol<decltype(&DrawSplineSegmentBezierCubic)>("DrawSplineSegmentBezierCubic"));
    if (!nb::hasattr(target, "draw_triangle")) target.def("draw_triangle", symbol<decltype(&DrawTriangle)>("DrawTriangle"));
    if (!nb::hasattr(target, "export_image")) target.def("export_image", symbol<decltype(&ExportImage)>("ExportImage"));
    if (!nb::hasattr(target, "get_glyph_index")) target.def("get_glyph_index", symbol<decltype(&GetGlyphIndex)>("GetGlyphIndex"));
    if (!nb::hasattr(target, "get_image_color")) target.def("get_image_color", symbol<decltype(&GetImageColor)>("GetImageColor"));
    if (!nb::hasattr(target, "get_render_height")) target.def("get_render_height", symbol<decltype(&GetRenderHeight)>("GetRenderHeight"));
    if (!nb::hasattr(target, "get_render_width")) target.def("get_render_width", symbol<decltype(&GetRenderWidth)>("GetRenderWidth"));
    if (!nb::hasattr(target, "get_screen_height")) target.def("get_screen_height", symbol<decltype(&GetScreenHeight)>("GetScreenHeight"));
    if (!nb::hasattr(target, "get_screen_width")) target.def("get_screen_width", symbol<decltype(&GetScreenWidth)>("GetScreenWidth"));
    if (!nb::hasattr(target, "is_key_pressed_repeat")) target.def("is_key_pressed_repeat", symbol<decltype(&IsKeyPressedRepeat)>("IsKeyPressedRepeat"));
    if (!nb::hasattr(target, "is_shader_valid")) target.def("is_shader_valid", symbol<decltype(&IsShaderValid)>("IsShaderValid"));
    if (!nb::hasattr(target, "is_texture_valid")) target.def("is_texture_valid", symbol<decltype(&IsTextureValid)>("IsTextureValid"));
    if (!nb::hasattr(target, "is_image_valid")) target.def("is_image_valid", symbol<decltype(&IsImageValid)>("IsImageValid"));
    if (!nb::hasattr(target, "load_font")) target.def("load_font", symbol<decltype(&LoadFont)>("LoadFont"));
    if (!nb::hasattr(target, "load_image_from_screen")) target.def("load_image_from_screen", symbol<decltype(&LoadImageFromScreen)>("LoadImageFromScreen"));
    if (!nb::hasattr(target, "load_texture")) target.def("load_texture", symbol<decltype(&LoadTexture)>("LoadTexture"));
    if (!nb::hasattr(target, "rl_disable_framebuffer")) target.def("rl_disable_framebuffer", symbol<decltype(&rlDisableFramebuffer)>("rlDisableFramebuffer"));
    if (!nb::hasattr(target, "rl_draw_render_batch_active")) target.def("rl_draw_render_batch_active", symbol<decltype(&rlDrawRenderBatchActive)>("rlDrawRenderBatchActive"));
    if (!nb::hasattr(target, "rl_get_shader_id_default")) target.def("rl_get_shader_id_default", symbol<decltype(&rlGetShaderIdDefault)>("rlGetShaderIdDefault"));
    if (!nb::hasattr(target, "rl_get_texture_id_default")) target.def("rl_get_texture_id_default", symbol<decltype(&rlGetTextureIdDefault)>("rlGetTextureIdDefault"));
    if (!nb::hasattr(target, "rl_rotatef")) target.def("rl_rotatef", symbol<decltype(&rlRotatef)>("rlRotatef"));
    if (!nb::hasattr(target, "rl_set_blend_factors")) target.def("rl_set_blend_factors", symbol<decltype(&rlSetBlendFactors)>("rlSetBlendFactors"));
    if (!nb::hasattr(target, "rl_set_blend_factors_separate")) target.def("rl_set_blend_factors_separate", symbol<decltype(&rlSetBlendFactorsSeparate)>("rlSetBlendFactorsSeparate"));
    if (!nb::hasattr(target, "rl_set_framebuffer_height")) target.def("rl_set_framebuffer_height", symbol<decltype(&rlSetFramebufferHeight)>("rlSetFramebufferHeight"));
    if (!nb::hasattr(target, "rl_set_framebuffer_width")) target.def("rl_set_framebuffer_width", symbol<decltype(&rlSetFramebufferWidth)>("rlSetFramebufferWidth"));
    if (!nb::hasattr(target, "rl_unload_texture")) target.def("rl_unload_texture", symbol<decltype(&rlUnloadTexture)>("rlUnloadTexture"));
    if (!nb::hasattr(target, "rlgl_close")) target.def("rlgl_close", symbol<decltype(&rlglClose)>("rlglClose"));
    if (!nb::hasattr(target, "rlgl_init")) target.def("rlgl_init", symbol<decltype(&rlglInit)>("rlglInit"));
    if (!nb::hasattr(target, "set_exit_key")) target.def("set_exit_key", symbol<decltype(&SetExitKey)>("SetExitKey"));
    if (!nb::hasattr(target, "set_shapes_texture")) target.def("set_shapes_texture", symbol<decltype(&SetShapesTexture)>("SetShapesTexture"));
    if (!nb::hasattr(target, "rl_set_texture")) target.def("rl_set_texture", symbol<decltype(&rlSetTexture)>("rlSetTexture"));
    if (!nb::hasattr(target, "rl_begin")) target.def("rl_begin", symbol<decltype(&rlBegin)>("rlBegin"));
    if (!nb::hasattr(target, "rl_tex_coord2f")) target.def("rl_tex_coord2f", symbol<decltype(&rlTexCoord2f)>("rlTexCoord2f"));
    if (!nb::hasattr(target, "rl_vertex2f")) target.def("rl_vertex2f", symbol<decltype(&rlVertex2f)>("rlVertex2f"));
    if (!nb::hasattr(target, "rl_end")) target.def("rl_end", symbol<decltype(&rlEnd)>("rlEnd"));
    if (!nb::hasattr(target, "init_audio_device")) target.def("init_audio_device", []() { symbol<decltype(&InitAudioDevice)>("InitAudioDevice")(); });
    if (!nb::hasattr(target, "close_audio_device")) target.def("close_audio_device", []() { symbol<decltype(&CloseAudioDevice)>("CloseAudioDevice")(); });
    if (!nb::hasattr(target, "is_audio_device_ready")) target.def("is_audio_device_ready", []() { return symbol<decltype(&IsAudioDeviceReady)>("IsAudioDeviceReady")(); });
    if (!nb::hasattr(target, "load_sound")) target.def("load_sound", [](const char *sound_path) { return symbol<decltype(&LoadSound)>("LoadSound")(sound_path); });
    if (!nb::hasattr(target, "unload_sound")) target.def("unload_sound", [](Sound sound) { symbol<decltype(&UnloadSound)>("UnloadSound")(sound); });
    if (!nb::hasattr(target, "play_sound")) target.def("play_sound", [](Sound sound) { symbol<decltype(&PlaySound)>("PlaySound")(sound); });
    if (!nb::hasattr(target, "stop_sound")) target.def("stop_sound", [](Sound sound) { symbol<decltype(&StopSound)>("StopSound")(sound); });
    if (!nb::hasattr(target, "set_sound_volume")) target.def("set_sound_volume", [](Sound sound, float volume) { symbol<decltype(&SetSoundVolume)>("SetSoundVolume")(sound, volume); });
    target.def("image_format", [](nb::handle object, int format) { if (nb::hasattr(object, "_buffer")) throw nb::value_error("Cannot mutate ownership of a Python-backed image"); auto &image = nb::cast<Image &>(object); symbol<decltype(&ImageFormat)>("ImageFormat")(&image, format); });
    target.def("image_flip_vertical", [](nb::handle object) { if (nb::hasattr(object, "_buffer")) throw nb::value_error("Cannot mutate ownership of a Python-backed image"); auto &image = nb::cast<Image &>(object); symbol<decltype(&ImageFlipVertical)>("ImageFlipVertical")(&image); });
    target.def("image_crop", [](nb::handle object, Rectangle rect) { if (nb::hasattr(object, "_buffer")) throw nb::value_error("Cannot mutate ownership of a Python-backed image"); auto &image = nb::cast<Image &>(object); symbol<decltype(&ImageCrop)>("ImageCrop")(&image, rect); });
    target.def("_image_data", [](nb::handle object) -> nb::object {
      auto &image = nb::cast<Image &>(object);
      if (!image.data) return nb::none();
      if (image.width <= 0 || image.height <= 0 ||
          size_t(image.width) * size_t(image.height) > size_t(std::numeric_limits<int>::max()) / 16 ||
          image.format < PIXELFORMAT_UNCOMPRESSED_GRAYSCALE || image.format > PIXELFORMAT_COMPRESSED_ASTC_8x8_RGBA)
        throw nb::value_error("Invalid image dimensions or pixel format");
      int size = symbol<decltype(&GetPixelDataSize)>("GetPixelDataSize")(image.width, image.height, image.format);
      if (size <= 0) throw std::runtime_error("Invalid Raylib image dimensions or format");
      if (nb::hasattr(object, "_buffer")) {
        auto view = static_cast<Py_buffer *>(nb::cast<nb::capsule>(object.attr("_buffer")).data());
        if (image.data != view->buf || size > view->len) throw nb::value_error("Image metadata exceeds its retained pixel buffer");
        if (view->readonly) {
          using ReadOnly = nb::ndarray<nb::memview, const uint8_t, nb::ndim<1>, nb::device::cpu>;
          return nb::cast(ReadOnly(image.data, {static_cast<size_t>(size)}, object));
        }
      }
      using Bytes = nb::ndarray<nb::memview, uint8_t, nb::ndim<1>, nb::device::cpu>;
      return nb::cast(Bytes(image.data, {static_cast<size_t>(size)}, object));
    });
    target.def("_font_glyphs", [](nb::handle font) { return GlyphView{nb::borrow<nb::object>(font), &nb::cast<Font &>(font)}; });
    target.def("_font_recs", [](nb::handle font) { return RectangleView{nb::borrow<nb::object>(font), &nb::cast<Font &>(font)}; });
    auto property = nb::module_::import_("builtins").attr("property");
    nb::type<Image>().attr("data") = property(target.attr("_image_data"));
    nb::type<Font>().attr("glyphs") = property(target.attr("_font_glyphs"));
    nb::type<Font>().attr("recs") = property(target.attr("_font_recs"));
    target.def("rl_load_extensions", [](uintptr_t loader) { symbol<decltype(&rlLoadExtensions)>("rlLoadExtensions")(reinterpret_cast<void *>(loader)); });
    target.def("rl_load_texture", [](nb::ndarray<const uint8_t, nb::ndim<1>, nb::c_contig> data, int w, int h, int format, int mipmaps) {
      int size = symbol<decltype(&GetPixelDataSize)>("GetPixelDataSize")(w, h, format);
      if (size <= 0 || data.size() < static_cast<size_t>(size) || mipmaps != 1) throw nb::value_error("Invalid texture byte buffer");
      return symbol<decltype(&rlLoadTexture)>("rlLoadTexture")(data.data(), w, h, format, mipmaps);
    });
    target.def("draw_triangle_strip", [](nb::handle points, int count, Color color) {
      auto draw = symbol<decltype(&DrawTriangleStrip)>("DrawTriangleStrip");
      using Vertices = nb::ndarray<float, nb::ndim<2>, nb::c_contig>;
      Vertices packed;
      if (nb::try_cast(points, packed)) {
        if (packed.shape(1) != 2 || count < 0 || static_cast<size_t>(count) > packed.shape(0)) throw nb::value_error("Invalid triangle strip bounds");
        draw(reinterpret_cast<Vector2 *>(packed.data()), count, color);
      } else {
        auto values = nb::borrow<nb::sequence>(points);
        if (count < 0 || static_cast<size_t>(count) > nb::len(values)) throw nb::value_error("Invalid triangle strip bounds");
        std::vector<Vector2> vertices;
        vertices.reserve(count);
        for (int i = 0; i < count; ++i) vertices.push_back(nb::cast<Vector2>(values[i]));
        draw(vertices.data(), count, color);
      }
    });
    nb::dict modes;
    modes["BLEND_ALPHA"] = static_cast<int>(BLEND_ALPHA);
    modes["BLEND_ADDITIVE"] = static_cast<int>(BLEND_ADDITIVE);
    modes["BLEND_MULTIPLIED"] = static_cast<int>(BLEND_MULTIPLIED);
    modes["BLEND_ADD_COLORS"] = static_cast<int>(BLEND_ADD_COLORS);
    modes["BLEND_SUBTRACT_COLORS"] = static_cast<int>(BLEND_SUBTRACT_COLORS);
    modes["BLEND_ALPHA_PREMULTIPLY"] = static_cast<int>(BLEND_ALPHA_PREMULTIPLY);
    modes["BLEND_CUSTOM"] = static_cast<int>(BLEND_CUSTOM);
    modes["BLEND_CUSTOM_SEPARATE"] = static_cast<int>(BLEND_CUSTOM_SEPARATE);
    if (!nb::hasattr(target, "BlendMode")) target.attr("BlendMode") = nb::module_::import_("enum").attr("IntEnum")("BlendMode", modes);
    target.attr("RL_QUADS") = RL_QUADS;
    target.attr("RL_ONE_MINUS_SRC_ALPHA") = RL_ONE_MINUS_SRC_ALPHA;
    target.attr("RL_FUNC_ADD") = RL_FUNC_ADD;
    target.attr("RL_SRC_ALPHA") = RL_SRC_ALPHA;
    target.attr("RL_ONE") = RL_ONE;
    backend = path;
  });
}

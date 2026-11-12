// ANDROID AUTO VIDEO PERFORMANCE GUARDRAIL: The hardware encoder's single-frame
// submission, buffer handling, and rate control are latency-sensitive optimizations.
// Altering them can degrade video smoothness or introduce lag on Android Auto.
// Do not change them autonomously; require an explicit user request specifically
// for these video optimizations.
// Isolated Qualcomm H.264 (or, with AA_ENCODER_HEVC, H.265) session for Android Auto projection. No cereal publisher,
// camera buffers, realtime priority, or background thread. Exactly one input in flight.
// Input is RGBA (converted here with NEON) or NV12 already converted on the GPU by the
// car renderer (copied into the encoder's padded layout). ABI 3 adds the NV12 entry
// point and create() options (AA_ENCODER_CBR). ABI 4 adds AA_ENCODER_HEVC for a head unit that offers only H.265;
// the H.264 path is untouched. HEVC controls follow system/loggerd/encoder/v4l_encoder.cc.
// Buffer/ioctl conventions follow system/loggerd/encoder/v4l_encoder.cc and
// msgq/visionipc/visionbuf_ion.cc. Built for comma_arm64 by this directory's SConscript.
//
// From yummydirtx/openpilot tools/starport/hardware_encoder.cc (MIT), pinned at
// 672a16f6183567c0ada53654f8527d97e1a483fa; only the include paths differ.
#include <algorithm>
#include <chrono>
#include <cstdio>
#include <cstring>
#include <memory>
#include <stdexcept>
#include <string>
#include <vector>
#include <cerrno>
#include <fcntl.h>
#include <poll.h>
#include <sys/ioctl.h>
#include <sys/mman.h>
#include <unistd.h>
#include <linux/ion.h>
#include <linux/msm_ion.h>
#include <media/msm_media_info.h>
#include <linux/v4l2-controls.h>
#include <linux/videodev2.h>
#include "starpilot/system/starport/hw/rgba_to_nv12.h"

namespace {
using Clock = std::chrono::steady_clock;
constexpr unsigned CODECCONFIG = 0x00020000;
constexpr int AA_ENCODER_CBR = 1;  // hold the configured bitrate instead of letting it float with scene complexity
constexpr int AA_ENCODER_HEVC = 2; // encode H.265 (Main profile) instead of H.264 baseline
constexpr size_t MAX_PACKET = 2 * 1024 * 1024 - 10;

void check(bool ok, const char *what) {
  if (!ok) throw std::runtime_error(std::string(what) + ": " + strerror(errno));
}
void call(int fd, unsigned long request, void *arg, const char *what) {
  int rc;
  do { rc = ioctl(fd, request, arg); } while (rc < 0 && errno == EINTR);
  check(rc >= 0, what);
}
void error_text(char *error, size_t size, const std::exception &e) {
  if (size) snprintf(error, size, "%s", e.what());
}

struct Buffer {
  int ion = -1, fd = -1, handle = 0;
  void *addr = MAP_FAILED;
  size_t size = 0;
  Buffer() = default;
  Buffer(const Buffer &) = delete;
  Buffer &operator=(const Buffer &) = delete;
  void allocate(size_t length) {
    size = length;
    ion = open("/dev/ion", O_RDWR | O_CLOEXEC);
    check(ion >= 0, "open ion");
    ion_allocation_data alloc = {};
    alloc.len = length; alloc.align = 4096;
    alloc.heap_id_mask = 1 << ION_IOMMU_HEAP_ID;
    alloc.flags = ION_FLAG_CACHED;
    call(ion, ION_IOC_ALLOC, &alloc, "ION allocate");
    handle = alloc.handle;
    ion_fd_data share = {}; share.handle = handle;
    call(ion, ION_IOC_SHARE, &share, "ION share");
    fd = share.fd;
    check(fcntl(fd, F_SETFD, FD_CLOEXEC) >= 0, "ION close-on-exec");
    addr = mmap(nullptr, size, PROT_READ | PROT_WRITE, MAP_SHARED, fd, 0);
    check(addr != MAP_FAILED, "ION mmap");
    memset(addr, 0, size);
  }
  void sync(bool from_device) {
    ion_flush_data flush = {};
    flush.handle = handle; flush.vaddr = addr; flush.length = size;
    ion_custom_data custom = {};
    custom.cmd = from_device ? ION_IOC_INV_CACHES : ION_IOC_CLEAN_CACHES;
    custom.arg = reinterpret_cast<unsigned long>(&flush);
    call(ion, ION_IOC_CUSTOM, &custom, "ION cache sync");
  }
  ~Buffer() {
    if (addr != MAP_FAILED) munmap(addr, size);
    if (fd >= 0) close(fd);
    if (handle) { ion_handle_data h = {}; h.handle = handle; ioctl(ion, ION_IOC_FREE, &h); }
    if (ion >= 0) close(ion);
  }
};

struct Encoder {
  int fd = -1, width, height, fps, stride, uv_offset, margin_height, options;
  bool capture_on = false, input_on = false, failed = false;
  unsigned frame = 0;
  double timing[3] = {};
  Buffer input;
  std::vector<std::unique_ptr<Buffer>> outputs;
  std::vector<unsigned char> header;
  Encoder(int w, int h, int rate, int margin, int opts) : width(w), height(h), fps(rate), margin_height(margin), options(opts) {}
  ~Encoder() {
    // Closing the device releases its DMA references before ION storage dies.
    if (fd >= 0) {
      v4l2_buf_type type = V4L2_BUF_TYPE_VIDEO_OUTPUT_MPLANE;
      if (input_on) ioctl(fd, VIDIOC_STREAMOFF, &type);
      type = V4L2_BUF_TYPE_VIDEO_CAPTURE_MPLANE;
      if (capture_on) ioctl(fd, VIDIOC_STREAMOFF, &type);
      close(fd);
    }
  }
  void control(unsigned id, int value) {
    v4l2_control c = {}; c.id = id; c.value = value;
    call(fd, VIDIOC_S_CTRL, &c, "encoder control");
  }
  unsigned buffers(v4l2_buf_type type, unsigned count) {
    v4l2_requestbuffers req = {}; req.type = type; req.memory = V4L2_MEMORY_USERPTR; req.count = count;
    call(fd, VIDIOC_REQBUFS, &req, "request buffers");
    if (!req.count || req.count > 32) throw std::runtime_error("Unexpected driver buffer count");
    return req.count;
  }
  void queue(v4l2_buf_type type, unsigned index, Buffer &buffer, int64_t timestamp = 0) {
    v4l2_plane plane = {};
    plane.bytesused = plane.length = buffer.size;
    plane.m.userptr = reinterpret_cast<unsigned long>(buffer.addr);
    plane.reserved[0] = buffer.fd;
    v4l2_buffer b = {}; b.type = type; b.index = index; b.memory = V4L2_MEMORY_USERPTR;
    b.flags = V4L2_BUF_FLAG_TIMESTAMP_COPY;
    b.timestamp.tv_sec = timestamp / 1000000; b.timestamp.tv_usec = timestamp % 1000000;
    b.m.planes = &plane; b.length = 1;
    call(fd, VIDIOC_QBUF, &b, "queue buffer");
  }
  void init(int bitrate) {
    fd = open("/dev/v4l/by-path/platform-aa00000.qcom_vidc-video-index1", O_RDWR | O_NONBLOCK | O_CLOEXEC);
    check(fd >= 0, "open hardware encoder");
    v4l2_capability cap = {};
    call(fd, VIDIOC_QUERYCAP, &cap, "encoder capability");
    if (strcmp(reinterpret_cast<char *>(cap.driver), "msm_vidc_driver") ||
        strcmp(reinterpret_cast<char *>(cap.card), "msm_vidc_venc")) throw std::runtime_error("Unsupported encoder driver");
    v4l2_format out = {}; out.type = V4L2_BUF_TYPE_VIDEO_CAPTURE_MPLANE;
    out.fmt.pix_mp.width = width; out.fmt.pix_mp.height = height;
    const bool hevc = options & AA_ENCODER_HEVC;
    out.fmt.pix_mp.pixelformat = hevc ? V4L2_PIX_FMT_HEVC : V4L2_PIX_FMT_H264;
    call(fd, VIDIOC_S_FMT, &out, hevc ? "HEVC format" : "H264 format");
    v4l2_streamparm parm = {}; parm.type = V4L2_BUF_TYPE_VIDEO_OUTPUT_MPLANE;
    parm.parm.output.timeperframe.numerator = 1; parm.parm.output.timeperframe.denominator = fps;
    call(fd, VIDIOC_S_PARM, &parm, "frame rate");
    v4l2_format in = {}; in.type = V4L2_BUF_TYPE_VIDEO_OUTPUT_MPLANE;
    in.fmt.pix_mp.width = width; in.fmt.pix_mp.height = height;
    in.fmt.pix_mp.pixelformat = V4L2_PIX_FMT_NV12;
    in.fmt.pix_mp.colorspace = V4L2_COLORSPACE_470_SYSTEM_BG;
    call(fd, VIDIOC_S_FMT, &in, "NV12 format");
    stride = VENUS_Y_STRIDE(COLOR_FMT_NV12, width);
    uv_offset = stride * VENUS_Y_SCANLINES(COLOR_FMT_NV12, height);
    if (in.fmt.pix_mp.pixelformat != V4L2_PIX_FMT_NV12 || in.fmt.pix_mp.width != unsigned(width) ||
        in.fmt.pix_mp.height != unsigned(height) || in.fmt.pix_mp.num_planes != 1 ||
        in.fmt.pix_mp.plane_fmt[0].sizeimage < unsigned(uv_offset + stride * height / 2) ||
        in.fmt.pix_mp.plane_fmt[0].sizeimage > 16 * 1024 * 1024 ||
        out.fmt.pix_mp.plane_fmt[0].sizeimage > MAX_PACKET) throw std::runtime_error("Unexpected encoder buffer layout");
    control(V4L2_CID_MPEG_VIDEO_BITRATE, bitrate);
    control(V4L2_CID_MPEG_VIDC_VIDEO_NUM_P_FRAMES, fps - 1);
    control(V4L2_CID_MPEG_VIDC_VIDEO_NUM_B_FRAMES, 0);
    control(V4L2_CID_MPEG_VIDEO_HEADER_MODE, V4L2_MPEG_VIDEO_HEADER_MODE_SEPARATE);
    control(V4L2_CID_MPEG_VIDC_VIDEO_RATE_CONTROL, options & AA_ENCODER_CBR ? V4L2_CID_MPEG_VIDC_VIDEO_RATE_CONTROL_CBR_CFR
                                                                         : V4L2_CID_MPEG_VIDC_VIDEO_RATE_CONTROL_VBR_CFR);
    control(V4L2_CID_MPEG_VIDC_VIDEO_PRIORITY, V4L2_MPEG_VIDC_VIDEO_PRIORITY_REALTIME_DISABLE);
    control(V4L2_CID_MPEG_VIDC_VIDEO_IDR_PERIOD, 1);
    if (hevc) {
      control(V4L2_CID_MPEG_VIDC_VIDEO_HEVC_PROFILE, V4L2_MPEG_VIDC_VIDEO_HEVC_PROFILE_MAIN);
      control(V4L2_CID_MPEG_VIDC_VIDEO_HEVC_TIER_LEVEL, V4L2_MPEG_VIDC_VIDEO_HEVC_LEVEL_MAIN_TIER_LEVEL_4_1);
    } else {
      control(V4L2_CID_MPEG_VIDEO_H264_PROFILE, V4L2_MPEG_VIDEO_H264_PROFILE_BASELINE);
      control(V4L2_CID_MPEG_VIDEO_H264_LEVEL, V4L2_MPEG_VIDEO_H264_LEVEL_3_1);
      control(V4L2_CID_MPEG_VIDEO_H264_ENTROPY_MODE, V4L2_MPEG_VIDEO_H264_ENTROPY_MODE_CAVLC);
      control(V4L2_CID_MPEG_VIDEO_H264_LOOP_FILTER_MODE, V4L2_MPEG_VIDEO_H264_LOOP_FILTER_MODE_ENABLED);
    }
    control(V4L2_CID_MPEG_VIDEO_MULTI_SLICE_MODE, 0);
    unsigned count = buffers(V4L2_BUF_TYPE_VIDEO_CAPTURE_MPLANE, 6);
    buffers(V4L2_BUF_TYPE_VIDEO_OUTPUT_MPLANE, 9);
    input.allocate(in.fmt.pix_mp.plane_fmt[0].sizeimage);
    // The native renderer guarantees black negotiated margins. Initialize the
    // Y/UV padding once; do not reconvert their RGBA pixels on every frame.
    memset(input.addr, 16, uv_offset);
    memset(static_cast<unsigned char *>(input.addr) + uv_offset, 128, input.size - uv_offset);
    for (unsigned i = 0; i < count; ++i) {
      auto buffer = std::make_unique<Buffer>(); buffer->allocate(out.fmt.pix_mp.plane_fmt[0].sizeimage);
      outputs.push_back(std::move(buffer));
    }
    v4l2_buf_type type = V4L2_BUF_TYPE_VIDEO_CAPTURE_MPLANE;
    call(fd, VIDIOC_STREAMON, &type, "capture stream on"); capture_on = true;
    type = V4L2_BUF_TYPE_VIDEO_OUTPUT_MPLANE;
    call(fd, VIDIOC_STREAMON, &type, "input stream on"); input_on = true;
    for (unsigned i = 0; i < count; ++i) queue(V4L2_BUF_TYPE_VIDEO_CAPTURE_MPLANE, i, *outputs[i]);
  }
  size_t encode(const unsigned char *rgba, size_t length, bool key,
                unsigned char *dest, size_t capacity) {
    if (failed) throw std::runtime_error("Encoder failed; reopen required");
    if (!rgba || !dest || length != size_t(width * height * 4)) throw std::runtime_error("Invalid RGBA input");
    auto started = Clock::now();
    auto *base = static_cast<unsigned char *>(input.addr);
    int top = margin_height / 2;
    rgba_to_nv12(rgba + size_t(top) * width * 4, base + top * stride, width, height - margin_height,
                 stride, uv_offset - top / 2 * stride);
    return submit(key, dest, capacity, started);
  }
  size_t encode_nv12(const unsigned char *nv12, size_t length, bool key,
                     unsigned char *dest, size_t capacity) {
    if (failed) throw std::runtime_error("Encoder failed; reopen required");
    if (!nv12 || !dest || length != size_t(width) * height * 3 / 2) throw std::runtime_error("Invalid NV12 input");
    auto started = Clock::now();
    auto *base = static_cast<unsigned char *>(input.addr);
    // Tightly packed planes into the encoder's padded ones (same stride for Y and UV).
    for (int row = 0; row < height; ++row) memcpy(base + size_t(row) * stride, nv12 + size_t(row) * width, width);
    const unsigned char *uv = nv12 + size_t(width) * height;
    for (int row = 0; row < height / 2; ++row) memcpy(base + uv_offset + size_t(row) * stride, uv + size_t(row) * width, width);
    return submit(key, dest, capacity, started);
  }
  // The VPU is shared with loggerd's camera encoders, which can hold it for a while;
  // the car only takes its screen back after 3 s without video.
  static constexpr int DEADLINE_MS = 500;
  [[noreturn]] void timed_out(bool input_done, bool frame_done, Clock::time_point started,
                              Clock::time_point converted, Clock::time_point queued) {
    auto ms = [](Clock::duration d) { return std::chrono::duration<double, std::milli>(d).count(); };
    char text[160];
    snprintf(text, sizeof(text), "Hardware encode exceeded %d ms (input %s, frame %s; copy %.1f ms, queue %.1f ms, wait %.1f ms)",
             DEADLINE_MS, input_done ? "done" : "pending", frame_done ? "done" : "pending",
             ms(converted - started), ms(queued - converted), ms(Clock::now() - queued));
    throw std::runtime_error(text);
  }
  size_t submit(bool key, unsigned char *dest, size_t capacity, Clock::time_point started) {
    auto deadline = started + std::chrono::milliseconds(DEADLINE_MS);
    auto converted = Clock::now();
    input.sync(false);
    if (key) control(V4L2_CID_MPEG_VIDC_VIDEO_REQUEST_IFRAME, 1);
    const int64_t timestamp = (int64_t(++frame) * 1000000) / fps;
    queue(V4L2_BUF_TYPE_VIDEO_OUTPUT_MPLANE, 0, input, timestamp);
    auto queued = Clock::now();
    bool input_done = false, frame_done = false;
    size_t result = 0;
    while (!input_done || !frame_done) {
      int remaining = std::chrono::duration_cast<std::chrono::milliseconds>(deadline - Clock::now()).count();
      if (remaining <= 0) timed_out(input_done, frame_done, started, converted, queued);
      pollfd p = {fd, short((frame_done ? 0 : POLLIN | POLLRDNORM) | (input_done ? 0 : POLLOUT | POLLWRNORM)), 0};
      int rc = poll(&p, 1, remaining);
      if (rc < 0 && errno == EINTR) continue;
      check(rc >= 0, "encoder poll");
      if (rc == 0) timed_out(input_done, frame_done, started, converted, queued);
      if (p.revents & (POLLERR | POLLHUP | POLLNVAL)) throw std::runtime_error("Encoder poll error");
      for (auto type : {V4L2_BUF_TYPE_VIDEO_CAPTURE_MPLANE, V4L2_BUF_TYPE_VIDEO_OUTPUT_MPLANE}) {
        if (!(p.revents & (type == V4L2_BUF_TYPE_VIDEO_CAPTURE_MPLANE ? POLLIN | POLLRDNORM : POLLOUT | POLLWRNORM))) continue;
        v4l2_plane plane = {}; v4l2_buffer b = {};
        b.type = type; b.memory = V4L2_MEMORY_USERPTR; b.m.planes = &plane; b.length = 1;
        call(fd, VIDIOC_DQBUF, &b, "dequeue buffer");
        if (type == V4L2_BUF_TYPE_VIDEO_OUTPUT_MPLANE) {
          if (b.index != 0 || input_done) throw std::runtime_error("Unexpected input completion");
          input_done = true;
        } else {
          if (b.index >= outputs.size() || plane.data_offset || !plane.bytesused ||
              plane.bytesused > outputs[b.index]->size || (b.flags & V4L2_BUF_FLAG_ERROR)) throw std::runtime_error("Invalid hardware packet");
          auto &buffer = *outputs[b.index]; buffer.sync(true);
          auto *bytes = static_cast<unsigned char *>(buffer.addr);
          if (b.flags & CODECCONFIG) {
            if (plane.bytesused > 65536) throw std::runtime_error("Oversized codec header");
            header.assign(bytes, bytes + plane.bytesused);
          } else {
            if (b.timestamp.tv_sec * 1000000LL + b.timestamp.tv_usec != timestamp || frame_done)
              throw std::runtime_error("Unexpected hardware frame timestamp");
            size_t prefix = b.flags & V4L2_BUF_FLAG_KEYFRAME ? header.size() : 0;
            result = prefix + plane.bytesused;
            if (result > capacity) throw std::runtime_error("Hardware frame exceeds capacity");
            if (prefix) memcpy(dest, header.data(), prefix);
            memcpy(dest + prefix, bytes, plane.bytesused); frame_done = true;
          }
          queue(V4L2_BUF_TYPE_VIDEO_CAPTURE_MPLANE, b.index, buffer);
        }
      }
    }
    timing[0] = std::chrono::duration<double>(converted - started).count();
    timing[1] = std::chrono::duration<double>(queued - converted).count();
    timing[2] = std::chrono::duration<double>(Clock::now() - queued).count();
    return result;
  }
};
}

extern "C" {
int starport_encoder_abi() { return 4; }
void starport_encoder_timing(void *ptr, double *output) {
  if (ptr && output) std::copy_n(static_cast<Encoder *>(ptr)->timing, 3, output);
}
void *starport_encoder_create(int width, int height, int fps, int bitrate, int margin_height, int options, char *error, size_t size) {
  try {
    // H.264 baseline level 3.1 is a 720p30 session; HEVC Main at main-tier level 4.1 carries 1080p30 for a head unit
    // that offers nothing else.
    const bool hevc = options & AA_ENCODER_HEVC;
    const int max_width = hevc ? 1920 : 1280, max_height = hevc ? 1080 : 720;
    const int short_side = std::min(width, height), long_side = std::max(width, height);
    if (short_side < 2 || short_side > max_height || long_side > max_width || width % 2 || height % 2 ||
        fps != 30 || bitrate < 1000000 || bitrate > 20000000 || margin_height < 0 ||
        margin_height >= height || margin_height % 4 || (options & ~(AA_ENCODER_CBR | AA_ENCODER_HEVC)))
      throw std::runtime_error("Unsupported encoder configuration");
    auto encoder = std::make_unique<Encoder>(width, height, fps, margin_height, options);
    encoder->init(bitrate); return encoder.release();
  } catch (const std::exception &e) { error_text(error, size, e); return nullptr; }
}
int starport_encoder_encode(void *ptr, const unsigned char *rgba, size_t length,
                      int key, unsigned char *output, size_t capacity, char *error, size_t size) {
  auto *encoder = static_cast<Encoder *>(ptr);
  try {
    if (!encoder) throw std::runtime_error("Encoder closed");
    return encoder->encode(rgba, length, key, output, capacity);
  } catch (const std::exception &e) {
    if (encoder) encoder->failed = true;
    error_text(error, size, e); return -1;
  }
}
int starport_encoder_encode_nv12(void *ptr, const unsigned char *nv12, size_t length,
                           int key, unsigned char *output, size_t capacity, char *error, size_t size) {
  auto *encoder = static_cast<Encoder *>(ptr);
  try {
    if (!encoder) throw std::runtime_error("Encoder closed");
    return encoder->encode_nv12(nv12, length, key, output, capacity);
  } catch (const std::exception &e) {
    if (encoder) encoder->failed = true;
    error_text(error, size, e); return -1;
  }
}
void starport_encoder_destroy(void *ptr) { delete static_cast<Encoder *>(ptr); }
}

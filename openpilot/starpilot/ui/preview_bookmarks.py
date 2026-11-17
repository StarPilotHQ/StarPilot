"""Render numbered bookmark feedback using the production UI and supplied state.

No camera/replay or vehicle services are started. Outputs are offline visual
review evidence, not a recording of a drive.
"""
import argparse
from dataclasses import replace
from pathlib import Path

from PIL import Image

from openpilot.selfdrive.selfdrived.bookmarks import BookmarkCounter
from openpilot.starpilot.ui.onroad_state import AlertSize, OnroadAlert
from openpilot.starpilot.ui.presentation import BitmapFonts, Profile
from openpilot.starpilot.ui.preview_home import reference_state
from openpilot.starpilot.ui.preview_settings import reference_settings_state
from openpilot.starpilot.ui.preview_shell import reference_onroad
from openpilot.starpilot.ui.raylib import rl
from openpilot.starpilot.ui.shell import ShellMode, ShellSnapshot, ShellView


def main():
  parser = argparse.ArgumentParser(description=__doc__)
  parser.add_argument('--output', type=Path, required=True)
  args = parser.parse_args()
  args.output.mkdir(parents=True, exist_ok=True)
  root = Path(__file__).resolve().parents[2]
  rl.set_config_flags(rl.ConfigFlags.FLAG_WINDOW_HIDDEN)
  rl.init_window(536, 240, 'Bookmark visual review')
  try:
    for profile in Profile:
      width, height = profile.size
      target = rl.load_render_texture(width, height)
      frames = []
      counter = BookmarkCounter()
      with BitmapFonts(profile, root / 'starpilot/ui/assets/fonts') as fonts:
        shell = ShellView(fonts, root / 'selfdrive/assets')
        try:
          # Two saved bookmarks, park (reset), then the first bookmark of a new drive.
          for frame in range(540):
            parked = 330 <= frame < 420
            drive_id = 2 if frame >= 420 else 1
            saved = frame in (60, 180, 450)
            counter.update(started=not parked, drive_id=drive_id, bookmarks=int(saved))
            alert = OnroadAlert()
            if 60 <= frame < 150 or 180 <= frame < 270 or 450 <= frame < 540:
              source = counter.alert(paired=True)
              alert = OnroadAlert(size=AlertSize.SMALL, text1=source.alert_text_1, text2=source.alert_text_2,
                                  alert_type='userBookmark/permanent')
            state = replace(reference_onroad('onroad_engaged_no_camera'), alert=alert)
            rl.begin_texture_mode(target)
            rl.clear_background(rl.BLACK)
            shell.render(ShellSnapshot(mode=ShellMode.HOME if parked else ShellMode.ONROAD,
                                       home=reference_state(), settings=reference_settings_state(), onroad=state))
            rl.end_texture_mode()
            if frame % 3 == 0:
              image = rl.load_image_from_texture(target.texture)
              rl.image_format(image, rl.PixelFormat.PIXELFORMAT_UNCOMPRESSED_R8G8B8A8)
              rl.image_flip_vertical(image)
              capture = Image.frombytes('RGBA', (width, height), bytes(image.data[:width * height * 4])).convert('RGB')
              rl.unload_image(image)
              if frame == 240:
                capture.save(args.output / f'bookmark-{profile.value}.png')
              if profile == Profile.LARGE:
                capture = capture.resize((1080, 540), Image.Resampling.LANCZOS)
              frames.append(capture)
          frames[0].save(args.output / f'bookmark-{profile.value}.gif', save_all=True, append_images=frames[1:],
                         duration=50, loop=0, optimize=False)
        finally:
          shell.close()
      rl.unload_render_texture(target)
  finally:
    rl.close_window()


if __name__ == '__main__':
  main()

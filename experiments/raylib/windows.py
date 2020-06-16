# windows.py

import raylibpy as rl
from ctypes import byref


def main():

    rl.init_window(rl.get_screen_width(), rl.get_screen_height(), "raylib [core] example - basic window")
    rl.toggle_fullscreen()

    camera = rl.Camera(
        rl.Vector3(4.0, 2.0, 4.0),
        rl.Vector3(0.0, 1.0, 0.0),
        rl.Vector3(0.0, 1.0, 0.0),
        60.0,
        rl.CAMERA_PERSPECTIVE)

    rl.set_camera_mode(camera, rl.CAMERA_FREE)
    rl.set_target_fps(60)

    while not rl.window_should_close():
        rl.update_camera(byref(camera))

        rl.begin_drawing()
        rl.clear_background(rl.DARKGRAY)
        rl.begin_mode3d(camera)

        rl.draw_cube(rl.Vector3(-16.0, 2.5, 0.0), 1.0, 5.0, 32.0, rl.BLUE)
        rl.draw_cube(rl.Vector3(16.0, 2.5, 0.0), 1.0, 5.0, 32.0, rl.LIME)
        rl.draw_cube(rl.Vector3(0.0, 2.5, 16.0), 32.0, 5.0, 1.0, rl.GOLD)

        rl.draw_sphere(rl.Vector3(0.0, 0.0, 0.0), 5.0, rl.LIME)

        rl.draw_text("Congrats! You created your first window!", 190, 200, 20, rl.WHITE)

        rl.draw_grid(40, 1)

        rl.end_mode3d()
        rl.end_drawing()

    rl.close_window()


if __name__ == '__main__':
    main()

Target Platforms
================

Raspberry Pi 4
--------------

- `sudo nano /boot/cmdline.txt`

  - add `logo.nologo quiet` at the end of the line

- Get rid of the rainbow:

  - OK. found it. Add `disable_splash=1` to `config.txt.`
  - Or, alternatively, replace it with something useful?

- Replace `console=tty1` with `console=tty3` to redirect boot messages
  to the third console.  at the end of the line, add `loglevel=3` to
  disable non-critical kernel log messages.
- Hide the logo by adding: `logo.nologo`
- Hide the flashing _ text cursor by adding `vt.global_cursor_default=0`
- The line should look like this:
  `dwc_otg.lpm_enable=0 console=ttyAMA0,115200 console=tty3 root=/devmmcblk0p2 rootfstype=ext4 elevator=deadline rootwait loglevel=3 logo.nologo vt.global_cursor_default=0`

Hide mouse cursor
-----------------

To hide the mouse cursor on inactivity open a terminal window on the
Raspberry Pi, type the following:
`sudo apt-get install unclutter`

Disable Sleep Screen
--------------------

To force the screen to stay on you need to do the following, this will
prevent the screen from going blank after 15 minutes.

- From a terminal window, type the following:
  `sudo nano /etc/lightdm/lightdm.conf`

- Add the following lines to the `[SeatDefaults]` section:
  `# don’t sleep the screen`
  `xserver-command=X -s 0 dpms`

Notes
=====

https://medium.com/@avik.das/writing-gui-applications-on-the-raspberry-pi-without-a-desktop-environment-8f8f840d9867

http://raspberrycompote.blogspot.com/2012/12/low-level-graphics-on-raspberry-pi-part_9509.html

https://www.raylib.com/

- supports RPi
- supports Python
- supports Go
- supports Rust
- supports Zig

https://github.com/raysan5/raylib/wiki/Working-on-Raspberry-Pi

https://dietpi.com/docs/

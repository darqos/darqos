Scenarios
=========

First Boot
----------

The Terminal Service is responsible for managing a terminal device.  This
basically means a screen, keyboard, and mouse (for now).  So the bootstrap
process will kick off the terminal service along with the other system
services, and it is the component that you initially interact with.

On first boot, it will run through an account creation process: it
collects a username and password, and creates a new account for this
user.  For subsequent logins, it displays a standard login dialog, and
authenticates the user.

After creating and/or logging into your account, you'll arrive at the
home screen.  On first boot, this will be empty, so there will be
basically nothing visible.  The screen has no menubar, no start
button, no dock.

From here:

- Pushing System-Space will bring up the object selector panel
- System-n will open the "New Object" panel
- B3 on the terminal window background will pop up a menu with these
  options available also.

Creating a new object will open an editing view for that type.  Let's
assume it's a text object: it will be a basic text editor.  There is
no need to save any work -- it is all saved immediately as you edit.

The window can be closed by: System-w, or B3 menu, Close.

The window can be resized by: B1 drag on the border?

The window can be moved by: B1 drag in the margin.

When you close the window, there's no graphical "record" of the object
any more: no icon, or anything.  It also has no name: there's no need
for objects to be named, unless you want to.  In the selector, it will
show in the history list, and it'll be searchable via the index service.

When you select an object, you can view/edit its metadata using
System-i (from Mac Finder, but ... maybe something else?)  If you want
to name an object, this is the place to do so.  You can also add any
other tags, etc, here.

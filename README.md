# ImageViewer

Simple but featured image viewer, designed for speed when browsing network drives
and on low power computers like Raspberry Pi 2

## Screenshots


### Windows 10

![imageviewer_pc](https://user-images.githubusercontent.com/6446344/180907186-7ca0b477-e825-4fec-ab0a-366642303f27.jpg)
*[Castle Defence &copy; Greg Rutkowski](https://www.artstation.com/artwork/k4lYqK)*

### Raspberry Pi LXDE 

![imageviewer_rpi](https://user-images.githubusercontent.com/6446344/180907188-552fde3e-21d2-4cd9-9e68-652795706eef.jpg)
*[Castle Defence &copy; Greg Rutkowski](https://www.artstation.com/artwork/k4lYqK)*


## Running

    imageviewer.py [image/slideshow filepath]

### LXDE File association

1. Copy the .desktop file to `.local\share\applications\imageviewer.desktop`.
1. If `imageviewer.py` is not in the path, modifie the Exec= entry to the
   absolute path, eg
   ```
   Exec=/usr/bin/imageviewer.py %f
   ```
1. Set the association with, eg
    ```
    xdg-mime default imageviewer.desktop image/jpeg
    ```
    Stored at `~\.config\mimeapps.list`



## Features
- Uses PyQt5
- Works on Raspberry Pi 2 with LXDE
- Works on Windows
- Loads Qt-supported images (jpeg, png...)
- Fast open dialog box on slow network drives
- Slideshow of current image directory
- Pseudo numeric file sorting for open dialog and slideshow of current image
  directory
- Support for .lst files for slideshow contents (text files with
  newline-separated filepaths, absolute or relative to the .lst filepath)
- Background next/previous image prefetching
- Image rotation in 90 degree increments
- Image gamma correction
- Image largest/smallest dimension fit to window
- Page scrolling when in fit to smallest
- Fullscreen mode
- Keyboard and mouse support (doubleclick to toggle fullscreen, wheel for
  next/previous image, middle click to start/stop slideshow)
- Copy / paste image path from clipboard
- Paste newline-separated paths as slideshow contents

## Requirements
- Python 2.7
- PyQt5
- Numpy (optional, otherwise gamma correction will be disabled)

## Todo
- Bugfixing
- Better error handling
- More command line options (debug level, open from clipboard, etc)
- Code cleanup
- More image filters (brightness, contrast, auto-gamma, etc)
- Save configuration, window & dialog position
- GIF support
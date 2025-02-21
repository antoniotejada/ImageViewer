# ImageViewer

Simple but featured image viewer, designed for speed when browsing network drives
and on low power computers like Raspberry Pi 2

## Screenshots

### Image and Thumbnails

![thumbnails](https://github.com/user-attachments/assets/855882aa-f9a8-4f69-bd95-7c49abd0d071)
*[Castle Defence &copy; Greg Rutkowski](https://www.artstation.com/artwork/k4lYqK)*

### 64-bit Windows 10

![imageviewer_win10](https://user-images.githubusercontent.com/6446344/180907186-7ca0b477-e825-4fec-ab0a-366642303f27.jpg)
*[Castle Defence &copy; Greg Rutkowski](https://www.artstation.com/artwork/k4lYqK)*

### 32-bit Windows XP

![imageviewer_winxp](https://user-images.githubusercontent.com/6446344/186052508-8ff7e543-dde4-403f-8b92-1822549ce9e2.png)
*[Castle Defence &copy; Greg Rutkowski](https://www.artstation.com/artwork/k4lYqK)*

### 32-bit Raspberry Pi LXDE 

![imageviewer_rpi](https://user-images.githubusercontent.com/6446344/180907188-552fde3e-21d2-4cd9-9e68-652795706eef.jpg)
*[Castle Defence &copy; Greg Rutkowski](https://www.artstation.com/artwork/k4lYqK)*


## Installing

### 32-bit Raspberry OS

1. Install Python 2.7
1. sudo apt install python-pyqt5 (pip install python-qt5 fails with missing egg-info)

### 64-bit Windows 10

1. Install Python 2.7
1. pip install python-qt5 (or follow https://github.com/pyqt/python-qt5)

### 32-bit Windows XP

python-qt5 is a 64-bit Windows project so it doesn't work in 32-bit Windows XP,
fortunately some versions of Anaconda do support PyQt5 and 32-bit Windows XP.

1. Install Anaconda 2.2.0 which is the last Anaconda Python 2.7.x version that
   is known to work on XP (2.3.0 also seems to work, but has missing DLL paths
   at runtime). This will install Python 2.7.9
1. Create a conda python 2.7 environment, this will install Python 2.7.13 in
   that environment.
1. conda install PyQt5

## Running

    imageviewer.py [image/slideshow filepath]

### LXDE File association

1. Copy the .desktop file to `.local\share\applications\imageviewer.desktop`.
1. If `imageviewer.py` is not in the path, modify the Exec= entry to the
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
- Uses PyQt5 and Python 2.7
- Works on 32-bit Raspberry Pi 2 with LXDE
- Works on 64-bit Windows 10, 32-bit Windows XP
- Loads Qt-supported images (currently PyQt5 reports support for .bmp, .dds,
  .gif, .icns, .ico, .jp2, .jpeg, .jpg, .mng, .pbm, .pgm, .png, .ppm, .svg,
  .svgz, .tga, .tif, .tiff, .wbmp, .webp, .xbm, .xpm)
- Play/pause animated images (currently only GIF, PyQt5 fails in different ways
  to support other animated image formats like APNG, MNG, multipage TIFF,
  animated WEBP)
- Fast open dialog box on slow network drives, automatic deferral of file stat
  fetching after one second timeout, substring keyboard search, history
  navigation, directory path button navigation.
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
- Background color cycling
- Delete current image
- Refresh current image
- Toggable thumbnail splitter pane
- Asynchronous tumnbnail loading

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
#!/usr/bin/env python2.7
"""
imageviewer
(c) Antonio Tejada 2022

Simple imageviewer

See https://github.com/baoboa/pyqt5/blob/master/examples/widgets/imageviewer.py


On LXDE

Copy the .desktop file to .local\share\applications\imageviewer.desktop

Set with

    xdg-mime default imageviewer.desktop image/jpeg
    
Stored at
    ~\.config\mimeapps.list


Sample image files
    Static webp
    https://developers.google.com/speed/webp/gallery1

    Animated webp, apng, mng, gif
    https://ezgif.com/help/alternative-animated-image-formats 

    Multipage tiff (can also be saved with Irfanview)
    https://www.nightprogrammer.org/development/multipage-tiff-example-download-test-image-file/

XXX Missing code cleanup (camelcasing vs snake, proper log levels, refactoring)
XXX Missing file/attribute reading error support
XXX Missing bug fixing, esp when a file fails to load
XXX Missing command line options (debuglevel, openfromclipboard, etc)
XXX Read/store settings with QSettings (Window position, MRU, zoom, fit mode, 
    slideshow timer interval, see saveGeometry, restoreGeometry)
XXX Use numpy for image effects (gamma, auto-gamma, brightness, contrast, etc)
    See https://note.nkmk.me/en/python-numpy-image-processing/
XXX Take a look on whether logging needs %r because of unicode (print to non
    unicode consoles raises UnicodeEncodeError)
    See https://stackoverflow.com/questions/21129020/how-to-fix-unicodedecodeerror-ascii-codec-cant-decode-byte
    See https://stackoverflow.com/questions/33955276/python3-unicodedecodeerror
    See https://stackoverflow.com/questions/5419/python-unicode-and-the-windows-console
"""

import datetime
import logging
import multiprocessing
import os
import Queue as queue
import re
import stat
import string
import sys
import threading
import time

from PyQt5.QtCore import *
from PyQt5.QtGui import *
from PyQt5.QtWidgets import *

class LineHandler(logging.StreamHandler):
    def __init__(self):
        super(LineHandler, self).__init__()

    def emit(self, record):
        text = record.getMessage()
        messages = text.split('\n')
        indent = ""
        for message in messages:
            r = record
            r.msg = "%s%s" % (indent, message)
            r.args = None
            super(LineHandler, self).emit(r)
            indent = "    " 


def setup_logger(logger):
    """
    Setup the logger with a line break handler
    """
    logging_format = "%(asctime).23s %(levelname)s:%(filename)s(%(lineno)d):[%(thread)d] %(message)s"

    logger_handler = LineHandler()
    logger_handler.setFormatter(logging.Formatter(logging_format))
    logger.addHandler(logger_handler) 

    return logger

def dbg(*args, **kwargs):
    logger.debug(*args, **kwargs)

def info(*args, **kwargs):
    logger.info(*args, **kwargs)

def warn(*args, **kwargs):
    logger.warning(*args, **kwargs)

def error(*args, **kwargs):
    logger.error(*args, **kwargs)

def exc(*args, **kwargs):
    logger.exception(*args, **kwargs)


def os_path_isroot(path):
    # Checking path == os.path.dirname(path) is the recommended way
    # of checking for the root directory
    # This works for both regular paths and SMB network shares
    dirname = os.path.dirname(path)
    return (path == dirname)


def os_path_abspath(path):
    # The os.path library (split and the derivatives dirname, basename) slash
    # terminates root directories, eg
    
    #   os.path.split("\\") ('\\', '')
    #   os.path.split("\\dir1") ('\\', 'dir1')
    #   os.path.split("\\dir1\\dir2") ('\\dir1', 'dir2')
    #   os.path.split("\\dir1\\dir2\\") ('\\dir1\\dir2', '')
    
    # this includes SMB network shares, where the root is considered to be the
    # pair \\host\share\ eg 
    
    #   os.path.split("\\\\host\\share") ('\\\\host\\share', '')
    #   os.path.split("\\\\host\\share\\") ('\\\\host\\share\\', '')
    #   os.path.split("\\\\host\\share\\dir1") ('\\\\host\\share\\', 'dir1')

    # abspath also slash terminates regular root directories, 
    
    #  os.path.abspath("\\") 'C:\\'
    #  os.path.abspath("\\..") 'C:\\'

    # unfortunately fails to slash terminate SMB network shares root
    # directories, eg
    
    #  os.path.abspath("\\\\host\\share\\..") \\\\host\\share
    #  os.path.abspath("\\\\host\\share\\..\\..") '\\\\host\\share

    # Without the trailing slash, functions like isabs fail, eg

    #   os.path.isabs("\\\\host\\share") False
    #   os.path.isabs("\\\\host\\share\\") True
    #   os.path.isabs("\\\\host\\share\\dir") True
    #   os.path.isabs("\\\\host\\share\\..") True
    
    # See https://stackoverflow.com/questions/34599208/python-isabs-does-not-recognize-windows-unc-path-as-absolute-path

    
    # This fixes that by making sure root directories are always slash
    # terminated
    abspath = os.path.abspath(os.path.expanduser(path))
    if ((not abspath.endswith(os.sep)) and os_path_isroot(abspath)):
        abspath += os.sep

    dbg("os_path_abspath %r is %r", path, abspath)
    return abspath


def os_path_safelong(filepath):
    # On Windows open fails with > MAX_PATH filenames, needs to use unicode and
    # \\?\ prefix, \\?\UNC\ for network paths
    #
    # See https://stackoverflow.com/a/60105517

    long_filepath = unicode(filepath)
    if (sys.platform.startswith("win")):
        # Relative paths are always limited to MAX_PATH, see
        # https://learn.microsoft.com/en-us/windows/win32/fileio/maximum-file-path-limitation
        # convert to absolute since paths reaching here are not. This has the
        # added benefit of resolving . and .. also which fail when using the \\?
        # long filename prefix
        long_filepath = os_path_abspath(long_filepath)

        if (long_filepath.startswith(r"\\")):
            long_filepath = u'\\\\?\\UNC' + long_filepath[1:]
        else:
            long_filepath = u'\\\\?\\' + long_filepath
            
    return long_filepath

g_image_reader_lock = threading.Lock()
def qThreadSafeImageReader(buffer):
    """
    This was found on pyqt5 win64 with qt5Core.dll 5.3.1.0

    There's a Qt/pyqt5 bug that causes a deadlock between the main thread and
    worker threads when accessing completely unrelated QImageReader instances 
    (note qt dlls below are using export symbols so function names and offsets can be off):

    Thread 0  Id: 34c0.55b0 Suspend: 0 Teb: 00000000`00efe000 Unfrozen
        # Child-SP          RetAddr               Call Site
       00 00000000`011e9898 00007fff`a048920e     ntdll!NtWaitForSingleObject+0x14
       01 00000000`011e98a0 00000000`576e5ce8     KERNELBASE!WaitForSingleObjectEx+0x8e
       02 00000000`011e9940 00000000`576c11d9     Qt5Core!QBasicMutex::lockInternal+0x148
       03 00000000`011e9970 00007fff`98998285     Qt5Core!QMutexLocker::QMutexLocker+0x19
       04 00000000`011e99a0 00007fff`9899a07f     Qt5Gui!QImageReader::clipRect+0xd5
       05 00000000`011e9b20 00007fff`98999a81     Qt5Gui!QImageReader::imageFormat+0x48f
       06 00000000`011e9ba0 00007fff`98d3b2b8     Qt5Gui!QImageReader::imageCount+0x11
       07 00000000`011e9bd0 00000000`57e6cdd9     QtGui!initQtGui+0x300e8
       08 00000000`011e9c10 00000000`57ed3028     python27!PyCFunction_Call+0x69 [c:\build27\cpython\objects\methodobject.c @ 81] 
       09 00000000`011e9c40 00000000`57ed050b     python27!call_function+0x328 [c:\build27\cpython\python\ceval.c @ 4376] 
       0a 00000000`011e9ca0 00000000`57ed1a81     python27!PyEval_EvalFrameEx+0x3a3b [c:\build27\cpython\python\ceval.c @ 3017] 
       0b 00000000`011e9db0 00000000`57ed3269     python27!PyEval_EvalCodeEx+0x911 [c:\build27\cpython\python\ceval.c @ 3608] 
       0c 00000000`011e9e60 00000000`57ed30af     python27!fast_function+0x139 [c:\build27\cpython\python\ceval.c @ 4475] 
       ...

    Thread 10  Id: 34c0.2bfc Suspend: 0 Teb: 00000000`00f18000 Unfrozen
    # Child-SP          RetAddr               Call Site
    00 00000000`0baff078 00007fff`a048920e     ntdll!NtWaitForSingleObject+0x14
    01 00000000`0baff080 00000000`57ecc765     KERNELBASE!WaitForSingleObjectEx+0x8e
    02 00000000`0baff120 00000000`57ef8338     python27!PyEval_RestoreThread+0x55 [c:\build27\cpython\python\ceval.c @ 359] 
    03 00000000`0baff150 00007fff`9c7b428c     python27!PyGILState_Ensure+0x88 [c:\build27\cpython\python\pystate.c @ 625] 
    04 00000000`0baff180 00007fff`9905ab99     sip+0x428c
    *** WARNING: Unable to verify checksum for qico.dll
    05 00000000`0baff1e0 00007fff`9c491b20     QtCore!initQtCore+0x1e0d9
    06 00000000`0baff220 00007fff`9c491426     qico+0x1b20
    07 00000000`0baff290 00007fff`98998b6e     qico+0x1426
    08 00000000`0baff2c0 00007fff`9899a07f     Qt5Gui!QImageReader::clipRect+0x9be
    09 00000000`0baff440 00007fff`9899a64d     Qt5Gui!QImageReader::imageFormat+0x48f
    0a 00000000`0baff4c0 00007fff`9899a54a     Qt5Gui!QImageReader::read+0x5d
    0b 00000000`0baff530 00007fff`98d3af6e     Qt5Gui!QImageReader::read+0x3a
    0c 00000000`0baff5c0 00000000`57e6cdd9     QtGui!initQtGui+0x2fd9e
    0d 00000000`0baff620 00000000`57ed3028     python27!PyCFunction_Call+0x69 [c:\build27\cpython\objects\methodobject.c @ 81] 

    Thread 0 above has the GIL and is calling into imageCount(), which grabs a 
    QMutexLocker, 

    0:010> ? 55b0
    Evaluate expression: 21936 = 00000000`000055b0
    0:010> dx -r1 _PyThreadState_Current
    _PyThreadState_Current                 : 0x3343e70 [Type: _ts *]
        ...
        [+0x084] gilstate_counter : 2 [Type: int]
        [+0x088] async_exc        : 0x0 [Type: _object *]
        [+0x090] thread_id        : 21936 [Type: long]
        ...
    https://github.com/certik/python-2.7/blob/master/Python/pystate.c

    thread 10 has probably grabbed that QMutexLocker but is waiting on the GIL 
    (again note that the export symbols are probably hiding that the callstack 
    is deep inside createReadHandlerHelper)

    0:010> ? 2bfc
    Evaluate expression: 11260 = 00000000`00002bfc
    0:010> dx -r1 ((python27!_ts *)0x3e34ff0)
    ((python27!_ts *)0x3e34ff0)    
        ...
        [+0x084] gilstate_counter : 1 [Type: int]
        [+0x088] async_exc        : 0x0 [Type: _object *]
        [+0x090] thread_id        : 11260 [Type: long]
        ...

    This seems to be because QImageReader createReadHandlerHelper takes a lock
    in order to initialize the plugin libraries: The main thread grabs the
    QImageReader lock and then relinquishes the GIL, then the thread with he GIL gets
    stuck waiting on the QImageReader lock without releasing it.

    static QImageIOHandler *createReadHandlerHelper(QIODevice *device,
    ...
    static QMutex mutex;
    QMutexLocker locker(&mutex);
    ...
    https://github.com/qt/qtbase/blob/v5.3.1/src/gui/image/qimagereader.cpp#L225

    The lock is not taken at creation, but in the first member function that
    finds the handler uninitialized, eg for imageCount the check for initialization
    is

    int QImageReader::imageCount() const
    {
        if (!d->initHandler())
            return -1;
        return d->handler->imageCount();
    }
    https://github.com/qt/qtbase/blob/v5.3.1/src/gui/image/qimagereader.cpp#L1315


    The workaround is to perform the first QImageReader call under a lock so
    all calls to initHanlder and createReadHandlerHelper are serialized by Python

    Another possibility could be not access the QImageReader from the main
    thread but from the worker thread, since it looks like competing worker 
    threads have a different timing that avoid or make this issue less frequent,
    but this workaround is simpler and more fail proof.

    This links the buffer lifetime to the reader in order to avoid the buffer 
    being garbage collected while still in use by the reader and the application
    silently exiting
    """
    reader = QImageReader(buffer)
    with g_image_reader_lock:
        reader.imageCount()
        # Link the buffer to the reader lifetime, this prevents the buffer
        # from being garbage collected before the reader is done with it, which
        # causes the app to silently exit
        reader.safe_buffer = buffer
    return reader

# XXX Support animations via QMovie of a local temp file or QImageReader of
#     QBuffer/QIODevice of a python buffer in the file cache, to avoid PyQt
#     locking the UI thread
#     See https://code.woboq.org/qt5/qtbase/src/gui/image/qimage.cpp.html#_ZN6QImage8fromDataEPKhiPKc
#     Also see http://blog.ssokolow.com/archives/2019/08/14/displaying-an-image-or-animated-gif-in-qt-with-aspect-ratio-preserving-scaling/

# XXX Use pillow instead which supports more formats? (not clear about the last
#     version for Python 2.7)
#     See https://stackoverflow.com/questions/71112986/retrieve-a-list-of-supported-read-file-extensions-formats
# XXX Note PyQt5 doesn't support JPEG compressed TIFF
#     Eg https://www.nightprogrammer.org/wp-uploads/2013/02/multipage_tif_example.tif
#     fails with 
#       TIFFReadDirectory: Warning, Unknown field with tag 347 (0x15b) encountered.
#       foo: JPEG compression support is not configured.
#       foo: Sorry, requested compression method is not configured.
# XXX Even if that TIFF is saved as lzw, it will load the first page but it
#     won't report as animated
# XXX Note PyQt5 doesn't report mng as animated, it will only load the first frame
# XXX Note PyQt5 doesn't report apng as animated, it will only load the first frame
#     fails with 
#           libpng warning: No space in chunk cache for unknown chunk
# XXX Note PyQt5 fails to load animated webp, static webp load ok
# This is currently 
# ['.bmp', '.dds', '.gif', '.icns', '.ico', '.jp2', '.jpeg', '.jpg', '.mng', 
#  '.pbm', '.pgm', '.png', '.ppm', '.svg', '.svgz', '.tga', '.tif', '.tiff', 
#  '.wbmp', '.webp', '.xbm', '.xpm']
# XXX This causes Qt to load which forces to move Qt patching higher up, move
#     inside Qt appp?
image_extensions = [".%s" % fmt for fmt in QImageReader.supportedImageFormats()]
supported_extensions = image_extensions + [".lst"]
FIRST_IMAGE_DELTA = -float("inf")
LAST_IMAGE_DELTA = float("inf")
slideshow_interval_ms = 5000
# Interval between nextFrame calls. If a frame hasn't been decoded when the
# interval expires, it will be delayed another interval
# XXX Should this be dynamic depending on QImageReader information (eg GIF delay
#     between frames)?
animation_interval_ms = 50
most_recently_used_max_count = 10
stat_timeout_secs = 0.25
# QApplication.keyboardInputInterval() is 400ms, which is too short
listview_keyboard_search_timeout_secs = 0.75
# XXX Have a config to disable placeholders to prevent flashing when browsing?
use_image_placeholders = False
use_thumbnail_placeholders = True


# queue is an old style class, inherit from object to make newstyle
# XXX Use PriorityQueue? Would allow two priorities one for thumbs and another
#     for main image, or even rolling priorities where stale images (thumbs,
#     main) are left with lower prio. Currently this is done by emptying the
#     queue on nagivating away from the image
class Queue(queue.Queue, object):
    """
    Queue with a clear method to flush/drain the queue
    """
    def __init__(self, *args, **kwargs):
        # Call init directly since super can't be used in oldstyle classes which
        # queue is
        queue.Queue.__init__(self, *args, **kwargs)

    def move_to_front(self, item):
        # This assumes the item was in the queue at some point, but will only 
        # be re-inserted if found while traversing, since it's possible the item
        # was removed from a different thread while traversing
        info("move_to_front %r", item)
        entries = []
        found = False
        try:
            while (True):
                entry = self.get_nowait()
                if (entry != item):
                    entries.append(entry)
                else:
                    found = True
        except queue.Empty:
            pass

        if (found):
            self.put(item)

        info("move_to_front restoring %d entries", len(entries))
        for entry in entries:
            self.put(entry)

    def clear(self):
        """
        Clear the queue.
        
        This clears the queue until it finds no more entries, as such it's race
        condition prone unless there are no simultaneous threads putting items
        in the queue.
        """
        info("Queue.clear")
        # Doing gets seems to be the safest and simplest way of draining the
        # queue, other ways may be faster but more brittle or more complicated
        # See https://stackoverflow.com/questions/6517953/clear-all-items-from-the-queue
        entries = []
        try:
            while (True):
                entry = self.get_nowait()
                entries.append(entry)
                info("Drained entry %.200r", entry)
        except queue.Empty:
            pass
        info("Queue.clear done")

        return entries


# XXX Merge FileFetcher and PixmapReader common functionality into an ancestor
#     class QueuedTaskWorker

class FileFetcher(QThread):
    fileFetched = pyqtSignal(str, tuple)

    def __init__(self, request_queue, parent=None):
        """
        @param parent must be not None or the thread will get garbage collected
        """
        super(FileFetcher, self).__init__(parent)
        self.request_queue = request_queue

    def run(self):
        info("FileFetcher.run")
        request_queue = self.request_queue
        while (True):
            data = request_queue.get()
            if (data is None):
                break

            filepath = data
            info("worker prefetching %r (%d in queue)", filepath, self.request_queue.qsize())
            # Store file contents, don't use QImage yet because:
            # - Using QImage.load will block the GUI thread for the whole duration
            #   of the load and conversion, which makes prefetching useless.
            # - Just Converting to QImage here blocks the GUI thread, which makes
            #   prefetching useless
            # - Converting the data to QImage increases 100x fold the cache memory
            #   footprint
            # - Doing QImage conversion on the GUI thread is not that taxing and
            #   doesn't stall for long
            #
            # Also store the file stat otherwise os.stat calls in the main thread
            # get queued behind data requests, taking seconds, making the prefetch
            # useless
            try:
                # Caller expects the original filepath in the reply (specifically,
                # to use as the cache key), don't modify it
                long_filepath = os_path_safelong(filepath)
                
                t = time.time()
                with open(long_filepath, "rb") as f:
                    # XXX Read in chunks to reduce chance of network failures? Could
                    #     also allow aborting to give higher prio to queued transfer
                    #     and to report progress, but the latter would need to be
                    #     cross-thread compatible (in some shared variable?)
                    data = f.read()
                t = time.time() - t
                info("worker prefetched data in %0.2fs %0.2fKB/s %r %d in queue", t, len(data) / (t * 1024.0) if t > 0 else 0, long_filepath, self.request_queue.qsize())
                t = time.time()
                filestat = os.stat(long_filepath)
                t = time.time() - t
                info("worker prefetched stat in %0.2fs %r %d in queue", t, long_filepath, self.request_queue.qsize())
            except:
                # If there was an error, let the caller handle it by placing None
                # data
                exc("Unable to prefetch %r", long_filepath)
                data = None
                filestat = None
            
            self.fileFetched.emit(filepath, (data, filestat))

        info("FileFetcher.run ends")


class PixmapReader(QThread):
    # XXX See https://mayaposch.wordpress.com/2011/11/01/how-to-really-truly-use-qthreads-the-full-explanation/
    # XXX See https://stackoverflow.com/questions/10776509/qthreads-qobject-and-sleep-function
    # XXX See https://woboq.com/blog/qthread-you-were-not-doing-so-wrong.html
    
    pixmapReady = pyqtSignal(str, tuple)
    
    def __init__(self, request_queue, parent=None):
        """
        @param parent must be not None or the thread will get garbage collected
        """
        info("PixmapReader.__init__")
        super(PixmapReader, self).__init__(parent)
        self.request_queue = request_queue
        
    def run(self):
        info("PixmapReader.run")
        request_queue = self.request_queue

        while (True):
            data = request_queue.get()
            if (data is None):
                break

            filepath, (file_data, imageWidget, scale, reader) = data
            
            if (reader is None):
                info("Creating reader")
                buffer = QBuffer()
                buffer.setData(file_data)
                reader = qThreadSafeImageReader(buffer)
                info("Using new reader %r", reader)

            else:
                # Note this will fail if a recycled reader is used in multiple
                # threads simultaneously making the resulting pixmap None.
                # Readers should only be recycled when used for different frames
                # of the same image, but code in nextFrame prevents from queueing
                # multiple frames for the same image, so this should be safe.
                # XXX Prevent by having a single main image queue?
                info("Recycling reader %r", reader)
            info("Reading image %r %d", filepath, len(file_data or []))
            image = reader.read()
            info("Converting image %r", filepath)
            pixmap = QPixmap.fromImage(image)
            info("Scaling pixmap %r", filepath)
            if ((not pixmap.isNull()) and (scale is not None)):
                pixmap = pixmap.scaled(scale, 
                    Qt.KeepAspectRatioByExpanding if (False) else Qt.KeepAspectRatio, 
                        Qt.SmoothTransformation)

            info("Emitting pixmap %r null %s error %s", filepath, pixmap.isNull(), reader.errorString())
            
            self.pixmapReady.emit(filepath, (pixmap, imageWidget))

        info("PixmapReader.run ends")


def split_base_index(s):
    """
    Split the string s into:
    - base, NNN if s has the pattern NNNbase or baseNNN, where base is some
      string and NNN a numeric index.
    - s, None otherwise
    """
    index = None
    base = s

    # XXX This could use regular expressions?

    i = 0
    while ((i < len(s)) and (s[i].isdigit())):
        index = int(s[:i+1])
        base = s[i+1:]
        i += 1

    if (index is None):
        # Try reverse
        # XXX Not clear reverse should be a different call, is it ok for the 
        #     caller to compare a suffix with a preffix vs. not using suffix?
        i = len(s) - 1
        while ((i >= 0) and (s[i].isdigit())):
            index = int(s[i:])
            base = s[:i-1]
            i -= 1

    if (index is None):
        # Try parenthesis
        m = re.search(r"\((\d+)\)", s)
        if (m is not None):
            index = int(m.group(1))
            if (m.start() > 0):
                base = s[:m.start()]
            else:
                base = s[m.end():]
            
    return base, index

def cmp_numerically(a, b):
    """

    Compares filenames pseudo-numerically, eg sorts 9.jpg before 10.jpg and 
    page9.jpg before page10.jpg
    
    Want to sort
        doca-page1.jpg
        doca-page2.jpg
        doca-page10.jpg
        doca-page1.jpg
        doca-page2.jpg
        doca-page10.jpg

    and
        1-doca.jpg
        2-doca.jpg
        10-doca.jpg
        1-docb.jpg
        2-docb.jpg
        10-docb.jpg
    
    XXX Note the above will sort 
        1-docc
        2-docb
        3-doca
      into 
        3-doca
        2-docb
        1-docc
      which is undesirable?

    """
    a, _ = os.path.splitext(a)
    b, _ = os.path.splitext(b)

    a_base, a_index = split_base_index(a)
    b_base, b_index = split_base_index(b)
    
    comp = cmp(a_base, b_base)
    if (comp == 0):
        return cmp(a_index, b_index)

    else:
        return comp

def size_to_human_friendly_units(u):
    """
    @return {string} u as a human friendly power of 1024 unit (TB, GB, MB, KB,
            B)
    """
    d = 1
    for unit in ["B", "KB", "MB", "GB", "TB"]:
        new_d = d * (2 ** 10)
        if (u < new_d):
            break
        d = new_d
        
    return "%0.2f %s" % (u * 1.0/d, unit)


class StatFetcher(QThread):
    statFetched = pyqtSignal(tuple)

    def __init__(self, request_queue, response_queue = None):
        self.request_queue = request_queue
        self.response_queue = response_queue
        return super(StatFetcher, self).__init__()

    def run(self):
        info("StatFetcher started")
        while (True):
            entry = self.request_queue.get()
            if (entry is None):
                self.response_queue.put(None)
                break
            request_id, filepath = entry
            
            info("Popped stat request id %d for %r", request_id, filepath)
            try:
                filestat = os.stat(filepath)
                
            except Exception as e:
                exc("Exception when fetching stat request id %d for %r", request_id, filepath)
                filestat = None

            # Put the stat in the response queue so it can be collected
            # synchronously to the GUI thread, and emit the statFetched signal
            # it too so it can also be done asynchronously to the GUI thread

            entry = (request_id, filepath, filestat)            
            info("Responding stat request id %d for %r", request_id, filepath)
            self.response_queue.put(entry)
            # Theoretically if this queue fills and blocks it would deadlock
            # updateDirpath, but Qt connection queue sizes are supposed to be
            # bounded only by memory. Other options would be to call statFetched
            # directly in the non deferred part of updateDirPath and
            # blockSignal/disconnect()/have an entry flag indicating if it
            # should be emitted or not
            self.statFetched.emit(entry)
        info("StatFetcher ended")


class FileDialog(QDialog):
    """
    Qt file dialog (native or not) is extremely slow on SMB network drives with
    ~200 files on Raspberry Pi (minutes to pop), implement a simple one that
    just works

    Features:
    - Keyboard navigation
    - Mouse navigation
    - Keyboard history navigation
    - Keyboard substring search
    - Threaded file stat
    - Background file stat if foreground exceeded a given timeout

    """
    # XXX Think about what initialization should be elsewhere if the dialog is
    #     kept around and reused
    
    def __init__(self, filepath, parent=None):
        super(FileDialog, self).__init__(parent)

        self.statRequestQueue = Queue()
        # XXX Remove response queue since with signals it's not necessary? (but
        #     it's still used for the blocking phase of updateDirpath, until the
        #     timer fires, and using the notify path has decreased quality like
        #     filtering out unsupported files and sorting?)
        self.statResponseQueue = Queue()
        self.requestId = 0

        # navigationHistoryIndex points to the current entry in the history, -1
        # if empty. updateDirpath will add the current dirpath to the history
        # and increment the index. Lower indices in navigationHistory are older
        # history entries.
        self.navigationHistory = []
        self.navigationHistoryIndex = -1
        
        num_stat_fetchers = 10
        self.statFetchers = []
        for i in xrange(num_stat_fetchers):
            info("Creating statFetcher %d", i)
            statFetcher = StatFetcher(self.statRequestQueue, self.statResponseQueue)
            # Note the default connection parameter AutoConnection will use
            # QueuedConnection (verified), which is what is desired in this case
            # that uses cross thread signals
            statFetcher.statFetched.connect(self.statFetched)
            statFetcher.start()
            self.statFetchers.append(statFetcher)
            
        self.setWindowTitle("Open File")

        self.layout = QVBoxLayout()

        l = QHBoxLayout()
        l.setContentsMargins(0,0,0,0)
        l.setSpacing(0)
        self.pathLayout = l

        w = QWidget()
        w.setLayout(l)
        self.layout.addWidget(w)

        edit = QLineEdit()
        self.edit = edit
        self.layout.addWidget(edit)
                
        listWidget = QListWidget()
        listWidget.itemDoubleClicked.connect(self.entryDoubleClicked)
        listWidget.itemSelectionChanged.connect(lambda : self.edit.setText(listWidget.currentItem().text()))
        listWidget.installEventFilter(self)
        listWidget.setTabKeyNavigation(False)
        self.listWidget = listWidget
        self.listKeyDownTime = 0
        self.listKeyDownText = ""
        
        self.layout.addWidget(listWidget)

        total = QLabel()
        self.total = total
        self.layout.addWidget(total)

        buttonBox = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttonBox.accepted.connect(self.navigate)
        buttonBox.rejected.connect(self.reject)
        self.buttonBox = buttonBox

        self.layout.addWidget(buttonBox)

        self.setLayout(self.layout)

        self.chosenFilepath = None

        dirpath, basename = os.path.split(filepath)

        self.dirpath = ""
        self.navigate(dirpath)
        self.listWidget.setFocus()
        self.resize(500, 400)

    def statFetched(self, entry):
        info("fetched stat %r", entry)
        
        request_id, filepath, filestat = entry
        # This could get emits for a previous id, discard them, in particular
        # this could receive emits after the dialog has been dismissed (since
        # dismissing the dialog doesn't close it, just hides it)
        if (self.requestId == request_id):
            if (stat.S_ISDIR(filestat.st_mode)):
                filename = os.path.basename(filepath)
                items = self.listWidget.findItems(filename, Qt.MatchExactly)
                if (len(items) == 1):
                    item = items[0]
                    # XXX Cache this font?
                    bold_font = item.font()
                    bold_font.setBold(True)
                    item.setFont(bold_font)

                else:
                    error("found %d items for stat %r, expected one", len(items), filepath)

            # XXX This could check if it's in deferred mode and remove any one
            #     entry (or all) from the queue, since the queue is not used
            #     once in deferred mode. This prevent the bulk of the stales
            #     puts that need to be cleanup the next updateDirpath

        else:
            info("Ignoring stale statFetched emit id %d vs. %d %r vs %r for %r", 
                self.requestId, request_id, self.dirpath, os.path.dirname(filepath), filepath)

    def cleanup(self):
        """
        This clears the queues

        Note that after clearing the request queue, a thread could still be
        servicing a previous request, so stale emits can still be emitted and
        queued in the response queue even after the queues have been cleared.
        """
        info("cleanup")
        # Set dirpath to None so any further emits are ignored for good measure
        self.dirpath = None
        self.clearQueues()

        info("Signaling %d fetchers to end", len(self.statFetchers))
        for fetcher in self.statFetchers:
            self.statRequestQueue.put(None)
        info("Signaled")
        # Wait for each fetcher after queing as many stop items as fetchers,
        # can't send and wait individually since the queue is shared by fetchers
        # and it's not deterministic which fetcher will end when
        info("Waiting for %d fetchers to end", len(self.statFetchers))
        for fetcher in self.statFetchers:
            fetcher.wait()
        info("Waited")
        

    def clearQueues(self):
        """
        Note this doesn't guarantee the queues remain empty when the function
        returns in the presence of other threads putting items on them
        (specifically, the worker thread may still be servicing a previous
        request after clearQueues is called, so it can still put items/emit).
        """
        info("clearQueues")
        self.statRequestQueue.clear()
        self.statResponseQueue.clear()

    def accept(self, *args, **kwargs):
        info("accept")
        # Note closing a dialog actually calls hide(), not closeEvent, cleanup
        # needs to be called from accept/reject
        self.cleanup()
        return super(FileDialog, self).accept(*args, **kwargs)

    def reject(self, *args, **kwargs):
        info("reject")
        # Note closing a dialog actually calls hide(), not closeEvent, cleanup
        # needs to be called from accept/reject
        self.cleanup()
        return super(FileDialog, self).reject(*args, **kwargs)


    def updateDirpath(self, dirpath):
        # Only navigate calls this, should pass absolute and unicode
        assert os.path.isabs(dirpath) and isinstance(dirpath, unicode)

        # listdir and isdir can take time on network drives
        QApplication.setOverrideCursor(Qt.WaitCursor)

        # Clear any pending (stale) requests or responses
        # Note there may be stale emits and puts in the response queue even
        # after clearing, but they are discarded at fetchedStat time based on
        # the request id
        self.clearQueues()
        
        # XXX Do this in a thread and block with timeout so the box can at least
        #     be dismissed when network paths are slow?
        # XXX Also do the stat fetching in a similar way?
        info("listdir %r", dirpath)
        names = os.listdir(dirpath)
        info("listdired %d files", len(names))

        filenames = []
        dirnames = []

        # Create a new request id and request all stats, the request id will be
        # used in the puts and emits to be able to tell the current
        # updateDirpath from stale reponses from a previous updateDirpath
        self.requestId += 1
        for name in names:
            path = os.path.join(dirpath, name)
            info("Requesting stat id %d for %r", self.requestId, path)
            self.statRequestQueue.put((self.requestId, path))

        stat_start_time_secs = time.time()
        processed_names = len(names)
        while (processed_names > 0):
            if (time.time() - stat_start_time_secs > stat_timeout_secs):
                info("Stat timed out, deferring further stats")
                # Switch all to non directories, this will cause a different
                # list than with non deferred stats in the following ways:
                # - directories won't be clustered on top
                # - the not-yet-processed directories won't be displayed in bold
                #   until the deferred stat is processed
                # - files won't be filtered by supported extensions
                #
                # but there's little that can be done in that case since there's
                # no filename vs. dir information until all the stats have been
                # fetched, and modifying the list after it's show is bad UX
                # since the user may be interacting with it
                #
                # XXX Switching to deferred will leave all remaining puts as
                #     stale items in the response queue since the statFetched
                #     handler doesn't remove them (it's not aware of deferred
                #     vs. non deferred). These stale puts will be discarded with
                #     the next requestId but it's not clean to leave those
                #     around, fix?
                #     Note most of these puts will be removed by clearQueues()
                #     rather than filtered by request id anyway.
                #     Note this is not the only source of stale puts, there's a
                #     small source of stale puts even after clearQueues() if
                #     some thread was still servicing a request when
                #     clearQueues() happened
                
                filenames = names
                dirnames = []
                break
            request_id, filepath, filestat = self.statResponseQueue.get()
            # filestat could be None if there was a transient error, etc, in that
            # case, default to non directory
            is_dir = ((filestat is not None) and stat.S_ISDIR(filestat.st_mode))
            name = os.path.basename(filepath)

            # Ignore stale requests from old IDs.
            #
            # This could use the dirpath to tell requests apart, but it's not
            # clear if it's theoretically possible to get stale requests for the
            # current path if there's a fast switch from pathA to pathB to pathA
            # again
            if (request_id == self.requestId):
                _, ext = os.path.splitext(name)
                
                if (is_dir):
                    dirnames.append(name)

                # XXX This filter is missing when deferring because has to be
                #     applied only to files and not dirs, reapply when the stats
                #     come?

                elif (ext.lower() in supported_extensions):
                    filenames.append(name)
                processed_names -= 1

            else:
                info("Ignoring stale put id %d vs. %d %r vs. %r for %r", 
                    self.requestId, request_id, dirpath, os.path.dirname(filepath), filepath)

        # XXX Allow sorting by date if stats were not deferred?
        dirnames.sort(cmp=cmp_numerically)
        filenames.sort(cmp=cmp_numerically)

        self.listWidget.clear()
        # Add ".." to navigate to parent if not root
        if (not os_path_isroot(dirpath)):
            self.listWidget.addItem("..")
        self.listWidget.addItems(dirnames)
        self.listWidget.addItems(filenames)

        self.total.setText("%d files and dirs" % self.listWidget.count())

        # Focus on the child coming from, if any
        # XXX If this is doing history navigation it should focus on the
        #     previous entry in the history?
        if (self.dirpath.startswith(dirpath)):
            dirname = ""
            basename = self.dirpath
            while (len(basename) > len(dirpath)):
                basename, dirname = os.path.split(basename)
            
            info("path %r dirpath %r dirname %r ", dirpath, self.dirpath, dirname)

            if (dirname != ""):
                items = self.listWidget.findItems(dirname, Qt.MatchExactly)
                if (len(items) == 1):
                    self.listWidget.setCurrentItem(items[0])

                else:
                    error("Expected one item for %r, found %d %s", dirname, len(items), [item.text() for item in items])
        
        # Count could be 0 on empty root dirs
        # XXX Also on invalid ones, but that's probably not properly handled yet
        #     anyway
        elif (self.listWidget.count() > 0):
            self.listWidget.setCurrentItem(self.listWidget.item(0))

        # Clear the path buttons by removing from the layout and setting the
        # parent to None (otherwise the parent will still keep a reference and 
        # they will still be displayed)
        while (self.pathLayout.count() > 0): 
            item = self.pathLayout.takeAt(0)
            # widget() can be None for eg stretches
            if (item.widget() is not None):
                item.widget().setParent(None)
            
        # Convert path to list of directory names
        dirname = dirpath
        names = []
        while True:
            dirname, basename = os.path.split(dirname)
            if (basename == ""):
                names.append(dirname)
                break
            names.append(basename)
        names.reverse()

        # Build the path buttons
        info("dir names are %s", names)
        for i, name in enumerate(names):
            button = QToolButton()
            button.setText(name)
            button_path = os.path.join(*names[:i+1])
            # Note that lambdas in Python don't capture loop-modified variables, 
            # only the last iteration value is captured. To prevent that
            # the loop-modified variable needs to be set as default value of a
            # lambda parameter
            # See https://stackoverflow.com/questions/3431676/creating-functions-in-a-loop
            # XXX In addition lambdas cause leaks with connect?
            #     https://stackoverflow.com/questions/35819538/using-lambda-expression-to-connect-slots-in-pyqt   
            button.clicked.connect(lambda state, button_path=button_path: self.navigate(button_path))
            self.pathLayout.addWidget(button)

        self.pathLayout.addStretch()
        
        self.dirpath = dirpath
        self.dirnames = dirnames
        self.filenames = filenames

        QApplication.restoreOverrideCursor()


    def navigate(self, path = None, add_to_history = True):
        if (path is None):
            path = self.edit.text()
        info("navigate %r", path)
        
        if (not os.path.isabs(path)):
            path = os.path.expanduser(path)
            path = os.path.join(self.dirpath, path)
        
        # Make absolute and normalize (this resolves ".." anywhere, but
        # specifically the trailing ".." that may have been added when
        # doubleclicking on the listwidget)
        path = os_path_abspath(path)
        path = unicode(path)

        info("abspath is %r", path)
        if (os.path.isdir(path)):
            if (add_to_history):
                self.navigationHistoryIndex += 1
                # Trim the history and push the current path (could also insert
                # instead of trimming, but feels more confusing UX-wise?)
                self.navigationHistory = self.navigationHistory[:self.navigationHistoryIndex]
                self.navigationHistory.append(path)
                info("Added %r to history %d/%d %r", path, 
                    self.navigationHistoryIndex, len(self.navigationHistory), 
                    self.navigationHistory)

            self.updateDirpath(path)

        else:
            # XXX This should probably be a function rather than having the
            #     caller access chosenFilepath directly
            # XXX Dialog could also return list of files and the current dir
            self.chosenFilepath = path
            self.accept()

    def eventFilter(self, source, event):
        """
        Do listWidget keyboard navigation on left/right/backpace.

        Do listWidget history navigation on alt+left/alt+right

        Do listWidget substring search on key input. Qt already does prefix
        search, but unfortunately it uses the default MatchStartsWith when
        qabstractitemview.keyboardsearch calls model.match and Qlistwidget
        setModel cannot be called with a model that changes that behavior
        because it's disabled
        
        See https://code.woboq.org/qt5/qtbase/src/widgets/itemviews/qabstractitemview.cpp.html
        See https://code.woboq.org/qt5/qtbase/src/corelib/itemmodels/qabstractitemmodel.h.html
        See https://code.woboq.org/qt5/qtbase/src/widgets/itemviews/qlistwidget.cpp.html#_ZN11QListWidget8setModelEP18QAbstractItemModel

        Another option is to derive from QListWidget and reimplement
        keyboardSearch

        XXX Do directory num files calculation on spacebar/alt+enter, all on shift+alt+enter? 

        """
        if (event.type() == QEvent.KeyPress):
            info("eventFilter %r key %d text %r", event.text(), event.key(), event.text())
            assert (source is self.listWidget)
            if (event.key() in [Qt.Key_Backspace, Qt.Key_Left]):
                if (event.modifiers() & Qt.AltModifier):
                    if (0 < self.navigationHistoryIndex < len(self.navigationHistory)):
                        info("Navigating history %d/%d %r", self.navigationHistoryIndex, 
                            len(self.navigationHistory), self.navigationHistory)
                        self.navigationHistoryIndex -= 1
                        path = self.navigationHistory[self.navigationHistoryIndex]
                        self.navigate(path, add_to_history=False)
                
                else:
                    self.navigate("..")
                return True

            elif (event.key() == Qt.Key_Right):
                if (event.modifiers() & Qt.AltModifier):
                    if (0 <= self.navigationHistoryIndex < len(self.navigationHistory) - 1):
                        info("Navigating history %d/%d %r", self.navigationHistoryIndex, 
                            len(self.navigationHistory), self.navigationHistory)
                        self.navigationHistoryIndex += 1
                        path = self.navigationHistory[self.navigationHistoryIndex]
                        self.navigate(path, add_to_history=False)
                
                else:
                    self.navigate()
                return True

            elif (
                (event.text() != "") and (event.text() in string.printable) and 
                (event.key() != Qt.Key_Tab)
                ):

                event_time = time.time()
                current_row = self.listWidget.currentIndex().row()
                enter_pressed = ((event.key() ==  Qt.Key_Enter) or (event.key() == Qt.Key_Return))
                
                if ((event_time - self.listKeyDownTime) < listview_keyboard_search_timeout_secs):
                    if (enter_pressed):
                        # XXX Allow to go back if shift+enter is pressed?
                        current_row += 1

                    else:
                        self.listKeyDownText += event.text()
                    
                elif (enter_pressed):
                    return False
                
                else:
                    self.listKeyDownText = event.text()
                    current_row += 1
                
                self.listKeyDownTime = event_time

                # XXX This could also hide the unmatched items instead of just
                #     traversing to the next matching item? or have a
                #     search/filter textedit
                
                # This could use model().match(Qt.MatchContains) or derive from
                # QListWidget and override keyboardSearch but this way is simple
                # enough
                new_current_item = None
                for i in xrange(self.listWidget.count()):
                    row = (current_row + i) % self.listWidget.count()
                    item = self.listWidget.item(row)
                    info("Checking item %d/%d %r vs. %r", row, self.listWidget.count(), 
                        item.text().lower(), self.listKeyDownText.lower())
                    if (self.listKeyDownText.lower() in item.text().lower()):
                        info("found item %r", item.text())
                        new_current_item = item
                        break

                if (new_current_item is not None):
                    self.listWidget.setCurrentItem(new_current_item)
                
                else:
                    self.listKeyDownText = self.listKeyDownText[:-1]

                # XXX QToolTip is a simple solution to display the current
                #     search string but not ideal: the tooltip fades if the
                #     text didn't change (because eg return or an invalid
                #     character is pressed), instead of restarting the timer on
                #     every showtext. Could use an explicit QLabel out of the 
                #     layout.
                #
                #     See https://stackoverflow.com/questions/65022624/how-create-a-visual-aid-for-tablewidget
                QToolTip.showText(
                    self.listWidget.parentWidget().mapToGlobal(self.listWidget.geometry().bottomRight()), 
                    self.listKeyDownText, 
                    self.listWidget, 
                    QRect(), 
                    int(listview_keyboard_search_timeout_secs * 1000.0)
                )

                return True
                
        return super(FileDialog, self).eventFilter(source, event)

    def entryDoubleClicked(self, item):
        self.navigate()


class ImageWidget(QLabel):
    # See https://stackoverflow.com/questions/30553467/resizable-pyqt-widget-displaying-an-image-with-fixed-aspect-ratio
    # XXX Have a message capability for when in fullscreen
    def __init__(self, parent=None):
        super(ImageWidget, self).__init__(parent)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.setMinimumSize(1, 1)
        self.setAutoFillBackground(True)
        self.borderStyle = ""
        self.backgroundColor = Qt.darkGray
        self.backgroundColorStyle = ""
        # Calculate and set the styles
        self.setBorder(0)
        self.setBackgroundColor(self.backgroundColor)
        
        self.originalPixmap = None
        self.text = None
        self.rotation_degrees = 0
        self.gamma = 1.0
        self.fitToSmallest = False
        self.scroll = 0

    def setPixmap(self, pixmap):
        """
        Caller needs to call resizePixmap to update
        """
        self.originalPixmap = pixmap

    def setText(self, text):
        """
        Caller needs to call resizePixmap to update
        """
        self.text = text

    def commitStyles(self):
        current_style = self.styleSheet()
        new_style = self.borderStyle + self.backgroundColorStyle
        # Don't cause repaint if no change
        if (current_style != new_style):
            dbg("old style %s new style %s", current_style, new_style)
            self.setStyleSheet(new_style)

    def setBorder(self, width, color=None):
        self.borderStyle = "" if (width == 0) else " border: %dpx solid %s;" % (width, QColor(color).name())
        self.commitStyles()

    def setBackgroundColor(self, color):
        # Background can be changed via 
        #    pal.setColor(QPalette.Background, ...)
        #    setPalette()
        # but the palette is ignored once a stylesheet is set with setStyleSheet, 
        # and the stylesheet is the only way of setting a border without 
        # overriding the paint method, so change the background via setStyleSheet
        # instead of using setPalette()
        dbg("setBackground from %s to %s, %s", self.backgroundColor, color, color != self.backgroundColor)
        self.backgroundColor = color
        self.backgroundColorStyle = "" if color is None else " background-color: %s;" % QColor(color).name()
        self.commitStyles()
    
    def toggleFit(self):
        self.fitToSmallest = not self.fitToSmallest
        self.scroll = 0
        self.resizePixmap(self.size())

    def rotatePixmap(self, degrees):
        # XXX Have a -1 or > 360 rotation that rotates dynamically so it takes
        #     the most space?
        info("rotatePixmap from %d to %d", self.rotation_degrees, degrees)
        self.rotation_degrees = degrees
        self.scroll = 0
        self.resizePixmap(self.size())

    def gammaCorrectPixmap(self, gamma):
        # Don't trigger repaint if already that value
        if (gamma != self.gamma):
            info("gammaCorrectPixmap from %d to %d", self.gamma, gamma)
            self.gamma = gamma
            self.resizePixmap(self.size())


    def resizePixmap(self, size):
        info("resizing pixmap from %s to %s and %s", self.originalPixmap.size(), size, self.size())
        
        # XXX Reset scroll if resizing window (resizes, fullscreen), or clamp 
        #     below

        pixmap = self.originalPixmap

        if (self.gamma != 1.0):
            # XXX This is not very efficient, conversions from pixmap to image
            #     and back are done every time and at the original image size,
            #     should probably be cached and merged with the scaling/rotating
            #     below? (still takes only 20ms time on laptop)
            #     Also, .scaled does that conversion from QPixmap to QImage and back
            #     again under the hood
            #     See https://www.qt.io/blog/2009/12/16/qt-graphics-and-performance-an-overview
            # XXX This processing could be done on a background thread on QImages,
            #     but not on QPixmap
            #     See https://stackoverflow.com/questions/10307860/what-is-the-difference-between-qimage-and-qpixmap
            # XXX Do auto-gamma, see https://stackoverflow.com/questions/61695773/how-to-set-the-best-value-for-gamma-correction
            try:
                info("Gamma correcting %2.2f", self.gamma)
                import numpy as np
                image = pixmap.toImage()
                width = image.width()
                height = image.height()
                depth = image.depth() / 8
                
                ptr = image.constBits()
                ptr.setsize(width * height * depth)
                im = np.frombuffer(ptr, np.uint8)

                lut = np.array([int(255.0 * (i / 255.0) ** (1.0 / self.gamma)) for i in xrange(256)], dtype=np.uint8)
                im = lut[im]

                image = QImage(im.data, width, height, width * depth, QImage.Format_RGB32)

                pixmap = QPixmap.fromImage(image)
                info("Gamma corrected")

            except ImportError as e:
                warn("Can't import numpy, image won't be gamma corrected")

        
        if (self.rotation_degrees != 0):
            # This transformation is lossless, could keep the rotated pixmap
            # instead of the original one, but it doesn't seem to be a lengthy
            # operation, so it's ok to do it
            info("Rotating %d", self.rotation_degrees)
            t = QTransform()
            t.rotate(self.rotation_degrees)
            pixmap = pixmap.transformed(t)
            info("rotated")
        
        info("scaling %s", size)
        pixmap = pixmap.scaled(size, 
            Qt.KeepAspectRatioByExpanding if (self.fitToSmallest) else Qt.KeepAspectRatio, 
            Qt.SmoothTransformation
        )
        info("scaled")

        if (self.fitToSmallest):
            if (size.width() == pixmap.width()):
                info("fit to width")
                self.setAlignment(Qt.AlignTop | Qt.AlignHCenter)
                info("scrolling %s", self.scroll)
                pixmap.scroll(0, -self.scroll, 0, 0, size.width(), pixmap.height())
                info("scrolled")
            else:
                info("fit to height")
                self.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)
                info("scrolling %s", self.scroll)
                pixmap.scroll(-self.scroll, 0, 0, 0, pixmap.width(), size.height())
                info("scrolled %s", self.scroll)
        
        else:
            info("fit to both")
            self.setAlignment(Qt.AlignHCenter| Qt.AlignVCenter)

        if (self.text is not None):
            painter = QPainter(pixmap)
            pen = QPen(Qt.green, 3)
            font = painter.font()
            font.setPointSize(12)
            font.setBold(True)
            font.setFamily("Courier")
            painter.setFont(font)
            painter.setPen(pen)
            # XXX This wraps the text to the pixmap width, ideally it should
            #     spill to the margins of the pixmap if there's room?
            painter.drawText(pixmap.rect(), Qt.TextWrapAnywhere, self.text)
            painter.end()

        super(ImageWidget, self).setPixmap(pixmap)

    def resizeEvent(self, event):
        info("resizeEvent %s", event.size())
        if (self.originalPixmap is not None):
            self.resizePixmap(event.size())

        return super(ImageWidget, self).resizeEvent(event)

    def sizeHint(self):
        width = 10
        height = 10
        if (self.originalPixmap is not None):
            ag = QApplication.desktop().availableGeometry(-1)
            width = self.pixmap().width()
            height = self.pixmap().height()
            aspect_ratio_w = ag.width() * 1.0 / width
            aspect_ratio_h = ag.height() * 1.0 / height
            aspect_ratio = min(aspect_ratio_w, aspect_ratio_h)
            width = int(aspect_ratio * width)
            height = int(aspect_ratio * height)

            width = self.pixmap().width()
            height = self.pixmap().height()

            info("sizeHint %dx%d", width, height)
        
        return QSize(width, height)

# Image states
# init -> loading (or queued) -> decoded (error)
# init -> loading (or queued) -> loaded -> decoding -> decoded (or error)
IMAGE_STATE_INIT =  0
IMAGE_STATE_LOADING = 1
IMAGE_STATE_LOADED = 2
IMAGE_STATE_DECODING = 3
IMAGE_STATE_DECODED = 4

class ImageViewer(QMainWindow):
    
    def __init__(self):
        super(ImageViewer, self).__init__()

        self.slideshow_timer = None

        self.animation_timer = None
        self.animation_timer_start = None
        self.animation_reader = None
        # Initialize to 0 and 1 so statusbar displays 1/1 on non-animated files
        self.animation_frame = 0
        self.animation_count = 1
        self.animation_fps = 0

        self.recent_filepaths = []

        # Use the scripts directory as FileDialog opening dir
        self.image_filepath = sys.argv[0]
        
        # Thumbnail pane settings
        self.thumbnail_columns = 5
        self.thumbnail_rows = 5
        self.thumbnails_per_page = self.thumbnail_columns * self.thumbnail_rows
        self.cached_files = []
        # XXX This could have a max_size_bytes instead
        # XXX This is x2 because the cache has +- around the current image, 
        #     fix it so it has more in the direction of the movement?
        self.cached_files_max_count = self.thumbnails_per_page * 2 + 2

        self.prefetch_request_queue = Queue()
        # XXX Check any relationship between prefetch and cache counts, looks
        #     like there shouldn't be any even if the current is evicted from
        #     lru since the current is also kept separately? (although prefetch
        #     > cache is probably silly)
        # XXX The "browse direction" cache should be larger than the opposite
        #     direction cache? (eg have more files forward if browsing forward)
        #     otherwise the cache is halved with typical forward browsing
        self.prefetched_images_max_count = self.thumbnails_per_page * 2
        self.prefetch_pending = set()
        self.prefetcher_count = 2

        self.decoder_request_queue = Queue()
        self.decoder_count = multiprocessing.cpu_count()
        
        def receive_file(filepath, data):
            for thumbWidget in self.thumbWidgets:
                if ((filepath == thumbWidget.image_filepath) and 
                    # Ignore if the thumbnail already has the right image XXX
                    # Why do images come for thumbnails that already have
                    # decoded the image, cache too small? multiple thumbs with
                    # the same image? investigate? Looks like as gotoImage is
                    # called, new prefetches can evict the thumbnails?
                    (thumbWidget.image_state <= IMAGE_STATE_LOADING)):
                    thumbWidget.image_state = IMAGE_STATE_LOADED
                    thumbWidget.image_data = data
                    
            self.prefetch_pending.discard(filepath)
            # Note this evicts and inserts even if the file is invalid, which 
            # is good since it won't try to fetch the file again. If the problem
            # is transient, the user can reload manually
            if (len(self.cached_files) >= self.cached_files_max_count):
                info("evicting %r for %r", self.cached_files[-1][0], filepath)
                self.cached_files.pop(-1)
            info("inserting in cache %r", filepath)
            self.cached_files.insert(0, (filepath, data))
            info("inserted in cache %r", filepath)
            if (self.image_filepath == filepath):
                self.updateImageData(filepath, None, data)
            
            self.updateThumbnails()
            self.updateStatus()

        def receive_image(filepath, pixmap, imageWidget):
            info("Receiving pixmap %r", filepath)

            if (filepath != self.image_filepath):
                info("Main image changed after decoding, ignoring %r vs. %r", filepath, self.image_filepath)
                return

            self.clearMessage()
            info("Decoded %r %dx%d", filepath, pixmap.width(), pixmap.height())
        
            imageWidget.image_state = IMAGE_STATE_DECODED
            if (pixmap.isNull()):
                # XXX This should turn animation off if enabled, otherwise will
                #     cause infinite dialog boxes? Animation won't be enabled if
                #     the file failed to read, but it Could happen if the file
                #     was read and some middle frame is corrupt?
                warn("Invalid image file %r", filepath)
                QMessageBox.information(self, "Image Viewer",
                    "Invalid image file %s." % filepath)
                pixmap = self.errorPixmap
                
            # XXX Is this image to pixmap to setpixmap redundant? should we use image?
            #     or pixmap?
            self.imageWidget.setPixmap(pixmap)

            if ((self.animation_timer is not None) and (self.animationAct.isChecked())):
                new_report_time = time.time()
                self.animation_fps = (1.0 / ((new_report_time - self.animation_report_time) or 1.0))
                self.animation_report_time = new_report_time

            self.updateImage()
            self.updateStatus()
            self.updateActions()

        def receive_thumbnail(filepath, pixmap, thumbWidget):
            info("Receiving pixmap %r", filepath)
            
            # Ignore if this thumbnail no longer shows this filepath,
            # leave whatever state
            if (thumbWidget.image_filepath != filepath):
                return

            thumbWidget.image_state = IMAGE_STATE_DECODED
            if (pixmap.isNull()):
                pixmap = self.errorPixmap
            thumbWidget.setPixmap(pixmap)
            thumbWidget.resizePixmap(thumbWidget.size())

        def receive_pixmap(filepath, payload):
            pixmap, imageWidget = payload
            if (imageWidget is self.imageWidget):
                receive_image(filepath, pixmap, imageWidget)
            else:
                receive_thumbnail(filepath, pixmap, imageWidget)

        # XXX To use a threadpool needs to be a qrunnable but qrunnables are not
        #     qobjects so they cannot send signals, so the qrunnable needs to
        #     inherit from both qrunnable and qobject or to be passed some
        #     sacrificial qobject to emit on
        # QThreadPool.globalInstance().start(t)
        # threads.append(t)

        # Create prefetcher threads and pool them via the prefetch_request_queue
        for i in xrange(self.prefetcher_count):
            info("Creating file fetcher %d", i)
            t = FileFetcher(self.prefetch_request_queue, self)
            t.fileFetched.connect(receive_file)
            t.start()

        # Create decoder threads and pool them via the decoder_request_queue
        for i in xrange(self.decoder_count):
            info("Creating pixmap decoder %d", i)
            t = PixmapReader(self.decoder_request_queue, self)
            t.pixmapReady.connect(receive_pixmap)
            t.start()

        w = QWidget(self)
        self.setCentralWidget(w)
        hl = QHBoxLayout()
        w.setLayout(hl)

        splitter = QSplitter(Qt.Horizontal)
        hl.addWidget(splitter)
        hl.setContentsMargins(0, 0, 0, 0)
        hl.setSpacing(0)

        w = QWidget(self)
        self.thumbnailsWidget = w
        gl = QGridLayout()
        gl.setContentsMargins(0, 0, 0, 0)
        gl.setSpacing(1)
        # Put the thumbnails on a top widget, this makes easier to hide with a
        # single call and also fixes some layout issues that happen if the
        # gridlayout is added directly to the parent
        w.setLayout(gl)
        self.thumbWidgets = []
        for i in xrange(self.thumbnails_per_page):
            col = i % self.thumbnail_columns
            row = i / self.thumbnail_columns
            thumbWidget = ImageWidget()
            thumbWidget.image_state = IMAGE_STATE_INIT
            thumbWidget.image_data = None
            thumbWidget.image_filepath = None
            gl.addWidget(thumbWidget, row, col)
            gl.setRowStretch(row, 1)
            gl.setRowMinimumHeight(row, 50)
            gl.setColumnStretch(col, 1)
            gl.setColumnMinimumWidth(col, 50)
            self.thumbWidgets.append(thumbWidget)
            # XXX Missing connecting button click to changing main image and
            #     focusing cell
            
        splitter.addWidget(w)

        self.thumbnailsWidget.hide()

        imageWidget = ImageWidget()
        imageWidget.setMouseTracking(True)
        imageWidget.installEventFilter(self)
        imageWidget.image_state = IMAGE_STATE_INIT
        self.imageWidget = imageWidget

        splitter.addWidget(imageWidget)

        splitter.setSizes([600, 800])

        self.emptyPixmap = QPixmap(10, 10)
        # XXX Should probably use whatever background color the grid has?
        self.emptyPixmap.fill(Qt.white)

        self.queuedPixmap = QPixmap(10, 10)
        # XXX Should refresh on changes to background color?
        self.queuedPixmap.fill(self.imageWidget.backgroundColor)

        self.loadingPixmap = QPixmap(10, 10)
        self.loadingPixmap.fill(Qt.blue)
        
        self.decodingPixmap = QPixmap(10, 10)
        self.decodingPixmap.fill(Qt.magenta)

        self.queuedDecodingPixmap = QPixmap(10, 10)
        self.queuedDecodingPixmap.fill(Qt.yellow)

        self.errorPixmap = QPixmap(10, 10)
        self.errorPixmap.fill(Qt.red)

        # Initialize for the first time while waiting to load
        self.imageWidget.setPixmap(self.loadingPixmap)

        self.createActions()
        self.createMenus()
        self.createStatus()
        
        self.setWindowTitle("Image Viewer")
        
        if (len(sys.argv) > 1):
            filepath = unicode(sys.argv[1])
        else:
            # Default parameter if running under debugger
            if (hasattr(sys, 'gettrace') and sys.gettrace() is not None):
                filepath = unicode(R"C:\Users\Public\Pictures\greg-rutkowski-castle-defence-1920.jpg")
            else:
                filepath = None

        if (filepath is not None):
            self.loadImage(filepath)
            # XXX Actions are disabled by default, this is needed so shortcut
            #     keys work before updateimagedata is called, move elsewhere?
            self.updateActions()
            self.setRecentFile(filepath)
        
        # show() in order to initialize frameGeometry()
        self.show()
        
        ag = QApplication.desktop().availableGeometry(-1)

        width, height = ag.size().width(), ag.size().height()
        frame_width = self.frameGeometry().width() - self.geometry().width()
        frame_height = self.frameGeometry().height()  - self.geometry().height()
        # XXX resize doesn't include the titlebar, this causes windows larger
        #     than available geometry
        # See https://doc.qt.io/qt-5/application-windows.html#window-geometry
        # XXX Note before showing self.frameGeometry() is the same as geometry
        #     needs to show but that needs empty pixmap support
        # Note setGeometry is client rect relative, use move instead which is 
        # frame rect relative for QWindows
        #self.setGeometry(ag.topLeft().x() + frame_width / 2, ag.topLeft().y() + frame_height / 2, width - frame_height, height - frame_height)
        self.move(ag.topLeft())
        self.resize(width - frame_width, height - frame_height)

    def clearRequests(self):
        info("clearRequests")

        info("Clearing requests")
        entries = self.prefetch_request_queue.clear()
        self.prefetch_pending -= set(entries)
        info("removing %d stale prefetch requests", len(entries))
        # XXX This could just set all pending thumbWidgets to INIT? (but may be
        #     too conservative for files that were read just before clearing?)
        entries = set(entries)
        for thumbWidget in self.thumbWidgets:
            if ((thumbWidget.image_filepath in entries) and 
                (thumbWidget.image_state == IMAGE_STATE_LOADING)):
                thumbWidget.image_state = IMAGE_STATE_INIT
                    
        entries =[fp for (fp, payload) in self.decoder_request_queue.clear()]
        info("removing %d stale decode requests", len(entries))
        entries = set(entries)
        for thumbWidget in self.thumbWidgets:
            if ((thumbWidget.image_filepath in entries) and 
                (thumbWidget.image_state == IMAGE_STATE_DECODING)):
                thumbWidget.image_state = IMAGE_STATE_LOADED
        info("Cleared requests")
        

    def clearQueues(self):
        info("clearQueues")
        self.prefetch_request_queue.clear()
        self.prefetch_pending.clear()

        self.decoder_request_queue.clear()

    def cleanup(self):
        info("Signaling %d prefetchers to end", self.prefetcher_count)
        for _ in xrange(self.prefetcher_count):
            self.prefetch_request_queue.put(None)
            # XXX Missing .wait the QThread, but they are not stored anywhere?
        info("Signaled prefetchers")
        info("Signaling %d decoders to end", self.prefetcher_count)
        for _ in xrange(self.decoder_count):
            self.decoder_request_queue.put(None)
            # XXX Missing .wait the QThread, but they are not stored anywhere?
        info("Signaled decoders")
        
    def closeEvent(self, event):
        info("closeEvent")
        # XXX Ignore cleanup at closeEvent time since it blocks unnecessarily
        #     at exit time when there are pending prefetches
        # self.cleanup()

        return super(ImageViewer, self).closeEvent(event)

    def eventFilter(self, source, event):
        if (event.type() == QEvent.MouseButtonDblClick):
            assert (source is self.imageWidget)
            self.fullscreenAct.setChecked(not self.fullscreenAct.isChecked())
            self.fullscreenToggled()

        elif ((event.type() == QEvent.MouseButtonRelease) and (event.button() == Qt.MiddleButton)):
            self.slideshowAct.setChecked(not self.slideshowAct.isChecked())
            self.slideshowToggled()
            
        return super(ImageViewer, self).eventFilter(source, event)

    def askForFilepath(self):
        filepath = None
        if (self.slideshow_timer is not None):
            self.slideshow_timer.stop()

        restore_animation = False
        if (self.animationAct.isChecked() and self.animationAct.isEnabled()):
            restore_animation = True
            self.animationAct.setChecked(False)
            
        # Remove prefetches that haven't been serviced yet to prevent prefetches
        # fighting for filesystem bandwidth with the filedialog list and stat
        # (note the prefetches in flight will still be serviced and put in the
        # reponse queue)
        # XXX Ideally pause/stop/abort in flight too?
        # Reset image_filepaths so updatethumbnails stops requesting images
        # XXX Also do something so in flight requests don't start decoding threads?
        # XXX Stopping requests this way is kludgy, should have some background
        #     task enabled flag that is checked in multiple places?
        filepaths = self.image_filepaths
        self.image_filepaths = None
        self.clearRequests()

        dirpath = os.path.curdir if self.image_filepath is None else self.image_filepath
        use_native_dialog = False
        if (use_native_dialog):
            filepath, _ = QFileDialog.getOpenFileName(self, "Open File", dirpath)

            if (filepath == ""):
                filepath = None

        else:
            dlg = FileDialog(dirpath, self)
            if (dlg.exec_() == QDialog.Accepted):
                # XXX Should set all thumbnails to INIT?
                filepath = dlg.chosenFilepath
            else:
                # Restore image_filepaths and force request any necessary images
                self.image_filepaths = filepaths
                self.gotoImage(0)
                
        # Restart the animation timer if it was running, if the new image is
        # loaded and found not to have animations, the timer will be stopped
        # there
        if (restore_animation):
            self.animationAct.setChecked(True)

        if (self.slideshow_timer is not None):
            self.slideshow_timer.start(slideshow_interval_ms)

        assert (filepath is None) or isinstance(filepath, unicode) 

        return filepath


    def openRecentFile(self):
        info("openRecentFile")
        action = self.sender()
        if (action is not None):
            self.loadImage(action.data())
            # Don't reshuffle the recent file list, changing all the shortcuts
            # is bad UX


    def setRecentFile(self, filepath):
        """
        See
        https://github.com/baoboa/pyqt5/blob/master/examples/mainwindows/recentfiles.py

        Note adding actions dynamically didn't work because when the action is
        removed from the menu, the action seems to remain referenced somewhere
        in the system, which causes all the actions to refer to the first recent
        file ever registered.

        XXX The above problem could be related to using lambdas in a loop?
        """
        info("setRecentFile %r", filepath)

        # XXX This needs to remove duplicates?
        self.recent_filepaths.insert(0, os_path_abspath(filepath))
        if (len(self.recent_filepaths) > most_recently_used_max_count):
            self.recent_filepaths.pop(-1)

        for i in xrange(most_recently_used_max_count):
            if (i < len(self.recent_filepaths)):
                filepath = self.recent_filepaths[i]
                info("Setting MRU %s", filepath)
                
                self.recentFileActs[i].setShortcut(QKeySequence("%d"%i))
                self.recentFileActs[i].setText(filepath)
                self.recentFileActs[i].setData(filepath)
                self.recentFileActs[i].setVisible(True)

            else:
                self.recentFileActs[i].setVisible(False)
   
    def open(self):
        filepath = self.askForFilepath()
        if (filepath is not None):
            self.loadImage(filepath)
            self.setRecentFile(filepath)

    def openFromClipboard(self):
        clipboard = qApp.clipboard()
        filepath = clipboard.text()
        filepaths = filepath.splitlines()
        
        info("openFromClipboard %r %r", filepath, filepaths)
        if (filepath != ""):
            if (len(filepaths) > 1):
                self.image_filepaths = filepaths
                
                self.loadImage(filepaths[0], 0, len(filepaths))
                # Only add the first one to MRU
                self.setRecentFile(filepaths[0])
            else:
                
                self.loadImage(filepath)
                self.setRecentFile(filepath)

    def copyToClipboard(self):
        # XXX Note the clipboard contents disappear on Windows when the app exits, 
        #     needs some exit code
        #     See https://stackoverflow.com/questions/2007103/how-can-i-disable-clear-of-clipboard-on-exit-of-pyqt-application
        # XXX This could copy the .lst or all the files in the current slideshow
        #     separated by newlines
        info("copyToClipboard %r", self.image_filepath)
        clipboard = qApp.clipboard()
        
        clipboard.setText(self.image_filepath)
        # XXX Ideally would like to put both filepath and pixmap into the
        #     clipboard but setText and setPixmap overwrite each other's
        #     contents, is there a way of copying multiple MIME types to the
        #     clipboard?
        #     clipboard.setPixmap(self.imageWidget.originalPixmap)
        #     See https://www.qtcentre.org/threads/39887-QMimeData-using-setText-and-setUrls-at-the-same-time


    def getDataFromCache(self, filepath, clear=False):
        """
        @return 
        - bytes if image in cache and didn't fail to load
        - None if image in cache and failed to load
        - False if image not in cache
        """
        assert isinstance(filepath, unicode) 
        # XXX Qt already has QPixmapCache, look into it?

        # Get the file from the cache and bring it to the front if in the cache,
        # request it and put it in the front otherwise

        # XXX Use a set/dict/ordereddict for this test
        try:
            filepaths = [entry_filepath for entry_filepath, entry_data in self.cached_files]
            dbg("filepaths in cache %r", filepaths)
            i = filepaths.index(filepath)
            info("cache hit for %r", filepath)
            entry = self.cached_files[i]
            entry_filepath, entry_data = entry
            self.cached_files.pop(i)
            # Put it at the beginning of the LRU cache
            self.cached_files.insert(0, entry)

        except ValueError:
            info("cache miss for %r", filepath)

            if (clear):
                # Empty the request queues so:
                # - this image request takes higher priority
                # - stale prefetches around this image don't accumulate (ok
                #   to discard because prefetch is restarted below)
                # - stale thumbnail requests don't accumulate when moving across
                #   thumbnail pages (ok to discard because updateThumbnails will
                #   also re-request as needed) 

                # XXX Additionally this could signal the prefetch threads to
                #     abort the current transfers, but to do it efficiently, it
                #     requires the prefetch threads to chunk the reads and check
                #     on some flag

                # XXX Should this happen even if there's a cache hit so stale
                #     thumbnails are always removed?
                
                # XXX Inferring there are stale thumbnails/prefetches from the
                #     block parameter is hacky and will probably break once
                #     reading the main image is non-blocking, should probably be
                #     moved elsewhere, in gotoImage, etc?

                entries = self.prefetch_request_queue.clear()
                info("removing %d stale prefetch requests", len(entries))
                # Note clearing the prefetch_pending set here may cause the
                # prefetch_pending set to get out of sync wrt
                # prefetch_response_queue: a prefetch request may be serviced by
                # the prefetch thread before it had the chance to be serviced in
                # this thread and this thread will receive a stale prefetch
                # without a corresponding item in the prefetch_pending set
                # cleared here. This is ok since this thread .discards items
                # from the set instead of .removing them, and .discard doesn't
                # require the item to be in the set

                # Only remove entries that were cleared, otherwise entries that
                # are currently being downloaded could be downloaded twice
                self.prefetch_pending -= set(entries)
                # XXX Handling the thumbnail state here is not very clean, find
                #     another place to do it?
                # XXX This could just set all pending thumbWidgets to INIT? (but
                #     may be too conservative for files that were read just
                #     before clearing?)
                entries = set(entries)
                for thumbWidget in self.thumbWidgets:
                    if ((thumbWidget.image_filepath in entries) and 
                        (thumbWidget.image_state == IMAGE_STATE_LOADING)):
                        thumbWidget.image_state = IMAGE_STATE_INIT
                
                entries =[fp for (fp, _, _, _, _) in self.decoder_request_queue.clear()]
                info("removing %d stale decode requests", len(entries))
                entries = set(entries)
                for thumbWidget in self.thumbWidgets:
                    if ((thumbWidget.image_filepath in entries) and 
                        (thumbWidget.image_state == IMAGE_STATE_DECODING)):
                        thumbWidget.image_state = IMAGE_STATE_LOADED
                    
            # The filepath is not in the cache, request if not already pending
            if (filepath not in self.prefetch_pending):
                info("prefetch pending miss for %r", filepath)
                info("ordering prefetch for %r", filepath)
                self.prefetch_request_queue.put(filepath)
                self.prefetch_pending.add(filepath)

            else:
                info("prefetch pending hit for %r", filepath)

            entry_data = False

        return entry_data

    def updateThumbnails(self):
        info("updateThumbnails")
        if (not self.thumbnailsWidget.isVisible()):
            return
            
        filepaths = self.image_filepaths
        if (filepaths is None):
            # When a single image was loaded, only show that image in the
            # thumbnails (until the thumnbails are navigated and filepaths
            # populated)
            # XXX This is also hit when askForFilepath aborts outstanding
            #     requests, fix?
            filepaths = [self.image_filepath]
            
        # Collect the filepaths in this thumbnail page. Account for more
        # thumbnail slots than filepaths or viceversa
        try:
            # XXX This fails if thumbnails have the same path, can happen with
            #     .lst files with repeated entries, support or filter at .lst
            #     load time?
            i = filepaths.index(self.image_filepath)
            page = i / self.thumbnails_per_page
            i = page*self.thumbnails_per_page
            filepaths = filepaths[i:i+self.thumbnails_per_page]
        except ValueError:
            filepaths = self.image_filepaths[:self.thumbnails_per_page]

        # Fill the thumbnail slots with the appropriate image: filepath image,
        # loading image, decoding image, failed image, or empty image
        for i, filepath in enumerate(filepaths):
            thumbWidget = self.thumbWidgets[i]
            scaled_pixmap = thumbWidget.originalPixmap
            
            if (thumbWidget.image_filepath != filepath):
                thumbWidget.image_state = IMAGE_STATE_INIT
                thumbWidget.image_data = None
            
            # State switch from INIT to LOADING, set the placeholder pixmap to
            # loading/queued if INIT or LOADING ,and state switch to LOADED if
            # done LOADING
            if ((thumbWidget.image_state == IMAGE_STATE_INIT) or 
                (thumbWidget.image_state == IMAGE_STATE_LOADING)):
                # XXX Should this be done unconditionally outside so the LRU
                #     cache is primed? This may cause fighting with the prefetch
                #     if the prefetch count is improperly set wrt the number of
                #     thumbnails. Does an LRU cache make any sense when the
                #     cache is properly sized wrt thumbnails and prefetch?
                entry = self.getDataFromCache(filepath)
                # XXX Checking the internal queue member variable is not nice,
                #     but it's only used for UI status and not worth doing
                #     something thread-safe that is going to be racy anyway?
                #     Note this doesn't trigger "deque mutated during iteration"
                #     exception because "in" is atomic from GIL point of view
                if (use_thumbnail_placeholders):
                    scaled_pixmap = self.queuedPixmap if filepath in self.prefetch_request_queue.queue else self.loadingPixmap
                
                if (entry is False):
                    # Note there's no race condition here between getDataFromCache
                    # request finishing before this state is set, because the 
                    # state is only modified on this thread and this thread is
                    # still busy
                    thumbWidget.image_state = IMAGE_STATE_LOADING
                    
                else:
                    thumbWidget.image_state = IMAGE_STATE_LOADED
                    # It can happen that by the time this is fetched, dealing
                    # with _LOADED the data may not be in the cache anymore, so
                    # store it in thumbWidget.image_data
                    thumbWidget.image_data = entry

            if (thumbWidget.image_state == IMAGE_STATE_LOADED):
                entry = thumbWidget.image_data
                # XXX This needs to check for null entry if it failed to load?
                file_data, file_stat = entry
                info("Requesting thumbnail %r", filepath)
                    
                if (use_thumbnail_placeholders):
                    scaled_pixmap = self.decodingPixmap
                thumbWidget.image_state = IMAGE_STATE_DECODING

                # This could use the thumbWidget.size() but then it
                # needs refreshing when the splitter changes
                # XXX Use screen DPI to calculate the best thumbnail size?
                # Note it's ok for this request to race the setPixmap below
                # since the response is handled in this thread so it's not racy
                self.decoder_request_queue.put((filepath, (file_data, thumbWidget, QSize(150, 150), None)))

            if (thumbWidget.image_state == IMAGE_STATE_DECODING):
                # XXX Checking the internal queue member variable is not nice,
                #     but it's only used for UI status and not worth doing
                #     something thread-safe that is going to be racy anyway?
                # XXX Without converting to list, this can return deque mutated
                #     during iteration?
                if (use_thumbnail_placeholders):
                    q = list(self.decoder_request_queue.queue)
                    scaled_pixmap = self.queuedDecodingPixmap if any([(filepath == fp) for (fp, payload) in q]) else self.decodingPixmap

            # Don't cause continuous bitmap setting if already set
            if (scaled_pixmap is not thumbWidget.originalPixmap):
                info("Setting thumbnail %r", filepath)
                thumbWidget.setPixmap(scaled_pixmap)
                # XXX Can the grid be updated instead of each individual imagewidget?
                thumbWidget.resizePixmap(thumbWidget.size())
                
            # Set the current thumbnail rectangle and background or remove it 
            # in case it was previously set
            if (filepath == self.image_filepath):
                thumbWidget.setBackgroundColor(Qt.white)
                thumbWidget.setBorder(3, Qt.red)

            else:
                # XXX Needs to refresh all thumbnails background if user changes
                #     background color?
                thumbWidget.setBackgroundColor(Qt.darkGray)
                thumbWidget.setBorder(0)

            thumbWidget.image_filepath = filepath
        
        # Set all unused thumbnail slots to empty
        for i in xrange(len(self.thumbWidgets)-len(filepaths)):
            thumbWidget = self.thumbWidgets[len(filepaths)+i]
            # XXX This unnecessarily sets the thumbnail state that is probably
            #     already set and relies on checking originalPixmap to avoid
            #     repaints, should there be an empty state or set them to
            #     DECODED and look at the filepath to tell between empty and ?
            thumbWidget.image_filepath = None
            thumbWidget.image_state = IMAGE_STATE_INIT
            thumbWidget.image_data = None
            if (thumbWidget.originalPixmap is not self.emptyPixmap):
                thumbWidget.setPixmap(self.emptyPixmap)
                thumbWidget.setBackgroundColor(Qt.white)
                thumbWidget.setBorder(0)
                # Force a repaint since the pixmap was changed
                thumbWidget.resizePixmap(thumbWidget.size())

    def loadImage(self, filepath, index = None, count = None, frame = None):
        info("loadImage %r i %s c %s f %s", filepath, index, count, frame)
        info("Supported extensions %s", supported_extensions)
        assert isinstance(filepath, unicode) 
        if (filepath.lower().endswith(".lst")):
            lst_filepath = filepath
            filepaths = []
            long_filepath = os_path_safelong(lst_filepath)
            try:
                info("loading lst file %r", long_filepath)
                with open(long_filepath, "r") as f:
                    filepaths = f.readlines()
                info("loaded lst file %r", long_filepath)

            except:
                exc("Unable to read %s", long_filepath)
            
            if (len(filepaths) == 0):
                # XXX Missing setting failed image
                QMessageBox.information(self, "Image Viewer",
                    "Cannot load %s." % lst_filepath)
                return
            
            filepaths = [unicode(filepath.strip()) for filepath in filepaths]
            # XXX This should also allow lst files containing directories? What
            #     files to include? allow wildcards (multiple wildcards could be
            #     done by replicating the entry)? include all and let the
            #     program fail to load whatever wrong file?
            for i, filepath in enumerate(filepaths):
                if (not os.path.isabs(filepath)):
                    filepaths[i] = os.path.join(os.path.dirname(lst_filepath), filepath)

            filepath = filepaths[0]
            self.image_filepaths = filepaths
            self.image_index = 0
            index = 0
            count = len(filepaths)
            self.image_index = 0
            self.image_count = count

        else:
            # If there's no index and count information and this is not a frame
            # reset filenames cache
            if (index is None):
                # XXX Index and count passed as parameter is messy, should only
                #     update the internal variables when needed?
                # XXX Why this special casing to reset the filenames cache?
                if (frame is None):
                    self.image_filepaths = None
                    self.image_index = 0
                    self.image_count = 1

            else:
                self.image_index = index
                self.image_count = count
            
        # Update these early in case the image fails to load other filepaths can
        # still be cycled
        if ((self.image_filepaths is not None) and (len(self.image_filepaths) > 1)):
            self.firstImageAct.setEnabled(True)
            self.lastImageAct.setEnabled(True)
            self.prevImageAct.setEnabled(True)
            self.nextImageAct.setEnabled(True)
            self.slideshowAct.setEnabled(True)

        self.image_filepath = filepath
        self.imageWidget.image_state = IMAGE_STATE_INIT

        info("Caching %r", filepath)
        self.showMessage("Loading...")
        self.imageWidget.image_state = IMAGE_STATE_LOADING
        data = self.getDataFromCache(filepath, clear=True)
        
        # XXX Unify the path so it always goes through the FileFetcher thread
        #     even if the data is in the cache?
        if (data is not False):
            self.imageWidget.image_state = IMAGE_STATE_LOADED
            self.updateImageData(filepath, frame, data)

        else:
            if (use_image_placeholders):
                self.imageWidget.setPixmap(self.loadingPixmap)
            self.updateImage()
            self.updateThumbnails()

        self.updateStatus()

    def updateImageData(self, filepath, frame, data):
        info("updateImageData %r", filepath)
        if (data is None):
            QMessageBox.information(self, "Image Viewer",
                "Cannot load %s." % filepath)
            pixmap = self.errorPixmap
            
        else:
            if (use_image_placeholders):
                pixmap = self.decodingPixmap
            else:
                pixmap = self.imageWidget.originalPixmap
            file_data, file_stat = data

            if (file_data is None):
                # setData needs a bytearray, not a list
                # XXX review file_data uses, find the best place to detect None
                #     file_data?
                file_data = ""

            info("Decoding %r", filepath)
            self.showMessage("Decoding...")
            
            # If this is not a sequential frame or there's no exising reader,
            # create a new reader
            if (
                (self.animation_reader is None) or 
                (frame is None) or
                (frame != (self.animation_frame + 1)) or
                (frame >= self.animation_reader.imageCount())
                ):

                # If animating, don't use placeholders to avoid flashing
                if (self.animation_count > 1):
                    pixmap = self.imageWidget.originalPixmap

                # This can be using a new reader because 
                # 1) It's a new file
                # 2) It requested an out of order frame (including the first frame)
                info("Using new reader for %d bytes", len(file_data))
                buffer = QBuffer()
                buffer.setData(file_data)
                reader = qThreadSafeImageReader(buffer)
                info("Created reader %r", reader)
                # XXX Missing rotating images using the EXIF information
                #     QImageReader.setAutoTransform is Qt 5.5, but 5.3.1 is the
                #     one on pip Windows 10 
                #     See https://stackoverflow.com/questions/15123340/qimage-loads-with-wrong-orientation-for-certain-images
                #     Install from git instead of pip which has Qt 5.7.1? 
                #     See https://github.com/pyqt/python-qt5
                #     XP from Anaconda has Qt 5.6 and Linux from apt has Qt 5.11 
                # XXX Or use pillow
                # XXX Or use native (see chatgpt: APP1 marker, orientation tag 0x0112)

                if (reader.imageCount() > 1):
                    # XXX Get other from reader.loopCount(), reader.nextImageDelay()
                    # This image has animations and we are using a new reader,
                    # initialize animation machinery
                    if (self.animation_timer is None):
                        timer = QTimer()
                        timer.timeout.connect(self.nextFrame)
                        timer.setSingleShot(True)
                        self.animation_timer = timer
                        self.animation_timer_start = self.animation_timer
                        timer.start(animation_interval_ms)

                    elif (not self.animationAct.isChecked()):
                        self.animation_timer.stop()
                    
                    self.animation_report_time = 0.0
                    self.animation_frame = 0
                    self.animation_count = reader.imageCount()
                    self.animation_reader = reader
                    
                    if (frame is not None):
                        # This is a non-sequential frame, advance as many frames
                        # as necessary by reading, since QGIFHandler doesn't
                        # support jumpToNextImage or jumpToImage 
                        # See https://code.woboq.org/qt5/qtbase/src/plugins/imageformats/gif/qgifhandler.cpp.html
                        frame = frame % reader.imageCount()
                        for _ in xrange(frame-1):
                            reader.read()
                        self.animation_frame = frame

                elif (self.animation_reader is not None):
                    # No animations in this image but the previous image had,
                    # cleanup animation machinery
                    info("Cleaning up animation machinery")
                    assert frame is None
                    self.animation_reader = None
                    if (self.animationAct.isChecked()):
                        self.animation_timer.stop()
                    self.animation_timer = None
                    self.animation_count = 1
                    self.animation_frame = 0
            
            else:
                info("Recycling reader %r", self.animation_reader)
                reader = self.animation_reader
                self.animation_frame = frame

                if (self.animation_count > 1):
                    # Showing the decoding pixmap makes animation flash, keep
                    # the old pixmap while decoding on the second and successive
                    # frames
                    # XXX Do this also when looping?
                    pixmap = self.imageWidget.originalPixmap

        if (pixmap is not self.imageWidget.originalPixmap):
            self.imageWidget.setPixmap(pixmap)
            self.updateImage()
        
        if (data is None):
            # Unable to read file, pixmap was set to error, nothing more to be
            # done, set the state to DECODED
            self.imageWidget.image_state = IMAGE_STATE_DECODED

        else:
            # Reading takes the most time, especially for svg, queue on a QT
            # thread (verified it releases the GIL)
            
            # XXX This is replicated in getDataFromCache, refactor?
            entries =[fp for (fp, payload) in self.decoder_request_queue.clear()]
            info("removing ~%d stale decode requests", len(entries))
            entries = set(entries)
            for thumbWidget in self.thumbWidgets:
                if ((thumbWidget.image_filepath in entries) and 
                    (thumbWidget.image_state == IMAGE_STATE_DECODING)):
                    thumbWidget.image_state = IMAGE_STATE_LOADED

            self.animation_frame = frame or 0
            self.imageWidget.image_state = IMAGE_STATE_DECODING
            self.decoder_request_queue.put((filepath, (file_data, self.imageWidget, None, reader)))

            # Pending thumbnails have been removed from the decoder queue,
            # refresh
            self.updateThumbnails()

        

    def updateImage(self, redraw=True):
        info("updateImage")

        # Display text information in fullscreen, in windowed mode this is in
        # the statusbar
        # XXX Do we need to tell the difference between image_count = 1 because
        #     the directory hasn't been listed yet vs. the directory only
        #     contains one image file?
        s = "" if (self.image_count == 1) else " [%d / %d]" % (self.image_index + 1, self.image_count)
        if (self.fullscreenAct.isChecked()):
            if (self.animationAct.isChecked() and self.animationAct.isEnabled()):
                s += " %d/%d %2.2f fps" % (self.animation_frame, self.animation_count, self.animation_fps)
            self.imageWidget.setText("%s%s" % (self.image_filepath, s))

        else:
            self.setWindowTitle("Image Viewer - %s%s" % (
                os.path.basename(self.image_filepath), 
                s
            ))
            
            self.imageWidget.setText("")
        
        if (redraw):
            self.imageWidget.resizePixmap(self.imageWidget.size())

    def showMessage(self, msg, timeout_ms=0):
        self.status_message_timer.stop()
        self.status_message_widget.setText(msg)
        if (timeout_ms > 0):
            self.status_message_timer.start(timeout_ms)
            
    def clearMessage(self):
        self.status_message_widget.setText("")

    def updateStatus(self):
        info("updateStatus")

        widget_size = self.imageWidget.size()
        
        # XXX This shows the previous image information when loading, get the
        #     state and show ?? instead?
        orig_pixmap = self.imageWidget.originalPixmap
        orig_pixmap_size = orig_pixmap.size()
        pixmap_size = self.imageWidget.pixmap().size()

        info("pixmap size %s widget_size %s", widget_size, pixmap_size)
        if (widget_size.width() != self.imageWidget.pixmap().width()):
            zoom_factor = (pixmap_size.width() * 100) / orig_pixmap_size.width()
        else:
            zoom_factor = (pixmap_size.height() * 100) / orig_pixmap_size.height()

        self.statusIndex.setText("%d / %d" % (self.image_index + 1, self.image_count))
        self.statusResolution.setText("%d x %d x %d BPP" % (orig_pixmap.width(), orig_pixmap.height(), orig_pixmap.depth()))

        self.statusZoom.setText("%d%% %s %s %d d %d/%d%s" % (
            zoom_factor,
            "S" if self.imageWidget.fitToSmallest else "L",
            "%2.1fg" % self.imageWidget.gamma,
            self.imageWidget.rotation_degrees,
            self.animation_frame + 1,
            self.animation_count, 
            " %2.1f" % self.animation_fps if (self.animationAct.isEnabled() and self.animationAct.isChecked()) else ""
        ))

        self.statusFilepath.setText(os_path_abspath(self.image_filepath))

        info("Statusing")
        # XXX Store elsewhere instead of hitting the cache which will mess with
        #     the LRU? Find it directly in cached_files?
        data = self.getDataFromCache(self.image_filepath) 
        if (data):
            file_data, file_stat = data
            if (file_data is None):
                file_data = []
        else:
            file_data = None
            file_stat = None
        self.statusSize.setText("%s / %s (%d: %s)" % (
            "?? MB" if (file_data is None) else size_to_human_friendly_units(len(file_data)), 
            # XXX This should use image.byteCount() but there's none for QPixmap
            size_to_human_friendly_units(orig_pixmap.width() * orig_pixmap.height()*orig_pixmap.depth()),
            # XXX This accesses the queue directly, could use prefetch_pending
            #     but it's not updated frequently
            len(self.prefetch_pending),
            size_to_human_friendly_units(
                sum([(0 if entry_file_data is None else len(entry_file_data)) for entry_filepath, (entry_file_data, entry_file_stat) in self.cached_files]) + 
                # XXX These getsize cause a noticeable stall, and they are not
                #     cached yet so can't be obtained from the cache
                # sum([os.path.getsize(entry_filepath) for entry_filepath in self.prefetch_pending])
                0
                )
        ))

        filedate = None if file_stat is None else datetime.datetime.fromtimestamp(file_stat.st_mtime)
        self.statusDate.setText("%s" % ("??-??-?? ??:??:??" if (filedate is None) else filedate.strftime("%Y-%m-%d %H:%M:%S")))

        info("Statused")

    def deleteImage(self):
        if (QMessageBox.question(self, "Image Viewer",
            "Delete %s." % os.path.basename(self.image_filepath), buttons=QMessageBox.Yes|QMessageBox.No|QMessageBox.Cancel, defaultButton=QMessageBox.Yes) == QMessageBox.Yes):
            # Clear the file cache and force the directory to be reloaded
            
            # XXX This interacts badly when there's an .lst file loaded, the
            #     current image directory is loaded instead of reloading the
            #     .lst file, fix

            # XXX With .lst images does this mean to remove the image from the
            #     .lst too? only from the .lst?
            
            # XXX What to do when a single file was loaded and it's deleted?
            self.image_filepaths = None
            os.remove(self.image_filepath)
            # Filling the file cache will reset self.image_index when
            # self.image_filepath is not found, so the delta has to be the
            # current self.image_index
            self.gotoImage(self.image_index)
            
    def refreshImage(self, all=False):
        if (all):
            # Clear the file cache and force the directory to be reloaded
            # XXX This interacts badly when there's an .lst file loaded, the
            #     current image directory is loaded instead of reloading the
            #     .lst file, fix
            self.image_filepaths = None
            self.cached_files = []
            for thumbWidget in self.thumbWidgets:
                thumbWidget.image_filepath = None
                thumbWidget.image_state = IMAGE_STATE_INIT
                thumbWidget.image_data = None

        else:
            # XXX This try is not necessary unless loadimage is non-blocking 
            #     because cached_files.index should always have the image?
            try:
                filepath = self.image_filepath
                filepaths = [entry_filepath for entry_filepath, entry_data in self.cached_files]
                i = filepaths.index(filepath)
                self.cached_files.pop(i)
                for thumbWidget in self.thumbWidgets:
                    if (thumbWidget.image_filepath == filepath):
                        thumbWidget.image_filepath = None
                        thumbWidget.image_state = IMAGE_STATE_INIT
                        thumbWidget.image_data = None
                        
            except ValueError:
                pass

        self.gotoImage(0)
        
    def animationToggled(self):
        if (self.animationAct.isChecked()):
            info("starting animation timer")
            self.animation_timer_start = self.animation_timer
            self.animation_timer.start(animation_interval_ms)
            
            # fps indicator will be shown when the new frame is loaded
        
        else:
            info("stopping animation timer")
            self.animation_timer.stop()
            # Refresh the status bar and image to remove the fps indicator
            self.updateImage()
            self.updateStatus()
        
    def cycleBackgroundColor(self, forward=True):
        backgroundColors = [Qt.white, Qt.lightGray, Qt.gray, Qt.darkGray, Qt.green, Qt.red, Qt.blue, Qt.magenta, Qt.cyan, Qt.black ]
        assert self.imageWidget.backgroundColor in backgroundColors
        delta = 1 if forward else -1
        color = backgroundColors[(backgroundColors.index(self.imageWidget.backgroundColor) + delta + len(backgroundColors)) % len(backgroundColors)]
        info("Setting background color to %s", QColor(color).name())
        self.imageWidget.setBackgroundColor(color)
        
    def slideshowToggled(self):
        if (self.slideshowAct.isChecked()):
            info("creating timer")
            timer = QTimer()

            timer.timeout.connect(self.nextImage)
            # Use a singleshot timer that gets reset on every new image loaded
            # - prevents continuously loading if the timer is less than the load
            #   time
            # - allows navigating manually at the same time the timer is running
            timer.setSingleShot(True)
            self.slideshow_timer = timer

            self.nextImage()

        else:
            info("destroying timer")
            self.slideshow_timer.stop()
            self.slideshow_timer = None

    def gammaCorrectionToggled(self):
        # XXX Allow increment/decrement or several values and cycle through them
        #     like it's done with background colors
        if (self.gammaCorrectAct.isChecked()):
            self.imageWidget.gammaCorrectPixmap(2.2)
        else:
            self.imageWidget.gammaCorrectPixmap(1.0)

        self.updateStatus()

    def thumbnailsToggled(self):
        info("thumbnailsToggled")
        states = ((True, False), (True, True), (False, True))
        i = states.index((self.imageWidget.isVisible(), self.thumbnailsWidget.isVisible()))
        delta =  -1 if (QApplication.keyboardModifiers() & Qt.ShiftModifier) else 1
        next_state = states[(i + delta) % len(states)]
        self.imageWidget.setVisible(next_state[0])
        self.thumbnailsWidget.setVisible(next_state[1])
        if (self.thumbnailsWidget.isVisible()):
            # The filepath cache will be None the first time after loading a
            # single image, force loading the filepath cache so the thumbnails
            # can be loaded. This also causes updateThumbnails to be called
            self.gotoImage(0)
            
    def fullscreenToggled(self):
        #self.setWindowFlags(self.windowFlags() ^ Qt.FramelessWindowHint)
        # Needs showing after changing flags
        #self.show()

        self.updateImage(False)

        if (self.fullscreenAct.isChecked()):
            info("starting fullscreen")
            if (False):
                self.windowed_rect = self.frameGeometry()
                
                sg = QApplication.desktop().screenGeometry(-1)
                ag = QApplication.desktop().availableGeometry(-1)
                self.setGeometry(ag)
            self.menuBar().hide()
            self.statusBar().hide()
            self.showFullScreen()
            
        else:
            info("restoring windowed")
            if (False):
                print "restoring windowed", self.windowed_rect
                
                self.setFrameGeometry(self.windowed_rect)
                print "new geometry", self.frameGeometry()
            self.menuBar().show()
            self.statusBar().show()
            self.showNormal()
            
        
    def gotoImage(self, delta):
        """
        Navigate to the Nth previous or next image.

        @param delta Number of images to move forwards (positive) or backwards
            (negative). Normally will be 1 or -1 when navigating left/right keys
            but could be larger when driven with the mousewheel or other keys 
            Also called with delta = 0 to refresh filepaths, etc
        """
        info("gotoImage %s", delta)
        if (self.image_filepaths is None):
            # Initialize filepaths with the files in the current directory

            image_dirname = os.path.dirname(self.image_filepath)
            info("listing %r", image_dirname)
            QApplication.setOverrideCursor(Qt.WaitCursor)
            try:
                # XXX This could happen on another thread
                filenames = os.listdir(image_dirname)

            except:
                # This can fail if the host is down or if the path is invalid,
                # in that case return empty filenames
                warn("Error listing dir %r", image_dirname)
                filenames = []
            QApplication.restoreOverrideCursor()
            info("listed %r", image_dirname)
            
            # XXX Right now this ignores .lst files because it would replace 
            #     image_filepaths, fix?
            filenames = filter(lambda s: any([s.lower().endswith(ext) for ext in image_extensions]), filenames)
            # XXX Allow sorting by date (getting the date will be slow, will
            #     need latency hiding)
            filenames.sort(cmp=cmp_numerically)

            filepaths = [os.path.join(image_dirname, filename) for filename in filenames]
            
            self.image_filepaths = filepaths

        else:
            # XXX If filepath is a directory here, replace and insert the
            #     contents in filepaths? (eg if the program was opened with a
            #     path as argument or if a path was found in the .lst file).
            #     Will need to take care of setting the index at the beginning
            #     or end of the inserted list depending on delta
            filepaths = self.image_filepaths
        
        filepath = None

        # This could be not in list if an invalid file was introduced
        # XXX This needs error handling if the current dirpath is invalid, 
        #     in which case filepaths is empty and should go directly to show
        #     the open dialog box
        # XXX This fails if images have the same path, can happen with .lst
        #     files with repeated entries, support or filter at .lst load time?
        try:
            prev_i = filepaths.index(self.image_filepath)

        except ValueError as e:
            prev_i = 0

        if (delta == FIRST_IMAGE_DELTA):
            i = 0

        elif (delta == LAST_IMAGE_DELTA):
            i = len(filepaths) - 1

        else:

            i = (prev_i + delta + len(filepaths)) % len(filepaths)
            # Offer to load a new file if at the end
            if ((prev_i + delta < 0) or (prev_i + delta >= len(filepaths))):
                filepath = self.askForFilepath()

        if (filepath is not None):
            self.loadImage(filepath)

        else:
            self.loadImage(filepaths[i], i, len(filepaths))
            # Prefetch around the current image if not already pending or
            # prefetched
            
            # Start with the current index, then prefetch half prefetch count
            # forwards and then half backwards. This helps fetching the next
            # probable image assuming the browsing direction is forwards and if
            # there's a thumbnail page being shown (prefetches will finish out
            # of order with multiple prefetch threads, but still prioritizes
            # what is probably visible)
            
            # XXX When navigating (eg by pgup/pgdown) to the middle of a new
            #     thumbnail page, blindly prioritizing forward images can cause
            #     invisibile thumbnails in the next page to be fetched before a
            #     previous visible thumbnail in this page, should prioritize 
            #     visible thumbnails?
            # XXX This should favor the browsing direction?
            # XXX Think if this is the right prefetch behavior, right now when
            #     moving up/down/left/right it's fetching and evicting images,
            #     have some page granularity so it doesn't fetch if moving inside 
            #     the same page?
            # XXX Move this prefetch after updateThumbnails so the thumbnails
            #     are requested first and then any additional prefetches, but
            #     will still fight and evict thumbnails if one and the other are
            #     not aware of each other?
            for j in xrange(self.prefetched_images_max_count):
                delta = j
                if (j <= self.prefetched_images_max_count / 2):
                    delta = j
                else:
                    # prefetch backwards, starting with the previous image
                    delta = (self.prefetched_images_max_count / 2) - j 
                filepath = filepaths[(i + delta + len(filepaths)) % len(filepaths)]
                if ((filepath not in self.prefetch_pending) and 
                    # XXX Have a set for cached images instead of a an all() reduce
                    all([entry_filepath != filepath for entry_filepath, entry_data in self.cached_files])):
                    info("ordering prefetch for %r", filepath)
                    self.prefetch_request_queue.put(filepath)
                    self.prefetch_pending.add(filepath)
                
        if (self.slideshow_timer is not None):
            self.slideshow_timer.start(slideshow_interval_ms)

        self.updateThumbnails()
        
    def toggleFit(self):
        self.imageWidget.toggleFit()
        self.scroll = 0
        # XXX This should toggle between fit to largest, fit to smallest and
        #     original size? (but original size may need scrolling support)
        if (self.imageWidget.fitToSmallest):
            self.toggleFitAct.setText("&Fit To Largest")

        else:
            self.toggleFitAct.setText("&Fit To Smallest")

        self.updateStatus()
        
    def rotateImage(self, delta_degrees):
        self.imageWidget.rotatePixmap(
            ((self.imageWidget.rotation_degrees + delta_degrees) % 360) )

        self.updateStatus()

    def firstImage(self):
        self.gotoImage(FIRST_IMAGE_DELTA)

    def lastImage(self):
        self.gotoImage(LAST_IMAGE_DELTA)


    def getCanvasPixmapLimits(self):
        pixmap = self.imageWidget.pixmap()
        size = self.imageWidget.size()
        
        if (pixmap.width() != size.width()):
            canvas_limit = size.width()
            pixmap_limit = pixmap.width()

        else:
            canvas_limit = size.height()
            pixmap_limit = pixmap.height()
        
        return canvas_limit, pixmap_limit

    def nextFrame(self):
        # It's possible that this is a stale callback from a previous animation
        # image timer, ignore if so. Note that despit both the call to stop()
        # and nextframe() being done in the UI thread, stop() is racy wrt to the
        # timeout event being posted on the UI thread, so just stopping the
        # previous timer before creating the new one doesn't prevent this issue,
        # and the timer object being the same needs to be checked as well.
        stale_frame = self.animation_timer_start is not self.animation_timer
        info("nextFrame %d stale %s", self.animation_frame, stale_frame)
        if (not stale_frame):
            if ((self.animation_timer is not None) and (self.animationAct.isChecked())):
                self.animation_timer_start = self.animation_timer
                self.animation_timer.start(animation_interval_ms)

            # In addition to stale timer calls, there's an additional race
            # condition that causes null pixmaps in receive_pixmap because a
            # recycled reader is accessed simultaneously from two threads: This
            # happens when the decoder thread for frame N is not done reading
            # when frame N+1 is scheduled, even the decoder queue is cleared
            # before queueing frame N+1, frame N can be currently being decoded,
            # so not in the queue and not removed. This doesn't happen with
            # different (non frame) images because there's a check for stale
            # decoding comparing filenames.

            # The symptom is that pixmap is null in receive_image and the
            # "invalid image file" pops up. Probably this can still happen fi by
            # the time loadImage is called the timer is already in the queue,
            # investigate further?
            
            # XXX Theoretically could also happen with non-animated images if
            #     one image is switched on off and on again fast enough?
            
            # Possible fixes:
            # - Reschedule/skip the frame if the previous frame hasn't been
            #   decoded yet? (have a setting to reschedule vs. skip?
            #   skipping would also fix the "same image switched fast
            #   enough" case since the second decoding is redundant?)
            # - do a sequence id to check for stale frames instead of
            #   checking filepath?
            # - mutex the access to a recycled reader? But this won't
            #   guarantee frame ordering and will confuse the reader since
            #   threads can be interrupted and serviced out of order
            # - Have a single queue for decoding images? (only images
            #   recycle readers, thumbnails don't)

            # Detect if the image has been decoded and reschedule otherwise
            if (self.imageWidget.image_state == IMAGE_STATE_DECODED):
                self.loadImage(self.image_filepath, frame=self.animation_frame + 1)
            else:
                # XXX Skip this frame? Flag and schedule as soon as it's ready
                #     instead of delaying one full interval?
                info("Previous frame not ready, delaying")

    def prevImage(self):
        info("prevImage")

        # XXX This needs fixing when the first image in a list failed to load so
        #     there's no current image

        canvas_limit, pixmap_limit = self.getCanvasPixmapLimits()
    
        # Scroll one full canvas_height if possible, otherwise reset scroll and 
        # go to the next image
        
        info("prevImage scroll %d canvasl %d pixmapl %d", self.imageWidget.scroll, canvas_limit, pixmap_limit )
        if (self.imageWidget.scroll > 0):
            self.imageWidget.scroll -= canvas_limit
            self.imageWidget.scroll = max(0, self.imageWidget.scroll)
            info("Scrolling to %d canvas limit %d pixmap limit %d", self.imageWidget.scroll, canvas_limit, pixmap_limit)
            self.updateImage()

        else:
            self.gotoImage(-1)
            # A new image was loaded, recalculate scroll for the new dimensions
            canvas_limit, pixmap_limit = self.getCanvasPixmapLimits()
                
            self.imageWidget.scroll = max(0, pixmap_limit - canvas_limit)
            # XXX this is redundant with the call in gotoImage but that one 
            #     has the wrong scroll value, fix?
            self.updateImage()
 
    def nextImage(self):
        info("nextImage")

        # XXX This needs fixing when the first image in a list failed to load so
        #     there's no current image

        canvas_limit, pixmap_limit = self.getCanvasPixmapLimits()
            
        # Scroll one full canvas if possible, otherwise reset scroll and go to
        # the next image
        
        info("nextImage scroll %d canvasl %d pixmapl %d", self.imageWidget.scroll, canvas_limit, pixmap_limit )
        if (self.imageWidget.scroll + canvas_limit < pixmap_limit):
            self.imageWidget.scroll += canvas_limit
            self.imageWidget.scroll = min(self.imageWidget.scroll, pixmap_limit - canvas_limit)
            info("Scrolling to %d canvas limit %d pixmap limit %d", self.imageWidget.scroll, canvas_limit, pixmap_limit)
            self.updateImage()

            # Restart the slideshow timer since there are no calls to gotoImage
            # that will do it
            # XXX All the scrolling code should really be in gotoImage?
            if (self.slideshow_timer is not None):
                self.slideshow_timer.start(slideshow_interval_ms)

        else:
            self.imageWidget.scroll = 0
            self.gotoImage(1)

    def prevPage(self):
        # Don't do image scrolling and reset image scrolling it
        self.imageWidget.scroll = 0
        self.gotoImage(-self.thumbnails_per_page)

    def nextPage(self):
        # Don't do image scrolling and reset image scrolling it
        self.imageWidget.scroll = 0
        self.gotoImage(self.thumbnails_per_page)

    def prevRow(self):
        # Don't do image scrolling and reset image scrolling it
        self.imageWidget.scroll = 0
        self.gotoImage(-self.thumbnail_columns)

    def nextRow(self):
        # Don't do image scrolling and reset image scrolling it
        self.imageWidget.scroll = 0
        self.gotoImage(self.thumbnail_columns)

    def about(self):
        QMessageBox.about(self, "About Image Viewer",
                "<p>Simple no-frills <b>Image Viewer</b> optimized for high latency network drives</p>")

    def createActions(self):
        def createGlobalAction(title, triggered, shortcut, checkable=False, enabled=True):
            # Note shortcut parameter is compulsory since global actions need to
            # be able to be called when there's no menubar
            if (isinstance(shortcut, list)):
                shortcuts = shortcut
            else:
                shortcuts = [shortcut]

            action = QAction(title, self, triggered=triggered, enabled=enabled, checkable=checkable)
            # QAction constructor doesn't allow multiple shortcuts, use
            # setShortcuts after construction instead
            action.setShortcuts(shortcuts)
            
            # It's necessary to add the actions to the widget because in
            # fullscreen mode there's no menubar to route them
            # XXX a widget-agnostic option would be to use QSortcut?
            #     QShortcut(action.shortcut(), self, action.trigger)
            self.centralWidget().addAction(action)
            
            return action

        self.openAct = createGlobalAction("&Open...", shortcut="O", triggered=self.open)

        self.openFromClipboardAct = createGlobalAction("O&pen From Clipboard", shortcut="Ctrl+v",
            triggered=self.openFromClipboard)

        self.copyToClipboardAct = createGlobalAction("&Copy To Cli&pboard", enabled=False, 
            shortcut="Ctrl+c", triggered=self.copyToClipboard)

        self.exitAct = createGlobalAction("E&xit", shortcut="esc", triggered=self.close)

        self.recentFileActs = []
        for i in range(most_recently_used_max_count):
            self.recentFileActs.append(
                    QAction(self, visible=False,
                            triggered=self.openRecentFile, shortcut="%d" % i))

        # XXX Support fit window to width, fit window to image
        # XXX Support arbitrary scrolling
        # XXX Support arbitrary zooming
        # XXX Support renaming images
        # XXX Support multiple image selection
        # XXX Support image search/filtering by filename
        # XXX Support saving thumbnail page/s as big image
        # XXX Support saving thumbnails to a directory and fetching from there
        #     (or with _thumb extension/configurable, etc) instead of the big
        #     images
        
        self.toggleFitAct = createGlobalAction("&Fit To Smallest", enabled=False, 
            shortcut="F", triggered=lambda : self.toggleFit())

        self.deleteImageAct = createGlobalAction("Delete", enabled=False, 
            shortcut="del", triggered=self.deleteImage)

        self.refreshImageAct = createGlobalAction("Refresh", enabled=False, 
            shortcut="ctrl+r", triggered=self.refreshImage)
        self.refreshAllImagesAct = createGlobalAction("Refresh All", enabled=False, 
            shortcut="ctrl+shift+r", triggered=lambda : self.refreshImage(True))
        
        self.rotateRightAct = createGlobalAction("Rotate Ri&ght", enabled=False, 
            shortcut="R", triggered=lambda : self.rotateImage(90))
        self.rotateLeftAct = createGlobalAction("Rotate &Left", enabled=False, 
            shortcut="Shift+R", triggered=lambda : self.rotateImage(-90))

        self.gammaCorrectAct = createGlobalAction("&Gamma Correct", enabled=False, 
            checkable=True, shortcut="G", triggered=self.gammaCorrectionToggled)

        self.fullscreenAct = createGlobalAction("&Fullscreen", enabled=False,
            checkable=True, shortcut="return", triggered=self.fullscreenToggled)

        self.toggleThumbnailsAct = createGlobalAction("Toggle &Thumbnails/Image", enabled=False,
            checkable=False, shortcut=["T", "Shift+T"], triggered=self.thumbnailsToggled)
        
        self.nextBackgroundColorAct = createGlobalAction("Next &Background Color", 
            shortcut="B", triggered=lambda : self.cycleBackgroundColor(True))
        self.prevBackgroundColorAct = createGlobalAction("Previous Background Color",
            shortcut="Shift+B", triggered=lambda : self.cycleBackgroundColor(False))
        
        self.prevImageAct = createGlobalAction("&Previous Image", shortcut="left", 
            enabled=False, triggered=self.prevImage)
        self.nextImageAct = createGlobalAction("&Next Image", shortcut="right", 
            enabled=False, triggered=self.nextImage)

        self.prevRowAct = createGlobalAction("Previous Row", shortcut="up", 
            enabled=False, triggered=self.prevRow)
        self.nextRowAct = createGlobalAction("Next Row", shortcut="down", 
            enabled=False, triggered=self.nextRow)

        self.prevPageAct = createGlobalAction("Previous Page", shortcut=["page up", "ctrl+up"], 
            enabled=False, triggered=self.prevPage)
        self.nextPageAct = createGlobalAction("Next Page", shortcut=["page down", "ctrl+down"], 
            enabled=False, triggered=self.nextPage)

        self.firstImageAct = createGlobalAction("Fi&rst Image", shortcut=["home", "ctrl+left"], 
            enabled=False, triggered=self.firstImage)
        self.lastImageAct = createGlobalAction("&Last Image", shortcut=["end", "ctrl+right"], 
            enabled=False, triggered=self.lastImage)
        
        self.slideshowAct = createGlobalAction("Toggle Slidesho&w", shortcut="space", 
            checkable=True, enabled=False, triggered=self.slideshowToggled)

        self.animationAct = createGlobalAction("Toggle &Animation", shortcut="A", 
            checkable=True, enabled=False, triggered=self.animationToggled)
        self.animationAct.setChecked(True)
        
        self.aboutAct = QAction("&About", self, triggered=self.about)

        self.aboutQtAct = QAction("About &Qt", self,
            triggered=QApplication.instance().aboutQt)

    def createMenus(self):
        
        self.fileMenu = QMenu("&File", self)
        self.fileMenu.addAction(self.openAct)
        self.fileMenu.addAction(self.openFromClipboardAct)
        self.fileMenu.addAction(self.copyToClipboardAct)
        self.fileMenu.addSeparator()
        self.fileMenu.addAction(self.exitAct)
        self.fileMenu.addSeparator()
        for action in self.recentFileActs:
            self.fileMenu.addAction(action)

        self.viewMenu = QMenu("&View", self)
        self.viewMenu.addAction(self.toggleFitAct)
        self.viewMenu.addSeparator()
        self.viewMenu.addAction(self.refreshImageAct)
        self.viewMenu.addAction(self.refreshAllImagesAct)
        self.viewMenu.addAction(self.deleteImageAct)
        self.viewMenu.addSeparator()
        self.viewMenu.addAction(self.rotateLeftAct)
        self.viewMenu.addAction(self.rotateRightAct)
        self.viewMenu.addSeparator()
        self.viewMenu.addAction(self.gammaCorrectAct)
        self.viewMenu.addSeparator()
        self.viewMenu.addAction(self.prevBackgroundColorAct)
        self.viewMenu.addAction(self.nextBackgroundColorAct)
        self.viewMenu.addSeparator()
        self.viewMenu.addAction(self.fullscreenAct)
        self.viewMenu.addSeparator()
        self.viewMenu.addAction(self.toggleThumbnailsAct)
        self.viewMenu.addSeparator()
        self.viewMenu.addAction(self.firstImageAct)
        self.viewMenu.addAction(self.lastImageAct)
        self.viewMenu.addAction(self.prevImageAct)
        self.viewMenu.addAction(self.nextImageAct)
        self.viewMenu.addAction(self.prevPageAct)
        self.viewMenu.addAction(self.nextPageAct)
        self.viewMenu.addAction(self.prevRowAct)
        self.viewMenu.addAction(self.nextRowAct)
        self.viewMenu.addSeparator()
        self.viewMenu.addAction(self.slideshowAct)
        self.viewMenu.addSeparator()
        self.viewMenu.addAction(self.animationAct)


        self.helpMenu = QMenu("&Help", self)
        self.helpMenu.addAction(self.aboutAct)
        self.helpMenu.addAction(self.aboutQtAct)

        self.menuBar().addMenu(self.fileMenu)
        self.menuBar().addMenu(self.viewMenu)
        self.menuBar().addMenu(self.helpMenu)

    def createStatus(self):
        frame_style = QFrame.WinPanel | QFrame.Sunken

        # Can't set sunken style on QStatusBar.showMessage, use a widget and
        # reimplement showMessage and clearMessage
        timer = QTimer()
        timer.setSingleShot(True)
        timer.timeout.connect(self.clearMessage)
        self.status_message_timer = timer

        self.status_message_widget = QLabel()
        self.status_message_widget.setFrameStyle(frame_style)
        self.statusBar().addWidget(self.status_message_widget, 1)

        self.statusFilepath = QLabel()
        self.statusFilepath.setFrameStyle(frame_style)
        self.statusBar().addPermanentWidget(self.statusFilepath)
        self.statusResolution = QLabel()
        self.statusResolution.setFrameStyle(frame_style)
        self.statusBar().addPermanentWidget(self.statusResolution)
        self.statusIndex = QLabel()
        self.statusIndex.setFrameStyle(frame_style)
        self.statusBar().addPermanentWidget(self.statusIndex)
        self.statusZoom = QLabel()
        self.statusZoom.setFrameStyle(frame_style)
        self.statusBar().addPermanentWidget(self.statusZoom)
        self.statusSize = QLabel()
        self.statusSize.setFrameStyle(frame_style)
        self.statusBar().addPermanentWidget(self.statusSize)
        self.statusDate = QLabel()
        self.statusDate.setFrameStyle(frame_style)
        self.statusBar().addPermanentWidget(self.statusDate)
        
    def updateActions(self):
        self.copyToClipboardAct.setEnabled(True)
        self.refreshImageAct.setEnabled(True)
        self.deleteImageAct.setEnabled(True)
        self.refreshAllImagesAct.setEnabled(True)
        self.toggleFitAct.setEnabled(True)
        self.rotateLeftAct.setEnabled(True)
        self.rotateRightAct.setEnabled(True)
        self.gammaCorrectAct.setEnabled(True)
        self.firstImageAct.setEnabled(True)
        self.lastImageAct.setEnabled(True)
        self.prevImageAct.setEnabled(True)
        self.nextImageAct.setEnabled(True)
        self.prevPageAct.setEnabled(True)
        self.nextPageAct.setEnabled(True)
        self.prevRowAct.setEnabled(True)
        self.nextRowAct.setEnabled(True)
        self.slideshowAct.setEnabled(True)
        self.animationAct.setEnabled(self.animation_count > 1)
        self.fullscreenAct.setEnabled(True)
        self.toggleThumbnailsAct.setEnabled(True)
        self.nextBackgroundColorAct.setEnabled(True)
        self.prevBackgroundColorAct.setEnabled(True)
        

    def wheelEvent(self, event):
        notches = event.angleDelta().y() / 120
        # Use prev/nextImage so image scrolling works, for larger amounts don't
        # do image scrolling and reset it
        # XXX Should the scroll be done at gotoImage level?
        if (notches == -1):
            self.nextImage()

        elif (notches == 1):
            self.prevImage()

        else:
            self.imageWidget.scroll = 0
            self.gotoImage(-notches)

    def resizeEvent(self, event):
        info("resizeEvent %s", event.size())
        # This changes the zoom level, update
        if (self.imageWidget.originalPixmap is not None):
            self.updateStatus()
        
        return super(ImageViewer, self).resizeEvent(event)

def verify_pyqt5_installation():
    # Anaconda 2.3.0 puts DLLs in pkgs/<module_version>/Library/bin folders but
    # forgets to add them to the path and cannot later be found, include those
    # in the path. Anaconda 2.2.0 doesn't have this issue
    add_anaconda_dlls_to_path = False
    if (add_anaconda_dlls_to_path):
        pkgs_dir = R"c:\Anaconda\pkgs"
        for pkg_dir in os.listdir(pkgs_dir):
            pkg_filepath = os.path.join(pkgs_dir, pkg_dir)
            library_bin_path = os.path.join(pkg_filepath, "Library", "bin")
            if (os.path.exists(library_bin_path )) and not pkg_dir.startswith("sqlite"):
                info("adding DLL library path %s", library_bin_path)
                os.environ["PATH"] = library_bin_path + ";" + os.environ["PATH"]

    # Anaconda 2.2.0 and 2.3.0 fail to set QT_PLUGIN_PATH giving the error
    # "couldn't find or load qt platform plugin "windows"
    # See https://github.com/ContinuumIO/anaconda-issues/issues/1270
    # See https://github.com/pyqt/python-qt5/issues/2
    # See https://github.com/ContinuumIO/anaconda-issues/issues/1270
    # See https://github.com/pyqt/python-qt5/wiki/Qt-Environment-Variable-Reference#qt-plugin-path
    # See https://github.com/pyqt/python-qt5/blob/master/qt.conf
    # See https://stackoverflow.com/questions/51286721/changing-qt-plugin-path-in-environment-variables-causes-programs-to-fail
    # On 64-bit python-qt5 pip installs, this is properly set to
    #   C:\Python27\lib\site-packages\PyQt5\plugins 
    # when C:\Python27\Lib\site-packages\PyQt5\__init__.py runs since this commit
    # https://github.com/pyqt/python-qt5/blob/06ce5b1d1909929130ee0cc8b53e0199d92cbcfd/PyQt5/__init__.py
    # until this commit that updates to Qt 5.4
    # https://github.com/pyqt/python-qt5/blob/93b127adc95e681ea87abd9ab5e66a0e299fce19/PyQt5/__init__.py
    # which moves qt.conf generation to setup.py
    # It also ships a proper C:\Python27\Lib\site-packages\PyQt5\qt.conf
    # which contains the entries
    #   Prefix = C:/Python27/Lib/site-packages/PyQt5
    #   Binaries = C:/Python27/Lib/site-packages/PyQt5
    # (a specific entry Plugins is also allowed, default is "plugins", see
    # https://doc.qt.io/qt-6/qt-conf.html)
    # But Anaconda 2.2.0 only has Qt4 qt.conf around 
    #   C:\Anaconda\Lib\site-packages\PyQt4\qt.conf
    #   C:\Anaconda\qt.conf
    # With the entries
    #   [Paths]
    #   Prefix = ./Lib/site-packages/PyQt4
    #   Binaries = ./Lib/site-packages/PyQt4
    # And C:\Anaconda\pkgs\pyqt-5.6.0-py27_2\Lib\site-packages\PyQt5\__init__.py
    # is empty.
    # In addition, a python-qt5 anaconda installation doesn't have neither DLLs in 
    # path nor a plugin subdir but in C:\Anaconda\pkgs\qt-5.6.2-vc9_6\Library
    # in that path, instead that one is on 
    # C:\Anaconda\pkgs\qt-5.6.2-vc9_6\Library\bin\Qt5Gui.dll
    # XXX This path will probably change with anaconda qt updates, not clear the
    #     best way of getting this, probably move to a conda batch file?
    # XXX This needs to be set before any Qt usage, but can be set after the imports
    
    # Anaconda 2.2.0 and 2.3.0 (the last versions that are known to work on
    # 32-bit Windows XP) fail to install Qt properly: don't set QT_PLUGIN_PATH
    # nor provide a qt.conf file. 
    #
    # Those Anacondas require QT_PLUGIN_PATH to be set manually before running
    # the app. Note this is an Anaconda-specific problem, other environments
    # either set QT_PLUGIN_PATH (eg 64-bit Windows 10 PyQt5 5.3.2 installed from
    # pip) or provide qt.conf (eg Linux PyQt 5.11.3 installed from pip) or both.
    needs_qt_plugin_path = (" 32 bit " in sys.version) and  ("|Continuum Analytics, Inc.|" in sys.version)
    if (needs_qt_plugin_path and ("QT_PLUGIN_PATH" not in os.environ)):
        # XXX Note that QT_PLUGIN_PATH set is not necessary for PyQt5 to work,
        #     eg Linux PyQt 5.11.3 doesn't set it but it works (and setting one
        #     gets ignored when the first QApplication is created)
        #os.environ["QT_PLUGIN_PATH"] = R"C:\Anaconda\pkgs\qt-5.6.2-vc9_6\Library\plugins"
        raise Exception("QT_PLUGIN_PATH not set but conda Python found \"%s\"\n"
            "Qt applications will fail with \"couldn't find or load qt platform plugin \"windows\"\"\n"
            "Set QT_PLUGIN_PATH to point to Qt plugins before running the application, eg\n"
            "SET QT_PLUGIN_PATH C:\\Anaconda\\pkgs\\qt-5.6.2-vc9_6\\Library\\plugins\n" % sys.version)


def report_versions():
    info("Python version: %s", sys.version)

    # Numpy is only needed to apply gamma correction
    np_version = "Not installed"
    try:
        import numpy as np
        np_version = np.__version__
        
    except:
        warn("numpy not installed, image filters disabled")
    info("Numpy version: %s", np_version)
    

    info("Qt version: %s", QT_VERSION_STR)
    info("PyQt version: %s", PYQT_VERSION_STR)

    pyqt5_sqlite_version = "Not installed"
    pyqt5_sqlite_compile_options = []
    try:
        from PyQt5.QtSql import QSqlDatabase
        db = QSqlDatabase.addDatabase("QSQLITE")
        db.open()
        query = db.exec_("SELECT sqlite_version();")
        query.first()
        pyqt5_sqlite_version = query.value(0)

        query = db.exec_("PRAGMA compile_options;")
        while (query.next()):
            pyqt5_sqlite_compile_options.append(query.value(0))
        db.close()
    
    except:
        # On Linux QtSql import is known to fail when python-pyqt5.qtsql is not
        # installed, needs 
        #   apt install python-pyqt5.qtsql 
        pass
        
    info("QSQLITE version: %s", pyqt5_sqlite_version)
    info("QSQLITE compile options: %s", pyqt5_sqlite_compile_options)
    info("Qt plugin path: %s", os.environ.get("QT_PLUGIN_PATH", "Not set"))
    info("QCoreApplication.libraryPaths: %s", QCoreApplication.libraryPaths())
    info("QLibraryInfo.PrefixPath: %s", QLibraryInfo.location(QLibraryInfo.PrefixPath))
    info("QLibraryInfo.PluginsPath: %s", QLibraryInfo.location(QLibraryInfo.PluginsPath))
    info("QLibraryInfo.LibrariesPath: %s", QLibraryInfo.location(QLibraryInfo.LibrariesPath))
    info("QLibraryInfo.LibrarieExecutablesPath: %s", QLibraryInfo.location(QLibraryInfo.LibraryExecutablesPath))
    info("QLibraryInfo.BinariesPath: %s", QLibraryInfo.location(QLibraryInfo.BinariesPath))


logger = logging.getLogger(__name__)
setup_logger(logger)
#logger.setLevel(logging.WARNING)
logger.setLevel(logging.INFO)

if (__name__ == '__main__'):
    report_versions()
    
    verify_pyqt5_installation()
    
    app = QApplication(sys.argv)
    imageViewer = ImageViewer()
    imageViewer.show()
    sys.exit(app.exec_())
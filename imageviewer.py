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


XXX Missing code cleanup (camelcasing vs snake, proper log levels, refactoring)
XXX Missing file/attribute reading error support
XXX Missing bug fixing, esp when a file fails to load
XXX Missing command line options (debuglevel, openfromclipboard, etc)
XXX Read/store settings with QSettings (Window position, MRU, zoom, fit mode, 
    slideshow timer interval, see saveGeometry, restoreGeometry)
XXX Use numpy for image effects (gamma, auto-gamma, brightness, contrast, etc)
    See https://note.nkmk.me/en/python-numpy-image-processing/
"""

import datetime
import logging
import os
import Queue
import sys
import thread

from PyQt5.QtCore import *
from PyQt5.QtGui import *
from PyQt5.QtWidgets import *
from PyQt5.QtPrintSupport import *

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
    logging_format = "%(asctime).23s %(levelname)s:%(filename)s(%(lineno)d): %(message)s"

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


# XXX Support animations via QMovie of a local temp file or QImageReader of
#     QBuffer/QIODevice of a python buffer in the file cache, to avoid PyQt
#     locking the UI thread
#     Also see http://blog.ssokolow.com/archives/2019/08/14/displaying-an-image-or-animated-gif-in-qt-with-aspect-ratio-preserving-scaling/

# XXX Check QImageReader for supportedFormats
# XXX Check tie Image I/O plugins for tga, tiff webp
image_extensions = [".bmp", ".jfif", ".jpg", ".jpeg", ".png", ".pbm", ".pgm", ".ppm", ".xbm", ".xpm", ".tga", ".tiff"]
supported_extensions = image_extensions + [".lst"]
first_image = -float("inf")
last_image = float("inf")
slideshow_interval_ms = 5000
most_recently_used_max_count = 10

def prefetch_files(queue_in, queue_out):
    while (True):
        filepath = queue_in.get()
        if (filepath is None):
            break

        info("worker prefetching %s", repr(filepath))
        # Store file contents, don't convert to QImage yet:
        # - Converting to QImage here blocks the GUI thread, which makes
        #   prefetching useless
        # - Converting the data to QImage explodes cache memory requirements
        # - Doing QImage conversion on the GUI thread is not that taxing and
        #   doesn't stall for long
        try:
            with open(filepath, "rb") as f:
                data = f.read()
        except:
            # If there was an error, let the caller handle it by placing None
            # data
            exc("Unable to prefetch %s", repr(filepath))
            data = None
        info("worker prefetched %s", repr(filepath))
        queue_out.put((filepath, data))


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


class FileDialog(QDialog):
    """
    Qt file dialog is extremely slow on Raspberry Pi (minutes to pop), implement
    a simple one that just works
    """
    # XXX Do substring search on key input. Qt already does prefix search, but
    #     unfortunately it uses the default MatchStartsWith when
    #     qabstractitemview.keyboardsearch calls model.match and qlistwidget
    #     setModel cannot be called 
    #
    #     See https://code.woboq.org/qt5/qtbase/src/widgets/itemviews/qabstractitemview.cpp.html
    #     See https://code.woboq.org/qt5/qtbase/src/corelib/itemmodels/qabstractitemmodel.h.html
    #     See https://code.woboq.org/qt5/qtbase/src/widgets/itemviews/qlistwidget.cpp.html#_ZN11QListWidget8setModelEP18QAbstractItemModel

    # XXX Do directory navigation on left, right
    # XXX Think about what initialization should be elsewhere if the dialog is
    #     kept around and reused
    
    def __init__(self, filepath, parent=None):
        super(FileDialog, self).__init__(parent)

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
        self.listWidget = listWidget
        
    
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

        self.updateDirpath(dirpath)
        self.listWidget.setFocus()
        self.listWidget.setCurrentItem(self.listWidget.findItems(basename, Qt.MatchExactly)[0])
        self.resize(500, 400)


    def updateDirpath(self, dirpath):
        dirpath = unicode(dirpath)
        dirpath = os.path.abspath(os.path.expanduser(dirpath))

        # listdir and isdir can take time on network drives
        QApplication.setOverrideCursor(Qt.WaitCursor)

        info("listdir")
        names = os.listdir(dirpath)
        info("listdired %d files", len(names))

        filenames = []
        dirnames = []
        
        for i, name in enumerate(names):
            path = os.path.join(dirpath, name)
            _, ext = os.path.splitext(name)
            # XXX isdir for each file takes a long time on network drives with 
            #     lots of images, thread and or defer it?
            is_dir = False
            if (False):
                print "isdir", name, i, len(names)
                is_dir = os.path.isdir(path)
                print "isdir done"
            if (is_dir):
                dirnames.append(name)

            elif ((ext.lower() in supported_extensions) or True):
                filenames.append(name)
        
        # XXX Allow sorting by date (getting the date will be slow, use some
        #     latency hiding)
        dirnames.sort(cmp=cmp_numerically)
        filenames.sort(cmp=cmp_numerically)

        self.listWidget.clear()
        if (os.path.dirname(dirpath) != dirpath):
            self.listWidget.addItem("..")
        self.listWidget.addItems(dirnames)
        # Make items for dirs bold
        if (self.listWidget.count() > 0):
            bold_font = self.listWidget.item(0).font()
            bold_font.setBold(True)
            for i in xrange(self.listWidget.count()):
                item = self.listWidget.item(i)
                item.setFont(bold_font)
        self.listWidget.addItems(filenames)
        
        if (self.listWidget.count() > 0):
            self.listWidget.setCurrentItem(self.listWidget.item(0))

        self.total.setText("%d files and dirs" % self.listWidget.count())

        while (self.pathLayout.count() > 0): 
            item = self.pathLayout.takeAt(0)
            # eg stretches are not widgets
            if (item.widget() is not None):
                item.widget().setParent(None)
            
        dirname = dirpath
        names = []
        while True:
            dirname, basename = os.path.split(dirname)
            if (basename == ""):
                names.append(dirname)
                break
            names.append(basename)

        names.reverse()
        info("dir names are %s", names)

        for i, name in enumerate(names):
            # XXX There's a bug in LXDE where the root button switches between /
            #     and // as buttons are pressed
            button = QToolButton()
            button.setText(name)
            button_path = str.join(os.path.sep, names[:i+1])
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

    def navigate(self, path = None):
        if (path is None):
            path = self.edit.text()
        info("navigate %s", repr(path))
        if (not os.path.isabs(path)):
            path = os.path.expanduser(path)
            path = os.path.join(self.dirpath, path)
        
        # Make absolute and normalize ..
        path = os.path.abspath(path)

        if (os.path.isdir(path)):
            # Update with the directory contents, focus the list on the children
            # coming from, if any
            dirname = ""
            if (self.dirpath.startswith(path)):
                dirname = self.dirpath[len(path)+1:].split(os.path.sep)    
                dirname = dirname[0]
                
            info("path %s dirpath %s dirname %s ", repr(path), repr(self.dirpath), repr(dirname))

            self.updateDirpath(path)

            if (dirname != ""):
                
                items = self.listWidget.findItems(dirname, Qt.MatchExactly)
                if (len(items) == 1):
                    self.listWidget.setCurrentItem(items[0])

                else:
                    # XXX This is hit on LXDE when accessing the root path /
                    error("Expected one item for %s, found %d %s", repr(dirname), len(items), [item.text() for item in items])

        else:
            # XXX This should probably be a function rather than having the
            #     caller access chosenFilepath directly
            # XXX Dialog could also return list of files and the current dir
            self.chosenFilepath = path
            self.accept()

    def entryDoubleClicked(self, item):
        self.navigate()


class ImageWidget(QLabel):
    # See https://stackoverflow.com/questions/30553467/resizable-pyqt-widget-displaying-an-image-with-fixed-aspect-ratio
    # XXX Have a message capability for when in fullscreen
    def __init__(self, parent=None):
        super(ImageWidget, self).__init__(parent)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.setMinimumSize(1, 1)
        pal = self.palette()
        pal.setColor(QPalette.Background, Qt.black)
        self.setAutoFillBackground(True)
        self.setPalette(pal)
        
        self.originalPixmap = None
        self.text = None
        self.rotation_degrees = 0
        self.gamma = 1.0
        self.fitToSmallest = False
        self.scroll = 0

    def setPixmap(self, pixmap):
        self.originalPixmap = pixmap
        self.scroll = 0
        self.resizePixmap(self.size())

    def setText(self, text):
        self.text = text

    def toggleFit(self):
        self.fitToSmallest = not self.fitToSmallest
        self.scroll = 0
        self.resizePixmap(self.size())

    def rotatePixmap(self, degrees):
        info("rotatePixmap from %d to %d", self.rotation_degrees, degrees)
        self.rotation_degrees = degrees
        self.scroll = 0
        self.resizePixmap(self.size())

    def gammaCorrectPixmap(self, gamma):
        info("gammaCorrectPixmap from %d to %d", self.gamma, gamma)
        self.gamma = gamma
        self.resizePixmap(self.size())


    def resizePixmap(self, size):
        info("resizing pixmap from %s to %s", self.originalPixmap.size(), size)
        pixmap = self.originalPixmap

        if (self.gamma != 1.0):
            # XXX This is not very efficient, conversions from pixmap to image
            #     and back and done every time and at the original image size,
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
            painter.drawText(QPoint(10, 18), self.text)
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

class VLine(QFrame):
    # a simple VLine, like the one you get from designer
    # See https://stackoverflow.com/questions/57943862/pyqt5-statusbar-separators
    def __init__(self):
        super(VLine, self).__init__()
        self.setFrameShape(self.VLine|self.Sunken)
    
class ImageViewer(QMainWindow):
    def __init__(self):
        super(ImageViewer, self).__init__()

        self.slideshow_timer = None

        self.recent_filepaths = []

        # Use the scripts directory as FileDialog opening dir
        self.image_filepath = sys.argv[0]

        self.cached_files = []
        # XXX This could have a max_size_bytes instead
        self.cached_files_max_count = 20

        self.prefetch_queue_in = Queue.Queue()
        self.prefetch_queue_out = Queue.Queue()
        # XXX Check any relationship between prefetch and cache counts, looks
        #     like there shouldn't be any even if the current is evicted from
        #     lur since the current is also kept separately? (although prefetch
        #     > cache is probably silly)
        self.prefetched_images_max_count = 10
        self.prefetch_pending = set()
        self.prefetcher_count = (self.prefetched_images_max_count / 2) + 1

        for i in xrange(self.prefetcher_count):
            thread.start_new_thread(prefetch_files, (self.prefetch_queue_in, self.prefetch_queue_out))

        
        
        w = QWidget(self)
        self.setCentralWidget(w)
        l = QVBoxLayout()
        w.setLayout(l)
                
        
        imageWidget = ImageWidget()
        imageWidget.setMouseTracking(True)
        imageWidget.installEventFilter(self)
        self.imageWidget = imageWidget
        
        l.addWidget(self.imageWidget)
        l.setContentsMargins(0, 0, 0, 0)
        l.setSpacing(0)

        self.createActions()
        self.createMenus()
        self.createStatus()

        # It's necessary to add the actions to the widget because in fullscreen
        # mode there's no menubar to handle them
        self.imageWidget.addAction(self.openAct)
        self.imageWidget.addAction(self.openFromClipboardAct)
        self.imageWidget.addAction(self.copyToClipboardAct)
        self.imageWidget.addAction(self.toggleFitAct)
        self.imageWidget.addAction(self.rotateLeftAct)
        self.imageWidget.addAction(self.rotateRightAct)
        self.imageWidget.addAction(self.gammaCorrectAct)
        self.imageWidget.addAction(self.fullscreenAct)
        self.imageWidget.addAction(self.firstImageAct)
        self.imageWidget.addAction(self.lastImageAct)
        self.imageWidget.addAction(self.prevImageAct)
        self.imageWidget.addAction(self.nextImageAct)
        self.imageWidget.addAction(self.slideshowAct)
        self.imageWidget.addAction(self.exitAct)
    
        
        self.setWindowTitle("Image Viewer")
        if (len(sys.argv) > 1):
            filepath = unicode(sys.argv[1])
            self.loadImage(filepath)
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

        dirpath = os.path.curdir if self.image_filepath is None else self.image_filepath
        if (False):
            filepath, _ = QFileDialog.getOpenFileName(self, "Open File", dirpath)

            if (filepath == ""):
                filepath = None

        else:
            dlg = FileDialog(dirpath, self)
            if (dlg.exec_() == QDialog.Accepted):
                filepath = dlg.chosenFilepath

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
        info("setRecentFile %s", repr(filepath))

        # XXX This needs to remove duplicates?
        self.recent_filepaths.insert(0, os.path.abspath(filepath))
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
        
        info("openFromClipboard %s %s", repr(filepath), repr(filepaths))
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
        info("copyToClipboard %s", repr(self.image_filepath))
        clipboard = qApp.clipboard()
        
        clipboard.setText(self.image_filepath)
        # XXX Ideally would like to put both filepath and pixmap into the
        #     clipboard but setText and setPixmap overwrite each other's
        #     contents, is there a way of copying multiple MIME types to the
        #     clipboard?
        #     clipboard.setPixmap(self.imageWidget.originalPixmap)
        

    def getDataFromCache(self, filepath):
        assert isinstance(filepath, unicode) 
        # XXX Qt already has QPixmapCache, look into it?

        # Get the file from the cache and bring it to the front if in the cache,
        # request it and put it in the front otherwise

        for i, entry in enumerate(self.cached_files):
            entry_filepath, entry_data = entry
            ## print "cache entry", repr(entry_filepath), "vs", repr(filepath)
            if (filepath == entry_filepath):
                info("cache hit for %s", repr(filepath))
                self.cached_files.pop(i)
                # Put it at the beginning of the LRU cache
                self.cached_files.insert(0, entry)
                break

        else:

            info("cache miss for %s", repr(filepath))
            
            # The filepath is not in the cache, request if not already pending
            if (filepath not in self.prefetch_pending):
                info("prefetch pending miss for %s", repr(filepath))
                info("ordering prefetch for %s", repr(filepath))
                self.prefetch_queue_in.put(filepath)
                self.prefetch_pending.add(filepath)

            else:
                info("prefetch pending hit for %s", repr(filepath))
                
            # Drain the requests as they are satisfied until the requested one
            # is found. Note this won't be in order if there are more than one
            # prefetcher threads
            while (True):
                entry_filepath, entry_data = self.prefetch_queue_out.get()
                
                self.prefetch_pending.remove(entry_filepath)

                if (entry_data is not None):
                    if (len(self.cached_files) >= self.cached_files_max_count):
                        info("evicting %s for %s", repr(self.cached_files[-1][0]), repr(filepath))
                        self.cached_files.pop(-1)
                    info("inserting in cache %s", repr(entry_filepath))
                    self.cached_files.insert(0, (entry_filepath, entry_data))

                if (entry_filepath == filepath):
                    break

        return entry_data
            
    def loadImage(self, filepath, index = None, count = None):
        info("loadImage %s %s %s", repr(filepath), index, count)
        assert isinstance(filepath, unicode) 
        if (filepath.lower().endswith(".lst")):
            filepaths = []
            try:
                with open(filepath, "r") as f:
                    filepaths = f.readlines()

            except:
                exc("Unable to read %s", filepath)
            
            if (len(filepaths) == 0):
                QMessageBox.information(self, "Image Viewer",
                    "Cannot load %s." % filepath)
                return
            
            filepaths = [unicode(filepath.strip()) for filepath in filepaths]
            for i, filepath in enumerate(filepaths):
                if (not os.path.isabs(filepath)):
                    filepaths[i] = os.path.join(os.path.dirname(filepath), filepath)
                
            filepath = filepaths[0]
            self.image_filepaths = filepaths
            self.image_index = 0
            index = 0
            count = len(filepaths)

        else:
            # If there's no index and count information, reset filenames cache
            if (index is None):
                self.image_filepaths = None
            # Update the image index early so it's skipped if it cannot be
            # loaded
            self.image_index = index

        # Update these early in case the image fails to load other filepaths can
        # still be cycled
        if ((self.image_filepaths is not None) and (len(self.image_filepaths) > 1)):
            self.firstImageAct.setEnabled(True)
            self.lastImageAct.setEnabled(True)
            self.prevImageAct.setEnabled(True)
            self.nextImageAct.setEnabled(True)
            self.slideshowAct.setEnabled(True)

        info("QImaging %s", repr(filepath))
        self.statusBar().showMessage("Loading...")
        QApplication.setOverrideCursor(Qt.WaitCursor)
        data = self.getDataFromCache(filepath)
        QApplication.restoreOverrideCursor()
        if (data is None):
            # XXX This should skip the image in case it cannot be loaded
            QMessageBox.information(self, "Image Viewer",
                    "Cannot load %s." % filepath)
            return

        self.statusBar().showMessage("Converting...")
        QApplication.setOverrideCursor(Qt.WaitCursor)
        image = QImage.fromData(data)
        QApplication.restoreOverrideCursor()
        self.statusBar().clearMessage()
        info("QImaged %s", repr(filepath)) 
        
        if (image.isNull()):
            # XXX This should skip the image in case it cannot be loaded
            QMessageBox.information(self, "Image Viewer",
                    "Invalid image file %s." % filepath)
            return

        self.image_filepath = filepath
        
        self.setWindowTitle("Image Viewer - %s%s" % (
            os.path.basename(filepath), 
            "" if index is None else " [%d / %d]" % (index + 1, count)
        ))
        # XXX Do this only if fullscreen? (but it needs to be updated at
        #     fullscreen toggle too, which requires proper storing of index and
        #     count, and general file list cleanup)
        self.imageWidget.setText("%s%s" % (filepath, "" if index is None else " [%d / %d]" % (index + 1, count) ))
        # XXX Is this image to pixmap to setpixmap redundant? should we use image?
        #     or pixmap?
        pixmap = QPixmap.fromImage(image)
        self.imageWidget.setPixmap(pixmap)

        info("Statusing")
        self.statusFilepath.setText(filepath)
        self.statusResolution.setText("%d x %d x %d BPP" % (image.size().width(), image.size().height(), image.depth()))
        self.statusSize.setText("%s / %s (%s)" % (
            size_to_human_friendly_units(len(data)), 
            size_to_human_friendly_units(image.byteCount()),
            size_to_human_friendly_units(
                sum([len(entry_data) for entry_filepath, entry_data in self.cached_files]) + 
                # XXX These getsize cause a noticeable stall
                # sum([os.path.getsize(entry_filepath) for entry_filepath in self.prefetch_pending])
                0
                )
        ))
        self.statusDate.setText("%s" % datetime.datetime.fromtimestamp(os.path.getmtime(filepath)).strftime("%Y-%m-%d %H:%M:%S"))
        self.statusIndex.setText("%d / %d" % (1 if index is None else index + 1, 1 if count is None else count))
        info("Statused")

        self.updateStatus()
        self.updateActions()

    def updateStatus(self):
        info("updateStatus")

        widget_size = self.imageWidget.size()
        orig_pixmap_size = self.imageWidget.originalPixmap.size()
        pixmap_size = self.imageWidget.pixmap().size()

        info("pixmap size %s widget_size %s", widget_size, pixmap_size)
        if (widget_size.width() != self.imageWidget.pixmap().width()):
            zoom_factor = (pixmap_size.width() * 100) / orig_pixmap_size.width()
        else:
            zoom_factor = (pixmap_size.height() * 100) / orig_pixmap_size.height()
        
        self.statusZoom.setText("%d%% %s %s %d deg" % (
            zoom_factor,
            "S" if self.imageWidget.fitToSmallest else "L",
            "%2.1fg" % self.imageWidget.gamma,
            self.imageWidget.rotation_degrees            
        ))


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
        if (self.gammaCorrectAct.isChecked()):
            self.imageWidget.gammaCorrectPixmap(2.2)
        else:
            self.imageWidget.gammaCorrectPixmap(1.0)

        self.updateStatus()

    def fullscreenToggled(self):
        #self.setWindowFlags(self.windowFlags() ^ Qt.FramelessWindowHint)
        # Needs showing after changing flags
        #self.show()

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
        info("gotoImage %s", delta)
        if (self.image_filepaths is None):
            image_dirname = os.path.dirname(self.image_filepath)
            info("listing %s", repr(image_dirname))
            QApplication.setOverrideCursor(Qt.WaitCursor)        
            filenames = os.listdir(image_dirname)
            QApplication.restoreOverrideCursor()
            info("listed %s", repr(image_dirname))
            
            # XXX Right now this ignores .lst files because it would replace 
            #     image_filepaths, fix?
            filenames = filter(lambda s: any([s.lower().endswith(ext) for ext in image_extensions]), filenames)
            # XXX Allow sorting by date (getting the date will be slow, will
            #     need latency hiding)
            filenames.sort(cmp=cmp_numerically)

            filepaths = [os.path.join(image_dirname, filename) for filename in filenames]
            
            self.image_filepaths = filepaths

        else:
            filepaths = self.image_filepaths
        
        filepath = None
        prev_i = filepaths.index(self.image_filepath)
        if (delta == first_image):
            i = 0

        elif (delta == last_image):
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
            
            # Start with the current index, then leapfrog between the next
            # forward and backward prefetching (but note that images will be
            # returned out of order anyway if there are multiple prefetch
            # threads)
            delta = 1
            for _ in xrange(self.prefetched_images_max_count):
                filepath = filepaths[(i + delta + len(filepaths)) % len(filepaths)]
                if ((filepath not in self.prefetch_pending) and 
                    # XXX Have a set for cached images instead of a an all() reduce
                    all([entry_filepath != filepath for entry_filepath, entry_data in self.cached_files])):
                    info("ordering prefetch for %s", repr(filepath))
                    self.prefetch_queue_in.put(filepath)
                    self.prefetch_pending.add(filepath)
                
                delta = -delta
                if (delta > 0):
                    delta += 1

        if (self.slideshow_timer is not None):
            self.slideshow_timer.start(slideshow_interval_ms)

    def toggleFit(self):
        self.imageWidget.toggleFit()
        self.scroll = 0
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
        self.gotoImage(first_image)

    def lastImage(self):
        self.gotoImage(last_image)


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
            self.imageWidget.resizePixmap(self.imageWidget.size())

        else:
            self.gotoImage(-1)
            # A new image was loaded, recalculate scroll for the new dimensions
            canvas_limit, pixmap_limit = self.getCanvasPixmapLimits()
                
            self.imageWidget.scroll = max(0, pixmap_limit - canvas_limit)
            # XXX this is redundant with the call in gotoImage but that one 
            #     has the wrong scroll value, fix?
            self.imageWidget.resizePixmap(self.imageWidget.size())

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
            self.imageWidget.resizePixmap(self.imageWidget.size())

            # Restart the slideshow timer since there are no calls to gotoImage
            # that will do it
            # XXX All the scrolling code should really be in gotoImage?
            if (self.slideshow_timer is not None):
                self.slideshow_timer.start(slideshow_interval_ms)

        else:
            # XXX Right now gotoImage will call loadImage which will reset
            #     scroll, no need to do here until loadImage is fixed to not
            #     reset it?
            self.imageWidget.scroll = 0
            self.gotoImage(1)

    def about(self):
        QMessageBox.about(self, "About Image Viewer",
                "<p>Simple no-frills <b>Image Viewer</b> optimized for high latency network drives</p>")

    def createActions(self):
        self.openAct = QAction("&Open...", self, shortcut="O",
            triggered=self.open)

        self.openFromClipboardAct = QAction("O&pen From Clipboard", self, shortcut="Ctrl+V",
            triggered=self.openFromClipboard)

        self.copyToClipboardAct = QAction("&Copy To Cli&pboard", self, enabled=False, 
            shortcut="Ctrl+C", triggered=self.copyToClipboard)

        self.exitAct = QAction("E&xit", self, shortcut="esc",
                triggered=self.close)

        self.recentFileActs = []
        for i in range(most_recently_used_max_count):
            self.recentFileActs.append(
                    QAction(self, visible=False,
                            triggered=self.openRecentFile, shortcut="%d" % i))

        # XXX Support fit window to width, fit window to image
        # XXX Support arbitrary scrolling
        # XXX Support arbitrary zooming
        
        self.toggleFitAct = QAction("&Fit To Smallest", self, enabled=False, 
            shortcut="F", triggered=lambda : self.toggleFit())
        
        self.rotateRightAct = QAction("Rotate Ri&ght", self, enabled=False, 
            shortcut="R", triggered=lambda : self.rotateImage(90))
        self.rotateLeftAct = QAction("Rotate &Left", self, enabled=False, 
            shortcut="Shift+R", triggered=lambda : self.rotateImage(-90))

        self.gammaCorrectAct = QAction("&Gamma Correct", self, enabled=False, 
            checkable=True, shortcut="G", triggered=self.gammaCorrectionToggled)

        self.fullscreenAct = QAction("&Fullscreen", self, enabled=False,
            checkable=True, shortcut="return", triggered=self.fullscreenToggled)
        
        self.firstImageAct = QAction("Fi&rst Image", self, shortcut="up", 
            enabled=False, triggered=self.firstImage)

        self.lastImageAct = QAction("&Last Image", self, shortcut="down", 
            enabled=False, triggered=self.lastImage)

        self.prevImageAct = QAction("&Previous Image", self, shortcut="left", 
            enabled=False, triggered=self.prevImage)

        self.nextImageAct = QAction("&Next Image", self, shortcut="right", 
            enabled=False, triggered=self.nextImage)
        
        self.slideshowAct = QAction("Toggle Slidesho&w", self, shortcut="space", 
            checkable=True, enabled=False, triggered=self.slideshowToggled)
        
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
        self.viewMenu.addAction(self.rotateLeftAct)
        self.viewMenu.addAction(self.rotateRightAct)
        self.viewMenu.addSeparator()
        self.viewMenu.addAction(self.gammaCorrectAct)
        self.viewMenu.addSeparator()
        self.viewMenu.addAction(self.fullscreenAct)
        self.viewMenu.addSeparator()
        self.viewMenu.addAction(self.firstImageAct)
        self.viewMenu.addAction(self.lastImageAct)
        self.viewMenu.addAction(self.prevImageAct)
        self.viewMenu.addAction(self.nextImageAct)
        self.viewMenu.addSeparator()
        self.viewMenu.addAction(self.slideshowAct)

        self.helpMenu = QMenu("&Help", self)
        self.helpMenu.addAction(self.aboutAct)
        self.helpMenu.addAction(self.aboutQtAct)

        self.menuBar().addMenu(self.fileMenu)
        self.menuBar().addMenu(self.viewMenu)
        self.menuBar().addMenu(self.helpMenu)

    def createStatus(self):
        self.statusBar().addPermanentWidget(VLine())
        self.statusFilepath = QLabel()
        self.statusBar().addPermanentWidget(self.statusFilepath)
        self.statusBar().addPermanentWidget(VLine())
        self.statusResolution = QLabel()
        self.statusBar().addPermanentWidget(self.statusResolution)
        self.statusBar().addPermanentWidget(VLine())
        self.statusIndex = QLabel()
        self.statusBar().addPermanentWidget(self.statusIndex)
        self.statusBar().addPermanentWidget(VLine())
        self.statusZoom = QLabel()
        self.statusBar().addPermanentWidget(self.statusZoom)
        self.statusBar().addPermanentWidget(VLine())
        self.statusSize = QLabel()
        self.statusBar().addPermanentWidget(self.statusSize)
        self.statusBar().addPermanentWidget(VLine())
        self.statusDate = QLabel()
        self.statusBar().addPermanentWidget(self.statusDate)
        
    def updateActions(self):
        self.copyToClipboardAct.setEnabled(True)
        self.toggleFitAct.setEnabled(True)
        self.rotateLeftAct.setEnabled(True)
        self.rotateRightAct.setEnabled(True)
        self.gammaCorrectAct.setEnabled(True)
        self.firstImageAct.setEnabled(True)
        self.lastImageAct.setEnabled(True)
        self.prevImageAct.setEnabled(True)
        self.nextImageAct.setEnabled(True)
        self.slideshowAct.setEnabled(True)
        self.fullscreenAct.setEnabled(True)
        

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

logger = logging.getLogger(__name__)
setup_logger(logger)
logger.setLevel(logging.INFO)

if (__name__ == '__main__'):
    app = QApplication(sys.argv)
    imageViewer = ImageViewer()
    imageViewer.show()
    sys.exit(app.exec_())
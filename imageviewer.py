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
XXX Take a look on whether logging needs %r because of unicode (print to non
    unicode consoles raises UnicodeEncodeError)
    See https://stackoverflow.com/questions/21129020/how-to-fix-unicodedecodeerror-ascii-codec-cant-decode-byte
    See https://stackoverflow.com/questions/33955276/python3-unicodedecodeerror
    See https://stackoverflow.com/questions/5419/python-unicode-and-the-windows-console
"""

import datetime
import logging
import os
import Queue as queue
import stat
import string
import sys
import thread
import time

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

    info("os_path_abspath %r is %r", path, abspath)
    return abspath


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
stat_timeout_secs = 0.25
# QApplication.keyboardInputInterval() is 400ms, which is too short
listview_keyboard_search_timeout_secs = 0.75


# queue is an old style class, inherit from object to make newstyle
class Queue(queue.Queue, object):
    """
    Queue with a clear method to flush/drain the queue
    """
    def __init__(self, *args, **kwargs):
        # Call init directly since super can't be used in oldstyle classes which
        # queue is
        queue.Queue.__init__(self, *args, **kwargs)

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
        try:
            while (True):
                entry = self.get_nowait()
                info("Drained entry %.200r", entry)
        except queue.Empty:
            pass
        info("Queue.clear done")


def prefetch_files(request_queue, response_queue):
    info("prefetch_files starts")
    while (True):
        filepath = request_queue.get()
        if (filepath is None):
            response_queue.put(None)
            break

        info("worker prefetching %r", filepath)
        # Store file contents, don't use QImage yet because:
        # - Using QImage.load will block the GUI thread for the whole duration
        #   of the load and conversion, which makes prefetching useless.
        # - Just Converting to QImage here blocks the GUI thread, which makes
        #   prefetching useless
        # - Converting the data to QImage increases 100x fold the cache memory
        #   footprint
        # - Doing QImage conversion on the GUI thread is not that taxing and
        #   doesn't stall for long
        try:
            with open(filepath, "rb") as f:
                data = f.read()
        except:
            # If there was an error, let the caller handle it by placing None
            # data
            exc("Unable to prefetch %r", filepath)
            data = None
        info("worker prefetched %r", filepath)
        response_queue.put((filepath, data))

    info("prefetch_files ends")


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
            entry = ""
            while (entry is not None):
                entry = self.statResponseQueue.get()
                info("Drained entry %.200r", entry)
        info("Signaled")

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
        
        info("listdir")
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
        info("resizing pixmap from %s to %s and %s", self.originalPixmap.size(), size, self.size())
        
        # XXX Reset scroll if resizing window (resizes, fullscreen), or clamp 
        #     below

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

        self.prefetch_request_queue = Queue()
        self.prefetch_response_queue = Queue()
        # XXX Check any relationship between prefetch and cache counts, looks
        #     like there shouldn't be any even if the current is evicted from
        #     lur since the current is also kept separately? (although prefetch
        #     > cache is probably silly)
        self.prefetched_images_max_count = 10
        self.prefetch_pending = set()
        self.prefetcher_count = (self.prefetched_images_max_count / 2) + 1

        for i in xrange(self.prefetcher_count):
            thread.start_new_thread(prefetch_files, (self.prefetch_request_queue, self.prefetch_response_queue))
        
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

    def clearRequests(self):
        info("clearRequests")

        info("Clearing requests")
        # These need to be cleared manually since they need to be removed from 
        # prefetch_pending
        try:
            filepath = self.prefetch_request_queue.get_nowait()
            info("cleared request for %r", filepath)
            self.prefetch_pending.remove(filepath)

        except queue.Empty:
            pass
        info("Cleared requests")
        

    def clearQueues(self):
        info("clearQueues")
        self.prefetch_request_queue.clear()
        self.prefetch_response_queue.clear()
        self.prefetch_pending.clear()

    def cleanup(self):
        info("Signaling %d prefetchers to end", self.prefetcher_count)
        for _ in xrange(self.prefetcher_count):
            self.prefetch_request_queue.put(None)
            self.prefetch_response_queue.get()
        info("Signaled")
        
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

        # Remove prefetches that haven't been serviced yet to prevent prefetches
        # fighting for filesystem bandwidth with the filedialog list and stat
        # (note the prefetches in flight will still be serviced and put in the
        # reponse queue)
        self.clearRequests()

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
        

    def getDataFromCache(self, filepath):
        assert isinstance(filepath, unicode) 
        # XXX Qt already has QPixmapCache, look into it?

        # Get the file from the cache and bring it to the front if in the cache,
        # request it and put it in the front otherwise

        for i, entry in enumerate(self.cached_files):
            entry_filepath, entry_data = entry
            dbg("cache entry %r vs. %r", entry_filepath, filepath)
            if (filepath == entry_filepath):
                info("cache hit for %r", filepath)
                self.cached_files.pop(i)
                # Put it at the beginning of the LRU cache
                self.cached_files.insert(0, entry)
                break

        else:

            info("cache miss for %r", filepath)
            
            # The filepath is not in the cache, request if not already pending
            if (filepath not in self.prefetch_pending):
                info("prefetch pending miss for %r", filepath)
                info("ordering prefetch for %r", filepath)
                self.prefetch_request_queue.put(filepath)
                self.prefetch_pending.add(filepath)

            else:
                info("prefetch pending hit for %r", filepath)
                
            # Drain the requests as they are satisfied until the requested one
            # is found. Note this won't be in order if there are more than one
            # prefetcher threads
            while (True):
                entry_filepath, entry_data = self.prefetch_response_queue.get()
                
                self.prefetch_pending.remove(entry_filepath)

                if (entry_data is not None):
                    if (len(self.cached_files) >= self.cached_files_max_count):
                        info("evicting %r for %r", self.cached_files[-1][0], filepath)
                        self.cached_files.pop(-1)
                    info("inserting in cache %r", entry_filepath)
                    self.cached_files.insert(0, (entry_filepath, entry_data))

                if (entry_filepath == filepath):
                    break

        return entry_data
            
    def loadImage(self, filepath, index = None, count = None):
        info("loadImage %r %s %s", filepath, index, count)
        assert isinstance(filepath, unicode) 
        if (filepath.lower().endswith(".lst")):
            lst_filepath = filepath
            filepaths = []
            try:
                info("loading lst file %r", lst_filepath)
                with open(lst_filepath, "r") as f:
                    filepaths = f.readlines()
                info("loaded lst file %r", lst_filepath)

            except:
                exc("Unable to read %s", lst_filepath)
            
            if (len(filepaths) == 0):
                QMessageBox.information(self, "Image Viewer",
                    "Cannot load %s." % lst_filepath)
                return
            
            filepaths = [unicode(filepath.strip()) for filepath in filepaths]
            for i, filepath in enumerate(filepaths):
                if (not os.path.isabs(filepath)):
                    filepaths[i] = os.path.join(os.path.dirname(lst_filepath), filepath)
                
            filepath = filepaths[0]
            self.image_filepaths = filepaths
            self.image_index = 0
            index = 0
            count = len(filepaths)

        else:
            # If there's no index and count information, reset filenames cache
            if (index is None):
                self.image_filepaths = None
            
        # Update these early in case the image fails to load other filepaths can
        # still be cycled
        if ((self.image_filepaths is not None) and (len(self.image_filepaths) > 1)):
            self.firstImageAct.setEnabled(True)
            self.lastImageAct.setEnabled(True)
            self.prevImageAct.setEnabled(True)
            self.nextImageAct.setEnabled(True)
            self.slideshowAct.setEnabled(True)

        info("QImaging %r", filepath)
        self.statusBar().showMessage("Loading...")
        QApplication.setOverrideCursor(Qt.WaitCursor)
        data = self.getDataFromCache(filepath)
        QApplication.restoreOverrideCursor()
        if (data is None):
            QMessageBox.information(self, "Image Viewer",
                    "Cannot load %s." % filepath)
            image = None

        else:

            self.statusBar().showMessage("Converting...")
            QApplication.setOverrideCursor(Qt.WaitCursor)
            image = QImage.fromData(data)
            QApplication.restoreOverrideCursor()
            self.statusBar().clearMessage()
            info("QImaged %r", filepath)
        
            if (image.isNull()):
                QMessageBox.information(self, "Image Viewer",
                        "Invalid image file %s." % filepath)
                image = None

        if (image is None):
            # Create a dummy image, this is the easiest way of preventing
            # exceptions everywhere when an invalid file is encountered
            # (prev/next navigation, etc)
            image = QImage(10, 10, QImage.Format_RGB32)
            image.fill(Qt.black)

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
            size_to_human_friendly_units(len(data) if data is not None else 0), 
            size_to_human_friendly_units(image.byteCount()),
            size_to_human_friendly_units(
                sum([len(entry_data) for entry_filepath, entry_data in self.cached_files]) + 
                # XXX These getsize cause a noticeable stall
                # sum([os.path.getsize(entry_filepath) for entry_filepath in self.prefetch_pending])
                0
                )
        ))

        try:
            filemtime = os.path.getmtime(filepath)
            
        except:
            exc("Unable to get the filetime for %s", filepath)
            filemtime = 0

        filedate = datetime.datetime.fromtimestamp(filemtime)
        self.statusDate.setText("%s" % filedate.strftime("%Y-%m-%d %H:%M:%S"))
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
            info("listing %r", image_dirname)
            QApplication.setOverrideCursor(Qt.WaitCursor)
            try:
                filenames = os.listdir(image_dirname)

            except:
                # This can fail if the host is down or if the path is invalid,
                # in that case return empty filenames
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
            filepaths = self.image_filepaths
        
        filepath = None

        # This could be not in list if an invalid file was introduced
        # XXX This needs error handling if the current dirpath is invalid, 
        #     in which case filepaths is empty and should go directly to show
        #     the open dialog box
        try:
            prev_i = filepaths.index(self.image_filepath)

        except ValueError as e:
            prev_i = 0

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
                    info("ordering prefetch for %r", filepath)
                    self.prefetch_request_queue.put(filepath)
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
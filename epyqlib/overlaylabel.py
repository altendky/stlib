#!/usr/bin/env python3

#TODO: """DocString if there is one"""

import enum
import functools
import io
import os
import weakref
from PyQt5 import QtWidgets, uic
from PyQt5.QtCore import (pyqtProperty, pyqtSlot, Qt, QFile,
                          QFileInfo, QTextStream)
from PyQt5.QtGui import QFontMetrics

# See file COPYING in this source tree
__copyright__ = 'Copyright 2016, EPC Power Corp.'
__license__ = 'GPLv2+'


styles = {
    'red': "background-color: rgba(255, 255, 255, 0);"
                           "color: rgba(255, 85, 85, 25);",
    'blue': "background-color: rgba(255, 255, 255, 0);"
                           "color: rgba(85, 85, 255, 25);"
}


def parent_resizeEvent(event, child, parent_resizeEvent):
    child = child()
    if child is not None:
        child.resize(event.size())
        parent = parent_resizeEvent()
        if parent is not None:
            parent(event)


class OverlayLabel(QtWidgets.QWidget):
    def __init__(self, parent=None, in_designer=False):
        QtWidgets.QWidget.__init__(self, parent=parent)

        self.in_designer = in_designer

        ui = 'overlaylabel.ui'
        # TODO: CAMPid 9549757292917394095482739548437597676742
        if not QFileInfo(ui).isAbsolute():
            ui_file = os.path.join(
                QFileInfo.absolutePath(QFileInfo(__file__)), ui)
        else:
            ui_file = ui
        ui_file = QFile(ui_file)
        ui_file.open(QFile.ReadOnly | QFile.Text)
        ts = QTextStream(ui_file)
        sio = io.StringIO(ts.readAll())
        self.ui = uic.loadUi(sio, self)

        parent_widget = self.parentWidget()

        if parent_widget is not None:
            new_resizeEvent = functools.partial(
                parent_resizeEvent,
                child=weakref.ref(self),
                parent_resizeEvent=weakref.ref(parent_widget.resizeEvent)
            )

            parent_widget.resizeEvent = new_resizeEvent

        self.setStyleSheet(styles['red'])

        self.ui.setAttribute(Qt.WA_TransparentForMouseEvents)

        self._width_ratio = 0.8
        self._height_ratio = 0.8

        if parent_widget is not None:
            self.update_overlay_size(parent_widget.size())

    @pyqtProperty(float)
    def width_ratio(self):
        return self._width_ratio

    @width_ratio.setter
    def width_ratio(self, value):
        self._width_ratio = value

    @pyqtProperty(float)
    def height_ratio(self):
        return self._height_ratio

    @height_ratio.setter
    def height_ratio(self, value):
        self._height_ratio = value

    def resizeEvent(self, event):
        QtWidgets.QWidget.resizeEvent(self, event)

        self.update_overlay_size(event.size())

    def update_overlay_size(self, size):
        text = self.label.text()
        if not text:
            text = '-'
        font = self.label.font()
        font.setPixelSize(1000)
        metric = QFontMetrics(font)
        rect = metric.boundingRect(text)

        pixel_size_width = (
            font.pixelSize() *
            (size.width() * self.width_ratio) / rect.width()
        )

        pixel_size_height = (
            font.pixelSize() *
            (size.height() * self.height_ratio) / rect.height()
        )

        self.label.setStyleSheet('font-size: {}px; font-weight: bold'.format(
            round(min(pixel_size_width, pixel_size_height))))


if __name__ == '__main__':
    import sys

    print('No script functionality here')
    sys.exit(1)     # non-zero is a failure

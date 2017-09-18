from PyQt5.QtCore import pyqtSlot, Qt, QCoreApplication, QEvent, QPoint
from PyQt5 import QtWidgets
from PyQt5.QtGui import QMouseEvent


def default(node):
    if hasattr(node, 'enumeration_strings'):
        if len(node.enumeration_strings()) > 0:
            return create_combo, None

    if hasattr(node, 'secret'):
        if node.secret:
            return None, modify_password

    return None, None


class ByFunction(QtWidgets.QStyledItemDelegate):
    def __init__(self, model, parent, proxy=None, function=default):
        QtWidgets.QStyledItemDelegate.__init__(self, parent=parent)

        self.model = model
        self.proxy = proxy
        self.function = function

    def createEditor(self, parent, option, index):
        if self.proxy is not None:
            index = self.proxy.mapToSource(index)
        # TODO: way too particular
        node = self.model.node_from_index(index)

        creator, modifier = self.function(node=node)

        if creator is None:
            widget = QtWidgets.QStyledItemDelegate.createEditor(
                self, parent, option, index)
        else:
            widget = creator(index=index, node=node, parent=parent)
            widget.currentIndexChanged.connect(self.current_index_changed)

        if modifier is not None:
            modifier(widget=widget)

        return widget

    @pyqtSlot()
    def current_index_changed(self):
        self.commitData.emit(self.sender())


def create_combo(index, node, parent):
    widget = QtWidgets.QComboBox(parent=parent)

    # TODO: use the userdata to make it easier to get in and out
    widget.addItems(node.enumeration_strings(include_values=True))

    present_string = str(node.fields[index.column()])
    index = widget.findText(present_string)
    if index == -1:
        widget.setCurrentIndex(0)
    else:
        widget.setCurrentIndex(index)

    view = widget.view()
    view.setMinimumWidth(calculate_combo_view_width(widget))

    event = QMouseEvent(QEvent.MouseButtonPress,
                        QPoint(),
                        Qt.LeftButton,
                        Qt.LeftButton,
                        Qt.NoModifier)
    QCoreApplication.postEvent(widget, event)

    return widget


def calculate_combo_view_width(widget):
    view = widget.view()
    metric = view.fontMetrics()
    scrollbar = view.verticalScrollBar()

    scrollbar_width = 0
    if scrollbar.isVisibleTo(view):
        scrollbar_width = scrollbar.width()

    text = (widget.itemText(i) for i in range(widget.count()))
    text_width = max(metric.width(s) for s in text)

    # consider width of icons, for example

    return text_width + scrollbar_width


def create_button(index, node, parent):
    text = node.button_text(index.column())
    widget = QtWidgets.QPushButton(parent=parent)
    widget.setText(text)

    return widget


def modify_password(widget):
    widget.setEchoMode(QtWidgets.QLineEdit.Password)

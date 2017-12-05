#!/usr/bin/env python3

# TODO: get some docstrings in here!

import logging
logger = logging.getLogger(__name__)

import attr
import can
import canmatrix.formats
import collections
import epyqlib.canneo
import epyqlib.deviceextension
try:
    import epyqlib.resources.code
except ImportError:
    pass # we will catch the failure to open the file
import epyqlib.nv
import epyqlib.nvview
import epyqlib.overlaylabel
import epyqlib.scripting
import epyqlib.twisted.loopingset
import epyqlib.txrx
import epyqlib.txrxview
import epyqlib.utils.qt
import epyqlib.utils.j1939
import epyqlib.variableselectionmodel
import functools
import importlib.util
import io
import itertools
import json
import math
import os
import shutil
import tempfile
import textwrap
import twisted.internet.task
import zipfile
from twisted.internet.defer import setDebugging
setDebugging(True)

from collections import OrderedDict
from enum import Enum, unique
from epyqlib.busproxy import BusProxy
import epyqlib.updateepc
from epyqlib.widgets.abstractwidget import AbstractWidget
from PyQt5 import uic
from PyQt5.QtCore import (pyqtSlot, Qt, QFile, QFileInfo, QTextStream, QObject,
                          QSortFilterProxyModel, QIODevice)
from PyQt5.QtWidgets import (
    QWidget, QMessageBox, QInputDialog, QLineEdit, QVBoxLayout)
from PyQt5 import QtCore

# See file COPYING in this source tree
__copyright__ = 'Copyright 2016, EPC Power Corp.'
__license__ = 'GPLv2+'


class CancelError(Exception):
    pass


@unique
class Elements(Enum):
    dash = 1
    tx = 2
    rx = 3
    variables = 4
    nv = 5
    scripting = 6


@unique
class Tabs(Enum):
    dashes = 1
    txrx = 2
    variables = 3
    nv = 4
    scripting = 5

    @classmethod
    def defaults(cls):
        return set(cls) - {cls.variables, cls.scripting}


def j1939_node_id_adjust(message_id, device_id, to_device, controller_id):
    # CCP stuff is in bounds now but leave this for backwards compatibility
    if message_id > 0x1FFFFF00:
        return message_id

    id = epyqlib.utils.j1939.Id.unpack(message_id)

    if to_device:
        source = controller_id
        destination = device_id
    else:
        source = device_id
        destination = controller_id

    if id.is_pdu1():
        if destination is not None:
            id.destination_address = destination

    if source is not None:
        id.source_address = source

    return id.pack()


def simple_node_id_adjust(message_id, device_id, to_device, controller_id):
    return message_id + device_id


node_id_types = OrderedDict([
    ('j1939', j1939_node_id_adjust),
    ('simple', simple_node_id_adjust)
])


@attr.s
class CanConfiguration:
    data_logger_reset_signal_path = attr.ib()
    data_logger_recording_signal_path = attr.ib()
    data_logger_configuration_is_valid_signal_path = attr.ib()
    monitor_frame = attr.ib()


can_configurations = {
    'original': CanConfiguration(
        data_logger_reset_signal_path=(
            'CommandModeControl', 'ResetDatalogger'),
        data_logger_recording_signal_path=(
            'StatusBits', 'DataloggerRecording'),
        data_logger_configuration_is_valid_signal_path=(
            'StatusBits', 'DataloggerConfigurationIsValid'),
        monitor_frame='StatusBits',
    ),
    'j1939': CanConfiguration(
        data_logger_reset_signal_path=(
            'ParameterQuery', 'DataloggerConfig', 'ResetDatalogger'),
        data_logger_recording_signal_path=(
            'ParameterQuery', 'DataloggerStatus', 'DataloggerRecording'),
        data_logger_configuration_is_valid_signal_path=(
            'ParameterQuery', 'DataloggerStatus',
            'DataloggerConfigurationIsValid'),
        monitor_frame='StatusBits',
    ),
}


def load(file):
    if isinstance(file, str):
        pass
    elif isinstance(file, io.IOBase):
        pass


def ignore_timeout(failure):
    acceptable_errors = (
        epyqlib.twisted.nvs.RequestTimeoutError,
        epyqlib.twisted.nvs.SendFailedError,
        epyqlib.twisted.nvs.CanceledError,
    )
    if failure.type in acceptable_errors:
        return None

    return epyqlib.utils.twisted.errbackhook(
        failure)


class Device:
    def __init__(self, *args, **kwargs):
        self.bus = None
        self.from_zip = False

        if kwargs.get('file', None) is not None:
            constructor = self._init_from_file
        else:
            constructor = self._init_from_parameters

        constructor(*args, **kwargs)

    def __del__(self):
        if self.bus is not None:
            self.bus.set_bus()

    def _init_from_file(self, file, only_for_files=False, **kwargs):
        extension = os.path.splitext(file)[1].casefold()

        if extension in ('.epz', '.zip'):
            zip_file = zipfile.ZipFile(file)
            self._init_from_zip(zip_file, **kwargs)
        else:
            try:
                self.config_path = os.path.abspath(file)
                file = open(file, 'r')
            except TypeError:
                return
            else:
                converted_directory = None
                final_file = file
                if not epyqlib.updateepc.is_latest(file.name):
                    converted_directory = tempfile.TemporaryDirectory()
                    final_file = open(epyqlib.updateepc.convert(
                        file.name,
                        converted_directory.name,
                    ))
                self.config_path = os.path.abspath(final_file.name)

                self._load_config(file=final_file, only_for_files=only_for_files,
                                  **kwargs)

                if final_file is not file:
                    final_file.close()

                if converted_directory is not None:
                    converted_directory.cleanup()

    def _load_config(self, file, elements=None,
                     tabs=None, rx_interval=0, edit_actions=None,
                     only_for_files=False, node_id=None, **kwargs):
        if tabs is None:
            tabs = Tabs.defaults()

        self.node_id = node_id

        self.elements = Elements if elements == None else elements
        self.elements = set(Elements)

        s = file.read()
        d = json.loads(s, object_pairs_hook=OrderedDict)
        self.raw_dict = d

        self.module_path = d.get('module', None)
        self.plugin = None
        if self.module_path is None:
            module = epyqlib.deviceextension
        else:
            spec = importlib.util.spec_from_file_location(
                'extension', self.absolute_path(self.module_path))
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)

        extension_class = module.DeviceExtension

        import weakref
        self.extension = extension_class(device=weakref.ref(self))

        path = os.path.dirname(file.name)
        for ui_path_name in ['ui_path', 'ui_paths', 'menu']:
            try:
                json_ui_paths = d[ui_path_name]
                break
            except KeyError:
                pass
        else:
            json_ui_paths = {}

        if not isinstance(json_ui_paths, dict):
            json_ui_paths = {"Dash": json_ui_paths}

        self.ui_paths = json_ui_paths

        hierarchy_path = d.get('parameter_hierarchy', None)
        if hierarchy_path is None:
            hierarchy = None
        else:
            with open(self.absolute_path(hierarchy_path)) as f:
                hierarchy = json.load(f)

        for tab in Tabs:
            try:
                value = d['tabs'][tab.name]
            except KeyError:
                pass
            else:
                if int(value):
                    tabs.add(tab)
                else:
                    tabs.discard(tab)

        if Tabs.txrx not in tabs:
            self.elements.discard(Elements.tx)
            self.elements.discard(Elements.rx)

        if Tabs.variables not in tabs:
            self.elements.discard(Elements.variables)

        if Tabs.nv not in tabs:
            self.elements.discard(Elements.nv)

        if Tabs.scripting not in tabs:
            self.elements.discard(Elements.scripting)

        self.referenced_files = [
            f for f in [
                d.get('module', None),
                d.get('can_path', None),
                d.get('compatibility', None),
                d.get('parameter_defaults', None),
                d.get('parameter_hierarchy', None),
                *self.ui_paths.values(),
                *module.referenced_files(self.raw_dict),
            ]
            if f is not None
        ]

        self.shas = []
        compatibility_file = d.get('compatibility', None)
        if compatibility_file is not None:
            compatibility_file = os.path.join(
                os.path.dirname(self.config_path), compatibility_file)
            with open(compatibility_file) as file:
                s = file.read()
                c = json.loads(s, object_pairs_hook=OrderedDict)

            self.shas.extend(c.get('shas', []))

        if not only_for_files:
            self.can_path = os.path.join(path, d['can_path'])

            self.node_id_type = d.get('node_id_type',
                                      next(iter(node_id_types))).lower()
            if self.node_id is None:
                self.node_id = d.get('node_id')
            if self.node_id is None and self.node_id_type == 'j1939':
                self.node_id, ok = QInputDialog.getInt(
                    None,
                    *(('Converter Node ID',) * 2),
                    247,
                    0,
                    247,
                )

                if not ok:
                    raise CancelError('User canceled node ID dialog')
            self.node_id = int(self.node_id)
            self.controller_id = int(d.get('controller_id', 65))
            self.node_id_adjust = functools.partial(
                node_id_types[self.node_id_type],
                device_id=self.node_id,
                controller_id=self.controller_id,
            )

            self._init_from_parameters(
                uis=self.ui_paths,
                serial_number=d.get('serial_number', ''),
                name=d.get('name', ''),
                tabs=tabs,
                rx_interval=rx_interval,
                edit_actions=edit_actions,
                nv_configuration=d.get('nv_configuration'),
                can_configuration=d.get('can_configuration'),
                hierarchy=hierarchy,
                **kwargs)

    def _init_from_zip(self, zip_file, rx_interval=0, **kwargs):
        self.from_zip = True
        path = tempfile.mkdtemp()

        code = epyqlib.utils.qt.get_code()

        while True:
            try:
                zip_file.extractall(path=path, pwd=code)
            except RuntimeError:
                code, ok = QInputDialog.getText(
                    None,
                    '.epz Password',
                    '.epz Password',
                    QLineEdit.Password)

                if not ok:
                    raise CancelError('User canceled password dialog')

                code = code.encode('ascii')
            else:
                break

        # TODO error dialog if no .epc found in zip file
        filename = None
        for directory, directories, files in os.walk(path):
            for f in files:
                print(f)
                if os.path.splitext(f)[1] == '.epc':
                    filename = os.path.join(path, directory, f)
                    break

            if filename is not None:
                break

        self.config_path = os.path.abspath(filename)

        converted_directory = None
        if not epyqlib.updateepc.is_latest(filename):
            converted_directory = tempfile.TemporaryDirectory()
            file = epyqlib.updateepc.convert(filename, converted_directory.name)
            self.config_path = os.path.abspath(file)

        with open(filename, 'r') as f:
            self._load_config(f, rx_interval=rx_interval, **kwargs)

        if converted_directory is not None:
            converted_directory.cleanup()
        shutil.rmtree(path)

    def _init_from_parameters(self, uis, serial_number, name, bus=None,
                              tabs=None, rx_interval=0, edit_actions=None,
                              nv_configuration=None, can_configuration=None,
                              hierarchy=None):
        if tabs is None:
            tabs = Tabs.defaults()

        if can_configuration is None:
            can_configuration = 'original'

        can_configuration = can_configurations[can_configuration]

        self.bus_online = False
        self.bus_tx = False

        self.bus = BusProxy(bus=bus)

        self.nv_looping_set = None
        self.nv_tab_looping_set = None

        self.rx_interval = rx_interval
        self.serial_number = serial_number
        self.name = '{name} :{id}'.format(name=name,
                                          id=self.node_id)

        device_ui = 'device.ui'
        # TODO: CAMPid 9549757292917394095482739548437597676742
        if not QFileInfo(device_ui).isAbsolute():
            ui_file = os.path.join(
                QFileInfo.absolutePath(QFileInfo(__file__)), device_ui)
        else:
            ui_file = device_ui
        ui_file = QFile(ui_file)
        ui_file.open(QFile.ReadOnly | QFile.Text)
        ts = QTextStream(ui_file)
        sio = io.StringIO(ts.readAll())
        self.ui = uic.loadUi(sio)
        self.loaded_uis = {}

        def traverse(dict_node):
            for key, value in dict_node.items():
                if isinstance(value, dict):
                    traverse(value)
                elif value.endswith('.ui'):
                    path = value
                    try:
                        dict_node[key] = self.loaded_uis[path]
                    except KeyError:
                        # TODO: CAMPid 9549757292917394095482739548437597676742
                        if not QFileInfo(path).isAbsolute():
                            ui_file = os.path.join(
                                QFileInfo.absolutePath(QFileInfo(self.config_path)),
                                path)
                        else:
                            ui_file = path
                        ui_file = QFile(ui_file)
                        ui_file.open(QFile.ReadOnly | QFile.Text)
                        ts = QTextStream(ui_file)
                        sio = io.StringIO(ts.readAll())
                        dict_node[key] = uic.loadUi(sio)
                        dict_node[key].file_name = path
                        self.loaded_uis[path] = dict_node[key]

        traverse(uis)

        # TODO: yuck, actually tidy the code
        self.dash_uis = uis

        notifiees = []

        if Elements.dash in self.elements:
            self.uis = self.dash_uis

            matrix = list(canmatrix.formats.loadp(self.can_path).values())[0]
            # TODO: this is icky
            if Elements.tx not in self.elements:
                self.neo_frames = epyqlib.canneo.Neo(matrix=matrix,
                                                  bus=self.bus,
                                                  rx_interval=self.rx_interval)

                notifiees.append(self.neo_frames)

        if Elements.rx in self.elements:
            # TODO: the repetition here is not so pretty
            matrix_rx = list(canmatrix.formats.loadp(self.can_path).values())[0]
            neo_rx = epyqlib.canneo.Neo(
                matrix=matrix_rx,
                frame_class=epyqlib.txrx.MessageNode,
                signal_class=epyqlib.txrx.SignalNode,
                node_id_adjust=self.node_id_adjust,
                strip_summary=False,
            )

            rx = epyqlib.txrx.TxRx(tx=False, neo=neo_rx)
            notifiees.append(rx)
            rx_model = epyqlib.txrx.TxRxModel(rx)

            # TODO: put this all in the model...
            rx.changed.connect(rx_model.changed)
            rx.begin_insert_rows.connect(rx_model.begin_insert_rows)
            rx.end_insert_rows.connect(rx_model.end_insert_rows)

        if Elements.tx in self.elements:
            matrix_tx = list(canmatrix.formats.loadp(self.can_path).values())[0]
            message_node_tx_partial = functools.partial(epyqlib.txrx.MessageNode,
                                                        tx=True)
            signal_node_tx_partial = functools.partial(epyqlib.txrx.SignalNode,
                                                       tx=True)
            neo_tx = epyqlib.canneo.Neo(matrix=matrix_tx,
                                     frame_class=message_node_tx_partial,
                                     signal_class=signal_node_tx_partial,
                                     node_id_adjust=self.node_id_adjust)
            notifiees.extend(f for f in neo_tx.frames if f.mux_name is None)

            self.neo_frames = neo_tx

            tx = epyqlib.txrx.TxRx(tx=True, neo=neo_tx, bus=self.bus)
            tx_model = epyqlib.txrx.TxRxModel(tx)
            tx.changed.connect(tx_model.changed)

        # TODO: something with sets instead?
        if (Elements.rx in self.elements or
            Elements.tx in self.elements):
            txrx_views = self.ui.findChildren(epyqlib.txrxview.TxRxView)
            if len(txrx_views) > 0:
                # TODO: actually find them and actually support multiple
                pairs = (
                    (self.ui.rx, rx_model),
                    (self.ui.tx, tx_model),
                )
                column = epyqlib.txrx.Columns.indexes.name
                for view, model in pairs:
                    if model.root.tx:
                        proxy = epyqlib.utils.qt.PySortFilterProxyModel(
                            filter_column=column,
                        )
                        proxy.setSortCaseSensitivity(Qt.CaseInsensitive)
                        proxy.setSourceModel(model)
                        view.setModel(proxy)
                        view.set_sorting_enabled(True)
                        view.sort_by_column(
                            column=column,
                            order=Qt.AscendingOrder
                        )
                    else:
                        view.setModel(model)

        if Elements.nv in self.elements:
            matrix_nv = list(canmatrix.formats.loadp(self.can_path).values())[0]
            self.frames_nv = epyqlib.canneo.Neo(
                matrix=matrix_nv,
                frame_class=epyqlib.nv.Frame,
                signal_class=epyqlib.nv.Nv,
                node_id_adjust=self.node_id_adjust,
                strip_summary=False,
            )

            self.nv_looping_set = epyqlib.twisted.loopingset.Set()
            self.nv_tab_looping_set = epyqlib.twisted.loopingset.Set()

            self.nvs = epyqlib.nv.Nvs(
                neo=self.frames_nv,
                bus=self.bus,
                configuration=nv_configuration,
                hierarchy=hierarchy,
                metas=reversed(epyqlib.nv.MetaEnum),
            )

            self.widget_frames_nv = epyqlib.canneo.Neo(
                matrix=matrix_nv,
                frame_class=epyqlib.nv.Frame,
                signal_class=epyqlib.nv.Nv,
                node_id_adjust=self.node_id_adjust
            )
            self.widget_nvs = epyqlib.nv.Nvs(
                neo=self.widget_frames_nv,
                bus=self.bus,
                stop_cyclic=self.nv_looping_set.stop,
                start_cyclic=self.nv_looping_set.start,
                configuration=nv_configuration
            )
            notifiees.append(self.widget_nvs)

            nv_views = self.ui.findChildren(epyqlib.nvview.NvView)
            if len(nv_views) > 0:
                nv_model = epyqlib.nv.NvModel(self.nvs)
                self.nvs.changed.connect(nv_model.changed)

                column = epyqlib.nv.Columns.indexes.name
                for view in nv_views:
                    proxy = epyqlib.utils.qt.PySortFilterProxyModel(
                        filter_column=column,
                    )
                    proxy.setSortCaseSensitivity(Qt.CaseInsensitive)
                    proxy.setSourceModel(nv_model)
                    view.setModel(proxy)
                    view.set_sorting_enabled(True)
                    view.sort_by_column(
                        column=column,
                        order=Qt.AscendingOrder
                    )

                    nv_range_check_overridable = self.raw_dict.get(
                        'nv_range_check_overridable',
                        False,
                    )

                    view.ui.enforce_range_limits_check_box.setVisible(
                        nv_range_check_overridable,
                    )

        if Elements.variables in self.elements:
            variable_model = epyqlib.variableselectionmodel.VariableModel(
                nvs=self.nvs,
                bus=self.bus,
                tx_id=self.neo_frames.frame_by_name('CCP').id,
                rx_id=self.neo_frames.frame_by_name('CCPResponse').id
            )

            column = epyqlib.variableselectionmodel.Columns.indexes.name
            proxy = epyqlib.utils.qt.PySortFilterProxyModel(
                filter_column=column,
            )
            proxy.setSortCaseSensitivity(Qt.CaseInsensitive)
            proxy.setSourceModel(variable_model)
            self.ui.variable_selection.set_model(proxy)
            self.ui.variable_selection.set_sorting_enabled(True)
            self.ui.variable_selection.sort_by_column(
                column=column,
                order=Qt.AscendingOrder
            )
            self.ui.variable_selection.set_signal_paths(
                reset_signal_path=
                    can_configuration.data_logger_reset_signal_path,
                recording_signal_path=
                    can_configuration.data_logger_recording_signal_path,
                configuration_is_valid_signal_path=
                    can_configuration.data_logger_configuration_is_valid_signal_path,
            )

        if Tabs.dashes in tabs:
            for i, (name, dash) in enumerate(self.dash_uis.items()):
                self.ui.tabs.insertTab(i,
                                       dash,
                                       name)
        if Tabs.txrx not in tabs:
            self.ui.tabs.removeTab(self.ui.tabs.indexOf(self.ui.txrx))
        if Tabs.variables not in tabs:
            self.ui.tabs.removeTab(self.ui.tabs.indexOf(self.ui.variables))
        if Tabs.nv not in tabs:
            self.ui.tabs.removeTab(self.ui.tabs.indexOf(self.ui.nv))
        else:
            def tab_changed(index):
                tabs = {
                    self.ui.tabs.indexOf(x)
                    for x in (self.ui.nv, self.ui.scripting)
                }
                if index in tabs:
                    self.nv_looping_set.stop()
                    self.nv_tab_looping_set.start()
                else:
                    self.nv_looping_set.start()
                    self.nv_tab_looping_set.stop()

            self.ui.tabs.currentChanged.connect(tab_changed)
        if Tabs.scripting not in tabs:
            self.ui.tabs.removeTab(self.ui.tabs.indexOf(self.ui.scripting))

        self.ui.tabs.setCurrentIndex(0)

        self.widget_nv_frames = collections.defaultdict(list)

        def flatten(dict_node):
            flat = set()
            for key, value in dict_node.items():
                if isinstance(value, dict):
                    flat |= flatten(value)
                else:
                    flat.add(value)

            return flat

        flat = flatten(self.dash_uis)
        flat = [v for v in flat if isinstance(v, QWidget)]

        default_widget_value = math.nan

        self.dash_connected_signals = set()
        self.dash_missing_signals = set()
        self.dash_missing_defaults = set()
        self.nv_looping_reads = {}
        if Tabs.variables in tabs:
            flat.append(self.ui.variable_selection)
        if Tabs.nv in tabs:
            flat.append(self.ui.nv)
        for dash in flat:
            # TODO: CAMPid 99457281212789437474299
            children = dash.findChildren(QObject)
            widgets = [c for c in children if
                       isinstance(c, AbstractWidget)]

            dash.connected_frames = set()
            frames = dash.connected_frames

            for widget in widgets:
                # TODO: CAMPid 07340793413419714301373147
                widget.set_range(min=0, max=100)
                try:
                    widget.set_value(default_widget_value)
                except ValueError:
                    widget.set_value(0)

                frame = widget.property('frame')
                if frame is not None:
                    signal = widget.property('signal')
                    signal_path = (frame, signal)
                else:
                    signal_path = tuple(
                        e for e in widget._signal_path if len(e) > 0)

                try:
                    signal = self.neo_frames.signal_by_path(*signal_path)
                except epyqlib.canneo.NotFoundError:
                    if not widget.ignore:
                        widget_path = []
                        p = widget
                        while p is not dash:
                            widget_path.insert(0, p.objectName())
                            p = p.parent()

                        self.dash_missing_signals.add(
                            '{}:/{} - {}'.format(
                                (
                                    dash.file_name
                                    if hasattr(dash, 'file_name')
                                    else '<builtin>'
                                ),
                                '/'.join(widget_path),
                                ':'.join(signal_path) if len(signal_path) > 0
                                    else '<none specified>'
                            )
                        )
                else:
                    # TODO: CAMPid 079320743340327834208
                    if signal.frame.id == self.nvs.set_frames[0].id:
                        nv_signal = self.widget_nvs.neo.signal_by_path(*signal_path)

                        self.widget_nv_frames[nv_signal.frame].append(
                            nv_signal,
                        )

                        if nv_signal.multiplex not in self.nv_looping_reads:
                            def read(nv_signal=nv_signal):
                                d = self.nvs.protocol.read(
                                    nv_signal=nv_signal,
                                    meta=epyqlib.nv.MetaEnum.value,
                                )

                                d.addErrback(ignore_timeout)

                                return d

                            self.nv_looping_reads[nv_signal.multiplex] = read

                        if dash is self.ui.nv:
                            self.nv_tab_looping_set.add_request(
                                key=widget,
                                request=epyqlib.twisted.loopingset.Request(
                                    f=self.nv_looping_reads[nv_signal.multiplex],
                                    period=1,
                                )
                            )
                        else:
                            self.nv_looping_set.add_request(
                                key=widget,
                                request=epyqlib.twisted.loopingset.Request(
                                    f=self.nv_looping_reads[nv_signal.multiplex],
                                    period=1,
                                )
                            )

                        if hasattr(widget, 'tx') and widget.tx:
                            signal = self.widget_nvs.neo.signal_by_path(
                                self.nvs.set_frames[0].name, *signal_path[1:])
                        else:
                            signal = self.widget_nvs.neo.signal_by_path(
                                self.nvs.status_frames[0].name, *signal_path[1:])

                    frame = signal.frame
                    frames.add(frame)
                    self.dash_connected_signals.add(signal)
                    widget.set_signal(signal)

                if edit_actions is not None:
                    # TODO: CAMPid 97453289314763416967675427
                    if widget.property('editable'):
                        for action in edit_actions:
                            if action[1](widget):
                                action[0](dash=dash,
                                          widget=widget,
                                          signal=widget.edit)
                                break

        monitor_matrix = list(
            canmatrix.formats.loadp(self.can_path).values()
        )[0]
        monitor_frames = epyqlib.canneo.Neo(matrix=monitor_matrix)
        monitor_frame = monitor_frames.frame_by_name(
            can_configuration.monitor_frame,
        )

        self.connection_monitor = FrameTimeout(frame=monitor_frame)

        self.overlay_widget = epyqlib.overlaylabel.OverlayWidget(
            parent=self.ui,
        )
        layout = QVBoxLayout()
        self.overlay_widget.setLayout(layout)

        self.ui.offline_overlay = epyqlib.overlaylabel.OverlayLabel()
        self.ui.offline_overlay.label.setText('offline')
        layout.addWidget(self.ui.offline_overlay)


        self.ui.connection_monitor_overlay = epyqlib.overlaylabel.OverlayLabel()
        layout.addWidget(self.ui.connection_monitor_overlay)

        self.connection_monitor.lost.connect(self.connection_status_changed)
        self.connection_monitor.found.connect(self.connection_status_changed)

        self.connection_monitor.found.connect(self.read_nv_widget_min_max)

        self.connection_monitor.start()

        notifiees.append(self.connection_monitor)

        self.bus_status_changed(online=False, transmit=False)

        all_signals = set()
        for frame in self.neo_frames.frames:
            for signal in frame.signals:
                if signal.name != '__padding__':
                    all_signals.add(signal)

        frame_signals = []
        for signal in all_signals - self.dash_connected_signals:
            frame_signals.append('{} : {}'.format(signal.frame.name, signal.name))

        if Elements.nv in self.elements:
            nv_frame_signals = []
            for frame in (list(self.nvs.set_frames.values())
                              + list(self.nvs.status_frames.values())):
                for signal in frame.signals:
                    nv_frame_signals.append(
                        '{} : {}'.format(signal.frame.name, signal.name))

            frame_signals = list(set(frame_signals) - set(nv_frame_signals))

        if len(frame_signals) > 0:
            logger.warning('\n === Signals not referenced by a widget')
            for frame_signal in sorted(frame_signals):
                logger.warning(frame_signal)

        if len(self.dash_missing_signals) > 0:
            logger.error('\n === Signals referenced by a widget but not defined')
            undefined_signals = sorted(self.dash_missing_signals)
            logger.error('\n'.join(undefined_signals))

            message = ('The following signals are referenced by the .ui '
                       'files but were not found in the loaded CAN '
                       'database.  The widgets will show `{}`.'
                       .format(default_widget_value))

            message = textwrap.dedent('''\
            {message}

            {signals}
            ''').format(message=message,
                        signals='\n\n'.join(undefined_signals))

            epyqlib.utils.qt.dialog(
                parent=None,
                message=message,
                icon=QMessageBox.Information,
            )

        scripting_model = epyqlib.scripting.Model(
            tx_neo=self.neo_frames,
            nvs=self.widget_nvs,
        )
        self.ui.scripting_view.set_model(scripting_model)

        for notifiee in notifiees:
            self.bus.notifier.add(notifiee)

        self.extension.post()

    def absolute_path(self, path=''):
        # TODO: CAMPid 9549757292917394095482739548437597676742
        if not QFileInfo(path).isAbsolute():
            path = os.path.join(
                QFileInfo.absolutePath(QFileInfo(self.config_path)),
                path)

        return path

    def get_frames(self):
        return self.frames

    @pyqtSlot(bool)
    def bus_status_changed(self, online, transmit):
        self.bus_online = online
        self.bus_tx = transmit

        style = epyqlib.overlaylabel.styles['red']
        text = ''
        if online:
            if not transmit:
                text = 'passive'
                style = epyqlib.overlaylabel.styles['blue']
        else:
            text = 'offline'

        self.ui.offline_overlay.label.setText(text)
        self.ui.offline_overlay.setVisible(len(text) > 0)
        self.ui.offline_overlay.setStyleSheet(style)

        self.read_nv_widget_min_max()

    def read_nv_widget_min_max(self):
        print('bus_online', self.bus_online)
        print('bus_tx', self.bus_tx)
        print('present', self.connection_monitor.present)
        active = all((
            self.bus_online,
            self.bus_tx,
            self.connection_monitor.present,
        ))

        if not active:
            return

        print('reading min/max for nv widgets')

        metas = (
            epyqlib.nv.MetaEnum.minimum,
            epyqlib.nv.MetaEnum.maximum,
        )

        for frame, signals in self.widget_nv_frames.items():
            for meta in metas:
                d = self.nvs.protocol.read_multiple(
                    nv_signals=signals,
                    meta=meta,
                )

                d.addErrback(ignore_timeout)

        return d

    def connection_status_changed(self):
        present = self.connection_monitor.present

        text = 'no status'
        if present:
            text = ''

        self.ui.connection_monitor_overlay.label.setText(text)
        self.ui.connection_monitor_overlay.setVisible(len(text) > 0)

    def terminate(self):
        self.neo_frames.terminate()
        try:
            self.ui.tabs.currentChanged.disconnect()
        except TypeError:
            # We don't really mind if there aren't any slots connect
            pass
        if self.nv_looping_set is not None:
            self.nv_looping_set.stop()
        if self.nv_tab_looping_set is not None:
            self.nv_tab_looping_set.stop()
        logging.debug('{} terminated'.format(object.__repr__(self)))


class FrameTimeout(epyqlib.canneo.QtCanListener):
    lost = epyqlib.utils.qt.Signal()
    found = epyqlib.utils.qt.Signal()

    def __init__(self, frame, relative=lambda t: 5 * t, absolute=0.5,
                 parent=None):
        super().__init__(self.message_received, parent=parent)

        self.frame = frame

        self.timeout = 1000 * max(
            absolute,
            relative(float(self.frame.cycle_time) / 1000),
        )

        self.present = False

        self.timer = QtCore.QTimer()
        self.timer.timeout.connect(self._lost)

    def _lost(self):
        self.timer.stop()
        self.present = False
        self.lost.emit()

    def start(self):
        self._lost()

    def message_received(self, msg):
        if not self.frame.message_received(msg):
            return

        self.timer.start(self.timeout)

        if not self.present:
            self.present = True
            self.found.emit()


if __name__ == '__main__':
    import sys

    print('No script functionality here')
    sys.exit(1)     # non-zero is a failure

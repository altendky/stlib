import collections
import io
import math
import textwrap

import attr

import epyqlib.utils.general
import epyqlib.canneo

# See file COPYING in this source tree
__copyright__ = 'Copyright 2017, EPC Power Corp.'
__license__ = 'GPLv2+'


class MessageType(epyqlib.utils.general.AutoNumberIntEnum):
    Rx = ()
    Tx = ()
    Error = ()


@attr.s(hash=True)
class Log(epyqlib.canneo.QtCanListener):
    name = attr.ib()
    messages = attr.ib(default=None, hash=False)
    _active = attr.ib(default=False)
    _messages_factory = attr.ib(
        default=lambda: collections.deque(maxlen=100000)
    )

    def __attrs_post_init__(self):
        super().__init__(receiver=self._message_received)

        self.clear()

    def _message_received(self, message):
        if self._active:
            self.messages.append(Message.from_pythoncan(message))

    def start(self):
        self._active = True

    def stop(self):
        self._active = False

    def clear(self):
        self.messages = self._messages_factory()

    def restart(self):
        self.clear()
        self.start()

    def minimum_timestamp(self):
        timestamps = (m.time for m in self.messages if m.time is not None)

        for t in timestamps:
            return t

        return None


@attr.s
class Id:
    value = attr.ib()
    extended = attr.ib()


@attr.s
class Message:
    time = attr.ib()
    type = attr.ib()
    id = attr.ib()
    data = attr.ib()

    @classmethod
    def from_pythoncan(cls, message):
        return cls(
            time=message.timestamp,
            type=MessageType.Rx,
            id=Id(
                value=message.arbitration_id,
                extended=message.id_type,
            ),
            data=bytearray(message.data)
        )

    @property
    def ms(self):
        if self.time is None:
            return self.time

        return self.time * 1000

    @ms.setter
    def ms(self, value):
        if value is not None:
            value = value / 1000

        self.time = value

    @property
    def data_string_spaced(self):
        return ' '.join('{:02X}'.format(b) for b in self.data)

    @property
    def length(self):
        return len(self.data)


def to_trc_v1_1_s(messages):
    s = io.StringIO()

    to_trc_v1_1(messages, s)

    s.seek(0)

    return s.read()


def to_trc_v1_1(messages, f):
    header = textwrap.dedent('''\
        ;$FILEVERSION=1.1
        ;$STARTTIME={start_time}
        ;
        ;   {path}
        ;
        ;   Start time: {start_string}
        ;   Generated by EPyQ {version_string}
        ;
        ;   Message Number
        ;   |         Time Offset (ms)
        ;   |         |        Type
        ;   |         |        |        ID (hex)
        ;   |         |        |        |     Data Length
        ;   |         |        |        |     |   Data Bytes (hex) ...
        ;   |         |        |        |     |   |
        ;---+--   ----+----  --+--  ----+---  +  -+ -- -- -- -- -- -- --'''
    ).format(
        start_time=0,
        path=f.name,
        start_string='',
        version_string=''
    )

    format = '  '.join((
        '{i: 6d})',
        '{ms: 10.1f}',
        '{type:<5s}',
        '{id:08X}',
        '{length:1d}',
        '{data}',
    ))
    format += ' \n'

    for line in header.splitlines():
        f.write(line.rstrip() + '\n')

    for i, message in enumerate(messages, start=1):
        f.write(format.format(
            i=i,
            ms=message.ms,
            type=message.type.name,
            id=message.id.value,
            length=message.length,
            data=message.data_string_spaced,
        ))

def to_trc_v1_3(messages, bus):
    """`messages` should be a dict.  Keys are bus numbers and values are
    iterables of messages"""
    raise Exception('Not implemented')

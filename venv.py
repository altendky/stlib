#!/usr/bin/env python3

import argparse
import errno
import os
import platform
import shutil
import stat
import subprocess
import sys

if sys.platform not in ['win32', 'linux']:
    raise Exception("Unsupported sys.platform: {}".format(sys.platform))

# TODO: CAMPid 097541134161236179854863478319
try:
    import pip
except ImportError:
    print('')
    print('')
    print('    pip not installed:')
    print('')
    print('        Use your package manager to install')
    print('')
    print('        e.g. sudo apt-get install python3-pip')
    print('')

    sys.exit(1)


# http://stackoverflow.com/a/10840586/228539
def silentremove(filename):
    try:
        os.remove(filename)
    except OSError as e:  # this would be "except OSError, e:" before Python 2.6
        if e.errno != errno.ENOENT:  # errno.ENOENT = no such file or directory
            raise # re-raise exception if a different error occured


# TODO: CAMPid 0238493420143087667542054268097120437916848
# http://stackoverflow.com/a/21263493/228539
def del_rw(action, name, exc):
    os.chmod(name, stat.S_IWRITE)
    if os.path.isdir(name):
        os.rmdir(name)
    else:
        os.remove(name)


files = ['activate', 'activate.bat', 'activate.ps1',
         'designer', 'designer.bat', 'designer.ps1', 'designer.vbs']

for file in files:
    silentremove(file)

try:
    shutil.rmtree('venv', onerror=del_rw)
except FileNotFoundError:
    pass


try:
    import tox
except ModuleNotFoundError:
    subprocess.run(
        [
            sys.executable,
            '-m',
            'pip',
            'install',
            '--user',
            'tox',
        ],
        check=True
    )
    print()
    print(' - - - Tox installed, please rerun this program - - -')
    print()
    sys.exit(0)

tox_arguments = [
    '-e', 'devenv',
    *sys.argv[1:]
]

config = tox.session.prepare(tox_arguments)
tox.session.Session(config).runcommand()


if sys.platform == 'win32':
    import ctypes
    try:
        ctypes.windll.LoadLibrary("PCANBasic")
    except OSError:
        print('')
        print('')
        print('    Unable to load PCANBasic.dll, it is recommended you')
        print('    install the PEAK drivers: ')
        print('')
        print('        http://www.peak-system.com/produktcd/Drivers/PeakOemDrv.exe')

    print('')
    print('')
    print('    I have not been programmed to detect it but you may need the right')
    print('    redist (x86 or x64) to match your Python install:')
    print('')
    print('        https://www.microsoft.com/en-us/download/details.aspx?id=52685')
    print('')
    print('    Similarly:')
    print('')
    print('        https://www.microsoft.com/en-us/download/details.aspx?id=8328')
    print


print('')
print('')
print('    To use the new virtualenv:')
print('')
print('        posix: source activate')
if sys.platform == 'win32':
    print('        win32: activate')
print('')

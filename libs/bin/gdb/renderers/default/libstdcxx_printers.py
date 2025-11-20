# Copyright 2000-2018 JetBrains s.r.o.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
# http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

import gdb

import re
import default

class StdChronoDurationPrinter:
    orig_to_string = None

    def to_string(self):
        s = StdChronoDurationPrinter.orig_to_string(self)
        return strip(s, 'std::chrono::duration = {', '}')


class StdChronoTimePointPrinter:
    prefix = re.compile(r'std::chrono::(?:\w+|[^{}=]+ time_point) = \{')
    orig_to_string = None

    def to_string(self, abbrev = None):
        if abbrev is not None:
            return StdChronoTimePointPrinter.orig_to_string(self, abbrev)
        s = StdChronoTimePointPrinter.orig_to_string(self)
        return strip(s, StdChronoTimePointPrinter.prefix, '}')


class StdChronoZonedTimePrinter:
    orig_to_string = None

    def to_string(self):
        s = StdChronoZonedTimePrinter.orig_to_string(self)
        return strip(s, 'std::chrono::zoned_time = {', '}')


class StdChronoTimeZonePrinter:
    orig_to_string = None

    def to_string(self):
        s = StdChronoTimeZonePrinter.orig_to_string(self)
        return strip(strip(s, self.typename), '=')


class StdChronoLeapSecondPrinter:
    orig_to_string = None

    def to_string(self):
        s = StdChronoLeapSecondPrinter.orig_to_string(self)
        return strip(strip(s, self.typename), '=')


class StdChronoTzdbPrinter:
    orig_to_string = None

    def to_string(self):
        s = StdChronoTzdbPrinter.orig_to_string(self)
        return strip(strip(s, self.typename), '=')


def strip(s, prefix, suffix=''):
    if isinstance(prefix, re.Pattern):
        match = prefix.match(s)
        start_matches = match is not None
        if start_matches:
            prefix = match.group()
    else:
        start_matches = s.startswith(prefix)

    if start_matches and s.endswith(suffix):
        return s[len(prefix):len(s)-len(suffix)].strip()
    return s


class CidrStdArrayPrinter(default.printers.printer_base):

    """Prints std::array variables"""

    def __init__(self, typename, val):
        self._val = val
        try:
            self._elems = self._val['_M_elems']
        except:
            self._elems = None

    def children(self):
        if self._elems is not None:
            return default.printers.ArrayPrinter(self._elems, default.printers.untypedef(self._elems.type)).children()
        else:
            return default.printers.CidrNoOpStructPrinter(self._val.type.strip_typedefs(), self._val).children()

    def display_hint(self):
        return 'array'

class CidrStdValarrayPrinter(default.printers.printer_base):

    """Prints std::valarray variables"""

    class _iterator:
        def __init__(self, elems, size):
            self._elems = elems
            self._size = size
            self._index = 0

        def __iter__(self):
            return self

        def __next__(self):
            index = self._index
            if index >= self._size:
                raise StopIteration
            self._index = self._index + 1
            elem = self._elems[index]
            return ('[%d]' % index, elem)

    def __init__(self, typename, val):
        self._val = val
        try:
            self._data = self._val['_M_data']
            self._size = self._val['_M_size']
            print("ok")
        except:
            print("err")
            self._data = None
            self._size = None

    def children(self):
        if self._data is not None and self._size is not None:
            return self._iterator(self._data, self._size)
        else:
            return default.printers.CidrNoOpStructPrinter(self._val.type.strip_typedefs(), self._val).children()

    def to_string(self):
        if self._size is not None:
            return "size %d" % self._size
        else:
            return ""

    def display_hint(self):
        return 'array'

def patch_libstdcxx_printers_module():
    from libstdcxx.v6 import printers

    for patched_type in (StdChronoDurationPrinter,
                         StdChronoTimePointPrinter,
                         StdChronoZonedTimePrinter,
                         StdChronoTimeZonePrinter,
                         StdChronoLeapSecondPrinter,
                         StdChronoTzdbPrinter):
        name = patched_type.__name__
        orig_type = getattr(printers, name, None)
        if orig_type is not None:
            patched_type.orig_to_string = orig_type.to_string
            orig_type.to_string = patched_type.to_string

    cidr_libstdcxx = printers.Printer("cidr-libstdc++-v6")
    cidr_libstdcxx.add_container('std::', 'array', CidrStdArrayPrinter)
    cidr_libstdcxx.add_container('std::', 'valarray', CidrStdValarrayPrinter)
    gdb.pretty_printers.append(cidr_libstdcxx)

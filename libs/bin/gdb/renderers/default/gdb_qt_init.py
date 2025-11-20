import sys
import gdb
import traceback

def init_qt_support():
    if sys.version_info[0] == 2:
        print("Qt renderers require python3, Qt support will not be enabled")
        return

    try:
        from dumper import ReportItem
        from gdbbridge import Dumper
    except ImportError:
        print("Failed to find Qt gdbbridge, Qt support will not be enabled")
        return

    try:
        from default.gdb_qt import cidrDumper, qtMatcher

        cidrDumper.loadDumpers({})
        # int8_t and uint8_t dumpers change the -data-list-register-values output
        # which affects vector registers presentation. Disable them:
        del cidrDumper.qqDumpers['int8_t']
        del cidrDumper.qqDumpers['uint8_t']

        args = {
            'fancy': 1,
            'passexceptions': 1,
            'autoderef': 1,
            'qobjectnames': 1,
            'allowinferiorcalls': 1,
        }
        cidrDumper.prepare(args)

        enable_array_elements_reporting(cidrDumper)

        # We don't use gdb.printing.register_pretty_printer() here because it will
        # put qt matcher in front of std printers and this will change std types
        # presentation.
        gdb.pretty_printers.append(qtMatcher)

        print("Qt support was enabled")
    except Exception as e:
        print('Failed to enable Qt support')
        traceback.print_exc()


def enable_array_elements_reporting(dumper):
    """
    Enables individual array element reporting instead of
    dumping array memory. See enc logic in dumper.putArrayData().
    """

    dumper.type_encoding_cache = {}

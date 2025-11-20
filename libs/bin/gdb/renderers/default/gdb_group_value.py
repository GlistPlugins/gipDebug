# Group value support for gdb, see CidrGroupValue.kt for details

import gdb
import traceback

# _group_value_printers holds printers for group values.
# A printer is identified by its position in this list.
_group_value_printers = []

# _group_value_type is a type we use to recognize group values.
# Group value is a printer_id casted to this type.
_group_value_type = 'void **********'


def is_group_value_type(typ):
    return typ is not None and str(typ) == _group_value_type


def get_group_value_name(name):
    """
    Returns a name for a group value recognized by the IDE
    """

    return '__jetbrains_group_value:{}'.format(name)


def create_group_value(printer):
    """
    Creates a new group value with the given printer
    """

    printer_id = len(_group_value_printers)
    _group_value_printers.append(printer)
    typ = gdb.lookup_type(_group_value_type)
    result = gdb.Value(printer_id).cast(typ)
    return result


def _clear_group_value_printers():
    _group_value_printers[:] = []


def _group_value_printer_matcher(val):
    """
    Returns a printer for group values or None of the val is not a group value
    """

    if is_group_value_type(val.type):
        printer_id = int(val)
        if printer_id < len(_group_value_printers):
            printer = _group_value_printers[printer_id]
            return printer
        else:
            print('Group value printer_id is out of bounds: printer_id={}, upper_bound={}'.format(printer_id, len(_group_value_printers)))
    return None


def init_group_value_support(obj, use_gdb_printing):
    try:
        # Clear group value printers every time inferior stops (is suspended).
        # Group value printers will be re-added during variable children calculation.
        gdb.events.stop.connect(lambda event: _clear_group_value_printers())

        if use_gdb_printing:
            gdb.printing.register_pretty_printer(obj, _group_value_printer_matcher)
        else:
            if obj is None:
                obj = gdb
            obj.pretty_printers.append(_group_value_printer_matcher)
    except:
        print('Failed to initialize group values support')
        traceback.print_exc()

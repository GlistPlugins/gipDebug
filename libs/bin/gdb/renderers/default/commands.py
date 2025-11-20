import gdb
import gdb.types
import traceback
import default

from default.gdb_group_value import is_group_value_type

try:
    # Need gdb.printing to run pretty printers.
    # IDE calls register_commands only in gdb 14+ where gdb.printing is available.
    # If this import fails, then we are in some earlier gdb version and
    # the commands won't be registered, so we can ignore the failure.
    import gdb.printing
except ImportError:
    pass

# _cidr_vars contains CidrVars.
# Index in the list serves as cidr_id.
_cidr_vars = []

class CidrMiListFrameVariables(gdb.MICommand):
    """Lists variables in the current thread and frame"""

    frame_var_address_classes = {
        gdb.SYMBOL_LOC_ARG,
        gdb.SYMBOL_LOC_REF_ARG,
        gdb.SYMBOL_LOC_REGPARM_ADDR,
        gdb.SYMBOL_LOC_LOCAL,
        gdb.SYMBOL_LOC_STATIC,
        gdb.SYMBOL_LOC_REGISTER,
        gdb.SYMBOL_LOC_COMPUTED,
    }


    def __init__(self):
        super(CidrMiListFrameVariables, self).__init__("-cidr-stack-list-frame-variables")

    def invoke(self, argv):
        # the first argument == '1' means filter out-of-scope variables
        # if the argument is not present, filtering is disabled
        filter = False
        if len(argv) > 0:
            filter = argv[0] == '1'

        frame = gdb.selected_frame()
        if not frame.is_valid():
            return {'variables' : [], 'filtered' : False}

        try:
            block = frame.block()
        except RuntimeError as e: # raised when block is not found
            block = None

        if filter:
            try:
                sal = frame.find_sal()
                if sal.symtab is None:
                    filter = False
                else:
                    frame_line = sal.line
                    frame_file = sal.symtab.filename
            except gdb.error as e:
                filter = False

        blocks_inside_out = [] # lists of block variables from the most to the least nested block
        # to check standard gdb/mi -stack-list-frame-variables see list_args_or_locals in mi-cmd-stack.c
        # clashing variable names are handled by the client

        # We try to filter out-of-scope variables using lines from debug info.
        # This is not always possible. A variable can have an invalid line (== 0)
        # or a file different from the frame's file, so we cannot compare lines.
        # The filtered variable below is True if we were asked to filter and
        # filtering was reliable (all symbols have valid lines from the frame's file).
        # If we failed to filter reliably, we report that in response and the IDE asks
        # a language engine to refine the result.
        filtered = filter
        is_cpp = frame.language() == 'c++'
        while block is not None and block.is_valid():
            block_vars = []
            blocks_inside_out.append(block_vars)
            for sym in block:
                if not sym.is_valid():
                    continue
                if sym.addr_class not in CidrMiListFrameVariables.frame_var_address_classes:
                    continue
                if not sym.is_variable and not sym.is_argument:
                    continue
                if filter:
                    symtab = sym.symtab
                    # Line is usually 1-based, it is 0 when the corresponding
                    # dwarf attribute is missing (see new_symbol() in gdb/dwarf2/read.c).
                    if is_cpp and sym.name == 'this':
                        # The 'this' symbol usually has 0 line and sometimes a file different from the frame's file.
                        # Accept it without setting filtered to False to avoid filtering using the language engine
                        # only because of 'this'.
                        pass
                    elif symtab is None or sym.line == 0 or symtab.filename != frame_file:
                        if is_cpp and is_range_loop_synthetic_var(sym):
                            continue
                        filtered = False
                    elif sym.line >= frame_line:
                        continue
                var = {'name' : sym.name}
                block_vars.append(var)
            if block.function is not None:
                break
            block = block.superblock

        result = []
        for block_vars in reversed(blocks_inside_out):
            result.extend(block_vars)

        return {'variables' : result, 'filtered' : filtered}


_range_loop_synthetic_vars = {'__for_range', '__for_begin', '__for_end'}

def is_range_loop_synthetic_var(sym):
    return sym.line == 0 and sym.name in _range_loop_synthetic_vars


class CidrMiVarCreate(gdb.MICommand):
    """
    Wrapper around gdb/mi -var-create command.
    Uses the gdb.execute_mi api which was added in gdb 14.
    """

    _run = None

    def __init__(self):
        super(CidrMiVarCreate, self).__init__("-cidr-mi-var-create")

    def invoke(self, argv):
        try:
            return CidrMiVarCreate._run(lambda: gdb.execute_mi("-var-create", *argv))
        except Exception as e:
            # wrap into gdb.GdbError to drop the 'Error occurred in Python:' prefix in error messages
            # and to disable python traceback printing
            # https://sourceware.org/gdb/current/onlinedocs/gdb.html/Exception-Handling.html#Exception-Handling
            raise gdb.GdbError(str(e))


class CidrIterator:
    """Iterator with an ability to check if it has more items"""

    # Custom gdb/mi python commands work only in gdb 14+ which supports only python3,
    # so we don't try to be compatible with python2 (this would require calling iter.next()
    # instead of iter.__next__().

    def __init__(self, child_iter):
        self._iter = iter(child_iter)
        self._count = 0 # number of items read consumed
        self._saved = None

    def next(self):
        self._count = self._count + 1
        if self._saved is not None:
            result = self._saved
            self._saved = None
            return result
        return self._iter.__next__()

    def has_more(self):
        if self._saved is not None:
            return True
        try:
            self._saved = self._iter.__next__()
        except StopIteration:
            return False
        return True


class CidrVar:
    """CidrVar holds gdb.Value, the expression it was created for, and other data necessary for IDE. Similar to gdb/mi variable objects"""

    def __init__(self, exp, val, children_val, lang, parent_cidr_var, raw):
        self._exp = exp # expression for which var is created; in case of child variables - the name of the child
        self._val = val # gdb.Value
        self._children_val = children_val # gdb.Value for listing children, different from val e.g. for pointers to structs/unions
        self._lang = lang # language of the variable, frame vars get it from frame, children inherit it from the parent.
        self._parent_cidr_var = parent_cidr_var # parent CidrVar or None for top-level vars
        self._iter = None # children iterator
        self._raw = raw # if True, the default pretty printer was disabled and we show raw presentation


class CidrVarCreate(gdb.MICommand):
    """CidrVarCreate creates a new CidrVar for the given expression, similar to gdb/mi -var-create"""

    _to_string_runner = None

    def __init__(self):
        super(CidrVarCreate, self).__init__("-cidr-var-create")

    def invoke(self, argv):
        # usage: -cidr-var-create <expression> [--raw]

        if len(argv) < 1:
            raise gdb.GdbError("Wrong number of arguments, expected 1, got %d" % len(argv))

        raw = False
        if len(argv) == 2:
            raw = argv[1] == "--raw"

        exp = argv[0]

        try:
            val = gdb.parse_and_eval(exp)
        except Exception as e:
            raise gdb.GdbError(str(e))

        frame = gdb.selected_frame()
        lang = frame.language() if frame.is_valid() else None
        return cidr_make_var_with_fallback(val, None, exp, lang, raw=raw)


class CidrVarListChildren(gdb.MICommand):
    """CidrVarListChildren lists children of the CidrVar with the given id, similar to gdb/mi -var-list-children"""

    _to_string_runner = None

    def __init__(self):
        super(CidrVarListChildren, self).__init__("-cidr-var-list-children")

    def invoke(self, argv):
        # usage: -cidr-var-list-children <var_id> <from0> <to0> [--raw "child_name"+]

        if len(argv) < 3:
            raise gdb.GdbError("Wrong number of arguments, expected 3, got %d" % len(argv))


        try:
            var_id = int(argv[0])
        except:
            raise gdb.GdbError("Failed to parse var id '%s'" % argv[0])

        if var_id < 0 or var_id >= len(_cidr_vars):
            raise gdb.GdbError("Var id is out of bounds: %d" % var_id)

        try:
            from0 = int(argv[1])
        except:
            raise gdb.GdbError("Failed to parse from: '%s'" % argv[1])

        try:
            to0 = int(argv[2])
        except:
            raise gdb.GdbError("Failed to parse to: '%s'" % argv[2])

        if from0 > to0:
            raise gdb.GdbError("Wrong bounds: from=%d, to=%d" % (from0, to0))

        raw_children = {}
        if len(argv) > 3 and argv[3] == '--raw':
            for i in range(4, len(argv)):
                raw_children[argv[i]] = True

        cidr_var = _cidr_vars[var_id]

        return cidr_list_children_with_fallback(cidr_var, from0, to0, raw_children=raw_children)

def cidr_list_children_with_fallback(cidr_var, from0, to0, raw_children={}):
    raw = cidr_var._raw
    try:
        return cidr_list_children(cidr_var, from0, to0, raw, raw_children)
    except Exception as e:
        if raw:
            raise e
        print("Error while running pretty printer")
        traceback.print_exc()
        print("Fallback to raw presentation")
        result = cidr_list_children(cidr_var, from0, to0, True, raw_children)
        result['cidr-default-printer-failed'] = 1
        return result


def cidr_list_children(cidr_var, from0, to0, raw=False, raw_children={}):
    report_all = from0 < 0 or to0 < 0
    result = {}
    children = []
    has_more = False

    children_iter = cidr_var._iter
    if children_iter is None or children_iter._count > from0:
        pretty_printer, _ = get_printer(cidr_var._children_val, raw)
        if hasattr(pretty_printer, 'children'):
            children_iter = CidrIterator(pretty_printer.children())
            cidr_var._iter = children_iter

    if children_iter is None:
        result['children'] = children
        return result

    # fast forward to the requested from0 if needed
    try:
        while children_iter._count < from0:
            children_iter.next()
    except StopIteration:
        pass

    try:
        while report_all or from0 < to0:
            (name, val) = children_iter.next()
            if not isinstance(val, gdb.Value):
                val = gdb.Value(val)
            is_raw_child = name in raw_children
            child_var_dict = cidr_make_var_with_fallback(val, cidr_var, name, cidr_var._lang, raw=is_raw_child)
            children.append(child_var_dict)
            if not report_all:
                from0 = from0 + 1
        has_more = children_iter.has_more()
    except StopIteration:
        pass

    result['children'] = children
    result['has_more'] = has_more

    return result

def cidr_make_var_with_fallback(val, parent_cidr_var, exp, lang, register=True, raw=False):
    try:
        return cidr_make_var(val, parent_cidr_var, exp, lang, register, raw)
    except Exception as e:
        if raw:
            raise e
        print("Error while running pretty printer")
        traceback.print_exc()
        print("Fallback to raw presentation")
        var = cidr_make_var(val, parent_cidr_var, exp, lang, register, True)
        var['cidr-default-printer-failed'] = 1
        return var

def cidr_make_var(val, parent_cidr_var, exp, lang, register=True, raw=False):
    # -var-create output example:
    # <44^done,name="var1_points",numchild="0",value="{length 2, capacity 2}",type="std::vector<P, std::allocator<P> >",thread-id="1",displayhint="array",dynamic="1",has_more="1"
    # The thread-id is not used, so we don't report it.
    # Instead of name our var is identified by the cidr-id attribute.
    # We still report the name and set it to 'v<cidr-id>', but it is
    # not used to identify the variable.

    val_type = val.type
    val_type_no_typedefs = val_type.strip_typedefs()

    # pointers and references to structs/classes show a dynamic (runtime) type,
    # and use a pretty_printer for it instead of a static (declaration) type
    # see value_actual_type()
    if is_ptr_or_ref(val_type_no_typedefs) and val_type_no_typedefs.target().strip_typedefs().code == gdb.TYPE_CODE_STRUCT:
        val_type = val.dynamic_type
        val_type_no_typedefs = val_type.strip_typedefs()
        val = val.cast(val_type)

    pretty_printer, dynamic = get_printer(val, raw)
    # non-dynamic reference vars show referenced value
    # see c_value_of_variable()
    value_pretty_printer = pretty_printer
    if not dynamic and val_type_no_typedefs.code in (gdb.TYPE_CODE_REF, gdb.TYPE_CODE_RVALUE_REF):
        print_val_type = gdb.types.get_basic_type(val_type_no_typedefs)
        print_val = val.cast(print_val_type)
        value_pretty_printer, dynamic = get_printer(print_val, raw)
    elif not dynamic and val_type.code == gdb.TYPE_CODE_TYPEDEF:
        no_typedef_printer, no_typedef_printer_found = get_printer(val.cast(val_type_no_typedefs), raw)
        if no_typedef_printer_found:
            value_pretty_printer = no_typedef_printer
            dynamic = True

    # When printing children of a pointer to a struct/union we want to
    # remove one layer of indirection and show fields of the struct/union.
    # Children_val will contain the value from which to read children and numchild.
    # See adjust_value_for_child_access()
    children_val_basic_type = gdb.types.get_basic_type(val_type) # strips typedefs and refs
    children_val = val.cast(children_val_basic_type)
    children_val_pretty_printer, children_val_dynamic = get_printer(children_val, raw)
    if children_val_basic_type.code == gdb.TYPE_CODE_PTR and children_val != 0:
        target_type = gdb.types.get_basic_type(children_val_basic_type.target())
        if target_type.code in (gdb.TYPE_CODE_STRUCT, gdb.TYPE_CODE_UNION):
            referenced_value = children_val.referenced_value()
            children_val = referenced_value
            children_val_pretty_printer, children_val_dynamic = get_printer(children_val, raw)

    dynamic |= children_val_dynamic

    var_id = len(_cidr_vars)
    cidr_var = CidrVar(exp, val, children_val, lang, parent_cidr_var, raw)
    if register:
        _cidr_vars.append(cidr_var)

    # adjust expression for a child of a pointer but preserve expressions of group value children
    # see c_describe_child()
    if parent_cidr_var is not None:
        children_val_type = parent_cidr_var._children_val.type
        ty = children_val_type.strip_typedefs()
        if ty.code == gdb.TYPE_CODE_PTR and not is_group_value_type(children_val_type):
            exp = "*%s" % parent_cidr_var._exp

    result = {'cidr-id': var_id, 'name': 'v%d' % var_id, 'exp': exp}

    # dynamic
    if dynamic:
        result['dynamic'] = True

    # value
    value = None
    if hasattr(value_pretty_printer, 'to_string'):
        value = CidrVarCreate._to_string_runner(value_pretty_printer.to_string)
        if val_type_no_typedefs.code == gdb.TYPE_CODE_PTR and not raw and isinstance(value_pretty_printer, default.printers.PointerPrinter):
            ptr_target_printer, ptr_target_dynamic = get_printer(val.referenced_value(), raw)
            if ptr_target_dynamic and hasattr(ptr_target_printer, 'to_string'):
                ptr_target_value = CidrVarCreate._to_string_runner(ptr_target_printer.to_string)
                value = "%s %s" % (value, ptr_target_value)
    elif hasattr(children_val_pretty_printer, 'children'):
        # see varobj_value_get_print_value()
        value = "{...}"
    else:
        value = ""

    if lang == "c" or lang == "c++":
        if isinstance(value, str) and value == "" and (isinstance(value_pretty_printer, gdb.printing.NoOpStructPrinter) or isinstance(value_pretty_printer, default.printers.CidrNoOpStructPrinter)):
           # gdb/mi returns {...} for structs without a printer or when printer.to_string() returns an empty string
           # see my_value_of_variable(), c_value_of_variable()
           value = "{...}"
        elif isinstance(value, bool):
            # Python booleans are converted to "0" and "1" in output.
            # Gdb/mi shows language-specific true/false values.
            # see varobj_value_get_print_value(), c_value_print_inner(), generic_value_print(), generic_value_print_bool()
            if value:
                value = "true"
            else:
                value = "false"

    if value is not None:
        result['value'] = value

    # type
    if val_type is not None:
        result['type'] = val_type

    #displayhint
    if hasattr(value_pretty_printer, 'display_hint'):
        try:
            display_hint = value_pretty_printer.display_hint()
        except:
            display_hint = None
        if display_hint is not None:
            result['displayhint'] = display_hint

    # numchild
    # For dynamic vars gdb/mi relies on printer's children/num_children methods, see update_dynamic_varobj_children().
    # For non-dynamic vars numchild is computed in c code, see c_number_of_children()/cplus_number_of_children().
    # Gdb/mi docs says that numchild is not reliable for dynamic vars, and clients should look into has has_more, so
    # we don't compute numchild for dynamic vars.
    #
    # We could use pretty_printer's children/num_children in all cases because nop printers seem to repeat
    # the c code logic (e.g. return fields for structs).
    # One exception to this is 'void *' and 'func *', which is done in c_number_of_children().
    # We handle it first and run everything else if this logic wasn't executed.
    if not dynamic:
        numchild = None
        ty = children_val.type.strip_typedefs()
        if ty.code == gdb.TYPE_CODE_PTR:
            target_type = ty.target().strip_typedefs()
            if target_type.code in (gdb.TYPE_CODE_FUNC, gdb.TYPE_CODE_VOID): # see c_number_of_children()
                numchild = 0
            else:
                numchild = 1

        if numchild is None:
            if isinstance(children_val_pretty_printer, gdb.ValuePrinter) and hasattr(children_val_pretty_printer, 'num_children'):
                numchild = children_val_pretty_printer.num_children()
            elif hasattr(children_val_pretty_printer, 'children'):
                numchild = 0
                for child in children_val_pretty_printer.children():
                    numchild = numchild + 1
            else:
                numchild = 0

        if numchild is not None:
            result['numchild'] = numchild

    # has_more
    # compute only for dynamic vars, for non-dynamic vars clients should only use the numchild attribute
    if dynamic and hasattr(children_val_pretty_printer, 'children'):
        has_more = False
        for child in children_val_pretty_printer.children():
            has_more = True
            break
        result['has_more'] = has_more

    return result


class CidrVarDeleteAll(gdb.MICommand):
    """Clears all CidrVars, similar to gdb/mi -var-delete, but clears all variables """

    def __init__(self):
        super(CidrVarDeleteAll, self).__init__("-cidr-var-delete-all")

    def invoke(self, argv):
        # usage: -cidr-var-delete-all

        _cidr_vars.clear()


class CidrVarRead(gdb.MICommand):
    """Reads details of an existing cidr variable, returns the same result as -cidr-var-create"""

    def __init__(self):
        super(CidrVarRead, self).__init__("-cidr-var-read")

    def invoke(self, argv):
        # usage: -cidr-var-read <var_id>

        if len(argv) != 1:
            raise gdb.GdbError("Wrong number of arguments, expected 1, got %d" % len(argv))

        try:
            var_id = int(argv[0])
        except:
            raise gdb.GdbError("Failed to parse var-id '%s'" % argv[0])

        if var_id < 0 or var_id >= len(_cidr_vars):
            raise gdb.GdbError("Var-id is out of bounds: %d" % var_id)

        cidr_var = _cidr_vars[var_id]
        result = cidr_make_var_with_fallback(cidr_var._val, cidr_var._parent_cidr_var, cidr_var._exp, cidr_var._lang, False, raw=cidr_var._raw)
        result['cidr-id'] = "%d" % var_id
        result['name'] = "v%d" % var_id

        return result

class CidrVarSetRaw(gdb.MICommand):
    """Sets the raw flag for cidr variable"""

    def __init__(self):
        super(CidrVarSetRaw, self).__init__("-cidr-var-set-raw")

    def invoke(self, argv):
        # usage: -cidr-var-set-raw <var_id> <1 or 0>

        if len(argv) < 2:
            raise gdb.GdbError("Wrong number of arguments, expected 2, got %d" % len(argv))

        try:
            var_id = int(argv[0])
        except:
            raise gdb.GdbError("Failed to parse var-id '%s'" % argv[0])

        if var_id < 0 or var_id >= len(_cidr_vars):
            raise gdb.GdbError("Var-id is out of bounds: %d" % var_id)

        raw = argv[1] == '1'
        cidr_var = _cidr_vars[var_id]
        cidr_var._raw = raw
        return None

def is_ptr_or_ref(val_type):
    return val_type is not None and val_type.code in (gdb.TYPE_CODE_REF, gdb.TYPE_CODE_RVALUE_REF, gdb.TYPE_CODE_PTR)

def get_printer(val, raw=False):
    """
    Returns a tuple: pretty-printer for the given gdb.Value and a flag indicating whether the printer is the default val's printer.
    If raw is True doesn't attempt to create a printer for the value and always returns the nop printer.
    """

    default_printer = None
    if not raw:
        default_printer = gdb.default_visualizer(val)
    if default_printer is not None:
        return (default_printer, True)
    return (make_nop_visualizer(val), False)

def make_nop_visualizer(value):
    # see fallback part of the make_visualizer() in gdb printing.py
    result = None
    ty = value.type.strip_typedefs()
    if ty.is_string_like:
        result = gdb.printing.NoOpScalarPrinter(value)
    elif ty.code == gdb.TYPE_CODE_ARRAY:
        result = gdb.printing.NoOpArrayPrinter(ty, value)
    elif ty.is_array_like:
        value = value.to_array()
        ty = value.type.strip_typedefs()
        result = gdb.printing.NoOpArrayPrinter(ty, value)
    elif ty.code in (gdb.TYPE_CODE_STRUCT, gdb.TYPE_CODE_UNION):
        result = default.printers.CidrNoOpStructPrinter(ty, value)
    elif ty.code in (
        gdb.TYPE_CODE_PTR,
        gdb.TYPE_CODE_REF,
        gdb.TYPE_CODE_RVALUE_REF,
    ):
        result = gdb.printing.NoOpPointerReferencePrinter(value)
    else:
        result = gdb.printing.NoOpScalarPrinter(value)
    return result

# _cidr_finish_breakpoints contains temporary breakpoints created by
# -cidr-exec-step and -cidr-exec-next commands.
# We need to track and delete breakpoints manually because they are
# not removed automatically if not reached.
# Cleanup happens every time the program stops.
_cidr_finish_breakpoints = []

def _clear_finish_breakpoints(event):
    for bp in _cidr_finish_breakpoints:
        if bp.is_valid():
            bp.delete()
    _cidr_finish_breakpoints.clear()


def _find_frame_for_finish_breakpoint():
    """
    Returns a frame for which the caller frame has sources
    or None if no such frame found.
    """
    frame = gdb.newest_frame()
    while frame is not None and frame.is_valid():
        callerFrame = frame.older()
        if callerFrame is not None and callerFrame.is_valid():
            sal = callerFrame.find_sal()
            if sal.is_valid() and sal.symtab is not None:
                return frame
        frame = callerFrame
    return None


class CidrExecNextCommand(gdb.MICommand):
    """
    CidrExecNextCommand is similar to the -exec-next gdb/mi command,
    but also stops on exit from a function like -exec-finish does.
    """

    def __init__(self):
        super(CidrExecNextCommand, self).__init__("-cidr-exec-next")

    def invoke(self, argv):
        frame = _find_frame_for_finish_breakpoint()
        if frame is not None:
            _cidr_finish_breakpoints.append(gdb.FinishBreakpoint(frame, internal=True))
        gdb.execute_mi("-exec-next")
        return None


class CidrExecStepCommand(gdb.MICommand):
    """
    CidrExecStepCommand is similar to the -exec-step gdb/mi command,
    but also stops on exit from a function like -exec-finish does.
    """

    def __init__(self):
        super(CidrExecStepCommand, self).__init__("-cidr-exec-step")

    def invoke(self, argv):
        frame = _find_frame_for_finish_breakpoint()
        if frame is not None:
            _cidr_finish_breakpoints.append(gdb.FinishBreakpoint(frame, internal=True))
        gdb.execute_mi("-exec-step")
        return None


# note that register_commands will fail for old gdb versions not supporting gdb.MICommand
# it is responsibility of the caller to check the version
def register_commands():
    CidrMiListFrameVariables()
    try:
        from libstdcxx.v6.printers import cidr_run_with_omitting_types_inside_to_string as run
    except ImportError:
        # libstdcxx doesn't support omitting types in to_string
        run = lambda fn: fn()
    CidrMiVarCreate._run = run
    CidrMiVarCreate()
    CidrVarCreate._to_string_runner = run
    CidrVarCreate()
    CidrVarListChildren._to_string_runner = run
    CidrVarListChildren()
    CidrVarDeleteAll()
    CidrVarRead()
    CidrVarSetRaw()

    CidrExecNextCommand()
    CidrExecStepCommand()
    gdb.events.stop.connect(_clear_finish_breakpoints)

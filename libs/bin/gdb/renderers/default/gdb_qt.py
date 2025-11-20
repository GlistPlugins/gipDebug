import gdb

from dumper import ReportItem, TopLevelItem
from gdbbridge import Dumper
from default.gdb_group_value import get_group_value_name, create_group_value

class CidrQNode:
    def __init__(self, parent_node, is_group_node):
        self.parent_node = parent_node
        self.is_group_node = is_group_node
        self.name = None
        self.value = None
        self.report_item = None # dumper.ReportItem
        self.children = []
        self.key = None
        self.key_encoding = None
        self.key_prefix = ''

    def set_name(self, name):
        if name is None:
            return

        if isinstance(name, int):
            self.name = f'[{name}]'
        else:
            self.name = str(name)


    def get_name(self):
        name = self.name if self.name is not None else ''
        if not self.is_group_node:
            return name
        if self.key is not None:
            name = f'{self.key_prefix}{decode_value(self.key, self.key_encoding)}'
            if self.report_item is not None and self.report_item.value is not None:
                name += ' = '
                name += decode_value(self.report_item.value, self.report_item.encoding)

        return get_group_value_name(name)

    def to_string(self):
        item = self.report_item
        if not isinstance(item, ReportItem):
            return None
        return decode_value(item.value, item.encoding)

    def to_gdb_value(self):
        if self.is_group_node:
            return create_group_value(CidrQtGroupValuePrinter(self.children))

        if self.value is not None:
            nativeValue = self.value.nativeValue
            if nativeValue is not None:
                return nativeValue
            if self.value.laddress is not None and self.value.type is not None and self.value.type.name is not None:
                expr = f'*(({self.value.type.name} *) {self.value.laddress})'
                try:
                    return gdb.parse_and_eval(expr)
                except Exception as e:
                    print(f'Failed to evaluate expression "{expr}"')
                    raise e

        return ''

    def __repr__(self):
        return (
            f"CidrQNode(name={self.name}, value={self.value}, "
            f"is_group_node={self.is_group_node}, key={self.key}, "
            f"key_encoding={self.key_encoding}, key_prefix={self.key_prefix})"
        )

text_encodings = {'utf16', 'utf8', 'latin1'}
special_encodings = {'empty', 'undefined', 'null', 'notaccessible', 'optimizedout', 'nullreference', 'emptystructure', 'uninitialized', 'invalid', 'notcallable', 'outofscope'}

def decode_value(v, encoding=None):
    debugLog(f'decode_value({v}, {encoding})')
    if not isinstance(encoding, str):
        return str(v)
    if encoding == 'itemcount':
        return f'<{v} items>'
    if encoding == 'minimumitemcount':
        return f'<at least {v} items>'
    if encoding in text_encodings:
        try:
            decodedValue = Dumper.hexdecode(v, encoding)
            return f'"{decodedValue}"'
        except:
            pass
    if encoding in special_encodings:
        return f'<{encoding}>'
    return f'<{v}, encoding={encoding}>'


class CidrQtGroupValuePrinter(object):
    def __init__(self, children): # children are CidrQNodes
        self._child_nodes = children

    def children(self):
        for child in self._child_nodes:
            name = child.get_name()
            val = child.to_gdb_value()
            yield (name, val)

class CidrDumper(Dumper):

    def __init__(self):
        Dumper.__init__(self)
        self.__node = None
        self.__is_group_node = False
        self.cidr_dumper_enabled = True
        self.cidr_indent = 0

    def getCidrNode(self):
        return self.__node

    def putPairItem(self, index, pair, keyName='first', valueName='second'):
        dumperLog(self, 'putPairItem')
        self.__is_group_node = True
        super().putPairItem(index, pair, keyName, valueName)

    def enterSubItem(self, item):
        self.__node = CidrQNode(self.__node, self.__is_group_node)
        self.__node.set_name(item.name)
        self.__is_group_node = False

        dumperLog(self, f'enterSubItem')
        self.cidr_indent += 1
        super().enterSubItem(item)

    def exitSubItem(self, item, exType, exValue, exTraceBack):
        self.__node.report_item = self.currentValue
        super().exitSubItem(item, exType, exValue, exTraceBack)

        complete_node = self.__node
        self.__node = complete_node.parent_node
        if self.__node is not None:
            self.__node.children.append(complete_node)

        self.cidr_indent -= 1
        dumperLog(self, 'exitSubItem')

    def putField(self, name, value):
        dumperLog(self, f'putField(name={name}, value={value})')

        if name == 'name':
            self.__node.set_name(value)

        # see putPairContents() for key-related handling
        if name == 'key':
            self.__node.key = value
        elif name == 'keyencoded':
            self.__node.key_encoding = value
        elif name == 'keyprefix':
            self.__node.key_prefix = value

        super().putField(name, value)

    def putItem(self, value):
        self.__node.value = value
        super().putItem(value)

    def putValue(self, value, encoding=None, priority=0, length=None):
        # override for logging only
        dumperLog(self, f'putValue(value={value}, encoding={encoding}, priority={priority}, length={length})')
        super().putValue(value, encoding, priority, length)

    def childRange(self):
        result = range(self.currentNumChild)
        dumperLog(self, f'childRange() = {result}')
        return result

    def showException(self, msg, exType, exValue, exTraceback):
        super().showException(msg, exType, exValue, exTraceback)
        raise exValue # raise error to show the warning in the variables view

    def fromNativeValue(self, nativeValue):
        global _qtMatcherEnabled
        self.cidr_dumper_enabled = False
        try:
            return super().fromNativeValue(nativeValue)
        finally:
            self.cidr_dumper_enabled = True


class CidrQPrinter(object):

    def __init__(self, val):
        self.__val = val

    def to_string(self):
        dumper = cidrDumper

        qtVal = dumper.fromNativeValue(self.__val)
        node = None
        with TopLevelItem(dumper, ''):
            dumper.putItem(qtVal)
            node = dumper.getCidrNode()

        return node.to_string()

    def children(self):
        dumper = cidrDumper
        qtVal = dumper.fromNativeValue(self.__val)

        dumper.expandedINames = {'__cidr_printer__': 100}
        children = []
        with TopLevelItem(dumper, '__cidr_printer__'):
            dumper.putItem(qtVal)
            children = dumper.getCidrNode().children # take children before dumper.__node is reset to None on exit from TopLevelItem
        dumper.expandedINames = {}

        for child in children:
            name = child.get_name()
            val = child.to_gdb_value()
            yield (name, val)

def dumperLog(dumper, msg):
    debugLog(msg, dumper.cidr_indent)

log_enabled = False

def debugLog(msg, indent = 0):
    indentStr = "  " * indent
    if log_enabled:
        print(f'{indentStr}{msg}')

def qtMatcher(val):
    if val.type is None or val.type.name is None or not cidrDumper.cidr_dumper_enabled:
        return None

    # prepare type name like it is done in dumper.tryPutPrettyItem
    nsStrippedType = cidrDumper.stripNamespaceFromType(val.type.name).replace('::', '__')
    if nsStrippedType is None:
        return None

    # Strip leading 'struct' for C structs
    if nsStrippedType.startswith('struct '):
        nsStrippedType = nsStrippedType[7:]

    dumper = cidrDumper.qqDumpers.get(nsStrippedType)
    if dumper is not None:
        return CidrQPrinter(val)

    return None

cidrDumper = CidrDumper()

"""Map `OCC.Core.<Mod>` (pythonocc-core API) onto `OCP.<Mod>` (cadquery-ocp, OCCT 7.9.3).

Differences bridged: static methods named `X_s` in OCP are exposed as `X`;
lowercase helper classes (topods, brepgprop, ...) resolve case-insensitively;
str arguments are retried as TCollection_ExtendedString / AsciiString.
"""
import importlib, importlib.abc, importlib.util, inspect, sys, types

_patched: set = set()


def _retry(fn):
    def w(*args, **kw):
        try:
            return fn(*args, **kw)
        except TypeError:
            if not any(isinstance(a, str) for a in args):
                raise
            from OCP.TCollection import TCollection_AsciiString, TCollection_ExtendedString
            for conv in (TCollection_ExtendedString, TCollection_AsciiString):
                try:
                    return fn(*(conv(a) if isinstance(a, str) else a for a in args), **kw)
                except TypeError:
                    continue
            raise
    return w


class _Dual:
    """Class access -> static variant; instance access -> instance method (or static)."""

    def __init__(self, inst, stat):
        self.inst, self.stat = inst, stat

    def __get__(self, obj, objtype=None):
        if obj is None or self.inst is None:
            return self.stat
        bound = self.inst.__get__(obj, objtype)

        def call(*a, **k):
            try:
                return bound(*a, **k)
            except TypeError:
                return self.stat(*a, **k)
        return call


def _patch_class(cls):
    if cls in _patched:
        return
    _patched.add(cls)
    d = dict(vars(cls))
    for name, raw in d.items():
        if name.startswith("__"):
            continue
        if name.endswith("_s"):
            base = name[:-2]
            stat = _retry(getattr(cls, name))
            inst = d.get(base)
            inst_fn = None
            if inst is not None and not isinstance(inst, staticmethod):
                f = _retry(inst)
                inst_fn = f  # plain function -> binds as method
            try:
                setattr(cls, base, _Dual(inst_fn, stat))
            except Exception:
                pass
        elif callable(raw) and (name[:-2] if name.endswith("_s") else name) + "_s" not in d:
            try:
                setattr(cls, name, _retry(raw))
            except Exception:
                pass
    init = d.get("__init__")
    if init is not None:
        try:
            setattr(cls, "__init__", _retry(init))
        except Exception:
            pass


class _Mod(types.ModuleType):
    def __init__(self, name, ocp):
        super().__init__(name)
        self._ocp = ocp
        self._lower = {k.lower(): k for k in dir(ocp)}

    def __getattr__(self, attr):
        ocp = object.__getattribute__(self, "_ocp")
        if not hasattr(ocp, attr):
            key = object.__getattribute__(self, "_lower").get(attr.lower())
            if key is None:
                raise AttributeError(f"{self.__name__}.{attr}")
            attr = key
        return getattr(ocp, attr)


class _Finder(importlib.abc.MetaPathFinder, importlib.abc.Loader):
    def find_spec(self, fullname, path, target=None):
        if fullname.startswith("OCC.Core.") and fullname.count(".") == 2:
            return importlib.util.spec_from_loader(fullname, self)
        return None

    def create_module(self, spec):
        ocp = importlib.import_module("OCP." + spec.name.split(".")[-1])
        for k in dir(ocp):
            v = getattr(ocp, k)
            if inspect.isclass(v):
                _patch_class(v)
        return _Mod(spec.name, ocp)

    def exec_module(self, module):
        pass


sys.meta_path.insert(0, _Finder())


def _install_extras():
    """pythonocc SWIG conveniences missing from OCP."""
    from OCP.TDF import TDF_Label
    from OCP.TDataStd import TDataStd_Name

    def GetLabelName(self):
        attr = TDataStd_Name()
        if self.FindAttribute(TDataStd_Name.GetID_s(), attr):
            return attr.Get().ToExtString()
        return ""

    TDF_Label.GetLabelName = GetLabelName


_install_extras()

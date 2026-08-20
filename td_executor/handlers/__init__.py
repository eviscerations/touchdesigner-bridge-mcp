# Import every handler module so their @endpoint decorators register into server._REGISTRY.
# Adding a handler module = adding an import line here.
from td_executor.handlers import control      # noqa: F401
from td_executor.handlers import io           # noqa: F401
from td_executor.handlers import reference     # noqa: F401
from td_executor.handlers import diagnostics   # noqa: F401
from td_executor.handlers import animation      # noqa: F401
from td_executor.handlers import glsl           # noqa: F401
from td_executor.handlers import expr           # noqa: F401
from td_executor.handlers import scan           # noqa: F401
from td_executor.handlers import device         # noqa: F401

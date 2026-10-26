__version__ = "0.1.5"

import logging as _logging

# PyTorch logs a harmless notice on macOS at import ("Redirects are currently not supported in MacOs").
_logging.getLogger("torch.distributed.elastic.multiprocessing.redirects").setLevel(_logging.ERROR)

"""`qew project status`: compare the board with the ledger and report drift, never writing."""

from typing import Callable, Optional, Tuple

from qew import forge_registry
from qew.forge import Forge
from qew.ledger_config import Config, Mirror


def status(config: Config, wanted: Optional[str], as_json: bool,
           build: Callable[[Mirror], Forge] = forge_registry.build) -> Tuple[str, int]:
    return "", 0

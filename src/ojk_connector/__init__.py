"""ojk-connector: ingests OJK regulations, behind the team's source connector interface.

The orchestrator uses ``OJKConnector`` (see ``contract/CONNECTOR_INTERFACE.md``);
``ojk-connector`` on the command line runs the standalone pipeline.
"""

__version__ = "0.1.0"

from ojk_connector.connector import OJKConnector  # noqa: E402  (needs __version__)

__all__ = ["OJKConnector", "__version__"]

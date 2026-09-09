"""
Alert service layer. Same boundary contract as the other services.
"""

from typing import List

from app.services import mock_data


def list_alerts() -> List[dict]:
    return mock_data.list_alerts()

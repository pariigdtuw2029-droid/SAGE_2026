"""
Alert service layer. Same boundary contract as the other services.
Currently backed by mock data; Member 2's database layer can be wired here later.
"""

from typing import List

from app.services import mock_data


def list_alerts() -> List[dict]:
    return mock_data.list_alerts()

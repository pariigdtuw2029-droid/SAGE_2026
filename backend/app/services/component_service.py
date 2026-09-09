"""
Component service layer.

Same boundary contract as lot_service.py: routers call these
functions only, so Member 2 can swap the implementation for real
database queries without touching `app/api/components.py`.
"""

from typing import Optional

from app.services import mock_data


def get_component(component_id: str) -> Optional[dict]:
    return mock_data.MOCK_COMPONENTS.get(component_id)


def get_trajectory(component_id: str) -> Optional[dict]:
    if component_id not in mock_data.MOCK_COMPONENTS:
        return None
    return mock_data.MOCK_TRAJECTORIES.get(
        component_id, mock_data.get_default_trajectory(component_id)
    )


def get_report(component_id: str) -> Optional[dict]:
    if component_id not in mock_data.MOCK_COMPONENTS:
        return None
    return mock_data.MOCK_REPORTS.get(
        component_id, mock_data.get_default_report(component_id)
    )

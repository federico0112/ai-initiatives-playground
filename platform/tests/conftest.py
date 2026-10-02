import pytest

from telco_platform.gate import ApprovalGate


@pytest.fixture
def gate(tmp_path):
    return ApprovalGate(tmp_path / "approvals")

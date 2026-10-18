import asyncio

import pytest


async def test_network_is_blocked_in_tests():
    with pytest.raises(RuntimeError, match="network"):
        await asyncio.wait_for(asyncio.open_connection("149.154.167.50", 443), 5)

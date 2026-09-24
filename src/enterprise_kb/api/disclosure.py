"""Put what the runtime controls did on a response, so the user who asked can see it.

One field here, ``input_redacted``: redaction changed the query the user typed before it was
searched or reached the model. The value comes from the request-scoped
:class:`~enterprise_kb.adapters.controls.DisclosingRedaction`, which the route receives from
the same FastAPI dependency its service was built with. It asks about the user's own query,
never about the model's draft or caveats, which the answer path also redacts.

There is no ``review_routing``: this service binds no review router.
"""

from __future__ import annotations

from pydantic import BaseModel

from ..adapters.controls import DisclosingRedaction


def disclose[ResponseT: BaseModel](
    response: ResponseT, user_input: str, redaction: DisclosingRedaction | None
) -> ResponseT:
    """Return ``response`` with ``input_redacted`` filled in for ``user_input``."""
    if redaction is None:
        return response
    return response.model_copy(update={"input_redacted": redaction.changed(user_input)})

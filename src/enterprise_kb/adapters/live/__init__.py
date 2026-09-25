"""``live`` profile adapters: the laptop lane, answered by the local open-weight model.

The live profile is the ``local`` stack with the core model swapped (owner decision
2026-09-23): every port binds the same SDK-free local adapter ``local`` binds, except the
model port, which reaches the fleet's local open-weight model through the shared
``hex_service_kit.localmodel`` client (``LOCAL_MODEL_URL`` / ``LOCAL_MODEL``).

Gemini appears in exactly one place: the OPTIONAL web-grounding leg (:mod:`.grounding`), and
only while ``KB_GROUNDING_ENABLED`` is on. With it off, the default, the profile needs no
cloud credentials at all.
"""

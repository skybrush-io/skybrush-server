"""Extension that prevents the machine running the server from going to sleep
while the server is running.
"""

from contextlib import ExitStack, nullcontext

from pydantic import BaseModel, Field
from trio import sleep_forever


class InsomniaConfig(BaseModel):
    """Configuration model for the insomnia extension."""

    keep_display_on: bool = Field(
        default=False,
        title="Keep display on",
        description=(
            "Tick this checkbox to prevent the display from turning off while "
            "the server is running."
        ),
        json_schema_extra={"format": "checkbox"},
    )


async def run(app, configuration: InsomniaConfig, logger):
    try:
        from adrenaline import prevent_sleep

        context = prevent_sleep(
            app_name="Skybrush Server",
            display=configuration.keep_display_on,
            reason="Skybrush Server",
        )
    except Exception:
        context = nullcontext()
        logger.warn("Cannot prevent sleep mode on this platform")

    with ExitStack() as stack:
        from adrenaline.errors import NotSupportedError

        try:
            stack.enter_context(context)
        except NotSupportedError:
            logger.warn("Cannot prevent sleep mode on this platform")

        await sleep_forever()


description = "Prevents the machine running the server from going to sleep"
schema = InsomniaConfig

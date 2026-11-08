"""Wing email draft tools and dashboard voice transport."""


def register(ctx):
    from .drafts import CREATE_SCHEMA, READ_SCHEMA, create_draft, read_draft

    for schema, handler in ((CREATE_SCHEMA, create_draft), (READ_SCHEMA, read_draft)):
        ctx.register_tool(
            name=schema["name"],
            toolset="wing_email",
            schema=schema,
            handler=handler,
            description=schema["description"],
            emoji="✉️",
        )

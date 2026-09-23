"""The click commands, one module per builder family.

Each module defines its commands with ``@click.command`` -- the same option stacks and
docstrings they had inside ``cli.py`` -- and exposes ``register(main, build, calibrate)``,
which attaches them to the CLI groups. ``cli.py`` imports every module here eagerly and
calls each ``register`` once, so the command tree and its ``--help`` text are assembled at
import time, exactly as before the split.
"""

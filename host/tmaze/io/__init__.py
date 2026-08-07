"""io - hardware transport: the serial broker, its TCP client, and process helpers.

What belongs here: the single owner of the Arduino Due's COM port (broker), the
shared TCP client, the headless logger, and OS/process utilities. Talks to the
rig; imports from ``core`` are fine, but not from ``ui``.
"""

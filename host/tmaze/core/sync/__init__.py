"""sync - pure core for lining up the video, Arduino and (later) Neuropixels
streams on one time axis, by matching things that happened in two streams at
once (see docs/design/cross-stream-sync-design.md, Phase 1, and FW-0045).

Stdlib-only and headless-tested, like the rest of ``tmaze.core``. Pixels never
come here: the vision layer turns video into per-frame numbers (one brightness
value per LED per frame), and this package turns numbers into *time spans*,
matches those spans against the serial log, and fits the clock relationship.

Modules, in the order data flows through them:

- ``spans``     - inputs. Parse the per-session serial capture and the video
                  sidecar; turn a per-frame brightness trace into lit/dark spans
                  (hysteresis); the ``Span`` record everything else consumes.
- ``matching``  - which LED span is which serial event: an ordered, one-to-one
                  matcher within a host-clock band, and the per-LED ``Binding``
                  (which channel, which polarity, how good).
- ``clockfit``  - the robust straight line between two hardware clocks
                  (Theil-Sen, shared rate across firmware-clock segments, one
                  offset per edge polarity) plus the host-clock step check.
- ``pipeline``  - ``analyze()``: the whole thing for one session, returning
                  bindings, the fit, and per-LED / overall verdicts.

Glossary (docs/glossary.md): *span*, *fiducial*, *clock fit / drift*.
"""

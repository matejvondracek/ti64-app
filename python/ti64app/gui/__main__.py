"""Entry point:  python -m ti64app.gui  (absolute imports, also used by the GUI tests as main file)."""
from ti64app.gui.app import main

if __name__ in {"__main__", "__mp_main__"}:
    main()

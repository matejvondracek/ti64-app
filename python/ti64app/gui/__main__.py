"""Entry point:  python -m ti64app.gui [--browser] [--port N] [--project ROOT]  (absolute imports; also the main file
of the GUI tests, which always run in browser mode)."""
from ti64app.gui.app import main

if __name__ in {"__main__", "__mp_main__"}:
    main()
